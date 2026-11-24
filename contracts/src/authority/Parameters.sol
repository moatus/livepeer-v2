// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {IParameters} from "../../interfaces/IParameters.sol";
import {IRoundClock} from "../../interfaces/IRoundClock.sol";
import {IBuybackExecutor} from "../../interfaces/IBuybackExecutor.sol";
import {RewardMath} from "../math/RewardMath.sol";

/// The values governance may change (rulebook R-25), each kept as the
/// history of its group's changes, so every part reads the values of the
/// round the rulebook names and never the values now. It holds and moves no
/// token.
///
/// How a round's values are kept. Each group has a list of changes, sorted
/// by the round each counts from. A change announced in round A counts from
/// A + 7 and is appended, or, when the last change is still pending
/// (counting after the current round), written over it: a group has at most
/// one pending change, and only the last can be pending. A withdrawal
/// removes that last change. So a change that has started to count is never
/// written again, and the values of a round that has started are fixed: the
/// last change counting in it or before, the deployment's before any. A
/// pending change is in the list, so valuesAt shows it for the rounds it
/// would count in, as it stands; withdrawn or replaced, it is gone.
///
/// The deployment's values are the rulebook's (R-25's table, the units
/// section): three of them, the score source, the pool and the executor, are
/// addresses given at construction. It holds no pool design: an announced
/// pool is put to an executor, which accepts it or not.
contract Parameters is IParameters {
    uint256 private constant FEE = 1;
    uint256 private constant EMISSION = 2;
    uint256 private constant SCHEDULE = 3;
    uint256 private constant REWARD_DELAY = 4;
    uint256 private constant VALIDATOR_SET_SIZE = 5;
    uint256 private constant SCORE_SOURCE = 6;
    uint256 private constant UNBONDING = 7;
    uint256 private constant BUYBACK_CUTS = 8;
    uint256 private constant BUYBACK_VALUES = 9;
    uint256 private constant BUYBACK_EXECUTOR = 10;

    /// A change counts from the round of its announcement + 7.
    uint256 private constant NOTICE = 7;
    /// A whole, in millionths.
    uint256 private constant ONE = 1_000_000;
    /// The largest delay, period, drop and buy interval: 840 rounds or
    /// steps.
    uint256 private constant MOST = 840;
    /// Gas kept back from the executor asked about a pool, for the
    /// refusal after its answer (R-25).
    uint256 private constant ANSWER_RESERVE = 20_000;
    /// The largest validator set.
    uint256 private constant MOST_MEMBERS = 33;

    address public immutable authority;
    IRoundClock private immutable clock;
    address private immutable scoreSource;
    address private immutable pool;
    /// Not checked for code: the deployment predicts the Buyback's address
    /// and deploys it after the Parameters; address(0) where there is none.
    address private immutable executor;

    struct Change {
        uint256 countsFrom;
        uint256[] values;
    }

    /// group => its changes, sorted by countsFrom.
    mapping(uint256 => Change[]) private changes;

    constructor(address authority_, IRoundClock clock_, address scoreSource_, address pool_, address executor_) {
        require(authority_ != address(0) && address(clock_) != address(0), "zero address");
        authority = authority_;
        clock = clock_;
        scoreSource = scoreSource_;
        pool = pool_;
        executor = executor_;
    }

    // --- the authority's calls ------------------------------------------

    function announce(uint256 group, uint256[] calldata values) external returns (uint256 countsFrom) {
        if (msg.sender != authority) revert NOT_AUTHORIZED();
        if (group == 0 || group > BUYBACK_EXECUTOR) revert NOT_ADJUSTABLE();
        uint256 now_ = clock.currentRound();
        uint256[] memory inForce = _at(group, now_);
        if (group == SCHEDULE && values.length == 2 && values[1] > inForce[1]) revert SCHEDULE_RAISED();
        if (!_inRange(group, values, inForce)) revert VALUE_OUT_OF_RANGE();
        if ((group == FEE || group == EMISSION) && _sum(values) != ONE) revert IDENTITY_NOT_WHOLE();
        if (group == BUYBACK_VALUES) _checkPool(address(uint160(values[0])), now_);

        countsFrom = now_ + NOTICE;
        uint256[] memory recorded = values;
        if (group == SCHEDULE) {
            // R-05: the new half-life applies from the position the schedule
            // in force reaches in the round before the change counts, lowered
            // by the drop. No other change can count between now and then: a
            // pending one is the one this replaces.
            recorded = new uint256[](3);
            recorded[0] = RewardMath.position(inForce[0], inForce[1], inForce[2], countsFrom - 1) + values[0];
            recorded[1] = values[1];
            recorded[2] = countsFrom - 1;
        }
        Change[] storage list = changes[group];
        uint256 n = list.length;
        if (n > 0 && list[n - 1].countsFrom > now_) {
            list[n - 1] = Change(countsFrom, recorded);
        } else {
            list.push(Change(countsFrom, recorded));
        }
        emit Announced(group, recorded, countsFrom);
    }

    function withdraw(uint256 group) external {
        if (msg.sender != authority) revert NOT_AUTHORIZED();
        if (group == 0 || group > BUYBACK_EXECUTOR) revert NOT_ADJUSTABLE();
        Change[] storage list = changes[group];
        uint256 n = list.length;
        if (n == 0 || list[n - 1].countsFrom <= clock.currentRound()) revert NOTHING_PENDING();
        uint256 countsFrom = list[n - 1].countsFrom;
        list.pop();
        emit Withdrawn(group, countsFrom);
    }

    // --- views ----------------------------------------------------------

    function valuesAt(uint256 group, uint256 round) external view returns (uint256[] memory) {
        if (group == 0 || group > BUYBACK_EXECUTOR) revert NOT_ADJUSTABLE();
        return _at(group, round);
    }

    function pending(uint256 group) external view returns (uint256[] memory values, uint256 countsFrom) {
        if (group == 0 || group > BUYBACK_EXECUTOR) revert NOT_ADJUSTABLE();
        Change[] storage list = changes[group];
        uint256 n = list.length;
        if (n > 0 && list[n - 1].countsFrom > clock.currentRound()) {
            return (list[n - 1].values, list[n - 1].countsFrom);
        }
    }

    /// Group 9's values and group 8's burn, both in force for `round`, the
    /// round being bought (R-16).
    function buybackValuesAt(uint256 round) external view returns (BuybackValues memory v) {
        uint256[] memory b = _at(BUYBACK_VALUES, round);
        v.pool = address(uint160(b[0]));
        v.burnPpm = _at(BUYBACK_CUTS, round)[0];
        v.twapSeconds = b[1];
        v.maxSlippagePpm = b[2];
        v.buyEveryRounds = b[3];
    }

    // --- internals ------------------------------------------------------

    /// The last change of `group` counting in `round` or before, or the
    /// deployment's values.
    function _at(uint256 group, uint256 round) private view returns (uint256[] memory) {
        Change[] storage list = changes[group];
        uint256 lo = 0;
        uint256 hi = list.length;
        while (lo < hi) {
            uint256 mid = (lo + hi) / 2;
            if (list[mid].countsFrom <= round) lo = mid + 1;
            else hi = mid;
        }
        if (lo > 0) return list[lo - 1].values;
        return _deployment(group);
    }

    /// The deployment's values (R-25, the units section).
    function _deployment(uint256 group) private view returns (uint256[] memory v) {
        if (group == FEE) return _list(500_000, 500_000);
        if (group == EMISSION) return _list(940_000, 10_000, 50_000);
        if (group == SCHEDULE) return _list(0, MOST, 1);
        if (group == REWARD_DELAY) return _list(7);
        if (group == VALIDATOR_SET_SIZE) return _list(3);
        if (group == SCORE_SOURCE) return _list(uint256(uint160(scoreSource)));
        if (group == UNBONDING) return _list(21);
        if (group == BUYBACK_CUTS) return _list(ONE, 0, 0);
        if (group == BUYBACK_EXECUTOR) return _list(uint256(uint160(executor)));
        v = new uint256[](4);
        (v[0], v[1], v[2], v[3]) = (uint256(uint160(pool)), 3600, 10_000, 1);
    }

    /// Each value within its group's range and the group's count of values.
    /// `inForce` is the group's values in the announcing round.
    function _inRange(uint256 group, uint256[] calldata v, uint256[] memory inForce) private view returns (bool) {
        if (group == FEE) return v.length == 2 && v[0] <= ONE && v[1] <= ONE;
        if (group == EMISSION) return v.length == 3 && v[0] <= ONE && v[1] <= ONE && v[2] <= ONE;
        if (group == SCHEDULE) {
            // The raise is refused before this, so h <= inForce[1] holds when
            // two values are given.
            return v.length == 2 && v[0] <= MOST && v[1] != 0 && MOST % v[1] == 0;
        }
        if (group == REWARD_DELAY || group == UNBONDING) return v.length == 1 && v[0] >= 1 && v[0] <= MOST;
        if (group == VALIDATOR_SET_SIZE) return v.length == 1 && v[0] >= inForce[0] && v[0] <= MOST_MEMBERS;
        if (group == SCORE_SOURCE || group == BUYBACK_EXECUTOR) {
            return v.length == 1 && v[0] <= type(uint160).max && address(uint160(v[0])).code.length > 0;
        }
        if (group == BUYBACK_CUTS) return v.length == 3 && v[0] == ONE && v[1] == 0 && v[2] == 0;
        return v.length == 4 && v[0] <= type(uint160).max && v[1] >= 1800 && v[1] <= 7200 && v[2] >= 5000
            && v[2] <= 50_000 && v[3] >= 1 && v[3] <= MOST;
    }

    function _sum(uint256[] calldata v) private pure returns (uint256 s) {
        for (uint256 i = 0; i < v.length; i++) {
            s += v[i];
        }
    }

    /// R-25's pool check: the executor of group 10's pending change
    /// if one is pending, else the one in force in `round`, is asked whether
    /// it accepts `p`. Only a yes accepts; a no, a failed call or an address
    /// that cannot answer (address(0) included) is POOL_INVALID. Group 10
    /// takes any address with code, so the answer is read with care: at
    /// most one word of it is copied, however long it is, and the call is
    /// given all but ANSWER_RESERVE of the gas, so the refusal can always
    /// be made after it.
    function _checkPool(address p, uint256 round) private view {
        Change[] storage list = changes[BUYBACK_EXECUTOR];
        uint256 n = list.length;
        uint256[] memory e = n > 0 && list[n - 1].countsFrom > round ? list[n - 1].values : _at(BUYBACK_EXECUTOR, round);
        address asked = address(uint160(e[0]));
        bytes memory ask = abi.encodeCall(IBuybackExecutor.acceptsPool, (p));
        uint256 left = gasleft();
        uint256 give = left > ANSWER_RESERVE ? left - ANSWER_RESERVE : 0;
        bool ok;
        uint256 size;
        uint256 word;
        assembly ("memory-safe") {
            // The answer's first word goes to scratch space; the rest is
            // never copied.
            ok := staticcall(give, asked, add(ask, 32), mload(ask), 0, 32)
            size := returndatasize()
            word := mload(0)
        }
        if (!ok || size != 32 || word != 1) revert POOL_INVALID(p);
    }

    function _list(uint256 a) private pure returns (uint256[] memory v) {
        v = new uint256[](1);
        v[0] = a;
    }

    function _list(uint256 a, uint256 b) private pure returns (uint256[] memory v) {
        v = new uint256[](2);
        (v[0], v[1]) = (a, b);
    }

    function _list(uint256 a, uint256 b, uint256 c) private pure returns (uint256[] memory v) {
        v = new uint256[](3);
        (v[0], v[1], v[2]) = (a, b, c);
    }
}
