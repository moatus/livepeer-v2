"""demo/public-run: the stage 3 system on a chain the run does not control.

Deploys the stage 3 contracts to the chain at LP2_RPC_URL, with the
deployer's keystore given by the environment, and runs the team's node N
through one paid job and one full reward cycle in real time: N registers
with no stake and bonds test LPT to itself, payer P funds, V opts in as the
one scripted validator, the keeper starts every round, N serves one paid
relay and redeems its ticket, and seven rounds after the round its fee
counts in, N claims its LPT and withdraws its part. Every figure is then
read from the chain and printed beside the independent calculator's, by the
demo's own reconciliation (common.reconcile), and the run is written as a
ledger case that ./calculator/check reproduces.

The path sends no anvil_* or evm_* call: it never mines, moves time or
sets a balance. Rounds advance only when the keeper starts them, so a cycle
takes as long as the chain's clock says (on Arbitrum Sepolia the claim
comes about 80 or 90 minutes after the deployment, see demo/PUBLIC.md).

Environment (all required but the last four):
  LP2_RPC_URL                 the chain's JSON-RPC URL
  LP2_DEPLOYER_KEYSTORE       the deployer's keystore file, outside the repository
  LP2_DEPLOYER_PASSWORD_FILE  its password file, outside the repository
  LP2_STATE_DIR               the run's state directory, outside the repository:
                              keys the run makes, addresses, steps done, logs,
                              the node's data and the run's manifest
  LP2_ROLE_GAS_WEI            gas money the deployer gives each of N, P, V and
                              the keeper (default 3000000000000000, 0.003 ETH)
  LP2_DEMO_PORT_BASE          moves the local ports of the node, signer and
                              model (default 47700)
  LP2_RPC_BATCH_SIZE          the most calls in one JSON-RPC batch (default 100)
  LP2_LOG_PAGE_BLOCKS         the most blocks asked for in one eth_getLogs
                              (default 5000; common.LOG_PAGE)

A second run with the same state directory sends nothing that was already
sent: a run stopped between steps resumes, and one whose last step is done
only reconciles again. A run stopped while a step was being sent, or during
the deployment, is refused, because whether that step landed is unknown and
a resend could pay twice. One run at a time holds the state directory, and
so does every program it starts, so a killed run's keeper or node keeps it.

Exit 0 every figure equal; 1 a figure differed or a behaviour was missing;
2 the run could not run or was refused.
"""
from __future__ import annotations

import fcntl
import json
import os
import subprocess
import time
from pathlib import Path

import common
import stage3
from common import NotRun, Failed, say, check, call, rpc, money, lpt, C, ADDR, NAMES, RECORDS, FAILURES, PORT, MANIFEST

# The chains this path may send to. Local development chains, and
# Arbitrum Sepolia (D-14). Nothing with real funds is on it.
ALLOWED_CHAINS = {31337: "a local anvil (its default chain id)", 1337: "a local development chain",
                  474747: "the demo's local anvil", 421614: "Arbitrum Sepolia"}
LOCAL_CHAINS = {31337, 1337, 474747}
TEMPLATE = "public-arbitrum-sepolia.json"
ROLES = ("N", "P", "V", "Keeper")
LPT = 10**18
N_BOND = 1_000 * LPT
V_BOND = 1_000 * LPT
SCRIPT = common.HERE / "public-validators.script"

STATE: dict = {}
TEMPLATE_DOC: dict = {}   # the template as it was when this state's contracts were deployed
STATE_DIR = Path()
LOCK = None   # the open lock file, held until the process exits


# --- the environment and the state ---------------------------------------------

def outside_repo(var: str, must_exist: bool) -> Path:
    raw = os.environ.get(var, "")
    if not raw:
        raise NotRun(f"{var} is not set (see demo/PUBLIC.md)")
    p = Path(raw).expanduser().resolve()
    if p.is_relative_to(common.ROOT):
        raise NotRun(f"{var} is inside the repository; keys and run state must live outside it")
    if must_exist and not p.is_file():
        raise NotRun(f"{var} does not name a file")
    return p


