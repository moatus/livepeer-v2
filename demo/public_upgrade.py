"""demo/public-run upgrade, stop and lift: the upgrade key U and the pause
key S on a running deployment (rulebook R-27), beside demo/public-run serve.

  demo/public-run upgrade CONTRACT SOURCE [--replace] [--call SIGNATURE ARG...]
  demo/public-run stop usdc|lpt|both
  demo/public-run lift usdc|lpt|both

upgrade replaces the code of CONTRACT, one of the deployment's contracts
behind a proxy, with the contract SOURCE names (a file in the repository and
the contract's name, as in contracts/test/BrokerWithTicketCount.sol:
BrokerWithTicketCount), at the same address and with every stored figure
kept. Before it sends anything it checks the chain id, that the proxy is
this deployment's with Upgrades as its admin, that the key given is the
upgrade key in force, and two gates against the record the run kept of the
code in force: the new code's constructor takes the same parameter types in
the same order, and is built with the arguments the code in force was
built with; its storage layout begins with the layout of the code in force,
entry by entry and each type in full. Code in force the run has no record
of is refused.

It then decides from the chain, never from a file of its own: code in force
that is already the new code (the same runtime code and the same wiring)
only writes the manifest; a pending, unlapsed announcement of the same code
and call is waited for and applied; a different one is refused unless
--replace, which announces over it; with none, it deploys the new code with
U's key, announces it and prints the round it applies from and the round it
lapses at. It waits for that round, which the standing keeper starts, or
applies at once while the contract's side is stopped. After the apply it
checks that the proxy runs the new code with every address unchanged, and
writes the code in force into the run's manifest. So a command stopped at
any point and run again sends no second announcement and no second apply.

stop sets a stop with the pause key S (in force from its round through
six more; the Broker's payments, or bonding, claims and the buy, or both),
and lift lifts one with U. Neither waits for anything.

The commands hold their own lock in the state directory, one for U's
sends and one for S's, never the run's, so demo/public-run serve keeps
running beside them. Each sends only to chains on the run's allow-list.

Environment:
  LP2_RPC_URL                  the chain's JSON-RPC URL
  LP2_STATE_DIR                the state directory of a demo/public-run full run
  LP2_UPGRADER_KEYSTORE        U's keystore file, outside the repository (upgrade, lift)
  LP2_UPGRADER_PASSWORD_FILE   its password file, outside the repository
  LP2_PAUSER_KEYSTORE          S's keystore file, outside the repository (stop)
  LP2_PAUSER_PASSWORD_FILE     its password file, outside the repository
  LP2_TEST_HOLD                deployed, announced or applied: stop and wait
                               to be killed at that point (./dev demo upgrade)

Exit 0 done; 1 a check after a send failed; 2 refused or could not run,
with nothing sent.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import signal
import subprocess
import time
from pathlib import Path

import common
import public
from common import NotRun, Failed, say, C

SIDES = {"usdc": 0, "lpt": 1, "both": 2}
SIDE_NAMES = ("USDC", "LPT", "both")
HOLDS = ("deployed", "announced", "applied")
POLL = 2   # seconds between reads of the round while waiting
RECORDS = "upgrades.json"   # the record of each code this command deployed, for the next upgrade's gates
STATE: dict = {}
KEY: dict = {}   # the key this command sends with: its address and cast's signing flags
LOCK = None


# --- opening the run ------------------------------------------------------------

def open_run(role: str) -> None:
    """The run's state, read only; this command's own lock; the chain and
    the key. `role` is "upgrader" or "pauser"."""
    global LOCK
    url = os.environ.get("LP2_RPC_URL", "")
    if not url:
        raise NotRun("LP2_RPC_URL is not set (see demo/PUBLIC.md)")
    state_dir = public.outside_repo("LP2_STATE_DIR", False)
    if not (state_dir / "state.json").is_file():
        raise NotRun(f"{state_dir} holds no run's state.json: no demo/public-run full run deployed there")
    public.STATE_DIR = state_dir
    LOCK = open(os.open(public.private(state_dir / f"{role}.lock"), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600), "r+")
    try:
        fcntl.flock(LOCK, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise NotRun(f"another command with the {role} key is running on this state directory (process "
                     f"{LOCK.read().strip() or 'unknown'}); one at a time. Nothing was sent")
    LOCK.seek(0)
    LOCK.truncate()
    LOCK.write(str(os.getpid()))
    LOCK.flush()
    common.RUN = state_dir
    common.RPC = url
    common.LOG = open(state_dir / "upgrades.log", "a")
    STATE.update(json.loads((state_dir / "state.json").read_text()))
    if "deploy" not in STATE.get("done", {}):
        raise NotRun("this state directory's run has not deployed")
    try:
        chain_id = int(common.rpc("eth_chainId", []), 16)
    except (OSError, ValueError, common.urllib.error.URLError) as e:
        raise NotRun(f"the chain at LP2_RPC_URL did not answer: {e}")
    if chain_id not in public.ALLOWED_CHAINS:
        raise NotRun(f"chain id {chain_id} is not on the allow-list; nothing was sent")
    if chain_id != STATE["chain_id"]:
        raise NotRun(f"this state directory belongs to chain {STATE['chain_id']}, not {chain_id}. Nothing was sent")
    deploy = STATE["done"]["deploy"]
    C.clear()
    C.update(deploy["contracts"])
    common.FROM_BLOCK = deploy["from_block"]
    public.STATE.clear()
    public.STATE.update(STATE)
    public.check_deployment()
    if "Upgrades" not in C:
        raise NotRun("this state directory's deployment has no Upgrades: it predates upgrades")
    var = "UPGRADER" if role == "upgrader" else "PAUSER"
    keystore = public.outside_repo(f"LP2_{var}_KEYSTORE", True)
    password = public.outside_repo(f"LP2_{var}_PASSWORD_FILE", True)
    got = subprocess.run(["cast", "wallet", "address", "--keystore", str(keystore), "--password-file", str(password)],
                         capture_output=True, text=True)
    if got.returncode != 0:
        raise NotRun(f"LP2_{var}_KEYSTORE could not be opened with its password file. Nothing was sent")
    address = got.stdout.strip()
    owners = {STATE.get("deployer", "").lower(): "the deployer's", STATE.get("authority", "").lower(): "G's"}
    if address.lower() in owners:
        raise NotRun(f"LP2_{var}_KEYSTORE is {owners[address.lower()]} key, not the {role} key. Nothing was sent")
    in_force = address_of(common.call(C["Upgrades"], f"{role}()"))
    if in_force.lower() != address.lower():
        raise NotRun(f"LP2_{var}_KEYSTORE holds {address}, but this deployment's {role} key in force is {in_force}. "
                     "Nothing was sent")
    KEY.update(address=address, signer=["--keystore", str(keystore), "--password-file", str(password)])
    say(f"\n=== demo/public-run, {time.strftime('%Y-%m-%d %H:%M:%S')}: chain {chain_id}, "
        f"Upgrades {C['Upgrades']}, the {role} key {address}")


def address_of(word: int) -> str:
    return f"0x{word:040x}"


# --- reading and sending ----------------------------------------------------------

ERRORS: dict[str, tuple[str, list[str]]] = {}


def refusal(data: str) -> str:
    """A refusal's revert data as its error, from the build's ABIs: the
    proxy's, Upgrades', the stopped contracts' and the new code's."""
    if not ERRORS:
        found = {}
        for f in (common.ROOT / "contracts" / "out").glob("*.sol/*.json"):
            for x in json.loads(f.read_text()).get("abi", []):
                if x["type"] == "error":
                    found[f"{x['name']}({','.join(i['type'] for i in x['inputs'])})"] = \
                        (x["name"], [i["type"] for i in x["inputs"]])
        for sig, named in found.items():
            ERRORS[common.sel(sig)] = named
    data = data or "0x"
    name, types = ERRORS.get(data[:10], (None, []))
    if not name:
        return f"revert data {data[:74]}"
    words = [int(data[10 + 64 * i:74 + 64 * i] or "0", 16) for i in range(len(types))]
    shown = [address_of(w) if t == "address" else str(w) for t, w in zip(types, words) if t in ("address", "uint256")]
    return f"{name}({', '.join(shown)})" if shown else name


def simulate(to: str, data: str) -> str | None:
    """The refusal a call from the key would meet now, or None."""
    a, = common.rpc_batch([("eth_call", [{"from": KEY["address"], "to": to, "data": data}, "latest"])])
    if "error" not in a:
        return None
    return refusal(a["error"].get("data") or "")


def send(to: str, signature: str, *args) -> dict:
    """A transaction from the key, asked first by a call: a refusal sends
    nothing. Returns its receipt."""
    data = common.sh(["cast", "calldata", signature, *map(str, args)])
    refused = simulate(to, data)
    if refused:
        raise NotRun(f"{signature.split('(')[0]} would be refused: {refused}. Nothing was sent")
    done = subprocess.run(["cast", "send", "--json", "--rpc-url", common.RPC, *KEY["signer"], to, data],
                          capture_output=True, text=True)
    if done.returncode != 0:
        raise Failed(f"{signature.split('(')[0]}: {done.stderr.strip()[-400:]}")
    r = json.loads(done.stdout)
    if r["status"] != "0x1":
        raise Failed(f"{signature.split('(')[0]}: reverted in transaction {r['transactionHash']}")
    return r


def event(r: dict, signature: str) -> list[dict]:
    t = common.topic(signature)
    return [lg for lg in r["logs"] if lg["topics"][0] == t and lg["address"].lower() == C["Upgrades"].lower()]


def words_of(data: str) -> list[int]:
    data = data[2:]
    return [int(data[i:i + 64], 16) for i in range(0, len(data), 64)]


def current_round() -> int:
    return common.call(C["RoundClock"], "currentRound()")


def slot(proxy: str, which: str) -> str:
    return "0x" + common.rpc("eth_getStorageAt", [proxy, which, "latest"])[-40:]


def hold(point: str) -> None:
    """./dev demo upgrade's kill points: stop here until killed."""
    if os.environ.get("LP2_TEST_HOLD") == point:
        say(f"  holding after it was {point} (LP2_TEST_HOLD)")
        while True:
            time.sleep(60)


