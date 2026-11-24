// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {vm} from "./Vm.sol";
import {FullFixture} from "./FullFixture.sol";
import {Broker} from "../src/money/Broker.sol";
import {ITicketBroker} from "../interfaces/ITicketBroker.sol";

/// A node sizes a redemption's gas limit by estimating it, with no margin,
/// and drops a ticket whose redemption reverts. A buy that empties the
/// network share between that estimate and the redemption must not make
/// the redemption cost more than the estimate.
contract RedeemAfterBuyTest is FullFixture {
    uint256 private constant GENESIS = 10_000_000e18;
    uint256 private constant HOUR = 3600;

    function setUp() external {
        loadVectors("october-main-text-v5");
        vm.warp(1_000_000);
    }

    /// Each call from this test runs as its own transaction, as on a
    /// chain: a slot's cost depends on its value when the transaction
    /// starts.
    /// forge-config: default.isolate = true
    function test_a_redemption_sized_while_the_share_is_held_runs_after_a_buy_empties_it() external {
        deployFull(GENESIS, STUB);
        seed(pool, 500_000e6, 100_000e18);
        require(poke().length == 0, "poke");
        vm.warp(block.timestamp + HOUR);
        job(1000e6);
        job(1000e6);
        uint256 face = 1000e6;
        require(fund("P", face, address(0), 0).length == 0, "fund");
        (ITicketBroker.Ticket memory t, bytes memory sig, uint256 rand) =
            ticket("estimated before the buy", "P", who("N"), clock.currentRound(), face);
        bytes memory data = abi.encodeCall(Broker.redeemWinningTicket, (t, sig, rand));
        require(broker.networkHeld() > 0, "a share is held");
        uint256 limit = leastGas(data);

        require(buy().length == 0, "the buy");
        expectEq(broker.networkHeld(), 0, "the buy drew the whole share");
        vm.prank(who("N"));
        (bool ok,) = address(broker).call{gas: limit}(data);
        require(ok, string.concat("the redemption ran out of its estimated ", vm.toString(limit), " gas"));
        require(broker.usedTickets(ticketHash(t)), "the ticket used");
        require(broker.networkHeld() > 0, "its share held");
    }

    /// The least gas with which N's call of `data` succeeds now, found by
    /// bisection, each try undone.
    function leastGas(bytes memory data) internal returns (uint256 lo) {
        uint256 hi = 1_000_000;
        lo = 21_000;
        while (lo < hi) {
            uint256 mid = (lo + hi) / 2;
            uint256 s = vm.snapshotState();
            vm.prank(who("N"));
            (bool ok,) = address(broker).call{gas: mid}(data);
            require(vm.revertToState(s), "revert");
            if (ok) hi = mid;
            else lo = mid + 1;
        }
    }
}
