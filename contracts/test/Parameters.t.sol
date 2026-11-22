// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {vm} from "./Vm.sol";
import {Stage4, LyingPool} from "./Stage4.sol";
import {ScoreStub} from "./Stage3.sol";
import {Parameters} from "../src/authority/Parameters.sol";
import {TestPool} from "../src/money/TestPool.sol";
import {MockUSDC} from "../src/money/MockUSDC.sol";
import {RewardMath} from "../src/math/RewardMath.sol";
import {IParameters} from "../interfaces/IParameters.sol";

/// TestPool with one more function, which delegates: what S-10 3.1's walk of
/// the pool's runtime must refuse.
contract DelegatingPool is TestPool {
    constructor(address a, address b) TestPool(a, b) {}

    function run(address target) external returns (bool ok) {
        (ok,) = target.delegatecall("");
    }
}

/// TestPool with one more function, which destroys it.
contract DestroyingPool is TestPool {
    constructor(address a, address b) TestPool(a, b) {}

    function end() external {
        selfdestruct(payable(msg.sender));
    }
}

/// Parameters (rulebook v4, R-25): every `parameters` case of the shared
/// examples, step by step, on a Parameters built for the case beside the
/// deployment the demo makes, with its pool and tokens. Every input and
/// expected number is read from vectors/october-main-text-v4.json.
///
/// After every step, the values of every round that has started are the
/// values that round started with, for every group: the values of a round,
/// never the values now. An observed schedule is held to the case's
/// ceiling at that round's position, exactly.
///
/// test_half_life_420_residues prints the ceiling of every round from 100
/// to 519 under the half-life of 420 the case parameters-half-life
/// announces; contracts/check holds each to R-05's exact inequality and to
/// the calculator.
contract ParametersTest is Stage4 {
    event log_named_uint(string key, uint256 val);
    event log_string(string val);

    uint256 private constant GROUPS = 9;

    Parameters internal p;
    /// The case's names for addresses: the deployment's score source and
    /// pool, and every address its `addresses` gives.
    string[] internal names;
    address[] internal addrs;

    string internal at;
    string[] internal announcedSteps;
    uint256[] internal announcedFrom;
    string[] internal refusedSteps;
    string[] internal refusedCodes;
    string[] internal observedSteps;
    bytes[] internal observedValues;
    /// The observation's ceiling, or 2^256 - 1 when it has none.
    uint256[] internal observedCeiling;
    uint256[] internal observedFrom;
    /// keccak of valuesAt(group, round) when round had started, by group
    /// and round: 0 until then.
    mapping(uint256 => mapping(uint256 => bytes32)) internal started;

    function setUp() external {
        loadVectors("october-main-text-v4");
    }

    // --- the shared examples ----------------------------------------------

    function test_parameters_emission() external {
        parametersCase("parameters-emission");
    }

    function test_parameters_half_life() external {
        parametersCase("parameters-half-life");
    }

    function test_parameters_drop() external {
        parametersCase("parameters-drop");
    }

    function test_parameters_drop_840() external {
        parametersCase("parameters-drop-840");
    }

    function test_parameters_schedule_replaced() external {
        parametersCase("parameters-schedule-replaced");
    }

    function test_parameters_buyback() external {
        parametersCase("parameters-buyback");
    }

    function test_parameters_delays() external {
        parametersCase("parameters-delays");
    }

    function test_parameters_score_source() external {
        parametersCase("parameters-score-source");
    }

    function test_parameters_set_size() external {
        parametersCase("parameters-set-size");
    }

    function test_parameters_refusal_order() external {
        parametersCase("parameters-refusal-order");
    }

    /// Each case of kind `parameters` in the examples has a test above.
    function test_every_parameters_case_is_run() external view {
        string[10] memory run = [
            "parameters-emission",
            "parameters-half-life",
            "parameters-drop",
            "parameters-drop-840",
            "parameters-schedule-replaced",
            "parameters-buyback",
            "parameters-delays",
            "parameters-score-source",
            "parameters-set-size",
            "parameters-refusal-order"
        ];
        string[] memory ids = abi.decode(vm.parseJson(vectors, ".cases[*].id"), (string[]));
        string[] memory kinds = abi.decode(vm.parseJson(vectors, ".cases[*].kind"), (string[]));
        uint256 seen;
        for (uint256 i = 0; i < ids.length; i++) {
            if (!eq(kinds[i], "parameters")) continue;
            bool found;
            for (uint256 j = 0; j < run.length; j++) {
                found = found || eq(run[j], ids[i]);
            }
            require(found, string.concat("parameters case not run: ", ids[i]));
            seen++;
        }
        require(seen == run.length, "a case run here is not in the examples");
    }

    /// A case's world: the deployment the demo makes, then a Parameters
    /// with the authority the case names, the stub "Scores" as its score
    /// source and the deployment's pool "Pool", pinned to that pool's code.
    /// Each name under `addresses` is made as the case describes it.
    function world() internal {
        deployStage4(1_000_000_000e18, 0);
        ScoreStub s = new ScoreStub();
        name("Scores", address(s));
        name("Pool", address(pool));
        name("MockUSDC", address(usdc));
        name("TestLPT", address(lpt));
        name("OtherToken", address(new MockUSDC()));
        p = new Parameters(
            who(str(at, ".inputs.authority")),
            clock,
            address(s),
            address(pool),
            address(pool).codehash,
            address(usdc),
            address(lpt)
        );
        string memory a = string.concat(at, ".inputs.addresses");
        if (!vm.keyExistsJson(vectors, a)) return;
        string[] memory ns = vm.parseJsonKeys(vectors, a);
        for (uint256 i = 0; i < ns.length; i++) {
            string memory e = string.concat(a, ".", ns[i]);
            if (!vm.parseJsonBool(vectors, string.concat(e, ".code"))) {
                name(ns[i], who(ns[i]));
            } else if (!vm.keyExistsJson(vectors, string.concat(e, ".pinned_pool_code"))) {
                name(ns[i], address(new ScoreStub()));
            } else {
                address t0 = addressOf(str(e, ".token0"));
                address t1 = addressOf(str(e, ".token1"));
                // A pool with the pinned code is a TestPool; one without is
                // a pool of other code that reports the tokens it is given.
                address made = vm.parseJsonBool(vectors, string.concat(e, ".pinned_pool_code"))
                    ? address(new TestPool(t0, t1))
                    : address(new LyingPool(t0, t1));
                name(ns[i], made);
            }
        }
    }

    function parametersCase(string memory id) internal {
        at = string.concat(".cases[", vm.toString(caseIndex(id)), "]");
        world();
        for (uint256 i = 0;; i++) {
            string memory s = string.concat(at, ".inputs.steps[", vm.toString(i), "]");
            if (!vm.keyExistsJson(vectors, s)) break;
            this.runStep(s, string.concat(id, " step ", str(s, ".step")));
        }
        checkExpected(id);
    }

    /// One step, then the holding check, in a call frame of its own.
    function runStep(string memory s, string memory where) external {
        require(msg.sender == address(this), "the runner only");
        toRound(vm.parseUint(str(s, ".round")));
        holdsStarted(where);
        step(s);
        holdsStarted(where);
    }

    function step(string memory s) internal {
        string memory action = str(s, ".action");
        address caller = vm.keyExistsJson(vectors, string.concat(s, ".caller")) ? who(str(s, ".caller")) : address(0);
        uint256 group = vm.parseUint(str(s, ".group"));
        if (eq(action, "announce")) {
            string[] memory raw = strings(string.concat(s, ".values"));
            uint256[] memory values = new uint256[](raw.length);
            for (uint256 i = 0; i < raw.length; i++) {
                values[i] = valueOf(raw[i]);
            }
            vm.prank(caller);
            (bool ok, bytes memory ret) = address(p).call(abi.encodeCall(IParameters.announce, (group, values)));
            if (ok) {
                announcedSteps.push(str(s, ".step"));
                announcedFrom.push(abi.decode(ret, (uint256)));
            } else {
                refused(s, ret);
            }
        } else if (eq(action, "withdraw")) {
            vm.prank(caller);
            (bool ok, bytes memory ret) = address(p).call(abi.encodeCall(IParameters.withdraw, (group)));
            if (!ok) refused(s, ret);
        } else if (eq(action, "observe")) {
            uint256 r = vm.parseUint(str(s, ".at_round"));
            uint256[] memory v = p.valuesAt(group, r);
            observedSteps.push(str(s, ".step"));
            observedValues.push(abi.encode(v));
            observedCeiling.push(
                group == 3 ? RewardMath.ceiling(RewardMath.position(v[0], v[1], v[2], r)) : type(uint256).max
            );
            observedFrom.push(0);
        } else if (eq(action, "observe_pending")) {
            (uint256[] memory v, uint256 from) = p.pending(group);
            observedSteps.push(str(s, ".step"));
            observedValues.push(abi.encode(v));
            observedCeiling.push(type(uint256).max);
            observedFrom.push(from);
        } else {
            revert(string.concat("unknown action ", action));
        }
    }

    function refused(string memory s, bytes memory ret) internal {
        require(ret.length > 0, "reverted with no data");
        refusedSteps.push(str(s, ".step"));
        refusedCodes.push(code(ret));
    }

    /// Every round that has started keeps the values it started with, in
    /// every group: a round is first recorded once it is current.
    function holdsStarted(string memory where) internal {
        uint256 now_ = clock.currentRound();
        for (uint256 g = 1; g <= GROUPS; g++) {
            for (uint256 r = 0; r <= now_; r++) {
                bytes32 h = keccak256(abi.encode(p.valuesAt(g, r)));
                if (started[g][r] == 0) started[g][r] = h;
                require(
                    started[g][r] == h,
                    string.concat(where, ": group ", vm.toString(g), " changed in started round ", vm.toString(r))
                );
            }
        }
    }

    function checkExpected(string memory id) internal view {
        string memory x = string.concat(at, ".expected");

        uint256 n = count(string.concat(x, ".announced"));
        expectEq(announcedSteps.length, n, string.concat(id, ": announcements"));
        for (uint256 i = 0; i < n; i++) {
            string memory e = string.concat(x, ".announced[", vm.toString(i), "]");
            require(eq(announcedSteps[i], str(e, ".step")), string.concat(id, ": announced step ", str(e, ".step")));
            expectEq(
                announcedFrom[i],
                vm.parseUint(str(e, ".counts_from")),
                string.concat(id, " step ", str(e, ".step"), ": counts from")
            );
        }

        n = count(string.concat(x, ".refused"));
        expectEq(refusedSteps.length, n, string.concat(id, ": refused steps"));
        for (uint256 i = 0; i < n; i++) {
            string memory e = string.concat(x, ".refused[", vm.toString(i), "]");
            require(
                eq(refusedSteps[i], str(e, ".step")) && eq(refusedCodes[i], str(e, ".code")),
                string.concat(
                    id,
                    ": refusal ",
                    vm.toString(i),
                    ": got step ",
                    refusedSteps[i],
                    " ",
                    refusedCodes[i],
                    ", want step ",
                    str(e, ".step"),
                    " ",
                    str(e, ".code")
                )
            );
        }

        n = count(string.concat(x, ".observed"));
        expectEq(observedSteps.length, n, string.concat(id, ": observed steps"));
        for (uint256 i = 0; i < n; i++) {
            string memory e = string.concat(x, ".observed[", vm.toString(i), "]");
            string memory where = string.concat(id, " step ", str(e, ".step"));
            require(eq(observedSteps[i], str(e, ".step")), string.concat(where, ": observed out of order"));
            uint256[] memory got = abi.decode(observedValues[i], (uint256[]));
            string[] memory want = strings(string.concat(e, ".values"));
            expectEq(got.length, want.length, string.concat(where, ": values"));
            for (uint256 j = 0; j < want.length; j++) {
                expectEq(got[j], valueOf(want[j]), string.concat(where, ": value ", vm.toString(j)));
            }
            if (vm.keyExistsJson(vectors, string.concat(e, ".ceiling_wei"))) {
                expectEq(observedCeiling[i], vm.parseUint(str(e, ".ceiling_wei")), string.concat(where, ": ceiling"));
            }
            if (vm.keyExistsJson(vectors, string.concat(e, ".counts_from"))) {
                expectEq(observedFrom[i], vm.parseUint(str(e, ".counts_from")), string.concat(where, ": counts from"));
            }
        }
    }

    // --- the pool check (R-16 rule 13, at the announce) --------------------

    /// A pool with the pinned code must hold this deployment's two tokens,
    /// in either order; one of other code is refused whatever it holds. A
    /// Parameters pinned to a pool code that reports any tokens it is given
    /// shows the token check on its own.
    function test_the_pool_check_reads_the_tokens() external {
        deployStage4(1_000_000_000e18, 0);
        LyingPool ours = new LyingPool(address(usdc), address(lpt));
        LyingPool other = new LyingPool(address(usdc), address(new MockUSDC()));
        LyingPool same = new LyingPool(address(usdc), address(usdc));
        Parameters q = new Parameters(
            who("G"), clock, address(0), address(ours), address(ours).codehash, address(usdc), address(lpt)
        );
        require(address(other).codehash == address(ours).codehash, "one code");
        expectEq(announceOn(q, address(ours)).length, 0, "the deployment's two tokens");
        expectPoolInvalid(announceOn(q, address(other)), address(other), "another token");
        expectPoolInvalid(announceOn(q, address(same)), address(same), "USDC twice");
        expectPoolInvalid(announceOn(q, who("Nobody")), who("Nobody"), "no code");
        expectPoolInvalid(announceOn(q, address(pool)), address(pool), "TestPool, not the pinned code");
    }

    function announceOn(Parameters q, address poolAddress) internal returns (bytes memory) {
        uint256[] memory v = new uint256[](4);
        (v[0], v[1], v[2], v[3]) = (uint256(uint160(poolAddress)), 3600, 10_000, 1);
        vm.prank(who("G"));
        (bool ok, bytes memory ret) = address(q).call(abi.encodeCall(IParameters.announce, (9, v)));
        return ok ? bytes("") : ret;
    }

    function expectPoolInvalid(bytes memory ret, address poolAddress, string memory what) internal pure {
        require(
            keccak256(ret) == keccak256(abi.encodeWithSelector(IParameters.POOL_INVALID.selector, poolAddress)),
            string.concat(what, ": not POOL_INVALID(pool)")
        );
    }

    // --- counts and words the shared examples do not reach ----------------

    /// A Parameters of this deployment, with a stub score source and the
    /// deployment's pool, pinned to that pool's code.
    function freshParameters() internal returns (Parameters q, address scores) {
        deployStage4(1_000_000_000e18, 0);
        scores = address(new ScoreStub());
        q = new Parameters(who("G"), clock, scores, address(pool), address(pool).codehash, address(usdc), address(lpt));
    }

    function announceAs(Parameters q, uint256 group, uint256[] memory v) internal returns (string memory) {
        vm.prank(who("G"));
        (bool ok, bytes memory ret) = address(q).call(abi.encodeCall(IParameters.announce, (group, v)));
        return ok ? "" : code(ret);
    }

    function list(uint256 a, uint256 b, uint256 c, uint256 d, uint256 n) internal pure returns (uint256[] memory v) {
        uint256[4] memory all = [a, b, c, d];
        v = new uint256[](n);
        for (uint256 i = 0; i < n; i++) {
            v[i] = all[i];
        }
    }

    function expectCode(string memory got, string memory want, string memory what) internal pure {
        require(
            eq(got, want), string.concat(what, ": got ", bytes(got).length == 0 ? "accepted" : got, ", want ", want)
        );
    }

    /// The emission takes three values and the schedule two: a list of
    /// another length is VALUE_OUT_OF_RANGE, even one whose shares sum to
    /// the whole, and the schedule's before any raise is weighed.
    function test_a_wrong_count_of_values_is_out_of_range() external {
        (Parameters q,) = freshParameters();
        expectCode(announceAs(q, 2, list(900_000, 10_000, 50_000, 40_000, 4)), "VALUE_OUT_OF_RANGE", "emission, 4");
        expectCode(announceAs(q, 2, list(990_000, 10_000, 0, 0, 2)), "VALUE_OUT_OF_RANGE", "emission, 2");
        expectCode(announceAs(q, 3, list(0, 0, 0, 0, 1)), "VALUE_OUT_OF_RANGE", "schedule, 1");
        expectCode(announceAs(q, 3, list(0, 420, 1, 0, 3)), "VALUE_OUT_OF_RANGE", "schedule, 3");
        expectCode(announceAs(q, 3, list(0, 420, 0, 0, 2)), "", "schedule, 2");
        expectCode(announceAs(q, 2, list(900_000, 50_000, 50_000, 0, 3)), "", "emission, 3");
    }

    /// An address is a word of 160 bits: one above, whose low 160 bits name
    /// a score source or the pinned pool, is VALUE_OUT_OF_RANGE, not that
    /// address.
    function test_an_address_word_above_160_bits_is_out_of_range() external {
        (Parameters q, address scores) = freshParameters();
        uint256 high = 1 << 160;
        expectCode(announceAs(q, 6, list(high | uint160(scores), 0, 0, 0, 1)), "VALUE_OUT_OF_RANGE", "source");
        expectCode(
            announceAs(q, 9, list(high | uint160(address(pool)), 3600, 10_000, 1, 4)), "VALUE_OUT_OF_RANGE", "pool"
        );
        expectCode(announceAs(q, 6, list(uint160(scores), 0, 0, 0, 1)), "", "the source's own word");
        expectCode(announceAs(q, 9, list(uint160(address(pool)), 3600, 10_000, 1, 4)), "", "the pool's own word");
    }

    /// A number with no group is NOT_ADJUSTABLE in each view too.
    function test_the_views_refuse_a_number_with_no_group() external {
        (Parameters q,) = freshParameters();
        uint256[2] memory none = [uint256(0), GROUPS + 1];
        for (uint256 i = 0; i < 2; i++) {
            (bool ok, bytes memory ret) = address(q).call(abi.encodeCall(IParameters.valuesAt, (none[i], 1)));
            expectCode(ok ? "" : code(ret), "NOT_ADJUSTABLE", string.concat("valuesAt ", vm.toString(none[i])));
            (ok, ret) = address(q).call(abi.encodeCall(IParameters.pending, (none[i])));
            expectCode(ok ? "" : code(ret), "NOT_ADJUSTABLE", string.concat("pending ", vm.toString(none[i])));
        }
    }

    /// A change is pending until the round it counts from starts; in that
    /// round pending shows none, and valuesAt the change.
    function test_pending_ends_in_the_round_the_change_counts_from() external {
        (Parameters q,) = freshParameters();
        uint256 r = clock.currentRound();
        expectCode(announceAs(q, 4, list(9, 0, 0, 0, 1)), "", "reward delay 9");
        toRound(r + 6);
        (uint256[] memory v, uint256 from) = q.pending(4);
        expectEq(from, r + 7, "pending the round before");
        expectEq(v.length == 1 ? v[0] : 0, 9, "the pending value");
        toRound(r + 7);
        (v, from) = q.pending(4);
        expectEq(from, 0, "pending in the round it counts from");
        expectEq(v.length, 0, "values pending in the round it counts from");
        expectEq(q.valuesAt(4, r + 7)[0], 9, "in force from that round");
    }

    // --- the pool's runtime (S-10 3.1) -----------------------------------------

    /// A pool that passes the check at the announce still passes at use only
    /// if nothing can change or remove its code: TestPool is deployed
    /// directly, and its runtime has no SELFDESTRUCT and no DELEGATECALL.
    /// The walk reads the deployed pool's code instruction by instruction.
    /// It is not vacuous: the same code holds 0xff bytes, as push data or
    /// in the metadata, which a scan of every byte would take for
    /// SELFDESTRUCT.
    function test_the_pool_runtime_has_no_selfdestruct_or_delegatecall() external {
        deployStage4(1_000_000_000e18, 0);
        bytes memory code = address(pool).code;
        require(code.length > 0, "no pool code");
        (bool found, uint256 at_) = forbiddenAt(code);
        require(
            !found,
            string.concat(
                "TestPool's runtime: opcode ", vm.toString(abi.encodePacked(code[at_])), " at byte ", vm.toString(at_)
            )
        );
        require(bytesOf(code, 0xff) > 0, "the runtime has no 0xff byte at all: the push-data skip is not shown");
    }

    /// A TestPool with a function that delegates, and one with a function
    /// that destroys it: the walk finds the opcode in each compiled runtime.
    function test_the_opcode_walk_finds_either_opcode_in_a_broken_pool() external {
        deployStage4(1_000_000_000e18, 0);
        bytes memory d = address(new DelegatingPool(address(usdc), address(lpt))).code;
        (bool found, uint256 at_) = forbiddenAt(d);
        require(found && uint8(d[at_]) == 0xf4, "a pool that delegates: DELEGATECALL not found");
        bytes memory s = address(new DestroyingPool(address(usdc), address(lpt))).code;
        (found, at_) = forbiddenAt(s);
        require(found && uint8(s[at_]) == 0xff, "a pool that destroys itself: SELFDESTRUCT not found");
    }

    /// 0xff and 0xf4 as PUSH1, PUSH2 and PUSH32 data and in the metadata are
    /// not instructions; the same bytes as instructions are found, at their
    /// offset.
    function test_the_opcode_walk_skips_push_data_and_metadata() external pure {
        bytes memory meta = hex"a2fff4ff0004";
        bytes memory pushes = abi.encodePacked(
            hex"60ff60f461f4ff7f", hex"fff4fff4fff4fff4fff4fff4fff4fff4fff4fff4fff4fff4fff4fff4fff4fff4", hex"00"
        );
        (bool found,) = forbiddenAt(abi.encodePacked(pushes, meta));
        require(!found, "push data or metadata taken for an instruction");
        uint256 at_;
        (found, at_) = forbiddenAt(abi.encodePacked(pushes, hex"ff", meta));
        require(found && at_ == pushes.length, "SELFDESTRUCT after the push data not found");
        (found, at_) = forbiddenAt(abi.encodePacked(hex"6000f4", meta));
        require(found && at_ == 2, "DELEGATECALL after PUSH1 0 not found");
    }

    /// The first SELFDESTRUCT (0xff) or DELEGATECALL (0xf4) among `code`'s
    /// instructions, and its offset. The walk steps over each PUSH1 to
    /// PUSH32's data and stops where Solidity's trailing metadata starts:
    /// the last two bytes give its length, and it starts with a CBOR map.
    function forbiddenAt(bytes memory code) internal pure returns (bool found, uint256 at_) {
        uint256 n = code.length;
        require(n >= 2, "no metadata length");
        uint256 meta = (uint256(uint8(code[n - 2])) << 8) | uint8(code[n - 1]);
        require(meta + 2 <= n, "the metadata is longer than the code");
        uint256 end = n - 2 - meta;
        require(uint8(code[end]) & 0xe0 == 0xa0, "no CBOR map where the metadata starts");
        for (uint256 i = 0; i < end; i++) {
            uint8 op = uint8(code[i]);
            if (op == 0xff || op == 0xf4) return (true, i);
            if (op >= 0x60 && op <= 0x7f) i += op - 0x5f;
        }
    }

    function bytesOf(bytes memory code, uint8 b) internal pure returns (uint256 n) {
        for (uint256 i = 0; i < code.length; i++) {
            if (uint8(code[i]) == b) n++;
        }
    }

    // --- the hard core: the ceiling after a change, exact --------------------

    /// parameters-half-life's change: (0, 420) announced in round 94 counts
    /// from round 101. Prints "H420(R): values V, position P, ceiling C"
    /// for every round R from 100 to 519: V is the schedule Parameters holds
    /// for R, P is R's position under it, in 840ths of a halving, and C its
    /// ceiling. The positions run 99 to 937 by 2, so every odd residue
    /// below 840 is printed once. contracts/check holds each C to
    /// R-05's exact inequality at P, and every round to the calculator.
    function test_half_life_420_residues() external {
        at = string.concat(".cases[", vm.toString(caseIndex("parameters-half-life")), "]");
        world();
        toRound(94);
        uint256[] memory v = new uint256[](2);
        v[1] = 420;
        vm.prank(who("G"));
        expectEq(p.announce(3, v), 101, "counts from");
        for (uint256 r = 100; r < 520; r++) {
            uint256[] memory s = p.valuesAt(3, r);
            uint256 pos = RewardMath.position(s[0], s[1], s[2], r);
            expectEq(pos, r == 100 ? 99 : 101 + 2 * (r - 101), "the position");
            emit log_string(string.concat(
                    "H420(",
                    vm.toString(r),
                    "): values ",
                    string.concat(vm.toString(s[0]), "/", vm.toString(s[1]), "/", vm.toString(s[2])),
                    ", position ",
                    vm.toString(pos),
                    ", ceiling ",
                    vm.toString(RewardMath.ceiling(pos))
                ));
        }
    }

    // --- reading the examples ------------------------------------------------

    function name(string memory n, address a) internal {
        names.push(n);
        addrs.push(a);
    }

    function addressOf(string memory n) internal view returns (address) {
        for (uint256 i = 0; i < names.length; i++) {
            if (eq(names[i], n)) return addrs[i];
        }
        revert(string.concat("no address named ", n));
    }

    /// A decimal value, or the address the case gave a name.
    function valueOf(string memory raw) internal view returns (uint256) {
        bytes1 c = bytes(raw)[0];
        return c >= "0" && c <= "9" ? vm.parseUint(raw) : uint256(uint160(addressOf(raw)));
    }

    function strings(string memory key) internal view returns (string[] memory out) {
        out = new string[](count(key));
        for (uint256 i = 0; i < out.length; i++) {
            out[i] = vm.parseJsonString(vectors, string.concat(key, "[", vm.toString(i), "]"));
        }
    }

    function count(string memory key) internal view returns (uint256 n) {
        while (vm.keyExistsJson(vectors, string.concat(key, "[", vm.toString(n), "]"))) n++;
    }

    function str(string memory s, string memory field) internal view returns (string memory) {
        return vm.parseJsonString(vectors, string.concat(s, field));
    }
}
