// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {vm} from "./Vm.sol";
import {RewardLedgerRunner} from "./RewardLedgerRunner.sol";
import {StakeLedger} from "../src/money/StakeLedger.sol";
import {TestLPT} from "../src/money/TestLPT.sol";
import {Registry} from "../src/authority/Registry.sol";

/// Runs the `ledger` cases with no claims, from
/// vectors/october-main-text-v5.json, with the holding checks of rulebook
/// v2 after every step; then the refusals no case reaches, the mint
/// refusals, and R-23's gas check. Claims.t.sol runs the cases with claims.
contract StakeLedgerTest is RewardLedgerRunner {
    function setUp() external {
        loadVectors("october-main-text-v5");
    }

    function test_ledger_stake() external {
        runRewardCase("ledger-stake");
    }

    // v1's ledger cases, unchanged in v2, on a stage 3 deployment.
    function test_v2_ledger_demo() external {
        runRewardCase("ledger-demo");
    }

    function test_v2_ledger_reserve() external {
        runRewardCase("ledger-reserve");
    }

    function test_v2_ledger_partial() external {
        runRewardCase("ledger-partial");
    }

    function test_v2_ledger_empty() external {
        runRewardCase("ledger-empty");
    }

    function test_v2_ledger_replay() external {
        runRewardCase("ledger-replay");
    }

    function test_v2_ledger_expired() external {
        runRewardCase("ledger-expired");
    }

    function test_v2_ledger_unregistered() external {
        runRewardCase("ledger-unregistered");
    }

    function test_v2_ledger_release() external {
        runRewardCase("ledger-release");
    }

    function test_v2_ledger_payout() external {
        runRewardCase("ledger-payout");
    }

    function runRewardCase(string memory id) internal {
        deployRewards();
        rewardSteps(id);
    }

    /// Every ledger case is run here or in Claims.t.sol. A new case fails
    /// this until someone runs it.
    function test_every_ledger_case_is_run() external view {
        string[] memory ran = new string[](22);
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
        // Claims.t.sol.
        ran[10] = "ledger-cycle";
        ran[11] = "ledger-window";
        ran[12] = "ledger-mint-order";
        ran[13] = "ledger-round-zero";
        ran[14] = "ledger-same-node";
        ran[15] = "refusal-order";
        // They name validator members (S-06 phase 4).
        ran[16] = "ledger-validator-pot";
        ran[17] = "ledger-mid-round";
        // Parameters (S-10 phase 3).
        ran[18] = "ledger-authority";
        ran[19] = "ledger-delays";
        ran[20] = "ledger-score-source";
        // Key rotation (S-10 phase 4).
        ran[21] = "ledger-rotation";
        string memory missing = ledgerCasesNotIn(ran);
        require(bytes(missing).length == 0, string.concat("ledger case not run:", missing));
    }

    // --- refusals no shared case reaches ---------------------------------

    function test_transfer_and_rebond_to_an_unregistered_node_are_refused() external {
        deployRewards();
        address h = who("H");
        register("P");
        lpt.transfer(h, 100e18);
        expectDone(bondAs(h, who("P"), 100e18), "bond");
        expectRefused(
            as_(h, address(ledger), abi.encodeCall(StakeLedger.transferBond, (who("P"), who("X"), 10e18))),
            "NOT_REGISTERED",
            "transfer to X"
        );
        // X to X, with no bond at X: NOT_REGISTERED before SAME_NODE and the
        // amount (D-38, D-39).
        expectRefused(
            as_(h, address(ledger), abi.encodeCall(StakeLedger.transferBond, (who("X"), who("X"), 1))),
            "NOT_REGISTERED",
            "transfer X to X"
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

    // --- amounts in the token's whole range ------------------------------

    /// No rule caps a bond below what the token can hold (uint256), so the
    /// ledger keeps every amount and count in that range. A test LPT whose
    /// genesis exceeds 2**192 makes the boundary reachable.
    function deployWithSupply(uint256 supply) internal {
        deployRewards(supply);
    }

    /// One holder bonds more than 2**192, then moves, unbonds, rebonds and
    /// withdraws it; every count and view holds the whole amount.
    function test_a_bond_above_2_192_is_kept_whole() external {
        uint256 big = 2 ** 192 + 5;
        deployWithSupply(2 ** 200);
        address h = who("H");
        address p = who("P");
        address q = who("Q");
        register("P");
        register("Q");
        lpt.transfer(h, big);
        expectDone(bondAs(h, p, big), "bond above 2**192");
        expectEq(ledger.stayingOf(h, p), big, "staying");
        toRound(2);
        expectEq(ledger.bondAt(h, p, 2), big, "bond in round 2");
        expectEq(ledger.stakeAt(p, 2), big, "P in round 2");
        expectEq(ledger.totalStakeAt(2), big, "total in round 2");
        // Move it all, and unbond all of the move before it arrives.
        expectDone(as_(h, address(ledger), abi.encodeCall(StakeLedger.transferBond, (p, q, big))), "transfer");
        expectEq(ledger.inTransferOf(h, p, q), big, "in transfer");
        toRound(3);
        expectDone(as_(h, address(ledger), abi.encodeCall(StakeLedger.unbondTransfer, (p, q, big))), "unbond it");
        (uint256 waiting,) = ledger.unbondedOf(h);
        expectEq(waiting, big, "waiting");
        expectEq(ledger.stakeAt(p, 3), big, "P in round 3");
        expectEq(ledger.stakeAt(p, 4), 0, "P in round 4");
        expectEq(ledger.stakeAt(q, 23), 0, "Q never");
        // Rebond part of it, withdraw the rest when it is ready.
        expectDone(as_(h, address(ledger), abi.encodeCall(StakeLedger.rebond, (p, big - 2 ** 191))), "rebond");
        toRound(24);
        expectEq(ledger.stakeAt(p, 24), big - 2 ** 191, "P after the rebond");
        expectDone(as_(h, address(ledger), abi.encodeCall(StakeLedger.withdrawUnbonded, ())), "withdraw");
        expectEq(lpt.balanceOf(h), 2 ** 191, "withdrawn");
        expectEq(lpt.balanceOf(address(ledger)), big - 2 ** 191, "held");
    }

    /// Bonds each below 2**192 whose sum crosses it: the node's stake and
    /// the total hold the sum.
    function test_bonds_summing_across_2_192_are_counted_whole() external {
        uint256 each = 2 ** 191 + 3;
        deployWithSupply(2 ** 200);
        address p = who("P");
        register("P");
        string[3] memory names = ["H1", "H2", "H3"];
        for (uint256 i = 0; i < names.length; i++) {
            address h = who(names[i]);
            lpt.transfer(h, each);
            expectDone(bondAs(h, p, each), string.concat("bond of ", names[i]));
        }
        toRound(2);
        expectEq(ledger.stakeAt(p, 2), 3 * each, "P in round 2");
        expectEq(ledger.totalStakeAt(2), 3 * each, "total in round 2");
        expectEq(ledger.bondAt(who("H2"), p, 2), each, "H2 in round 2");
        expectDone(as_(who("H1"), address(ledger), abi.encodeCall(StakeLedger.unbond, (p, each))), "unbond H1");
        expectEq(ledger.stakeAt(p, 3), 2 * each, "P in round 3");
        expectEq(ledger.totalStakeAt(3), 2 * each, "total in round 3");
    }

    function test_reward_cut_needs_the_node_and_a_cut_in_range() external {
        deployRewards();
        address n = who("N");
        register("N");
        expectEq(ledger.cutAt(n, 1), 0, "default cut");
        // A holder may not set N's cut. An unregistered address setting its
        // own is refused for the node it names, after who (D-39).
        expectRefused(
            as_(who("H"), address(ledger), abi.encodeCall(StakeLedger.setRewardCut, (n, 1))), "NOT_AUTHORIZED", "holder"
        );
        expectRefused(
            as_(who("X"), address(ledger), abi.encodeCall(StakeLedger.setRewardCut, (who("X"), 1))),
            "NOT_REGISTERED",
            "unregistered, its own"
        );
        expectRefused(
            as_(who("H"), address(ledger), abi.encodeCall(StakeLedger.setRewardCut, (who("X"), 1))),
            "NOT_AUTHORIZED",
            "holder, for unregistered X"
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
        deployRewards();
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
        deployRewards();
        expectEq(lpt.totalSupply(), GENESIS, "supply");
        expectEq(lpt.balanceOf(address(this)), GENESIS, "deployer");
        expectEq(lpt.decimals(), 18, "decimals");
    }

    /// S-05 3.2: a mint from the deployer, StakeLedger, a node or a holder
    /// is refused NOT_AUTHORIZED; Claims, fixed at deployment, mints.
    function test_mint_only_from_claims() external {
        deployRewards();
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
        deployRewards();
        if (caller == claimsAddress()) return;
        expectRefused(as_(caller, address(lpt), abi.encodeCall(TestLPT.mint, (to, amount))), "NOT_AUTHORIZED", "mint");
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
        deployRewards();
        uint256[] memory few = gasOfEveryCall();
        resetRunner();
        deployRewards();
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

    /// EIP-7825's cap on one transaction's gas.
    uint256 private constant TX_GAS_CAP = 1 << 24;

    /// At the longest unbonding wait (840) a holder who unbonds in each of
    /// 840 rounds has 840 waiting unbonds, and a rebond walks them all. The
    /// holder's own calls stay under one transaction's gas cap.
    function test_gas_at_840_waiting_unbonds() external {
        deployRewards();
        address p = who("P");
        address h = who("H");
        register("P");
        uint256[] memory wait = new uint256[](1);
        wait[0] = 840;
        vm.prank(who("G"));
        uint256 from = params.announce(7, wait);
        toRound(from);
        lpt.transfer(h, 1000e18);
        expectDone(bondAs(h, p, 1000e18), "bond");
        uint256 most;
        for (uint256 i = 0; i < 840; i++) {
            if (i > 0) toRound(clock.currentRound() + 1);
            expectDone(as_(h, address(ledger), abi.encodeCall(StakeLedger.unbond, (p, 1e18))), "unbond");
            uint256 g = vm.lastFrameGas().gasTotalUsed;
            if (g > most) most = g;
        }
        expectEq(ledger.rebondableOf(h, p), 840e18, "840 unbonds waiting");
        require(most < TX_GAS_CAP, "an unbond: over one transaction's gas cap");
        // Rebond takes the latest unbond first, so the last call of a run
        // of calls takes the oldest.
        measure(h, address(ledger), abi.encodeCall(StakeLedger.rebond, (p, 1e18)));
        uint256 first = stepGas[stepGas.length - 1];
        expectDone(as_(h, address(ledger), abi.encodeCall(StakeLedger.rebond, (p, 840e18 - 2e18))), "the rest");
        measure(h, address(ledger), abi.encodeCall(StakeLedger.rebond, (p, 1e18)));
        uint256 last = stepGas[stepGas.length - 1];
        expectEq(ledger.rebondableOf(h, p), 0, "all rebonded");
        require(first < TX_GAS_CAP && last < TX_GAS_CAP, "one unbond's rebond: over the gas cap");
    }

    function gasOfEveryCall() internal returns (uint256[] memory) {
        steps("ledger-stake");
        address p = who("P");
        address h = who("H");
        address q = who("Q");
        // The calls ledger-stake does not make, and every view.
        register("N2");
        stepGas.push(vm.lastFrameGas().gasTotalUsed);
        measure(who("N2"), address(registry), abi.encodeCall(Registry.setPayout, (who("N2"), who("W"))));
        measure(who("N2"), address(registry), abi.encodeCall(Registry.setOperator, (who("N2"), who("K1"))));
        measure(who("K1"), address(registry), abi.encodeCall(Registry.acceptOperator, (who("N2"))));
        measure(who("N2"), address(registry), abi.encodeCall(Registry.setOperator, (who("N2"), who("K1"))));
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
        measure(h, address(registry), abi.encodeCall(Registry.operatorOf, (who("N2"))));
        measure(h, address(registry), abi.encodeCall(Registry.proposedOperatorOf, (who("N2"))));
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
}
