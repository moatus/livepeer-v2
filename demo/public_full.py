"""demo/public-run full and demo/public-run serve: the full system on a
chain the run does not control, and the services that keep it running.

full deploys the full system's contracts to the chain at LP2_RPC_URL by the full system's
own deployment function (the rewards demo's contracts with the test pool, Parameters
naming the Buyback as the buyback executor, then the Buyback), with the
deployer's keystore and the authority's given by the environment: G, the
authority, is Parameters' one authority. The deployer seeds the pool and
grants the faucet account its share of genesis test LPT. The team's node N,
payer P and validator V then do what the rewards run has them do, and G
announces one parameter change. In real time: N serves one paid job, so the
network share holds USDC; the keeper, given Parameters' address, pokes the
Buyback and, once the pool's average price is an interval old, buys test LPT
with the share and burns it; and the change is read back in the round before
it counts and in the round it counts from. Every figure is read from the
chain and printed beside the calculator's: the payment, stake and each
round's shares as a ledger case, as the rewards run does; the buys as a
buyback case; the change as a parameters case. Only when every figure is
equal does the run hand over: it writes deployment.json, the file operators
give their node container, and services.json, in the state directory, and
the same in its manifest, with what the standing services need and no key of
the deployer's or G's, and labels.json, naming the run's accounts for the
explorer, P marked simulated. A run that fails removes both deployment.json
and services.json, so serve refuses it.

serve starts the standing services from services.json and the role keys in
the state directory alone: N's node with the light responder, the keeper, the
scripted validator V, lp2-sender and lp2-faucet, and beside them lp2-explorer
with no node, its index in index/ in the state directory, naming accounts
from labels.json. It never reads the deployer's or G's keystore. It runs
until it is stopped, and exits when one of its programs does, naming it. On
a local chain, once the explorer has caught up, serve checks N's, V's and
P's figures in it against the chain's views at the explorer's block and
against the run's ledger case, once.

The path sends no anvil_* or evm_* call. Environment, as demo/public.py's,
and for full also:
  LP2_AUTHORITY_KEYSTORE       G's keystore file, outside the repository
  LP2_AUTHORITY_PASSWORD_FILE  its password file, outside the repository
  LP2_UPGRADER_KEYSTORE        U's keystore file, the upgrade key, outside the
                               repository; full reads only its address
  LP2_UPGRADER_PASSWORD_FILE   its password file, outside the repository
  LP2_PAUSER_KEYSTORE          S's keystore file, the pause key, outside the
                               repository; full reads only its address
  LP2_PAUSER_PASSWORD_FILE     its password file, outside the repository
  LP2_SENDER_URL               the sender's public URL, which deployment.json
                               names; on a local chain, by default, its
                               loopback address. Required on a public chain
serve reads LP2_STATE_DIR, LP2_DEMO_PORT_BASE and, if set, LP2_RPC_URL (by
default the URL the run used, kept in the state directory), LP2_SENDER_URL
(a new one is written into deployment.json), LP2_SENDER_LISTEN and
LP2_NODE_URL.

Exit 0 every figure equal (serve: stopped); 1 a figure differed (serve: an
explorer figure, said when it is stopped), a behaviour was missing or a
service exited; 2 the run could not run or was refused.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path

import common
import public
import rewards
import buyback as bb
import ledgercase
from common import NotRun, Failed, say, check, call, words, money, lpt, C, ADDR, NAMES, PORT, MANIFEST

TEMPLATE = "public-arbitrum-sepolia-full.json"
ROLES = ("N", "P", "V", "Keeper", "Faucet")
SERVICES = "services.json"
OPERATORS = "deployment.json"   # the deployment file operators give their node container
LABELS = "labels.json"          # the names the explorer beside serve shows
# What the operator container reads from it (programs/container/README.md):
# each a string or a number, and nothing else.
# Upgrades, so that an operator can see a stop (R-27).
OPERATOR_CONTRACTS = ("Controller", "Parameters", "StakeLedger", "Claims", "TestLPT", "MockUSDC", "Upgrades")
OPERATOR_NODE = ("job_price_usd", "price_unit", "wei_per_usd", "ticket_face_value", "ticket_win_prob")
# A round that has not started for this long means the keeper cannot start
# it: three rounds on Arbitrum Sepolia, thirty on a local chain.
STALL_SECONDS = 1800
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


def key_address(var: str, what: str) -> str:
    """The address of the keystore LP2_<var>_KEYSTORE names, opened with
    its password file, both outside the repository. The run reads the
    address only and sends nothing with the key."""
    keystore = public.outside_repo(f"LP2_{var}_KEYSTORE", True)
    password = public.outside_repo(f"LP2_{var}_PASSWORD_FILE", True)
    got = subprocess.run(["cast", "wallet", "address", "--keystore", str(keystore), "--password-file", str(password)],
                         capture_output=True, text=True)
    if got.returncode != 0:
        raise NotRun(f"{what}'s keystore could not be opened with its password file. Nothing was sent")
    return got.stdout.strip()


def lp2_params(*argv: str) -> str:
    """lp2-params as G: it asks by a call first and sends only what would
    succeed; a refusal (exit 3) fails the run."""
    return common.sh([str(public.STATE_DIR / "bin" / "lp2-params"), "--rpc-url", common.RPC,
                      "--parameters-address", C["Parameters"], "--round-clock-address", C["RoundClock"],
                      *AUTHORITY, *argv])


def deploy() -> None:
    bb.RUN = common.RUN  # the Buyback demo writes its addresses beside its run: here, the state directory
    public.deploy(bb.deploy_full)
    for n in ("TestPool", "Buyback", "Broker", "Parameters"):
        NAMES[C[n].lower()] = n


def keeper_gas() -> int:
    """How many more transactions the keeper can pay for: its balance over
    200,000 gas, about what a buy uses, at the chain's gas price. It sends
    two a round, so its gas money runs out unless someone tops it up."""
    balance, price = common.rpc_batch([("eth_getBalance", [ADDR["Keeper"], "latest"]), ("eth_gasPrice", [])])
    return int(balance["result"], 16) // (KEEPER_TX_GAS * max(1, int(price["result"], 16)))


def executor_in_force() -> str:
    return f"0x{words(C['Parameters'], 'valuesAt(uint256,uint256)', 10, rewards.current_round())[2]:040x}"


def unbonding_now(at_round: int) -> list[int]:
    """The values in force for the template's group in `at_round`."""
    w = words(C["Parameters"], "valuesAt(uint256,uint256)", int(public.TEMPLATE_DOC["parameter_change"]["group"]), at_round)
    return w[2:2 + w[1]]