def private(path: Path) -> Path:
    """A path for keys, passwords or the RPC URL in the state directory. No
    part of it below the state directory may be a symlink, so nothing
    secret is written or read somewhere else, such as in the repository."""
    p = path
    while p != STATE_DIR:
        if p.is_symlink():
            raise NotRun(f"{p.relative_to(STATE_DIR)} in the state directory is a symlink; keys, passwords and the "
                         "RPC URL are not written or read through one. Nothing was sent")
        p = p.parent
    return path


def lock() -> None:
    """Hold the state directory alone until this process and every program
    it starts have exited: a second invocation could send the same step, or
    run a second keeper or validator with the same key. Every program the
    run starts inherits the locked descriptor (common.KEEP_FDS), so the lock
    outlives a run that is killed while its programs still run."""
    global LOCK
    path = private(STATE_DIR / "lock")
    LOCK = open(os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600), "r+")
    try:
        fcntl.flock(LOCK, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        holder = LOCK.read().strip() or "another process"
        raise NotRun(f"this state directory is in use by process {holder}; one run at a time. If that run is gone, "
                     "a program it started still holds the directory and may send with its keys: stop it first. "
                     "Nothing was sent")
    common.KEEP_FDS = (LOCK.fileno(),)
    note_holders()


def note_holders() -> None:
    """Name in the lock file this process and the programs it started, for
    the refusal a second run gets."""
    progs = ", ".join(f"{n} {p.pid}" for n, p in common.PROCS.items() if p.poll() is None)
    LOCK.seek(0)
    LOCK.truncate()
    LOCK.write(str(os.getpid()) + (f" (programs it started: {progs})" if progs else ""))
    LOCK.flush()


def check_deployment() -> None:
    """Send only where this state's contracts are: each one has code on the
    chain at LP2_RPC_URL, whose id open_state has checked."""
    contracts = STATE["done"]["deploy"]["contracts"]
    try:
        codes = common.rpc_batch([("eth_getCode", [a, "latest"]) for a in contracts.values()])
    except (Failed, OSError, common.urllib.error.URLError) as e:
        raise NotRun(f"the chain at LP2_RPC_URL did not answer: {e}")
    empty = [n for n, c in zip(contracts, codes) if c.get("result") in (None, "0x")]
    if empty:
        raise NotRun(f"no code at this state's {', '.join(empty)} on the chain at LP2_RPC_URL. Nothing was sent")


def save() -> None:
    STATE["records"] = RECORDS
    STATE["vrecords"] = stage3.VRECORDS
    tmp = STATE_DIR / "state.json.tmp"
    tmp.write_text(json.dumps(STATE, indent=1, default=list))
    tmp.replace(STATE_DIR / "state.json")


def step(name: str, fn, sends: bool = True):
    """Run `fn` once for this state: skipped when done, refused when a
    previous run was stopped while sending it. Every send waits for its
    receipt, so a step that returns has landed. A step that `sends` nothing,
    such as a read, is not marked as sending."""
    if name in STATE["done"]:
        return STATE["done"][name]
    if STATE.get("sending") == name:
        raise NotRun(f"a previous run was stopped while sending step {name!r}; whether it landed is not known to this "
                     f"run, so it is not sent again. Check the sender's transactions, then start a new state directory")
    if sends:
        STATE["sending"] = name
        save()
    result = fn()
    STATE["done"][name] = result if result is not None else True
    STATE["sending"] = None
    save()
    return STATE["done"][name]


def cast_signer(who: str) -> list[str]:
    if who == "deployer":
        return ["--keystore", STATE["deployer_keystore"], "--password-file", STATE["deployer_password_file"]]
    return ["--keystore", str(private(stage3.KEYS[who])), "--password-file", str(private(STATE_DIR / "pw"))]


def ksend(who: str, to: str, *args: str, value: int | None = None) -> dict:
    """A transaction signed from a keystore: no key on a command line."""
    argv = ["cast", "send", "--json", "--rpc-url", common.RPC, *cast_signer(who)]
    if value is not None:
        argv += ["--value", str(value)]
    done = subprocess.run(argv + [to, *map(str, args)], capture_output=True, text=True, pass_fds=common.KEEP_FDS)
    if done.returncode != 0:
        raise Failed(f"{who}: send {' '.join(map(str, args))[:40] or 'value'}: {done.stderr.strip()[-400:]}")
    r = json.loads(done.stdout)
    common.SENT.append(r["transactionHash"])  # saved with its step
    if r["status"] != "0x1":
        raise Failed(f"{who}: send {' '.join(map(str, args))[:40]}: reverted")
    return r


def tx_step(fields: dict, who: str, to: str, *args) -> dict:
    return common.record(fields, ksend(who, to, *args))


def new_role_key(who: str) -> str:
    """A fresh key for a role, made by cast into the state directory; the
    password goes by the environment, the key never leaves the keystore."""
    d = private(STATE_DIR / "keys" / who)
    d.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "CAST_PASSWORD": private(STATE_DIR / "pw").read_text()}
    out = json.loads(subprocess.run(["cast", "wallet", "new", str(d), "--json"], capture_output=True, text=True,
                                    env=env, check=True).stdout)
    made = out["data"][0] if isinstance(out, dict) else out[0]
    f = private(d / who)
    Path(made["path"]).rename(f)
    data = json.loads(f.read_text())
    data["address"] = made["address"][2:].lower()  # the node reads the address field
    f.write_text(json.dumps(data))
    f.chmod(0o600)
    return made["address"]


