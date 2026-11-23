"""./dev demo buyback: S-07 section 1 on a fresh local chain.

Starts a local chain and deploys stage 4's contracts: stage 3's, with the
Broker given the Buyback, then the test pool, Parameters, which holds the
buyback values, and the Buyback. Checks the deployment: the Broker's
Buyback, the Buyback's Broker and the pool code hash it pins. Sends LPT to the Buyback, which it
must never count or burn. Then runs section 1's walk at the walk's own
times, offset by the start time it records: jobs paid by tickets signed
and redeemed directly, the pool seeded, a trader's swap, and every buy and
poke, refused or not. Then Parameters as G (S-10 R-25).

Each buy and refusal is printed beside section 1's. The run is written as
a `buyback` case into a copy of the shared examples, and
`./calculator/check` reproduces it from the rules alone.

Exit 0 every figure equal and every behaviour seen; 1 a figure differed or
a behaviour was missing; 2 the demo could not run (a tool or a port). The
chain, the LPT, the pool and the buyback values are read from
deployments/stage-4-local.json.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import time
import urllib.error

import common
common.use("stage-4-local.json")
from common import *  # noqa: E402,F401,F403
from common import RUN, ROOT, MANIFEST, PARAMS, CASES, VECTORS, PORT, RPC, C, ADDR, KEY, NAMES, FAILURES  # noqa: E402
import ledgercase  # noqa: E402

WALK = CASES["buyback-walk"]
# LPT the deployer sends to the Buyback before the walk: never counted,
# never burned (R-16 rule 9).
DONATION = 7 * 10**18
# A buy or poke is sent with this much gas, so a refusal is mined and its
# code read from the chain at the block and time it was refused.
GAS = 3_000_000
WIN_ALWAYS = 2**256 - 1

# R-16's refusals: each code with its arguments' types.
REFUSALS = {"ALREADY_BOUGHT": ("uint256",), "NOTHING_HELD": (), "NO_LIQUIDITY": (),
            "TWAP_NOT_READY": ("uint256", "uint256"), "SLIPPAGE": ("uint256", "uint256"),
            "NOT_DUE": ("uint256",), "POOL_INVALID": ("address",), "LPT_SHORT": ("uint256", "uint256")}
BOUGHT = "Bought(uint256,uint256,uint256,uint256,uint256,uint256,uint256)"


# --- the chain, with time the demo sets ------------------------------------------

def preflight() -> None:
    missing = [t for t in ("go", "forge", "anvil", "cast") if not shutil.which(t)]
    if missing:
        raise NotRun(f"missing {', '.join(missing)} (see README: Run the demo)")
    port = PORT["chain"]
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            raise NotRun(f"port {port} (chain) is in use; set LP2_DEMO_PORT_BASE to move the demo's ports")


def start_chain() -> None:
    """anvil mining a block for each transaction, at the time the demo sets.
    Each block's time is the one before's (an interval of 0) unless the
    demo sets it, never the wall clock's: otherwise a second passing during
    a run would carry blocks past the walk's time."""
    start("anvil", ["anvil", "--port", str(PORT["chain"]), "--chain-id", str(MANIFEST["chain"]["chain_id"])])
    for _ in range(100):
        try:
            block_number()
            rpc("anvil_setBlockTimestampInterval", [0])
            common.RPC_FILE.write_text(RPC)
            return
        except (urllib.error.URLError, ConnectionError, OSError):
            time.sleep(0.1)
    raise NotRun("the local chain did not start")


T0 = 0   # the chain time of the walk's time 0


def at(t: int) -> None:
    """The next block is at the walk's time t."""
    rpc("evm_setNextBlockTimestamp", [T0 + t])


def block_time(r: dict) -> int:
    return int(rpc("eth_getBlockByNumber", [r["blockNumber"], False])["timestamp"], 16)


def to_round(round_: int, t: int) -> None:
    """Starts rounds, a round's blocks apart, until `round_` is current,
    all at the walk's time t."""
    while current_round() < round_:
        at(t)
        rpc("anvil_mine", [hex(MANIFEST["chain"]["round_length_blocks"]), "0x0"])
        at(t)
        send(KEY["Keeper"], C["RoundClock"], "initializeRound()")


