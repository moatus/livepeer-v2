"""demo/public-run stage-4 and demo/public-run serve: the stage 4 system on a
chain the run does not control, and the services that keep it running.

stage-4 deploys stage 4's contracts to the chain at LP2_RPC_URL by stage 4's
own deployment function (stage 3's contracts with the test pool, Parameters
naming the Buyback as the buyback executor, then the Buyback), with the
deployer's keystore and the authority's given by the environment: G, the
authority, is Parameters' one authority. The deployer seeds the pool and
grants the faucet account its share of genesis test LPT. The team's node N,
payer P and validator V then do what the stage 3 run has them do, and G
announces one parameter change. In real time: N serves one paid job, so the
network share holds USDC; the keeper, given Parameters' address, pokes the
Buyback and, once the pool's average price is an interval old, buys test LPT
with the share and burns it; and the change is read back in the round before
it counts and in the round it counts from. Every figure is read from the
chain and printed beside the calculator's: the payment, stake and each
round's shares as a ledger case, as the stage 3 run does; the buys as a
buyback case; the change as a parameters case. The run then hands over: it
writes services.json in the state directory, and the same in its manifest,
with what the standing services need and no key of the deployer's or G's.

serve starts the standing services from services.json and the role keys in
the state directory alone: N's node with its model, the keeper and the
scripted validator V. It never reads the deployer's or G's keystore. It runs
until it is stopped, and exits when one of its programs does, naming it.

The path sends no anvil_* or evm_* call. Environment, as demo/public.py's,
and for stage-4 also:
  LP2_AUTHORITY_KEYSTORE       G's keystore file, outside the repository
  LP2_AUTHORITY_PASSWORD_FILE  its password file, outside the repository
serve reads LP2_STATE_DIR, LP2_DEMO_PORT_BASE and, if set, LP2_RPC_URL (by
default the URL the run used, kept in the state directory).

Exit 0 every figure equal (serve: stopped); 1 a figure differed, a behaviour
was missing or a service exited; 2 the run could not run or was refused.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time

import common
import public
import stage3
import buyback as bb
import ledgercase
from common import NotRun, Failed, say, check, call, words, money, lpt, C, ADDR, NAMES, MANIFEST

TEMPLATE = "public-arbitrum-sepolia-stage-4.json"
ROLES = ("N", "P", "V", "Keeper", "Faucet")
SERVICES = "services.json"
# The keeper in the run and the services: Parameters' address, so it buys and
# pokes at the executor in force, polled every 5 s (a round is 50 blocks).
SYNC = "Sync(uint112,uint112)"
OBSERVED = "Observed(address,uint256,uint256)"
FEE_SETTLED = "FeeSettled(uint256,address,address,bytes32,uint256,uint256,uint256,address)"
AUTHORITY: list[str] = []
KEEPER_TX_GAS = 200_000   # lp2-params' signing flags: G's keystore and password file


def keeper_options() -> tuple[str, ...]:
    return ("--parameters-address", C["Parameters"], "--poll", "5s")


def use_template(doc: dict, chain_id: int) -> None:
    """What runs is what the template says: its node, model, price and LPT,
    and the shared examples it names."""
    common.MANIFEST.update(json.loads(json.dumps(doc)))
    common.MANIFEST["chain"] = dict(common.MANIFEST["chain"], chain_id=chain_id)
    vectors = json.loads((common.ROOT / doc["vectors"]).read_text())
    for d in (common.VECTORS, common.CASES, common.PARAMS):
        d.clear()
    common.VECTORS.update(vectors)
    common.CASES.update({c["id"]: c for c in vectors["cases"]})
    common.PARAMS.update(vectors["parameters"])


def authority() -> str:
    """G's address, from the keystore the environment names. Checked before
    anything is made or sent: the run does not start without it."""
    keystore = public.outside_repo("LP2_AUTHORITY_KEYSTORE", True)
    password = public.outside_repo("LP2_AUTHORITY_PASSWORD_FILE", True)
    got = subprocess.run(["cast", "wallet", "address", "--keystore", str(keystore), "--password-file", str(password)],
                         capture_output=True, text=True)
    if got.returncode != 0:
        raise NotRun("the authority's keystore could not be opened with its password file. Nothing was sent")
    AUTHORITY[:] = ["--keystore-file", str(keystore), "--keystore-password-file", str(password)]
    return got.stdout.strip()


def lp2_params(*argv: str) -> str:
    """lp2-params as G: it asks by a call first and sends only what would
    succeed; a refusal (exit 3) fails the run."""
    return common.sh([str(public.STATE_DIR / "bin" / "lp2-params"), "--rpc-url", common.RPC,
                      "--parameters-address", C["Parameters"], "--round-clock-address", C["RoundClock"],
                      *AUTHORITY, *argv])


def deploy() -> None:
    bb.RUN = common.RUN  # the Buyback demo writes its addresses beside its run: here, the state directory
    public.deploy(bb.deploy_stage4)
    for n in ("TestPool", "Buyback", "Broker", "Parameters"):
        NAMES[C[n].lower()] = n


def keeper_gas() -> int:
    """How many more transactions the keeper can pay for: its balance over
    200,000 gas, about what a buy uses, at the chain's gas price. It sends
    two a round, so its gas money runs out unless someone tops it up."""
    balance, price = common.rpc_batch([("eth_getBalance", [ADDR["Keeper"], "latest"]), ("eth_gasPrice", [])])
    return int(balance["result"], 16) // (KEEPER_TX_GAS * max(1, int(price["result"], 16)))


def executor_in_force() -> str:
    return f"0x{words(C['Parameters'], 'valuesAt(uint256,uint256)', 10, stage3.current_round())[2]:040x}"


def unbonding_now(at_round: int) -> list[int]:
    """The values in force for the template's group in `at_round`."""
    w = words(C["Parameters"], "valuesAt(uint256,uint256)", int(public.TEMPLATE_DOC["parameter_change"]["group"]), at_round)
    return w[2:2 + w[1]]


