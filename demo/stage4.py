"""Stage 4 on a fresh chain, with real jobs and exact calculator checks."""
import os
import signal
import time

import stage3 as s3
import buyback as bb
import common as c

from common import ADDR, C, KEY, NAMES


def pool_from_node():
    pool = c.rpc("txpool_content", [])
    return [tx for bucket in ("pending", "queued")
            for sender, txs in pool[bucket].items() if sender.lower() == ADDR["N"].lower()
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
    s3.wait("N's redemption in the transaction pool", pool_from_node)
    tx, = pool_from_node()
    pending = tx["hash"]
    if tx["to"].lower() != C["Broker"].lower() or c.rpc("eth_getTransactionReceipt", [pending]) is not None:
        raise c.Failed("N's pooled transaction is not an unmined redemption")
    proc = c.PROCS.pop("orchestrator-N")
    os.killpg(proc.pid, signal.SIGKILL)
    c.check("SIGKILL while redemption pending", proc.wait(10), -signal.SIGKILL)
    c.rpc("anvil_dropTransaction", [pending])
    c.check("pending redemption dropped while N is down", c.rpc("eth_getTransactionByHash", [pending]), None)
    start_node()
    c.rpc("evm_setIntervalMining", [1])
    s3.wait("redemption recovered after restart", lambda: len(c.settlements()) > count, 420)
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
        s3.side(f"Restart: {label}", new - old, want, c.money)


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
    calc = s3.calculate("buyback", {"lpt_supply_wei": c.MANIFEST["lpt"]["genesis_supply_wei"], "steps": steps})
    c.check("Bought amounts agree with the calculator", observed, calc["bought"])


def main():
    started = time.time()
    c.open_log()
    c.say("Stage 4: announced parameters, operator rotation and settlement recovery")
    c.say("The unmodified node earns in mock USDC, and the keeper buys and burns test LPT.")
    c.preflight(["chain", "orch", "orch_metrics", "signer_p", "signer_p_metrics", "model"])
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
        s3.KEYS[who] = c.keystore(who, pw)
    treasury = "0x" + os.urandom(20).hex()
    ADDR["Treasury"], NAMES[treasury.lower()] = treasury, "Treasury"
    bb.deploy_stage4()
    vector = c.CASES["ledger-authority"]
    fixture = vector["inputs"]
    calc = s3.calculate("ledger", fixture)
    for who in ("N", "V1", "V2", "V3"):
        c.send(KEY[who], C["Registry"], "register()")
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
            s3.bond(h, step["node"], int(amount))
            if h != "H":
                c.send(KEY[h], C["Scores"], "optIn(address)", ADDR[h])
    face = c.ticket_face()
    c.fund("P", next(int(s["deposit_micro_usdc"]) for s in fixture["steps"] if s["action"] == "fund"), face, "N")

    pool = c.MANIFEST["pool"]
    bb.T0 = bb.later()
    seeded = bb.seed(int(pool["seed_usdc_micro_usdc"]), int(pool["seed_lpt_wei"]), 0)

    def start_node():
        c.start_orchestrator("orchestrator-N", "N", node_bin, s3.KEYS["N"], pw, c.PORT["orch"], c.PORT["orch_metrics"])

    c.start_model(model)
    start_node()
    c.start_signer("P", node_bin, s3.KEYS["P"], pw, c.PORT["signer_p"], c.PORT["signer_p_metrics"], [c.PORT["orch"]])
    stake("N", "set-operator", n, ADDR["K1"])
    stake("K1", "accept-operator", n)
    c.rpc("anvil_setBlockTimestampInterval", [1])
    c.rpc("evm_setAutomine", [False])
    c.rpc("evm_setIntervalMining", [1])
    s3.start_keeper("--buyback-address", C["Buyback"])

    def earn(round_, before_job=None):
        s3.to_round(round_)
        count = len(c.settlements()) + 1
        c.check(f"round {round_}: validators seated", s3.members_at(round_),
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
        s3.wait(f"round {round_}'s redemption in the pool", pool_from_node)
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
            c.check(f"{old}'s {signature} refused: NOT_AUTHORIZED",
                    result.get("error", {}).get("data", "")[:10], c.sel("NOT_AUTHORIZED()"))
    s3.to_round(4)
    emission = next(s["values"] for s in fixture["steps"] if s.get("group") == "2")
    code, out = bb.lp2_params("G", pw, "announce", "emission", *emission)
    c.check("G announced in round 4", c.current_round(), 4)
    c.check("announcement counts from round 11", (code, "counts from round 11" in out), (0, True))
    earn(5)
    s3.to_round(6)
    for args in (("announce", "unbonding", "42"), ("withdraw", "unbonding")):
        code, _ = bb.lp2_params("G", pw, *args)
        c.check(" ".join(args), code, 0)
    c.check("withdrawn unbonding change never counts",
            c.words(C["Parameters"], "valuesAt(uint256,uint256)", 7, 13),
            c.words(C["Parameters"], "valuesAt(uint256,uint256)", 7, 6))
    code, out = bb.lp2_params("G", pw, "announce", "0", "100")
    c.check("round length refused: NOT_ADJUSTABLE", (code, "NOT_ADJUSTABLE" in out), (3, True))

    def claim(earned, expected):
        s3.to_round(earned + int(c.PARAMS["reward_delay_rounds"]))
        before = c.call(C["TestLPT"], "balanceOf(address)", w)
        stake("K2", "claim", n, str(earned))
        paid = c.call(C["TestLPT"], "balanceOf(address)", w) - before
        s3.side(f"Round {earned}: W receives LPT", paid, expected, c.lpt)

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
    s3.wait("keeper buys from the pool", lambda: c.logs(C["Buyback"], bb.BOUGHT))
    claim(5, int(calc["lpt_wei"]["W5"]))

    # Keep the buyback from drawing down the network share in the recovery view.
    c.stop("keeper")
    check_buys(seeded)
    recover(start_node, face)
    s3.start_keeper()
    claim(11, int(calc["lpt_wei"]["W11"]))
    shares = {}
    for event in c.logs(C["Claims"], "RoundSharesMinted(uint256,uint256,uint256)"):
        earned = str(int(event["topics"][1], 16))
        shares[earned] = tuple(int(event["data"][2 + 64*i:66 + 64*i], 16) for i in range(2))
    c.say("  Round 2 has no seated validators, so its validator allocation goes to the treasury.")
    for earned, expected in calc["shares_wei"].items():
        for label, value in zip(("validators", "treasury"), shares.get(earned, (0, 0))):
            s3.side(f"Round {earned}: {label} allocation in LPT", value, int(expected[label + "_wei"]), c.lpt)
    return c.finish(started, "Stage 4: parameter change, key rotation, recovery and buyback")