def keeper_once(keystore_file: Path, password_file: Path) -> tuple[int, str]:
    """Runs lp2-keeper --once with the Buyback's address; its exit code and log."""
    done = subprocess.run([str(RUN / "bin" / "lp2-keeper"), "--rpc-url", RPC, "--keystore-file", str(keystore_file),
                           "--keystore-password-file", str(password_file), "--controller-address", C["Controller"],
                           "--buyback-address", C["Buyback"], "--once"], capture_output=True, text=True, timeout=120)
    return done.returncode, (done.stdout + done.stderr).strip()


# --- deployment -------------------------------------------------------------------

def deploy_stage4() -> None:
    """Stage 3's contracts with the pool, and the Broker given the Buyback's
    address, then the Buyback, which reads its values from Parameters and
    is pinned to the pool's code."""
    # Stage 2 creates five contracts and stage 3 with the pool six more
    # (the token, StakeLedger, Scores, the pool, Parameters and Claims).
    # The Buyback is the twelfth.
    buyback_at = next_address(11)
    deploy_stage3(buyback_at, pool=True)
    code_hash = sh(["cast", "keccak", rpc("eth_getCode", [C["TestPool"], "latest"])])
    C["Buyback"] = forge_create("src/money/Buyback.sol:Buyback", C["Parameters"], C["Broker"],
                                C["RoundClock"], C["TestLPT"], C["MockUSDC"], code_hash)
    if C["Buyback"].lower() != buyback_at.lower():
        raise Failed(f"the Buyback landed at {C['Buyback']}, not {buyback_at} as the Broker was told")
    for n in ("TestPool", "Buyback", "Broker", "Parameters"):
        NAMES[C[n].lower()] = n
    (RUN / "addresses.json").write_text(json.dumps({**C, "t0": T0}, indent=1) + "\n")
    say("  " + ", ".join(f"{n} {C[n]}" for n in ("Broker", "TestLPT", "TestPool", "Parameters", "Buyback")))

    say("\n2. The deployment's check")
    check("broker.buyback() is the Buyback", hex(call(C["Broker"], "buyback()")), hex(int(C["Buyback"], 16)))
    check("buyback.broker() is the Broker", hex(call(C["Buyback"], "broker()")), hex(int(C["Broker"], 16)))
    check("buyback.values() is Parameters", hex(call(C["Buyback"], "values()")), hex(int(C["Parameters"], 16)))
    check("parameters.authority() is G, the governance stand-in", hex(call(C["Parameters"], "authority()")),
          hex(int(ADDR["G"], 16)))
    check("buyback.poolCodeHash() is the TestPool's runtime code hash", hex(call(C["Buyback"], "poolCodeHash()")),
          hex(int(code_hash, 16)))
    check("parameters.poolCodeHash() equals buyback.poolCodeHash()", hex(call(C["Parameters"], "poolCodeHash()")),
          hex(call(C["Buyback"], "poolCodeHash()")))
    got = words(C["Parameters"], "buybackValuesAt(uint256)", 1)
    want = [int(C["TestPool"], 16)] + [int(PARAMS[k]) for k in ("buyback_burn_ppm", "buyback_twap_seconds",
                                                                 "buyback_max_slippage_ppm", "buyback_every_rounds")]
    check("the deployment's buyback values are the manifest's and the shared examples'", got, want)


# --- the walk's actions ---------------------------------------------------------

