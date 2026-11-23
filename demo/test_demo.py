"""The demo's fast checks: no chain, no node, seconds.

The demo writes its run as a `ledger` case. These checks feed the case
writer each frozen ledger case's own steps, with its expected figures as
the "chain views", and require the frozen case back exactly: the same steps,
the same figures, the same shape. So the case the demo writes says what the
chain showed, in the form the calculator reads. They also check that each
manifest the demo reads its flags from names every stand-in and gives the
shared examples' ticket, and that the node's programs start safely: nothing
they read is rewritten after they start, and one that exits at startup is
named with its log.
"""
import ast
import copy
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock

import common
import ledgercase
import validatorcases

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = json.loads((ROOT / "deployments" / "stage-2-local.json").read_text())
VECTORS = json.loads((ROOT / MANIFEST["vectors"]).read_text())
LEDGER = [c for c in VECTORS["cases"] if c["kind"] == "ledger"]
MANIFEST3 = json.loads((ROOT / "deployments" / "stage-3-local.json").read_text())
VECTORS3 = json.loads((ROOT / MANIFEST3["vectors"]).read_text())
LEDGER3 = [c for c in VECTORS3["cases"] if c["kind"] == "ledger"]
VALIDATORS3 = [c for c in VECTORS3["cases"] if c["kind"] == "validators"]
PAY3 = [c for c in VECTORS3["cases"] if c["kind"] == "validator-pay"]


