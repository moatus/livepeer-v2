"""Full system on a fresh chain, with real jobs and exact calculator checks."""
import json
import os
import signal
import time
import urllib.error
import urllib.request

import rewards as rewards
import buyback as bb
import common as c

from common import ADDR, C, KEY, NAMES


def pooled_from(who):
    pool = c.rpc("txpool_content", [])
    return [tx for bucket in ("pending", "queued")
            for sender, txs in pool[bucket].items() if sender.lower() == ADDR[who].lower()
            for tx in txs.values()]


def balances():
    return (c.words(C["Broker"], "getSenderInfo(address)", ADDR["P"])[0],
            c.call(C["MockUSDC"], "balanceOf(address)", ADDR["W"]),
            c.call(C["Broker"], "networkHeld()"))


def paid_job():
    answer = c.job("P", c.PORT["orch"], c.PORT["signer_p"])
    c.say(f"  round {c.current_round()}: paid node job returned {answer[:100]!r}")


def recover(start_node, face):
    count = len(c.settlements())
    before = balances()
    c.rpc("evm_setIntervalMining", [0])
    c.rpc("evm_setAutomine", [False])
    paid_job()
    c.mine(41)
    rewards.wait("N's redemption in the transaction pool", lambda: pooled_from("N"))
    tx, = pooled_from("N")
    pending = tx["hash"]
    if tx["to"].lower() != C["Broker"].lower() or c.rpc("eth_getTransactionReceipt", [pending]) is not None:
        raise c.Failed("N's pooled transaction is not an unmined redemption")
    proc = c.PROCS.pop("orchestrator-N")
    os.killpg(proc.pid, signal.SIGKILL)
    proc.wait(10)
    c.rpc("anvil_dropTransaction", [pending])
    c.check("pending redemption dropped while N is down", c.rpc("eth_getTransactionByHash", [pending]), None)
    start_node()
    c.rpc("evm_setIntervalMining", [1])
    rewards.wait("redemption recovered after restart", lambda: len(c.settlements()) > count, 420)
    time.sleep(12)  # Let two more redeemer passes run.
    settled = c.settlements()
    c.check("the recovered transaction settled once", [s["tx"] for s in settled[count:]], [pending])
    c.check("every ticket settled once", len({s["hash"] for s in settled}), len(settled))
    steps = [dict(step) for step in c.CASES["ledger-authority"]["inputs"]["steps"]
             if step["action"] in ("register", "set_payout", "fund", "redeem") and int(step["step"]) <= 17]
    steps[-1]["face_micro_usdc"] = str(face)
    calc = c.calculator(steps)
    funded = int(next(step["deposit_micro_usdc"] for step in steps if step["action"] == "fund"))
    expected = (int(calc["deposits_micro_usdc"]["P"]) - funded,
                int(calc["paid_micro_usdc"]["W"]), int(calc["network_micro_usdc"]))
    for label, old, new, want in zip(("payer deposit (micro-USDC)", "W payout (micro-USDC)", "network share (micro-USDC)"), before, balances(), expected):
        rewards.side(f"Restart: {label}", new - old, want, c.money)


