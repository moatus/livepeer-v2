// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {IController} from "../../interfaces/IController.sol";

/// The names the node looks up, fixed at deployment. There is no owner and
/// no way to change a name later (spec S-09, section 3.4).
contract Controller is IController {
    bytes32 private constant BONDING_MANAGER = keccak256("BondingManager");
    bytes32 private constant ROUNDS_MANAGER = keccak256("RoundsManager");
    bytes32 private constant TICKET_BROKER = keccak256("TicketBroker");

    address private immutable registry;
    address private immutable clock;
    address private immutable broker;

    constructor(address registry_, address clock_, address broker_) {
        require(registry_ != address(0) && clock_ != address(0) && broker_ != address(0), "zero address");
        registry = registry_;
        clock = clock_;
        broker = broker_;
    }

    function getContract(bytes32 id) external view returns (address) {
        if (id == BONDING_MANAGER) return registry;
        if (id == ROUNDS_MANAGER) return clock;
        if (id == TICKET_BROKER) return broker;
        return address(0);
    }
}
