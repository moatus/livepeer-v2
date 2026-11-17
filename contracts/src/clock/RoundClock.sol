// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {IRoundClock} from "../../interfaces/IRoundClock.sol";

/// The round clock (rulebook R-18). Round 1 starts at deployment. Anyone
/// starts the next round once the current one has lasted ROUND_LENGTH
/// blocks of the block number the node reads; until then a round just
/// lasts longer, and no round number is skipped. The node finds it under
/// the name "RoundsManager".
contract RoundClock is IRoundClock {
    /// RoundLength (R-18): a test value (D-16).
    uint256 private constant ROUND_LENGTH = 50;

    uint256 private round;
    uint256 private startBlock;
    mapping(uint256 => bytes32) private roundHash;

    constructor() {
        _start(1);
    }

    function initializeRound() external {
        if (block.number < startBlock + ROUND_LENGTH) revert ROUND_NOT_OVER();
        _start(round + 1);
    }

    function currentRound() external view returns (uint256) {
        return round;
    }

    /// Rounds are counted by initializeRound, so the round the node signs
    /// against and the round redemption checks are one number.
    function lastInitializedRound() external view returns (uint256) {
        return round;
    }

    function blockHashForRound(uint256 r) external view returns (bytes32) {
        return roundHash[r];
    }

    function roundLength() external pure returns (uint256) {
        return ROUND_LENGTH;
    }

    /// Stores the hash of the block before the round started, as the
    /// current protocol's RoundsManager does.
    function _start(uint256 r) private {
        bytes32 h = blockhash(block.number - 1);
        round = r;
        startBlock = block.number;
        roundHash[r] = h;
        emit RoundStarted(r, block.number, h);
    }
}
