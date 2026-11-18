// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {vm} from "./Vm.sol";
import {LedgerRunner} from "./LedgerRunner.sol";

/// Runs the shared `ledger` cases through the contracts. Every input and
/// every expected number is read from vectors/october-main-text-v1.json.
///
/// test_every_ledger_case_is_run fails on any ledger case left unrun.
contract LedgerTest is LedgerRunner {
    function setUp() external {
        loadVectors();
    }

    function test_ledger_demo() external {
        run("ledger-demo");
    }

    function test_ledger_reserve() external {
        run("ledger-reserve");
    }

    function test_ledger_partial() external {
        run("ledger-partial");
    }

    function test_ledger_empty() external {
        run("ledger-empty");
    }

    function test_ledger_replay() external {
        run("ledger-replay");
    }

    function test_ledger_expired() external {
        run("ledger-expired");
    }

    function test_ledger_unregistered() external {
        run("ledger-unregistered");
    }

    function test_ledger_release() external {
        run("ledger-release");
    }

    function test_ledger_payout() external {
        run("ledger-payout");
    }

    /// R-23: ledger-reserve gives the same numbers after 999 more
    /// registrations, made before its steps.
    function test_ledger_reserve_after_999_registrations() external {
        deploy();
        for (uint256 i = 0; i < 999; i++) {
            vm.prank(address(uint160(0x10000 + i)));
            registry.register();
        }
        steps("ledger-reserve");
    }

    function test_every_ledger_case_is_run() external view {
        string[] memory ran = new string[](9);
        ran[0] = "ledger-demo";
        ran[1] = "ledger-reserve";
        ran[2] = "ledger-partial";
        ran[3] = "ledger-empty";
        ran[4] = "ledger-replay";
        ran[5] = "ledger-expired";
        ran[6] = "ledger-unregistered";
        ran[7] = "ledger-release";
        ran[8] = "ledger-payout";
        string memory missing = ledgerCasesNotIn(ran);
        require(bytes(missing).length == 0, string.concat("ledger case not run:", missing));
    }
}
