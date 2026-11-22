# Rulebook `october-main-text-v3`

The rules stage 4 is built and tested against: stage 3's rules, and the
rules of changing the parameters after deployment and of rotating the key
that acts for a node. The
profile follows the main text of the October 2026 Livepeer 2.0 litepaper
wherever the main text and the appendix or a diagram disagree. Pages are
PDF pages of that paper. It is paraphrased here, never quoted.

This profile is `october-main-text-v2` with R-25 and R-26 added, R-05
rewritten, and R-01, R-03, R-07, R-08, R-09, R-10, R-12, R-13, R-14, R-15,
R-20 and R-24 amended (spec S-10): a value governance may change is read
from the Parameters for the round the entry names, and an address acts for
a node only as its operator key. Every other entry, and every case of v2, is
unchanged.

The worked numbers live in `vectors/october-main-text-v3.json`, the shared
examples file. Each entry below names the cases that show it. The
calculator and the contracts are tested against those cases, and where
this text and a case disagree, the disagreement is a defect to be decided
against this text.

Where the paper is silent or unclear, the choice is ours and is labelled
"Ours". Each entry names what it rests on. The council rulings it names
(D-03, D-04, D-05, D-07, D-08, D-09, D-10, D-11, D-12, D-18, D-21, D-22,
D-23, D-24, D-25, D-26, D-27, D-28, D-29, D-30, D-31, D-32, D-35, D-36,
D-37, D-38, D-39, D-40, D-41, D-42, D-43, D-47) were made in the owner's
absence, and the owner keeps or changes each later. A council ruling is
not an owner decision. D-16 is a proposed answer in the decision
register, not a ruling. D-49, the largest drop one schedule change may
make, was decided in the review of spec S-10, not by the council, and
R-26 rests on no ruling.

## How to read an entry

Each rule carries five fields: what the paper says, what is unclear, what
we build, an example, and what would change it. It also carries two tags
and the decisions it rests on.

- **Origin.** `Paper`: stated in the litepaper. `Interpretation`: our
  reading of an unclear or contradictory passage. `Placeholder`: our
  invention, standing in until something better exists.
- **Delivery.** `Decide now`: fixed before code is written. `Substitute`:
  a declared temporary stand-in. `Decide before stage 3`: only the paper's
  literal part is frozen here; the rest is an open decision.

## Units, parameters and codes

Every amount is an integer in its smallest unit: USDC in micro-USDC
(10^-6 USDC), LPT in wei (10^-18 LPT). The fee cut, the three emission
shares, a reward cut and a score are integers over 10^6: 1,000,000 is one
whole.

The table gives each value at deployment. A value with a group number is
held by the Parameters, and the authority may change it as R-25 says; the
group is changed whole. A value with none is fixed at deployment, and no
call names it: a different value is a new deployment.

| Parameter | Value at deployment | Group (R-25) |
|---|---|---|
| EmissionBase | 10^21 wei (1,000 LPT) a round | none: a schedule change moves the position, never the base (R-05) |
| EmissionHalfLife | 840 rounds | 3, `SCHEDULE`: the triple (position 0, half-life 840, start 1) (R-05) |
| RewardDelay | 7 rounds | 4, `REWARD_DELAY` |
| Node cut | 500,000, the network 500,000 | 1, `FEE` |
| Node emission share | 940,000 | 2, `EMISSION` |
| Validator emission share | 10,000 | 2, `EMISSION` |
| Treasury emission share | 50,000 | 2, `EMISSION` |
| ValidatorSetSize | 3, a stand-in for the paper's 33 (R-13) | 5, `VALIDATOR_SET_SIZE` |
| Score source | Scores, the deployment's score contract (R-20) | 6, `SCORE_SOURCE` |
| UnbondingPeriod | 21 rounds, also the time a transfer takes (R-15) | 7, `UNBONDING` |
| Buyback cuts | burn 1,000,000, treasury 0, validators 0 | 8, `BUYBACK_CUTS` |
| Buyback values | pool Pool, TWAP 3,600 s, slippage 10,000, a buy every 1 round | 9, `BUYBACK_VALUES` |
| RoundLength | 50 blocks (R-18) | none |
| Ticket validity | the creation round and the 2 rounds after it (R-22) | none |
| Release wait | 3 rounds (R-22) | none |
| ClaimWindow | 21 rounds after the reward delay: round R is claimable in rounds R + d to R + d + 21, d the reward delay in force for R; R + 7 to R + 28 at deployment (R-12) | none |
| Default reward cut | 0 (R-24) | none |
| Notice | 7 rounds: a change announced in round A counts from round A + 7 (R-25) | none |

Pool is the deployment's TestPool of MockUSDC and TestLPT; the buyback and
the meaning of groups 8 and 9 belong to spec S-07, and this profile holds
and checks their values only (R-25).

Rounds on-chain are numbered R from 1, starting at deployment (R-18). The
emission index of round R is r = R - 1. The ceiling, round, claim and
validator-pay cases count r, as v0 did; a `ledger` or `validators` case
counts R.

A refusal is an outcome with a stable code. These are rule outcomes. The
stage 2 contracts name their errors after the codes they produce; their
ticket checks add `TICKET_INVALID` and one kept revert text, which belong
to the contracts' public shape, not to a rule.

| Code | When |
|---|---|
| `NOT_REGISTERED` | A payment is made to, an earmark is funded for, or a payout is set for, an address that is not a registered node (R-01); an operator key is proposed for one (R-26); a bond, a transfer or a rebond to an address that is not a registered node (R-15); an opt-in, a claim or a reward cut by an address that is not a registered node, acting for itself (R-13, R-12, R-24) |
| `SCORE_OUT_OF_RANGE` | A claim reads a score above 10^6 (R-11); a vote above 10^6 is cast (R-11) |
| `CLAIM_TOO_EARLY` | A claim is made before its round is claimable (R-12); a round's shares are minted before the round has ended (R-10) |
| `ALREADY_CLAIMED` | An entitlement that has already paid is claimed again (R-12), or a holder's part, a holder's validator part or a round's shares taken again (R-24, R-10) |
| `ROUND_NOT_OVER` | A round is started before the current one has lasted RoundLength blocks (R-18) |
| `PAYOUT_INVALID` | A node sets its payout to the Broker's own address (R-01) |
| `NOTHING_TO_PAY` | A ticket would move 0: the payer's deposit and its earmark for that node are both 0 (R-04) |
| `TICKET_USED` | A ticket that has paid is redeemed again (R-04) |
| `TICKET_EXPIRED` | A ticket is redeemed after the second round following its creation round (R-22) |
| `AMOUNT_TOO_LARGE` | A release request is above the deposit or earmark it names (R-22), or an unbond, transfer, unbond of a transfer or rebond above what the named record holds (R-15) |
| `RELEASE_NOT_READY` | A release with none pending, or before its round (R-22) |
| `CLAIM_EXPIRED` | A claim is made after its round's claim window has closed (R-12) |
| `NOT_AUTHORIZED` | A claim, a reward cut, a payout, an operator proposal, an opt-in, an opt-out or a vote for a node by an address that is not its operator key (R-12, R-24, R-01, R-26, R-13); an acceptance by any address but the node's proposed key (R-26); an announcement or a withdrawal by any address but the authority (R-25); a mint of test LPT by any address but Claims (R-09) |
| `NOT_CLAIMED` | A holder withdraws its part of a round its node has not claimed, or its validator part of a round whose shares are not minted (R-24) |
| `UNBOND_NOT_READY` | A holder withdraws when none of its unbonded LPT is withdrawable yet (R-15) |
| `CUT_OUT_OF_RANGE` | A node sets a reward cut above 10^6 (R-24) |
| `NOT_VALIDATOR` | A vote by a node that is not a member of the current round (R-11, R-13) |
| `STAKE_TOO_LOW` | Seating a node whose stake is 0, or, with every seat taken, not above the smallest member's (R-13) |
| `NOT_OPTED_IN` | Seating a node that has not opted in (R-13) |
| `NO_SUCH_ROUND` | A mint of shares, a claim or a withdrawal list that names round 0, which is no round (R-18, R-10, R-12, R-24) |
| `SAME_NODE` | A transfer to the node the bond is leaving, or an unbond of such a transfer (R-15) |
| `NOT_ADJUSTABLE` | An announcement or a withdrawal names a number that is not one of the nine groups (R-25) |
| `SCHEDULE_RAISED` | A schedule change whose half-life is above the one in force (R-25, R-05) |
| `VALUE_OUT_OF_RANGE` | An announcement with the wrong number of values for its group, or a value outside its group's range (R-25) |
| `IDENTITY_NOT_WHOLE` | An announcement of the fee cuts or the emission shares, each in range, that does not sum to 10^6 (R-25) |
| `POOL_INVALID` | An announcement of buyback values whose pool fails the pool check: not the pinned TestPool code, or not this deployment's MockUSDC and TestLPT (R-25). Spec S-07's code, declared here first |
| `NOTHING_PENDING` | A withdrawal for a group with no change pending (R-25) |
| `OPERATOR_INVALID` | An operator proposal that names the address zero (R-26) |

When two refusals apply to one call, the first in this order is given:
who, `NOT_AUTHORIZED`; then the node or round named, `NO_SUCH_ROUND`,
`NOT_REGISTERED`, `SAME_NODE`, `NOT_OPTED_IN`, `NOT_VALIDATOR`, and for a
claim its `CLAIM_TOO_EARLY`, `CLAIM_EXPIRED` and `ALREADY_CLAIMED`; then
the amount, `AMOUNT_TOO_LARGE`, `CUT_OUT_OF_RANGE`, `STAKE_TOO_LOW`,
`SCORE_OUT_OF_RANGE` (D-26, D-38, D-39, D-40). `refusal-order` and
`refusal-order-validators` combine two. An operator proposal follows it:
`NOT_AUTHORIZED`, `NOT_REGISTERED`, `OPERATOR_INVALID` (R-26). An
announcement has its own order: `NOT_AUTHORIZED`, `NOT_ADJUSTABLE`,
`SCHEDULE_RAISED`, `VALUE_OUT_OF_RANGE`, `IDENTITY_NOT_WHOLE`,
`POOL_INVALID`; a withdrawal `NOT_AUTHORIZED`, `NOT_ADJUSTABLE`,
`NOTHING_PENDING` (R-25). `parameters-refusal-order` combines two.

An address may act for a node only when it is the node's operator key
(R-26). The operator key is the node's own address until it is rotated;
the check reads nothing else about the node (R-01, R-12, R-13, R-24).

Four moments are kept apart. The earning round fixes the ceiling, the
rules, and every registered node's recognized fees and stake; they are
fixed when that round closes. The claim reads the score. The claim's
execution checks whether it may pay now. From this profile, the stake of
round R, each holder's bond, each node's reward cut and R's validator
members are those at the start of R (R-08, R-13, R-15, R-24).

The Parameters' values follow the same moments (R-25). The earning round
fixes its emission shares, its schedule and so its ceiling, and its
reward delay and so its claim window. A fee takes the fee cut in force in
the round it is redeemed in, which is its earning round. A claim reads
its score from the score source in force when the claim runs. An unbond
or a transfer takes the unbonding period in force in its own round, and a
seat call the set size in force for the round being seated, the next.

## The shared examples

`vectors/october-main-text-v3.json` holds the parameters above and 92
cases: v2's 74, unchanged, and 18 new ones. Every number in it is a
decimal string; any other string a step gives is a name, and a name
stands for one address. A case has exactly four keys: an `id`, a `kind`
(`settlement`, `ceiling`, `round`, `claim`, `ledger`, `split`, `score`,
`validators`, `validator-pay` or `parameters`), its `inputs`, and
`expected`: exact amounts, one `code`, or for a `ledger`, `validators` or
`parameters` case the outcome of its steps. The calculator refuses a case
with any other key and is given only its `inputs`, so removing every
`expected` leaves the inputs alone.

Every `ledger`, `validators` and `parameters` case starts from a fresh
deployment whose Parameters hold the values above. Its authority is the
case's `authority`, and where a case names none, no step announces. Every
node's operator key is its own address. A case's `addresses` describe
the addresses its announcements name, other than the deployment's Scores
and Pool, which have code, Pool being the pinned TestPool of MockUSDC and
TestLPT: `code`, whether the address has code; for a pool,
`pinned_pool_code`, whether its code hash is the pinned TestPool's, and
`token0` and `token1`, its two tokens. The address zero is named `zero`.

- A **settlement** case is one payment of `gross_micro_usdc` to an address
  that is or is not a registered node. It states the node's recognized fee
  (`recognized_micro_usdc`, R-04) and the split (`node_micro_usdc` and
  `network_micro_usdc`, R-03), or a code.
- A **ceiling** case is one round number. It states `ceiling_wei` (R-05),
  or, where its id says shares, R-10's `validators_wei` and
  `treasury_wei` of that ceiling.
- A **round** case is one round number and the round's registered nodes,
  each with its recognized fees, its stake and its score. The round's
  totals are taken over the nodes listed. `paid_wei` is each node's reward
  at that score, and `validators_wei` and `treasury_wei` are R-10's two
  amounts. `minted_wei` is everything the round mints: the nodes' rewards
  plus the validator and treasury amounts. It is given only where every
  score is 10^6. A score below 10^6 withholds a part that is never minted
  (D-21).