# --- the run ------------------------------------------------------------------------

def main() -> int:
    started = time.time()
    g = authority()
    # The upgrade key U and the pause key S (R-27): named in Upgrades at
    # deployment; demo/public-run upgrade, stop and lift send with them.
    u, s = key_address("UPGRADER", "The upgrade key"), key_address("PAUSER", "The pause key")
    if len({g.lower(), u.lower(), s.lower()}) != 3:
        raise NotRun("the authority, the upgrade key and the pause key must be three different keys. Nothing was sent")
    public.ROLES = ROLES
    chain_id = public.open_state(deployment="full")
    sender_url(chain_id)   # the handover's deployment.json names it: checked before anything is sent
    state = public.STATE
    if state["deployer"].lower() in (u.lower(), s.lower()):
        raise NotRun("the upgrade key or the pause key is the deployer's key. Nothing was sent")
    for field, got in (("authority", g), ("upgrader", u), ("pauser", s)):
        if state.get(field, got).lower() != got.lower():
            raise NotRun(f"this state directory's {field} is {state[field]}, not {got}. Nothing was sent")
        state[field] = got
    for who, addr in (("G", g), ("Upgrader", u), ("Pauser", s)):
        ADDR[who], NAMES[addr.lower()] = addr, who
    public.save()
    public.TEMPLATE_DOC.update(state["done"]["deploy"]["template"] if "deploy" in state["done"] else
                               json.loads((common.ROOT / "deployments" / TEMPLATE).read_text()))
    doc = public.TEMPLATE_DOC
    use_template(doc, chain_id)
    say("Public run, full system: the buyback buys and burns test LPT, and an announced parameter change counts seven "
        "rounds later, in real time; then the deployment is handed over to services without the deployer's key")
    say(f"  chain id {chain_id}, {public.ALLOWED_CHAINS[chain_id]}; state directory outside the repository")
    say(f"  deployer {state['deployer']}; authority G {g}; upgrade key U {u}; pause key S {s}; N {ADDR['N']}, "
        f"P {ADDR['P']}, V {ADDR['V']}, keeper {ADDR['Keeper']}, faucet {ADDR['Faucet']}")

    if all(n in state["done"] for n in ("buy", "change before", "change from")):
        deploy()
        # The services' own sends are not steps of this run: lp2-sender's
        # funding and payments, and lp2-faucet's grants (it sends nothing
        # during the run).
        if len(common.settlements()) > 1 or int(common.rpc("eth_getTransactionCount", [ADDR["Faucet"], "latest"]), 16):
            raise NotRun("the standing services have paid or granted nodes since the handover: lp2-sender's funding "
                         "and lp2-faucet's grants are not steps of this run, so its figures cannot be read again from "
                         "the chain. They were reconciled at the handover; see public-run.log in the state directory. "
                         "Nothing was sent")
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

    say("\n2. Deploying full system (round 1 starts with the round clock)")
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
        "lp2-faucet hands it out under demo/public-run serve")
    for who in ("N", "V"):
        public.step(f"register {who}", lambda who=who: public.tx_step({"action": "register", "node": who}, who,
                                                                      C["Registry"], "register()") and None)
    check("N is registered, with no stake", (call(C["Registry"], "isRegistered(address)", ADDR["N"]),
                                             call(C["StakeLedger"], "stakeAt(address,uint256)", ADDR["N"], 1)), (1, 0))
    public.step("bond N", lambda: rewards.bond("N", "N", public.N_BOND))
    public.step("bond V", lambda: rewards.bond("V", "V", public.V_BOND))
    say(f"  N bonds {lpt(public.N_BOND)} and V {lpt(public.V_BOND)} to themselves; each counts from the next round")
    public.fund_p(face)
    public.step("observe", lambda: common.observe("P", "N") and None, sends=False)
    change = public.step("announce", announce)
    pc = doc["parameter_change"]
    say(f"  G announced {pc['name']} {' '.join(pc['values'])} in round {change['round']}: it counts from round "
        f"{change['counts_from']}")

    public.start_programs(node_bin, model, keeper=keeper_options(), script=validator_script())
    public.paid_job()
    wait_both(change)
    return reconcile(chain_id, started)


