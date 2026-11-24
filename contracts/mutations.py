#!/usr/bin/env python3
"""Seeded errors: each mutation breaks the contracts in one known way, and a
check must turn red on it. contracts/qualify runs this.

Every mutation is made in a throwaway copy of contracts/ and vectors/,
never in this checkout, and each file it changes is put back before the
next one; the copy is removed on exit, interrupt or termination.

Each mutation names the check that must catch it:
  check       the whole fast check (contracts/check)
  <regex>     the fast check's tests matching it (forge test --match-test)
  qualify:<contract>  the heavy runs of that test contract (test-qualify/,
              the qualify profile)
A mutation marked fast_green must also leave the whole fast check green:
it is a defect only the heavy runs find.

Usage: mutations.py [name-substring ...]
Exit 0 every mutation caught as expected; 1 a check that ran missed one,
or one no longer applies; 2 otherwise a check or the formatter could not
run for one (NOTRUN), or nothing could run.
"""
import os, pathlib, re, shutil, signal, subprocess, sys, tempfile, time

B, R, C = "src/money/Broker.sol", "src/authority/Registry.sol", "src/clock/RoundClock.sol"
S = "src/scores/Scores.sol"
CL, RM, SL = "src/money/Claims.sol", "src/math/RewardMath.sol", "src/money/StakeLedger.sol"
# The Scores tests read rulebook v5's examples.
V5 = "../vectors/october-main-text-v5.json"
P = "src/authority/Parameters.sol"
TP, PT = "src/money/TestPool.sol", "test/Parameters.t.sol"
RT = "test/Rotation.t.sol"
LPT, USD = "src/money/TestLPT.sol", "src/money/MockUSDC.sol"

