"""./dev demo: one full reward cycle and
the scripted validators, on a fresh local chain.

Deploys the rewards contracts and runs the unmodified node for four nodes,
N, M, K and L, the round keeper and four scripted validators. Four holders
bond test LPT to N, M and K; in round 2 the node serves five paid relays of
1 USDC each, three for N and one each for M and K. The keeper is stopped as
round 2 ends, which delays round 2's validator and treasury amounts; once it
runs again, after somebody else has started round 3, it mints them.

V1 to V3, with 5,000, 3,000 and 2,000 LPT (4,000 of V1's from holder H, and
V1 a 10 % reward cut), opt in in round 3 and are members from round 4. In
round 4 they vote M 0.5 and K 0, V1 alone votes L 0, and N, M, K and L each
serve one paid relay, so that four nodes earn in one round;
V4, with 6,000 LPT, opts in and takes V3's seat from round 5, and its vote
in round 4 is refused. A second payer, P2, with its own signer, pays two of
round 4's four relays, M's and L's, asks for one USDC of its deposit back
and takes it in round 8. Round 4's validator amount is divided among V1,
V2 and V3 by stake; V1's cut goes to its payout and its pot to Claims, from
which V1 and H withdraw their parts. In round 8 N's claim is refused as too
early; in round 9 a rival's and a delegator's claims for N are refused, N
claims at score 1, its holders withdraw their parts, N's second claim is
refused, M claims at 0.5 and K at 0. F then moves 100 LPT from K toward M,
unbonds 40 of the move and rebonds it, which can go back only to K. In round
11 N, M, L and K claim their round 4 rewards
at scores 1, 0.5, 1 (one vote cannot cut a node) and 0.

Every figure read from the chain is printed beside the independent
calculator's figure for the same steps, and checked against the shared ledger-cycle example.

lp2-explorer runs beside the whole run with no node, keeping its index of
events in the run's folder. Each node's fees by round and claims, each
payer's deposit, funded and released, and each payer's fees by node are
checked against the figures the run checks against the calculator; its fee
sums against feesOf and totalFees, and every node's score against scoreOf,
each at the explorer's block, after round 4's votes, in round 5 once V4
holds V3's seat, and at the end. A second explorer, reading through a
counting stand-in provider in pages of 10 blocks, is killed with SIGKILL
while it catches up and started again: it resumes from its checkpoint,
asks for no logs below it, and shows the figures of the first.

The run is written as a
`ledger` case, a `validators` case and one `validator-pay` case for each
round minted, and `./calculator/check` reproduces each.

Exit 0 every figure equal and every behaviour seen; 1 a figure differed or
a behaviour was missing; 2 the demo could not run. What runs, and every
stand-in, is read from deployments/rewards-local.json.
"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import time
import urllib.error
import urllib.request

import common
common.use("rewards-local.json")
from common import *  # noqa: E402,F401,F403
from common import MANIFEST, PARAMS, CASES, VECTORS, PORT, C, ADDR, KEY, NAMES, RECORDS, FAILURES, ZERO  # noqa: E402
import ledgercase  # noqa: E402
import validatorcases  # noqa: E402

LPT = 10**18
ORCHS = {"N": ("orch", "orch_metrics"), "M": ("orch_m", "orch_m_metrics"), "K": ("orch_k", "orch_k_metrics"),
         "L": ("orch_l", "orch_l_metrics")}
SCRIPT = common.HERE / "validators.script"
KEYS: dict[str, object] = {}
CYCLE = CASES["ledger-cycle"]  # the ledger-cycle example, step for step
# the validator example's validators are pay-split's members A, B and C: their
# stakes, A's reward cut, and A's bonds, its operator's and H's.
PAY = CASES["pay-split"]["inputs"]
SEATED = dict(zip(("V1", "V2", "V3"), (int(m["stake_wei"]) for m in PAY["members"])))
V1_CUT = int(PAY["splits"]["A"]["cut_millionths"])
V1_OWN, H_BOND = (int(PAY["splits"]["A"]["bonds_wei"][h]) for h in ("O", "H"))
# The seat replacement example: V4, with 6,000 LPT, takes V3's seat. L is the
# fourth node in the validator reward example; its stake is the demo's choice.
V4_STAKE = 6_000 * LPT
L_STAKE = 1_000 * LPT
VALIDATORS = (*SEATED, "V4")
REGISTERED = ("N", "M", "K", "L", *VALIDATORS)


def cycle_step(**match) -> dict:
    """The step of ledger-cycle that has these fields."""
    return next(s for s in CYCLE["inputs"]["steps"] if all(s.get(k) == v for k, v in match.items()))


def amount(**match) -> int:
    return int(cycle_step(**match)["amount_wei"])


# --- steps --------------------------------------------------------------------

def tx_step(fields: dict, who: str, to: str, signature: str, *args) -> dict:
    """A step sent with cast as `who`."""
    return record(fields, send(KEY[who], to, signature, *args))


def stake(who: str, fields: dict, *argv: str, want: str | None = None) -> dict:
    """A step sent by lp2-stake as `who`: a transaction, or a refusal the
    contract gave at a block, recorded with its code. `want` is the code the
    demo expects, or None for a step that takes effect."""
    done = subprocess.run([str(common.RUN / "bin" / "lp2-stake"), "--rpc-url", common.RPC, "--keystore-file", str(KEYS[who]),
                           "--keystore-password-file", str(common.RUN / "pw"), "--stake-ledger-address", C["StakeLedger"],
                           "--claims-address", C["Claims"], "--registry-address", C["Registry"], "--lpt-address", C["TestLPT"], *argv],
                          capture_output=True, text=True, pass_fds=common.KEEP_FDS)
    out = done.stdout.strip().splitlines()
    common.SENT.extend(common.sent_by(done.stdout))
    with open(common.RUN / "logs" / "lp2-stake.log", "a") as log:
        log.write(f"$ {who}: lp2-stake {' '.join(argv)}\n{done.stdout}{done.stderr}")
    last = out[-1] if out else ""
    if done.returncode == 0 and last.startswith("sent:"):
        r = record(fields, receipt(last.split("tx ")[-1].rstrip(")")))
    elif done.returncode == 3 and last.startswith("refused:"):
        code, block = last.split()[1], int(last.split()[4])
        r = record(fields, block=block, code=code)
    else:
        raise Failed(f"lp2-stake {argv[0]} as {who}: exit {done.returncode}: {(done.stderr or done.stdout).strip()[-300:]}")
    got = r.get("code")
    what = f"{who}: {argv[0]} {' '.join(name(a) if a.startswith('0x') else a for a in argv[1:])}"
    if want or got:
        check(f"{what} is refused", got, want)
    return r


def a(n: str) -> str:
    return ADDR[n]


def bond(holder: str, node: str, wei: int) -> None:
    stake(holder, {"action": "bond", "holder": holder, "node": node, "amount_wei": wei}, "bond", a(node), str(wei))


def claim(caller: str, node: str, earned: int, want: str | None = None) -> dict:
    """The claim step states the score the claim read: scoreOf(node) at the
    claim's block, the same block whatever else happens in it, since nothing
    in a claim moves a vote."""
    r = stake(caller, {"action": "claim", "caller": caller, "node": node, "earned_round": earned},
              "claim", a(node), str(earned), want=want)
    r["fields"]["score"] = call(C["Scores"], "scoreOf(address)", a(node), block=r["block"])
    if r.get("tx"):
        claimed = [lg for lg in receipt(r["tx"])["logs"] if lg["topics"][0] == topic(
            "Claimed(address,uint256,address,uint256,uint256,uint256,uint256)")]
        d = claimed[0]["data"][2:]
        r["claimed"] = {"payout": name("0x" + d[24:64]), "score": int(d[64:128], 16), "paid": int(d[128:192], 16),
                        "cut": int(d[192:256], 16), "pot": int(d[256:320], 16),
                        "node": name("0x" + claimed[0]["topics"][1][-40:]), "round": int(claimed[0]["topics"][2], 16)}
        check(f"{node}'s claim read the score scoreOf gives at its block", r["claimed"]["score"], r["fields"]["score"])
        CLAIM_SEEN[(node, earned)] = vobserve(node, r["block"])
    return r


def withdraw(holder: str, node: str, rounds: list[int], want: str | None = None, validator: bool = False) -> dict:
    """A holder's withdrawal of its parts of `node`'s claims, or with
    `validator` of `node`'s validator pots, and what it moved."""
    before = call(C["TestLPT"], "balanceOf(address)", a(holder))
    action = "withdraw_validator_parts" if validator else "withdraw_parts"
    r = stake(holder, {"action": action, "holder": holder, "node": node, "rounds": rounds},
              action.replace("_", "-"), a(node), ",".join(map(str, rounds)), want=want)
    r["moved"] = call(C["TestLPT"], "balanceOf(address)", a(holder)) - before
    return r


