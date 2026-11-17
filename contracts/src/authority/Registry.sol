// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {IRegistry} from "../../interfaces/IRegistry.sol";
import {IRoundClock} from "../../interfaces/IRoundClock.sol";

/// Who is a node, and where its USDC half is paid (rulebook R-01). Anyone
/// registers, at any time, free; registration is permanent and registering
/// again changes nothing. The node finds it under the name "BondingManager".
contract Registry is IRegistry {
    IRoundClock private immutable clock;
    /// The Broker's address, fixed at deployment. A payout there would keep
    /// the node's half inside the Broker, so it is refused. The Broker is
    /// deployed after this contract, so the deployer passes the address the
    /// Broker will have.
    address private immutable broker;

    mapping(address => bool) private registered;
    /// address(0) means the node itself.
    mapping(address => address) private payout;

    constructor(IRoundClock clock_, address broker_) {
        require(broker_ != address(0), "zero address");
        clock = clock_;
        broker = broker_;
    }

    function register() external {
        if (registered[msg.sender]) return;
        registered[msg.sender] = true;
        emit NodeRegistered(msg.sender, clock.currentRound());
    }

    /// Only the node sets its own payout; address(0) makes it the node
    /// itself again. The event names the address the node is now paid at.
    function setPayout(address payout_) external {
        if (!registered[msg.sender]) revert NOT_REGISTERED();
        if (payout_ == broker) revert PAYOUT_INVALID();
        payout[msg.sender] = payout_;
        emit PayoutSet(msg.sender, payoutOf(msg.sender));
    }

    function isRegistered(address a) external view returns (bool) {
        return registered[a];
    }

    /// The same as isRegistered: a registered node with zero stake is
    /// active. It does not mean the validator set.
    function isActiveTranscoder(address a) external view returns (bool) {
        return registered[a];
    }

    /// The node itself until it sets another. Read by the Broker at
    /// redemption (R-03).
    function payoutOf(address node) public view returns (address) {
        address p = payout[node];
        return p == address(0) ? node : p;
    }
}
