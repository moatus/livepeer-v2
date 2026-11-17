"""Independent integer implementation of rulebook october-main-text-v2.

LPT replay models round-start stake/cuts, bonding, transfers, unbonding,
claims, minting and holder pots. It rejects overspending, earning round zero,
unordered refusals and unregistered self-claims/cuts lacking a rule.

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
    if kind == "validators":
        return validators(inputs, parameters)

    if kind == "split":
        return split(integer(inputs["paid_wei"]), inputs)

    if kind == "validator-pay":
        stakes = {m["node"]: integer(m["stake_wei"]) for m in inputs["members"]}
        if len(stakes) != len(inputs["members"]):
            raise ValueError("duplicate member")
        total = sum(stakes.values())
        cap = emission(parameters, integer(inputs["round"]))
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

    if kind == "score":
        return {"score": median_score(inputs["members"], inputs["votes"])}

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
    lpt = LptReplay(inputs, parameters)
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
        if action in ("seat", "opt_in"):
            repeated = node in opted
            if action == "opt_in":
                opted.add(node)
            if action == "opt_in" and repeated:
                continue
            if node not in opted:
                code = "NOT_OPTED_IN"
            elif node not in pending:
                smallest = min(reversed(pending), key=lambda n: stakes.get(n, 0)) if pending else None
                if not stakes.get(node, 0) or (len(pending) >= parameters.validator_set_size
                        and stakes[node] <= stakes.get(smallest, 0)):
                    code = "STAKE_TOO_LOW"
                else:
                    if len(pending) >= parameters.validator_set_size:
                        pending.remove(smallest)
                    pending.append(node)
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

    def __init__(self, inputs, parameters):
        from collections import defaultdict
        self.p = parameters
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
            if n not in payouts:
                raise ValueError('ledger input error: R-24 gives no rule for an unregistered set_cut')
            cut = integer(s['cut_millionths'])
            if cut > MILLION:
                return 'CUT_OUT_OF_RANGE'
            self.cuts[n] = cut
        elif a == 'observe_stake':
            b = self.snapshots[r]
            observed.append({'step': s['step'], 'stake_wei': sum(v for (holder, node), v in b.items() if node == n), 'total_stake_wei': sum(b.values())})
        elif a in ('bond', 'transfer', 'unbond', 'unbond_transfer', 'rebond'):
            old, new = s.get('from', n), s.get('to', n)
            if a in ('transfer', 'rebond') and new not in payouts:
                available = (self.staying[h, old] if a == 'transfer' else
                             sum(t[-1] for t in self.unbonded if t[:2] == [h, n] and t[-2] > r))
                if amount > available:
                    raise ValueError('ledger input error: R-15 does not order simultaneous NOT_REGISTERED and AMOUNT_TOO_LARGE refusals')
            if a in ('bond', 'transfer', 'rebond') and new not in payouts:
                return 'NOT_REGISTERED'
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
                    self.transfers.append([h, old, new, r + self.p.unbonding_period_rounds, amount])
                else:
                    self.unbonded.append([h, old, r + self.p.unbonding_period_rounds, amount])
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
                    self.unbonded.append([h, old, r + self.p.unbonding_period_rounds, amount])
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
            if earned == 0:
                raise ValueError('earning rounds start at one')
            if a == 'claim' and s['caller'] != n:
                return 'NOT_AUTHORIZED'
            if a == 'claim' and n not in payouts:
                raise ValueError('ledger input error: R-12 gives no rule for an unregistered self-claim')
            if a == 'mint_shares':
                if earned >= r:
                    return 'CLAIM_TOO_EARLY'
                if str(earned) in self.shares:
                    return 'ALREADY_CLAIMED'
                members = self.members.get(str(earned), [])
                result = calculate('validator-pay', {'round': str(earned - 1), 'members': [{'node': m, 'stake_wei': str(sum(integer(b) for b in self.details(earned, m)['bonds_wei'].values()))} for m in members]}, self.p, {})
                shares = fixed_shares(self.p, emission(self.p, earned - 1))
                self.shares[str(earned)] = shares
                value = sum(shares.values())
                self.bal['Treasury'] += shares['treasury_wei'] + result['to_treasury_wei']
                for m, paid in result['paid_wei'].items():
                    self.pay(earned, m, paid, 'validator', payouts)
            else:
                nodes = set(payouts)
                earned_input = {'round': str(earned - 1), 'nodes': [{'node': m, 'fees_micro_usdc': str(fees.get(str(earned), {}).get(m, 0)), 'stake_wei': str(sum(integer(b) for b in self.details(earned, m)['bonds_wei'].values())), 'score': str(MILLION)} for m in nodes]} if earned < r else {'round': str(earned - 1)}
                result = calculate('claim', {'earned_in': 'round', 'claim_round': str(r - 1), 'node': n, 'score': s['score'], 'already_paid': ('node', earned, n) in self.pots}, self.p, {'round': earned_input})
                if 'code' in result:
                    return result['code']
                value = result['paid_wei']
                self.pay(earned, n, value, 'node', payouts)
            if value > 0:
                self.minted[str(earned)] = self.minted.get(str(earned), 0) + value
        elif a in ('withdraw_parts', 'withdraw_validator_parts'):
            category = 'node' if a == 'withdraw_parts' else 'validator'
            rounds = [integer(v) for v in s['rounds']]
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
