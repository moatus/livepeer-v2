// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {IScores} from "../../interfaces/IScores.sol";
import {IRegistryV2} from "../../interfaces/IRegistry.sol";
import {IRoundClock} from "../../interfaces/IRoundClock.sol";
import {IStakeLedger} from "../../interfaces/IStakeLedger.sol";

/// The validator set and the median (rulebook R-11, R-13): stage 3's score
/// source. It holds and moves no token.
///
/// How a round's members are kept. The members are a history of seat lists,
/// each holding at most N nodes in seating order from its round on. A call
/// made in round R changes only the list that starts at R + 1, created from
/// the list before it on the round's first change. So the list of a round
/// that has started is never written again, and a round's members are fixed
/// when it starts with nothing done per round: the list for R + 1 simply
/// becomes the current one when R + 1 starts.
///
/// How votes lapse. Each seating gives a node a number, larger than any
/// given before, and a vote is kept with the number its node held when it
/// voted. A vote counts only while its node still holds that number. A node
/// that a round has started without is given a new number when it is seated
/// again, so its earlier votes lapse; a member of the current round whose
/// exit is undone in the same round keeps its number, and with it its votes
/// and its seating order (D-36, D-37). The seat lists are kept sorted by the
/// number, so the one seated last is the last in its list.
///
/// No call visits the opted-in nodes: each reads the node it names and the
/// at most N members (R-23).
contract Scores is IScores {
    /// ValidatorSetSize (R-13): a stand-in for the paper's 33.
    uint256 private constant N = 3;
    /// A score is 0 to 10^6 (R-11); a member that has not voted counts as
    /// 10^6.
    uint256 private constant MAX_SCORE = 1_000_000;

    IRegistryV2 private immutable registry;
    IRoundClock private immutable clock;
    IStakeLedger private immutable ledger;

    /// The members from `fromRound` on, in seating order, until the next
    /// list. Empty seats are address(0), after every member.
    struct Seats {
        uint256 fromRound;
        address[N] members;
    }

    struct Node {
        bool optedIn;
        /// The number of the node's latest seating; 0 if never seated.
        uint64 seated;
    }

    struct Vote {
        /// The voter's seating number when it voted; 0 for no vote.
        uint64 seated;
        uint64 score;
    }

    /// Sorted by fromRound. Only the last list can start after the current
    /// round, and only that one is ever changed.
    Seats[] private history;
    mapping(address => Node) private nodes;
    /// voter => target => its standing vote.
    mapping(address => mapping(address => Vote)) private votes;
    /// The last seating number given.
    uint64 private lastSeated;

    constructor(IRegistryV2 registry_, IRoundClock clock_, IStakeLedger ledger_) {
        require(
            address(registry_) != address(0) && address(clock_) != address(0) && address(ledger_) != address(0),
            "zero address"
        );
        registry = registry_;
        clock = clock_;
        ledger = ledger_;
    }

    // --- calls made for a node ------------------------------------------------

    /// Refused, the first that applies (D-39), with NOT_AUTHORIZED, then
    /// NOT_REGISTERED. Seats the node when it can, as seat would, and is not
    /// refused when it cannot.
    function optIn(address node) external {
        if (!registry.mayActFor(msg.sender, node)) revert NOT_AUTHORIZED();
        if (!registry.isRegistered(node)) revert NOT_REGISTERED();
        if (nodes[node].optedIn) return;
        nodes[node].optedIn = true;
        uint256 r = clock.currentRound();
        emit OptedIn(node, r);
        _seat(node, r);
    }

    /// A member leaves from the next round. Its votes count while it is a
    /// member and lapse when a round starts without it.
    function optOut(address node) external {
        if (!registry.mayActFor(msg.sender, node)) revert NOT_AUTHORIZED();
        if (!nodes[node].optedIn) return;
        nodes[node].optedIn = false;
        uint256 r = clock.currentRound();
        _leave(node, r);
        emit OptedOut(node, r + 1);
    }

    /// Refused, the first that applies, with NOT_AUTHORIZED, NOT_VALIDATOR
    /// when `node` is not a member of the current round, and
    /// SCORE_OUT_OF_RANGE above 10^6, the earlier vote kept.
    function vote(address node, address target, uint256 score) external {
        if (!registry.mayActFor(msg.sender, node)) revert NOT_AUTHORIZED();
        uint256 r = clock.currentRound();
        if (!_has(_current(r), node)) revert NOT_VALIDATOR();
        if (score > MAX_SCORE) revert SCORE_OUT_OF_RANGE();
        votes[node][target] = Vote(nodes[node].seated, uint64(score));
        emit Voted(node, target, score, r);
    }

    // --- calls anyone makes ---------------------------------------------------

    /// Refused with NOT_OPTED_IN, then STAKE_TOO_LOW.
    function seat(address node) external {
        if (!nodes[node].optedIn) revert NOT_OPTED_IN();
        if (!_seat(node, clock.currentRound())) revert STAKE_TOO_LOW();
    }

    /// A member for the next round whose stake at the start of the current
    /// round is 0 leaves from the next round. Anything else changes nothing.
    function unseat(address node) external {
        uint256 r = clock.currentRound();
        if (!_has(_latest(), node) || ledger.stakeAt(node, r) != 0) return;
        _leave(node, r);
        emit Unseated(node, r + 1);
    }

    // --- views ----------------------------------------------------------------

    /// The median over the members of the current round of their standing
    /// votes on `node`, a member with none counting as 10^6; 10^6 with no
    /// members; with an even count, the floor of the mean of the two middle
    /// votes (R-11).
    function scoreOf(address node) external view returns (uint256) {
        address[N] memory m = _current(clock.currentRound());
        uint256[N] memory s;
        uint256 k = 0;
        for (; k < N && m[k] != address(0); k++) {
            Vote memory v = votes[m[k]][node];
            uint256 x = v.seated == nodes[m[k]].seated ? v.score : MAX_SCORE;
            // Insertion into the sorted s[0..k).
            uint256 j = k;
            for (; j > 0 && s[j - 1] > x; j--) {
                s[j] = s[j - 1];
            }
            s[j] = x;
        }
        if (k == 0) return MAX_SCORE;
        if (k % 2 == 1) return s[k / 2];
        return (s[k / 2 - 1] + s[k / 2]) / 2;
    }

    /// At most N, in seating order; fixed when `round` starts. For a round
    /// after the current one, the members as they stand so far.
    function membersAt(uint256 round) external view returns (address[] memory out) {
        address[N] memory m = _at(round);
        uint256 k = 0;
        while (k < N && m[k] != address(0)) k++;
        out = new address[](k);
        for (uint256 i = 0; i < k; i++) {
            out[i] = m[i];
        }
    }

    // --- the seat lists -------------------------------------------------------

    /// Seats `node`, opted in, from round r + 1 if it is not already on that
    /// list, as R-13 says. Returns false, changing nothing, when its stake at
    /// the start of r is 0, or every seat is taken and that stake is not
    /// above the smallest member's.
    function _seat(address node, uint256 r) private returns (bool) {
        address[N] memory next = _latest();
        if (_has(next, node)) return true;
        uint256 stake = ledger.stakeAt(node, r);
        if (stake == 0) return false;

        // The seat it takes: the first free one, or else the smallest
        // member's; of equal smallest, the one seated last, the last in the
        // list.
        uint256 at = N;
        address displaced = address(0);
        if (next[N - 1] != address(0)) {
            uint256 smallest = type(uint256).max;
            for (uint256 j = 0; j < N; j++) {
                uint256 s = ledger.stakeAt(next[j], r);
                if (s <= smallest) {
                    smallest = s;
                    at = j;
                }
            }
            if (stake <= smallest) return false;
            displaced = next[at];
            _remove(next, at);
        }

        // A member of round r whose exit for r + 1 was recorded in r keeps
        // its number; any other node is seated last.
        uint64 number = nodes[node].seated;
        if (!_has(_current(r), node)) {
            number = ++lastSeated;
            nodes[node].seated = number;
        }
        uint256 i = N - 1;
        for (; i > 0 && (next[i - 1] == address(0) || nodes[next[i - 1]].seated > number); i--) {
            next[i] = next[i - 1];
        }
        next[i] = node;

        _write(next, r);
        emit Seated(node, r + 1, displaced);
        return true;
    }

    /// Takes `node` off the list for r + 1, if it is on it.
    function _leave(address node, uint256 r) private {
        address[N] memory next = _latest();
        for (uint256 i = 0; i < N; i++) {
            if (next[i] == node) {
                _remove(next, i);
                _write(next, r);
                return;
            }
        }
    }

    /// Writes the list for r + 1: the last list when it already starts at
    /// r + 1, or else a new one after it.
    function _write(address[N] memory members, uint256 r) private {
        uint256 n = history.length;
        if (n == 0 || history[n - 1].fromRound != r + 1) {
            history.push(Seats(r + 1, members));
        } else {
            history[n - 1].members = members;
        }
    }

    /// The members as they stand for every round from the next on.
    function _latest() private view returns (address[N] memory m) {
        uint256 n = history.length;
        if (n > 0) m = history[n - 1].members;
    }

    /// The members of round r, the current round: the last list, or the one
    /// before it when the last starts at r + 1.
    function _current(uint256 r) private view returns (address[N] memory m) {
        uint256 n = history.length;
        if (n > 0 && history[n - 1].fromRound <= r) return history[n - 1].members;
        if (n > 1) return history[n - 2].members;
    }

    /// The members of `round`: the last list that starts at or before it.
    function _at(uint256 round) private view returns (address[N] memory m) {
        uint256 lo = 0;
        uint256 hi = history.length;
        while (lo < hi) {
            uint256 mid = (lo + hi) / 2;
            if (history[mid].fromRound <= round) lo = mid + 1;
            else hi = mid;
        }
        if (lo > 0) m = history[lo - 1].members;
    }

    function _has(address[N] memory m, address node) private pure returns (bool) {
        for (uint256 i = 0; i < N; i++) {
            if (m[i] == node && node != address(0)) return true;
        }
        return false;
    }

    /// Removes m[i], closing the gap so the empty seats stay last.
    function _remove(address[N] memory m, uint256 i) private pure {
        for (; i + 1 < N; i++) {
            m[i] = m[i + 1];
        }
        m[N - 1] = address(0);
    }
}