# name, [(file, old, new)], catching check[, fast_green]
M = [
 # name, [(file, old, new)], command ('check' or forge --match-test regex)
 ("abi: extra public function on Broker", [(B, "    // --- internals", "    function extra() external {}\n\n    // --- internals")], "check"),
 ("abi: extra error on Registry, never reached", [(R, "        if (payout_ == broker) revert PAYOUT_INVALID();", "        if (payout_ == broker) revert PAYOUT_INVALID();\n        if (payout_ == address(1)) revert EXTRA();"), (R, "contract Registry is IRegistry {", "error EXTRA();\n\ncontract Registry is IRegistry {")], "check"),
 ("abi: interface function dropped from Registry (compiler)", [(R, "    function isActiveTranscoder(address a) external view returns (bool) {\n        return registered[a];\n    }\n", "")], "check"),
 ("release wait 2", [(B, "RELEASE_WAIT = 3", "RELEASE_WAIT = 2")], "test_ledger_release"),
 ("redemption does not clamp pending", [(B, "        if (b.pending < left) {", "        if (true) {")], "test_ledger_release"),
 ("node shown whole deposit", [(B, "Sender(d.amount - d.pending, 0)", "Sender(d.amount, 0)")], "test_ledger_release"),
 ("claimable ignores pending", [(B, "        return e.amount - e.pending;", "        return e.amount;")], "test_ledger_release"),
 ("payout ignored at redemption", [(B, "registry.payoutOf(t.recipient)", "t.recipient")], "test_ledger_payout"),
 ("setPayout stores nothing", [(R, "        payout[node] = payout_;\n", "")], "test_ledger_payout"),
 ("release does not lower the balance", [(B, "_set(msg.sender, node, b, b.amount - amount, 0, 0);", "_set(msg.sender, node, b, b.amount, 0, 0);")], "test_ledger_release"),
 ("cap of 100 nodes on earmark draws", [
    (R, "    mapping(address => bool) private registered;", "    mapping(address => bool) private registered;\n    uint256 public count;"),
    (R, "        registered[msg.sender] = true;", "        registered[msg.sender] = true;\n        count++;"),
    (B, "        uint256 fromEarmark = e.amount;", "        uint256 fromEarmark = Counted(address(registry)).count() > 100 ? 0 : e.amount;"),
    (B, "contract Broker is INetworkShareTicketBroker {", "interface Counted { function count() external view returns (uint256); }\n\ncontract Broker is INetworkShareTicketBroker {")], "test_ledger_reserve"),
 ("round started one block early", [(C, "block.number < startBlock + ROUND_LENGTH", "block.number < startBlock + ROUND_LENGTH - 1")], "test_round_starts_after_round_length"),
 ("round counted from blocks (skips rounds)", [(C, "        _start(round + 1);", "        _start(round + (block.number - startBlock) / ROUND_LENGTH);")], "test_ticket_signed_in_a_long_round_is_paid"),
 ("zero-address check removed", [(B, "        if (t.recipient == address(0) || t.sender == address(0)) revert TICKET_INVALID();\n", "")], "test_zero_"),
 ("zero recipient not checked", [(B, "t.recipient == address(0) || ", "")], "test_zero_recipient"),
 ("zero sender not checked", [(B, " || t.sender == address(0)", "")], "test_zero_sender"),
 ("auxData length not checked", [(B, "        if (t.auxData.length != 64) revert TICKET_INVALID();\n", "")], "test_wrong_aux_data_length"),
 ("block hash not checked", [(B, "        if (creationHash != roundHash) revert TICKET_INVALID();\n", "")], "test_wrong_block_hash"),
 ("random value not checked", [(B, "keccak256(abi.encodePacked(recipientRand)) != t.recipientRandHash", "false")], "test_wrong_random_value"),
 ("signature not checked", [(B, "        if (_signer(ticketHash, sig) != t.sender) revert TICKET_INVALID();\n", "")], "test_wrong_signature"),
 ("win test removed", [(B, "uint256(keccak256(abi.encodePacked(sig, recipientRand))) >= t.winProb", "false")], "test_losing_ticket"),
 ("kept revert text changed", [(B, '"ticket creationRound does not have a block hash"', '"no block hash"')], "test_round_not_started"),
 ("setPayout by unregistered accepted", [(R, "        if (!registered[node]) revert NOT_REGISTERED();\n        if (payout_ == broker)", "        if (payout_ == broker)")], "test_set_payout_by_unregistered"),
 ("payout to the Broker accepted", [(R, "        if (payout_ == broker) revert PAYOUT_INVALID();\n", "")], "test_payout_to_the_broker"),
 ("payout 0 kept as 0", [(R, "        return p == address(0) ? node : p;", "        return p;")], "test_payout_zero"),
 ("registering again resets the payout", [(R, "        if (registered[msg.sender]) return;", "        payout[msg.sender] = address(0);\n        if (registered[msg.sender]) return;")], "test_registering_again"),
 ("refused earmark still credits the deposit", [(B, "            if (!registry.isRegistered(node)) revert NOT_REGISTERED();\n            Balance storage e", "            if (!registry.isRegistered(node)) { earmark_ = 0; node = address(0); } else {\n            Balance storage e"), (B, "            _set(msg.sender, node, e, e.amount + earmark_, e.pending, e.readyRound);\n        }", "            _set(msg.sender, node, e, e.amount + earmark_, e.pending, e.readyRound);\n        }}")], "test_refused_fund"),
 ("new request keeps the old wait", [(B, "uint256 readyRound = amount == 0 ? 0 : clock.currentRound() + RELEASE_WAIT;", "uint256 readyRound = amount == 0 ? 0 : (b.readyRound != 0 ? b.readyRound : clock.currentRound() + RELEASE_WAIT);")], "test_new_request_restarts"),
 ("request of 0 does not cancel", [(B, "        if (amount > b.amount) revert AMOUNT_TOO_LARGE();\n", "        if (amount > b.amount) revert AMOUNT_TOO_LARGE();\n        if (amount == 0) return;\n")], "test_request_of_zero"),
 ("release does not clear its request", [(B, "_set(msg.sender, node, b, b.amount - amount, 0, 0);", "_set(msg.sender, node, b, b.amount - amount, amount > b.amount - amount ? b.amount - amount : amount, b.readyRound);")], "test_release_clears"),
 ("request above balance accepted", [(B, "        if (amount > b.amount) revert AMOUNT_TOO_LARGE();\n", "")], "test_request_above"),
 ("request of exactly the balance refused", [(B, "if (amount > b.amount) revert AMOUNT_TOO_LARGE();", "if (amount >= b.amount) revert AMOUNT_TOO_LARGE();")], "test_request_above"),
 ("release with none pending accepted", [(B, "if (amount == 0 || clock.currentRound() < b.readyRound)", "if (clock.currentRound() < b.readyRound)")], "test_release_with_none_pending"),
 ("earmark pending not clamped", [(B, "        _draw(t.sender, t.recipient, e, fromEarmark);", "        _set(t.sender, t.recipient, e, e.amount - fromEarmark, e.pending > e.amount - fromEarmark ? e.pending : e.pending, e.readyRound);")], "test_redemption_lowers"),
 ("fundsRemaining ignores pending", [(B, "shownEarmarks[payer] = shownEarmarks[payer] - (b.amount - b.pending) + (amount - pending);", "shownEarmarks[payer] = shownEarmarks[payer] - b.amount + amount;")], "test_signer_is_shown"),
 ("clamp keeps pending below left wrongly lowered", [(B, "            _set(payer, node, b, left, b.pending, b.readyRound);", "            _set(payer, node, b, left, 0, 0);")], "test_redemption_keeps"),
 ("node half in one product (overflows on large amounts; a fast test also catches it)", [(B, "        return paid / ONE * cut + paid % ONE * cut / ONE;", "        return paid * cut / ONE;")], "qualify:AmountsFuzz"),
 # --- defects only the heavy runs catch: the fast check stays green -----
 ("partly paid ticket not marked used", [(B, "        usedTickets[ticketHash] = true;", "        usedTickets[ticketHash] = paid == t.faceValue;")], "qualify:MoneyInvariants", True),
 ("used ticket paid again in a later round", [(B, "        if (usedTickets[ticketHash]) revert TICKET_USED();", "        if (usedTickets[ticketHash] && round == creationRound) revert TICKET_USED();")], "qualify:MoneyInvariants", True),
 ("drawn to 0 keeps its ready round", [(B, "_set(payer, node, b, left, left, left == 0 ? 0 : b.readyRound);", "_set(payer, node, b, left, left, b.readyRound);")], "qualify:MoneyInvariants", True),
 ("top-up cancels a pending deposit release", [(B, "        _set(msg.sender, address(0), d, d.amount + deposit_, d.pending, d.readyRound);", "        _set(msg.sender, address(0), d, d.amount + deposit_, 0, 0);")], "qualify:MoneyInvariants", True),

 # Scores: each rule of R-11 and R-13 a case or test pins.
 ("scores: an example's expected score changed", [(V5, '"expected": {"score": "166666"}', '"expected": {"score": "166667"}')], "test_score_even_floor"),
 ("scores: abi: extra public function", [(S, "    // --- views", "    function size() external pure returns (uint256) {\n        return N;\n    }\n\n    // --- views")], "check"),
 ("scores: no vote counts as 0", [(S, "v.seated == nodes[m[k]].seated ? v.score : MAX_SCORE", "v.seated == nodes[m[k]].seated ? v.score : 0")], "test_score_none"),
 ("scores: even count takes the upper middle", [(S, "        return (s[k / 2 - 1] + s[k / 2]) / 2;", "        return s[k / 2];")], "test_score_even"),
 ("scores: even count rounds up", [(S, "        return (s[k / 2 - 1] + s[k / 2]) / 2;", "        return (s[k / 2 - 1] + s[k / 2] + 1) / 2;")], "test_score_even_floor"),
 ("scores: no members scores 0", [(S, "        if (k == 0) return MAX_SCORE;", "        if (k == 0) return 0;")], "test_score_empty"),
 ("scores: of equal smallest, the one seated first leaves", [(S, "                if (s <= smallest) {", "                if (s < smallest) {")], "test_validators_tie"),
 ("scores: a tie displaces the incumbent", [(S, "            if (stake <= smallest) return false;", "            if (stake < smallest) return false;")], "test_a_tie_keeps_the_incumbent"),
 ("scores: zero stake seated in a free seat", [(S, "        if (stake == 0) return false;\n", "")], "test_calls_that_change_nothing"),
 ("scores: a change counts from the current round", [(S, "        if (n > 0 && history[n - 1].fromRound <= r) return history[n - 1].members;", "        if (n > 0) return history[n - 1].members;")], "test_validators_opt_out_vote"),
 ("scores: membersAt reads the latest list", [(S, "        uint256 lo = 0;\n        uint256 hi = history.length;", "        uint256 lo = history.length;\n        uint256 hi = history.length;")], "test_validators_seats"),
 ("scores: a started round's list is rewritten", [(S, "if (n == 0 || history[n - 1].fromRound != r + 1) {", "if (n == 0) {")], "test_validators_seats"),
 ("scores: votes never lapse", [(S, "v.seated == nodes[m[k]].seated ? v.score : MAX_SCORE", "v.seated != 0 ? v.score : MAX_SCORE")], "test_validators_seats"),
 ("scores: a return within the round is seated anew", [(S, "        if (!_has(_current(r), node)) {", "        if (true) {")], "test_validators_return_"),
 ("scores: seating order is the call order", [(S, "for (; i > 0 && nodes[next[i - 1]].seated > number; i--) {", "for (; i > 0 && false; i--) {")], "test_validators_return_order"),
 ("scores: a displaced member may not vote in its last round", [(S, "        if (!_has(_current(r), node)) revert NOT_VALIDATOR();", "        if (!_has(_current(r), node) || !_has(_latest(), node)) revert NOT_VALIDATOR();")], "test_displaced_member_vote"),
 ("scores: displacement drops the votes at once", [(S, "            displaced = next[at];", "            displaced = next[at];\n            nodes[displaced].seated = 0;")], "test_displaced_member_vote"),
 ("scores: unseat ignores stake", [(S, "        if (!_has(_latest(), node) || ledger.stakeAt(node, r) != 0) return;", "        if (!_has(_latest(), node)) return;")], "test_calls_that_change_nothing"),
 ("scores: unseat does nothing", [(S, "        _leave(node, r);\n        emit Unseated(node, r + 1);", "        emit Unseated(node, r + 1);")], "test_validators_zero_stake"),
 ("scores: opt-out keeps the seat", [(S, "        nodes[node].optedIn = false;\n        uint256 r = clock.currentRound();\n        _leave(node, r);", "        nodes[node].optedIn = false;\n        uint256 r = clock.currentRound();")], "test_validators_seats"),
 ("scores: opting in again seats", [(S, "        if (nodes[node].optedIn) return;\n        nodes[node].optedIn = true;", "        if (nodes[node].optedIn) { _seat(node, clock.currentRound()); return; }\n        nodes[node].optedIn = true;")], "test_calls_that_change_nothing"),
 ("scores: opt-in checks registration before authority", [(S, "        if (!registry.mayActFor(msg.sender, node)) revert NOT_AUTHORIZED();\n        if (!registry.isRegistered(node)) revert NOT_REGISTERED();", "        if (!registry.isRegistered(node)) revert NOT_REGISTERED();\n        if (!registry.mayActFor(msg.sender, node)) revert NOT_AUTHORIZED();")], "test_refusal_order_validators"),
 ("scores: opt-in by an unregistered node accepted", [(S, "        if (!registry.isRegistered(node)) revert NOT_REGISTERED();\n", "")], "test_refusal_order_validators"),
 ("scores: seat checks stake before opt-in", [(S, "        if (!nodes[node].optedIn) revert NOT_OPTED_IN();\n        if (!_seat(node, clock.currentRound())) revert STAKE_TOO_LOW();", "        if (ledger.stakeAt(node, clock.currentRound()) == 0) revert STAKE_TOO_LOW();\n        if (!nodes[node].optedIn) revert NOT_OPTED_IN();\n        if (!_seat(node, clock.currentRound())) revert STAKE_TOO_LOW();")], "test_refusal_order_validators"),
 ("scores: vote range checked before membership", [(S, "        if (!_has(_current(r), node)) revert NOT_VALIDATOR();\n        if (score > MAX_SCORE) revert SCORE_OUT_OF_RANGE();", "        if (score > MAX_SCORE) revert SCORE_OUT_OF_RANGE();\n        if (!_has(_current(r), node)) revert NOT_VALIDATOR();")], "test_refusal_order_validators"),
 ("scores: vote above 10^6 accepted", [(S, "        if (score > MAX_SCORE) revert SCORE_OUT_OF_RANGE();\n", "")], "test_validators_seats"),
 ("scores: anyone votes for a node", [(S, "    function vote(address node, address target, uint256 score) external {\n        if (!registry.mayActFor(msg.sender, node)) revert NOT_AUTHORIZED();", "    function vote(address node, address target, uint256 score) external {")], "test_calls_for_a_node"),
 ("scores: anyone opts a node out", [(S, "    function optOut(address node) external {\n        if (!registry.mayActFor(msg.sender, node)) revert NOT_AUTHORIZED();", "    function optOut(address node) external {")], "test_calls_for_a_node"),
 ("scores: a vote by a non-member accepted", [(S, "        if (!_has(_current(r), node)) revert NOT_VALIDATOR();\n", "")], "test_validators_seats"),
 ("scores: Seated names no displaced member", [(S, "        emit Seated(node, r + 1, displaced);", "        emit Seated(node, r + 1, address(0));")], "test_displaced_member_vote"),
 ("scores: seat walks every opted-in node (R-23)", [
    (S, "    uint64 private lastSeated;", "    uint64 private lastSeated;\n    address[] private everyOptedIn;"),
    (S, "        nodes[node].optedIn = true;\n", "        nodes[node].optedIn = true;\n        everyOptedIn.push(node);\n"),
    (S, "        if (_has(next, node)) return true;", "        if (_has(next, node)) return true;\n        uint256 seen = 0;\n        for (uint256 q = 0; q < everyOptedIn.length; q++) {\n            if (everyOptedIn[q] == node) seen++;\n        }\n        if (seen > 1) return false;")], "test_gas_does_not_grow"),
 ("scores: scoreOf reads one slot per opted-in node (R-23)", [
    (S, "    uint64 private lastSeated;", "    uint64 private lastSeated;\n    address[] private everyOptedIn;"),
    (S, "        nodes[node].optedIn = true;\n", "        nodes[node].optedIn = true;\n        everyOptedIn.push(node);\n"),
    (S, "        if (k == 0) return MAX_SCORE;", "        for (uint256 q = 0; q < everyOptedIn.length; q++) {\n            if (everyOptedIn[q] == node) return MAX_SCORE;\n        }\n        if (k == 0) return MAX_SCORE;")], "test_gas_does_not_grow"),

 # Validator pay: R-10, R-14 and B1 to B5 in Claims.
 ("pay: members read when the mint runs, not as the round started", [(CL, "validators.membersAt(round)", "validators.membersAt(clock.currentRound())")], "test_a_node_seated_after_the_round"),
 ("pay: stake read when the mint runs", [(CL, "stakes[i] = ledger.stakeAt(members[i], round);", "stakes[i] = ledger.stakeAt(members[i], clock.currentRound());")], "test_ledger_mid_round"),
 ("pay: cut read when the mint runs", [(CL, "RewardMath.cutOf(paid, ledger.cutAt(m, round))", "RewardMath.cutOf(paid, ledger.cutAt(m, clock.currentRound()))")], "test_ledger_mid_round"),
 ("pay: cut minted to the member, not its payout", [(CL, "        if (cut > 0) lpt.mint(payout, cut);\n    }", "        if (cut > 0) lpt.mint(m, cut);\n    }")], "test_ledger_validator_pot"),
 ("pay: two divisions, from the validator amount", [(RM, "return (c * share * stake) / (ONE * totalStake);", "return (c * share / ONE) * stake / totalStake;")], "test_ledger_mid_round"),
 ("pay: equal shares, not by stake", [(CL, "_payMember(members[i], round, c, shares[1], stakes[i], totalStake)", "_payMember(members[i], round, c, shares[1], totalStake == 0 ? 0 : 1, members.length)")], "test_pay_weighted"),
 ("pay: the division's remainder lost", [(CL, "lpt.mint(treasury, toTreasury + (validatorAmount - paidOut));", "lpt.mint(treasury, toTreasury);")], "test_pay_thirds"),
 ("pay: validator amount also minted whole to the treasury", [(CL, "lpt.mint(treasury, toTreasury + (validatorAmount - paidOut));", "lpt.mint(treasury, toTreasury + validatorAmount);")], "test_pay_weighted"),
 ("pay: no stake among the members divides by 0", [(RM, "        if (totalStake == 0) return 0;\n", "")], "test_pay_no_stake"),
 ("pay: pots not minted to Claims", [(CL, "        if (pots > 0) lpt.mint(address(this), pots);\n", "")], "test_pay_weighted"),
 ("pay: pot recorded as the whole pay", [(CL, "(v.paid, v.pot) = (paid, pot);", "(v.paid, v.pot) = (paid, paid);")], "test_pay_split"),
 ("pay: no ValidatorPaid for a member paid 0", [(CL, "        emit IClaims.ValidatorPaid(round, m, payout, paid, cut, pot);", "        if (paid > 0) emit IClaims.ValidatorPaid(round, m, payout, paid, cut, pot);")], "test_pay_no_stake"),
 ("pay: validatorPayOf gives the pot as the cut", [(CL, "return (v.paid, v.paid - v.pot, v.pot);", "return (v.paid, v.pot, v.pot);")], "test_pay_split"),
 ("vparts: paid from the claim's pot", [(CL, "amount += _part(validatorPays[node][round].pot, msg.sender, node, round);", "amount += _part(entitlements[node][round].pot, msg.sender, node, round);")], "test_both_parts_of_a_round"),
 ("vparts: one flag for both kinds of part", [(CL, "            if (validatorPartWithdrawn[msg.sender][node][round]) revert IClaims.ALREADY_CLAIMED();\n            validatorPartWithdrawn[msg.sender][node][round] = true;", "            if (partWithdrawn[msg.sender][node][round]) revert IClaims.ALREADY_CLAIMED();\n            partWithdrawn[msg.sender][node][round] = true;")], "test_both_parts_of_a_round"),
 ("vparts: not marked withdrawn", [(CL, "            validatorPartWithdrawn[msg.sender][node][round] = true;\n", "")], "test_pay_split"),
 ("vparts: today's bond and stake", [(CL, "amount += _part(validatorPays[node][round].pot, msg.sender, node, round);", "amount += _part(validatorPays[node][round].pot, msg.sender, node, clock.currentRound());")], "test_ledger_validator_pot"),
 ("vparts: taken before the round's shares are minted", [(CL, "            if (!minted[round]) revert IClaims.NOT_CLAIMED();\n", "")], "test_ledger_validator_pot"),
 ("vparts: a list naming round 0 accepted", [(CL, "    function withdrawValidatorParts(address node, uint256[] calldata rounds) external {\n        _noRoundZero(rounds);\n", "    function withdrawValidatorParts(address node, uint256[] calldata rounds) external {\n")], "test_ledger_round_zero"),
 ("vparts: validatorPartOf shows 0", [(CL, "        amount = _part(validatorPays[node][round].pot, holder, node, round);", "        amount = 0;")], "test_pay_split"),
 ("abi: Claims drops validatorPartOf (compiler)", [(CL, "    /// 0 until `round`'s shares are minted: the pot is fixed by the mint.\n    function validatorPartOf(address holder, address node, uint256 round)\n        external\n        view\n        returns (uint256 amount, bool withdrawn)\n    {\n        amount = _part(validatorPays[node][round].pot, holder, node, round);\n        withdrawn = validatorPartWithdrawn[holder][node][round];\n    }\n", "")], "check"),
 ("abi: extra public function on Claims", [(CL, "    // --- views ----", "    function validatorsAddress() external view returns (address) {\n        return address(validators);\n    }\n\n    // --- views ----")], "check"),
 ("pay: the mint walks every opted-in node (R-23)", [
    (S, "    uint64 private lastSeated;", "    uint64 private lastSeated;\n    address[] private everyOptedIn;"),
    (S, "        nodes[node].optedIn = true;\n", "        nodes[node].optedIn = true;\n        everyOptedIn.push(node);\n"),
    (S, "        return _at(round);\n", "        for (uint256 q = 0; q < everyOptedIn.length; q++) {\n            if (everyOptedIn[q] == address(1)) revert();\n        }\n        return _at(round);\n")], "test_mint_gas_does_not_grow"),
 ("pay: the mint reads one slot per holder (R-23)", [
    (SL, "    Point[] private totalPoints;", "    Point[] private totalPoints;\n    address[] private everyHolder;"),
    (SL, "        if (!registry.isRegistered(node)) revert NOT_REGISTERED();\n        uint256 now_ = clock.currentRound();\n        _add(stayingPoints[msg.sender][node], now_, amount);", "        if (!registry.isRegistered(node)) revert NOT_REGISTERED();\n        uint256 now_ = clock.currentRound();\n        everyHolder.push(msg.sender);\n        _add(stayingPoints[msg.sender][node], now_, amount);"),
    (SL, "        return _at(stakePoints[node], round);", "        for (uint256 q = 0; q < everyHolder.length; q++) {\n            if (everyHolder[q] == address(1)) revert();\n        }\n        return _at(stakePoints[node], round);")], "test_mint_gas_does_not_grow"),
 # Parameters: the values of a round, not the values
 # now; the ceiling after a change, exact; the bound on a drop; and each
 # reader taking its group from Parameters, for its round.
 ("params: an overwriting setter replaces a change that counts", [(P, "        if (n > 0 && list[n - 1].countsFrom > now_) {", "        if (n > 0) {")], "test_parameters_"),
 ("params: the values now, not the round's", [(P, "            if (list[mid].countsFrom <= round) lo = mid + 1;", "            if (list[mid].countsFrom <= type(uint256).max) lo = mid + 1;")], "test_parameters_"),
 ("params: a withdrawn change still counts", [(P, "        list.pop();\n", "")], "test_parameters_|test_validators_set_size_withdrawn"),
 ("params: drops above 840 accepted", [(P, "v.length == 2 && v[0] <= MOST && v[1] != 0", "v.length == 2 && v[1] != 0")], "test_parameters_drop"),
 ("params: a raised half-life accepted", [(P, "values.length == 2 && values[1] > inForce[1]", "values.length == 2 && false")], "test_parameters_half_life"),
 ("params: the new half-life starts a round late", [(P, "RewardMath.position(inForce[0], inForce[1], inForce[2], countsFrom - 1) + values[0];", "RewardMath.position(inForce[0], inForce[1], inForce[2], countsFrom) + values[0];")], "test_parameters_half_life|test_half_life_420"),
 ("params: pending still shown in the round the change counts from", [(P, "        if (n > 0 && list[n - 1].countsFrom > clock.currentRound()) {", "        if (n > 0 && list[n - 1].countsFrom >= clock.currentRound()) {")], "test_pending_ends_in_the_round_the_change_counts_from"),
 ("params: the emission's count of values not checked", [(P, "if (group == EMISSION) return v.length == 3 && v[0] <= ONE", "if (group == EMISSION) return v.length >= 2 && v[0] <= ONE")], "test_a_wrong_count_of_values_is_out_of_range"),
 ("params: the schedule's count of values not checked", [(P, "return v.length == 2 && v[0] <= MOST && v[1] != 0", "return v.length >= 2 && v[0] <= MOST && v[1] != 0")], "test_a_wrong_count_of_values_is_out_of_range"),
 ("params: a score source word above 160 bits accepted", [(P, "return v.length == 1 && v[0] <= type(uint160).max && address(uint160(v[0])).code.length > 0;", "return v.length == 1 && address(uint160(v[0])).code.length > 0;")], "test_an_address_word_above_160_bits_is_out_of_range"),
 ("params: a pool word above 160 bits accepted", [(P, "return v.length == 4 && v[0] <= type(uint160).max && v[1] >= 1800", "return v.length == 4 && v[1] >= 1800")], "test_an_address_word_above_160_bits_is_out_of_range"),
 ("params: valuesAt answers a number with no group", [(P, "    function valuesAt(uint256 group, uint256 round) external view returns (uint256[] memory) {\n        if (group == 0 || group > BUYBACK_EXECUTOR) revert NOT_ADJUSTABLE();\n", "    function valuesAt(uint256 group, uint256 round) external view returns (uint256[] memory) {\n")], "test_the_views_refuse_a_number_with_no_group"),
 # the pool's runtime has no SELFDESTRUCT and no DELEGATECALL.
 ("pool: TestPool given a function that delegates", [(TP, "    constructor(address tokenA, address tokenB) {", "    function relay(address t) external returns (bool ok) {\n        (ok,) = t.delegatecall(\"\");\n    }\n\n    constructor(address tokenA, address tokenB) {")], "test_the_pool_runtime_has_no_selfdestruct_or_delegatecall"),
 ("pool: TestPool given a function that destroys it", [(TP, "    constructor(address tokenA, address tokenB) {", "    function close() external {\n        selfdestruct(payable(msg.sender));\n    }\n\n    constructor(address tokenA, address tokenB) {")], "test_the_pool_runtime_has_no_selfdestruct_or_delegatecall"),
 ("ceiling: one factor 2^-64 low", [(RM, "K9 = 0xa7c92571bd511e01b2", "K9 = 0xa7c92571bd511e00b2")], "check"),
 ("position: the half-life ignored", [(RM, "        return p + (round - s) * (HALF_LIFE / h);", "        return p + (round - s);")], "test_ledger_authority|test_parameters_half_life"),
 ("claims: the schedule now, not the earning round's", [(CL, "        uint256[] memory s = params.valuesAt(SCHEDULE, round);", "        uint256[] memory s = params.valuesAt(SCHEDULE, clock.currentRound());")], "test_ledger_delays"),
 ("claims: the emission shares now, not the earning round's", [(CL, "                params.valuesAt(EMISSION, round)[0],", "                params.valuesAt(EMISSION, clock.currentRound())[0],")], "test_ledger_authority"),
 ("claims: the validator share fixed at 1%", [(CL, "uint256 validatorAmount = RewardMath.amountOf(c, shares[1]);", "uint256 validatorAmount = RewardMath.amountOf(c, 10_000);"), (CL, "_payMember(members[i], round, c, shares[1], stakes[i], totalStake);", "_payMember(members[i], round, c, 10_000, stakes[i], totalStake);")], "test_validator_share_change"),
 ("claims: the treasury share fixed at 5%", [(CL, "uint256 toTreasury = RewardMath.amountOf(c, shares[2]);", "uint256 toTreasury = RewardMath.amountOf(c, 50_000);")], "test_validator_share_change"),
 ("claims: the round's validator and treasury shares now, not the earning round's", [(CL, "uint256[] memory shares = params.valuesAt(EMISSION, round);", "uint256[] memory shares = params.valuesAt(EMISSION, clock.currentRound());")], "test_validator_share_change"),
 ("claims: the reward delay fixed at 7", [(CL, "        uint256 delay = params.valuesAt(REWARD_DELAY, round)[0];", "        uint256 delay = 7;")], "test_ledger_delays"),
 ("claims: the deployment's score source, always", [(CL, "params.valuesAt(SCORE_SOURCE, now_)[0]", "params.valuesAt(SCORE_SOURCE, 0)[0]")], "test_ledger_score_source"),
 ("ledger: the unbonding wait fixed at 21", [(SL, "        return params.valuesAt(UNBONDING, now_)[0];", "        return 21;")], "test_ledger_delays"),
 ("scores: the set size fixed at 3", [(S, "        if (n >= params.valuesAt(VALIDATOR_SET_SIZE, r)[0]) {", "        if (n >= 3) {")], "test_validators_set_size"),
 ("params: a buy reads group 9 now, not for its round", [(P, "        uint256[] memory b = _at(BUYBACK_VALUES, round);", "        uint256[] memory b = _at(BUYBACK_VALUES, clock.currentRound());")], "test_a_buy_reads_the_values_of_its_round"),
 # The executor is a parameter: the draw answers to the round's
 # executor, and a pool is put to the pending executor when one is pending.
 ("broker: the deployment's executor draws, not the round's", [(B, "params.valuesAt(BUYBACK_EXECUTOR, clock.currentRound())[0]", "params.valuesAt(BUYBACK_EXECUTOR, 0)[0]")], "test_the_draw_follows_the_executor_of_its_round"),
 ("params: the pool is put to the executor in force while one is pending", [(P, "n > 0 && list[n - 1].countsFrom > round ? list[n - 1].values : _at(BUYBACK_EXECUTOR, round)", "_at(BUYBACK_EXECUTOR, round)")], "test_parameters_executor"),
 ("broker: the node cut fixed at a half", [(B, "        uint256 cut = params.valuesAt(FEE, round)[0];", "        uint256 cut = 500_000;")], "test_ledger_authority|test_ledger_delays|test_parameters_"),
 # Key rotation (R-26): the old key refused everywhere,
 # one proposal slot per node, holder funds only the caller's own, and the
 # six lists read from the compiler's ABI.
 ("rotation: setPayout without the authority check", [(R, '    function setPayout(address node, address payout_) external {\n        if (!mayActFor(msg.sender, node)) revert NOT_AUTHORIZED();', '    function setPayout(address node, address payout_) external {')], "test_ledger_rotation|test_validators_rotation|test_refusal_order_set_payout"),
 ("rotation: setPayout asks the node's own address", [(R, '    function setPayout(address node, address payout_) external {\n        if (!mayActFor(msg.sender, node)) revert NOT_AUTHORIZED();', '    function setPayout(address node, address payout_) external {\n        if (msg.sender != node) revert NOT_AUTHORIZED();')], "test_ledger_rotation|test_validators_rotation|test_refusal_order_set_payout"),
 ("rotation: setOperator without the authority check", [(R, '    function setOperator(address node, address next) external {\n        if (!mayActFor(msg.sender, node)) revert NOT_AUTHORIZED();', '    function setOperator(address node, address next) external {')], "test_ledger_rotation|test_validators_rotation"),
 ("rotation: setOperator asks the node's own address", [(R, '    function setOperator(address node, address next) external {\n        if (!mayActFor(msg.sender, node)) revert NOT_AUTHORIZED();', '    function setOperator(address node, address next) external {\n        if (msg.sender != node) revert NOT_AUTHORIZED();')], "test_ledger_rotation|test_validators_rotation"),
 ("rotation: acceptOperator does not empty the slot", [(R, "        delete proposed[node];\n", "")], "test_ledger_rotation"),
 ("rotation: acceptOperator by any caller while a proposal waits", [(R, "if (slot == address(0) || msg.sender != slot) revert NOT_AUTHORIZED();", "if (slot == address(0)) revert NOT_AUTHORIZED();")], "test_ledger_rotation"),
 ("rotation: an empty slot accepts its caller", [(R, "if (slot == address(0) || msg.sender != slot) revert NOT_AUTHORIZED();", "if (slot != address(0) && msg.sender != slot) revert NOT_AUTHORIZED();")], "test_ledger_rotation"),
 ("rotation: proposing the current key leaves the slot", [(R, "address slot = next == operatorOf(node) ? address(0) : next;", "address slot = next == operatorOf(node) ? proposed[node] : next;")], "test_ledger_rotation|test_proposal_events"),
 ("rotation: a new proposal does not replace the slot", [(R, "        proposed[node] = slot;\n", "        if (proposed[node] == address(0) || slot == address(0)) proposed[node] = slot;\n")], "test_ledger_rotation"),
 ("rotation: mayActFor asks the node's own address", [(R, "        return caller == operatorOf(node);", "        return caller == node;")], "test_ledger_rotation|test_validators_rotation|test_refusal_order_set_payout"),
 ("rotation: mayActFor also lets the node's own address act", [(R, "        return caller == operatorOf(node);", "        return caller == operatorOf(node) || caller == node;")], "test_ledger_rotation|test_validators_rotation"),
 ("rotation: mayActFor also lets the previous key act", [
    (R, "    mapping(address => address) private proposed;", "    mapping(address => address) private proposed;\n    mapping(address => address) private previous;"),
    (R, "        delete proposed[node];\n", "        delete proposed[node];\n        previous[node] = operatorOf(node);\n"),
    (R, "        return caller == operatorOf(node);", "        return caller == operatorOf(node) || caller == previous[node];")], "test_ledger_rotation|test_validators_rotation|test_refusal_order_set_payout"),
 ("rotation: claim without mayActFor", [(CL, "        if (!registry.mayActFor(msg.sender, node)) revert IClaims.NOT_AUTHORIZED();\n", "")], "test_ledger_rotation|test_validators_rotation"),
 ("rotation: setRewardCut without mayActFor", [(SL, "    function setRewardCut(address node, uint256 cut) external {\n        if (!registry.mayActFor(msg.sender, node)) revert NOT_AUTHORIZED();", "    function setRewardCut(address node, uint256 cut) external {")], "test_ledger_rotation|test_validators_rotation"),
 ("rotation: optIn without mayActFor", [(S, "    function optIn(address node) external {\n        if (!registry.mayActFor(msg.sender, node)) revert NOT_AUTHORIZED();", "    function optIn(address node) external {")], "test_ledger_rotation|test_validators_rotation"),
 ("rotation: optOut without mayActFor", [(S, "    function optOut(address node) external {\n        if (!registry.mayActFor(msg.sender, node)) revert NOT_AUTHORIZED();", "    function optOut(address node) external {")], "test_ledger_rotation|test_validators_rotation"),
 ("rotation: vote without mayActFor", [(S, "    function vote(address node, address target, uint256 score) external {\n        if (!registry.mayActFor(msg.sender, node)) revert NOT_AUTHORIZED();", "    function vote(address node, address target, uint256 score) external {")], "test_ledger_rotation|test_validators_rotation"),
 ("rotation: the operator key unbonds the node's own bond", [(SL, "        Point[] storage staying = stayingPoints[msg.sender][node];\n        if (amount > _at(staying, now_)) revert AMOUNT_TOO_LARGE();\n        _sub(staying, now_, amount);\n        _countSub(now_, msg.sender, node, now_ + 1, amount);", "        address holder = registry.mayActFor(msg.sender, node) ? node : msg.sender;\n        Point[] storage staying = stayingPoints[holder][node];\n        if (amount > _at(staying, now_)) revert AMOUNT_TOO_LARGE();\n        _sub(staying, now_, amount);\n        _countSub(now_, holder, node, now_ + 1, amount);")], "test_holder_funds_move_only"),
 ("holder funds: bond by the operator key moves the node's own", [(SL, '    function bond(address node, uint256 amount) external {\n        if (!registry.isRegistered(node)) revert NOT_REGISTERED();\n        uint256 now_ = clock.currentRound();\n        _add(stayingPoints[msg.sender][node], now_, amount);\n        _countAdd(now_, msg.sender, node, now_ + 1, amount);\n        _totalAdd(now_, now_ + 1, amount);\n        require(lpt.transferFrom(msg.sender, address(this), amount), "transfer failed");\n        emit Bonded(msg.sender, node, amount, now_ + 1);\n    }', '    function bond(address node, uint256 amount) external {\n        address h = registry.mayActFor(msg.sender, node) ? node : msg.sender;\n        if (!registry.isRegistered(node)) revert NOT_REGISTERED();\n        uint256 now_ = clock.currentRound();\n        _add(stayingPoints[h][node], now_, amount);\n        _countAdd(now_, h, node, now_ + 1, amount);\n        _totalAdd(now_, now_ + 1, amount);\n        require(lpt.transferFrom(h, address(this), amount), "transfer failed");\n        emit Bonded(h, node, amount, now_ + 1);\n    }')], "test_holder_funds_move_only"),
 ("holder funds: transferBond by the operator key moves the node's own", [(SL, "    function transferBond(address from, address to, uint256 amount) external {\n        if (!registry.isRegistered(to)) revert NOT_REGISTERED();\n        if (from == to) revert SAME_NODE();\n        uint256 now_ = clock.currentRound();\n        Point[] storage staying = stayingPoints[msg.sender][from];\n        if (amount > _at(staying, now_)) revert AMOUNT_TOO_LARGE();\n        uint256 arrival = now_ + _wait(now_);\n        // It leaves the old node's staying bond now, counts for the old node\n        // through arrival - 1, and for the new one, and stays there, from\n        // arrival. The total does not change.\n        _sub(staying, now_, amount);\n        _add(stayingPoints[msg.sender][to], arrival, amount);\n        _countAdd(now_, msg.sender, to, arrival, amount);\n        _countSub(now_, msg.sender, from, arrival, amount);\n        _insert(transfers[msg.sender][from][to], arrival, amount);\n        emit TransferStarted(msg.sender, from, to, amount, arrival);\n    }", "    function transferBond(address from, address to, uint256 amount) external {\n        address h = registry.mayActFor(msg.sender, from) ? from : msg.sender;\n        if (!registry.isRegistered(to)) revert NOT_REGISTERED();\n        if (from == to) revert SAME_NODE();\n        uint256 now_ = clock.currentRound();\n        Point[] storage staying = stayingPoints[h][from];\n        if (amount > _at(staying, now_)) revert AMOUNT_TOO_LARGE();\n        uint256 arrival = now_ + _wait(now_);\n        // It leaves the old node's staying bond now, counts for the old node\n        // through arrival - 1, and for the new one, and stays there, from\n        // arrival. The total does not change.\n        _sub(staying, now_, amount);\n        _add(stayingPoints[h][to], arrival, amount);\n        _countAdd(now_, h, to, arrival, amount);\n        _countSub(now_, h, from, arrival, amount);\n        _insert(transfers[h][from][to], arrival, amount);\n        emit TransferStarted(h, from, to, amount, arrival);\n    }")], "test_holder_funds_move_only"),
 ("holder funds: unbondTransfer by the operator key moves the node's own", [(SL, '    function unbondTransfer(address from, address to, uint256 amount) external {\n        if (from == to) revert SAME_NODE();\n        uint256 now_ = clock.currentRound();\n        Point[] storage list = transfers[msg.sender][from][to];\n        if (amount > _pending(list, now_)) revert AMOUNT_TOO_LARGE();\n        // The transfer arriving last first. For each part taken, its arrival\n        // at round t is cancelled, at `to` and at `from` alike, so it keeps\n        // counting for `from` until it is unbonded below.\n        uint256 left = amount;\n        while (left > 0) {\n            Point storage last = list[list.length - 1];\n            uint256 t = last.round;\n            uint256 part = last.value < left ? last.value : left;\n            if (part == last.value) list.pop();\n            else last.value -= part;\n            left -= part;\n            _sub(stayingPoints[msg.sender][to], t, part);\n            _countSub(now_, msg.sender, to, t, part);\n            _countAdd(now_, msg.sender, from, t, part);\n        }\n        // It stops counting for `from` next round and never arrives.\n        _countSub(now_, msg.sender, from, now_ + 1, amount);\n        _totalSub(now_, now_ + 1, amount);\n        uint256 ready = now_ + _wait(now_);\n        _lock(msg.sender, from, now_, ready, amount);\n        emit TransferUnbonded(msg.sender, from, to, amount, ready);\n    }', '    function unbondTransfer(address from, address to, uint256 amount) external {\n        address h = registry.mayActFor(msg.sender, from) ? from : msg.sender;\n        if (from == to) revert SAME_NODE();\n        uint256 now_ = clock.currentRound();\n        Point[] storage list = transfers[h][from][to];\n        if (amount > _pending(list, now_)) revert AMOUNT_TOO_LARGE();\n        // The transfer arriving last first. For each part taken, its arrival\n        // at round t is cancelled, at `to` and at `from` alike, so it keeps\n        // counting for `from` until it is unbonded below.\n        uint256 left = amount;\n        while (left > 0) {\n            Point storage last = list[list.length - 1];\n            uint256 t = last.round;\n            uint256 part = last.value < left ? last.value : left;\n            if (part == last.value) list.pop();\n            else last.value -= part;\n            left -= part;\n            _sub(stayingPoints[h][to], t, part);\n            _countSub(now_, h, to, t, part);\n            _countAdd(now_, h, from, t, part);\n        }\n        // It stops counting for `from` next round and never arrives.\n        _countSub(now_, h, from, now_ + 1, amount);\n        _totalSub(now_, now_ + 1, amount);\n        uint256 ready = now_ + _wait(now_);\n        _lock(h, from, now_, ready, amount);\n        emit TransferUnbonded(h, from, to, amount, ready);\n    }')], "test_holder_funds_move_only"),
 ("holder funds: rebond by the operator key moves the node's own", [(SL, '    function rebond(address node, uint256 amount) external {\n        if (!registry.isRegistered(node)) revert NOT_REGISTERED();\n        uint256 now_ = clock.currentRound();\n        Unbond[] storage list = unbonds[msg.sender][node];\n        if (amount > _waiting(list, now_)) revert AMOUNT_TOO_LARGE();\n        // The latest unbond first, of those not yet withdrawable; what is\n        // rebonded is no longer withdrawable at its ready round.\n        uint256 left = amount;\n        for (uint256 i = list.length; left > 0; i--) {\n            Unbond storage u = list[i - 1];\n            if (u.round <= now_ || u.value == 0) continue;\n            uint256 part = u.value < left ? u.value : left;\n            u.value -= part;\n            left -= part;\n            _sub(readyPoints[msg.sender], u.round, part);\n        }\n        while (list.length > 0 && list[list.length - 1].value == 0) {\n            list.pop();\n        }\n        _add(stayingPoints[msg.sender][node], now_, amount);\n        _countAdd(now_, msg.sender, node, now_ + 1, amount);\n        _totalAdd(now_, now_ + 1, amount);\n        emit Rebonded(msg.sender, node, amount, now_ + 1);\n    }', '    function rebond(address node, uint256 amount) external {\n        address h = registry.mayActFor(msg.sender, node) ? node : msg.sender;\n        if (!registry.isRegistered(node)) revert NOT_REGISTERED();\n        uint256 now_ = clock.currentRound();\n        Unbond[] storage list = unbonds[h][node];\n        if (amount > _waiting(list, now_)) revert AMOUNT_TOO_LARGE();\n        // The latest unbond first, of those not yet withdrawable; what is\n        // rebonded is no longer withdrawable at its ready round.\n        uint256 left = amount;\n        for (uint256 i = list.length; left > 0; i--) {\n            Unbond storage u = list[i - 1];\n            if (u.round <= now_ || u.value == 0) continue;\n            uint256 part = u.value < left ? u.value : left;\n            u.value -= part;\n            left -= part;\n            _sub(readyPoints[h], u.round, part);\n        }\n        while (list.length > 0 && list[list.length - 1].value == 0) {\n            list.pop();\n        }\n        _add(stayingPoints[h][node], now_, amount);\n        _countAdd(now_, h, node, now_ + 1, amount);\n        _totalAdd(now_, now_ + 1, amount);\n        emit Rebonded(h, node, amount, now_ + 1);\n    }')], "test_holder_funds_move_only"),
 ("holder funds: withdrawParts by the operator key moves the node's own", [(CL, '    function withdrawParts(address node, uint256[] calldata rounds) external {\n        _noRoundZero(rounds);\n        uint256 amount = 0;\n        for (uint256 i = 0; i < rounds.length; i++) {\n            uint256 round = rounds[i];\n            Entitlement storage e = entitlements[node][round];\n            if (!e.claimed) revert IClaims.NOT_CLAIMED();\n            if (partWithdrawn[msg.sender][node][round]) revert IClaims.ALREADY_CLAIMED();\n            partWithdrawn[msg.sender][node][round] = true;\n            amount += _part(e.pot, msg.sender, node, round);\n        }\n        emit IClaims.PartsWithdrawn(msg.sender, node, rounds, amount);\n        if (amount > 0) require(lpt.transfer(msg.sender, amount), "transfer failed");\n    }', '    function withdrawParts(address node, uint256[] calldata rounds) external {\n        address h = registry.mayActFor(msg.sender, node) ? node : msg.sender;\n        _noRoundZero(rounds);\n        uint256 amount = 0;\n        for (uint256 i = 0; i < rounds.length; i++) {\n            uint256 round = rounds[i];\n            Entitlement storage e = entitlements[node][round];\n            if (!e.claimed) revert IClaims.NOT_CLAIMED();\n            if (partWithdrawn[h][node][round]) revert IClaims.ALREADY_CLAIMED();\n            partWithdrawn[h][node][round] = true;\n            amount += _part(e.pot, h, node, round);\n        }\n        emit IClaims.PartsWithdrawn(h, node, rounds, amount);\n        if (amount > 0) require(lpt.transfer(h, amount), "transfer failed");\n    }')], "test_holder_funds_move_only"),
 ("holder funds: withdrawValidatorParts by the operator key moves the node's own", [(CL, '    function withdrawValidatorParts(address node, uint256[] calldata rounds) external {\n        _noRoundZero(rounds);\n        uint256 amount = 0;\n        for (uint256 i = 0; i < rounds.length; i++) {\n            uint256 round = rounds[i];\n            if (!minted[round]) revert IClaims.NOT_CLAIMED();\n            if (validatorPartWithdrawn[msg.sender][node][round]) revert IClaims.ALREADY_CLAIMED();\n            validatorPartWithdrawn[msg.sender][node][round] = true;\n            amount += _part(validatorPays[node][round].pot, msg.sender, node, round);\n        }\n        emit IClaims.ValidatorPartsWithdrawn(msg.sender, node, rounds, amount);\n        if (amount > 0) require(lpt.transfer(msg.sender, amount), "transfer failed");\n    }', '    function withdrawValidatorParts(address node, uint256[] calldata rounds) external {\n        address h = registry.mayActFor(msg.sender, node) ? node : msg.sender;\n        _noRoundZero(rounds);\n        uint256 amount = 0;\n        for (uint256 i = 0; i < rounds.length; i++) {\n            uint256 round = rounds[i];\n            if (!minted[round]) revert IClaims.NOT_CLAIMED();\n            if (validatorPartWithdrawn[h][node][round]) revert IClaims.ALREADY_CLAIMED();\n            validatorPartWithdrawn[h][node][round] = true;\n            amount += _part(validatorPays[node][round].pot, h, node, round);\n        }\n        emit IClaims.ValidatorPartsWithdrawn(h, node, rounds, amount);\n        if (amount > 0) require(lpt.transfer(h, amount), "transfer failed");\n    }')], "test_holder_funds_move_only"),
 ("holder funds: fund by the operator key moves the node's own", [(B, '    function fund(uint256 deposit_, address node, uint256 earmark_) external {\n        if (earmark_ == 0) {\n            node = address(0);\n        } else {\n            if (!registry.isRegistered(node)) revert NOT_REGISTERED();\n            Balance storage e = balance[msg.sender][node];\n            _set(msg.sender, node, e, e.amount + earmark_, e.pending, e.readyRound);\n        }\n        Balance storage d = balance[msg.sender][address(0)];\n        _set(msg.sender, address(0), d, d.amount + deposit_, d.pending, d.readyRound);\n        emit Funded(msg.sender, deposit_, node, earmark_);\n        require(usdc.transferFrom(msg.sender, address(this), deposit_ + earmark_), "USDC transfer failed");\n    }', '    function fund(uint256 deposit_, address node, uint256 earmark_) external {\n        address h = registry.mayActFor(msg.sender, node) ? node : msg.sender;\n        if (earmark_ == 0) {\n            node = address(0);\n        } else {\n            if (!registry.isRegistered(node)) revert NOT_REGISTERED();\n            Balance storage e = balance[h][node];\n            _set(h, node, e, e.amount + earmark_, e.pending, e.readyRound);\n        }\n        Balance storage d = balance[h][address(0)];\n        _set(h, address(0), d, d.amount + deposit_, d.pending, d.readyRound);\n        emit Funded(h, deposit_, node, earmark_);\n        require(usdc.transferFrom(h, address(this), deposit_ + earmark_), "USDC transfer failed");\n    }')], "test_holder_funds_move_only"),
 ("holder funds: requestRelease by the operator key moves the node's own", [(B, '    function requestRelease(address node, uint256 amount) external {\n        Balance storage b = balance[msg.sender][node];\n        if (amount > b.amount) revert AMOUNT_TOO_LARGE();\n        uint256 readyRound = amount == 0 ? 0 : clock.currentRound() + RELEASE_WAIT;\n        _set(msg.sender, node, b, b.amount, amount, readyRound);\n        emit ReleaseRequested(msg.sender, node, amount, readyRound);\n    }', '    function requestRelease(address node, uint256 amount) external {\n        address h = registry.mayActFor(msg.sender, node) ? node : msg.sender;\n        Balance storage b = balance[h][node];\n        if (amount > b.amount) revert AMOUNT_TOO_LARGE();\n        uint256 readyRound = amount == 0 ? 0 : clock.currentRound() + RELEASE_WAIT;\n        _set(h, node, b, b.amount, amount, readyRound);\n        emit ReleaseRequested(h, node, amount, readyRound);\n    }')], "test_holder_funds_move_only"),
 ("holder funds: release by the operator key moves the node's own", [(B, '    function release(address node) external {\n        Balance storage b = balance[msg.sender][node];\n        uint256 amount = b.pending;\n        if (amount == 0 || clock.currentRound() < b.readyRound) revert RELEASE_NOT_READY();\n        _set(msg.sender, node, b, b.amount - amount, 0, 0);\n        emit Released(msg.sender, node, amount);\n        require(usdc.transfer(msg.sender, amount), "USDC transfer failed");\n    }', '    function release(address node) external {\n        address h = registry.mayActFor(msg.sender, node) ? node : msg.sender;\n        Balance storage b = balance[h][node];\n        uint256 amount = b.pending;\n        if (amount == 0 || clock.currentRound() < b.readyRound) revert RELEASE_NOT_READY();\n        _set(h, node, b, b.amount - amount, 0, 0);\n        emit Released(h, node, amount);\n        require(usdc.transfer(h, amount), "USDC transfer failed");\n    }')], "test_holder_funds_move_only"),
 ("holder funds: TestLPT.approve sets the spender's allowance to the caller", [(LPT, "        allowance[msg.sender][spender] = amount;\n        emit Approval(msg.sender, spender, amount);", "        allowance[spender][msg.sender] = amount;\n        emit Approval(spender, msg.sender, amount);")], "test_holder_funds_move_only"),
 ("holder funds: TestLPT.transfer moves from the address named", [(LPT, "    function transfer(address to, uint256 amount) external returns (bool) {\n        _move(msg.sender, to, amount);", "    function transfer(address to, uint256 amount) external returns (bool) {\n        _move(to, msg.sender, amount);")], "test_holder_funds_move_only"),
 ("holder funds: TestLPT.transferFrom reads the allowance the wrong way round", [(LPT, "        uint256 allowed = allowance[from][msg.sender];", "        uint256 allowed = allowance[msg.sender][from];")], "test_holder_funds_move_only"),
 ("holder funds: MockUSDC.approve sets the spender's allowance to the caller", [(USD, "        allowance[msg.sender][spender] = amount;\n        emit Approval(msg.sender, spender, amount);", "        allowance[spender][msg.sender] = amount;\n        emit Approval(spender, msg.sender, amount);")], "test_holder_funds_move_only"),
 ("holder funds: MockUSDC.transfer moves from the address named", [(USD, "    function transfer(address to, uint256 amount) external returns (bool) {\n        _move(msg.sender, to, amount);", "    function transfer(address to, uint256 amount) external returns (bool) {\n        _move(to, msg.sender, amount);")], "test_holder_funds_move_only"),
 ("holder funds: MockUSDC.transferFrom reads the allowance the wrong way round", [(USD, "        uint256 allowed = allowance[from][msg.sender];", "        uint256 allowed = allowance[msg.sender][from];")], "test_holder_funds_move_only"),
 ("six lists: a function on no list", [(R, "    function isRegistered(address a) external view returns (bool) {", "    function forget() external {\n        registered[msg.sender] = false;\n    }\n\n    function isRegistered(address a) external view returns (bool) {")], "test_every_state_changing_function"),
 ("six lists: an overload on no list", [(R, "    function isRegistered(address a) external view returns (bool) {", "    function register(address node) external {\n        registered[node] = true;\n    }\n\n    function isRegistered(address a) external view returns (bool) {")], "test_every_state_changing_function"),
 ("six lists: a receive function on no list", [(R, "    function isRegistered(address a) external view returns (bool) {", "    receive() external payable {}\n\n    function isRegistered(address a) external view returns (bool) {")], "test_every_state_changing_function"),
 ("six lists: a fallback function on no list", [(R, "    function isRegistered(address a) external view returns (bool) {", "    fallback() external {}\n\n    function isRegistered(address a) external view returns (bool) {")], "test_every_state_changing_function"),
 ("six lists: a function on two lists", [(RT, '        l = new string[](2);\n        (l[0], l[1]) = ("Parameters.announce(uint256,uint256[])", "Parameters.withdraw(uint256)");', '        l = new string[](3);\n        (l[0], l[1], l[2]) = ("Parameters.announce(uint256,uint256[])", "Parameters.withdraw(uint256)", "Registry.setPayout(address,address)");')], "test_every_state_changing_function"),
 ("six lists: a listed function the ABI does not have", [(RT, '        l = new string[](2);\n        (l[0], l[1]) = ("Parameters.announce(uint256,uint256[])", "Parameters.withdraw(uint256)");', '        l = new string[](3);\n        (l[0], l[1], l[2]) = ("Parameters.announce(uint256,uint256[])", "Parameters.withdraw(uint256)", "Parameters.cancel(uint256)");')], "test_every_state_changing_function"),
]

