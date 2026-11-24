// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {vm, Vm} from "./Vm.sol";
import {LedgerRunner} from "./LedgerRunner.sol";
import {BrokerWithTicketCount} from "./BrokerWithTicketCount.sol";
import {ITicketBroker} from "../interfaces/ITicketBroker.sol";
import {IMockUSDC} from "../interfaces/IMockUSDC.sol";

/// A deployment carried through a shared `ledger` case, then its Broker
/// upgraded in place to BrokerWithTicketCount after the notice (rulebook
/// R-27): every view of the Broker that existed before reads the same at
/// the same address, and the next redemption, through the new code, moves
/// the count.
contract BrokerUpgradeTest is LedgerRunner {
    bytes32 internal constant IMPLEMENTATION = 0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc;
    bytes32 internal constant ADMIN = 0xb53127684a568b3173ae13b9f8a6016e243e63b6e8ee1178d6a717850b5d6103;
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
        vm.prank(who("U"));
        upgrades.applyUpgrade(proxy);
        require(views() == before, "every view reads the same after the apply");
        require(vm.load(proxy, IMPLEMENTATION) == bytes32(uint256(uint160(address(next)))), "the new code in force");

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

    /// Every view function of the Broker, its USDC balance and its admin
    /// slot, folded into one digest: each call's arguments range over every
    /// address, round and ticket the case could have touched, and its answer
    /// is taken as raw bytes, a refusal included. The apply writes only the
    /// Broker's storage, and the Broker has no right on any other contract.
    function views() internal view returns (bytes32 d) {
        address[] memory as_ = everyone();
        for (uint256 i = 0; i < as_.length; i++) {
            d = see(d, address(broker), abi.encodeCall(ITicketBroker.getSenderInfo, (as_[i])));
            for (uint256 j = 0; j < as_.length; j++) {
                d = see(d, address(broker), abi.encodeCall(ITicketBroker.claimableReserve, (as_[i], as_[j])));
                d = see(d, address(broker), abi.encodeCall(ITicketBroker.earmarkOf, (as_[i], as_[j])));
            }
        }
        uint256 last = clock.currentRound() + 1;
        for (uint256 round = 0; round <= last; round++) {
            d = see(d, address(broker), abi.encodeCall(ITicketBroker.totalFees, (round)));
            for (uint256 i = 0; i < as_.length; i++) {
                d = see(d, address(broker), abi.encodeCall(ITicketBroker.feesOf, (round, as_[i])));
            }
        }
        d = see(d, address(broker), abi.encodeCall(ITicketBroker.networkHeld, ()));
        d = see(d, address(usdc), abi.encodeCall(IMockUSDC.balanceOf, (address(broker))));
        d = keccak256(abi.encode(d, vm.load(address(broker), ADMIN)));
        d = ticketViews(d);
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

    /// Folds one view's answer, or its refusal, into `d`.
    function see(bytes32 d, address target, bytes memory data) internal view returns (bytes32) {
        (bool ok, bytes memory ret) = target.staticcall(data);
        return keccak256(abi.encode(d, target, data, ok, ret));
    }

    function expectDone(bytes memory ret, string memory what) internal pure {
        require(ret.length == 0, string.concat(what, ": refused with ", code(ret)));
    }
}
