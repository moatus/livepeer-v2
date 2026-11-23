// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {IBuybackExecutor} from "./IBuybackExecutor.sol";

/// The executor for TestPool (R-16 rule 13). Buys LPT with the network share and burns it (rulebook R-16). Anyone may
/// buy, once every buyEveryRounds rounds, and the buy computes its own
/// amount: the least of the share held and the most the slippage bound
/// allows over the pool's two-slot TWAP. What it cannot buy stays held for a
/// later round. Anyone may poke, which starts the TWAP on a pool and records
/// an observation when one is due. It accepts a pool with poolCodeHash
/// whose two tokens are this deployment's MockUSDC and TestLPT, in either
/// order. It draws only in a round whose group 10 names it.
///
/// constructor(IBuybackValues values, ITicketBrokerV2 broker, IRoundClock clock,
///             ITestLPTV2 lpt, IMockUSDC usdc, bytes32 poolCodeHash)
interface IBuyback is IBuybackExecutor {
    // buy() reads the values once for the current round, records an
    // observation if one is due, has the Broker pay usdcIn of the network
    // share to the pool, swaps it for lptOut and burns burnPpm of the LPT
    // received. Refused, the first that applies: ALREADY_BOUGHT,
    // NOTHING_HELD, POOL_INVALID, NO_LIQUIDITY, TWAP_NOT_READY, SLIPPAGE,
    // the Broker's NOT_AUTHORIZED when group 10 does not name this executor
    // for the round, LPT_SHORT; a refusal undoes the draw, the swap and the
    // recording.
    //
    // poke(), with no observation, or observations of another pool, sets
    // both slots to the current round's pool's cumulative price now;
    // otherwise records an observation when one is due. Refused, the first
    // that applies: POOL_INVALID, NO_LIQUIDITY, NOT_DUE.

    /// The values source, fixed at deployment.
    function values() external view returns (address);

    /// The Broker, fixed at deployment.
    function broker() external view returns (address);

    /// The round of the last buy that took effect; 0 before the first.
    function lastBuyRound() external view returns (uint256);

    /// The pool the two observations are of; address(0) before any poke.
    function observedPool() external view returns (address);

    /// The runtime code hash an authentic pool has: TestPool's. Fixed at
    /// deployment.
    function poolCodeHash() external view returns (bytes32);

    /// O0, the older observation, and O1, the newer: each a cumulative price
    /// of LPT in micro-USDC per wei (UQ112x112, summed over seconds) and its
    /// time.
    function observations() external view returns (uint256 cum0, uint256 time0, uint256 cum1, uint256 time1);

    /// held: the network share before the buy. twapX112: the TWAP the bound
    /// was taken from. maxIn: the most the bound allowed.
    event Bought(
        uint256 indexed round,
        uint256 held,
        uint256 usdcIn,
        uint256 lptBought,
        uint256 burned,
        uint256 twapX112,
        uint256 maxIn
    );
    event Observed(address indexed pool, uint256 cumulative, uint256 time);

    /// A buy before nextRound, the round the next buy is due.
    error ALREADY_BOUGHT(uint256 nextRound);
    /// A buy with no network share held.
    error NOTHING_HELD();
    /// A buy or poke while either of the pool's reserves is 0.
    error NO_LIQUIDITY();
    /// A buy whose observation is younger than the interval, or with nothing
    /// observed of this round's pool (elapsed 0).
    error TWAP_NOT_READY(uint256 elapsed, uint256 needed);
    /// A buy that can buy nothing within the bound.
    error SLIPPAGE(uint256 held, uint256 maxIn);
    /// A poke before the next observation is due at dueTime.
    error NOT_DUE(uint256 dueTime);
    /// This executor does not accept the round's pool: it lacks the pinned
    /// code hash, or its two tokens are not this deployment's MockUSDC and
    /// TestLPT.
    error POOL_INVALID(address pool);
    /// The pool delivered less than it quoted.
    error LPT_SHORT(uint256 quoted, uint256 received);
}