# --- the run ------------------------------------------------------------------------

def main() -> int:
    started = time.time()
    g = authority()
    public.ROLES = ROLES
    chain_id = public.open_state(stage=4)
    state = public.STATE
    if state.get("authority", g).lower() != g.lower():
        raise NotRun(f"this state directory's authority is {state['authority']}, not {g}. Nothing was sent")
    state["authority"] = g
    ADDR["G"], NAMES[g.lower()] = g, "G"
    public.save()
    public.TEMPLATE_DOC.update(state["done"]["deploy"]["template"] if "deploy" in state["done"] else
                               json.loads((common.ROOT / "deployments" / TEMPLATE).read_text()))
    doc = public.TEMPLATE_DOC
    use_template(doc, chain_id)
    say("Public run, stage 4: the buyback buys and burns test LPT, and an announced parameter change counts seven "
        "rounds later, in real time; then the deployment is handed over to services without the deployer's key")
    say(f"  chain id {chain_id}, {public.ALLOWED_CHAINS[chain_id]}; state directory outside the repository")
    say(f"  deployer {state['deployer']}; authority G {g}; N {ADDR['N']}, P {ADDR['P']}, V {ADDR['V']}, "
        f"keeper {ADDR['Keeper']}, faucet {ADDR['Faucet']}")

    if all(n in state["done"] for n in ("buy", "change before", "change from")):
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

    say("\n2. Deploying stage 4 (round 1 starts with the round clock)")
    deploy()
    say("  contracts: " + ", ".join(f"{k} {v}" for k, v in C.items()))
    say(f"  from block {common.FROM_BLOCK}; treasury {ADDR['Treasury']} (no key); Parameters' authority G {g}")
    gas = int(os.environ.get("LP2_ROLE_GAS_WEI", "3000000000000000"))
    for who in ROLES:
        public.step(f"gas money to {who}", lambda who=who: public.ksend("deployer", ADDR[who], value=gas) and None)
    say(f"  the deployer gave N, P, V, the keeper and the faucet {gas / 10**18:g} of the chain's native token each, for gas")

    say("\n3. Round 1: the pool is seeded, the faucet granted its share, N and V registered and bonded, P funds, "
        "and G announces a change")
    pool = MANIFEST["pool"]
    usdc_seed, lpt_seed = int(pool["seed_usdc_micro_usdc"]), int(pool["seed_lpt_wei"])
    public.step("seed the pool: its USDC", lambda: public.ksend(
        "deployer", C["MockUSDC"], "mint(address,uint256)", C["TestPool"], usdc_seed) and None)
    public.step("seed the pool: its LPT", lambda: public.ksend(
        "deployer", C["TestLPT"], "transfer(address,uint256)", C["TestPool"], lpt_seed) and None)

    def seed():
        r = public.ksend("deployer", C["TestPool"], "mint(address)", state["deployer"])
        return {"tx": r["transactionHash"], "block": int(r["blockNumber"], 16), "index": int(r["transactionIndex"], 16)}
    public.step("seed the pool", seed)
    ru, rl = bb.reserves()
    say(f"  the pool holds {bb.usdc(ru)} USDC and {lpt(rl)} LPT: {bb.price(ru, rl)} USDC per LPT")
    faucet = int(doc["faucet"]["lpt_wei"])
    for who, wei in (("N", public.N_BOND), ("V", public.V_BOND), ("Faucet", faucet)):
        public.step(f"grant LPT to {who}", lambda who=who, wei=wei: public.tx_step(
            {"action": "grant_lpt", "to": who, "amount_wei": wei}, "deployer", C["TestLPT"],
            "transfer(address,uint256)", ADDR[who], wei) and None)
    say(f"  the faucet {ADDR['Faucet']} holds {lpt(call(C['TestLPT'], 'balanceOf(address)', ADDR['Faucet']))} test LPT; "
        "nothing hands it out yet")
    for who in ("N", "V"):
        public.step(f"register {who}", lambda who=who: public.tx_step({"action": "register", "node": who}, who,
                                                                      C["Registry"], "register()") and None)
    check("N is registered, with no stake", (call(C["Registry"], "isRegistered(address)", ADDR["N"]),
                                             call(C["StakeLedger"], "stakeAt(address,uint256)", ADDR["N"], 1)), (1, 0))
    public.step("bond N", lambda: stage3.bond("N", "N", public.N_BOND))
    public.step("bond V", lambda: stage3.bond("V", "V", public.V_BOND))
    say(f"  N bonds {lpt(public.N_BOND)} and V {lpt(public.V_BOND)} to themselves; each counts from the next round")
    public.fund_p(face)
    public.step("observe", lambda: common.observe("P", "N") and None, sends=False)
    change = public.step("announce", announce)
    pc = doc["parameter_change"]
    say(f"  G announced {pc['name']} {' '.join(pc['values'])} in round {change['round']}: it counts from round "
        f"{change['counts_from']}")

    public.start_programs(node_bin, model, keeper=keeper_options())
    public.paid_job()
    wait_both(change)
    return reconcile(chain_id, started)