def observe_stake(node: str) -> tuple[int, int]:
    b = block_number()
    rnd = current_round(b)
    shown = (call(C["StakeLedger"], "stakeAt(address,uint256)", a(node), rnd, block=b),
             call(C["StakeLedger"], "totalStakeAt(uint256)", rnd, block=b))
    record({"action": "observe_stake", "node": node}, block=b, shown=shown)
    return shown


def keeper_steps() -> list[dict]:
    """Each round's share mint the keeper sent, as the chain shows it."""
    out = []
    for lg in logs(C["Claims"], "RoundSharesMinted(uint256,uint256,uint256)"):
        tx = rpc("eth_getTransactionByHash", [lg["transactionHash"]])
        out.append({"fields": {"action": "mint_shares", "caller": name(tx["from"]), "earned_round": int(lg["topics"][1], 16)},
                    "block": int(lg["blockNumber"], 16), "index": int(lg["transactionIndex"], 16),
                    "seq": 2 * 10**9 + int(lg["blockNumber"], 16), "tx": lg["transactionHash"]})
    return [s for s in out if s["tx"].lower() not in {r.get("tx", "").lower() for r in RECORDS}]


def members_at(r: int, block="latest") -> list[str]:
    """Scores' membersAt(r), by name, in alphabetical order."""
    w = words(C["Scores"], "membersAt(uint256)", r, block=block)
    return sorted(name("0x" + f"{x:064x}"[-40:]) for x in w[2:2 + w[1]])


def members() -> dict:
    """Each earning round's validator members, for every round whose shares
    were minted: the ledger case's stand-in for the validator set."""
    out = {}
    for s in keeper_steps():
        r = s["fields"]["earned_round"]
        if listed := members_at(r):
            out[str(r)] = listed
    return out


# --- the validators -----------------------------------------------------------

VRECORDS: list[dict] = []   # the observe steps of the validators case
CLAIM_SEEN: dict[tuple[str, int], dict] = {}   # (node, earned round) -> the observe step at its claim's block
SENT = re.compile(r"line (\d+): .*\(block \d+, tx (0x[0-9a-fA-F]{64})\)")
REFUSED = re.compile(r"line (\d+): .*: refused (\w+) at block (\d+)")


def script_lines() -> dict[int, dict]:
    """The script's acts by line number, as programs/validators numbers them."""
    out = {}
    for n, text in enumerate(SCRIPT.read_text().splitlines(), 1):
        f = text.split()
        if not f or f[0].startswith("#"):
            continue
        fields = {"action": "opt_in", "node": f[1]} if f[2] == "opt-in" else \
            {"action": "vote", "node": f[1], "target": f[2], "score": int(f[3])}
        out[n] = {"round": int(f[0]), "fields": fields}
    return out


def runner_acts() -> dict[int, dict]:
    """What the scripted validators did with each line, from their log: the
    transaction they sent, or the code of the refusal and the block it was
    asked at."""
    log = common.RUN / "logs" / "validators.log"
    out: dict[int, dict] = {}
    for text in (log.read_text().splitlines() if log.is_file() else []):
        if m := SENT.search(text):
            out[int(m[1])] = {"tx": m[2]}
        elif m := REFUSED.search(text):
            out[int(m[1])] = {"code": m[2], "block": int(m[3])}
    return out


def wait_runner(upto: int) -> None:
    due = [n for n, line in script_lines().items() if line["round"] <= upto]
    wait(f"the scripted validators to act on every line of rounds 1 to {upto}",
         lambda: all(n in runner_acts() for n in due))


def validator_steps() -> list[dict]:
    """Each line of the script as the validators sent it: a transaction,
    placed by its receipt, or a refusal, placed after its block."""
    acts, out = runner_acts(), []
    for n, line in script_lines().items():
        act = acts.get(n)
        if act is None:
            raise Failed(f"the scripted validators did not act on line {n} of {SCRIPT.name}")
        if "tx" in act:
            rc = receipt(act["tx"])
            out.append({"fields": dict(line["fields"]), "block": int(rc["blockNumber"], 16),
                        "index": int(rc["transactionIndex"], 16), "seq": n, "tx": act["tx"]})
        else:
            out.append({"fields": dict(line["fields"]), "block": act["block"], "index": None, "seq": n,
                        "code": act["code"]})
    return out


def vobserve(target: str, block: int | None = None) -> dict:
    """An observe step of the validators case: the members of the round and
    the target's score, as a claim at `block` reads them."""
    b = block or block_number()
    r = {"fields": {"action": "observe", "target": target}, "block": b, "index": None, "seq": 10**6 + len(VRECORDS),
         "shown": (members_at(current_round(b), b), call(C["Scores"], "scoreOf(address)", a(target), block=b))}
    VRECORDS.append(r)
    return r


def votes_on(target: str, upto_block: int) -> dict[str, int]:
    """The votes standing on `target` that the validators sent before a block."""
    out = {}
    for s in sorted(validator_steps(), key=lambda s: s["block"]):
        f = s["fields"]
        if f["action"] == "vote" and f["target"] == target and not s.get("code") and s["block"] <= upto_block:
            out[f["node"]] = f["score"]
    return out


def validators_run_case() -> tuple[dict, list[dict]]:
    """The run's validators case: the script's acts as sent and every
    observe step, against each validator's stake at the start of every round
    read from StakeLedger (a node is named once it has had stake)."""
    recs = validator_steps() + VRECORDS
    common.set_rounds(recs)
    ordered = ledgercase.order(recs)
    nodes = sorted({r["fields"]["node"] for r in recs if "node" in r["fields"]})
    stakes, ever = {}, set()
    for rnd in range(1, current_round() + 1):
        got = dict(zip(nodes, calls([(C["StakeLedger"], "stakeAt(address,uint256)", (a(n), rnd)) for n in nodes])))
        ever |= {n for n, v in got.items() if v}
        if ever:
            stakes[rnd] = {n: got[n] for n in sorted(ever)}
    return validatorcases.validators_case("validators-run", ordered, stakes), ordered


def validator_pay_run_cases() -> list[dict]:
    """One validator-pay case for each round whose shares were minted: its
    members and their stakes, cuts and bonds at the start of the round, what
    Claims paid each (validatorPayOf) and each holder's part
    (validatorPartOf), and what the mint sent the treasury beyond R-10's
    treasury amount (its Transfer events)."""
    bonded: dict[str, set] = {}
    for lg in logs(C["StakeLedger"], "Bonded(address,address,uint256,uint256)"):
        bonded.setdefault(name("0x" + lg["topics"][2][-40:]), set()).add(name("0x" + lg["topics"][1][-40:]))
    transfer = topic("Transfer(address,address,uint256)")
    out = []
    for lg in sorted(logs(C["Claims"], "RoundSharesMinted(uint256,uint256,uint256)"), key=lambda g: int(g["topics"][1], 16)):
        rnd = int(lg["topics"][1], 16)
        sent = sum(int(t["data"], 16) for t in receipt(lg["transactionHash"])["logs"]
                   if t["address"].lower() == C["TestLPT"].lower() and t["topics"][0] == transfer
                   and int(t["topics"][1], 16) == 0 and int(t["topics"][2], 16) == int(ADDR["Treasury"], 16))
        listed = members_at(rnd)
        members, splits, shown, paid = [], {}, {}, {}
        for m in listed:
            members.append((m, call(C["StakeLedger"], "stakeAt(address,uint256)", a(m), rnd)))
            paid[m], cut, pot = words(C["Claims"], "validatorPayOf(address,uint256)", a(m), rnd)
            bonds = {h: call(C["StakeLedger"], "bondAt(address,address,uint256)", a(h), a(m), rnd) for h in sorted(bonded.get(m, ()))}
            bonds = {h: b for h, b in bonds.items() if b}
            splits[m] = (call(C["StakeLedger"], "cutAt(address,uint256)", a(m), rnd), bonds)
            shown[m] = (cut, pot, {h: words(C["Claims"], "validatorPartOf(address,address,uint256)", a(h), a(m), rnd)[0] for h in bonds})
        out.append(validatorcases.validator_pay_case(f"validator-pay-round-{rnd}", rnd, members, splits, paid,
                                                     sent - int(lg["data"][66:130], 16), shown))
    return out