def job(face: int, t: int) -> dict:
    """P funds `face` and pays N one winning ticket of `face` created in the
    current round, which N redeems: half of it is the network's."""
    P, N = ADDR["P"], ADDR["N"]
    for key, to, sig, *args in ((KEY["P"], C["MockUSDC"], "mint(address,uint256)", P, face),
                                (KEY["P"], C["MockUSDC"], "approve(address,uint256)", C["Broker"], face),
                                (KEY["P"], C["Broker"], "fund(uint256,address,uint256)", face, ZERO, 0)):
        at(t)
        send(key, to, sig, *args)
    created = current_round()
    aux = f"{created:064x}" + f"{call(C['RoundClock'], 'blockHashForRound(uint256)', created):064x}"
    rand = int.from_bytes(os.urandom(32), "big")
    nonce = int.from_bytes(os.urandom(4), "big")
    rand_hash = sh(["cast", "keccak", f"0x{rand:064x}"])
    packed = N[2:] + P[2:] + f"{face:064x}{WIN_ALWAYS:064x}{nonce:064x}" + rand_hash[2:] + aux
    signature = sh(["cast", "wallet", "sign", "--private-key", KEY["P"], sh(["cast", "keccak", "0x" + packed])])
    at(t)
    return send(KEY["N"], C["Broker"],
                "redeemWinningTicket((address,address,uint256,uint256,uint256,bytes32,bytes),bytes,uint256)",
                f"({N},{P},{face},{WIN_ALWAYS},{nonce},{rand_hash},0x{aux})", signature, rand)


def seed(usdc: int, lpt_wei: int, t: int) -> dict:
    """The deployer adds mock USDC and genesis LPT to the pool's reserves."""
    at(t)
    send(KEY["deployer"], C["MockUSDC"], "mint(address,uint256)", C["TestPool"], usdc)
    at(t)
    send(KEY["deployer"], C["TestLPT"], "transfer(address,uint256)", C["TestPool"], lpt_wei)
    at(t)
    return send(KEY["deployer"], C["TestPool"], "mint(address)", ADDR["deployer"])


def reserves() -> tuple[int, int]:
    """The pool's USDC and LPT reserves."""
    r0, r1, _ = words(C["TestPool"], "getReserves()")
    return (r0, r1) if usdc_is_0() else (r1, r0)


def usdc_is_0() -> bool:
    return call(C["TestPool"], "token0()") == int(C["MockUSDC"], 16)


def swap(usdc: int, t: int) -> dict:
    """A trader pays `usdc` and takes v2's getAmountOut in LPT."""
    ru, rl = reserves()
    out = usdc * 997 * rl // (ru * 1000 + usdc * 997)
    at(t)
    send(KEY["deployer"], C["MockUSDC"], "mint(address,uint256)", ADDR["Trader"], usdc)
    at(t)
    send(KEY["Trader"], C["MockUSDC"], "transfer(address,uint256)", C["TestPool"], usdc)
    at(t)
    a0, a1 = (0, out) if usdc_is_0() else (out, 0)
    return send(KEY["Trader"], C["TestPool"], "swap(uint256,uint256,address,bytes)", a0, a1, ADDR["Trader"], "0x")


def refusal_of(r: dict) -> dict | None:
    """The refusal a mined transaction reverted with, as a case's `refused`
    entry: its code and arguments. None when it took effect."""
    if r["status"] == "0x1":
        return None
    out = rpc("debug_traceTransaction", [r["transactionHash"], {"tracer": "callTracer"}]).get("output", "0x")
    for code, types in REFUSALS.items():
        if out[:10] == sel(f"{code}({','.join(types)})"):
            data = out[10:]
            args = [data[i * 64:(i + 1) * 64] for i in range(len(types))]
            entry = {"code": code}
            if types:
                entry["args"] = ["0x" + a[24:] if ty == "address" else str(int(a, 16)) for a, ty in zip(args, types)]
            return entry
    raise Failed(f"a buyback call reverted with {out[:74]}, not one of R-16's codes")


def bought_of(r: dict) -> dict:
    """The Bought event of a buy, as a case's `bought` entry."""
    found = [lg for lg in r["logs"] if lg["address"].lower() == C["Buyback"].lower() and lg["topics"][0] == topic(BOUGHT)]
    if len(found) != 1:
        raise Failed("a buy that took effect emitted no Bought event")
    held, usdc_in, lpt_out, burned, twap, max_in = (int(found[0]["data"][2 + 64 * i:66 + 64 * i], 16) for i in range(6))
    return {"usdc_in_micro_usdc": str(usdc_in), "lpt_out_wei": str(lpt_out), "burned_wei": str(burned),
            "max_in_micro_usdc": str(max_in), "twap_x112": str(twap)}


def buyback_call(action: str, t: int) -> dict:
    at(t)
    return send(KEY["Keeper"], C["Buyback"], f"{action}()", gas=GAS)