ROOT = pathlib.Path(__file__).resolve().parent


def run(args, cwd, env=None):
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True, env=env)
    return r.returncode, r.stdout + r.stderr


def first_failure(out):
    for line in out.splitlines():
        s = line.strip()
        if s.startswith("[FAIL") or (line.startswith("abi:") and "equals" not in line) or "Error" in s[:12]:
            return s[:150]
    lines = out.strip().splitlines()
    return lines[-1][:150] if lines else ""


def catch(cmd, work):
    """Runs a catching check in the copy. Returns (red, ran, detail)."""
    if cmd == "check":
        # contracts/check exits 1 on a failure and 2 when it could not run;
        # only a failure catches a mutant.
        code, out = run(["sh", "./check"], work)
        return code == 1, code in (0, 1), first_failure(out)
    if cmd.startswith("qualify:"):
        # Failures kept by the last mutant (foundry.toml) must not replay here.
        shutil.rmtree(work / "cache" / "qualify" / "failures", ignore_errors=True)
        env = dict(os.environ, FOUNDRY_PROFILE="qualify")
        code, out = run(["forge", "test", "--match-contract", cmd[len("qualify:"):]], work, env)
    else:
        code, out = run(["forge", "test", "--match-test", cmd], work)
    red, ran = test_outcome(out)
    return red, ran, first_failure(out)