def validator_script() -> Path:
    """V's script: the public run's, which opts V in in round 2, unless V
    has opted in already, as when a run is started again. The validator
    program does a line whose round has passed at once, and the contract
    takes a second opt-in, so the script would send it again."""
    opted = [lg for lg in common.logs(C["Scores"], "OptedIn(address,uint256)")
             if int(lg["topics"][1], 16) == int(ADDR["V"], 16)]
    if not opted:
        return public.SCRIPT
    script = public.STATE_DIR / "validators-resumed.script"
    script.write_text(f"# Written by demo/public-run full: V opted in at block {int(opted[0]['blockNumber'], 16)}, "
                      "so it has nothing left to send.\n")
    return script


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
    seen_round, round_at = None, time.time()
    while True:
        now = rewards.current_round()
        if now != seen_round:
            seen_round, round_at = now, time.time()
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
        # Each poll, not each round: a keeper out of gas starts no round.
        left = keeper_gas()
        if left < 1:
            raise Failed(f"the keeper's gas money has run out, so no round can start: send its address {ADDR['Keeper']} "
                         "more of the chain's native token, then start the run again (it resumes)")
        if time.time() - round_at > STALL_SECONDS:
            raise Failed(f"no round has started for {STALL_SECONDS // 60} minutes (round {now}): the keeper is not "
                         f"starting rounds; it can pay for about {left} more transactions. See logs/keeper.log in the "
                         "state directory, then start the run again (it resumes)")
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
        while not rewards.shares_minted(last):
            if common.PROCS["keeper"].poll() is not None or time.time() > give_up:
                raise Failed(f"the keeper did not mint round {last}'s shares; see logs/keeper.log in the state directory")
            time.sleep(5)
    for p in ("keeper", "validators", "signer-P"):
        common.stop(p)
    say("\n7. Every figure, read from the chain, beside the calculator's")
    check(f"rounds 1 to {last} whose shares the keeper did not mint",
          [r for r in range(1, last + 1) if not rewards.shares_minted(r)], [])
    check(f"rounds 3 to {last} without V as their one validator",
          [r for r in range(3, last + 1) if rewards.members_at(r) != ["V"]], [])
    buys = common.logs(C["Buyback"], bb.BOUGHT)
    figures = [bb.bought_of({"logs": [lg]}, C["Buyback"]) for lg in buys]
    seeded = int(MANIFEST["pool"]["seed_usdc_micro_usdc"])
    moved = {"spent_micro_usdc": sum(int(f["usdc_in_micro_usdc"]) for f in figures),
             "burned_wei": sum(int(f["burned_wei"]) for f in figures), "seeded_micro_usdc": seeded}
    m = rewards.members()
    ordered, views, calc = common.reconcile("The payment, the stake and each round's shares", rewards.keeper_steps(), m,
                                            moved)
    pay = rewards.validator_pay_run_cases()
    count = differ = 0
    for c in pay:
        n, d = rewards.compare(c["id"], c["expected"], rewards.calculate("validator-pay", c["inputs"]))
        count, differ = count + n, differ + d
    say(f"  the validator pay of rounds 1 to {len(pay)} (members {', '.join(sorted({x for v in m.values() for x in v})) or 'none'}): "
        f"{count} figures, {'all equal' if not differ else f'{differ} DIFFER'}")
    common.write_case("public-run", ordered, views, m)
    minted = int(calc["lpt_minted_wei"])
    bcase = buyback_case(buys, minted, views["block"])
    pcase = parameters_case()
    write_cases([bcase, pcase])
    handover(chain_id)   # only when every figure is equal; otherwise it withdraws any earlier handover
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
    calc = rewards.calculate("buyback", the_case["inputs"])
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
    n, d = rewards.compare("the buyback case", chain, calc)
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
    calc = rewards.calculate("parameters", the_case["inputs"])
    side(f"announced in round {a['round']}: counts from round", a["counts_from"],
                int(calc["announced"][0]["counts_from"]) if calc.get("announced") else None)
    for got, want, r in zip(observed, calc.get("observed", []), (before, after)):
        side(f"{pc['name']} in round {r['at_round']} (read in round {r['round']})",
                    " ".join(got["values"]), " ".join(want["values"]))
    n, d = rewards.compare("the parameters case", chain, calc)
    say(f"  every figure of the parameters case: {n}, {'all equal' if not d else f'{d} DIFFER'}")
    return the_case


def write_cases(cases: list[dict]) -> None:
    out = public.STATE_DIR / "full-cases.json"
    out.write_text(json.dumps(ledgercase.document(common.VECTORS, cases), indent=1) + "\n")
    done = subprocess.run([str(common.ROOT / "calculator" / "check"), str(out)], capture_output=True, text=True)
    say(f"\n  wrote full-cases.json: the buyback and parameters cases, as the chain shows them")
    said = [ln for ln in (done.stdout + done.stderr).splitlines() if any(c["id"] in ln for c in cases)]
    for line in said or (done.stdout + done.stderr).strip().splitlines()[-2:]:
        say(f"  ./calculator/check full-cases.json: {line[:150]}{' ...' if len(line) > 150 else ''}")
    check("the calculator reproduces the run's buyback and parameters cases", done.returncode, 0)


# --- the handover -------------------------------------------------------------------

def services_doc(chain_id: int) -> dict:
    """What the standing services need, and nothing of the deployer's or G's:
    the chain, the deployment's first block, every contract, the executor in
    force, and the role key files, relative to the state directory, which
    holds them with their password file and the RPC URL."""
    state = public.STATE
    return {"deployment": "full", "chain_id": chain_id, "from_block": common.FROM_BLOCK, "contracts": dict(C),
            "executor": executor_in_force(), "authority": state["authority"],
            "accounts": {k: ADDR[k] for k in ROLES},
            "keys": {who: f"keys/{who}/{who}" for who in ROLES}, "password_file": "pw", "rpc_url_file": "rpc-url",
            "services": ["N's node, the unmodified orchestrator, serving lp2-responder",
                         "lp2-keeper, given Parameters' address", "lp2-validators for V",
                         "lp2-sender, paying as P each node that joins, with P's signer on loopback",
                         "lp2-faucet, granting each registered node test LPT once, from the deployment's first block"]}


def labels_doc() -> dict:
    """The explorer's labels file: each of the run's accounts named by its
    role, and P marked simulated, since the sender's paid requests stand in
    for users' demand."""
    names = {"N": "N, the team's node", "P": "P, the sender's payer", "V": "V, the team's validator",
             "Keeper": "the keeper", "Faucet": "the faucet"}
    return {"scenario": "the sender pays each node that joins one request a round",
            "accounts": {ADDR[k]: {"name": names[k], "simulated": k == "P"} for k in ROLES}}


# The pinned node's destination policy (destination.Public in
# github.com/livepeer/node, the version programs/go.mod pins), which
# lp2-sender applies to every node URL: an address is public when it is in
# none of these IPv4 blocks, or in 2000::/3 and none of these IPv6 blocks,
# an IPv4-mapped IPv6 address read as its IPv4 address.
DENIED_V4 = ("0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8",
             "169.254.0.0/16", "172.16.0.0/12", "192.0.0.0/24", "192.0.2.0/24",
             "192.88.99.0/24", "192.168.0.0/16", "198.18.0.0/15", "198.51.100.0/24",
             "203.0.113.0/24", "224.0.0.0/4", "240.0.0.0/4")
