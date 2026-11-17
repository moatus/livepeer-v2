// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {vm} from "./Vm.sol";
import {Stage2} from "./Stage2.sol";
import {TestLPT} from "../src/money/TestLPT.sol";
import {StakeLedger} from "../src/money/StakeLedger.sol";
import {Claims} from "../src/money/Claims.sol";
import {IScoreSource} from "../interfaces/IScoreSource.sol";

/// The score a claim reads, set by the test: the stand-in for S-06's
/// Scores until its phase 2 lands (spec S-05, phase 4). Unset reads 10^6,
/// as a node nobody voted on does.
contract ScoreStub is IScoreSource {
    mapping(address => bool) private given;
    mapping(address => uint256) private score;

    function set(address node, uint256 s) external {
        given[node] = true;
        score[node] = s;
    }

    function scoreOf(address node) external view returns (uint256) {
        return given[node] ? score[node] : 1_000_000;
    }
}

/// A fresh stage 2 deployment plus test LPT, StakeLedger and Claims, and
/// the steps a holder takes. Claims is created at the address the token
/// was given at deployment, as the Broker is at the Registry's.
abstract contract Stage3 is Stage2 {
    /// Test LPT's genesis supply, a test value outside every round's
    /// issuance. The test contract deploys the token, so it holds it.
    uint256 internal constant GENESIS = 1_000_000_000e18;

    TestLPT internal lpt;
    StakeLedger internal ledger;
    ScoreStub internal scores;
    Claims internal claims;

    function deployStage3() internal {
        deployStage3(GENESIS);
    }

    function deployStage3(uint256 genesis) internal {
        deploy();
        // The token, StakeLedger and the stub come first; Claims is the
        // fourth contract created from here.
        address next = vm.computeCreateAddress(address(this), vm.getNonce(address(this)) + 3);
        lpt = new TestLPT(next, genesis);
        ledger = new StakeLedger(lpt, registry, clock);
        scores = new ScoreStub();
        claims = new Claims(lpt, registry, clock, broker, ledger, scores, treasuryAddress());
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
