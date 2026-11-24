// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {vm, Vm} from "./Vm.sol";
import {FullFixture, ValuesStub, LyingPool, OtherExecutor} from "./FullFixture.sol";
import {TestPool} from "../src/money/TestPool.sol";
import {TestLPT} from "../src/money/TestLPT.sol";
import {Broker} from "../src/money/Broker.sol";
import {Buyback} from "../src/money/Buyback.sol";
import {IParameters} from "../interfaces/IParameters.sol";
import {IBuyback} from "../interfaces/IBuyback.sol";
import {IBuybackExecutor} from "../interfaces/IBuybackExecutor.sol";
import {IBuybackValues} from "../interfaces/IBuybackValues.sol";

/// What the shared examples cannot show (R-16): lying pools, a pool of
/// another pair under its own hash, a pool that delivers short or over,
/// each refusal of rule 11 in its order, a change of pool, a bound whose
/// product passes 2^256, the draw, the burn and the values' ranges.
contract BuybackRefusalsTest is FullFixture {
    uint256 private constant GENESIS = 10_000_000e18;
    uint256 private constant HOUR = 3600;
    uint256 private constant T0 = 1_000_000;

    function setUp() external {
        loadVectors("october-main-text-v5");
        vm.warp(T0);
    }

    // --- rule 13: the pool is authentic -------------------------------------

    /// A pool of the right two tokens that reports invented reserves or
    /// cumulatives, TestPool's code over another token, and an address
    /// with no code are refused POOL_INVALID by buy and poke, held or not.
    function test_lying_pools_are_refused() external {
        deployFull(GENESIS, STUB);
        seed(pool, 500_000e6, 100_000e18);
        job(1000e6);

        LyingPool liar = new LyingPool(address(usdc), address(lpt));
        liar.tell(500_000e6, 100_000e18, 0, 0);
        TestLPT other = new TestLPT(who("Claims"), GENESIS);
        TestPool foreign = new TestPool(address(usdc), address(other));
        usdc.mint(address(foreign), 500_000e6);
        require(other.transfer(address(foreign), 100_000e18), "seed foreign");
        foreign.mint(address(this));
        // Its tokens are immutables in its runtime code, so its code hash
        // is not the pinned one and the hash alone refuses it. The pair
        // check behind the hash is reached in the next test.
        require(address(foreign).codehash != address(pool).codehash, "another token, another code hash");

        address[3] memory bad = [address(liar), address(foreign), who("no code")];
        for (uint256 i = 0; i < bad.length; i++) {
            usePool(bad[i]);
            expectRefused(poke(), poolInvalid(bad[i]), "poke of a lying pool");
            expectRefused(buy(), poolInvalid(bad[i]), "buy from a lying pool");
        }
        // TestPool's code over the same two tokens, in either order, passes.
        TestPool twin = new TestPool(address(lpt), address(usdc));
        require(address(twin).codehash == address(pool).codehash, "one code hash for the pair");
        seed(twin, 1000e6, 200e18);
        usePool(address(twin));
        require(poke().length == 0, "poke of the twin");
    }

    /// Rule 13's second half: a Buyback deployed with the code hash of a
    /// TestPool over another pair, and pointed at that pool, passes the hash
    /// but not the pair. Both a pool of USDC and another token and a pool of
    /// another token and LPT are refused POOL_INVALID by poke and buy.
    function test_a_pool_of_another_pair_is_refused_under_its_own_hash() external {
        deployFull(GENESIS, STUB);
        job(1000e6);
        TestLPT other = new TestLPT(who("Claims"), GENESIS);
        TestPool[2] memory foreign =
            [new TestPool(address(usdc), address(other)), new TestPool(address(other), address(lpt))];
        // The pool of other and LPT: LPT from the deployer, other as its pair.
        usdc.mint(address(foreign[0]), 500_000e6);
        require(other.transfer(address(foreign[0]), 100_000e18), "seed the USDC pool");
        foreign[0].mint(address(this));
        require(other.transfer(address(foreign[1]), 100_000e18), "seed the LPT pool");
        require(lpt.transfer(address(foreign[1]), 100_000e18), "seed the LPT pool");
        foreign[1].mint(address(this));

        for (uint256 i = 0; i < foreign.length; i++) {
            address f = address(foreign[i]);
            ValuesStub named = new ValuesStub(
                IBuybackValues.BuybackValues(
                    f,
                    param("buyback_burn_ppm"),
                    param("buyback_twap_seconds"),
                    param("buyback_max_slippage_ppm"),
                    param("buyback_every_rounds")
                )
            );
            Buyback wired = new Buyback(named, broker, clock, lpt, usdc, f.codehash);
            require(wired.poolCodeHash() == f.codehash, "the hash passes");
            expectRefused(
                as_(who("Anyone"), address(wired), abi.encodeCall(IBuybackExecutor.poke, ())), poolInvalid(f), "poke"
            );
            vm.warp(block.timestamp + HOUR);
            expectRefused(
                as_(who("Anyone"), address(wired), abi.encodeCall(IBuybackExecutor.buy, ())), poolInvalid(f), "buy"
            );
        }
    }

    /// A pool that delivers one wei less than it quotes, admitted by a
    /// Buyback pinned to its hash: LPT_SHORT, and the held share, the pool
    /// and the LPT sent to the Buyback beforehand are as they were.
    function test_short_delivery_is_refused_and_moves_nothing() external {
        deployFull(GENESIS, SHORT);
        require(lpt.transfer(address(shortPool), 200e18), "the short pool's LPT");
        require(lpt.transfer(address(buyback), 3e18), "LPT sent beforehand");
        require(poke().length == 0, "poke");
        job(10e6);
        expectRefused(buy(), err("TWAP_NOT_READY(uint256,uint256)", 0, HOUR), "before the interval");
        vm.warp(block.timestamp + HOUR);

        uint256 held = broker.networkHeld();
        uint256 poolUsdc = usdc.balanceOf(address(shortPool));
        uint256 poolLpt = lpt.balanceOf(address(shortPool));
        uint256 supply = lpt.totalSupply();
        (uint256 c0, uint256 t0, uint256 c1, uint256 t1) = buyback.observations();
        bytes memory ret = buy();
        require(bytes4(ret) == IBuyback.LPT_SHORT.selector, string.concat("want LPT_SHORT, got ", vm.toString(ret)));
        (uint256 quoted, uint256 received) = abi.decode(slice4(ret), (uint256, uint256));
        require(quoted > 0 && received == quoted - 1, "quoted and received");
        expectEq(broker.networkHeld(), held, "held share");
        expectEq(usdc.balanceOf(address(shortPool)), poolUsdc, "the pool's USDC");
        expectEq(lpt.balanceOf(address(shortPool)), poolLpt, "the pool's LPT");
        expectEq(lpt.balanceOf(address(buyback)), 3e18, "the LPT sent beforehand");
        expectEq(lpt.totalSupply(), supply, "supply");
        expectEq(buyback.lastBuyRound(), 0, "the round is not used");
        (uint256 d0, uint256 u0, uint256 d1, uint256 u1) = buyback.observations();
        require(d0 == c0 && u0 == t0 && d1 == c1 && u1 == t1, "the recording is undone");
    }

    /// A pool that delivers one wei more than it quotes, admitted by a
    /// Buyback pinned to its hash: the buy takes effect and burns what
    /// arrived, the quote and the extra wei, not the quote; the LPT sent
    /// to the Buyback beforehand stays.
    function test_over_delivery_burns_what_arrived() external {
        deployFull(GENESIS, OVER);
        require(lpt.transfer(address(shortPool), 200e18), "the pool's LPT");
        require(lpt.transfer(address(buyback), 3e18), "LPT sent beforehand");
        require(poke().length == 0, "poke");
        job(10e6);
        vm.warp(block.timestamp + HOUR);
        uint256 poolLpt = lpt.balanceOf(address(shortPool));
        uint256 supply = lpt.totalSupply();
        vm.recordLogs();
        require(buy().length == 0, "the buy");
        bytes[] memory logs = boughtLogs();
        require(logs.length == 1, "one Bought event");
        (,, uint256 lptBought, uint256 burned,,) =
            abi.decode(logs[0], (uint256, uint256, uint256, uint256, uint256, uint256));
        uint256 arrived = poolLpt - lpt.balanceOf(address(shortPool));
        require(arrived > 1, "something bought");
        expectEq(lptBought, arrived, "lptBought is what arrived");
        expectEq(burned, arrived, "burned is what arrived");
        expectEq(supply - lpt.totalSupply(), arrived, "supply fell by what arrived");
        expectEq(lpt.balanceOf(address(buyback)), 3e18, "the LPT sent beforehand");
    }

    // --- rule 11: each refusal in its order ---------------------------------

    /// Buy: each refusal, and where two apply, the earlier one.
    function test_buy_refusals_in_order() external {
        deployFull(GENESIS, STUB);
        LyingPool empty = new LyingPool(address(usdc), address(lpt));

        // NOTHING_HELD before POOL_INVALID.
        usePool(address(empty));
        expectRefused(buy(), err("NOTHING_HELD()"), "nothing held, a lying pool");
        // POOL_INVALID before NO_LIQUIDITY: the liar reports no reserves.
        job(1000e6);
        expectRefused(buy(), poolInvalid(address(empty)), "a lying pool with no reserves");
        // NO_LIQUIDITY before TWAP_NOT_READY: an authentic empty pool, never observed.
        usePool(address(pool));
        expectRefused(buy(), err("NO_LIQUIDITY()"), "an empty pool");
        expectRefused(poke(), err("NO_LIQUIDITY()"), "poke of an empty pool");
        // TWAP_NOT_READY: nothing observed of this pool, then a short window.
        seed(pool, 500_000e6, 100_000e18);
        expectRefused(buy(), err("TWAP_NOT_READY(uint256,uint256)", 0, HOUR), "nothing observed");
        require(poke().length == 0, "poke");
        vm.warp(block.timestamp + HOUR - 1);
        // TWAP_NOT_READY before SLIPPAGE: a trader moves the price a second
        // before the window is long enough.
        swapIn(pool, 50_000e6);
        expectRefused(buy(), err("TWAP_NOT_READY(uint256,uint256)", HOUR - 1, HOUR), "a window a second short");
        vm.warp(block.timestamp + 1);
        expectRefused(buy(), err("SLIPPAGE(uint256,uint256)", 500e6, 0), "spot above the bound");
        // A refused buy does not use the round: after the average follows,
        // the round's buy succeeds, and then ALREADY_BOUGHT before
        // NOTHING_HELD.
        vm.warp(block.timestamp + 2 * HOUR);
        require(poke().length == 0, "poke");
        vm.warp(block.timestamp + HOUR);
        require(buy().length == 0, "buy");
        expectEq(broker.networkHeld(), 0, "all of it bought");
        expectRefused(buy(), err("ALREADY_BOUGHT(uint256)", clock.currentRound() + 1), "the round's second buy");
        toRound(clock.currentRound() + 1);
        expectRefused(buy(), err("NOTHING_HELD()"), "the next round, nothing held");
    }

    /// Poke: POOL_INVALID, NO_LIQUIDITY, NOT_DUE with the time it is due.
    function test_poke_refusals_in_order() external {
        deployFull(GENESIS, STUB);
        LyingPool empty = new LyingPool(address(usdc), address(lpt));
        usePool(address(empty));
        expectRefused(poke(), poolInvalid(address(empty)), "a lying pool with no reserves");
        usePool(address(pool));
        expectRefused(poke(), err("NO_LIQUIDITY()"), "an empty pool");
        seed(pool, 500_000e6, 100_000e18);
        require(poke().length == 0, "the first poke");
        expectRefused(poke(), err("NOT_DUE(uint256)", T0 + HOUR), "at once");
        vm.warp(T0 + HOUR - 1);
        expectRefused(poke(), err("NOT_DUE(uint256)", T0 + HOUR), "a second early");
        vm.warp(T0 + HOUR);
        require(poke().length == 0, "due");
        (uint256 c0, uint256 t0, uint256 c1, uint256 t1) = buyback.observations();
        require(t0 == T0 && t1 == T0 + HOUR && c1 - c0 == (uint256(500_000e6) << 112) / 100_000e18 * HOUR, "slots");
    }

    // --- rule 3: the switch from O0 to O1 -----------------------------------

    /// R-16 rule 3 at the second the buy switches observations. An hour at
    /// 5 USDC per LPT, a trader selling 10,000 LPT, a poke: O0 starts the
    /// first hour and O1 the second. A second before O1 is an interval old
    /// the buy prices from O0, over both hours; at that second it prices
    /// from O1, over the second hour alone. Both buys are from the same
    /// state, with 100,000 USDC held, more than either bound allows.
    function test_the_twap_switches_from_o0_to_o1_when_o1_is_an_interval_old() external {
        deployFull(GENESIS, STUB);
        seed(pool, 500_000e6, 100_000e18);
        require(poke().length == 0, "poke at T0");
        job(200_000e6);
        uint256 held = broker.networkHeld();
        (uint256 ru, uint256 rl, bool usdcIs0) = reserves(pool);
        uint256 a = (ru << 112) / rl;
        vm.warp(T0 + HOUR);
        {
            uint256 sold = 10_000e18;
            uint256 out = sold * 997 * ru / (rl * 1000 + sold * 997);
            require(lpt.transfer(address(pool), sold), "sell LPT");
            pool.swap(usdcIs0 ? out : 0, usdcIs0 ? 0 : out, who("Trader"), "");
        }
        require(poke().length == 0, "poke at T0 + HOUR");
        (ru, rl,) = reserves(pool);
        uint256 b = (ru << 112) / rl;

        uint256 before = vm.snapshotState();
        vm.warp(T0 + 2 * HOUR - 1);
        uint256[6] memory o0 = boughtAt();
        expectEq(o0[4], (a * HOUR + b * (HOUR - 1)) / (2 * HOUR - 1), "a second early: the TWAP of both hours");
        expectEq(o0[5], maxInAt(o0[4], ru, rl), "a second early: maxIn");
        require(vm.revertToState(before), "revert");
        vm.warp(T0 + 2 * HOUR);
        uint256[6] memory o1 = boughtAt();
        expectEq(o1[4], b, "on time: the TWAP of the second hour");
        expectEq(o1[5], maxInAt(b, ru, rl), "on time: maxIn");
        // Neither bound reaches what is held: each buy spends its bound.
        require(o0[5] < held && o1[5] < held, "the bound is the cap");
        require(o0[1] > o1[1], "the earlier buy spends more");
    }

    /// One buy now; the Bought figures in the event's order.
    function boughtAt() internal returns (uint256[6] memory got) {
        vm.recordLogs();
        bytes memory ret = buy();
        require(ret.length == 0, string.concat("the buy: refused ", vm.toString(ret)));
        bytes[] memory logs = boughtLogs();
        require(logs.length == 1, "one Bought event");
        got = abi.decode(logs[0], (uint256[6]));
    }

    /// Rule 4's maxIn in 256 bits, for a TWAP small enough not to need more.
    function maxInAt(uint256 twap, uint256 ru, uint256 rl) internal view returns (uint256) {
        uint256 lim = twap * (1e6 + param("buyback_max_slippage_ppm")) / 1e6 * 997 * rl;
        uint256 base = (1000 * ru) << 112;
        return lim > base ? (lim - base) / (997 << 112) : 0;
    }

    /// Draw: NOT_AUTHORIZED from any address but group 10's, the amount
    /// aside; then AMOUNT_TOO_LARGE above what is held.
    function test_draw_refusals_in_order() external {
        deployFull(GENESIS, STUB);
        job(1000e6);
        uint256 held = broker.networkHeld();
        bytes memory over = abi.encodeCall(Broker.drawNetwork, (held + 1, who("anyone")));
        bytes memory within = abi.encodeCall(Broker.drawNetwork, (held, who("anyone")));
        expectRefused(as_(who("Anyone"), address(broker), over), err("NOT_AUTHORIZED()"), "another address, too much");
        expectRefused(as_(who("Anyone"), address(broker), within), err("NOT_AUTHORIZED()"), "another address");
        expectRefused(as_(address(buyback), address(broker), over), err("AMOUNT_TOO_LARGE()"), "above held");
        require(params.valuesAt(10, clock.currentRound())[0] == uint256(uint160(address(buyback))), "group 10");
        require(buyback.broker() == address(broker), "the Buyback's Broker");
        require(as_(address(buyback), address(broker), within).length == 0, "the Buyback draws");
        expectEq(broker.networkHeld(), 0, "held after the draw");
        expectEq(usdc.balanceOf(who("anyone")), held, "paid to");
    }

    /// A deployment with no Buyback has group 10 address(0), and nobody
    /// may draw.
    function test_no_buyback_no_draw() external {
        deploy();
        require(params.valuesAt(10, clock.currentRound())[0] == 0, "no executor");
        bytes memory d = abi.encodeCall(Broker.drawNetwork, (0, who("anyone")));
        expectRefused(as_(address(0), address(broker), d), err("NOT_AUTHORIZED()"), "address(0)");
        expectRefused(as_(address(this), address(broker), d), err("NOT_AUTHORIZED()"), "the deployer");
    }

    // --- rule 10: the draw follows group 10, by round (D-51) ----------------

    /// The parameters-executor case's draws, which a case does not hold.
    /// X, the deployment's Buyback, buys from P. X3, an executor of another
    /// design, accepts only Q, a pool that is not a TestPool. In round A the
    /// authority announces X3 for group 10, counting from A + 7, and in
    /// A + 1 Q for group 9, which X3, pending, is asked about and accepts,
    /// counting from A + 8. Up to A + 6 only X draws: X3's draw is refused
    /// NOT_AUTHORIZED and X buys. In A + 7 the executor is X3 and the pool
    /// still P: X's buy, which X accepts, is refused at the draw with
    /// NOT_AUTHORIZED, and no balance moves; X3 refuses P. From A + 8 X3
    /// draws all that is held to Q.
    function test_the_draw_follows_the_executor_of_its_round() external {
        deployFull(GENESIS, 0);
        seed(pool, 500_000e6, 100_000e18);
        require(poke().length == 0, "poke P");
        vm.warp(block.timestamp + HOUR);
        LyingPool q = new LyingPool(address(usdc), address(lpt));
        OtherExecutor x3 = new OtherExecutor(broker, params, clock);
        x3.accept(address(q));
        require(!buyback.acceptsPool(address(q)) && buyback.acceptsPool(address(pool)), "X accepts P, not Q");
        bytes memory x3buy = abi.encodeCall(IBuybackExecutor.buy, ());
        bytes memory x3draw = abi.encodeCall(Broker.drawNetwork, (1, address(q)));

        uint256 a = clock.currentRound();
        uint256[] memory v = new uint256[](4);
        (v[0], v[1], v[2], v[3]) = (uint256(uint160(address(q))), HOUR, 10_000, 1);
        expectRefused(announce(9, v), poolInvalid(address(q)), "Q, before X3 is pending, is put to X");
        uint256[] memory e = new uint256[](1);
        e[0] = uint256(uint160(address(x3)));
        expectRefused(announce(10, e), "", "X3 for group 10");
        toRound(a + 1);
        expectRefused(announce(9, v), "", "Q, put to X3, pending");

        // A + 6: X is the executor.
        toRound(a + 6);
        job(1000e6);
        vm.warp(block.timestamp + HOUR);
        expectRefused(as_(address(x3), address(broker), x3draw), err("NOT_AUTHORIZED()"), "X3 draws in A + 6");
        require(buy().length == 0, "X buys in A + 6");
        expectEq(broker.networkHeld(), 0, "A + 6: all bought");

        // A + 7: X3 is the executor, the pool still P.
        toRound(a + 7);
        job(1000e6);
        vm.warp(block.timestamp + HOUR);
        uint256 held = broker.networkHeld();
        uint256[4] memory was = [
            usdc.balanceOf(address(broker)),
            usdc.balanceOf(address(pool)),
            lpt.balanceOf(address(pool)),
            lpt.totalSupply()
        ];
        uint256 last = buyback.lastBuyRound();
        expectRefused(buy(), err("NOT_AUTHORIZED()"), "X buys in A + 7");
        expectEq(broker.networkHeld(), held, "A + 7: held");
        uint256[4] memory now_ = [
            usdc.balanceOf(address(broker)),
            usdc.balanceOf(address(pool)),
            lpt.balanceOf(address(pool)),
            lpt.totalSupply()
        ];
        for (uint256 i = 0; i < 4; i++) {
            expectEq(now_[i], was[i], "A + 7: a balance moved");
        }
        expectEq(buyback.lastBuyRound(), last, "A + 7: the round is not used");
        expectRefused(as_(who("Anyone"), address(x3), x3buy), poolInvalid(address(pool)), "X3 refuses P");

        // A + 8: X3 draws to Q; X still may not.
        toRound(a + 8);
        expectRefused(buy(), poolInvalid(address(q)), "X refuses Q");
        expectRefused(as_(address(buyback), address(broker), x3draw), err("NOT_AUTHORIZED()"), "X draws in A + 8");
        require(as_(who("Anyone"), address(x3), x3buy).length == 0, "X3 buys in A + 8");
        expectEq(broker.networkHeld(), 0, "A + 8: all drawn");
        expectEq(usdc.balanceOf(address(q)), held, "A + 8: paid to Q");
    }

    // --- rule 4 at full width ----------------------------------------------

    /// R-16 asks a product that can pass 2^256 to be taken at full width.
    /// Rule 4's lim = p x 997 x rl does when the TWAP is far above the
    /// present price and the LPT reserve is now large: here a past hour at
    /// 2^100 µUSDC per wei, then a trader selling 2^55 wei of LPT into the
    /// pool, and a buy at once. The buy takes effect, and maxIn and the
    /// buy's figures equal ones computed here without a 512-bit product.
    function test_the_bound_is_taken_at_full_width() external {
        deployFull(GENESIS, STUB);
        seed(pool, 2 ** 110, 2 ** 10);
        require(poke().length == 0, "poke");
        job(1000e6);
        vm.warp(block.timestamp + HOUR);
        // A trader sells LPT for USDC, v2's getAmountOut.
        {
            (uint256 ru0, uint256 rl0, bool usdcIs0) = reserves(pool);
            uint256 sold = 2 ** 55;
            uint256 out = sold * 997 * ru0 / (rl0 * 1000 + sold * 997);
            require(lpt.transfer(address(pool), sold), "sell LPT");
            pool.swap(usdcIs0 ? out : 0, usdcIs0 ? 0 : out, who("Trader"), "");
        }
        uint256[6] memory want = fullWidthBuy();
        uint256 supply = lpt.totalSupply();
        vm.recordLogs();
        bytes memory ret = buy();
        require(ret.length == 0, string.concat("the buy: refused ", vm.toString(ret)));
        bytes[] memory logs = boughtLogs();
        require(logs.length == 1, "one Bought event");
        uint256[6] memory got = abi.decode(logs[0], (uint256[6]));
        string[6] memory names = ["held", "usdcIn", "lptBought", "burned", "twapX112", "maxIn"];
        for (uint256 i = 0; i < 6; i++) {
            expectEq(got[i], want[i], names[i]);
        }
        expectEq(supply - lpt.totalSupply(), want[3], "supply");
        expectEq(broker.networkHeld(), want[0] - want[1], "held after");
    }

    /// The Bought figures the full-width test expects, in the event's
    /// order, from the pool's reserves and the held share now.
    function fullWidthBuy() internal view returns (uint256[6] memory w) {
        (uint256 ru, uint256 rl,) = reserves(pool);
        uint256 held = broker.networkHeld();
        // The TWAP: one hour at (2^110 << 112) / 2^10.
        uint256 twap = 2 ** 212;
        uint256 p = twap * (1e6 + param("buyback_max_slippage_ppm")) / 1e6;
        // lim = p x 997 x rl passes 2^256 ...
        require(p > type(uint256).max / (997 * rl), "lim passes 2^256");
        // ... so with p = (a << 112) + b, lim - base = ((a x 997 x rl -
        // 1000 x ru) << 112) + b x 997 x rl; each part is divided by
        // 997 << 112 in 256 bits.
        uint256 x = (p >> 112) * 997 * rl - 1000 * ru;
        uint256 y = (p & (2 ** 112 - 1)) * 997 * rl;
        uint256 maxIn = x / 997 + (((x % 997) << 112) + y) / (997 << 112);
        require(held < maxIn, "held is the cap");
        uint256 lptOut = held * 997 * rl / (1000 * ru + 997 * held);
        require(lptOut > 0, "something to buy");
        uint256 usdcIn = (1000 * ru * lptOut + 997 * (rl - lptOut) - 1) / (997 * (rl - lptOut));
        w = [held, usdcIn, lptOut, lptOut, twap, maxIn];
    }

    // --- rule 9: the burn -------------------------------------------------

    /// Anyone burns its own: balance and supply fall, Transfer to 0. Above
    /// the balance it is refused as a transfer is. A transfer to 0xdEaD
    /// leaves supply as it was.
    function test_burn() external {
        deployFull(GENESIS, STUB);
        require(lpt.transfer(who("H"), 5e18), "give");
        vm.recordLogs();
        vm.prank(who("H"));
        lpt.burn(2e18);
        expectEq(lpt.balanceOf(who("H")), 3e18, "balance");
        expectEq(lpt.totalSupply(), GENESIS - 2e18, "supply");
        bytes32 transfer = keccak256("Transfer(address,address,uint256)");
        Vm.Log[] memory logs = vm.getRecordedLogs();
        require(
            logs.length == 1 && logs[0].topics[0] == transfer
                && logs[0].topics[1] == bytes32(uint256(uint160(who("H")))) && logs[0].topics[2] == bytes32(0)
                && abi.decode(logs[0].data, (uint256)) == 2e18,
            "Transfer(H, 0, 2e18)"
        );
        bytes memory more = abi.encodeCall(TestLPT.burn, (3e18 + 1));
        require(eq(code(as_(who("H"), address(lpt), more)), "balance too low"), "above the balance");
        require(lpt.transfer(address(0xdEaD), 1e18), "to 0xdEaD");
        expectEq(lpt.totalSupply(), GENESIS - 2e18, "a transfer burns nothing");
    }

    // --- 3.1: the values' ranges --------------------------------------------

    /// Parameters takes each of group 9's values at either end of D-47
    /// (v)'s range, and group 8's one value (10^6, 0, 0), and refuses one
    /// past either end with VALUE_OUT_OF_RANGE (R-25).
    function test_values_ranges() external {
        deployFull(GENESIS, 0);
        uint256 p = uint256(uint160(address(pool)));
        uint256[4][4] memory ok =
            [[p, 1800, 5000, 1], [p, 7200, 50_000, 840], [p, 3600, 10_000, 1], [p, 1800, 50_000, 840]];
        for (uint256 i = 0; i < ok.length; i++) {
            expectRefused(announce(9, _four(ok[i])), "", string.concat("in range: case ", vm.toString(i)));
        }
        uint256[4][6] memory bad = [
            [p, 1799, 10_000, 1],
            [p, 7201, 10_000, 1],
            [p, 3600, 4999, 1],
            [p, 3600, 50_001, 1],
            [p, 3600, 10_000, 0],
            [p, 3600, 10_000, 841]
        ];
        for (uint256 i = 0; i < bad.length; i++) {
            expectRefused(
                announce(9, _four(bad[i])), err("VALUE_OUT_OF_RANGE()"), string.concat("case ", vm.toString(i))
            );
        }
        uint256[] memory cuts = new uint256[](3);
        cuts[0] = 1e6;
        expectRefused(announce(8, cuts), "", "the burn, whole");
        cuts[0] = 999_999;
        cuts[1] = 1;
        expectRefused(announce(8, cuts), err("VALUE_OUT_OF_RANGE()"), "not all burnt");
    }

    // --- R-25: a buy reads the values of its round -------------------------

    /// A buy reads groups 8 and 9 in force for the round it runs in. A
    /// change of group 9 announced in round A counts from A + 7: a buy in
    /// A + 6 still buys from the deployment's pool, and from A + 7 the buy
    /// follows the new interval, pool and TWAP window. Group 8 can
    /// hold only (10^6, 0, 0), so its reading shows as the whole buy burnt.
    function test_a_buy_reads_the_values_of_its_round() external {
        deployFull(GENESIS, 0);
        require(address(source) == address(params), "the values are Parameters'");
        seed(pool, 500_000e6, 100_000e18);
        require(poke().length == 0, "poke");
        vm.warp(block.timestamp + HOUR);
        job(1000e6);
        require(buy().length == 0, "a buy from the deployment's pool");
        uint256 a = clock.currentRound();

        TestPool next = new TestPool(address(usdc), address(lpt));
        seed(next, 400_000e6, 100_000e18);
        uint256[] memory v = new uint256[](4);
        (v[0], v[1], v[2], v[3]) = (uint256(uint160(address(next))), 2 * HOUR, 50_000, 2);
        expectRefused(announce(9, v), "", "the change of group 9");
        uint256[] memory cuts = new uint256[](3);
        cuts[0] = 1e6;
        expectRefused(announce(8, cuts), "", "group 8, as it is");
        for (uint256 r = a; r <= a + 7; r++) {
            IBuybackValues.BuybackValues memory w = params.buybackValuesAt(r);
            uint256[] memory g9 = params.valuesAt(9, r);
            require(w.pool == address(uint160(g9[0])), "the pool is group 9's");
            expectEq(w.twapSeconds, g9[1], "the window is group 9's");
            expectEq(w.maxSlippagePpm, g9[2], "the slippage is group 9's");
            expectEq(w.buyEveryRounds, g9[3], "the interval is group 9's");
            expectEq(w.burnPpm, params.valuesAt(8, r)[0], "the burn is group 8's");
            require(w.pool == (r < a + 7 ? address(pool) : address(next)), "the pool of the round");
        }

        // A + 6: the change is announced and does not count yet.
        toRound(a + 6);
        job(1000e6);
        vm.warp(block.timestamp + HOUR);
        uint256 old = usdc.balanceOf(address(pool));
        uint256 fresh = usdc.balanceOf(address(next));
        require(buy().length == 0, "a buy in A + 6");
        require(usdc.balanceOf(address(pool)) > old, "A + 6 buys from the deployment's pool");
        expectEq(usdc.balanceOf(address(next)), fresh, "and not from the new one");

        // A + 7: the interval is 2, so A + 6's buy holds the next to A + 8.
        toRound(a + 7);
        job(1000e6);
        vm.warp(block.timestamp + HOUR);
        expectRefused(buy(), err("ALREADY_BOUGHT(uint256)", a + 8), "every 2 rounds from A + 7");

        // A + 8: the new pool, observed for two hours, not one.
        toRound(a + 8);
        expectRefused(buy(), err("TWAP_NOT_READY(uint256,uint256)", 0, 2 * HOUR), "the new pool, unobserved");
        require(poke().length == 0, "poke of the new pool");
        vm.warp(block.timestamp + HOUR);
        expectRefused(buy(), err("TWAP_NOT_READY(uint256,uint256)", HOUR, 2 * HOUR), "one hour of two");
        vm.warp(block.timestamp + HOUR);
        uint256 supply = lpt.totalSupply();
        vm.recordLogs();
        require(buy().length == 0, "a buy in A + 8");
        require(usdc.balanceOf(address(next)) > fresh, "A + 8 buys from the new pool");
        bytes[] memory logs = boughtLogs();
        require(logs.length == 1, "one Bought event");
        (,, uint256 bought, uint256 burned,,) =
            abi.decode(logs[0], (uint256, uint256, uint256, uint256, uint256, uint256));
        expectEq(burned, bought, "group 8: the whole buy burnt");
        expectEq(supply - lpt.totalSupply(), burned, "supply");
    }

    function announce(uint256 group, uint256[] memory values) internal returns (bytes memory) {
        return as_(who("G"), address(params), abi.encodeCall(IParameters.announce, (group, values)));
    }

    function _four(uint256[4] memory v) internal pure returns (uint256[] memory out) {
        out = new uint256[](4);
        for (uint256 i = 0; i < 4; i++) {
            out[i] = v[i];
        }
    }

    // --- helpers ----------------------------------------------------------

    function usePool(address p) internal {
        ValuesStub(address(source))
            .set(
                IBuybackValues.BuybackValues(
                    p,
                    param("buyback_burn_ppm"),
                    param("buyback_twap_seconds"),
                    param("buyback_max_slippage_ppm"),
                    param("buyback_every_rounds")
                )
            );
    }

    function slice4(bytes memory b) internal pure returns (bytes memory out) {
        out = new bytes(b.length - 4);
        for (uint256 i = 0; i < out.length; i++) {
            out[i] = b[i + 4];
        }
    }
}