def check_buys(seed):
    bought = c.logs(C["Buyback"], bb.BOUGHT)
    if not bought:
        raise c.Failed("keeper did not emit Bought while the node was working")
    # Replay the pool seed and the calls that changed the buyback's state.
    # A buy can emit Observed itself, so only standalone pokes are inputs.
    buy_txs = {event["transactionHash"] for event in bought}
    events = [(seed, "seed", {"usdc_micro_usdc": c.MANIFEST["pool"]["seed_usdc_micro_usdc"],
                             "lpt_wei": c.MANIFEST["pool"]["seed_lpt_wei"]})]
    events.extend((c.receipt(s["tx"]), "job", {"face_micro_usdc": str(s["gross"])}) for s in c.settlements())
    events.extend((event, "poke", {}) for event in c.logs(C["Buyback"], "Observed(address,uint256,uint256)")
                  if event["transactionHash"] not in buy_txs)
    events.extend((event, "buy", {}) for event in bought)
    events.sort(key=lambda row: (int(row[0]["blockNumber"], 16), int(row[0]["transactionIndex"], 16)))
    steps, observed = [], []
    for index, (event, action, fields) in enumerate(events, 1):
        block = int(event["blockNumber"], 16)
        steps.append(dict(step=str(index), round=str(c.current_round(block)), time=str(bb.block_time(event)),
                          action=action, **fields))
        if action == "buy":
            tx = c.rpc("eth_getTransactionByHash", [event["transactionHash"]])
            c.check("Bought sent by the keeper", tx["from"].lower(), ADDR["Keeper"].lower())
            result = bb.bought_of({"logs": [event]})
            observed.append({"step": str(index), **result})
            supply = [c.call(C["TestLPT"], "totalSupply()", block=hex(b)) for b in (block - 1, block)]
            minted = c.rpc("eth_getLogs", [{"address": C["TestLPT"], "fromBlock": hex(block), "toBlock": hex(block),
                                            "topics": [c.topic("Transfer(address,address,uint256)"), "0x" + "0"*64]}])
            issued = sum(int(log["data"], 16) for log in minted)
            c.check("Bought: fall in LPT supply after accounting for same-block mints (wei)",
                    supply[0] + issued - supply[1], int(result["burned_wei"]))
    calc = rewards.calculate("buyback", {"lpt_supply_wei": c.MANIFEST["lpt"]["genesis_supply_wei"], "steps": steps})
    c.check("Bought amounts agree with the calculator", observed, calc["bought"])


# --- the explorer: one node's page, read from the chain --------------------------

def explorer_get(path: str) -> str | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{c.PORT['explorer']}{path}", timeout=30) as r:
            return r.read().decode()
    except (urllib.error.URLError, ConnectionError, OSError):
        return None


def explorer_status() -> dict:
    body = explorer_get("/status.json")
    if body is None:
        raise c.Failed("lp2-explorer did not answer /status.json")
    return json.loads(body)


def start_explorer(first_block: int) -> None:
    """lp2-explorer for N, scanning events from the block before the deployment."""
    c.start("explorer", [str(c.RUN / "bin" / "lp2-explorer"), "--rpc-url", c.RPC,
                         "--controller-address", C["Controller"], "--parameters-address", C["Parameters"],
                         "--stake-ledger-address", C["StakeLedger"], "--claims-address", C["Claims"],
                         "--usdc-address", C["MockUSDC"], "--first-block", str(first_block),
                         "--node", ADDR["N"], "--listen", f"127.0.0.1:{c.PORT['explorer']}"])
    rewards.wait("lp2-explorer to answer", lambda: explorer_get("/status.json") is not None, 30)
    status = explorer_status()
    c.check("explorer, first read: N registered, every figure read, no buy yet",
            (status["node"]["registered"], status["unavailable"], status["testnet"]["buys"]), (True, [], []))


