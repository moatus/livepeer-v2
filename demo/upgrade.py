"""./dev demo upgrade: the Broker's code replaced in place on a running full
system by demo/public-run upgrade, with every stored figure and address
kept, then a stop drill; on a fresh local chain.

Deploys the full system behind its proxies, with the upgrade key U and the
pause key S as throwaway keystores outside the repository, and runs N's
node, P's signer, the keeper and the validators V1 to V3. Before the
upgrade N is paid in several rounds, claims its first earned round r1 and
leaves the next, r2, unclaimed. Then demo/public-run upgrade, the command itself, run as a program:

  1. refuses, before it sends anything, new code whose layout does not
     begin with the Broker's (a counter declared first), new code that
     reorders the members of the balance struct held in a mapping, and new
     code whose constructor takes its parameter types in another order;
  2. announces Broker -> BrokerWithTicketCount with
     initializeTicketCount(1000) in round A, killed after deploying, then
     after announcing, then after its apply is mined, and run again each
     time: one announcement and one apply in all, and the manifest names
     the new code.

During the notice: N is paid in each round by the old code; an apply in
A + 6 is refused UPGRADE_NOT_READY(A + 7); G's, S's and N's announce and
apply are refused NOT_AUTHORIZED; in A + 6 a winning ticket T1 is redeemed
and another, T2, made and held (a ticket lives its creation round and two
more, so only one made this late is still good after the notice). In A + 7 the command applies, alone in its block: every view function
of the Broker, Claims, StakeLedger, Registry, Scores, Parameters, RoundClock,
Upgrades and the Controller, from the built ABIs, over every account,
contract, round, group and ticket of the run, and both ERC-1967 slots and
the code of every address, read the same at the block before the apply and
at its block (left out: Upgrades' pending for the Broker, the Broker's
implementation slot). After: T2 is redeemed through the new code; T1 is
refused as used; ticketCount() is 1,000 plus the FeeSettled events after the
apply, and the old code could not answer it; a second initializeTicketCount
is refused; N's claim of r2 is paid and of r1 refused ALREADY_CLAIMED with
nothing minted.

Then the drill: S sets the LPT stop with demo/public-run stop; N's claim is
refused STOPPED while N is still paid and a validator's vote is accepted; U
announces Claims -> its code in force and applies it at once; U also puts a
Scores code it built itself in force at once, and demo/public-run upgrade
then refuses to upgrade Scores, its code in force having no recorded
constructor arguments; U lifts the stop with demo/public-run lift and the
claim is paid. Every balance, stake and claim is reconciled against the
calculator, and the run is written as a ledger case ./calculator/check
reproduces.

Exit 0 every check passed; 1 one failed; 2 the demo could not run.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path

import rewards
import buyback as bb
import common as c
import public
import public_upgrade

from common import ADDR, C, KEY, NAMES

NEW = "contracts/test/BrokerWithTicketCount.sol:BrokerWithTicketCount"
GATES = "contracts/test/UpgradeGates.sol"
COUNT_FROM = 1000
WIN_ALWAYS = 2**256 - 1
TICKET = "(address,address,uint256,uint256,uint256,bytes32,bytes)"
REDEEM = f"redeemWinningTicket({TICKET},bytes,uint256)"
VIEWED = ("Broker", "Claims", "StakeLedger", "Registry", "Scores", "Parameters", "RoundClock", "Upgrades", "Controller")
N_BOND = 1_000 * 10**18
V_BOND = 2_000 * 10**18
OUTSIDE = Path()   # the command's state directory and U's and S's keystores, outside the repository
TICKETS: dict[str, dict] = {}
RUNS = [0]


# --- the command, as a program --------------------------------------------------

def env(role: str = "upgrader") -> dict:
    var = "UPGRADER" if role == "upgrader" else "PAUSER"
    out = {k: v for k, v in os.environ.items() if not k.startswith("LP2_")}
    return {**out, "LP2_RPC_URL": c.RPC, "LP2_STATE_DIR": str(OUTSIDE / "state"),
            f"LP2_{var}_KEYSTORE": str(OUTSIDE / "keys" / role), f"LP2_{var}_PASSWORD_FILE": str(OUTSIDE / "pw")}


def command(*argv: str, role: str = "upgrader", hold: str | None = None) -> tuple[subprocess.Popen, Path]:
    """demo/public-run with `argv`, started as an operator would start it;
    its output in logs/command-<n>.log."""
    RUNS[0] += 1
    log = c.RUN / "logs" / f"command-{RUNS[0]}.log"
    e = env(role)
    if hold:
        e["LP2_TEST_HOLD"] = hold
    p = subprocess.Popen([str(c.HERE / "public-run"), *argv], env=e, stdout=open(log, "w"), stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, start_new_session=True)
    c.say(f"  $ demo/public-run {' '.join(argv)}{f'   (LP2_TEST_HOLD={hold})' if hold else ''}: {c.where(log)}")
    return p, log


def finished(*argv: str, role: str = "upgrader", seconds: float = 120) -> tuple[int | str, str]:
    """The command run to its end; one still running after `seconds`, such
    as one waiting for a notice it should never have given, is killed."""
    p, log = command(*argv, role=role)
    try:
        code = p.wait(seconds)
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, signal.SIGKILL)
        p.wait(10)
        code = f"still running after {seconds:.0f} s"
    out = log.read_text()
    for line in out.strip().splitlines()[-2:]:
        c.say(f"    {line[:200]}")
    return code, out


def killed_at(p: subprocess.Popen, log: Path, point: str, seconds: float = 120, during=None) -> None:
    """Wait for the command to say it holds at `point`, doing `during`
    meanwhile, then kill it outright."""
    end = time.time() + seconds
    while f"holding after it was {point}" not in log.read_text():
        if p.poll() is not None:
            raise c.Failed(f"the command exited {p.returncode} before it was {point}; see {c.where(log)}")
        if time.time() > end:
            raise c.Failed(f"the command was not {point} within {seconds:.0f} s; see {c.where(log)}")
        if during:
            during()
        time.sleep(0.5)
    os.killpg(p.pid, signal.SIGKILL)
    p.wait(10)
    c.say(f"  killed (SIGKILL) after it was {point}")


def nonce(who: str) -> int:
    return int(c.rpc("eth_getTransactionCount", [ADDR[who], "latest"]), 16)


def refused_before_sending(what: str, source: str, want: str, contract: str = "Broker") -> None:
    before = nonce("Upgrader")
    code, out = finished("upgrade", contract, source)
    c.check(f"{what}: refused before anything is sent (exit 2, U's nonce unchanged, the gate named)",
            (code, nonce("Upgrader") - before, "REFUSED" in out and want in out), (2, 0, True))


# --- refusals, read by a call -----------------------------------------------------

def refusal(who: str, to: str, signature: str, *args) -> str:
    """What a call from `who` meets now: its error, or "" when it passes."""
    data = c.sh(["cast", "calldata", signature, *map(str, args)])
    a, = c.rpc_batch([("eth_call", [{"from": ADDR[who], "to": to, "data": data}, "latest"])])
    return public_upgrade.refusal(a["error"].get("data") or "") if "error" in a else ""


# --- tickets made by the demo -----------------------------------------------------

def make_ticket(tag: str) -> None:
    """A winning ticket from P to N, created this round, signed with P's key
    as the node's signer signs one."""
    created = c.current_round()
    rand = int.from_bytes(os.urandom(32), "big")
    rand_hash = c.sh(["cast", "keccak", f"0x{rand:064x}"])
    nonce_ = int.from_bytes(os.urandom(4), "big")
    face = c.ticket_face()
    block_hash = c.call(C["RoundClock"], "blockHashForRound(uint256)", created)
    aux = f"{created:064x}{block_hash:064x}"
    packed = (ADDR["N"][2:] + ADDR["P"][2:]).lower() + f"{face:064x}{WIN_ALWAYS:064x}{nonce_:064x}" + rand_hash[2:] + aux
    digest = c.sh(["cast", "keccak", "0x" + packed])
    sig = c.sh(["cast", "wallet", "sign", "--private-key", KEY["P"], digest])
    TICKETS[tag] = {"hash": digest, "created": created, "rand": rand, "sig": sig,
                    "tuple": f"({ADDR['N']},{ADDR['P']},{face},{WIN_ALWAYS},{nonce_},{rand_hash},0x{aux})"}