def announce() -> dict:
    """G announces the template's change. The round it counts from is the
    one the transaction's Announced event gives, which lp2-params prints."""
    pc = public.TEMPLATE_DOC["parameter_change"]
    out = lp2_params("announce", pc["name"], *pc["values"])
    found = re.search(r"counts from round (\d+) \(block (\d+), tx (0x[0-9a-fA-F]{64})\)", out)
    if not found:
        raise Failed(f"lp2-params did not report its announcement: {out[-300:]}")
    common.SENT.append(found.group(3))
    r = common.receipt(found.group(3), 300)
    place = (int(r["blockNumber"], 16), int(r["transactionIndex"], 16))
    return {"tx": found.group(3), "round": common.round_batch([place])[0], "counts_from": int(found.group(1))}


def wait_both(change: dict) -> None:
    """In real time, until both are seen: the change read back in the round
    before it counts and in the round it counts from, and the keeper's first
    buy. Either may come first."""
    cf = change["counts_from"]
    twap = int(common.PARAMS["buyback_twap_seconds"])
    say(f"\n6. Rounds to {cf}: the change counts from round {cf}; the keeper buys once the pool's average price is "
        f"{twap:,} s old and the network share holds USDC")
    last, filled_at = None, None
    while True:
        now = stage3.current_round()
        if now >= cf - 1 and "change before" not in public.STATE["done"]:
            read_change("change before", cf - 1, now, cf)
            last = now
        if now >= cf and "change from" not in public.STATE["done"]:
            read_change("change from", cf, now, cf)
            last = now
        if "buy" not in public.STATE["done"]:
            if call(C["Buyback"], "lastBuyRound()"):
                seen_buy()
            else:
                held = call(C["Broker"], "networkHeld()")
                _, time0, _, _ = words(C["Buyback"], "observations()")
                chain_time = int(common.rpc("eth_getBlockByNumber", ["latest", False])["timestamp"], 16)
                fills = time0 + twap if time0 else None
                if now != last:
                    window = (f"the average is {chain_time - time0:,} s old, an interval at chain time {fills:,}"
                              if time0 else "the Buyback has no observation yet")
                    say(f"  {time.strftime('%H:%M:%S')} round {now}: {money(held)} micro-USDC held; {window}")
                # The keeper buys within a few seconds of a buy becoming
                # possible; 15 minutes past that, something is wrong.
                if fills and held and chain_time >= fills:
                    filled_at = filled_at or time.time()
                    if time.time() - filled_at > 900:
                        raise Failed("the price window has filled and the share holds USDC, but the keeper has not "
                                     "bought for 15 minutes; see logs/keeper.log in the state directory")
        elif now != last:
            say(f"  {time.strftime('%H:%M:%S')} round {now}")
        if now != last and keeper_gas() < 1:
            raise Failed(f"the keeper's gas money has run out, so no round can start: send its address {ADDR['Keeper']} "
                         "more of the chain's native token, then start the run again (it resumes)")
        last = now
        if all(n in public.STATE["done"] for n in ("buy", "change before", "change from")):
            return
        if common.PROCS["keeper"].poll() is not None:
            raise Failed("the keeper exited; see logs/keeper.log in the state directory")
        time.sleep(5)