# --- the case -------------------------------------------------------------------

def case_of(case_id: str, supply: int, run: list[dict], final: dict) -> dict:
    """The run as a `buyback` case: each step as sent, at the round and time
    the chain gave it, and what the chain showed. `run` holds each step's
    fields (step, round, time, action and amounts) and, for a buy or poke,
    its `bought` or `refused` entry."""
    steps, bought, refused = [], [], []
    for s in run:
        steps.append({k: v for k, v in s.items() if k not in ("bought", "refused", "shown")})
        if "bought" in s:
            bought.append({"step": s["step"], **s["bought"]})
        if "refused" in s:
            refused.append({"step": s["step"], **s["refused"]})
    return {"id": case_id, "kind": "buyback", "inputs": {"lpt_supply_wei": str(supply), "steps": steps},
            "expected": {"bought": bought, "refused": refused, **{k: str(v) for k, v in final.items()}}}


def shifted(entry: dict, t0: int) -> dict:
    """A refusal the chain gave, with a time argument (NOT_DUE's) told from
    the walk's start, as section 1 tells it."""
    if entry["code"] != "NOT_DUE":
        return entry
    return {**entry, "args": [str(int(entry["args"][0]) - t0)]}


# --- printing -------------------------------------------------------------------

def usdc(v) -> str:
    v = int(v)
    return f"{v // 10**6:,}.{v % 10**6:06d}"


def price(num: int, den: int) -> str:
    """USDC per LPT, from micro-USDC over wei, to the nearest millionth, as
    section 1 gives it."""
    v = (2 * num * 10**18 + den) // (2 * den)
    return f"{v // 10**6:,}.{v % 10**6:06d}"


def outcome(s: dict) -> str:
    if "refused" in s:
        e = s["refused"]
        return f"refused {e['code']}" + (f"({', '.join(e.get('args', []))})" if e.get("args") else "")
    if "bought" in s:
        b = s["bought"]
        return (f"{usdc(b['usdc_in_micro_usdc'])} USDC in, {lpt(b['lpt_out_wei'])} LPT bought and "
                f"{lpt(b['burned_wei'])} burned; TWAP {price(int(b['twap_x112']), 2**112)}, paid "
                f"{price(int(b['usdc_in_micro_usdc']), int(b['lpt_out_wei']))}, bound {usdc(b['max_in_micro_usdc'])} USDC")
    return s.get("shown", "done")


# --- the demo -----------------------------------------------------------------

