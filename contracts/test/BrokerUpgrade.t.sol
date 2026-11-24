// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {vm, Vm} from "./Vm.sol";
import {LedgerRunner} from "./LedgerRunner.sol";
import {BrokerWithTicketCount} from "./BrokerWithTicketCount.sol";
import {ITicketBroker} from "../interfaces/ITicketBroker.sol";
import {IUpgrades} from "../interfaces/IUpgrades.sol";
import {IRegistry} from "../interfaces/IRegistry.sol";
import {IRoundClock} from "../interfaces/IRoundClock.sol";
import {IParameters} from "../interfaces/IParameters.sol";
import {IBuybackValues} from "../interfaces/IBuybackValues.sol";
import {IController} from "../interfaces/IController.sol";
import {IMockUSDC} from "../interfaces/IMockUSDC.sol";

/// A deployment carried through a shared `ledger` case, then its Broker
/// upgraded in place to BrokerWithTicketCount after the notice (rulebook
/// R-27): every view that existed before reads the same at the same
/// addresses, and the next redemption, through the new code, moves the
/// count.
contract BrokerUpgradeTest is LedgerRunner {
    bytes32 internal constant IMPLEMENTATION = 0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc;
    bytes32 internal constant ADMIN = 0xb53127684a568b3173ae13b9f8a6016e243e63b6e8ee1178d6a717850b5d6103;
    /// Parameters' adjustable groups are 1 to GROUPS.
    uint256 internal constant GROUPS = 10;
    string internal constant CASE = "ledger-demo";

    function setUp() external {
        loadVectors();
    }

    function test_a_ledger_run_upgraded_to_the_ticket_count_reads_the_same() external {
        run(CASE);
        BrokerWithTicketCount next = new BrokerWithTicketCount(usdc, registry, clock, params, upgradesAt);
        bytes memory init = abi.encodeCall(BrokerWithTicketCount.initializeTicketCount, (1000));
        vm.prank(who("U"));
        uint256 appliesFrom = upgrades.announceUpgrade(address(broker), address(next), init);
        toRound(appliesFrom);

        address proxy = address(broker);
        bytes32 before = views();
        bytes32 code = vm.load(proxy, IMPLEMENTATION);
        vm.prank(who("U"));
        upgrades.applyUpgrade(proxy);
        require(views() == before, "every view reads the same after the apply");
        require(vm.load(proxy, IMPLEMENTATION) == bytes32(uint256(uint160(address(next)))), "the new code in force");
        require(vm.load(proxy, IMPLEMENTATION) != code, "not the old code");
        require(address(broker) == proxy, "the same address");

        BrokerWithTicketCount counted = BrokerWithTicketCount(proxy);
        expectEq(counted.ticketCount(), 1000, "the count starts at its initialisation");
        (ITicketBroker.Ticket memory t, bytes memory sig, uint256 rand) =
            ticket("after the upgrade", "P", who("N"), clock.currentRound(), 10_001);
        expectDone(redeem(t, sig, rand), "a redemption through the new code");
        expectEq(counted.ticketCount(), 1001, "the redemption counted");
        require(broker.usedTickets(ticketHash(t)), "the ticket used");
        bytes memory again = as_(who("U"), proxy, abi.encodeCall(BrokerWithTicketCount.initializeTicketCount, (0)));
        require(bytes4(again) == BrokerWithTicketCount.ALREADY_COUNTING.selector, "a second initialisation refused");
    }

    /// Every view function of the deployment's six contracts (the Broker,
    /// the Registry, the clock, Parameters, the Controller and MockUSDC) and
    /// of Upgrades, and both ERC-1967 slots of each proxy, folded into one
    /// digest: each call's arguments range over every address, round,
    /// parameter group and ticket the case could have touched, and its
    /// answer is taken as raw bytes, a refusal included. The Broker's own
    /// pending announcement and implementation slot are left out: the apply
    /// changes them, as it must.
    function views() internal view returns (bytes32 d) {
        address[] memory as_ = everyone();
        for (uint256 i = 0; i < as_.length; i++) {
            d = accountViews(d, as_[i]);
            for (uint256 j = 0; j < as_.length; j++) {
                d = pairViews(d, as_[i], as_[j]);
            }
        }
        uint256 last = clock.currentRound() + 1;
        for (uint256 round = 0; round <= last; round++) {
            d = see(d, address(broker), abi.encodeCall(ITicketBroker.totalFees, (round)));
            d = see(d, address(clock), abi.encodeCall(IRoundClock.blockHashForRound, (round)));
            d = see(d, address(params), abi.encodeCall(IBuybackValues.buybackValuesAt, (round)));
            for (uint256 i = 0; i < as_.length; i++) {
                d = see(d, address(broker), abi.encodeCall(ITicketBroker.feesOf, (round, as_[i])));
            }
            for (uint256 group = 0; group <= GROUPS + 1; group++) {
                d = see(d, address(params), abi.encodeCall(IParameters.valuesAt, (group, round)));
            }
        }
        for (uint256 group = 0; group <= GROUPS + 1; group++) {
            d = see(d, address(params), abi.encodeCall(IParameters.pending, (group)));
        }
        d = see(d, address(broker), abi.encodeCall(ITicketBroker.networkHeld, ()));
        d = see(d, address(params), abi.encodeCall(IParameters.authority, ()));
        d = see(d, address(clock), abi.encodeCall(IRoundClock.currentRound, ()));
        d = see(d, address(clock), abi.encodeCall(IRoundClock.lastInitializedRound, ()));
        d = see(d, address(clock), abi.encodeCall(IRoundClock.roundLength, ()));
        d = see(d, address(usdc), abi.encodeCall(IMockUSDC.name, ()));
        d = see(d, address(usdc), abi.encodeCall(IMockUSDC.symbol, ()));
        d = see(d, address(usdc), abi.encodeCall(IMockUSDC.decimals, ()));
        d = see(d, address(usdc), abi.encodeCall(IMockUSDC.totalSupply, ()));
        bytes32[4] memory names =
            [keccak256("BondingManager"), keccak256("RoundsManager"), keccak256("TicketBroker"), keccak256("Minter")];
        for (uint256 i = 0; i < names.length; i++) {
            d = see(d, address(controller), abi.encodeCall(IController.getContract, (names[i])));
        }
        d = ticketViews(d);
        d = upgradeViews(d);
    }

    /// The case's named participants, the shared examples' keys, address 0
    /// and every contract of the deployment.
    function everyone() internal view returns (address[] memory as_) {
        as_ = new address[](14);
        (as_[0], as_[1], as_[2], as_[3], as_[4], as_[5]) =
        (address(0), who("P"), who("N"), who("G"), who("U"), who("S"));
        (as_[6], as_[7], as_[8], as_[9], as_[10]) =
        (address(this), address(broker), address(registry), address(clock), address(params));
        (as_[11], as_[12], as_[13]) = (address(usdc), address(controller), address(upgrades));
    }

    function accountViews(bytes32 d, address a) internal view returns (bytes32) {
        d = see(d, address(broker), abi.encodeCall(ITicketBroker.getSenderInfo, (a)));
        d = see(d, address(registry), abi.encodeCall(IRegistry.isRegistered, (a)));
        d = see(d, address(registry), abi.encodeCall(IRegistry.isActiveTranscoder, (a)));
        d = see(d, address(registry), abi.encodeCall(IRegistry.payoutOf, (a)));
        d = see(d, address(registry), abi.encodeCall(IRegistry.operatorOf, (a)));
        d = see(d, address(registry), abi.encodeCall(IRegistry.proposedOperatorOf, (a)));
        return see(d, address(usdc), abi.encodeCall(IMockUSDC.balanceOf, (a)));
    }

    function pairViews(bytes32 d, address a, address b) internal view returns (bytes32) {
        d = see(d, address(broker), abi.encodeCall(ITicketBroker.claimableReserve, (a, b)));
        d = see(d, address(broker), abi.encodeCall(ITicketBroker.earmarkOf, (a, b)));
        d = see(d, address(registry), abi.encodeCall(IRegistry.mayActFor, (a, b)));
        return see(d, address(usdc), abi.encodeCall(IMockUSDC.allowance, (a, b)));
    }

    /// usedTickets of each ticket the case redeemed, and of one never made.
    function ticketViews(bytes32 d) internal view returns (bytes32) {
        for (uint256 i = 0;; i++) {
            string memory st = string.concat(path, ".inputs.steps[", vm.toString(i), "]");
            if (!vm.keyExistsJson(vectors, st)) break;
            if (!eq(str(st, ".action"), "redeem")) continue;
            (ITicketBroker.Ticket memory t,,) = ticket(
                str(st, ".ticket"),
                str(st, ".payer"),
                who(str(st, ".node")),
                num(st, ".created_round"),
                num(st, ".face_micro_usdc")
            );
            d = see(d, address(broker), abi.encodeCall(ITicketBroker.usedTickets, (ticketHash(t))));
        }
        return see(d, address(broker), abi.encodeCall(ITicketBroker.usedTickets, (keccak256("never made"))));
    }

    /// Upgrades' views, and each proxy's two slots, but for the Broker's
    /// announcement and code, which the apply consumes and replaces.
    function upgradeViews(bytes32 d) internal view returns (bytes32) {
        address u = address(upgrades);
        d = see(d, u, abi.encodeCall(IUpgrades.upgrader, ()));
        d = see(d, u, abi.encodeCall(IUpgrades.pauser, ()));
        d = see(d, u, abi.encodeCall(IUpgrades.pauserNamed, ()));
        d = see(d, u, abi.encodeCall(IUpgrades.clock, ()));
        for (uint256 s = 0; s < 3; s++) {
            d = see(d, u, abi.encodeCall(IUpgrades.stopped, (IUpgrades.Side(s))));
            d = see(d, u, abi.encodeCall(IUpgrades.stopEndsAt, (IUpgrades.Side(s))));
        }
        for (uint256 i = 0; i < proxies.length; i++) {
            d = see(d, u, abi.encodeCall(IUpgrades.sideOf, (proxies[i])));
            d = keccak256(abi.encode(d, vm.load(proxies[i], ADMIN)));
            if (proxies[i] == address(broker)) continue;
            d = see(d, u, abi.encodeCall(IUpgrades.pending, (proxies[i])));
            d = keccak256(abi.encode(d, vm.load(proxies[i], IMPLEMENTATION)));
        }
        return see(d, u, abi.encodeCall(IUpgrades.pending, (address(usdc))));
    }

    /// Folds one view's answer, or its refusal, into `d`.
    function see(bytes32 d, address target, bytes memory data) internal view returns (bytes32) {
        (bool ok, bytes memory ret) = target.staticcall(data);
        return keccak256(abi.encode(d, target, data, ok, ret));
    }

    function expectDone(bytes memory ret, string memory what) internal pure {
        require(ret.length == 0, string.concat(what, ": refused with ", code(ret)));
    }
}
