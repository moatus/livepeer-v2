// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {vm, Vm} from "./Vm.sol";
import {RewardsFixture} from "./RewardsFixture.sol";
import {RefusesInit} from "./Upgrades.t.sol";
import {Proxy} from "../src/authority/Proxy.sol";
import {RoundClock} from "../src/clock/RoundClock.sol";
import {IProxy} from "../interfaces/IProxy.sol";
import {IRoundClock} from "../interfaces/IRoundClock.sol";

/// Test code with a function of its own whose selector is upgradeToAndCall's:
/// it answers with what it was called with, or refuses with it.
contract SelectorTwin {
    error TWIN_REFUSED(address sender, uint256 value, bytes data);

    function upgradeToAndCall(address implementation, bytes calldata) external payable returns (bytes memory) {
        if (implementation == address(0xdead)) revert TWIN_REFUSED(msg.sender, msg.value, msg.data);
        return abi.encode(msg.sender, msg.value, msg.data);
    }
}

/// Code that refuses every call, so that a proxy which called it would
/// revert.
contract Tripwire {
    fallback() external payable {
        revert("called");
    }
}

/// Test code that writes any address into the proxy's ERC-1967 admin slot.
contract AdminSlotWriter {
    bytes32 private constant ADMIN = 0xb53127684a568b3173ae13b9f8a6016e243e63b6e8ee1178d6a717850b5d6103;

    function writeAdmin(address a) external {
        assembly {
            sstore(ADMIN, a)
        }
    }
}

