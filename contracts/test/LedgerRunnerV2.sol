// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {vm, Vm} from "./Vm.sol";
import {LedgerRunner} from "./LedgerRunner.sol";
import {Stage3} from "./Stage3.sol";
import {StakeLedger} from "../src/money/StakeLedger.sol";
import {Claims} from "../src/money/Claims.sol";
import {IClaims} from "../interfaces/IClaims.sol";

/// Runs a v2 `ledger` case on a stage 3 deployment: v1's steps, the LPT
/// steps of rulebook v2 and the claim, withdrawal and share steps. After
/// every step it holds the contracts to the shared examples' after-step
/// checks, each figure seen by the test from balances and the supply, not
/// taken from Claims' own books alone:
///
/// - StakeLedger's LPT is every bond staying, in transfer or arriving, plus
///   the unbonded LPT not withdrawn;
/// - Claims' LPT is the pots of the claims made less the parts withdrawn,
///   and no claim's withdrawn parts exceed its pot;
/// - the LPT minted for each earning round R is at most C(R - 1), read
///   from the examples' ceiling cases;
/// - the supply is the genesis supply plus what was minted for each round.
///
/// Only a claim or a share mint may change the supply, and a withdrawal
/// moves LPT out of Claims to the holder by transfer, never by a mint.
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
    /// LPT that withdrawals moved out of Claims, as the holders' balances rose.
    uint256 internal withdrawnTotal;

    // --- the steps ------------------------------------------------------

    function act(string memory s, string memory action) internal virtual override returns (bytes memory refused) {
        if (eq(action, "claim")) return claimStep(s);
        if (eq(action, "withdraw_parts")) return withdrawStep(s);
        if (eq(action, "mint_shares")) return mintSharesStep(s);
        if (eq(action, "withdraw_validator_parts")) revert("withdraw_validator_parts needs S-06 phase 4");
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
        uint256 r = num(s, ".earned_round");
        scores.set(n, num(s, ".score"));
        address payout = registry.payoutOf(n);
        uint256 supply = lpt.totalSupply();
        uint256 held = lpt.balanceOf(address(claims));
        uint256 paidBefore = lpt.balanceOf(payout);
        refused = as_(who(str(s, ".caller")), address(claims), abi.encodeCall(Claims.claim, (n, r)));
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
        address h = holder(s);
        address n = node(s, ".node");
        uint256[] memory rounds = numList(s, ".rounds");
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

    /// `caller` mints `earned_round`'s validator and treasury amounts. With
    /// no members (S-06's phase 4 adds them) both go to the treasury.
    function mintSharesStep(string memory s) internal returns (bytes memory refused) {
        string memory c = str(s, ".caller");
        uint256 r = num(s, ".earned_round");
        uint256 supply = lpt.totalSupply();
        uint256 kept = lpt.balanceOf(treasuryAddress());
        vm.recordLogs();
        refused = as_(who(c), address(claims), abi.encodeCall(Claims.mintRoundShares, (r)));
        stepGas.push(vm.lastFrameGas().gasTotalUsed);
        Vm.Log[] memory logs = vm.getRecordedLogs();
        if (refused.length > 0) {
            expectEq(lpt.totalSupply(), supply, "a refused share mint minted");
            return refused;
        }
        require(claims.sharesMinted(r), "shares minted but not recorded");
        uint256 v;
        uint256 t;
        uint256 seen = 0;
        for (uint256 i = 0; i < logs.length; i++) {
            if (logs[i].emitter == address(claims) && logs[i].topics[0] == IClaims.RoundSharesMinted.selector) {
                expectEq(uint256(logs[i].topics[1]), r, "RoundSharesMinted round");
                (v, t) = abi.decode(logs[i].data, (uint256, uint256));
                seen++;
            }
        }
        expectEq(seen, 1, "RoundSharesMinted events");
        expectEq(lpt.totalSupply() - supply, v + t, "shares: minted");
        expectEq(lpt.balanceOf(treasuryAddress()) - kept, v + t, "shares: to the treasury");
        sharesRound.push(r);
        sharesValidators.push(v);
        sharesTreasury.push(t);
        addMinted(r, v + t);
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

    /// A case that names validator members needs S-06's phase 4.
    function stepsV2(string memory id) internal {
        string memory at = string.concat(".cases[", vm.toString(caseIndex(id)), "].inputs.members");
        require(!vm.keyExistsJson(vectors, at), string.concat(id, ": members need S-06 phase 4"));
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

    /// Claims' LPT is the pots of the claims made less what withdrawals
    /// moved out. For each claim, the parts marked withdrawn never exceed its
    /// pot, and all of them together are what the withdrawals moved.
    function holdsPots(string memory where) internal view {
        uint256 pots = 0;
        uint256 parts = 0;
        for (uint256 i = 0; i < claimedNode.length; i++) {
            (, , uint256 pot) = claims.entitlementOf(claimedNode[i], claimedRound[i]);
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
        expectEq(lpt.balanceOf(address(claims)), pots - withdrawnTotal, string.concat(where, ": Claims LPT"));
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

    /// C(R - 1) for earning round R, from the examples' ceiling case for
    /// emission index R - 1. A round with none fails the test.
    function ceilingOf(uint256 round) internal view returns (uint256) {
        string[] memory kinds = abi.decode(vm.parseJson(vectors, ".cases[*].kind"), (string[]));
        for (uint256 i = 0; i < kinds.length; i++) {
            if (!eq(kinds[i], "ceiling")) continue;
            string memory c = string.concat(".cases[", vm.toString(i), "]");
            if (num(c, ".inputs.round") + 1 != round) continue;
            if (!vm.keyExistsJson(vectors, string.concat(c, ".expected.ceiling_wei"))) continue;
            return num(c, ".expected.ceiling_wei");
        }
        revert(string.concat("no ceiling case for earning round ", vm.toString(round)));
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
        withdrawnTotal = 0;
    }
}
