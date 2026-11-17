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
import copy
import json
import shutil
import socket
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import common
import ledgercase

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = json.loads((ROOT / "deployments" / "stage-2-local.json").read_text())
VECTORS = json.loads((ROOT / MANIFEST["vectors"]).read_text())
LEDGER = [c for c in VECTORS["cases"] if c["kind"] == "ledger"]
MANIFEST3 = json.loads((ROOT / "deployments" / "stage-3-local.json").read_text())
VECTORS3 = json.loads((ROOT / MANIFEST3["vectors"]).read_text())
LEDGER3 = [c for c in VECTORS3["cases"] if c["kind"] == "ledger"]


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


STAGE2_STAND_INS = (["local chain"], ["mockusdc"], ["--wei-per-usd 1000000"], ["2^256 - 2", "price + 1"],
                    ["--max-ticket-ev", "--max-batch-ev", "micro-usdc"], ["50-block rounds"], ["tiny model"],
                    ["no governance"], ["no buyback"])

# S-05 phase 6: the stand-ins stage 3 adds. Each is its own entry, found by
# the start of its name, with the words its value must hold. They are
# checked entry by entry because the names overlap ("test LPT" is also in
# the genesis supply's name).
STAGE3_STAND_INS = {"test lpt": ["testlpt.sol", "only claims"],
                    "genesis supply of 10,000,000 test lpt": ["outside every round's issuance"],
                    "treasury address": ["nobody holds"],
                    "scripted validators": ["programs/validators", "v1, v2 and v3"]}


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


if __name__ == "__main__":
    unittest.main(verbosity=1)