def redeem(tag: str) -> dict:
    t = TICKETS[tag]
    count = len(c.settlements())
    c.send(KEY["N"], C["Broker"], REDEEM, t["tuple"], t["sig"], t["rand"])
    s = c.settlements()
    c.check(f"{tag} redeemed: one ticket settled, its hash, paying N", (len(s) - count, s[-1]["hash"].lower(),
                                                                       c.name(s[-1]["recipient"])), (1, t["hash"], "N"))
    return s[-1]


# --- rounds and jobs ------------------------------------------------------------

def paid(note: str = "") -> dict:
    """One paid job to N, redeemed by its node once 40 blocks have passed."""
    count = len(c.settlements())
    c.job("P", c.PORT["orch"], c.PORT["signer_p"])
    c.mine(41)
    rewards.wait("N's node to redeem its ticket", lambda: len(c.settlements()) > count, 120)
    s = c.settlements()[-1]
    c.check(f"round {c.current_round()}: N paid for a job{note}, its ticket redeemed by its node",
            (len(c.settlements()) - count, c.name(s["recipient"]), c.name(s["from"]), s["round"]),
            (1, "N", "N", c.current_round()))
    return s


def code_in_force(name: str) -> str:
    return "0x" + c.rpc("eth_getStorageAt", [C[name], c.IMPLEMENTATION_SLOT, "latest"])[-40:]