def calculate(kind: str, inputs: dict) -> dict:
    """The independent calculator's figures for a case's inputs."""
    from reference import Parameters, calculate as calc
    return ledgercase.strings(calc(kind, inputs, Parameters.from_strings(PARAMS), {}))


def round_inputs(rnd: int, scores: dict[str, int]) -> dict:
    """A round case for on-chain round `rnd`: every registered node with its
    recognized fees and its stake read from the chain, and the score each
    claim read. The fees and stakes must add up to the chain's totals."""
    fees = calls([(C["Broker"], "feesOf(uint256,address)", (rnd, a(n))) for n in REGISTERED])
    stakes = calls([(C["StakeLedger"], "stakeAt(address,uint256)", (a(n), rnd)) for n in REGISTERED])
    check(f"round {rnd}: the registered nodes' fees and stakes are the chain's totals",
          (sum(fees), sum(stakes)), (call(C["Broker"], "totalFees(uint256)", rnd), call(C["StakeLedger"], "totalStakeAt(uint256)", rnd)))
    return {"round": str(rnd - 1), "nodes": [{"node": n, "fees_micro_usdc": str(f), "stake_wei": str(st),
                                              "score": str(scores.get(n, 1_000_000))}
                                             for n, f, st in zip(REGISTERED, fees, stakes)]}


def side(label: str, chain, calc, fmt=str) -> None:
    """One figure read from the chain beside the calculator's."""
    mark = "equal" if chain == calc else "DIFFERS"
    say(f"  {label:<52}{fmt(chain):>26}{fmt(calc):>26}  {mark}")
    if chain != calc:
        FAILURES.append(f"{label}: chain {chain}, calculator {calc}")


def compare(what: str, chain: dict, calc: dict) -> tuple[int, int]:
    """Every figure of a case the demo wrote beside the calculator's: how
    many there are, and how many differ."""
    diffs = ledgercase.differences(chain, calc)
    for path, got, want in diffs:
        FAILURES.append(f"{what}: {path}: chain {got}, calculator {want}")
    return len(ledgercase.flatten(chain)), len(diffs)


# --- rounds -------------------------------------------------------------------

def start_keeper(*options: str) -> None:
    start("keeper", [str(common.RUN / "bin" / "lp2-keeper"), "--rpc-url", common.RPC, "--keystore-file", str(KEYS["Keeper"]),
                     "--keystore-password-file", str(common.RUN / "pw"), "--controller-address", C["Controller"],
                     "--claims-address", C["Claims"], "--poll", "1s", *options])


def wait(what: str, ready, seconds: float = 60) -> None:
    end = time.time() + seconds
    while time.time() < end:
        if ready():
            return
        time.sleep(0.5)
    raise Failed(f"timed out waiting for {what}")


def shares_minted(r: int) -> bool:
    return bool(call(C["Claims"], "sharesMinted(uint256)", r))


def next_round() -> int:
    """Mine to the end of the current round and wait for the keeper to start
    the next one and mint the ended round's shares."""
    r0 = current_round()
    left = round_started_at() + int(PARAMS["round_length_blocks"]) - block_number()
    if left > 0:
        mine(left)
    wait(f"the keeper to start round {r0 + 1}", lambda: current_round() == r0 + 1)
    wait(f"the keeper to mint round {r0}'s shares", lambda: shares_minted(r0))
    return r0 + 1


def to_round(target: int) -> None:
    while current_round() < target:
        next_round()
    say(f"  round {current_round()}")


def resume_keeper() -> int:
    """Restart a stopped keeper once the current round has lasted a round
    length, and wait for it to start the next round and mint the ended
    one's shares."""
    r0 = current_round()
    left = round_started_at() + int(PARAMS["round_length_blocks"]) - block_number()
    if left > 0:
        mine(left)
    start_keeper()
    wait(f"the keeper to start round {r0 + 1}", lambda: current_round() == r0 + 1)
    wait(f"the keeper to mint round {r0}'s shares", lambda: shares_minted(r0))
    return r0 + 1


# --- the explorer -------------------------------------------------------------

def start_explorer(name: str, port: int, index_dir, rpc_url: str, *more: str) -> None:
    """lp2-explorer with no node, reading the deployment from block 0."""
    start(name, [str(common.RUN / "bin" / "lp2-explorer"), "--rpc-url", rpc_url,
                 "--controller-address", C["Controller"], "--parameters-address", C["Parameters"],
                 "--stake-ledger-address", C["StakeLedger"], "--claims-address", C["Claims"],
                 "--usdc-address", C["MockUSDC"], "--lpt-address", C["TestLPT"], "--first-block", "0",
                 "--index-dir", str(index_dir), "--listen", f"127.0.0.1:{port}", *more])


def explorer_json(path: str, port: int | None = None) -> dict | None:
    """One of the explorer's documents, or None while it does not answer."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port or PORT['explorer']}{path}", timeout=30) as r:
            return json.loads(r.read())
    except (urllib.error.URLError, ConnectionError, OSError, ValueError):
        return None


def explorer_docs(block: int, port: int | None = None, seconds: float = 90) -> dict[str, dict]:
    """The explorer's five kinds of document, every one read at one block,
    `block` or later: /network.json, /orchestrators.json, /apps.json and
    each node's and payer's own, by path with the address in lower case."""
    def read() -> dict[str, dict] | None:
        docs = {p: explorer_json(p, port) for p in ("/network.json", "/orchestrators.json", "/apps.json")}
        if any(d is None or d["block"] is None or d["block"] < block for d in docs.values()):
            return None
        for row in docs["/orchestrators.json"]["orchestrators"]:
            docs[f"/orchestrators/{row['address'].lower()}.json"] = explorer_json(f"/orchestrators/{row['address']}.json", port)
        for row in docs["/apps.json"]["apps"]:
            docs[f"/apps/{row['address'].lower()}.json"] = explorer_json(f"/apps/{row['address']}.json", port)
        if any(d is None for d in docs.values()) or len({d["block"] for d in docs.values()}) != 1:
            return None
        return docs
    got: list[dict] = []
    wait(f"lp2-explorer's documents at block {block} or later", lambda: bool(got.append(read()) or got[-1]), seconds)
    return got[-1]


def beside(label: str, got, want, fmt=str) -> None:
    """A figure of the explorer's beside the run's, on lines of their own."""
    mark = "equal" if got == want else "DIFFERS"
    say(f"  {label}: {mark}\n    explorer:        {fmt(got)}\n    run, calculator: {fmt(want)}")
    if got != want:
        FAILURES.append(f"{label}: explorer {got}, run and calculator {want}")


def by_address(rows: list[dict], unknown: bool = False) -> dict[str, dict]:
    """Rows by the name of their address; with unknown, an address the run
    did not name keys its row by itself."""
    return {NAMES.get(r["address"].lower(), r["address"]) if unknown else name(r["address"]): r for r in rows}


def check_against_views(when: str) -> int:
    """Every registered node's score in the explorer equals scoreOf, and
    each payer's deposit and its pending release earmarkOf, at the
    explorer's block. Returns the round the explorer read."""
    docs = explorer_docs(block_number())
    block = docs["/network.json"]["block"]
    rows = by_address(docs["/orchestrators.json"]["orchestrators"])
    got = {n: int(rows[n]["score_now"]) for n in REGISTERED}
    check(f"explorer, {when}: every node's score is scoreOf at its block {block}", got,
          {n: call(C["Scores"], "scoreOf(address)", a(n), block=block) for n in REGISTERED})
    say(f"  the explorer at block {block}: scores " + ", ".join(f"{n} {v:,}" for n, v in got.items()))
    apps = by_address(docs["/apps.json"]["apps"])
    got = {p: [int(apps[p]["deposit_usdc_micro"]), int(apps[p]["deposit_pending_usdc_micro"])] for p in apps}
    check(f"explorer, {when}: each payer's deposit and its pending release are earmarkOf's at its block {block}", got,
          {p: words(C["Broker"], "earmarkOf(address,address)", a(p), ZERO, block=block)[:2] for p in apps})
    return docs["/network.json"]["round"]


