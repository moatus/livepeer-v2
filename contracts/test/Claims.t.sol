// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {vm, Vm} from "./Vm.sol";
import {LedgerRunnerV2} from "./LedgerRunnerV2.sol";
import {Claims} from "../src/money/Claims.sol";
import {Scores} from "../src/scores/Scores.sol";
import {StakeLedger} from "../src/money/StakeLedger.sol";
import {RewardMath} from "../src/math/RewardMath.sol";
import {IClaims} from "../interfaces/IClaims.sol";
import {ITicketBroker} from "../interfaces/ITicketBroker.sol";

/// Claims (S-05 phase 4, S-06 phase 4): the v2 `ledger` cases with claims,
/// withdrawals, share mints and validator pots, from
/// vectors/october-main-text-v4.json, with the after-step checks of the
/// shared examples (LedgerRunnerV2); then the refusals and orders no case
/// reaches, validator pay on the real Scores, and R-23's gas checks.
contract ClaimsTest is LedgerRunnerV2 {
    event log_named_uint(string key, uint256 val);

    /// The deployment's validator share of emission (R-07, R-25).
    uint256 private constant VALIDATOR_SHARE = 10_000;

    function setUp() external {
        loadVectors("october-main-text-v4");
    }

    function test_ledger_cycle() external {
        runV2("ledger-cycle");
    }

    function test_ledger_window() external {
        runV2("ledger-window");
    }

    function test_ledger_mint_order() external {
        runV2("ledger-mint-order");
    }

    function test_ledger_round_zero() external {
        runV2("ledger-round-zero");
    }

    function test_ledger_same_node() external {
        runV2("ledger-same-node");
    }

    function test_refusal_order() external {
        runV2("refusal-order");
    }

    function test_ledger_validator_pot() external {
        runV2("ledger-validator-pot");
    }

    function test_ledger_mid_round() external {
        runV2("ledger-mid-round");
    }

    // R-25 (S-10 phase 3): Parameters' changes, read for the round each
    // part names. The section 1 table: round 5 claimed after round 11.
    function test_ledger_authority() external {
        runV2("ledger-authority");
    }

    function test_ledger_delays() external {
        runV2("ledger-delays");
    }

    /// A claim in round 13 reads the score source that counts from it.
    function test_ledger_score_source() external {
        runV2("ledger-score-source");
    }

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

    // --- R-18, R-24: validator parts of a round with no members ---------------

    /// A round with no members has no validator pot, so each part is 0,
    /// with the refusals R-24 names: a list naming round 0, whatever else it
    /// holds; a round whose shares are not minted; a round taken already. A
    /// refused list records nothing.
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

    // --- R-13, R-14 on the real Scores: whose pay a round mints -------------

    /// A node seated after round R and then making R's mint is paid 0; R's
    /// members, read as R started, are paid, including the one the late node
    /// displaced. R's members are fixed when it starts (membersAt(R)), not
    /// read when the mint runs. The next round pays the late node and not
    /// the one it displaced.
    ///
    /// A, B and C, with 5,000, 3,000 and 2,000 LPT bonded to themselves in
    /// round 1, opt in in round 2 and are the members of round 3. X, with
    /// 6,000 LPT, opts in during round 3 and displaces C from round 4. X
    /// mints round 3's shares in round 4, and round 4's in round 5.
    function test_a_node_seated_after_the_round_is_paid_nothing() external {
        deployStage3WithScores();
        string[4] memory ns = ["A", "B", "C", "X"];
        uint256[4] memory amounts = [uint256(5_000e18), 3_000e18, 2_000e18, 6_000e18];
        for (uint256 i = 0; i < 4; i++) {
            register(ns[i]);
            remember(lptNodes, ns[i]);
            remember(holders, ns[i]);
            lpt.transfer(who(ns[i]), amounts[i]);
            expectDone(bondAs(who(ns[i]), who(ns[i]), amounts[i]), string.concat(ns[i], " bonds"));
        }
        toRound(2);
        for (uint256 i = 0; i < 3; i++) {
            optInAs(ns[i]);
        }
        toRound(3);
        optInAs("X");
        requireMembers(3, who("A"), who("B"), who("C"));
        requireMembers(4, who("A"), who("B"), who("X"));
        toRound(4);
        address x = who("X");
        expectDone(doMintShares(x, 3), "X mints round 3's shares");
        holdsEverything("round 3's shares");
        (uint256 paid, uint256 cut, uint256 pot) = claims.validatorPayOf(x, 3);
        require(paid == 0 && cut == 0 && pot == 0, "X, seated after round 3, paid for it");
        expectEq(lpt.balanceOf(x), 0, "X's LPT after round 3's mint");
        // Each of round 3's members is paid R-14's pay, by its stake at the
        // start of round 3 over the members' 10,000 LPT; C, displaced since,
        // too. Its cut is 0, so all of it is the pot, here for its holders.
        uint256 c3 = RewardMath.ceiling(2);
        for (uint256 i = 0; i < 3; i++) {
            (paid,, pot) = claims.validatorPayOf(who(ns[i]), 3);
            expectEq(
                paid,
                RewardMath.validatorPayOf(c3, VALIDATOR_SHARE, amounts[i], 10_000e18),
                string.concat(ns[i], " paid for round 3")
            );
            require(paid > 0 && pot == paid, "a member of round 3 paid, all to its pot");
        }
        // X's part of round 3 is 0, and X may still take it, once.
        expectDone(doWithdrawValidator(x, x, one(3)), "X's validator part of round 3");
        expectEq(lpt.balanceOf(x), 0, "X's part of round 3");
        holdsEverything("X withdraws round 3");

        toRound(5);
        expectDone(doMintShares(x, 4), "X mints round 4's shares");
        holdsEverything("round 4's shares");
        (paid,,) = claims.validatorPayOf(x, 4);
        expectEq(
            paid,
            RewardMath.validatorPayOf(RewardMath.ceiling(3), VALIDATOR_SHARE, 6_000e18, 14_000e18),
            "X paid for round 4"
        );
        (paid,,) = claims.validatorPayOf(who("C"), 4);
        expectEq(paid, 0, "C, displaced from round 4, paid for it");
    }

    /// A holder of a member that also claimed a node reward for round R
    /// withdraws both of its parts of R, each once, in either order; a
    /// second of either is ALREADY_CLAIMED; neither takes or blocks the
    /// other; each comes from its own pot. After every step Claims holds
    /// both kinds of pot less the parts withdrawn (holdsEverything).
    ///
    /// V, cut 100,000, paid at W, has its operator's 600 LPT and H's 400
    /// behind it from round 2, opts in in round 2 and is round 3's one
    /// member. It earns a ticket's fee in round 3, round 3's shares are
    /// minted in round 4, and V claims round 3 in round 10. H takes its
    /// validator part first and its node part second; V's operator, the
    /// other way round, in round 40.
    function test_both_parts_of_a_round_withdrawn_once_each() external {
        deployStage3WithScores();
        address v = who("V");
        address h = who("H");
        register("V");
        setPayout("V", who("W"));
        expectDone(as_(v, address(ledger), abi.encodeCall(StakeLedger.setRewardCut, (v, 100_000))), "cut");
        remember(lptNodes, "V");
        remember(holders, "V");
        remember(holders, "H");
        remember(payers, "P");
        lpt.transfer(v, 600e18);
        lpt.transfer(h, 400e18);
        expectDone(bondAs(v, v, 600e18), "V bonds");
        expectDone(bondAs(h, v, 400e18), "H bonds");
        expectDone(fund("P", 1_000_000, address(0), 0), "fund");
        holdsEverything("round 1");
        toRound(2);
        optInAs("V");
        toRound(3);
        requireMembers(3, v, address(0), address(0));
        (ITicketBroker.Ticket memory tk, bytes memory sig, uint256 rand) = ticket("t1", "P", v, 3, 1_000_000);
        expectDone(redeem(tk, sig, rand), "redeem");
        // H's bond changes after round 3 started: neither part moves.
        lpt.transfer(h, 100e18);
        expectDone(bondAs(h, v, 100e18), "H bonds more in round 3");
        holdsEverything("round 3");

        toRound(4);
        expectRefused(doWithdrawValidator(h, v, one(3)), "NOT_CLAIMED", "H's validator part before the mint");
        expectDone(doMintShares(who("Keeper"), 3), "round 3's shares");
        holdsEverything("shares");
        (,, uint256 vpot) = claims.validatorPayOf(v, 3);
        require(vpot > 0, "V's validator pot");
        expectDone(doWithdrawValidator(h, v, one(3)), "H's validator part");
        expectEq(lpt.balanceOf(h), vpot * 400 / 1_000, "H's validator part: its bond at the start of round 3");
        holdsEverything("H's validator part");
        expectRefused(doWithdrawValidator(h, v, one(3)), "ALREADY_CLAIMED", "H's validator part again");
        holdsEverything("H's validator part again");
        expectRefused(doWithdraw(h, v, one(3)), "NOT_CLAIMED", "H's node part before V claims");
        holdsEverything("H's node part before the claim");

        toRound(10);
        expectDone(doClaim(v, v, 3, 1_000_000), "V claims round 3");
        holdsEverything("V's claim");
        (,, uint256 pot) = claims.entitlementOf(v, 3);
        require(pot > 0 && pot != vpot, "V's claim pot, apart from its validator pot");
        uint256 before = lpt.balanceOf(h);
        expectDone(doWithdraw(h, v, one(3)), "H's node part");
        expectEq(lpt.balanceOf(h) - before, pot * 400 / 1_000, "H's node part");
        holdsEverything("H's node part");
        expectRefused(doWithdraw(h, v, one(3)), "ALREADY_CLAIMED", "H's node part again");
        expectRefused(doWithdrawValidator(h, v, one(3)), "ALREADY_CLAIMED", "H's validator part, third time");
        holdsEverything("H again");

        // V's operator, in round 40, past round 3's claim window (rounds 10
        // to 31): parts never expire (R-24). Node part first, then validator
        // part, then each again.
        toRound(40);
        before = lpt.balanceOf(v);
        expectDone(doWithdraw(v, v, one(3)), "V's node part");
        expectEq(lpt.balanceOf(v) - before, pot * 600 / 1_000, "V's node part");
        holdsEverything("V's node part");
        expectDone(doWithdrawValidator(v, v, one(3)), "V's validator part");
        expectEq(lpt.balanceOf(v) - before, pot * 600 / 1_000 + vpot * 600 / 1_000, "V's validator part");
        holdsEverything("V's validator part");
        expectRefused(doWithdraw(v, v, one(3)), "ALREADY_CLAIMED", "V's node part again");
        expectRefused(doWithdrawValidator(v, v, one(3)), "ALREADY_CLAIMED", "V's validator part again");
        holdsEverything("V again");
        // What the floors left of each pot stays in Claims.
        expectEq(
            lpt.balanceOf(address(claims)),
            pot - pot * 400 / 1_000 - pot * 600 / 1_000 + vpot - vpot * 400 / 1_000 - vpot * 600 / 1_000,
            "Claims keeps the floors' dust"
        );
    }

    // --- R-23: the share mint does not grow with holders or opted-in nodes ---

    /// Bound: the gas of mintRoundShares at 100 and 200 holders, and at 100
    /// and 200 opted-in nodes, equals its gas at 1 holder and 3 opted-in
    /// nodes, to the unit. Why that bound: in every world the same three
    /// members hold the same stake and cut, so the mint reads and writes the
    /// same slots, all cold (each world is measured with every contract's
    /// storage cooled first), and none of them is chosen by how many holders
    /// bonded or how many nodes opted in. Any read per holder or per opted-in
    /// node would add at least 100 gas each, 9,900 more at 100.
    ///
    /// A, B and C opt in in round 2 and are round 3's members, with 1,000,
    /// 3,000 and 2,000 LPT at its start. A's 1,000 are bonded by `holders`
    /// holders of 1 LPT each and its operator, who bonds the rest; every
    /// extra opted-in node has no stake, so none is seated.
    function test_mint_gas_does_not_grow_with_holders_or_opted_in_nodes() external {
        uint256[6] memory hs = [uint256(1), 100, 200, 1, 1, 200];
        uint256[6] memory os = [uint256(3), 3, 3, 100, 200, 200];
        uint256 base;
        for (uint256 k = 0; k < hs.length; k++) {
            uint256 g = mintGasAt(hs[k], os[k]);
            emit log_named_uint(
                string.concat("mintRoundShares at ", vm.toString(hs[k]), " holders, ", vm.toString(os[k]), " opted in"),
                g
            );
            if (k == 0) {
                base = g;
            } else {
                expectEq(g, base, string.concat("mint gas at ", vm.toString(hs[k]), " holders, ", vm.toString(os[k])));
            }
        }
    }

    function mintGasAt(uint256 holderCount, uint256 optedIn) internal returns (uint256) {
        resetRunner();
        deployStage3WithScores();
        string[3] memory ns = ["A", "B", "C"];
        uint256[3] memory amounts = [uint256(1_000e18), 3_000e18, 2_000e18];
        for (uint256 i = 0; i < 3; i++) {
            register(ns[i]);
            uint256 own = i == 0 ? amounts[0] - holderCount * 1e18 : amounts[i];
            lpt.transfer(who(ns[i]), own);
            expectDone(bondAs(who(ns[i]), who(ns[i]), own), "operator bonds");
        }
        for (uint256 i = 0; i < holderCount; i++) {
            address hd = address(uint160(0x30000 + i));
            lpt.transfer(hd, 1e18);
            expectDone(bondAs(hd, who("A"), 1e18), "holder bonds");
        }
        for (uint256 i = 0; i < optedIn - 3; i++) {
            vm.prank(address(uint160(0x20000 + i)));
            registry.register();
        }
        toRound(2);
        for (uint256 i = 0; i < 3; i++) {
            optInAs(ns[i]);
        }
        for (uint256 i = 0; i < optedIn - 3; i++) {
            address e = address(uint160(0x20000 + i));
            expectDone(as_(e, address(validatorSet), abi.encodeCall(Scores.optIn, (e))), "extra opts in");
        }
        toRound(4);
        requireMembers(3, who("A"), who("B"), who("C"));
        vm.cool(address(claims));
        vm.cool(address(validatorSet));
        vm.cool(address(ledger));
        vm.cool(address(registry));
        vm.cool(address(clock));
        vm.cool(address(lpt));
        expectDone(as_(who("Keeper"), address(claims), abi.encodeCall(Claims.mintRoundShares, (3))), "mint");
        uint256 g = vm.lastFrameGas().gasTotalUsed;
        (uint256 paid,,) = claims.validatorPayOf(who("A"), 3);
        require(paid > 0, "A paid");
        return g;
    }

    function optInAs(string memory name) internal {
        address n = who(name);
        expectDone(as_(n, address(validatorSet), abi.encodeCall(Scores.optIn, (n))), string.concat(name, " opts in"));
    }

    /// membersAt(round) is exactly a, b, c, in that order, less any 0.
    function requireMembers(uint256 round, address a, address b, address c) internal view {
        address[] memory got = validatorSet.membersAt(round);
        address[3] memory want = [a, b, c];
        uint256 n = 0;
        for (uint256 i = 0; i < 3; i++) {
            if (want[i] != address(0)) n++;
        }
        expectEq(got.length, n, string.concat("members of round ", vm.toString(round)));
        for (uint256 i = 0; i < n; i++) {
            require(got[i] == want[i], string.concat("member of round ", vm.toString(round)));
        }
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
        measure(h, address(claims), abi.encodeCall(Claims.validatorPayOf, (n, 2)));
        measure(h, address(claims), abi.encodeCall(Claims.validatorPartOf, (h, n, 2)));
        require(lpt.balanceOf(h) > 0 && lpt.balanceOf(who("W")) > 0, "the claim paid");
        return stepGas;
    }
}
