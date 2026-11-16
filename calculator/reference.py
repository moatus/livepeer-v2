"""Independent integer implementation of rulebook october-main-text-v0.

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
        if already_paid:
            refusals.append("ALREADY_CLAIMED")
        if score > MILLION:
            refusals.append("SCORE_OUT_OF_RANGE")
        if len(refusals) > 1:
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