DENIED_V6 = ("::/128", "::1/128", "fc00::/7", "fe80::/10", "ff00::/8", "2001::/23", "2001:db8::/32", "2002::/16")
PUBLIC_V6 = "2000::/3"


def public_address(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if ip.version == 4:
        return not any(ip in ipaddress.ip_network(p) for p in DENIED_V4)
    return ip in ipaddress.ip_network(PUBLIC_V6) and not any(ip in ipaddress.ip_network(p) for p in DENIED_V6)


def resolve(host: str) -> list[str]:
    """Every address `host` resolves to here."""
    return sorted({a[4][0] for a in socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)})


def host_name(host: str) -> str:
    """A URL's host as the name it is: no case, no root dot, an IP address
    in its one form."""
    host = host.lower().removesuffix(".")
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        return host


DEFAULT_PORTS = {"http": 80, "https": 443}


def endpoint(url: str) -> tuple[str, str, int | None]:
    """A URL's endpoint: its scheme, its host as a name (host_name) and its
    port, the scheme's default when none is written."""
    try:
        u = urllib.parse.urlsplit(url)
        port = u.port
    except ValueError:
        return (url, "", None)
    scheme = u.scheme.lower()
    return (scheme, host_name(u.hostname or ""), DEFAULT_PORTS.get(scheme) if port is None else port)


def url_problem(url: str, public_chain: bool, rpc: str | None = None) -> str | None:
    """Why `url` cannot be the sender's published URL, or None: the one
    check of it, at the run's start, at the handover and in serve. It is
    published, so it names the sender and nothing else: an origin, an http
    or https scheme, a host (a name, or an address with no zone) and an
    optional port, with no user, no path but "/", no query and no fragment,
    written as both Python and Go read it. Nodes join at its /join. It is not the
    RPC endpoint (`rpc`, by default the one in use): not its scheme, host
    and port. The sender may share the RPC's host at another port or
    scheme. On a public chain it resolves only to addresses public by the
    policy lp2-sender applies to node URLs, so operators' nodes can reach
    it."""
    if not url or len(url) > 512 or any(c <= " " or c > "~" for c in url) or "?" in url or "#" in url:
        return "is empty, longer than 512 bytes, or holds a space, a character that is not printable ASCII, a query or a fragment"
    try:
        u = urllib.parse.urlsplit(url)
        u.port
    except ValueError:
        return "is not a URL"
    if u.scheme not in ("http", "https") or not u.hostname:
        return "is not an http:// or https:// URL"
    if u.username is not None or u.password is not None:
        return "carries a user or password, and it is published"
    if "%" in u.hostname:
        return "has a % in its host, such as an IPv6 zone, which names an interface of the machine reading it"
    # Written exactly as read: the node's Go client reads the host and
    # port as written, so a spelling Python reads otherwise (a port with a
    # leading zero) is refused, not published (test_demo, SPELLINGS).
    written = f"[{u.hostname}]" if ":" in u.hostname else u.hostname
    if u.netloc.lower().removesuffix(":") != written + ("" if u.port is None else f":{u.port}"):
        return "spells its host or port in a form URL readers may read differently"
    host = host_name(u.hostname)
    if not re.fullmatch(r"[a-z0-9_-]+(\.[a-z0-9_-]+)*", host):
        try:
            ipaddress.ip_address(host)
        except ValueError:
            return "does not name a host by a name or an address"
    rpc = common.RPC if rpc is None else rpc
    if rpc and endpoint(rpc) == endpoint(url):
        return "is the RPC endpoint (its scheme, host and port), which is not published"
    if u.path not in ("", "/"):
        return "has a path: the sender is published at an origin, a scheme, a host and a port (demo/PUBLIC.md)"
    if not public_chain:
        return None
    try:
        addrs = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            addrs = [ipaddress.ip_address(a.split("%")[0]) for a in resolve(host)]
        except (OSError, ValueError) as e:
            return f"names {host}, which does not resolve here ({e}), so it cannot be checked as public"
    private = [str(a) for a in addrs if not public_address(a)]
    if private or not addrs:
        return (f"names a loopback or private host, which operators cannot reach ({host} resolves to "
                f"{', '.join(private) or 'no address'})")
    return None


def sender_listen() -> str:
    return os.environ.get("LP2_SENDER_LISTEN", f"127.0.0.1:{SENDER}")


def sender_url(chain_id: int) -> str:
    """The sender's URL as operators reach it, the setting the services run
    with: LP2_SENDER_URL, or on a local chain by default its loopback
    address."""
    url = os.environ.get("LP2_SENDER_URL", "")
    if not url:
        if chain_id not in public.LOCAL_CHAINS:
            raise NotRun("LP2_SENDER_URL is not set: on a public chain, the deployment file operators are given names "
                         "the sender's public URL (demo/PUBLIC.md). Nothing was sent")
        url = "http://127.0.0.1:" + sender_listen().rsplit(":", 1)[1]
    problem = url_problem(url, chain_id not in public.LOCAL_CHAINS)
    if problem:
        raise NotRun(f"LP2_SENDER_URL {problem}. Nothing was sent")
    return url


def operator_doc(chain_id: int, url: str) -> dict:
    """The deployment file operators give their node container, in its
    fields (programs/container/README.md): public addresses and settings
    only, and the sender's public URL."""
    price = MANIFEST["price"]
    return {"testnet": f"public-run-{chain_id}", "chain_id": chain_id, "first_block": common.FROM_BLOCK,
            "contracts": {k: C[k] for k in OPERATOR_CONTRACTS},
            "node": {"job_price_usd": price["job_price_usd"], "price_unit": price["unit"],
                     "wei_per_usd": price["wei_per_usd"], "ticket_face_value": str(common.ticket_face()),
                     "ticket_win_prob": price["ticket_win_prob"]},
            "sender_url": url}