- A **claim** case claims one node's reward from a round case
  (`earned_in`), at a claim round, with the score read at claim and
  whether that entitlement has already paid. It states `paid_wei`, or a
  code. The node itself claims.
- A **split** case is one node reward split (R-24): `paid_wei`, the node's
  `cut_millionths`, and `bonds_wei`, every bond behind the node at the
  start of the earning round; the node's stake is their sum. It states the
  cut to the payout (`cut_wei`), the pot to Claims (`pot_wei`), each
  holder's part (`parts_wei`) and what the floors leave in Claims
  (`dust_wei`).
- A **score** case is the members of a claim round (`members`) and each
  member's standing vote on one node (`votes`); a member it does not list
  under `votes` has not voted. It states the node's `score` (R-11).
- A **validators** case runs ordered `steps` against `stakes`: each entry
  gives, from its `from_round`, the stake of each node it names at the
  start of every round, until a later entry names that node again; a node
  no entry has yet named has 0. Every node a step names, and the target
  `X`, is a registered node, except a node the case lists under
  `unregistered`. Nothing has opted in when it starts, and
  ValidatorSetSize is 3 until a change counts. Each step has a `step`
  number, its on-chain `round` and an `action`:
  - `opt_in`, `opt_out`: `caller` opts `node` in or out; a step that
    names no `caller` is made by `node` itself (R-13).
  - `seat`, `unseat`: anyone seats or unseats `node` (R-13).
  - `vote`: `caller` votes `score` for `node` on `target`; a step that
    names no `caller` is made by `node` itself (R-11).
  - `set_operator`, `accept_operator`, `announce`: as in a `ledger`
    case.
  - `observe`: the members of the current round and `target`'s score as a
    claim would read it now.

  A step either takes effect or is refused with one code, and a refused
  step changes nothing. `expected` states, in step order, what each
  `observe` step was shown (`observed`: `members`, listed in alphabetical
  order, a set, and `score`) and the code of each refused step
  (`refused`).
- A **validator-pay** case is an emission index `round` and that round's
  members (`members`), each with its stake at the start of the round
  (`stake_wei`). Where it gives `splits`, a member's `cut_millionths` and
  `bonds_wei`, every bond behind it at the start of the round, also count.
  It states each member's pay (`paid_wei`, R-14), what goes to the
  treasury on top of R-10's treasury amount (`to_treasury_wei`), and for
  each split given, the cut, the pot, each holder's part and the dust, as
  a split case states them.
- A **ledger** case starts from a fresh deployment and runs its `steps`
  in order. It may give `members`: for an earning round, the nodes that
  are its validator members, a stand-in for the validator set; a round it
  does not name has none. Each step has a `step` number, the on-chain
  `round` it is made in, and an `action`:
  - `register`: `node` registers (R-01).
  - `set_payout`: `caller` sets `node`'s payout address to `payout`; a
    step that names no `caller` is made by `node` itself (R-01).
  - `set_operator`: `caller` proposes `next` as `node`'s operator key; a
    step that names no `caller` is made by `node` itself (R-26).
  - `accept_operator`: `caller` accepts the proposal for `node` (R-26).
  - `announce`, `withdraw`: as in a `parameters` case (R-25).
  - `fund`: `payer` adds `deposit_micro_usdc` to its deposit and
    `earmark_micro_usdc` to its earmark for `node`, in one step. A step
    with an earmark of 0 names no node (R-21).
  - `request_release`: `payer` requests `amount_micro_usdc` out of its
    earmark for `node`, or out of its deposit when the step names no node
    (R-22).
  - `release`: `payer` releases its pending request on the earmark for
    `node`, or on its deposit when the step names no node (R-22).
  - `redeem`: `ticket`, an id unique to one ticket in the case, from
    `payer` to `node`, created in `created_round` with face
    `face_micro_usdc`, is redeemed (R-04, R-22). Redeeming the same id
    again redeems the same ticket.
  - `observe`: what `node` is shown of `payer`'s account: the deposit and
    its claimable earmark (R-21), stated as `deposit_micro_usdc` and
    `claimable_micro_usdc`.
  - `grant_lpt`: `amount_wei` of test LPT goes to `to` from the genesis
    supply. It is a stand-in for holding LPT: a transfer, not a mint, and
    no round's issuance.
  - `bond`, `unbond`: `holder` bonds `amount_wei` of its LPT to `node`,
    or unbonds it from its bond staying at `node` (R-15).
  - `transfer`: `holder` moves `amount_wei` of its bond staying at `from`
    to `to` (R-15).
  - `unbond_transfer`: `holder` unbonds `amount_wei` of its stake in
    transfer from `from` to `to` (R-15).
  - `rebond`: `holder` rebonds `amount_wei` of its unbonded LPT to `node`
    (R-15).
  - `withdraw_unbonded`: `holder` withdraws all its withdrawable unbonded
    LPT (R-15).
  - `set_cut`: `caller` sets `node`'s reward cut to `cut_millionths`; a
    step that names no `caller` is made by `node` itself (R-24).
  - `claim`: `caller` claims `node`'s reward for `earned_round`, and the
    claim reads `score`. A step that gives `scores` instead gives what
    each score source answers for `node` when the claim runs; the claim
    reads the source in force then, and a source `scores` does not name
    answers 10^6, as a source with no votes does (R-11, R-12, R-20).
  - `withdraw_parts`, `withdraw_validator_parts`: `holder` withdraws its
    parts of `node`'s claims, or of `node`'s validator pots, for the
    earning rounds listed in `rounds` (R-24).
  - `mint_shares`: `caller` mints the validator and treasury amounts of
    `earned_round` (R-10, R-14).
  - `observe_stake`: `node`'s stake and the total stake at the start of
    the step's round (R-08, R-15), stated as `stake_wei` and
    `total_stake_wei`.

  A step is either refused with one code, and then changes nothing, or
  takes effect. `expected` states the balances after the last step:
  `deposits_micro_usdc` for every payer; `earmarks_micro_usdc` for every
  payer, holding every node one of its `fund` steps names, and empty
  (`{}`) for a payer whose `fund` steps name none; `paid_micro_usdc`,
  the USDC the Broker paid out to every address a step names as a node or
  a payout; `released_micro_usdc`, what every payer has received by
  release; `network_micro_usdc`, the network share held; and
  `fees_micro_usdc`, each node's recognized fees (R-04) for every round
  and node of a `redeem` step, with `total_fees_micro_usdc` for each of
  those rounds. Every amount that is listed and nothing reached is 0. A
  case with an LPT step also states `lpt_wei`, the test LPT held by every
  address a step names as a node, a payout, a holder, a caller or a
  recipient, and by `Claims`, `StakeLedger` and `Treasury`, the treasury
  address; `shares_wei`, for every round whose shares were minted, its
  `validators_wei` and `treasury_wei` (R-10), whoever received them;
  `minted_for_round_wei`, the LPT minted for each earning round for which
  any was; and `lpt_minted_wei`, all the LPT minted, which is the token's
  supply less its genesis supply. It then states, in step order, what
  each `observe` and `observe_stake` step was shown (`observed`) and the
  code of each refused step (`refused`). A step not listed under
  `refused` took effect.

  After every step of a `ledger` case, these hold. The LPT StakeLedger
  holds equals every bond counting or due (staying, in transfer or
  arriving) plus the unbonded LPT not withdrawn. For each claim, and each
  (round, member) validator pot, the parts withdrawn never exceed its
  pot. The LPT Claims holds equals the pots of the claims made and of the
  validator shares minted, less the parts of each withdrawn. For each
  earning round R the LPT minted for R never exceeds C(R - 1). The
  token's supply equals its genesis supply plus what has been minted for
  every round.
- A **parameters** case runs its `steps` against a fresh deployment's
  Parameters. Each step has a `step` number, its on-chain `round` and an
  `action`:
  - `announce`: `caller` announces `values` for `group`, R-25's group
    number; the values are in the group's order, an address by its name.
  - `withdraw`: `caller` withdraws `group`'s pending change.
  - `observe`: the values in force for `group` in on-chain round
    `at_round`; a round before any change counts, round 0 included, has
    the deployment's. For group 3 these are the triple (position,
    half-life, start), and the step also states `ceiling_wei`, that
    round's ceiling C(at_round - 1) (R-05).
  - `observe_pending`: `group`'s pending change, its `values` and
    `counts_from`, or no values and `counts_from` 0 when none is
    pending. A schedule change's values are the triple it records.

  `expected` states, in step order, the round each accepted announcement
  counts from (`announced`), what each `observe` and `observe_pending`
  step was shown (`observed`) and the code of each refused step
  (`refused`).

---

## R-01 Registration

Origin: `Paper`; ours for "only registered", for permanence and for the
payout. Delivery: `Decide now`. Rests on: D-08 (b2), D-23, D-28; R-26
for who sets the payout.

**The paper says.** Any address can register as a node at any time, and
no LPT bond is required (p. 3). There is no cap on the number of nodes
(pp. 3, 12). A new node has no ramp-up period before it can earn rewards
(p. 10). It says nothing about registering twice, leaving the registry,
or the address a node is paid at.

**Unclear.** What happens to a payment sent to an address that has not
registered. The current protocol refuses fees to a recipient that is not
an active transcoder, a cap the paper removes. Whether a registration
lasts, and whether a node is paid at the key it runs with.

**We build.** Any address registers, at any time, with no bond and no cap,
and can earn from the round in which it registers. Ours: only a registered
node can be paid. A payment to any other address is refused with
`NOT_REGISTERED`; nothing moves and nothing counts as a fee anywhere.

Ours: registration is permanent, and registering again changes nothing; it
is not refused. Each node has a payout address, the node itself until its
operator key sets another, and only its operator key (R-26) sets it. A
payout set for a node from any other address is refused with
`NOT_AUTHORIZED`, and one set for an address that is not registered,
acting for itself, with `NOT_REGISTERED`, in that order. Setting
it to the Broker's own address is refused with `PAYOUT_INVALID`, since the
node's half would then stay inside the Broker. The payout is where the
node is paid (R-03); the fees stay the node's (R-04).

**Example.** `settle-even` pays a registered node 1,250,000 micro-USDC of
2,500,000. `settle-unregistered` sends the same 2,500,000 micro-USDC to an
address that is not registered and is refused with `NOT_REGISTERED`.
`ledger-payout`: N registers and sets its payout to W; a ticket of 10,001
to N pays W 5,000 and N 0, and the fee of 10,001 is N's.
`ledger-unregistered`: an earmark of 1,000,000 for X and a ticket of
10,001 to X, which never registered, are both refused with
`NOT_REGISTERED`, and nothing moves or counts. `ledger-rotation`: once K2
is N's operator key, K1's payout for N and N's own are refused with
`NOT_AUTHORIZED`; once K3 is, K2's is refused, and K3 sets W2, so round
12's ticket of 1,000,000 pays W2 500,000, where those of rounds 3 and 11
paid W.

**Would change it.** A gateway design that must settle to an address that
is not a node (D-23). A discovery run where dead listings measurably hurt
gateways, which would reopen a way to leave the registry at stage 5; the
owner preferring to pay the ticket's recipient directly, or a run showing
the payout breaks the unmodified node (D-28).

## R-02 Currency

Origin: `Paper`. Delivery: `Substitute` for the token. Rests on: D-03,
D-07.

**The paper says.** Nodes price in USD and are paid in USDC, in place of
ETH (pp. 6, 12).

**Unclear.** Nothing about the currency. The test token stands in for
USDC.

**We build.** Fees are mock USDC with six decimals, counted in micro-USDC.
Ours: no ETH amount and no price appears in any rule of this profile; the
emission is in LPT and depends on no price (R-05).

**Example.** `settle-even`: 2.50 USDC is 2,500,000 micro-USDC.

**Would change it.** A payment rail that settles in another unit (D-06).

## R-03 Fee split

Origin: `Paper`, rounding and the address ours. Delivery: `Decide now`.
Rests on: D-07, D-23, D-28, D-41, D-42.

**The paper says.** Of the USDC fees a node earns, half goes to the node
and half to the network, which buys LPT and burns it (p. 6). The node cut
and the network fee together are all of the gross fee (p. 11). The node's
half goes to it directly (pp. 6, 7).

**Unclear.** Which side takes an odd micro-USDC. Which address of the node
receives its half, and when.

**We build.** node = floor(gross x f / 10^6), f the node cut of the `FEE`
group in force in the round the redemption runs in (R-25), 500,000 at
deployment; network = gross - node, held for buyback. The two always sum
to gross. Ours: the odd unit goes to
the network, so a node paying itself never gains from rounding.

Ours: the node's half is paid to its payout address (R-01), read at
redemption, in the same transaction as the redemption. The network's half
stays in the Broker as the network share, held for buyback.

**Example.** `settle-even`: 2,500,000 splits into 1,250,000 and 1,250,000.
`settle-odd`: 1,000,001 splits into 500,000 for the node and 500,001 for
the network. `settle-one`: 1 gives the node 0 and the network 1.
`ledger-demo`: six tickets of 10,001 pay the node 5,000 each and the
network 5,001 each, 30,000 and 30,006 in all. `ledger-payout`: the node's
5,000 goes to its payout W. `ledger-delays`: a fee cut of 600,000,
announced in round 4, counts from round 11; round 10's ticket of
1,000,000 pays N 500,000 and round 11's 600,000.