# --- every view, at two blocks ------------------------------------------------------

def view_calls() -> list[tuple[str, str, list]]:
    """Every view function of the contracts VIEWED, from the built ABIs, with
    every argument filled from the run: each account and contract (and
    address 0), each round and parameter group, each ticket and the
    Controller's names; both ERC-1967 slots of each proxy and the code at
    every address. Left out, as the apply must change them: Upgrades'
    pending for the Broker, and the Broker's implementation slot."""
    accounts = sorted({a.lower() for a in (*ADDR.values(), *C.values())} | {c.ZERO})
    numbers = list(range(max(c.current_round() + 2, 12)))
    ids = [c.sh(["cast", "keccak", n]) for n in ("BondingManager", "RoundsManager", "TicketBroker")]
    hashes = sorted({s["hash"].lower() for s in c.settlements()} | {t["hash"] for t in TICKETS.values()} |
                    set(ids) | {"0x" + "0" * 64})
    values = {"address": accounts, "bytes32": hashes}
    out = []
    for name in VIEWED:
        abi = json.loads((c.ROOT / "contracts" / "out" / f"{name}.sol" / f"{name}.json").read_text())["abi"]
        for f in abi:
            if f["type"] != "function" or f["stateMutability"] not in ("view", "pure"):
                continue
            types = [i["type"] for i in f["inputs"]]
            signature = f"{f['name']}({','.join(types)})"
            combos = [[]]
            for t in types:
                vs = values.get(t) or (numbers if t.startswith("uint") else None)
                if vs is None:
                    raise c.Failed(f"{name}.{signature}: no values for a {t} argument")
                combos = [k + [v] for k in combos for v in vs]
            for args in combos:
                if name == "Upgrades" and f["name"] == "pending" and args[0] == C["Broker"].lower():
                    continue
                out.append((f"{name}.{f['name']}({', '.join(map(str, args))})", "eth_call",
                            [{"to": C[name], "data": c.calldata(signature, *args)}]))
    for name, address in C.items():
        out.append((f"code at {name}", "eth_getCode", [address]))
        if name in c.UPGRADEABLE:
            out.append((f"{name}'s admin slot", "eth_getStorageAt", [address, c.ADMIN_SLOT]))
            if name != "Broker":
                out.append((f"{name}'s implementation slot", "eth_getStorageAt", [address, c.IMPLEMENTATION_SLOT]))
    return out