def check_explorer(ordered: list[dict], calc: dict) -> None:
    """The explorer's figures against the figures the run checks against the
    calculator, and its sums against the views that hold the same totals."""
    say("\n14. The explorer's figures, beside the run's and the calculator's")
    docs = explorer_docs(block_number())
    net, block = docs["/network.json"], docs["/network.json"]["block"]
    check("explorer: no figure unavailable on any document", {p: d["unavailable"] for p, d in docs.items() if d["unavailable"]}, {})
    steps = ledgercase.steps(ordered)
    nodes = by_address(docs["/orchestrators.json"]["orchestrators"])
    check("explorer: the registered nodes, from their NodeRegistered events", sorted(nodes), sorted(REGISTERED))
    claims = [r for r in ordered if r["fields"]["action"] == "claim" and not r.get("code")]
    paid_by_round = {}
    for e in sorted({r["fields"]["earned_round"] for r in claims}):
        scores = {r["fields"]["node"]: int(r["fields"]["score"]) for r in claims if r["fields"]["earned_round"] == e}
        paid_by_round[e] = calculate("round", round_inputs(e, scores))["paid_wei"]
    for n in ("N", "M", "K", "L"):
        doc = docs[f"/orchestrators/{a(n).lower()}.json"]
        fees = {str(r["round"]): r["fees_usdc_micro"] for r in doc["fees_by_round"]}
        want = {r: f[n] for r, f in calc["fees_micro_usdc"].items() if n in f}
        beside(f"{n}: fees by round", fees, want, lambda v: ", ".join(f"r{k} {money(x)}" for k, x in sorted(v.items())))
        got = [(c["round"], int(c["score"]), int(c["paid_wei"])) for c in doc["claims"]]
        want = sorted((r["fields"]["earned_round"], int(r["fields"]["score"]), int(paid_by_round[r["fields"]["earned_round"]][n]))
                      for r in claims if r["fields"]["node"] == n)
        beside(f"{n}: claims (round, score, LPT paid)", got, want,
             lambda v: "; ".join(f"r{r} {s:,} {lpt(p)}" for r, s, p in v))
    apps = by_address(docs["/apps.json"]["apps"])
    check("explorer: the payers, from their events", sorted(apps), ["P", "P2"])
    split = {}
    for s in steps:
        if s["action"] == "redeem":
            cut = calculate("settlement", {"gross_micro_usdc": s["face_micro_usdc"], "payee_registered": True})
            f = split.setdefault((s["payer"], s["node"]), [0, 0, 0])
            for i, k in enumerate(("recognized_micro_usdc", "node_micro_usdc", "network_micro_usdc")):
                f[i] += int(cut[k])
    for p in ("P", "P2"):
        row = apps[p]
        funded = sum(int(s["deposit_micro_usdc"]) + int(s["earmark_micro_usdc"]) for s in steps
                     if s["action"] == "fund" and s["payer"] == p)
        beside(f"{p}: deposit now, funded (each fund step's deposit and earmark), released",
               [int(row[k]) for k in ("deposit_usdc_micro", "funded_usdc_micro", "released_usdc_micro")],
               [int(calc["deposits_micro_usdc"][p]), funded, int(calc["released_micro_usdc"][p])],
               lambda v: " / ".join(money(x) for x in v))
        check(f"explorer: {p}'s pending release is earmarkOf's at its block", int(row["deposit_pending_usdc_micro"]),
              words(C["Broker"], "earmarkOf(address,address)", a(p), ZERO, block=block)[1])
        got = {name(f["address"]): [int(f["fees_usdc_micro"]), int(f["node_usdc_micro"]), int(f["network_usdc_micro"])]
               for f in docs[f"/apps/{a(p).lower()}.json"]["by_node"]}
        want = {n: f for (q, n), f in split.items() if q == p}
        beside(f"{p}: by node, gross, node and network amounts", got, want,
             lambda v: "; ".join(f"{n} {'/'.join(money(x) for x in f)}" for n, f in sorted(v.items())))
    now = current_round(block)
    wanted = [(C["Broker"], "feesOf(uint256,address)", (r, a(n))) for r in range(1, now + 1) for n in REGISTERED]
    got_views = iter(calls(wanted + [(C["Broker"], "totalFees(uint256)", (r,)) for r in range(1, now + 1)], block))
    per_node = {(r, n): next(got_views) for r in range(1, now + 1) for n in REGISTERED}
    totals = {r: next(got_views) for r in range(1, now + 1)}
    shown = {(r["round"], n): int(r["fees_usdc_micro"]) for n in REGISTERED
             for r in docs[f"/orchestrators/{a(n).lower()}.json"]["fees_by_round"]}
    check(f"explorer: every node's fees in every round, summed from FeeSettled, equal feesOf at block {block}",
          {k: v for k, v in per_node.items() if v}, shown)
    check(f"explorer: all fees in every round equal totalFees at block {block}",
          {r: v for r, v in totals.items() if v}, {r["round"]: int(r["fees_usdc_micro"]) for r in net["fees_by_round"]})
    check_against_views("at the end")


def check_restart() -> None:
    """A second explorer, reading through a counting stand-in provider in
    pages of 10 blocks, killed with SIGKILL while it catches up and started
    again: it resumes above the first block, asks for no logs below where
    it resumed, and shows the figures of the explorer never stopped."""
    say("\n15. An explorer killed while it catches up, and started again")
    stop("keeper")
    say("  the keeper is stopped, so nothing changes on the chain while the two explorers are compared")
    provider = common.Provider("127.0.0.1", PORT["provider"])
    url, port, folder = f"http://127.0.0.1:{PORT['provider']}", PORT["explorer_restarted"], common.RUN / "explorer-restarted-index"
    pace = ("--log-page-blocks", "10", "--requests-per-second", "25")
    head = block_number()
    start_explorer("explorer-restarted", port, folder, url, *pace)
    progress: list[dict] = []

    def caught_some() -> bool:
        doc = explorer_json("/network.json", port)
        progress.append(doc["index"] if doc else {})
        return (progress[-1].get("through") or 0) >= 100

    wait("the second explorer to commit its first hundred blocks", caught_some, 120)
    os.killpg(common.PROCS["explorer-restarted"].pid, signal.SIGKILL)
    common.PROCS.pop("explorer-restarted").wait()
    killed = progress[-1]["through"]
    check(f"killed while catching up: its last progress, block {killed}, is above the first block and below the newest, {head}",
          0 < killed < head, True)
    before = len(provider.log_from_blocks)
    start_explorer("explorer-restarted", port, folder, url, *pace)
    restarted = explorer_docs(head, port, 300)
    main = explorer_docs(restarted["/network.json"]["block"])
    resumed = restarted["/network.json"]["index"]["resumed_from"]
    asked = provider.log_from_blocks[before:]
    say(f"  killed at block {killed} of {head}; started again, it resumed from block {resumed}, and read {len(asked)} pages")
    check("started again: index.resumed_from is above the first block, after what it had committed",
          resumed is not None and resumed > killed, True)
    floor = resumed if resumed is not None else killed + 1
    check(f"the counting provider saw no eth_getLogs below block {floor} after the restart, where the index resumed",
          [b for b in asked if b < floor], [])
    blocks = sorted({main["/network.json"]["block"], restarted["/network.json"]["block"]})
    sent = sum(len(b["result"]["transactions"]) for b in rpc_batch([("eth_getBlockByNumber", [hex(n), False])
                                                                     for n in range(blocks[0] + 1, blocks[-1] + 1)]))
    check(f"no transaction between the two explorers' blocks, {blocks[0]} and {blocks[-1]}: the chain's state is the same", sent, 0)
    strip = lambda d: {k: v for k, v in d.items() if k not in ("rpc", "index", "block", "block_time")}  # noqa: E731
    differ = [p for p in main if strip(main[p]) != strip(restarted.get(p, {}))]
    check(f"the restarted explorer's {len(main)} documents equal those of the explorer never stopped", differ, [])
    provider.refuse()