def read_change(name: str, at_round: int, now: int, cf: int) -> None:
    values = unbonding_now(at_round)
    public.step(name, lambda: {"round": now, "at_round": at_round, "values": values}, sends=False)
    late = "" if now == at_round else f" (read late, in round {now}: a round that has started keeps its values)"
    pc = public.TEMPLATE_DOC["parameter_change"]
    say(f"  {time.strftime('%H:%M:%S')} round {now}: {pc['name']} in round {at_round} is "
        f"{', '.join(map(str, values))}, {'not yet the change' if at_round < cf else 'the change, in force'}{late}")


def seen_buy() -> None:
    found = common.logs(C["Buyback"], bb.BOUGHT)
    if not found:
        raise Failed("the Buyback's lastBuyRound is set, but it emitted no Bought event")
    b = bb.bought_of({"logs": found[:1]}, C["Buyback"])
    sender = common.rpc("eth_getTransactionByHash", [found[0]["transactionHash"]])["from"]
    public.step("buy", lambda: {"tx": found[0]["transactionHash"], "block": int(found[0]["blockNumber"], 16),
                                "from": sender, **b}, sends=False)
    say(f"  {time.strftime('%H:%M:%S')} the keeper's buy: {bb.usdc(b['usdc_in_micro_usdc'])} USDC of the network share "
        f"bought {lpt(b['lpt_out_wei'])} test LPT, and {lpt(b['burned_wei'])} was burned")
    check("the buy was sent by the keeper", common.name(sender) if sender.lower() in NAMES else sender, "Keeper")


# --- reconciling --------------------------------------------------------------------

def side(label: str, chain, calc, fmt=str) -> None:
    """One figure read from the chain beside the calculator's, in columns
    wide enough for the supply of LPT."""
    mark = "equal" if chain == calc else "DIFFERS"
    say(f"  {label:<46}{fmt(chain):>30}{fmt(calc):>30}  {mark}")
    if chain != calc:
        common.FAILURES.append(f"{label}: chain {chain}, calculator {calc}")


