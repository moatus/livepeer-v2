// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {Broker} from "../src/money/Broker.sol";
import {IMockUSDC} from "../interfaces/IMockUSDC.sol";
import {IRegistry} from "../interfaces/IRegistry.sol";
import {IRoundClock} from "../interfaces/IRoundClock.sol";
import {IParameters} from "../interfaces/IParameters.sol";
import {IUpgrades} from "../interfaces/IUpgrades.sol";

// New code for the Broker's proxy that demo/public-run upgrade refuses
// before it sends anything (./dev demo upgrade). None of it is deployed, so
// none of it needs to work: each differs from the Broker in one thing the
// command's gates check.

/// A counter declared before the Broker's figures, not after them: every
/// figure would be read from the slot after its own.
contract BrokerCounterFirst {
    struct Balance {
        uint256 amount;
        uint256 pending;
        uint256 readyRound;
    }

    uint256 public ticketCount;
    mapping(address => mapping(address => Balance)) private balance;
    mapping(address => uint256) private shownEarmarks;
    mapping(bytes32 => bool) public usedTickets;
    mapping(uint256 => mapping(address => uint256)) public feesOf;
    mapping(uint256 => uint256) public totalFees;
    uint256 public networkHeld;

    constructor(IMockUSDC, IRegistry, IRoundClock, IParameters, IUpgrades) {}
}

/// The Broker's figures with two members of a payer's balance, a struct
/// held in a mapping, in the other order: each deposit would be read as
/// its pending release.
contract BrokerBalanceReordered {
    struct Balance {
        uint256 pending;
        uint256 amount;
        uint256 readyRound;
    }

    mapping(address => mapping(address => Balance)) private balance;
    mapping(address => uint256) private shownEarmarks;
    mapping(bytes32 => bool) public usedTickets;
    mapping(uint256 => mapping(address => uint256)) public feesOf;
    mapping(uint256 => uint256) public totalFees;
    uint256 public networkHeld;

    constructor(IMockUSDC, IRegistry, IRoundClock, IParameters, IUpgrades) {}
}

/// The Broker, its constructor's first two parameters in the other order:
/// built with the recorded arguments, it would take the Registry for the
/// token.
contract BrokerConstructorSwapped is Broker {
    constructor(IRegistry registry_, IMockUSDC usdc_, IRoundClock clock_, IParameters params_, IUpgrades upgrades_)
        Broker(usdc_, registry_, clock_, params_, upgrades_)
    {}
}