def operator_problems(doc: dict, public_chain: bool) -> list[str]:
    """What is wrong with an operators' deployment file: a field the
    container reads that is missing or not a string or number, a field it
    does not read, an address that is not one, a sender URL that cannot be
    published (url_problem), or an RPC URL, path or key file in another
    field.
    The handover and serve check the whole file with it before writing it."""
    problems = []

    def fields(where: str, got: dict, want: tuple[str, ...], objects: tuple[str, ...] = ()) -> None:
        for k in want:
            v = got.get(k)
            if not isinstance(v, (str, int)) or isinstance(v, bool) or v == "":
                problems.append(f"{where}{k} is missing or not a string or number")
        problems.extend(f"{where}{k} is not a field the container reads" for k in got if k not in want + objects)
    fields("", doc, ("testnet", "chain_id", "first_block", "sender_url"), ("contracts", "node"))
    for k, want in (("contracts", OPERATOR_CONTRACTS), ("node", OPERATOR_NODE)):
        if isinstance(doc.get(k), dict):
            fields(f"{k}.", doc[k], want)
        else:
            problems.append(f"{k} is missing or not an object")
    for k, v in (doc.get("contracts") or {}).items():
        if isinstance(v, str) and not re.fullmatch(r"0x[0-9a-fA-F]{40}", v):
            problems.append(f"contracts.{k} is not an address")
    for k in ("chain_id", "first_block"):
        if k in doc and (not isinstance(doc[k], int) or isinstance(doc[k], bool) or doc[k] < 0):
            problems.append(f"{k} is not a whole number")
    if doc.get("sender_url") not in (None, ""):
        v = doc["sender_url"]
        problem = url_problem(v, public_chain) if isinstance(v, str) else "is not a URL"
        if problem:
            problems.append(f"sender_url {problem}")
    # The sender URL is an origin, checked above, so it holds no path for
    # the RPC URL, a path or a key file to hide in. It may start as an RPC
    # URL with no path does, on the RPC's host at another port.
    text = json.dumps({k: v for k, v in doc.items() if k != "sender_url"})
    state_dir = str(public.STATE_DIR) if public.STATE_DIR != Path() else ""
    if any(s and s in text for s in (common.RPC, state_dir, "keys/")):
        problems.append("it names an RPC URL, path or key file")
    return problems


