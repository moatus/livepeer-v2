// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {vm} from "./Vm.sol";
import {LedgerRunner} from "./LedgerRunner.sol";
import {Stage3} from "./Stage3.sol";
import {StakeLedger} from "../src/money/StakeLedger.sol";
import {TestLPT} from "../src/money/TestLPT.sol";
import {Registry} from "../src/authority/Registry.sol";

/// Runs the v2 `ledger` cases StakeLedger and test LPT can run on their
/// own, from vectors/october-main-text-v2.json, with the holding checks of
/// rulebook v2 after every step; then the refusals no case reaches, the
/// mint refusals, and R-23's gas check.
contract StakeLedgerTest is LedgerRunner, Stage3 {
    /// Names an LPT step gave, as holders and as nodes.
    string[] private holders;
    string[] private lptNodes;
    /// Gas of the last call of each step, when measured.
    uint256[] private stepGas;

    function setUp() external {
        loadVectors("october-main-text-v2");
    }

    function test_ledger_stake() external { runV2("ledger-stake"); }

    // v1's ledger cases, unchanged in v2, on a stage 3 deployment.
    function test_v2_ledger_demo() external { runV2("ledger-demo"); }
    function test_v2_ledger_reserve() external { runV2("ledger-reserve"); }
    function test_v2_ledger_partial() external { runV2("ledger-partial"); }
    function test_v2_ledger_empty() external { runV2("ledger-empty"); }
    function test_v2_ledger_replay() external { runV2("ledger-replay"); }
    function test_v2_ledger_expired() external { runV2("ledger-expired"); }
    function test_v2_ledger_unregistered() external { runV2("ledger-unregistered"); }
    function test_v2_ledger_release() external { runV2("ledger-release"); }
    function test_v2_ledger_payout() external { runV2("ledger-payout"); }

    function runV2(string memory id) internal {
        deployStage3();
        steps(id);
    }

    /// Every v2 ledger case is run here, or needs Claims and is S-05 phase
    /// 4's to run. A new case fails this until someone runs it.
    function test_every_v2_ledger_case_is_run_or_waits_for_claims() external view {
        string[] memory ran = new string[](15);
        ran[0] = "ledger-demo";
        ran[1] = "ledger-reserve";
        ran[2] = "ledger-partial";
        ran[3] = "ledger-empty";
        ran[4] = "ledger-replay";
        ran[5] = "ledger-expired";
        ran[6] = "ledger-unregistered";
        ran[7] = "ledger-release";
        ran[8] = "ledger-payout";
        ran[9] = "ledger-stake";
        // Phase 4 (Claims): each has claim, withdrawal or mint_shares steps.
        ran[10] = "ledger-cycle";
        ran[11] = "ledger-window";
        ran[12] = "ledger-mint-order";
        ran[13] = "ledger-validator-pot";
        ran[14] = "ledger-mid-round";
        string memory missing = ledgerCasesNotIn(ran);
        require(bytes(missing).length == 0, string.concat("ledger case not run:", missing));
    }

    // --- refusals no shared case reaches ---------------------------------

    function test_transfer_and_rebond_to_an_unregistered_node_are_refused() external {
        deployStage3();
        address h = who("H");
        register("P");
        lpt.transfer(h, 100e18);
        expectDone(bondAs(h, who("P"), 100e18), "bond");
        expectRefused(
            as_(h, address(ledger), abi.encodeCall(StakeLedger.transferBond, (who("P"), who("X"), 10e18))),
            "NOT_REGISTERED",
            "transfer to X"
        );
        expectDone(as_(h, address(ledger), abi.encodeCall(StakeLedger.unbond, (who("P"), 10e18))), "unbond");
        expectRefused(
            as_(h, address(ledger), abi.encodeCall(StakeLedger.rebond, (who("X"), 10e18))),
            "NOT_REGISTERED",
            "rebond to X"
        );
        expectEq(ledger.stayingOf(h, who("P")), 90e18, "nothing moved: staying");
        expectEq(ledger.rebondableOf(h, who("P")), 10e18, "nothing moved: rebondable");
    }

    function test_reward_cut_needs_the_node_and_a_cut_in_range() external {
        deployStage3();
        address n = who("N");
        register("N");
        expectEq(ledger.cutAt(n, 1), 0, "default cut");
        // Neither a holder nor an unregistered node may set N's cut, or its own.
        expectRefused(
            as_(who("H"), address(ledger), abi.encodeCall(StakeLedger.setRewardCut, (n, 1))), "NOT_AUTHORIZED", "holder"
        );
        expectRefused(
            as_(who("X"), address(ledger), abi.encodeCall(StakeLedger.setRewardCut, (who("X"), 1))),
            "NOT_AUTHORIZED",
            "unregistered"
        );
        expectRefused(
            as_(n, address(ledger), abi.encodeCall(StakeLedger.setRewardCut, (n, 1_000_001))),
            "CUT_OUT_OF_RANGE",
            "above 10^6"
        );
        // NOT_AUTHORIZED comes first when both apply.
        expectRefused(
            as_(who("H"), address(ledger), abi.encodeCall(StakeLedger.setRewardCut, (n, 1_000_001))),
            "NOT_AUTHORIZED",
            "holder above 10^6"
        );
        // A cut set in round R counts from R + 1; the last one set in R wins.
        toRound(3);
        expectDone(as_(n, address(ledger), abi.encodeCall(StakeLedger.setRewardCut, (n, 1_000_000))), "10^6");
        expectDone(as_(n, address(ledger), abi.encodeCall(StakeLedger.setRewardCut, (n, 200_000))), "200,000");
        toRound(6);
        expectDone(as_(n, address(ledger), abi.encodeCall(StakeLedger.setRewardCut, (n, 0))), "0");
        expectEq(ledger.cutAt(n, 3), 0, "cut in round 3");
        expectEq(ledger.cutAt(n, 4), 200_000, "cut in round 4");
        expectEq(ledger.cutAt(n, 6), 200_000, "cut in round 6");
        expectEq(ledger.cutAt(n, 7), 0, "cut in round 7");
    }

    function test_withdrawal_with_nothing_unbonded_is_refused() external {
        deployStage3();
        expectRefused(
            as_(who("H"), address(ledger), abi.encodeCall(StakeLedger.withdrawUnbonded, ())),
            "UNBOND_NOT_READY",
            "withdrawal"
        );
    }

    // --- the one minter -------------------------------------------------

    /// The genesis supply is the deployer's, and the token's whole supply;
    /// nothing has been minted.
    function test_genesis_supply_is_the_deployers() external {
        deployStage3();
        expectEq(lpt.totalSupply(), GENESIS, "supply");
        expectEq(lpt.balanceOf(address(this)), GENESIS, "deployer");
        expectEq(lpt.decimals(), 18, "decimals");
    }

    /// S-05 3.2: a mint from the deployer, StakeLedger, a node or a holder
    /// is refused NOT_AUTHORIZED; Claims, fixed at deployment, mints.
    function test_mint_only_from_claims() external {
        deployStage3();
        register("N");
        lpt.transfer(who("H"), 10e18);
        address[4] memory callers = [address(this), address(ledger), who("N"), who("H")];
        string[4] memory names = ["deployer", "StakeLedger", "node", "holder"];
        for (uint256 i = 0; i < callers.length; i++) {
            expectRefused(
                as_(callers[i], address(lpt), abi.encodeCall(TestLPT.mint, (callers[i], 1))),
                "NOT_AUTHORIZED",
                string.concat("mint from ", names[i])
            );
        }
        expectEq(lpt.totalSupply(), GENESIS, "nothing minted");
        expectDone(as_(claimsAddress(), address(lpt), abi.encodeCall(TestLPT.mint, (who("W"), 7))), "Claims");
        expectEq(lpt.balanceOf(who("W")), 7, "minted by Claims");
        expectEq(lpt.totalSupply(), GENESIS + 7, "supply after");
    }

    /// Any address but Claims's is refused, and nothing is minted.
    function testFuzz_mint_refused_from_any_other_address(address caller, address to, uint256 amount) external {
        deployStage3();
        if (caller == claimsAddress()) return;
        expectRefused(
            as_(caller, address(lpt), abi.encodeCall(TestLPT.mint, (to, amount))), "NOT_AUTHORIZED", "mint"
        );
        expectEq(lpt.totalSupply(), GENESIS, "supply");
    }

    function test_token_needs_a_claims_address() external {
        (bool ok,) = address(this).call(abi.encodeCall(this.newToken, (address(0))));
        require(!ok, "deployed with no Claims address");
    }

    function newToken(address claims) external returns (address) {
        return address(new TestLPT(claims, GENESIS));
    }

    // --- R-23: nothing grows with the number of nodes --------------------

    /// Every StakeLedger, test LPT and Registry call of ledger-stake, plus
    /// the calls it does not make, costs the same gas after 999 more
    /// registrations as without them, and ledger-stake gives the same
    /// numbers.
    function test_gas_does_not_grow_with_registered_nodes() external {
        deployStage3();
        uint256[] memory few = gasOfEveryCall();
        resetRunner();
        deployStage3();
        for (uint256 i = 0; i < 999; i++) {
            vm.prank(address(uint160(0x10000 + i)));
            registry.register();
        }
        uint256[] memory many = gasOfEveryCall();
        expectEq(many.length, few.length, "calls measured");
        for (uint256 i = 0; i < few.length; i++) {
            expectEq(many[i], few[i], string.concat("gas of call ", vm.toString(i)));
        }
    }

    function gasOfEveryCall() internal returns (uint256[] memory) {
        steps("ledger-stake");
        address p = who("P");
        address h = who("H");
        address q = who("Q");
        // The calls ledger-stake does not make, and every view.
        register("N2");
        stepGas.push(vm.lastFrameGas().gasTotalUsed);
        measure(who("N2"), address(registry), abi.encodeCall(Registry.setPayout, (who("W"))));
        measure(p, address(ledger), abi.encodeCall(StakeLedger.setRewardCut, (p, 300_000)));
        measure(h, address(ledger), abi.encodeCall(StakeLedger.setRewardCut, (p, 1)));
        measure(p, address(ledger), abi.encodeCall(StakeLedger.setRewardCut, (p, 2_000_000)));
        measure(h, address(ledger), abi.encodeCall(StakeLedger.transferBond, (p, q, 10e18)));
        measure(h, address(ledger), abi.encodeCall(StakeLedger.transferBond, (p, who("X"), 1)));
        measure(h, address(ledger), abi.encodeCall(StakeLedger.unbondTransfer, (p, q, 5e18)));
        measure(h, address(ledger), abi.encodeCall(StakeLedger.rebond, (p, 5e18)));
        measure(h, address(ledger), abi.encodeCall(StakeLedger.rebond, (who("X"), 1)));
        measure(h, address(lpt), abi.encodeCall(TestLPT.approve, (p, 1)));
        measure(p, address(lpt), abi.encodeCall(TestLPT.transferFrom, (h, p, 1)));
        measure(claimsAddress(), address(lpt), abi.encodeCall(TestLPT.mint, (p, 1)));
        measure(h, address(lpt), abi.encodeCall(TestLPT.mint, (p, 1)));
        measure(h, address(registry), abi.encodeCall(Registry.mayActFor, (p, p)));
        measure(h, address(registry), abi.encodeCall(Registry.isRegistered, (p)));
        measure(h, address(registry), abi.encodeCall(Registry.payoutOf, (p)));
        measure(h, address(ledger), abi.encodeCall(StakeLedger.stakeAt, (p, 15)));
        measure(h, address(ledger), abi.encodeCall(StakeLedger.totalStakeAt, (12)));
        measure(h, address(ledger), abi.encodeCall(StakeLedger.bondAt, (h, p, 13)));
        measure(h, address(ledger), abi.encodeCall(StakeLedger.cutAt, (p, 40)));
        measure(h, address(ledger), abi.encodeCall(StakeLedger.stayingOf, (h, p)));
        measure(h, address(ledger), abi.encodeCall(StakeLedger.inTransferOf, (h, p, q)));
        measure(h, address(ledger), abi.encodeCall(StakeLedger.unbondedOf, (h)));
        measure(h, address(ledger), abi.encodeCall(StakeLedger.rebondableOf, (h, p)));
        return stepGas;
    }

    function measure(address caller, address target, bytes memory data) internal {
        vm.prank(caller);
        (bool ok,) = target.call(data);
        ok; // refused calls are measured too
        stepGas.push(vm.lastFrameGas().gasTotalUsed);
    }

    // --- the LPT steps ------------------------------------------------------

    function act(string memory s, string memory action) internal override returns (bytes memory refused) {
        if (eq(action, "grant_lpt")) {
            // A transfer from the genesis supply, which the deployer holds.
            remember(holders, str(s, ".to"));
            lpt.transfer(who(str(s, ".to")), num(s, ".amount_wei"));
        } else if (eq(action, "bond")) {
            refused = bondAs(holder(s), node(s, ".node"), num(s, ".amount_wei"));
        } else if (eq(action, "unbond")) {
            refused = holderCall(s, abi.encodeCall(StakeLedger.unbond, (node(s, ".node"), num(s, ".amount_wei"))));
        } else if (eq(action, "transfer")) {
            refused = holderCall(
                s, abi.encodeCall(StakeLedger.transferBond, (node(s, ".from"), node(s, ".to"), num(s, ".amount_wei")))
            );
        } else if (eq(action, "unbond_transfer")) {
            refused = holderCall(
                s,
                abi.encodeCall(StakeLedger.unbondTransfer, (node(s, ".from"), node(s, ".to"), num(s, ".amount_wei")))
            );
        } else if (eq(action, "rebond")) {
            refused = holderCall(s, abi.encodeCall(StakeLedger.rebond, (node(s, ".node"), num(s, ".amount_wei"))));
        } else if (eq(action, "withdraw_unbonded")) {
            refused = holderCall(s, abi.encodeCall(StakeLedger.withdrawUnbonded, ()));
        } else if (eq(action, "set_cut")) {
            address n = node(s, ".node");
            refused =
                as_(n, address(ledger), abi.encodeCall(StakeLedger.setRewardCut, (n, num(s, ".cut_millionths"))));
        } else if (eq(action, "observe_stake")) {
            uint256 r = clock.currentRound();
            observed(s, ".stake_wei", ledger.stakeAt(node(s, ".node"), r), ".total_stake_wei", ledger.totalStakeAt(r));
        } else if (
            eq(action, "claim") || eq(action, "withdraw_parts") || eq(action, "withdraw_validator_parts")
                || eq(action, "mint_shares")
        ) {
            revert(string.concat(action, " needs Claims (S-05 phase 4)"));
        } else {
            return super.act(s, action);
        }
        stepGas.push(vm.lastFrameGas().gasTotalUsed);
    }

    function holder(string memory s) internal returns (address) {
        remember(holders, str(s, ".holder"));
        return who(str(s, ".holder"));
    }

    function node(string memory s, string memory field) internal returns (address) {
        remember(lptNodes, str(s, field));
        return who(str(s, field));
    }

    function holderCall(string memory s, bytes memory data) internal returns (bytes memory) {
        return as_(holder(s), address(ledger), data);
    }

    /// After every step, also (rulebook v2, the shared examples): the LPT
    /// StakeLedger holds equals every bond staying, in transfer or arriving,
    /// plus the unbonded LPT not withdrawn; Claims holds nothing, since no
    /// claim exists yet; and the supply is the genesis supply, since only
    /// Claims mints.
    function holdsEverything(string memory where) internal view override {
        super.holdsEverything(where);
        uint256 sum = 0;
        for (uint256 i = 0; i < holders.length; i++) {
            address h = who(holders[i]);
            (uint256 waiting, uint256 ready) = ledger.unbondedOf(h);
            sum += waiting + ready;
            for (uint256 j = 0; j < lptNodes.length; j++) {
                sum += ledger.stayingOf(h, who(lptNodes[j]));
                for (uint256 k = 0; k < lptNodes.length; k++) {
                    sum += ledger.inTransferOf(h, who(lptNodes[j]), who(lptNodes[k]));
                }
            }
        }
        expectEq(lpt.balanceOf(address(ledger)), sum, string.concat(where, ": StakeLedger LPT"));
        expectEq(lpt.balanceOf(claimsAddress()), 0, string.concat(where, ": Claims LPT"));
        expectEq(lpt.totalSupply(), GENESIS, string.concat(where, ": supply"));
    }

    function checkExpected() internal view override {
        super.checkExpected();
        string memory x = string.concat(path, ".expected");
        if (!vm.keyExistsJson(vectors, string.concat(x, ".lpt_wei"))) return;
        string[] memory names = vm.parseJsonKeys(vectors, string.concat(x, ".lpt_wei"));
        for (uint256 i = 0; i < names.length; i++) {
            expectEq(
                lpt.balanceOf(lptAddress(names[i])),
                num(x, string.concat(".lpt_wei.", names[i])),
                string.concat("lpt ", names[i])
            );
        }
        expectEq(lpt.totalSupply() - GENESIS, num(x, ".lpt_minted_wei"), "lpt minted");
        // Shares and per-round issuance need Claims (phase 4).
        expectEq(vm.parseJsonKeys(vectors, string.concat(x, ".shares_wei")).length, 0, "shares_wei needs Claims");
        expectEq(
            vm.parseJsonKeys(vectors, string.concat(x, ".minted_for_round_wei")).length,
            0,
            "minted_for_round_wei needs Claims"
        );
    }

    /// The case's names for the contracts and the treasury.
    function lptAddress(string memory name) internal view returns (address) {
        if (eq(name, "StakeLedger")) return address(ledger);
        if (eq(name, "Claims")) return claimsAddress();
        if (eq(name, "Treasury")) return treasuryAddress();
        return who(name);
    }

    function resetRunner() internal {
        delete payers;
        delete nodes;
        delete refusedSteps;
        delete refusedCodes;
        delete observedSteps;
        delete observedFieldA;
        delete observedFieldB;
        delete observedA;
        delete observedB;
        delete holders;
        delete lptNodes;
        delete stepGas;
    }
}