# --- the gates ----------------------------------------------------------------------

def described(layout: dict, type_id: str) -> dict:
    """A storage type as the compiler describes it, in full: its label,
    encoding and size, and its members, key, value or element, each in
    full. The contract a struct is declared in is left out of a label: it
    does not change where anything is stored."""
    t = layout["types"][type_id]
    d = {"label": re.sub(r"\b[A-Za-z_]\w*\.(?=[A-Za-z_])", "", t["label"]), "encoding": t["encoding"],
         "bytes": t["numberOfBytes"]}
    if "members" in t:
        d["members"] = [{"name": m["label"], "slot": m["slot"], "offset": m["offset"], "type": described(layout, m["type"])}
                        for m in t["members"]]
    for k in ("key", "value", "base"):
        if k in t:
            d[k] = described(layout, t[k])
    return d


def entries(layout: dict) -> list[dict]:
    return [{"name": e["label"], "slot": e["slot"], "offset": e["offset"], "type": described(layout, e["type"])}
            for e in layout.get("storage", [])]


def first_difference(path: str, old, new) -> str:
    if isinstance(old, dict) and isinstance(new, dict):
        for k in old:
            if k not in new:
                return f"{path}{k}: none in the new code"
            if old[k] != new[k]:
                return first_difference(f"{path}{k}: ", old[k], new[k])
        return f"{path}the new code adds {', '.join(k for k in new if k not in old)}"
    if isinstance(old, list) and isinstance(new, list):
        for i, (a, b) in enumerate(zip(old, new)):
            if a != b:
                return first_difference(f"{path}{i + 1}: ", a, b)
        return f"{path}{len(old)} in the code in force, {len(new)} in the new code"
    return f"{path}{old} in the code in force, {new} in the new code"


