"""The demo's fast checks: no chain, no node, seconds.

The demo writes its run as a `ledger` case. These checks feed the case
writer each frozen ledger case's own steps, with its expected figures as
the "chain views", and require the frozen case back exactly: the same steps,
the same figures, the same shape. So the case the demo writes says what the
chain showed, in the form the calculator reads. They also check that the
manifest the demo reads its flags from names every stand-in and gives the
shared examples' ticket.
"""
import copy
import json
import unittest
from pathlib import Path

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


class Manifest(unittest.TestCase):
    def test_every_stand_in_is_named(self):
        # S-09 section 6, phase 5: the stand-ins the manifest must name.
        text = " ".join(f"{s['stand_in']} {s['value']}" for s in MANIFEST["stand_ins"]).lower()
        for words in (["local chain"], ["mockusdc"], ["--wei-per-usd 1000000"], ["2^256 - 2", "price + 1"],
                      ["--max-ticket-ev", "--max-batch-ev", "micro-usdc"], ["50-block rounds"], ["tiny model"],
                      ["no governance"], ["no buyback"]):
            for w in words:
                self.assertIn(w, text)

    def test_the_manifest_gives_the_shared_examples_ticket(self):
        price = MANIFEST["price"]
        demo = next(c for c in LEDGER if c["id"] == "ledger-demo")
        face = int(demo["inputs"]["steps"][2]["face_micro_usdc"])
        self.assertEqual(int(float(price["job_price_usd"]) * int(price["wei_per_usd"]) + 0.5) + 1, face)
        self.assertEqual(int(price["ticket_win_prob"]), 2**256 - 2)
        self.assertEqual(price["wei_per_usd"], "1000000")
        self.assertEqual(MANIFEST["chain"]["round_length_blocks"], int(VECTORS["parameters"]["round_length_blocks"]))
        self.assertEqual(MANIFEST["rulebook"], VECTORS["rulebook"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
