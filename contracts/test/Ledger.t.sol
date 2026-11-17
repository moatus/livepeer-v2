// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {vm} from "./Vm.sol";
import {Stage2} from "./Stage2.sol";
import {ITicketBroker} from "../interfaces/ITicketBroker.sol";

/// Runs the shared `ledger` cases through the contracts. Every input and
/// every expected number is read from vectors/october-main-text-v1.json.
///
/// test_every_ledger_case_is_run fails on any ledger case left unrun.
contract LedgerTest is Stage2 {
    string private path;
    string[] private payers;
    string[] private nodes;
    string[] private refusedSteps;
    string[] private refusedCodes;
    /// Each observe step: its number, and the deposit and claimable earmark
    /// the node was shown.
    string[] private observedSteps;
    uint256[] private observedDeposits;
    uint256[] private observedClaimable;

    function setUp() external {
        loadVectors();
    }

    function test_ledger_demo() external { run("ledger-demo"); }
    function test_ledger_reserve() external { run("ledger-reserve"); }
    function test_ledger_partial() external { run("ledger-partial"); }
    function test_ledger_empty() external { run("ledger-empty"); }
    function test_ledger_replay() external { run("ledger-replay"); }
    function test_ledger_expired() external { run("ledger-expired"); }
    function test_ledger_unregistered() external { run("ledger-unregistered"); }
    function test_ledger_release() external { run("ledger-release"); }
    function test_ledger_payout() external { run("ledger-payout"); }

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
        string[9] memory ran = [
            "ledger-demo", "ledger-reserve", "ledger-partial", "ledger-empty",
            "ledger-replay", "ledger-expired", "ledger-unregistered", "ledger-release",
            "ledger-payout"
        ];
        string[] memory ids = abi.decode(vm.parseJson(vectors, ".cases[*].id"), (string[]));
        string[] memory kinds = abi.decode(vm.parseJson(vectors, ".cases[*].kind"), (string[]));
        for (uint256 i = 0; i < ids.length; i++) {
            if (!eq(kinds[i], "ledger")) continue;
            bool found = false;
            for (uint256 j = 0; j < ran.length; j++) found = found || eq(ids[i], ran[j]);
            require(found, string.concat("ledger case not run: ", ids[i]));
        }
    }

    // --- the runner -----------------------------------------------------

    function run(string memory id) internal {
        deploy();
        steps(id);
    }

    /// Runs a case's steps on the current deployment, then checks every
    /// expected figure.
    function steps(string memory id) internal {
        path = string.concat(".cases[", vm.toString(caseIndex(id)), "]");
        for (uint256 i = 0; ; i++) {
            string memory s = string.concat(path, ".inputs.steps[", vm.toString(i), "]");
            if (!vm.keyExistsJson(vectors, s)) break;
            step(s, str(s, ".action"));
            holdsEverything(string.concat(id, " step ", vm.toString(i + 1)));
        }
        checkExpected();
    }

    function step(string memory s, string memory action) internal {
        toRound(num(s, ".round"));
        bytes memory refused;
        if (eq(action, "register")) {
            register(str(s, ".node"));
        } else if (eq(action, "fund")) {
            string memory payer = str(s, ".payer");
            uint256 mark = num(s, ".earmark_micro_usdc");
            address node = address(0);
            if (mark > 0) {
                node = who(str(s, ".node"));
                remember(nodes, str(s, ".node"));
            }
            remember(payers, payer);
            refused = fund(payer, num(s, ".deposit_micro_usdc"), node, mark);
        } else if (eq(action, "redeem")) {
            (ITicketBroker.Ticket memory t, bytes memory sig, uint256 rand) = ticket(
                str(s, ".ticket"), str(s, ".payer"), who(str(s, ".node")), num(s, ".created_round"),
                num(s, ".face_micro_usdc")
            );
            refused = redeem(t, sig, rand);
        } else if (eq(action, "set_payout")) {
            refused = setPayout(str(s, ".node"), who(str(s, ".payout")));
        } else if (eq(action, "request_release")) {
            refused = requestRelease(str(s, ".payer"), optionalNode(s), num(s, ".amount_micro_usdc"));
        } else if (eq(action, "release")) {
            refused = release(str(s, ".payer"), optionalNode(s));
        } else if (eq(action, "observe")) {
            address payer = who(str(s, ".payer"));
            (ITicketBroker.Sender memory shown,) = broker.getSenderInfo(payer);
            observedSteps.push(str(s, ".step"));
            observedDeposits.push(shown.deposit);
            observedClaimable.push(broker.claimableReserve(payer, who(str(s, ".node"))));
        } else {
            revert(string.concat("unknown action ", action));
        }
        if (refused.length > 0) {
            refusedSteps.push(str(s, ".step"));
            refusedCodes.push(code(refused));
        }
    }

    /// After every step, the Broker's USDC equals every deposit and
    /// earmark the case funded, plus the network share (R-21).
    function holdsEverything(string memory where) internal view {
        uint256 sum = broker.networkHeld();
        for (uint256 i = 0; i < payers.length; i++) {
            (uint256 d,,) = broker.earmarkOf(who(payers[i]), address(0));
            sum += d;
            for (uint256 j = 0; j < nodes.length; j++) {
                (uint256 e,,) = broker.earmarkOf(who(payers[i]), who(nodes[j]));
                sum += e;
            }
        }
        expectEq(usdc.balanceOf(address(broker)), sum, string.concat(where, ": Broker USDC"));
    }

    function checkExpected() internal view {
        string memory x = string.concat(path, ".expected");

        string[] memory names = vm.parseJsonKeys(vectors, string.concat(x, ".deposits_micro_usdc"));
        for (uint256 i = 0; i < names.length; i++) {
            (uint256 d,,) = broker.earmarkOf(who(names[i]), address(0));
            expectEq(d, num(x, string.concat(".deposits_micro_usdc.", names[i])), string.concat("deposit ", names[i]));
        }

        names = vm.parseJsonKeys(vectors, string.concat(x, ".earmarks_micro_usdc"));
        for (uint256 i = 0; i < names.length; i++) {
            string memory p = string.concat(".earmarks_micro_usdc.", names[i]);
            string[] memory ns = vm.parseJsonKeys(vectors, string.concat(x, p));
            for (uint256 j = 0; j < ns.length; j++) {
                (uint256 e,,) = broker.earmarkOf(who(names[i]), who(ns[j]));
                expectEq(e, num(x, string.concat(p, ".", ns[j])), string.concat("earmark ", names[i], " ", ns[j]));
            }
        }

        names = vm.parseJsonKeys(vectors, string.concat(x, ".paid_micro_usdc"));
        for (uint256 i = 0; i < names.length; i++) {
            expectEq(
                usdc.balanceOf(who(names[i])),
                num(x, string.concat(".paid_micro_usdc.", names[i])),
                string.concat("paid ", names[i])
            );
        }

        // A payer is minted exactly what it funds, so what it holds is
        // what release paid it.
        names = vm.parseJsonKeys(vectors, string.concat(x, ".released_micro_usdc"));
        for (uint256 i = 0; i < names.length; i++) {
            expectEq(
                usdc.balanceOf(who(names[i])),
                num(x, string.concat(".released_micro_usdc.", names[i])),
                string.concat("released ", names[i])
            );
        }

        expectEq(broker.networkHeld(), num(x, ".network_micro_usdc"), "network");

        string[] memory rounds = vm.parseJsonKeys(vectors, string.concat(x, ".fees_micro_usdc"));
        for (uint256 i = 0; i < rounds.length; i++) {
            uint256 r = vm.parseUint(rounds[i]);
            string memory p = string.concat(".fees_micro_usdc.", rounds[i]);
            string[] memory ns = vm.parseJsonKeys(vectors, string.concat(x, p));
            for (uint256 j = 0; j < ns.length; j++) {
                expectEq(
                    broker.feesOf(r, who(ns[j])),
                    num(x, string.concat(p, ".", ns[j])),
                    string.concat("fees round ", rounds[i], " ", ns[j])
                );
            }
        }
        rounds = vm.parseJsonKeys(vectors, string.concat(x, ".total_fees_micro_usdc"));
        for (uint256 i = 0; i < rounds.length; i++) {
            expectEq(
                broker.totalFees(vm.parseUint(rounds[i])),
                num(x, string.concat(".total_fees_micro_usdc.", rounds[i])),
                string.concat("total fees round ", rounds[i])
            );
        }

        string[] memory nums = listField(x, ".refused", "step");
        string[] memory codes = listField(x, ".refused", "code");
        expectEq(refusedSteps.length, nums.length, "refused steps");
        for (uint256 i = 0; i < nums.length; i++) {
            require(
                eq(refusedSteps[i], nums[i]) && eq(refusedCodes[i], codes[i]),
                string.concat(
                    "refusal ", vm.toString(i), ": got step ", refusedSteps[i], " ", refusedCodes[i],
                    ", want step ", nums[i], " ", codes[i]
                )
            );
        }

        nums = listField(x, ".observed", "step");
        expectEq(observedSteps.length, nums.length, "observed steps");
        for (uint256 i = 0; i < nums.length; i++) {
            require(eq(observedSteps[i], nums[i]), string.concat("observed ", vm.toString(i), ": step ", nums[i]));
            string memory o = string.concat(x, ".observed[", vm.toString(i), "]");
            expectEq(observedDeposits[i], num(o, ".deposit_micro_usdc"), string.concat("step ", nums[i], " deposit shown"));
            expectEq(
                observedClaimable[i], num(o, ".claimable_micro_usdc"), string.concat("step ", nums[i], " claimable shown")
            );
        }
    }

    /// One field of each entry of a list. Read one by one: forge returns a
    /// lone match of `[*]` as a value, not a list.
    function listField(string memory x, string memory list, string memory field)
        internal
        view
        returns (string[] memory out)
    {
        string memory at = string.concat(x, list);
        uint256 n = 0;
        while (vm.keyExistsJson(vectors, string.concat(at, "[", vm.toString(n), "]"))) n++;
        out = new string[](n);
        for (uint256 i = 0; i < n; i++) {
            out[i] = str(at, string.concat("[", vm.toString(i), "].", field));
        }
    }

    // --- reading the examples --------------------------------------------

    /// A step names its round only when it is not round 2; a fund step
    /// with no earmark names no node, so absent numbers read as 0.
    function num(string memory at, string memory field) internal view returns (uint256) {
        string memory k = string.concat(at, field);
        if (!vm.keyExistsJson(vectors, k)) return eq(field, ".round") ? 2 : 0;
        return vm.parseUint(vm.parseJsonString(vectors, k));
    }

    /// A release step names no node when it is about the deposit.
    function optionalNode(string memory s) internal view returns (address) {
        return vm.keyExistsJson(vectors, string.concat(s, ".node")) ? who(str(s, ".node")) : address(0);
    }

    function str(string memory at, string memory field) internal view returns (string memory) {
        return vm.parseJsonString(vectors, string.concat(at, field));
    }

    function remember(string[] storage list, string memory name) internal {
        for (uint256 i = 0; i < list.length; i++) {
            if (eq(list[i], name)) return;
        }
        list.push(name);
    }
}
