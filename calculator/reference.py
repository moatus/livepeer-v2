"""Independent integer implementation of rulebook october-main-text-v4.

LPT replay models round-start stake/cuts, bonding, transfers, unbonding,
claims, minting and holder pots. It rejects overspending as invalid input.
Earning round zero gets NO_SUCH_ROUND; unregistered self-claims and self-cuts
get NOT_REGISTERED. Simultaneous refusals follow the rulebook units section
(D-26, D-38, D-39, D-40); only a withdrawal that is both ALREADY_CLAIMED and
NOT_CLAIMED remains an input error among simultaneous LPT refusals.

From v3, Settings holds R-25's announced changes and the R-05 schedule
triple, and Operators holds R-26's operator keys. Without an announcement or
a rotation they give every v0 to v2 case the deployment's values.

From v4, buyback() replays R-16: jobs add R-03's network share, a test pool
keeps v2's cumulative price, and the Buyback pokes and buys against it.

Only parameters and inputs enter these functions; expected vector outputs do not.
Amounts returned by calculate() are Python integers in the rulebook's units.
"""

from dataclasses import dataclass, replace
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
    notice_rounds: int = 7
    buyback_burn_ppm: int = MILLION
    buyback_treasury_ppm: int = 0
    buyback_validator_ppm: int = 0
    buyback_twap_seconds: int = 3600
    buyback_max_slippage_ppm: int = 10_000
    buyback_every_rounds: int = 1

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


GROUPS = {1: "FEE", 2: "EMISSION", 3: "SCHEDULE", 4: "REWARD_DELAY",
          5: "VALIDATOR_SET_SIZE", 6: "SCORE_SOURCE", 7: "UNBONDING",
          8: "BUYBACK_CUTS", 9: "BUYBACK_VALUES"}
STEPS = 840          # R-05: positions are 840ths of the deployment's half-life
LARGEST_DROP = 840   # D-49: one announcement drops at most one halving
LONGEST = 840        # D-47 (iii): reward delay, unbonding and a buy interval
LARGEST_SET = 33     # D-47 (ii)
ADDRESS_VALUES = {(6, 0), (9, 0)}