def open_state(stage: int = 3) -> int:
    """Check the chain and the keys, then load or start the run's state of
    `stage`'s system. Returns the chain id."""
    global STATE_DIR
    url = os.environ.get("LP2_RPC_URL", "")
    if not url:
        raise NotRun("LP2_RPC_URL is not set (see demo/PUBLIC.md)")
    keystore = outside_repo("LP2_DEPLOYER_KEYSTORE", True)
    password = outside_repo("LP2_DEPLOYER_PASSWORD_FILE", True)
    STATE_DIR = outside_repo("LP2_STATE_DIR", False)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.chmod(0o700)
    lock()
    common.RUN = STATE_DIR
    common.RPC = url
    common.RPC_FILE = STATE_DIR / "rpc-url"
    try:
        common.BATCH = int(os.environ.get("LP2_RPC_BATCH_SIZE", "100"))
    except ValueError:
        common.BATCH = 0
    if common.BATCH < 1:
        raise NotRun("LP2_RPC_BATCH_SIZE must be a positive number")
    (STATE_DIR / "logs").mkdir(exist_ok=True)
    common.LOG = open(STATE_DIR / "public-run.log", "a")
    say(f"\n=== demo/public-run, {time.strftime('%Y-%m-%d %H:%M:%S')}")

    try:
        chain_id = int(rpc("eth_chainId", []), 16)
    except (OSError, ValueError, common.urllib.error.URLError) as e:
        raise NotRun(f"the chain at LP2_RPC_URL did not answer: {e}")
    if chain_id not in ALLOWED_CHAINS:
        raise NotRun(f"chain id {chain_id} is not on the allow-list "
                     f"({', '.join(f'{k} {v}' for k, v in ALLOWED_CHAINS.items())}); nothing was sent")
    deployer = subprocess.run(["cast", "wallet", "address", "--keystore", str(keystore), "--password-file", str(password)],
                              capture_output=True, text=True)
    if deployer.returncode != 0:
        raise NotRun("the deployer's keystore could not be opened with its password file")
    deployer = deployer.stdout.strip()

    path = STATE_DIR / "state.json"
    pw = private(STATE_DIR / "pw")
    if path.is_file():
        STATE.update(json.loads(path.read_text()))
        STATE.setdefault("sent", [])
        if STATE["chain_id"] != chain_id:
            raise NotRun(f"this state directory belongs to chain {STATE['chain_id']}, not {chain_id}")
        if STATE["deployer"].lower() != deployer.lower():
            raise NotRun(f"this state directory was deployed by {STATE['deployer']}, not {deployer}")
        if STATE.get("stage", 3) != stage:  # a state from before stages were recorded is stage 3's
            raise NotRun(f"this state directory holds the stage {STATE.get('stage', 3)} system; run "
                         f"demo/public-run{' stage-4' if STATE.get('stage', 3) == 4 else ''} to resume it. Nothing was sent")
        if "deploy" in STATE["done"]:
            check_deployment()
        say(f"  resuming the run in this state directory: {len(STATE['done'])} steps done")
    else:
        STATE.update({"chain_id": chain_id, "deployer": deployer, "accounts": {}, "contracts": {}, "done": {},
                      "sending": None, "records": [], "vrecords": [], "sent": [], "stage": stage})
        pw.write_text("pw-" + os.urandom(16).hex())
        pw.chmod(0o600)
    STATE["deployer_keystore"], STATE["deployer_password_file"] = str(keystore), str(password)
    common.DEPLOYER_SIGNER = cast_signer("deployer")
    # Every process this run starts reads the URL checked above: the node
    # and signer from this file, the helpers from their command lines.
    tmp = private(STATE_DIR / "rpc-url.tmp")
    tmp.write_text(url)
    tmp.chmod(0o600)
    tmp.replace(private(common.RPC_FILE))

    for who in ROLES:
        if who not in STATE["accounts"]:
            STATE["accounts"][who] = new_role_key(who)
            save()
        stage3.KEYS[who] = private(STATE_DIR / "keys" / who / who)
    if "Treasury" not in STATE["accounts"]:
        STATE["accounts"]["Treasury"] = "0x" + os.urandom(20).hex()
    STATE["accounts"]["deployer"] = deployer
    for who, addr in STATE["accounts"].items():
        ADDR[who] = addr
        NAMES[addr.lower()] = who
    RECORDS[:] = STATE["records"]
    stage3.VRECORDS[:] = STATE["vrecords"]
    common.SENT = STATE["sent"]   # every transaction the run sends itself, saved with its steps
    save()
    return chain_id


