// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {vm, Vm} from "./Vm.sol";
import {Stage4, ValuesStub, LyingPool} from "./Stage4.sol";
import {TestPool} from "../src/money/TestPool.sol";
import {TestLPT} from "../src/money/TestLPT.sol";
import {Broker} from "../src/money/Broker.sol";
import {FixedBuybackValues} from "../src/authority/FixedBuybackValues.sol";
import {IBuyback} from "../interfaces/IBuyback.sol";
import {IBuybackValues} from "../interfaces/IBuybackValues.sol";

/// What the shared examples cannot show (R-16): lying pools, a pool that
/// delivers short, each refusal of rule 11 in its order, a change of pool,
/// the draw, the burn and the values' ranges.
contract BuybackRefusalsTest is Stage4 {
    uint256 private constant GENESIS = 10_000_000e18;
    uint256 private constant HOUR = 3600;
    uint256 private constant T0 = 1_000_000;

    function setUp() external {
        loadVectors("october-main-text-v4");
        vm.warp(T0);
    }

    // --- rule 13: the pool is authentic -------------------------------------

    /// A pool of the right two tokens that reports invented reserves or
    /// cumulatives, TestPool's code over another token, and an address
    /// with no code are refused POOL_INVALID by buy and poke, held or not.
    function test_lying_pools_are_refused() external {
        deployStage4(GENESIS, true);
        seed(pool, 500_000e6, 100_000e18);
        job(1000e6);

        LyingPool liar = new LyingPool(address(usdc), address(lpt));
        liar.tell(500_000e6, 100_000e18, 0, 0);
        TestLPT other = new TestLPT(who("Claims"), GENESIS);
        TestPool foreign = new TestPool(address(usdc), address(other));
        usdc.mint(address(foreign), 500_000e6);
        require(other.transfer(address(foreign), 100_000e18), "seed foreign");
        foreign.mint(address(this));

        address[3] memory bad = [address(liar), address(foreign), who("no code")];
        for (uint256 i = 0; i < bad.length; i++) {
            usePool(bad[i]);
            expectRefused(poke(), poolInvalid(bad[i]), "poke of a lying pool");
            expectRefused(buy(), poolInvalid(bad[i]), "buy from a lying pool");
            vm.warp(block.timestamp + HOUR);
            liar.tell(1, 1, type(uint256).max, 12345);
            expectRefused(poke(), poolInvalid(bad[i]), "poke, invented cumulatives");
            expectRefused(buy(), poolInvalid(bad[i]), "buy, invented cumulatives");
        }
        // TestPool's code over the same two tokens, in either order, passes.
        TestPool twin = new TestPool(address(lpt), address(usdc));
        require(address(twin).codehash == address(pool).codehash, "one code hash for the pair");
        seed(twin, 1000e6, 200e18);
        usePool(address(twin));
        require(poke().length == 0, "poke of the twin");
    }

    /// A pool that delivers one wei less than it quotes, admitted by a
    /// Buyback pinned to its hash: LPT_SHORT, and the held share, the pool
    /// and the LPT sent to the Buyback beforehand are as they were.
    function test_short_delivery_is_refused_and_moves_nothing() external {
        deployStage4(GENESIS, false, true);
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

    // --- rule 11: each refusal in its order ---------------------------------

    /// Buy: each refusal, and where two apply, the earlier one.
    function test_buy_refusals_in_order() external {
        deployStage4(GENESIS, true);
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
        deployStage4(GENESIS, true);
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

    /// Draw: NOT_AUTHORIZED from any other address, the amount aside; then
    /// AMOUNT_TOO_LARGE above what is held.
    function test_draw_refusals_in_order() external {
        deployStage4(GENESIS, true);
        job(1000e6);
        uint256 held = broker.networkHeld();
        bytes memory over = abi.encodeCall(Broker.drawNetwork, (held + 1, who("anyone")));
        bytes memory within = abi.encodeCall(Broker.drawNetwork, (held, who("anyone")));
        address[4] memory others = [who("Anyone"), address(this), who("N"), address(pool)];
        for (uint256 i = 0; i < others.length; i++) {
            expectRefused(as_(others[i], address(broker), over), err("NOT_AUTHORIZED()"), "another address, too much");
            expectRefused(as_(others[i], address(broker), within), err("NOT_AUTHORIZED()"), "another address");
        }
        expectRefused(as_(address(buyback), address(broker), over), err("AMOUNT_TOO_LARGE()"), "above held");
        require(broker.buyback() == address(buyback) && buyback.broker() == address(broker), "fixed at deployment");
        require(as_(address(buyback), address(broker), within).length == 0, "the Buyback draws");
        expectEq(broker.networkHeld(), 0, "held after the draw");
        expectEq(usdc.balanceOf(who("anyone")), held, "paid to");
    }

    /// A Broker deployed with no Buyback lets nobody draw.
    function test_no_buyback_no_draw() external {
        deploy();
        require(broker.buyback() == address(0), "no Buyback");
        bytes memory d = abi.encodeCall(Broker.drawNetwork, (0, who("anyone")));
        expectRefused(as_(address(0), address(broker), d), err("NOT_AUTHORIZED()"), "address(0)");
        expectRefused(as_(address(this), address(broker), d), err("NOT_AUTHORIZED()"), "the deployer");
    }

    // --- a change of pool -------------------------------------------------

    /// After the values name another pool, buys refuse TWAP_NOT_READY until
    /// a poke and one interval, then succeed.
    function test_pool_change_waits_for_a_poke_and_an_interval() external {
        deployStage4(GENESIS, true);
        seed(pool, 500_000e6, 100_000e18);
        require(poke().length == 0, "poke");
        vm.warp(block.timestamp + HOUR);
        job(1000e6);
        require(buy().length == 0, "a buy from the first pool");

        TestPool next = new TestPool(address(usdc), address(lpt));
        seed(next, 400_000e6, 100_000e18);
        usePool(address(next));
        toRound(clock.currentRound() + 1);
        job(1000e6);
        vm.warp(block.timestamp + 5 * HOUR);
        expectRefused(buy(), err("TWAP_NOT_READY(uint256,uint256)", 0, HOUR), "another pool observed");
        require(poke().length == 0, "poke of the new pool");
        require(buyback.observedPool() == address(next), "observes the new pool");
        expectRefused(buy(), err("TWAP_NOT_READY(uint256,uint256)", 0, HOUR), "just poked");
        vm.warp(block.timestamp + HOUR - 1);
        expectRefused(buy(), err("TWAP_NOT_READY(uint256,uint256)", HOUR - 1, HOUR), "a second short");
        vm.warp(block.timestamp + 1);
        uint256 reserveBefore = usdc.balanceOf(address(next));
        require(buy().length == 0, "a buy from the new pool");
        expectEq(usdc.balanceOf(address(next)) - reserveBefore, 500e6, "paid to the new pool");
    }

    // --- rule 9: the burn -------------------------------------------------

    /// Anyone burns its own: balance and supply fall, Transfer to 0. Above
    /// the balance it is refused as a transfer is. A transfer to 0xdEaD
    /// leaves supply as it was.
    function test_burn() external {
        deployStage4(GENESIS, true);
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

    /// FixedBuybackValues takes each value at either end of D-47 (v)'s range
    /// and refuses one past either end with VALUE_OUT_OF_RANGE.
    function test_fixed_values_ranges() external {
        address p = who("pool");
        uint256[5][4] memory ok = [
            [uint256(1e6), 1800, 5000, 1, 0],
            [uint256(1e6), 7200, 50_000, 840, 0],
            [uint256(1e6), 3600, 10_000, 1, 0],
            [uint256(1e6), 1800, 50_000, 840, 0]
        ];
        for (uint256 i = 0; i < ok.length; i++) {
            FixedBuybackValues f = new FixedBuybackValues(p, ok[i][0], ok[i][1], ok[i][2], ok[i][3]);
            IBuybackValues.BuybackValues memory v = f.buybackValuesAt(i * 1000);
            require(
                v.pool == p && v.burnPpm == ok[i][0] && v.twapSeconds == ok[i][1] && v.maxSlippagePpm == ok[i][2]
                    && v.buyEveryRounds == ok[i][3],
                "the values given, in every round"
            );
        }
        uint256[4][9] memory bad = [
            [uint256(999_999), 3600, 10_000, 1],
            [uint256(1_000_001), 3600, 10_000, 1],
            [uint256(1e6), 1799, 10_000, 1],
            [uint256(1e6), 7201, 10_000, 1],
            [uint256(1e6), 3600, 4999, 1],
            [uint256(1e6), 3600, 50_001, 1],
            [uint256(1e6), 3600, 10_000, 0],
            [uint256(1e6), 3600, 10_000, 841],
            [uint256(0), 0, 0, 0]
        ];
        for (uint256 i = 0; i < bad.length; i++) {
            try new FixedBuybackValues(p, bad[i][0], bad[i][1], bad[i][2], bad[i][3]) {
                revert(string.concat("out of range accepted: case ", vm.toString(i)));
            } catch (bytes memory ret) {
                require(eq(code(ret), "VALUE_OUT_OF_RANGE"), string.concat("case ", vm.toString(i)));
            }
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