class Settings:
    """R-25: announced changes per group, and R-05's schedule triple.

    Each group keeps (counts_from, values) entries in order. The deployment's
    entry counts from round 0. An entry counting after the current round is
    the group's one pending change. Values are integers, or names for the
    score source and the pool.
    """

    def __init__(self, parameters: Parameters, authority=None, addresses=None):
        p = parameters
        if p.emission_half_life_rounds <= 0 or STEPS % p.emission_half_life_rounds:
            raise ValueError("the deployment's half-life must divide 840")
        self.p, self.authority = p, authority
        self.addresses = {"Scores": {"code": True},
                          "Pool": {"code": True, "pinned_pool_code": True,
                                   "token0": "MockUSDC", "token1": "TestLPT"},
                          **(addresses or {})}
        deployment = {
            1: (p.node_cut_millionths, MILLION - p.node_cut_millionths),
            2: (p.node_emission_share_millionths, p.validator_emission_share_millionths,
                p.treasury_emission_share_millionths),
            3: (0, p.emission_half_life_rounds, 1),
            4: (p.reward_delay_rounds,),
            5: (p.validator_set_size,),
            6: ("Scores",),
            7: (p.unbonding_period_rounds,),
            8: (p.buyback_burn_ppm, p.buyback_treasury_ppm, p.buyback_validator_ppm),
            9: ("Pool", p.buyback_twap_seconds, p.buyback_max_slippage_ppm,
                p.buyback_every_rounds),
        }
        self.history = {g: [(0, values)] for g, values in deployment.items()}

    def values_at(self, group: int, round_number: int) -> tuple:
        """The last change counting at or before the round."""
        return [v for counts_from, v in self.history[group] if counts_from <= round_number][-1]

    def pending(self, group: int, now: int):
        last = self.history[group][-1]
        return last if last[0] > now else None

    def position(self, round_number: int) -> int:
        """R-05: position + (R - start) x 840 / half-life, a whole number."""
        position, half_life, start = self.values_at(3, round_number)
        steps = (round_number - start) * STEPS
        if steps % half_life or position * half_life + steps < 0:
            raise ValueError(f"round {round_number} has no schedule position")
        return position + steps // half_life

    def ceiling_at(self, round_number: int) -> int:
        """C(R) of an on-chain round R: the deployment's ceiling at R's position."""
        return ceiling(self.p.emission_base_wei, STEPS, self.position(round_number))

    def for_round(self, round_number: int) -> Parameters:
        """The values a round reads, as the v2 parameter names hold them."""
        emission_shares = self.values_at(2, round_number)
        return replace(self.p,
                       node_cut_millionths=self.values_at(1, round_number)[0],
                       node_emission_share_millionths=emission_shares[0],
                       validator_emission_share_millionths=emission_shares[1],
                       treasury_emission_share_millionths=emission_shares[2],
                       reward_delay_rounds=self.values_at(4, round_number)[0],
                       validator_set_size=self.values_at(5, round_number)[0],
                       unbonding_period_rounds=self.values_at(7, round_number)[0])

    def read(self, group: int, raw: list) -> tuple:
        values = []
        for index, value in enumerate(raw):
            values.append(value if (group, index) in ADDRESS_VALUES else integer(value))
        return tuple(values)

    def refusal(self, caller, group: int, values: tuple, now: int):
        """R-25's checks in the units section's order; None when none applies."""
        if caller != self.authority:
            return "NOT_AUTHORIZED"
        if group not in GROUPS:
            return "NOT_ADJUSTABLE"
        in_force = self.values_at(group, now)
        if group == 3 and len(values) == 2 and values[1] > in_force[1]:
            return "SCHEDULE_RAISED"
        counts = {1: 2, 2: 3, 3: 2, 4: 1, 5: 1, 6: 1, 7: 1, 8: 3, 9: 4}
        if len(values) != counts[group]:
            return "VALUE_OUT_OF_RANGE"
        if group in (1, 2) and any(v > MILLION for v in values):
            return "VALUE_OUT_OF_RANGE"
        if group == 3:
            drop, half_life = values
            if drop > LARGEST_DROP or half_life == 0 or STEPS % half_life:
                return "VALUE_OUT_OF_RANGE"
        if group in (4, 7) and not 1 <= values[0] <= LONGEST:
            return "VALUE_OUT_OF_RANGE"
        if group == 5 and not in_force[0] <= values[0] <= LARGEST_SET:
            return "VALUE_OUT_OF_RANGE"
        if group == 6 and not self.addresses.get(values[0], {}).get("code", False):
            return "VALUE_OUT_OF_RANGE"
        if group == 8 and values != (MILLION, 0, 0):
            return "VALUE_OUT_OF_RANGE"
        if group == 9:
            _, twap, slippage, every = values
            if (not 1_800 <= twap <= 7_200 or not 5_000 <= slippage <= 50_000
                    or not 1 <= every <= LONGEST):
                return "VALUE_OUT_OF_RANGE"
        if group in (1, 2) and sum(values) != MILLION:
            return "IDENTITY_NOT_WHOLE"
        if group == 9:
            pool = self.addresses.get(values[0], {})
            if not (pool.get("pinned_pool_code", False)
                    and {pool.get("token0"), pool.get("token1")} == {"MockUSDC", "TestLPT"}):
                return "POOL_INVALID"
        return None

    def announce(self, caller, group: int, raw: list, now: int):
        """Returns (code, None) or (None, counts_from)."""
        values = self.read(group, raw) if group in GROUPS else tuple(raw)
        code = self.refusal(caller, group, values, now)
        if code:
            return code, None
        counts_from = now + self.p.notice_rounds
        if self.pending(group, now):
            self.history[group].pop()
        if group == 3:
            # With the pending change gone, the triple in force at E - 1 is
            # the one in force now, so a replaced change never moves q.
            before = counts_from - 1
            values = (self.position(before) + values[0], values[1], before)
        self.history[group].append((counts_from, values))
        return None, counts_from

    def withdraw(self, caller, group: int, now: int):
        if caller != self.authority:
            return "NOT_AUTHORIZED"
        if group not in GROUPS:
            return "NOT_ADJUSTABLE"
        if not self.pending(group, now):
            return "NOTHING_PENDING"
        self.history[group].pop()
        return None


