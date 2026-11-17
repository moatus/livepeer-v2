"""./dev demo: S-05 section 1, one full reward cycle on a fresh local chain.

Deploys the stage 3 contracts and runs the unmodified node for three nodes,
N, M and K, the round keeper and three scripted validators. Four holders
bond test LPT to the nodes; in round 2 the node serves five paid relays of
1 USDC each, three for N and one each for M and K. The keeper is stopped as
round 2 ends, which delays round 2's validator and treasury amounts; once it
runs again, after somebody else has started round 3, it mints them. The
validators vote M 0.5 and K 0. In round 8 N's claim is refused as too early; in round 9 a rival's and
a delegator's claims for N are refused, N claims at score 1, its holders
withdraw their parts, N's second claim is refused, M claims at 0.5 and K at
0. F then moves 100 LPT from K toward M, unbonds 40 of the move and rebonds
it, which can go back only to K.

Every figure read from the chain is printed beside the independent
calculator's figure for the same steps, and checked against section 1's
figures (the shared example ledger-cycle). The run is written as a `ledger`
case and `./calculator/check` reproduces it.

Exit 0 every figure equal and every behaviour seen; 1 a figure differed or
a behaviour was missing; 2 the demo could not run. What runs, and every
stand-in, is read from deployments/stage-3-local.json.
"""
from __future__ import annotations

import json
import os
import subprocess
import time

import common
common.use("stage-3-local.json")
from common import *  # noqa: E402,F401,F403
from common import RUN, MANIFEST, PARAMS, CASES, PORT, RPC, C, ADDR, KEY, NAMES, RECORDS  # noqa: E402

LPT = 10**18
ORCHS = {"N": ("orch", "orch_metrics"), "M": ("orch_m", "orch_m_metrics"), "K": ("orch_k", "orch_k_metrics")}
VALIDATORS = {"V1": 5_000 * LPT, "V2": 3_000 * LPT, "V3": 2_000 * LPT}
SCRIPT = common.HERE / "validators.script"
KEYS: dict[str, object] = {}
CYCLE = CASES["ledger-cycle"]  # section 1, step for step


def cycle_step(**match) -> dict:
    """The step of ledger-cycle that has these fields."""
    return next(s for s in CYCLE["inputs"]["steps"] if all(s.get(k) == v for k, v in match.items()))


def amount(**match) -> int:
    return int(cycle_step(**match)["amount_wei"])


# --- deployment ---------------------------------------------------------------

def deploy_stage3() -> None:
    """Stage 2's contracts, then test LPT, StakeLedger, Scores and Claims.
    The token is given the address Claims will have, its one minter."""
    deploy_stage2()
    claims_at = next_address(3)
    C["TestLPT"] = forge_create("src/money/TestLPT.sol:TestLPT", claims_at, MANIFEST["lpt"]["genesis_supply_wei"])
    C["StakeLedger"] = forge_create("src/money/StakeLedger.sol:StakeLedger", C["TestLPT"], C["Registry"], C["RoundClock"])
    C["Scores"] = forge_create("src/scores/Scores.sol:Scores", C["Registry"], C["RoundClock"], C["StakeLedger"])
    C["Claims"] = forge_create("src/money/Claims.sol:Claims", C["TestLPT"], C["Registry"], C["RoundClock"], C["Broker"],
                               C["StakeLedger"], C["Scores"], C["Scores"], ADDR["Treasury"])
    if C["Claims"].lower() != claims_at.lower():
        raise Failed(f"Claims landed at {C['Claims']}, not {claims_at} as the token was told")
    for n in ("Claims", "StakeLedger"):
        NAMES[C[n].lower()] = n
        ADDR[n] = C[n]


# --- steps --------------------------------------------------------------------

def tx_step(fields: dict, who: str, to: str, signature: str, *args) -> dict:
    """A step sent with cast as `who`."""
    return record(fields, send(KEY[who], to, signature, *args))


