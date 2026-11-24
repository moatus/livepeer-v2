// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {vm, Vm} from "./Vm.sol";
import {LedgerRunner} from "./LedgerRunner.sol";
import {BrokerWithTicketCount} from "./BrokerWithTicketCount.sol";
import {ITicketBroker} from "../interfaces/ITicketBroker.sol";
import {IUpgrades} from "../interfaces/IUpgrades.sol";

/// A deployment carried through a shared `ledger` case, then its Broker
/// upgraded in place to BrokerWithTicketCount after the notice (rulebook
/// R-27): every view that existed before reads the same at the same
/// addresses, and the next redemption, through the new code, moves the
/// count.
contract BrokerUpgradeTest is LedgerRunner {
    bytes32 internal constant IMPLEMENTATION = 0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc;
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
        bytes memory before = views();
        bytes32 code = vm.load(proxy, IMPLEMENTATION);
        vm.prank(who("U"));
        upgrades.applyUpgrade(proxy);
        require(keccak256(views()) == keccak256(before), "every view reads the same after the apply");
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

    /// Every view of the Broker, the Registry, the clock, Parameters and the
    /// Controller over the accounts, tickets and rounds the case used, and
    /// the USDC each holds.
    function views() internal view returns (bytes memory) {
        return abi.encode(accountViews(), roundViews(), ticketViews(), nameViews());
    }

    function accountViews() internal view returns (bytes memory out) {
        (address p, address n) = (who("P"), who("N"));
        (ITicketBroker.Sender memory s, ITicketBroker.ReserveInfo memory r) = broker.getSenderInfo(p);
        out = abi.encode(s, r, broker.claimableReserve(p, n), broker.networkHeld());
        (uint256 a, uint256 b, uint256 c) = broker.earmarkOf(p, address(0));
        out = abi.encode(out, a, b, c);
        (a, b, c) = broker.earmarkOf(p, n);
        out = abi.encode(out, a, b, c);
        out = abi.encode(out, registry.isRegistered(n), registry.payoutOf(n), registry.operatorOf(n));
        out = abi.encode(out, usdc.balanceOf(address(broker)), usdc.balanceOf(p), usdc.balanceOf(n));
    }

    function roundViews() internal view returns (bytes memory out) {
        address n = who("N");
        for (uint256 round = 0; round <= clock.currentRound(); round++) {
            out = abi.encode(out, broker.feesOf(round, n), broker.totalFees(round), clock.blockHashForRound(round));
            out = abi.encode(out, params.valuesAt(1, round > 0 ? round : 1));
        }
        out = abi.encode(out, clock.currentRound());
    }

    /// usedTickets of each ticket the case redeemed.
    function ticketViews() internal view returns (bytes memory out) {
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
            out = abi.encode(out, broker.usedTickets(ticketHash(t)));
        }
    }

    /// The Controller's three names, and the Broker's side.
    function nameViews() internal view returns (bytes memory out) {
        bytes32[3] memory names = [keccak256("BondingManager"), keccak256("RoundsManager"), keccak256("TicketBroker")];
        for (uint256 i = 0; i < 3; i++) {
            out = abi.encode(out, controller.getContract(names[i]));
        }
        out = abi.encode(out, upgrades.sideOf(address(broker)) == IUpgrades.Side.USDC);
    }

    function expectDone(bytes memory ret, string memory what) internal pure {
        require(ret.length == 0, string.concat(what, ": refused with ", code(ret)));
    }
}
