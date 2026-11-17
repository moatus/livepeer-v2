// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {vm} from "./Vm.sol";
import {RewardMath} from "../src/math/RewardMath.sol";

/// Runs every `ceiling`, `round` and `split` case of
/// vectors/october-main-text-v2.json through RewardMath. Every input and
/// every expected number is read from that file.
///
/// test_ceiling_residues prints C(r) for every residue r below 840 and for
/// r = 840, 841 and 1,680, and the gas of the costliest; contracts/check
/// tests each against R-05's exact inequality.
contract RewardMathTest {
    /// Read by forge and printed in its output.
    event log_named_uint(string key, uint256 val);

    string private vectors;

    function setUp() external {
        vectors = vm.readFile(string.concat(vm.projectRoot(), "/../vectors/october-main-text-v2.json"));
    }

    function test_parameters_are_the_examples() external view {
        expectEq(RewardMath.EMISSION_BASE, param("emission_base_wei"), "EmissionBase");
        expectEq(RewardMath.HALF_LIFE, param("emission_half_life_rounds"), "EmissionHalfLife");
        expectEq(RewardMath.NODE_SHARE, param("node_emission_share_millionths"), "node share");
        expectEq(RewardMath.VALIDATOR_SHARE, param("validator_emission_share_millionths"), "validator share");
        expectEq(RewardMath.TREASURY_SHARE, param("treasury_emission_share_millionths"), "treasury share");
    }

    /// A ceiling case states C(r), or, where its id says shares, R-10's two
    /// amounts of C(r).
    function test_ceiling_cases() external view {
        uint256 ran = 0;
        string[] memory ids = caseIds();
        for (uint256 i = 0; i < ids.length; i++) {
            if (!isKind(i, "ceiling")) continue;
            string memory at = caseAt(i);
            uint256 c = RewardMath.ceiling(num(at, ".inputs.round"));
            if (has(at, ".expected.ceiling_wei")) {
                expectEq(c, num(at, ".expected.ceiling_wei"), ids[i]);
            } else {
                expectEq(RewardMath.validatorsOf(c), num(at, ".expected.validators_wei"), string.concat(ids[i], " validators"));
                expectEq(RewardMath.treasuryOf(c), num(at, ".expected.treasury_wei"), string.concat(ids[i], " treasury"));
            }
            ran++;
        }
        require(ran > 0, "no ceiling case");
    }

    /// A round case pays each node listed at its score from the round's
    /// ceiling, with the totals taken over the nodes listed; minted_wei,
    /// where given, is the rewards plus R-10's two amounts.
    function test_round_cases() external view {
        uint256 ran = 0;
        string[] memory ids = caseIds();
        for (uint256 i = 0; i < ids.length; i++) {
            if (!isKind(i, "round")) continue;
            string memory at = caseAt(i);
            uint256 c = RewardMath.ceiling(num(at, ".inputs.round"));
            string[] memory names = nodeNames(at);
            uint256 totalFee = 0;
            uint256 totalStake = 0;
            for (uint256 n = 0; n < names.length; n++) {
                totalFee += num(nodeAt(at, n), ".fees_micro_usdc");
                totalStake += num(nodeAt(at, n), ".stake_wei");
            }
            uint256 minted = RewardMath.validatorsOf(c) + RewardMath.treasuryOf(c);
            for (uint256 n = 0; n < names.length; n++) {
                string memory node = nodeAt(at, n);
                uint256 paid = RewardMath.nodeReward(
                    c,
                    num(node, ".fees_micro_usdc"),
                    totalFee,
                    num(node, ".stake_wei"),
                    totalStake,
                    num(node, ".score")
                );
                expectEq(paid, num(at, string.concat(".expected.paid_wei.", names[n])), string.concat(ids[i], " ", names[n]));
                minted += paid;
            }
            expectEq(RewardMath.validatorsOf(c), num(at, ".expected.validators_wei"), string.concat(ids[i], " validators"));
            expectEq(RewardMath.treasuryOf(c), num(at, ".expected.treasury_wei"), string.concat(ids[i], " treasury"));
            if (has(at, ".expected.minted_wei")) {
                expectEq(minted, num(at, ".expected.minted_wei"), string.concat(ids[i], " minted"));
            }
            ran++;
        }
        require(ran > 0, "no round case");
    }

    /// A split case: the cut to the payout, the pot to Claims, each holder's
    /// part by its bond over the node's stake, the sum of the bonds, and
    /// what the floors leave in Claims.
    function test_split_cases() external view {
        uint256 ran = 0;
        string[] memory ids = caseIds();
        for (uint256 i = 0; i < ids.length; i++) {
            if (!isKind(i, "split")) continue;
            string memory at = caseAt(i);
            uint256 p = num(at, ".inputs.paid_wei");
            uint256 cut = RewardMath.cutOf(p, num(at, ".inputs.cut_millionths"));
            uint256 pot = p - cut;
            expectEq(cut, num(at, ".expected.cut_wei"), string.concat(ids[i], " cut"));
            expectEq(pot, num(at, ".expected.pot_wei"), string.concat(ids[i], " pot"));
            string[] memory holders = vm.parseJsonKeys(vectors, string.concat(at, ".inputs.bonds_wei"));
            uint256 stake = 0;
            for (uint256 h = 0; h < holders.length; h++) {
                stake += num(at, string.concat(".inputs.bonds_wei.", holders[h]));
            }
            uint256 parts = 0;
            for (uint256 h = 0; h < holders.length; h++) {
                uint256 part = RewardMath.partOf(pot, num(at, string.concat(".inputs.bonds_wei.", holders[h])), stake);
                expectEq(part, num(at, string.concat(".expected.parts_wei.", holders[h])), string.concat(ids[i], " ", holders[h]));
                parts += part;
            }
            expectEq(pot - parts, num(at, ".expected.dust_wei"), string.concat(ids[i], " dust"));
            ran++;
        }
        require(ran > 0, "no split case");
    }

    /// Prints C(r) for r = 0 to 839 and r = 840, 841 and 1,680, as
    /// "C(r)" lines, then "gas max" for the costliest call. contracts/check
    /// runs this test and holds each value to R-05's exact inequality.
    function test_ceiling_residues() external {
        uint256 maxGas = 0;
        for (uint256 r = 0; r < RewardMath.HALF_LIFE; r++) {
            maxGas = printCeiling(r, maxGas);
        }
        maxGas = printCeiling(RewardMath.HALF_LIFE, maxGas);
        maxGas = printCeiling(RewardMath.HALF_LIFE + 1, maxGas);
        maxGas = printCeiling(2 * RewardMath.HALF_LIFE, maxGas);
        emit log_named_uint("gas max", maxGas);
    }

    /// C(r + 840k) = floor(C(r) / 2^k), R-05, and the ceiling only falls.
    function test_ceiling_halves_and_falls() external pure {
        uint256 last = RewardMath.ceiling(0);
        for (uint256 r = 1; r < 3 * RewardMath.HALF_LIFE; r++) {
            uint256 c = RewardMath.ceiling(r);
            require(c <= last, "the ceiling rose");
            last = c;
        }
        for (uint256 r = 0; r < RewardMath.HALF_LIFE; r += 7) {
            for (uint256 k = 1; k < 80; k += 13) {
                expectEq(RewardMath.ceiling(r + k * RewardMath.HALF_LIFE), RewardMath.ceiling(r) >> k, "C(r + 840k)");
            }
        }
        expectEq(RewardMath.ceiling(256 * RewardMath.HALF_LIFE), 0, "C after 256 half-lives");
        expectEq(RewardMath.ceiling(type(uint256).max), 0, "C at the largest r");
    }

    /// A node with no stake has no parts; a cut of 10^6 leaves no pot.
    function test_split_edges() external pure {
        expectEq(RewardMath.partOf(5, 0, 0), 0, "part of a node with no stake");
        expectEq(RewardMath.cutOf(7, RewardMath.ONE), 7, "whole cut");
        expectEq(RewardMath.cutOf(7, 0), 0, "no cut");
    }

    // --- helpers ---------------------------------------------------------

    function printCeiling(uint256 r, uint256 maxGas) private returns (uint256) {
        uint256 before = gasleft();
        uint256 c = RewardMath.ceiling(r);
        uint256 used = before - gasleft();
        emit log_named_uint(string.concat("C(", vm.toString(r), ")"), c);
        return used > maxGas ? used : maxGas;
    }

    function caseIds() private view returns (string[] memory) {
        return abi.decode(vm.parseJson(vectors, ".cases[*].id"), (string[]));
    }

    function caseAt(uint256 i) private pure returns (string memory) {
        return string.concat(".cases[", vm.toString(i), "]");
    }

    /// Read one by one: forge reads a one-element array as its element.
    function nodeNames(string memory at) private view returns (string[] memory names) {
        uint256 n = 0;
        while (vm.keyExistsJson(vectors, nodeAt(at, n))) n++;
        names = new string[](n);
        for (uint256 i = 0; i < n; i++) {
            names[i] = vm.parseJsonString(vectors, string.concat(nodeAt(at, i), ".node"));
        }
    }

    function nodeAt(string memory at, uint256 n) private pure returns (string memory) {
        return string.concat(at, ".inputs.nodes[", vm.toString(n), "]");
    }

    function isKind(uint256 i, string memory kind) private view returns (bool) {
        return eq(vm.parseJsonString(vectors, string.concat(caseAt(i), ".kind")), kind);
    }

    function has(string memory at, string memory field) private view returns (bool) {
        return vm.keyExistsJson(vectors, string.concat(at, field));
    }

    function num(string memory at, string memory field) private view returns (uint256) {
        return vm.parseUint(vm.parseJsonString(vectors, string.concat(at, field)));
    }

    function param(string memory name) private view returns (uint256) {
        return vm.parseUint(vm.parseJsonString(vectors, string.concat(".parameters.", name)));
    }

    function eq(string memory a, string memory b) private pure returns (bool) {
        return keccak256(bytes(a)) == keccak256(bytes(b));
    }

    function expectEq(uint256 got, uint256 want, string memory what) private pure {
        if (got != want) {
            revert(string.concat(what, ": got ", vm.toString(got), ", want ", vm.toString(want)));
        }
    }
}