def reconcile(chain_id: int, started: float) -> int:
    state = public.STATE
    last = max(state["done"]["change from"]["round"], int(common.round_batch(
        [(state["done"]["buy"]["block"], None)])[0])) - 1
    if "keeper" in common.PROCS:
        give_up = time.time() + 300
        while not stage3.shares_minted(last):
            if common.PROCS["keeper"].poll() is not None or time.time() > give_up:
                raise Failed(f"the keeper did not mint round {last}'s shares; see logs/keeper.log in the state directory")
            time.sleep(5)
    for p in ("keeper", "validators", "signer-P"):
        common.stop(p)
    say("\n7. Every figure, read from the chain, beside the calculator's")
    check(f"rounds 1 to {last} whose shares the keeper did not mint",
          [r for r in range(1, last + 1) if not stage3.shares_minted(r)], [])
    check(f"rounds 3 to {last} without V as their one validator",
          [r for r in range(3, last + 1) if stage3.members_at(r) != ["V"]], [])
    buys = common.logs(C["Buyback"], bb.BOUGHT)
    figures = [bb.bought_of({"logs": [lg]}, C["Buyback"]) for lg in buys]
    seeded = int(MANIFEST["pool"]["seed_usdc_micro_usdc"])
    moved = {"spent_micro_usdc": sum(int(f["usdc_in_micro_usdc"]) for f in figures),
             "burned_wei": sum(int(f["burned_wei"]) for f in figures), "seeded_micro_usdc": seeded}
    m = stage3.members()
    ordered, views, calc = common.reconcile("The payment, the stake and each round's shares", stage3.keeper_steps(), m,
                                            moved)
    pay = stage3.validator_pay_run_cases()
    count = differ = 0
    for c in pay:
        n, d = stage3.compare(c["id"], c["expected"], stage3.calculate("validator-pay", c["inputs"]))
        count, differ = count + n, differ + d
    say(f"  the validator pay of rounds 1 to {len(pay)} (members {', '.join(sorted({x for v in m.values() for x in v})) or 'none'}): "
        f"{count} figures, {'all equal' if not differ else f'{differ} DIFFER'}")
    common.write_case("public-run", ordered, views, m)
    minted = int(calc["lpt_minted_wei"])
    bcase = buyback_case(buys, minted, views["block"])
    pcase = parameters_case()
    write_cases([bcase, pcase])
    handover(chain_id)
    public.spending()
    return common.finish(started, "the network share bought and burned test LPT, and G's change counted seven rounds "
                                  "after its announcement; every figure read from the chain equals the calculator's. "
                                  "Start the standing services with: demo/public-run serve")