def layout_problem(old: dict, new: dict) -> str | None:
    """Why the new code's storage layout does not begin with the layout of
    the code in force, or None: entry by entry, each entry's name, slot,
    offset and type, and each type in full (rulebook R-27)."""
    a, b = entries(old), entries(new)
    for i, e in enumerate(a):
        if i >= len(b):
            return f"its layout ends after {len(b)} entries; the code in force stores {e['name']} at slot {e['slot']}"
        if b[i] != e:
            return f"its layout's entry {i + 1}, {e['name']} in the code in force, differs: " + \
                first_difference("", e, b[i])
    return None


def constructor_problem(old: dict, new: dict) -> str | None:
    if new["constructor"] != old["constructor"]:
        return (f"its constructor takes ({', '.join(new['constructor'])}), not the code in force's "
                f"({', '.join(old['constructor'])}) in that order")
    return None


def records() -> dict:
    """What the run and this command know of each code they deployed, by
    address."""
    out = {k.lower(): v for k, v in STATE["done"]["deploy"].get("codes", {}).items()}
    f = public.STATE_DIR / RECORDS
    if f.is_file():
        out.update({k.lower(): v for k, v in json.loads(f.read_text())["codes"].items()})
    return out


def keep_record(address: str, record: dict) -> None:
    f = public.private(public.STATE_DIR / RECORDS)
    doc = json.loads(f.read_text()) if f.is_file() else {"codes": {}}
    doc["codes"][address.lower()] = record
    public.write_whole(f, json.dumps(doc, indent=1) + "\n")


