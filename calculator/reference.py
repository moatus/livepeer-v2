"""Independent integer implementation of rulebook october-main-text-v1.

Only parameters and inputs enter these functions; expected vector outputs do not.
Amounts returned by calculate() are Python integers in the rulebook's units.
"""

from dataclasses import dataclass
from functools import cache


MILLION = 1_000_000


def integer(value: str) -> int:
    """Read the vectors' unsigned decimal strings without rounding or coercion."""
    if not isinstance(value, str) or not value or any(c not in "0123456789" for c in value):
        raise ValueError(f"expected an unsigned decimal string, got {value!r}")
    return int(value)


def boolean(value: bool) -> bool:
    if type(value) is not bool:
        raise ValueError(f"expected a boolean, got {value!r}")
    return value


@dataclass(frozen=True)
class Parameters:
    emission_base_wei: int
    emission_half_life_rounds: int
    reward_delay_rounds: int
    node_cut_millionths: int
    node_emission_share_millionths: int
    validator_emission_share_millionths: int
    treasury_emission_share_millionths: int

    round_length_blocks: int = 50
    ticket_validity_rounds: int = 2
    release_wait_rounds: int = 3
    claim_window_rounds: int | None = None
    unbonding_period_rounds: int = 21
    default_reward_cut_millionths: int = 0
    validator_set_size: int = 3

    @classmethod
    def from_strings(cls, values: dict) -> "Parameters":
        parameters = cls(**{key: integer(value) for key, value in values.items()})
        if parameters.emission_half_life_rounds == 0:
            raise ValueError("emission half-life must be positive")
        return parameters


@dataclass(frozen=True)
class Node:
    name: str
    fees: int
    stake: int
    score: int


def read_nodes(inputs: dict) -> list[Node]:
    nodes = [Node(n["node"], integer(n["fees_micro_usdc"]),
                  integer(n["stake_wei"]), integer(n["score"]))
             for n in inputs["nodes"]]
    if len({node.name for node in nodes}) != len(nodes):
        raise ValueError("duplicate node in earning round")
    return nodes


@cache
def ceiling(base: int, half_life: int, round_number: int) -> int:
    """R-05: largest c with c**half_life * 2**round_number <= base**half_life."""
    if base < 0 or half_life <= 0 or round_number < 0:
        raise ValueError("ceiling needs nonnegative base/round and positive half-life")
    halvings, residue = divmod(round_number, half_life)
    if halvings >= base.bit_length():
        return 0
    # R-05 permits computing one residue, then shifting by whole half-lives.
    # For integer c**half_life, comparison with this floored target is exact.
    target = (base ** half_life) >> residue
    low, high = 0, base + 1
    while high - low > 1:
        middle = (low + high) // 2
        if middle ** half_life <= target:
            low = middle
        else:
            high = middle
    return low >> halvings


def emission(parameters: Parameters, round_number: int) -> int:
    return ceiling(parameters.emission_base_wei,
                   parameters.emission_half_life_rounds, round_number)


def fixed_shares(parameters: Parameters, cap: int) -> dict:
    """R-07, R-10: each non-node share has its own single floor division."""
    return {
        "validators_wei": cap * parameters.validator_emission_share_millionths // MILLION,
        "treasury_wei": cap * parameters.treasury_emission_share_millionths // MILLION,
    }


