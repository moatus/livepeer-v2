// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {IRegistry} from "../../interfaces/IRegistry.sol";
import {IRoundClock} from "../../interfaces/IRoundClock.sol";

/// Who is a node, where its USDC half is paid (rulebook R-01), and which key
/// acts for it (R-26). Anyone registers, at any time, free; registration is
/// permanent and registering again changes nothing. The node finds it under
/// the name "BondingManager". It also answers the one authority check,
/// mayActFor, which StakeLedger, Claims and Scores read at the moment they
/// execute.
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
    /// The node's operator key; address(0) means the node itself.
    mapping(address => address) private operator;
    /// The node's one proposal slot; address(0) when empty.
    mapping(address => address) private proposed;

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

    /// Only the node's operator key sets its payout; address(0) makes it the
    /// node itself again. The event names the address the node is now paid
    /// at.
    function setPayout(address node, address payout_) external {
        if (!mayActFor(msg.sender, node)) revert NOT_AUTHORIZED();
        if (!registered[node]) revert NOT_REGISTERED();
        if (payout_ == broker) revert PAYOUT_INVALID();
        payout[node] = payout_;
        emit PayoutSet(node, payoutOf(node));
    }

    /// The operator key proposes `next`, replacing any proposal. Proposing
    /// the current operator key clears the slot: nobody is then proposed.
    function setOperator(address node, address next) external {
        if (!mayActFor(msg.sender, node)) revert NOT_AUTHORIZED();
        if (!registered[node]) revert NOT_REGISTERED();
        if (next == address(0)) revert OPERATOR_INVALID();
        address slot = next == operatorOf(node) ? address(0) : next;
        proposed[node] = slot;
        emit OperatorProposed(node, slot);
    }

    /// The key in the slot takes over in this call, and the slot empties, so
    /// a proposal is used once. An empty slot refuses every caller.
    function acceptOperator(address node) external {
        address slot = proposed[node];
        if (slot == address(0) || msg.sender != slot) revert NOT_AUTHORIZED();
        delete proposed[node];
        operator[node] = slot;
        emit OperatorSet(node, slot);
    }

    function isRegistered(address a) external view returns (bool) {
        return registered[a];
    }

    /// The same as isRegistered: a registered node with zero stake is
    /// active. It does not mean the validator set.
    function isActiveTranscoder(address a) external view returns (bool) {
        return registered[a];
    }

    /// The node itself until its operator key sets another. Read by the
    /// Broker at redemption (R-03).
    function payoutOf(address node) public view returns (address) {
        address p = payout[node];
        return p == address(0) ? node : p;
    }

    /// The node itself until a proposed key accepts.
    function operatorOf(address node) public view returns (address) {
        address o = operator[node];
        return o == address(0) ? node : o;
    }

    function proposedOperatorOf(address node) external view returns (address) {
        return proposed[node];
    }

    /// Whether `caller` may act for `node`: it is the node's operator key
    /// (R-26). It reads nothing else about the node, so the caller that
    /// checks it gives NOT_REGISTERED itself, after it. Read at
    /// execution by every call that acts for a node, setPayout and
    /// setOperator here included.
    function mayActFor(address caller, address node) public view returns (bool) {
        return caller == operatorOf(node);
    }
}
