// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {vm} from "./Vm.sol";
import {LedgerRunnerV2} from "./LedgerRunnerV2.sol";
import {Claims} from "../src/money/Claims.sol";
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

    function one(uint256 r) internal pure returns (uint256[] memory list) {
        list = new uint256[](1);
        list[0] = r;
    }

    // --- D-26: the first refusal that applies -----------------------------

    /// R-12's order, NOT_AUTHORIZED, CLAIM_TOO_EARLY, CLAIM_EXPIRED,
    /// ALREADY_CLAIMED, SCORE_OUT_OF_RANGE, for each pair that can apply
    /// together. ledger-cycle and ledger-mint-order show who before when
    /// and when before once; this shows the rest.
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
        // An unregistered address may not act even for itself.
        expectRefused(claimAs(who("X"), who("X"), 1), "NOT_AUTHORIZED", "unregistered");
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

    /// Shares are minted only once their round has ended; round 0 never ran.
    function test_shares_wait_for_the_round_to_end() external {
        deployStage3();
        toRound(3);
        string[3] memory what = ["round 0", "the current round", "a later round"];
        uint256[3] memory rounds = [uint256(0), 3, 4];
        for (uint256 i = 0; i < 3; i++) {
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
        measure(h, address(claims), abi.encodeCall(Claims.entitlementOf, (n, 2)));
        measure(h, address(claims), abi.encodeCall(Claims.partOf, (h, n, 2)));
        measure(h, address(claims), abi.encodeCall(Claims.sharesMinted, (2)));
        require(lpt.balanceOf(h) > 0 && lpt.balanceOf(who("W")) > 0, "the claim paid");
        return stepGas;
    }
}