# --- the run -----------------------------------------------------------------------

def deploy(create=stage3.deploy_stage3) -> dict:
    """Deploy once, with `create`, the system's own deployment function
    (stage 3's, or stage 4's in demo/public4.py)."""
    if "deploy" not in STATE["done"] and STATE.get("sending") == "deploy":
        raise NotRun("a previous run was stopped during the deployment; its contracts are abandoned. "
                     "Start a new state directory to deploy afresh")

    def fresh():
        start = common.block_number()
        commit = common.sh(["git", "rev-parse", "HEAD"], cwd=common.ROOT)
        dirty = bool(common.sh(["git", "status", "--porcelain", "--untracked-files=no"], cwd=common.ROOT))
        create()
        common.SENT.extend(common.CREATED)
        # What was deployed, kept with it: a rerun or resume from a later
        # checkout uses this template (rulebook, node, model) and labels the
        # manifest with this commit, not its own.
        return {"contracts": dict(C), "from_block": start,
                "template": TEMPLATE_DOC, "source_commit": commit + ("-dirty" if dirty else "")}
    got = step("deploy", fresh)
    C.update(got["contracts"])
    common.FROM_BLOCK = got["from_block"]
    for n in ("Claims", "StakeLedger"):
        NAMES[C[n].lower()] = n
        ADDR[n] = C[n]
    STATE["contracts"] = dict(C)
    return got


def wait_round(target: int, why: str, give_up=None) -> None:
    """Wait in real time for the keeper to start round `target`."""
    last = None
    while (now := stage3.current_round()) < target:
        if now != last:
            say(f"  {time.strftime('%H:%M:%S')} round {now}; waiting for round {target} ({why})")
            last = now
        if give_up:
            give_up()
        if "keeper" in common.PROCS and common.PROCS["keeper"].poll() is not None:
            raise Failed("the keeper exited; see logs/keeper.log in the state directory")
        time.sleep(5)
    say(f"  {time.strftime('%H:%M:%S')} round {stage3.current_round()}")


