// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {vm} from "./Vm.sol";
import {Stage2} from "./Stage2.sol";
import {TestLPT} from "../src/money/TestLPT.sol";
import {StakeLedger} from "../src/money/StakeLedger.sol";
import {Claims} from "../src/money/Claims.sol";
import {Scores} from "../src/scores/Scores.sol";
import {IScoreSource} from "../interfaces/IScoreSource.sol";
import {IScores} from "../interfaces/IScores.sol";

/// The score a claim reads and a round's validator members, set by the
/// test: the stand-in for Scores where a shared case states them. A
/// `ledger` case states a claim's score and, under `members`, an earning
/// round's members, "a stand-in for the validator set" (rulebook, the
/// shared examples). Unset, a score reads 10^6, as a node nobody voted on
/// does, and a round has no members. Claims reads it through the two
/// views it calls, scoreOf and membersAt.
contract ScoreStub is IScoreSource {
    mapping(address => bool) private given;
    mapping(address => uint256) private score;
    mapping(uint256 => address[]) private members;

    function set(address node, uint256 s) external {
        given[node] = true;
        score[node] = s;
    }

    function setMembers(uint256 round, address[] calldata list) external {
        members[round] = list;
    }

    function scoreOf(address node) external view returns (uint256) {
        return given[node] ? score[node] : 1_000_000;
    }

    function membersAt(uint256 round) external view returns (address[] memory) {
        return members[round];
    }
}

/// A fresh stage 2 deployment plus test LPT, StakeLedger and Claims, and
/// the steps a holder takes. Claims is created at the address the token
/// was given at deployment, as the Broker is at the Registry's. Claims
/// reads scores and members from the stub, or, deployed with
/// deployStage3WithScores, from the real Scores, as stage 3 deploys it:
/// one contract as both the score source and the validator set.
abstract contract Stage3 is Stage2 {
    /// Test LPT's genesis supply, a test value outside every round's
    /// issuance. The test contract deploys the token, so it holds it.
    uint256 internal constant GENESIS = 1_000_000_000e18;

    TestLPT internal lpt;
    StakeLedger internal ledger;
    /// The stub, or address(0) when Claims reads the real Scores.
    ScoreStub internal scores;
    /// What Claims reads membersAt from: the stub or the real Scores.
    IScores internal validatorSet;
    Claims internal claims;

    function deployStage3() internal {
        deployStage3(GENESIS);
    }

    function deployStage3(uint256 genesis) internal {
        deployStage3(genesis, false);
    }

    /// As deployStage3, with the real Scores as the deployment's score
    /// source and Claims' validator set.
    function deployStage3WithScores() internal {
        deployStage3(GENESIS, true);
    }

    /// Stage 2's five contracts, then the token, StakeLedger, the stub or
    /// Scores, Parameters and Claims, in that order: the Broker, StakeLedger
    /// and Scores are given the address Parameters will have, and the token
    /// Claims'.
    function deployStage3(uint256 genesis, bool real) internal {
        uint256 n = vm.getNonce(address(this));
        address parameters = vm.computeCreateAddress(address(this), n + 8);
        address next = vm.computeCreateAddress(address(this), n + 9);
        deployBase(address(0), parameters);
        lpt = new TestLPT(next, genesis);
        ledger = new StakeLedger(lpt, registry, clock, params);
        address source;
        if (real) {
            Scores s = new Scores(registry, clock, ledger, params);
            scores = ScoreStub(address(0));
            validatorSet = s;
            source = address(s);
        } else {
            scores = new ScoreStub();
            validatorSet = IScores(address(scores));
            source = address(scores);
        }
        newParameters(source, address(0), bytes32(0), address(lpt));
        claims = new Claims(lpt, registry, clock, broker, ledger, params, validatorSet, treasuryAddress());
        require(address(claims) == next, "Claims at the address the token was given");
    }

    function claimsAddress() internal view returns (address) {
        return address(claims);
    }

    /// The treasury address fixed at deployment (R-10): a test address.
    function treasuryAddress() internal pure returns (address) {
        return who("Treasury");
    }

    /// `caller` calls `target` with `data`. Returns the revert data, empty
    /// when the call took effect. A revert with no data is a failure of the
    /// test, never read as success.
    function as_(address caller, address target, bytes memory data) internal returns (bytes memory) {
        vm.prank(caller);
        (bool ok, bytes memory ret) = target.call(data);
        if (ok) return "";
        require(ret.length > 0, "reverted with no data");
        return ret;
    }

    /// `holder` bonds `amount` to `node`, approving exactly that first; a
    /// refused bond leaves no approval behind.
    function bondAs(address holder, address node, uint256 amount) internal returns (bytes memory refused) {
        vm.prank(holder);
        lpt.approve(address(ledger), amount);
        refused = as_(holder, address(ledger), abi.encodeCall(StakeLedger.bond, (node, amount)));
        if (refused.length > 0) {
            vm.prank(holder);
            lpt.approve(address(ledger), 0);
        }
    }

    function expectRefused(bytes memory ret, string memory want, string memory what) internal pure {
        require(ret.length > 0, string.concat(what, ": took effect, want ", want));
        require(eq(code(ret), want), string.concat(what, ": refused with ", code(ret), ", want ", want));
    }

    function expectDone(bytes memory ret, string memory what) internal pure {
        require(ret.length == 0, string.concat(what, ": refused with ", code(ret)));
    }
}
