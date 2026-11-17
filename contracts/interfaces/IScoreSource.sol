// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

/// What a claim reads (rulebook R-11, R-20): the one surface that may be
/// swapped. A claim still refuses a score above 10^6, whatever answers.
interface IScoreSource {
    /// 0 to 10^6: the median over membersAt(currentRound()) of their
    /// standing votes on `node`.
    function scoreOf(address node) external view returns (uint256);
}
