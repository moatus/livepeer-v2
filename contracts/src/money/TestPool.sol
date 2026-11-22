// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {ITestPool} from "../../interfaces/ITestPool.sol";

/// The test liquidity pool of MockUSDC and TestLPT (rulebook R-16): a port
/// of the Uniswap v2 pair's mint and swap paths, the subset ITestPool
/// declares, with v2's events, revert texts and cumulative prices. Its two
/// tokens are immutables, sorted as v2's factory sorts them, so every pool
/// of one deployment's two tokens has one runtime code hash, which the
/// Buyback pins. It has no owner, proxy, upgrade or self-destruct, no
/// protocol fee and no flash swaps. Its liquidity shares are counted but
/// not transferable: a stand-in needs only the first mint.
contract TestPool is ITestPool {
    uint256 private constant MINIMUM_LIQUIDITY = 1000;
    uint224 private constant Q112 = 2 ** 112;

    address public immutable token0;
    address public immutable token1;

    uint256 public totalSupply;
    mapping(address => uint256) public balanceOf;

    uint112 private reserve0;
    uint112 private reserve1;
    uint32 private blockTimestampLast;

    uint256 public price0CumulativeLast;
    uint256 public price1CumulativeLast;

    uint256 private unlocked = 1;

    modifier lock() {
        require(unlocked == 1, "UniswapV2: LOCKED");
        unlocked = 0;
        _;
        unlocked = 1;
    }

    constructor(address tokenA, address tokenB) {
        require(tokenA != tokenB, "UniswapV2: IDENTICAL_ADDRESSES");
        (address t0, address t1) = tokenA < tokenB ? (tokenA, tokenB) : (tokenB, tokenA);
        require(t0 != address(0), "UniswapV2: ZERO_ADDRESS");
        token0 = t0;
        token1 = t1;
    }

    function getReserves() public view returns (uint112, uint112, uint32) {
        return (reserve0, reserve1, blockTimestampLast);
    }

    /// v2's mint, less the protocol fee: liquidity for what was sent since
    /// the last update. The first mint locks MINIMUM_LIQUIDITY at
    /// address(0).
    function mint(address to) external lock returns (uint256 liquidity) {
        (uint112 _reserve0, uint112 _reserve1,) = getReserves();
        uint256 balance0 = _balanceOf(token0);
        uint256 balance1 = _balanceOf(token1);
        uint256 amount0 = balance0 - _reserve0;
        uint256 amount1 = balance1 - _reserve1;
        uint256 _totalSupply = totalSupply;
        if (_totalSupply == 0) {
            liquidity = _sqrt(amount0 * amount1) - MINIMUM_LIQUIDITY;
            _mintShares(address(0), MINIMUM_LIQUIDITY);
        } else {
            uint256 a = amount0 * _totalSupply / _reserve0;
            uint256 b = amount1 * _totalSupply / _reserve1;
            liquidity = a < b ? a : b;
        }
        require(liquidity > 0, "UniswapV2: INSUFFICIENT_LIQUIDITY_MINTED");
        _mintShares(to, liquidity);
        _update(balance0, balance1, _reserve0, _reserve1);
        emit Mint(msg.sender, amount0, amount1);
    }

    /// v2's swap: the output is sent first, then the input is read from the
    /// balance and the 0.3% fee check holds k. Non-empty data is refused.
    function swap(uint256 amount0Out, uint256 amount1Out, address to, bytes calldata data) external lock {
        require(data.length == 0, "TestPool: NO_FLASH_SWAPS");
        require(amount0Out > 0 || amount1Out > 0, "UniswapV2: INSUFFICIENT_OUTPUT_AMOUNT");
        (uint112 _reserve0, uint112 _reserve1,) = getReserves();
        require(amount0Out < _reserve0 && amount1Out < _reserve1, "UniswapV2: INSUFFICIENT_LIQUIDITY");
        require(to != token0 && to != token1, "UniswapV2: INVALID_TO");
        if (amount0Out > 0) _safeTransfer(token0, to, amount0Out);
        if (amount1Out > 0) _safeTransfer(token1, to, amount1Out);
        (uint256 amount0In, uint256 amount1In) = _checkIn(amount0Out, amount1Out, _reserve0, _reserve1);
        emit Swap(msg.sender, amount0In, amount1In, amount0Out, amount1Out, to);
    }

    // --- internals ------------------------------------------------------

    /// The rest of v2's swap, scoped apart as v2 scopes it: the input read
    /// from the balances, the fee check on k, and the update.
    function _checkIn(uint256 amount0Out, uint256 amount1Out, uint112 _reserve0, uint112 _reserve1)
        private
        returns (uint256 amount0In, uint256 amount1In)
    {
        uint256 balance0 = _balanceOf(token0);
        uint256 balance1 = _balanceOf(token1);
        amount0In = balance0 > _reserve0 - amount0Out ? balance0 - (_reserve0 - amount0Out) : 0;
        amount1In = balance1 > _reserve1 - amount1Out ? balance1 - (_reserve1 - amount1Out) : 0;
        require(amount0In > 0 || amount1In > 0, "UniswapV2: INSUFFICIENT_INPUT_AMOUNT");
        uint256 balance0Adjusted = balance0 * 1000 - amount0In * 3;
        uint256 balance1Adjusted = balance1 * 1000 - amount1In * 3;
        require(balance0Adjusted * balance1Adjusted >= uint256(_reserve0) * _reserve1 * 1000 ** 2, "UniswapV2: K");
        _update(balance0, balance1, _reserve0, _reserve1);
    }

    /// v2's _update: on the first call of a block, adds each price times the
    /// seconds since the last update to its cumulative, wrapping as v2's
    /// does, then stores the balances as reserves.
    function _update(uint256 balance0, uint256 balance1, uint112 _reserve0, uint112 _reserve1) private {
        require(balance0 <= type(uint112).max && balance1 <= type(uint112).max, "UniswapV2: OVERFLOW");
        uint32 blockTimestamp = uint32(block.timestamp % 2 ** 32);
        unchecked {
            uint32 timeElapsed = blockTimestamp - blockTimestampLast;
            if (timeElapsed > 0 && _reserve0 != 0 && _reserve1 != 0) {
                price0CumulativeLast += uint256(uint224(_reserve1) * Q112 / _reserve0) * timeElapsed;
                price1CumulativeLast += uint256(uint224(_reserve0) * Q112 / _reserve1) * timeElapsed;
            }
        }
        reserve0 = uint112(balance0);
        reserve1 = uint112(balance1);
        blockTimestampLast = blockTimestamp;
        emit Sync(reserve0, reserve1);
    }

    function _mintShares(address to, uint256 value) private {
        totalSupply += value;
        balanceOf[to] += value;
    }

    function _balanceOf(address token) private view returns (uint256) {
        (bool ok, bytes memory ret) = token.staticcall(abi.encodeWithSelector(0x70a08231, address(this)));
        require(ok && ret.length >= 32, "TestPool: BALANCE_FAILED");
        return abi.decode(ret, (uint256));
    }

    function _safeTransfer(address token, address to, uint256 value) private {
        // transfer(address,uint256)
        (bool ok, bytes memory ret) = token.call(abi.encodeWithSelector(0xa9059cbb, to, value));
        require(ok && (ret.length == 0 || abi.decode(ret, (bool))), "UniswapV2: TRANSFER_FAILED");
    }

    /// The Babylonian square root v2 uses.
    function _sqrt(uint256 y) private pure returns (uint256 z) {
        if (y > 3) {
            z = y;
            uint256 x = y / 2 + 1;
            while (x < z) {
                z = x;
                x = (y / x + x) / 2;
            }
        } else if (y != 0) {
            z = 1;
        }
    }
}
