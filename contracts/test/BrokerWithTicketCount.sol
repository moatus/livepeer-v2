// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {Broker} from "../src/money/Broker.sol";
import {IMockUSDC} from "../interfaces/IMockUSDC.sol";
import {IRegistry} from "../interfaces/IRegistry.sol";
import {IRoundClock} from "../interfaces/IRoundClock.sol";
import {IParameters} from "../interfaces/IParameters.sol";
import {IUpgrades} from "../interfaces/IUpgrades.sol";

/// The Broker with a ticket count: new code for the Broker's proxy that keeps
/// every stored figure of the Broker and adds one counter after them. The
/// counter starts at the value its one-time initializeTicketCount sets,
/// run by the upgrade, and counts each redemption made through this code.
/// Built with the wiring the code in force was built with.
contract BrokerWithTicketCount is Broker {
    /// Redemptions through this code, from the starting value.
    uint256 public ticketCount;
    bool private counting;

    error ALREADY_COUNTING();

    constructor(IMockUSDC usdc_, IRegistry registry_, IRoundClock clock_, IParameters params_, IUpgrades upgrades_)
        Broker(usdc_, registry_, clock_, params_, upgrades_)
    {}

    /// Once: refused with ALREADY_COUNTING after the first.
    function initializeTicketCount(uint256 start) external {
        if (counting) revert ALREADY_COUNTING();
        (counting, ticketCount) = (true, start);
    }

    function redeemWinningTicket(Ticket calldata t, bytes calldata sig, uint256 recipientRand) public override {
        super.redeemWinningTicket(t, sig, recipientRand);
        ticketCount += 1;
    }
}
