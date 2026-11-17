// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {vm} from "./Vm.sol";
import {LedgerRunnerV2} from "./LedgerRunnerV2.sol";
import {Claims} from "../src/money/Claims.sol";
import {StakeLedger} from "../src/money/StakeLedger.sol";
import {ITicketBroker} from "../interfaces/ITicketBroker.sol";

/// Claims (S-05 phase 4): the v2 `ledger` cases with claims, withdrawals
/// and share mints, from vectors/october-main-text-v2.json, with the
/// after-step checks of the shared examples (LedgerRunnerV2); then the
/// refusals and orders no case reaches, and R-23's gas check.
contract ClaimsTest is LedgerRunnerV2 {
    function setUp() external {
        loadVectors("october-main-text-v2");
    }

    function test_ledger_cycle() external { runV2("ledger-cycle"); }
    function test_ledger_window() external { runV2("ledger-window"); }
    function test_ledger_mint_order() external { runV2("ledger-mint-order"); }
    function test_ledger_round_zero() external { runV2("ledger-round-zero"); }
    function test_ledger_same_node() external { runV2("ledger-same-node"); }
    function test_refusal_order() external { runV2("refusal-order"); }

    function runV2(string memory id) internal {
        deployStage3();
        stepsV2(id);
    }

    function claimAs(address caller, address node, uint256 round) internal returns (bytes memory) {
        return as_(caller, address(claims), abi.encodeCall(Claims.claim, (node, round)));
    }

    function withdrawAs(address holder, address node, uint256[] memory rounds) internal returns (bytes memory) {
        return as_(holder, address(claims), abi.encodeCall(Claims.withdrawParts, (node, rounds)));
    }

    function validatorWithdrawAs(address holder, address node, uint256[] memory rounds)
        internal
        returns (bytes memory)
    {
        return as_(holder, address(claims), abi.encodeCall(Claims.withdrawValidatorParts, (node, rounds)));
    }

    function one(uint256 r) internal pure returns (uint256[] memory list) {
        list = new uint256[](1);
        list[0] = r;
    }

    function two(uint256 a, uint256 b) internal pure returns (uint256[] memory list) {
        list = new uint256[](2);
        (list[0], list[1]) = (a, b);
    }

    // --- D-26: the first refusal that applies -----------------------------

    /// R-12's order, NOT_AUTHORIZED, NO_SUCH_ROUND, NOT_REGISTERED,
    /// CLAIM_TOO_EARLY, CLAIM_EXPIRED, ALREADY_CLAIMED, SCORE_OUT_OF_RANGE,
    /// for each pair that can apply together. ledger-cycle and
    /// ledger-mint-order show who before when and when before once;
    /// ledger-round-zero who before the round; refusal-order the round
    /// before the node, and the node before when. This shows the rest.
    function test_claim_refusals_come_in_d26_order() external {
        deployStage3();
        address n = who("N");
        register("N");
        scores.set(n, 1_000_001);
        toRound(8);
        // When before the score: claim-order's pair (round 2 is claimable from 9).
        expectRefused(claimAs(n, n, 2), "CLAIM_TOO_EARLY", "early, score out of range");
        // Who before the score, and before when.
        expectRefused(claimAs(who("H"), n, 2), "NOT_AUTHORIZED", "holder, early, score out of range");
        // An unregistered address acting for itself is refused for the node
        // it names, after who (D-39); another address for it, for who.
        expectRefused(claimAs(who("X"), who("X"), 1), "NOT_REGISTERED", "unregistered, for itself");
        expectRefused(claimAs(who("H"), who("X"), 0), "NOT_AUTHORIZED", "for unregistered X, round 0");
        // The round before when, and before the score (D-40).
        expectRefused(claimAs(n, n, 0), "NO_SUCH_ROUND", "round 0, score out of range");
        // A round far beyond any now is early, not an overflow.
        expectRefused(claimAs(n, n, type(uint256).max), "CLAIM_TOO_EARLY", "the last round number");
        toRound(9);
        expectRefused(claimAs(n, n, 2), "SCORE_OUT_OF_RANGE", "score out of range");
        (bool claimed,,) = claims.entitlementOf(n, 2);
        require(!claimed, "a score out of range closed the entitlement");
        scores.set(n, 0);
        expectDone(claimAs(n, n, 2), "claim at 0");
        (bool closed, uint256 paid,) = claims.entitlementOf(n, 2);
        require(closed && paid == 0, "a claim at 0 pays 0 and closes");
        // Once before the score.
        scores.set(n, 1_000_001);
        expectRefused(claimAs(n, n, 2), "ALREADY_CLAIMED", "again, score out of range");
        // When before once (also ledger-mint-order), and who before both.
        toRound(31);
        expectRefused(claimAs(n, n, 2), "CLAIM_EXPIRED", "expired, again");
        expectRefused(claimAs(who("H"), n, 2), "NOT_AUTHORIZED", "holder, expired, again");
        // An unregistered node's own claim, expired: the node before when.
        expectRefused(claimAs(who("X"), who("X"), 2), "NOT_REGISTERED", "unregistered, expired");
        expectEq(lpt.totalSupply(), GENESIS, "nothing minted");
    }

    /// The claim window's two ends: round R is claimable in rounds R + 7
    /// to R + 28.
    function test_claim_window_ends() external {
        deployStage3();
        address n = who("N");
        address m = who("M");
        register("N");
        register("M");
        toRound(8);
        expectRefused(claimAs(n, n, 2), "CLAIM_TOO_EARLY", "round 2 in round 8");
        expectDone(claimAs(n, n, 1), "round 1 in round 8, the first of its window");
        toRound(29);
        expectDone(claimAs(m, m, 1), "round 1 in round 29, the last of its window");
        toRound(30);
        expectDone(claimAs(n, n, 2), "round 2 in round 30, the last of its window");
        toRound(31);
        expectRefused(claimAs(m, m, 2), "CLAIM_EXPIRED", "round 2 in round 31");
    }

    // --- R-10: a round's shares --------------------------------------------

    /// Shares are minted only once their round has ended. There is no
    /// round 0 (ledger-round-zero shows it in rounds 1 and 500).
    function test_shares_wait_for_the_round_to_end() external {
        deployStage3();
        toRound(3);
        expectRefused(
            as_(who("Keeper"), address(claims), abi.encodeCall(Claims.mintRoundShares, (0))), "NO_SUCH_ROUND", "round 0"
        );
        string[2] memory what = ["the current round", "a later round"];
        uint256[2] memory rounds = [uint256(3), 4];
        for (uint256 i = 0; i < 2; i++) {
            expectRefused(
                as_(who("Keeper"), address(claims), abi.encodeCall(Claims.mintRoundShares, (rounds[i]))),
                "CLAIM_TOO_EARLY",
                what[i]
            );
        }
        expectEq(lpt.totalSupply(), GENESIS, "nothing minted");
        expectDone(as_(who("Keeper"), address(claims), abi.encodeCall(Claims.mintRoundShares, (2))), "round 2");
        require(claims.sharesMinted(2) && !claims.sharesMinted(3), "sharesMinted");
    }

    // --- R-24: the cut in force when the earning round started -------------

    /// F-1: the claim of round 2 uses the cut set before round 2 started,
    /// not one set during round 2 nor the one current at the claim. N sets
    /// 100,000 in round 1, 300,000 in round 2 and 500,000 in round 5; with
    /// one fee in round 2 and H's bond behind it, it claims round 2 in round
    /// 9. ledger-same-node pins the same rule with the examples' numbers.
    function test_claim_reads_the_cut_in_force_at_the_start_of_the_earned_round() external {
        deployStage3();
        address n = who("N");
        address h = who("H");
        register("N");
        lpt.transfer(h, 100e18);
        expectDone(bondAs(h, n, 100e18), "bond");
        expectDone(fund("P", 1_000_000, address(0), 0), "fund");
        expectDone(as_(n, address(ledger), abi.encodeCall(StakeLedger.setRewardCut, (n, 100_000))), "cut in round 1");
        toRound(2);
        (ITicketBroker.Ticket memory t, bytes memory sig, uint256 rand) = ticket("t1", "P", n, 2, 1_000_000);
        expectDone(redeem(t, sig, rand), "redeem");
        expectDone(as_(n, address(ledger), abi.encodeCall(StakeLedger.setRewardCut, (n, 300_000))), "cut in round 2");
        toRound(5);
        expectDone(as_(n, address(ledger), abi.encodeCall(StakeLedger.setRewardCut, (n, 500_000))), "cut in round 5");
        toRound(9);
        expectDone(claimAs(n, n, 2), "claim");
        (, uint256 paid, uint256 pot) = claims.entitlementOf(n, 2);
        require(paid > 0, "the claim paid");
        expectEq(lpt.balanceOf(n), paid * 100_000 / 1_000_000, "cut: the one in force when round 2 started");
        expectEq(pot, paid - paid * 100_000 / 1_000_000, "pot");
    }

    // --- R-18, R-24: validator parts, with no members yet ---------------------

    /// Until S-06's phase 4 seats members, every validator part is 0, but
    /// the refusals are those R-24 names: a list naming round 0, whatever
    /// else it holds; a round whose shares are not minted; a round taken
    /// already. A refused list records nothing.
    function test_validator_parts_with_no_members() external {
        deployStage3();
        address h = who("H");
        address n = who("N");
        register("N");
        toRound(3);
        expectDone(as_(who("Keeper"), address(claims), abi.encodeCall(Claims.mintRoundShares, (1))), "shares 1");
        expectRefused(validatorWithdrawAs(h, n, one(2)), "NOT_CLAIMED", "shares of round 2 not minted");
        expectRefused(validatorWithdrawAs(h, n, two(2, 0)), "NO_SUCH_ROUND", "[2, 0]");
        expectRefused(validatorWithdrawAs(h, n, two(1, 1)), "ALREADY_CLAIMED", "round 1 named twice");
        expectDone(validatorWithdrawAs(h, n, one(1)), "round 1: no pot, so 0");
        expectRefused(validatorWithdrawAs(h, n, one(1)), "ALREADY_CLAIMED", "round 1 again");
        expectRefused(validatorWithdrawAs(h, n, two(1, 0)), "NO_SUCH_ROUND", "[1, 0], 1 taken");
        expectEq(lpt.balanceOf(h), 0, "H paid nothing");
    }

    // --- LPT that reaches Claims any other way -------------------------------

    /// LPT sent to Claims directly is never paid out: a part comes only from
    /// its own claim's pot, and a node that names Claims as its payout adds
    /// its cut to what is never paid.
    function test_lpt_sent_to_claims_is_never_paid() external {
        deployStage3();
        address n = who("N");
        address h = who("H");
        register("N");
        lpt.transfer(h, 10e18);
        expectDone(bondAs(h, n, 10e18), "bond");
        lpt.transfer(address(claims), 5e18);
        toRound(9);
        // Round 2 had no fees: the claim pays 0 and H's part is 0.
        expectDone(claimAs(n, n, 2), "claim");
        expectRefused(withdrawAs(h, n, one(3)), "NOT_CLAIMED", "round not claimed");
        expectDone(withdrawAs(h, n, one(2)), "withdraw");
        expectEq(lpt.balanceOf(h), 0, "H paid");
        expectEq(lpt.balanceOf(address(claims)), 5e18, "Claims keeps what it was sent");
    }

    // --- R-23: nothing grows with the number of nodes -----------------------

    /// Every Claims call, refusals and views included, costs the same gas
    /// after 999 more registrations as without them.
    function test_gas_does_not_grow_with_registered_nodes() external {
        deployStage3();
        uint256[] memory few = gasOfEveryClaimsCall();
        resetRunner();
        deployStage3();
        for (uint256 i = 0; i < 999; i++) {
            vm.prank(address(uint160(0x10000 + i)));
            registry.register();
        }
        uint256[] memory many = gasOfEveryClaimsCall();
        expectEq(many.length, few.length, "calls measured");
        for (uint256 i = 0; i < few.length; i++) {
            expectEq(many[i], few[i], string.concat("gas of call ", vm.toString(i)));
        }
    }

    /// N, cut 200,000, paid at W, with H's 100 LPT behind it, earns one
    /// ticket's fee in round 2; then every Claims call is made once.
    function gasOfEveryClaimsCall() internal returns (uint256[] memory) {
        address n = who("N");
        address h = who("H");
        register("N");
        setPayout("N", who("W"));
        vm.prank(n);
        ledger.setRewardCut(n, 200_000);
        lpt.transfer(h, 100e18);
        expectDone(bondAs(h, n, 100e18), "bond");
        expectDone(fund("P", 1_000_000, address(0), 0), "fund");
        toRound(2);
        (ITicketBroker.Ticket memory t, bytes memory sig, uint256 rand) = ticket("t1", "P", n, 2, 1_000_000);
        expectDone(redeem(t, sig, rand), "redeem");
        toRound(3);
        measure(who("Keeper"), address(claims), abi.encodeCall(Claims.mintRoundShares, (2)));
        measure(who("Keeper"), address(claims), abi.encodeCall(Claims.mintRoundShares, (2)));
        measure(who("Keeper"), address(claims), abi.encodeCall(Claims.mintRoundShares, (3)));
        toRound(9);
        measure(h, address(claims), abi.encodeCall(Claims.claim, (n, 2)));
        measure(n, address(claims), abi.encodeCall(Claims.claim, (n, 2)));
        measure(n, address(claims), abi.encodeCall(Claims.claim, (n, 2)));
        measure(h, address(claims), abi.encodeCall(Claims.withdrawParts, (n, one(3))));
        measure(h, address(claims), abi.encodeCall(Claims.withdrawParts, (n, one(2))));
        measure(h, address(claims), abi.encodeCall(Claims.withdrawParts, (n, one(2))));
        measure(h, address(claims), abi.encodeCall(Claims.withdrawParts, (n, one(0))));
        measure(h, address(claims), abi.encodeCall(Claims.withdrawValidatorParts, (n, one(2))));
        measure(h, address(claims), abi.encodeCall(Claims.withdrawValidatorParts, (n, one(2))));
        measure(h, address(claims), abi.encodeCall(Claims.claim, (n, 0)));
        measure(h, address(claims), abi.encodeCall(Claims.entitlementOf, (n, 2)));
        measure(h, address(claims), abi.encodeCall(Claims.partOf, (h, n, 2)));
        measure(h, address(claims), abi.encodeCall(Claims.sharesMinted, (2)));
        require(lpt.balanceOf(h) > 0 && lpt.balanceOf(who("W")) > 0, "the claim paid");
        return stepGas;
    }
}
