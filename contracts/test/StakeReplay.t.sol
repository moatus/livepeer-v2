// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {vm} from "./Vm.sol";
import {RewardsFixture} from "./RewardsFixture.sol";
import {StakeLedger} from "../src/money/StakeLedger.sol";

/// S-05's hard core, stake at the start of a past round: bonds, unbonds,
/// transfers, unbonds of transfers, rebonds and withdrawals across 40
/// rounds, replayed by a model written another way. The model keeps only
/// the present: each holder's bond staying at each node, its transfers and
/// its unbonds. At the start of every round it lands the transfers due and
/// writes down what counts for every node, which is R-15 read literally:
/// the bond staying there plus what is in transfer away from it. After the
/// last round every bondAt, stakeAt and totalStakeAt of every round is
/// checked against those snapshots, so a ledger that rewrites a past round,
/// or knows only the current one, fails.
contract StakeReplayTest is RewardsFixture {
    uint256 private constant ROUNDS = 40;
    uint256 private constant HOLDERS = 3;
    /// Registered nodes are 0 to NODES - 1; node NODES never registers.
    uint256 private constant NODES = 3;
    uint256 private constant GRANT = 1_000e18;
    uint256 private constant WAIT = 21;

    struct Move {
        uint256 holder;
        uint256 from;
        uint256 to;
        uint256 amount;
        uint256 arrival;
        bool landed;
    }

    struct Lock {
        uint256 holder;
        uint256 node;
        uint256 amount;
        uint256 ready;
    }

    // The model.
    mapping(uint256 => mapping(uint256 => uint256)) private staying;
    mapping(uint256 => uint256) private balance;
    Move[] private moves;
    Lock[] private locks;
    mapping(uint256 => mapping(uint256 => mapping(uint256 => uint256))) private snapBond;
    mapping(uint256 => mapping(uint256 => uint256)) private snapStake;
    mapping(uint256 => uint256) private snapTotal;

    // What the run exercised.
    uint256 private done;
    uint256 private refusals;
    uint256 private arrivals;
    uint256 private transferUnbonds;
    uint256 private transferUnbondsOfSeveral;
    uint256 private cancelsBeforeALaterArrival;
    uint256 private rebonds;
    uint256 private rebondsOfSeveral;
    uint256 private withdrawals;

    uint256 private seed;
    uint256 private draws;

    // --- the tests ----------------------------------------------------------

    function test_replay_seed_1() external {
        random(1);
        covered();
    }

    function test_replay_seed_2() external {
        random(2);
        covered();
    }

    function test_replay_seed_3() external {
        random(3);
        covered();
    }

    /// More seeds, a few per run of the fast checks. A seed may miss an
    /// order; the fixed seeds and the script show each is reached.
    /// forge-config: default.fuzz.runs = 4
    function testFuzz_replay(uint256 s) external {
        random(s);
    }

    /// The orders a random run may miss, on purpose: an arrival cancelled
    /// while a later one is already written, an unbond of several transfers,
    /// a rebond of several unbonds, a rebond of an unbonded transfer, a
    /// transfer to the node it leaves, arrivals with nobody acting, and
    /// withdrawals.
    function test_replay_scripted() external {
        start();
        for (uint256 r = 1; r <= ROUNDS; r++) {
            begin(r);
            if (r == 1) {
                bond(0, 0, 600e18);
                bond(1, 1, 300e18);
                bond(2, 2, 100e18);
            } else if (r == 10) {
                transfer(0, 0, 1, 100e18); // arrives 31
            } else if (r == 11) {
                transfer(0, 0, 2, 50e18); // arrives 32
                transfer(0, 0, 1, 30e18); // arrives 32
                transfer(1, 1, 1, 20e18); // to the node it leaves, arrives 32
            } else if (r == 12) {
                unbondTransfer(0, 0, 1, 40e18); // takes from round 32's first
                unbondTransfer(0, 0, 2, 50e18); // all of round 32's
                unbond(0, 0, 100e18);
            } else if (r == 13) {
                unbondTransfer(0, 0, 1, 50e18); // the arrival at 31, before 32's
                rebond(0, 0, 120e18); // round 13's 50 first, then 70 of round 12's
                unbond(2, 2, 60e18);
            } else if (r == 14) {
                rebond(0, 0, 30e18);
                rebond(2, 2, 10e18);
                unbondTransfer(1, 1, 1, 5e18);
            } else if (r == 20) {
                transfer(2, 2, 0, 50e18); // arrives 41, after the run
            } else if (r == 33) {
                withdraw(0);
                withdraw(2); // round 13's unbond is ready at 34
            } else if (r == 35) {
                withdraw(1);
                withdraw(2);
                unbond(1, 1, 15e18);
                bond(2, 1, 1e18);
            }
        }
        finish();
        require(transferUnbondsOfSeveral > 0, "an unbond of several transfers");
        require(cancelsBeforeALaterArrival > 0, "an arrival cancelled before a later one");
        require(rebondsOfSeveral > 0, "a rebond of several unbonds");
    }

    // --- the runs -----------------------------------------------------------

    function random(uint256 s) internal {
        seed = s;
        start();
        for (uint256 r = 1; r <= ROUNDS; r++) {
            begin(r);
            uint256 actions = 2 + draw(4);
            for (uint256 k = 0; k < actions; k++) {
                randomAction();
            }
        }
        finish();
    }

    /// The run was not vacuous: every kind of move took effect, including
    /// the orders a sorted list gets wrong, and refusals were checked too.
    function covered() internal view {
        require(transferUnbonds > 0 && rebonds > 0 && arrivals > 0 && withdrawals > 0, "every kind of move ran");
        require(transferUnbondsOfSeveral > 0 && cancelsBeforeALaterArrival > 0, "the orders a list can break");
        require(rebondsOfSeveral > 0, "a rebond of several unbonds");
        require(refusals > 0 && done > refusals, "more moves take effect than are refused");
    }

    /// A move of a random kind by a random holder. Mostly it names a
    /// record that holds something, so most moves take effect; one in eight
    /// names any node at all.
    function randomAction() internal {
        uint256 kind = draw(100);
        uint256 h = draw(HOLDERS);
        bool any = draw(8) == 0;
        uint256 a = pickNode();
        uint256 b = pickNode();
        if (kind < 20) {
            bond(h, a, amount(balance[h]));
        } else if (kind < 35) {
            if (!any) a = nodeWith(h, 0);
            unbond(h, a, amount(a < NODES ? staying[h][a] : 0));
        } else if (kind < 55) {
            if (!any) a = nodeWith(h, 0);
            transfer(h, a, b, amount(a < NODES ? staying[h][a] : 0));
        } else if (kind < 75) {
            if (!any) (a, b) = pairInTransfer(h);
            unbondTransfer(h, a, b, amount(inTransfer(h, a, b)));
        } else if (kind < 92) {
            if (!any) a = nodeWith(h, 1);
            rebond(h, a, amount(waitingFor(h, a)));
        } else {
            withdraw(h);
        }
    }

    /// A node where the holder has a bond staying (what 0) or an unbond
    /// waiting (what 1), from a random start; any node if none.
    function nodeWith(uint256 h, uint256 what) internal returns (uint256) {
        uint256 first = draw(NODES);
        for (uint256 k = 0; k < NODES; k++) {
            uint256 j = (first + k) % NODES;
            if ((what == 0 ? staying[h][j] : waitingFor(h, j)) > 0) return j;
        }
        return first;
    }

    function pairInTransfer(uint256 h) internal returns (uint256, uint256) {
        uint256 first = draw(NODES * NODES);
        for (uint256 k = 0; k < NODES * NODES; k++) {
            uint256 p = (first + k) % (NODES * NODES);
            if (inTransfer(h, p / NODES, p % NODES) > 0) return (p / NODES, p % NODES);
        }
        return (first / NODES, first % NODES);
    }

    function pickNode() internal returns (uint256) {
        return draw(12) == 0 ? NODES : draw(NODES);
    }

    /// Mostly part or all of what is available; sometimes just above it.
    function amount(uint256 available) internal returns (uint256) {
        if (available == 0 || draw(10) == 0) return available + 1 + draw(5e18);
        if (draw(4) == 0) return available;
        return 1 + draw(available);
    }

    function draw(uint256 n) internal returns (uint256) {
        draws++;
        return uint256(keccak256(abi.encode(seed, draws))) % n;
    }

    function start() internal {
        deployRewards();
        for (uint256 j = 0; j < NODES; j++) {
            vm.prank(nodeAt(j));
            registry.register();
        }
        for (uint256 i = 0; i < HOLDERS; i++) {
            lpt.transfer(holderAt(i), GRANT);
            balance[i] = GRANT;
        }
    }

    /// Starts round r: lands the transfers due, writes down what counts at
    /// the start of r, and checks the ledger agrees now.
    function begin(uint256 r) internal {
        toRound(r);
        for (uint256 m = 0; m < moves.length; m++) {
            Move storage mv = moves[m];
            if (!mv.landed && mv.amount > 0 && mv.arrival <= r) {
                staying[mv.holder][mv.to] += mv.amount;
                mv.landed = true;
                arrivals++;
            }
        }
        uint256 total = 0;
        for (uint256 j = 0; j < NODES; j++) {
            uint256 stake = 0;
            for (uint256 i = 0; i < HOLDERS; i++) {
                uint256 counts = staying[i][j];
                for (uint256 m = 0; m < moves.length; m++) {
                    Move storage mv = moves[m];
                    if (mv.holder == i && mv.from == j && !mv.landed) counts += mv.amount;
                }
                snapBond[r][i][j] = counts;
                stake += counts;
            }
            snapStake[r][j] = stake;
            total += stake;
        }
        snapTotal[r] = total;
        checkRound(r, "at its start");
    }

    /// One round past the last, then every round again.
    function finish() internal {
        begin(ROUNDS + 1);
        for (uint256 r = 1; r <= ROUNDS + 1; r++) {
            checkRound(r, "after the run");
        }
    }

    function checkRound(uint256 r, string memory when) internal view {
        string memory at = string.concat("round ", vm.toString(r), " ", when);
        for (uint256 j = 0; j < NODES; j++) {
            for (uint256 i = 0; i < HOLDERS; i++) {
                expectEq(
                    ledger.bondAt(holderAt(i), nodeAt(j), r),
                    snapBond[r][i][j],
                    string.concat(at, ": bondAt ", vm.toString(i), " ", vm.toString(j))
                );
            }
            expectEq(ledger.stakeAt(nodeAt(j), r), snapStake[r][j], string.concat(at, ": stakeAt ", vm.toString(j)));
        }
        expectEq(ledger.totalStakeAt(r), snapTotal[r], string.concat(at, ": totalStakeAt"));
    }

    // --- each move, in the model and on the ledger ------------------------

    function bond(uint256 h, uint256 n, uint256 amt) internal {
        bytes memory ret = bondAs(holderAt(h), nodeAt(n), amt);
        if (n >= NODES) return refused(ret, "NOT_REGISTERED", "bond");
        // The token's own kept text: there is no ledger code for it.
        if (amt > balance[h]) return refused(ret, "balance too low", "bond");
        staying[h][n] += amt;
        balance[h] -= amt;
        took(ret, "bond");
    }

    function unbond(uint256 h, uint256 n, uint256 amt) internal {
        bytes memory ret = call(h, abi.encodeCall(StakeLedger.unbond, (nodeAt(n), amt)));
        if (amt > (n < NODES ? staying[h][n] : 0)) return refused(ret, "AMOUNT_TOO_LARGE", "unbond");
        staying[h][n] -= amt;
        locks.push(Lock(h, n, amt, clock.currentRound() + WAIT));
        took(ret, "unbond");
    }

    function transfer(uint256 h, uint256 from, uint256 to, uint256 amt) internal {
        bytes memory ret = call(h, abi.encodeCall(StakeLedger.transferBond, (nodeAt(from), nodeAt(to), amt)));
        if (to >= NODES) return refused(ret, "NOT_REGISTERED", "transfer");
        if (to == from) return refused(ret, "SAME_NODE", "transfer");
        if (amt > (from < NODES ? staying[h][from] : 0)) return refused(ret, "AMOUNT_TOO_LARGE", "transfer");
        staying[h][from] -= amt;
        moves.push(Move(h, from, to, amt, clock.currentRound() + WAIT, false));
        took(ret, "transfer");
    }

    /// The transfer arriving last first; it stops counting for `from` at
    /// the next round's start, never lands, and waits as an unbond of
    /// `from`.
    function unbondTransfer(uint256 h, uint256 from, uint256 to, uint256 amt) internal {
        bytes memory ret = call(h, abi.encodeCall(StakeLedger.unbondTransfer, (nodeAt(from), nodeAt(to), amt)));
        if (to == from) return refused(ret, "SAME_NODE", "unbond of a transfer");
        if (amt > inTransfer(h, from, to)) return refused(ret, "AMOUNT_TOO_LARGE", "unbond of a transfer");
        uint256 left = amt;
        uint256 pieces = 0;
        while (left > 0) {
            uint256 last = type(uint256).max;
            for (uint256 m = 0; m < moves.length; m++) {
                Move storage mv = moves[m];
                if (mv.holder != h || mv.from != from || mv.to != to || mv.landed || mv.amount == 0) continue;
                if (last == type(uint256).max || mv.arrival >= moves[last].arrival) last = m;
            }
            Move storage taken = moves[last];
            if (laterArrivalWritten(taken.arrival)) cancelsBeforeALaterArrival++;
            uint256 part = taken.amount < left ? taken.amount : left;
            taken.amount -= part;
            left -= part;
            pieces++;
        }
        if (amt > 0) locks.push(Lock(h, from, amt, clock.currentRound() + WAIT));
        transferUnbonds++;
        if (pieces > 1) transferUnbondsOfSeveral++;
        took(ret, "unbond of a transfer");
    }

    /// The latest unbond first, of those counting for `n` and not yet
    /// withdrawable.
    function rebond(uint256 h, uint256 n, uint256 amt) internal {
        bytes memory ret = call(h, abi.encodeCall(StakeLedger.rebond, (nodeAt(n), amt)));
        if (n >= NODES) return refused(ret, "NOT_REGISTERED", "rebond");
        if (amt > waitingFor(h, n)) return refused(ret, "AMOUNT_TOO_LARGE", "rebond");
        uint256 now_ = clock.currentRound();
        uint256 left = amt;
        uint256 pieces = 0;
        while (left > 0) {
            uint256 last = type(uint256).max;
            for (uint256 k = 0; k < locks.length; k++) {
                Lock storage l = locks[k];
                if (l.holder != h || l.node != n || l.ready <= now_ || l.amount == 0) continue;
                if (last == type(uint256).max || l.ready >= locks[last].ready) last = k;
            }
            Lock storage taken = locks[last];
            uint256 part = taken.amount < left ? taken.amount : left;
            taken.amount -= part;
            left -= part;
            pieces++;
        }
        staying[h][n] += amt;
        rebonds++;
        if (pieces > 1) rebondsOfSeveral++;
        took(ret, "rebond");
    }

    function withdraw(uint256 h) internal {
        bytes memory ret = call(h, abi.encodeCall(StakeLedger.withdrawUnbonded, ()));
        uint256 now_ = clock.currentRound();
        uint256 ready = 0;
        for (uint256 k = 0; k < locks.length; k++) {
            if (locks[k].holder == h && locks[k].ready <= now_) {
                ready += locks[k].amount;
                locks[k].amount = 0;
            }
        }
        if (ready == 0) return refused(ret, "UNBOND_NOT_READY", "withdrawal");
        balance[h] += ready;
        withdrawals++;
        took(ret, "withdrawal");
    }

    // --- the model's reads -------------------------------------------------

    function inTransfer(uint256 h, uint256 from, uint256 to) internal view returns (uint256 sum) {
        for (uint256 m = 0; m < moves.length; m++) {
            Move storage mv = moves[m];
            if (mv.holder == h && mv.from == from && mv.to == to && !mv.landed) sum += mv.amount;
        }
    }

    function waitingFor(uint256 h, uint256 n) internal view returns (uint256 sum) {
        uint256 now_ = clock.currentRound();
        for (uint256 k = 0; k < locks.length; k++) {
            if (locks[k].holder == h && locks[k].node == n && locks[k].ready > now_) sum += locks[k].amount;
        }
    }

    /// Whether any transfer, of any holder, still arrives after `arrival`.
    function laterArrivalWritten(uint256 arrival) internal view returns (bool) {
        for (uint256 m = 0; m < moves.length; m++) {
            if (!moves[m].landed && moves[m].amount > 0 && moves[m].arrival > arrival) {
                return true;
            }
        }
        return false;
    }

    // --- after every move ---------------------------------------------------

    function refused(bytes memory ret, string memory want, string memory what) internal {
        expectRefused(ret, want, what);
        refusals++;
        agrees(what);
    }

    function took(bytes memory ret, string memory what) internal {
        expectDone(ret, what);
        done++;
        agrees(what);
    }

    /// Every view of the present equals the model, and StakeLedger holds
    /// every bond staying, in transfer or arriving, and every unbond not
    /// withdrawn.
    function agrees(string memory what) internal view {
        string memory at = string.concat("round ", vm.toString(clock.currentRound()), " after ", what);
        uint256 now_ = clock.currentRound();
        uint256 held = 0;
        for (uint256 i = 0; i < HOLDERS; i++) {
            address h = holderAt(i);
            expectEq(lpt.balanceOf(h), balance[i], string.concat(at, ": LPT of ", vm.toString(i)));
            uint256 waiting = 0;
            uint256 ready = 0;
            for (uint256 k = 0; k < locks.length; k++) {
                if (locks[k].holder != i) continue;
                if (locks[k].ready <= now_) ready += locks[k].amount;
                else waiting += locks[k].amount;
            }
            (uint256 w, uint256 rd) = ledger.unbondedOf(h);
            expectEq(w, waiting, string.concat(at, ": waiting"));
            expectEq(rd, ready, string.concat(at, ": ready"));
            held += waiting + ready;
            for (uint256 j = 0; j < NODES; j++) {
                uint256 stays = staying[i][j];
                // A transfer due this round has landed, whether or not the
                // model's begin() has run since: count it as staying.
                for (uint256 m = 0; m < moves.length; m++) {
                    Move storage mv = moves[m];
                    if (mv.holder == i && mv.to == j && !mv.landed && mv.arrival <= now_) stays += mv.amount;
                }
                expectEq(ledger.stayingOf(h, nodeAt(j)), stays, string.concat(at, ": staying"));
                expectEq(ledger.rebondableOf(h, nodeAt(j)), waitingFor(i, j), string.concat(at, ": rebondable"));
                held += stays;
                for (uint256 k = 0; k < NODES; k++) {
                    uint256 moving = inTransfer(i, j, k);
                    expectEq(ledger.inTransferOf(h, nodeAt(j), nodeAt(k)), moving, string.concat(at, ": in transfer"));
                    held += moving;
                }
            }
        }
        expectEq(lpt.balanceOf(address(ledger)), held, string.concat(at, ": StakeLedger LPT"));
    }

    // --- names ----------------------------------------------------------------

    function call(uint256 h, bytes memory data) internal returns (bytes memory) {
        return as_(holderAt(h), address(ledger), data);
    }

    function holderAt(uint256 i) internal pure returns (address) {
        return who(string.concat("holder ", vm.toString(i)));
    }

    function nodeAt(uint256 j) internal pure returns (address) {
        return who(string.concat("node ", vm.toString(j)));
    }
}
