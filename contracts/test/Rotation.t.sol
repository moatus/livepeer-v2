// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {vm, Vm} from "./Vm.sol";
import {RewardsFixture} from "./RewardsFixture.sol";
import {IRegistry} from "../interfaces/IRegistry.sol";
import {Broker} from "../src/money/Broker.sol";
import {Claims} from "../src/money/Claims.sol";
import {MockUSDC} from "../src/money/MockUSDC.sol";
import {Scores} from "../src/scores/Scores.sol";
import {StakeLedger} from "../src/money/StakeLedger.sol";
import {TestLPT} from "../src/money/TestLPT.sol";
import {ITicketBroker} from "../interfaces/ITicketBroker.sol";

/// Key rotation (rulebook R-26, "the old key refused
/// everywhere"), on a rewards deployment with the real Scores.
///
/// Every public state-changing function of every part, read from the
/// compiler's ABI by its full signature, is on exactly one of six lists. The
/// tests of three of them:
///   current operator   the shared cases ledger-rotation and
///                      validators-rotation (Claims.t.sol, Scores.t.sol)
///   proposed operator  the shared case ledger-rotation, and
///                      test_proposal_events below
///   holder funds       test_holder_funds_move_only_the_callers_own (aimedAt
///                      builds a call for every entry)
contract RotationTest is RewardsFixture {
    uint256 internal constant ONE = 1_000_000;

    /// The six lists, each function named Part.signature, as the compiler's
    /// method identifiers name it.
    function currentOperator() internal pure returns (string[] memory l) {
        l = new string[](7);
        (l[0], l[1]) = ("Claims.claim(address,uint256)", "StakeLedger.setRewardCut(address,uint256)");
        (l[2], l[3]) = ("Registry.setPayout(address,address)", "Registry.setOperator(address,address)");
        (l[4], l[5], l[6]) = ("Scores.optIn(address)", "Scores.optOut(address)", "Scores.vote(address,address,uint256)");
    }

    function proposedOperator() internal pure returns (string[] memory l) {
        l = new string[](1);
        l[0] = "Registry.acceptOperator(address)";
    }

    /// Moves only the caller's own bond, deposit, parts or tokens, or tokens
    /// it was approved for; naming a node chooses where, never whose.
    function holderFunds() internal pure returns (string[] memory l) {
        l = new string[](18);
        (l[0], l[1]) = ("StakeLedger.bond(address,uint256)", "StakeLedger.unbond(address,uint256)");
        (l[2], l[3]) =
        ("StakeLedger.transferBond(address,address,uint256)", "StakeLedger.unbondTransfer(address,address,uint256)");
        (l[4], l[5]) = ("StakeLedger.rebond(address,uint256)", "StakeLedger.withdrawUnbonded()");
        (l[6], l[7]) = ("Claims.withdrawParts(address,uint256[])", "Claims.withdrawValidatorParts(address,uint256[])");
        (l[8], l[9], l[10]) =
        ("Broker.fund(uint256,address,uint256)", "Broker.requestRelease(address,uint256)", "Broker.release(address)");
        (l[11], l[12]) = ("TestLPT.approve(address,uint256)", "TestLPT.transfer(address,uint256)");
        (l[13], l[14]) = ("TestLPT.transferFrom(address,address,uint256)", "TestLPT.burn(uint256)");
        (l[15], l[16]) = ("MockUSDC.approve(address,uint256)", "MockUSDC.transfer(address,uint256)");
        l[17] = "MockUSDC.transferFrom(address,address,uint256)";
    }

    /// Any address; the effect is fixed by rule. The clock's initialize is
    /// refused once a round has started, and a proxy's fallback runs the
    /// code in force, whose own calls are listed here.
    function permissionless() internal pure returns (string[] memory l) {
        l = new string[](12);
        (l[0], l[1], l[2]) = ("Registry.register()", "Scores.seat(address)", "Scores.unseat(address)");
        (l[3], l[4]) = ("Claims.mintRoundShares(uint256)", "RoundClock.initializeRound()");
        l[5] = "Broker.redeemWinningTicket((address,address,uint256,uint256,uint256,bytes32,bytes),bytes,uint256)";
        (l[6], l[7]) = ("Buyback.buy()", "Buyback.poke()");
        (l[8], l[9]) = ("TestPool.mint(address)", "TestPool.swap(uint256,uint256,address,bytes)");
        (l[10], l[11]) = ("RoundClock.initialize()", "Proxy.fallback()");
    }

    /// The parameter authority's calls, and the upgrade and pause keys'
    /// (R-25, R-27).
    function authority() internal pure returns (string[] memory l) {
        l = new string[](9);
        (l[0], l[1]) = ("Parameters.announce(uint256,uint256[])", "Parameters.withdraw(uint256)");
        (l[2], l[3]) = ("Upgrades.announceUpgrade(address,address,bytes)", "Upgrades.applyUpgrade(address)");
        (l[4], l[5]) = ("Upgrades.withdrawUpgrade(address)", "Upgrades.liftStop(uint8)");
        (l[6], l[7]) = ("Upgrades.removePauser()", "Upgrades.namePauser(address)");
        l[8] = "Upgrades.setStop(uint8)";
    }

    /// A caller fixed at deployment, or open on a test token: the mints;
    /// and the draw of the network share, by the executor group 10 names
    /// for the round.
    function restricted() internal pure returns (string[] memory l) {
        l = new string[](3);
        (l[0], l[1]) = ("TestLPT.mint(address,uint256)", "MockUSDC.mint(address,uint256)");
        l[2] = "Broker.drawNetwork(uint256,address)";
    }

    function lists() internal pure returns (string[][6] memory all, string[6] memory names) {
        all = [currentOperator(), proposedOperator(), holderFunds(), permissionless(), authority(), restricted()];
        names = ["current operator", "proposed operator", "holder funds", "permissionless", "authority", "restricted"];
    }

    // --- the six lists against the compiler's ABI ------------------------------

    /// Every part in src/, every function of its compiled ABI that is not
    /// view or pure, by its full signature, is on exactly one list; so is a
    /// fallback or receive function, named Part.fallback() or
    /// Part.receive(). Every listed function exists. A part, function or
    /// overload added later on no list fails here.
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
    /// neither view nor pure, named Part.signature, and its fallback and
    /// receive functions. Each signature built from the ABI must be one of
    /// the part's method identifiers.
    function stateChanging(string memory path, string[] memory seen, uint256 found) internal view returns (uint256) {
        string memory part = trim(baseName(path), 5);
        string memory json = vm.readFile(path);
        string[] memory ids = vm.keyExistsJson(json, ".methodIdentifiers")
            ? vm.parseJsonKeys(json, ".methodIdentifiers")
            : new string[](0);
        for (uint256 i = 0; vm.keyExistsJson(json, string.concat(".abi[", vm.toString(i), "]")); i++) {
            string memory e = string.concat(".abi[", vm.toString(i), "]");
            string memory kind = vm.parseJsonString(json, string.concat(e, ".type"));
            string memory fn;
            if (eq(kind, "fallback") || eq(kind, "receive")) {
                fn = string.concat(part, ".", kind, "()");
            } else if (eq(kind, "function")) {
                string memory m = vm.parseJsonString(json, string.concat(e, ".stateMutability"));
                if (eq(m, "view") || eq(m, "pure")) continue;
                string memory sig = signature(json, e);
                require(contains(ids, sig), string.concat(part, ": built signature ", sig, " is no method identifier"));
                fn = string.concat(part, ".", sig);
            } else {
                continue;
            }
            if (containsN(seen, found, fn)) continue;
            seen[found++] = fn;
        }
        return found;
    }

    /// The canonical signature of the ABI function at `e`: name(types).
    function signature(string memory json, string memory e) internal view returns (string memory) {
        return
            string.concat(vm.parseJsonString(json, string.concat(e, ".name")), types(json, string.concat(e, ".inputs")));
    }

    /// "(t1,t2,...)" for the parameter list at `at`; a tuple is its
    /// components, recursively, with any array suffix kept.
    function types(string memory json, string memory at) internal view returns (string memory out) {
        out = "(";
        for (uint256 j = 0;; j++) {
            string memory p = string.concat(at, "[", vm.toString(j), "]");
            if (!vm.keyExistsJson(json, p)) break;
            string memory t = vm.parseJsonString(json, string.concat(p, ".type"));
            if (startsWith(t, "tuple")) {
                t = string.concat(types(json, string.concat(p, ".components")), suffix(t, 5));
            }
            out = string.concat(out, j == 0 ? "" : ",", t);
        }
        out = string.concat(out, ")");
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
        loadVectors("october-main-text-v5");
        deployRewardsWithScores();
    }

    /// N proposes K1 and K1 accepts: K1 is N's operator key.
    function rotateToK1() internal {
        register("N");
        expectDone(setOperatorAs(who("N"), who("N"), who("K1")), "N proposes K1");
        expectDone(acceptOperatorAs(who("K1"), who("N")), "K1 accepts");
        require(registry.operatorOf(who("N")) == who("K1"), "K1 is N's operator");
    }

    /// The events of a cleared and of a used proposal: proposing the
    /// current key emits one OperatorProposed naming no key, and an
    /// acceptance one OperatorSet naming the node and its new key.
    function test_proposal_events() external {
        rotateToK1();
        address n = who("N");
        expectDone(setOperatorAs(who("K1"), n, who("K2")), "K1 proposes K2");
        vm.recordLogs();
        expectDone(setOperatorAs(who("K1"), n, who("K1")), "K1 proposes itself");
        Vm.Log[] memory logs = vm.getRecordedLogs();
        require(logs.length == 1 && logs[0].topics[0] == IRegistry.OperatorProposed.selector, "one OperatorProposed");
        require(logs[0].topics[2] == bytes32(0), "the event names no proposed key");
        expectDone(setOperatorAs(who("K1"), n, who("K2")), "K1 proposes K2 again");
        vm.recordLogs();
        expectDone(acceptOperatorAs(who("K2"), n), "K2 accepts");
        logs = vm.getRecordedLogs();
        require(logs.length == 1 && logs[0].topics[0] == IRegistry.OperatorSet.selector, "one OperatorSet");
        require(logs[0].topics[1] == bytes32(uint256(uint160(n))), "OperatorSet names N");
        require(logs[0].topics[2] == bytes32(uint256(uint160(who("K2")))), "OperatorSet names K2");
    }

    /// R-26's order for setPayout: NOT_AUTHORIZED, NOT_REGISTERED,
    /// PAYOUT_INVALID, with mayActFor's answer.
    function test_refusal_order_set_payout() external {
        rotateToK1();
        address n = who("N");
        address x = who("X");
        expectRefused(setPayoutAs(who("K1"), x, address(broker)), "NOT_AUTHORIZED", "K1 for X, the Broker");
        expectRefused(setPayoutAs(x, x, address(broker)), "NOT_REGISTERED", "X for itself, the Broker");
        expectRefused(setPayoutAs(n, n, address(broker)), "NOT_AUTHORIZED", "N, rotated, the Broker");
        expectRefused(setPayoutAs(who("K1"), n, address(broker)), "PAYOUT_INVALID", "K1 sets the Broker");
    }

    // --- holder funds ---------------------------------------------------------

    /// The call `fn` from the holder-funds list, made by N's operator key K2,
    /// which holds nothing, aimed at what N's own address holds: N's bond to
    /// N, its transfer to Q, its unbonds, its parts of round 3, its earmark
    /// for N and its tokens. Every entry on the list must have one.
    function aimedAt(string memory fn, address n, address q, address k) internal view returns (address, bytes memory) {
        uint256[] memory rounds = new uint256[](1);
        rounds[0] = 3;
        if (eq(fn, "StakeLedger.bond(address,uint256)")) {
            return (address(ledger), abi.encodeCall(StakeLedger.bond, (n, 1e18)));
        }
        if (eq(fn, "StakeLedger.unbond(address,uint256)")) {
            return (address(ledger), abi.encodeCall(StakeLedger.unbond, (n, 1e18)));
        }
        if (eq(fn, "StakeLedger.transferBond(address,address,uint256)")) {
            return (address(ledger), abi.encodeCall(StakeLedger.transferBond, (n, q, 1e18)));
        }
        if (eq(fn, "StakeLedger.unbondTransfer(address,address,uint256)")) {
            return (address(ledger), abi.encodeCall(StakeLedger.unbondTransfer, (n, q, 1e18)));
        }
        if (eq(fn, "StakeLedger.rebond(address,uint256)")) {
            return (address(ledger), abi.encodeCall(StakeLedger.rebond, (n, 1e18)));
        }
        if (eq(fn, "StakeLedger.withdrawUnbonded()")) {
            return (address(ledger), abi.encodeCall(StakeLedger.withdrawUnbonded, ()));
        }
        if (eq(fn, "Claims.withdrawParts(address,uint256[])")) {
            return (address(claims), abi.encodeCall(Claims.withdrawParts, (n, rounds)));
        }
        if (eq(fn, "Claims.withdrawValidatorParts(address,uint256[])")) {
            return (address(claims), abi.encodeCall(Claims.withdrawValidatorParts, (n, rounds)));
        }
        if (eq(fn, "Broker.fund(uint256,address,uint256)")) {
            return (address(broker), abi.encodeCall(Broker.fund, (1, n, 1)));
        }
        if (eq(fn, "Broker.requestRelease(address,uint256)")) {
            return (address(broker), abi.encodeCall(Broker.requestRelease, (n, 1)));
        }
        if (eq(fn, "Broker.release(address)")) return (address(broker), abi.encodeCall(Broker.release, (n)));
        if (eq(fn, "TestLPT.approve(address,uint256)")) {
            return (address(lpt), abi.encodeCall(TestLPT.approve, (n, 1e18)));
        }
        if (eq(fn, "TestLPT.transfer(address,uint256)")) {
            return (address(lpt), abi.encodeCall(TestLPT.transfer, (n, 1e18)));
        }
        if (eq(fn, "TestLPT.transferFrom(address,address,uint256)")) {
            return (address(lpt), abi.encodeCall(TestLPT.transferFrom, (n, k, 1e18)));
        }
        if (eq(fn, "TestLPT.burn(uint256)")) return (address(lpt), abi.encodeCall(TestLPT.burn, (1e18)));
        if (eq(fn, "MockUSDC.approve(address,uint256)")) {
            return (address(usdc), abi.encodeCall(MockUSDC.approve, (n, 1)));
        }
        if (eq(fn, "MockUSDC.transfer(address,uint256)")) {
            return (address(usdc), abi.encodeCall(MockUSDC.transfer, (n, 1)));
        }
        if (eq(fn, "MockUSDC.transferFrom(address,address,uint256)")) {
            return (address(usdc), abi.encodeCall(MockUSDC.transferFrom, (n, k, 1)));
        }
        revert(string.concat("no call built for ", fn));
    }

    /// What N's own address holds and has approved, and what K2 holds.
    function holdings(address n, address q, address k) internal view returns (uint256[] memory h) {
        h = new uint256[](24);
        h[0] = ledger.stayingOf(n, n);
        h[1] = ledger.inTransferOf(n, n, q);
        (h[2], h[3]) = ledger.unbondedOf(n);
        h[4] = ledger.rebondableOf(n, n);
        h[5] = lpt.balanceOf(n);
        h[6] = usdc.balanceOf(n);
        h[7] = lpt.allowance(n, address(ledger));
        h[8] = usdc.allowance(n, address(broker));
        h[9] = lpt.allowance(n, k);
        h[10] = usdc.allowance(n, k);
        (h[11], h[12], h[13]) = broker.earmarkOf(n, address(0));
        (h[14], h[15], h[16]) = broker.earmarkOf(n, n);
        (, bool w) = claims.partOf(n, n, 3);
        h[17] = w ? 1 : 0;
        (, w) = claims.validatorPartOf(n, n, 3);
        h[18] = w ? 1 : 0;
        h[19] = ledger.stayingOf(k, n);
        h[20] = lpt.balanceOf(k);
        h[21] = usdc.balanceOf(k);
        (h[22],,) = broker.earmarkOf(k, n);
        h[23] = lpt.totalSupply();
    }

    /// N's own address holds something real for every holder-funds entry:
    /// a bond to N, a transfer of part of it to Q in flight, an unbond ready
    /// and one waiting, both its parts of round 3 unwithdrawn, a deposit, an
    /// earmark for N with a release ready, LPT and USDC, and approvals left
    /// to the ledger and the Broker. Then N rotates to K2. K2, now acting for
    /// N, calls every holder-funds function aimed at those holdings: nothing
    /// of N's moves and K2 gains nothing. Then N's own address moves each of
    /// them, so each was there to move.
    function test_holder_funds_move_only_the_callers_own() external {
        address n = who("N");
        address k = who("K2");
        address q = who("Q");
        holdEverything(n, q, k);
        uint256[] memory before = holdings(n, q, k);
        // Each holding to move: bond, transfer, unbonds waiting and ready,
        // rebondable, LPT, USDC, approvals to the ledger and the Broker, the
        // deposit, the earmark for N with its release pending and ready.
        uint8[13] memory held = [0, 1, 2, 3, 4, 5, 6, 7, 8, 11, 14, 15, 16];
        for (uint256 j = 0; j < held.length; j++) {
            require(before[held[j]] > 0, string.concat("N's address holds nothing at ", vm.toString(held[j])));
        }
        require(before[16] <= clock.currentRound(), "N's release is ready");
        (uint256 part,) = claims.partOf(n, n, 3);
        (uint256 vpart,) = claims.validatorPartOf(n, n, 3);
        require(part > 0 && vpart > 0, "N's parts of round 3");

        string[] memory list = holderFunds();
        for (uint256 i = 0; i < list.length; i++) {
            (address target, bytes memory data) = aimedAt(list[i], n, q, k);
            as_(k, target, data);
            unchanged(list[i], before, holdings(n, q, k));
        }
        moveOwn(n, q, part, vpart);
    }

    /// The holdings of the holder-funds test, then N rotates to K2.
    function holdEverything(address n, address q, address k) internal {
        uint256 wait = params.valuesAt(7, 1)[0];
        register("N");
        register("Q");
        lpt.transfer(n, 1000e18);
        expectDone(as_(n, address(lpt), abi.encodeCall(TestLPT.approve, (address(ledger), 700e18))), "N approves");
        expectDone(as_(n, address(ledger), abi.encodeCall(StakeLedger.bond, (n, 600e18))), "N bonds to itself");
        expectDone(as_(n, address(ledger), abi.encodeCall(StakeLedger.unbond, (n, 50e18))), "N unbonds, round 1");
        usdc.mint(n, 10 * ONE);
        expectDone(as_(n, address(usdc), abi.encodeCall(MockUSDC.approve, (address(broker), 6 * ONE))), "N approves");
        expectDone(as_(n, address(broker), abi.encodeCall(Broker.fund, (2 * ONE, n, 3 * ONE))), "N funds");
        expectDone(fund("P", 10 * ONE, address(0), 0), "P funds");
        toRound(2);
        expectDone(as_(n, address(validatorSet), abi.encodeCall(Scores.optIn, (n))), "N opts in");
        toRound(3);
        (ITicketBroker.Ticket memory t, bytes memory sig, uint256 rand) = ticket("t1", "P", n, 3, ONE);
        expectDone(redeem(t, sig, rand), "N's node redeems");
        toRound(4);
        expectDone(as_(who("Keeper"), address(claims), abi.encodeCall(Claims.mintRoundShares, (3))), "round 3's shares");
        toRound(3 + params.valuesAt(4, 3)[0]);
        expectDone(as_(n, address(claims), abi.encodeCall(Claims.claim, (n, 3))), "N claims round 3");
        uint256 now_ = clock.currentRound();
        expectDone(as_(n, address(ledger), abi.encodeCall(StakeLedger.unbond, (n, 40e18))), "N unbonds again");
        expectDone(as_(n, address(ledger), abi.encodeCall(StakeLedger.transferBond, (n, q, 30e18))), "N to Q");
        expectDone(as_(n, address(broker), abi.encodeCall(Broker.requestRelease, (n, ONE))), "N asks a release");
        expectDone(setOperatorAs(n, n, k), "N proposes K2");
        expectDone(acceptOperatorAs(k, n), "K2 accepts");
        (,, uint256 releaseAt) = broker.earmarkOf(n, n);
        toRound(1 + wait > releaseAt ? 1 + wait : releaseAt);
        require(clock.currentRound() < now_ + wait, "the second unbond and the transfer are still waiting");
    }

    function unchanged(string memory fn, uint256[] memory before, uint256[] memory now_) internal pure {
        for (uint256 j = 0; j < now_.length; j++) {
            require(now_[j] == before[j], string.concat(fn, " by K2 moved what N's address holds, at ", vm.toString(j)));
        }
    }

    /// N's own address moves each holding.
    function moveOwn(address n, address q, uint256 part, uint256 vpart) internal {
        uint256 lptBefore = lpt.balanceOf(n);
        expectDone(as_(n, address(ledger), abi.encodeCall(StakeLedger.withdrawUnbonded, ())), "N withdraws");
        uint256[] memory r3 = new uint256[](1);
        r3[0] = 3;
        expectDone(as_(n, address(claims), abi.encodeCall(Claims.withdrawParts, (n, r3))), "N's node part");
        expectDone(
            as_(n, address(claims), abi.encodeCall(Claims.withdrawValidatorParts, (n, r3))), "N's validator part"
        );
        expectEq(lpt.balanceOf(n) - lptBefore, 50e18 + part + vpart, "N is paid its unbond and both parts");
        expectDone(as_(n, address(ledger), abi.encodeCall(StakeLedger.rebond, (n, 40e18))), "N rebonds");
        expectDone(as_(n, address(ledger), abi.encodeCall(StakeLedger.unbondTransfer, (n, q, 30e18))), "N's transfer");
        expectDone(as_(n, address(ledger), abi.encodeCall(StakeLedger.bond, (n, 100e18))), "N bonds its approval");
        expectEq(ledger.stayingOf(n, n), 600e18 - 50e18 - 30e18 + 100e18, "N's bond");
        uint256 usdcBefore = usdc.balanceOf(n);
        expectDone(as_(n, address(broker), abi.encodeCall(Broker.release, (n))), "N releases");
        expectEq(usdc.balanceOf(n) - usdcBefore, ONE, "N is paid its release");
        expectDone(as_(n, address(broker), abi.encodeCall(Broker.fund, (ONE / 2, n, ONE / 2))), "N funds its approval");
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

    function startsWith(string memory s, string memory start) internal pure returns (bool) {
        bytes memory a = bytes(s);
        bytes memory b = bytes(start);
        if (a.length < b.length) return false;
        for (uint256 i = 0; i < b.length; i++) {
            if (a[i] != b[i]) return false;
        }
        return true;
    }

    /// `s` from byte `n` on.
    function suffix(string memory s, uint256 n) internal pure returns (string memory) {
        bytes memory p = bytes(s);
        bytes memory out = new bytes(p.length - n);
        for (uint256 i = 0; i < out.length; i++) {
            out[i] = p[n + i];
        }
        return string(out);
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