def source_of(given: str) -> str:
    """SOURCE as forge names it inside contracts/."""
    path, _, name = given.partition(":")
    p = (common.ROOT / path).resolve()
    if not name or not p.is_file() or not p.is_relative_to(common.ROOT / "contracts"):
        raise NotRun(f"{given} does not name a contract in a file under contracts/ (path:Name). Nothing was sent")
    return f"{p.relative_to(common.ROOT / 'contracts')}:{name}"


def runtime(record: dict, source: str) -> str:
    """The runtime code the new code has once built with the recorded
    arguments, as the chain would create it, asked by a call."""
    code = common.inspect(source, "bytecode")
    args = common.sh(["cast", "abi-encode", f"f({','.join(record['abi_types'])})", *record["args"]])[2:] \
        if record["abi_types"] else ""
    a, = common.rpc_batch([("eth_call", [{"from": KEY["address"], "data": code + args}, "latest"])])
    if "error" in a:
        raise NotRun(f"the new code's constructor refuses the recorded arguments: {a['error'].get('message')}. "
                     "Nothing was sent")
    return a["result"].lower()


def code_at(address: str) -> str:
    return common.rpc("eth_getCode", [address, "latest"]).lower()


# --- the manifest ---------------------------------------------------------------

def write_manifest() -> None:
    """The run's manifest with the code in force of each contract behind a
    proxy read from the chain, written whole beside the running services."""
    path = public.private(public.STATE_DIR / "manifest.json")
    m = json.loads(path.read_text())
    public.fill_contracts(m)
    public.write_whole(path, json.dumps(m, indent=2, ensure_ascii=False) + "\n")
    shown = ", ".join(f"{c['contract']} {c['implementation']}" for c in m["contracts"] if "implementation" in c)
    say(f"  wrote manifest.json in the state directory; the code in force: {shown}")


# --- the commands -----------------------------------------------------------------

