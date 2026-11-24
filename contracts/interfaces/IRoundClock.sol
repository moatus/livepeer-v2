// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

/// The round clock (rulebook R-18). Round 1 starts at deployment. The node
/// finds it under the name "RoundsManager".
interface IRoundClock {
    /// Anyone. Starts round currentRound() + 1 once the current round has
    /// lasted roundLength() blocks; refused with ROUND_NOT_OVER before that.
    function initializeRound() external;

    /// Starts round 1 while the round is 0, as a proxy's creation runs it;
    /// refused with ALREADY_STARTED once a round has started (R-18).
    function initialize() external;

    /// Counted from 1. A round lasts until someone starts the next one.
    function currentRound() external view returns (uint256);

    /// Always equal to currentRound().
    function lastInitializedRound() external view returns (uint256);

    /// The hash of the block before the round started; 0 for a round not
    /// started.
    function blockHashForRound(uint256 round) external view returns (bytes32);

    /// 50.
    function roundLength() external view returns (uint256);

    event RoundStarted(uint256 indexed round, uint256 startBlock, bytes32 blockHash);

    error ROUND_NOT_OVER();
    error ALREADY_STARTED();
}
