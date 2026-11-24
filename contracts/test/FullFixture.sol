// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {vm, Vm} from "./Vm.sol";
import {PaymentsFixture} from "./PaymentsFixture.sol";
import {TestLPT} from "../src/money/TestLPT.sol";
import {TestPool} from "../src/money/TestPool.sol";
import {Buyback} from "../src/money/Buyback.sol";
import {IBuyback} from "../interfaces/IBuyback.sol";
import {IBuybackExecutor} from "../interfaces/IBuybackExecutor.sol";
import {IBuybackValues} from "../interfaces/IBuybackValues.sol";
import {ITicketBroker, INetworkShareTicketBroker} from "../interfaces/ITicketBroker.sol";
import {IRoundClock} from "../interfaces/IRoundClock.sol";

/// A values source a test can change, so a test can name another pool or
/// another interval from one round to the next.
contract ValuesStub is IBuybackValues {
    BuybackValues private v;

    constructor(BuybackValues memory v_) {
        v = v_;
    }

    function set(BuybackValues memory v_) external {
        v = v_;
    }

    function buybackValuesAt(uint256) external view returns (BuybackValues memory) {
        return v;
    }
}

/// An executor of another design (R-16 rule 13, D-51), test-only: it
/// accepts the pools it is told to, whatever their code, and its buy draws
/// all that is held to its round's pool and buys nothing. No exchange.
contract OtherExecutor is IBuybackExecutor {
    INetworkShareTicketBroker private immutable broker;
    IBuybackValues private immutable source;
    IRoundClock private immutable clock;
    mapping(address => bool) public acceptsPool;

    constructor(INetworkShareTicketBroker broker_, IBuybackValues source_, IRoundClock clock_) {
        (broker, source, clock) = (broker_, source_, clock_);
    }

    function accept(address pool) external {
        acceptsPool[pool] = true;
    }

    function buy() external returns (uint256 usdcIn, uint256, uint256) {
        address pool = source.buybackValuesAt(clock.currentRound()).pool;
        if (!acceptsPool[pool]) revert IBuyback.POOL_INVALID(pool);
        usdcIn = broker.networkHeld();
        broker.drawNetwork(usdcIn, pool);
    }

    function poke() external {}
}

/// A pool that reports this deployment's two tokens and whatever reserves
/// and cumulatives it is told: not TestPool's code.
contract LyingPool {
    address public token0;
    address public token1;
    uint112 private r0;
    uint112 private r1;
    uint256 public price0CumulativeLast;
    uint256 public price1CumulativeLast;

    constructor(address a, address b) {
        (token0, token1) = a < b ? (a, b) : (b, a);
    }

    function tell(uint112 r0_, uint112 r1_, uint256 c0, uint256 c1) external {
        (r0, r1, price0CumulativeLast, price1CumulativeLast) = (r0_, r1_, c0, c1);
    }

    function getReserves() external view returns (uint112, uint112, uint32) {
        return (r0, r1, uint32(block.timestamp));
    }

    function swap(uint256, uint256, address, bytes calldata) external {}
}

/// A pool of this deployment's two tokens that quotes from its reserves but
/// delivers one wei less than asked. A Buyback pinned to its code hash
/// admits it.
contract ShortPool {
    address public immutable token0;
    address public immutable token1;
    uint112 private immutable r0;
    uint112 private immutable r1;
    uint32 private immutable last;
    uint256 public constant price0CumulativeLast = 0;
    uint256 public constant price1CumulativeLast = 0;

    constructor(address usdc, address lpt, uint112 ru, uint112 rl) {
        (token0, token1, r0, r1) = usdc < lpt ? (usdc, lpt, ru, rl) : (lpt, usdc, rl, ru);
        last = uint32(block.timestamp);
    }

    function getReserves() external view returns (uint112, uint112, uint32) {
        return (r0, r1, last);
    }

    function swap(uint256 out0, uint256 out1, address to, bytes calldata) external {
        if (out0 > 0) TestLPT(token0).transfer(to, delivered(out0));
        if (out1 > 0) TestLPT(token1).transfer(to, delivered(out1));
    }

    function delivered(uint256 asked) internal pure virtual returns (uint256) {
        return asked - 1;
    }
}