def stake(who: str, fields: dict, *argv: str, want: str | None = None) -> dict:
    """A step sent by lp2-stake as `who`: a transaction, or a refusal the
    contract gave at a block, recorded with its code. `want` is the code the
    demo expects, or None for a step that takes effect."""
    done = subprocess.run([str(RUN / "bin" / "lp2-stake"), "--rpc-url", RPC, "--keystore-file", str(KEYS[who]),
                           "--keystore-password-file", str(RUN / "pw"), "--stake-ledger-address", C["StakeLedger"],
                           "--claims-address", C["Claims"], "--lpt-address", C["TestLPT"], *argv],
                          capture_output=True, text=True)
    out = done.stdout.strip().splitlines()
    with open(RUN / "logs" / "lp2-stake.log", "a") as log:
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
                        "cut": int(d[192:256], 16), "pot": int(d[256:320], 16)}
        check(f"{node}'s claim read the score scoreOf gives at its block", r["claimed"]["score"], r["fields"]["score"])
    return r


def withdraw(holder: str, node: str, rounds: list[int], want: str | None = None) -> dict:
    before = call(C["TestLPT"], "balanceOf(address)", a(holder))
    r = stake(holder, {"action": "withdraw_parts", "holder": holder, "node": node, "rounds": rounds},
              "withdraw-parts", a(node), ",".join(map(str, rounds)), want=want)
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


def members() -> dict:
    """Each earning round's validator members, for every round whose shares
    were minted: the case's stand-in for the validator set."""
    out = {}
    for s in keeper_steps():
        r = s["fields"]["earned_round"]
        w = words(C["Scores"], "membersAt(uint256)", r)
        listed = sorted(name("0x" + f"{x:064x}"[-40:]) for x in w[2:2 + w[1]])
        if listed:
            out[str(r)] = listed
    return out


# --- rounds -------------------------------------------------------------------

def start_keeper() -> None:
    start("keeper", [str(RUN / "bin" / "lp2-keeper"), "--rpc-url", RPC, "--keystore-file", str(KEYS["Keeper"]),
                     "--keystore-password-file", str(RUN / "pw"), "--controller-address", C["Controller"],
                     "--claims-address", C["Claims"], "--poll", "1s"])


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


# --- the demo -----------------------------------------------------------------