def start_programs(node_bin: Path, model: Path | None, keeper: tuple[str, ...] = (), script: Path = SCRIPT,
                   payer: bool = True, runner: tuple[str, str, int] | None = None) -> None:
    """Start the model, the node, P's signer (with `payer`), the keeper with
    its `keeper` options and V's validator from `script`. With `runner`
    and no model, the node serves that runner instead (demo/public4.py's
    services)."""
    if model:
        common.start_model(model)
    common.start_orchestrator("orchestrator-N", "N", node_bin, stage3.KEYS["N"], STATE_DIR / "pw",
                              PORT["orch"], PORT["orch_metrics"], runner=runner)
    if payer:
        common.start_signer("P", node_bin, stage3.KEYS["P"], STATE_DIR / "pw", PORT["signer_p"], PORT["signer_p_metrics"],
                            [PORT["orch"]])
    stage3.start_keeper(*keeper)
    names = STATE_DIR / "names.json"
    names.write_text(json.dumps({n: ADDR[n] for n in ("N", "V")}))
    common.start("validators", [str(STATE_DIR / "bin" / "lp2-validators"), "--rpc-url", common.RPC,
                                "--keystore-password-file", str(STATE_DIR / "pw"), "--controller-address", C["Controller"],
                                "--scores-address", C["Scores"], "--script", str(script), "--names", str(names),
                                "--poll", "5s", f"--keystore=V={stage3.KEYS['V']}"])
    note_holders()
    served = "the model server, " if model else "N's runner, "
    signer = "P's signer, " if payer else ""
    say(f"  {served}N's orchestrator, {signer}the keeper and V's validator program are running")


def fund_p(face: int) -> None:
    deposit, earmark = face, face
    step("mint USDC to P", lambda: ksend("deployer", C["MockUSDC"], "mint(address,uint256)", ADDR["P"], deposit + earmark)
         and None)

    def fund():
        argv = [str(STATE_DIR / "bin" / "lp2-fund"), "--rpc-url", common.RPC, "--keystore-file", str(stage3.KEYS["P"]),
                "--keystore-password-file", str(STATE_DIR / "pw"), "--controller-address", C["Controller"],
                "--usdc-address", C["MockUSDC"], "--deposit", str(deposit), "--earmark", str(earmark), "--node", ADDR["N"]]
        out = common.sh(argv)
        common.SENT.extend(common.sent_by(out))  # the approval and the funding
        tx = common.sent_by("\n".join(line for line in out.splitlines() if line.startswith("funded:")))[0]
        common.record({"action": "fund", "payer": "P", "deposit_micro_usdc": deposit, "node": "N",
                       "earmark_micro_usdc": earmark}, common.receipt(tx, 300))
    step("fund", fund)
    say(f"  P deposits {money(deposit)} micro-USDC and earmarks {money(earmark)} for N")


def paid_job() -> dict:
    """Round 2: P pays N for one job, sent once, and N redeems its ticket.
    Returns the settled ticket."""
    say("\n4. Round 2: the paid job")
    wait_round(2, "N's bond counts from it")
    # The job is sent off the chain, to N: this marks only that it was sent
    # (so it is never sent twice), not that anything settled. Its settlement
    # is a step of its own below.
    sent = STATE.get("job")
    if not sent:
        STATE["job"] = {"round": stage3.current_round()}
        save()
        answer = common.job("P", PORT["orch"], PORT["signer_p"])
        STATE["job"]["answer"] = answer
        save()
        say(f"  relay to N in round {STATE['job']['round']}: paid, the model answered {answer[:50]!r}")
    else:
        say(f"  the job was sent in round {sent['round']} by an earlier run; it is not sent again")
    job_round = STATE["job"]["round"]

    say("\n5. N redeems its ticket, once 40 blocks of its clock have passed")

    def expired():
        if stage3.current_round() > job_round + 2:
            raise Failed(f"no ticket was redeemed by round {job_round + 3}: the ticket of round {job_round} has expired "
                         f"(R-22). Not sending another job; see logs/orchestrator-N.log")
    while not (settled := common.settlements()):
        expired()
        time.sleep(5)
    if len(settled) != 1:
        raise Failed(f"the Broker settled {len(settled)} tickets, want 1")
    s = step("ticket settled", lambda: settled[0], sends=False)
    earned = s["round"]
    say(f"  {time.strftime('%H:%M:%S')} N redeemed a ticket of {money(s['face'])} in round {earned}: "
        f"N +{money(s['node_amount'])} micro-USDC, network +{money(s['network_amount'])}")
    check("the redeemed ticket pays N and was redeemed by N", (common.name(s["recipient"]), common.name(s["from"])), ("N", "N"))
    return s


