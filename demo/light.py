"""./dev demo light: the standing testnet's services on a fresh local chain,
with the unmodified node, no model and no Docker.

Deploys the stage 3 contracts and starts the keeper. Nodes N, M and S
register; each runs the node's orchestrator, N and M in front of their own
lp2-responder, S in front of a runner that answers its health check and
never answers a request. X registers and runs nothing; U never registers.
The payer P's signer listens on loopback, and lp2-sender, holding P's key,
answers joins.

Then: each join that should be refused is refused, with its code, and
leaves no earmark and no list line. N and M join and are each sent a paid
request at once, then one a round; S joins and its request never answers.
The sender is stopped and restarted within a round. Once N and M have been
paid in four rounds, the sender stops, the nodes redeem, and the chain is
read: one ticket per node per round, one ticket for each relay, and each
node's payout up by its half of each.

Exit 0 every check passed; 1 one failed; 2 the run could not run.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import common
common.use("stage-3-local.json")
from common import *  # noqa: E402,F401,F403
from common import PORT, C, ADDR, KEY, BASE, MANIFEST, FAILURES  # noqa: E402

ORCH = {"N": ("orch", "orch_metrics"), "M": ("orch_m", "orch_m_metrics"), "S": ("orch_k", "orch_k_metrics")}
RUNNER = {"N": BASE + 20, "M": BASE + 21, "S": BASE + 22}
NOBODY = BASE + 23   # a port nothing listens on
SENDER = BASE + 24   # the sender's joins
PORT.update({"runner_n": RUNNER["N"], "runner_m": RUNNER["M"], "runner_s": RUNNER["S"], "nobody": NOBODY, "sender": SENDER})
BOUND = 10           # --node-timeout: the most one node's attempt takes, in seconds
FIRST_RELAY_LIMIT = 60
ROUNDS = 4           # N and M are paid in this many rounds: the join's, then three more
KEYS: dict[str, object] = {}
LIST = common.RUN / "nodes.txt"

T = r"^(\S+ \S+) "
RELAY = re.compile(T + r"relay: node (0x[0-9a-fA-F]{40}) round (\d+): answered in (\d+) ms, ([\d.]+) s after the round "
                   r"was seen, ([\d.]+) s for the whole attempt(?:, its first relay, ([\d.]+) s after its join)?")
GAVE_UP = re.compile(T + r"not paid: node (0x[0-9a-fA-F]{40}) round (\d+): the paid request was sent and failed after (\d+) ms"
                     r".*?(context deadline exceeded)?$")
STARTED = re.compile(T + r"payer 0x")
SEEN = re.compile(T + r"round (\d+) seen")


def sender_log() -> str:
    f = common.RUN / "logs" / "sender.log"
    return f.read_text() if f.is_file() else ""


def runs() -> list[list[str]]:
    """The sender's log, one list of lines per process that started."""
    out: list[list[str]] = []
    for line in sender_log().splitlines():
        if STARTED.match(line):
            out.append([])
        if out:
            out[-1].append(line)
    return out


def relays(lines: list[str] | None = None) -> dict[str, list[dict]]:
    """Each node's relays in the sender's log: its round and timings."""
    out: dict[str, list[dict]] = {}
    for line in sender_log().splitlines() if lines is None else lines:
        if m := RELAY.match(line):
            out.setdefault(name(m.group(2)), []).append(
                {"round": int(m.group(3)), "after_round": float(m.group(5)), "attempt": float(m.group(6)),
                 "first": float(m.group(7)) if m.group(7) else None})
    return out


def gave_up() -> dict[str, list[tuple[int, int, bool]]]:
    """Each node's paid requests sent and not answered: (round, ms, whether
    the bound ended it rather than the sender stopping)."""
    out: dict[str, list[tuple[int, int, bool]]] = {}
    for line in sender_log().splitlines():
        if m := GAVE_UP.match(line):
            out.setdefault(name(m.group(2)), []).append((int(m.group(3)), int(m.group(4)), bool(m.group(5))))
    return out


