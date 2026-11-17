// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {IClaims} from "../../interfaces/IClaims.sol";
import {ITestLPT} from "../../interfaces/ITestLPT.sol";
import {IRegistryV2} from "../../interfaces/IRegistry.sol";
import {IRoundClock} from "../../interfaces/IRoundClock.sol";
import {ITicketBroker} from "../../interfaces/ITicketBroker.sol";
import {IStakeLedger} from "../../interfaces/IStakeLedger.sol";
import {IScoreSource} from "../../interfaces/IScoreSource.sol";
import {RewardMath} from "../math/RewardMath.sol";

/// The one LPT minter (rulebook R-09, R-10, R-12, R-24). It mints each
/// node's claim and each round's validator and treasury amounts, and holds
/// every claim's pot until the node's holders withdraw their parts. A part
/// is paid only from its own pot, by transfer, never by a mint; what the
/// floors leave stays here and is never paid.
///
/// Every number a payment depends on is read, never asserted by the caller:
/// the earning round's fees from the Broker and its stake, bonds and cut
/// from StakeLedger, all fixed once that round has started or ended; the
/// score from the score source at the claim; who may act for the node and
/// its payout address from the Registry at execution.
///
/// S-05 phase 4 builds IClaims less its validator-pot functions and events,
/// which S-06's phase 4 adds: until then no round has validator members, so
/// a round's whole validator amount goes to the treasury (R-10).
contract Claims {
    /// RewardDelay (R-12): round R is claimable from R + 7.
    uint256 private constant REWARD_DELAY = 7;
    /// The last round R is claimable in is R + 28: the reward delay plus a
    /// ClaimWindow of 21 rounds (R-12, D-08).
    uint256 private constant CLAIM_LAST = 28;
    /// A score is 0 to 10^6 (R-11).
    uint256 private constant MAX_SCORE = 1_000_000;

    ITestLPT private immutable lpt;
    IRegistryV2 private immutable registry;
    IRoundClock private immutable clock;
    ITicketBroker private immutable broker;
    IStakeLedger private immutable ledger;
    IScoreSource private immutable scores;
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

    constructor(
        ITestLPT lpt_,
        IRegistryV2 registry_,
        IRoundClock clock_,
        ITicketBroker broker_,
        IStakeLedger ledger_,
        IScoreSource scores_,
        address treasury_
    ) {
        require(
            address(lpt_) != address(0) && address(registry_) != address(0) && address(clock_) != address(0)
                && address(broker_) != address(0) && address(ledger_) != address(0)
                && address(scores_) != address(0) && treasury_ != address(0),
            "zero address"
        );
        lpt = lpt_;
        registry = registry_;
        clock = clock_;
        broker = broker_;
        ledger = ledger_;
        scores = scores_;
        treasury = treasury_;
    }

    // --- the node's claim (R-12, R-24) --------------------------------------

    /// Refusals in D-26's order: who, then when, then once, then the score.
    function claim(address node, uint256 round) external {
        if (!registry.mayActFor(msg.sender, node)) revert IClaims.NOT_AUTHORIZED();
        uint256 now_ = clock.currentRound();
        if (now_ < round || now_ - round < REWARD_DELAY) revert IClaims.CLAIM_TOO_EARLY();
        if (now_ - round > CLAIM_LAST) revert IClaims.CLAIM_EXPIRED();
        Entitlement storage e = entitlements[node][round];
        if (e.claimed) revert IClaims.ALREADY_CLAIMED();
        uint256 score = scores.scoreOf(node);
        if (score > MAX_SCORE) revert IClaims.SCORE_OUT_OF_RANGE();

        // R-08 with the earning round's fees and stake, both fixed since it
        // ended, and its ceiling C(round - 1). A round with zero total fees
        // or zero total stake pays its nodes 0 (round 0, which never ran,
        // among them), and the claim closes at 0.
        uint256 paid = 0;
        uint256 totalFee = broker.totalFees(round);
        uint256 totalStake = ledger.totalStakeAt(round);
        if (totalFee != 0 && totalStake != 0) {
            paid = RewardMath.nodeReward(
                RewardMath.ceiling(round - 1),
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
    /// list moves nothing.
    function withdrawParts(address node, uint256[] calldata rounds) external {
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

    // --- the round's shares (R-10) ------------------------------------------

    /// Anyone, once `round` has ended, once. With no validator members until
    /// S-06's phase 4, the validator amount goes to the treasury with the
    /// treasury amount. Round 0 never ran, so it never ends.
    function mintRoundShares(uint256 round) external {
        if (round == 0 || round >= clock.currentRound()) revert IClaims.CLAIM_TOO_EARLY();
        if (minted[round]) revert IClaims.ALREADY_CLAIMED();
        minted[round] = true;
        uint256 c = RewardMath.ceiling(round - 1);
        uint256 validators = RewardMath.validatorsOf(c);
        uint256 toTreasury = RewardMath.treasuryOf(c);
        emit IClaims.RoundSharesMinted(round, validators, toTreasury);
        lpt.mint(treasury, validators + toTreasury);
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

    function sharesMinted(uint256 round) external view returns (bool) {
        return minted[round];
    }

    /// R-24: floor(pot x bond / S), with the holder's bond and the node's
    /// stake at the start of the earning round, so nothing done since moves it.
    function _part(uint256 pot, address holder, address node, uint256 round) private view returns (uint256) {
        return RewardMath.partOf(pot, ledger.bondAt(holder, node, round), ledger.stakeAt(node, round));
    }
}
