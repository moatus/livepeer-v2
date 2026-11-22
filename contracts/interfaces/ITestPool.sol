// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

/// The test liquidity pool of MockUSDC and TestLPT (rulebook R-16): a port of
/// the Uniswap v2 pair's swap path and a subset of IUniswapV2Pair, so a real
/// v2 pair can replace it. Its two tokens are immutables set at construction,
/// so every pool of one deployment's two tokens has one runtime code hash. It
/// has no owner, proxy, upgrade or self-destruct. Its events and revert texts
/// are v2's.
interface ITestPool {
    /// The two tokens, sorted as v2's factory sorts them.
    function token0() external view returns (address);
    function token1() external view returns (address);

    function getReserves() external view returns (uint112 reserve0, uint112 reserve1, uint32 blockTimestampLast);

    /// v2's cumulative prices: UQ112x112, summed over seconds, as of the
    /// pool's last update.
    function price0CumulativeLast() external view returns (uint256);
    function price1CumulativeLast() external view returns (uint256);

    /// The pool's liquidity shares.
    function totalSupply() external view returns (uint256);
    function balanceOf(address owner) external view returns (uint256);

    /// Mints liquidity for the tokens sent to the pool since its last
    /// update; the first mint locks MINIMUM_LIQUIDITY, 1000.
    function mint(address to) external returns (uint256 liquidity);

    /// v2's swap with its 0.3% fee check. Non-empty data is refused: no
    /// flash swaps.
    function swap(uint256 amount0Out, uint256 amount1Out, address to, bytes calldata data) external;

    event Mint(address indexed sender, uint256 amount0, uint256 amount1);
    event Swap(
        address indexed sender,
        uint256 amount0In,
        uint256 amount1In,
        uint256 amount0Out,
        uint256 amount1Out,
        address indexed to
    );
    event Sync(uint112 reserve0, uint112 reserve1);
}