def buyback_case(buys: list[dict], minted: int, block: int) -> dict:
    """The buys as a buyback case: the pool's seed, every standalone poke,
    every job and every buy, at the rounds and times the chain gave them,
    from a supply of the genesis supply and all the LPT the calculator says
    was minted (burning and minting commute). The chain's figures are the
    Bought events, then what is held, the pool's reserves and the supply."""
    say("\n  The buyback, as a buyback case")
    seed = public.STATE["done"]["seed the pool"]
    pool = MANIFEST["pool"]
    buy_txs = {lg["transactionHash"] for lg in buys}
    pokes = [lg for lg in common.logs(C["Buyback"], OBSERVED, block) if lg["transactionHash"] not in buy_txs]
    jobs = common.logs(C["Broker"], FEE_SETTLED, block)
    syncs = common.logs(C["TestPool"], SYNC, block)
    check("the pool's reserves changed only at its seed and the buys: nobody else traded on it",
          len(syncs), 1 + len(buys))
    rows = [((seed["block"], seed["index"]), "seed", {"usdc_micro_usdc": pool["seed_usdc_micro_usdc"],
                                                     "lpt_wei": pool["seed_lpt_wei"]}, None)]
    rows += [((int(lg["blockNumber"], 16), int(lg["transactionIndex"], 16)), "poke", {}, None) for lg in pokes]
    rows += [((int(lg["blockNumber"], 16), int(lg["transactionIndex"], 16)), "job",
              {"face_micro_usdc": str(int(lg["data"][2 + 64:2 + 128], 16))}, None) for lg in jobs]
    rows += [((int(lg["blockNumber"], 16), int(lg["transactionIndex"], 16)), "buy", {}, lg) for lg in buys]
    rows.sort(key=lambda r: r[0])
    rounds = common.round_batch([r[0] for r in rows])
    blocks = sorted({r[0][0] for r in rows})
    times = dict(zip(blocks, (int(a["result"]["timestamp"], 16) for a in common.rpc_batch(
        [("eth_getBlockByNumber", [hex(b), False]) for b in blocks]))))
    steps, bought = [], []
    for i, ((b, _), action, fields, lg) in enumerate(rows, 1):
        steps.append({"step": str(i), "round": str(rounds[i - 1]), "time": str(times[b]), "action": action, **fields})
        if lg:
            bought.append({"step": str(i), **bb.bought_of({"logs": [lg]}, C["Buyback"])})
    supply = int(MANIFEST["lpt"]["genesis_supply_wei"]) + minted
    r0, r1, _ = words(C["TestPool"], "getReserves()", block=block)
    ru, rl = (r0, r1) if bb.usdc_is_0() else (r1, r0)
    chain = {"bought": bought, "refused": [], "held_micro_usdc": str(call(C["Broker"], "networkHeld()", block=block)),
             "reserve_usdc_micro_usdc": str(ru), "reserve_lpt_wei": str(rl),
             "lpt_supply_wei": str(call(C["TestLPT"], "totalSupply()", block=block))}
    the_case = {"id": "public-run-buyback", "kind": "buyback",
                "inputs": {"lpt_supply_wei": str(supply), "steps": steps}, "expected": chain}
    calc = stage3.calculate("buyback", the_case["inputs"])
    say(f"  {len(steps)} steps: the seed, {len(pokes)} poke(s), {len(jobs)} job(s) and {len(buys)} buy(s), "
        f"supply before any burn {lpt(supply)} LPT (genesis and every round's mint)")
    for got, want in zip(bought, calc.get("bought", [])):
        for key, label, fmt in (("usdc_in_micro_usdc", "USDC spent (micro-USDC)", money),
                                ("lpt_out_wei", "LPT bought", lpt), ("burned_wei", "LPT burned", lpt),
                                ("max_in_micro_usdc", "the bound (micro-USDC)", money), ("twap_x112", "TWAP (UQ112x112)", str)):
            side(f"buy at step {got['step']}: {label}", int(got[key]), int(want[key]), fmt)
    if len(bought) != len(calc.get("bought", [])):
        common.FAILURES.append(f"the chain shows {len(bought)} buys, the calculator {len(calc.get('bought', []))}")
    for key, label, fmt in (("held_micro_usdc", "network share held after (micro-USDC)", money),
                            ("reserve_usdc_micro_usdc", "the pool's USDC (micro-USDC)", money),
                            ("reserve_lpt_wei", "the pool's LPT", lpt), ("lpt_supply_wei", "LPT supply after the burn", lpt)):
        side(label, int(chain[key]), int(calc[key]), fmt)
    n, d = stage3.compare("the buyback case", chain, calc)
    say(f"  every figure of the buyback case: {n}, {'all equal' if not d else f'{d} DIFFER'}")
    return the_case


def parameters_case() -> dict:
    """G's change as a parameters case: the announcement, and the values in
    force in the round before it counts and in the round it counts from,
    each read in the round it was read in. The values are read again now: a
    round that has started keeps its values, so they must not have moved."""
    say("\n  The parameter change, as a parameters case")
    done = public.STATE["done"]
    pc = public.TEMPLATE_DOC["parameter_change"]
    a, before, after = done["announce"], done["change before"], done["change from"]
    group = str(pc["group"])
    steps = [{"step": "1", "round": str(a["round"]), "action": "announce", "caller": "G", "group": group,
              "values": list(pc["values"])}]
    observed = []
    for i, r in enumerate((before, after), 2):
        now = unbonding_now(r["at_round"])
        check(f"{pc['name']} in round {r['at_round']}, read again, is what was read in round {r['round']}",
              now, r["values"])
        steps.append({"step": str(i), "round": str(r["round"]), "action": "observe", "group": group,
                      "at_round": str(r["at_round"])})
        observed.append({"step": str(i), "values": [str(v) for v in now]})
    chain = {"announced": [{"step": "1", "counts_from": str(a["counts_from"])}], "observed": observed, "refused": []}
    the_case = {"id": "public-run-parameters", "kind": "parameters", "inputs": {"authority": "G", "steps": steps},
                "expected": chain}
    calc = stage3.calculate("parameters", the_case["inputs"])
    side(f"announced in round {a['round']}: counts from round", a["counts_from"],
                int(calc["announced"][0]["counts_from"]) if calc.get("announced") else None)
    for got, want, r in zip(observed, calc.get("observed", []), (before, after)):
        side(f"{pc['name']} in round {r['at_round']} (read in round {r['round']})",
                    " ".join(got["values"]), " ".join(want["values"]))
    n, d = stage3.compare("the parameters case", chain, calc)
    say(f"  every figure of the parameters case: {n}, {'all equal' if not d else f'{d} DIFFER'}")
    return the_case