class RPCBatchTests(unittest.TestCase):
    def test_batches_split_in_order_and_retry_when_rate_limited(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        # The first batch is refused with 429, then with one error object
        # for the whole batch (status 200), as some endpoints rate-limit.
        batches, refusals = [], iter(["429", "error", None, None])

        def open_request(req, *, timeout):
            batch = json.loads(req.data)
            batches.append(batch)
            if batch[0]["method"] == "eth_sendRawTransaction":
                raise common.urllib.error.URLError("connection reset")
            refusal = next(refusals)
            if refusal == "429":
                raise common.urllib.error.HTTPError(common.RPC, 429, "rate limited", {}, None)
            response.read.return_value = json.dumps({"error": {"code": -32005, "message": "limit"}} if refusal else [
                {"id": call["id"], "result": call["params"][0]} for call in reversed(batch)
            ])
            return response

        with mock.patch.object(common, "BATCH", 2), \
             mock.patch.object(common.urllib.request, "urlopen", side_effect=open_request), \
             mock.patch.object(common.time, "sleep") as sleep:
            results = common.rpc_batch([("eth_getBlockByNumber", [n, True]) for n in range(3)])
            self.assertEqual([r["result"] for r in results], [0, 1, 2])
            self.assertEqual([len(batch) for batch in batches], [2, 2, 2, 1])
            self.assertEqual([c.args[0] for c in sleep.call_args_list], list(common.RETRY_WAITS[:2]))
            # A send whose answer was lost may have landed: it is not sent again.
            with self.assertRaises(common.urllib.error.URLError):
                common.rpc("eth_sendRawTransaction", ["0x"])
        self.assertEqual(len(batches), 5)


def records_and_views(case):
    """A run that sent exactly the case's steps, one block each, and whose
    chain views read exactly the case's expected figures."""
    exp = case["expected"]
    observed = {o["step"]: (int(o.get("deposit_micro_usdc", o.get("stake_wei"))),
                            int(o.get("claimable_micro_usdc", o.get("total_stake_wei")))) for o in exp["observed"]}
    refused = {r["step"]: r["code"] for r in exp["refused"]}
    records = []
    for seq, s in enumerate(reversed(case["inputs"]["steps"])):  # arrival order must not matter
        fields = {k: v for k, v in s.items() if k not in ("step", "round")}
        r = {"fields": fields, "block": int(s["step"]), "round": int(s["round"]), "seq": seq,
             "index": None if s["action"] in ("observe", "observe_stake") else 0}
        if s["step"] in observed:
            r["shown"] = observed[s["step"]]
        if s["step"] in refused:
            r["code"] = refused[s["step"]]
        records.append(r)
    wallet = {k: int(v) for k, v in exp["paid_micro_usdc"].items()}
    wallet.update({k: int(v) for k, v in exp["released_micro_usdc"].items()})
    views = {"deposit": {k: int(v) for k, v in exp["deposits_micro_usdc"].items()},
             "earmark": {(p, n): int(v) for p, ns in exp["earmarks_micro_usdc"].items() for n, v in ns.items()},
             "wallet": wallet, "network": int(exp["network_micro_usdc"]),
             "fees": {(r, n): int(v) for r, ns in exp["fees_micro_usdc"].items() for n, v in ns.items()},
             "total_fees": {r: int(v) for r, v in exp["total_fees_micro_usdc"].items()}}
    if "lpt_wei" in exp:
        views.update({"lpt": {k: int(v) for k, v in exp["lpt_wei"].items()},
                      "shares": {r: (int(v["validators_wei"]), int(v["treasury_wei"])) for r, v in exp["shares_wei"].items()},
                      "minted_for_round": {r: int(v) for r, v in exp["minted_for_round_wei"].items()},
                      "lpt_minted": int(exp["lpt_minted_wei"])})
    return records, views


class CaseWriter(unittest.TestCase):
    def test_every_frozen_ledger_case_is_written_back_exactly(self):
        # v2 holds v1's nine ledger cases unchanged and nine with LPT steps.
        self.assertEqual(len(LEDGER), 9)
        self.assertEqual(len(LEDGER3), 18)
        self.assertEqual([c for c in LEDGER3 if c["id"] in {d["id"] for d in LEDGER}], LEDGER)
        for frozen in LEDGER3:
            with self.subTest(case=frozen["id"]):
                records, views = records_and_views(frozen)
                ordered = ledgercase.order(records)
                need = ledgercase.views_needed(ledgercase.steps(ordered))
                written = ledgercase.case(frozen["id"], ordered, need, views, frozen["inputs"].get("members"))
                self.assertEqual(json.dumps(written, sort_keys=True), json.dumps(frozen, sort_keys=True))

    def test_a_read_comes_after_the_transactions_of_its_block(self):
        records = [{"fields": {"action": "observe", "payer": "P", "node": "N"}, "block": 5, "index": None, "round": 2, "seq": 0},
                   {"fields": {"action": "register", "node": "N"}, "block": 5, "index": 3, "round": 2, "seq": 1}]
        self.assertEqual([r["fields"]["action"] for r in ledgercase.order(records)], ["register", "observe"])

    def test_a_transaction_is_in_the_round_at_its_index(self):
        # A redemption at index 0 of a block whose index 1
        # starts round 3 was put in round 3, the round after the whole block.
        started = [{"blockNumber": hex(b), "transactionIndex": hex(i), "topics": ["0x", hex(r)]}
                   for b, i, r in ((4, 0, 1), (10, 1, 2), (20, 1, 3))]
        records = [{"block": b, "index": i} for b, i in ((20, 0), (20, 1), (20, 2), (20, None), (10, 0), (19, None))]
        with mock.patch.object(common, "logs", lambda *a, **k: started), \
                mock.patch.dict(common.C, {"RoundClock": "0x" + "22" * 20}):
            common.set_rounds(records)
        self.assertEqual([r["round"] for r in records], [2, 3, 3, 3, 1, 2])

    def test_a_round_going_backwards_is_refused(self):
        records = [{"fields": {"action": "register", "node": "N"}, "block": 5, "index": 0, "round": 3, "seq": 0},
                   {"fields": {"action": "register", "node": "M"}, "block": 6, "index": 0, "round": 2, "seq": 1}]
        with self.assertRaises(ValueError):
            ledgercase.order(records)

    def test_one_changed_figure_is_a_difference(self):
        frozen = next(c for c in LEDGER if c["id"] == "ledger-release")
        cycle = next(c for c in LEDGER3 if c["id"] == "ledger-cycle")
        for case, path in [(frozen, p) for p in (("network_micro_usdc",), ("earmarks_micro_usdc", "P", "N"),
                                                  ("observed", 1, "claimable_micro_usdc"), ("refused", 0, "code"),
                                                  ("fees_micro_usdc", "11", "N"))] + \
                          [(cycle, p) for p in (("lpt_wei", "Claims"), ("shares_wei", "2", "treasury_wei"),
                                                ("minted_for_round_wei", "2"), ("lpt_minted_wei",),
                                                ("observed", 2, "stake_wei"))]:
            with self.subTest(case=case["id"], path=path):
                changed = copy.deepcopy(case["expected"])
                target = changed
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = target[path[-1]] + "1"
                diffs = ledgercase.differences(changed, case["expected"])
                self.assertEqual(len(diffs), 1, diffs)
        self.assertEqual(ledgercase.differences(frozen["expected"], copy.deepcopy(frozen["expected"])), [])


def validators_records(case):
    """A run that sent exactly the case's steps, one block each, whose
    observe steps were shown the case's figures, and whose stake views read
    the case's stakes in every round."""
    shown = {o["step"]: (o["members"], int(o["score"])) for o in case["expected"]["observed"]}
    refused = {r["step"]: r["code"] for r in case["expected"]["refused"]}
    records = []
    for seq, s in enumerate(reversed(case["inputs"]["steps"])):  # arrival order must not matter
        r = {"fields": {k: v for k, v in s.items() if k not in ("step", "round")}, "block": int(s["step"]),
             "round": int(s["round"]), "seq": seq, "index": None if s["action"] == "observe" else 0}
        if s["step"] in shown:
            r["shown"] = shown[s["step"]]
        if s["step"] in refused:
            r["code"] = refused[s["step"]]
        records.append(r)
    entries = case["inputs"]["stakes"]
    stakes, now = {}, {}
    for r in range(int(entries[0]["from_round"]), max(int(s["round"]) for s in case["inputs"]["steps"]) + 1):
        for e in entries:
            if int(e["from_round"]) == r:
                now.update({n: int(v) for n, v in e["stake_wei"].items()})
        stakes[r] = dict(now)
    return records, stakes


class ValidatorCaseWriter(unittest.TestCase):
    """S-06 phase 5: the demo writes its run as `validators` and
    `validator-pay` cases. Fed each frozen case's own steps and figures as
    the chain's, the writers must give the frozen case back exactly."""

    def test_every_frozen_validators_case_is_written_back_exactly(self):
        self.assertEqual(len(VALIDATORS3), 9)
        for frozen in VALIDATORS3:
            with self.subTest(case=frozen["id"]):
                records, stakes = validators_records(frozen)
                written = validatorcases.validators_case(frozen["id"], ledgercase.order(records), stakes,
                                                         frozen["inputs"].get("unregistered"))
                self.assertEqual(json.dumps(written, sort_keys=True), json.dumps(frozen, sort_keys=True))

    def test_every_frozen_validator_pay_case_is_written_back_exactly(self):
        self.assertEqual(len(PAY3), 7)
        for frozen in PAY3:
            with self.subTest(case=frozen["id"]):
                i, e = frozen["inputs"], frozen["expected"]
                members = [(m["node"], int(m["stake_wei"])) for m in i["members"]]
                splits = {n: (int(s["cut_millionths"]), {h: int(b) for h, b in s["bonds_wei"].items()})
                          for n, s in i.get("splits", {}).items()}
                shown = {n: (int(s["cut_wei"]), int(s["pot_wei"]), {h: int(p) for h, p in s["parts_wei"].items()})
                         for n, s in e.get("splits", {}).items()}
                written = validatorcases.validator_pay_case(
                    frozen["id"], int(i["round"]) + 1, members, splits,
                    {n: int(v) for n, v in e["paid_wei"].items()}, int(e["to_treasury_wei"]), shown)
                self.assertEqual(json.dumps(written, sort_keys=True), json.dumps(frozen, sort_keys=True))

    def test_a_stake_is_named_again_only_when_it_changes(self):
        got = validatorcases.stake_entries({3: {"V1": 5, "V2": 0}, 4: {"V1": 5, "V2": 0}, 5: {"V1": 5, "V2": 3, "V4": 6},
                                            6: {"V1": 5, "V2": 3, "V4": 6}})
        self.assertEqual(got, [{"from_round": "3", "stake_wei": {"V1": "5", "V2": "0"}},
                               {"from_round": "5", "stake_wei": {"V2": "3", "V4": "6"}}])

    def test_section_one_claims_share_one_earning_round(self):
        """S-06 section 1's four claims are four nodes' rewards earned in one
        round. The demo's table rows, and the rule the run checks them by."""
        tree = ast.parse((Path(common.__file__).parent / "stage3.py").read_text())
        rows = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                    and any(getattr(t, "id", None) == "CLAIM_ROWS" for t in n.targets))
        self.assertEqual(validatorcases.one_earning_round(list(rows)), 4)
        # Phase 5's first build: N, M and K from round 2, L from round 4.
        self.assertIsNone(validatorcases.one_earning_round([("A", "N", 2), ("B", "M", 2), ("C", "L", 4), ("D", "K", 2)]))
        self.assertIsNone(validatorcases.one_earning_round([("A", "N", 4), ("B", "M", 4), ("C", "N", 4), ("D", "K", 4)]))
        self.assertIsNone(validatorcases.one_earning_round([("A", "N", 4), ("B", "M", 4), ("C", "L", 4)]))