# --- the demo -----------------------------------------------------------------

def main() -> int:
    open_log()
    started = time.time()
    say("Rewards demo: one full reward cycle, stake, scores, claims and LPT, and the scripted")
    say("validators: their votes, a seat changing hands and their pay, on a fresh local chain")
    say(f"Stand-ins, from deployments/rewards-local.json: {'; '.join(s['stand_in'] for s in MANIFEST['stand_ins'])}.")

    say("\n1. Setting up")
    preflight(["chain", "orch", "orch_metrics", "orch_m", "orch_m_metrics", "orch_k", "orch_k_metrics",
               "orch_l", "orch_l_metrics", "signer_p", "signer_p_metrics", "signer_p2", "signer_p2_metrics", "model",
               "explorer", "explorer_restarted", "provider"])
    node_bin = fetch_node()
    model = fetch_model()
    build_ours()
    say(f"  node {MANIFEST['node']['commit'][:12]} (unmodified), model sha256 {MANIFEST['model']['sha256'][:12]}, contracts and programs built")
    face = ticket_face()
    check("the ticket face the manifest gives (job price + 1) equals ledger-cycle's 1 USDC",
          face, int(cycle_step(action="redeem")["face_micro_usdc"]))

    start_chain()
    pw = common.RUN / "pw"
    pw.write_text("pw-" + os.urandom(8).hex())
    pw.chmod(0o600)
    people = ["deployer", "N", "M", "K", "L", "W", "O", "D", "E", "F", "H", "P", "P2", *VALIDATORS, "Keeper", "Anyone"]
    for who in people:
        new_key(who)
    treasury = "0x" + os.urandom(20).hex()
    ADDR["Treasury"], NAMES[treasury.lower()] = treasury, "Treasury"
    for who in people:
        if who not in ("deployer", "W"):
            KEYS[who] = keystore(who, pw)
    deploy_rewards()
    (common.RUN / "addresses.json").write_text(json.dumps({"contracts": C, "implementations": common.IMPL, "accounts": dict(ADDR)}, indent=1))
    say(f"  local chain on {common.RPC} (chain id {MANIFEST['chain']['chain_id']}); throwaway keys for nodes N, M, K and L, N's payout W,")
    say("  holders O, D, E, F and H, payers P and P2, validators V1 to V4, the keeper and an anyone; the treasury is an address with no key")
    say("  contracts: " + ", ".join(f"{k} {v}" for k, v in C.items()))
    check("RoundClock.roundLength() equals the shared examples' round length", call(C["RoundClock"], "roundLength()"),
          int(PARAMS["round_length_blocks"]))
    check("test LPT's supply is the manifest's genesis supply, held by the deployer",
          (call(C["TestLPT"], "totalSupply()"), call(C["TestLPT"], "balanceOf(address)", a("deployer"))),
          (int(MANIFEST["lpt"]["genesis_supply_wei"]),) * 2)
    refused = rpc_batch([("eth_call", [{"from": a("deployer"), "to": C["TestLPT"],
                                        "data": calldata("mint(address,uint256)", a("deployer"), 1)}, "latest"])])[0]
    check("a mint of test LPT by the deployer is refused NOT_AUTHORIZED",
          (refused.get("error") or {}).get("data", "")[:10], sel("NOT_AUTHORIZED()"))

    start_model(model)
    for n, (port, metrics) in ORCHS.items():
        start_orchestrator(f"orchestrator-{n}", n, node_bin, KEYS[n], pw, PORT[port], PORT[metrics])
    start_signer("P", node_bin, KEYS["P"], pw, PORT["signer_p"], PORT["signer_p_metrics"], [PORT[p] for p, _ in ORCHS.values()])
    start_signer("P2", node_bin, KEYS["P2"], pw, PORT["signer_p2"], PORT["signer_p2_metrics"], [PORT[p] for p, _ in ORCHS.values()])
    say(f"  model server on port {PORT['model']}; an orchestrator for each of N, M, K and L; a signer for each of P and P2")
    start_explorer("explorer", PORT["explorer"], common.RUN / "explorer-index", common.RPC, "--requests-per-second", "500")
    say(f"  lp2-explorer, with no node, on port {PORT['explorer']}: its index of events in {common.where(common.RUN / 'explorer-index')}")

    # --- round 1: nodes, holders and the payer
    say("\n2. Round 1: four nodes register, four holders bond test LPT to them, the payer funds")
    for n in REGISTERED:
        tx_step({"action": "register", "node": n}, n, C["Registry"], "register()")
    stake("N", {"action": "set_payout", "node": "N", "payout": "W"}, "set-payout", a("N"), a("W"))
    cut = int(cycle_step(action="set_cut")["cut_millionths"])
    stake("N", {"action": "set_cut", "node": "N", "cut_millionths": cut}, "set-cut", a("N"), str(cut))
    grants = {h: amount(action="grant_lpt", to=h) for h in ("O", "D", "E", "F")} | \
        {"V1": V1_OWN, "H": H_BOND, "V2": SEATED["V2"], "V3": SEATED["V3"], "V4": V4_STAKE, "L": L_STAKE}
    for h, wei in grants.items():
        tx_step({"action": "grant_lpt", "to": h, "amount_wei": wei}, "deployer", C["TestLPT"], "transfer(address,uint256)", a(h), wei)
    for h, n in (("O", "N"), ("D", "N"), ("E", "M"), ("F", "K")):
        bond(h, n, amount(action="bond", holder=h, node=n))
    say(f"  N, M, K and L register; N pays to W and sets a reward cut of {cut:,} millionths; V1 to V4 register too")
    say(f"  bonded: O {lpt(amount(action='bond', holder='O', node='N'))} and D {lpt(amount(action='bond', holder='D', node='N'))} to N, "
        f"E {lpt(amount(action='bond', holder='E', node='M'))} to M, F {lpt(amount(action='bond', holder='F', node='K'))} to K")
    deposit = int(cycle_step(action="fund")["deposit_micro_usdc"])
    fund("P", deposit, 0, None)
    for n in ORCHS:
        fund("P", 0, face, n)
    say(f"  P deposits {money(deposit)} micro-USDC and earmarks {money(face)} for each node: the node will not take a payer")
    say("  who has set nothing aside for it (the payments demo's refusal 2), so ledger-cycle's payer, with no earmark, could not pay here")

    names_file = common.RUN / "names.json"
    names_file.write_text(json.dumps({n: ADDR[n] for n in REGISTERED}))
    start("validators", [str(common.RUN / "bin" / "lp2-validators"), "--rpc-url", common.RPC, "--keystore-password-file", str(pw),
                         "--controller-address", C["Controller"], "--scores-address", C["Scores"],
                         "--script", str(SCRIPT), "--names", str(names_file), "--poll", "1s",
                         *[f"--keystore={v}={KEYS[v]}" for v in VALIDATORS]])
    say(f"  the scripted validators start (programs/validators, script {SCRIPT.relative_to(common.ROOT)})")
    start_keeper()
    say("  the keeper starts: it starts each round once 50 blocks have passed, then mints the ended round's shares")
    next_round()
    say(f"  round {current_round()}: the keeper started it and minted round 1's validator and treasury amounts")

    # --- round 2: five paid relays, with the keeper stopped
    say("\n3. Round 2: five paid relays of 1 USDC each, with the keeper stopped as the round ends")
    stop("keeper")
    say("  the keeper is stopped: round 2 lasts until somebody starts round 3")
    for i, n in enumerate(("N", "N", "N", "M", "K"), 1):
        answer = job("P", PORT[ORCHS[n][0]], PORT["signer_p"])
        say(f"  relay {i}, to {n}: paid, the model answered {answer[:50]!r}")
    settled = wait_settled(5, "five relays")
    for s in settled:
        payout = name(f"0x{call(C['Registry'], 'payoutOf(address)', s['recipient']):040x}")
        say(f"  {name(s['recipient'])} redeemed a ticket of {money(s['face'])} in round {s['round']}: "
            f"its payout {payout} +{money(s['node_amount'])}, network +{money(s['network_amount'])}")
    check("every relay was redeemed in round 2", sorted({s["round"] for s in settled}), [2])
    bond("O", "N", amount(action="bond", holder="O", node="N", round="2"))
    bond("V1", "V1", V1_OWN)
    bond("H", "V1", H_BOND)
    for v in ("V2", "V3"):
        bond(v, v, SEATED[v])
    bond("V4", "V4", V4_STAKE)
    bond("L", "L", L_STAKE)
    stake("V1", {"action": "set_cut", "node": "V1", "cut_millionths": V1_CUT}, "set-cut", a("V1"), str(V1_CUT))
    stake("L", {"action": "set_cut", "node": "L", "cut_millionths": 1_000_000}, "set-cut", a("L"), "1000000")
    stake_n = observe_stake("N")
    say(f"  O bonds a further {lpt(amount(action='bond', holder='O', node='N', round='2'))} to N; all of these count from round 3:")
    say(f"  V1 bonds {lpt(V1_OWN)} to itself and H {lpt(H_BOND)} to V1, which sets a reward cut of {V1_CUT:,} millionths;")
    say(f"  V2 {lpt(SEATED['V2'])}, V3 {lpt(SEATED['V3'])} and V4 {lpt(V4_STAKE)} bond to themselves, cut 0;")
    say(f"  L bonds {lpt(L_STAKE)} to itself and keeps its whole reward (cut 100 %)")
    say(f"  N's stake for round 2: {lpt(stake_n[0])} of {lpt(stake_n[1])}")

    length = int(PARAMS["round_length_blocks"])
    mine(round_started_at() + 2 * length - block_number())
    say(f"\n4. The keeper stopped: round 2 has lasted {block_number() - round_started_at()} blocks, two round lengths")
    check("round 2 has not ended: nobody started round 3", current_round(), 2)
    check("round 2's shares are not minted", shares_minted(2), False)
    stake("Anyone", {"action": "mint_shares", "caller": "Anyone", "earned_round": 2}, "mint-shares", "2",
          want="CLAIM_TOO_EARLY")
    r3 = send(KEY["Anyone"], C["RoundClock"], "initializeRound()")
    say(f"  anyone may start a round (R-18): Anyone starts round 3 in block {int(r3['blockNumber'], 16)}, with the keeper still stopped")
    mine(10)
    check("round 3 has started", current_round(), 3)
    check("round 2 has ended and its shares are due, but nobody has minted them", shares_minted(2), False)
    start_keeper()
    wait("the restarted keeper to mint round 2's shares", lambda: shares_minted(2))
    mint2 = next(s for s in keeper_steps() if s["fields"]["earned_round"] == 2)
    d = next(lg for lg in receipt(mint2["tx"])["logs"] if lg["topics"][0] == topic("RoundSharesMinted(uint256,uint256,uint256)"))["data"][2:]
    got = (int(d[0:64], 16), int(d[64:128], 16))
    want = CASES["ceiling-1-shares"]["expected"]
    say(f"  the keeper runs again: it finds round 2's shares unminted and, in block {mint2['block']}, mints its validator amount "
        f"{lpt(got[0])} and treasury amount {lpt(got[1])}")
    check("round 2's shares, minted late, are the full amounts (ceiling-1-shares)", got,
          (int(want["validators_wei"]), int(want["treasury_wei"])))
    check("minted by the keeper, in round 3", (mint2["fields"]["caller"], current_round(mint2["block"])), ("Keeper", 3))
    stake("Anyone", {"action": "mint_shares", "caller": "Anyone", "earned_round": 2}, "mint-shares", "2",
          want="ALREADY_CLAIMED")
    stake_n3 = observe_stake("N")
    say(f"  N's stake for round 3: {lpt(stake_n3[0])} (O's further bond counts now)")

    # --- the validators
    say("\n5. Round 3: the scripted validators V1 to V3 opt in")
    wait_runner(3)
    for lg in logs(C["Scores"], "Seated(address,uint256,address)"):
        say(f"  {name('0x' + lg['topics'][1][-40:])} opts in in round 3 and is seated from round {int(lg['data'][2:66], 16)}")
    check("V1 to V3 are seated from round 4", members_at(4), ["V1", "V2", "V3"])
    next_round()

    say("\n6. Round 4: the votes, V4 takes V3's seat, and N, M, K and L each earn")
    stop("keeper")
    say("  the keeper is stopped again, so round 4 lasts until the nodes have redeemed their tickets, 41 blocks after the relays")
    wait_runner(4)
    for lg in logs(C["Scores"], "Voted(address,address,uint256,uint256)"):
        say(f"  {name('0x' + lg['topics'][1][-40:])} votes {int(lg['data'][2:66], 16):,} on {name('0x' + lg['topics'][2][-40:])} in round {int(lg['data'][66:130], 16)}")
    seat4 = [lg for lg in logs(C["Scores"], "Seated(address,uint256,address)") if name("0x" + lg["topics"][1][-40:]) == "V4"]
    v4 = [(int(lg["data"][2:66], 16), name("0x" + lg["data"][-40:])) for lg in seat4]
    say(f"  V4 opts in in round 4 and is seated from round {v4[0][0] if v4 else '?'}, in place of {v4[0][1] if v4 else '?'}, "
        "the smallest member: a fourth seat does not exist (ValidatorSetSize 3)")
    check("V4's opt-in seats it from round 5 in place of V3 (Seated)", v4, [(5, "V3")])
    refusals = [(script_lines()[n]["fields"], act["code"]) for n, act in sorted(runner_acts().items()) if "code" in act]
    for f, code in refusals:
        say(f"  {f['node']}'s vote of {f['score']:,} on {f['target']} is refused {code}: a member only from round 5, it cannot vote in round 4")
    check("the scripted validators' refusals: V4's vote in round 4", [(f["node"], c) for f, c in refusals], [("V4", "NOT_VALIDATOR")])
    earners = ("N", "M", "K", "L")
    payer_of = {"N": "P", "M": "P2", "K": "P", "L": "P2"}
    fund("P", 2 * face, 0, None)
    fund("P2", 4 * face, 0, None)
    for n in ("M", "L"):
        fund("P2", 0, face, n)
    say(f"  P deposits {money(2 * face)} micro-USDC more; P2 deposits {money(4 * face)} and earmarks {money(face)} for each of M and L")
    for i, n in enumerate(earners, 6):
        payer = payer_of[n]
        answer = job(payer, PORT[ORCHS[n][0]], PORT[f"signer_{payer.lower()}"])
        say(f"  relay {i}, to {n}, paid by {payer}: the model answered {answer[:50]!r}")
    settled4 = wait_settled(5 + len(earners), "round 4's relays")[5:]
    for s in settled4:
        payout = name(f"0x{call(C['Registry'], 'payoutOf(address)', s['recipient']):040x}")
        say(f"  {name(s['recipient'])} redeemed a ticket of {money(s['face'])} in round {s['round']}: its payout {payout} +{money(s['node_amount'])}")
    check("N's, M's, K's and L's relays were redeemed in round 4", sorted((name(s["recipient"]), s["round"]) for s in settled4),
          sorted((n, 4) for n in earners))
    request_release("P2", None, face)
    say(f"  P2 asks for {money(face)} of its deposit back, ready from round 7")
    for n in ("N", "M", "L", "K"):
        vobserve(n)
    say(f"  members of round 4: {', '.join(members_at(4))}; of round 5, as they stand: {', '.join(members_at(5))}")
    say("  scores now: " + ", ".join(f"{r['fields']['target']} {r['shown'][1]:,}" for r in VRECORDS[-4:]))
    check_against_views("after round 4's votes")
    resume_keeper()

    say("\n7. Round 5: round 4's validator amount, divided among V1, V2 and V3 by stake")
    shares4 = next(lg for lg in logs(C["Claims"], "RoundSharesMinted(uint256,uint256,uint256)") if int(lg["topics"][1], 16) == 4)
    say(f"  the keeper mints round 4's validator amount, {lpt(int(shares4['data'][2:66], 16))}, among its members:")
    for lg in logs(C["Claims"], "ValidatorPaid(uint256,address,address,uint256,uint256,uint256)"):
        if int(lg["topics"][1], 16) == 4:
            w = [int(lg["data"][2 + 64 * i:66 + 64 * i], 16) for i in range(4)]
            say(f"  {name('0x' + lg['topics'][2][-40:])}: paid {lpt(w[1])}; its cut {lpt(w[2])} to {name(f'0x{w[0]:040x}')}, its pot {lpt(w[3])} to Claims")
    withdrawn = {h: withdraw(h, "V1", [4], validator=True)["moved"] for h in ("V1", "H")}
    say(f"  V1 and H withdraw their parts of V1's round 4 pot: V1 {lpt(withdrawn['V1'])}, H {lpt(withdrawn['H'])}")
    withdraw("H", "V1", [4], want="ALREADY_CLAIMED", validator=True)
    say("  V2 and V3 leave their pay in Claims, for themselves to withdraw")
    seen5 = vobserve("K")
    say(f"  members of round 5: {', '.join(seen5['shown'][0])}; K's score {seen5['shown'][1]:,}: V1 and V2 still vote K 0")
    stop("keeper")  # round 5 lasts while the explorer reads it
    check("the explorer read round 5", check_against_views("in round 5, V4 seated in V3's place with no new vote"), 5)
    start_keeper()

    # --- claims
    say("\n8. Round 8: N's claim for round 2 is too early")
    to_round(8)
    claim("N", "N", 2, want="CLAIM_TOO_EARLY")
    claim("M", "N", 2, want="NOT_AUTHORIZED")
    release("P2", None)
    say(f"  P2 takes the {money(face)} back")

    say("\n9. Round 9: the claims")
    to_round(9)
    claim("M", "N", 2, want="NOT_AUTHORIZED")
    claim("D", "N", 2, want="NOT_AUTHORIZED")
    cn = claim("N", "N", 2)["claimed"]
    say(f"  N claims at score {cn['score']:,}: paid {lpt(cn['paid'])}; the cut {lpt(cn['cut'])} to {cn['payout']}, the pot {lpt(cn['pot'])} to Claims")
    claim("N", "N", 2, want="ALREADY_CLAIMED")
    for h in ("O", "D"):
        r = withdraw(h, "N", [2])
        say(f"  {h} withdraws its part of N's round 2 pot from Claims: {lpt(r['moved'])}")
    withdraw("D", "N", [2], want="ALREADY_CLAIMED")
    cm = claim("M", "M", 2)["claimed"]
    say(f"  M claims at score {cm['score']:,}: paid {lpt(cm['paid'])}, all of it to Claims as E's part (cut 0); E does not withdraw")
    ck = claim("K", "K", 2)["claimed"]
    say(f"  K claims at score {ck['score']:,}: paid {lpt(ck['paid'])}, and its entitlement closes")
    move = amount(action="transfer", holder="F")
    stake("F", {"action": "transfer", "holder": "F", "from": "K", "to": "M", "amount_wei": move},
          "transfer", a("K"), a("M"), str(move))
    say(f"  F starts to leave K: it moves {lpt(move)} toward M, arriving in round 30")

    say("\n10. Rounds 10 to 12: F unbonds part of the move and rebonds it, which can go back only to K; the round 4 claims")
    to_round(10)
    observe_stake("K")
    part = amount(action="unbond_transfer", holder="F")
    stake("F", {"action": "unbond_transfer", "holder": "F", "from": "K", "to": "M", "amount_wei": part},
          "unbond-transfer", a("K"), a("M"), str(part))
    say(f"  F unbonds {lpt(part)} of the move before it arrives")
    to_round(11)
    observe_stake("K")
    back = amount(action="rebond", holder="F", node="K")
    stake("F", {"action": "rebond", "holder": "F", "node": "M", "amount_wei": back}, "rebond", a("M"), str(back),
          want="AMOUNT_TOO_LARGE")
    stake("F", {"action": "rebond", "holder": "F", "node": "K", "amount_wei": back}, "rebond", a("K"), str(back))
    say(f"  F rebonds {lpt(back)}: to M it is refused, to K, where it was counting, it is taken")
    c4 = {}
    for n, why in (("N", "the cut to W, the pot to Claims"), ("M", "all of it to Claims as E's part (cut 0)"),
                   ("L", "all of it to L (cut 100 %): V1 alone voted L 0, and one vote cannot cut a node"),
                   ("K", "and its entitlement closes")):
        c4[n] = claim(n, n, 4)["claimed"]
        say(f"  {n} claims its round 4 reward at score {c4[n]['score']:,}: paid {lpt(c4[n]['paid'])}, {why}")
    to_round(12)
    observe_stake("K")

    # --- the reconciliation
    say("\n11. Every figure, read from the chain, beside the calculator's")
    extra = keeper_steps()
    m = members()
    ordered, views, calc = reconcile("At the end of the run", extra, m)
    say("  against ledger-cycle in the shared examples:")
    want = CYCLE["expected"]
    check("W's LPT: ledger-cycle's (N's round 2 cut), plus N's round 4 cut", views["lpt"]["W"],
          int(want["lpt_wei"]["W"]) + c4["N"]["cut"])
    for who in ("O", "D"):
        check(f"{who}'s LPT", views["lpt"][who], int(want["lpt_wei"][who]))
    pots = sum(int(lg["data"][-64:], 16) for lg in logs(C["Claims"], "ValidatorPaid(uint256,address,address,uint256,uint256,uint256)"))
    taken = sum(int(lg["data"][66:130], 16) for lg in logs(C["Claims"], "ValidatorPartsWithdrawn(address,address,uint256[],uint256)"))
    pots4 = sum(c["pot"] for c in c4.values())
    say(f"  Claims holds ledger-cycle's {lpt(int(want['lpt_wei']['Claims']))} (E's part), plus the pots of the round 4 claims, {lpt(pots4)},")
    say(f"  plus the validator pots of rounds 4 to 11, {lpt(pots)}, less the validator parts withdrawn, {lpt(taken)} (R-14, R-24)")
    check("Claims's LPT: ledger-cycle's, plus the round 4 claims' pots and the validator pots, less the validator parts withdrawn",
          views["lpt"]["Claims"], int(want["lpt_wei"]["Claims"]) + pots4 + pots - taken)
    n4 = sum(s["node_amount"] for s in settled4 if name(s["recipient"]) == "N")
    check("W's USDC: ledger-cycle's (N's half of its three round 2 relays), plus N's half of its round 4 relay",
          views["wallet"]["W"], int(want["paid_micro_usdc"]["W"]) + n4)
    check("LPT minted for round 2", views["minted_for_round"].get(2), int(want["minted_for_round_wei"]["2"]))
    ceiling = int(CASES["ceiling-1"]["expected"]["ceiling_wei"])
    never = ceiling - views["minted_for_round"][2]
    calc_never = int(calc_ceiling(1)) - int(calc["minted_for_round_wei"]["2"])
    say(f"  {'LPT never minted for round 2, C(1) less the above':<46}{lpt(never):>28}{lpt(calc_never):>28}  "
        f"{'equal' if never == calc_never else 'DIFFERS'}")
    check("LPT never minted for round 2 (ledger-cycle)", never, 544_750_300_038_956_261_284)
    shown = [r for r in ordered if r["fields"]["action"] == "observe_stake"]
    check("N's stake in rounds 2 and 3", [(r["round"], r["shown"][0]) for r in shown if r["fields"]["node"] == "N"],
          [(2, 400 * LPT), (3, 500 * LPT)])
    check("K's stake in rounds 10, 11 and 12", [(r["round"], r["shown"][0]) for r in shown if r["fields"]["node"] == "K"],
          [(10, 250 * LPT), (11, 210 * LPT), (12, 250 * LPT)])
    check("the scores the round 2 claims read: N, M, K",
          [r["fields"]["score"] for r in ordered if r["fields"]["action"] == "claim" and not r.get("code")
           and r["fields"]["earned_round"] == 2], [1_000_000, 500_000, 0])
    check("K's entitlement for round 2 is closed, paid 0 (entitlementOf)",
          words(C["Claims"], "entitlementOf(address,uint256)", a("K"), 2)[:2], [1, 0])
    check("F's unbonded LPT waiting and ready: none (unbondedOf)", words(C["StakeLedger"], "unbondedOf(address)", a("F")), [0, 0])

    vcase, vordered = validators_run_case()
    pay_cases = validator_pay_run_cases()
    show_validator_rewards(ordered, vcase, pay_cases, withdrawn)

    say("\n13. The run as cases, reproduced by the calculator")
    scan(ordered, ["Registry", "Broker", "StakeLedger", "Claims"])
    scan(vordered, ["Scores"])
    write_case("ledger-cycle-run", ordered, views, m)
    write_cases("validators-case.json", [vcase], f"{len(vcase['inputs']['steps'])} steps")
    write_cases("validator-pay-cases.json", pay_cases, f"one for each of rounds 1 to {len(pay_cases)}")

    check_explorer(ordered, calc)
    check_restart()
    return finish(started, "one reward cycle and the scripted validators, every refusal seen, every figure read from the chain equals the calculator's.")


