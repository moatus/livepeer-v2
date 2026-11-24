// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {vm, Vm} from "./Vm.sol";
import {RewardsFixture} from "./RewardsFixture.sol";
import {BrokerWithTicketCount} from "./BrokerWithTicketCount.sol";
import {Broker} from "../src/money/Broker.sol";
import {Claims} from "../src/money/Claims.sol";
import {StakeLedger} from "../src/money/StakeLedger.sol";
import {Buyback} from "../src/money/Buyback.sol";
import {Registry} from "../src/authority/Registry.sol";
import {Parameters} from "../src/authority/Parameters.sol";
import {RoundClock} from "../src/clock/RoundClock.sol";
import {Scores} from "../src/scores/Scores.sol";
import {Upgrades} from "../src/authority/Upgrades.sol";
import {ITicketBroker} from "../interfaces/ITicketBroker.sol";
import {IUpgrades} from "../interfaces/IUpgrades.sol";
import {IParameters} from "../interfaces/IParameters.sol";
import {IScores} from "../interfaces/IScores.sol";
import {IBuybackExecutor} from "../interfaces/IBuybackExecutor.sol";

/// Code whose initialisation call is refused: X2 in upgrades-order.
contract RefusesInit {
    error INIT_REFUSED();

    function init() external pure {
        revert INIT_REFUSED();
    }
}