def main() -> int:
    started = time.time()
    chain_id = open_state()
    TEMPLATE_DOC.update(STATE["done"]["deploy"]["template"] if "deploy" in STATE["done"] else
                        json.loads((common.ROOT / "deployments" / TEMPLATE).read_text()))
    common.MANIFEST.update(json.loads(json.dumps(TEMPLATE_DOC)))
    common.MANIFEST["chain"] = dict(common.MANIFEST["chain"], chain_id=chain_id)  # what the node and signer are given
    say("Public run: the stage 3 system, the team's node N paid for one job, and one reward cycle in real time")
    say(f"  chain id {chain_id}, {ALLOWED_CHAINS[chain_id]}; state directory outside the repository")
    say(f"  deployer {STATE['deployer']}; N {ADDR['N']}, P {ADDR['P']}, V {ADDR['V']}, keeper {ADDR['Keeper']}")

    if "withdraw" in STATE["done"]:
        deploy()
        say("\nThis state's last step is done: nothing is sent; every figure is reconciled again from the chain.")
        return reconcile(chain_id, started)

    say("\n1. Setting up")
    common.preflight(["orch", "orch_metrics", "signer_p", "signer_p_metrics", "model"])
    node_bin = common.fetch_node()
    model = common.fetch_model()
    common.build_ours()
    face = common.ticket_face()
    say(f"  node {MANIFEST['node']['commit'][:12]} (unmodified), model sha256 {MANIFEST['model']['sha256'][:12]}, "
        f"contracts and programs built; ticket face {money(face)} micro-USDC")

    say("\n2. Deploying (round 1 starts with the round clock)")
    deploy()
    say("  contracts: " + ", ".join(f"{k} {v}" for k, v in C.items()))
    say(f"  from block {common.FROM_BLOCK}; treasury {ADDR['Treasury']} (no key)")
    gas = int(os.environ.get("LP2_ROLE_GAS_WEI", "3000000000000000"))
    for who in ROLES:
        step(f"gas money to {who}", lambda who=who: ksend("deployer", ADDR[who], value=gas) and None)
    say(f"  the deployer gave N, P, V and the keeper {gas / 10**18:g} of the chain's native token each, for gas")

    say("\n3. Round 1: N registers with no stake, N and V bond test LPT to themselves, P funds")
    for who, wei in (("N", N_BOND), ("V", V_BOND)):
        step(f"grant LPT to {who}", lambda who=who, wei=wei: tx_step(
            {"action": "grant_lpt", "to": who, "amount_wei": wei}, "deployer", C["TestLPT"],
            "transfer(address,uint256)", ADDR[who], wei) and None)
    for who in ("N", "V"):
        step(f"register {who}", lambda who=who: tx_step({"action": "register", "node": who}, who, C["Registry"],
                                                        "register()") and None)
    check("N is registered, with no stake", (call(C["Registry"], "isRegistered(address)", ADDR["N"]),
                                             call(C["StakeLedger"], "stakeAt(address,uint256)", ADDR["N"], 1)), (1, 0))
    step("bond N", lambda: stage3.bond("N", "N", N_BOND))
    step("bond V", lambda: stage3.bond("V", "V", V_BOND))
    say(f"  N bonds {lpt(N_BOND)} and V {lpt(V_BOND)} to themselves; each counts from the next round")
    fund_p(face)
    step("observe", lambda: common.observe("P", "N") and None, sends=False)

    start_programs(node_bin, model)
    s = paid_job()
    earned = s["round"]

    say(f"\n6. Rounds {earned + 1} to {earned + 7}: the keeper starts each round and mints the ended one's shares")
    wait_round(earned + 7, f"N may claim round {earned} seven rounds later")
    give_up = time.time() + 300
    while not stage3.shares_minted(earned + 6):
        if common.PROCS["keeper"].poll() is not None or time.time() > give_up:
            raise Failed(f"the keeper started round {earned + 7} but did not mint round {earned + 6}'s shares; "
                         f"see logs/keeper.log in the state directory")
        time.sleep(5)

    say(f"\n7. Round {earned + 7}: N claims round {earned}'s reward and withdraws its part")
    cn = step("claim", lambda: stage3.claim("N", "N", earned)["claimed"])
    say(f"  N claims at score {cn['score']:,}: paid {lpt(cn['paid'])}; cut {lpt(cn['cut'])} to {cn['payout']}, "
        f"pot {lpt(cn['pot'])} to Claims")
    w = step("withdraw", lambda: stage3.withdraw("N", "N", [earned])["moved"])
    say(f"  N withdraws its part of the pot: {lpt(w)}")
    check("N's claim paid more than nothing", cn["paid"] > 0, True)
    return reconcile(chain_id, started)