def upgrade(contract: str, given: str, call: list[str] | None, replace: bool) -> int:
    open_run("upgrader")
    if contract not in common.UPGRADEABLE or contract not in C:
        raise NotRun(f"{contract} is not one of this deployment's contracts behind a proxy "
                     f"({', '.join(n for n in common.UPGRADEABLE if n in C)}). Nothing was sent")
    if not (public.STATE_DIR / "manifest.json").is_file():
        raise NotRun("the state directory holds no manifest.json: the run has not finished. Nothing was sent")
    proxy = C[contract]
    if slot(proxy, common.ADMIN_SLOT).lower() != C["Upgrades"].lower():
        raise NotRun(f"{contract}'s proxy does not name this deployment's Upgrades as its admin. Nothing was sent")
    side = common.call(C["Upgrades"], "sideOf(address)", proxy)
    in_force = slot(proxy, common.IMPLEMENTATION_SLOT)
    say(f"Upgrade {contract} ({proxy}, the {SIDE_NAMES[side]} side) from its code in force {in_force} to {given}")
    old = records().get(in_force.lower())
    if not old:
        raise NotRun(f"the code in force at {in_force} has no recorded constructor arguments or storage layout: "
                     "neither the run nor this command deployed it. Nothing was sent")
    source = source_of(given)
    common.sh(["forge", "build", "--root", str(common.ROOT / "contracts")])
    new = common.code_record(contract, source, old["args"])
    for problem in (constructor_problem(old, new), layout_problem(old["layout"], new["layout"])):
        if problem:
            raise NotRun(f"{given} is refused: {problem}. Nothing was sent")
    say(f"  the gates pass: its constructor takes the code in force's parameter types in order, built with its "
        f"{len(old['args'])} recorded arguments, and its layout begins with the code in force's "
        f"{len(entries(old['layout']))} entries")
    data = common.sh(["cast", "calldata", call[0], *call[1:]]) if call else "0x"
    want = runtime(new, source)

    while True:
        if code_at(slot(proxy, common.IMPLEMENTATION_SLOT)) == want:
            say(f"  {contract}'s code in force is already this code: nothing to send")
            check_after(proxy, slot(proxy, common.IMPLEMENTATION_SLOT))
            write_manifest()
            return 0
        code, pdata, applies, lapses = pending(proxy)
        now = current_round()
        if code and now < lapses:
            if code_at(code) == want and pdata.lower() == data.lower():
                say(f"  the announcement of this code and call is pending at {code}: applies from round {applies}, "
                    f"lapses at round {lapses}")
                if code.lower() not in records():
                    keep_record(code, new)
                if wait_and_apply(proxy, side, code, applies, lapses):
                    continue
            elif not replace:
                raise NotRun(f"a different announcement for {contract} is pending: code {code}, call {pdata[:74]}, "
                             f"applies from round {applies}. --replace announces this one over it. Nothing was sent")
            else:
                say(f"  --replace: announcing over the pending code {code}")
        elif code:
            say(f"  the announcement of {code} lapsed at round {lapses}: it counts as none")
        address = deploy(source, new)
        keep_record(address, new)
        hold("deployed")
        r = send(C["Upgrades"], "announceUpgrade(address,address,bytes)", proxy, address, data)
        w = words_of(event(r, "UpgradeAnnounced(address,address,bytes,uint256,uint256)")[0]["data"])
        say(f"  announced in round {w[2] - 7} (tx {r['transactionHash']}): applies from round {w[2]}, lapses at round {w[3]}")
        hold("announced")
        replace = False


def pending(proxy: str) -> tuple[str, str, int, int]:
    raw = common.rpc("eth_call", [{"to": C["Upgrades"], "data": common.calldata("pending(address)", proxy)}, "latest"])
    w = words_of(raw)
    start = w[1] // 32
    length = w[start]
    data = "0x" + raw[2 + 64 * (start + 1):2 + 64 * (start + 1) + 2 * length]
    return (address_of(w[0]) if w[0] else ""), data, w[2], w[3]


def deploy(source: str, record: dict) -> str:
    argv = ["forge", "create", "--root", str(common.ROOT / "contracts"), "--rpc-url", common.RPC, *KEY["signer"],
            "--broadcast", source]
    if record["args"]:
        argv += ["--constructor-args", *record["args"]]
    out = common.sh(argv)
    address = next(ln.split()[-1] for ln in out.splitlines() if ln.startswith("Deployed to:"))
    say(f"  deployed {source} at {address} with U's key")
    return address