STAGE2_STAND_INS = (["local chain"], ["mockusdc"], ["--wei-per-usd 1000000"], ["2^256 - 2", "price + 1"],
                    ["--max-ticket-ev", "--max-batch-ev", "micro-usdc"], ["50-block rounds"], ["tiny model"],
                    # S-10 phase 3: governance is Parameters whose authority's
                    # key nobody holds, so every value is the deployment's.
                    ["parameters with no key", "nobody holds", "every value is the deployment's"], ["no buyback"])

# S-05 phase 6: the stand-ins stage 3 adds. Each is its own entry, found by
# the start of its name, with the words its value must hold. They are
# checked entry by entry because the names overlap ("test LPT" is also in
# the genesis supply's name).
STAGE3_STAND_INS = {"test lpt": ["testlpt.sol", "only claims"],
                    "genesis supply of 10,000,000 test lpt": ["outside every round's issuance"],
                    "treasury address": ["nobody holds"],
                    "scripted validators": ["programs/validators", "v1, v2 and v3", "and v4 with 6,000", "holder h"],
                    # S-06 phase 5: N = 3 is named as its own stand-in.
                    "validatorsetsize 3": ["at most 3"]}


class CaseDocuments(unittest.TestCase):
    def test_a_run_case_is_written_beside_every_shared_example(self):
        # The calculator's check refuses a file that lacks the rulebook's
        # named examples, so a run's cases go beside them; a case with a
        # shared example's id replaces it.
        vectors = {"rulebook": "r", "cases": [{"id": "a"}, {"id": "b"}]}
        run = {"id": "run", "kind": "ledger"}
        self.assertEqual(ledgercase.document(vectors, [run])["cases"], [{"id": "a"}, {"id": "b"}, run])
        two = [{"id": "a", "x": 2}, run]
        doc = ledgercase.document(vectors, two)
        self.assertEqual(doc["cases"], [{"id": "b"}] + two)
        self.assertEqual(doc["rulebook"], "r")
        self.assertEqual(len(vectors["cases"]), 2)


