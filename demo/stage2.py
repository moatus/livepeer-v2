"""./dev demo stage-2: S-09 section 1 on a fresh local chain.

Starts a local chain, deploys the stage 2 contracts, and runs the
unmodified node (orchestrator and signer) and a tiny model. A stake-free
node registers, a payer funds 10 USDC of mock USDC and earmarks 0.02 USDC,
and six real jobs each settle one ticket. Then: the three refusals where
each happens, a job after 999 junk registrations, a job with the keeper
stopped for five round lengths, and a release requested right after a job.

Every figure read from the chain is printed beside the independent
calculator's figure for the same steps. The run is written as a `ledger`
case and `./calculator/check` reproduces it.

Exit 0 every figure equal and every behaviour seen; 1 a figure differed or
a behaviour was missing; 2 the demo could not run (a tool, a download or a
port). What the node, model and chain are, and every stand-in, is read from
deployments/stage-2-local.json.
"""
from __future__ import annotations

import json
import os
import time

import common
common.use("stage-2-local.json")
from common import *  # noqa: E402,F401,F403
from common import RUN, ROOT, MANIFEST, PARAMS, CASES, VECTORS, PORT, RPC, C, ADDR, KEY, NAMES, sel  # noqa: E402

JUNK = 999  # S-09 section 1: a paid job after 999 junk registrations
ORCH, SIGNER = PORT["orch"], {"P": PORT["signer_p"], "P2": PORT["signer_p2"]}


def junk_registrations(n: int) -> None:
    """n addresses nobody holds a key for each register: anvil sends as them."""
    addrs = ["0x" + os.urandom(20).hex() for _ in range(n)]
    for i, a in enumerate(addrs, 1):
        NAMES[a] = f"J{i:03d}"
    setup = [("anvil_setBalance", [a, hex(10**18)]) for a in addrs] + [("anvil_impersonateAccount", [a]) for a in addrs]
    for a in rpc_batch(setup):
        if "error" in a:
            raise Failed(f"junk setup: {a['error']}")
    sent = rpc_batch([("eth_sendTransaction", [{"from": a, "to": C["Registry"], "data": sel("register()"), "gas": hex(200000)}])
                      for a in addrs])
    hashes = []
    for a, s in zip(addrs, sent):
        if "error" in s:
            raise Failed(f"junk registration: {s['error']}")
        hashes.append(s["result"])
    end = time.time() + 60
    receipts = []
    while time.time() < end:
        receipts = rpc_batch([("eth_getTransactionReceipt", [h]) for h in hashes])
        if all(r.get("result") for r in receipts):
            break
        time.sleep(0.5)
    for a, r in zip(addrs, receipts):
        if not r.get("result") or r["result"]["status"] != "0x1":
            raise Failed("a junk registration was not mined")
        record({"action": "register", "node": NAMES[a]}, r["result"])
    if not all(calls([(C["Registry"], "isRegistered(address)", (a,)) for a in addrs])):
        raise Failed("a junk address is not registered")


# --- the demo -----------------------------------------------------------------

