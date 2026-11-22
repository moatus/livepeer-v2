// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {vm, Vm} from "./Vm.sol";
import {LedgerRunner} from "./LedgerRunner.sol";
import {Stage3, ScoreStub} from "./Stage3.sol";
import {StakeLedger} from "../src/money/StakeLedger.sol";
import {Claims} from "../src/money/Claims.sol";
import {IClaims} from "../interfaces/IClaims.sol";
import {RewardMath} from "../src/math/RewardMath.sol";
import {IParameters} from "../interfaces/IParameters.sol";

/// Runs a v2 `ledger` case on a stage 3 deployment: v1's steps, the LPT
/// steps of rulebook v2 and the claim, withdrawal and share steps. After
/// every step it holds the contracts to the shared examples' after-step
/// checks, each figure seen by the test from balances and the supply, not
/// taken from Claims' own books alone:
///
/// - StakeLedger's LPT is every bond staying, in transfer or arriving, plus
///   the unbonded LPT not withdrawn;
/// - Claims' LPT is the pots of the claims made and of the validator
///   shares minted, less the parts of each withdrawn, and no claim's or
///   (round, member) validator pot's withdrawn parts exceed it;
/// - the LPT minted for each earning round R is at most R's ceiling, C at
///   R's schedule position (R - 1 until a schedule change), read from the
///   examples' ceiling cases;
/// - the supply is the genesis supply plus what was minted for each round.
///
/// Only a claim or a share mint may change the supply, and a withdrawal of
/// either kind of part moves LPT out of Claims to the holder by transfer,
/// never by a mint.
///
/// Each step also has an address form (doClaim, doWithdraw,
/// doWithdrawValidator, doMintShares), so a test can take steps no case
/// lists and hold the same checks after each with holdsEverything.
abstract contract LedgerRunnerV2 is LedgerRunner, Stage3 {
    /// Names an LPT step gave, as holders and as nodes.
    string[] internal holders;
    string[] internal lptNodes;
    /// Gas of the main call of each step, when measured.
    uint256[] internal stepGas;

    /// Each claim that took effect: its node and earning round.
    address[] internal claimedNode;
    uint256[] internal claimedRound;
    /// The LPT minted for each earning round, as the supply rose at that
    /// round's claim and share steps.
    uint256[] internal mintedRound;
    uint256[] internal mintedAmount;
    /// Every round whose shares were minted, with R-10's two amounts as
    /// RoundSharesMinted gave them.
    uint256[] internal sharesRound;
    uint256[] internal sharesValidators;
    uint256[] internal sharesTreasury;
    /// LPT that withdrawals moved out of Claims, as the holders' balances
    /// rose: of claims' pots, and of validator pots.
    uint256 internal withdrawnTotal;
    uint256 internal validatorWithdrawnTotal;
    /// Each (round, member) validator pay a share mint made, as its
    /// ValidatorPaid event gave it.
    uint256[] internal paidRound;
    address[] internal paidMember;
    /// Contracts a v4 case names under `addresses`, each a score stub with
    /// code, with the deployment's own stub as "Scores": the score sources a
    /// claim's `scores` names (R-25, group 6).
    string[] internal sourceNames;
    /// The examples' ceiling cases, read once per case: position and C.
    uint256[] internal ceilingAt;
    uint256[] internal ceilingWei;
    ScoreStub[] internal sources;

    // --- the steps ------------------------------------------------------

    function act(string memory s, string memory action) internal virtual override returns (bytes memory refused) {
        if (eq(action, "claim")) return claimStep(s);
        if (eq(action, "withdraw_parts")) return withdrawStep(s);
        if (eq(action, "mint_shares")) return mintSharesStep(s);
        if (eq(action, "withdraw_validator_parts")) return withdrawValidatorStep(s);
        if (eq(action, "announce")) return announceStep(s);
        if (eq(action, "withdraw")) {
            return
                as_(who(str(s, ".caller")), address(params), abi.encodeCall(IParameters.withdraw, (num(s, ".group"))));
        }
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
                s, abi.encodeCall(StakeLedger.unbondTransfer, (node(s, ".from"), node(s, ".to"), num(s, ".amount_wei")))
            );
        } else if (eq(action, "rebond")) {
            refused = holderCall(s, abi.encodeCall(StakeLedger.rebond, (node(s, ".node"), num(s, ".amount_wei"))));
        } else if (eq(action, "withdraw_unbonded")) {
            refused = holderCall(s, abi.encodeCall(StakeLedger.withdrawUnbonded, ()));
        } else if (eq(action, "set_cut")) {
            // `caller` sets `node`'s cut; with no `caller`, the node itself.
            address n = node(s, ".node");
            address c = vm.keyExistsJson(vectors, string.concat(s, ".caller")) ? who(str(s, ".caller")) : n;
            refused = as_(c, address(ledger), abi.encodeCall(StakeLedger.setRewardCut, (n, num(s, ".cut_millionths"))));
        } else if (eq(action, "observe_stake")) {
            uint256 r = clock.currentRound();
            observed(s, ".stake_wei", ledger.stakeAt(node(s, ".node"), r), ".total_stake_wei", ledger.totalStakeAt(r));
        } else {
            return super.act(s, action);
        }
        stepGas.push(vm.lastFrameGas().gasTotalUsed);
    }

    /// `caller` claims `node`'s reward for `earned_round`, and the claim
    /// reads `score`. What it mints is the supply's rise, the pot is Claims'
    /// rise and the cut the payout's, each equal to what Claims recorded.
    function claimStep(string memory s) internal returns (bytes memory refused) {
        address n = node(s, ".node");
        if (!vm.keyExistsJson(vectors, string.concat(s, ".scores"))) {
            return doClaim(who(str(s, ".caller")), n, num(s, ".earned_round"), num(s, ".score"));
        }
        // What each score source gives now; a source the step does not name
        // gives 10^6. The claim must read the one in force (R-25, group 6).
        for (uint256 i = 0; i < sources.length; i++) {
            string memory k = string.concat(".scores.", sourceNames[i]);
            sources[i].set(n, vm.keyExistsJson(vectors, string.concat(s, k)) ? num(s, k) : 1_000_000);
        }
        return doClaimAsGiven(who(str(s, ".caller")), n, num(s, ".earned_round"));
    }

    /// `caller` announces `values` for `group` (R-25). A value that is a
    /// name is the address of the contract the case gave it.
    function announceStep(string memory s) internal returns (bytes memory) {
        string[] memory raw = strList(s, ".values");
        uint256[] memory values = new uint256[](raw.length);
        for (uint256 i = 0; i < raw.length; i++) {
            bytes1 c = bytes(raw[i])[0];
            values[i] = c >= "0" && c <= "9" ? vm.parseUint(raw[i]) : uint256(uint160(sourceOf(raw[i])));
        }
        return
            as_(
                who(str(s, ".caller")),
                address(params),
                abi.encodeCall(IParameters.announce, (num(s, ".group"), values))
            );
    }

    function sourceOf(string memory name) internal view returns (address) {
        for (uint256 i = 0; i < sources.length; i++) {
            if (eq(sourceNames[i], name)) return address(sources[i]);
        }
        revert(string.concat("no contract named ", name));
    }

    function doClaim(address caller, address n, uint256 r, uint256 score) internal returns (bytes memory refused) {
        // The stub is told the case's score; the real Scores must give it.
        if (address(scores) != address(0)) scores.set(n, score);
        else expectEq(validatorSet.scoreOf(n), score, "claim: the score Scores gives");
        return doClaimAsGiven(caller, n, r);
    }

    function doClaimAsGiven(address caller, address n, uint256 r) internal returns (bytes memory refused) {
        address payout = registry.payoutOf(n);
        uint256 supply = lpt.totalSupply();
        uint256 held = lpt.balanceOf(address(claims));
        uint256 paidBefore = lpt.balanceOf(payout);
        refused = as_(caller, address(claims), abi.encodeCall(Claims.claim, (n, r)));
        stepGas.push(vm.lastFrameGas().gasTotalUsed);
        if (refused.length > 0) {
            expectEq(lpt.totalSupply(), supply, "a refused claim minted");
            return refused;
        }
        (bool claimed, uint256 paid, uint256 pot) = claims.entitlementOf(n, r);
        require(claimed, "a claim that took effect is not recorded");
        expectEq(lpt.totalSupply() - supply, paid, "claim: minted, against what Claims recorded as paid");
        expectEq(lpt.balanceOf(address(claims)) - held, pot, "claim: pot to Claims");
        expectEq(lpt.balanceOf(payout) - paidBefore, paid - pot, "claim: cut to the payout");
        claimedNode.push(n);
        claimedRound.push(r);
        addMinted(r, paid);
    }

    /// `holder` withdraws its parts of `node`'s claims for `rounds`. Claims
    /// falls by exactly what the holder gains, and the supply does not move.
    function withdrawStep(string memory s) internal returns (bytes memory refused) {
        return doWithdraw(holder(s), node(s, ".node"), numList(s, ".rounds"));
    }

    function doWithdraw(address h, address n, uint256[] memory rounds) internal returns (bytes memory refused) {
        uint256 supply = lpt.totalSupply();
        uint256 held = lpt.balanceOf(address(claims));
        uint256 mine = lpt.balanceOf(h);
        refused = as_(h, address(claims), abi.encodeCall(Claims.withdrawParts, (n, rounds)));
        stepGas.push(vm.lastFrameGas().gasTotalUsed);
        expectEq(lpt.totalSupply(), supply, "a withdrawal minted");
        uint256 gained = lpt.balanceOf(h) - mine;
        expectEq(held - lpt.balanceOf(address(claims)), gained, "withdrawal: Claims paid other than the holder gained");
        if (refused.length > 0) expectEq(gained, 0, "a refused withdrawal moved LPT");
        withdrawnTotal += gained;
    }

    /// `holder` withdraws its parts of `node`'s validator pots for `rounds`.
    /// Claims falls by exactly what the holder gains, and the supply does
    /// not move.
    function withdrawValidatorStep(string memory s) internal returns (bytes memory refused) {
        return doWithdrawValidator(holder(s), node(s, ".node"), numList(s, ".rounds"));
    }

    function doWithdrawValidator(address h, address n, uint256[] memory rounds)
        internal
        returns (bytes memory refused)
    {
        uint256 supply = lpt.totalSupply();
        uint256 held = lpt.balanceOf(address(claims));
        uint256 mine = lpt.balanceOf(h);
        refused = as_(h, address(claims), abi.encodeCall(Claims.withdrawValidatorParts, (n, rounds)));
        stepGas.push(vm.lastFrameGas().gasTotalUsed);
        expectEq(lpt.totalSupply(), supply, "a validator withdrawal minted");
        uint256 gained = lpt.balanceOf(h) - mine;
        expectEq(
            held - lpt.balanceOf(address(claims)),
            gained,
            "validator withdrawal: Claims paid other than the holder gained"
        );
        if (refused.length > 0) expectEq(gained, 0, "a refused validator withdrawal moved LPT");
        validatorWithdrawnTotal += gained;
    }

    /// `caller` mints `earned_round`'s validator and treasury amounts. The
    /// supply rises by exactly R-10's two amounts. One ValidatorPaid comes
    /// for each member the validator set gives for the round, in its order,
    /// each agreeing with Claims' view; each payout rises by the cuts paid
    /// to it, Claims by the pots, and the treasury by the treasury amount
    /// and whatever the members' pay leaves of the validator amount.
    function mintSharesStep(string memory s) internal returns (bytes memory refused) {
        return doMintShares(who(str(s, ".caller")), num(s, ".earned_round"));
    }

    function doMintShares(address caller, uint256 r) internal returns (bytes memory refused) {
        address[] memory members = claimsMembersAt(r);
        uint256[] memory before = new uint256[](members.length);
        for (uint256 i = 0; i < members.length; i++) {
            before[i] = lpt.balanceOf(registry.payoutOf(members[i]));
        }
        uint256 supply = lpt.totalSupply();
        uint256 kept = lpt.balanceOf(treasuryAddress());
        uint256 held = lpt.balanceOf(address(claims));
        vm.recordLogs();
        refused = as_(caller, address(claims), abi.encodeCall(Claims.mintRoundShares, (r)));
        stepGas.push(vm.lastFrameGas().gasTotalUsed);
        Vm.Log[] memory logs = vm.getRecordedLogs();
        if (refused.length > 0) {
            expectEq(lpt.totalSupply(), supply, "a refused share mint minted");
            return refused;
        }
        require(claims.sharesMinted(r), "shares minted but not recorded");
        (uint256 v, uint256 t, uint256 paid, uint256 pots) = sharesFromLogs(logs, r, members);
        expectEq(lpt.totalSupply() - supply, v + t, "shares: minted");
        require(paid <= v, "shares: members paid above the validator amount");
        expectEq(lpt.balanceOf(treasuryAddress()) - kept, t + v - paid, "shares: to the treasury");
        expectEq(lpt.balanceOf(address(claims)) - held, pots, "shares: pots to Claims");
        holdsCuts(members, before, r);
        sharesRound.push(r);
        sharesValidators.push(v);
        sharesTreasury.push(t);
        addMinted(r, v + t);
    }

    /// Each member's payout rose by the cuts the mint paid to it.
    function holdsCuts(address[] memory members, uint256[] memory before, uint256 r) internal view {
        for (uint256 i = 0; i < members.length; i++) {
            address payout = registry.payoutOf(members[i]);
            uint256 cuts = 0;
            for (uint256 j = 0; j < members.length; j++) {
                if (registry.payoutOf(members[j]) != payout) continue;
                (, uint256 cut,) = claims.validatorPayOf(members[j], r);
                cuts += cut;
            }
            expectEq(lpt.balanceOf(payout) - before[i], cuts, "shares: cuts to the payout");
        }
    }

    /// R-10's two amounts from the one RoundSharesMinted, and the members'
    /// pay and pots from one ValidatorPaid per member, in the order the
    /// validator set gives them, each equal to Claims' view and splitting
    /// its pay into the cut and the pot.
    function sharesFromLogs(Vm.Log[] memory logs, uint256 r, address[] memory members)
        internal
        returns (uint256 v, uint256 t, uint256 paid, uint256 pots)
    {
        uint256 seen = 0;
        uint256 k = 0;
        for (uint256 i = 0; i < logs.length; i++) {
            if (logs[i].emitter != address(claims)) continue;
            if (logs[i].topics[0] == IClaims.RoundSharesMinted.selector) {
                expectEq(uint256(logs[i].topics[1]), r, "RoundSharesMinted round");
                (v, t) = abi.decode(logs[i].data, (uint256, uint256));
                seen++;
            } else if (logs[i].topics[0] == IClaims.ValidatorPaid.selector) {
                require(k < members.length, "ValidatorPaid for more than the members");
                (uint256 p, uint256 pot) = paidFromLog(logs[i], r, members[k]);
                paid += p;
                pots += pot;
                paidRound.push(r);
                paidMember.push(members[k]);
                k++;
            }
        }
        expectEq(seen, 1, "RoundSharesMinted events");
        expectEq(k, members.length, "ValidatorPaid events, one per member");
    }

    /// One ValidatorPaid, for member `m` of round `r`: its payout, and its
    /// cut and pot making its pay, each as Claims' view gives them.
    function paidFromLog(Vm.Log memory log, uint256 r, address m) internal view returns (uint256 p, uint256 pot) {
        expectEq(uint256(log.topics[1]), r, "ValidatorPaid round");
        require(address(uint160(uint256(log.topics[2]))) == m, "ValidatorPaid member");
        address payout;
        uint256 cut;
        (payout, p, cut, pot) = abi.decode(log.data, (address, uint256, uint256, uint256));
        require(payout == registry.payoutOf(m), "ValidatorPaid payout");
        expectEq(cut + pot, p, "ValidatorPaid: cut and pot make the pay");
        (uint256 vp, uint256 vc, uint256 vpot) = claims.validatorPayOf(m, r);
        require(vp == p && vc == cut && vpot == pot, "ValidatorPaid against validatorPayOf");
    }

    /// The members Claims' validator set gives for round `r`.
    function claimsMembersAt(uint256 r) internal view returns (address[] memory) {
        return validatorSet.membersAt(r);
    }

    /// `caller` calls `target`, and the gas of that call is kept, whether
    /// it took effect or was refused.
    function measure(address caller, address target, bytes memory data) internal {
        vm.prank(caller);
        (bool ok,) = target.call(data);
        ok;
        stepGas.push(vm.lastFrameGas().gasTotalUsed);
    }

    function addMinted(uint256 r, uint256 amount) internal {
        for (uint256 i = 0; i < mintedRound.length; i++) {
            if (mintedRound[i] == r) {
                mintedAmount[i] += amount;
                return;
            }
        }
        mintedRound.push(r);
        mintedAmount.push(amount);
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

    /// Runs a v2 ledger case. Its `members`, for each earning round it
    /// names, are given to the stand-in validator set first; a round it does
    /// not name has none.
    function stepsV2(string memory id) internal {
        string memory inputs = string.concat(".cases[", vm.toString(caseIndex(id)), "].inputs");
        loadCeilings();
        if (vm.keyExistsJson(vectors, string.concat(inputs, ".authority"))) {
            require(params.authority() == who(str(inputs, ".authority")), "the case's authority");
        }
        if (address(scores) != address(0)) {
            sourceNames.push("Scores");
            sources.push(scores);
        }
        if (vm.keyExistsJson(vectors, string.concat(inputs, ".addresses"))) {
            string[] memory named = vm.parseJsonKeys(vectors, string.concat(inputs, ".addresses"));
            for (uint256 i = 0; i < named.length; i++) {
                require(
                    vm.parseJsonBool(vectors, string.concat(inputs, ".addresses.", named[i], ".code")),
                    "a named address with no code"
                );
                sourceNames.push(named[i]);
                sources.push(new ScoreStub());
            }
        }
        string memory at = string.concat(inputs, ".members");
        if (vm.keyExistsJson(vectors, at)) {
            string[] memory rounds = vm.parseJsonKeys(vectors, at);
            for (uint256 i = 0; i < rounds.length; i++) {
                string[] memory names = abi.decode(vm.parseJson(vectors, string.concat(at, ".", rounds[i])), (string[]));
                address[] memory list = new address[](names.length);
                for (uint256 j = 0; j < names.length; j++) {
                    list[j] = who(names[j]);
                    remember(lptNodes, names[j]);
                }
                scores.setMembers(vm.parseUint(rounds[i]), list);
            }
        }
        steps(id);
    }

    // --- after every step -------------------------------------------------

    function holdsEverything(string memory where) internal view virtual override {
        super.holdsEverything(where);
        holdsStake(where);
        holdsPots(where);
        holdsIssuance(where);
    }

    /// StakeLedger's LPT is every bond staying, in transfer or arriving,
    /// plus the unbonded LPT not withdrawn.
    function holdsStake(string memory where) internal view {
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
    }

    /// Claims' LPT is the pots of the claims made and of the validator
    /// shares minted, less what withdrawals of each kind moved out. For each
    /// claim and each (round, member) validator pot, the parts marked
    /// withdrawn never exceed it, and those of each kind together are what
    /// that kind's withdrawals moved.
    function holdsPots(string memory where) internal view {
        uint256 pots = 0;
        uint256 parts = 0;
        for (uint256 i = 0; i < claimedNode.length; i++) {
            (,, uint256 pot) = claims.entitlementOf(claimedNode[i], claimedRound[i]);
            pots += pot;
            uint256 taken = 0;
            for (uint256 j = 0; j < holders.length; j++) {
                (uint256 amount, bool withdrawn) = claims.partOf(who(holders[j]), claimedNode[i], claimedRound[i]);
                if (withdrawn) taken += amount;
            }
            require(
                taken <= pot,
                string.concat(where, ": parts withdrawn above the pot of round ", vm.toString(claimedRound[i]))
            );
            parts += taken;
        }
        expectEq(parts, withdrawnTotal, string.concat(where, ": parts marked withdrawn, against LPT moved"));
        uint256 vpots = 0;
        uint256 vparts = 0;
        for (uint256 i = 0; i < paidRound.length; i++) {
            (,, uint256 pot) = claims.validatorPayOf(paidMember[i], paidRound[i]);
            vpots += pot;
            uint256 taken = 0;
            for (uint256 j = 0; j < holders.length; j++) {
                (uint256 amount, bool withdrawn) = claims.validatorPartOf(who(holders[j]), paidMember[i], paidRound[i]);
                if (withdrawn) taken += amount;
            }
            require(
                taken <= pot,
                string.concat(where, ": validator parts withdrawn above the pot of round ", vm.toString(paidRound[i]))
            );
            vparts += taken;
        }
        expectEq(
            vparts,
            validatorWithdrawnTotal,
            string.concat(where, ": validator parts marked withdrawn, against LPT moved")
        );
        expectEq(
            lpt.balanceOf(address(claims)),
            pots + vpots - withdrawnTotal - validatorWithdrawnTotal,
            string.concat(where, ": Claims LPT")
        );
    }

    /// Each earning round's issuance is at most its ceiling, and the supply
    /// is the genesis supply plus every round's issuance.
    function holdsIssuance(string memory where) internal view {
        uint256 all = 0;
        for (uint256 i = 0; i < mintedRound.length; i++) {
            uint256 c = ceilingOf(mintedRound[i]);
            require(
                mintedAmount[i] <= c,
                string.concat(where, ": minted for round ", vm.toString(mintedRound[i]), " above its ceiling")
            );
            all += mintedAmount[i];
        }
        expectEq(lpt.totalSupply(), GENESIS + all, string.concat(where, ": supply"));
    }

    /// The ceiling of earning round R: C at R's position under the schedule
    /// Parameters holds for R (R-05, R-25), R - 1 until a change. From the
    /// examples' ceiling case for that position; a position no case covers
    /// reads RewardMath, which contracts/check holds to R-05's exact
    /// inequality for every residue and to the calculator for the 420
    /// half-life.
    function ceilingOf(uint256 round) internal view returns (uint256) {
        uint256[] memory sch = params.valuesAt(3, round);
        uint256 pos = RewardMath.position(sch[0], sch[1], sch[2], round);
        for (uint256 i = 0; i < ceilingAt.length; i++) {
            if (ceilingAt[i] == pos) return ceilingWei[i];
        }
        return RewardMath.ceiling(pos);
    }

    function loadCeilings() internal {
        delete ceilingAt;
        delete ceilingWei;
        string[] memory kinds = abi.decode(vm.parseJson(vectors, ".cases[*].kind"), (string[]));
        for (uint256 i = 0; i < kinds.length; i++) {
            if (!eq(kinds[i], "ceiling")) continue;
            string memory c = string.concat(".cases[", vm.toString(i), "]");
            if (!vm.keyExistsJson(vectors, string.concat(c, ".expected.ceiling_wei"))) continue;
            ceilingAt.push(num(c, ".inputs.round"));
            ceilingWei.push(num(c, ".expected.ceiling_wei"));
        }
    }

    // --- after the last step ---------------------------------------------

    function checkExpected() internal view virtual override {
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

        string[] memory rounds = vm.parseJsonKeys(vectors, string.concat(x, ".shares_wei"));
        expectEq(sharesRound.length, rounds.length, "rounds whose shares were minted");
        for (uint256 i = 0; i < rounds.length; i++) {
            uint256 r = vm.parseUint(rounds[i]);
            uint256 k = indexOf(sharesRound, r, string.concat("shares of round ", rounds[i], " not minted"));
            string memory p = string.concat(".shares_wei.", rounds[i]);
            expectEq(sharesValidators[k], num(x, string.concat(p, ".validators_wei")), string.concat(p, " validators"));
            expectEq(sharesTreasury[k], num(x, string.concat(p, ".treasury_wei")), string.concat(p, " treasury"));
        }

        rounds = vm.parseJsonKeys(vectors, string.concat(x, ".minted_for_round_wei"));
        expectEq(mintedRound.length, rounds.length, "rounds LPT was minted for");
        for (uint256 i = 0; i < rounds.length; i++) {
            uint256 k = indexOf(mintedRound, vm.parseUint(rounds[i]), string.concat("nothing minted for ", rounds[i]));
            expectEq(
                mintedAmount[k],
                num(x, string.concat(".minted_for_round_wei.", rounds[i])),
                string.concat("minted for round ", rounds[i])
            );
        }
    }

    function indexOf(uint256[] storage list, uint256 value, string memory missing) internal view returns (uint256) {
        for (uint256 i = 0; i < list.length; i++) {
            if (list[i] == value) return i;
        }
        revert(missing);
    }

    /// The case's names for the contracts and the treasury.
    function lptAddress(string memory name) internal view returns (address) {
        if (eq(name, "StakeLedger")) return address(ledger);
        if (eq(name, "Claims")) return address(claims);
        if (eq(name, "Treasury")) return treasuryAddress();
        return who(name);
    }

    /// A list of strings, read one by one, as listField does.
    function strList(string memory s, string memory field) internal view returns (string[] memory out) {
        string memory at = string.concat(s, field);
        uint256 n = 0;
        while (vm.keyExistsJson(vectors, string.concat(at, "[", vm.toString(n), "]"))) n++;
        out = new string[](n);
        for (uint256 i = 0; i < n; i++) {
            out[i] = vm.parseJsonString(vectors, string.concat(at, "[", vm.toString(i), "]"));
        }
    }

    /// A list of decimal strings. Read one by one, as listField does.
    function numList(string memory s, string memory field) internal view returns (uint256[] memory out) {
        string memory at = string.concat(s, field);
        uint256 n = 0;
        while (vm.keyExistsJson(vectors, string.concat(at, "[", vm.toString(n), "]"))) n++;
        out = new uint256[](n);
        for (uint256 i = 0; i < n; i++) {
            out[i] = vm.parseUint(vm.parseJsonString(vectors, string.concat(at, "[", vm.toString(i), "]")));
        }
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
        delete claimedNode;
        delete claimedRound;
        delete mintedRound;
        delete mintedAmount;
        delete sharesRound;
        delete sharesValidators;
        delete sharesTreasury;
        delete paidRound;
        delete paidMember;
        delete sourceNames;
        delete sources;
        withdrawnTotal = 0;
        validatorWithdrawnTotal = 0;
    }
}