class Manifest(unittest.TestCase):
    def test_every_stand_in_is_named(self):
        # S-09 section 6, phase 5: the stand-ins the manifest must name, in
        # stage 2's manifest and, as stage 3 keeps them, in stage 3's.
        for manifest in (MANIFEST, MANIFEST3):
            text = " ".join(f"{s['stand_in']} {s['value']}" for s in manifest["stand_ins"]).lower()
            for words in STAGE2_STAND_INS:
                for w in words:
                    with self.subTest(manifest=manifest["testnet"], word=w):
                        self.assertIn(w, text)

    def test_every_stage_3_stand_in_is_named(self):
        entries = [(s["stand_in"].lower(), s["value"].lower()) for s in MANIFEST3["stand_ins"]]
        for name, words in STAGE3_STAND_INS.items():
            with self.subTest(stand_in=name):
                found = [value for n, value in entries if n.startswith(name)]
                self.assertEqual(len(found), 1, f"no stand-in named {name!r} in stage-3-local.json")
                for w in words:
                    self.assertIn(w, found[0])

    def test_the_genesis_supply_named_is_the_one_deployed(self):
        named = next(s["stand_in"] for s in MANIFEST3["stand_ins"] if s["stand_in"].lower().startswith("genesis supply"))
        supply = int(MANIFEST3["lpt"]["genesis_supply_wei"])
        self.assertIn(f"{supply // 10**MANIFEST3['lpt']['decimals']:,}", named)
        self.assertEqual(supply % 10**MANIFEST3["lpt"]["decimals"], 0)

    def test_the_manifest_gives_the_shared_examples_ticket(self):
        price = MANIFEST["price"]
        demo = next(c for c in LEDGER if c["id"] == "ledger-demo")
        face = int(demo["inputs"]["steps"][2]["face_micro_usdc"])
        self.assertEqual(int(float(price["job_price_usd"]) * int(price["wei_per_usd"]) + 0.5) + 1, face)
        self.assertEqual(int(price["ticket_win_prob"]), 2**256 - 2)
        self.assertEqual(price["wei_per_usd"], "1000000")
        self.assertEqual(MANIFEST["chain"]["round_length_blocks"], int(VECTORS["parameters"]["round_length_blocks"]))
        self.assertEqual(MANIFEST["rulebook"], VECTORS["rulebook"])

    def test_the_public_template_invents_no_address_and_runs_stage_3(self):
        # The public testnet's template names no address before a
        # run fills it, runs what stage 3 runs, and names its stand-ins.
        public = json.loads((ROOT / "deployments" / "public-arbitrum-sepolia.json").read_text())
        self.assertEqual([c["address"] for c in public["contracts"]], [None] * len(MANIFEST3["contracts"]))
        self.assertFalse(public["deployment"]["deployed"])
        self.assertEqual(public["chain"]["chain_id"], 421614)
        for key in ("node", "model", "price", "signer_limits", "lpt", "rulebook", "vectors"):
            with self.subTest(key=key):
                self.assertEqual(public[key], MANIFEST3[key])
        self.assertEqual([{k: v for k, v in c.items() if k != "address"} for c in public["contracts"]], MANIFEST3["contracts"])
        named = {s["stand_in"].lower() for s in public["stand_ins"]}
        for name in ("mockusdc", "test lpt", "scripted validators", "tiny model", "every ticket wins",
                     "fixed rate --wei-per-usd 1000000", "50-block rounds", "keeper", "treasury address",
                     "parameters with no key"):
            with self.subTest(stand_in=name):
                self.assertTrue(any(n.startswith(name) for n in named))



class BuybackCase(unittest.TestCase):
    """demo/buyback.py's case writer and stage 4's manifest. Importing the
    demo reads stage 4's manifest into common's shared state, so each test
    puts that state back afterwards."""

    def setUp(self):
        for d in (common.MANIFEST, common.VECTORS, common.CASES, common.PARAMS):
            p = mock.patch.dict(d)
            p.start()
            self.addCleanup(p.stop)
        common.use("stage-4-local.json")
        import buyback
        self.b = buyback
        self.walk = common.CASES["buyback-walk"]

    def run_of(self, case):
        """A case as the demo records it: each step with its own bought or
        refused entry."""
        bought = {e["step"]: e for e in case["expected"]["bought"]}
        refused = {e["step"]: e for e in case["expected"]["refused"]}
        run = []
        for s in case["inputs"]["steps"]:
            done = dict(s, shown="what the chain showed")
            for key, entries in (("bought", bought), ("refused", refused)):
                if s["step"] in entries:
                    done[key] = {k: v for k, v in entries[s["step"]].items() if k != "step"}
            run.append(done)
        return run

    def test_the_walk_is_written_back_exactly(self):
        final = {k: int(v) for k, v in self.walk["expected"].items() if k not in ("bought", "refused")}
        got = self.b.case_of("buyback-walk", int(self.walk["inputs"]["lpt_supply_wei"]), self.run_of(self.walk), final)
        self.assertEqual(got, self.walk)

    def test_only_not_due_is_told_from_the_walks_start(self):
        self.assertEqual(self.b.shifted({"code": "NOT_DUE", "args": ["1003600"]}, 1_000_000),
                         {"code": "NOT_DUE", "args": ["3600"]})
        other = {"code": "TWAP_NOT_READY", "args": ["0", "3600"]}
        self.assertEqual(self.b.shifted(other, 1_000_000), other)

    def test_the_manifest_gives_the_walks_values_and_pool(self):
        m = common.MANIFEST
        self.assertEqual(m["rulebook"], common.VECTORS["rulebook"])
        for k, v in m["buyback"].items():
            with self.subTest(value=k):
                self.assertEqual(v, common.PARAMS["buyback_" + k])
        seed = next(s for s in self.walk["inputs"]["steps"] if s["action"] == "seed")
        self.assertEqual((m["pool"]["seed_usdc_micro_usdc"], m["pool"]["seed_lpt_wei"]),
                         (seed["usdc_micro_usdc"], seed["lpt_wei"]))
        self.assertEqual(m["lpt"]["genesis_supply_wei"], self.walk["inputs"]["lpt_supply_wei"])

    def test_the_manifest_names_parameters_and_g(self):
        m = common.MANIFEST
        names = [c["contract"] for c in m["contracts"]]
        self.assertLess(names.index("Parameters"), names.index("Buyback"))
        named = [s["stand_in"].lower() for s in m["stand_ins"]]
        self.assertTrue(any(n.startswith("g, the governance stand-in") for n in named))


