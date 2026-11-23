// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {vm} from "./Vm.sol";
import {LedgerRunnerV2} from "./LedgerRunnerV2.sol";
import {ScoreStub} from "./Stage3.sol";
import {TestLPT} from "../src/money/TestLPT.sol";
import {Claims} from "../src/money/Claims.sol";
import {IScores} from "../interfaces/IScores.sol";
import {IStakeLedger} from "../interfaces/IStakeLedger.sol";

/// Stake, bonds and cuts at the start of a round, set by the test: what a
/// `validator-pay` case states as its inputs. Claims reads it through the
/// three views the share mint and a validator withdrawal call.
contract StakeStub {
    mapping(address => mapping(uint256 => uint256)) private stake;
    mapping(address => mapping(uint256 => uint256)) private cut;
    mapping(address => mapping(address => mapping(uint256 => uint256))) private bond;

    function set(address node, uint256 round, uint256 stake_, uint256 cut_) external {
        stake[node][round] = stake_;
        cut[node][round] = cut_;
    }

    function setBond(address holder, address node, uint256 round, uint256 amount) external {
        bond[holder][node][round] = amount;
    }

    function stakeAt(address node, uint256 round) external view returns (uint256) {
        return stake[node][round];
    }

    function cutAt(address node, uint256 round) external view returns (uint256) {
        return cut[node][round];
    }

    function bondAt(address holder, address node, uint256 round) external view returns (uint256) {
        return bond[holder][node][round];
    }
}