def wait(what: str, ready, seconds: float) -> float:
    start = time.time()
    while time.time() - start < seconds:
        if ready():
            return time.time() - start
        time.sleep(0.25)
    raise Failed(f"timed out after {seconds:.0f} s waiting for {what}")


class Stuck(BaseHTTPRequestHandler):
    """S's runner: it answers the orchestrator's health check, and accepts
    every other request and never answers it."""

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Length", "3")
            self.end_headers()
            self.wfile.write(b"ok\n")
            return
        self.hang()

    def do_POST(self):
        self.hang()

    def hang(self):
        while not STOP.is_set():
            STOP.wait(1)

    def log_message(self, *_):
        pass


STOP = threading.Event()


def start_stuck_runner() -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer(("127.0.0.1", RUNNER["S"]), Stuck)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def orchestrator(n: str, node_bin) -> None:
    port, metrics = ORCH[n]
    if n != "S":
        start(f"responder-{n}", [str(common.RUN / "bin" / "lp2-responder"), "--listen", f"127.0.0.1:{RUNNER[n]}"])
    start_orchestrator(f"orchestrator-{n}", n, node_bin, KEYS[n], common.RUN / "pw", PORT[port], PORT[metrics],
                       runner=("light", "light", RUNNER[n]))


def runner_listed(n: str) -> bool:
    """Whether n's discovery lists its runner: after its first health check."""
    try:
        with urllib.request.urlopen(url(n) + "/discovery", timeout=2) as r:
            return any(x.get("app") == "light" for e in json.loads(r.read()) for x in e.get("runners") or [])
    except (urllib.error.URLError, ConnectionError, OSError, ValueError):
        return False


def url(n: str) -> str:
    return f"http://127.0.0.1:{PORT[ORCH[n][0]]}"


def start_sender() -> None:
    start("sender", [str(common.RUN / "bin" / "lp2-sender"), "--rpc-url", common.RPC, "--keystore-file", str(KEYS["P"]),
                     "--keystore-password-file", str(common.RUN / "pw"), "--controller-address", C["Controller"],
                     "--usdc-address", C["MockUSDC"], "--signer-url", f"http://127.0.0.1:{PORT['signer_p']}",
                     "--nodes", str(LIST), "--listen", f"127.0.0.1:{SENDER}", "--allow-private-urls",
                     "--max-nodes", "3", "--poll", "1s", "--node-timeout", f"{BOUND}s", "--retry", "3s"])
    wait("the sender to answer joins", lambda: post(f"http://127.0.0.1:{SENDER}/join", {})[0] == 400, 30)


def join(node: str, signer: str, at: str, issued: int | None = None, body: dict | None = None) -> tuple[int, str, dict]:
    """Sends a join for `node`, signed by `signer`'s key, and returns the
    HTTP status, the code or status, and the body sent."""
    if body is None:
        issued = int(time.time()) if issued is None else issued
        msg = f"lp2-join:v1:{MANIFEST['chain']['chain_id']}:{C['Registry'].lower()}:{ADDR[node].lower()}:{at}:{issued}"
        sig = sh(["cast", "wallet", "sign", "--private-key", KEY[signer], msg])
        body = {"node": ADDR[node].lower(), "url": at, "issuedAt": issued, "signature": sig}
    status, text = post(f"http://127.0.0.1:{SENDER}/join", body)
    try:
        answer = json.loads(text)
    except ValueError:
        answer = {}
    return status, answer.get("code") or answer.get("status") or text[:120], body


def listed() -> list[str]:
    if not LIST.is_file():
        return []
    return [line for line in LIST.read_text().splitlines() if line.strip() and not line.startswith("#")]


def earmark(n: str) -> int:
    return call(C["Broker"], "claimableReserve(address,address)", ADDR["P"], ADDR[n])