def read_all(items: list[tuple[str, str, list]], block: int) -> list:
    got = c.rpc_batch([(m, [*p, hex(block)]) for _, m, p in items])
    return [a.get("result", ("refused", (a.get("error") or {}).get("data"))) for a in got]


def compare_at_apply(block: int) -> None:
    items = view_calls()
    before, after = read_all(items, block - 1), read_all(items, block)
    differ = [label for (label, _, _), a, b in zip(items, before, after) if a != b]
    refused = sum(1 for a in before if isinstance(a, tuple))
    functions = len({label.split("(")[0] for label, m, _ in items if m == "eth_call"})
    c.say(f"  {len(items):,} reads: {functions} view functions of {len(VIEWED)} contracts over every account, round, "
          f"group and ticket, both slots of each proxy and the code at every address ({refused:,} of them refusals, "
          "compared as their bytes)")
    for label in differ[:5]:
        c.say(f"    differs: {label}")
    c.check(f"every read the same at block {block - 1}, before the apply, and at block {block}, the apply's", len(differ), 0)


# --- the run ------------------------------------------------------------------------

def setup() -> None:
    global OUTSIDE
    OUTSIDE = Path(tempfile.mkdtemp(prefix="lp2-upgrade-demo-")).resolve()
    if OUTSIDE.is_relative_to(c.ROOT):
        raise c.NotRun("the temporary directory is inside the repository")
    pw = c.RUN / "pw"
    pw.write_text(os.urandom(24).hex())
    pw.chmod(0o600)
    (OUTSIDE / "pw").write_text(os.urandom(24).hex())
    (OUTSIDE / "keys").mkdir(mode=0o700)
    for who in ("deployer", "N", "P", "G", "Keeper", "V1", "V2", "V3", "Upgrader", "Pauser"):
        c.new_key(who)
    for who in ("N", "P", "Keeper", "V1", "V2", "V3"):
        rewards.KEYS[who] = c.keystore(who, pw)
    for role, who in (("upgrader", "Upgrader"), ("pauser", "Pauser")):
        c.sh(["cast", "wallet", "import", "-k", str(OUTSIDE / "keys"), "--private-key", KEY[who],
              "--unsafe-password", (OUTSIDE / "pw").read_text(), role])
    treasury = "0x" + os.urandom(20).hex()
    ADDR["Treasury"], NAMES[treasury.lower()] = treasury, "Treasury"


def hand_to_command(first_block: int) -> None:
    """The state directory demo/public-run full leaves, as far as the
    command reads it: the run's state with what it deployed (each proxy's
    code with its source, constructor arguments and storage layout), and
    its manifest."""
    state = OUTSIDE / "state"
    state.mkdir(mode=0o700)
    (state / "state.json").write_text(json.dumps({
        "chain_id": c.MANIFEST["chain"]["chain_id"], "deployment": "full", "deployer": ADDR["deployer"],
        "authority": ADDR["G"], "upgrader": ADDR["Upgrader"], "pauser": ADDR["Pauser"],
        "done": {"deploy": {"contracts": dict(C), "from_block": first_block, "codes": dict(c.CODES)}}}, indent=1))
    m = json.loads(json.dumps(c.MANIFEST))
    public.fill_contracts(m)
    (state / "manifest.json").write_text(json.dumps(m, indent=2) + "\n")


def main():
    started = time.time()
    c.open_log()
    c.say("Upgrade: the Broker's code replaced in place on a running full system, every figure kept, then a stop drill")
    c.preflight(["chain", "orch", "orch_metrics", "signer_p", "signer_p_metrics", "model"])
    node_bin, model = c.fetch_node(), c.fetch_model()
    c.build_ours()
    c.start_chain()
    c.rpc("evm_setIntervalMining", [0])
    c.rpc("evm_setAutomine", [True])   # each transaction alone in its block; the demo mines the rest
    try:
        return run(node_bin, model, started)
    finally:
        shutil.rmtree(OUTSIDE, ignore_errors=True) if OUTSIDE != Path() else None


