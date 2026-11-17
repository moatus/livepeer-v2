// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {IScoreSource} from "./IScoreSource.sol";

/// The validator set and the median: stage 3's score source (rulebook R-11,
/// R-13). A call made for a node takes the node's address and is refused
/// with NOT_AUTHORIZED unless the Registry's mayActFor(caller, node). Every
/// change to the members counts from the next round. Opting in again,
/// seating a node already a member for the next round, unseating a node not
/// a member for the next round or with stake, and opting out without having
/// opted in change nothing and are not refused.
interface IScores is IScoreSource {
    /// Refused with NOT_AUTHORIZED unless mayActFor(caller, node), then with
    /// NOT_REGISTERED when `node` is not a registered node (D-39). Sets the
    /// flag, then seats `node` if it can.
    function optIn(address node) external;

    /// Clears the flag; a member leaves from the next round.
    function optOut(address node) external;

    /// Anyone. Refused with NOT_OPTED_IN, and with STAKE_TOO_LOW when its
    /// stake at the start of the current round is 0 or, with every seat
    /// taken, not above the smallest member's.
    function seat(address node) external;

    /// Anyone. A member whose stake at the start of the current round is 0
    /// leaves from the next round, and its votes lapse.
    function unseat(address node) external;

    /// `node` must be a member of the current round (NOT_VALIDATOR); a score
    /// above 10^6 is refused with SCORE_OUT_OF_RANGE. Replaces `node`'s vote
    /// on `target`.
    function vote(address node, address target, uint256 score) external;

    /// At most N; fixed when `round` starts. For currentRound() + 1, the
    /// members as they stand so far.
    function membersAt(uint256 round) external view returns (address[] memory);

    event OptedIn(address indexed node, uint256 round);
    event OptedOut(address indexed node, uint256 fromRound);

    /// `displaced` is 0 if none.
    event Seated(address indexed node, uint256 fromRound, address displaced);
    event Unseated(address indexed node, uint256 fromRound);
    event Voted(address indexed node, address indexed target, uint256 score, uint256 round);

    error NOT_REGISTERED();
    error NOT_AUTHORIZED();
    error NOT_OPTED_IN();
    error STAKE_TOO_LOW();
    error NOT_VALIDATOR();
    error SCORE_OUT_OF_RANGE();
}