**Would change it.** A tiny-payment pattern where the odd unit matters
(D-23). The owner preferring to pay the ticket's recipient directly, or a
run showing the payout breaks the unmodified node (D-28).

## R-04 Recognized fee

Origin: `Interpretation`. Delivery: `Decide now`. Rests on: D-05, D-28.

**The paper says.** Nothing defines the fee that a node's fee share is
counted in. It speaks of fees only as USDC paid to nodes and split (pp. 6,
11), and argues that a node faking volume pays half of it into the burn to
chase a fixed pool (p. 9). Nothing on a ticket the payer cannot cover.

**Unclear.** Whether a fee is money actually settled or value reported or
signed, and, for tickets, which round a fee counts in. What a ticket pays
when the payer holds less for that node than its face.

**We build.** A node's fees for round r are the gross USDC settled to it
on-chain in round r: the amount actually moved, before the split. A ticket
counts in the round it is redeemed, not the round it was signed. Signed
but unsettled value counts for nothing. A node paying itself counts. Only
a registered node's settlements count (R-01).

Ours: a ticket pays min(face, the payer's deposit + the payer's earmark
for that node), taken from the deposit first and then from that earmark;
no other node's earmark is drawn (R-21). What moved is the fee: it is
split (R-03), counted in the redemption round, and the ticket is used. A
used ticket redeemed again is refused with `TICKET_USED` and pays
nothing. A ticket that would move 0 is refused with `NOTHING_TO_PAY` and
stays unused, so it can pay once the payer funds. Any refusal leaves
money, fee records and used tickets unchanged.

**Example.** In `settle-even`, `settle-odd` and `settle-one` the
recognized fee is the gross: 2,500,000, 1,000,001 and 1 micro-USDC. In
`round-self-payer`, S settles 250,000,000 micro-USDC to itself and it
counts in the round's total. `ledger-partial`: a deposit of 300,001 and an
earmark of 500,000 against a ticket of 1,000,000 move 800,001; the node is
paid 400,000, the network 400,001, the fee is 800,001 and the ticket is
used. `ledger-empty`: a ticket of 1,000,000 to N1, while the deposit is 0
and only N2 has an earmark, is refused with `NOTHING_TO_PAY`; once the
payer funds its deposit with 1,000,000 the same ticket pays N1 500,000 and
the network 500,000. `ledger-reserve`: 30 tickets of 1,000,000 move
20,000,000 in all, and 10 are refused with `NOTHING_TO_PAY`.
`ledger-replay`: a ticket of 10,001 redeemed twice pays once, and the
second is refused with `TICKET_USED`. `ledger-expired`: a ticket created
in round 8 and redeemed in round 10 counts its 10,001 in round 10.

**Would change it.** Paper text defining the fee share by signed or
expected value; a run where nodes gain materially by timing redemptions
into thin rounds (D-05). A run where a node paid less than face
mis-accounts and stops serving a payer that still has funds (D-28).

## R-05 Emission ceiling

Origin: `Interpretation`; ours for the schedule's integer form after a
change. Delivery: `Decide now`. Rests on: D-03, D-12, D-23, D-47 (iv);
D-49.

**The paper says.** Emission follows a fixed schedule in LPT, halving
every two years as a smooth per-round decay: EmissionBase x
2^(-r / EmissionHalfLife), with a half-life of 840 rounds (p. 7). The
mint is fixed in tokens and is a ceiling (p. 8). It needs no oracle and
has no USD cap (p. 9). EmissionBase is in LPT, not USD (p. 13). The
diagram on p. 7 instead labels the schedule as a multiple of fees with a
USD cap. The base and the half-life may only be lowered (p. 13), and
governance may speed the decline but should weigh whether the two are
fully adjustable (p. 9).

**Unclear.** The diagram contradicts the text. The formula is irrational
for most rounds, and the paper gives no integer form. Which round is
round 0. How the schedule continues once it is lowered, and by how much
one change may lower it.

**We build.** For a whole position n >= 0, E(n) is the largest integer c,
in LPT wei, with c^840 x 2^n <= EmissionBase^840: the exact floor of
EmissionBase x 2^(-n/840). No fee, price or USD cap enters it. Because
floor(floor(x) / 2^k) = floor(x / 2^k), E(n + 840k) = floor(E(n) / 2^k).

The schedule in force for on-chain round R (R-25) is a triple (p, H, s):
a position p, a half-life H that divides 840, and the round s it starts
from. R's ceiling is E(p + (R - s) x 840 / H). Entries that count a round
by its emission index write it C(r), the ceiling of round R = r + 1. The
deployment's triple is (0, 840, 1), so until a change counts C(r) = E(r):
r is counted from the deployment's first round, which emits the full
EmissionBase, and every ceiling, round and claim case reads E(r).

Ours: a schedule change names a drop d and a half-life H (D-12, D-47
(iv), D-49). A change counting from round E records (q + d, H, E - 1),
where q is the position the schedule in force reaches in round E - 1. So
round E - 1 keeps its ceiling, and from round E the new half-life runs on
from the ceiling already reached, d whole steps of 2^(-1/840) lower. The
base never changes. H must divide 840 and be no higher than the half-life
in force when the change is announced: a higher one is refused with
`SCHEDULE_RAISED`, and one that does not divide 840, 0 included, with
`VALUE_OUT_OF_RANGE`. d is 0 to 840 (D-49); more is refused with
`VALUE_OUT_OF_RANGE`. Since H divides 840, every position is a whole
number and every ceiling is some E(n); the position never falls, so the
ceiling never rises.

**Example.** `ceiling-0`: 1,000,000,000,000,000,000,000 wei.
`ceiling-1`: 999,175,165,148,489,107,267. `ceiling-420`:
707,106,781,186,547,524,400. `ceiling-839`:
500,412,757,882,842,450,669. `ceiling-840`:
500,000,000,000,000,000,000. `ceiling-841`:
499,587,582,574,244,553,633, which is floor(C(1) / 2). `ceiling-1680`:
250,000,000,000,000,000,000.

`parameters-half-life`, D-12's example: with no change, round 101's
ceiling is 920,795,304,004,931,970,727 wei. A half-life of 420, drop 0,
announced in round 94, counts from round 101 and records (99, 420, 100):
round 100 keeps 921,555,435,045,356,663,643 at position 99, round 101 is
position 101, 920,035,799,947,081,135,332, and round 102 position 103,
918,518,670,710,899,293,460. A half-life of 1,680 is refused with
`SCHEDULE_RAISED`, and 800 and 0 with `VALUE_OUT_OF_RANGE`. In round 101,
840 is refused with `SCHEDULE_RAISED`, 420 being in force; 210 counts from
round 108 and records (113, 210, 107): round 107 is
910,970,468,074,747,749,456 and round 108, position 117,
907,968,583,953,629,007,541.

`parameters-drop`: lowering round 100's ceiling to 800 LPT or less,
half-life kept, announced in round 93 so that it counts from round 100.
A drop of 171 records (269, 840, 99) and gives round 100
800,277,042,580,965,387,769 wei; a drop of 172, announced after it,
replaces it, records (270, 840, 99) and gives round 100
799,616,946,185,380,540,803 at position 271 and round 101
798,957,394,260,308,128,939; round 99 keeps 922,316,193,585,939,213,611.
Drops of 841, of 17,200 typed for 172, and of 1,720 are refused with
`VALUE_OUT_OF_RANGE`. `parameters-drop-840`: D-49's largest drop halves
the ceiling, 460,777,717,522,678,331,821 wei in round 100 and
460,397,652,002,465,985,363 in round 101. `ledger-delays`: a drop of 84
counting from round 11 mints round 11 at position 94.

**Would change it.** A later paper or author statement that the diagram
is the current design (D-03). A contract at the real EmissionBase that
cannot reproduce all 840 residues within acceptable gas, which would make
a table derived from the exact floor the rule; a stage that must continue
a schedule across deployments (D-23). A need for a base or half-life the
840ths grid cannot reach (D-47). A cut the authority means that 840 steps
an announcement cannot make in time, or a slip within 840 steps that does
lasting harm (D-49).

## R-06 Emission base

Origin: `Placeholder`. Delivery: `Substitute`. Rests on: no ruling; D-20
is untouched.

**The paper says.** EmissionBase is a launch snapshot of the existing
protocol's annualized issuance, converted to LPT a round (pp. 7, 13). The
half-life starts at 840 rounds, about two years (pp. 7, 13).

**Unclear.** How many rounds make a year when annual issuance becomes a
per-round amount (D-20).

**We build.** EmissionBase is 1,000 LPT a round, a test value. The
half-life stays 840 rounds.

**Example.** `ceiling-0`: 1,000,000,000,000,000,000,000 wei.

**Would change it.** D-20, when EmissionBase is taken from real issuance.

## R-07 Emission shares

Origin: `Paper`. Delivery: `Decide now`. Rests on: D-41, D-42; the paper
states the values.

**The paper says.** Each round's emission divides 94 % to nodes, 1 % to
validators and 5 % to the treasury, and the three are all of it (pp. 11,
13).

**Unclear.** Nothing about the values.

**We build.** The shares of a round are the `EMISSION` group in force
for it, its earning round (R-25): node 940,000, validators 10,000,
treasury 50,000 millionths of C(r) at deployment. Each is 0 to 10^6 and
the three sum to 10^6. A round keeps the shares in force when it
started, whenever it is claimed or its shares minted. Each share is
applied inside its own single division (R-08, R-10), never stored as an
amount first.

**Example.** `ceiling-1-shares`: of C(1), validators
9,991,751,651,484,891,072 wei and treasury 49,958,758,257,424,455,363 wei.
`round-roadmap`, at r = 0: validators 10,000,000,000,000,000,000 wei and
treasury 50,000,000,000,000,000,000 wei.

`ledger-authority`, the result of spec S-10: N's stake share is 0.32 and
its fee share 1, so its stake share binds, and its cut is 10^6, so its
payout receives the whole entitlement; N sets a new payout before each
claim, so each amount stands alone. Shares of 900,000, 10,000 and 90,000,
announced in round 4, count from round 11. N's round 2, claimed in round
9, pays W 300,551,889,676,665,523,465 wei at 94 %; round 5, claimed in
round 12, after the change counts, still pays W5
299,808,785,931,841,616,464 at 94 %; round 11, claimed in round 18, pays
W11 285,633,273,630,105,808,770 at 90 %, and its treasury amount is
89,260,398,009,408,065,240. `ceiling-4` and `ceiling-10` give the
ceilings of rounds 5 and 11, 996,704,740,464,898,990,905 and
991,782,200,104,534,058,230 wei.

**Would change it.** Paper text giving the shares a narrower range than
the identity.

## R-08 Node reward

Origin: `Interpretation`. Delivery: `Decide now`. Rests on: D-04, D-07,
D-11, D-22, D-23, D-42.

**The paper says.** A node's reward share is the smaller of its share of
network fees and its share of network stake (pp. 3, 12), so stake sets a
reward ceiling (p. 4). The stake behind a node is its own and its
delegators' together (p. 3). The median score sets what fraction of that
capped reward the node receives (p. 4). The capped part is not minted
(p. 8). The parameter table on p. 13 instead describes the node share as
divided by share of work, weighted by score, with no stake term.

**Unclear.** p. 13 against pp. 3, 4, 8 and 12 (D-04). Whether a node
later scored at zero still counts in the network total (D-22). The
integer form and where it rounds (D-07). What a share is when a total is
zero (D-23). At what moment a round's stake is read, when bonds move
during the round (D-11). The rejected forms on the cases below: flooring
the 94 % pool before the share would pay D in `round-chained` one wei
less, 704,418,491,429,684,820,622; flooring the share before the score
would pay each node in `round-one-division` 234.999999999999999999 LPT;
without the zero-total rule, `round-no-stake` would read 0 <= 0 and pay A
940 LPT.

**We build.**

paid = floor(C x n x a x score / (10^6 x b x 10^6))

where C is the earning round's ceiling C(r), n its node emission share
(R-07), 940,000 at deployment, and a/b is fee/totalFee when
fee x totalStake <= stake x totalFee, and stake/totalStake otherwise. Fee
and stake amounts are never rescaled; they meet only inside this one
division. The totals are every registered node's recognized fees (R-04)
and stake for round r, fixed when the round closes. A node's fees count in
the total whatever score it is later given; a score scales only that
node's own reward, read at claim (R-11, R-12), and never reopens another
node's share. A round with zero total fees or zero total stake pays no
node.

A node's stake for round R is the sum of the bonds counting for it at the
start of R (R-15), its operator's and its delegators' alike, and the total
stake is every registered node's. A bond made during R counts from R + 1.

What this means: a node paying itself with no stake is paid nothing, but
its fees still thin the others' fee shares, within their stake caps. In
`round-self-payer`, Z would be paid 752 LPT without S; S's self-payment
costs it 125 USDC to the buyback and cuts Z by 188 LPT, whatever score S
is given. In `round-staked-self-payer`, W's self-payment costs it 125 USDC
and earns it 188 LPT that would otherwise not be minted. The rules of this
profile do not stop that; only a score can.

