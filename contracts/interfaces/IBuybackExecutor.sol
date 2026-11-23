// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

/// What every buyback executor offers (rulebook R-16 rule 13, R-25): the
/// Parameters ask it whether it accepts a pool when one is announced, and a
/// keeper calls its buy and poke without knowing its design. Group 10,
/// BUYBACK_EXECUTOR, names the one executor that may draw the network share
/// in a round. Its buy and poke check their round's pool with the same
/// acceptsPool the Parameters ask, so the two checks cannot drift apart.
interface IBuybackExecutor {
    /// Whether this executor accepts `pool`: an authenticated, immutable pair
    /// of this deployment's two tokens, of its design. Never reverts for a
    /// pool it does not accept; it answers false.
    function acceptsPool(address pool) external view returns (bool);

    /// Buys LPT with the network share, drawn from the Broker, and burns it.
    function buy() external returns (uint256 usdcIn, uint256 lptBought, uint256 burned);

    /// Starts or advances the price observations a buy needs.
    function poke() external;
}
