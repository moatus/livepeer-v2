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

/// Every `validator-pay` case of vectors/october-main-text-v2.json through
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

    function setUp() external {
        loadVectors("october-main-text-v2");
    }

    function test_pay_weighted() external { payCase("pay-weighted"); }
    function test_pay_thirds() external { payCase("pay-thirds"); }
    function test_pay_chained() external { payCase("pay-chained"); }
    function test_pay_empty() external { payCase("pay-empty"); }
    function test_pay_no_stake() external { payCase("pay-no-stake"); }
    function test_pay_split() external { payCase("pay-split"); }
    function test_pay_split_dust() external { payCase("pay-split-dust"); }

    /// A new validator-pay case fails this until it is run here.
    function test_every_validator_pay_case_is_run() external view {
        string[7] memory ran =
            ["pay-weighted", "pay-thirds", "pay-chained", "pay-empty", "pay-no-stake", "pay-split", "pay-split-dust"];
        string[] memory ids = abi.decode(vm.parseJson(vectors, ".cases[*].id"), (string[]));
        string[] memory kinds = abi.decode(vm.parseJson(vectors, ".cases[*].kind"), (string[]));
        for (uint256 i = 0; i < ids.length; i++) {
            if (!eq(kinds[i], "validator-pay")) continue;
            bool found = false;
            for (uint256 j = 0; j < ran.length; j++) found = found || eq(ids[i], ran[j]);
            require(found, string.concat("validator-pay case not run: ", ids[i]));
        }
    }

    /// Stage 2, the token, the stubs and Claims, created at the address the
    /// token was given.
    function deployWithStubs() internal {
        deploy();
        address next = vm.computeCreateAddress(address(this), vm.getNonce(address(this)) + 3);
        lpt = new TestLPT(next, GENESIS);
        stakes = new StakeStub();
        scores = new ScoreStub();
        validatorSet = IScores(address(scores));
        claims = new Claims(
            lpt, registry, clock, broker, IStakeLedger(address(stakes)), scores, validatorSet, treasuryAddress()
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
