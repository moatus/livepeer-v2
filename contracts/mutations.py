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
Exit 0 every mutation caught as expected; 1 one was not, or no longer
applies; 2 could not run.
"""
import os, pathlib, shutil, signal, subprocess, sys, tempfile, time

B, R, C = "src/money/Broker.sol", "src/authority/Registry.sol", "src/clock/RoundClock.sol"

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
 ("setPayout stores nothing", [(R, "        payout[msg.sender] = payout_;\n", "")], "test_ledger_payout"),
 ("release does not lower the balance", [(B, "_set(msg.sender, node, b, b.amount - amount, 0, 0);", "_set(msg.sender, node, b, b.amount, 0, 0);")], "test_ledger_release"),
 ("cap of 100 nodes on earmark draws", [
    (R, "    mapping(address => bool) private registered;", "    mapping(address => bool) private registered;\n    uint256 public count;"),
    (R, "        registered[msg.sender] = true;", "        registered[msg.sender] = true;\n        count++;"),
    (B, "        uint256 fromEarmark = e.amount;", "        uint256 fromEarmark = Counted(address(registry)).count() > 100 ? 0 : e.amount;"),
    (B, "contract Broker is ITicketBroker {", "interface Counted { function count() external view returns (uint256); }\n\ncontract Broker is ITicketBroker {")], "test_ledger_reserve"),
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
 ("setPayout by unregistered accepted", [(R, "        if (!registered[msg.sender]) revert NOT_REGISTERED();\n", "")], "test_set_payout_by_unregistered"),
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
 ("node half in one product (overflows on large amounts; a fast test also catches it)", [(B, "        uint256 nodeAmount = paid / ONE * NODE_CUT + paid % ONE * NODE_CUT / ONE;", "        uint256 nodeAmount = paid * NODE_CUT / ONE;")], "qualify:AmountsFuzz"),
 # --- defects only the heavy runs catch: the fast check stays green -----
 ("partly paid ticket not marked used", [(B, "        usedTickets[ticketHash] = true;", "        usedTickets[ticketHash] = paid == t.faceValue;")], "qualify:MoneyInvariants", True),
 ("used ticket paid again in a later round", [(B, "        if (usedTickets[ticketHash]) revert TICKET_USED();", "        if (usedTickets[ticketHash] && round == creationRound) revert TICKET_USED();")], "qualify:MoneyInvariants", True),
 ("drawn to 0 keeps its ready round", [(B, "_set(payer, node, b, left, left, left == 0 ? 0 : b.readyRound);", "_set(payer, node, b, left, left, b.readyRound);")], "qualify:MoneyInvariants", True),
 ("top-up cancels a pending deposit release", [(B, "        _set(msg.sender, address(0), d, d.amount + deposit_, d.pending, d.readyRound);", "        _set(msg.sender, address(0), d, d.amount + deposit_, 0, 0);")], "qualify:MoneyInvariants", True),

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
        code, out = run(["sh", "./check"], work)
        return code != 0, True, first_failure(out)
    if cmd.startswith("qualify:"):
        # Failures kept by the last mutant (foundry.toml) must not replay here.
        shutil.rmtree(work / "cache" / "qualify" / "failures", ignore_errors=True)
        env = dict(os.environ, FOUNDRY_PROFILE="qualify")
        code, out = run(["forge", "test", "--match-contract", cmd[len("qualify:"):]], work, env)
        return code != 0, "Ran " in out, first_failure(out)
    code, out = run(["forge", "test", "--match-test", cmd], work)
    red, ran = test_outcome(out)
    return red, ran, first_failure(out)


def test_outcome(out):
    """(red, ran) for a --match-test run, from forge's per-test lines only.
    A test ran only if forge printed its [PASS] or [FAIL] line; red only if
    one of those is a [FAIL]. A mutant that does not compile, a tool error
    before any test, or a pattern that matches no test is not ran (NOTRUN
    fails the run), whatever the exit code. Only the "check" path may rely
    on the compiler."""
    marks = [l.strip()[:5] for l in out.splitlines() if l.strip().startswith(("[PASS", "[FAIL"))]
    return "[FAIL" in marks, bool(marks)


def main(sel):
    if not shutil.which("forge"):
        print("mutations: forge is not installed (./dev doctor)")
        return 2
    results = []
    with tempfile.TemporaryDirectory(prefix="lp2-mutations-") as tmp:
        work = pathlib.Path(tmp) / "contracts"
        shutil.copytree(ROOT, work)
        shutil.copytree(ROOT.parent / "vectors", pathlib.Path(tmp) / "vectors")
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
                    results.append(("STALE", name, cmd, stale, False))
                    continue
                red, ran, detail = catch(cmd, work)
                verdict = "RED" if red and ran else "GREEN" if ran else "NOTRUN"
                ok = verdict == "RED"
                if fast_green and ok:
                    fred, _, fdetail = catch("check", work)
                    if fred:
                        ok = False
                        detail = f"fast check also red, so not heavy-only: {fdetail}"
                    else:
                        detail += " | fast check: green"
                results.append((verdict, name, cmd, f"{detail} ({time.time() - started:.0f}s)", ok))
            finally:
                for f, t in saved.items():
                    (work / f).write_text(t)
            print(f"{results[-1][0]:<6} | {name} | {cmd} | {results[-1][3]}", flush=True)
    bad = [r for r in results if not r[4]]
    print(f"mutations: {len(results) - len(bad)} of {len(results)} caught as expected")
    for r in bad:
        print(f"mutations: NOT CAUGHT as expected: {r[1]} ({r[0]})")
    return 1 if bad else 0


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
