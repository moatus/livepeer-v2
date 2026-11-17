"""Turn what the demo sent, and what the chain shows, into a `ledger` case.

Pure functions only. `demo/run` records each step as it is sent (or, for a
node's redemption, as the chain shows it) and reads the chain's views; this
file puts the steps in chain order, says which views a case needs, writes
the case and compares two sets of figures. It never computes a figure.
"""
from __future__ import annotations


def order(records: list[dict]) -> list[dict]:
    """Number the steps in the order the chain applied them.

    Each record has `block`, `index` (the transaction's index in its block,
    or None for a read taken after the block), `round` and `fields` (the
    step's action and names). Records in one block keep their transaction
    order; a read comes after every transaction of its block.
    """
    def key(r):
        return (r["block"], r["index"] if r["index"] is not None else 1 << 62, r["seq"])
    ordered = sorted(records, key=key)
    for number, r in enumerate(ordered, 1):
        r["step"] = str(number)
    last = 0
    for r in ordered:
        if r["round"] < last:
            raise ValueError(f"step {r['step']}: the round went backwards ({last} to {r['round']})")
        last = r["round"]
    return ordered


def steps(ordered: list[dict]) -> list[dict]:
    return [{"step": r["step"], "round": str(r["round"]), **{k: str(v) for k, v in r["fields"].items()}}
            for r in ordered]


def views_needed(case_steps: list[dict]) -> dict:
    """The views a case's expected figures read: every payer's deposit and
    wallet, each earmark a fund step names, the wallet of every node and
    payout address named, the fee record of each round and node a redeem
    step names, and that round's total."""
    payers = sorted({s["payer"] for s in case_steps if "payer" in s})
    wallets = sorted({s[k] for s in case_steps for k in ("node", "payout") if k in s})
    earmarks = sorted({(s["payer"], s["node"]) for s in case_steps if s["action"] == "fund" and "node" in s})
    fees = sorted({(s["round"], s["node"]) for s in case_steps if s["action"] == "redeem"},
                  key=lambda rn: (int(rn[0]), rn[1]))
    return {"payers": payers, "wallets": wallets, "earmarks": earmarks, "fees": fees}


def expected(ordered: list[dict], need: dict, views: dict) -> dict:
    """The case's expected figures, every one read from the chain.

    `views` holds what was read after the last step: `deposit[payer]`,
    `earmark[(payer, node)]`, `wallet[name]`, `network`, `fees[(round,
    node)]` and `total_fees[round]`. A payer's wallet is what was released
    to it, since the demo mints a payer exactly what it funds.
    """
    earmarks = {p: {} for p in need["payers"]}
    for p, n in need["earmarks"]:
        earmarks[p][n] = str(views["earmark"][(p, n)])
    fees: dict = {}
    for r, n in need["fees"]:
        fees.setdefault(r, {})[n] = str(views["fees"][(r, n)])
    return {
        "deposits_micro_usdc": {p: str(views["deposit"][p]) for p in need["payers"]},
        "earmarks_micro_usdc": earmarks,
        "paid_micro_usdc": {w: str(views["wallet"][w]) for w in need["wallets"]},
        "released_micro_usdc": {p: str(views["wallet"][p]) for p in need["payers"]},
        "network_micro_usdc": str(views["network"]),
        "fees_micro_usdc": fees,
        "total_fees_micro_usdc": {r: str(views["total_fees"][r]) for r in fees},
        "observed": [{"step": r["step"], "deposit_micro_usdc": str(r["shown"][0]),
                      "claimable_micro_usdc": str(r["shown"][1])}
                     for r in ordered if r["fields"]["action"] == "observe"],
        "refused": [{"step": r["step"], "code": r["code"]} for r in ordered if r.get("code")],
    }


def case(case_id: str, ordered: list[dict], need: dict, views: dict) -> dict:
    return {"id": case_id, "kind": "ledger", "inputs": {"steps": steps(ordered)},
            "expected": expected(ordered, need, views)}


def document(vectors: dict, the_case: dict) -> dict:
    """A shared examples file holding one case, which ./calculator/check reads."""
    head = {k: v for k, v in vectors.items() if k != "cases"}
    return {**head, "cases": [the_case]}


def strings(value):
    if isinstance(value, dict):
        return {str(k): strings(v) for k, v in value.items()}
    if isinstance(value, list):
        return [strings(v) for v in value]
    return str(value) if type(value) is int else value


def flatten(figures: dict) -> dict:
    """Every figure of an expected block, keyed by a readable path."""
    out = {}
    for key, value in figures.items():
        if key in ("observed", "refused"):
            for item in value:
                for field, v in item.items():
                    if field != "step":
                        out[(key, f"step {item['step']}", field)] = v
            out[(key, "steps")] = ",".join(item["step"] for item in value)
        elif isinstance(value, dict):
            for k1, v1 in value.items():
                if isinstance(v1, dict):
                    for k2, v2 in v1.items():
                        out[(key, k1, k2)] = v2
                    if not v1:
                        out[(key, k1)] = "{}"
                else:
                    out[(key, k1)] = v1
        else:
            out[(key,)] = value
    return out


def differences(chain: dict, calculator: dict) -> list[tuple]:
    a, b = flatten(chain), flatten(calculator)
    return [(path, a.get(path, "absent"), b.get(path, "absent"))
            for path in sorted(set(a) | set(b), key=str) if a.get(path) != b.get(path)]
