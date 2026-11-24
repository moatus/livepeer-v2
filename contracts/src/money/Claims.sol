// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {IClaims} from "../../interfaces/IClaims.sol";
import {ITestLPT} from "../../interfaces/ITestLPT.sol";
import {IRegistry} from "../../interfaces/IRegistry.sol";
import {IRoundClock} from "../../interfaces/IRoundClock.sol";
import {ITicketBroker} from "../../interfaces/ITicketBroker.sol";
import {IStakeLedger} from "../../interfaces/IStakeLedger.sol";
import {IScoreSource} from "../../interfaces/IScoreSource.sol";
import {IScores} from "../../interfaces/IScores.sol";
import {IParameters} from "../../interfaces/IParameters.sol";
import {RewardMath} from "../math/RewardMath.sol";

/// The one LPT minter (rulebook R-09, R-10, R-12, R-14, R-24). It mints each
/// node's claim and each round's validator and treasury amounts, and holds
/// two kinds of pot until their holders withdraw their parts: each claim's,
/// and each (round, member) validator pot. A part is paid only from its own
/// pot, by transfer, never by a mint; what the floors leave stays here and
/// is never paid.
///
/// Every number a payment depends on is read, never asserted by the caller:
/// the earning round's fees from the Broker; its stake, bonds and cuts from
/// StakeLedger, its validator members from the validator set, and its
/// emission shares, schedule and reward delay from Parameters, all fixed
/// once that round has started (R-25); the score from the score source in
/// force in the round the claim runs in; who may act for the node and its
/// payout address from the Registry at execution.
///
/// Two addresses answer for scores (S-06, A3), the same contract in stage
/// 3: the score source, for scoreOf, which Parameters' group 6 replaces
/// (R-20, R-25), and the validator set, for membersAt, which stays.
///
/// There is no round 0 (R-18): every call that names it is refused with
/// NO_SUCH_ROUND, after who and before anything else (D-39, D-40).
contract Claims is IClaims {
    /// Parameters' groups read here (R-25).
    uint256 private constant EMISSION = 2;
    uint256 private constant SCHEDULE = 3;
    uint256 private constant REWARD_DELAY = 4;
    uint256 private constant SCORE_SOURCE = 6;
    /// ClaimWindow (R-12, D-08): round R with reward delay d is claimable in
    /// R + d to R + d + 21.
    uint256 private constant CLAIM_WINDOW = 21;
    /// A score is 0 to 10^6 (R-11).
    uint256 private constant MAX_SCORE = 1_000_000;

    ITestLPT private immutable lpt;
    IRegistry private immutable registry;
    IRoundClock private immutable clock;
    ITicketBroker private immutable broker;
    IStakeLedger private immutable ledger;
    IParameters private immutable params;
    IScores private immutable validators;
    /// The treasury address, fixed at deployment (R-10).
    address private immutable treasury;

    /// One (node, round) entitlement: whether it has been claimed, what the
    /// claim minted, and the pot it left here for the node's holders.
    struct Entitlement {
        bool claimed;
        uint256 paid;
        uint256 pot;
    }

    /// node => earning round => its entitlement.
    mapping(address => mapping(uint256 => Entitlement)) private entitlements;
    /// holder => node => earning round => whether the holder has withdrawn
    /// its part of that claim's pot.
    mapping(address => mapping(address => mapping(uint256 => bool))) private partWithdrawn;
    /// earning round => whether its validator and treasury amounts are minted.
    mapping(uint256 => bool) private minted;

    /// One (round, member) validator pay: what R's share mint paid the
    /// member and the pot it left here for the member's holders. The cut is
    /// the difference. Written once, by that mint; 0s for a node that was no
    /// member of the round.
    struct ValidatorPay {
        uint256 paid;
        uint256 pot;
    }

    /// member => round => its validator pay.
    mapping(address => mapping(uint256 => ValidatorPay)) private validatorPays;
    /// holder => node => round => whether the holder has withdrawn its part
    /// of the node's validator pot for that round: a flag apart from
    /// partWithdrawn, so neither withdrawal takes or blocks the other.
    mapping(address => mapping(address => mapping(uint256 => bool))) private validatorPartWithdrawn;

    constructor(
        ITestLPT lpt_,
        IRegistry registry_,
        IRoundClock clock_,
        ITicketBroker broker_,
        IStakeLedger ledger_,
        IParameters params_,
        IScores validators_,
        address treasury_
    ) {
        require(
            address(lpt_) != address(0) && address(registry_) != address(0) && address(clock_) != address(0)
                && address(broker_) != address(0) && address(ledger_) != address(0) && address(params_) != address(0)
                && address(validators_) != address(0) && treasury_ != address(0),
            "zero address"
        );
        lpt = lpt_;
        registry = registry_;
        clock = clock_;
        broker = broker_;
        ledger = ledger_;
        params = params_;
        validators = validators_;
        treasury = treasury_;
    }

    // --- the node's claim (R-12, R-24) --------------------------------------

    /// Refusals in the units section's order (D-26, D-39, D-40): who, then
    /// the round and the node named, then when, then once, then the score.
    function claim(address node, uint256 round) external {
        if (!registry.mayActFor(msg.sender, node)) revert IClaims.NOT_AUTHORIZED();
        if (round == 0) revert IClaims.NO_SUCH_ROUND();
        if (!registry.isRegistered(node)) revert IClaims.NOT_REGISTERED();
        uint256 now_ = clock.currentRound();
        // The earning round's reward delay (R-12, R-25).
        uint256 delay = params.valuesAt(REWARD_DELAY, round)[0];
        if (now_ < round || now_ - round < delay) revert IClaims.CLAIM_TOO_EARLY();
        if (now_ - round > delay + CLAIM_WINDOW) revert IClaims.CLAIM_EXPIRED();
        Entitlement storage e = entitlements[node][round];
        if (e.claimed) revert IClaims.ALREADY_CLAIMED();
        // The score source in force in the round the claim runs in (R-20).
        uint256 score = IScoreSource(address(uint160(params.valuesAt(SCORE_SOURCE, now_)[0]))).scoreOf(node);
        if (score > MAX_SCORE) revert IClaims.SCORE_OUT_OF_RANGE();

        // R-08 with the earning round's fees and stake, both fixed since it
        // ended, and its ceiling and node share. A round with zero total fees
        // or zero total stake pays its nodes 0, and the claim closes at 0.
        // So does a node with no stake at the start of the round (R-24).
        uint256 paid = 0;
        uint256 totalFee = broker.totalFees(round);
        uint256 totalStake = ledger.totalStakeAt(round);
        if (totalFee != 0 && totalStake != 0) {
            paid = RewardMath.nodeReward(
                _ceiling(round),
                params.valuesAt(EMISSION, round)[0],
                broker.feesOf(round, node),
                totalFee,
                ledger.stakeAt(node, round),
                totalStake,
                score
            );
        }
        // R-24: the cut as at the start of the earning round.
        uint256 cut = RewardMath.cutOf(paid, ledger.cutAt(node, round));
        uint256 pot = paid - cut;
        (e.claimed, e.paid, e.pot) = (true, paid, pot);

        address payout = registry.payoutOf(node);
        emit IClaims.Claimed(node, round, payout, score, paid, cut, pot);
        if (cut > 0) lpt.mint(payout, cut);
        if (pot > 0) lpt.mint(address(this), pot);
    }

    /// The caller's parts of `node`'s claims for `rounds`, transferred out of
    /// the pots held here. Any refusal reverts the whole list, so a refused
    /// list moves nothing. A list naming round 0 is refused with
    /// NO_SUCH_ROUND, whatever else it holds.
    function withdrawParts(address node, uint256[] calldata rounds) external {
        _noRoundZero(rounds);
        uint256 amount = 0;
        for (uint256 i = 0; i < rounds.length; i++) {
            uint256 round = rounds[i];
            Entitlement storage e = entitlements[node][round];
            if (!e.claimed) revert IClaims.NOT_CLAIMED();
            if (partWithdrawn[msg.sender][node][round]) revert IClaims.ALREADY_CLAIMED();
            partWithdrawn[msg.sender][node][round] = true;
            amount += _part(e.pot, msg.sender, node, round);
        }
        emit IClaims.PartsWithdrawn(msg.sender, node, rounds, amount);
        if (amount > 0) require(lpt.transfer(msg.sender, amount), "transfer failed");
    }

    // --- the round's shares (R-10, R-14) -------------------------------------

    /// Anyone, once `round` has ended, once. R-10's two amounts are minted in
    /// full: the validator amount as each member's cut to its payout, read
    /// now, each member's pot to Claims, and what R-14's division leaves to
    /// the treasury with the treasury amount. With no members, or no stake
    /// among them at the start of the round, the whole validator amount goes
    /// to the treasury.
    ///
    /// Each input is the round's own: the members of `round`, fixed when it
    /// started, and each one's stake and cut at its start. Only the payout
    /// address is read now (D-10 e1). The loop runs over the members, at most
    /// 33 (R-13), never over holders or opted-in nodes (R-23).
    function mintRoundShares(uint256 round) external {
        if (round == 0) revert IClaims.NO_SUCH_ROUND();
        if (round >= clock.currentRound()) revert IClaims.CLAIM_TOO_EARLY();
        if (minted[round]) revert IClaims.ALREADY_CLAIMED();
        minted[round] = true;
        // The round's own ceiling and shares (R-07, R-25).
        uint256 c = _ceiling(round);
        uint256[] memory shares = params.valuesAt(EMISSION, round);
        uint256 validatorAmount = RewardMath.amountOf(c, shares[1]);
        uint256 toTreasury = RewardMath.amountOf(c, shares[2]);
        emit IClaims.RoundSharesMinted(round, validatorAmount, toTreasury);

        address[] memory members = validators.membersAt(round);
        uint256[] memory stakes = new uint256[](members.length);
        uint256 totalStake = 0;
        for (uint256 i = 0; i < members.length; i++) {
            stakes[i] = ledger.stakeAt(members[i], round);
            totalStake += stakes[i];
        }
        uint256 paidOut = 0;
        uint256 pots = 0;
        for (uint256 i = 0; i < members.length; i++) {
            (uint256 paid, uint256 pot) = _payMember(members[i], round, c, shares[1], stakes[i], totalStake);
            paidOut += paid;
            pots += pot;
        }
        if (pots > 0) lpt.mint(address(this), pots);
        // The sum of the floors is at most the validator amount (R-14), so
        // this never underflows: the remainder is what the division left.
        lpt.mint(treasury, toTreasury + (validatorAmount - paidOut));
    }

    /// R-14's pay of member `m` of `round`, split as R-24 splits a claim
    /// (B1): the cut, by m's cut at the start of the round, minted to m's
    /// payout address read now; the pot recorded here and returned, for the
    /// caller to mint to Claims with the other members' pots.
    function _payMember(address m, uint256 round, uint256 c, uint256 share, uint256 stake, uint256 totalStake)
        private
        returns (uint256 paid, uint256 pot)
    {
        paid = RewardMath.validatorPayOf(c, share, stake, totalStake);
        uint256 cut = RewardMath.cutOf(paid, ledger.cutAt(m, round));
        pot = paid - cut;
        ValidatorPay storage v = validatorPays[m][round];
        (v.paid, v.pot) = (paid, pot);
        address payout = registry.payoutOf(m);
        emit IClaims.ValidatorPaid(round, m, payout, paid, cut, pot);
        if (cut > 0) lpt.mint(payout, cut);
    }

    /// The caller's parts of `node`'s validator pots for `rounds`,
    /// transferred out of the pots held here. Each part is floor(pot x bond
    /// / stake) with the bond and the node's stake at the start of the round,
    /// as a node part is (R-24). A round in which `node` was no member has a
    /// pot of 0 and withdraws as 0. Any refusal reverts the whole list, so a
    /// refused list moves nothing.
    function withdrawValidatorParts(address node, uint256[] calldata rounds) external {
        _noRoundZero(rounds);
        uint256 amount = 0;
        for (uint256 i = 0; i < rounds.length; i++) {
            uint256 round = rounds[i];
            if (!minted[round]) revert IClaims.NOT_CLAIMED();
            if (validatorPartWithdrawn[msg.sender][node][round]) revert IClaims.ALREADY_CLAIMED();
            validatorPartWithdrawn[msg.sender][node][round] = true;
            amount += _part(validatorPays[node][round].pot, msg.sender, node, round);
        }
        emit IClaims.ValidatorPartsWithdrawn(msg.sender, node, rounds, amount);
        if (amount > 0) require(lpt.transfer(msg.sender, amount), "transfer failed");
    }

    // --- views --------------------------------------------------------------

    function entitlementOf(address node, uint256 round)
        external
        view
        returns (bool claimed, uint256 paid, uint256 pot)
    {
        Entitlement storage e = entitlements[node][round];
        return (e.claimed, e.paid, e.pot);
    }

    /// 0 until the node has claimed `round`: the pot is fixed by the claim.
    function partOf(address holder, address node, uint256 round)
        external
        view
        returns (uint256 amount, bool withdrawn)
    {
        Entitlement storage e = entitlements[node][round];
        if (e.claimed) amount = _part(e.pot, holder, node, round);
        withdrawn = partWithdrawn[holder][node][round];
    }

    /// 0s when `node` was no member of `round` or its shares are not minted.
    function validatorPayOf(address node, uint256 round)
        external
        view
        returns (uint256 paid, uint256 cut, uint256 pot)
    {
        ValidatorPay storage v = validatorPays[node][round];
        return (v.paid, v.paid - v.pot, v.pot);
    }

    /// 0 until `round`'s shares are minted: the pot is fixed by the mint.
    function validatorPartOf(address holder, address node, uint256 round)
        external
        view
        returns (uint256 amount, bool withdrawn)
    {
        amount = _part(validatorPays[node][round].pot, holder, node, round);
        withdrawn = validatorPartWithdrawn[holder][node][round];
    }

    function sharesMinted(uint256 round) external view returns (bool) {
        return minted[round];
    }

    /// The ceiling of on-chain round `round` (R-05): its position under the
    /// schedule Parameters holds for it, on the fixed base.
    function _ceiling(uint256 round) private view returns (uint256) {
        uint256[] memory s = params.valuesAt(SCHEDULE, round);
        return RewardMath.ceiling(RewardMath.position(s[0], s[1], s[2], round));
    }

    /// R-18: there is no round 0, and a list that names it is refused.
    function _noRoundZero(uint256[] calldata rounds) private pure {
        for (uint256 i = 0; i < rounds.length; i++) {
            if (rounds[i] == 0) revert IClaims.NO_SUCH_ROUND();
        }
    }

    /// R-24: floor(pot x bond / S), with the holder's bond and the node's
    /// stake at the start of the earning round, so nothing done since moves it.
    /// A node with no stake then has parts of 0, without the division, and
    /// so does an empty pot, without reading StakeLedger.
    function _part(uint256 pot, address holder, address node, uint256 round) private view returns (uint256) {
        if (pot == 0) return 0;
        return RewardMath.partOf(pot, ledger.bondAt(holder, node, round), ledger.stakeAt(node, round));
    }
}
