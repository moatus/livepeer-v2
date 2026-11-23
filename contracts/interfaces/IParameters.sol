// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {IBuybackValues} from "./IBuybackValues.sol";

/// The values governance may change, and the round each counts from
/// (rulebook R-25). One authority, named at deployment, announces a change;
/// it counts from 7 rounds after the round of the announcement, and until
/// then the authority may withdraw it or announce another that replaces it.
/// Every part reads a value for the round the rulebook names, never as it is
/// now.
///
/// Groups, each changed whole, its values in this order:
///   1 FEE                 (node cut, network)
///   2 EMISSION            (node, validators, treasury)
///   3 SCHEDULE            (drop, half-life); valuesAt and pending give the
///                         triple it records, (position, half-life, start)
///   4 REWARD_DELAY        (rounds)
///   5 VALIDATOR_SET_SIZE  (members)
///   6 SCORE_SOURCE        (address)
///   7 UNBONDING           (rounds)
///   8 BUYBACK_CUTS        (burn, treasury, validators): exactly
///                         (10^6, 0, 0), else VALUE_OUT_OF_RANGE
///   9 BUYBACK_VALUES      (pool, twapSeconds, maxSlippagePpm, buyEveryRounds),
///                         R-16's order: twapSeconds 1800 to 7200,
///                         maxSlippagePpm 5000 to 50000, buyEveryRounds 1 to
///                         840, else VALUE_OUT_OF_RANGE; then a pool the
///                         executor asked does not accept, POOL_INVALID. The
///                         executor asked is group 10's pending change if one
///                         is pending, else group 10's value in force; it
///                         accepts only by answering acceptsPool(pool) true
///  10 BUYBACK_EXECUTOR    (executor): an address with code, else
///                         VALUE_OUT_OF_RANGE; never checked against a pool
/// Any other number is refused with NOT_ADJUSTABLE. An address travels as
/// uint256(uint160(a)).
///
/// Parameters is the Buyback's values source (R-16): buybackValuesAt(round)
/// returns group 9's pool, twapSeconds, maxSlippagePpm and buyEveryRounds
/// in force for `round`, and group 8's burn share as burnPpm. Group 8's
/// treasury and validator cuts are held and checked here and not returned:
/// they are 0 while burn is 10^6. Both groups are checked when announced,
/// as above; the pool is checked again by the executor in force at each
/// use. The Broker reads group 10 for the round a draw runs in.
///
/// constructor(address authority, IRoundClock clock, address scoreSource,
///             address pool, address executor): the deployment's score
/// source, pool and executor. The executor is the address the deployment
/// predicts for its Buyback, or address(0) in a deployment with none, and
/// is not checked for code.
interface IParameters is IBuybackValues {
    /// Named at deployment and never changed.
    function authority() external view returns (address);

    /// Announces `values` for `group`, replacing any change of the group
    /// still pending, and returns the round it counts from: the current
    /// round + 7. Refused, the first that applies: NOT_AUTHORIZED,
    /// NOT_ADJUSTABLE, SCHEDULE_RAISED (a schedule change of two values whose
    /// half-life is above the one in force), VALUE_OUT_OF_RANGE (also a wrong
    /// number of values), IDENTITY_NOT_WHOLE, POOL_INVALID. A raise is what
    /// D-12 forbids, so it is named first; the pool, the one value another
    /// contract is asked about, is checked last.
    function announce(uint256 group, uint256[] calldata values) external returns (uint256 countsFrom);

    /// Withdraws the group's pending change, which then never counts.
    /// Refused with NOT_AUTHORIZED, then NOT_ADJUSTABLE, then NOTHING_PENDING
    /// when none is pending, the round it counts from included.
    function withdraw(uint256 group) external;

    /// The values in force for `group` in `round`: those of the last change
    /// counting in `round` or before, the deployment's before any does.
    /// Round 0 has the deployment's. NOT_ADJUSTABLE for any other number.
    function valuesAt(uint256 group, uint256 round) external view returns (uint256[] memory);

    /// The group's pending change, or no values and countsFrom 0 when none
    /// is pending. NOT_ADJUSTABLE for any other number.
    function pending(uint256 group) external view returns (uint256[] memory values, uint256 countsFrom);

    /// For group 3, `values` is the triple recorded.
    event Announced(uint256 indexed group, uint256[] values, uint256 countsFrom);
    event Withdrawn(uint256 indexed group, uint256 countsFrom);

    error NOT_AUTHORIZED();
    error NOT_ADJUSTABLE();
    error SCHEDULE_RAISED();
    error VALUE_OUT_OF_RANGE();
    error IDENTITY_NOT_WHOLE();
    error NOTHING_PENDING();
    /// The executor asked does not accept `pool`: it answers no, the call
    /// fails, or the address cannot answer (R-25).
    error POOL_INVALID(address pool);
}