def reward(parameters: Parameters, cap: int, node: Node,
           total_fees: int, total_stake: int, score: int) -> int:
    """R-08, R-19: compare exact shares, then floor the complete product once."""
    if not 0 <= score <= MILLION:
        raise ValueError("reward score must be between 0 and 1000000")
    if total_fees == 0 or total_stake == 0:
        return 0
    if node.fees * total_stake <= node.stake * total_fees:
        numerator, denominator = node.fees, total_fees
    else:
        numerator, denominator = node.stake, total_stake
    return (cap * parameters.node_emission_share_millionths * numerator * score
            // (MILLION * denominator * MILLION))


def calculate(kind: str, inputs: dict, parameters: Parameters,
              rounds: dict[str, dict]) -> dict:
    """Calculate one case using only inputs; rounds maps IDs to earning inputs.

    A ceiling returns both its ceiling and fixed shares. A round returns total
    minting only when all scores are full: D-21 leaves withheld rewards open.
    A claim is a stateless calculation against the supplied already-paid flag.
    """
    if kind == "ledger":
        return ledger(inputs, parameters)

    if kind == "settlement":
        gross = integer(inputs["gross_micro_usdc"])
        if not boolean(inputs["payee_registered"]):
            return {"code": "NOT_REGISTERED"}
        node = gross * parameters.node_cut_millionths // MILLION
        return {"node_micro_usdc": node, "network_micro_usdc": gross - node,
                "recognized_micro_usdc": gross}

    if kind == "ceiling":
        cap = emission(parameters, integer(inputs["round"]))
        return {"ceiling_wei": cap, **fixed_shares(parameters, cap)}

    if kind == "round":
        nodes = read_nodes(inputs)
        cap = emission(parameters, integer(inputs["round"]))
        total_fees = sum(node.fees for node in nodes)
        total_stake = sum(node.stake for node in nodes)
        paid = {node.name: reward(parameters, cap, node, total_fees, total_stake, node.score)
                for node in nodes}
        result = {"paid_wei": paid, **fixed_shares(parameters, cap)}
        if all(node.score == MILLION for node in nodes):
            result["minted_wei"] = (sum(paid.values()) + result["validators_wei"]
                                    + result["treasury_wei"])
        return result

    if kind == "claim":
        earned = rounds[inputs["earned_in"]]
        earning_round = integer(earned["round"])
        claim_round = integer(inputs["claim_round"])
        score = integer(inputs["score"])
        already_paid = boolean(inputs["already_paid"])
        refusals = []
        if claim_round < earning_round + parameters.reward_delay_rounds:
            refusals.append("CLAIM_TOO_EARLY")
        if (parameters.claim_window_rounds is not None and
                claim_round > earning_round + parameters.reward_delay_rounds + parameters.claim_window_rounds):
            refusals.append("CLAIM_EXPIRED")
        if already_paid:
            refusals.append("ALREADY_CLAIMED")
        if score > MILLION:
            refusals.append("SCORE_OUT_OF_RANGE")
        if len(refusals) > 1 and parameters.claim_window_rounds is None:
            raise ValueError("R-12 does not order simultaneous refusals: " + ", ".join(refusals))
        if refusals:
            return {"code": refusals[0]}
        nodes = read_nodes(earned)
        matching = [node for node in nodes if node.name == inputs["node"]]
        if len(matching) != 1:
            raise ValueError("claim node is absent from earning round")
        return {"paid_wei": reward(parameters, emission(parameters, earning_round),
                                   matching[0], sum(n.fees for n in nodes),
                                   sum(n.stake for n in nodes), score)}

    raise ValueError(f"unknown case kind: {kind!r}")


def ledger(inputs: dict, parameters: Parameters) -> dict:
    """R-01/03/04/21/22: replay a fresh account ledger from inputs alone."""
    steps = inputs["steps"]
    previous_round = 0
    tickets = {}
    for s in steps:
        round_number = integer(s["round"])
        if round_number < previous_round:
            raise ValueError(f"ledger input error at step {s['step']}: round decreases")
        previous_round = round_number
        if s["action"] == "observe" and not s.get("node"):
            raise ValueError(f"ledger input error at step {s['step']}: observe requires a node")
        if s["action"] == "redeem":
            details = (s["payer"], s["node"], integer(s["face_micro_usdc"]),
                       integer(s["created_round"]))
            if s["ticket"] in tickets and tickets[s["ticket"]] != details:
                raise ValueError(f"ledger input error at step {s['step']}: ticket details changed")
            tickets[s["ticket"]] = details
    payers = {s["payer"] for s in steps if "payer" in s}
    deposits = dict.fromkeys(payers, 0)
    earmarks = {p: {} for p in payers}
    paid = {s[key]: 0 for s in steps for key in ("node", "payout") if key in s}
    fees = {}
    for s in steps:
        if s["action"] == "fund" and "node" in s:
            earmarks[s["payer"]][s["node"]] = 0
        if s["action"] == "redeem":
            fees.setdefault(s["round"], {})[s["node"]] = 0
    released = dict.fromkeys(payers, 0)
    payouts, pending = {}, {}
    used = set()
    observed, refused = [], []
    network = 0

    def balance(payer, node):
        return deposits[payer] if node is None else earmarks[payer].get(node, 0)

    def debit(payer, node, amount):
        if node is None:
            deposits[payer] -= amount
        elif amount:
            earmarks[payer][node] -= amount
        key = (payer, node)
        if key in pending:
            requested, ready = pending[key]
            remaining = min(requested, balance(payer, node))
            if remaining:
                pending[key] = (remaining, ready)
            else:
                del pending[key]

    for s in steps:
        action = s["action"]
        round_number = integer(s["round"])
        node, payer = s.get("node"), s.get("payer")
        key = (payer, node)
        code = None
        if action == "register":
            payouts.setdefault(node, node)
        elif action == "set_payout":
            if node not in payouts:
                code = "NOT_REGISTERED"
            else:
                payouts[node] = s["payout"]
        elif action == "fund":
            deposit = integer(s["deposit_micro_usdc"])
            earmark = integer(s["earmark_micro_usdc"])
            if earmark and node not in payouts:
                code = "NOT_REGISTERED"
            else:
                deposits[payer] += deposit
                if earmark:
                    earmarks[payer][node] += earmark
        elif action == "request_release":
            amount = integer(s["amount_micro_usdc"])
            if amount > balance(payer, node):
                code = "AMOUNT_TOO_LARGE"
            elif amount:
                pending[key] = (amount, round_number + parameters.release_wait_rounds)
            else:
                pending.pop(key, None)
        elif action == "release":
            amount, ready = pending.get(key, (0, 0))
            if not amount or round_number < ready:
                code = "RELEASE_NOT_READY"
            else:
                del pending[key]
                debit(payer, node, amount)
                released[payer] += amount
        elif action == "observe":
            observed.append({"step": s["step"],
                             "deposit_micro_usdc": max(0, deposits[payer] - pending.get((payer, None), (0, 0))[0]),
                             "claimable_micro_usdc": max(0, balance(payer, node) - pending.get(key, (0, 0))[0])})
        elif action == "redeem":
            created = integer(s["created_round"])
            if created > round_number:
                raise ValueError("R-22 gives no stable code for a future ticket")
            gross = min(integer(s["face_micro_usdc"]), deposits[payer] + balance(payer, node))
            reasons = []
            if node not in payouts:
                reasons.append("NOT_REGISTERED")
            if s["ticket"] in used:
                reasons.append("TICKET_USED")
            if round_number > created + parameters.ticket_validity_rounds:
                reasons.append("TICKET_EXPIRED")
            if not gross:
                reasons.append("NOTHING_TO_PAY")
            if len(reasons) > 1:
                raise ValueError("rulebook does not order simultaneous ledger refusals")
            if reasons:
                code = reasons[0]
            else:
                from_deposit = min(gross, deposits[payer])
                debit(payer, None, from_deposit)
                debit(payer, node, gross - from_deposit)
                cut = gross * parameters.node_cut_millionths // MILLION
                paid[payouts[node]] += cut
                network += gross - cut
                fees[s["round"]][node] += gross
                used.add(s["ticket"])
        else:
            raise NotImplementedError(f"ledger action: {action!r}")
        if code:
            refused.append({"step": s["step"], "code": code})
    return {"deposits_micro_usdc": deposits, "earmarks_micro_usdc": earmarks,
            "paid_micro_usdc": paid, "released_micro_usdc": released,
            "network_micro_usdc": network, "fees_micro_usdc": fees,
            "total_fees_micro_usdc": {r: sum(nodes.values()) for r, nodes in fees.items()},
            "observed": observed, "refused": refused}