def main() -> int:
    open_log()
    started = time.time()
    say("Stage 2 demo (S-09 section 1): the unmodified node paid in mock USDC on a fresh local chain")
    say(f"Stand-ins, from deployments/stage-2-local.json: {'; '.join(s['stand_in'] for s in MANIFEST['stand_ins'])}.")

    say("\n1. Setting up")
    preflight(["chain", "orch", "orch_metrics", "signer_p", "signer_p_metrics", "signer_p2", "signer_p2_metrics", "model"])
    node_bin = fetch_node()
    model = fetch_model()
    build_ours()
    say(f"  node {MANIFEST['node']['commit'][:12]} (unmodified), model sha256 {MANIFEST['model']['sha256'][:12]}, contracts and programs built")

    price = MANIFEST["price"]
    job_price = int(float(price["job_price_usd"]) * int(price["wei_per_usd"]) + 0.5)
    face = job_price + 1
    demo_case = CASES["ledger-demo"]
    fund_step = demo_case["inputs"]["steps"][1]
    deposit, earmark = int(fund_step["deposit_micro_usdc"]), int(fund_step["earmark_micro_usdc"])
    check("the ticket face the manifest gives (job price + 1) equals ledger-demo's face",
          face, int(demo_case["inputs"]["steps"][2]["face_micro_usdc"]))

    chain = MANIFEST["chain"]
    start_chain()
    pw = RUN / "pw"
    pw.write_text("pw-" + os.urandom(8).hex())
    pw.chmod(0o600)
    for who in ("deployer", "N", "P", "P2", "keeper", "anyone"):
        new_key(who)
    keys = {who: keystore(who, pw) for who in ("N", "P", "P2", "keeper")}
    deploy_stage2()
    (RUN / "addresses.json").write_text(json.dumps({"contracts": C, "accounts": {k: v for k, v in ADDR.items()}}, indent=1))
    say(f"  local chain on {RPC} (chain id {chain['chain_id']}); throwaway keys for the node N, payers P and P2, the keeper")
    say("  contracts: " + ", ".join(f"{k} {v}" for k, v in C.items()))
    check("RoundClock.roundLength() equals the shared examples' round length", call(C["RoundClock"], "roundLength()"),
          int(PARAMS["round_length_blocks"]))
    check("and the manifest's", chain["round_length_blocks"], int(PARAMS["round_length_blocks"]))

    start_model(model)
    start_orchestrator("orchestrator", "N", node_bin, keys["N"], pw, PORT["orch"], PORT["orch_metrics"])
    for payer in ("P", "P2"):
        start_signer(payer, node_bin, keys[payer], pw, SIGNER[payer], PORT[f"signer_{payer.lower()}_metrics"], [ORCH])
    say(f"  model server on port {PORT['model']}; orchestrator for N on http://127.0.0.1:{ORCH}; a signer for each payer")

    # --- refusal 1: the node will not serve while its address is unregistered
    say("\n2. Refusal 1: the node will not serve while its address is unregistered")
    code, body = 0, ""
    for _ in range(90):
        code, body = challenge("P", ORCH)
        if code in (402, 502) and ("payment" in body):
            break
        time.sleep(1)
    check("N is unregistered", not call(C["Registry"], "isRegistered(address)", ADDR["N"]), True)
    say(f"  a job sent to N gets HTTP {code}: {body.strip()[:80]}")
    check("the orchestrator refuses to issue a payment challenge", code, 502)

    r = send(KEY["N"], C["Registry"], "register()")
    record({"action": "register", "node": "N"}, r)
    code, _ = challenge("P", ORCH)
    say(f"  N registers for free, with no stake (block {int(r['blockNumber'], 16)}); the same job now gets HTTP {code}, a payment challenge.")
    say("  Nothing else changed between the two requests.")
    check("after registering, the node issues a payment challenge", code, 402)

    # --- funding
    say("\n3. The payer funds its account")
    fund("P", deposit, earmark, "N")
    shown = observe("P", "N")
    say(f"  P puts {money(deposit)} micro-USDC (10 USDC) in its deposit and earmarks {money(earmark)} (0.02 USDC) for N, with lp2-fund")
    say(f"  N is shown: deposit {money(shown[0])}, earmark for N {money(shown[1])}")
    start("keeper", [str(RUN / "bin" / "lp2-keeper"), "--rpc-url", RPC, "--keystore-file", str(keys["keeper"]),
                     "--keystore-password-file", str(pw), "--controller-address", C["Controller"], "--poll", "1s"])
    say("  the round keeper starts (it starts each round once 50 blocks have passed)")

    # --- six jobs
    say(f"\n4. Six real jobs through the node, each paid with one ticket of {money(face)} micro-USDC")
    for i in range(1, 7):
        answer = job("P", ORCH, SIGNER["P"])
        say(f"  job {i}: paid, the model answered: {answer[:60]!r}")
    settled = wait_settled(6, "six jobs")
    say("  the node redeemed each ticket itself, after its parameters expired (40 blocks; the demo mines empty blocks rather than wait):")
    for i, s in enumerate(settled, 1):
        say(f"  ticket {i}: created in round {s['created_round']}, redeemed in round {s['round']}, face {money(s['face'])}: "
            f"node +{money(s['node_amount'])}, network +{money(s['network_amount'])}")
    ordered, views, _ = reconcile("After the six jobs")
    want = demo_case["expected"]
    fees_n = sum(v for (r, n), v in views["fees"].items() if n == "N")
    say("  against S-09 section 1 (ledger-demo in the shared examples):")
    check("P's deposit", views["deposit"]["P"], int(want["deposits_micro_usdc"]["P"]))
    check("P's earmark for N", views["earmark"][("P", "N")], int(want["earmarks_micro_usdc"]["P"]["N"]))
    check("N's wallet", views["wallet"]["N"], int(want["paid_micro_usdc"]["N"]))
    check("network share held", views["network"], int(want["network_micro_usdc"]))
    check("N's fee records across the rounds redeemed in", fees_n, sum(int(v["N"]) for v in want["fees_micro_usdc"].values()))

    # --- refusal 3: a ticket sent a second time
    say("\n5. Refusal 3: the contract refuses a ticket sent a second time")
    first = settled[0]
    data = first["input"]
    answer = rpc_batch([("eth_call", [{"from": ADDR["anyone"], "to": C["Broker"], "data": data}, "latest"])])[0]
    revert = (answer.get("error") or {}).get("data", "")
    code_name = next((c for c in VECTORS["codes"] if revert[:10] == sel(f"{c}()")), revert[:10] or "accepted")
    before = call(C["Broker"], "networkHeld()")
    r = send(KEY["anyone"], C["Broker"], data, gas=1_000_000)
    record({"action": "redeem", "ticket": first["hash"], "payer": "P", "node": "N",
            "created_round": first["created_round"], "face_micro_usdc": first["face"]}, r, code=code_name)
    say(f"  ticket 1's redemption, sent again by another account, block {int(r['blockNumber'], 16)}: {'reverted' if r['status'] == '0x0' else 'ACCEPTED'} with {code_name}")
    check("the second redemption is refused on chain", r["status"], "0x0")
    check("its code", code_name, "TICKET_USED")
    check("the network share did not move", call(C["Broker"], "networkHeld()"), before)

    # --- refusal 2: a payer that has set nothing aside for the node
    say("\n6. Refusal 2: the node refuses a payer that has set nothing aside for it")
    fund("P2", deposit, 0, None)
    shown = observe("P2", "N")
    say(f"  P2 funds a deposit of {money(deposit)} and earmarks nothing; N is shown deposit {money(shown[0])}, earmark {money(shown[1])}")
    code, body = challenge("P2", ORCH)
    check("the orchestrator issues P2 a payment challenge", code, 402)
    code, signed = sign(SIGNER["P2"], body)
    say(f"  P2's own signer (the node's signer program) will not sign: HTTP {code} {signed.strip()[:60]}")
    check("P2's signer refuses", code, 400)
    check("because P2's funds for N are unavailable", "payer funds unavailable" in signed, True)
    say("  the orchestrator makes the same check itself. P2 earmarks for N, its signer signs a payment,")
    say("  and P2 asks for the earmark back before sending it:")
    fund("P2", 0, earmark, "N")
    code, body = challenge("P2", ORCH)
    check("the orchestrator issues P2 a payment challenge", code, 402)
    code, signed = sign(SIGNER["P2"], body)
    check("P2's signer signs while the earmark is there", code, 200)
    request_release("P2", "N", earmark)
    shown = observe("P2", "N")
    say(f"  N is now shown for P2: deposit {money(shown[0])}, earmark {money(shown[1])}")
    code, answer = pay("P2", signed, ORCH)
    say(f"  the orchestrator answers the paid request: HTTP {code} {answer.strip()[:60]}")
    check("the node refuses the payment", code, 400)
    check("because the earmark it is shown is 0", "claimable reserve is unavailable" in answer, True)
    reconcile("After the three refusals")

    # --- 999 junk registrations
    say(f"\n7. A paid job after {JUNK} junk registrations")
    junk_registrations(JUNK)
    say(f"  {JUNK} addresses register (anvil sends as them, since nobody holds their keys)")
    answer = job("P", ORCH, SIGNER["P"])
    settled = wait_settled(7, "the job after the junk registrations")
    s7, s1 = settled[6], settled[0]
    say(f"  job 7: paid, the model answered {answer[:50]!r}; ticket redeemed in round {s7['round']}: node +{money(s7['node_amount'])}, network +{money(s7['network_amount'])}")
    check("job 7 moved what job 1 moved (gross, node, network)", (s7["gross"], s7["node_amount"], s7["network_amount"]),
          (s1["gross"], s1["node_amount"], s1["network_amount"]))
    reconcile(f"After {JUNK} registrations and job 7")

    # --- the keeper stopped for five round lengths
    length = int(PARAMS["round_length_blocks"])
    say(f"\n8. A job with the keeper stopped for five round lengths ({5 * length} blocks)")
    stop("keeper")
    round_before, started_at = current_round(), round_started_at()
    mine(5 * length)
    say(f"  keeper stopped; round {round_before} started at block {started_at}; the chain is now at block {block_number()}")
    answer = job("P", ORCH, SIGNER["P"])
    settled = wait_settled(8, "the job with the keeper stopped")
    s8 = settled[7]
    say(f"  job 8: paid, the model answered {answer[:50]!r}; ticket created in round {s8['created_round']}, redeemed in round {s8['round']} at block {s8['block']}")
    check("no round started meanwhile", current_round(), round_before)
    check("the ticket was paid in full", s8["gross"], face)
    check("the round had lasted at least five round lengths when it was paid", s8["block"] - started_at >= 5 * length, True)
    start("keeper", [str(RUN / "bin" / "lp2-keeper"), "--rpc-url", RPC, "--keystore-file", str(keys["keeper"]),
                     "--keystore-password-file", str(pw), "--controller-address", C["Controller"], "--poll", "1s"])
    for _ in range(30):
        if current_round() > round_before:
            break
        time.sleep(1)
    say(f"  the keeper restarts and starts round {current_round()}: one round, none skipped")
    check("the next round is one more", current_round(), round_before + 1)
    reconcile("After the job with the keeper stopped")

    # --- a release requested right after a job
    say("\n9. A release the payer requests right after a job")
    answer = job("P", ORCH, SIGNER["P"])
    dep = call(C["Broker"], "earmarkOf(address,address)", ADDR["P"], ZERO)
    ear = call(C["Broker"], "earmarkOf(address,address)", ADDR["P"], ADDR["N"])
    r1 = request_release("P", None, dep)
    r2 = request_release("P", "N", ear)
    shown = observe("P", "N")
    say(f"  job 9 paid (the model answered {answer[:40]!r}); P at once asks for its whole deposit ({money(dep)}) and earmark ({money(ear)}) back")
    say(f"  N is now shown: deposit {money(shown[0])}, earmark {money(shown[1])}; it holds job 9's ticket")
    nh = call(C["MockUSDC"], "balanceOf(address)", ADDR["N"])
    settled = wait_settled(9, "the job before the release request")
    s9 = settled[8]
    say(f"  the node redeems job 9's ticket in block {s9['block']}, after the requests (blocks {int(r1['blockNumber'], 16)} and {int(r2['blockNumber'], 16)}):"
        f" gross {money(s9['gross'])}, node +{money(s9['node_amount'])}, network +{money(s9['network_amount'])}")
    check("the requests came before the redemption", int(r2["blockNumber"], 16) < s9["block"], True)
    check("the job is paid in full", s9["gross"], face)
    check("N's wallet rose by its half", call(C["MockUSDC"], "balanceOf(address)", ADDR["N"]) - nh, s9["node_amount"])
    shown = observe("P", "N")
    ready = words(C["Broker"], "earmarkOf(address,address)", ADDR["P"], ADDR["N"])[2]
    say(f"  after it, N is shown deposit {money(shown[0])}, earmark {money(shown[1])}; the payer may release from round {ready}")
    while current_round() < ready:
        r0 = current_round()
        mine(length)
        for _ in range(30):
            if current_round() > r0:
                break
            time.sleep(1)
        else:
            raise Failed("the keeper did not start the next round")
    release("P", "N")
    release("P", None)
    say(f"  round {current_round()}: P releases both and receives {money(call(C['MockUSDC'], 'balanceOf(address)', ADDR['P']))} micro-USDC")
    observe("P", "N")
    ordered, views, _ = reconcile("At the end of the run")

    # --- the run as a ledger case, reproduced by the calculator
    say("\n10. The run as a ledger case, reproduced by the calculator")
    scan(ordered, ["Registry", "Broker"])
    write_case("ledger-demo-run", ordered, views)
    return finish(started, "six jobs paid, three refusals seen, every balance read from the chain equals the calculator's.")


if __name__ == "__main__":
    run_main(main)
