// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {vm} from "./Vm.sol";
import {Stage2} from "./Stage2.sol";
import {TestLPT} from "../src/money/TestLPT.sol";
import {StakeLedger} from "../src/money/StakeLedger.sol";

/// A fresh stage 2 deployment plus test LPT and StakeLedger, and the steps
/// a holder takes. Claims is not built yet (S-05 phase 4): its address is a
/// stand-in, fixed in the token at deployment as the real one will be.
abstract contract Stage3 is Stage2 {
    /// Test LPT's genesis supply, a test value outside every round's
    /// issuance. The test contract deploys the token, so it holds it.
    uint256 internal constant GENESIS = 1_000_000_000e18;

    TestLPT internal lpt;
    StakeLedger internal ledger;

    function deployStage3() internal {
        deploy();
        lpt = new TestLPT(claimsAddress(), GENESIS);
        ledger = new StakeLedger(lpt, registry, clock);
    }

    /// Stand-ins until Claims exists.
    function claimsAddress() internal pure returns (address) {
        return who("Claims");
    }

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