class PublicState(unittest.TestCase):
    """demo/public-run's state directory holds keys and sends money, so a
    symlink must not lead a key elsewhere, and two runs must not share it."""

    def setUp(self):
        import public
        self.public = public
        self.dir = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.dir, True)
        for patch in (mock.patch.object(public, "STATE_DIR", self.dir), mock.patch.object(public, "LOCK", None),
                      mock.patch.object(common, "KEEP_FDS", ())):
            patch.start()
            self.addCleanup(patch.stop)

    def test_a_symlinked_keys_directory_is_refused(self):
        elsewhere = self.dir / "elsewhere"
        elsewhere.mkdir()
        (self.dir / "keys").symlink_to(elsewhere)
        with self.assertRaises(common.NotRun) as e:
            self.public.private(self.dir / "keys" / "N" / "N")
        self.assertIn("keys in the state directory is a symlink", str(e.exception))
        self.assertEqual(self.public.private(self.dir / "pw"), self.dir / "pw")

    def test_a_second_run_on_the_state_directory_is_refused(self):
        self.public.lock()
        self.addCleanup(self.public.LOCK.close)
        held = self.public.LOCK
        with self.assertRaises(common.NotRun) as e:
            self.public.lock()
        self.public.LOCK.close()
        self.public.LOCK = held
        self.assertIn(f"in use by process {os.getpid()}", str(e.exception))

    @staticmethod
    def reap(group: int) -> None:
        # os.killpg, never the kill program: procps-ng 4.0.4 reads
        # `kill -9 -<group>` as kill(-1, SIGKILL), every process the user has.
        try:
            os.killpg(group, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def test_a_program_left_by_a_killed_run_keeps_the_directory(self):
        # A run killed with SIGKILL must not free the directory while its
        # keeper runs on, or a second run could start a second keeper with
        # the same key: the program holds the lock itself.
        here = Path(__file__).resolve().parent
        run = subprocess.Popen([sys.executable, "-B", "-c", textwrap.dedent(f"""
            import sys, time
            sys.path.insert(0, {str(here)!r})
            from pathlib import Path
            import common, public
            public.STATE_DIR = common.RUN = Path({str(self.dir)!r})
            (common.RUN / "logs").mkdir()
            public.lock()
            common.start("keeper", ["sleep", "60"])
            public.note_holders()
            print(common.PROCS["keeper"].pid, flush=True)
            time.sleep(60)
        """)], stdout=subprocess.PIPE, text=True)
        child = int(run.stdout.readline())
        self.addCleanup(self.reap, child)
        os.kill(run.pid, signal.SIGKILL)
        run.wait()
        os.kill(child, 0)  # the run is gone, its keeper is not
        with self.assertRaises(common.NotRun) as e:
            self.public.lock()
        self.addCleanup(self.public.LOCK.close)
        self.assertIn(f"programs it started: keeper {child}", str(e.exception))
        os.killpg(child, signal.SIGKILL)
        end = time.time() + 10
        while time.time() < end:
            try:
                os.kill(child, 0)
                time.sleep(0.05)
            except ProcessLookupError:
                break
        self.public.LOCK.close()
        self.public.lock()  # once the keeper is gone, the directory is free
        self.addCleanup(self.public.LOCK.close)


class PublicSteps(unittest.TestCase):
    """A step of demo/public-run is sent once: a run stopped while sending it
    does not send it again, since it may have landed."""

    def setUp(self):
        import public
        self.public = public
        state = {"done": {"deploy": {"contracts": {"Registry": "0xc", "Broker": "0xd"}}}, "sent": [], "sending": None}
        for patch in (mock.patch.dict(public.STATE, state, clear=True), mock.patch.object(public, "save", lambda: None),
                      mock.patch.object(common, "SENT", state["sent"])):
            patch.start()
            self.addCleanup(patch.stop)

    def test_a_step_is_sent_once_and_its_sends_are_kept(self):
        self.public.step("bond", lambda: common.SENT.extend(["0xa", "0xb"]) and None)
        self.public.step("bond", lambda: self.fail("sent again"))
        self.assertEqual((self.public.STATE["done"]["bond"], self.public.STATE["sending"]), (True, None))
        self.assertEqual(self.public.STATE["sent"], ["0xa", "0xb"])

    def test_a_step_stopped_while_sending_is_refused(self):
        with self.assertRaises(RuntimeError):
            self.public.step("fund", lambda: (_ for _ in ()).throw(RuntimeError("killed")))
        self.assertEqual(self.public.STATE["sending"], "fund")
        with self.assertRaises(common.NotRun) as e:
            self.public.step("fund", lambda: self.fail("sent again"))
        self.assertIn("stopped while sending step 'fund'", str(e.exception))

    def test_a_chain_without_the_contracts_is_refused(self):
        codes = {"0xc": "0x60", "0xd": "0x"}
        with mock.patch.object(common, "rpc_batch", lambda calls: [{"result": codes[p[0]]} for _, p in calls]):
            with self.assertRaises(common.NotRun) as e:
                self.public.check_deployment()
        self.assertIn("no code at this state's Broker", str(e.exception))


class PublicStage4(unittest.TestCase):
    """demo/public-run stage-4 and serve, with no chain: the template they
    read, mission 11's manifest beside it, and the refusals that come before
    anything is made or sent. Importing them reads stage 4's shared examples
    into common's shared state, so each test puts that state back."""

    def setUp(self):
        for d in (common.MANIFEST, common.VECTORS, common.CASES, common.PARAMS):
            p = mock.patch.dict(d)
            p.start()
            self.addCleanup(p.stop)
        import public4
        self.p4 = public4
        self.t4 = json.loads((ROOT / "deployments" / public4.TEMPLATE).read_text())
        self.local4 = json.loads((ROOT / "deployments" / "stage-4-local.json").read_text())
        self.dir = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_the_stage_4_template_invents_no_address_and_runs_stage_4(self):
        t = self.t4
        self.assertEqual([c["address"] for c in t["contracts"]], [None] * len(self.local4["contracts"]))
        self.assertEqual([{k: v for k, v in c.items() if k not in ("address", "checks")} for c in t["contracts"]],
                         [{k: v for k, v in c.items() if k != "checks"} for c in self.local4["contracts"]])
        self.assertFalse(t["deployment"]["deployed"])
        self.assertEqual(t["chain"]["chain_id"], 421614)
        for key in ("node", "model", "price", "signer_limits", "lpt", "pool", "buyback", "rulebook", "vectors"):
            with self.subTest(key=key):
                self.assertEqual(t[key], self.local4[key])
        named = {s["stand_in"].lower() for s in t["stand_ins"]}
        for name in ("role accounts made by the run", "mockusdc", "test lpt", "scripted validators", "tiny model",
                     "every ticket wins", "50-block rounds", "keeper", "treasury address", "g, the governance stand-in",
                     "testpool", "the deployment's buyback values", "faucet account", "one paid job"):
            with self.subTest(stand_in=name):
                self.assertTrue(any(n.startswith(name) for n in named))

    def test_the_faucet_share_and_the_change_fit_the_deployment(self):
        import public
        t, params = self.t4, json.loads((ROOT / self.t4["vectors"]).read_text())["parameters"]
        faucet, decimals = int(t["faucet"]["lpt_wei"]), t["lpt"]["decimals"]
        self.assertEqual(faucet % 10**decimals, 0)
        spare = int(t["lpt"]["genesis_supply_wei"]) - int(t["pool"]["seed_lpt_wei"]) - public.N_BOND - public.V_BOND
        self.assertTrue(0 < faucet <= spare)
        self.assertIn(f"{faucet // 10**decimals:,} test LPT", t["faucet"]["note"])
        # Unbonding, group 7: D-47's range is 1 to 840 rounds, and a change
        # must differ from the deployment's value to show anything.
        change = t["parameter_change"]
        self.assertEqual((change["group"], change["name"]), (7, "unbonding"))
        self.assertTrue(1 <= int(change["values"][0]) <= 840)
        self.assertNotEqual(change["values"][0], params["unbonding_period_rounds"])

    def test_mission_11s_manifest_reads_as_the_stage_3_template_filled_in(self):
        # A dry read of the dated manifest of the first public deployment,
        # which the stage 4 template stands beside and does not replace: what
        # the stage 3 path reads from a template is the same in it, and its
        # contracts are the stage 3 template's, each with its address, but
        # Parameters, which that deployment predates.
        dated = json.loads((ROOT / "deployments" / "public-arbitrum-sepolia-2026-11-21.json").read_text())
        stage3 = json.loads((ROOT / "deployments" / "public-arbitrum-sepolia.json").read_text())
        for key in ("node", "model", "price", "signer_limits", "lpt", "rulebook", "vectors", "chain"):
            with self.subTest(key=key):
                self.assertEqual(dated[key], stage3[key])
        self.assertEqual([c["contract"] for c in dated["contracts"]],
                         [c["contract"] for c in stage3["contracts"] if c["contract"] != "Parameters"])
        self.assertTrue(all(c["address"].startswith("0x") and len(c["address"]) == 42 for c in dated["contracts"]))
        self.assertEqual((dated["deployment"]["deployed"], dated["deployment"]["chain_id"]), (True, 421614))
        self.assertTrue(dated["about"].endswith(f"from {stage3['testnet']}.json. The addresses, accounts and "
                                                "source commit are this run's."))

    def env(self, **set_):
        keys = ("LP2_RPC_URL", "LP2_DEPLOYER_KEYSTORE", "LP2_DEPLOYER_PASSWORD_FILE", "LP2_AUTHORITY_KEYSTORE",
                "LP2_AUTHORITY_PASSWORD_FILE", "LP2_STATE_DIR")
        env = {k: v for k, v in os.environ.items() if k not in keys}
        return mock.patch.dict(os.environ, {**env, **set_}, clear=True)

    def test_the_stage_4_run_refuses_to_start_without_the_authority_keystore(self):
        key, pw = self.dir / "deployer.json", self.dir / "deployer.pw"
        key.write_text("{}")
        pw.write_text("x")
        state = self.dir / "state"
        given = dict(LP2_RPC_URL="http://127.0.0.1:1", LP2_DEPLOYER_KEYSTORE=str(key),
                     LP2_DEPLOYER_PASSWORD_FILE=str(pw), LP2_STATE_DIR=str(state))
        for extra, want in (({}, "LP2_AUTHORITY_KEYSTORE is not set"),
                            ({"LP2_AUTHORITY_KEYSTORE": str(self.dir / "none.json")}, "LP2_AUTHORITY_KEYSTORE does not name a file"),
                            ({"LP2_AUTHORITY_KEYSTORE": str(key)}, "LP2_AUTHORITY_PASSWORD_FILE is not set")):
            with self.subTest(want=want), self.env(**given, **extra):
                with self.assertRaises(common.NotRun) as e:
                    self.p4.main()
                self.assertIn(want, str(e.exception))
                self.assertFalse(state.exists(), "nothing is made before the authority's keystore is checked")

    def test_serve_refuses_a_state_directory_no_run_handed_over(self):
        with self.env(LP2_STATE_DIR=str(self.dir)):
            with self.assertRaises(common.NotRun) as e:
                self.p4.serve()
        self.assertIn("holds no services.json", str(e.exception))

    def test_the_handover_names_the_role_keys_and_no_key_of_the_owners(self):
        import public
        owner = {"deployer_keystore": "/owner/deployer.json", "deployer_password_file": "/owner/deployer.pw",
                 "authority": "0x" + "a1" * 20}
        accounts = {w: "0x" + f"{i:02x}" * 20 for i, w in enumerate(self.p4.ROLES, 1)}
        with mock.patch.dict(public.STATE, owner, clear=True), mock.patch.dict(common.ADDR, accounts), \
                mock.patch.dict(common.C, {"Controller": "0x" + "cc" * 20}), \
                mock.patch.object(self.p4, "executor_in_force", lambda: "0x" + "bb" * 20), \
                mock.patch.object(self.p4, "AUTHORITY", ["--keystore-file", "/owner/authority.json"]):
            svc = self.p4.services_doc(31337)
        self.assertEqual(svc["keys"], {w: f"keys/{w}/{w}" for w in self.p4.ROLES})
        self.assertNotIn("deployer", svc["keys"])
        text = json.dumps(svc)
        for secret in ("/owner/deployer.json", "/owner/deployer.pw", "/owner/authority.json"):
            self.assertNotIn(secret, text)
        self.assertEqual((svc["executor"], svc["accounts"]), ("0x" + "bb" * 20, accounts))


class PagedLogs(unittest.TestCase):
    """Hosted endpoints cap eth_getLogs' range, so the logs are read
    LOG_PAGE blocks at a time, giving the same logs."""

    def test_the_logs_are_read_in_pages(self):
        asked = []
        found = {3: "a", 4999: "b", 5000: "c", 12345: "d"}

        def chain(calls):
            out = []
            for i, (m, p) in enumerate(calls):
                if m == "eth_getBlockByNumber":
                    out.append({"id": i, "result": {"number": hex(12345)}})
                else:
                    a, b = int(p[0]["fromBlock"], 16), int(p[0]["toBlock"], 16)
                    asked.append((a, b))
                    out.append({"id": i, "result": [{"blockNumber": hex(n), "data": d} for n, d in sorted(found.items())
                                                    if a <= n <= b]})
            return out
        with mock.patch.object(common, "rpc_batch", chain), mock.patch.object(common, "FROM_BLOCK", 3), \
                mock.patch.dict(common.SELECTORS, {"E()": "0x" + "ee" * 32}):
            self.assertEqual([lg["data"] for lg in common.logs("0xc", "E()")], ["a", "b", "c", "d"])
            self.assertEqual(asked, [(3, 5002), (5003, 10002), (10003, 12345)])
            asked.clear()
            with mock.patch.object(common, "LOG_PAGE", 1000):
                self.assertEqual([lg["data"] for lg in common.logs("0xc", "E()", 5000)], ["a", "b", "c"])
            self.assertEqual(len(asked), 5)
            self.assertEqual(asked[-1], (4003, 5000))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Startup(unittest.TestCase):
    """The node reads its chain URL from a file once, as it starts; an empty
    file leaves the URL unset and it exits with 'on-chain payment requires
    ...'. The demo once rewrote that file before starting each orchestrator,
    so an orchestrator already starting could read it between the truncate
    and the write: N and M exited, and the run failed at its first job."""

    def setUp(self):
        self.run_dir = Path(tempfile.mkdtemp())
        (self.run_dir / "logs").mkdir()
        self.addCleanup(shutil.rmtree, self.run_dir, True)
        self.addCleanup(common.PROCS.clear)

    def test_no_file_a_node_reads_is_written_after_it_starts(self):
        events, waited = [], []
        real_write = Path.write_text

        def write_text(path, *args, **kwargs):
            events.append(("write", str(path)))
            return real_write(path, *args, **kwargs)

        def start(name, argv, cwd=None):
            events.append(("start", name, argv))

        common.use("stage-3-local.json")
        run = self.run_dir
        with mock.patch.object(Path, "write_text", write_text), mock.patch.object(common, "RUN", run), \
                mock.patch.object(common, "RPC_FILE", run / "rpc-url"), mock.patch.object(common, "start", start), \
                mock.patch.object(common, "wait_up", lambda name, metrics: waited.append(name)), \
                mock.patch.object(common, "block_number", lambda: 1), \
                mock.patch.dict(common.C, {"Controller": "0x" + "11" * 20}):
            common.start_chain()
            for i, n in enumerate("NMK"):
                common.start_orchestrator(f"orchestrator-{n}", n, run, run / "key", run / "pw", 100 + 2 * i, 101 + 2 * i)
            common.start_signer("P", run, run / "key", run / "pw", 200, 201, [100, 102, 104])

        read_by: dict[str, str] = {}
        rewritten = []
        for e in events:
            if e[0] == "start":
                for arg in e[2]:
                    for part in arg.split("="):
                        if part.startswith(str(run)):
                            read_by.setdefault(part, e[1])
            elif e[1] in read_by:
                rewritten.append((e[1], read_by[e[1]]))
        self.assertEqual(rewritten, [], "a file was written after a process that reads it started")
        self.assertIn(str(run / "rpc-url"), read_by)
        self.assertEqual((run / "rpc-url").read_text(), common.RPC)
        self.assertEqual(waited, ["orchestrator-N", "orchestrator-M", "orchestrator-K", "signer-P"])

    def wait_on(self, script: str, seconds: float = 60):
        with mock.patch.object(common, "RUN", self.run_dir):
            common.start("orchestrator-N", ["sh", "-c", script])
            self.addCleanup(common.stop, "orchestrator-N")
            with self.assertRaises(common.Failed) as e:
                common.wait_up("orchestrator-N", free_port(), seconds)
        return str(e.exception)

    def test_a_node_that_exits_at_startup_is_named_with_its_log(self):
        began = time.time()
        said = self.wait_on("echo starting; echo 'on-chain payment requires --redeemer-db'; exit 1", seconds=30)
        self.assertLess(time.time() - began, 10, "an exit is reported at once, not after the wait runs out")
        self.assertIn("orchestrator-N exited with status 1 at startup", said)
        self.assertIn("on-chain payment requires --redeemer-db", said)
        self.assertIn(str(self.run_dir / "logs" / "orchestrator-N.log"), said)

    def test_a_node_that_never_answers_is_named_with_its_log(self):
        said = self.wait_on("echo still starting; exec sleep 30", seconds=1)
        self.assertIn("orchestrator-N did not answer /healthz within 1 s", said)
        self.assertIn("still starting", said)


class ContainerPayments(unittest.TestCase):
    """demo/container.py's payment check: the node is paid once in every
    round it was up for whole. Importing the demo reads stage 4's manifest
    into common's shared state, so each test puts that state back."""

    def setUp(self):
        for d in (common.MANIFEST, common.VECTORS, common.CASES, common.PARAMS):
            p = mock.patch.dict(d)
            p.start()
            self.addCleanup(p.stop)
        import container
        self.unpaid = container.unpaid

    def test_every_whole_round_paid_once_passes(self):
        # Joined in 2, killed in 5 and up again in 6, stopped in 11: rounds
        # 2, 5, 6 and 11 were partial, and need not have been paid.
        self.assertEqual(self.unpaid([3, 4, 7, 8, 9, 10], 2, (5, 6), 11), ([3, 4, 7, 8, 9, 10], [], []))
        self.assertEqual(self.unpaid([2, 3, 4, 5, 6, 7, 8, 9, 10, 11], 2, (5, 6), 11)[1:], ([], []))

    def test_a_missed_middle_round_is_found(self):
        self.assertEqual(self.unpaid([2, 3, 4, 5, 6, 7, 9, 10], 2, (5, 6), 11)[1], [8])
        self.assertEqual(self.unpaid([2, 4, 10], 2, (5, 6), 11)[1], [3, 7, 8, 9])

    def test_a_round_paid_twice_is_found(self):
        self.assertEqual(self.unpaid([3, 4, 7, 7, 8, 9, 10], 2, (5, 6), 11)[2], [7])


class ContainerPorts(unittest.TestCase):
    """programs/container/entrypoint compares ports as numbers: a port
    written with leading zeros is the same port, and anything that is not a
    port from 1 to 65535 is refused. Each run stops at the data folder,
    which does not exist, after it has logged the ports it chose."""

    def start(self, public: str, explorer: str | None = None) -> tuple[int, str]:
        env = {"PATH": os.environ["PATH"], "LP2_DATA": "/nonexistent/lp2-ports-test", "LP2_DEPLOYMENT": "/nonexistent",
               "LP2_RPC_URL": "http://127.0.0.1:1", "LP2_PUBLIC_URL": public}
        if explorer is not None:
            env["LP2_EXPLORER_LISTEN"] = explorer
        done = subprocess.run(["sh", str(ROOT / "programs" / "container" / "entrypoint")], env=env,
                              capture_output=True, text=True, timeout=30)
        return done.returncode, done.stdout + done.stderr

    def ports(self, public: str, explorer: str | None = None) -> str:
        code, said = self.start(public, explorer)
        line = next((x for x in said.splitlines() if " ports: " in x), None)
        self.assertIsNotNone(line, said)
        self.assertIn("/nonexistent/lp2-ports-test is not a folder", said)
        return line.split(" ports: ", 1)[1]

    def refused(self, public: str, explorer: str | None, why: str) -> None:
        code, said = self.start(public, explorer)
        self.assertEqual(code, 1, said)
        self.assertIn(why, said)
        self.assertNotIn(" ports: ", said)

    def test_a_port_with_leading_zeros_is_that_port(self):
        self.assertEqual(self.ports("http://127.0.0.1:09000"),
                         "the node on 0.0.0.0:9000, the explorer on 0.0.0.0:8080, the responder on 127.0.0.1:9001, "
                         "the node's metrics on 127.0.0.1:8936")
        self.assertEqual(self.ports("http://127.0.0.1:9000", "0.0.0.0:08936"),
                         "the node on 0.0.0.0:9000, the explorer on 0.0.0.0:8936, the responder on 127.0.0.1:9001, "
                         "the node's metrics on 127.0.0.1:8937")
        self.assertIn("the node on 0.0.0.0:443,", self.ports("https://node.example"))

    def test_the_explorers_port_is_refused_however_it_is_written(self):
        both = "both name port 49597"
        self.refused("http://127.0.0.1:49597", "0.0.0.0:49597", both)
        self.refused("http://127.0.0.1:49597", "0.0.0.0:049597", both)
        self.refused("http://127.0.0.1:0049597", "127.0.0.1:49597", both)
        self.refused("http://127.0.0.1:08080", None, "both name port 8080")

    def test_what_is_not_a_port_is_refused(self):
        for public in ("http://h:0", "http://h:000", "http://h:65536", "http://h:9000x", "http://h:+9000", "http://h:-1",
                       "http://h:99999999999999999999"):
            self.refused(public, None, "which is not a port number from 1 to 65535")
        for explorer in ("0.0.0.0:abc", "0.0.0.0:", "0.0.0.0:0", "0.0.0.0:70000"):
            self.refused("http://h:9000", explorer, "which is not a port number from 1 to 65535")
        self.refused("http://h:9000", "8080", "is not an address and port")


if __name__ == "__main__":
    unittest.main(verbosity=1)