def reconcile(chain_id: int, started: float) -> int:
    for p in ("keeper", "validators"):
        common.stop(p)
    say("\n8. Every figure, read from the chain, beside the calculator's")
    settled = common.settlements()
    if not settled:
        raise Failed("the chain shows no redeemed ticket")
    last = settled[0]["round"] + 6
    check(f"rounds 1 to {last} whose shares the keeper did not mint",
          [r for r in range(1, last + 1) if not stage3.shares_minted(r)], [])
    check(f"rounds 3 to {last} without V as their one validator",
          [r for r in range(3, last + 1) if stage3.members_at(r) != ["V"]], [])
    extra = stage3.keeper_steps()
    m = stage3.members()
    ordered, views, calc = common.reconcile("At the end of the run", extra, m)
    pay = stage3.validator_pay_run_cases()
    count = differ = 0
    for c in pay:
        n, d = stage3.compare(c["id"], c["expected"], stage3.calculate("validator-pay", c["inputs"]))
        count, differ = count + n, differ + d
    say(f"  the validator pay of rounds 1 to {len(pay)} (members {', '.join(sorted({x for v in m.values() for x in v})) or 'none'}): "
        f"{count} figures, {'all equal' if not differ else f'{differ} DIFFER'}")
    common.write_case("public-run", ordered, views, m)
    write_manifest(chain_id)
    spending()
    return common.finish(started, "N was paid for its job and claimed one round's LPT; every figure read from the chain "
                                  "equals the calculator's.")


def write_manifest(chain_id: int, more: dict | None = None) -> None:
    """The run's manifest, in the shape of deployments/: the template as it
    was at the deployment, with the chain, the addresses, the deployment's
    source commit and the accounts filled in, and `more` added to its
    deployment."""
    d = STATE["done"]["deploy"]
    m = json.loads(json.dumps(d["template"]))
    template = f"{m['testnet']}.json"  # the template's file is named for its testnet
    m["testnet"] = f"public-run-{chain_id}"
    m["about"] = (f"Written by demo/public-run for its run on chain {chain_id}, from {template}. "
                  "The addresses, accounts and source commit are this run's.")
    for c in m["contracts"]:
        c["address"] = C[c["contract"]]
    m["deployment"] = {"deployed": True, "chain_id": chain_id, "deployed_at_block": common.FROM_BLOCK,
                       "deployer": STATE["deployer"], "source_commit": d["source_commit"],
                       "accounts": {k: ADDR[k] for k in ROLES}, "treasury": ADDR["Treasury"],
                       # Parameters' one authority, read from the chain: in
                       # stage 3 an address the run made up, whose key nobody
                       # holds; in stage 4 the owner's authority key.
                       "parameters_authority": f"0x{call(C['Parameters'], 'authority()'):040x}", **(more or {})}
    if chain_id in LOCAL_CHAINS:
        m["chain"] = {"kind": "a local development chain, started apart from the run and mining on its own",
                      "chain_id": chain_id, "round_length_blocks": m["chain"]["round_length_blocks"]}
        m["stand_ins"].insert(0, {"stand_in": "Local chain", "for": "Arbitrum Sepolia (D-14)",
                                  "value": f"chain id {chain_id} on this machine, not controlled by the run: no block, "
                                           "time or balance was set by it"})
    out = STATE_DIR / "manifest.json"
    out.write_text(json.dumps(m, indent=2, ensure_ascii=False) + "\n")
    say(f"  wrote the run's manifest to manifest.json in the state directory (source {m['deployment']['source_commit'][:12]})")