**Example.** `round-roadmap`: A has 600 USDC of fees and 2,000 LPT of
stake, B 400 USDC and 8,000 LPT. A's stake share is the smaller of its
two, so A is paid 188,000,000,000,000,000,000 wei; B's fee share is the
smaller, so B is paid 376,000,000,000,000,000,000. `round-self-payer`: X
18,800,000,000,000,000,000, Y 47,000,000,000,000,000,000, Z
564,000,000,000,000,000,000; S, which pays itself 250 USDC and has no
stake, is paid 0, and its fees still count in the total.
`round-staked-self-payer`: H 752,000,000,000,000,000,000; W, which pays
itself 250 USDC with 2,000 LPT of stake, is paid
188,000,000,000,000,000,000. `round-small-node`: T, with 1 USDC of fees
and 1 LPT of stake against U's 2,999,999 USDC and 2 LPT, is paid
313,333,333,333,333 wei and U 626,666,666,666,666,666,666. `round-thirds`:
three equal nodes are paid 313,333,333,333,333,333,333 wei each.
`round-one-division`: the same nodes at score 750,000 are paid
235,000,000,000,000,000,000 wei each, exactly. `round-no-fees` and
`round-no-stake`: every node is paid 0. `round-chained`, at r = 1: D
704,418,491,429,684,820,623 wei and E 234,806,163,809,894,940,207.

`ledger-cycle`: in round 2 N has 3,000,000 micro-USDC of the 5,000,000 in
fees and 400 of the 1,250 LPT of stake, 100 from O and 300 from D. O
bonds 100 LPT more in round 2, which counts from round 3: N's stake is
400 LPT in round 2 and 500 from round 3. N's stake share is the smaller,
so N is eligible for 300,551,889,676,665,523,465 wei; M and K, each with
1,000,000 micro-USDC, are eligible for 187,844,931,047,915,952,166 by their
fee share. `ledger-mint-order`: the bonds made in round 1 count from round
2, so round 1 has fees and zero total stake and pays its nodes 0.

**Would change it.** Paper text that drops the stake cap (D-04); paper
text defining the network total as score-weighted or excluding zero-scored
nodes, or a run showing self-paid dilution driving honest nodes off at a
scale the stake cap does not bound (D-22); an authoritative arithmetic
requirement, or implementation limits that make exact arithmetic
impractical (D-07); paper text reading stake other than at the start of
the round (D-11).

## R-09 Unminted

Origin: `Paper`; ours for zero totals and for issuance; interpretation for
the withheld part. Delivery: `Decide now`. Rests on: D-04, D-07, D-08,
D-21, D-23, D-29.

**The paper says.** The part of a round's node emission that nodes cannot
claim because their stake share is below their fee share is not minted;
realized issuance runs at or below the schedule (p. 8). A score below 1.0
pays a node only that fraction of its capped reward (p. 4).

**Unclear.** Whether the remainder of rounding, a round in which no node
can be paid, and the part a score withholds are treated the same way.
When a round's issuance is final, and who mints it.

**We build.** What the stake cap, a score below 10^6 and the rounding of
R-08's one division leave of the node share is never minted, carried
forward or shared out among other nodes (D-21). The dust of a holder
split is not among them: it is minted to Claims with the pot and never
paid (R-24, D-29). Ours: so is the whole node share in a round with zero
total fees or zero total stake. An entitlement not claimed in its claim
window is never minted (R-12, D-08). A round's never-minted amount is
C(r) minus everything minted for it. A round's never-minted amount is
final when its claim window closes.

Ours: test LPT's genesis supply is created once by its constructor, not by
a mint, and belongs to no round. Everything minted afterwards is minted by
Claims, the only minter, for one earning round R: R's validator and
treasury amounts (R-10, R-14) and each claim's reward (R-12, R-24). A mint
by any other address is refused with `NOT_AUTHORIZED`. What is minted for
R never exceeds C(R - 1), and the supply is always the genesis supply plus
what has been minted for each round. Round R's issuance is final at the
end of R + d + 21, d the reward delay in force for R (R-12): every claim
has been made or has expired, and R's two shares are fixed amounts,
minted once whenever the call comes.

**Example.** `round-roadmap`: C(0) is 1,000,000,000,000,000,000,000 wei
(`ceiling-0`) and 624,000,000,000,000,000,000 is minted, so
376,000,000,000,000,000,000 is never minted. `round-thirds` mints
999,999,999,999,999,999,999 wei of the same ceiling: the rounding dust of
three equal nodes is never minted. `round-no-fees` and `round-no-stake`
each mint 60,000,000,000,000,000,000 wei, validators and treasury only.
`ledger-cycle`: of round 2's ceiling C(1), 999,175,165,148,489,107,267,
454,424,865,109,532,845,983 is minted and 544,750,300,038,956,261,284 is
never minted, among it the half of M's reward its score of 500,000
withholds, 93,922,465,523,957,976,083, and the whole of K's at score 0,
187,844,931,047,915,952,166. `ledger-mint-order`: 60,000,000,000,000,000,000
is minted for round 1 and 294,756,673,718,804,286,642 for round 2, where M
lets its 469,612,327,619,789,880,415 expire; the 1 wei of N's split that
stays in Claims is minted.

**Would change it.** Paper text that redistributes the capped part
(D-04), or that sends the withheld part somewhere (D-21). A run where
most of the pool stays unminted and stake does not move is evidence about
the design for the owner, not about what the paper says.

## R-10 Validators and treasury

Origin: `Paper`; ours for the trigger, the treasury address and the
remainder. Delivery: `Decide now`. Rests on: D-10, D-29, D-40; no ruling
for the trigger and the address.

**The paper says.** The validator and treasury shares of each round's
emission are minted in full (p. 8). The validator share is split among
the active validators (p. 4), proportionally to each one's delegated
stake, with each validator's reward cut deciding what it keeps and what
passes to its delegators, and the treasury share goes to the DAO treasury
(p. 13).

**Unclear.** How the validator amount divides among validators: equally
(p. 4) or by delegated stake (p. 13); that is R-14 now. Who mints the two
amounts, and when. Where the treasury share goes on a testnet, and where
what the division leaves goes.

**We build.** validators = floor(C x v / 10^6) and treasury =
floor(C x t / 10^6), with C, v and t the ceiling and the validator and
treasury shares of R, the earning round (R-05, R-07): 10,000 and 50,000
at deployment. They are minted in full for every round whatever the fees,
stake or scores.

Ours: after round R has ended anyone may mint R's two amounts, once, and
the call never expires. A call before R has ended is refused with
`CLAIM_TOO_EARLY`, and a second call for R with `ALREADY_CLAIMED`. A mint
that names round 0 is refused with `NO_SUCH_ROUND` (R-18).
Claims divides the validator amount among R's members as R-14 says and
splits each member's pay as R-24 does: each cut to the member's payout
address, read when the mint runs, and each pot to Claims. The treasury
amount, and whatever R-14's division leaves, goes to the treasury address
fixed at deployment. So does the whole validator amount when R has no
members or their stake at the start of R is 0. The cuts, the pots and
that remainder are the validator amount, so R's two amounts are minted in
full, as before.

**Example.** `ceiling-1-shares`: validators 9,991,751,651,484,891,072 wei
and treasury 49,958,758,257,424,455,363 wei of C(1), as in
`round-chained`. Every round case at r = 0, including `round-no-fees`
and `round-no-stake`: validators 10,000,000,000,000,000,000 wei and
treasury 50,000,000,000,000,000,000 wei. `ledger-cycle`: round 2 has no
members, so both amounts, 59,950,509,908,909,346,435 wei in all, go to the
treasury; a second mint of round 2's shares is refused with
`ALREADY_CLAIMED`. `ledger-mint-order`: round 2's shares are minted in
round 3 before round 1's, and a third call for round 2 in round 31 is
refused with `ALREADY_CLAIMED`. `pay-thirds`: the division leaves 1 wei,
which goes to the treasury; `pay-empty` and `pay-no-stake`: the whole
10,000,000,000,000,000,000 wei does. `ledger-round-zero`: a mint of round
0's shares is refused with `NO_SUCH_ROUND` in round 1 and again in round
500; round 1, which has no members, mints its 10 LPT and 50 LPT to the
treasury in round 2, and a second mint of it in round 500 is refused with
`ALREADY_CLAIMED`. `ledger-authority`: round 2's treasury amount is
49,958,758,257,424,455,363 wei and round 5's 49,835,237,023,244,949,545,
each at 5 %, and round 11's 89,260,398,009,408,065,240 at 9 %, each of its
own round's ceiling.

**Would change it.** Paper text that ties these shares to fees or to the
node share. Paper text naming who mints them, or a treasury contract to
send to. Paper text sending what the division leaves elsewhere (D-10).

## R-11 Score

Origin: `Interpretation`; ours for the refusal. Delivery: `Decide now`.
Rests on: D-09, D-21, D-23, D-36.

**The paper says.** Each active validator scores each node from 0.0 to
1.0; the median across active validators sets the fraction of the capped
reward the node receives, 1.0 meaning all of it and 0.0 none (p. 4). The
score applied is the median current when the claim is made (p. 6). The
contract that produces scores can be swapped by governance (pp. 5, 12).

**Unclear.** Missing votes, quorum and even counts (D-09). What a claim
does with a score outside the range, since the scorer can be swapped.

**We build.** A score is an integer from 0 to 10^6. It is the median over
the members of the claim round (R-13) of each member's vote on the node,
as the votes stand at the claim. A member that has not voted counts as
10^6. There is no quorum: with no members the score is 10^6. With an even
count it is the floor of the mean of the two middle votes. Only a member
of the current round may vote, and any other vote is refused with
`NOT_VALIDATOR`. A vote above 10^6 is refused when cast with
`SCORE_OUT_OF_RANGE`, and the earlier vote is kept. A vote stands until
replaced. A validator leaves when a round starts without it; its votes
count for every claim in every round it is a member of, and lapse at the
start of the first round it is not. A member whose exit for the next round
is undone by a later call in the same round never left, and its votes
stand.

A claim applies the score inside R-08's one division. Ours: a claim that
reads a score above 10^6 is refused with `SCORE_OUT_OF_RANGE`; the
entitlement stays intact and no other node is affected. A score of
exactly 10^6 pays in full. The part a score below 10^6 withholds is never
minted (R-09, D-21).

**Example.** `claim-open` at score 1,000,000 pays 188,000,000,000,000,000,000
wei. `claim-rescored` at score 500,000 pays 94,000,000,000,000,000,000 wei.
`round-one-division` at score 750,000 pays 235,000,000,000,000,000,000 wei
a node. `claim-score-range` at score 1,000,001 is refused with
`SCORE_OUT_OF_RANGE`.

With three members, the median of the standing votes: `score-none`, no
votes, 1,000,000; `score-one-zero`, one vote of 0, 1,000,000, so one vote
cannot cut a node; `score-reduced`, two votes of 500,000, 500,000;
`score-majority-zero`, two votes of 0, 0; `score-spread`, 0, 250,000 and
750,000, 250,000. With two members: `score-even`, 0 and no vote, 500,000;
`score-even-floor`, 0 and 333,333, 166,666. `score-empty`, no members,
1,000,000. In `validators-seats` a vote by D, which is not a member, is
refused with `NOT_VALIDATOR`, and A's vote of 1,000,001 with
`SCORE_OUT_OF_RANGE`, A's vote of 0 standing; when B leaves and returns,
its vote of 0 has lapsed and the score is 1,000,000 again.
`validators-return-in-round`: A and B vote 0 in round 7 and B opts out in
round 8. A claim later in round 8 still reads B's vote, since B is a member
of round 8, and the score is 0. B opts in again in the same round, so it
never leaves, and its vote stands: the score in round 9 is 0 too. In
`validators-return-displaced` C's vote of 0 stands in the same way after C
is displaced and seated again within round 8, and in
`validators-return-seat-taken` B's after its opt-in displaces C; both
score 0 in round 9.
`ledger-cycle`: M's claim at 500,000 is paid 93,922,465,523,957,976,083 of
its 187,844,931,047,915,952,166, and K's at 0 is paid 0.

**Would change it.** S-06 enforcing the score range where scores are
produced, at the same seam as the swap (D-23). Paper text requiring votes
or fixing a quorum; a run where two-member rounds are common and one lone
zero halving a score looks arbitrary to operators (D-09). Paper text
sending the withheld part somewhere (D-21).

## R-12 Claim

Origin: `Paper`; ours for who, where, expiry, order and a claim by an
unregistered address. Delivery: `Decide now`. Rests on: roadmap section
7.2 for paying once; D-08, D-23, D-26, D-27, D-39, D-40, D-42; R-26.

**The paper says.** A round's rewards are not claimable at once: round n
becomes claimable at round n + 7, and the score applied is the median
current when the claim is made (pp. 6, 12). The node's reward is shared
with its delegators through the reward cut (pp. 3, 7).

**Unclear.** Who claims, where it pays, whether a zero score uses up the
entitlement, and whether a claim expires. Which refusal a claim is given
when two apply.

**We build.** Round n is claimable in rounds n + d to n + d + 21, d the
reward delay in force for round n (R-25), 7 at deployment: from the
reward delay to the end of the claim window. Only the node's operator key
(R-26) claims its (round, node) entitlement, one round per call; a
delegator may not.
The claim pays R-08's amount using round n's ceiling, shares, fees and
stake, and the score read at claim from the score source in force when
the claim runs (R-20): it mints the node's cut to its payout address and
the rest, the pot, to Claims for the node's holders (R-24). The payout
address is read when the claim executes.

