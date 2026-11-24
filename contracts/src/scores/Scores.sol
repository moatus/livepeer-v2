// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {IScores} from "../../interfaces/IScores.sol";
import {IRegistry} from "../../interfaces/IRegistry.sol";
import {IRoundClock} from "../../interfaces/IRoundClock.sol";
import {IStakeLedger} from "../../interfaces/IStakeLedger.sol";
import {IParameters} from "../../interfaces/IParameters.sol";

/// The validator set and the median (rulebook R-11, R-13): stage 3's score
/// source. It holds and moves no token.
///
/// How a round's members are kept. The members are a history of seat lists,
/// each holding at most N nodes in seating order from its round on, N the
/// ValidatorSetSize in force in the round of the call that wrote it (R-13,
/// R-25). N only rises, to at most 33, so no list is ever over the size of
/// a later round. A call
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
/// at most 33 members (R-23).
contract Scores is IScores {
    /// The ValidatorSetSize's group (R-25).
    uint256 private constant VALIDATOR_SET_SIZE = 5;
    /// A score is 0 to 10^6 (R-11); a member that has not voted counts as
    /// 10^6.
    uint256 private constant MAX_SCORE = 1_000_000;

    IRegistry private immutable registry;
    IRoundClock private immutable clock;
    IStakeLedger private immutable ledger;
    /// Where N is read, for the round a call runs in.
    IParameters private immutable params;

    /// The members from `fromRound` on, in seating order, until the next
    /// list.
    struct Seats {
        uint256 fromRound;
        address[] members;
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

    constructor(IRegistry registry_, IRoundClock clock_, IStakeLedger ledger_, IParameters params_) {
        require(
            address(registry_) != address(0) && address(clock_) != address(0) && address(ledger_) != address(0)
                && address(params_) != address(0),
            "zero address"
        );
        registry = registry_;
        clock = clock_;
        ledger = ledger_;
        params = params_;
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
        address[] memory m = _current(clock.currentRound());
        uint256[] memory s = new uint256[](m.length);
        uint256 k = 0;
        for (; k < m.length; k++) {
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
    function membersAt(uint256 round) external view returns (address[] memory) {
        return _at(round);
    }

    // --- the seat lists -------------------------------------------------------

    /// Seats `node`, opted in, from round r + 1 if it is not already on that
    /// list, as R-13 says. Returns false, changing nothing, when its stake at
    /// the start of r is 0, or every seat is taken and that stake is not
    /// above the smallest member's. N is the size in force in r, never one
    /// still pending.
    function _seat(address node, uint256 r) private returns (bool) {
        address[] memory next = _latest();
        if (_has(next, node)) return true;
        uint256 stake = ledger.stakeAt(node, r);
        if (stake == 0) return false;

        // The seat it takes: a free one, or else the smallest member's; of
        // equal smallest, the one seated last, the last in the list.
        uint256 n = next.length;
        address displaced = address(0);
        if (n >= params.valuesAt(VALIDATOR_SET_SIZE, r)[0]) {
            uint256 smallest = type(uint256).max;
            uint256 at = 0;
            for (uint256 j = 0; j < n; j++) {
                uint256 s = ledger.stakeAt(next[j], r);
                if (s <= smallest) {
                    smallest = s;
                    at = j;
                }
            }
            if (stake <= smallest) return false;
            displaced = next[at];
            next = _remove(next, at);
        }

        // A member of round r whose exit for r + 1 was recorded in r keeps
        // its number; any other node is seated last.
        uint64 number = nodes[node].seated;
        if (!_has(_current(r), node)) {
            number = ++lastSeated;
            nodes[node].seated = number;
        }
        address[] memory list = new address[](next.length + 1);
        uint256 i = next.length;
        for (; i > 0 && nodes[next[i - 1]].seated > number; i--) {
            list[i] = next[i - 1];
        }
        list[i] = node;
        for (; i > 0; i--) {
            list[i - 1] = next[i - 1];
        }

        _write(list, r);
        emit Seated(node, r + 1, displaced);
        return true;
    }

    /// Takes `node` off the list for r + 1, if it is on it.
    function _leave(address node, uint256 r) private {
        address[] memory next = _latest();
        for (uint256 i = 0; i < next.length; i++) {
            if (next[i] == node) {
                _write(_remove(next, i), r);
                return;
            }
        }
    }

    /// Writes the list for r + 1: the last list when it already starts at
    /// r + 1, or else a new one after it.
    function _write(address[] memory members, uint256 r) private {
        uint256 n = history.length;
        if (n == 0 || history[n - 1].fromRound != r + 1) {
            history.push(Seats(r + 1, members));
        } else {
            history[n - 1].members = members;
        }
    }

    /// The members as they stand for every round from the next on.
    function _latest() private view returns (address[] memory m) {
        uint256 n = history.length;
        if (n > 0) m = history[n - 1].members;
    }

    /// The members of round r, the current round: the last list, or the one
    /// before it when the last starts at r + 1.
    function _current(uint256 r) private view returns (address[] memory m) {
        uint256 n = history.length;
        if (n > 0 && history[n - 1].fromRound <= r) return history[n - 1].members;
        if (n > 1) return history[n - 2].members;
    }

    /// The members of `round`: the last list that starts at or before it.
    function _at(uint256 round) private view returns (address[] memory m) {
        uint256 lo = 0;
        uint256 hi = history.length;
        while (lo < hi) {
            uint256 mid = (lo + hi) / 2;
            if (history[mid].fromRound <= round) lo = mid + 1;
            else hi = mid;
        }
        if (lo > 0) m = history[lo - 1].members;
    }

    function _has(address[] memory m, address node) private pure returns (bool) {
        for (uint256 i = 0; i < m.length; i++) {
            if (m[i] == node) return true;
        }
        return false;
    }

    /// m without m[i], the rest in order.
    function _remove(address[] memory m, uint256 i) private pure returns (address[] memory out) {
        out = new address[](m.length - 1);
        for (uint256 j = 0; j < out.length; j++) {
            out[j] = m[j < i ? j : j + 1];
        }
    }
}