def run(node_bin, model, started):
    setup()
    first_block = c.block_number()
    c.say("\n1. Deploying the full system behind its proxies; U and S are keystores outside the repository")
    bb.deploy_full()
    c.check("Upgrades holds U and S", [f"0x{c.call(C['Upgrades'], f):040x}" for f in ("upgrader()", "pauser()")],
            [ADDR["Upgrader"].lower(), ADDR["Pauser"].lower()])
    hand_to_command(first_block)
    old_code = code_in_force("Broker")

    c.say("\n3. Round 1: N and V1 to V3 registered and bonded, P funds")
    for who, wei in (("N", N_BOND), ("V1", V_BOND), ("V2", V_BOND), ("V3", V_BOND)):
        rewards.tx_step({"action": "grant_lpt", "to": who, "amount_wei": wei}, "deployer", C["TestLPT"],
                        "transfer(address,uint256)", ADDR[who], wei)
        rewards.tx_step({"action": "register", "node": who}, who, C["Registry"], "register()")
        rewards.bond(who, who, wei)
    face = c.ticket_face()
    c.fund("P", 40 * face, face, "N")
    c.start_model(model)
    c.start_orchestrator("orchestrator-N", "N", node_bin, rewards.KEYS["N"], c.RUN / "pw", c.PORT["orch"],
                         c.PORT["orch_metrics"])
    c.start_signer("P", node_bin, rewards.KEYS["P"], c.RUN / "pw", c.PORT["signer_p"], c.PORT["signer_p_metrics"],
                   [c.PORT["orch"]])
    rewards.start_keeper()
    # The node lists its runner once its first health check passes.
    rewards.wait("N's node to list the model", lambda: c.challenge("P", c.PORT["orch"])[0] == 402, 120)

    c.say("\n4. Before the upgrade: N paid in rounds 2 to 4, r1 claimed and r2 left")
    rewards.to_round(2)
    for v in ("V1", "V2", "V3"):
        c.send(KEY[v], C["Scores"], "optIn(address)", ADDR[v])
    paid()
    rewards.to_round(3)
    paid()
    rewards.to_round(4)
    paid()
    r1, r2, r3 = 2, 3, 4
    rewards.to_round(r1 + 7)
    rewards.claim("N", "N", r1)
    c.check(f"round {r1 + 7}: V1 to V3 are the validators", rewards.members_at(c.current_round()), ["V1", "V2", "V3"])

    c.say("\n5. The command's gates: each refused before anything is sent")
    refused_before_sending("a counter declared before the Broker's figures", f"{GATES}:BrokerCounterFirst",
                           "entry 1, balance in the code in force, differs: name")
    refused_before_sending("a balance's members in another order, a struct in a mapping",
                           f"{GATES}:BrokerBalanceReordered", "value: value: members: 1: name: amount")
    refused_before_sending("a constructor taking its parameter types in another order",
                           f"{GATES}:BrokerConstructorSwapped", "its constructor takes (contract IRegistry, contract IMockUSDC")

    a = c.current_round()
    c.say(f"\n6. Round {a} = A: U announces Broker -> BrokerWithTicketCount with initializeTicketCount({COUNT_FROM}), "
          "the command killed after deploying and after announcing")
    call = ("--call", "initializeTicketCount(uint256)", str(COUNT_FROM))
    p, log = command("upgrade", "Broker", NEW, *call, hold="deployed")
    killed_at(p, log, "deployed")
    c.check("nothing announced yet", public_upgrade_pending(), "")
    p, log = command("upgrade", "Broker", NEW, *call, hold="announced")
    killed_at(p, log, "announced")
    announced = c.logs(C["Upgrades"], "UpgradeAnnounced(address,address,bytes,uint256,uint256)")
    w = public_upgrade.words_of(announced[-1]["data"])
    new_code = public_upgrade.address_of(w[0])
    c.check("announced in round A: applies from A + 7, lapses at A + 28", (w[2], w[3]), (a + 7, a + 28))
    c.check("the command printed it", f"applies from round {a + 7}, lapses at round {a + 28}" in log.read_text(), True)
    p, log = command("upgrade", "Broker", NEW, *call, hold="applied")

    c.say(f"\n7. The notice, rounds {a} to {a + 6}: N paid by the old code each round, while the command waits")
    for who in ("G", "Pauser", "N"):
        c.check(f"{who}'s announce and apply refused", (
            refusal(who, C["Upgrades"], "announceUpgrade(address,address,bytes)", C["Broker"], new_code, "0x"),
            refusal(who, C["Upgrades"], "applyUpgrade(address)", C["Broker"])), ("NOT_AUTHORIZED", "NOT_AUTHORIZED"))
    for r in range(a, a + 7):
        if r > a:
            rewards.to_round(r)
        paid(" by the old code")
        c.check(f"round {r}: the Broker's code in force is the old code", code_in_force("Broker"), old_code.lower())
        if p.poll() is not None:
            raise c.Failed(f"the command exited {p.returncode} during the notice; see {c.where(log)}")
    c.check(f"round A + 6: U's apply refused", refusal("Upgrader", C["Upgrades"], "applyUpgrade(address)", C["Broker"]),
            f"UPGRADE_NOT_READY({a + 7})")
    make_ticket("T1")
    redeem("T1")
    make_ticket("T2")
    c.say(f"  T2 made in round {a + 6} and held")

    c.say(f"\n8. Round A + 7 = {a + 7}: the command applies, and is killed once it is mined")
    rewards.to_round(a + 7)
    killed_at(p, log, "applied")
    applied = [lg for lg in c.logs(C["Upgrades"], "UpgradeApplied(address,address,uint256,bool)")
               if int(lg["topics"][1], 16) == int(C["Broker"], 16)]
    block = int(applied[-1]["blockNumber"], 16)
    txs = c.rpc("eth_getBlockByNumber", [hex(block), False])["transactions"]
    c.check("applied in round A + 7, alone in its block", (public_upgrade.words_of(applied[-1]["data"])[1], len(txs)),
            (a + 7, 1))
    compare_at_apply(block)
    c.check("the Controller's three names and every address unchanged", public_upgrade_check(), True)
    code, out = finished("upgrade", "Broker", NEW, *call)
    c.check("run again: the code in force is already the new code, and only the manifest is written",
            (code, "already this code" in out), (0, True))
    manifest = json.loads((OUTSIDE / "state" / "manifest.json").read_text())
    named = next(x["implementation"] for x in manifest["contracts"] if x["contract"] == "Broker")
    c.check("the manifest names the new code", named.lower(), new_code.lower())
    shutil.copy(OUTSIDE / "state" / "manifest.json", c.RUN / "manifest.json")
    events = [len([lg for lg in c.logs(C["Upgrades"], e) if int(lg["topics"][1], 16) == int(C["Broker"], 16)])
              for e in ("UpgradeAnnounced(address,address,bytes,uint256,uint256)",
                        "UpgradeApplied(address,address,uint256,bool)")]
    c.check("through three kills: one announcement and one apply in all", events, [1, 1])

    c.say("\n9. After the apply")
    redeem("T2")
    c.check("T1 refused again as a used ticket",
            refusal("N", C["Broker"], REDEEM, TICKETS["T1"]["tuple"], TICKETS["T1"]["sig"], TICKETS["T1"]["rand"]),
            "TICKET_USED")
    paid(" by the new code")
    after = [s for s in c.settlements() if s["block"] > block]
    c.check(f"ticketCount() is {COUNT_FROM:,} plus the {len(after)} FeeSettled events after the apply",
            c.call(C["Broker"], "ticketCount()"), COUNT_FROM + len(after))
    old_answer, = c.rpc_batch([("eth_call", [{"to": C["Broker"], "data": c.calldata("ticketCount()")}, hex(block - 1)])])
    c.check("the old code could not answer ticketCount()", "error" in old_answer, True)
    for who in ("Upgrader", "N"):
        c.check(f"a second initializeTicketCount from {who} refused",
                refusal(who, C["Broker"], "initializeTicketCount(uint256)", 0), "ALREADY_COUNTING")
    c.check(f"N's claim of r2, round {r2}, earned before the upgrade, paid",
            rewards.claim("N", "N", r2)["claimed"]["paid"] > 0, True)
    supply = c.call(C["TestLPT"], "totalSupply()")
    rewards.claim("N", "N", r1, want="ALREADY_CLAIMED")
    c.check("nothing minted by the refused claim", c.call(C["TestLPT"], "totalSupply()"), supply)

    c.say("\n10. The drill: S sets the LPT stop")
    code, out = finished("stop", "lpt", role="pauser")
    c.check("demo/public-run stop lpt", (code, c.call(C["Upgrades"], "stopped(uint8)", 1)), (0, 1))
    c.check(f"N's claim of round {r3} refused", refusal("N", C["Claims"], "claim(address,uint256)", ADDR["N"], r3),
            "STOPPED")
    paid(" under the LPT stop (the USDC side runs)")
    c.send(KEY["V1"], C["Scores"], "vote(address,address,uint256)", ADDR["V1"], ADDR["N"], 1_000_000)
    c.say("  V1's vote for N accepted")
    claims_code = code_in_force("Claims")
    c.send(KEY["Upgrader"], C["Upgrades"], "announceUpgrade(address,address,bytes)", C["Claims"], claims_code, "0x")
    r = c.send(KEY["Upgrader"], C["Upgrades"], "applyUpgrade(address)", C["Claims"])
    lg = [x for x in r["logs"] if x["address"].lower() == C["Upgrades"].lower()][-1]
    c.check("U announces Claims -> its code in force and applies it at once, under the stop",
            (public_upgrade.words_of(lg["data"])[1:], code_in_force("Claims")), ([c.current_round(), 1], claims_code))
    record = c.CODES[c.IMPL["Scores"].lower()]
    scores = c.sh(["forge", "create", "--root", str(c.ROOT / "contracts"), "--rpc-url", c.RPC, "--private-key",
                   KEY["Upgrader"], "--broadcast", record["source"], "--constructor-args", *record["args"]])
    scores = next(x.split()[-1] for x in scores.splitlines() if x.startswith("Deployed to:"))
    c.send(KEY["Upgrader"], C["Upgrades"], "announceUpgrade(address,address,bytes)", C["Scores"], scores, "0x")
    c.send(KEY["Upgrader"], C["Upgrades"], "applyUpgrade(address)", C["Scores"])
    c.check("U puts a Scores code of its own in force, under the stop", code_in_force("Scores"), scores.lower())
    refused_before_sending("Scores, whose code in force has no record", "contracts/src/scores/Scores.sol:Scores",
                           "has no recorded constructor arguments", contract="Scores")
    code, out = finished("lift", "lpt")
    c.check("demo/public-run lift lpt", (code, c.call(C["Upgrades"], "stopped(uint8)", 1)), (0, 0))
    c.check(f"the stop lifted, N's claim of round {r3} paid", rewards.claim("N", "N", r3)["claimed"]["paid"] > 0, True)

    c.say("\n11. Every figure, read from the chain, beside the calculator's")
    members = rewards.members()
    ordered, views, _ = c.reconcile("At the end of the run", rewards.keeper_steps(), members)
    c.write_case("upgrade-run", ordered, views, members)
    for name in ("upgrades.log",):
        shutil.copy(OUTSIDE / "state" / name, c.RUN / "logs" / "command-state-upgrades.log")
    return c.finish(started, "the Broker's code was replaced in place through three kills, every view read the same "
                             "across the apply, each gate refused before sending, and the stop drill held; every "
                             "figure equals the calculator's.")


def public_upgrade_pending() -> str:
    return public_upgrade.pending(C["Broker"])[0]


def public_upgrade_check() -> bool:
    for legacy, ours in {"BondingManager": "Registry", "RoundsManager": "RoundClock", "TicketBroker": "Broker"}.items():
        if c.call(C["Controller"], "getContract(bytes32)", c.sh(["cast", "keccak", legacy])) != int(C[ours], 16):
            return False
    return all(c.rpc("eth_getCode", [x, "latest"]) not in ("0x", None) for x in C.values())
