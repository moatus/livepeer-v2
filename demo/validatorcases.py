"""Turn what the scripted validators sent, what Claims minted and what the
chain's views show into `validators` and `validator-pay` cases (rulebook
v2, the shared examples).

Pure functions only, like `ledgercase.py`: `stage3.py` reads the chain and
hands the figures in; this file puts them in the form the calculator reads.
The one figure it works out is a split's dust, its pot less the parts
Claims states, which no view gives.
"""
from __future__ import annotations

import ledgercase


def stake_entries(stakes: dict[int, dict[str, int]]) -> list[dict]:
    """`stakes` gives, for each on-chain round, each node's stake at the
    start of it. A node is named in the first entry of the first round that
    gives it, and again only from a round in which its stake changed: a
    `validators` case's stakes hold until a later entry names the node."""
    out, last = [], {}
    for r in sorted(stakes):
        changed = {n: v for n, v in sorted(stakes[r].items()) if n not in last or last[n] != v}
        if changed:
            out.append({"from_round": str(r), "stake_wei": {n: str(v) for n, v in changed.items()}})
            last.update(changed)
    return out


def validators_case(case_id: str, ordered: list[dict], stakes: dict[int, dict[str, int]],
                    unregistered: list[str] | None = None) -> dict:
    """`ordered` is `ledgercase.order`'s numbering of the steps: `opt_in`,
    `opt_out`, `seat`, `unseat` and `vote` as sent, each with its `code` if
    it was refused, and `observe` steps with what they were `shown`: the
    members of the round and the target's score."""
    inputs = {"stakes": stake_entries(stakes), "steps": ledgercase.steps(ordered)}
    if unregistered:
        inputs = {"unregistered": list(unregistered), **inputs}
    observed = [{"step": r["step"], "members": sorted(r["shown"][0]), "score": str(r["shown"][1])}
                for r in ordered if r["fields"]["action"] == "observe"]
    refused = [{"step": r["step"], "code": r["code"]} for r in ordered if r.get("code")]
    return {"id": case_id, "kind": "validators", "inputs": inputs,
            "expected": {"observed": observed, "refused": refused}}


def validator_pay_case(case_id: str, round_: int, members: list[tuple[str, int]], splits: dict, paid: dict[str, int],
                       to_treasury: int, shown: dict) -> dict:
    """One round's validator mint. `round_` is the on-chain round R; the case
    counts the emission index r = R - 1. `members` is R's members with their
    stake at the start of R; `splits`, for each member whose split is
    stated, its cut and its holders' bonds at the start of R. `paid` and
    `to_treasury` are what the mint paid each member and what it sent the
    treasury beyond R-10's treasury amount; `shown`, for each split, the cut,
    the pot and each holder's part as Claims states them."""
    inputs = {"round": str(round_ - 1), "members": [{"node": n, "stake_wei": str(s)} for n, s in members]}
    expected = {"paid_wei": {n: str(paid[n]) for n, _ in members}, "to_treasury_wei": str(to_treasury)}
    if splits:
        inputs["splits"] = {n: {"cut_millionths": str(c), "bonds_wei": {h: str(b) for h, b in bonds.items()}}
                            for n, (c, bonds) in splits.items()}
        expected["splits"] = {}
        for n, (cut, pot, parts) in shown.items():
            expected["splits"][n] = {"cut_wei": str(cut), "pot_wei": str(pot),
                                     "parts_wei": {h: str(p) for h, p in parts.items()},
                                     "dust_wei": str(pot - sum(parts.values()))}
    return {"id": case_id, "kind": "validator-pay", "inputs": inputs, "expected": expected}


def document(vectors: dict, cases: list[dict]) -> dict:
    """A shared examples file holding these cases, which ./calculator/check reads."""
    return {**{k: v for k, v in vectors.items() if k != "cases"}, "cases": cases}