/// Every `validator-pay` case of vectors/october-main-text-v5.json through
/// Claims: the round's share mint, then each holder of each split shown
/// withdrawing its validator part, once. Every input and expected number is
/// read from that file.
///
/// A case states the members, their stake, cut and bonds at the start of
/// the round, as inputs. Six of the seven are at emission index 0, round 1,
/// at whose start no bond can count on the real StakeLedger (a bond counts
/// from the next round, and round 1 starts at deployment), so these inputs
/// come from stubs of StakeLedger's three views and of the validator set.
/// The token, the Registry, the clock and Claims are the real ones. The
/// ledger cases ledger-validator-pot and ledger-mid-round, and the tests in
/// Claims.t.sol, run validator pay on the real StakeLedger, and the real
/// Scores.
///
/// After the mint and after each withdrawal, the runner's checks hold (see
/// LedgerRunnerV2): R-10's two amounts minted in full; one ValidatorPaid
/// for each member, agreeing with validatorPayOf; each payout, Claims and
/// the treasury rising by what was paid them; Claims holding the pots less
/// the parts withdrawn; the round's issuance at most its ceiling.
contract ValidatorPayTest is LedgerRunnerV2 {
    StakeStub internal stakes;
    /// test_validator_share_change: each member's LPT before a share mint.
    uint256[2] internal payoutBefore;

    function setUp() external {
        loadVectors("october-main-text-v5");
    }

    function test_pay_weighted() external {
        payCase("pay-weighted");
    }

    function test_pay_thirds() external {
        payCase("pay-thirds");
    }

    function test_pay_chained() external {
        payCase("pay-chained");
    }

    function test_pay_empty() external {
        payCase("pay-empty");
    }

    function test_pay_no_stake() external {
        payCase("pay-no-stake");
    }

    function test_pay_split() external {
        payCase("pay-split");
    }

    function test_pay_split_dust() external {
        payCase("pay-split-dust");
    }

    /// A new validator-pay case fails this until it is run here.
    function test_every_validator_pay_case_is_run() external view {
        string[7] memory ran =
            ["pay-weighted", "pay-thirds", "pay-chained", "pay-empty", "pay-no-stake", "pay-split", "pay-split-dust"];
        string[] memory ids = abi.decode(vm.parseJson(vectors, ".cases[*].id"), (string[]));
        string[] memory kinds = abi.decode(vm.parseJson(vectors, ".cases[*].kind"), (string[]));
        for (uint256 i = 0; i < ids.length; i++) {
            if (!eq(kinds[i], "validator-pay")) continue;
            bool found = false;
            for (uint256 j = 0; j < ran.length; j++) {
                found = found || eq(ids[i], ran[j]);
            }
            require(found, string.concat("validator-pay case not run: ", ids[i]));
        }
    }

    // --- a change to the validator share (R-25 with R-10 and R-14) ---------

    /// G announces emission shares of 900,000, 60,000 and 40,000 in round 4;
    /// they count from round 11. Round 11's shares are minted first, then
    /// round 5's, after the change counts. Each round's validator amount,
    /// each member's pay, cut and pot, and the treasury's LPT follow that
    /// round's own shares: 60,000 and 40,000 for round 11, the deployment's
    /// 10,000 and 50,000 for round 5. The expected amounts are worked here
    /// from R-10 and R-14's formulas, with each round's ceiling read from
    /// the examples' ceiling cases, not from RewardMath or Claims.
    function test_validator_share_change() external {
        deployWithStubs();
        loadCeilings();
        address[] memory members = new address[](2);
        (members[0], members[1]) = (who("A"), who("B"));
        register("A");
        register("B");
        uint256[2] memory rounds = [uint256(5), 11];
        for (uint256 i = 0; i < 2; i++) {
            stakes.set(members[0], rounds[i], 3e18, 250_000);
            stakes.set(members[1], rounds[i], 1e18, 1_000_000);
            scores.setMembers(rounds[i], members);
        }
        toRound(4);
        uint256[] memory shares = new uint256[](3);
        (shares[0], shares[1], shares[2]) = (900_000, 60_000, 40_000);
        vm.prank(who("G"));
        expectEq(params.announce(2, shares), 11, "the change counts from");
        toRound(12);
        sharePay(11, 60_000, 40_000, members);
        sharePay(5, 10_000, 50_000, members);
        holdsPots("validator share change");
        holdsIssuance("validator share change");
    }

    /// Mints `round`'s shares and holds each amount to R-10 and R-14 at the
    /// validator share `v` and treasury share `t`, both in millionths.
    function sharePay(uint256 round, uint256 v, uint256 t, address[] memory members) internal {
        uint256 c = exampleCeiling(round - 1);
        uint256 treasuryBefore = lpt.balanceOf(treasuryAddress());
        uint256 claimsBefore = lpt.balanceOf(address(claims));
        for (uint256 i = 0; i < 2; i++) {
            payoutBefore[i] = lpt.balanceOf(members[i]);
        }
        expectDone(doMintShares(who("Keeper"), round), string.concat("round ", vm.toString(round), ": the share mint"));
        uint256 paid = 0;
        uint256 pots = 0;
        for (uint256 i = 0; i < 2; i++) {
            (uint256 pay, uint256 pot) = memberPay(i, members[i], round, c * v);
            paid += pay;
            pots += pot;
        }
        expectEq(lpt.balanceOf(address(claims)) - claimsBefore, pots, "pots to Claims");
        expectEq(
            lpt.balanceOf(treasuryAddress()) - treasuryBefore,
            c * t / 1_000_000 + (c * v / 1_000_000 - paid),
            string.concat("round ", vm.toString(round), ": to the treasury")
        );
    }

    /// Member `i`'s pay, R-14: floor(c x v x stake / (10^6 x 4 LPT)), with
    /// `cv` = c x v; its cut (R-24) at its payout, which is its own address;
    /// and its pot. A has 3 LPT at a cut of 25 %, B 1 LPT at 100 %.
    function memberPay(uint256 i, address member, uint256 round, uint256 cv)
        internal
        view
        returns (uint256 pay, uint256 pot)
    {
        pay = cv * [uint256(3e18), 1e18][i] / (1_000_000 * 4e18);
        uint256 cut = pay * [uint256(250_000), 1_000_000][i] / 1_000_000;
        pot = pay - cut;
        string memory m = string.concat("round ", vm.toString(round), i == 0 ? ": A" : ": B");
        (uint256 gotPay, uint256 gotCut, uint256 gotPot) = claims.validatorPayOf(member, round);
        expectEq(gotPay, pay, string.concat(m, " paid"));
        expectEq(gotCut, cut, string.concat(m, " cut"));
        expectEq(gotPot, pot, string.concat(m, " pot"));
        expectEq(lpt.balanceOf(member) - payoutBefore[i], cut, string.concat(m, "'s cut at its payout"));
    }

    /// The examples' ceiling for emission index `r`.
    function exampleCeiling(uint256 r) internal view returns (uint256) {
        for (uint256 i = 0; i < ceilingAt.length; i++) {
            if (ceilingAt[i] == r) return ceilingWei[i];
        }
        revert(string.concat("no ceiling case for emission index ", vm.toString(r)));
    }

    /// Stage 2, the token, the stubs and Claims, created at the address the
    /// token was given.
    function deployWithStubs() internal {
        uint256 n = vm.getNonce(address(this));
        address next = vm.computeCreateAddress(address(this), n + 9);
        deployBase(vm.computeCreateAddress(address(this), n + 8));
        lpt = new TestLPT(next, GENESIS);
        stakes = new StakeStub();
        scores = new ScoreStub();
        validatorSet = IScores(address(scores));
        newParameters(address(scores), address(0), address(0));
        claims = new Claims(
            lpt, registry, clock, broker, IStakeLedger(address(stakes)), params, validatorSet, treasuryAddress()
        );
        require(address(claims) == next, "Claims at the address the token was given");
    }

    function payCase(string memory id) internal {
        deployWithStubs();
        string memory at = string.concat(".cases[", vm.toString(caseIndex(id)), "]");
        // The case counts the emission index r; the round is R = r + 1.
        uint256 round = num(at, ".inputs.round") + 1;
        address[] memory members = setMembers(at, round);
        toRound(round + 1);
        expectDone(doMintShares(who("Keeper"), round), string.concat(id, ": the share mint"));
        holdsPots(id);
        holdsIssuance(id);

        string memory x = string.concat(at, ".expected");
        expectEq(
            vm.parseJsonKeys(vectors, string.concat(x, ".paid_wei")).length, members.length, string.concat(id, ": paid")
        );
        for (uint256 i = 0; i < members.length; i++) {
            string memory name = nameOf(at, i);
            (uint256 paid,,) = claims.validatorPayOf(members[i], round);
            expectEq(paid, num(x, string.concat(".paid_wei.", name)), string.concat(id, ": ", name, " paid"));
        }
        // Treasury got R-10's treasury amount, as RoundSharesMinted gave it,
        // and the case's figure on top.
        expectEq(
            lpt.balanceOf(treasuryAddress()) - sharesTreasury[0],
            num(x, ".to_treasury_wei"),
            string.concat(id, ": to the treasury on top")
        );
        if (vm.keyExistsJson(vectors, string.concat(x, ".splits"))) splits(id, at, round);
    }

    /// The case's members, with their stake and, for a split shown, the cut
    /// and every bond at the start of `round`, given to the stubs. Each
    /// member is a registered node, paid at its own address.
    function setMembers(string memory at, uint256 round) internal returns (address[] memory members) {
        uint256 n = 0;
        while (vm.keyExistsJson(vectors, string.concat(at, ".inputs.members[", vm.toString(n), "]"))) n++;
        members = new address[](n);
        for (uint256 i = 0; i < n; i++) {
            string memory name = nameOf(at, i);
            register(name);
            remember(lptNodes, name);
            members[i] = who(name);
            uint256 cut = 0;
            string memory sp = string.concat(at, ".inputs.splits.", name);
            if (vm.keyExistsJson(vectors, sp)) {
                cut = num(sp, ".cut_millionths");
                string[] memory hs = vm.parseJsonKeys(vectors, string.concat(sp, ".bonds_wei"));
                for (uint256 j = 0; j < hs.length; j++) {
                    remember(holders, hs[j]);
                    stakes.setBond(who(hs[j]), members[i], round, num(sp, string.concat(".bonds_wei.", hs[j])));
                }
            }
            stakes.set(
                members[i], round, num(string.concat(at, ".inputs.members[", vm.toString(i), "]"), ".stake_wei"), cut
            );
        }
        scores.setMembers(round, members);
    }

    /// For each split shown: the cut to the member's payout, the pot to
    /// Claims, each holder withdrawing exactly its part once (a second
    /// withdrawal is ALREADY_CLAIMED and moves nothing), and the dust left
    /// in Claims of that pot.
    function splits(string memory id, string memory at, uint256 round) internal {
        string[] memory ms = vm.parseJsonKeys(vectors, string.concat(at, ".expected.splits"));
        for (uint256 i = 0; i < ms.length; i++) {
            oneSplit(string.concat(id, ": ", ms[i]), string.concat(at, ".expected.splits.", ms[i]), who(ms[i]), round);
        }
    }

    function oneSplit(string memory w, string memory e, address m, uint256 round) internal {
        (, uint256 cut, uint256 pot) = claims.validatorPayOf(m, round);
        expectEq(cut, num(e, ".cut_wei"), string.concat(w, " cut"));
        expectEq(lpt.balanceOf(registry.payoutOf(m)), cut, string.concat(w, " cut at its payout"));
        expectEq(pot, num(e, ".pot_wei"), string.concat(w, " pot"));
        string[] memory hs = vm.parseJsonKeys(vectors, string.concat(e, ".parts_wei"));
        uint256 parts = 0;
        for (uint256 j = 0; j < hs.length; j++) {
            uint256 want = num(e, string.concat(".parts_wei.", hs[j]));
            withdrawOnce(string.concat(w, " ", hs[j]), who(hs[j]), m, round, want);
            parts += want;
        }
        expectEq(pot - parts, num(e, ".dust_wei"), string.concat(w, " dust kept in Claims"));
    }

    /// `h` withdraws exactly `want` of m's validator pot for `round`, and a
    /// second withdrawal is refused with ALREADY_CLAIMED and moves nothing.
    function withdrawOnce(string memory w, address h, address m, uint256 round, uint256 want) internal {
        uint256 mine = lpt.balanceOf(h);
        expectDone(doWithdrawValidator(h, m, one(round)), string.concat(w, " withdraws"));
        expectEq(lpt.balanceOf(h) - mine, want, string.concat(w, "'s part"));
        holdsPots(string.concat(w, " withdraws"));
        expectRefused(doWithdrawValidator(h, m, one(round)), "ALREADY_CLAIMED", string.concat(w, " again"));
        holdsPots(string.concat(w, " again"));
    }

    function nameOf(string memory at, uint256 i) internal view returns (string memory) {
        return str(at, string.concat(".inputs.members[", vm.toString(i), "].node"));
    }

    function one(uint256 r) internal pure returns (uint256[] memory list) {
        list = new uint256[](1);
        list[0] = r;
    }
}
