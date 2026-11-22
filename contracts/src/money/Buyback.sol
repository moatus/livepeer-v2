// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {IBuyback} from "../../interfaces/IBuyback.sol";
import {IBuybackValues} from "../../interfaces/IBuybackValues.sol";
import {ITicketBrokerV2} from "../../interfaces/ITicketBroker.sol";
import {IRoundClock} from "../../interfaces/IRoundClock.sol";
import {ITestLPTV2} from "../../interfaces/ITestLPT.sol";
import {IMockUSDC} from "../../interfaces/IMockUSDC.sol";
import {ITestPool} from "../../interfaces/ITestPool.sol";

/// Buys LPT with the network share and burns it (rulebook R-16). Anyone may
/// buy, once every buyEveryRounds rounds, and the buy computes its own
/// amount: the least of the share held and the most the slippage bound
/// allows over the pool's two-slot TWAP (D-48). Anyone may poke, which
/// starts the TWAP on a pool and records an observation when one is due.
/// It has no owner and no setting: every value comes from the values source
/// fixed at deployment, read once per buy or poke for the current round.
contract Buyback is IBuyback {
    uint256 private constant ONE = 1_000_000;

    IBuybackValues private immutable source;
    ITicketBrokerV2 private immutable broker_;
    IRoundClock private immutable clock;
    ITestLPTV2 private immutable lpt;
    IMockUSDC private immutable usdc;
    bytes32 public immutable poolCodeHash;

    uint256 public lastBuyRound;
    address public observedPool;

    /// O0, the older observation, and O1, the newer.
    uint256 private cum0;
    uint256 private time0;
    uint256 private cum1;
    uint256 private time1;

    /// The round's pool as one buy or poke reads it: USDC's and LPT's
    /// reserves and the cumulative price of LPT in µUSDC per wei now.
    struct Pool {
        address at;
        bool usdcIs0;
        uint256 ru;
        uint256 rl;
        uint256 cumNow;
    }

    constructor(
        IBuybackValues values_,
        ITicketBrokerV2 brokerAddress,
        IRoundClock clock_,
        ITestLPTV2 lpt_,
        IMockUSDC usdc_,
        bytes32 poolCodeHash_
    ) {
        source = values_;
        broker_ = brokerAddress;
        clock = clock_;
        lpt = lpt_;
        usdc = usdc_;
        poolCodeHash = poolCodeHash_;
    }

    function values() external view returns (address) {
        return address(source);
    }

    function broker() external view returns (address) {
        return address(broker_);
    }

    function observations() external view returns (uint256, uint256, uint256, uint256) {
        return (cum0, time0, cum1, time1);
    }

    /// One buy's figures, before anything moves.
    struct Quote {
        uint256 held;
        uint256 twapX112;
        uint256 maxIn;
        uint256 lptOut;
        uint256 usdcIn;
    }

    /// R-16 rules 2 to 7, refusals in rule 11's order.
    function buy() external returns (uint256 usdcIn, uint256 lptBought, uint256 burned) {
        uint256 round = clock.currentRound();
        IBuybackValues.BuybackValues memory v = source.buybackValuesAt(round);
        if (lastBuyRound != 0 && round < lastBuyRound + v.buyEveryRounds) {
            revert ALREADY_BOUGHT(lastBuyRound + v.buyEveryRounds);
        }
        uint256 held = broker_.networkHeld();
        if (held == 0) revert NOTHING_HELD();
        Pool memory p = _read(v.pool);
        Quote memory q = _quote(p, v, held);
        usdcIn = q.usdcIn;

        // Rule 7: record from the cumulative before the swap, then buy.
        if (block.timestamp - time1 >= v.twapSeconds) _record(p.at, p.cumNow);
        lastBuyRound = round;
        uint256 before = lpt.balanceOf(address(this));
        broker_.drawNetwork(usdcIn, p.at);
        (uint256 out0, uint256 out1) = p.usdcIs0 ? (uint256(0), q.lptOut) : (q.lptOut, uint256(0));
        ITestPool(p.at).swap(out0, out1, address(this), "");
        lptBought = lpt.balanceOf(address(this)) - before;
        if (lptBought < q.lptOut) revert LPT_SHORT(q.lptOut, lptBought);
        burned = lptBought * v.burnPpm / ONE;
        lpt.burn(burned);
        emit Bought(round, held, usdcIn, lptBought, burned, q.twapX112, q.maxIn);
    }

    /// Rules 3 to 5: the TWAP, the bound and the amount.
    function _quote(Pool memory p, IBuybackValues.BuybackValues memory v, uint256 held)
        private
        view
        returns (Quote memory q)
    {
        q.held = held;
        // Rule 3: O1 when it is an interval old, else O0; nothing observed
        // of this pool is elapsed 0.
        uint256 elapsed;
        uint256 from;
        if (observedPool == p.at) {
            (from, elapsed) = block.timestamp - time1 >= v.twapSeconds
                ? (cum1, block.timestamp - time1)
                : (cum0, block.timestamp - time0);
        }
        if (elapsed < v.twapSeconds) revert TWAP_NOT_READY(elapsed, v.twapSeconds);
        unchecked {
            q.twapX112 = (p.cumNow - from) / elapsed;
        }
        // Rule 4: the bound.
        q.maxIn = _maxIn(_mulDiv(q.twapX112, ONE + v.maxSlippagePpm, ONE), p.ru, p.rl);
        // Rule 5: the amount.
        uint256 cap = held < q.maxIn ? held : q.maxIn;
        if (cap > 0) q.lptOut = _mulDiv(cap, 997 * p.rl, 1000 * p.ru + 997 * cap);
        if (q.lptOut == 0) revert SLIPPAGE(held, q.maxIn);
        q.usdcIn = _mulDivUp(1000 * p.ru, q.lptOut, 997 * (p.rl - q.lptOut));
    }

    /// R-16 rule 8, refusals in rule 11's order.
    function poke() external {
        IBuybackValues.BuybackValues memory v = source.buybackValuesAt(clock.currentRound());
        Pool memory p = _read(v.pool);
        if (observedPool != p.at) {
            observedPool = p.at;
            (cum0, time0) = (p.cumNow, block.timestamp);
            (cum1, time1) = (p.cumNow, block.timestamp);
            emit Observed(p.at, p.cumNow, block.timestamp);
        } else if (block.timestamp - time1 >= v.twapSeconds) {
            _record(p.at, p.cumNow);
        } else {
            revert NOT_DUE(time1 + v.twapSeconds);
        }
    }

    // --- internals ------------------------------------------------------

    /// Rule 13, then the reserves (NO_LIQUIDITY) and the cumulative price
    /// of LPT in µUSDC per wei now, as v2's oracle library reads it: the
    /// stored cumulative plus the spot price times the seconds since the
    /// pool's last update.
    function _read(address pool) private view returns (Pool memory p) {
        if (pool.code.length == 0 || pool.codehash != poolCodeHash) revert POOL_INVALID(pool);
        ITestPool t = ITestPool(pool);
        (address t0, address t1) = (t.token0(), t.token1());
        bool usdcIs0 = t0 == address(usdc) && t1 == address(lpt);
        if (!usdcIs0 && !(t0 == address(lpt) && t1 == address(usdc))) revert POOL_INVALID(pool);
        (uint112 r0, uint112 r1, uint32 last) = t.getReserves();
        if (r0 == 0 || r1 == 0) revert NO_LIQUIDITY();
        p.at = pool;
        p.usdcIs0 = usdcIs0;
        (p.ru, p.rl) = usdcIs0 ? (uint256(r0), uint256(r1)) : (uint256(r1), uint256(r0));
        // LPT's price in USDC is token1's in token0 when USDC is token0.
        uint256 stored = usdcIs0 ? t.price1CumulativeLast() : t.price0CumulativeLast();
        unchecked {
            uint32 since = uint32(block.timestamp % 2 ** 32) - last;
            p.cumNow = stored + uint256((uint224(p.ru) << 112) / uint224(p.rl)) * since;
        }
    }

    function _record(address pool, uint256 cumNow) private {
        (cum0, time0) = (cum1, time1);
        (cum1, time1) = (cumNow, block.timestamp);
        emit Observed(pool, cumNow, block.timestamp);
    }

    /// Rule 4: maxIn = (p * 997 * rl - (1000 * ru << 112)) / (997 << 112)
    /// when the product is above the base, else 0, at full width.
    function _maxIn(uint256 price, uint256 ru, uint256 rl) private pure returns (uint256) {
        (uint256 hi, uint256 lo) = _mul512(price, 997 * rl);
        uint256 base = (1000 * ru) << 112;
        if (hi == 0 && lo <= base) return 0;
        unchecked {
            if (lo < base) hi -= 1;
            lo -= base;
        }
        return _div512(hi, lo, 997 << 112);
    }

    /// floor(x * y / d) at full width (OpenZeppelin's Math.mulDiv).
    function _mulDiv(uint256 x, uint256 y, uint256 d) private pure returns (uint256) {
        (uint256 hi, uint256 lo) = _mul512(x, y);
        return _div512(hi, lo, d);
    }

    /// ceil(x * y / d) at full width.
    function _mulDivUp(uint256 x, uint256 y, uint256 d) private pure returns (uint256 q) {
        q = _mulDiv(x, y, d);
        if (mulmod(x, y, d) != 0) q += 1;
    }

    /// The 512-bit product of x and y as (hi, lo).
    function _mul512(uint256 x, uint256 y) private pure returns (uint256 hi, uint256 lo) {
        assembly ("memory-safe") {
            let mm := mulmod(x, y, not(0))
            lo := mul(x, y)
            hi := sub(sub(mm, lo), lt(mm, lo))
        }
    }

    /// floor((hi * 2^256 + lo) / d), which must fit in 256 bits (hi < d),
    /// by Remco Bloemen's method as OpenZeppelin's Math.mulDiv takes it.
    function _div512(uint256 hi, uint256 lo, uint256 d) private pure returns (uint256 result) {
        if (hi == 0) return lo / d;
        require(d > hi, "mulDiv overflow");
        assembly ("memory-safe") {
            // Make the division exact by subtracting the remainder.
            // rem = (hi * (2^256 mod d) + lo) mod d.
            let rem := addmod(mulmod(hi, addmod(mod(not(0), d), 1, d), d), mod(lo, d), d)
            hi := sub(hi, gt(rem, lo))
            lo := sub(lo, rem)

            // Factor the powers of two out of d.
            let twos := and(d, sub(0, d))
            d := div(d, twos)
            lo := div(lo, twos)
            twos := add(div(sub(0, twos), twos), 1)
            lo := or(lo, mul(hi, twos))

            // The inverse of d modulo 2^256, by Newton-Raphson.
            let inverse := xor(mul(3, d), 2)
            inverse := mul(inverse, sub(2, mul(d, inverse)))
            inverse := mul(inverse, sub(2, mul(d, inverse)))
            inverse := mul(inverse, sub(2, mul(d, inverse)))
            inverse := mul(inverse, sub(2, mul(d, inverse)))
            inverse := mul(inverse, sub(2, mul(d, inverse)))
            inverse := mul(inverse, sub(2, mul(d, inverse)))
            result := mul(lo, inverse)
        }
    }
}