def write_cases(cases: list[dict]) -> None:
    out = public.STATE_DIR / "stage-4-cases.json"
    out.write_text(json.dumps(ledgercase.document(common.VECTORS, cases), indent=1) + "\n")
    done = subprocess.run([str(common.ROOT / "calculator" / "check"), str(out)], capture_output=True, text=True)
    say(f"\n  wrote stage-4-cases.json: the buyback and parameters cases, as the chain shows them")
    said = [ln for ln in (done.stdout + done.stderr).splitlines() if any(c["id"] in ln for c in cases)]
    for line in said or (done.stdout + done.stderr).strip().splitlines()[-2:]:
        say(f"  ./calculator/check stage-4-cases.json: {line[:150]}{' ...' if len(line) > 150 else ''}")
    check("the calculator reproduces the run's buyback and parameters cases", done.returncode, 0)


# --- the handover -------------------------------------------------------------------

def services_doc(chain_id: int) -> dict:
    """What the standing services need, and nothing of the deployer's or G's:
    the chain, the deployment's first block, every contract, the executor in
    force, and the role key files, relative to the state directory, which
    holds them with their password file and the RPC URL."""
    state = public.STATE
    return {"stage": 4, "chain_id": chain_id, "from_block": common.FROM_BLOCK, "contracts": dict(C),
            "executor": executor_in_force(), "authority": state["authority"],
            "accounts": {k: ADDR[k] for k in ROLES},
            "keys": {who: f"keys/{who}/{who}" for who in ROLES}, "password_file": "pw", "rpc_url_file": "rpc-url",
            "services": ["N's node, the unmodified orchestrator, with its model", "lp2-keeper, given Parameters' address",
                         "lp2-validators for V"]}


def handover(chain_id: int) -> None:
    """Hand the deployment to the standing services: services.json in the
    state directory, and the same in the run's manifest. The programs the
    run started stop with it; demo/public-run serve starts the services."""
    say("\n8. The handover")
    svc = services_doc(chain_id)
    out = public.STATE_DIR / SERVICES
    out.write_text(json.dumps({**svc, "template": public.TEMPLATE_DOC}, indent=1) + "\n")
    out.chmod(0o600)
    done = public.STATE["done"]
    pc = public.TEMPLATE_DOC["parameter_change"]
    more = {"authority": svc["authority"],
            "faucet": {"account": ADDR["Faucet"], "granted_lpt_wei": public.TEMPLATE_DOC["faucet"]["lpt_wei"]},
            "parameter_change": {"group": pc["group"], "name": pc["name"], "values": pc["values"],
                                 "announced_in_round": done["announce"]["round"],
                                 "counts_from": done["announce"]["counts_from"], "tx": done["announce"]["tx"]},
            "first_buy": {k: done["buy"][k] for k in ("tx", "usdc_in_micro_usdc", "lpt_out_wei", "burned_wei")},
            "services": {k: v for k, v in svc.items() if k not in ("stage", "chain_id", "from_block", "contracts",
                                                                   "authority", "accounts")}}
    public.write_manifest(chain_id, more)
    check("services.json names no key of the deployer's or G's",
          [k for k in (public.STATE["deployer_keystore"], public.STATE["deployer_password_file"], *AUTHORITY)
           if k in out.read_text()], [])
    say(f"  wrote services.json: chain {chain_id}, first block {svc['from_block']}, {len(svc['contracts'])} contracts, "
        f"executor in force {svc['executor']}, and the key files of {', '.join(svc['keys'])}")
    say("  the run's programs stop with it; until the services start, a round just lasts longer")