class Operators:
    """R-26: one operator key per node, its own address until rotated."""

    def __init__(self):
        self.operator, self.proposed = {}, {}

    def of(self, node):
        return self.operator.get(node, node)

    def may_act(self, caller, node) -> bool:
        return caller == self.of(node)

    def propose(self, caller, node, proposed, registered: bool):
        if not self.may_act(caller, node):
            return "NOT_AUTHORIZED"
        if not registered:
            return "NOT_REGISTERED"
        if proposed == "zero":
            return "OPERATOR_INVALID"
        if proposed == self.of(node):
            self.proposed.pop(node, None)
        else:
            self.proposed[node] = proposed
        return None

    def accept(self, caller, node):
        if node not in self.proposed or self.proposed[node] != caller:
            return "NOT_AUTHORIZED"
        self.operator[node] = self.proposed.pop(node)
        return None


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
    if kind == "validators":
        return validators(inputs, parameters)

    if kind == "split":
        return split(integer(inputs["paid_wei"]), inputs)

    if kind == "validator-pay":
        return validator_pay(inputs, parameters, emission(parameters, integer(inputs["round"])))

    if kind == "parameters":
        return parameter_steps(inputs, parameters)

    if kind == "score":
        return {"score": median_score(inputs["members"], inputs["votes"])}

    if kind == "ledger":
        return ledger(inputs, parameters)

    if kind == "buyback":
        return buyback(inputs, parameters)

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
        return claim(inputs, earned, parameters,
                     lambda: emission(parameters, integer(earned["round"])))

    raise ValueError(f"unknown case kind: {kind!r}")