def main() -> int:
    global T0
    open_log()
    started = time.time()
    say("Buyback demo (S-07 section 1): the network share buys test LPT from a test pool and burns it, on a fresh local chain")
    say(f"Stand-ins, from deployments/stage-4-local.json: {'; '.join(s['stand_in'] for s in MANIFEST['stand_ins'])}.")

    say("\n1. Setting up")
    preflight()
    try:
        sh(["forge", "build", "--root", str(ROOT / "contracts")])
        for prog in ("lp2-keeper", "lp2-params"):
            sh(["go", "build", "-o", str(RUN / "bin" / prog), PROGRAMS[prog]], cwd=ROOT / "programs", env=GO_ENV)
    except Failed as e:
        raise NotRun(f"could not build the contracts or the keeper: {e}")
    start_chain()
    for n in ("deployer", "P", "N", "Trader", "Keeper", "G"):
        new_key(n)
    treasury = "0x" + os.urandom(20).hex()
    ADDR["Treasury"], NAMES[treasury.lower()] = treasury, "Treasury"
    deploy_stage4()
    send(KEY["N"], C["Registry"], "register()")
    send(KEY["deployer"], C["TestLPT"], "transfer(address,uint256)", C["Buyback"], DONATION)
    say(f"  N registered; {lpt(DONATION)} LPT sent to the Buyback beforehand")

    latest = int(rpc("eth_getBlockByNumber", ["latest", False])["timestamp"], 16)
    T0 = (latest // 3600 + 2) * 3600
    (RUN / "addresses.json").write_text(json.dumps({**C, "t0": T0}, indent=1) + "\n")
    supply_before = call(C["TestLPT"], "totalSupply()")
    say(f"\n3. Section 1's walk, from chain time {T0} (time 0 below); supply {lpt(supply_before)} LPT")
    if current_round() != 1:
        raise Failed(f"the walk starts in round 1, the chain is in round {current_round()}")
    refused_vec = {e["step"]: e for e in WALK["expected"]["refused"]}
    bought_vec = {e["step"]: e for e in WALK["expected"]["bought"]}
    run = []
    for s in WALK["inputs"]["steps"]:
        t, action = int(s["time"]), s["action"]
        to_round(int(s["round"]), t)
        if action == "job":
            r = job(int(s["face_micro_usdc"]), t)
        elif action == "seed":
            r = seed(int(s["usdc_micro_usdc"]), int(s["lpt_wei"]), t)
        elif action == "swap":
            r = swap(int(s["usdc_micro_usdc"]), t)
        else:
            r = buyback_call(action, t)
        done = {**{k: v for k, v in s.items() if k not in ("round", "time")},
                "round": str(current_round(int(r["blockNumber"], 16))), "time": str(block_time(r))}
        if action in ("buy", "poke"):
            ref = refusal_of(r)
            if ref:
                done["refused"] = ref
            elif action == "buy":
                done["bought"] = bought_of(r)
            else:
                done["shown"] = "the pool's cumulative price recorded"
        if action == "job":
            done["shown"] = (f"face {usdc(s['face_micro_usdc'])} USDC; "
                             f"{usdc(call(C['Broker'], 'networkHeld()'))} USDC held")
        elif action in ("seed", "swap"):
            ru, rl = reserves()
            done["shown"] = f"reserves {usdc(ru)} USDC and {lpt(rl)} LPT, spot {price(ru, rl)}"
        run.append(done)
        want = ({"refused": {k: v for k, v in refused_vec[s["step"]].items() if k != "step"}} if s["step"] in refused_vec
                else {"bought": {k: v for k, v in bought_vec[s["step"]].items() if k != "step"}} if s["step"] in bought_vec
                else {})
        got = {k: shifted(v, T0) if k == "refused" else v for k, v in done.items() if k in ("refused", "bought")}
        same = got == want and done["round"] == s["round"] and int(done["time"]) - T0 == t
        note = ""
        if s["step"] == "20" and "refused" in done:
            note = " (expected: spot is above TWAP + 1% until the average follows; the 26,378.676029 USDC rolls over)"
        say(f"  step {s['step']:>2}  round {done['round']}  t {int(done['time']) - T0:>6}  {action:<4}  "
            f"{outcome(done)}{note}  {'as section 1' if same else 'NOT AS SECTION 1'}")
        if not same:
            FAILURES.append(f"step {s['step']}: chain {got} at round {done['round']}, time {int(done['time']) - T0}; "
                            f"section 1 {want} at round {s['round']}, time {t}")

    say("\n4. After the walk")
    ru, rl = reserves()
    final = {"held_micro_usdc": call(C["Broker"], "networkHeld()"), "reserve_usdc_micro_usdc": ru,
             "reserve_lpt_wei": rl, "lpt_supply_wei": call(C["TestLPT"], "totalSupply()")}
    burned = sum(int(s["bought"]["burned_wei"]) for s in run if "bought" in s)
    say(f"  {sum('bought' in s for s in run)} buys burned {lpt(burned)} LPT; supply {lpt(supply_before)} -> "
        f"{lpt(final['lpt_supply_wei'])}; {usdc(final['held_micro_usdc'])} USDC still held")
    check("supply fell by exactly the sum of Bought.burned", supply_before - final["lpt_supply_wei"], burned)
    check("the LPT sent to the Buyback beforehand is there, unburned", call(C["TestLPT"], "balanceOf(address)", C["Buyback"]),
          DONATION)
    for k, v in final.items():
        check(f"{k} as section 1", str(v), WALK["expected"][k])

    say("\n5. The calculator")
    the_case = case_of("buyback-demo-run", supply_before, run, final)
    out = RUN / "buyback-case.json"
    out.write_text(json.dumps(ledgercase.document(VECTORS, [the_case]), indent=1) + "\n")
    say(f"  wrote {where(out)}: the shared examples with the run as case buyback-demo-run, {len(run)} steps "
        f"at the times the chain gave them")
    done = subprocess.run([str(ROOT / "calculator" / "check"), str(out)], capture_output=True, text=True)
    said = [ln for ln in (done.stdout + done.stderr).splitlines() if "buyback-demo-run" in ln or "cases passed" in ln]
    for line in said or (done.stdout + done.stderr).strip().splitlines()[-3:]:
        say(f"  ./calculator/check: {line[:160]}{' ...' if len(line) > 160 else ''}")
    check("the calculator reproduces every figure of the run's case", done.returncode, 0)

    say("\n6. The keeper, given the Buyback's address")
    password = RUN / "keys" / "password"
    password.parent.mkdir(parents=True, exist_ok=True)
    password.write_text(os.urandom(16).hex())
    keeper_key = keystore("Keeper", password)
    held, supply = call(C["Broker"], "networkHeld()"), call(C["TestLPT"], "totalSupply()")
    code, said = keeper_once(keeper_key, password)
    for line in said.splitlines():
        say(f"  lp2-keeper: {line}")
    check("after round 9's buy, the keeper logs ALREADY_BOUGHT as expected and sends no buy",
          ("buy refused ALREADY_BOUGHT, as expected" in said, "buy at block" in said, code), (True, False, 3))
    check("held and supply are unchanged", (call(C["Broker"], "networkHeld()"), call(C["TestLPT"], "totalSupply()")),
          (held, supply))
    to_round(10, 39_600)
    say(f"  round {current_round()} starts at time 39,600, an interval after the walk's last buy (step 22)")
    code, said = keeper_once(keeper_key, password)
    for line in said.splitlines():
        say(f"  lp2-keeper: {line}")
    sent = re.search(r"buy at block (\d+)", said)
    check("in round 10 the keeper's call would succeed, so it sends the buy", (bool(sent), code), (True, 3))
    if sent:
        block = hex(int(sent.group(1)))
        found = rpc("eth_getLogs", [{"fromBlock": block, "toBlock": block, "address": C["Buyback"], "topics": [topic(BOUGHT)]}])
        words = [int(found[0]["data"][2 + 64 * i:66 + 64 * i], 16) for i in range(6)] if len(found) == 1 else [0] * 6
        say(f"  its buy: {usdc(words[1])} USDC for {lpt(words[2])} LPT, burned")
        check("its Bought event matches the chain: held and supply fell by its usdcIn and burned",
              (held - call(C["Broker"], "networkHeld()"), supply - call(C["TestLPT"], "totalSupply()")),
              (words[1], words[3]))

    parameters(password)
    return finish(started, "section 1's walk ran on the chain, every figure equal to the calculator's")


def lp2_params(who: str, password: Path, *words: str) -> tuple[int, str]:
    """Runs lp2-params as `who` (its keystore), or reading only when None."""
    argv = [str(RUN / "bin" / "lp2-params"), "--rpc-url", RPC, "--parameters-address", C["Parameters"],
            "--round-clock-address", C["RoundClock"]]
    if who:
        argv += ["--keystore-file", str(RUN / "keys" / who / who), "--keystore-password-file", str(password)]
    done = subprocess.run(argv + list(words), capture_output=True, text=True, timeout=120)
    out = (done.stdout + done.stderr).strip()
    for line in out.splitlines():
        say(f"  {who or 'anyone'}$ lp2-params {' '.join(words)}: {line}" if line == out.splitlines()[0]
            else f"      {line}")
    return done.returncode, out


def parameters(password: Path) -> None:
    """S-10 R-25 on the chain: G, the governance stand-in and Parameters'
    authority, announces and withdraws through lp2-params; every other
    caller and every value outside D-47's ranges is refused. The Buyback
    reads group 9 from Parameters, so a change to it is what a later
    buy would read from the round it counts from."""
    now = current_round()
    say(f"\n7. Parameters, as G (S-10 R-25), in round {now}")
    keystore("G", password)
    pool = C["TestPool"]
    code, out = lp2_params("G", password, "announce", "emission", "900000", "10000", "90000")
    check("G's emission change counts from the current round + 7",
          (code, out.split(" (block")[0]), (0, f"announced: emission 900000/10000/90000, counts from round {now + 7}"))
    code, out = lp2_params("G", password, "announce", "buyback-values", pool, "7200", "50000", "2")
    check("G's buyback values change counts from the current round + 7",
          (code, f"counts from round {now + 7}" in out), (0, True))
    refusals = [
        ("Keeper", ("announce", "fee", "600000", "400000"), "NOT_AUTHORIZED"),
        ("G", ("announce", "0", "100"), "NOT_ADJUSTABLE"),
        ("G", ("announce", "schedule", "0", "1680"), "SCHEDULE_RAISED"),
        ("G", ("announce", "schedule", "841", "840"), "VALUE_OUT_OF_RANGE"),
        ("G", ("announce", "buyback-values", pool, "1799", "10000", "1"), "VALUE_OUT_OF_RANGE"),
        ("G", ("announce", "fee", "600000", "300000"), "IDENTITY_NOT_WHOLE"),
        ("G", ("announce", "buyback-values", C["Buyback"], "3600", "10000", "1"), "POOL_INVALID"),
        ("Keeper", ("withdraw", "buyback-values"), "NOT_AUTHORIZED"),
        ("G", ("withdraw", "unbonding"), "NOTHING_PENDING"),
    ]
    for who, said, want in refusals:
        if who == "Keeper" and not (RUN / "keys" / "Keeper").is_dir():
            keystore("Keeper", password)
        code, out = lp2_params(who, password, *said)
        check(f"{who}'s {' '.join(said[:2])} is refused {want}, and nothing is sent",
              (code, out.startswith(f"refused: {want} at block")), (3, True))
    want = [int(pool, 16), 7200, 50000, 2]
    check("the buyback values change is pending, unchanged by the refusals",
          words(C["Parameters"], "pending(uint256)", 9), [64, now + 7, 4] + want)
    code, out = lp2_params("G", password, "withdraw", "buyback-values")
    check("G withdraws it", (code, out.startswith("withdrawn: buyback-values")), (0, True))
    lp2_params(None, password, "show", "buyback-values", str(now + 7))
    deployed = [int(pool, 16)] + [int(PARAMS[k]) for k in ("buyback_twap_seconds", "buyback_max_slippage_ppm",
                                                          "buyback_every_rounds")]
    check(f"a withdrawn change never counts: round {now + 7}'s buyback values are the deployment's",
          words(C["Parameters"], "valuesAt(uint256,uint256)", 9, now + 7), [32, 4] + deployed)
    check(f"the Buyback reads the same for round {now + 7}", words(C["Parameters"], "buybackValuesAt(uint256)", now + 7),
          [int(pool, 16), int(PARAMS["buyback_burn_ppm"])] + deployed[1:])
    lp2_params(None, password, "show", "emission", str(now + 7))


def lp2_stake(who: str, password: Path, *words: str) -> tuple[int, str]:
    """Runs lp2-stake as `who` (its keystore, made on first use)."""
    if not (RUN / "keys" / who).is_dir():
        keystore(who, password)
    argv = [str(RUN / "bin" / "lp2-stake"), "--rpc-url", RPC, "--keystore-file", str(RUN / "keys" / who / who),
            "--keystore-password-file", str(password), "--stake-ledger-address", C["StakeLedger"],
            "--claims-address", C["Claims"], "--registry-address", C["Registry"], "--lpt-address", C["TestLPT"]]
    done = subprocess.run(argv + list(words), capture_output=True, text=True, timeout=120)
    out = (done.stdout + done.stderr).strip()
    shown = " ".join(NAMES.get(w.lower(), w) if w.startswith("0x") else w for w in words)
    say(f"  {who}$ lp2-stake {shown}: {out.splitlines()[-1] if out else ''}")
    return done.returncode, out


def later() -> int:
    """A walk time a minute after the newest block: actions after the walk
    are sent at the chain's own pace, and a round is started after them."""
    return int(rpc("eth_getBlockByNumber", ["latest", False])["timestamp"], 16) - T0 + 60

