// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {IRegistry} from "../../interfaces/IRegistry.sol";
import {IRoundClock} from "../../interfaces/IRoundClock.sol";

/// Who is a node (rulebook R-01). Anyone registers, at any time, free;
/// registration is permanent and registering again changes nothing. The
/// node finds it under the name "BondingManager".
///
/// Stage 2, phase 2: setPayout is built in phase 4, so every node is paid
/// at its own address until then, and this contract does not yet declare
/// the whole IRegistry shape.
contract Registry {
    IRoundClock private immutable clock;
    mapping(address => bool) private registered;

    constructor(IRoundClock clock_) {
        clock = clock_;
    }

    function register() external {
        if (registered[msg.sender]) return;
        registered[msg.sender] = true;
        emit IRegistry.NodeRegistered(msg.sender, clock.currentRound());
    }

    function isRegistered(address a) public view returns (bool) {
        return registered[a];
    }

    /// The same as isRegistered: a registered node with zero stake is
    /// active. It does not mean the validator set.
    function isActiveTranscoder(address a) external view returns (bool) {
        return registered[a];
    }

    /// The node itself until it sets another (setPayout, phase 4).
    function payoutOf(address node) external pure returns (address) {
        return node;
    }
}
