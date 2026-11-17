// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

import {vm} from "../test/Vm.sol";
import {Stage2} from "../test/Stage2.sol";
import {Registry} from "../src/authority/Registry.sol";
import {Broker} from "../src/money/Broker.sol";
import {ITicketBroker} from "../interfaces/ITicketBroker.sol";

/// Drives a fresh stage 2 deployment through random sequences of what its
/// participants can do, and keeps, beside it, what the rulebook says each
/// step must do (R-01, R-03, R-04, R-21, R-22). The invariant runs compare
/// the two after every step. The parameters are read from the shared
/// examples file, never retyped.
///
/// Five actors each pay and may be nodes; actors 0 to 2 register at the
/// start and 3 and 4 only when a run registers them, so payments to an
/// address that is not a node happen too. Two wallets that are neither
/// payers nor nodes can be named as payouts.
contract Handler is Stage2 {
    uint256 internal constant ACTORS = 5;
    uint256 internal constant WALLETS = 2;
    uint256 internal constant ONE = 1_000_000;
    /// Tickets kept for redeeming again; the oldest is replaced after this.
    uint256 internal constant KEPT = 64;

    uint256 public nodeCut;
    uint256 public validity;
    uint256 public releaseWait;
    uint256 public roundLength;

    /// The rulebook's view of one balance (R-21, R-22).
    struct Bal {
        uint256 amount;
        uint256 pending;
        uint256 readyRound;
    }

    mapping(address => mapping(address => Bal)) internal model;
    mapping(address => bool) internal registeredInModel;
    /// address(0): paid at the node itself (R-01).
    mapping(address => address) internal payoutInModel;

    /// What each tracked address must hold in USDC.
    mapping(address => uint256) public walletShould;
    mapping(address => uint256) public fundedBy;
    mapping(address => uint256) public releasedTo;
    mapping(address => uint256) public paidFrom;

    uint256 public minted;
    uint256 public funded;
    uint256 public released;
    uint256 public paidToNodes;
    uint256 public paidToNetwork;
    mapping(uint256 => uint256) public feesShould;
    mapping(uint256 => mapping(address => uint256)) public feesOfShould;

    struct Kept {
        ITicketBroker.Ticket t;
        bytes sig;
        uint256 rand;
        bool paid;
    }

    address[] internal actors;
    Kept[] internal kept;
    uint256 internal nonce;

    /// A ticket the contracts paid when it had already paid.
    uint256 public paidTwice;
    /// Calls whose outcome the rules did not allow; the first is described.
    uint256 public mismatches;
    string public firstMismatch;

    /// How often each kind of step ran and took effect, for the report.
    mapping(string => uint256) public ran;

    constructor() {
        loadVectors();
        nodeCut = param("node_cut_millionths");
        validity = param("ticket_validity_rounds");
        releaseWait = param("release_wait_rounds");
        roundLength = param("round_length_blocks");
        deploy();
        for (uint256 i = 0; i < ACTORS; i++) {
            actors.push(who(actorName(i)));
        }
        for (uint256 i = 0; i < 3; i++) {
            vm.prank(actor(i));
            registry.register();
            registeredInModel[actor(i)] = true;
        }
    }

    // --- who --------------------------------------------------------------

    function actor(uint256 i) public view returns (address) {
        return actors[i % ACTORS];
    }

    function actorKey(uint256 i) internal pure returns (uint256) {
        return key(actorName(i % ACTORS));
    }

    function actorName(uint256 i) internal pure returns (string memory) {
        return string.concat("actor ", vm.toString(i));
    }

    function wallet(uint256 i) public pure returns (address) {
        return address(uint160(0x5000 + i % WALLETS));
    }

    /// Every address whose USDC the runs check: the actors and the wallets.
    function tracked(uint256 i) public view returns (address) {
        return i < ACTORS ? actor(i) : wallet(i - ACTORS);
    }

    function trackedCount() public pure returns (uint256) {
        return ACTORS + WALLETS;
    }

    /// The balances a payer can hold: its deposit (address(0)) and an
    /// earmark for each actor.
    function slot(uint256 i) public view returns (address) {
        return i == 0 ? address(0) : actor(i - 1);
    }

    function slotCount() public pure returns (uint256) {
        return ACTORS + 1;
    }

    function modelOf(address payer, address node) external view returns (uint256, uint256, uint256) {
        Bal storage b = model[payer][node];
        return (b.amount, b.pending, b.readyRound);
    }

    function brokerAddress() external view returns (address) {
        return address(broker);
    }

    function usdcAddress() external view returns (address) {
        return address(usdc);
    }

    function currentRound() external view returns (uint256) {
        return clock.currentRound();
    }

    function keptCount() external view returns (uint256) {
        return kept.length;
    }

    function keptTicket(uint256 i) external view returns (bytes32 hash, bool paid) {
        return (ticketHash(kept[i].t), kept[i].paid);
    }

    /// An amount spread over tiny, ordinary and large values, so balances
    /// meet faces exactly, fall short of them and cover them.
    function amount(uint256 seed) internal pure returns (uint256) {
        uint256 x = seed >> 8;
        uint256 k = seed % 4;
        if (k == 0) return x % 100;
        if (k == 1) return x % 10_000_000;
        if (k == 2) return (x % 20) * ONE;
        return x % 1_000_000_000_000;
    }

    // --- steps ------------------------------------------------------------

    function registerNode(uint256 a) external {
        address n = actor(a);
        vm.prank(n);
        registry.register();
        registeredInModel[n] = true;
        ran["register"]++;
    }

    /// R-01: only a registered node sets its payout, never to the Broker;
    /// address(0) makes it the node itself again.
    function setNodePayout(uint256 a, uint256 p) external {
        address n = actor(a);
        uint256 k = p % (ACTORS + WALLETS + 2);
        address target = k == 0 ? address(0) : k == 1 ? address(broker) : tracked(k - 2);
        vm.prank(n);
        (bool ok, bytes memory ret) = address(registry).call(abi.encodeCall(Registry.setPayout, (target)));
        if (!registeredInModel[n]) {
            refused(ok, ret, "NOT_REGISTERED", "", "setPayout by an address not registered");
        } else if (target == address(broker)) {
            refused(ok, ret, "PAYOUT_INVALID", "", "setPayout to the Broker");
        } else if (accepted(ok, ret, "setPayout")) {
            payoutInModel[n] = target;
            ran["setPayout"]++;
        }
    }

    /// R-21: funding adds to the deposit, one earmark or both; an earmark
    /// for an address that is not a node is refused and nothing moves.
    function fundAccount(uint256 s, uint256 dep, uint256 n, uint256 mark) external {
        address p = actor(s);
        address node = actor(n);
        dep = amount(dep);
        mark = mark % 3 == 0 ? 0 : amount(mark);
        uint256 total = dep + mark;
        usdc.mint(p, total);
        minted += total;
        walletShould[p] += total;
        vm.prank(p);
        usdc.approve(address(broker), total);
        vm.prank(p);
        (bool ok, bytes memory ret) = address(broker).call(abi.encodeCall(Broker.fund, (dep, node, mark)));
        if (mark > 0 && !registeredInModel[node]) {
            refused(ok, ret, "NOT_REGISTERED", "", "earmark for an address not registered");
            return;
        }
        if (!accepted(ok, ret, "fund")) return;
        walletShould[p] -= total;
        funded += total;
        fundedBy[p] += total;
        model[p][address(0)].amount += dep;
        if (mark > 0) model[p][node].amount += mark;
        ran["fund"]++;
    }

    /// R-22: a request is at most the balance; it replaces the pending one
    /// and restarts the wait, and 0 cancels.
    function askRelease(uint256 s, uint256 n, uint256 a) external {
        address p = actor(s);
        address node = slot(n % slotCount());
        Bal storage b = model[p][node];
        uint256 k = a % 5;
        uint256 want = k == 0 ? 0 : k == 1 ? b.amount : k == 2 ? b.amount + 1 : k == 3
            ? (a >> 8) % (b.amount + 1)
            : amount(a >> 8);
        vm.prank(p);
        (bool ok, bytes memory ret) = address(broker).call(abi.encodeCall(Broker.requestRelease, (node, want)));
        if (want > b.amount) {
            refused(ok, ret, "AMOUNT_TOO_LARGE", "", "request above the balance");
            return;
        }
        if (!accepted(ok, ret, "requestRelease")) return;
        b.pending = want;
        b.readyRound = want == 0 ? 0 : clock.currentRound() + releaseWait;
        ran["requestRelease"]++;
    }

    /// R-22: from the request's round + the wait, the payer receives the
    /// pending amount and the request is cleared.
    function takeRelease(uint256 s, uint256 n) external {
        address p = actor(s);
        address node = slot(n % slotCount());
        Bal storage b = model[p][node];
        vm.prank(p);
        (bool ok, bytes memory ret) = address(broker).call(abi.encodeCall(Broker.release, (node)));
        if (b.pending == 0 || clock.currentRound() < b.readyRound) {
            refused(ok, ret, "RELEASE_NOT_READY", "", "release with none pending or too early");
            return;
        }
        if (!accepted(ok, ret, "release")) return;
        uint256 out = b.pending;
        b.amount -= out;
        (b.pending, b.readyRound) = (0, 0);
        walletShould[p] += out;
        released += out;
        releasedTo[p] += out;
        ran["release"]++;
    }

    /// A payer signs a new ticket to a node and the node redeems it. The
    /// creation round may be the current one, an earlier one inside or
    /// outside the ticket life, or one not yet started; a few tickets lose.
    function redeemNew(uint256 s, uint256 n, uint256 face, uint256 back) external {
        uint256 round = clock.currentRound();
        uint256 created;
        // Mostly inside the ticket life; 1 in 4 outside it or not started.
        uint256 b = back % 8;
        uint256 ago = b < 6 ? b % (validity + 1) : validity + b - 5;
        if (b == 7) created = round + 1;
        else created = ago >= round ? 1 : round - ago;
        Kept storage x = kept.length < KEPT ? kept.push() : kept[nonce % KEPT];
        nonce++;
        x.rand = uint256(keccak256(abi.encodePacked("rand", nonce)));
        x.paid = false;
        x.t = ITicketBroker.Ticket({
            recipient: actor(n),
            sender: actor(s),
            faceValue: faceFor(actor(s), actor(n), face),
            winProb: (back >> 8) % 16 == 0 ? 0 : type(uint256).max,
            senderNonce: nonce,
            recipientRandHash: keccak256(abi.encodePacked(x.rand)),
            auxData: abi.encodePacked(created, clock.blockHashForRound(created))
        });
        x.sig = signTicket(actorKey(s), x.t);
        submit(x);
        ran["redeemNew"]++;
    }

    /// One face in four is exactly what the payer holds for that node, or
    /// one more, so tickets drain balances that have a release pending.
    function faceFor(address payer, address node, uint256 seed) internal view returns (uint256) {
        if (seed % 4 != 0) return amount(seed >> 2);
        return model[payer][address(0)].amount + model[payer][node].amount + (seed >> 2) % 2;
    }

    /// A ticket redeemed before is redeemed again: once paid it must never
    /// pay again (R-04); refused for nothing to pay, it may pay now.
    function redeemAgain(uint256 i) external {
        if (kept.length == 0) return;
        // Half the time one of the last few tickets, so replays happen
        // while they are still inside their life.
        uint256 j = i % 2 == 0 && nonce >= 4 ? (nonce - 1 - (i >> 1) % 4) % KEPT : (i >> 1) % kept.length;
        submit(kept[j]);
        ran["redeemAgain"]++;
    }

    /// Starts the next round, after a round that may have lasted longer
    /// than its length (R-18).
    function nextRound(uint256 extra) external {
        vm.roll(block.number + roundLength + extra % 100);
        clock.initializeRound();
        ran["nextRound"]++;
    }

    // --- redemption against the rules -----------------------------------

    function submit(Kept storage x) internal {
        ITicketBroker.Ticket memory t = x.t;
        bytes memory sig = x.sig;
        uint256 rand = x.rand;
        (uint256 created, bytes32 signedHash) = abi.decode(t.auxData, (uint256, bytes32));
        uint256 round = clock.currentRound();
        bool wins = uint256(keccak256(abi.encodePacked(sig, rand))) < t.winProb;
        (uint256 fromDeposit, uint256 fromEarmark) = draws(t);

        vm.prank(t.recipient);
        (bool ok, bytes memory ret) =
            address(broker).call(abi.encodeCall(Broker.redeemWinningTicket, (t, sig, rand)));

        // Every reason the rules and the contracts' ticket checks give to
        // refuse this ticket; any one of them may be the code.
        string[7] memory why;
        uint256 count;
        if (created > round) why[count++] = "ticket creationRound does not have a block hash";
        else if (signedHash != clock.blockHashForRound(created)) why[count++] = "TICKET_INVALID";
        else if (round > created + validity) why[count++] = "TICKET_EXPIRED";
        if (x.paid) why[count++] = "TICKET_USED";
        if (!wins) why[count++] = "TICKET_INVALID";
        if (!registeredInModel[t.recipient]) why[count++] = "NOT_REGISTERED";
        if (fromDeposit + fromEarmark == 0) why[count++] = "NOTHING_TO_PAY";

        if (count > 0) {
            if (ok && x.paid) paidTwice++;
            refusedAny(ok, ret, why, count);
            return;
        }
        if (!accepted(ok, ret, "redeemWinningTicket")) return;
        settle(x, fromDeposit, fromEarmark, round);
    }

    /// R-04: min(face, deposit + that node's earmark), deposit first.
    function draws(ITicketBroker.Ticket memory t) internal view returns (uint256 fromDeposit, uint256 fromEarmark) {
        uint256 d = model[t.sender][address(0)].amount;
        uint256 e = model[t.sender][t.recipient].amount;
        fromDeposit = d < t.faceValue ? d : t.faceValue;
        uint256 rest = t.faceValue - fromDeposit;
        fromEarmark = e < rest ? e : rest;
    }

    function settle(Kept storage x, uint256 fromDeposit, uint256 fromEarmark, uint256 round) internal {
        address payer = x.t.sender;
        address node = x.t.recipient;
        draw(model[payer][address(0)], fromDeposit);
        draw(model[payer][node], fromEarmark);
        x.paid = true;
        uint256 paid = fromDeposit + fromEarmark;
        // R-03: the node's half rounds down; the rest is the network's.
        uint256 toNode = paid * nodeCut / ONE;
        address payout = payoutInModel[node] == address(0) ? node : payoutInModel[node];
        walletShould[payout] += toNode;
        paidToNodes += toNode;
        paidToNetwork += paid - toNode;
        paidFrom[payer] += paid;
        // R-04: counted in the round it is redeemed.
        feesShould[round] += paid;
        feesOfShould[round][node] += paid;
        ran["paid"]++;
        if (fromDeposit + fromEarmark < x.t.faceValue) ran["paidPartly"]++;
    }

    /// R-22: a redemption lowers a pending amount to the balance left where
    /// it is above it, and a pending amount that reaches 0 is cleared.
    function draw(Bal storage b, uint256 taken) internal {
        b.amount -= taken;
        if (b.pending > b.amount) b.pending = b.amount;
        if (b.pending == 0) b.readyRound = 0;
    }

    // --- outcomes ---------------------------------------------------------

    function accepted(bool ok, bytes memory ret, string memory what) internal returns (bool) {
        if (!ok) mismatch(string.concat(what, " refused with ", code(ret), "; the rules accept it"));
        return ok;
    }

    function refused(bool ok, bytes memory ret, string memory a, string memory b, string memory what) internal {
        string[7] memory why;
        why[0] = a;
        uint256 count = 1;
        if (bytes(b).length > 0) why[count++] = b;
        if (!ok) {
            refusedAny(ok, ret, why, count);
        } else {
            mismatch(string.concat(what, " accepted; the rules refuse it with ", a));
        }
    }

    function refusedAny(bool ok, bytes memory ret, string[7] memory why, uint256 count) internal {
        if (ok) {
            mismatch(string.concat("ticket paid; the rules refuse it with ", why[0]));
            return;
        }
        string memory got = code(ret);
        for (uint256 i = 0; i < count; i++) {
            if (eq(got, why[i])) {
                ran[string.concat("refused ", got)]++;
                return;
            }
        }
        mismatch(string.concat("refused with ", got, "; the rules refuse with ", why[0]));
    }

    function mismatch(string memory what) internal {
        if (mismatches == 0) firstMismatch = what;
        mismatches++;
    }
}