A claim before round n + d is refused with `CLAIM_TOO_EARLY`, after round
n + d + 21 with `CLAIM_EXPIRED`, and from any other address, a delegator's
included, with `NOT_AUTHORIZED`; each leaves the entitlement as it was. A
claim of round 0 is refused with `NO_SUCH_ROUND` (R-18). Ours: a claim by
an address that is not a registered node, acting for itself, is refused
with `NOT_REGISTERED`, as R-01 refuses its payout.
An entitlement not claimed by the end of round n + d + 21 closes and is never
minted (R-09). Ours: each (round, node) entitlement pays once; claiming it
again is refused with `ALREADY_CLAIMED` and nothing more is paid. A score
of 0 pays 0 and closes the entitlement; a score above 10^6 is refused
(R-11) and leaves it open. When two refusals apply, the first of
`NOT_AUTHORIZED`, `NO_SUCH_ROUND`, `NOT_REGISTERED`, `CLAIM_TOO_EARLY`,
`CLAIM_EXPIRED`, `ALREADY_CLAIMED`, `SCORE_OUT_OF_RANGE` is given (D-26,
D-39, D-40), the general order of the units section; `claim-order`
combines two.

**Example.** Every `claim` case is a claim by node A of its
`round-roadmap` reward, earned at emission index r = 0, which is on-chain
round 1. A `claim` case counts its rounds as r (the units section), so
its round 7 is on-chain round 8, seven rounds after the round earned; none
names on-chain round 0. `claim-early`, in round 6:
`CLAIM_TOO_EARLY`. `claim-open`, in round 7: pays
188,000,000,000,000,000,000 wei.
`claim-rescored`, in round 7 at score 500,000: pays
94,000,000,000,000,000,000 wei. `claim-again`, in round 8 after it has
paid: `ALREADY_CLAIMED`. `claim-last`, in round 28, the last of its
window: pays 188,000,000,000,000,000,000 wei. `claim-expired`, in round
29: `CLAIM_EXPIRED`. `claim-zero`, at score 0: pays 0. `claim-order`, in
round 6 at score 1,000,001: `CLAIM_TOO_EARLY`; in round 7 the same claim
is `claim-score-range`.

`ledger-window`: round 2 is claimable in rounds 9 to 30. N's claim in
round 9 at score 1,000,001 is refused with `SCORE_OUT_OF_RANGE`; in round
30 at 1,000,000 it pays 469,612,327,619,789,880,415 wei, all of it to
Claims since N's cut is 0. E's claim for M in round 30 is refused with
`NOT_AUTHORIZED`, and M's own in round 31 with `CLAIM_EXPIRED`.
`ledger-cycle`: N's claim in round 8 is refused with `CLAIM_TOO_EARLY`
and M's claim for N in round 8 with `NOT_AUTHORIZED`, who before when; in
round 9 M's and D's claims for N are refused with `NOT_AUTHORIZED`, N's
own pays, and N's second with `ALREADY_CLAIMED`. `ledger-mint-order`: N
claims round 2 again in round 31 and is refused with `CLAIM_EXPIRED`,
when before once.

`ledger-round-zero`: A's claim of round 0 is refused with `NO_SUCH_ROUND`
in round 1 and in round 7, and H's claim of it for A in round 7 with
`NOT_AUTHORIZED`, who before the round named. A's claim of round 1, which
had no fees and no stake, pays 0 in round 8 and closes. `refusal-order`:
X, which never registered, claims round 1 in round 3 and is refused with
`NOT_REGISTERED`, not `CLAIM_TOO_EARLY`; its claim of round 0 with
`NO_SUCH_ROUND`, not `NOT_REGISTERED`.

`ledger-delays`: a reward delay of 3, announced in round 4, counts from
round 11. Round 10 still opens at round 17: N's claim in round 16 is
refused with `CLAIM_TOO_EARLY` and in round 17 pays. Round 11 opens at
round 14: refused in round 13, paid in round 14. Round 12 is claimable in
rounds 15 to 36, and N's claim in round 37 is refused with
`CLAIM_EXPIRED`. `ledger-score-source`: Scores2 replaces Scores from
round 13. N's claim of round 2 in round 12 reads Scores, 500,000, and
pays 469,612,327,619,789,880,415 wei; its claim of round 3 in round 13
reads Scores2, which has no vote on N and answers 10^6, and pays
938,449,950,010,539,852,638. `ledger-rotation`: after N's key passes to
K1 and then K2, K1's and N's own claims for N are refused with
`NOT_AUTHORIZED`, and K2's claim of round 3 in round 10 pays W
938,449,950,010,539,852,638 wei.

**Would change it.** Paper text fixing the score per round or naming an
unbounded claim window; a run where honest nodes lose rewards to mistaken
zeros corrected only after the window (D-08). A run where honest nodes
measurably fail to claim and their delegators lose at scale (D-27). A
batched claim that must name the bad round first (D-26). A replaced
score source that zeroes or fully pays honest claims before validators
populate it (D-42).

## R-13 Validator set

Origin: `Paper`; ours for the seat and unseat rules and the timing.
Delivery: `Decide now`; `Substitute` for N, 3 for the paper's 33.
Rests on: D-11, D-35, D-36, D-37, D-39, D-42, D-47 (ii); R-23, R-26.

**The paper says.** Node operators and validators are one set (p. 3).
Among nodes, the top N by total stake, their own and delegated, may
serve as validators but are not forced to; a node becomes an active
validator by choosing to register for it, and N starts at 33 (p. 4). A
top-ranked node that declines leaves its slot to the next-highest
registered node, and the set size is a governance parameter (p. 12).
Stake makes the set expensive to take over (p. 4), and there is no
separate election (p. 10).

**Unclear.** How the ranking follows stake that moves every round, and
when a change takes effect. What happens at a tie, at an empty seat and
for a member whose stake is gone. Whether a node must have stake to sit.

**We build.** A registered node opts in. The members are at most N
opted-in nodes, seated by calls. Anyone may seat an opted-in node whose
stake at the start of the current round is above 0, when a seat is free
or its stake is above the smallest member's; that member leaves, and of
equal smallest, the one seated last. Ties keep the incumbent. Anyone may
unseat a member whose stake at the start of the current round is 0; its
votes lapse when it leaves (R-11). Every change counts from the next
round, so a round's members are fixed when it starts. Stake is read only
at these calls, so the members need not be the N largest opted-in nodes:
one with more stake enters only when someone seats it. N is the
`VALIDATOR_SET_SIZE` in force for the round being seated, the next one
(R-25): 3 at deployment, for the paper's 33. It only rises, to at most 33
(D-47); a seat a rise adds is filled by calls, as any free seat is.

Ours: an opt-in seats the node when it can, as a seat call would, and is
not refused when it cannot. Seating a node that has not opted in is
refused with `NOT_OPTED_IN`; seating one whose stake is 0, or, with every
seat taken, not above the smallest member's, with `STAKE_TOO_LOW`. An
opt-in by an address that is not a registered node is refused with
`NOT_REGISTERED`; an opt-in, opt-out or vote for a node from any address
but its operator key (R-26) with `NOT_AUTHORIZED`, whatever else is
wrong. A node that opts
out leaves from the next round. An opt-in by a node that has opted out
in the same round is a fresh opt-in and seats it as a seat call would, so
a round's members are those on the list when the round starts, however
many calls changed it before then. A member seated again in the round in
which its exit was recorded keeps its seating order, so for the tie rule
a node is seated when its unbroken membership began, not at the call that
undid its exit. Only a node that a round has started without is seated
last when it returns. A return within the round never moves the
returning node ahead of any member; a node that stays out of a round is
seated behind every member when it comes back. Opting in again without
having opted out, seating a node that is already a member for the next
round, unseating a node that is not a member for the next round or has
stake, and opting out without having opted in change nothing and are not
refused. Nothing done per round visits the opted-in nodes (R-23).

**Example.** `validators-seats`: stake from round 2 is A 4,000 LPT, B
3,000, C 2,000, D 1,000 and E 0, and from round 4 D has 5,000. In round 2
A, B and C opt in and are members from round 3; D opts in and is not
seated, since 1,000 is not above C's 2,000; E opts in and is not seated,
with no stake. In round 4 seating D makes it a member from round 5 and C,
the smallest, leaves; seating E is refused with `STAKE_TOO_LOW`, and
seating C too, since 2,000 is not above B's 3,000. In round 5 B opts out
and leaves from round 6; in round 6 it opts in again and, a seat being
free, is a member from round 7. `validators-tie`: A, B and D have 2,000
LPT and C 3,000; A, B and C opt in, in that order, and D, opting in last,
is not seated, since 2,000 is not above 2,000. With 2,001 from round 4, D
is seated in round 4, and of A and B, equal smallest, B, seated last,
leaves: the members of round 5 are A, C and D. `validators-zero-stake`:
C's stake is 0 from round 5 and it is still a member in round 5 until
someone unseats it; then it leaves from round 6. E has 1,000 LPT from
round 6 and is not a member in round 7 until someone seats it; then it is
one from round 8.

`validators-return-in-round`: stake from round 5 is A 4,000 LPT, B 3,000
and C 2,000; they opt in in round 5 and are members from round 6. In round
8 B opts out and then opts in again; its seat is still free, so the opt-in
seats it, and the members of round 9 are A, B and C.
`validators-return-displaced`, with D at 5,000: in round 8 D opts in and
C, the smallest, leaves; A opts out, and seating C again puts it in A's
freed seat, so the members of round 9 are B, C and D.
`validators-return-seat-taken`: in round 8 B opts out, D opts in and takes
the freed seat, and B's opt-in, a fresh one, displaces C, since 3,000 is
above 2,000; the members of round 9 are A, B and D. Under D-36 (a) a free
seat goes to the first call, so a node that means to return should opt
back in at once. `validators-return-order`: A and B have 2,000 LPT, C
3,000 and E 2,001; A, B and C opt in in round 5, in that order. In round 6
A opts out and opts in again and keeps its place ahead of B. In round 7 E
opts in, and of A and B, equal smallest, B, seated last, leaves: the
members of round 8 are A, C and E.

`refusal-order-validators`: P and Q are registered and X is not; P has
100 LPT from round 2 and Q none. In round 3 X's opt-in for itself is
refused with `NOT_REGISTERED`, and Y's opt-in for X with
`NOT_AUTHORIZED`, who before the node named; seating Q, which has not
opted in and has no stake, with `NOT_OPTED_IN`, not `STAKE_TOO_LOW`; and
Q's vote of 1,000,001 on P with `NOT_VALIDATOR`, not
`SCORE_OUT_OF_RANGE`. Rounds 3 and 4 have no members.

`validators-set-size`: A, B, C and D have 4,000, 3,000, 2,000 and 1,000
LPT. A, B and C fill the three seats in round 1, and seating D is refused
with `STAKE_TOO_LOW`. A set size of 4, announced in round 1, counts from
round 8: seating D in round 6, for round 7, is still refused with
`STAKE_TOO_LOW`; in round 7, for round 8, a seat is free and D is a member
from round 8. In round 8 a size of 3 is refused with `VALUE_OUT_OF_RANGE`.
`validators-rotation`: V1's key passes to K1 in round 2. V1's own vote
and opt-out are then refused with `NOT_AUTHORIZED` and K1's take effect:
X's score is 500,000 in round 2, the median of K1's 500,000 for V1 and
V2's 400,000 with V3's missing vote counted in full, and 700,000 in round
3 with V1 gone. In round 3 V1's own opt-in is refused and K1's seats it
again, so the members of round 4 are V1, V2 and V3, and V1's vote having
lapsed, X's score is 1,000,000.

**Would change it.** Paper text that a declined slot stays empty. A run
where seats stay empty because no one calls seat, which would add a
keeper. The owner wanting 33 on the testnet. A run where equal-stake
churn is exploited, which would add a minimum margin (D-35). A run where
a rise lets dust-staked nodes hold the median, or a claim at 33 members
costs too much gas (D-47).

## R-14 Validator pay

Origin: `Paper`; ours for the integer form and the timing.
Delivery: `Decide now`. Rests on: D-07, D-10, D-29, D-42.

**The paper says.** Active validators share the validator part of each
round's mint (p. 4). The table on p. 13 distributes it to the validator
set and their delegators, proportionally to each validator's delegated
stake, and lets each validator set a reward cut deciding how much it
keeps and how much passes to its delegators. p. 4 says split among all
active validators, and p. 10 asks whether pay should depend on
participation.

**Unclear.** Equally (p. 4) or by stake (p. 13). Which members and which
stake count for a round. Whether votes change pay. The integer form, and
where what the division and the split leave goes.

**We build.** After round R ends, member m of R is paid

p_m = floor(C x v x s_m / (10^6 x S))

where C is R's ceiling C(r), v R's validator emission share (R-07),
10,000 at deployment, s_m is m's stake at the start of R and S is
the sum over R's members. Votes do not change it. p_m splits as R-24
splits a node reward, with m's one reward cut and its bonds as at the
start of R: the cut, floor(p_m x c / 10^6), is minted to m's payout
address read when the mint runs, and the pot, p_m less the cut, to
Claims for m's holders. Each holder withdraws its part, floor(pot x bond
/ s_m), once, separately from its parts of m's node rewards, with no
expiry. What those floors leave stays in Claims and is never paid. What
the division leaves goes to the treasury (R-10).