def main() -> int:
    open_log()
    started = time.time()
    say("The standing services: lp2-sender pays each node that joins, once a round; no model, no Docker")
    say("\n1. Setting up")
    preflight(["chain", *[p for pair in ORCH.values() for p in pair], "signer_p", "signer_p_metrics",
               "runner_n", "runner_m", "runner_s", "nobody", "sender"], docker=False)
    node_bin = fetch_node()
    common.PROGRAMS.update({"lp2-sender": "./cmd/lp2-sender", "lp2-responder": "./cmd/lp2-responder"})
    build_ours()
    start_chain()
    pw = common.RUN / "pw"
    pw.write_text("pw-" + os.urandom(8).hex())
    pw.chmod(0o600)
    for who in ("deployer", "N", "M", "S", "X", "U", "P", "Keeper"):
        new_key(who)
        if who != "deployer":
            KEYS[who] = keystore(who, pw)
    ADDR["Treasury"] = "0x" + os.urandom(20).hex()
    common.NAMES[ADDR["Treasury"].lower()] = "Treasury"
    deploy_stage3()
    for n in ("N", "M", "S", "X"):
        send(KEY[n], C["Registry"], "register()")
    start("keeper", [str(common.RUN / "bin" / "lp2-keeper"), "--rpc-url", common.RPC, "--keystore-file", str(KEYS["Keeper"]),
                     "--keystore-password-file", str(pw), "--controller-address", C["Controller"], "--poll", "1s"])
    say(f"  chain on {common.RPC}; stage 3 contracts; N, M, S and X registered, U not; the keeper starts each round "
        f"({MANIFEST['chain']['round_length_blocks']} blocks of {MANIFEST['chain']['block_time_seconds']} s)")
    stuck = start_stuck_runner()
    for n in ("N", "M", "S"):
        orchestrator(n, node_bin)
    for n in ("N", "M", "S"):
        wait(f"{n}'s discovery to list its runner", lambda: runner_listed(n), 30)
    start_signer("P", node_bin, KEYS["P"], pw, PORT["signer_p"], PORT["signer_p_metrics"], [PORT["orch"]])
    say("  N and M: an orchestrator each, serving lp2-responder; S: an orchestrator whose runner never answers; "
        "each lists its runner in its discovery")
    say("  P's signer on loopback; P holds nothing: the sender mints P's mock USDC as it needs it")
    start_sender()
    say(f"  lp2-sender answers joins on 127.0.0.1:{SENDER}, with --allow-private-urls (local only), at most 3 nodes, "
        f"each node's attempt bounded by {BOUND} s")

    say("\n2. Joins that should be refused")
    now = int(time.time())
    refused = [
        ("a bad signature: N's join signed with M's key", join("N", "M", url("N")), "BAD_SIGNATURE"),
        ("a stale issuedAt: N's join issued 700 s ago", join("N", "N", url("N"), now - 700), "STALE"),
        ("an unregistered node: U", join("U", "U", url("N")), "NOT_REGISTERED"),
        ("a URL that is not http or https", join("X", "X", "ftp://127.0.0.1:21"), "URL_NOT_ALLOWED"),
        ("an unreachable URL: X at a port nothing listens on", join("X", "X", f"http://127.0.0.1:{NOBODY}"), "UNREACHABLE"),
        ("RECIPIENT_MISMATCH: X signs a join for N's healthy endpoint", join("X", "X", url("N")), "RECIPIENT_MISMATCH"),
    ]
    for what, (status, code, _), want in refused:
        say(f"  {what}: HTTP {status} {code}")
        check(f"{what} is refused with {want}", code, want)
    check("the refused joins left no list line", listed(), [])
    check("the refused joins left no earmark", {n: earmark(n) for n in ("N", "X", "U")}, {"N": 0, "X": 0, "U": 0})
    say("  a private URL without --allow-private-urls is refused with URL_NOT_ALLOWED: shown by the sender's unit "
        "test TestURLPolicy (this run allows private URLs, since every node is on loopback)")

    say("\n3. N and M join; each is sent a paid request at once")
    accepted: dict[str, tuple[float, dict]] = {}
    for n in ("N", "M"):
        status, s, body = join(n, n, url(n))
        accepted[n] = (time.time(), body)
        say(f"  {n} joins: HTTP {status} {s}")
        check(f"{n}'s join is accepted", (status, s), (200, "listed"))
    first: dict[str, float] = {}
    for n in ("N", "M"):
        wait(f"{n}'s first relay", lambda: bool(relays().get(n)), FIRST_RELAY_LIMIT + 30)
        first[n] = time.time() - accepted[n][0]
    round0 = {n: relays()[n][0]["round"] for n in ("N", "M")}
    for n in ("N", "M"):
        said = relays()[n][0]["first"]
        say(f"  {n}: join to first relay {first[n]:.1f} s as the demo saw it ({said} s in the sender's log), in round {round0[n]}")
        check(f"{n}'s first relay came within {FIRST_RELAY_LIMIT} s of its join", first[n] <= FIRST_RELAY_LIMIT, True)
    status, code, _ = join("N", "N", url("N"), body=accepted["N"][1])
    say(f"  N's accepted join, sent again: HTTP {status} {code}")
    check("a replayed earlier join is refused with STALE", code, "STALE")
    check("the replayed join left the list as it was: N and M", [line.split()[0] for line in listed()],
          [ADDR["N"].lower(), ADDR["M"].lower()])

    say(f"\n4. S joins; its runner never answers, and each attempt on it is bounded by {BOUND} s")
    status, s, _ = join("S", "S", url("S"))
    say(f"  S joins: HTTP {status} {s}")
    check("S's join is accepted", (status, s), (200, "listed"))
    s_round = current_round()
    status, code, _ = join("X", "X", url("N"))
    say(f"  with 3 nodes listed, X's join: HTTP {status} {code}")
    check("a fourth node's join is refused with FULL while 3 are listed", (status, code), (503, "FULL"))
    check("the refused joins left no earmark for X or U", (earmark("X"), earmark("U")), (0, 0))

    say("\n5. The sender is stopped and restarted within one round")
    wait("a round after S joined in which N and M are both paid",
         lambda: all(any(r["round"] > s_round for r in relays().get(n, [])) for n in ("N", "M")), 3 * 60)
    r = max(x["round"] for x in relays()["N"])
    stop("sender")
    start_sender()
    after = current_round()
    say(f"  stopped after N and M were paid in round {r}; restarted in round {after}")
    check("the sender was restarted in the round it was stopped in", after, r)
    second = runs()[-1]
    check("the restarted sender began in that round", [int(m.group(2)) for line in second if (m := SEEN.match(line))][:1], [r])

    say(f"\n6. Each node is paid once a round, with nobody acting, until N and M have been paid in {ROUNDS} rounds")
    last = {n: round0[n] + ROUNDS - 1 for n in ("N", "M")}
    wait(f"N and M paid in {ROUNDS} rounds", lambda: all(any(x["round"] >= last[n] for x in relays().get(n, [])) for n in ("N", "M")),
         ROUNDS * 80)
    check("the restarted sender sent no node a relay in the round it was restarted in",
          sorted(n for n, xs in relays(second).items() for x in xs if x["round"] == r), [])
    stop("sender")
    STOP.set()
    stuck.shutdown()
    got = relays()
    for n in ("N", "M"):
        rounds = [x["round"] for x in got[n]]
        say(f"  {n}: relays in rounds {rounds}")
        check(f"{n} was paid once in each round from its join's, {round0[n]}, to {last[n]}",
              sorted(set(rounds))[:ROUNDS], list(range(round0[n], last[n] + 1)))
        check(f"{n} was sent no second relay in any round", len(rounds), len(set(rounds)))
    with_s = [(n, x) for n in ("N", "M") for x in got[n] if x["round"] > s_round]
    worst = max(x["after_round"] for _, x in with_s)
    say(f"  with S listed, N's and M's relays came {', '.join(f'{x[1]:.1f}' for x in sorted((x['round'], x['after_round']) for _, x in with_s))} s "
        "after the sender saw each round")
    s_gave_up = gave_up().get("S", [])
    say("  S's paid requests, never answered: " + ", ".join(
        f"round {r_} given up after {ms / 1000:.1f} s" + ("" if bound else " when the sender stopped") for r_, ms, bound in s_gave_up))
    check(f"S's paid requests were given up at the bound of {BOUND} s, and none ran longer (2 s of slack)",
          any(bound for _, _, bound in s_gave_up) and all(ms <= (BOUND + 2) * 1000 for _, ms, _ in s_gave_up), True)
    check(f"with S listed, N and M were each paid within {BOUND} s of the round being seen", worst <= BOUND, True)

    say("\n7. The nodes redeem each ticket once its parameters expire (40 blocks); the chain is read")
    want = {n: len(got[n]) for n in ("N", "M")}
    mine(41)

    def mine_tickets(n: str) -> list[dict]:
        return [x for x in settlements() if x["recipient"].lower() == ADDR[n].lower()]
    wait("N's and M's redemptions", lambda: all(len(mine_tickets(n)) >= want[n] for n in ("N", "M")), 3 * 60)
    time.sleep(3)
    face = ticket_face()
    for n in ("N", "M"):
        t = mine_tickets(n)
        payout = f"0x{call(C['Registry'], 'payoutOf(address)', ADDR[n]):040x}"
        usdc = call(C["MockUSDC"], "balanceOf(address)", payout)
        created = [x["created_round"] for x in t]
        say(f"  {n}: {len(t)} tickets redeemed, created in rounds {created}; node part {t[0]['node_amount'] if t else 0} "
            f"of {money(face)} each; its payout holds {money(usdc)} micro-USDC")
        check(f"{n}: one ticket redeemed for each relay", len(t), want[n])
        check(f"{n}: on chain, no two tickets created in one round", len(created), len(set(created)))
        check(f"{n}: on chain, the tickets' rounds are the rounds of its relays", sorted(created), sorted(x["round"] for x in got[n]))
        check(f"{n}'s payout rose by its half of the ticket for each relay", usdc, want[n] * (face // 2))
        check(f"{n}: each ticket's node part is half of it, rounded down", all(x["node_amount"] == x["gross"] // 2 for x in t), True)
    s_tickets = mine_tickets("S")
    say(f"  S: {len(s_tickets)} tickets redeemed for {len(s_gave_up)} paid requests that never answered: the node takes "
        "the ticket before its runner answers, so a redemption is not a relay")
    check("P's tickets are the only ones settled", {name(x["sender"]) for x in settlements()}, {"P"})
    # A fund with no earmark names no node (address 0).
    funded = {name("0x" + x["topics"][2][-40:]) for x in logs(C["Broker"], "Funded(address,uint256,address,uint256)")
              if int(x["topics"][2], 16) != 0}
    check("earmarks were funded for listed nodes only", sorted(funded), ["M", "N", "S"])
    redeemed_lines = [line for line in sender_log().splitlines() if " redeemed: " in line]
    say(f"  the sender's log: {sum(len(x) for x in got.values())} relay lines, {len(redeemed_lines)} redeemed lines "
        "(a redemption seen while the sender ran)")
    held = words(C["Broker"], "getSenderInfo(address)", ADDR["P"])
    say(f"  P at the end: wallet {money(call(C['MockUSDC'], 'balanceOf(address)', ADDR['P']))}, deposit {money(held[0])}, "
        f"earmarks {', '.join(f'{n} ' + money(earmark(n)) for n in ('N', 'M', 'S', 'X', 'U'))}")
    return finish(started, "joins refused and accepted, each node paid once a round, a stuck node bounded, a restart paid nobody twice")