def write_public(path: Path, text: str, mode: int) -> None:
    """Replace `path` whole, so a reader never sees half of it."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    tmp.chmod(mode)
    tmp.replace(path)


def withdraw_handover() -> None:
    """A run with a check failed hands nothing over, and removes what an
    earlier run handed over, so serve refuses the state directory."""
    gone = [n for n in (SERVICES, OPERATORS) if (public.STATE_DIR / n).is_file()]
    for n in (SERVICES, OPERATORS):
        (public.STATE_DIR / n).unlink(missing_ok=True)
    say(f"  no handover: {len(common.FAILURES)} check(s) failed, so demo/public-run serve will not start from this "
        f"state directory{' (removed ' + ' and '.join(gone) + ')' if gone else ''}")


def handover(chain_id: int) -> None:
    """Hand the deployment to the standing services, only when every figure
    is equal: deployment.json, the file operators give their node container,
    and services.json, in the state directory, and the same in the run's
    manifest. serve starts only from a services.json, so a run that failed
    removes any it handed over before. The programs the run started stop
    with it; demo/public-run serve starts the services."""
    say("\n8. The handover")
    if common.FAILURES:
        return withdraw_handover()
    svc = services_doc(chain_id)
    text = json.dumps({**svc, "template": public.TEMPLATE_DOC}, indent=1) + "\n"
    operators = operator_doc(chain_id, sender_url(chain_id))
    op_text = json.dumps(operators, indent=1) + "\n"
    owner = [k for k in (public.STATE["deployer_keystore"], public.STATE["deployer_password_file"], *AUTHORITY)
             if k in text or k in op_text]
    check("services.json and deployment.json name no key of the deployer's or G's", owner, [])
    check("deployment.json holds the fields the operator container reads, a sender URL it can publish, and no RPC "
          "URL, path or key file", operator_problems(operators, chain_id not in public.LOCAL_CHAINS), [])
    if common.FAILURES:
        return withdraw_handover()
    done = public.STATE["done"]
    pc = public.TEMPLATE_DOC["parameter_change"]
    more = {"authority": svc["authority"],
            "faucet": {"account": ADDR["Faucet"], "granted_lpt_wei": public.TEMPLATE_DOC["faucet"]["lpt_wei"]},
            "parameter_change": {"group": pc["group"], "name": pc["name"], "values": pc["values"],
                                 "announced_in_round": done["announce"]["round"],
                                 "counts_from": done["announce"]["counts_from"], "tx": done["announce"]["tx"]},
            "first_buy": {k: done["buy"][k] for k in ("tx", "usdc_in_micro_usdc", "lpt_out_wei", "burned_wei")},
            "services": {k: v for k, v in svc.items() if k not in ("deployment", "chain_id", "from_block", "contracts",
                                                                   "authority", "accounts")}}
    public.write_manifest(chain_id, more)
    write_public(public.STATE_DIR / OPERATORS, op_text, 0o644)
    write_public(public.STATE_DIR / LABELS, json.dumps(labels_doc(), indent=1) + "\n", 0o644)
    write_public(public.STATE_DIR / SERVICES, text, 0o600)   # last: serve starts from it
    say(f"  wrote deployment.json for operators: chain {chain_id}, first block {operators['first_block']}, "
        f"{len(operators['contracts'])} contracts, the node's price and tickets, and the sender at "
        f"{operators['sender_url']}. Publish it (demo/PUBLIC.md)")
    say(f"  wrote services.json: chain {chain_id}, first block {svc['from_block']}, {len(svc['contracts'])} contracts, "
        f"executor in force {svc['executor']}, and the key files of {', '.join(svc['keys'])}")
    say("  wrote labels.json: the explorer beside serve names the run's accounts, P marked simulated")
    say("  the run's programs stop with it; until the services start, a round just lasts longer")


# --- the services -------------------------------------------------------------------

RESPONDER = common.BASE + 20   # N's light responder, under serve
SENDER = common.BASE + 24      # lp2-sender's joins, by default on loopback


def start_sender(listen: str, local: bool) -> None:
    """lp2-sender, holding P's key beside P's signer on loopback: it pays
    each node that joins, once a round. Its list is in the state directory.
    Node URLs on private addresses are allowed on a local chain only."""
    common.start("sender", [str(public.STATE_DIR / "bin" / "lp2-sender"), "--rpc-url", common.RPC,
                            "--keystore-file", str(rewards.KEYS["P"]),
                            "--keystore-password-file", str(public.STATE_DIR / "pw"),
                            "--controller-address", C["Controller"], "--usdc-address", C["MockUSDC"],
                            "--signer-url", f"http://127.0.0.1:{PORT['signer_p']}",
                            "--nodes", str(public.STATE_DIR / "sender-nodes.txt"), "--listen", listen,
                            *(["--allow-private-urls"] if local else [])])
    public.note_holders()
    at = "http://127.0.0.1:" + listen.rsplit(":", 1)[1]
    end = time.time() + 60
    while common.post(f"{at}/join", {})[0] != 400:
        if time.time() > end or common.PROCS["sender"].poll() is not None:
            raise Failed("lp2-sender did not answer joins; see logs/sender.log in the state directory")
        time.sleep(1)
    say(f"  lp2-sender answers joins on {listen}, paying as P{' (private node URLs allowed: a local chain)' if local else ''}")


def start_faucet(state_dir: Path, from_block: int) -> None:
    """lp2-faucet, holding the faucet account's key: it grants each node
    registered from the deployment's first block test LPT once. Its journal
    is in faucet/ in the state directory, kept across restarts, so a node is
    never granted twice."""
    journal = state_dir / "faucet"
    journal.mkdir(mode=0o700, exist_ok=True)
    common.start("faucet", [str(state_dir / "bin" / "lp2-faucet"), "--rpc-url", common.RPC,
                            "--keystore-file", str(rewards.KEYS["Faucet"]),
                            "--keystore-password-file", str(state_dir / "pw"),
                            "--controller-address", C["Controller"], "--lpt-address", C["TestLPT"],
                            "--state-dir", str(journal), "--from-block", str(from_block)])
    public.note_holders()
    say(f"  lp2-faucet grants each node registered from block {from_block} test LPT once; the faucet "
        f"{ADDR['Faucet']} holds {lpt(call(C['TestLPT'], 'balanceOf(address)', ADDR['Faucet']))}")


def publish_sender(state_dir: Path, chain_id: int) -> None:
    """deployment.json names the sender's URL the services run with: a new
    LP2_SENDER_URL is written into it, to be published again. The whole
    file is checked first, as the handover checks it, and a file that fails
    is not written: the one in the state directory stays as it was."""
    path = state_dir / OPERATORS
    doc = json.loads(path.read_text())
    was = doc.get("sender_url")
    url = os.environ.get("LP2_SENDER_URL") or was
    doc["sender_url"] = url
    problems = operator_problems(doc, chain_id not in public.LOCAL_CHAINS)
    if problems:
        raise NotRun(f"{OPERATORS} {'with LP2_SENDER_URL ' if url != was else ''}is not one operators can be "
                     f"given: {'; '.join(problems)}. The one in the state directory is kept as it was")
    if url != was:
        write_public(path, json.dumps(doc, indent=1) + "\n", 0o644)
        say(f"  deployment.json now names the sender at {url}: publish it again (demo/PUBLIC.md)")
    say(f"  deployment.json, the file operators are given, names the sender at {url}")


def join_n(listen: str, chain_id: int) -> None:
    """The team's node joins the sender as any node does: a join signed with
    N's key. Its URL is LP2_NODE_URL, the URL at which N's orchestrator is
    reached; on a local chain, by default, its loopback address."""
    node_url = os.environ.get("LP2_NODE_URL") or (
        f"http://127.0.0.1:{PORT['orch']}" if chain_id in public.LOCAL_CHAINS else "")
    if not node_url:
        say("  N does not join: on a public chain, set LP2_NODE_URL to the public URL of N's orchestrator")
        return
    end = time.time() + 60
    while True:
        # N's discovery lists its runner after its first health check;
        # until then the sender answers UNREACHABLE.
        issued = int(time.time())
        msg = f"lp2-join:v1:{chain_id}:{C['Registry'].lower()}:{ADDR['N'].lower()}:{node_url}:{issued}"
        sig = common.sh(["cast", "wallet", "sign", "--keystore", str(rewards.KEYS["N"]),
                         "--password-file", str(public.STATE_DIR / "pw"), msg])
        status, text = common.post("http://127.0.0.1:" + listen.rsplit(":", 1)[1] + "/join",
                                   {"node": ADDR["N"].lower(), "url": node_url, "issuedAt": issued, "signature": sig})
        if status != 422 or time.time() > end:
            break
        time.sleep(2)
    say(f"  N joins the sender at {node_url}: HTTP {status} {text.strip()[:120]}")
    if status != 200:
        raise Failed(f"N's join was refused: HTTP {status} {text.strip()[:200]}")


def start_explorer(state_dir: Path, from_block: int) -> None:
    """lp2-explorer beside the services, with no node, so / is the Network
    page: its index in index/ in the state directory, kept across
    restarts, and the accounts named by the labels.json the run wrote."""
    labels = state_dir / LABELS
    common.start("explorer", [str(state_dir / "bin" / "lp2-explorer"), "--rpc-url", common.RPC,
                              "--controller-address", C["Controller"], "--parameters-address", C["Parameters"],
                              "--stake-ledger-address", C["StakeLedger"], "--claims-address", C["Claims"],
                              "--usdc-address", C["MockUSDC"], "--lpt-address", C["TestLPT"],
                              "--first-block", str(from_block), "--index-dir", str(state_dir / "index"),
                              *(["--labels", str(labels)] if labels.is_file() else []),
                              "--listen", f"127.0.0.1:{PORT['explorer']}"])
    public.note_holders()
    say(f"  lp2-explorer on http://127.0.0.1:{PORT['explorer']}/, its index in index/ in the state directory"
        + ("" if labels.is_file() else f"; no {LABELS} in the state directory, so it names no account"))


def check_explorer(since: int) -> bool:
    """Once the explorer has caught up to block `since`: N's, V's and P's
    figures in it, all read at its block, against the chain's views at that
    block, and those the run fixed (the rounds of its ledger case, and its
    bonds) against the case. False while the explorer is catching up."""
    net = rewards.explorer_json("/network.json")
    if not net or not net["index"]["caught_up"] or (net["block"] or 0) < since:
        return False
    docs = rewards.explorer_docs(since)
    net, b = docs["/network.json"], docs["/network.json"]["block"]
    r = net["round"]
    say(f"\n  The explorer at block {b}, round {r}: N's, V's and P's figures against the chain's views at that block and "
        "the run's ledger case")
    missing = {p: d["unavailable"] for p, d in docs.items() if d["unavailable"]}
    check("explorer: no figure unavailable on any document", missing, {})
    if missing:
        return True
    case = next(c for c in json.loads((public.STATE_DIR / "ledger-case.json").read_text())["cases"] if c["id"] == "public-run")
    steps, expected = case["inputs"]["steps"], case["expected"]
    last = max(int(s["round"]) for s in steps)
    labels = json.loads((public.STATE_DIR / LABELS).read_text())
    check("explorer: the scenario the run's labels.json names", net["scenario"], labels["scenario"])
    nodes = rewards.by_address(docs["/orchestrators.json"]["orchestrators"], unknown=True)
    check(f"explorer: the validator set of round {r} is membersAt's at block {b}",
          sorted(NAMES.get(v["address"].lower(), v["address"]) for v in net["validators"]), rewards.members_at(r, block=b))
    chain_fees: dict[str, dict[int, int]] = {}
    for n in ("N", "V"):
        doc = docs[f"/orchestrators/{ADDR[n].lower()}.json"]
        fees = {x["round"]: int(x["fees_usdc_micro"]) for x in doc["fees_by_round"]}
        views = iter(common.calls([(C["StakeLedger"], "stakeAt(address,uint256)", (ADDR[n], r)),
                                   (C["Scores"], "scoreOf(address)", (ADDR[n],)),
                                   (C["Registry"], "isRegistered(address)", (ADDR[n],))] +
                                  [(C["Broker"], "feesOf(uint256,address)", (x, ADDR[n])) for x in range(1, r + 1)], b))
        stake, score, registered = next(views), next(views), next(views)
        chain_fees[n] = {x: v for x, v in zip(range(1, r + 1), views) if v}
        check(f"explorer: {n} registered, its stake this round and score now, and its fees in every round, as the chain "
              f"has them at block {b}",
              (n in nodes, int(nodes[n]["stake_wei"]) if n in nodes else None, int(doc["node"]["score_now"]), fees),
              (bool(registered), stake, score, chain_fees[n]))
        check(f"explorer: {n} named by the run's labels.json, not simulated", (doc["label"], doc["simulated"]),
              (labels["accounts"][ADDR[n]]["name"], False))
        rewards.beside(f"{n}: fees by round, rounds 1 to {last}", {x: v for x, v in fees.items() if x <= last},
                       {int(x): int(f[n]) for x, f in expected["fees_micro_usdc"].items() if n in f})
        rewards.beside(f"{n}: stake this round, the run's bonds to it",
                       int(doc["node"]["stake_wei"]),
                       sum(int(s["amount_wei"]) for s in steps if s["action"] == "bond" and s["node"] == n), lpt)
    apps = rewards.by_address(docs["/apps.json"]["apps"], unknown=True)
    check("explorer: the payers, P alone, named by the run's labels.json and marked simulated",
          {k: (x["label"], x["simulated"]) for k, x in apps.items()}, {"P": (labels["accounts"][ADDR["P"]]["name"], True)})
    doc = docs[f"/apps/{ADDR['P'].lower()}.json"]
    totals = iter(common.calls([(C["Broker"], "totalFees(uint256)", (x,)) for x in range(1, r + 1)], b))
    check(f"explorer: P's deposit and its pending release are earmarkOf's, and P's fees in every round, P the one payer, "
          f"totalFees's, at block {b}",
          ([int(doc["deposit_usdc_micro"]), int(doc["deposit_pending_usdc_micro"])],
           {x["round"]: int(x["fees_usdc_micro"]) for x in doc["fees_by_round"]}),
          (words(C["Broker"], "earmarkOf(address,address)", ADDR["P"], common.ZERO, block=b)[:2],
           {x: v for x, v in zip(range(1, r + 1), totals) if v}))
    of_p = lambda signature: [x for x in common.logs(C["Broker"], signature, b)  # noqa: E731
                              if int(x["topics"][1], 16) == int(ADDR["P"], 16)]
    funded = [(int(x["data"][2:66], 16), int(x["data"][66:130], 16)) for x in of_p("Funded(address,uint256,address,uint256)")]
    released = [int(x["data"][2:66], 16) for x in of_p("Released(address,address,uint256)")]
    check(f"explorer: P's funded total (each Funded's deposit and earmark) and released total (each Released's amount), "
          f"as the chain's logs have them through block {b}",
          (int(doc["funded_usdc_micro"]), int(doc["released_usdc_micro"])), (sum(d + e for d, e in funded), sum(released)))
    run_funds = [(int(s["deposit_micro_usdc"]), int(s.get("earmark_micro_usdc", 0))) for s in steps
                 if s["action"] == "fund" and s["payer"] == "P"]
    check("the chain's first Funded events of P, deposit and earmark, are the fund steps of the run's ledger case",
          funded[:len(run_funds)], run_funds)
    flows = ("fees_usdc_micro", "node_usdc_micro", "network_usdc_micro")
    check(f"explorer: P's fees by node are the nodes' feesOf over every round at block {b}, P the one payer",
          {NAMES.get(x["address"].lower(), x["address"]): int(x["fees_usdc_micro"]) for x in doc["by_node"]},
          {n: sum(f.values()) for n, f in chain_fees.items() if f})
    check("explorer: P's fees, node and network totals are the sums of its rows by round and by node",
          ([sum(int(x[k]) for x in doc["fees_by_round"]) for k in flows], [sum(int(x[k]) for x in doc["by_node"]) for k in flows]),
          ([int(doc[k]) for k in flows],) * 2)
    split: dict[int, list[int]] = {}
    for s in steps:
        if s["action"] == "redeem" and s["payer"] == "P":
            cut = rewards.calculate("settlement", {"gross_micro_usdc": s["face_micro_usdc"], "payee_registered": True})
            f = split.setdefault(int(s["round"]), [0, 0, 0])
            for i, k in enumerate(("recognized_micro_usdc", "node_micro_usdc", "network_micro_usdc")):
                f[i] += int(cut[k])
    rewards.beside(f"P: by round, rounds 1 to {last}, gross, node and network amounts (the case's redeem steps, split as "
                   "the calculator splits them)",
                   {x["round"]: [int(x[k]) for k in ("fees_usdc_micro", "node_usdc_micro", "network_usdc_micro")]
                    for x in doc["fees_by_round"] if x["round"] <= last}, split)
    page = urllib.request.urlopen(f"http://127.0.0.1:{PORT['explorer']}/apps/{ADDR['P']}", timeout=30).read().decode()
    check("explorer: P's page marks it simulated", '<span class="badge sim">simulated</span>' in page, True)
    return True


def serve() -> int:
    """The standing services, from services.json and the role keys alone."""
    state_dir = public.outside_repo("LP2_STATE_DIR", False)
    if not (state_dir / SERVICES).is_file() or not (state_dir / OPERATORS).is_file():
        raise NotRun(f"{state_dir} holds no {SERVICES} and {OPERATORS}: no full system run has passed and handed over to "
                     "services there (demo/public-run full)")
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
    for who in ("N", "P", "V", "Keeper", "Faucet"):
        rewards.KEYS[who] = public.private(state_dir / svc["keys"][who])
        if not rewards.KEYS[who].is_file():
            raise NotRun(f"the state directory holds no key file for {who} ({svc['keys'][who]})")
    say("The standing services of the full system deployment: N's node with the light responder, the keeper, the "
        "scripted validator V, lp2-sender with P's signer, and lp2-faucet")
    publish_sender(state_dir, chain_id)
    say(f"  chain id {chain_id}; Controller {C['Controller']}; executor in force {executor_in_force()}; "
        f"the keeper {ADDR['Keeper']} can pay for about {keeper_gas()} more transactions, two a round")

    PORT.update({"responder": RESPONDER, "sender": SENDER})
    listen = sender_listen()
    common.preflight(["orch", "orch_metrics", "signer_p", "signer_p_metrics", "responder", "explorer"] +
                     (["sender"] if listen == f"127.0.0.1:{SENDER}" else []), docker=False)
    node_bin = common.fetch_node()
    common.PROGRAMS.update({"lp2-sender": "./cmd/lp2-sender", "lp2-responder": "./cmd/lp2-responder",
                            "lp2-faucet": "./cmd/lp2-faucet"})
    common.build_ours()
    # V votes the full score for N in the round after the services start,
    # which leaves every score as it was: a validator call each start.
    first = rewards.current_round()
    script = state_dir / "serve-validators.script"
    script.write_text("# Written by demo/public-run serve: V votes the full score for N in the round after the\n"
                      f"# services started (round {first}), which leaves every score as it was.\n{first + 1} V N 1000000\n")
    common.start("responder", [str(state_dir / "bin" / "lp2-responder"), "--listen", f"127.0.0.1:{RESPONDER}"])
    public.start_programs(node_bin, None, keeper=keeper_options(), script=script, runner=("light", "light", RESPONDER))
    sender_log = state_dir / "logs" / "sender.log"
    relays_from = sender_log.stat().st_size if sender_log.is_file() else 0   # this start's relays are read on from here
    start_sender(listen, chain_id in public.LOCAL_CHAINS)
    start_faucet(state_dir, svc["from_block"])
    join_n(listen, chain_id)
    start_explorer(state_dir, svc["from_block"])
    since = common.block_number()
    checked = chain_id not in public.LOCAL_CHAINS   # the explorer's figures are checked on a local chain
    say(f"  {time.strftime('%H:%M:%S')} round {first}; the services run until stopped (Ctrl-C or SIGTERM). "
        "Logs are in logs/ in the state directory")
    last, round_at, said_stall = first, time.time(), False
    relays = open(sender_log, "rb")
    relays.seek(relays_from)
    partial = b""
    try:
        while True:
            for name, p in common.PROCS.items():
                if p.poll() is not None:
                    raise Failed(f"{name} exited with status {p.returncode}; see logs/{name}.log in the state directory")
            now = rewards.current_round()
            if now != last:
                left = keeper_gas()
                low = (f"; the keeper can pay for about {left} more transactions: send its address {ADDR['Keeper']} "
                       "more of the chain's native token" if left < 100 else "")
                say(f"  {time.strftime('%H:%M:%S')} round {now} started{low}")
                last, round_at, said_stall = now, time.time(), False
            elif not said_stall and time.time() - round_at > STALL_SECONDS:
                # A keeper out of gas starts no round, so this is said here.
                said_stall = True
                say(f"  {time.strftime('%H:%M:%S')} no round has started for {STALL_SECONDS // 60} minutes (round {now}); "
                    f"the keeper can pay for about {keeper_gas()} more transactions: send its address {ADDR['Keeper']} "
                    "more of the chain's native token, or see logs/keeper.log in the state directory")
            *lines, partial = (partial + relays.read()).split(b"\n")
            for line in (ln.decode(errors="replace") for ln in lines):
                if " relay: node " in line:
                    say(f"  lp2-sender: {line.split(' ', 2)[2][:160]}")
            checked = checked or check_explorer(since)
            time.sleep(5)
    except KeyboardInterrupt:
        say("  the services are stopped")
        if common.FAILURES:
            say(f"{len(common.FAILURES)} check(s) of the explorer's figures failed:")
            for f in common.FAILURES:
                say(f"  {f}")
            return 1
        return 0