**Example.** `pay-weighted`, at r = 0 with A 5,000, B 3,000 and C 2,000
LPT: A 5,000,000,000,000,000,000 wei, B 3,000,000,000,000,000,000 and C
2,000,000,000,000,000,000; nothing more to the treasury. `pay-thirds`,
three members of 1,000 LPT: 3,333,333,333,333,333,333 wei each, and 1 wei
to the treasury. `pay-chained`, at r = 1 with A 1,000 and B 2,000 LPT: A
3,330,583,883,828,297,024 wei and B 6,661,167,767,656,594,048, each one
division from C(1). `pay-empty`, no members, and `pay-no-stake`, two
members with no stake: nobody is paid and the whole
10,000,000,000,000,000,000 wei goes to the treasury. `pay-split`: in
`pay-weighted`, A's cut is 100,000 and its bonds are its operator's
1,000 LPT and H's 4,000; its cut is 500,000,000,000,000,000 wei to its
payout, its pot 4,500,000,000,000,000,000, of which the operator's part is
900,000,000,000,000,000 and H's 3,600,000,000,000,000,000, with no dust.
`pay-split-dust`: in `pay-thirds`, A's cut is 150,000 and its bonds are
400 and 600 LPT; its cut is 499,999,999,999,999,999 wei, its pot
2,833,333,333,333,333,334, the parts 1,133,333,333,333,333,333 and
1,700,000,000,000,000,000, and 1 wei stays in Claims, never paid, while
the division's 1 wei goes to the treasury. `ledger-validator-pot`: V,
round 2's one member, is paid the whole validator amount.

**Would change it.** Paper text that sends the split's dust elsewhere.
The owner choosing to simulate p. 10's open question, or a participation
measure that is costly to fake (D-10).

## R-15 Stake

Origin: `Interpretation`; ours for which record. Delivery: `Decide now`.
Rests on: D-11, D-30, D-31, D-32, D-38, D-39, D-42, D-47 (iii).

**The paper says.** Delegators stake toward node operators with today's
bonding, unbonding and reward-cut mechanics (p. 3). Unbonding takes 21
rounds for all stake behind a node, the operator's and its delegators'
alike (pp. 5, 12). Moving a bond to another node must be eliminated or
wait 21 days before it applies to the new node, so a misbehaving node
cannot relocate its stake ahead of a score; that slows the active
delegation market the paper wants, an open question (pp. 5, 10, 12).

**Unclear.** When a bond counts. What a transfer does during its 21
rounds, and whether stake already leaving can be unbonded or returned.
Whether a holder may back several nodes. Which of a holder's records an
unbond or a rebond draws on.

**We build.** Anyone bonds test LPT to any number of registered nodes,
read at execution; a holder's bonds with one node are one balance. A
bond, unbond or rebond made in round R counts from R + 1. Let u be the
UnbondingPeriod in force in round R (R-25), 21 at deployment; a change
does not move an unbond or a transfer already made. A transfer made in
round R counts for the old node through R + u - 1 and for the new from
R + u, with no further action, and cannot be redirected. An unbond draws
only on the holder's bond staying at the node: bonded, rebonded or
arrived, and not leaving. Stake in transfer is unbonded by naming the
transfer, its old and new node, in whole or in part, the transfer
arriving last first; it stops counting for the old node from R + 1 and
never arrives. Unbonded LPT is withdrawable from R + u, u rounds after
the unbond. Until then the holder may rebond any amount of
it, only to the node it was counting for when unbonded, the latest unbond
first; what is rebonded is no longer withdrawable. An unbond, transfer,
unbond of a transfer or rebond above what the named record holds is
refused with `AMOUNT_TOO_LARGE`; a rebond to any other node is one of
them. A bond, transfer or rebond to an address that is not a registered
node is refused with `NOT_REGISTERED`. A transfer whose new node is its
old node, and an unbond that names such a transfer, is refused with
`SAME_NODE`; nothing is recorded. Both come before `AMOUNT_TOO_LARGE`
(the units section).

Ours: a holder's withdrawal takes all its withdrawable unbonded LPT at
once, and with none withdrawable is refused with `UNBOND_NOT_READY`.
StakeLedger holds every bonded LPT, staying, in transfer or arriving, and
every unbonded LPT not withdrawn. A holder's bond with a node, and a
node's stake, at the start of a round never change once that round has
started.

**Example.** `ledger-stake`: H bonds 300 LPT to P in round 1. In round 10
H transfers 100 to Q and unbonds 100 from P; a further transfer of 150 is
refused with `AMOUNT_TOO_LARGE`, since 100 stays at P. In round 12 H
unbonds 40 of the transfer; 70 more is refused with `AMOUNT_TOO_LARGE`,
since 60 is still in transfer. In round 14 a rebond of 200 to P is refused
with `AMOUNT_TOO_LARGE`, since 140 is waiting; H then rebonds 120 to P,
round 12's 40 first and then 80 of round 10's 100, leaving 20 waiting; a
rebond to Q is refused with `AMOUNT_TOO_LARGE`, since nothing H unbonded
was counting for Q. In round 30 an unbond of 1 at Q is refused with
`AMOUNT_TOO_LARGE`, since the transfer has not arrived, and H's withdrawal
with `UNBOND_NOT_READY`. In round 31 H withdraws 20 LPT, and an unbond of
61 at Q is refused with `AMOUNT_TOO_LARGE`. A bond to X, which never
registered, is refused with `NOT_REGISTERED`.

| Rounds | 1 | 2 to 10 | 11, 12 | 13, 14 | 15 to 30 | 31 on |
|---|---|---|---|---|---|---|
| P counts, LPT | 0 | 300 | 200 | 160 | 280 | 220 |
| Q counts, LPT | 0 | 0 | 0 | 0 | 0 | 60 |

StakeLedger holds 300 LPT until H's withdrawal and 280 after it. In
D-30's own figures, 100 LPT transferred from P to Q in round 10 and
unbonded in round 12 counts for P through round 12, never for Q, and is
withdrawable in round 33; unbonding 40 of it instead, 60 arrives at Q in
round 31. In D-32's, 100 LPT rebonded in round 14 counts for P from round
15.

`ledger-cycle`: F transfers 100 of its 250 LPT from K to M in round 9,
unbonds 40 of the transfer in round 10, and rebonds 40 in round 11; to M
that rebond is refused with `AMOUNT_TOO_LARGE`. K counts 250 through round
10, 210 in round 11, 250 in rounds 12 to 29 and 190 from round 30; M counts
660 from round 30. `ledger-validator-pot`: H transfers 1,000 of its 4,000
LPT from V to U and unbonds 1,000 from V in round 2, so V counts 5,000 in
round 2 and 4,000 in round 3; H rebonds the 1,000 in round 3, and V counts
5,000 from round 4.

`ledger-same-node`: H bonds 100 LPT to P in round 1. In round 5 H's
transfers of 60 and of 150 from P to P are both refused with `SAME_NODE`,
the second not with `AMOUNT_TOO_LARGE`. In round 6 H unbonds 50 from P,
and in round 7 its unbond of the P to P transfer is refused with
`SAME_NODE`. P counts 100 in rounds 2 to 6 and 50 from round 7. H's
withdrawal in round 26 is refused with `UNBOND_NOT_READY`, and in round
27 it withdraws 50 LPT; StakeLedger holds 100 through round 26 and 50
from round 27. `refusal-order`: with H's 100 LPT staying at P and nothing
unbonded, H's transfer of 150 from P to X, which never registered, and
its rebond of 10 to X are refused with `NOT_REGISTERED`, not
`AMOUNT_TOO_LARGE`; P counts 100 in every round from 2.

`ledger-delays`: an unbonding period of 42, announced in round 4, counts
from round 11. H's unbond of 100 LPT in round 10 is withdrawable in round
31, and its withdrawal in round 30 is refused with `UNBOND_NOT_READY`; its
unbond of 100 in round 11 is withdrawable in round 53, and its withdrawal
in round 52 is refused. `ledger-rotation`: K2, N's operator key, holds no
bond at N, so its unbond of 1 LPT from N is refused with
`AMOUNT_TOO_LARGE`; a rotation moves who acts for a node, never a bond.

**Would change it.** Paper text eliminating the transfer, or making one
irrevocable; a run showing stake in transfer exploited, such as holding
validator rank at a node its operator has left, or showing the 21-round
transfer still freezes the delegation market (D-11, D-30). Paper text
stating one node per delegator, or a run where split bonds game validator
rank or the median (D-31). Paper text saying unbonding is final, or a run
where unbond and rebond swing validator rank at a cost below a fresh
bond's (D-32).

## R-18 Round clock

Origin: `Placeholder`. Delivery: `Substitute` for the length, `Decide now`
for the count. Rests on: D-11 (b1), D-16 (proposed), D-23, D-28, D-40.

**The paper says.** Every delay is a number of rounds: rewards are
claimable 7 rounds later and unbonding takes 21 rounds (pp. 5, 12). It
speaks of 21 rounds and 21 days interchangeably (p. 5), and states the
emission schedule per round with a half-life of 840 rounds, about two
years (p. 7).

**Unclear.** How long a round is on a testnet, who starts one, and whether
the schedule and the delays count time or rounds actually started. The
current protocol computes the round from the block number while the node
signs against the last round started, so a late start expires tickets the
node still holds. What a call naming a round before the first does.

**We build.** Round 1 starts at deployment. Anyone may start round R + 1
once round R has lasted RoundLength blocks, 50, of the block number the
node reads; a start before that is refused with `ROUND_NOT_OVER`. Until
someone starts it, a round just lasts longer, and no round number is ever
skipped. The emission index of round R is r = R - 1, so a deployment's
first round emits C(0) (R-05). RoundLength is a test value (D-16).

There is no round 0. A call that names round 0 is refused with
`NO_SUCH_ROUND`: a mint of its shares (R-10), a claim of it (R-12), and a
withdrawal list that includes it (R-24), which moves nothing.

Ours: the emission schedule and every delay (the reward delay, the ticket
life and the release wait) advance by rounds started, so a late keeper
slows them, where p. 7 states the schedule in time. A slow clock defers
emission and never adds to it.

**Example.** Deployed at block 100: round 1, emission index 0, ceiling
C(0) (`ceiling-0`). Starting round 2 at block 149 is refused with
`ROUND_NOT_OVER`; at block 150 round 2 starts, with ceiling C(1) =
999,175,165,148,489,107,267 wei (`ceiling-1`). If nobody starts round 3
until block 400, round 2 has lasted 250 blocks and round 3 starts then;
no round number is skipped. No shared case holds blocks: a `ledger` step
takes its round as given, so the clock is checked by the contract tests,
not by the calculator. `ledger-round-zero`: every call that names round 0,
in round 1, 7 or 500, is refused with `NO_SUCH_ROUND`, except a claim of
it by an address that may not act for the node, which is
`NOT_AUTHORIZED`; nothing is ever minted for round 0, and Claims ends
holding 0.

**Would change it.** Paper text fixing the schedule in calendar time, or a
stage 3 run where a stalled clock lets a node hold its claim window open
against validators (D-28). The settled testnet round length (D-16).

## R-19 Rounding

Origin: `Interpretation`. Delivery: `Decide now`. Rests on: D-07, D-23,
D-29.

**The paper says.** Nothing about units or rounding. The reward is a plain
ratio (pp. 3, 12), emission is a ceiling (p. 8), and the simple, secure
path is preferred (p. 11).

**Unclear.** Every unit and every rounding direction.

**We build.** Every amount is an integer in its smallest unit: USDC in
micro-USDC, LPT in wei. Shares are compared by cross-multiplying, never by
dividing. Each amount is one division, rounded down, with no share or
intermediate amount stored in between. The LPT dust of a node's reward
(R-08) is never minted; the dust of a holder split (R-24) is minted to
Claims and never paid; the USDC remainder goes to the network. Arithmetic
is exact; a product that overflows is outside this rule.

**Example.** `settle-odd`: the odd micro-USDC of 1,000,001 goes to the
network, 500,001. `round-thirds`: 313,333,333,333,333,333,333 wei each,
and `round-thirds` mints 999,999,999,999,999,999,999 wei: the dust is not
minted. `round-small-node`: T is paid
313,333,333,333,333 wei. `round-one-division`: 235,000,000,000,000,000,000
wei each, exactly. `round-chained`: D is paid 704,418,491,429,684,820,623
wei. `split-d18`: of a pot of 451,200,000,000,000,000,000 wei the parts
take 451,199,999,999,999,999,999, and 1 wei stays in Claims, never paid.
`split-tiny`: 10 wei with a cut of 150,000 and bonds of 1 and 2 wei gives
a cut of 1, a pot of 9 and parts of 3 and 6, with nothing left.

**Would change it.** Paper text giving a rounding rule; a demonstrated
need to store a fixed-precision share for another consumer (D-07). A
tiny-bond pattern where the split rounds a holder above its exact share
(D-29).

## R-20 Scoring contract

Origin: `Paper`. Delivery: `Decide now`. Rests on: D-23, D-41, D-42.

