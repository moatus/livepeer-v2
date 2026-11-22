// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {vm} from "./Vm.sol";
import {Stage4} from "./Stage4.sol";
import {IBuyback} from "../interfaces/IBuyback.sol";

/// The buyback (rulebook v4, R-16): every `buyback` case of the shared
/// examples run step by step against the deployment the demo makes. What
/// the cases cannot show is in BuybackRefusals.t.sol.
contract BuybackTest is Stage4 {
    /// LPT sent to the Buyback before a case runs: never counted or burned.
    uint256 private constant DONATION = 7e18;

    function setUp() external {
        loadVectors("october-main-text-v4");
    }

    // --- the shared examples ------------------------------------------------

    /// Each case of kind `buyback` in the examples has a test below.
    function test_every_buyback_case_is_run() external view {
        string[4] memory run = ["buyback-walk", "buyback-nothing-out", "buyback-two-wei", "buyback-dust-pool"];
        string[] memory ids = abi.decode(vm.parseJson(vectors, ".cases[*].id"), (string[]));
        uint256 seen;
        for (uint256 i = 0; i < ids.length; i++) {
            string memory kind = vm.parseJsonString(vectors, string.concat(".cases[", vm.toString(i), "].kind"));
            if (!eq(kind, "buyback")) continue;
            bool found;
            for (uint256 j = 0; j < run.length; j++) {
                found = found || eq(run[j], ids[i]);
            }
            require(found, string.concat("buyback case not run: ", ids[i]));
            seen++;
        }
        require(seen == run.length, "a case run here is not in the examples");
    }

    /// Section 1's walk: every buy, refusal and final figure.
    function test_buyback_walk() external {
        runCase("buyback-walk", 0);
    }

    /// The same walk with LPT as the pool's token0: every figure is the
    /// same, so the Buyback reads the reserves, the cumulative and the
    /// swap's output side by the pool's order, not by one fixed order.
    function test_buyback_walk_lpt_token0() external {
        runCase("buyback-walk", LPT_FIRST);
    }

    /// Rule 6, first case: out(cap) is 0, SLIPPAGE(6990, 6990).
    function test_rule6_nothing_out() external {
        runCase("buyback-nothing-out", 0);
    }

    /// Rule 6, second case: 6,894 µUSDC buys 2 wei; 96 rolls over.
    function test_rule6_two_wei() external {
        runCase("buyback-two-wei", 0);
    }

    /// Rule 6, third case: a dust pool, 859 µUSDC for 863 wei.
    function test_rule6_dust_pool() external {
        runCase("buyback-dust-pool", 0);
    }

    struct Run {
        string c;
        uint256 bought;
        uint256 refused;
        uint256 burned;
    }

    function runCase(string memory id, uint256 opts) internal {
        Run memory r;
        r.c = string.concat(".cases[", vm.toString(caseIndex(id)), "]");
        deployStage4(num(string.concat(r.c, ".inputs.lpt_supply_wei")), opts);
        require(lpt.transfer(address(buyback), DONATION), "donation");
        uint256 supply = lpt.totalSupply();
        for (uint256 k = 0; vm.keyExistsJson(vectors, stepPath(r.c, k)); k++) {
            runStep(r, stepPath(r.c, k));
        }
        string memory e = string.concat(r.c, ".expected");
        require(!vm.keyExistsJson(vectors, string.concat(e, ".bought[", vm.toString(r.bought), "]")), "a buy not made");
        require(
            !vm.keyExistsJson(vectors, string.concat(e, ".refused[", vm.toString(r.refused), "]")), "a refusal not met"
        );
        (uint256 ru, uint256 rl,) = reserves(pool);
        expectEq(broker.networkHeld(), num(string.concat(e, ".held_micro_usdc")), "held");
        expectEq(ru, num(string.concat(e, ".reserve_usdc_micro_usdc")), "USDC reserve");
        expectEq(rl, num(string.concat(e, ".reserve_lpt_wei")), "LPT reserve");
        expectEq(lpt.totalSupply(), num(string.concat(e, ".lpt_supply_wei")), "supply");
        // Supply falls by exactly the LPT the Bought events burned, and the
        // LPT sent beforehand is still there.
        expectEq(supply - lpt.totalSupply(), r.burned, "supply fall against Bought.burned");
        expectEq(lpt.balanceOf(address(buyback)), DONATION, "the LPT sent to the Buyback");
    }

    function runStep(Run memory r, string memory s) internal {
        string memory step = str(s, ".step");
        string memory action = str(s, ".action");
        toRound(num(string.concat(s, ".round")));
        vm.warp(num(string.concat(s, ".time")));
        string memory e = string.concat(r.c, ".expected");
        string memory refusal = string.concat(e, ".refused[", vm.toString(r.refused), "]");
        bool refused = vm.keyExistsJson(vectors, refusal) && eq(str(refusal, ".step"), step);
        string memory what = string.concat(r.c, " step ", step, " ", action);

        if (eq(action, "job")) {
            job(num(string.concat(s, ".face_micro_usdc")));
        } else if (eq(action, "seed")) {
            seed(pool, num(string.concat(s, ".usdc_micro_usdc")), num(string.concat(s, ".lpt_wei")));
        } else if (eq(action, "swap")) {
            swapIn(pool, num(string.concat(s, ".usdc_micro_usdc")));
        } else if (eq(action, "poke") || eq(action, "buy")) {
            vm.recordLogs();
            bytes memory ret = eq(action, "buy") ? buy() : poke();
            if (refused) {
                expectRefused(ret, wanted(refusal), what);
                r.refused++;
                return;
            }
            require(ret.length == 0, string.concat(what, ": refused ", vm.toString(ret)));
            if (eq(action, "buy")) r.burned += checkBought(r, step, what);
            return;
        } else {
            revert(string.concat(what, ": unknown action"));
        }
        require(!refused, string.concat(what, ": a refusal is expected"));
    }

    /// Compares the Bought event with the next `bought` entry, which must
    /// be this step's. Returns the LPT it burned.
    function checkBought(Run memory r, string memory step, string memory what) internal returns (uint256) {
        string memory b = string.concat(r.c, ".expected.bought[", vm.toString(r.bought), "]");
        require(vm.keyExistsJson(vectors, b) && eq(str(b, ".step"), step), string.concat(what, ": not expected to buy"));
        r.bought++;
        bytes[] memory logs = boughtLogs();
        require(logs.length == 1, string.concat(what, ": one Bought event"));
        (, uint256 usdcIn, uint256 lptBought, uint256 burned, uint256 twapX112, uint256 maxIn) =
            abi.decode(logs[0], (uint256, uint256, uint256, uint256, uint256, uint256));
        expectEq(usdcIn, num(string.concat(b, ".usdc_in_micro_usdc")), string.concat(what, " usdcIn"));
        expectEq(lptBought, num(string.concat(b, ".lpt_out_wei")), string.concat(what, " lptBought"));
        expectEq(burned, num(string.concat(b, ".burned_wei")), string.concat(what, " burned"));
        expectEq(maxIn, num(string.concat(b, ".max_in_micro_usdc")), string.concat(what, " maxIn"));
        expectEq(twapX112, num(string.concat(b, ".twap_x112")), string.concat(what, " twapX112"));
        return burned;
    }

    /// The revert data a `refused` entry names: its code and arguments.
    function wanted(string memory refusal) internal view returns (bytes memory) {
        string memory code = str(refusal, ".code");
        string memory argsKey = string.concat(refusal, ".args");
        string[] memory args = vm.keyExistsJson(vectors, argsKey)
            ? abi.decode(vm.parseJson(vectors, argsKey), (string[]))
            : new string[](0);
        string memory types;
        bytes memory words;
        for (uint256 i = 0; i < args.length; i++) {
            types = string.concat(types, i == 0 ? "" : ",", eq(code, "POOL_INVALID") ? "address" : "uint256");
            words = abi.encodePacked(words, vm.parseUint(args[i]));
        }
        return abi.encodePacked(bytes4(keccak256(bytes(string.concat(code, "(", types, ")")))), words);
    }

    function stepPath(string memory c, uint256 k) internal pure returns (string memory) {
        return string.concat(c, ".inputs.steps[", vm.toString(k), "]");
    }

    function str(string memory path, string memory field) internal view returns (string memory) {
        return vm.parseJsonString(vectors, string.concat(path, field));
    }

    function num(string memory path) internal view returns (uint256) {
        return vm.parseUint(vm.parseJsonString(vectors, path));
    }
}