def wait_and_apply(proxy: str, side: int, code: str, applies: int, lapses: int) -> bool:
    """Wait for the round the announcement applies from, which the keeper
    starts, or for the contract's side to be stopped, then apply. False if
    it lapsed or another apply landed first, so the caller decides again."""
    seen = None
    while True:
        now = current_round()
        stopped = bool(common.call(C["Upgrades"], "stopped(uint8)", side))
        if now >= lapses:
            say(f"  round {now}: the announcement lapsed at round {lapses}")
            return False
        if now >= applies or stopped:
            break
        if now != seen:
            say(f"  {time.strftime('%H:%M:%S')} round {now}; it applies from round {applies}, or at once under a "
                f"{SIDE_NAMES[side]} stop")
            seen = now
        time.sleep(POLL)
    if pending(proxy)[0].lower() != code.lower():
        return True
    r = send(C["Upgrades"], "applyUpgrade(address)", proxy)
    applied = event(r, "UpgradeApplied(address,address,uint256,bool)")
    under = bool(words_of(applied[0]["data"])[2]) if applied else False
    say(f"  {time.strftime('%H:%M:%S')} applied in round {now}{' under the stop' if under else ''}, block "
        f"{int(r['blockNumber'], 16)} (tx {r['transactionHash']})")
    hold("applied")
    return True


def check_after(proxy: str, code: str) -> None:
    """The proxy runs the code given, Upgrades is still its admin, and every
    address of the deployment holds code with the Controller's names
    unchanged."""
    if slot(proxy, common.IMPLEMENTATION_SLOT).lower() != code.lower():
        raise Failed(f"the proxy {proxy} does not run {code}")
    if slot(proxy, common.ADMIN_SLOT).lower() != C["Upgrades"].lower():
        raise Failed(f"the proxy {proxy}'s admin slot no longer names Upgrades")
    public.check_deployment()
    for legacy, ours in {"BondingManager": "Registry", "RoundsManager": "RoundClock", "TicketBroker": "Broker"}.items():
        got = common.call(C["Controller"], "getContract(bytes32)", common.sh(["cast", "keccak", legacy]))
        if got != int(C[ours], 16):
            raise Failed(f"the Controller no longer answers {legacy} with the {ours}")
    say(f"  the proxy {proxy} runs {code}; Upgrades is its admin; every address of the deployment is unchanged")


def stop(side: str) -> int:
    open_run("pauser")
    r = send(C["Upgrades"], "setStop(uint8)", SIDES[side])
    for lg in event(r, "StopSet(uint8,uint256,uint256)"):
        _, ends = words_of(lg["data"])[:2]
        set_in = words_of(lg["data"])[0]
        say(f"  the {SIDE_NAMES[int(lg['topics'][1], 16)]} stop is set in round {set_in}: in force through round "
            f"{ends - 1}, ending at the start of round {ends} unless U lifts it (tx {r['transactionHash']})")
    return 0


def lift(side: str) -> int:
    open_run("upgrader")
    r = send(C["Upgrades"], "liftStop(uint8)", SIDES[side])
    for lg in event(r, "StopLifted(uint8,uint256)"):
        say(f"  the {SIDE_NAMES[int(lg['topics'][1], 16)]} stop is lifted in round {words_of(lg['data'])[0]} "
            f"(tx {r['transactionHash']})")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="demo/public-run", description="The upgrade and pause keys (demo/PUBLIC.md).")
    sub = parser.add_subparsers(dest="command", required=True)
    u = sub.add_parser("upgrade", help="replace a contract's code at its address, after notice or under a stop")
    u.add_argument("contract", help="one of the deployment's contracts behind a proxy, such as Broker")
    u.add_argument("source", help="the new code: a file under contracts/ and the contract's name, path:Name")
    u.add_argument("--replace", action="store_true", help="announce over a different pending announcement")
    u.add_argument("--call", nargs="+", metavar=("SIGNATURE", "ARG"), help="the initialisation call and its arguments")
    for name, what in (("stop", "set a stop with the pause key"), ("lift", "lift a stop with the upgrade key")):
        sub.add_parser(name, help=what).add_argument("side", choices=tuple(SIDES))
    args = parser.parse_args(argv)

    def interrupted(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    try:
        if args.command == "upgrade":
            return upgrade(args.contract, args.source, args.call, args.replace)
        return (stop if args.command == "stop" else lift)(args.side)
    except NotRun as e:
        say(f"REFUSED: {e}")
        return 2
    except Failed as e:
        say(f"FAILED: {e}")
        return 1
    except KeyboardInterrupt:
        say("interrupted")
        return 2
