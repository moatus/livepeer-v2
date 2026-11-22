// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {vm, Vm} from "./Vm.sol";
import {Stage3} from "./Stage3.sol";
import {Scores} from "../src/scores/Scores.sol";
import {IScores} from "../interfaces/IScores.sol";
import {StakeLedger} from "../src/money/StakeLedger.sol";
import {IParameters} from "../interfaces/IParameters.sol";

/// Scores on a stage 3 deployment: the real Registry, RoundClock and
/// StakeLedger, with every stake made by real bonds and unbonds. Runs every
/// `score` and `validators` case of vectors/october-main-text-v4.json, every
/// input and expected number read from there.
///
/// test_every_score_and_validators_case_is_run fails on a case left unrun.
contract ScoresTest is Stage3 {
    event log_named_uint(string key, uint256 val);

    uint256 internal constant ONE = 1_000_000;

    Scores internal vset;

    // The case being run.
    string internal at;
    /// Added to every round of a case: 1 for a case that gives stake at the
    /// start of round 1, which no bond can give (a bond counts from the next
    /// round, and round 1 starts at deployment); else 0. Scores reads no
    /// round number but the current one, so a shift changes no outcome.
    uint256 internal shift;
    string[] internal names;
    string[] internal refusedSteps;
    string[] internal refusedCodes;
    string[] internal observedSteps;
    string[] internal observedMembers;
    uint256[] internal observedScores;
    /// membersAt(r) as it was when round r started, by r.
    mapping(uint256 => bytes32) internal startedWith;
    /// The next step of the case to run.
    uint256 internal nextStep;

    function setUp() external {
        loadVectors("october-main-text-v4");
    }

    function world() internal {
        deployStage3();
        vset = new Scores(registry, clock, ledger, params);
    }

    // --- the shared examples ----------------------------------------------

    function test_score_none() external {
        scoreCase("score-none");
    }

    function test_score_one_zero() external {
        scoreCase("score-one-zero");
    }

    function test_score_reduced() external {
        scoreCase("score-reduced");
    }

    function test_score_majority_zero() external {
        scoreCase("score-majority-zero");
    }

    function test_score_spread() external {
        scoreCase("score-spread");
    }

    function test_score_even() external {
        scoreCase("score-even");
    }

    function test_score_even_floor() external {
        scoreCase("score-even-floor");
    }

    function test_score_empty() external {
        scoreCase("score-empty");
    }

    function test_validators_seats() external {
        validatorsCase("validators-seats");
    }

    function test_validators_tie() external {
        validatorsCase("validators-tie");
    }

    function test_validators_zero_stake() external {
        validatorsCase("validators-zero-stake");
    }

    function test_validators_opt_out_vote() external {
        validatorsCase("validators-opt-out-vote");
    }

    function test_validators_return_in_round() external {
        validatorsCase("validators-return-in-round");
    }

    function test_validators_return_displaced() external {
        validatorsCase("validators-return-displaced");
    }

    function test_validators_return_seat_taken() external {
        validatorsCase("validators-return-seat-taken");
    }

    function test_validators_return_order() external {
        validatorsCase("validators-return-order");
    }

    function test_refusal_order_validators() external {
        validatorsCase("refusal-order-validators");
    }

    /// R-25 (S-10 phase 3): the set size only rises, and a seat call reads
    /// the size in force in the round it runs in (the spec's note N1).
    function test_validators_set_size() external {
        validatorsCase("validators-set-size");
    }

    /// The same rise, withdrawn before it counts, never counts.
    function test_validators_set_size_withdrawn() external {
        validatorsCase("validators-set-size-withdrawn");
    }

    /// R-26 (S-10 phase 4): after V1 rotates to K1, V1's own vote, opt-out
    /// and opt-in are refused and K1's count.
    function test_validators_rotation() external {
        validatorsCase("validators-rotation");
    }

    /// Every score and validators case is run here.
    function test_every_score_and_validators_case_is_run() external view {
        string[20] memory ran = [
            "score-none",
            "score-one-zero",
            "score-reduced",
            "score-majority-zero",
            "score-spread",
            "score-even",
            "score-even-floor",
            "score-empty",
            "validators-seats",
            "validators-tie",
            "validators-zero-stake",
            "validators-opt-out-vote",
            "validators-return-in-round",
            "validators-return-displaced",
            "validators-return-seat-taken",
            "validators-return-order",
            "refusal-order-validators",
            "validators-set-size",
            "validators-set-size-withdrawn",
            // Key rotation (S-10 phase 4).
            "validators-rotation"
        ];
        string[] memory ids = abi.decode(vm.parseJson(vectors, ".cases[*].id"), (string[]));
        string[] memory kinds = abi.decode(vm.parseJson(vectors, ".cases[*].kind"), (string[]));
        string memory missing;
        for (uint256 i = 0; i < ids.length; i++) {
            if (!eq(kinds[i], "score") && !eq(kinds[i], "validators")) continue;
            bool found = false;
            for (uint256 j = 0; j < ran.length; j++) {
                found = found || eq(ids[i], ran[j]);
            }
            if (!found) missing = string.concat(missing, " ", ids[i]);
        }
        require(bytes(missing).length == 0, string.concat("case not run:", missing));
    }

    /// A `score` case: its members, each with 1,000 LPT from round 2, opt in
    /// in round 2 and are the members of round 3; in round 3 each votes as
    /// the case says, and X's score is read.
    function scoreCase(string memory id) internal {
        world();
        at = string.concat(".cases[", vm.toString(caseIndex(id)), "]");
        string[] memory members = list(string.concat(at, ".inputs.members"));
        register("X");
        for (uint256 i = 0; i < members.length; i++) {
            register(members[i]);
            names.push(members[i]);
            setStake(members[i], 1_000e18);
        }
        toRound(2);
        for (uint256 i = 0; i < members.length; i++) {
            expectDone(as_(who(members[i]), address(vset), abi.encodeCall(Scores.optIn, (who(members[i])))), "opt in");
        }
        toRound(3);
        string[] memory voters = vm.parseJsonKeys(vectors, string.concat(at, ".inputs.votes"));
        for (uint256 i = 0; i < voters.length; i++) {
            uint256 s = num(at, string.concat(".inputs.votes.", voters[i]));
            expectDone(
                as_(who(voters[i]), address(vset), abi.encodeCall(Scores.vote, (who(voters[i]), who("X"), s))), "vote"
            );
        }
        require(eq(memberNames(vset.membersAt(3)), sorted(members)), string.concat(id, ": members"));
        expectEq(vset.scoreOf(who("X")), num(at, ".expected.score"), string.concat(id, ": score"));
    }

    /// A `validators` case: its stakes made by bonds and unbonds, each in the
    /// round before it counts, and its steps in order. After every step,
    /// every node's stake at the start of the round is the case's, and the
    /// members of the round are those it started with.
    function validatorsCase(string memory id) internal {
        world();
        at = string.concat(".cases[", vm.toString(caseIndex(id)), "]");
        uint256 nStakes = count(string.concat(at, ".inputs.stakes"));
        uint256 nSteps = count(string.concat(at, ".inputs.steps"));
        string[] memory unregistered = list(string.concat(at, ".inputs.unregistered"));
        if (has(at, ".inputs.authority")) {
            require(params.authority() == who(str(at, ".inputs.authority")), "the case's authority");
        }

        uint256 last = 1;
        for (uint256 i = 0; i < nStakes; i++) {
            uint256 f = num(stakesAt(i), ".from_round");
            if (f == 1) shift = 1;
            string[] memory ns = vm.parseJsonKeys(vectors, string.concat(stakesAt(i), ".stake_wei"));
            for (uint256 j = 0; j < ns.length; j++) {
                remember(ns[j]);
            }
        }
        for (uint256 i = 0; i < nSteps; i++) {
            string memory s = stepAt(i);
            if (has(s, ".node")) remember(str(s, ".node"));
            if (has(s, ".target")) remember(str(s, ".target"));
            uint256 r = num(s, ".round") + shift;
            if (r > last) last = r;
        }
        for (uint256 i = 0; i < names.length; i++) {
            bool skip = false;
            for (uint256 j = 0; j < unregistered.length; j++) {
                skip = skip || eq(names[i], unregistered[j]);
            }
            if (!skip) register(names[i]);
        }

        for (uint256 r = 1; r <= last; r++) {
            this.runRound(id, r, nStakes, nSteps);
        }
        expectEq(nextStep, nSteps, "steps run, in round order");
        for (uint256 r = 1; r <= last; r++) {
            require(keccak256(abi.encode(vset.membersAt(r))) == startedWith[r], "a started round's members changed");
        }
        checkValidatorsExpected(id);
    }

    /// One round of a case, in a call frame of its own, so the memory spent
    /// reading the examples is freed after it.
    function runRound(string memory id, uint256 r, uint256 nStakes, uint256 nSteps) external {
        require(msg.sender == address(this), "the runner only");
        toRound(r);
        startedWith[r] = keccak256(abi.encode(vset.membersAt(r)));
        // Stake that counts from r + 1 is bonded or unbonded in r.
        for (uint256 i = 0; i < nStakes; i++) {
            if (num(stakesAt(i), ".from_round") + shift != r + 1) continue;
            string[] memory ns = vm.parseJsonKeys(vectors, string.concat(stakesAt(i), ".stake_wei"));
            for (uint256 j = 0; j < ns.length; j++) {
                setStake(ns[j], num(stakesAt(i), string.concat(".stake_wei.", ns[j])));
            }
        }
        for (; nextStep < nSteps && num(stepAt(nextStep), ".round") + shift == r; nextStep++) {
            validatorsStep(stepAt(nextStep));
            holdsCase(string.concat(id, " step ", str(stepAt(nextStep), ".step")), r - shift);
        }
    }

    function validatorsStep(string memory s) internal {
        string memory action = str(s, ".action");
        bytes memory refused;
        if (eq(action, "opt_in") || eq(action, "opt_out")) {
            address n = who(str(s, ".node"));
            address caller = has(s, ".caller") ? who(str(s, ".caller")) : n;
            bytes memory call =
                eq(action, "opt_in") ? abi.encodeCall(Scores.optIn, (n)) : abi.encodeCall(Scores.optOut, (n));
            refused = as_(caller, address(vset), call);
        } else if (eq(action, "seat")) {
            refused = as_(who("Anyone"), address(vset), abi.encodeCall(Scores.seat, (who(str(s, ".node")))));
        } else if (eq(action, "unseat")) {
            refused = as_(who("Anyone"), address(vset), abi.encodeCall(Scores.unseat, (who(str(s, ".node")))));
        } else if (eq(action, "vote")) {
            address n = who(str(s, ".node"));
            address caller = has(s, ".caller") ? who(str(s, ".caller")) : n;
            refused =
                as_(caller, address(vset), abi.encodeCall(Scores.vote, (n, who(str(s, ".target")), num(s, ".score"))));
        } else if (eq(action, "set_operator")) {
            address n = who(str(s, ".node"));
            address caller = has(s, ".caller") ? who(str(s, ".caller")) : n;
            refused = setOperatorAs(caller, n, named(str(s, ".next")));
        } else if (eq(action, "accept_operator")) {
            refused = acceptOperatorAs(who(str(s, ".caller")), who(str(s, ".node")));
        } else if (eq(action, "announce")) {
            string[] memory raw = list(string.concat(s, ".values"));
            uint256[] memory values = new uint256[](raw.length);
            for (uint256 i = 0; i < raw.length; i++) {
                values[i] = vm.parseUint(raw[i]);
            }
            refused = as_(
                who(str(s, ".caller")),
                address(params),
                abi.encodeCall(IParameters.announce, (num(s, ".group"), values))
            );
        } else if (eq(action, "withdraw")) {
            refused =
                as_(who(str(s, ".caller")), address(params), abi.encodeCall(IParameters.withdraw, (num(s, ".group"))));
        } else if (eq(action, "observe")) {
            observedSteps.push(str(s, ".step"));
            observedMembers.push(memberNames(vset.membersAt(clock.currentRound())));
            observedScores.push(vset.scoreOf(who(str(s, ".target"))));
        } else {
            revert(string.concat("unknown action ", action));
        }
        if (refused.length > 0) {
            refusedSteps.push(str(s, ".step"));
            refusedCodes.push(code(refused));
        }
    }

    /// After a step in case round `caseRound`: each node's stake at the
    /// start of the current round is the case's, and the round's members are
    /// those it started with.
    function holdsCase(string memory where, uint256 caseRound) internal view {
        uint256 r = clock.currentRound();
        for (uint256 i = 0; i < names.length; i++) {
            expectEq(
                ledger.stakeAt(who(names[i]), r),
                caseStake(names[i], caseRound),
                string.concat(where, ": stake of ", names[i])
            );
        }
        require(
            keccak256(abi.encode(vset.membersAt(r))) == startedWith[r],
            string.concat(where, ": the round's members changed after it started")
        );
    }

    function checkValidatorsExpected(string memory id) internal view {
        string memory x = string.concat(at, ".expected");
        uint256 n = count(string.concat(x, ".refused"));
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
            string memory want = sorted(list(string.concat(e, ".members")));
            require(
                eq(observedMembers[i], want), string.concat(where, ": members ", observedMembers[i], ", want ", want)
            );
            expectEq(observedScores[i], num(e, ".score"), string.concat(where, ": score"));
        }
    }

    // --- the hard core: a displaced member's vote ---------------------------

    /// R-11, R-13 (spec S-06 phase 2): a member displaced in round R may
    /// still vote in R, and that vote counts in R; it counts in no later
    /// round, even once the member is seated again.
    function test_displaced_member_vote_counts_in_its_last_round_only() external {
        world();
        string[5] memory ns = ["A", "B", "C", "D", "X"];
        for (uint256 i = 0; i < ns.length; i++) {
            reg(ns[i]);
        }
        setStake("A", 4_000e18);
        setStake("B", 3_000e18);
        setStake("C", 2_000e18);
        setStake("D", 5_000e18);
        toRound(2);
        optIn("A");
        optIn("B");
        optIn("C");
        toRound(4);
        address x = who("X");
        voteAs("A", x, 0);
        // D takes C's seat from round 5.
        vm.recordLogs();
        optIn("D");
        expectSeated(who("D"), 5, who("C"));
        requireMembers(vset.membersAt(4), "A,B,C", "round 4 members after the displacement");
        requireMembers(vset.membersAt(5), "A,B,D", "round 5 members as they stand");
        // C, displaced, votes in round 4, and the vote counts in round 4.
        expectEq(vset.scoreOf(x), ONE, "round 4, before C's vote");
        voteAs("C", x, 0);
        expectEq(vset.scoreOf(x), 0, "round 4: C's vote counts (0, 0, 10^6)");

        toRound(5);
        expectEq(vset.scoreOf(x), ONE, "round 5: C's vote does not count (0, 10^6, 10^6)");
        expectRefused(
            as_(who("C"), address(vset), abi.encodeCall(Scores.vote, (who("C"), x, 0))),
            "NOT_VALIDATOR",
            "C's vote in 5"
        );
        // B opts out, and C takes the free seat from round 6.
        expectDone(as_(who("B"), address(vset), abi.encodeCall(Scores.optOut, (who("B")))), "B opts out");
        expectDone(as_(who("Anyone"), address(vset), abi.encodeCall(Scores.seat, (who("C")))), "seat C");

        toRound(6);
        requireMembers(vset.membersAt(6), "A,C,D", "round 6 members");
        expectEq(vset.scoreOf(x), ONE, "round 6: C's vote of round 4 lapsed (0, 10^6, 10^6)");
        // Its new vote counts.
        voteAs("C", x, 0);
        expectEq(vset.scoreOf(x), 0, "round 6: C's new vote counts");
        // The earlier rounds' members never changed.
        requireMembers(vset.membersAt(4), "A,B,C", "round 4 members, later");
        requireMembers(vset.membersAt(5), "A,B,D", "round 5 members, later");
    }

    /// R-13: a tie keeps the incumbent. With every seat taken, a node with
    /// stake equal to the smallest member's is not seated, by opt-in or by
    /// seat; one unit more displaces the one seated last of the equal
    /// smallest. validators-tie observes only the end of this.
    function test_a_tie_keeps_the_incumbent() external {
        world();
        string[4] memory ns = ["A", "B", "C", "D"];
        for (uint256 i = 0; i < ns.length; i++) {
            reg(ns[i]);
        }
        setStake("A", 2_000e18);
        setStake("B", 2_000e18);
        setStake("C", 3_000e18);
        setStake("D", 2_000e18);
        toRound(2);
        optIn("A");
        optIn("B");
        optIn("C");
        optIn("D");
        requireMembers(vset.membersAt(3), "A,B,C", "D's opt-in at an equal stake");
        expectRefused(
            as_(who("Anyone"), address(vset), abi.encodeCall(Scores.seat, (who("D")))), "STAKE_TOO_LOW", "seat D, equal"
        );
        setStake("D", 2_000e18 + 1);
        toRound(3);
        expectDone(as_(who("Anyone"), address(vset), abi.encodeCall(Scores.seat, (who("D")))), "seat D, 1 wei more");
        requireMembers(vset.membersAt(4), "A,C,D", "B, seated last of A and B, leaves");
    }

    // --- refusals and calls that change nothing -------------------------------

    /// NOT_AUTHORIZED for each call made for a node, ahead of every other
    /// refusal (D-39); NOT_VALIDATOR ahead of SCORE_OUT_OF_RANGE.
    function test_calls_for_a_node_are_refused_for_anyone_else() external {
        world();
        reg("A");
        reg("X");
        setStake("A", 1_000e18);
        toRound(2);
        address a = who("A");
        address y = who("Y");
        expectRefused(as_(y, address(vset), abi.encodeCall(Scores.optIn, (a))), "NOT_AUTHORIZED", "Y opts A in");
        optIn("A");
        expectRefused(as_(y, address(vset), abi.encodeCall(Scores.optOut, (a))), "NOT_AUTHORIZED", "Y opts A out");
        // A is not a member of round 2, and the score is out of range.
        expectRefused(
            as_(y, address(vset), abi.encodeCall(Scores.vote, (a, who("X"), ONE + 1))),
            "NOT_AUTHORIZED",
            "Y votes for A"
        );
        expectRefused(
            as_(a, address(vset), abi.encodeCall(Scores.vote, (a, who("X"), ONE + 1))), "NOT_VALIDATOR", "A votes in 2"
        );
        toRound(3);
        expectRefused(
            as_(y, address(vset), abi.encodeCall(Scores.vote, (a, who("X"), 0))), "NOT_AUTHORIZED", "Y votes for A"
        );
        expectRefused(
            as_(a, address(vset), abi.encodeCall(Scores.vote, (a, who("X"), ONE + 1))),
            "SCORE_OUT_OF_RANGE",
            "A, 10^6 + 1"
        );
        // A vote of exactly 10^6 is taken.
        voteAs("A", who("X"), ONE);
        expectEq(vset.scoreOf(who("X")), ONE, "a vote of 10^6");
        requireMembers(vset.membersAt(3), "A", "members");
    }

    /// Opting in again, seating a node already a member for the next round,
    /// unseating one that is not or has stake, and opting out without having
    /// opted in change nothing, emit nothing and are not refused.
    function test_calls_that_change_nothing() external {
        world();
        string[4] memory ns = ["A", "B", "E", "F"];
        for (uint256 i = 0; i < ns.length; i++) {
            reg(ns[i]);
        }
        setStake("A", 1_000e18);
        toRound(2);
        optIn("A");
        optIn("E"); // no stake: opted in, not seated, though a seat is free
        requireMembers(vset.membersAt(3), "A", "members of round 3");
        expectRefused(
            as_(who("Anyone"), address(vset), abi.encodeCall(Scores.seat, (who("E")))),
            "STAKE_TOO_LOW",
            "seat E, no stake"
        );
        setStake("E", 1_000e18);
        toRound(3);
        bytes32 before = keccak256(abi.encode(vset.membersAt(4)));
        vm.recordLogs();
        // E has stake now, but opting in again does not seat it.
        expectDone(as_(who("E"), address(vset), abi.encodeCall(Scores.optIn, (who("E")))), "E opts in again");
        expectDone(as_(who("A"), address(vset), abi.encodeCall(Scores.optIn, (who("A")))), "A opts in again");
        expectDone(as_(who("Anyone"), address(vset), abi.encodeCall(Scores.seat, (who("A")))), "seat A, a member");
        expectDone(as_(who("Anyone"), address(vset), abi.encodeCall(Scores.unseat, (who("A")))), "unseat A, staked");
        expectDone(as_(who("Anyone"), address(vset), abi.encodeCall(Scores.unseat, (who("E")))), "unseat E, no member");
        expectDone(as_(who("Anyone"), address(vset), abi.encodeCall(Scores.unseat, (who("B")))), "unseat B");
        expectDone(as_(who("F"), address(vset), abi.encodeCall(Scores.optOut, (who("F")))), "F opts out, never in");
        expectEq(vm.getRecordedLogs().length, 0, "events from calls that change nothing");
        require(keccak256(abi.encode(vset.membersAt(4))) == before, "the members changed");
        // Seating B, never opted in, and F, opted out, is refused.
        expectRefused(
            as_(who("Anyone"), address(vset), abi.encodeCall(Scores.seat, (who("B")))), "NOT_OPTED_IN", "seat B"
        );
        optIn("F");
        expectDone(as_(who("F"), address(vset), abi.encodeCall(Scores.optOut, (who("F")))), "F opts out");
        expectRefused(
            as_(who("Anyone"), address(vset), abi.encodeCall(Scores.seat, (who("F")))), "NOT_OPTED_IN", "seat F"
        );
        // Seating E, opted in and with stake, takes effect.
        expectDone(as_(who("Anyone"), address(vset), abi.encodeCall(Scores.seat, (who("E")))), "seat E");
        requireMembers(vset.membersAt(4), "A,E", "members of round 4");
    }

    /// The events a participant reads who opted in and who voted what from.
    function test_events() external {
        world();
        string[3] memory ns = ["A", "B", "X"];
        for (uint256 i = 0; i < ns.length; i++) {
            reg(ns[i]);
        }
        setStake("A", 1_000e18);
        toRound(2);
        vm.recordLogs();
        optIn("A");
        Vm.Log[] memory logs = vm.getRecordedLogs();
        expectEq(logs.length, 2, "an opt-in that seats: two events");
        require(logs[0].topics[0] == IScores.OptedIn.selector && logs[0].topics[1] == word(who("A")), "OptedIn");
        expectEq(abi.decode(logs[0].data, (uint256)), 2, "OptedIn round");
        require(logs[1].topics[0] == IScores.Seated.selector, "Seated");
        (uint256 from, address displaced) = abi.decode(logs[1].data, (uint256, address));
        expectEq(from, 3, "Seated fromRound");
        require(displaced == address(0), "Seated displaced: none");
        toRound(3);
        vm.recordLogs();
        voteAs("A", who("X"), 250_000);
        logs = vm.getRecordedLogs();
        require(
            logs[0].topics[0] == IScores.Voted.selector && logs[0].topics[1] == word(who("A"))
                && logs[0].topics[2] == word(who("X")),
            "Voted"
        );
        (uint256 s, uint256 r) = abi.decode(logs[0].data, (uint256, uint256));
        expectEq(s, 250_000, "Voted score");
        expectEq(r, 3, "Voted round");
        vm.recordLogs();
        expectDone(as_(who("A"), address(vset), abi.encodeCall(Scores.optOut, (who("A")))), "A opts out");
        logs = vm.getRecordedLogs();
        require(logs.length == 1 && logs[0].topics[0] == IScores.OptedOut.selector, "OptedOut");
        expectEq(abi.decode(logs[0].data, (uint256)), 4, "OptedOut fromRound");
    }

    // --- R-23: no work grows with the opted-in nodes -------------------------

    /// Bound: the gas of seat, unseat, vote and scoreOf at 100 and 200
    /// opted-in nodes equals their gas at 3, to the unit. Why that bound: with
    /// the same members, votes and stakes, each call reads and writes the
    /// same slots, all cold (each call is measured from the same state, with
    /// every contract's storage cooled first), and no slot it touches is
    /// chosen by how many nodes opted in. Any read per opted-in node would
    /// add at least 100 gas a node, 9,700 more at 100 nodes; no allowance is
    /// needed to tell that apart from nothing.
    ///
    /// Two worlds. With a free seat: A and B are the members of round 4, D
    /// opted in with no stake and has 5,000 LPT from round 4, so 3 nodes
    /// have opted in; seat(D) fills the free seat. With every seat taken: A,
    /// B and C are the members, so 4 have opted in, the fewest that can
    /// make seat(D) displace one. In both, B's stake is 0 from round 4, so
    /// unseat(B) takes effect, and A voted on X in round 3. Each other
    /// opted-in node has no stake.
    function test_gas_does_not_grow_with_opted_in_nodes() external {
        uint256[3] memory sizes = [uint256(3), 100, 200];
        string[4] memory call = ["seat", "unseat", "vote", "scoreOf"];
        for (uint256 full = 0; full < 2; full++) {
            uint256[4] memory base;
            for (uint256 k = 0; k < sizes.length; k++) {
                uint256 n = sizes[k] < 3 + full ? 3 + full : sizes[k];
                uint256[4] memory g = gasAt(n, full == 1);
                for (uint256 c = 0; c < 4; c++) {
                    emit log_named_uint(
                        string.concat(full == 1 ? "full: " : "free seat: ", call[c], " at ", vm.toString(n)), g[c]
                    );
                    if (k == 0) base[c] = g[c];
                    else expectEq(g[c], base[c], string.concat(call[c], " gas at ", vm.toString(n)));
                }
            }
        }
    }

    function gasAt(uint256 optedIn, bool full) internal returns (uint256[4] memory g) {
        world();
        string[5] memory ns = ["A", "B", "C", "D", "X"];
        for (uint256 i = 0; i < ns.length; i++) {
            reg(ns[i]);
        }
        setStake("A", 4_000e18);
        setStake("B", 3_000e18);
        if (full) setStake("C", 2_000e18);
        uint256 base = full ? 4 : 3;
        for (uint256 i = 0; i < optedIn - base; i++) {
            address e = address(uint160(0x20000 + i));
            vm.prank(e);
            registry.register();
        }
        toRound(2);
        optIn("A");
        optIn("B");
        if (full) optIn("C");
        optIn("D");
        for (uint256 i = 0; i < optedIn - base; i++) {
            address e = address(uint160(0x20000 + i));
            expectDone(as_(e, address(vset), abi.encodeCall(Scores.optIn, (e))), "extra opts in");
        }
        toRound(3);
        voteAs("A", who("X"), 0);
        setStake("B", 0);
        setStake("D", 5_000e18);
        toRound(4);
        requireMembers(vset.membersAt(4), full ? "A,B,C" : "A,B", "members measured");

        uint256 snap = vm.snapshotState();
        g[0] = measured(who("Anyone"), abi.encodeCall(Scores.seat, (who("D"))), snap);
        requireMembers(vset.membersAt(5), full ? "A,C,D" : "A,B,D", "seat measured");
        g[1] = measured(who("Anyone"), abi.encodeCall(Scores.unseat, (who("B"))), snap);
        requireMembers(vset.membersAt(5), full ? "A,C" : "A", "unseat measured");
        g[2] = measured(who("A"), abi.encodeCall(Scores.vote, (who("A"), who("X"), 0)), snap);
        g[3] = measured(who("Anyone"), abi.encodeCall(Scores.scoreOf, (who("X"))), snap);
    }

    /// The gas of one call taking effect, from the state `snap`, with every
    /// contract's storage cold.
    function measured(address caller, bytes memory data, uint256 snap) internal returns (uint256) {
        vm.revertToState(snap);
        vm.cool(address(vset));
        vm.cool(address(ledger));
        vm.cool(address(registry));
        vm.cool(address(clock));
        expectDone(as_(caller, address(vset), data), "measured call");
        return vm.lastFrameGas().gasTotalUsed;
    }

    // --- helpers ----------------------------------------------------------

    /// Sets `name`'s own bond, and with it its stake, to `amount` from the
    /// next round.
    function setStake(string memory name, uint256 amount) internal {
        address n = who(name);
        uint256 now_ = ledger.stayingOf(n, n);
        if (amount > now_) {
            lpt.transfer(n, amount - now_);
            expectDone(bondAs(n, n, amount - now_), string.concat(name, " bonds"));
        } else if (amount < now_) {
            expectDone(as_(n, address(ledger), abi.encodeCall(StakeLedger.unbond, (n, now_ - amount))), "unbond");
        }
    }

    /// The case's stake for `name` at the start of case round `r`.
    function caseStake(string memory name, uint256 r) internal view returns (uint256 stake) {
        uint256 n = count(string.concat(at, ".inputs.stakes"));
        for (uint256 i = 0; i < n; i++) {
            if (num(stakesAt(i), ".from_round") > r) continue;
            string memory k = string.concat(stakesAt(i), ".stake_wei.", name);
            if (vm.keyExistsJson(vectors, k)) stake = vm.parseUint(vm.parseJsonString(vectors, k));
        }
    }

    /// Registers `name` and remembers it, so the members can be named.
    function reg(string memory name) internal {
        register(name);
        remember(name);
    }

    function optIn(string memory name) internal {
        expectDone(
            as_(who(name), address(vset), abi.encodeCall(Scores.optIn, (who(name)))), string.concat(name, " opts in")
        );
    }

    function voteAs(string memory name, address target, uint256 score) internal {
        expectDone(
            as_(who(name), address(vset), abi.encodeCall(Scores.vote, (who(name), target, score))),
            string.concat(name, " votes")
        );
    }

    function expectSeated(address node, uint256 fromRound, address displaced) internal {
        Vm.Log[] memory logs = vm.getRecordedLogs();
        for (uint256 i = 0; i < logs.length; i++) {
            if (logs[i].topics[0] != IScores.Seated.selector || logs[i].topics[1] != word(node)) continue;
            (uint256 f, address d) = abi.decode(logs[i].data, (uint256, address));
            expectEq(f, fromRound, "Seated fromRound");
            require(d == displaced, "Seated displaced");
            return;
        }
        revert("no Seated event");
    }

    function requireMembers(address[] memory got, string memory want, string memory what) internal view {
        string memory g = memberNames(got);
        require(eq(g, want), string.concat(what, ": ", g, ", want ", want));
    }

    /// The names of `members`, sorted and joined by commas.
    function memberNames(address[] memory members) internal view returns (string memory) {
        string[] memory out = new string[](members.length);
        for (uint256 i = 0; i < members.length; i++) {
            out[i] = "?";
            for (uint256 j = 0; j < names.length; j++) {
                if (who(names[j]) == members[i]) out[i] = names[j];
            }
            if (eq(out[i], "?")) out[i] = vm.toString(uint256(uint160(members[i])));
        }
        return sorted(out);
    }

    function sorted(string[] memory xs) internal pure returns (string memory joined) {
        for (uint256 i = 1; i < xs.length; i++) {
            for (uint256 j = i; j > 0 && before(xs[j], xs[j - 1]); j--) {
                (xs[j], xs[j - 1]) = (xs[j - 1], xs[j]);
            }
        }
        for (uint256 i = 0; i < xs.length; i++) {
            joined = string.concat(joined, i == 0 ? "" : ",", xs[i]);
        }
    }

    function before(string memory a, string memory b) internal pure returns (bool) {
        bytes memory x = bytes(a);
        bytes memory y = bytes(b);
        for (uint256 i = 0; i < x.length && i < y.length; i++) {
            if (x[i] != y[i]) return x[i] < y[i];
        }
        return x.length < y.length;
    }

    function word(address a) internal pure returns (bytes32) {
        return bytes32(uint256(uint160(a)));
    }

    function remember(string memory name) internal {
        for (uint256 i = 0; i < names.length; i++) {
            if (eq(names[i], name)) return;
        }
        names.push(name);
    }

    /// The strings of a JSON array, read one by one: forge reads a
    /// one-element array as its element. An absent key reads as empty.
    function list(string memory key) internal view returns (string[] memory out) {
        out = new string[](count(key));
        for (uint256 i = 0; i < out.length; i++) {
            out[i] = vm.parseJsonString(vectors, string.concat(key, "[", vm.toString(i), "]"));
        }
    }

    function count(string memory key) internal view returns (uint256 n) {
        while (vm.keyExistsJson(vectors, string.concat(key, "[", vm.toString(n), "]"))) n++;
    }

    function stakesAt(uint256 i) internal view returns (string memory) {
        return string.concat(at, ".inputs.stakes[", vm.toString(i), "]");
    }

    function stepAt(uint256 i) internal view returns (string memory) {
        return string.concat(at, ".inputs.steps[", vm.toString(i), "]");
    }

    function has(string memory s, string memory field) internal view returns (bool) {
        return vm.keyExistsJson(vectors, string.concat(s, field));
    }

    function str(string memory s, string memory field) internal view returns (string memory) {
        return vm.parseJsonString(vectors, string.concat(s, field));
    }

    function num(string memory s, string memory field) internal view returns (uint256) {
        return vm.parseUint(vm.parseJsonString(vectors, string.concat(s, field)));
    }
}