**The paper says.** The contract address that governs the scores should
be a parameter governance can swap, so the community can later give that
authority to another mechanism (p. 5). The median of scores is the
default and can be replaced without a protocol migration (p. 12). The
validator set is defined in a separate row of the same table (p. 12).

**Unclear.** What a claim may trust from a source that can be replaced,
and what stays when it is.

**We build.** A claim reads `scoreOf(node)` from the score source in
force when the claim runs: the `SCORE_SOURCE` group (R-25), Scores at
deployment. A change counts from its announce round + 7, like any other,
and every claim run from then reads the new source, whatever round it
claims (D-42 s1). Any address with code may be named, and one without is
refused with `VALUE_OUT_OF_RANGE`. Any contract that answers may stand
there, and the claim still refuses a score above 10^6 (R-11). The validator set
and its pay stay when the score source is replaced. Ours: in stage 3 one
contract holds the validator set (R-13) and answers as the score source;
Claims holds the two addresses apart, the score source for scores and the
validator set for a round's members (R-14), and only the first is the
swappable one.

**Example.** `parameters-score-source`: Nobody, which has no code, is
refused with `VALUE_OUT_OF_RANGE`; Scores2, announced in round 6, is in
force from round 13 and Scores in round 12. `ledger-score-source`: the
claim run in round 12 reads Scores and the one run in round 13 Scores2
(R-12). `claim-score-range`: a claim that reads 1,000,001 is refused with
`SCORE_OUT_OF_RANGE`, whatever source answered. The contract tests build
Claims against a stand-in score source, and it passes the `claim` cases.

**Would change it.** A run where a replaced score source zeroes or fully
pays honest claims before validators populate it (D-42).

## R-21 Payer account

Origin: `Interpretation`. Delivery: `Decide now`. Rests on: D-24.

**The paper says.** Securing the ticket payment mechanism with an
unlimited number of nodes is an open question (p. 10). Nothing else on
how a payer backs its tickets.

**Unclear.** How a payer's money backs tickets when any address can
register. The current protocol divides a payer's reserve by the number of
active nodes, so free registrations dilute every payer's reserve until no
ticket pays.

**We build.** A payer has one deposit and one earmark for each node it
names. The deposit is shared by every node and paid first come. Each
earmark is funded directly by the payer and backs that node only; no other
node draws it. No reserve exists apart from earmarks. Funding adds to the
deposit, to one earmark, or to both, at once; funding an earmark for an
address that is not a registered node is refused with `NOT_REGISTERED`
(R-01) and nothing moves.

A node is shown the deposit less its pending release, and its own
earmark less its pending release (R-22), each never below 0. As D-24
rules, a node refuses a payer that has set nothing aside for it. That is
the node's own check: it accepts a ticket only when the deposit and its
earmark, as shown, each cover the face. The contract pays a ticket
redeemed anyway, from the deposit (R-04).

After every step, the USDC the Broker holds equals every payer's deposit
plus every earmark plus the network share (R-03).

**Example.** `ledger-demo`: a deposit of 10,000,000 and an earmark of
20,000 for N; six tickets of 10,001 are paid from the deposit, which ends
at 9,939,994, and the earmark stays 20,000. `ledger-reserve`: a deposit of
10,000,000 and earmarks of 5,000,000, 3,000,000 and 2,000,000 for N1, N2
and N3; N1's 15 tickets of 1,000,000 take the whole deposit and N1's
earmark, so N1 moves 15,000,000, N2 3,000,000 and N3 2,000,000, and the
deposit and every earmark end at 0. `ledger-empty`: N2's earmark of
1,000,000 does not pay N1's ticket. `ledger-release`: before any request
the node is shown a deposit of 4,000,000 and an earmark of 5,000,000.

**Would change it.** A stage 3 run where earmarking before first use
measurably delays or blocks first jobs on nodes chosen by discovery, with
no gateway-side fix, or where a gateway using many small nodes must lock
far more than it spends; paper text choosing a reserve rule (D-24).

## R-22 Release and ticket life

Origin: `Placeholder`. Delivery: `Decide now`. Rests on: D-24, D-28.

**The paper says.** Nothing on how long a ticket stays valid or on how a
payer takes money out.

**Unclear.** How long a node may hold a ticket before redeeming it. How a
payer lowers its deposit or an earmark without taking money a node has
already accepted tickets against, and what a top-up does while a request
is pending.

**We build.** A ticket is valid in its creation round and the two rounds
after it; redeemed later, it is refused with `TICKET_EXPIRED`. A ticket
whose creation round has not started is refused.

To take money out of its deposit or one earmark, the payer requests an
amount, at most that balance; a larger request is refused with
`AMOUNT_TOO_LARGE` and changes nothing. From the request, the node is
shown that balance less the pending amount (R-21), while tickets still
draw on the whole balance (R-04). Each redemption lowers a pending amount
to the balance left where it is above it, and a pending amount that
reaches 0 is cleared, so a later top-up is shown at once. Funding leaves a
pending request as it is. From 3 rounds after the request's round the
payer may release it: it receives the pending amount, the balance falls
by that amount, and the request is cleared. A release with none pending,
or before its round, is refused with `RELEASE_NOT_READY`. A new request on
the same balance replaces the pending one and restarts the wait, and a
request of 0 cancels it. There is no whole-account unlock.

The wait outlasts the ticket life: a ticket accepted before a request was
created no later than the request's round, so it has expired before the
release can take the money it was accepted against.

**Example.** `ledger-expired`: tickets created in round 8 pay when
redeemed in round 10 and are refused with `TICKET_EXPIRED` in round 11;
the deposit ends at 989,999. `ledger-release`, in round 10: the payer
funds a deposit of 4,000,000 and an earmark for N of 5,000,000, N accepts
a ticket of 4,000,000, and the payer requests the whole deposit and
3,000,000 of the earmark; N is then shown a deposit of 0 and a claimable
earmark of 2,000,000. In round 11 the ticket moves 4,000,000 from the
deposit, N is paid 2,000,000 and the network 2,000,000, and the deposit
request falls to 0 and is cleared; the payer funds the deposit with
1,000,000 and N is shown 1,000,000 and 2,000,000. In round 12 releasing
the earmark is refused with `RELEASE_NOT_READY`. In round 13 the earmark
release pays 3,000,000 and the deposit release is refused with
`RELEASE_NOT_READY`; a deposit of 1,000,000 and an earmark of 2,000,000
remain. Had the request taken effect at once, the ticket would have moved
2,000,000 of its 4,000,000.

**Would change it.** A run where a release takes money a node accepted a
ticket against; a funding program repeatedly tripped by a request being
refused rather than clamped (D-28).

## R-23 No count

Origin: `Interpretation`, ours. Delivery: `Decide now`. Rests on: D-25.

**The paper says.** Any address may register as a node at any time, with
no cap on their number (pp. 3, 12), and there is no reason to split stake
across identities, since there is no per-node bond or cap (p. 12).

**Unclear.** Whether registration needs a cost or a limit when it is free
and unlimited. Under the current reserve rule, free registration is itself
the attack (R-21).

**We build.** Ours: no payment or reward amount depends on how many nodes
are registered, and nothing done per round visits them. Registration stays
free and unlimited (R-01).

**Example.** No shared case holds a count, so the calculator has none to
get wrong. `ledger-reserve` gives the same numbers after 999 more
registrations; the contract tests check that.

**Would change it.** A run where thousands of junk registrations
measurably degrade discovery for a gateway despite bounded reads, which
would reopen a discovery filter rather than a fee; paper text adding a
registration cost (D-25).

## R-24 Delegation

Origin: `Interpretation`; ours for the integer form and the timing.
Delivery: `Decide now`. Rests on: D-10, D-18, D-27, D-29, D-39, D-40;
R-26.

**The paper says.** Delegators stake toward node operators with today's
bonding, unbonding and reward-cut mechanics, and an operator may stake to
itself from a separate wallet (pp. 3, 5). A node's reward should reflect
its reward cut and fee share, so delegators who move stake to a capped
node earn more (p. 4), and the node payout is distributed to the node and
its delegators through the reward cut (p. 7). Validators may set reward
cuts that decide how much of their pay they keep and how much passes to
their delegators (p. 13).

**Unclear.** Which cut and which bonds apply to a round. The integer form,
and where its remainder goes. Who triggers the payment, whether the
delegators' parts are paid or held for them, and whether they expire.
Whether the USDC half is shared too.

**We build.** A node's operator key (R-26) sets its reward cut, 0 to
10^6, default 0; the cut
for round R is the one set before R started. A cut above 10^6 is refused
with `CUT_OUT_OF_RANGE`, and a cut set from an address that may not act
for the node with `NOT_AUTHORIZED`. Ours: a cut set by an address that is
not a registered node, acting for itself, is refused with
`NOT_REGISTERED`, as R-01 refuses its payout. A node's claim of P (R-12)
mints cut = floor(P x c / 10^6) to its payout address and the pot P - cut
to Claims. Each holder's part is floor(pot x bond / S), with its bond and
the node's stake S at the start of R (R-08, R-15). A node whose stake at
the start of R is 0 is paid 0 and its holders' parts are 0, without the
division.

The holder withdraws its parts from Claims, as LPT to itself, for any
rounds it lists, once each, with no expiry. Bonding it is an ordinary bond
(R-15). A list that names a round twice, or a round already withdrawn, is
refused with `ALREADY_CLAIMED`, and a round its node has not claimed with
`NOT_CLAIMED`; a refused list moves nothing, and no case combines two of
these refusals in one list. A part of 0 withdraws as 0. A list that names
round 0 is refused with `NO_SUCH_ROUND`, whatever else it holds, for a
holder's parts and for validator parts alike.

A validator's pay for round R (R-14) splits as a claim does, with the
member's cut and bonds as at the start of R: the cut is minted to its
payout address read when R's shares are minted, and the pot to Claims,
kept apart from the pots of its node's claims. Each holder withdraws its
part of that pot, floor(pot x bond / S), with its own withdrawal, once
each, with no expiry, separately from its parts of the node's claims. A
validator part of a round whose shares are not minted is refused with
`NOT_CLAIMED`, and one taken twice, or named twice in one list, with
`ALREADY_CLAIMED`; a round in which the node was no member withdraws as
0. A member whose stake at the start of R is 0 is paid 0, and its parts
are 0. What the floors leave, of either pot, stays in Claims and is never
paid.

Ours: a holder's parts go to the holder, not to a payout address. Claims
accounts for the pots of the claims made and of the validator shares
minted, less the parts of each withdrawn; a part is paid only from its
own pot, and the LPT Claims holds is never less than that. LPT that
reaches Claims any other way is never paid out. A round with zero total
fees or zero total stake pays its nodes 0 (R-08), and their claims close
at 0. The node's USDC half is not shared with its holders in this profile
(D-18).

**Example.** `split-d18`, D-18's own example: P = 564 LPT, cut 200,000,
bonds of 13 and 80 LPT. The claim mints the cut,
112,800,000,000,000,000,000 wei, to the payout and the pot,
451,200,000,000,000,000,000, to Claims; the operator's part is
63,070,967,741,935,483,870 and the delegator's 388,129,032,258,064,516,129,
and 1 wei stays in Claims, never payable. `split-tiny`: P = 10 wei, cut
150,000, bonds of 1 and 2 wei: cut 1, pot 9, parts 3 and 6.

`ledger-cycle`: N's cut is 200,000. Its claim mints
60,110,377,935,333,104,693 wei to W, its payout, and
240,441,511,741,332,418,772 to Claims; O withdraws
60,110,377,935,333,104,693 by its 100 LPT at the start of round 2, not
its 200 since, and D 180,331,133,805,999,314,079 by its 300; D's second
withdrawal is refused with `ALREADY_CLAIMED`. M's cut is 0, so all of
its 93,922,465,523,957,976,083 goes to Claims as E's part, and E does not
withdraw it: Claims ends holding it. `ledger-window`: E's part of M's
round 2, which M never claimed, is refused with `NOT_CLAIMED`.
`ledger-mint-order`: N's cut is 250,000 and its bonds are G's 70 LPT and
J's 30. G's withdrawal of round 2 before N claims is refused with
`NOT_CLAIMED`. N's claim mints 58,701,540,952,473,735,051 wei to W and
176,104,622,857,421,205,156 to Claims. G's list [2, 2] is refused with
`ALREADY_CLAIMED` and moves nothing; [2, 1] pays 123,273,236,000,194,843,609,
round 1's part being 0; [1] is then refused with `ALREADY_CLAIMED`. J
withdraws 52,831,386,857,226,361,546 in round 31, after the claim window,
and 1 wei stays in Claims.

`ledger-validator-pot`: V's cut is 100,000 and its bonds at the start of
round 2 are O's 1,000 LPT and H's 4,000. H's validator part of round 2,
asked for before the shares are minted, is refused with `NOT_CLAIMED`.
The mint pays V 9,991,751,651,484,891,072 wei: its cut,
999,175,165,148,489,107, to W and its pot, 8,992,576,486,336,401,965, to
Claims. H withdraws 7,194,061,189,069,121,572 by its 4,000 LPT at the
start of round 2, not its 3,000 when it withdraws, which would give
6,744,432,364,752,301,473; a second withdrawal is refused with
`ALREADY_CLAIMED`, and its part of V's node reward for round 2, which V
never claimed, with `NOT_CLAIMED`. O withdraws 1,798,515,297,267,280,393
and Claims ends holding 0. `pay-split` and `pay-split-dust` show the same
split of a validator's pay, the second with 1 wei of dust.

