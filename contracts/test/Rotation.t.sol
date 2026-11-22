// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {vm, Vm} from "./Vm.sol";
import {Stage3} from "./Stage3.sol";
import {Registry} from "../src/authority/Registry.sol";
import {IRegistryV3} from "../interfaces/IRegistry.sol";
import {Broker} from "../src/money/Broker.sol";
import {Claims} from "../src/money/Claims.sol";
import {MockUSDC} from "../src/money/MockUSDC.sol";
import {Scores} from "../src/scores/Scores.sol";
import {StakeLedger} from "../src/money/StakeLedger.sol";
import {TestLPT} from "../src/money/TestLPT.sol";
import {ITicketBroker} from "../interfaces/ITicketBroker.sol";

/// Key rotation (rulebook R-26, spec S-10 section 4, "the old key refused
/// everywhere"), on a stage 3 deployment with the real Scores.
///
/// Every public state-changing function of every part, read from the
/// compiler's ABI, is on exactly one of six lists, and each list has its
/// test:
///   current operator   test_second_rotation_refuses_the_old_keys_everywhere,
///                      and the shared cases ledger-rotation and
///                      validators-rotation (Claims.t.sol, Scores.t.sol)
///   proposed operator  the three proposal tests below
///   holder funds       test_holder_funds_move_only_the_callers_own
///   permissionless     test_a_ticket_after_rotation_pays_the_payout, and the
///                      tests of each part (seat, unseat, mintRoundShares,
///                      initializeRound, buy, poke, the pool's v2 calls)
///   authority          the parameters-refusal-order case (Parameters.t.sol)
///   restricted         test_mint_only_from_claims (StakeLedger.t.sol),
///                      test_draw_refusals_in_order (BuybackRefusals.t.sol);
///                      MockUSDC.mint is open, a test token
contract RotationTest is Stage3 {
    uint256 internal constant ONE = 1_000_000;

    /// The six lists, each function named as Part.function.
    function currentOperator() internal pure returns (string[] memory l) {
        l = new string[](7);
        (l[0], l[1], l[2], l[3]) =
        ("Claims.claim", "StakeLedger.setRewardCut", "Registry.setPayout", "Registry.setOperator");
        (l[4], l[5], l[6]) = ("Scores.optIn", "Scores.optOut", "Scores.vote");
    }

    function proposedOperator() internal pure returns (string[] memory l) {
        l = new string[](1);
        l[0] = "Registry.acceptOperator";
    }

    /// Moves only the caller's own bond, deposit, parts or tokens, or tokens
    /// it was approved for; naming a node chooses where, never whose.
    function holderFunds() internal pure returns (string[] memory l) {
        l = new string[](18);
        (l[0], l[1], l[2], l[3]) =
        ("StakeLedger.bond", "StakeLedger.unbond", "StakeLedger.transferBond", "StakeLedger.unbondTransfer");
        (l[4], l[5], l[6], l[7]) =
        ("StakeLedger.rebond", "StakeLedger.withdrawUnbonded", "Claims.withdrawParts", "Claims.withdrawValidatorParts");
        (l[8], l[9], l[10]) = ("Broker.fund", "Broker.requestRelease", "Broker.release");
        (l[11], l[12], l[13], l[14]) = ("TestLPT.approve", "TestLPT.transfer", "TestLPT.transferFrom", "TestLPT.burn");
        (l[15], l[16], l[17]) = ("MockUSDC.approve", "MockUSDC.transfer", "MockUSDC.transferFrom");
    }

    /// Any address; the effect is fixed by rule. The Buyback's buy and poke
    /// and the pool's mint and swap are S-07's, added after the spec's table.
    function permissionless() internal pure returns (string[] memory l) {
        l = new string[](10);
        (l[0], l[1], l[2], l[3]) = ("Registry.register", "Scores.seat", "Scores.unseat", "Claims.mintRoundShares");
        (l[4], l[5]) = ("RoundClock.initializeRound", "Broker.redeemWinningTicket");
        (l[6], l[7], l[8], l[9]) = ("Buyback.buy", "Buyback.poke", "TestPool.mint", "TestPool.swap");
    }

    function authority() internal pure returns (string[] memory l) {
        l = new string[](2);
        (l[0], l[1]) = ("Parameters.announce", "Parameters.withdraw");
    }

    /// One caller fixed at deployment, or open on a test token. The spec's
    /// "restricted mint", with S-07's draw of the network share, which only
    /// the Buyback makes.
    function restricted() internal pure returns (string[] memory l) {
        l = new string[](3);
        (l[0], l[1], l[2]) = ("TestLPT.mint", "MockUSDC.mint", "Broker.drawNetwork");
    }

    function lists() internal pure returns (string[][6] memory all, string[6] memory names) {
        all = [currentOperator(), proposedOperator(), holderFunds(), permissionless(), authority(), restricted()];
        names = ["current operator", "proposed operator", "holder funds", "permissionless", "authority", "restricted"];
    }

    // --- the six lists against the compiler's ABI ------------------------------

    /// Every part in src/, every function of its compiled ABI that is not
    /// view or pure, is on exactly one list; every listed function exists.
    /// A part or function added later on no list fails here.
    function test_every_state_changing_function_is_on_exactly_one_list() external view {
        (string[][6] memory all, string[6] memory names) = lists();
        string memory root = vm.projectRoot();
        Vm.DirEntry[] memory files = vm.readDir(string.concat(root, "/src"), 4);
        string[] memory seen = new string[](200);
        uint256 found = 0;
        uint256 parts = 0;
        for (uint256 f = 0; f < files.length; f++) {
            if (files[f].isDir || !endsWith(files[f].path, ".sol")) continue;
            string memory file = baseName(files[f].path);
            Vm.DirEntry[] memory built = vm.readDir(string.concat(root, "/out/", file), 1);
            require(built.length > 0, string.concat("no build output for ", file));
            for (uint256 b = 0; b < built.length; b++) {
                if (!endsWith(built[b].path, ".json")) continue;
                parts++;
                found = stateChanging(built[b].path, seen, found);
            }
        }
        require(parts >= 12, "fewer parts than the deployment has: the build output was not read");
        string memory problems;
        for (uint256 i = 0; i < found; i++) {
            problems = string.concat(problems, onLists(seen[i], all, names));
        }
        // Every listed function is one the compiler reports.
        for (uint256 k = 0; k < 6; k++) {
            for (uint256 i = 0; i < all[k].length; i++) {
                if (!containsN(seen, found, all[k][i])) {
                    problems = string.concat(problems, " ", all[k][i], " is listed but not in the ABI;");
                }
            }
        }
        require(bytes(problems).length == 0, string.concat("six lists:", problems));
    }

    /// Adds to `seen` each function of the compiled part at `path` that is
    /// neither view nor pure, named Part.function, once per name.
    function stateChanging(string memory path, string[] memory seen, uint256 found) internal view returns (uint256) {
        string memory part = trim(baseName(path), 5);
        string memory json = vm.readFile(path);
        for (uint256 i = 0; vm.keyExistsJson(json, string.concat(".abi[", vm.toString(i), "]")); i++) {
            string memory e = string.concat(".abi[", vm.toString(i), "]");
            if (!eq(vm.parseJsonString(json, string.concat(e, ".type")), "function")) continue;
            string memory m = vm.parseJsonString(json, string.concat(e, ".stateMutability"));
            if (eq(m, "view") || eq(m, "pure")) continue;
            string memory fn = string.concat(part, ".", vm.parseJsonString(json, string.concat(e, ".name")));
            if (containsN(seen, found, fn)) continue;
            seen[found++] = fn;
        }
        return found;
    }

    /// Empty when `fn` is on exactly one list; otherwise what is wrong.
    function onLists(string memory fn, string[][6] memory all, string[6] memory names)
        internal
        pure
        returns (string memory)
    {
        string memory on;
        uint256 n = 0;
        for (uint256 k = 0; k < 6; k++) {
            if (contains(all[k], fn)) {
                n++;
                on = string.concat(on, " ", names[k], ";");
            }
        }
        if (n == 0) return string.concat(" ", fn, " is on no list;");
        if (n > 1) return string.concat(" ", fn, " is on", on);
        return "";
    }

    // --- the proposed operator: one slot per node ----------------------------

    function setUp() external {
        loadVectors("october-main-text-v4");
        deployStage3WithScores();
    }

    /// N proposes K1 and K1 accepts: K1 is N's operator key.
    function rotateToK1() internal {
        register("N");
        expectDone(setOperatorAs(who("N"), who("N"), who("K1")), "N proposes K1");
        expectDone(acceptOperatorAs(who("K1"), who("N")), "K1 accepts");
        require(registry.operatorOf(who("N")) == who("K1"), "K1 is N's operator");
    }

    /// K1 proposes K2, then K3: K2's acceptance is refused and K3's counts.
    /// N's own address, K1 and the replaced key are refused.
    function test_a_replaced_proposal_takes_the_new_key() external {
        rotateToK1();
        address n = who("N");
        expectDone(setOperatorAs(who("K1"), n, who("K2")), "K1 proposes K2");
        expectDone(setOperatorAs(who("K1"), n, who("K3")), "K1 proposes K3");
        require(registry.proposedOperatorOf(n) == who("K3"), "the slot holds K3");
        expectRefused(acceptOperatorAs(who("K2"), n), "NOT_AUTHORIZED", "the replaced K2 accepts");
        expectRefused(acceptOperatorAs(who("K1"), n), "NOT_AUTHORIZED", "the operator K1 accepts");
        expectRefused(acceptOperatorAs(n, n), "NOT_AUTHORIZED", "N accepts");
        expectDone(acceptOperatorAs(who("K3"), n), "K3 accepts");
        require(registry.operatorOf(n) == who("K3"), "K3 is N's operator");
        require(registry.proposedOperatorOf(n) == address(0), "the slot is empty");
    }

    /// K1 proposes K2, then proposes K1, the current key: the slot is
    /// cleared and K2's acceptance refused.
    function test_a_cleared_proposal_refuses_the_key_it_named() external {
        rotateToK1();
        address n = who("N");
        expectDone(setOperatorAs(who("K1"), n, who("K2")), "K1 proposes K2");
        vm.recordLogs();
        expectDone(setOperatorAs(who("K1"), n, who("K1")), "K1 proposes itself");
        Vm.Log[] memory logs = vm.getRecordedLogs();
        require(logs.length == 1 && logs[0].topics[0] == IRegistryV3.OperatorProposed.selector, "one OperatorProposed");
        require(logs[0].topics[2] == bytes32(0), "the event names no proposed key");
        require(registry.proposedOperatorOf(n) == address(0), "the slot is empty");
        expectRefused(acceptOperatorAs(who("K2"), n), "NOT_AUTHORIZED", "K2 accepts a cleared proposal");
        expectRefused(acceptOperatorAs(who("K1"), n), "NOT_AUTHORIZED", "K1 accepts an empty slot");
        require(registry.operatorOf(n) == who("K1"), "K1 is still N's operator");
    }

    /// An acceptance empties the slot: the same key cannot accept twice.
    function test_a_used_proposal_cannot_be_accepted_twice() external {
        rotateToK1();
        address n = who("N");
        expectDone(setOperatorAs(who("K1"), n, who("K2")), "K1 proposes K2");
        vm.recordLogs();
        expectDone(acceptOperatorAs(who("K2"), n), "K2 accepts");
        Vm.Log[] memory logs = vm.getRecordedLogs();
        require(logs.length == 1 && logs[0].topics[0] == IRegistryV3.OperatorSet.selector, "one OperatorSet");
        require(logs[0].topics[1] == bytes32(uint256(uint160(n))), "OperatorSet names N");
        require(logs[0].topics[2] == bytes32(uint256(uint160(who("K2")))), "OperatorSet names K2");
        require(registry.proposedOperatorOf(n) == address(0), "the slot is empty");
        expectRefused(acceptOperatorAs(who("K2"), n), "NOT_AUTHORIZED", "K2 accepts again");
        require(registry.operatorOf(n) == who("K2"), "K2 is N's operator");
    }

    /// R-26's orders: setOperator NOT_AUTHORIZED, NOT_REGISTERED,
    /// OPERATOR_INVALID; setPayout NOT_AUTHORIZED, NOT_REGISTERED,
    /// PAYOUT_INVALID.
    function test_refusal_order_operator_and_payout() external {
        rotateToK1();
        address n = who("N");
        address x = who("X");
        expectRefused(setOperatorAs(who("K1"), x, address(0)), "NOT_AUTHORIZED", "K1 for unregistered X, zero");
        expectRefused(setOperatorAs(x, x, address(0)), "NOT_REGISTERED", "X for itself, zero");
        expectRefused(setOperatorAs(n, n, address(0)), "NOT_AUTHORIZED", "N, rotated, zero");
        expectRefused(setOperatorAs(who("K1"), n, address(0)), "OPERATOR_INVALID", "K1 proposes zero");
        expectRefused(setPayoutAs(who("K1"), x, address(broker)), "NOT_AUTHORIZED", "K1 for X, the Broker");
        expectRefused(setPayoutAs(x, x, address(broker)), "NOT_REGISTERED", "X for itself, the Broker");
        expectRefused(setPayoutAs(n, n, address(broker)), "NOT_AUTHORIZED", "N, rotated, the Broker");
        expectRefused(setPayoutAs(who("K1"), n, address(broker)), "PAYOUT_INVALID", "K1 sets the Broker");
    }

    // --- the current operator: the second rotation ----------------------------

    /// The call `fn` from the current-operator list, made for `node`, as an
    /// accepted call would make it in this test. Every name on the list must
    /// have one, so a function added to the list is tested here.
    function forNode(string memory fn, address node, uint256 round) internal view returns (address, bytes memory) {
        if (eq(fn, "Claims.claim")) return (address(claims), abi.encodeCall(Claims.claim, (node, round)));
        if (eq(fn, "StakeLedger.setRewardCut")) {
            return (address(ledger), abi.encodeCall(StakeLedger.setRewardCut, (node, 500_000)));
        }
        if (eq(fn, "Registry.setPayout")) {
            return (address(registry), abi.encodeCall(Registry.setPayout, (node, who("W3"))));
        }
        if (eq(fn, "Registry.setOperator")) {
            return (address(registry), abi.encodeCall(Registry.setOperator, (node, who("K4"))));
        }
        if (eq(fn, "Scores.optIn")) return (address(validatorSet), abi.encodeCall(Scores.optIn, (node)));
        if (eq(fn, "Scores.optOut")) return (address(validatorSet), abi.encodeCall(Scores.optOut, (node)));
        if (eq(fn, "Scores.vote")) {
            return (address(validatorSet), abi.encodeCall(Scores.vote, (node, who("X"), 500_000)));
        }
        revert(string.concat("no call built for ", fn));
    }

    /// N rotates to K1, K1 to K2, then K2 proposes K3 and K3 accepts. N, K1
    /// and K2 are each refused NOT_AUTHORIZED on every current-operator
    /// function; K3's calls take effect. N's payout is W until K3 sets it.
    function test_second_rotation_refuses_the_old_keys_everywhere() external {
        address n = who("N");
        address h = who("H");
        register("N");
        expectDone(setPayout("N", who("W")), "N sets payout W");
        lpt.transfer(h, 1000e18);
        expectDone(bondAs(h, n, 1000e18), "H bonds to N");
        expectDone(as_(n, address(validatorSet), abi.encodeCall(Scores.optIn, (n))), "N opts in");
        // H's bond counts from round 2; N is seated then and is a member
        // from round 3.
        toRound(2);
        expectDone(as_(who("Anyone"), address(validatorSet), abi.encodeCall(Scores.seat, (n))), "N seated");

        expectDone(setOperatorAs(n, n, who("K1")), "N proposes K1");
        expectDone(acceptOperatorAs(who("K1"), n), "K1 accepts");
        expectDone(setOperatorAs(who("K1"), n, who("K2")), "K1 proposes K2");
        expectDone(acceptOperatorAs(who("K2"), n), "K2 accepts");
        require(registry.payoutOf(n) == who("W"), "payout unchanged by the first rotations");
        expectDone(setOperatorAs(who("K2"), n, who("K3")), "K2 proposes K3");
        expectDone(acceptOperatorAs(who("K3"), n), "K3 accepts");
        require(registry.operatorOf(n) == who("K3"), "K3 is N's operator");

        // Round 2 is claimable once its reward delay has passed, and N
        // votes as a member, from round 3.
        uint256 delay = params.valuesAt(4, 2)[0];
        toRound(2 + delay > 3 ? 2 + delay : 3);
        string[] memory list = currentOperator();
        address[3] memory old = [n, who("K1"), who("K2")];
        for (uint256 i = 0; i < list.length; i++) {
            (address target, bytes memory data) = forNode(list[i], n, 2);
            for (uint256 j = 0; j < old.length; j++) {
                expectRefused(
                    as_(old[j], target, data), "NOT_AUTHORIZED", string.concat(list[i], " by an old key or N")
                );
            }
        }
        require(registry.payoutOf(n) == who("W"), "payout unchanged by the refused calls");
        require(registry.operatorOf(n) == who("K3"), "K3 is still N's operator");

        for (uint256 i = 0; i < list.length; i++) {
            (address target, bytes memory data) = forNode(list[i], n, 2);
            expectDone(as_(who("K3"), target, data), string.concat(list[i], " by K3"));
        }
        (bool claimed,,) = claims.entitlementOf(n, 2);
        require(claimed, "K3's claim is recorded");
        require(ledger.cutAt(n, clock.currentRound() + 1) == 500_000, "K3's cut counts");
        require(registry.proposedOperatorOf(n) == who("K4"), "K3's proposal is in the slot");
        require(registry.payoutOf(n) == who("W3"), "the payout is W3 once K3 sets it");
    }

    // --- holder funds ---------------------------------------------------------

    /// The call `fn` from the holder-funds list, made by a key that holds
    /// nothing, aimed at what N's own address holds. Every name on the list
    /// must have one.
    function aimedAt(string memory fn, address n, address q, address k) internal view returns (address, bytes memory) {
        uint256[] memory rounds = new uint256[](1);
        rounds[0] = 2;
        if (eq(fn, "StakeLedger.bond")) return (address(ledger), abi.encodeCall(StakeLedger.bond, (n, 1e18)));
        if (eq(fn, "StakeLedger.unbond")) return (address(ledger), abi.encodeCall(StakeLedger.unbond, (n, 1e18)));
        if (eq(fn, "StakeLedger.transferBond")) {
            return (address(ledger), abi.encodeCall(StakeLedger.transferBond, (n, q, 1e18)));
        }
        if (eq(fn, "StakeLedger.unbondTransfer")) {
            return (address(ledger), abi.encodeCall(StakeLedger.unbondTransfer, (n, q, 1e18)));
        }
        if (eq(fn, "StakeLedger.rebond")) return (address(ledger), abi.encodeCall(StakeLedger.rebond, (n, 1e18)));
        if (eq(fn, "StakeLedger.withdrawUnbonded")) {
            return (address(ledger), abi.encodeCall(StakeLedger.withdrawUnbonded, ()));
        }
        if (eq(fn, "Claims.withdrawParts")) {
            return (address(claims), abi.encodeCall(Claims.withdrawParts, (n, rounds)));
        }
        if (eq(fn, "Claims.withdrawValidatorParts")) {
            return (address(claims), abi.encodeCall(Claims.withdrawValidatorParts, (n, rounds)));
        }
        if (eq(fn, "Broker.fund")) return (address(broker), abi.encodeCall(Broker.fund, (1, n, 1)));
        if (eq(fn, "Broker.requestRelease")) {
            return (address(broker), abi.encodeCall(Broker.requestRelease, (address(0), 1)));
        }
        if (eq(fn, "Broker.release")) return (address(broker), abi.encodeCall(Broker.release, (address(0))));
        if (eq(fn, "TestLPT.approve")) return (address(lpt), abi.encodeCall(TestLPT.approve, (k, 1e18)));
        if (eq(fn, "TestLPT.transfer")) return (address(lpt), abi.encodeCall(TestLPT.transfer, (k, 1e18)));
        if (eq(fn, "TestLPT.transferFrom")) {
            return (address(lpt), abi.encodeCall(TestLPT.transferFrom, (n, k, 1e18)));
        }
        if (eq(fn, "TestLPT.burn")) return (address(lpt), abi.encodeCall(TestLPT.burn, (1e18)));
        if (eq(fn, "MockUSDC.approve")) return (address(usdc), abi.encodeCall(MockUSDC.approve, (k, 1)));
        if (eq(fn, "MockUSDC.transfer")) return (address(usdc), abi.encodeCall(MockUSDC.transfer, (k, 1)));
        if (eq(fn, "MockUSDC.transferFrom")) {
            return (address(usdc), abi.encodeCall(MockUSDC.transferFrom, (n, k, 1)));
        }
        revert(string.concat("no call built for ", fn));
    }

    /// What N's own address holds, and what K2 holds.
    function holdings(address n, address k) internal view returns (uint256[8] memory) {
        (ITicketBroker.Sender memory shown,) = broker.getSenderInfo(n);
        (ITicketBroker.Sender memory kShown,) = broker.getSenderInfo(k);
        return [
            ledger.stayingOf(n, n),
            lpt.balanceOf(n),
            usdc.balanceOf(n),
            shown.deposit,
            ledger.stayingOf(k, n),
            lpt.balanceOf(k),
            usdc.balanceOf(k),
            kShown.deposit
        ];
    }

    /// N's own address bonds to N, holds LPT and USDC and a deposit, and
    /// approves nobody; then N rotates to K2. K2, now acting for N, calls
    /// every holder-funds function at what N's address holds: nothing of
    /// N's moves and K2 gains nothing. N's address still moves its own.
    function test_holder_funds_move_only_the_callers_own() external {
        address n = who("N");
        address k = who("K2");
        address q = who("Q");
        register("N");
        register("Q");
        lpt.transfer(n, 1000e18);
        expectDone(bondAs(n, n, 500e18), "N bonds to itself");
        usdc.mint(n, 5_000_000);
        expectDone(fund("N", 2_000_000, address(0), 0), "N funds a deposit");
        expectDone(setOperatorAs(n, n, k), "N proposes K2");
        expectDone(acceptOperatorAs(k, n), "K2 accepts");
        toRound(3);

        uint256[8] memory before = holdings(n, k);
        string[] memory list = holderFunds();
        for (uint256 i = 0; i < list.length; i++) {
            (address target, bytes memory data) = aimedAt(list[i], n, q, k);
            bytes memory refused = as_(k, target, data);
            if (eq(list[i], "StakeLedger.unbond")) expectRefused(refused, "AMOUNT_TOO_LARGE", "K2 unbonds N's bond");
            uint256[8] memory now_ = holdings(n, k);
            for (uint256 j = 0; j < 8; j++) {
                require(now_[j] == before[j], string.concat(list[i], " by K2 moved what N's address holds"));
            }
        }
        expectDone(as_(n, address(ledger), abi.encodeCall(StakeLedger.unbond, (n, 100e18))), "N unbonds its own");
        require(ledger.stayingOf(n, n) == 400e18, "N's address keeps its own bond");
    }

    // --- permissionless: a ticket after the rotation ----------------------------

    /// After N rotates to K1 and K1 sets payout W, a ticket naming N,
    /// redeemed by N's unchanged node key, pays W the node's half.
    function test_a_ticket_after_rotation_pays_the_payout() external {
        rotateToK1();
        address n = who("N");
        expectDone(setPayoutAs(who("K1"), n, who("W")), "K1 sets payout W");
        expectDone(fund("P", 10_000_000, address(0), 0), "P funds");
        toRound(3);
        (ITicketBroker.Ticket memory t, bytes memory sig, uint256 rand) = ticket("t1", "P", n, 3, 1_000_000);
        uint256 cut = params.valuesAt(1, 3)[0];
        expectDone(redeem(t, sig, rand), "N's node redeems");
        require(usdc.balanceOf(who("W")) == 1_000_000 * cut / ONE, "W is paid the node's half");
        require(usdc.balanceOf(n) == 0 && usdc.balanceOf(who("K1")) == 0, "N and K1 are paid nothing");
    }

    // --- helpers --------------------------------------------------------------

    function containsN(string[] memory l, uint256 n, string memory s) internal pure returns (bool) {
        for (uint256 i = 0; i < n; i++) {
            if (eq(l[i], s)) return true;
        }
        return false;
    }

    function contains(string[] memory l, string memory s) internal pure returns (bool) {
        for (uint256 i = 0; i < l.length; i++) {
            if (eq(l[i], s)) return true;
        }
        return false;
    }

    function endsWith(string memory s, string memory end) internal pure returns (bool) {
        bytes memory a = bytes(s);
        bytes memory b = bytes(end);
        if (a.length < b.length) return false;
        for (uint256 i = 0; i < b.length; i++) {
            if (a[a.length - b.length + i] != b[i]) return false;
        }
        return true;
    }

    function baseName(string memory path) internal pure returns (string memory) {
        bytes memory p = bytes(path);
        uint256 start = 0;
        for (uint256 i = 0; i < p.length; i++) {
            if (p[i] == "/") start = i + 1;
        }
        bytes memory out = new bytes(p.length - start);
        for (uint256 i = 0; i < out.length; i++) {
            out[i] = p[start + i];
        }
        return string(out);
    }

    /// `s` without its last `n` bytes.
    function trim(string memory s, uint256 n) internal pure returns (string memory) {
        bytes memory p = bytes(s);
        bytes memory out = new bytes(p.length - n);
        for (uint256 i = 0; i < out.length; i++) {
            out[i] = p[i];
        }
        return string(out);
    }
}