def test_outcome(out):
    """(red, ran) for a forge test run, from forge's per-test lines only.
    A test ran only if forge printed its [PASS] or [FAIL] line; red only if
    one of those is a [FAIL]. A mutant that does not compile, a tool error
    before any test, a pattern that matches no test, or a failing setUp()
    (forge's suite line, printed when no test body ran) is not ran (NOTRUN
    fails the run), whatever the exit code. Only the "check" path may rely
    on the compiler."""
    marks = [l.strip()[:5] for l in out.splitlines()
             if l.strip().startswith(("[PASS", "[FAIL")) and not SETUP.search(l)]
    return "[FAIL" in marks, bool(marks)


SETUP = re.compile(r"\]\s+setUp\(\)")


# A mutation's outcome: caught as expected; missed by a check that ran (or
# no longer applies); or not checked, because a check could not run.
OK, FAIL, NOTRUN = "ok", "fail", "notrun"


def main(sel):
    if not shutil.which("forge"):
        print("mutations: forge is not installed (./dev doctor)")
        return 2
    results = []
    with tempfile.TemporaryDirectory(prefix="lp2-mutations-") as tmp:
        work = pathlib.Path(tmp) / "contracts"
        shutil.copytree(ROOT, work)
        shutil.copytree(ROOT.parent / "vectors", pathlib.Path(tmp) / "vectors")
        # contracts/check runs the calculator on the half-life 420 case;
        # without it that check exits 2 and its mutants are NOT RUN.
        for d in ("calculator", "rulebook"):
            if (ROOT.parent / d).is_dir():
                shutil.copytree(ROOT.parent / d, pathlib.Path(tmp) / d, ignore=shutil.ignore_patterns("__pycache__"))
        code, out = run(["forge", "build"], work)
        if code != 0:
            print("mutations: the unmutated contracts do not build\n" + out[-2000:])
            return 2
        for m in M:
            name, edits, cmd = m[0], m[1], m[2]
            fast_green = len(m) > 3 and m[3]
            if sel and not any(s in name for s in sel):
                continue
            saved = {}
            started = time.time()
            try:
                stale = None
                for f, old, new in edits:
                    p = work / f
                    saved.setdefault(f, p.read_text())
                    t = p.read_text()
                    if t.count(old) != 1:
                        stale = f"{f}: the text to mutate is found {t.count(old)} times"
                        break
                    p.write_text(t.replace(old, new))
                if stale:
                    results.append(("STALE", name, cmd, stale, FAIL))
                    continue
                # contracts/check holds the layout to forge fmt: lay the
                # mutant out the same way, so only the seeded error is new.
                laid = sorted({f for f, _, _ in edits if f.endswith(".sol") and not f.startswith("../")})
                fmt, fmt_out = run(["forge", "fmt", *laid], work) if laid else (0, "")
                if fmt != 0:
                    # A mutant the formatter rejects was never checked.
                    verdict, outcome = "NOTRUN", NOTRUN
                    detail = f"forge fmt failed, so no check ran: {first_failure(fmt_out)}"
                else:
                    red, ran, detail = catch(cmd, work)
                    verdict = "RED" if red and ran else "GREEN" if ran else "NOTRUN"
                    outcome = {"RED": OK, "GREEN": FAIL, "NOTRUN": NOTRUN}[verdict]
                if fast_green and outcome == OK:
                    fred, fran, fdetail = catch("check", work)
                    if not fran:
                        outcome = NOTRUN
                        detail = f"the fast check could not run, so heavy-only is not shown: {fdetail}"
                    elif fred:
                        outcome = FAIL
                        detail = f"fast check also red, so not heavy-only: {fdetail}"
                    else:
                        detail += " | fast check: green"
                results.append((verdict, name, cmd, f"{detail} ({time.time() - started:.0f}s)", outcome))
            finally:
                for f, t in saved.items():
                    (work / f).write_text(t)
            print(f"{results[-1][0]:<6} | {name} | {cmd} | {results[-1][3]}", flush=True)
    if not results:
        print("mutations: not_run (no mutation matches " + " ".join(map(repr, sel)) + ")")
        return 2
    bad = [r for r in results if r[4] != OK]
    print(f"mutations: {len(results) - len(bad)} of {len(results)} caught as expected")
    for r in bad:
        print(f"mutations: NOT CAUGHT as expected: {r[1]} ({r[0]})")
    if any(r[4] == FAIL for r in results):
        return 1
    if bad:
        print(f"mutations: not_run ({len(bad)} could not be checked)")
        return 2
    return 0


def stop(signum, _frame):
    # Turns termination into an exception, so every file is put back and
    # the copy removed on the way out.
    raise SystemExit(128 + signum)


if __name__ == "__main__":
    for s in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(s, stop)
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        print("mutations: interrupted; the copy is removed and nothing here was changed")
        sys.exit(2)