def main() -> int:
    open_log()
    started = time.time()
    say("Stage 3 demo (S-05 section 1): one full reward cycle, stake, scores, claims and LPT, on a fresh local chain")
    say(f"Stand-ins, from deployments/stage-3-local.json: {'; '.join(s['stand_in'] for s in MANIFEST['stand_ins'])}.")

    say("\n1. Setting up")
    preflight(["chain", "orch", "orch_metrics", "orch_m", "orch_m_metrics", "orch_k", "orch_k_metrics",
               "signer_p", "signer_p_metrics", "model"])
    node_bin = fetch_node()
    model = fetch_model()
    build_ours()
    say(f"  node {MANIFEST['node']['commit'][:12]} (unmodified), model sha256 {MANIFEST['model']['sha256'][:12]}, contracts and programs built")
    face = ticket_face()
    check("the ticket face the manifest gives (job price + 1) equals ledger-cycle's 1 USDC",
          face, int(cycle_step(action="redeem")["face_micro_usdc"]))

    start_chain()
    pw = RUN / "pw"
    pw.write_text("pw-" + os.urandom(8).hex())
    pw.chmod(0o600)
    people = ["deployer", "N", "M", "K", "W", "O", "D", "E", "F", "P", *VALIDATORS, "Keeper", "Anyone"]
    for who in people:
        new_key(who)
    treasury = "0x" + os.urandom(20).hex()
    ADDR["Treasury"], NAMES[treasury.lower()] = treasury, "Treasury"
    for who in people:
        if who not in ("deployer", "W"):
            KEYS[who] = keystore(who, pw)
    deploy_stage3()
    (RUN / "addresses.json").write_text(json.dumps({"contracts": C, "accounts": dict(ADDR)}, indent=1))
    say(f"  local chain on {RPC} (chain id {MANIFEST['chain']['chain_id']}); throwaway keys for nodes N, M and K, N's payout W,")
    say("  holders O, D, E and F, payer P, validators V1 to V3, the keeper and an anyone; the treasury is an address with no key")
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
    say(f"  model server on port {PORT['model']}; an orchestrator for each of N, M and K; a signer for P")

    # --- round 1: nodes, holders and the payer
    say("\n2. Round 1: three nodes register, four holders bond test LPT to them, the payer funds")
    for n in ("N", "M", "K", *VALIDATORS):
        tx_step({"action": "register", "node": n}, n, C["Registry"], "register()")
    tx_step({"action": "set_payout", "node": "N", "payout": "W"}, "N", C["Registry"], "setPayout(address)", a("W"))
    cut = int(cycle_step(action="set_cut")["cut_millionths"])
    stake("N", {"action": "set_cut", "node": "N", "cut_millionths": cut}, "set-cut", a("N"), str(cut))
    grants = {h: amount(action="grant_lpt", to=h) for h in ("O", "D", "E", "F")} | VALIDATORS
    for h, wei in grants.items():
        tx_step({"action": "grant_lpt", "to": h, "amount_wei": wei}, "deployer", C["TestLPT"], "transfer(address,uint256)", a(h), wei)
    for h, n in (("O", "N"), ("D", "N"), ("E", "M"), ("F", "K")):
        bond(h, n, amount(action="bond", holder=h, node=n))
    say(f"  N, M and K register; N pays to W and sets a reward cut of {cut:,} millionths; V1 to V3 register too")
    say(f"  bonded: O {lpt(amount(action='bond', holder='O', node='N'))} and D {lpt(amount(action='bond', holder='D', node='N'))} to N, "
        f"E {lpt(amount(action='bond', holder='E', node='M'))} to M, F {lpt(amount(action='bond', holder='F', node='K'))} to K")
    deposit = int(cycle_step(action="fund")["deposit_micro_usdc"])
    fund("P", deposit, 0, None)
    for n in ORCHS:
        fund("P", 0, face, n)
    say(f"  P deposits {money(deposit)} micro-USDC and earmarks {money(face)} for each node: the node will not take a payer")
    say("  who has set nothing aside for it (stage 2's refusal 2), so ledger-cycle's payer, with no earmark, could not pay here")

    names_file = RUN / "names.json"
    names_file.write_text(json.dumps({n: ADDR[n] for n in ("N", "M", "K", *VALIDATORS)}))
    start("validators", [str(RUN / "bin" / "lp2-validators"), "--rpc-url", RPC, "--keystore-password-file", str(pw),
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
    for v, wei in VALIDATORS.items():
        bond(v, v, wei)
        stake(v, {"action": "set_cut", "node": v, "cut_millionths": 1_000_000}, "set-cut", a(v), "1000000")
    stake_n = observe_stake("N")
    say(f"  O bonds a further {lpt(amount(action='bond', holder='O', node='N', round='2'))} to N; V1 to V3 bond 5,000, 3,000 and 2,000 "
        "to themselves and keep all of their validator pay (cut 100 %); all of it counts from round 3")
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
    say("\n5. The scripted validators opt in in round 3 and vote in round 4")
    wait("the three validators to opt in", lambda: len(logs(C["Scores"], "Seated(address,uint256,address)")) == 3)
    for lg in logs(C["Scores"], "Seated(address,uint256,address)"):
        say(f"  {name('0x' + lg['topics'][1][-40:])} opts in in round 3 and is seated from round {int(lg['data'][2:66], 16)}")
    next_round()
    wait("the validators' four votes", lambda: len(logs(C["Scores"], "Voted(address,address,uint256,uint256)")) == 4)
    for lg in logs(C["Scores"], "Voted(address,address,uint256,uint256)"):
        say(f"  {name('0x' + lg['topics'][1][-40:])} votes {int(lg['data'][2:66], 16):,} on {name('0x' + lg['topics'][2][-40:])} in round {int(lg['data'][66:130], 16)}")
    w = words(C["Scores"], "membersAt(uint256)", current_round())
    say(f"  members of round {current_round()}: {', '.join(sorted(name('0x' + f'{x:064x}'[-40:]) for x in w[2:2 + w[1]]))}")
    say(f"  scores now: N {call(C['Scores'], 'scoreOf(address)', a('N')):,} (nobody voted), M {call(C['Scores'], 'scoreOf(address)', a('M')):,}, "
        f"K {call(C['Scores'], 'scoreOf(address)', a('K')):,}")

    # --- claims
    say("\n6. Round 8: N's claim for round 2 is too early")
    to_round(8)
    claim("N", "N", 2, want="CLAIM_TOO_EARLY")
    claim("M", "N", 2, want="NOT_AUTHORIZED")

    say("\n7. Round 9: the claims")
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

    say("\n8. Rounds 10 to 12: F unbonds part of the move and rebonds it, which can go back only to K")
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
    to_round(12)
    observe_stake("K")

    # --- the reconciliation
    say("\n9. Every figure, read from the chain, beside the calculator's")
    extra = keeper_steps()
    m = members()
    ordered, views, calc = reconcile("At the end of the run", extra, m)
    say("  against S-05 section 1 (ledger-cycle in the shared examples):")
    want = CYCLE["expected"]
    for who in ("W", "O", "D", "Claims"):
        check(f"{who}'s LPT", views["lpt"][who], int(want["lpt_wei"][who]))
    check("W's USDC (N's half of its three relays)", views["wallet"]["W"], int(want["paid_micro_usdc"]["W"]))
    check("LPT minted for round 2", views["minted_for_round"].get(2), int(want["minted_for_round_wei"]["2"]))
    ceiling = int(CASES["ceiling-1"]["expected"]["ceiling_wei"])
    never = ceiling - views["minted_for_round"][2]
    calc_never = int(calc_ceiling(1)) - int(calc["minted_for_round_wei"]["2"])
    say(f"  {'LPT never minted for round 2, C(1) less the above':<46}{lpt(never):>28}{lpt(calc_never):>28}  "
        f"{'equal' if never == calc_never else 'DIFFERS'}")
    check("LPT never minted for round 2 (section 1)", never, 544_750_300_038_956_261_284)
    shown = [r for r in ordered if r["fields"]["action"] == "observe_stake"]
    check("N's stake in rounds 2 and 3", [(r["round"], r["shown"][0]) for r in shown if r["fields"]["node"] == "N"],
          [(2, 400 * LPT), (3, 500 * LPT)])
    check("K's stake in rounds 10, 11 and 12", [(r["round"], r["shown"][0]) for r in shown if r["fields"]["node"] == "K"],
          [(10, 250 * LPT), (11, 210 * LPT), (12, 250 * LPT)])
    check("the scores the claims read: N, M, K",
          [r["fields"]["score"] for r in ordered if r["fields"]["action"] == "claim" and not r.get("code")],
          [1_000_000, 500_000, 0])
    check("F's unbonded LPT waiting and ready: none (unbondedOf)", words(C["StakeLedger"], "unbondedOf(address)", a("F")), [0, 0])

    say("\n10. The run as a ledger case, reproduced by the calculator")
    scan(ordered, ["Registry", "Broker", "StakeLedger", "Claims"])
    write_case("ledger-cycle-run", ordered, views, m)
    return finish(started, "one reward cycle, every refusal seen, every balance read from the chain equals the calculator's.")


def calc_ceiling(r: int) -> str:
    from reference import Parameters, calculate
    return str(calculate("ceiling", {"round": str(r)}, Parameters.from_strings(PARAMS), {})["ceiling_wei"])