/// Every `upgrades` case of the shared examples, replayed against Upgrades
/// and the real contracts behind their proxies (rulebook R-27), Upgrades
/// reading the round through the clock's proxy; then the rules the cases do
/// not reach. A step's outcome is compared with the case's in one line: its
/// refusal's exact bytes, or what took effect.
contract UpgradesTest is RewardsFixture {
    /// ERC-1967's implementation slot.
    bytes32 internal constant IMPLEMENTATION = 0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc;

    string internal path;
    /// The round N claimed before the case's steps, so that a claim in a
    /// step can meet ALREADY_CLAIMED; 1 when none was.
    uint256 internal claimed;
    /// Each name of the case's `addresses`, and the address made for it.
    string[] internal madeNames;
    address[] internal made;

    function setUp() external {
        loadVectors("october-main-text-v6");
    }

    // --- the shared examples ------------------------------------------------

    function test_upgrades_broker_fix() external {
        replay("upgrades-broker-fix");
    }

    function test_upgrades_notice() external {
        replay("upgrades-notice");
    }

    function test_upgrades_stops() external {
        replay("upgrades-stops");
    }

    function test_upgrades_sides() external {
        replay("upgrades-sides");
    }

    function test_upgrades_order() external {
        replay("upgrades-order");
    }

    function test_upgrades_keys() external {
        replay("upgrades-keys");
    }

    function test_every_upgrades_case_is_run() external view {
        string[6] memory ran = [
            "upgrades-broker-fix",
            "upgrades-notice",
            "upgrades-stops",
            "upgrades-sides",
            "upgrades-order",
            "upgrades-keys"
        ];
        string[] memory ids = abi.decode(vm.parseJson(vectors, ".cases[*].id"), (string[]));
        string[] memory kinds = abi.decode(vm.parseJson(vectors, ".cases[*].kind"), (string[]));
        uint256 seen = 0;
        for (uint256 i = 0; i < ids.length; i++) {
            if (!eq(kinds[i], "upgrades")) continue;
            bool found = false;
            for (uint256 j = 0; j < ran.length; j++) {
                found = found || eq(ran[j], ids[i]);
            }
            require(found, string.concat("upgrades case not run: ", ids[i]));
            seen++;
        }
        require(seen == ran.length, "a case run here is not in the examples");
    }

    /// The whole system behind proxies, N registered and, from the first
    /// step's round, holding a claimed round; then each step in its round,
    /// in a call frame of its own.
    function replay(string memory id) internal {
        deploySystem();
        path = string.concat(".cases[", vm.toString(caseIndex(id)), "]");
        register("N");
        uint256 first = num(string.concat(path, ".inputs.steps[0]"), ".round");
        toRound(first);
        claimed = 1;
        uint256 delay = param("reward_delay_rounds");
        if (first > delay) {
            claimed = first - delay;
            expectDone(as_(who("N"), address(claims), abi.encodeCall(Claims.claim, (who("N"), claimed))), "N's claim");
        }
        for (uint256 i = 0;; i++) {
            string memory s = string.concat(path, ".inputs.steps[", vm.toString(i), "]");
            if (!vm.keyExistsJson(vectors, s)) break;
            toRound(num(s, ".round"));
            this.runStep(s, string.concat(id, " step ", str(s, ".step")));
        }
    }

    function runStep(string memory s, string memory where) external {
        require(msg.sender == address(this), "the runner only");
        string memory got = act(s, str(s, ".action"));
        string memory want = expected(str(s, ".step"));
        require(eq(got, want), string.concat(where, ": ", got, ", want ", want));
    }

    // --- rules the cases do not reach ------------------------------------------

    /// Only the pause key sets a stop: the upgrade key is refused for each
    /// side, so a change without notice needs two keys (R-27 rule 3).
    function test_the_upgrade_key_cannot_set_a_stop() external {
        deploySystem();
        for (uint256 s = 0; s < 3; s++) {
            IUpgrades.Side side_ = IUpgrades.Side(s);
            (bool ok, bytes memory ret) = send(who("U"), abi.encodeCall(IUpgrades.setStop, (side_)));
            require(!ok && bytes4(ret) == IUpgrades.NOT_AUTHORIZED.selector, "U setting a stop: NOT_AUTHORIZED");
            require(!upgrades.stopped(side_) && upgrades.stopEndsAt(side_) == 0, "no stop recorded");
        }
    }

    /// An announcement withdrawn leaves nothing to apply, at A + 7 or
    /// later, and the code in force as it was.
    function test_a_withdrawn_upgrade_is_never_applied() external {
        deploySystem();
        bytes32 before = vm.load(address(broker), IMPLEMENTATION);
        address next = newCode("Broker");
        vm.prank(who("U"));
        uint256 a = upgrades.announceUpgrade(address(broker), next, "");
        vm.recordLogs();
        vm.prank(who("U"));
        upgrades.withdrawUpgrade(address(broker));
        Vm.Log memory e = only(IUpgrades.UpgradeWithdrawn.selector);
        require(abi.decode(e.data, (address)) == next, "UpgradeWithdrawn names the code");
        (address code, bytes memory data, uint256 from, uint256 lapses) = upgrades.pending(address(broker));
        require(code == address(0) && data.length == 0 && from == 0 && lapses == 0, "nothing pending");
        uint256[2] memory rounds = [a, a + 20];
        for (uint256 i = 0; i < 2; i++) {
            toRound(rounds[i]);
            (bool ok, bytes memory ret) = send(who("U"), abi.encodeCall(IUpgrades.applyUpgrade, (address(broker))));
            require(!ok && bytes4(ret) == IUpgrades.NOTHING_PENDING.selector, "apply after withdrawal: NOTHING_PENDING");
        }
        require(vm.load(address(broker), IMPLEMENTATION) == before, "the code in force unchanged");
    }

    /// An announcement naming an address with no code is refused
    /// CODE_INVALID with that address, and nothing is pending.
    function test_an_announcement_of_an_address_without_code_is_refused() external {
        deploySystem();
        address empty = who("E");
        (bool ok, bytes memory ret) =
            send(who("U"), abi.encodeCall(IUpgrades.announceUpgrade, (address(broker), empty, "")));
        require(
            !ok && keccak256(ret) == keccak256(abi.encodeWithSelector(IUpgrades.CODE_INVALID.selector, empty)),
            "CODE_INVALID(E)"
        );
        (address code,,,) = upgrades.pending(address(broker));
        require(code == address(0), "nothing pending");
    }

    /// Parameters and RoundClock, on both sides, apply at once only while
    /// both stops are in force; Scores, on the LPT side, under the LPT stop
    /// (R-27 rule 3). Each is refused UPGRADE_NOT_READY under the other
    /// stops alone, and applied at once in the round it was announced under
    /// its own, its stored state kept.
    function test_both_sides_apply_at_once_only_under_both_stops() external {
        string[3] memory names = ["Parameters", "RoundClock", "Scores"];
        for (uint256 i = 0; i < names.length; i++) {
            deploySystem();
            toRound(5);
            address proxy = contractNamed(names[i]);
            bool lptAlone = eq(names[i], "Scores");
            address next = newCode(names[i]);
            vm.prank(who("U"));
            uint256 a = upgrades.announceUpgrade(proxy, next, "");
            bytes memory notReady = abi.encodeWithSelector(IUpgrades.UPGRADE_NOT_READY.selector, a);
            IUpgrades.Side[2] memory alone = [IUpgrades.Side.USDC, IUpgrades.Side.LPT];
            for (uint256 s = 0; s < 2; s++) {
                if (lptAlone && alone[s] == IUpgrades.Side.LPT) continue;
                uint256 snap = vm.snapshotState();
                vm.prank(who("S"));
                upgrades.setStop(alone[s]);
                (bool ok, bytes memory ret) = send(who("U"), abi.encodeCall(IUpgrades.applyUpgrade, (proxy)));
                require(!ok && keccak256(ret) == keccak256(notReady), string.concat(names[i], ": one side alone"));
                vm.revertToState(snap);
            }
            uint256 round = clock.currentRound();
            uint256 delay = params.valuesAt(4, round)[0];
            vm.prank(who("S"));
            upgrades.setStop(lptAlone ? IUpgrades.Side.LPT : IUpgrades.Side.BOTH);
            vm.recordLogs();
            vm.prank(who("U"));
            upgrades.applyUpgrade(proxy);
            require(eq(applied(proxy), "applied, under a stop true"), string.concat(names[i], ": applied at once"));
            require(clock.currentRound() == round && round < a, "in the round announced, before its notice ends");
            require(params.valuesAt(4, round)[0] == delay, "Parameters' values kept");
        }
    }

    /// Upgrades' creation refuses KEY_INVALID, with exactly that error's
    /// bytes, when U is zero, S is zero, or U and S are one key (R-27 rule
    /// 5): no Upgrades is left at the address.
    function test_creation_refuses_a_missing_or_shared_key() external {
        deploySystem();
        (address u, address s) = (who("U"), who("S"));
        address[2][3] memory pairs = [[address(0), s], [u, address(0)], [u, u]];
        for (uint256 i = 0; i < pairs.length; i++) {
            address at = vm.computeCreateAddress(address(this), vm.getNonce(address(this)));
            (bool ok, bytes memory ret) =
                address(this).call(abi.encodeCall(this.newUpgradesWith, (pairs[i][0], pairs[i][1])));
            require(
                !ok && keccak256(ret) == keccak256(abi.encodeWithSelector(IUpgrades.KEY_INVALID.selector)),
                "KEY_INVALID()"
            );
            require(at.code.length == 0, "no Upgrades left");
        }
    }

    function newUpgradesWith(address u, address s) external returns (address) {
        return address(new Upgrades(address(clock), u, s, proxies, sides));
    }

    /// Only the upgrade key removes the pause key: S itself, G and an
    /// outsider are refused NOT_AUTHORIZED, and the pause key and a pending
    /// naming are kept.
    function test_only_the_upgrade_key_removes_the_pause_key() external {
        deploySystem();
        vm.prank(who("U"));
        uint256 from = upgrades.namePauser(who("S2"));
        address[3] memory callers = [who("S"), who("G"), who("Outsider")];
        for (uint256 i = 0; i < callers.length; i++) {
            (bool ok, bytes memory ret) = send(callers[i], abi.encodeCall(IUpgrades.removePauser, ()));
            require(
                !ok && keccak256(ret) == keccak256(abi.encodeWithSelector(IUpgrades.NOT_AUTHORIZED.selector)),
                "removePauser: NOT_AUTHORIZED"
            );
            require(upgrades.pauser() == who("S"), "the pause key kept");
            (address next, uint256 counts) = upgrades.pauserNamed();
            require(next == who("S2") && counts == from, "the naming kept");
        }
        vm.prank(who("U"));
        upgrades.removePauser();
        require(upgrades.pauser() == address(0), "U removes it");
    }

    /// pending and sideOf refuse NOT_UPGRADEABLE, with the address, for an
    /// address that is not one of the proxies.
    function test_an_address_that_is_not_a_proxy_is_refused() external {
        deploySystem();
        bytes memory want = abi.encodeWithSelector(IUpgrades.NOT_UPGRADEABLE.selector, address(lpt));
        (bool ok, bytes memory ret) = address(upgrades).call(abi.encodeCall(IUpgrades.pending, (address(lpt))));
        require(!ok && keccak256(ret) == keccak256(want), "pending: NOT_UPGRADEABLE(address)");
        (ok, ret) = address(upgrades).call(abi.encodeCall(IUpgrades.sideOf, (address(lpt))));
        require(!ok && keccak256(ret) == keccak256(want), "sideOf: NOT_UPGRADEABLE(address)");
    }

    // --- one step ---------------------------------------------------------------

    function act(string memory s, string memory action) internal returns (string memory) {
        if (eq(action, "call")) return callStep(s);
        if (eq(action, "observe_keys")) {
            (address next, uint256 from) = upgrades.pauserNamed();
            return keys(upgrades.pauser(), next, from);
        }
        if (eq(action, "observe_pending")) {
            (address code, bytes memory data, uint256 a, uint256 l) =
                upgrades.pending(contractNamed(str(s, ".contract")));
            return pendingLine(code, data, a, l);
        }
        address caller = who(str(s, ".caller"));
        vm.recordLogs();
        vm.getRecordedLogs();
        bool ok;
        bytes memory ret;
        if (eq(action, "set_stop")) {
            (ok, ret) = send(caller, abi.encodeCall(IUpgrades.setStop, (side(str(s, ".side")))));
            if (ok) return string.concat("stop ends at ", vm.toString(abi.decode(ret, (uint256))));
        } else if (eq(action, "lift_stop")) {
            (ok, ret) = send(caller, abi.encodeCall(IUpgrades.liftStop, (side(str(s, ".side")))));
            if (ok) return lifted();
        } else if (eq(action, "announce_upgrade")) {
            string memory c = str(s, ".contract");
            bytes memory data = initCall(s);
            (ok, ret) = send(
                caller,
                abi.encodeCall(IUpgrades.announceUpgrade, (contractNamed(c), codeNamed(str(s, ".code"), c), data))
            );
            if (ok) return announced(abi.decode(ret, (uint256)));
        } else if (eq(action, "apply_upgrade")) {
            address proxy = contractNamed(str(s, ".contract"));
            (ok, ret) = send(caller, abi.encodeCall(IUpgrades.applyUpgrade, (proxy)));
            if (ok) return applied(proxy);
        } else if (eq(action, "withdraw_upgrade")) {
            (ok, ret) = send(caller, abi.encodeCall(IUpgrades.withdrawUpgrade, (contractNamed(str(s, ".contract")))));
        } else if (eq(action, "name_pauser")) {
            (ok, ret) = send(caller, abi.encodeCall(IUpgrades.namePauser, (named(str(s, ".next")))));
            if (ok) return string.concat("named, counts from ", vm.toString(abi.decode(ret, (uint256))));
        } else if (eq(action, "remove_pauser")) {
            (ok, ret) = send(caller, abi.encodeCall(IUpgrades.removePauser, ()));
        } else {
            revert(string.concat("unknown action ", action));
        }
        return ok ? "done" : refusal(ret);
    }

    /// A call on another contract: refused STOPPED, or past the stop check
    /// (taking effect or meeting a refusal of its own). A step that names
    /// the refusal it `meets` without the stop is also made with both stops
    /// lifted, and must meet it.
    function callStep(string memory s) internal returns (string memory) {
        (address caller, address target, bytes memory data) = callFor(s);
        bytes memory ret = as_(caller, target, data);
        string memory got = isStopped(ret) ? refusal(ret) : "done";
        string memory m = string.concat(s, ".meets");
        if (vm.keyExistsJson(vectors, m)) {
            uint256 snap = vm.snapshotState();
            vm.prank(who("U"));
            upgrades.liftStop(IUpgrades.Side.BOTH);
            bytes memory own = as_(caller, target, data);
            require(eq(code(own), str(s, ".meets")), string.concat("without the stop, it meets ", code(own)));
            vm.revertToState(snap);
        }
        return got;
    }

    function isStopped(bytes memory ret) internal pure returns (bool) {
        return keccak256(ret) == keccak256(abi.encodeWithSignature("STOPPED()"));
    }

    /// The step's call, as its caller makes it, with arguments that reach
    /// the contract's own checks.
    function callFor(string memory s) internal returns (address caller, address target, bytes memory data) {
        string memory c = str(s, ".contract");
        string memory fn = string.concat(c, ".", str(s, ".function"));
        caller = eq(str(s, ".caller"), "Buyback") ? address(buyback) : who(str(s, ".caller"));
        target = contractNamed(c);
        (address n, address q) = (who("N"), who("Q"));
        uint256[] memory rounds = new uint256[](1);
        rounds[0] = claimed;
        if (eq(fn, "Broker.fund")) {
            usdc.mint(caller, 1);
            vm.prank(caller);
            usdc.approve(address(broker), 1);
            data = abi.encodeCall(Broker.fund, (1, address(0), 0));
        } else if (eq(fn, "Broker.requestRelease")) {
            data = abi.encodeCall(Broker.requestRelease, (address(0), 0));
        } else if (eq(fn, "Broker.release")) {
            data = abi.encodeCall(Broker.release, (address(0)));
        } else if (eq(fn, "Broker.redeemWinningTicket")) {
            (ITicketBroker.Ticket memory t, bytes memory sig, uint256 rand) =
                ticket(string.concat(path, str(s, ".step")), "P", n, clock.currentRound(), 1);
            data = abi.encodeCall(Broker.redeemWinningTicket, (t, sig, rand));
        } else if (eq(fn, "Broker.drawNetwork")) {
            data = abi.encodeCall(Broker.drawNetwork, (0, caller));
        } else if (eq(fn, "StakeLedger.bond")) {
            data = abi.encodeCall(StakeLedger.bond, (n, 1));
        } else if (eq(fn, "StakeLedger.unbond")) {
            data = abi.encodeCall(StakeLedger.unbond, (n, 1));
        } else if (eq(fn, "StakeLedger.transferBond")) {
            data = abi.encodeCall(StakeLedger.transferBond, (n, q, 1));
        } else if (eq(fn, "StakeLedger.unbondTransfer")) {
            data = abi.encodeCall(StakeLedger.unbondTransfer, (n, q, 1));
        } else if (eq(fn, "StakeLedger.rebond")) {
            data = abi.encodeCall(StakeLedger.rebond, (n, 1));
        } else if (eq(fn, "StakeLedger.withdrawUnbonded")) {
            data = abi.encodeCall(StakeLedger.withdrawUnbonded, ());
        } else if (eq(fn, "StakeLedger.setRewardCut")) {
            data = abi.encodeCall(StakeLedger.setRewardCut, (n, 0));
        } else if (eq(fn, "Claims.claim")) {
            data = abi.encodeCall(Claims.claim, (n, claimed));
        } else if (eq(fn, "Claims.withdrawParts")) {
            data = abi.encodeCall(Claims.withdrawParts, (n, rounds));
        } else if (eq(fn, "Claims.mintRoundShares")) {
            data = abi.encodeCall(Claims.mintRoundShares, (clock.currentRound() - 1));
        } else if (eq(fn, "Claims.withdrawValidatorParts")) {
            data = abi.encodeCall(Claims.withdrawValidatorParts, (n, rounds));
        } else if (eq(fn, "Buyback.buy")) {
            data = abi.encodeCall(IBuybackExecutor.buy, ());
        } else if (eq(fn, "Buyback.poke")) {
            data = abi.encodeCall(IBuybackExecutor.poke, ());
        } else if (eq(fn, "Scores.vote")) {
            data = abi.encodeCall(IScores.vote, (caller, n, 1_000_000));
        } else if (eq(fn, "Scores.optIn")) {
            data = abi.encodeCall(IScores.optIn, (n));
        } else if (eq(fn, "Parameters.announce")) {
            // A fee change: group 1, announced as the values in force.
            data = abi.encodeCall(IParameters.announce, (1, params.valuesAt(1, clock.currentRound())));
        } else if (eq(fn, "RoundClock.initializeRound")) {
            data = abi.encodeCall(RoundClock.initializeRound, ());
        } else {
            revert(string.concat("no call for ", fn));
        }
    }

    // --- what took effect -------------------------------------------------------

    function send(address caller, bytes memory data) internal returns (bool ok, bytes memory ret) {
        vm.prank(caller);
        (ok, ret) = address(upgrades).call(data);
        require(ok || ret.length > 0, "reverted with no data");
    }

    function refusal(bytes memory ret) internal pure returns (string memory) {
        return string.concat("refused ", vm.toString(ret));
    }

    function announced(uint256 appliesFrom) internal returns (string memory) {
        Vm.Log memory e = only(IUpgrades.UpgradeAnnounced.selector);
        (,, uint256 a, uint256 l) = abi.decode(e.data, (address, bytes, uint256, uint256));
        require(a == appliesFrom, "announceUpgrade returns the event's appliesFrom");
        return string.concat("announced, applies from ", vm.toString(a), ", lapses at ", vm.toString(l));
    }

    /// The proxy now runs the code its UpgradeApplied names.
    function applied(address proxy) internal returns (string memory) {
        Vm.Log memory e = only(IUpgrades.UpgradeApplied.selector);
        (address code,, bool underStop) = abi.decode(e.data, (address, uint256, bool));
        require(vm.load(proxy, IMPLEMENTATION) == bytes32(uint256(uint160(code))), "the proxy runs the new code");
        return string.concat("applied, under a stop ", underStop ? "true" : "false");
    }

    function lifted() internal returns (string memory out) {
        Vm.Log[] memory logs = vm.getRecordedLogs();
        out = "lifted";
        for (uint256 i = 0; i < logs.length; i++) {
            if (logs[i].topics[0] != IUpgrades.StopLifted.selector) continue;
            out = string.concat(out, " ", uint256(logs[i].topics[1]) == 0 ? "USDC" : "LPT");
        }
    }

    /// The one log of `topic` the step emitted.
    function only(bytes32 topic) internal returns (Vm.Log memory found) {
        Vm.Log[] memory logs = vm.getRecordedLogs();
        uint256 n = 0;
        for (uint256 i = 0; i < logs.length; i++) {
            if (logs[i].topics[0] == topic) (found, n) = (logs[i], n + 1);
        }
        require(n == 1, "one event");
    }

    function keys(address pauser, address next, uint256 from) internal pure returns (string memory) {
        return string.concat("pauser ", vm.toString(pauser), ", named ", vm.toString(next), " from ", vm.toString(from));
    }

    function pendingLine(address code, bytes memory data, uint256 a, uint256 l) internal pure returns (string memory) {
        return string.concat(
            "pending ", vm.toString(code), " ", vm.toString(data), " from ", vm.toString(a), " to ", vm.toString(l)
        );
    }

    // --- what the case says ----------------------------------------------------

    /// The outcome the case gives `step`: from the list that names it, or
    /// "done".
    function expected(string memory step) internal returns (string memory) {
        string memory x = string.concat(path, ".expected");
        string memory e = entry(string.concat(x, ".refused"), step);
        if (bytes(e).length > 0) {
            if (vm.keyExistsJson(vectors, string.concat(e, ".by_call"))) {
                return refusal(abi.encodeWithSelector(RefusesInit.INIT_REFUSED.selector));
            }
            return refusal(refusedWith(e));
        }
        e = entry(string.concat(x, ".announced"), step);
        if (bytes(e).length > 0) {
            return
                string.concat("announced, applies from ", str(e, ".applies_from"), ", lapses at ", str(e, ".lapses_at"));
        }
        e = entry(string.concat(x, ".applied"), step);
        if (bytes(e).length > 0) {
            bool under = vm.parseJsonBool(vectors, string.concat(e, ".under_stop"));
            return string.concat("applied, under a stop ", under ? "true" : "false");
        }
        e = entry(string.concat(x, ".stops"), step);
        if (bytes(e).length > 0) return string.concat("stop ends at ", str(e, ".ends_at"));
        e = entry(string.concat(x, ".lifted"), step);
        if (bytes(e).length > 0) {
            string[] memory ls = abi.decode(vm.parseJson(vectors, string.concat(e, ".sides")), (string[]));
            string memory out = "lifted";
            for (uint256 i = 0; i < ls.length; i++) {
                out = string.concat(out, " ", ls[i]);
            }
            return out;
        }
        e = entry(string.concat(x, ".named"), step);
        if (bytes(e).length > 0) return string.concat("named, counts from ", str(e, ".counts_from"));
        e = entry(string.concat(x, ".observed"), step);
        if (bytes(e).length > 0) {
            if (vm.keyExistsJson(vectors, string.concat(e, ".pauser"))) {
                return keys(named(str(e, ".pauser")), named(str(e, ".named")), num(e, ".counts_from"));
            }
            string memory c = contractOfStep(step);
            bytes memory data = abi.encodeWithSignature(string.concat(str(e, ".call"), "()"));
            return pendingLine(codeNamed(str(e, ".code"), c), data, num(e, ".applies_from"), num(e, ".lapses_at"));
        }
        return "done";
    }

    /// The path of the entry of `list` for `step`; empty when none.
    function entry(string memory list, string memory step) internal view returns (string memory e) {
        for (uint256 i = 0;; i++) {
            e = string.concat(list, "[", vm.toString(i), "]");
            if (!vm.keyExistsJson(vectors, e)) return "";
            if (eq(str(e, ".step"), step)) return e;
        }
    }

    /// A refusal's exact bytes: its code, with its argument when it has one.
    function refusedWith(string memory e) internal view returns (bytes memory) {
        string memory c = str(e, ".code");
        if (!vm.keyExistsJson(vectors, string.concat(e, ".args"))) {
            return abi.encodeWithSignature(string.concat(c, "()"));
        }
        string memory arg = str(e, ".args[0]");
        if (eq(c, "NOT_UPGRADEABLE")) return abi.encodeWithSignature("NOT_UPGRADEABLE(address)", contractNamed(arg));
        if (eq(c, "UPGRADE_NOT_READY") || eq(c, "UPGRADE_LAPSED")) {
            return abi.encodeWithSignature(string.concat(c, "(uint256)"), vm.parseUint(arg));
        }
        revert(string.concat("no arguments known for ", c));
    }

    function contractOfStep(string memory step) internal view returns (string memory c) {
        for (uint256 i = 0;; i++) {
            string memory s = string.concat(path, ".inputs.steps[", vm.toString(i), "]");
            if (eq(str(s, ".step"), step)) return str(s, ".contract");
        }
    }

    // --- names -------------------------------------------------------------------

    function contractNamed(string memory c) internal view returns (address) {
        if (eq(c, "Broker")) return address(broker);
        if (eq(c, "Claims")) return address(claims);
        if (eq(c, "Registry")) return address(registry);
        if (eq(c, "StakeLedger")) return address(ledger);
        if (eq(c, "Scores")) return address(validatorSet);
        if (eq(c, "Parameters")) return address(params);
        if (eq(c, "RoundClock")) return address(clock);
        if (eq(c, "Buyback")) return address(buyback);
        if (eq(c, "TestLPT")) return address(lpt);
        revert(string.concat("no contract ", c));
    }

    /// The code an address of the case stands for, made the first time it is
    /// named: none for one without code; for one whose `init` is refused,
    /// RefusesInit; otherwise new code for `forContract`, with the wiring
    /// the code in force was built with.
    function codeNamed(string memory name, string memory forContract) internal returns (address) {
        for (uint256 i = 0; i < madeNames.length; i++) {
            if (eq(madeNames[i], name)) return made[i];
        }
        string memory e = string.concat(path, ".inputs.addresses.", name);
        address a;
        if (!vm.parseJsonBool(vectors, string.concat(e, ".code"))) {
            a = who(name);
        } else if (vm.keyExistsJson(vectors, string.concat(e, ".refuses"))) {
            a = address(new RefusesInit());
        } else {
            a = newCode(forContract);
        }
        madeNames.push(name);
        made.push(a);
        return a;
    }

    /// New code for a contract of the deployment, wired as the code in force.
    function newCode(string memory c) internal returns (address) {
        if (eq(c, "Broker")) return address(new BrokerWithTicketCount(usdc, registry, clock, params, upgradesAt));
        if (eq(c, "Claims")) {
            return address(
                new Claims(lpt, registry, clock, broker, ledger, params, validatorSet, treasuryAddress(), upgradesAt)
            );
        }
        if (eq(c, "Registry")) return address(new Registry(clock, address(broker)));
        if (eq(c, "StakeLedger")) return address(new StakeLedger(lpt, registry, clock, params, upgradesAt));
        if (eq(c, "Scores")) return address(new Scores(registry, clock, ledger, params));
        if (eq(c, "Parameters")) {
            return address(new Parameters(who("G"), clock, address(validatorSet), address(pool), address(buyback)));
        }
        if (eq(c, "RoundClock")) return address(new RoundClock());
        if (eq(c, "Buyback")) {
            return address(new Buyback(params, broker, clock, lpt, usdc, address(pool).codehash, upgradesAt));
        }
        revert(string.concat("no code for ", c));
    }

    function initCall(string memory s) internal view returns (bytes memory) {
        if (!vm.keyExistsJson(vectors, string.concat(s, ".call"))) return "";
        return abi.encodeWithSignature(string.concat(str(s, ".call"), "()"));
    }

    function side(string memory name) internal pure returns (IUpgrades.Side) {
        if (eq(name, "USDC")) return IUpgrades.Side.USDC;
        if (eq(name, "LPT")) return IUpgrades.Side.LPT;
        if (eq(name, "BOTH")) return IUpgrades.Side.BOTH;
        revert(string.concat("no side ", name));
    }

    function str(string memory at, string memory field) internal view returns (string memory) {
        return vm.parseJsonString(vectors, string.concat(at, field));
    }

    function num(string memory at, string memory field) internal view returns (uint256) {
        return vm.parseUint(str(at, field));
    }
}