/// The proxy (rulebook R-27, IProxy): only its admin's upgradeToAndCall is
/// its own; every other call, a colliding selector included, runs the code
/// in force with its calldata, value and bytes unchanged. Its refusals and
/// the clock's one start.
contract ProxyTest is RewardsFixture {
    bytes32 internal constant IMPLEMENTATION = 0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc;
    bytes32 internal constant ADMIN = 0xb53127684a568b3173ae13b9f8a6016e243e63b6e8ee1178d6a717850b5d6103;

    function setUp() external {
        loadVectors("october-main-text-v6");
        deploySystem();
    }

    // --- the dispatch -------------------------------------------------------------

    /// The twin behind a proxy whose admin is Upgrades, called with
    /// upgradeToAndCall's selector by U, S, G and an outsider, with value:
    /// each call runs the twin, which sees the caller, value and calldata
    /// unchanged, and returns or refuses with its own bytes; the code in
    /// force stays the twin.
    function test_a_colliding_selector_runs_the_code_in_force() external {
        SelectorTwin twin = new SelectorTwin();
        address proxy = address(new Proxy(address(twin), address(upgrades), ""));
        address[4] memory callers = [who("U"), who("S"), who("G"), who("Outsider")];
        for (uint256 i = 0; i < callers.length; i++) {
            vm.deal(callers[i], 1 ether);
            bytes memory data = abi.encodeCall(IProxy.upgradeToAndCall, (address(this), abi.encode(i)));
            vm.prank(callers[i]);
            (bool ok, bytes memory ret) = proxy.call{value: 7}(data);
            require(ok, "the twin answers");
            require(keccak256(ret) == keccak256(abi.encode(abi.encode(callers[i], 7, data))), "its bytes unchanged");

            data = abi.encodeCall(IProxy.upgradeToAndCall, (address(0xdead), abi.encode(i)));
            vm.prank(callers[i]);
            (ok, ret) = proxy.call{value: 3}(data);
            require(!ok, "the twin refuses");
            bytes memory want = abi.encodeWithSelector(SelectorTwin.TWIN_REFUSED.selector, callers[i], 3, data);
            require(keccak256(ret) == keccak256(want), "its refusal's bytes unchanged");
            require(vm.load(proxy, IMPLEMENTATION) == slot(address(twin)), "the twin still in force");
        }
        require(proxy.balance == 28, "the value went where the twin's code runs");
    }

    /// From any address but Upgrades, upgradeToAndCall on each of the
    /// deployment's proxies runs the code in force (which has no such
    /// function and refuses), and no proxy's code changes.
    function testFuzz_only_upgrades_replaces_code(address caller) external {
        if (caller == address(upgrades)) return;
        address other = address(new Tripwire());
        for (uint256 i = 0; i < proxies.length; i++) {
            bytes32 before = vm.load(proxies[i], IMPLEMENTATION);
            vm.prank(caller);
            (bool ok,) = proxies[i].call(abi.encodeCall(IProxy.upgradeToAndCall, (other, "")));
            require(!ok, "the code in force has no upgradeToAndCall");
            require(vm.load(proxies[i], IMPLEMENTATION) == before, "the code in force unchanged");
        }
        require(proxies.length == 8, "eight proxies");
    }

    /// The admin's call is the proxy's own: Upgrades' upgradeToAndCall puts
    /// the code in force and emits Upgraded, with no notice of its own.
    function test_the_admins_call_is_the_proxys_own() external {
        SelectorTwin twin = new SelectorTwin();
        address proxy = address(new Proxy(address(twin), address(upgrades), ""));
        address next = address(new RefusesInit());
        vm.recordLogs();
        vm.prank(address(upgrades));
        (bool ok, bytes memory ret) = proxy.call(abi.encodeCall(IProxy.upgradeToAndCall, (next, "")));
        require(ok && ret.length == 0, "the proxy's own call returns nothing");
        require(vm.load(proxy, IMPLEMENTATION) == slot(next), "the new code in force");
        upgradedOnce(proxy, next);
    }

    /// The proxy authorises against the admin fixed in its code, not the
    /// slot's copy: code in force that writes another address into the
    /// admin slot gives that address no upgrade (its call runs the code in
    /// force), and Upgrades still replaces the code.
    function test_the_admin_is_the_fixed_one_not_the_slots_copy() external {
        address writer = address(new AdminSlotWriter());
        address proxy = address(new Proxy(writer, address(upgrades), ""));
        address usurper = who("Usurper");
        AdminSlotWriter(proxy).writeAdmin(usurper);
        require(vm.load(proxy, ADMIN) == slot(usurper), "the slot now names another address");
        address other = address(new Tripwire());
        vm.prank(usurper);
        (bool ok,) = proxy.call(abi.encodeCall(IProxy.upgradeToAndCall, (other, "")));
        require(!ok, "the slot's address runs the code in force, which has no upgradeToAndCall");
        require(vm.load(proxy, IMPLEMENTATION) == slot(writer), "the code in force unchanged");
        vm.prank(address(upgrades));
        (ok,) = proxy.call(abi.encodeCall(IProxy.upgradeToAndCall, (other, "")));
        require(ok && vm.load(proxy, IMPLEMENTATION) == slot(other), "Upgrades still replaces the code");
    }

    // --- creation and replacement --------------------------------------------------

    /// A proxy writes both ERC-1967 slots and emits Upgraded at creation.
    function test_creation_writes_both_slots() external {
        address code = address(new SelectorTwin());
        vm.recordLogs();
        address proxy = address(new Proxy(code, who("Admin"), ""));
        require(vm.load(proxy, IMPLEMENTATION) == slot(code), "implementation slot");
        require(vm.load(proxy, ADMIN) == slot(who("Admin")), "admin slot");
        upgradedOnce(proxy, code);
        require(vm.load(address(broker), ADMIN) == slot(address(upgrades)), "the deployment's admin is Upgrades");
    }

    /// CODE_INVALID for an address without code: at creation, leaving no
    /// proxy, and on replacement, leaving the code in force.
    function test_code_without_code_is_refused() external {
        address empty = who("E");
        address at = ahead(0);
        (bool ok, bytes memory ret) = address(this).call(abi.encodeCall(this.newProxy, (empty, who("Admin"), "")));
        require(!ok && keccak256(ret) == keccak256(codeInvalid(empty)), "creation: CODE_INVALID(E)");
        require(at.code.length == 0, "no proxy");

        address code = address(new SelectorTwin());
        address proxy = address(new Proxy(code, address(this), ""));
        (ok, ret) = proxy.call(abi.encodeCall(IProxy.upgradeToAndCall, (empty, "")));
        require(!ok && keccak256(ret) == keccak256(codeInvalid(empty)), "replacement: CODE_INVALID(E)");
        require(vm.load(proxy, IMPLEMENTATION) == slot(code), "the code in force unchanged");
    }

    /// An initialisation call that reverts reverts the creation with its
    /// bytes, leaving no proxy, or the replacement, leaving the code in
    /// force and no Upgraded.
    function test_a_refused_initialisation_reverts_with_its_bytes() external {
        address refuses = address(new RefusesInit());
        bytes memory init = abi.encodeCall(RefusesInit.init, ());
        bytes memory want = abi.encodeWithSelector(RefusesInit.INIT_REFUSED.selector);
        address at = ahead(0);
        (bool ok, bytes memory ret) = address(this).call(abi.encodeCall(this.newProxy, (refuses, who("Admin"), init)));
        require(!ok && keccak256(ret) == keccak256(want), "creation: the call's bytes");
        require(at.code.length == 0, "no proxy");

        address code = address(new SelectorTwin());
        address proxy = address(new Proxy(code, address(this), ""));
        vm.recordLogs();
        (ok, ret) = proxy.call(abi.encodeCall(IProxy.upgradeToAndCall, (refuses, init)));
        require(!ok && keccak256(ret) == keccak256(want), "replacement: the call's bytes");
        require(vm.load(proxy, IMPLEMENTATION) == slot(code), "the code in force unchanged");
        require(vm.getRecordedLogs().length == 0, "no Upgraded");
    }

    /// Empty data calls nothing: code that refuses every call is put in
    /// force at creation and on replacement, and a clock created with no
    /// call is not started.
    function test_empty_data_calls_nothing() external {
        address trip = address(new Tripwire());
        address proxy = address(new Proxy(trip, address(this), ""));
        (bool ok,) = proxy.call(abi.encodeCall(IProxy.upgradeToAndCall, (address(new Tripwire()), "")));
        require(ok, "replaced with empty data");
        address unstarted = address(new Proxy(address(new RoundClock()), address(this), ""));
        require(IRoundClock(unstarted).currentRound() == 0, "the clock not started");
    }

    // --- the clock's one start -----------------------------------------------------

    /// A second initialize is refused ALREADY_STARTED on the clock's proxy and
    /// on its code, the round unchanged on each.
    function test_the_clock_starts_once() external {
        toRound(3);
        address code = address(uint160(uint256(vm.load(address(clock), IMPLEMENTATION))));
        bytes memory want = abi.encodeWithSelector(IRoundClock.ALREADY_STARTED.selector);
        bytes memory ret = as_(who("Anyone"), address(clock), abi.encodeCall(IRoundClock.initialize, ()));
        require(keccak256(ret) == keccak256(want), "the proxy: ALREADY_STARTED");
        require(clock.currentRound() == 3, "the proxy's round unchanged");
        ret = as_(who("Anyone"), code, abi.encodeCall(IRoundClock.initialize, ()));
        require(keccak256(ret) == keccak256(want), "the code: ALREADY_STARTED");
        require(IRoundClock(code).currentRound() == 1, "the code's round unchanged");
    }

    // --- helpers ---------------------------------------------------------------------

    function newProxy(address code, address admin, bytes calldata data) external returns (address) {
        return address(new Proxy(code, admin, data));
    }

    function upgradedOnce(address proxy, address code) internal {
        Vm.Log[] memory logs = vm.getRecordedLogs();
        uint256 n = 0;
        for (uint256 i = 0; i < logs.length; i++) {
            if (logs[i].topics[0] != IProxy.Upgraded.selector) continue;
            require(logs[i].emitter == proxy && logs[i].topics[1] == slot(code), "Upgraded names the code");
            n++;
        }
        require(n == 1, "one Upgraded");
    }

    function codeInvalid(address code) internal pure returns (bytes memory) {
        return abi.encodeWithSelector(IProxy.CODE_INVALID.selector, code);
    }

    function slot(address a) internal pure returns (bytes32) {
        return bytes32(uint256(uint160(a)));
    }
}
