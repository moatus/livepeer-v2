// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {IRegistryV2} from "../../interfaces/IRegistry.sol";
import {IRoundClock} from "../../interfaces/IRoundClock.sol";

/// Who is a node, and where its USDC half is paid (rulebook R-01). Anyone
/// registers, at any time, free; registration is permanent and registering
/// again changes nothing. The node finds it under the name "BondingManager".
/// It also answers the one authority check, mayActFor, which StakeLedger and
/// Claims read at the moment they execute.
contract Registry is IRegistryV2 {
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

    /// Whether `caller` may act for `node` (S-09 section 3.2). Until stage 4
    /// adds key rotation: the node's own address. It reads nothing about the
    /// node, so the caller that checks it gives NOT_REGISTERED itself, after
    /// it (D-39). Read at execution by every call that acts for a node.
    function mayActFor(address caller, address node) external pure returns (bool) {
        return caller == node;
    }
}