/// As ShortPool, but it delivers one wei more than asked.
contract OverPool is ShortPool {
    constructor(address usdc, address lpt, uint112 ru, uint112 rl) ShortPool(usdc, lpt, ru, rl) {}

    function delivered(uint256 asked) internal pure override returns (uint256) {
        return asked + 1;
    }
}

/// Stage 2's contracts, then the test LPT, the pool, a values source,
/// Parameters naming the Buyback as the executor (group 10), and the
/// Buyback; and the steps of a `buyback` case (rulebook v5, R-16).
abstract contract FullFixture is PaymentsFixture {
    TestLPT internal lpt;
    TestPool internal pool;
    IBuybackValues internal source;
    Buyback internal buyback;
    ShortPool internal shortPool;
    uint256 private jobs;

    /// deployFull's options. With none, the deployment the demo makes:
    /// Parameters as the values source, with the deployment's values, and
    /// USDC as the pool's token0.
    uint256 internal constant STUB = 1; // the values source is a ValuesStub a test may change
    uint256 internal constant SHORT = 2; // the round's pool is a ShortPool, and the Buyback is pinned to its hash
    uint256 internal constant OVER = 4; // the same with an OverPool
    uint256 internal constant LPT_FIRST = 8; // TestLPT's address sorts below MockUSDC's: LPT is token0

    /// A ShortPool or OverPool holds 1,000 USDC and 200 LPT of reserves.
    function deployFull(uint256 genesis, uint256 opts) internal {
        bool misdelivers = opts & (SHORT | OVER) != 0;
        bool stub = opts & STUB != 0;
        // deployBase creates five contracts; then the token, the pool, the
        // ShortPool if any, the stub if any, and Parameters. The Buyback is
        // next.
        uint256 n = vm.getNonce(address(this)) + 7 + (misdelivers ? 1 : 0) + (stub ? 1 : 0);
        address parameters = vm.computeCreateAddress(address(this), n);
        address at = vm.computeCreateAddress(address(this), n + 1);
        deployBase(parameters);
        lpt = newLpt(genesis, opts & LPT_FIRST != 0);
        pool = new TestPool(address(usdc), address(lpt));
        require((pool.token0() == address(lpt)) == (opts & LPT_FIRST != 0), "the pool's token order");
        address round_ = address(pool);
        if (misdelivers) {
            shortPool = opts & OVER != 0
                ? new OverPool(address(usdc), address(lpt), 1000e6, 200e18)
                : new ShortPool(address(usdc), address(lpt), 1000e6, 200e18);
            round_ = address(shortPool);
        }
        IBuybackValues.BuybackValues memory v = IBuybackValues.BuybackValues(
            round_,
            param("buyback_burn_ppm"),
            param("buyback_twap_seconds"),
            param("buyback_max_slippage_ppm"),
            param("buyback_every_rounds")
        );
        if (stub) source = IBuybackValues(address(new ValuesStub(v)));
        newParameters(address(0), round_, at);
        if (!stub) source = params;
        buyback = new Buyback(source, broker, clock, lpt, usdc, round_.codehash);
        require(address(buyback) == at, "Buyback at the address Parameters was given");
        register("N");
    }

    /// The test LPT, created with CREATE2 at the first salt whose address
    /// sorts on the side of MockUSDC's that `first` asks for, so the pool's
    /// token order is chosen, not left to the addresses.
    function newLpt(uint256 genesis, bool first) private returns (TestLPT) {
        bytes32 init = keccak256(abi.encodePacked(type(TestLPT).creationCode, abi.encode(who("Claims"), genesis)));
        uint256 salt;
        while (true) {
            address a = address(
                uint160(uint256(keccak256(abi.encodePacked(bytes1(0xff), address(this), bytes32(salt), init))))
            );
            if ((a < address(usdc)) == first) break;
            salt++;
        }
        return new TestLPT{salt: bytes32(salt)}(who("Claims"), genesis);
    }

    // --- the steps of a buyback case ------------------------------------

    /// `job`: a fully funded ticket of `face`, created and redeemed in the
    /// current round for node N.
    function job(uint256 face) internal {
        jobs++;
        string memory id = string.concat("job ", vm.toString(jobs));
        require(fund("P", face, address(0), 0).length == 0, "job: fund");
        (ITicketBroker.Ticket memory t, bytes memory sig, uint256 rand) =
            ticket(id, "P", who("N"), clock.currentRound(), face);
        require(redeem(t, sig, rand).length == 0, "job: redeem");
    }

    /// `seed`: the deployer adds USDC and LPT from its genesis to `p`'s
    /// reserves.
    function seed(TestPool p, uint256 usdcAmount, uint256 lptAmount) internal {
        usdc.mint(address(p), usdcAmount);
        require(lpt.transfer(address(p), lptAmount), "seed: LPT");
        p.mint(address(this));
    }

    /// `swap`: a trader pays `usdcAmount` and receives v2's getAmountOut.
    function swapIn(TestPool p, uint256 usdcAmount) internal {
        (uint256 ru, uint256 rl, bool usdcIs0) = reserves(p);
        uint256 withFee = usdcAmount * 997;
        uint256 out = withFee * rl / (ru * 1000 + withFee);
        address trader = who("Trader");
        usdc.mint(trader, usdcAmount);
        vm.prank(trader);
        require(usdc.transfer(address(p), usdcAmount), "swap: USDC");
        vm.prank(trader);
        p.swap(usdcIs0 ? 0 : out, usdcIs0 ? out : 0, trader, "");
    }

    /// USDC's and LPT's reserves in `p`, and whether USDC is token0.
    function reserves(TestPool p) internal view returns (uint256 ru, uint256 rl, bool usdcIs0) {
        (uint112 r0, uint112 r1,) = p.getReserves();
        usdcIs0 = p.token0() == address(usdc);
        (ru, rl) = usdcIs0 ? (uint256(r0), uint256(r1)) : (uint256(r1), uint256(r0));
    }

    /// Anyone calls the Buyback. Returns the revert data, empty when the
    /// call took effect.
    function callBuyback(bytes memory data) internal returns (bytes memory) {
        return as_(who("Anyone"), address(buyback), data);
    }

    function buy() internal returns (bytes memory) {
        return callBuyback(abi.encodeCall(IBuybackExecutor.buy, ()));
    }

    function poke() internal returns (bytes memory) {
        return callBuyback(abi.encodeCall(IBuybackExecutor.poke, ()));
    }

    /// The data of each Bought event the Buyback emitted since recordLogs.
    function boughtLogs() internal returns (bytes[] memory out) {
        Vm.Log[] memory logs = vm.getRecordedLogs();
        out = new bytes[](logs.length);
        uint256 n;
        for (uint256 i = 0; i < logs.length; i++) {
            if (logs[i].emitter == address(buyback) && logs[i].topics[0] == IBuyback.Bought.selector) {
                out[n++] = logs[i].data;
            }
        }
        assembly {
            mstore(out, n)
        }
    }

    // --- refusals with arguments ------------------------------------------

    function err(string memory sig) internal pure returns (bytes memory) {
        return abi.encodeWithSelector(bytes4(keccak256(bytes(sig))));
    }

    function err(string memory sig, uint256 a) internal pure returns (bytes memory) {
        return abi.encodeWithSelector(bytes4(keccak256(bytes(sig))), a);
    }

    function err(string memory sig, uint256 a, uint256 b) internal pure returns (bytes memory) {
        return abi.encodeWithSelector(bytes4(keccak256(bytes(sig))), a, b);
    }

    function poolInvalid(address p) internal pure returns (bytes memory) {
        return abi.encodeWithSelector(IBuyback.POOL_INVALID.selector, p);
    }

    /// Fails unless `got` is exactly the refusal `want`.
    function expectRefused(bytes memory got, bytes memory want, string memory what) internal pure {
        if (keccak256(got) != keccak256(want)) {
            revert(string.concat(what, ": refused with ", vm.toString(got), ", want ", vm.toString(want)));
        }
    }
}