# The validator reward example: four nodes' claims of rewards earned in one round.
CLAIM_ROWS = (("A", "N", 4), ("B", "M", 4), ("C", "L", 4), ("D", "K", 4))


def described(votes: dict[str, int]) -> str:
    """the validator example's "votes cast" column."""
    if not votes:
        return "none"
    who = " and ".join(sorted(votes)) if len(votes) > 1 else f"{next(iter(votes))} alone"
    return f"{who}: " + "/".join(sorted({f"{v:,}" for v in votes.values()}))


def show_validator_rewards(ordered: list[dict], vcase: dict, pay_cases: list[dict], withdrawn: dict[str, int]) -> None:
    """The validator reward example, read from the chain beside the calculator's:
    the four claims, V4 taking V3's seat, round 4's validator mint and the
    withdrawals from V1's pot."""
    say("\n12. Validator rewards, read from the chain beside the calculator's")
    vcalc = calculate("validators", vcase["inputs"])
    seen = {o["step"]: o for o in vcalc["observed"]}
    claims = {(r["fields"]["node"], r["fields"]["earned_round"]): r for r in ordered
              if r["fields"]["action"] == "claim" and not r.get("code")}
    rows = [(row, claims[(n, e)]["claimed"]["node"], claims[(n, e)]["claimed"]["round"]) for row, n, e in CLAIM_ROWS]
    check("Four nodes claimed rewards earned in the same round (their Claimed events)",
          validatorcases.one_earning_round(rows), 4)
    rounds = {e: calculate("round", round_inputs(e, {n: int(seen[CLAIM_SEEN[(n, e)]["step"]]["score"])
                                                     for _, n, r in CLAIM_ROWS if r == e}))
              for e in sorted({r for _, _, r in CLAIM_ROWS})}
    say(f"  {'figure (score in millionths; LPT)':<52}{'chain':>26}{'calculator':>26}")
    for row, n, earned in CLAIM_ROWS:
        cl = claims[(n, earned)]
        votes = votes_on(n, cl["block"])
        side(f"{row}: {n}, votes {described(votes)}; median at claim", int(cl["claimed"]["score"]),
             int(seen[CLAIM_SEEN[(n, earned)]["step"]]["score"]), money)
        side(f"{row}: {n}'s reward for round {earned}, claimed in round {cl['round']}", int(cl["claimed"]["paid"]),
             int(rounds[earned]["paid_wei"][n]), lpt)
    check("D: K's entitlement for round 4 is closed, paid 0 (entitlementOf)",
          words(C["Claims"], "entitlementOf(address,uint256)", a("K"), 4)[:2], [1, 0])
    obs = [r for r in VRECORDS if r["fields"]["target"] == "K" and current_round(r["block"]) == 5]
    for rnd in (4, 5):
        r = next(r for r in VRECORDS if current_round(r["block"]) == rnd)
        side(f"members of round {rnd}", ", ".join(r["shown"][0]), ", ".join(seen[r["step"]]["members"]))
    side("K's score in round 5, V4 seated", obs[0]["shown"][1], int(seen[obs[0]["step"]]["score"]), money)
    pay = next(c for c in pay_cases if c["inputs"]["round"] == "3")  # on-chain round 4
    got, calc = pay["expected"], calculate("validator-pay", pay["inputs"])
    for mm in pay["inputs"]["members"]:
        side(f"round 4: {mm['node']}'s validator pay, stake {int(mm['stake_wei']) // LPT:,} LPT",
             int(got["paid_wei"][mm["node"]]), int(calc["paid_wei"][mm["node"]]), lpt)
    side("round 4: to the treasury beyond its treasury amount", int(got["to_treasury_wei"]), int(calc["to_treasury_wei"]), lpt)
    v1, c1 = got["splits"]["V1"], calc["splits"]["V1"]
    side(f"round 4: V1's cut ({V1_CUT // 10_000} %), to its payout", int(v1["cut_wei"]), int(c1["cut_wei"]), lpt)
    side("round 4: V1's pot, to Claims", int(v1["pot_wei"]), int(c1["pot_wei"]), lpt)
    for h in ("V1", "H"):
        side(f"round 4: {h}'s part of V1's pot (validatorPartOf)", int(v1["parts_wei"][h]), int(c1["parts_wei"][h]), lpt)
        side(f"round 4: what {h} withdrew of V1's pot", withdrawn[h], int(c1["parts_wei"][h]), lpt)
    side("round 4: dust of V1's pot, kept in Claims", int(v1["dust_wei"]), int(c1["dust_wei"]), lpt)
    count, differ = compare("the validators case", vcase["expected"], vcalc)
    for c in pay_cases:
        n, d = compare(c["id"], c["expected"], calculate("validator-pay", c["inputs"]))
        count, differ = count + n, differ + d
    say(f"  every figure of the validators case and the {len(pay_cases)} validator-pay cases: {count} figures, "
        f"{'all equal' if not differ else f'{differ} DIFFER'}")


def write_cases(file: str, cases: list[dict], what: str) -> None:
    """Cases the run wrote, checked by ./calculator/check."""
    out = common.RUN / file
    out.write_text(json.dumps(ledgercase.document(VECTORS, cases), indent=1) + "\n")
    done = subprocess.run([str(common.ROOT / "calculator" / "check"), str(out)], capture_output=True, text=True)
    say(f"  wrote {out.relative_to(common.ROOT)}: {len(cases)} {cases[0]['kind']} case{'s' if len(cases) > 1 else ''}, {what}")
    for line in done.stdout.strip().splitlines()[-1:]:
        say(f"  ./calculator/check {out.relative_to(common.ROOT)}: {line[:150]}")
    check(f"the calculator reproduces {file}", done.returncode, 0)


def calc_ceiling(r: int) -> str:
    from reference import Parameters, calculate
    return str(calculate("ceiling", {"round": str(r)}, Parameters.from_strings(PARAMS), {})["ceiling_wei"])