# --- the services -------------------------------------------------------------------

def serve() -> int:
    """The standing services, from services.json and the role keys alone."""
    state_dir = public.outside_repo("LP2_STATE_DIR", False)
    if not (state_dir / SERVICES).is_file():
        raise NotRun(f"{state_dir} holds no {SERVICES}: no stage 4 run has handed over to services there "
                     "(demo/public-run stage-4)")
    public.STATE_DIR = state_dir
    public.lock()
    common.RUN = state_dir
    common.LOG = open(state_dir / "serve.log", "a")
    say(f"\n=== demo/public-run serve, {time.strftime('%Y-%m-%d %H:%M:%S')}")
    svc = json.loads((state_dir / SERVICES).read_text())
    common.RPC_FILE = public.private(state_dir / svc["rpc_url_file"])
    url = os.environ.get("LP2_RPC_URL") or common.RPC_FILE.read_text().strip()
    common.RPC = url
    try:
        chain_id = int(common.rpc("eth_chainId", []), 16)
    except (OSError, ValueError, common.urllib.error.URLError) as e:
        raise NotRun(f"the chain did not answer: {e}")
    if chain_id != svc["chain_id"]:
        raise NotRun(f"the services are for chain {svc['chain_id']}; the URL given is chain {chain_id}")
    if url != common.RPC_FILE.read_text().strip():
        tmp = public.private(state_dir / "rpc-url.tmp")
        tmp.write_text(url)
        tmp.chmod(0o600)
        tmp.replace(common.RPC_FILE)
    use_template(svc["template"], chain_id)
    C.update(svc["contracts"])
    common.FROM_BLOCK = svc["from_block"]
    public.STATE["done"] = {"deploy": {"contracts": svc["contracts"]}}
    public.check_deployment()
    for who, addr in svc["accounts"].items():
        ADDR[who], NAMES[addr.lower()] = addr, who
    for who in ("N", "V", "Keeper"):
        stage3.KEYS[who] = public.private(state_dir / svc["keys"][who])
        if not stage3.KEYS[who].is_file():
            raise NotRun(f"the state directory holds no key file for {who} ({svc['keys'][who]})")
    say("The standing services of the stage 4 deployment: N's node, the keeper and the scripted validator V")
    say(f"  chain id {chain_id}; Controller {C['Controller']}; executor in force {executor_in_force()}; "
        f"the keeper {ADDR['Keeper']} can pay for about {keeper_gas()} more transactions, two a round")

    common.preflight(["orch", "orch_metrics", "model"])
    node_bin = common.fetch_node()
    model = common.fetch_model()
    common.build_ours()
    # V votes the full score for N in the round after the services start,
    # which leaves every score as it was: a validator call each start.
    first = stage3.current_round()
    script = state_dir / "serve-validators.script"
    script.write_text("# Written by demo/public-run serve: V votes the full score for N in the round after the\n"
                      f"# services started (round {first}), which leaves every score as it was.\n{first + 1} V N 1000000\n")
    public.start_programs(node_bin, model, keeper=keeper_options(), script=script, payer=False)
    say(f"  {time.strftime('%H:%M:%S')} round {first}; the services run until stopped (Ctrl-C or SIGTERM). "
        "Logs are in logs/ in the state directory")
    last = first
    try:
        while True:
            for name, p in common.PROCS.items():
                if p.poll() is not None:
                    raise Failed(f"{name} exited with status {p.returncode}; see logs/{name}.log in the state directory")
            now = stage3.current_round()
            if now != last:
                left = keeper_gas()
                low = (f"; the keeper can pay for about {left} more transactions: send its address {ADDR['Keeper']} "
                       "more of the chain's native token" if left < 100 else "")
                say(f"  {time.strftime('%H:%M:%S')} round {now} started{low}")
                last = now
            time.sleep(5)
    except KeyboardInterrupt:
        say("  the services are stopped")
        return 0