def validator_pay(inputs: dict, parameters: Parameters, cap: int) -> dict:
    """R-14: each member's pay by stake; the remainder goes to the treasury."""
    stakes = {m["node"]: integer(m["stake_wei"]) for m in inputs["members"]}
    if len(stakes) != len(inputs["members"]):
        raise ValueError("duplicate member")
    total = sum(stakes.values())
    paid = {m: cap * parameters.validator_emission_share_millionths * stake
            // (MILLION * total) if total else 0 for m, stake in stakes.items()}
    result = {"paid_wei": paid,
              "to_treasury_wei": fixed_shares(parameters, cap)["validators_wei"] - sum(paid.values())}
    if "splits" in inputs:
        for m, details in inputs["splits"].items():
            if sum(integer(b) for b in details["bonds_wei"].values()) != stakes[m]:
                raise ValueError("bonds must sum to member stake")
        result["splits"] = {m: split(paid[m], details) for m, details in inputs["splits"].items()}
    return result


def claim(inputs: dict, earned: dict, parameters: Parameters, cap) -> dict:
    """R-12: one claim against its earning round's inputs; cap() is its ceiling."""
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
    return {"paid_wei": reward(parameters, cap(), matching[0], sum(n.fees for n in nodes),
                               sum(n.stake for n in nodes), score)}


def parameter_steps(inputs: dict, parameters: Parameters) -> dict:
    """R-25 and R-05: announce, withdraw and read Parameters, step by step."""
    settings = Settings(parameters, inputs["authority"], inputs.get("addresses"))
    announced, observed, refused = [], [], []
    previous = 0
    for s in inputs["steps"]:
        now = integer(s["round"])
        if now < previous:
            raise ValueError("parameter rounds decrease")
        previous = now
        group = integer(s["group"])
        code = None
        if s["action"] == "announce":
            code, counts_from = settings.announce(s["caller"], group, s["values"], now)
            if not code:
                announced.append({"step": s["step"], "counts_from": counts_from})
        elif s["action"] == "withdraw":
            code = settings.withdraw(s["caller"], group, now)
        elif s["action"] == "observe":
            at = integer(s["at_round"])
            shown = {"step": s["step"], "values": list(settings.values_at(group, at))}
            if group == 3:
                shown["ceiling_wei"] = settings.ceiling_at(at)
            observed.append(shown)
        elif s["action"] == "observe_pending":
            entry = settings.pending(group, now)
            observed.append({"step": s["step"],
                             "values": list(entry[1]) if entry else [],
                             "counts_from": entry[0] if entry else 0})
        else:
            raise ValueError(f"unknown parameters action: {s['action']!r}")
        if code:
            refused.append({"step": s["step"], "code": code})
    return {"announced": announced, "observed": observed, "refused": refused}


def ledger(inputs: dict, parameters: Parameters) -> dict:
    """R-01/03/04/21/22: replay a fresh account ledger from inputs alone."""
    steps = inputs["steps"]
    settings = Settings(parameters, inputs.get("authority"), inputs.get("addresses"))
    operators = Operators()
    lpt = LptReplay(inputs, parameters, settings, operators)
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
        lpt.advance(round_number)
        if action == "register":
            payouts.setdefault(node, node)
        elif action == "set_payout":
            # R-01, R-26: the operator key, then registration.
            if not operators.may_act(s.get("caller", node), node):
                code = "NOT_AUTHORIZED"
            elif node not in payouts:
                code = "NOT_REGISTERED"
            else:
                payouts[node] = s["payout"]
        elif action == "set_operator":
            code = operators.propose(s.get("caller", node), node, s["next"], node in payouts)
        elif action == "accept_operator":
            code = operators.accept(s["caller"], node)
        elif action == "announce":
            code, _ = settings.announce(s["caller"], integer(s["group"]), s["values"], round_number)
        elif action == "withdraw":
            code = settings.withdraw(s["caller"], integer(s["group"]), round_number)
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
                # R-03: the fee cut in force in the round the redemption runs in.
                cut = gross * settings.values_at(1, round_number)[0] // MILLION
                paid[payouts[node]] += cut
                network += gross - cut
                fees[s["round"]][node] += gross
                used.add(s["ticket"])
        else:
            code = lpt.act(s, payouts, fees, observed)
        if code:
            refused.append({"step": s["step"], "code": code})
    return {**lpt.result(), "deposits_micro_usdc": deposits, "earmarks_micro_usdc": earmarks,
            "paid_micro_usdc": paid, "released_micro_usdc": released,
            "network_micro_usdc": network, "fees_micro_usdc": fees,
            "total_fees_micro_usdc": {r: sum(nodes.values()) for r, nodes in fees.items()},
            "observed": observed, "refused": refused}


def median_score(members: list, votes: dict) -> int:
    """R-11 / D-09: missing votes are full; even medians floor."""
    values = sorted(integer(votes[m]) if m in votes else MILLION for m in members)
    if len(set(members)) != len(members) or any(v > MILLION for v in values):
        raise ValueError("invalid members or score")
    if not values:
        return MILLION
    n = len(values)
    return (values[(n - 1) // 2] + values[n // 2]) // 2


def split(paid: int, inputs: dict) -> dict:
    """R-24: cut first, holder floors second; dust stays in Claims."""
    cut_rate = integer(inputs["cut_millionths"])
    if cut_rate > MILLION:
        raise ValueError("cut out of range")
    bonds = {h: integer(b) for h, b in inputs["bonds_wei"].items()}
    total = sum(bonds.values())
    cut = paid * cut_rate // MILLION
    pot = paid - cut
    parts = {h: pot * b // total if total else 0 for h, b in bonds.items()}
    return {"cut_wei": cut, "pot_wei": pot, "parts_wei": parts,
            "dust_wei": pot - sum(parts.values())}


def validators(inputs: dict, parameters: Parameters) -> dict:
    """R-13 / D-35: calls edit next round seats, never current membership."""
    current, pending, opted = [], [], set()
    votes, observed, refused = {}, [], []
    settings = Settings(parameters, inputs.get("authority"), inputs.get("addresses"))
    operators = Operators()
    previous = 0
    for step in inputs["steps"]:
        r = integer(step["round"])
        if r < previous:
            raise ValueError("validator rounds decrease")
        if r > previous:
            for node in set(current) - set(pending):
                votes.pop(node, None)
            current = pending.copy()
            previous = r
        stakes = {}
        for entry in sorted(inputs["stakes"], key=lambda e: integer(e["from_round"])):
            if integer(entry["from_round"]) <= r:
                stakes.update({n: integer(v) for n, v in entry["stake_wei"].items()})
        node, action = step.get("node"), step["action"]
        code = None
        # R-26: rotation and R-25: parameter changes ride in the same steps.
        if action in ("set_operator", "accept_operator", "announce", "withdraw"):
            if action == "set_operator":
                code = operators.propose(step.get("caller", node), node, step["next"],
                                         node not in inputs.get("unregistered", []))
            elif action == "accept_operator":
                code = operators.accept(step["caller"], node)
            elif action == "announce":
                code, _ = settings.announce(step["caller"], integer(step["group"]), step["values"], r)
            else:
                code = settings.withdraw(step["caller"], integer(step["group"]), r)
            if code:
                refused.append({"step": step["step"], "code": code})
            continue
        # Units / D-39: identity precedes node status and amount.
        if (action in ("opt_in", "opt_out", "vote")
                and not operators.may_act(step.get("caller", node), node)):
            refused.append({"step": step["step"], "code": "NOT_AUTHORIZED"})
            continue
        if action == "opt_in" and node in inputs.get("unregistered", []):
            refused.append({"step": step["step"], "code": "NOT_REGISTERED"})
            continue
        if action in ("seat", "opt_in"):
            repeated = node in opted
            if action == "opt_in":
                opted.add(node)
            if action == "opt_in" and repeated:
                continue
            if node not in opted:
                code = "NOT_OPTED_IN"
            elif node not in pending:
                # R-13, R-25: the set size in force in the round the call
                # runs in, never one that can still be withdrawn.
                size = settings.values_at(5, r)[0]
                smallest = min(reversed(pending), key=lambda n: stakes.get(n, 0)) if pending else None
                if not stakes.get(node, 0) or (len(pending) >= size
                        and stakes[node] <= stakes.get(smallest, 0)):
                    code = "STAKE_TOO_LOW"
                else:
                    if len(pending) >= size:
                        pending.remove(smallest)
                    pending.append(node)
                    # R-13 / D-37: undoing an exit preserves current tenure.
                    # Newcomers stay after current members, in call order.
                    pending.sort(key=lambda n: current.index(n)
                                 if n in current else len(current))
            if action == "opt_in":
                code = None
        elif action in ("opt_out", "unseat"):
            if action == "opt_out":
                opted.discard(node)
            if node in pending and (action == "opt_out" or stakes.get(node, 0) == 0):
                pending.remove(node)
        elif action == "vote":
            value = integer(step["score"])
            if node not in current:
                code = "NOT_VALIDATOR"
            elif value > MILLION:
                code = "SCORE_OUT_OF_RANGE"
            else:
                votes.setdefault(node, {})[step["target"]] = step["score"]
        elif action == "observe":
            target = step["target"]
            standing = {n: v[target] for n, v in votes.items() if target in v}
            observed.append({"step": step["step"], "members": sorted(current),
                             "score": median_score(current, standing)})
        else:
            raise ValueError(f"unknown validator action: {action}")
        if code:
            refused.append({"step": step["step"], "code": code})
    return {"observed": observed, "refused": refused}


class LptReplay:
    """Round snapshots are immutable; execution balances change immediately."""

    def __init__(self, inputs, parameters, settings, operators):
        from collections import defaultdict
        self.p = parameters
        self.settings, self.operators = settings, operators
        self.members = inputs.get('members', {})
        self.bal = {s[k]: 0 for s in inputs['steps'] for k in
                    ('node', 'payout', 'holder', 'caller', 'to') if k in s}
        self.bal.update(dict.fromkeys(('Claims', 'StakeLedger', 'Treasury'), 0))
        self.staying = defaultdict(int)
        self.transfers, self.unbonded = [], []
        self.cuts, self.snapshots, self.cut_snapshots = {}, {}, {}
        self.shares, self.minted, self.pots = {}, {}, {}
        self.drawn = set()
        self.round = 0
        self.enabled = False

    def advance(self, r):
        while self.round < r:
            self.round += 1
            for t in self.transfers[:]:
                h, old, new, due, amount = t
                if due <= self.round:
                    self.staying[h, new] += amount
                    self.transfers.remove(t)
            bonds = dict(self.staying)
            for h, old, new, due, amount in self.transfers:
                bonds[h, old] = bonds.get((h, old), 0) + amount
            self.snapshots[self.round] = bonds
            self.cut_snapshots[self.round] = self.cuts.copy()

    def unbonding(self, r):
        """R-15: the unbonding period in force in the round of the unbond or transfer."""
        return self.settings.values_at(7, r)[0]

    def details(self, r, node):
        return {'cut_millionths': str(self.cut_snapshots[r].get(node, self.p.default_reward_cut_millionths)),
                'bonds_wei': {h: str(b) for (h, n), b in self.snapshots[r].items() if n == node}}

    def pay(self, r, node, amount, category, payouts):
        parts = split(amount, self.details(r, node))
        self.pots[category, r, node] = parts['parts_wei']
        self.bal[payouts[node]] += parts['cut_wei']
        self.bal['Claims'] += parts['pot_wei']

    def act(self, s, payouts, fees, observed):
        self.enabled = True
        a, r, n, h = s['action'], self.round, s.get('node'), s.get('holder')
        amount = integer(s.get('amount_wei', '0'))
        if a == 'grant_lpt':
            self.bal[s['to']] += amount
        elif a == 'set_cut':
            # R-24 and Units: who, then registration, then cut.
            if not self.operators.may_act(s.get('caller', n), n):
                return 'NOT_AUTHORIZED'
            if n not in payouts:
                return 'NOT_REGISTERED'
            cut = integer(s['cut_millionths'])
            if cut > MILLION:
                return 'CUT_OUT_OF_RANGE'
            self.cuts[n] = cut
        elif a == 'observe_stake':
            b = self.snapshots[r]
            observed.append({'step': s['step'], 'stake_wei': sum(v for (holder, node), v in b.items() if node == n), 'total_stake_wei': sum(b.values())})
        elif a in ('bond', 'transfer', 'unbond', 'unbond_transfer', 'rebond'):
            old, new = s.get('from', n), s.get('to', n)
            if a in ('bond', 'transfer', 'rebond') and new not in payouts:
                return 'NOT_REGISTERED'
            # R-15 / D-38: same-node refusal precedes amount checks.
            if a in ('transfer', 'unbond_transfer') and old == new:
                return 'SAME_NODE'
            if a == 'bond':
                if amount > self.bal[h]:
                    raise ValueError('bond exceeds holder LPT')
                self.bal[h] -= amount
                self.bal['StakeLedger'] += amount
                self.staying[h, n] += amount
            elif a in ('transfer', 'unbond'):
                if amount > self.staying[h, old]:
                    return 'AMOUNT_TOO_LARGE'
                self.staying[h, old] -= amount
                if a == 'transfer':
                    self.transfers.append([h, old, new, r + self.unbonding(r), amount])
                else:
                    self.unbonded.append([h, old, r + self.unbonding(r), amount])
            else:
                records = (sorted([t for t in self.transfers if t[:3] == [h, old, new]], key=lambda t: t[-2], reverse=True)
                           if a == 'unbond_transfer' else list(reversed([t for t in self.unbonded if t[:2] == [h, n] and t[-2] > r])))
                if amount > sum(t[-1] for t in records):
                    return 'AMOUNT_TOO_LARGE'
                remaining = amount
                for t in records:
                    take = min(remaining, t[-1]); t[-1] -= take; remaining -= take
                if a == 'rebond':
                    self.staying[h, n] += amount
                else:
                    self.unbonded.append([h, old, r + self.unbonding(r), amount])
        elif a == 'withdraw_unbonded':
            records = [t for t in self.unbonded if t[0] == h and t[-2] <= r]
            value = sum(t[-1] for t in records)
            if not value:
                return 'UNBOND_NOT_READY'
            for t in records:
                self.unbonded.remove(t)
            self.bal[h] += value
            self.bal['StakeLedger'] -= value
        elif a in ('claim', 'mint_shares'):
            earned = integer(s['earned_round'])
            if a == 'claim' and not self.operators.may_act(s['caller'], n):
                return 'NOT_AUTHORIZED'
            # R-18 / D-40 and R-12: round zero follows authorization.
            if earned == 0:
                return 'NO_SUCH_ROUND'
            if a == 'claim' and n not in payouts:
                return 'NOT_REGISTERED'
            if a == 'mint_shares':
                if earned >= r:
                    return 'CLAIM_TOO_EARLY'
                if str(earned) in self.shares:
                    return 'ALREADY_CLAIMED'
                members = self.members.get(str(earned), [])
                # R-07, R-10, R-05: the earning round's shares and ceiling.
                values, cap = self.settings.for_round(earned), self.settings.ceiling_at(earned)
                result = validator_pay({'members': [{'node': m, 'stake_wei': str(sum(integer(b) for b in self.details(earned, m)['bonds_wei'].values()))} for m in members]}, values, cap)
                shares = fixed_shares(values, cap)
                self.shares[str(earned)] = shares
                value = sum(shares.values())
                self.bal['Treasury'] += shares['treasury_wei'] + result['to_treasury_wei']
                for m, paid in result['paid_wei'].items():
                    self.pay(earned, m, paid, 'validator', payouts)
            else:
                nodes = set(payouts)
                earned_input = {'round': str(earned - 1), 'nodes': [{'node': m, 'fees_micro_usdc': str(fees.get(str(earned), {}).get(m, 0)), 'stake_wei': str(sum(integer(b) for b in self.details(earned, m)['bonds_wei'].values())), 'score': str(MILLION)} for m in nodes]} if earned < r else {'round': str(earned - 1)}
                # R-12, R-20: the score source in force when the claim runs; a
                # source a step's scores do not name answers 10^6.
                source = self.settings.values_at(6, r)[0]
                score = s['score'] if 'score' in s else s['scores'].get(source, str(MILLION))
                result = claim({'claim_round': str(r - 1), 'node': n, 'score': score, 'already_paid': ('node', earned, n) in self.pots},
                               earned_input, self.settings.for_round(earned), lambda: self.settings.ceiling_at(earned))
                if 'code' in result:
                    return result['code']
                value = result['paid_wei']
                self.pay(earned, n, value, 'node', payouts)
            if value > 0:
                self.minted[str(earned)] = self.minted.get(str(earned), 0) + value
        elif a in ('withdraw_parts', 'withdraw_validator_parts'):
            category = 'node' if a == 'withdraw_parts' else 'validator'
            rounds = [integer(v) for v in s['rounds']]
            # R-24: zero anywhere refuses the entire list before other checks.
            if 0 in rounds:
                return 'NO_SUCH_ROUND'
            keys = [(category, e, n, h) for e in rounds]
            already = len(set(keys)) != len(keys) or any(k in self.drawn for k in keys)
            missing = any((category, e, n) not in self.pots and (category == 'node' or str(e) not in self.shares) for e in rounds)
            if already and missing:
                raise ValueError('ledger input error: R-24 does not order simultaneous ALREADY_CLAIMED and NOT_CLAIMED refusals')
            if already:
                return 'ALREADY_CLAIMED'
            if missing:
                return 'NOT_CLAIMED'
            value = sum(self.pots.get((category, e, n), {}).get(h, 0) for e in rounds)
            self.drawn.update(keys)
            self.bal[h] += value
            self.bal['Claims'] -= value
        else:
            raise ValueError(f'unknown ledger action: {a!r}')

    def result(self):
        return ({'lpt_wei': self.bal, 'shares_wei': self.shares,
                 'minted_for_round_wei': self.minted, 'lpt_minted_wei': sum(self.minted.values())} if self.enabled else {})


# R-16: the buyback. Amounts are integers: USDC in µUSDC, LPT in wei, a
# price µUSDC per wei as UQ112x112. Python integers never wrap, so every
# product is taken at full width. The pool keeps its cumulative price as
# Uniswap v2 keeps it (rule 3).
WORD = 1 << 256
UINT32 = 1 << 32
UINT112 = (1 << 112) - 1
BUYBACK_ACTIONS = {
    "job": {"face_micro_usdc"},
    "seed": {"usdc_micro_usdc", "lpt_wei"},
    "swap": {"usdc_micro_usdc"},
    "poke": set(),
    "buy": set(),
}


class TestPool:
    """The pool's reserves and v2's cumulative price of LPT in µUSDC per wei."""

    def __init__(self):
        self.usdc = self.lpt = 0
        self.cumulative = self.last = 0

    def price(self) -> int:
        return (self.usdc << 112) // self.lpt

    def cumulative_at(self, now: int) -> int:
        """What a reader sees: the stored sum plus the time since the update."""
        held_for = (now - self.last) % UINT32
        if held_for and self.usdc and self.lpt:
            return (self.cumulative + self.price() * held_for) % WORD
        return self.cumulative

    def move(self, now: int, usdc_in: int, lpt_in: int, lpt_out: int) -> None:
        """Update the cumulative from the old reserves, then change them."""
        self.cumulative, self.last = self.cumulative_at(now), now
        self.usdc += usdc_in
        self.lpt += lpt_in - lpt_out
        if self.usdc > UINT112 or self.lpt > UINT112:
            raise ValueError("a reserve passes v2's 112 bits")


def amount_out(usdc_in: int, usdc: int, lpt: int) -> int:
    """v2's getAmountOut with the 0.3% fee."""
    return usdc_in * 997 * lpt // (1000 * usdc + 997 * usdc_in)


def buyback(inputs: dict, parameters: Parameters) -> dict:
    """R-16 and R-03: replay jobs, the pool and the Buyback from inputs alone."""
    burn = parameters.buyback_burn_ppm
    interval = parameters.buyback_twap_seconds
    slippage = parameters.buyback_max_slippage_ppm
    every = parameters.buyback_every_rounds
    supply = integer(inputs["lpt_supply_wei"])
    pool = TestPool()
    held = 0
    last_buy = None              # lastBuyRound, none before the first buy
    observations = None          # (O0, O1), each (cumulative, time)
    bought, refused = [], []
    previous_round, previous_time, labels = 1, 0, set()

    for s in inputs["steps"]:
        label, action = s["step"], s.get("action")
        where = f"buyback input error at step {label}"
        if label in labels:
            raise ValueError(f"{where}: step repeated")
        labels.add(label)
        if action not in BUYBACK_ACTIONS:
            raise ValueError(f"{where}: unknown action {action!r}")
        extra = set(s) - {"step", "round", "time", "action"} - BUYBACK_ACTIONS[action]
        if extra:
            raise ValueError(f"{where}: {action} takes no {', '.join(sorted(extra))}")
        now_round, now = integer(s["round"]), integer(s["time"])
        if now_round < 1:
            raise ValueError(f"{where}: rounds are numbered from 1")
        if now_round < previous_round or now < previous_time:
            raise ValueError(f"{where}: round or time decreases")
        previous_round, previous_time = now_round, now
        code, args = None, None

        if action == "job":
            # R-03: the node's cut floors; the network share is the rest.
            face = integer(s["face_micro_usdc"])
            held += face - face * parameters.node_cut_millionths // MILLION

        elif action == "seed":
            pool.move(now, integer(s["usdc_micro_usdc"]), integer(s["lpt_wei"]), 0)

        elif action == "swap":
            usdc_in = integer(s["usdc_micro_usdc"])
            if not (pool.usdc and pool.lpt):
                raise ValueError(f"{where}: a swap needs both reserves")
            lpt_out = amount_out(usdc_in, pool.usdc, pool.lpt)
            if lpt_out == 0:
                raise ValueError(f"{where}: a swap must receive some LPT")
            pool.move(now, usdc_in, 0, lpt_out)

        elif action == "poke":
            # Rule 8, refused in rule 11's order (one pool, so never POOL_INVALID).
            if not (pool.usdc and pool.lpt):
                code = "NO_LIQUIDITY"
            elif observations is None:
                here = (pool.cumulative_at(now), now)
                observations = (here, here)
            elif now - observations[1][1] >= interval:
                observations = (observations[1], (pool.cumulative_at(now), now))
            else:
                code, args = "NOT_DUE", [observations[1][1] + interval]

        else:  # buy, rules 2 to 7, refused in rule 11's order
            if last_buy is not None and now_round < last_buy + every:
                code, args = "ALREADY_BOUGHT", [last_buy + every]
            elif held == 0:
                code = "NOTHING_HELD"
            elif not (pool.usdc and pool.lpt):
                code = "NO_LIQUIDITY"
            elif observations is None:
                code, args = "TWAP_NOT_READY", [0, interval]
            else:
                # Rule 3: O1 when an observation is due, else O0.
                cumulative = pool.cumulative_at(now)
                due = now - observations[1][1] >= interval
                observed = observations[1] if due else observations[0]
                elapsed = now - observed[1]
                if elapsed < interval:
                    code, args = "TWAP_NOT_READY", [elapsed, interval]
                else:
                    twap = (cumulative - observed[0]) % WORD // elapsed
                    # Rule 4: the most the bound allows.
                    ru, rl = pool.usdc, pool.lpt
                    limit = twap * (MILLION + slippage) // MILLION * 997 * rl
                    base = (1000 * ru) << 112
                    max_in = (limit - base) // (997 << 112) if limit > base else 0
                    # Rule 5: one quote, then the least input for it, rounded up.
                    cap = min(held, max_in)
                    lpt_out = amount_out(cap, ru, rl) if cap else 0
                    if lpt_out == 0:
                        code, args = "SLIPPAGE", [held, max_in]
                    else:
                        usdc_in = -(-1000 * ru * lpt_out // (997 * (rl - lpt_out)))
                        # Rule 7: record, draw, swap and burn, in one transaction.
                        if due:
                            observations = (observations[1], (cumulative, now))
                        last_buy = now_round
                        held -= usdc_in
                        pool.move(now, usdc_in, 0, lpt_out)
                        burned = lpt_out * burn // MILLION
                        supply -= burned
                        bought.append({"step": label, "usdc_in_micro_usdc": usdc_in,
                                       "lpt_out_wei": lpt_out, "burned_wei": burned,
                                       "max_in_micro_usdc": max_in, "twap_x112": twap})

        if code:
            refused.append({"step": label, "code": code, **({"args": args} if args else {})})

    return {"bought": bought, "refused": refused, "held_micro_usdc": held,
            "reserve_usdc_micro_usdc": pool.usdc, "reserve_lpt_wei": pool.lpt,
            "lpt_supply_wei": supply}