`ledger-round-zero`: H's lists [0], of A's node parts and of its
validator parts, are refused with `NO_SUCH_ROUND` in round 1. A's claim
of round 1 pays 0, A's stake at the start of round 1 being 0, so H's part
is 0 without the division and its list [1] withdraws 0; its list [1, 0]
is then refused with `NO_SUCH_ROUND`, not `ALREADY_CLAIMED`.
`ledger-same-node`: P sets its cut to 100,000 in round 1, 300,000 in
round 2 and 500,000 in round 3. Its claim of round 2 in round 9, the
whole 939,224,655,239,579,760,830 wei of C(1)'s node share, reads the
cut set before round 2 started: 93,922,465,523,957,976,083 wei to P and
the pot, 845,302,189,715,621,784,747, to Claims. The cut set during round
2 would give 281,767,396,571,873,928,249 and the one current at the
claim 469,612,327,619,789,880,415. `refusal-order`: Q's cut of 1,000,001
for P is refused with `NOT_AUTHORIZED`, not `CUT_OUT_OF_RANGE`, and X's
cut of 1,000,001 for itself, X never having registered, with
`NOT_REGISTERED`. `ledger-rotation`: once K2 is N's operator key, K1's
cut for N and N's own are refused with `NOT_AUTHORIZED`.

**Would change it.** The owner placing USDC distribution in stage 3's
exit, or finding that the fee cut cannot be added later without changing
the claim path (D-18). A run where a cut raised for the next round
measurably strips delegators who cannot leave in time; a tiny-bond
pattern where the split rounds a holder above its exact share; a finding
that Claims holding undrawn LPT is an exposure today's minter does not
already carry (D-29). A run where honest nodes measurably fail to claim
and their delegators lose at scale, or the owner wanting today's
compounding into the bond (D-27).

## R-25 Parameters

Origin: `Paper` for which values may change; ours for the announcement,
the notice, the ranges and the refusals. Delivery: `Decide now`. Rests
on: D-12, D-41, D-42, D-43, D-47; D-49, decided in the review of spec
S-10.

**The paper says.** The design should leave room for governance to
adjust it (p. 2). The parameter table marks which values may change: the
validator set size, the score contract address, the unbonding period, the
reward delay, the fee cuts, the buyback cuts, the three emission shares,
the emission base and half-life, which may only be lowered, and the
buyback values (pp. 5, 12 to 14). Governance may speed the emission's
decline (p. 9). The allocations each sum to the whole (p. 11). Stake keeps
a weight in governance, with no mechanism given (p. 4). Seven rounds is
the paper's time to observe (p. 6).

**Unclear.** Who changes a value, by what call, and when the change
counts. Which round's value a payment reads when a change falls between
the round earned and the round paid. What ranges hold a value the paper
gives no range for. Whether any change may be stopped or undone.

**We build.** One authority, named at deployment and never changed,
alone announces and withdraws changes (D-41); any other address is
refused with `NOT_AUTHORIZED`. Another authority is another deployment.
There is no stop and no other way to change a value (D-43).

A change names one group by its number and gives all of its values, in
the group's order: a group is changed whole. A number that is not one of
the nine groups is refused with `NOT_ADJUSTABLE`; the values with no
group in the units section's table can never be named.

| Group | Values, in order | Accepted (D-47, D-49) | Read for |
|---|---|---|---|
| 1 `FEE` | node cut, network | each 0 to 10^6, summing to 10^6 | the round a fee is redeemed in (R-03) |
| 2 `EMISSION` | node, validators, treasury | each 0 to 10^6, summing to 10^6 | the earning round (R-07, R-10) |
| 3 `SCHEDULE` | drop, half-life | drop 0 to 840; half-life a divisor of 840, no higher than the one in force | the earning round (R-05) |
| 4 `REWARD_DELAY` | rounds | 1 to 840 | the earning round (R-12) |
| 5 `VALIDATOR_SET_SIZE` | members | from the size in force to 33 | the round being seated (R-13) |
| 6 `SCORE_SOURCE` | address | an address with code | the round the claim runs in (R-20) |
| 7 `UNBONDING` | rounds | 1 to 840 | the round of the unbond or transfer (R-15) |
| 8 `BUYBACK_CUTS` | burn, treasury, validators | exactly 10^6, 0, 0 | the round being bought (spec S-07) |
| 9 `BUYBACK_VALUES` | pool, TWAP seconds, slippage, rounds between buys | an authentic pool; TWAP 1,800 to 7,200; slippage 5,000 to 50,000; a buy every 1 to 840 rounds | the round being bought (spec S-07) |

"In force" in a check means in force in the round of the announcement.
The pool is authentic when its code hash is the pinned TestPool's and its
two tokens are this deployment's MockUSDC and TestLPT, in either order;
any other is refused with `POOL_INVALID`. The pool is checked when the
change is announced, though used from 7 rounds later; the buyback checks
it again at each use (spec S-07). Groups 8 and 9 are held and checked
here; what they mean is spec S-07's.

When two refusals apply, the first of `NOT_AUTHORIZED`, `NOT_ADJUSTABLE`,
`SCHEDULE_RAISED`, `VALUE_OUT_OF_RANGE`, `IDENTITY_NOT_WHOLE`,
`POOL_INVALID` is given: a raised half-life is what D-12 forbids, so it
is named first, and the pool, the one value read from another contract,
last. `VALUE_OUT_OF_RANGE` also covers a wrong number of values;
`SCHEDULE_RAISED` is given only to a schedule change of two values.
`IDENTITY_NOT_WHOLE` is given only when every value is in range.

Ours: a change announced in round A counts from round A + 7 (D-42). It
replaces any change of the same group still pending, so a group has at
most one pending change. In any round before it counts, the authority may
withdraw it; a withdrawal with none pending, including in the round the
change starts to count, is refused with `NOTHING_PENDING`. A replaced or
withdrawn change never counts. The values in force for round R are those
of the last change of the group counting in R or before, and the
deployment's before any does; round 0 has the deployment's. A refused
announcement or withdrawal changes nothing, and a pending change stays
pending.

Each value is read for the round the table names, never as it is now: a
round keeps the values in force when it started, whenever it is paid. A
schedule change records the triple R-05 gives, and its pending values are
that triple.

**Example.** `parameters-emission`, the result of spec S-10: G, the
authority, announces shares of 900,000, 10,000 and 90,000 in round 4; they
count from round 11, so round 10 has 940,000, 10,000 and 50,000 and round
11 the new ones, and round 5 keeps the old after round 11. An unbonding
period of 42, announced in round 6 and withdrawn in the same round, never
counts: round 13 has 21. A second withdrawal is refused with
`NOTHING_PENDING`, and so is a withdrawal of the shares in round 11, the
round they count from. Groups 0 and 10 are refused with `NOT_ADJUSTABLE`,
and N's announcement and withdrawal with `NOT_AUTHORIZED`.
`ledger-authority`: the same change pays N's rounds 2 and 5 at 94 % and
round 11 at 90 % (R-07), and a call for the round length, group 0, is
refused with `NOT_ADJUSTABLE`.

`parameters-schedule-replaced`: drop 100, announced in round 93, would
count from round 100; half-life 420, drop 0, announced in round 95,
replaces it before it counts and records (100, 420, 101), from the
deployment's schedule, which stays in force through round 101: round 100's
ceiling is 921,555,435,045,356,663,643 wei, round 101's
920,795,304,004,931,970,727 and round 102's, at position 102,
919,276,922,354,647,079,252. `parameters-half-life`, `parameters-drop`
and `parameters-drop-840`: R-05.

`parameters-buyback`: TWAPs of 1,799 and 7,201, slippages of 4,999 and
50,001, buys every 0 and 841 rounds, cuts of 900,000, 100,000 and 0, and
three values for group 9 are each refused with `VALUE_OUT_OF_RANGE`.
FakePool, which reports the two tokens but has another code hash, and
OtherPool, the pinned code with MockUSDC and another token, are each
refused with `POOL_INVALID`; FakePool with a TWAP of 1,799 with
`VALUE_OUT_OF_RANGE`. Pool2, the pinned code with TestLPT and MockUSDC in
that order, is accepted at both ends of every range, the second
announcement replacing the first, and from round 8 group 9 is Pool2,
7,200, 50,000 and 1. `parameters-delays`: a reward delay or an unbonding
period of 0 or 841 is refused with `VALUE_OUT_OF_RANGE`; 1, then 3, and
840, then 42, are accepted, each replacing the last. `parameters-set-size`:
in round 1, 2 and 34 are refused with `VALUE_OUT_OF_RANGE`, and 33 and
then 4 are accepted; with 4 in force in round 8, 3 is refused and 4
accepted. `parameters-score-source`: R-20.

`parameters-refusal-order`: N's announcement for group 0 is refused with
`NOT_AUTHORIZED`, and G's with `NOT_ADJUSTABLE`. A schedule change of drop
841 and half-life 1,680 is refused with `SCHEDULE_RAISED`, and three
values with half-life 1,680 with `VALUE_OUT_OF_RANGE`. Fee cuts of
600,000, 400,000 and 0, three values, and of 1,000,001 and 0 are refused
with `VALUE_OUT_OF_RANGE`; 600,000 and 500,000, and shares of 900,000,
10,000 and 80,000, with `IDENTITY_NOT_WHOLE`; FakePool with a TWAP of
1,799 with `VALUE_OUT_OF_RANGE`. N's withdrawal for group 11 is refused
with `NOT_AUTHORIZED`, G's with `NOT_ADJUSTABLE`, and G's for group 1,
with none pending, with `NOTHING_PENDING`. Fee cuts of 600,000 and
400,000 are then accepted and count from round 8.

**Would change it.** A run where outside operators need a constant, such
as the round length, changed live, or where one announced change does
damage a limit on its size would have caught and the seven-round notice
did not (D-41). Evidence that seven rounds is too short for nodes to
reprice (D-42). A run where a rise of the set size lets dust-staked nodes
hold the median; a need for a base or half-life the grid cannot reach; a
version that replaces emissions with fees and needs the burn cut to move;
manipulation and recovery runs that establish better buyback bounds
(D-47). The owner choosing a vote by stake or an authority stake can stop
(D-41), or an emergency stop (D-43).

## R-26 Operator key

Origin: ours. Delivery: `Decide now`. Rests on: no ruling; D-39 for who
acts for a node; R-01 for the node's permanent identity.

**The paper says.** Nothing on the key that acts for a node, or on
replacing it.

**Unclear.** Who may act for a node once its key may have leaked, how a
new key takes over, and what the old one keeps.

**We build.** Each registered node has one operator key, the node's own
address until it is rotated. Only the operator key acts for the node:
it claims (R-12), sets the reward cut (R-24) and the payout (R-01),
opts the node in or out of the validator set and votes for it (R-13),
and proposes the next key. Any other address, the node's own included
once rotated, is refused with `NOT_AUTHORIZED`.

The operator key proposes a next key; the proposal counts when that key
accepts it, in the accepting call, not after a notice. Each node has one
proposal slot: a new proposal replaces the one in it, proposing the
current operator key clears it, and an acceptance empties it. A proposal
has no expiry. An acceptance by any address but the key in the node's
slot, or with the slot empty, is refused with `NOT_AUTHORIZED`. A
proposal by any address but the operator key is refused with
`NOT_AUTHORIZED`; for an address that is not a registered node, acting
for itself, with `NOT_REGISTERED`; naming the address zero with
`OPERATOR_INVALID`, in that order.

A rotation moves who acts for the node, not who owns anything. The node's
identity stays: tickets name it, anyone may redeem them, and they pay its
payout address (R-03, R-04). A bond stays its holder's, and stake bonded
from the node's own address stays with whoever holds that key. The payout
stays where it was until the new key sets another.

**Example.** `ledger-rotation`: N proposes K1 in round 4 and K1 accepts.
In round 5 K1 proposes K2; K3's acceptance is refused with
`NOT_AUTHORIZED` and K2's takes effect. From then K1's and N's own claim,
reward cut, payout and proposal for N, and K1's acceptance, are each
refused with `NOT_AUTHORIZED`, and K2, holding no bond at N, cannot unbond
H's: it is refused with `AMOUNT_TOO_LARGE` (R-15). X's proposal for
itself, X never having registered, is refused with `NOT_REGISTERED`, and
K1's for X with `NOT_AUTHORIZED`. K2's claim of round 3 in round 10 pays
W, N's payout. In round 11 K2 proposes K3 and then K4, and K3's acceptance
is refused; K2 proposes itself, which clears the slot, and K4's acceptance
is refused; a proposal of zero is refused with `OPERATOR_INVALID`. K2
proposes K3, K3 accepts, and a second acceptance by K3 is refused. K2's
and K1's reward cuts are then refused, and round 11's ticket still pays W:
the payout is unchanged until K3 sets W2 in round 12, after K2's payout is
refused. `validators-rotation`: R-13.

**Would change it.** A run where the unmodified node cannot sign or be
paid across a rotation. The owner wanting a rotation to wait a notice, or
a proposal to expire. A need for a node to leave its identity, which R-01
makes permanent.