def check_explorer(fixture: dict, calc: dict) -> None:
    """The explorer's figures for N against the chain, read at the block the
    explorer read, and against the calculator for round 2, which N claimed."""
    c.say("\nThe explorer: N's page against the chain and the calculator")
    status = explorer_status()
    node, net, block = status["node"], status["testnet"], status["block"]
    n = ADDR["N"]
    c.check("explorer: no figure unavailable", status["unavailable"], [])
    now = c.current_round(block)
    c.check("explorer: round, executor in force, validator set, as the chain has them",
            (net["round"], net["executor"].lower(), sorted(c.name(v) for v in net["validators"])),
            (now, C["Buyback"].lower(), rewards.members_at(now, block)))
    c.check("explorer: registered, operator key, payout address",
            (node["registered"], node["operator"].lower(), node["payout"].lower()),
            (True, ADDR["K2"].lower(), ADDR["W"].lower()))
    c.check("explorer: stake and own bond this round, payout's USDC, total fees this round, node key's ETH",
            [int(node["stake_wei"]), int(node["bond_wei"]), int(node["payout_usdc_micro"]), int(net["total_fees_usdc_micro"]),
             int(node["eth_wei"])],
            [c.call(C["StakeLedger"], "stakeAt(address,uint256)", n, now, block=block),
             c.call(C["StakeLedger"], "bondAt(address,address,uint256)", n, n, now, block=block),
             c.call(C["MockUSDC"], "balanceOf(address)", ADDR["W"], block=block),
             c.call(C["Broker"], "totalFees(uint256)", now, block=block),
             int(c.rpc("eth_getBalance", [n, hex(block)]), 16)])
    started = [e for e in c.logs(C["RoundClock"], "RoundStarted(uint256,uint256,bytes32)", block)
               if int(e["topics"][1], 16) == now]
    c.check("explorer: the block the round started in", net["round_start_block"], int(started[-1]["blockNumber"], 16))
    c.check("explorer: N's score now, which a claim made now would read, as the chain has it",
            int(node["score_now"]), c.call(C["Scores"], "scoreOf(address)", n, block=block))
    # Each round N claimed carries the score its claim applied, from its
    # Claimed event; a round not claimed carries none.
    applied = {int(e["topics"][2], 16): int(e["data"][2 + 64:2 + 128], 16)
               for e in c.logs(C["Claims"], "Claimed(address,uint256,address,uint256,uint256,uint256,uint256)", block)
               if int(e["topics"][1], 16) == int(n, 16)}
    c.check("explorer: the score each of N's claims applied, as its Claimed event has it",
            {r["round"]: int(r["claim_score"]) for r in node["rounds"] if r["claim_score"] is not None},
            {r: sc for r, sc in applied.items() if any(row["round"] == r for row in node["rounds"])})

    # Round 2: N earned 1 USDC of fees, and its operator key claimed it in round 9.
    row = next(r for r in node["rounds"] if r["round"] == 2)
    steps = fixture["steps"]
    delay, window = int(c.PARAMS["reward_delay_rounds"]), int(c.PARAMS["claim_window_rounds"])
    c.check("explorer: round 2 claimed, claimable from round 9 to 30",
            (row["state"], row["claimable_from"], row["claimable_until"]), ("claimed", 2 + delay, 2 + delay + window))
    # The calculator's figures: W, N's payout when it claimed round 2, holds
    # only that claim's cut; the pot is the rest; N holds no bond to itself,
    # so its part of the pot is 0 (R-24's floor of pot x bond / stake).
    paid = int(calc["lpt_wei"]["W"])
    cut = paid * int(next(s["cut_millionths"] for s in steps if s["action"] == "set_cut")) // 10**6
    stake = int(next(o["stake_wei"] for o in calc["observed"] if o["step"] == "16"))
    own = sum(int(s["amount_wei"]) for s in steps if s["action"] == "bond" and s["holder"] == "N" and s["node"] == "N")
    _, chain_paid, chain_pot = c.words(C["Claims"], "entitlementOf(address,uint256)", n, 2, block=block)
    chain_part, chain_withdrawn = c.words(C["Claims"], "partOf(address,address,uint256)", n, n, 2, block=block)
    claimed_at = next(s for s in steps if s["action"] == "claim" and s["earned_round"] == "2")
    for label, page, chain, calculator, fmt in (
            ("Round 2: fees counted for N", int(row["fees_usdc_micro"]),
             c.call(C["Broker"], "feesOf(uint256,address)", 2, n, block=block), int(calc["fees_micro_usdc"]["2"]["N"]), c.money),
            ("Round 2: N's stake", int(row["stake_wei"]),
             c.call(C["StakeLedger"], "stakeAt(address,uint256)", n, 2, block=block), stake, c.lpt),
            ("Round 2: the score its claim applied", int(row["claim_score"]), applied[2], int(claimed_at["score"]), str),
            ("Round 2: claim paid (cut and pot)", int(row["paid_wei"]), chain_paid, paid, c.lpt),
            ("Round 2: pot", int(row["pot_wei"]), chain_pot, paid - cut, c.lpt),
            ("Round 2: N's own part", int(row["part_wei"]), chain_part, (paid - cut) * own // stake, c.lpt)):
        rewards.side(f"Explorer: {label}", page, calculator, fmt)
        c.check(f"explorer: {label} as the chain has it", page, chain)
    c.check("explorer: round 2 part not withdrawn, as the chain has it", row["part_withdrawn"], bool(chain_withdrawn))

    bought = c.logs(C["Buyback"], bb.BOUGHT, block)
    want = [{"round": int(e["topics"][1], 16), "block": int(e["blockNumber"], 16), "tx": e["transactionHash"],
             **{k: str(int(e["data"][2 + 64 * i:66 + 64 * i], 16)) for i, k in
                ((1, "usdc_in_micro"), (2, "lpt_bought_wei"), (3, "burned_wei"))}}
            for e in reversed(bought[-5:])]
    c.check("explorer: the latest buys and burns, as the Bought events have them", net["buys"], want)
    c.check("explorer: at least one buy burned LPT", any(int(b["burned_wei"]) > 0 for b in net["buys"]), True)
    page = explorer_get("/") or ""
    c.check("explorer: the page reloads itself, carries no script and shows no figure unavailable",
            ('http-equiv="refresh"' in page, "<script" in page.lower(), "unavailable" in page), (True, False, False))


def main():
    started = time.time()
    c.open_log()
    c.say("Full system: announced parameters, operator rotation and settlement recovery")
    c.say("The unmodified node earns in mock USDC, and the keeper buys and burns test LPT.")
    c.preflight(["chain", "orch", "orch_metrics", "signer_p", "signer_p_metrics", "model", "explorer"])
    node_bin, model = c.fetch_node(), c.fetch_model()
    c.build_ours()
    c.start_chain()
    c.rpc("evm_setIntervalMining", [0])
    c.rpc("evm_setAutomine", [True])
    c.rpc("anvil_setBlockTimestampInterval", [0])
    pw = c.RUN / "pw"
    pw.write_text(os.urandom(24).hex())
    pw.chmod(0o600)
    for who in ("deployer", "N", "H", "W", "P", "G", "Keeper", "K1", "K2", "V1", "V2", "V3"):
        c.new_key(who)
    for who in ("N", "H", "P", "G", "Keeper", "K1", "K2", "V1", "V2", "V3"):
        rewards.KEYS[who] = c.keystore(who, pw)
    treasury = "0x" + os.urandom(20).hex()
    ADDR["Treasury"], NAMES[treasury.lower()] = treasury, "Treasury"
    first_block = c.block_number()
    bb.deploy_full()
    vector = c.CASES["ledger-authority"]
    fixture = vector["inputs"]
    calc = rewards.calculate("ledger", fixture)
    for who in ("N", "V1", "V2", "V3"):
        c.send(KEY[who], C["Registry"], "register()")
    start_explorer(first_block)
    n, w = ADDR["N"], ADDR["W"]

    def stake(who, *args):
        code, _ = bb.lp2_stake(who, pw, *args)
        if code != 0:
            raise c.Failed(f"{who}'s {args[0]} exited {code}")

    stake("N", "set-payout", n, w)
    cut = next(step["cut_millionths"] for step in fixture["steps"] if step["action"] == "set_cut")
    stake("N", "set-cut", n, cut)
    for step in fixture["steps"]:
        if step["action"] == "bond":
            h, amount = step["holder"], step["amount_wei"]
            c.send(KEY["deployer"], C["TestLPT"], "transfer(address,uint256)", ADDR[h], amount)
            rewards.bond(h, step["node"], int(amount))
            if h != "H":
                c.send(KEY[h], C["Scores"], "optIn(address)", ADDR[h])
    face = c.ticket_face()
    c.fund("P", next(int(s["deposit_micro_usdc"]) for s in fixture["steps"] if s["action"] == "fund"), face, "N")

    pool = c.MANIFEST["pool"]
    bb.T0 = bb.later()
    seeded = bb.seed(int(pool["seed_usdc_micro_usdc"]), int(pool["seed_lpt_wei"]), 0)

    def start_node():
        c.start_orchestrator("orchestrator-N", "N", node_bin, rewards.KEYS["N"], pw, c.PORT["orch"], c.PORT["orch_metrics"])

    c.start_model(model)
    start_node()
    c.start_signer("P", node_bin, rewards.KEYS["P"], pw, c.PORT["signer_p"], c.PORT["signer_p_metrics"], [c.PORT["orch"]])
    stake("N", "set-operator", n, ADDR["K1"])
    stake("K1", "accept-operator", n)
    c.rpc("anvil_setBlockTimestampInterval", [1])
    c.rpc("evm_setAutomine", [False])
    c.rpc("evm_setIntervalMining", [1])
    rewards.start_keeper("--parameters-address", C["Parameters"])

    def earn(round_, before_job=None):
        rewards.to_round(round_)
        count = len(c.settlements()) + 1
        c.check(f"round {round_}: validators seated", rewards.members_at(round_),
                [] if round_ == 2 else ["V1", "V2", "V3"])
        stake_inputs = [step for step in fixture["steps"] if step["action"] == "bond"]
        for step in stake_inputs:
            staked = c.call(C["StakeLedger"], "stakeAt(address,uint256)", ADDR[step["node"]], round_)
            c.check(f"round {round_}: {step['node']} stake in wei", staked, int(step["amount_wei"]))
        c.check(f"round {round_}: total stake in wei",
                c.call(C["StakeLedger"], "totalStakeAt(uint256)", round_),
                sum(int(step["amount_wei"]) for step in stake_inputs))
        if before_job:
            before_job()
        c.rpc("evm_setIntervalMining", [0])
        paid_job()
        c.mine(41)
        rewards.wait(f"round {round_}'s redemption in the pool", lambda: pooled_from("N"))
        c.rpc("evm_mine", [])
        c.rpc("evm_setIntervalMining", [1])
        settled = c.settlements()
        c.check("one ticket settled for the job", len(settled), count)
        actual = settled[-1]
        c.check("ticket settles in its earning round at full face",
                (actual["round"], actual["gross"]), (round_, face))
        c.check("N is the only node with recognized fees in the round",
                (c.call(C["Broker"], "feesOf(uint256,address)", round_, n),
                 c.call(C["Broker"], "totalFees(uint256)", round_)), (face, face))

    def seat_validators():
        # Round-1 bonds count from round 2. Seats first count in round 3.
        for validator in ("V1", "V2", "V3"):
            c.send(KEY["deployer"], C["Scores"], "seat(address)", ADDR[validator])

    earn(2, seat_validators)
    earn(3)
    stake("K1", "set-operator", n, ADDR["K2"])
    stake("K2", "accept-operator", n)
    for old in ("K1", "N"):
        for args in (("claim", n, "3"), ("set-payout", n, ADDR[old]), ("set-cut", n, "1"),
                     ("set-operator", n, ADDR[old]), ("accept-operator", n)):
            code, out = bb.lp2_stake(old, pw, *args)
            c.check(f"{old}'s {args[0]} refused: NOT_AUTHORIZED",
                    (code, out.splitlines()[-1].split()[:2]), (3, ["refused:", "NOT_AUTHORIZED"]))
        for signature, args in (("optIn(address)", (n,)), ("optOut(address)", (n,)),
                                ("vote(address,address,uint256)", (n, ADDR["V1"], 0))):
            result, = c.rpc_batch([("eth_call", [{"from": ADDR[old], "to": C["Scores"],
                                                 "data": c.calldata(signature, *args)}, "latest"])])
            c.check(f"{old}'s simulated {signature} call refused: NOT_AUTHORIZED",
                    result.get("error", {}).get("data", "")[:10], c.sel("NOT_AUTHORIZED()"))
    rewards.to_round(4)
    emission = next(s["values"] for s in fixture["steps"] if s.get("group") == "2")
    code, out = bb.lp2_params("G", pw, "announce", "emission", *emission)
    c.check("G announced in round 4", c.current_round(), 4)
    c.check("announcement counts from round 11", (code, "counts from round 11" in out), (0, True))
    earn(5)
    rewards.to_round(6)
    for args in (("announce", "unbonding", "42"), ("withdraw", "unbonding")):
        code, _ = bb.lp2_params("G", pw, *args)
        c.check(" ".join(args), code, 0)
    c.check("withdrawn unbonding change never counts",
            c.words(C["Parameters"], "valuesAt(uint256,uint256)", 7, 13),
            c.words(C["Parameters"], "valuesAt(uint256,uint256)", 7, 6))
    code, out = bb.lp2_params("G", pw, "announce", "0", "100")
    c.check("round length refused: NOT_ADJUSTABLE", (code, "NOT_ADJUSTABLE" in out), (3, True))

    def claim(earned, expected):
        rewards.to_round(earned + int(c.PARAMS["reward_delay_rounds"]))
        before = c.call(C["TestLPT"], "balanceOf(address)", w)
        stake("K2", "claim", n, str(earned))
        paid = c.call(C["TestLPT"], "balanceOf(address)", w) - before
        rewards.side(f"Round {earned}: W receives LPT", paid, expected, c.lpt)

    claim(2, int(calc["lpt_wei"]["W"]))
    round3 = [dict(s) for s in fixture["steps"] if int(s["step"]) <= 17]
    round3[-1].update(round="3", created_round="3")
    round3.append({"step": "18", "round": "10", "action": "claim", "caller": "N", "node": "N",
                   "earned_round": "3", "score": "1000000"})
    claim(3, int(c.calculator(round3)["lpt_wei"]["W"]))
    # Age the observation while keeping the round clock on its block schedule.
    now = int(c.rpc("eth_getBlockByNumber", ["latest", False])["timestamp"], 16)
    c.rpc("evm_setNextBlockTimestamp", [now + int(c.MANIFEST["buyback"]["twap_seconds"])])
    earn(11)
    rewards.wait("keeper buys from the pool", lambda: c.logs(C["Buyback"], bb.BOUGHT))
    claim(5, int(calc["lpt_wei"]["W5"]))

    # Keep the buyback from drawing down the network share in the recovery view.
    c.stop("keeper")
    # A buy sent just before the stop is still in the pool. Let it be mined
    # before the recovery view reads its starting balances.
    rewards.wait("the keeper's last transaction mined", lambda: not pooled_from("Keeper"))
    check_buys(seeded)
    recover(start_node, face)
    rewards.start_keeper()
    claim(11, int(calc["lpt_wei"]["W11"]))
    check_share_mints(fixture)
    check_explorer(fixture, calc)
    return c.finish(started, "Full system: parameter change, key rotation, recovery and buyback")


def check_share_mints(fixture):
    steps = fixture["steps"]
    mint_steps = [step for step in steps if step["action"] == "mint_shares"]
    members = {step["earned_round"]: rewards.members_at(int(step["earned_round"]))
               for step in mint_steps}
    # Use the announcement event only to locate each share-mint transaction.
    transactions = {str(int(event["topics"][1], 16)): event["transactionHash"]
                    for event in c.logs(C["Claims"], "RoundSharesMinted(uint256,uint256,uint256)")}
    transfer = c.topic("Transfer(address,address,uint256)")
    recipients = {C["Claims"].lower(): "Claims", ADDR["Treasury"].lower(): "Treasury"}
    recipients.update({ADDR[member].lower(): member for group in members.values() for member in group})
    for step in mint_steps:
        earned = step["earned_round"]
        prior = steps[:steps.index(step)]
        inputs = {**fixture, "steps": prior, "members": members}
        before = rewards.calculate("ledger", inputs)["lpt_wei"]
        after = rewards.calculate("ledger", {**inputs, "steps": [*prior, step]})
        expected = {recipient: int(balance) - int(before.get(recipient, 0))
                    for recipient, balance in after["lpt_wei"].items()
                    if int(balance) != int(before.get(recipient, 0))}
        minted = {}
        tx = transactions.get(earned)
        if tx:
            for log in c.receipt(tx)["logs"]:
                topics = log["topics"]
                if (log["address"].lower() == C["TestLPT"].lower() and topics[0] == transfer
                        and int(topics[1], 16) == 0):
                    address = "0x" + topics[2][-40:].lower()
                    recipient = recipients.get(address, address)
                    minted[recipient] = minted.get(recipient, 0) + int(log["data"], 16)
        treasury = minted.pop("Treasury", 0)
        rewards.side(f"Round {earned}: treasury receives LPT",
                treasury, expected.pop("Treasury", 0), c.lpt)
        rewards.side(f"Round {earned}: validators receive LPT",
                sum(minted.values()), sum(expected.values()), c.lpt)
        c.check(f"Round {earned}: validator mints by recipient (wei)", minted, expected)
