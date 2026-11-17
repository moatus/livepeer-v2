// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {IStakeLedger} from "../../interfaces/IStakeLedger.sol";
import {ITestLPT} from "../../interfaces/ITestLPT.sol";
import {IRegistryV2} from "../../interfaces/IRegistry.sol";
import {IRoundClock} from "../../interfaces/IRoundClock.sol";

/// Holds every bonded and unbonded LPT and answers stake by round (rulebook
/// R-08, R-15, R-24).
///
/// How stake by round is kept. Every quantity that changes by round is a
/// history: a list of points (round, value), sorted by round, where the
/// value holds from that round until the next point. A read for any round
/// is a binary search, with no loop over rounds, holders or nodes (R-23).
///
/// Every change to a start-of-round count is written at a round after the
/// current one: R + 1 for a bond, unbond, rebond or the end of a transfer's
/// count at the old node, R + 21 for a transfer's arrival, and the arrival
/// round T, which is after R, when an unbond of a transfer cancels it. So the
/// count at the start of a round that has started is never written again
/// (`_countAdd` and `_countSub` assert it), and a transfer arrives with no
/// action: its arrival was written when it was made. A write adds to the
/// value at its round and every later point, and the later points all lie
/// within the next 21 rounds, so a write touches at most 22 points however
/// long the ledger has run.
contract StakeLedger is IStakeLedger {
    /// UnbondingPeriod (R-15): unbonded LPT is withdrawable 21 rounds on.
    uint256 private constant UNBONDING_PERIOD = 21;
    /// R-15 (D-11): a transfer made in round R counts for the new node from
    /// R + 21.
    uint256 private constant TRANSFER_WAIT = 21;
    /// A reward cut is in millionths, 0 to 10^6 (R-24).
    uint256 private constant MAX_CUT = 1_000_000;

    ITestLPT private immutable lpt;
    IRegistryV2 private immutable registry;
    IRoundClock private immutable clock;

    /// One point of a history: `value` holds from `round` on. Also one
    /// entry of a list: a transfer of `value` arriving at `round`, or an
    /// unbond of `value` withdrawable from `round`. Entries of one list are
    /// appended in round order, and those made in one round are merged.
    /// Amounts are kept in the token's whole range: no rule caps a bond.
    struct Point {
        uint256 round;
        uint256 value;
    }

    // --- counts at the start of a round (written only for later rounds) ---

    /// holder => node => the holder's bond counting for the node.
    mapping(address => mapping(address => Point[])) private bondPoints;
    /// node => its stake: every bond counting for it.
    mapping(address => Point[]) private stakePoints;
    /// Every node's stake.
    Point[] private totalPoints;
    /// node => its reward cut; default 0.
    mapping(address => Point[]) private cutPoints;

    // --- as they stand now, with arrivals already scheduled ---------------

    /// holder => node => the bond staying at the node: bonded, rebonded or
    /// arrived, and not leaving. A transfer adds to it at its arrival round.
    mapping(address => mapping(address => Point[])) private stayingPoints;
    /// holder => every unbond, less what was rebonded, summed by the round
    /// it became or becomes withdrawable.
    mapping(address => Point[]) private readyPoints;
    /// holder => everything it has withdrawn.
    mapping(address => uint256) private withdrawn;

    /// holder => from => to => its transfers, by arrival round.
    mapping(address => mapping(address => mapping(address => Point[]))) private transfers;
    /// holder => node it was counting for => its unbonds, by ready round.
    mapping(address => mapping(address => Point[])) private unbonds;

    constructor(ITestLPT lpt_, IRegistryV2 registry_, IRoundClock clock_) {
        require(
            address(lpt_) != address(0) && address(registry_) != address(0) && address(clock_) != address(0),
            "zero address"
        );
        lpt = lpt_;
        registry = registry_;
        clock = clock_;
    }

    // --- holder actions -----------------------------------------------------

    function bond(address node, uint256 amount) external {
        if (!registry.isRegistered(node)) revert NOT_REGISTERED();
        uint256 now_ = clock.currentRound();
        _add(stayingPoints[msg.sender][node], now_, amount);
        _countAdd(now_, msg.sender, node, now_ + 1, amount);
        _totalAdd(now_, now_ + 1, amount);
        require(lpt.transferFrom(msg.sender, address(this), amount), "transfer failed");
        emit Bonded(msg.sender, node, amount, now_ + 1);
    }

    function unbond(address node, uint256 amount) external {
        uint256 now_ = clock.currentRound();
        Point[] storage staying = stayingPoints[msg.sender][node];
        if (amount > _at(staying, now_)) revert AMOUNT_TOO_LARGE();
        _sub(staying, now_, amount);
        _countSub(now_, msg.sender, node, now_ + 1, amount);
        _totalSub(now_, now_ + 1, amount);
        _lock(msg.sender, node, now_ + UNBONDING_PERIOD, amount);
        emit Unbonded(msg.sender, node, amount, now_ + UNBONDING_PERIOD);
    }

    function transferBond(address from, address to, uint256 amount) external {
        if (!registry.isRegistered(to)) revert NOT_REGISTERED();
        uint256 now_ = clock.currentRound();
        Point[] storage staying = stayingPoints[msg.sender][from];
        if (amount > _at(staying, now_)) revert AMOUNT_TOO_LARGE();
        uint256 arrival = now_ + TRANSFER_WAIT;
        // It leaves the old node's staying bond now, counts for the old node
        // through arrival - 1, and for the new one, and stays there, from
        // arrival. The total does not change.
        _sub(staying, now_, amount);
        _add(stayingPoints[msg.sender][to], arrival, amount);
        _countAdd(now_, msg.sender, to, arrival, amount);
        _countSub(now_, msg.sender, from, arrival, amount);
        _append(transfers[msg.sender][from][to], arrival, amount);
        emit TransferStarted(msg.sender, from, to, amount, arrival);
    }

    function unbondTransfer(address from, address to, uint256 amount) external {
        uint256 now_ = clock.currentRound();
        Point[] storage list = transfers[msg.sender][from][to];
        if (amount > _pending(list, now_)) revert AMOUNT_TOO_LARGE();
        // The transfer arriving last first. For each part taken, its arrival
        // at round t is cancelled, at `to` and at `from` alike, so it keeps
        // counting for `from` until it is unbonded below.
        uint256 left = amount;
        while (left > 0) {
            Point storage last = list[list.length - 1];
            uint256 t = last.round;
            uint256 part = last.value < left ? last.value : left;
            if (part == last.value) list.pop();
            else last.value -= part;
            left -= part;
            _sub(stayingPoints[msg.sender][to], t, part);
            _countSub(now_, msg.sender, to, t, part);
            _countAdd(now_, msg.sender, from, t, part);
        }
        // It stops counting for `from` next round and never arrives.
        _countSub(now_, msg.sender, from, now_ + 1, amount);
        _totalSub(now_, now_ + 1, amount);
        _lock(msg.sender, from, now_ + UNBONDING_PERIOD, amount);
        emit TransferUnbonded(msg.sender, from, to, amount, now_ + UNBONDING_PERIOD);
    }

    function rebond(address node, uint256 amount) external {
        if (!registry.isRegistered(node)) revert NOT_REGISTERED();
        uint256 now_ = clock.currentRound();
        Point[] storage list = unbonds[msg.sender][node];
        if (amount > _pending(list, now_)) revert AMOUNT_TOO_LARGE();
        // The latest unbond first; what is rebonded is no longer
        // withdrawable at its ready round.
        uint256 left = amount;
        while (left > 0) {
            Point storage last = list[list.length - 1];
            uint256 t = last.round;
            uint256 part = last.value < left ? last.value : left;
            if (part == last.value) list.pop();
            else last.value -= part;
            left -= part;
            _sub(readyPoints[msg.sender], t, part);
        }
        _add(stayingPoints[msg.sender][node], now_, amount);
        _countAdd(now_, msg.sender, node, now_ + 1, amount);
        _totalAdd(now_, now_ + 1, amount);
        emit Rebonded(msg.sender, node, amount, now_ + 1);
    }

    function withdrawUnbonded() external {
        uint256 ready = _at(readyPoints[msg.sender], clock.currentRound()) - withdrawn[msg.sender];
        if (ready == 0) revert UNBOND_NOT_READY();
        withdrawn[msg.sender] += ready;
        require(lpt.transfer(msg.sender, ready), "transfer failed");
        emit UnbondedWithdrawn(msg.sender, ready);
    }

    /// Authority is read from the Registry at execution.
    function setRewardCut(address node, uint256 cut) external {
        if (!registry.mayActFor(msg.sender, node)) revert NOT_AUTHORIZED();
        if (cut > MAX_CUT) revert CUT_OUT_OF_RANGE();
        uint256 from = clock.currentRound() + 1;
        // Every cut is written for the next round, so the last point is
        // either that round's, set again, or earlier.
        Point[] storage points = cutPoints[node];
        uint256 n = points.length;
        if (n > 0 && points[n - 1].round == from) points[n - 1].value = cut;
        else points.push(Point(from, cut));
        emit RewardCutSet(node, cut, from);
    }

    // --- at the start of a round ------------------------------------------

    function stakeAt(address node, uint256 round) external view returns (uint256) {
        return _at(stakePoints[node], round);
    }

    function totalStakeAt(uint256 round) external view returns (uint256) {
        return _at(totalPoints, round);
    }

    function bondAt(address holder, address node, uint256 round) external view returns (uint256) {
        return _at(bondPoints[holder][node], round);
    }

    function cutAt(address node, uint256 round) external view returns (uint256) {
        return _at(cutPoints[node], round);
    }

    // --- as they stand now ------------------------------------------------

    function stayingOf(address holder, address node) external view returns (uint256) {
        return _at(stayingPoints[holder][node], clock.currentRound());
    }

    function inTransferOf(address holder, address from, address to) external view returns (uint256) {
        return _pending(transfers[holder][from][to], clock.currentRound());
    }

    function unbondedOf(address holder) external view returns (uint256 waiting, uint256 ready) {
        // The last point holds every unbond less what was rebonded.
        Point[] storage points = readyPoints[holder];
        uint256 due = _at(points, clock.currentRound());
        ready = due - withdrawn[holder];
        waiting = points.length == 0 ? 0 : points[points.length - 1].value - due;
    }

    function rebondableOf(address holder, address node) external view returns (uint256) {
        return _pending(unbonds[holder][node], clock.currentRound());
    }

    // --- counts --------------------------------------------------------------

    /// A start-of-round count is only ever written for a round that has not
    /// started.
    function _countAdd(uint256 now_, address holder, address node, uint256 round, uint256 amount) private {
        assert(round > now_);
        _add(bondPoints[holder][node], round, amount);
        _add(stakePoints[node], round, amount);
    }

    function _countSub(uint256 now_, address holder, address node, uint256 round, uint256 amount) private {
        assert(round > now_);
        _sub(bondPoints[holder][node], round, amount);
        _sub(stakePoints[node], round, amount);
    }

    function _totalAdd(uint256 now_, uint256 round, uint256 amount) private {
        assert(round > now_);
        _add(totalPoints, round, amount);
    }

    function _totalSub(uint256 now_, uint256 round, uint256 amount) private {
        assert(round > now_);
        _sub(totalPoints, round, amount);
    }

    /// Records unbonded LPT counting for `node`, withdrawable from `ready`.
    function _lock(address holder, address node, uint256 ready, uint256 amount) private {
        _append(unbonds[holder][node], ready, amount);
        _add(readyPoints[holder], ready, amount);
    }

    // --- lists ----------------------------------------------------------------

    function _append(Point[] storage list, uint256 round, uint256 amount) private {
        if (amount == 0) return;
        uint256 n = list.length;
        if (n > 0 && list[n - 1].round == round) list[n - 1].value += amount;
        else list.push(Point(round, amount));
    }

    /// What the entries after `now_` hold: transfers not yet arrived, or
    /// unbonds not yet withdrawable. They are the last entries of the list,
    /// at most one per round of the next 21.
    function _pending(Point[] storage list, uint256 now_) private view returns (uint256 sum) {
        for (uint256 i = list.length; i > 0 && list[i - 1].round > now_; i--) {
            sum += list[i - 1].value;
        }
    }

    // --- histories ------------------------------------------------------------

    /// The value at `round`: that of the last point at or before it, or 0.
    function _at(Point[] storage points, uint256 round) private view returns (uint256) {
        uint256 hi = points.length;
        if (hi == 0) return 0;
        if (points[hi - 1].round <= round) return points[hi - 1].value;
        uint256 lo = 0;
        while (lo < hi) {
            uint256 mid = (lo + hi) / 2;
            if (points[mid].round <= round) lo = mid + 1;
            else hi = mid;
        }
        return lo == 0 ? 0 : points[lo - 1].value;
    }

    /// Adds `amount` to the value from `round` on.
    function _add(Point[] storage points, uint256 round, uint256 amount) private {
        if (amount == 0) return;
        for (uint256 i = _open(points, round); i < points.length; i++) {
            points[i].value += amount;
        }
    }

    /// Takes `amount` from the value from `round` on. Never below 0: a
    /// shortfall reverts.
    function _sub(Point[] storage points, uint256 round, uint256 amount) private {
        if (amount == 0) return;
        for (uint256 i = _open(points, round); i < points.length; i++) {
            points[i].value -= amount;
        }
    }

    /// The index of the point at `round`, inserted with the value before it
    /// if there is none. The walk back passes only points after `round`,
    /// which all lie within the next 21 rounds.
    function _open(Point[] storage points, uint256 round) private returns (uint256 i) {
        uint256 n = points.length;
        i = n;
        while (i > 0 && points[i - 1].round > round) i--;
        if (i > 0 && points[i - 1].round == round) return i - 1;
        uint256 before = i == 0 ? 0 : points[i - 1].value;
        points.push();
        for (uint256 k = n; k > i; k--) {
            points[k] = points[k - 1];
        }
        points[i] = Point(round, before);
    }
}