def spending() -> None:
    """What the run cost in fees, by account. The deployer's are from the
    receipts of the transactions the run sent with its key, which may also
    be used elsewhere. N, P, V and the keeper send only for the run, so
    their count is their nonce and their fees are the gas money the deployer
    gave them less what they hold now."""
    hashes = STATE["sent"]
    got = common.rpc_batch([("eth_getTransactionByHash", [h]) for h in hashes] +
                           [("eth_getTransactionReceipt", [h]) for h in hashes])
    pairs = list(zip([a.get("result") for a in got[:len(hashes)]], [a.get("result") for a in got[len(hashes):]]))
    missing = [h for h, (t, r) in zip(hashes, pairs) if not t or not r]
    if missing:
        raise NotRun(f"the chain at LP2_RPC_URL does not show {len(missing)} of the run's transactions, such as {missing[0]}")
    mine = [(t, r) for t, r in pairs if t["from"].lower() == STATE["deployer"].lower()]
    roles = [ADDR[w] for w in ROLES]
    given = {a.lower(): sum(int(t["value"], 16) for t, _ in mine if (t.get("to") or "").lower() == a.lower()) for a in roles}
    now = common.rpc_batch([("eth_getTransactionCount", [a, "latest"]) for a in roles] +
                           [("eth_getBalance", [a, "latest"]) for a in roles])
    rows = []
    # The owner's keys, the deployer's and, in stage 4, the authority's: their
    # fees from the receipts of what the run sent with them.
    for who in ("deployer", "authority"):
        if who in STATE:
            sent = [r for t, r in pairs if t["from"].lower() == STATE[who].lower()]
            rows.append((who, len(sent), sum(int(r["gasUsed"], 16) * int(r["effectiveGasPrice"], 16) for r in sent)))
    rows += [(w, int(now[i]["result"], 16), given[a.lower()] - int(now[len(roles) + i]["result"], 16))
             for i, (w, a) in enumerate(zip(ROLES, roles))]
    say(f"\n  what the run paid in fees\n  {'account':<10}{'transactions':>14}{'fees (wei)':>26}")
    for who, count, fee in rows:
        # An account sent more gas money than the run gave it, such as a
        # standing keeper topped up, holds more than it was given: its fees
        # are not known from its balance, and are left out of the total.
        say(f"  {who:<10}{count:>14}{fee:>26,}" if fee >= 0 else
            f"  {who:<10}{count:>14}{'not known':>26}  (it was sent more gas money than the run gave it)")
    fees = sum(r[2] for r in rows if r[2] >= 0)
    say(f"  {'all':<10}{sum(r[1] for r in rows):>14}{fees:>26,}  ({fees / 10**18:.6f} of the chain's native token)")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(prog="demo/public-run", description="The public run (demo/PUBLIC.md).")
    parser.add_argument("command", nargs="?", choices=("stage-4", "serve"),
                        help="stage-4: deploy and run the stage 4 system; serve: start the services a finished "
                             "stage 4 run handed over to. With none, the stage 3 run")
    command = parser.parse_args().command
    if command:
        import public4  # the stage 4 run and its services, which read stage 4's shared examples
        common.run_main(public4.main if command == "stage-4" else public4.serve)
    else:
        common.run_main(main)
