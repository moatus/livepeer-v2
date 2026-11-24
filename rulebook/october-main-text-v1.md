# Rulebook `october-main-text-v1`

The rules the payments demo is built and tested against: the first slice's rules,
and the payment rules of a payer, a ticket and a round on-chain. The
profile follows the main text of the October 2026 Livepeer 2.0 litepaper
wherever the main text and the appendix or a diagram disagree. Pages are
PDF pages of that paper. It is paraphrased here, never quoted.

This profile is `october-main-text-v0` with R-01, R-03 and R-04 extended
and R-18, R-21, R-22 and R-23 added. Every other entry, and
every case of v0, is unchanged.

The worked numbers live in `vectors/october-main-text-v1.json`, the shared
examples file. Each entry below names the cases that show it. The
calculator and the contracts are tested against those cases, and where
this text and a case disagree, the disagreement is a defect to be decided
against this text.

Where the paper is silent or unclear, the choice is ours and is labelled
"Ours". Each entry names what it rests on. The council rulings were made in the owner's absence, and the owner keeps or changes each later. A
council ruling is not an owner decision. The round length was a proposed answer.

## How to read an entry

Each rule carries five fields: what the paper says, what is unclear, what
we build, an example, and what would change it. It also carries two tags
and the decisions it rests on.

- **Origin.** `Paper`: stated in the litepaper. `Interpretation`: our
  reading of an unclear or contradictory passage. `Placeholder`: our
  invention, standing in until something better exists.
- **Delivery.** `Decide now`: fixed before code is written. `Substitute`:
  a declared temporary stand-in. `Decide before the reward cycle`: only the paper's
  literal part is frozen here; the rest is an open decision.

## Units, parameters and codes

Every amount is an integer in its smallest unit: USDC in micro-USDC
(10^-6 USDC), LPT in wei (10^-18 LPT). The fee cut, the three emission
shares and a score are integers over 10^6: 1,000,000 is one whole.

| Parameter | Value |
|---|---|
| EmissionBase | 10^21 wei (1,000 LPT) a round |
| EmissionHalfLife | 840 rounds |
| RewardDelay | 7 rounds |
| Node cut | 500,000 |
| Node emission share | 940,000 |
| Validator emission share | 10,000 |
| Treasury emission share | 50,000 |
| RoundLength | 50 blocks (R-18) |
| Ticket validity | the creation round and the 2 rounds after it (R-22) |
| Release wait | 3 rounds (R-22) |

Rounds on-chain are numbered R from 1, starting at deployment (R-18). The
emission index of round R is r = R - 1. The ceiling, round and claim
cases count r, as v0 did; a `ledger` case counts R.

A refusal is an outcome with a stable code. These are rule outcomes. The
payments contracts name their errors after the codes they produce; their
ticket checks add `TICKET_INVALID` and one kept revert text, which belong
to the contracts' public shape, not to a rule.

| Code | When |
|---|---|
| `NOT_REGISTERED` | A payment is made to, an earmark is funded for, or a payout is set by, an address that is not a registered node (R-01) |
| `SCORE_OUT_OF_RANGE` | A claim reads a score above 10^6 (R-11) |
| `CLAIM_TOO_EARLY` | A claim is made before its round is claimable (R-12) |
| `ALREADY_CLAIMED` | An entitlement that has already paid is claimed again (R-12) |
| `ROUND_NOT_OVER` | A round is started before the current one has lasted RoundLength blocks (R-18) |
| `PAYOUT_INVALID` | A node sets its payout to the Broker's own address (R-01) |
| `NOTHING_TO_PAY` | A ticket would move 0: the payer's deposit and its earmark for that node are both 0 (R-04) |
| `TICKET_USED` | A ticket that has paid is redeemed again (R-04) |
| `TICKET_EXPIRED` | A ticket is redeemed after the second round following its creation round (R-22) |
| `AMOUNT_TOO_LARGE` | A release request is above the deposit or earmark it names (R-22) |
| `RELEASE_NOT_READY` | A release with none pending, or before its round (R-22) |

Four moments are kept apart. The earning round fixes the ceiling, the
rules, and every registered node's recognized fees and stake; they are
fixed when that round closes. The claim reads the score. The claim's
execution checks whether it may pay now.

## The shared examples

`vectors/october-main-text-v1.json` holds the parameters above and 35
cases: v0's 26, unchanged, and nine `ledger` cases. Every number in it is
a decimal string. A case has an `id`, a `kind` (`settlement`, `ceiling`,
`round`, `claim` or `ledger`), its `inputs`, and `expected`: exact
amounts, one `code`, or for a `ledger` case the outcome of its steps.
Removing every `expected` leaves the inputs alone.

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
  score is 10^6, so no case depends on where a withheld part goes, which remains open here.
- A **claim** case claims one node's reward from a round case
  (`earned_in`), at a claim round, with the score read at claim and
  whether that entitlement has already paid. It states `paid_wei`, or a
  code.
- A **ledger** case starts from a fresh deployment and runs its `steps`
  in order. Each step has a `step` number, the on-chain `round` it is made
  in, and an `action`:
  - `register`: `node` registers (R-01).
  - `set_payout`: `node` sets its payout address to `payout` (R-01).
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

  A step is either refused with one code, and then changes nothing, or
  takes effect. `expected` states the balances after the last step:
  `deposits_micro_usdc` for every payer; `earmarks_micro_usdc` for every
  payer and every node one of its `fund` steps names; `paid_micro_usdc`,
  the USDC the Broker paid out to every address a step names as a node or
  a payout; `released_micro_usdc`, what every payer has received by
  release; `network_micro_usdc`, the network share held; and
  `fees_micro_usdc`, each node's recognized fees (R-04) for every round
  and node of a `redeem` step, with `total_fees_micro_usdc` for each of
  those rounds. Every amount that is listed and nothing reached is 0. It
  then states, in step order, what each `observe` step was shown
  (`observed`) and the code of each refused step (`refused`). A step not
  listed under `refused` took effect.

---

## R-01 Registration

Origin: `Paper`; ours for "only registered", for permanence and for the
payout. Delivery: `Decide now`.

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
is not refused. Each node has a payout address, the node itself until it
sets another, and only the node sets its own. Setting a payout from an
address that is not registered is refused with `NOT_REGISTERED`. Setting
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
`NOT_REGISTERED`, and nothing moves or counts.

**Would change it.** A gateway design that must settle to an address that
is not a node. A discovery run where dead listings measurably hurt
gateways, which would reopen a way to leave the registry at public testnet deployment; the
owner preferring to pay the ticket's recipient directly, or a run showing
the payout breaks the unmodified node.

## R-02 Currency

Origin: `Paper`. Delivery: `Substitute` for the token.

**The paper says.** Nodes price in USD and are paid in USDC, in place of
ETH (pp. 6, 12).

**Unclear.** Nothing about the currency. The test token stands in for
USDC.

**We build.** Fees are mock USDC with six decimals, counted in micro-USDC.
Ours: no ETH amount and no price appears in any rule of this profile; the
emission is in LPT and depends on no price (R-05).

**Example.** `settle-even`: 2.50 USDC is 2,500,000 micro-USDC.

**Would change it.** A payment rail that settles in another unit.

## R-03 Fee split

Origin: `Paper`, rounding and the address ours. Delivery: `Decide now`.

**The paper says.** Of the USDC fees a node earns, half goes to the node
and half to the network, which buys LPT and burns it (p. 6). The node cut
and the network fee together are all of the gross fee (p. 11). The node's
half goes to it directly (pp. 6, 7).

**Unclear.** Which side takes an odd micro-USDC. Which address of the node
receives its half, and when.

**We build.** node = floor(gross x 500,000 / 10^6); network = gross - node,
held for buyback. The two always sum to gross. Ours: the odd unit goes to
the network, so a node paying itself never gains from rounding.

Ours: the node's half is paid to its payout address (R-01), read at
redemption, in the same transaction as the redemption. The network's half
stays in the Broker as the network share, held for buyback.

**Example.** `settle-even`: 2,500,000 splits into 1,250,000 and 1,250,000.
`settle-odd`: 1,000,001 splits into 500,000 for the node and 500,001 for
the network. `settle-one`: 1 gives the node 0 and the network 1.
`ledger-demo`: six tickets of 10,001 pay the node 5,000 each and the
network 5,001 each, 30,000 and 30,006 in all. `ledger-payout`: the node's
5,000 goes to its payout W.

**Would change it.** A tiny-payment pattern where the odd unit matters. The owner preferring to pay the ticket's recipient directly, or a
run showing the payout breaks the unmodified node.

## R-04 Recognized fee

Origin: `Interpretation`. Delivery: `Decide now`.

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
into thin rounds. A run where a node paid less than face
mis-accounts and stops serving a payer that still has funds.

## R-05 Emission ceiling

Origin: `Interpretation`. Delivery: `Decide now`.

**The paper says.** Emission follows a fixed schedule in LPT, halving
every two years as a smooth per-round decay: EmissionBase x
2^(-r / EmissionHalfLife), with a half-life of 840 rounds (p. 7). The
mint is fixed in tokens and is a ceiling (p. 8). It needs no oracle and
has no USD cap (p. 9). EmissionBase is in LPT, not USD (p. 13). The
diagram on p. 7 instead labels the schedule as a multiple of fees with a
USD cap.

**Unclear.** The diagram contradicts the text. The formula is irrational
for most rounds, and the paper gives no integer form. Which round is
round 0.

**We build.** C(r) is the largest integer c, in LPT wei, with
c^840 x 2^r <= EmissionBase^840: the exact floor of EmissionBase x
2^(-r/840). r is counted from the deployment's first round, so a new
deployment's first round emits the full EmissionBase. No fee, price or
USD cap enters it. Because floor(floor(x) / 2^k) = floor(x / 2^k),
C(r + 840k) = floor(C(r) / 2^k).

**Example.** `ceiling-0`: 1,000,000,000,000,000,000,000 wei.
`ceiling-1`: 999,175,165,148,489,107,267. `ceiling-420`:
707,106,781,186,547,524,400. `ceiling-839`:
500,412,757,882,842,450,669. `ceiling-840`:
500,000,000,000,000,000,000. `ceiling-841`:
499,587,582,574,244,553,633, which is floor(C(1) / 2). `ceiling-1680`:
250,000,000,000,000,000,000.

**Would change it.** A later paper or author statement that the diagram
is the current design. A contract at the real EmissionBase that
cannot reproduce all 840 residues within acceptable gas, which would make
a table derived from the exact floor the rule; a stage that must continue
a schedule across deployments.

## R-06 Emission base

Origin: `Placeholder`. Delivery: `Substitute`. The question of using real issuance remains open.

**The paper says.** EmissionBase is a launch snapshot of the existing
protocol's annualized issuance, converted to LPT a round (pp. 7, 13). The
half-life starts at 840 rounds, about two years (pp. 7, 13).

**Unclear.** How many rounds make a year when annual issuance becomes a
per-round amount.

**We build.** EmissionBase is 1,000 LPT a round, a test value. The
half-life stays 840 rounds.

**Example.** `ceiling-0`: 1,000,000,000,000,000,000,000 wei.

**Would change it.** Taking EmissionBase from real issuance.

## R-07 Emission shares

Origin: `Paper`. Delivery: `Decide now`. Rests on: no ruling; the paper
states the values.

**The paper says.** Each round's emission divides 94 % to nodes, 1 % to
validators and 5 % to the treasury, and the three are all of it (pp. 11,
13).

**Unclear.** Nothing about the values.

**We build.** Node 940,000, validators 10,000, treasury 50,000 millionths
of C(r). Each share is applied inside its own single division (R-08,
R-10), never stored as an amount first.

**Example.** `ceiling-1-shares`: of C(1), validators
9,991,751,651,484,891,072 wei and treasury 49,958,758,257,424,455,363 wei.
`round-roadmap`, at r = 0: validators 10,000,000,000,000,000,000 wei and
treasury 50,000,000,000,000,000,000 wei.

**Would change it.** A governance change to the shares, which this
profile does not have.

## R-08 Node reward

Origin: `Interpretation`. Delivery: `Decide now`.

**The paper says.** A node's reward share is the smaller of its share of
network fees and its share of network stake (pp. 3, 12), so stake sets a
reward ceiling (p. 4). The median score sets what fraction of that
capped reward the node receives (p. 4). The capped part is not minted
(p. 8). The parameter table on p. 13 instead describes the node share as
divided by share of work, weighted by score, with no stake term.

**Unclear.** p. 13 against pp. 3, 4, 8 and 12. Whether a node
later scored at zero still counts in the network total. The
integer form and where it rounds. What a share is when a total is
zero. The rejected forms on the cases below: flooring the 94 %
pool before the share would pay D in `round-chained` one wei less,
704,418,491,429,684,820,622; flooring the share before the score would
pay each node in `round-one-division` 234.999999999999999999 LPT; without
the zero-total rule, `round-no-stake` would read 0 <= 0 and pay A 940 LPT.

**We build.**

paid = floor(C x 940,000 x a x score / (10^6 x b x 10^6))

where C is the earning round's ceiling C(r), and a/b is fee/totalFee when
fee x totalStake <= stake x totalFee, and stake/totalStake otherwise. Fee
and stake amounts are never rescaled; they meet only inside this one
division. The totals are every registered node's recognized fees (R-04)
and stake for round r, fixed when the round closes. A node's fees count in
the total whatever score it is later given; a score scales only that
node's own reward, read at claim (R-11, R-12), and never reopens another
node's share. A round with zero total fees or zero total stake pays no
node.

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

**Would change it.** Paper text that drops the stake cap; paper
text defining the network total as score-weighted or excluding zero-scored
nodes, or a run showing self-paid dilution driving honest nodes off at a
scale the stake cap does not bound; an authoritative arithmetic
requirement, or implementation limits that make exact arithmetic
impractical.

## R-09 Unminted

Origin: `Paper`; ours for zero totals. Delivery: `Decide now`.

**The paper says.** The part of a round's node emission that nodes cannot
claim because their stake share is below their fee share is not minted;
realized issuance runs at or below the schedule (p. 8).

**Unclear.** Whether the remainder of rounding, and a round in which no
node can be paid, are treated the same way.

**We build.** What the stake cap and rounding leave of the node share is
never minted, carried forward or shared out among other nodes. Ours: so is
the whole node share in a round with zero total fees or zero total stake.
A round's never-minted amount is C(r) minus everything minted in it.

**Example.** `round-roadmap`: C(0) is 1,000,000,000,000,000,000,000 wei
(`ceiling-0`) and 624,000,000,000,000,000,000 is minted, so
376,000,000,000,000,000,000 is never minted. `round-thirds` mints
999,999,999,999,999,999,999 wei of the same ceiling: the rounding dust of
three equal nodes is never minted. `round-no-fees` and `round-no-stake`
each mint 60,000,000,000,000,000,000 wei, validators and treasury only.

**Would change it.** Paper text that redistributes the capped part. A run where most of the pool stays unminted and stake does not
move is evidence about the design for the owner, not about what the
paper says.

## R-10 Validators and treasury

Origin: `Paper`. Delivery: `Decide now`. Rests on: no ruling; the paper
states it. How the validator amount divides among validators remains open here,
open.

**The paper says.** The validator and treasury shares of each round's
emission are minted in full (p. 8).

**Unclear.** How the validator amount divides among validators: equally
(p. 4) or by delegated stake (p. 13). Not decided here.

**We build.** validators = floor(C x 10,000 / 10^6) and treasury =
floor(C x 50,000 / 10^6), minted in full every round whatever the fees,
stake or scores.

**Example.** `ceiling-1-shares`: validators 9,991,751,651,484,891,072 wei
and treasury 49,958,758,257,424,455,363 wei of C(1), as in
`round-chained`. Every round case at r = 0, including `round-no-fees`
and `round-no-stake`: validators 10,000,000,000,000,000,000 wei and
treasury 50,000,000,000,000,000,000 wei.

**Would change it.** Paper text that ties these shares to fees or to the
node share.

## R-11 Score

Origin: `Interpretation`; ours for the refusal. Delivery: `Decide before
the reward cycle`. The scoring and withheld reward questions remain open here.

**The paper says.** Each active validator scores each node from 0.0 to
1.0; the median across active validators sets the fraction of the capped
reward the node receives, 1.0 meaning all of it and 0.0 none (p. 4). The
contract that produces scores can be swapped by governance (pp. 5, 12).

**Unclear.** Missing votes, quorum and even counts. Where the part
of a reward that a score below 10^6 withholds goes. What a claim
does with a score outside the range, since the scorer can be swapped.

**We build.** A score is an integer from 0 to 10^6, the median of active
validators. This profile takes it as an input to the claim and applies it
inside R-08's one division. Ours: a claim that reads a score above 10^6 is
refused with `SCORE_OUT_OF_RANGE`; the entitlement stays intact and no
other node is affected. A score of exactly 10^6 pays in full. No example
here settles missing votes or withheld rewards.

**Example.** `claim-open` at score 1,000,000 pays 188,000,000,000,000,000,000
wei. `claim-rescored` at score 500,000 pays 94,000,000,000,000,000,000 wei.
`round-one-division` at score 750,000 pays 235,000,000,000,000,000,000 wei
a node. `claim-score-range` at score 1,000,001 is refused with
`SCORE_OUT_OF_RANGE`.

**Would change it.** Enforcing the score range where scores are
produced, at the same seam as the swap; the rulings on missing votes and withheld rewards.

## R-12 Claim

Origin: `Paper`; ours for paying once. Delivery: `Decide before the reward cycle`.
The rule pays each entitlement once. A council ruled on the claim window after v0. The reward-cycle profile incorporates that ruling.

**The paper says.** A round's rewards are not claimable at once: round n
becomes claimable at round n + 7, and the score applied is the median
current when the claim is made (pp. 6, 12).

**Unclear.** Who claims, where it pays, whether a zero score uses up the
entitlement, and whether a claim expires. Not decided here.

**We build.** Round n is claimable from round n + 7. The claim pays R-08's
amount using round n's ceiling, fees and stake, and the score read at
claim. A claim before round n + 7 is refused with `CLAIM_TOO_EARLY` and
the entitlement stays intact. Ours: each (round, node) entitlement pays
once; claiming it again is refused with `ALREADY_CLAIMED` and nothing more
is paid. This profile does not order the refusals, and no case combines
two of them.

**Example.** All are claims of node A's `round-roadmap` reward, earned in
round 0. `claim-early`, in round 6: `CLAIM_TOO_EARLY`. `claim-open`, in
round 7: pays 188,000,000,000,000,000,000 wei. `claim-rescored`, in round
7 at score 500,000: pays 94,000,000,000,000,000,000 wei. `claim-again`, in
round 8 after it has paid: `ALREADY_CLAIMED`.

**Would change it.** A ruling on the claim window.

## R-18 Round clock

Origin: `Placeholder`. Delivery: `Substitute` for the length, `Decide now`
for the count.

**The paper says.** Every delay is a number of rounds: rewards are
claimable 7 rounds later and unbonding takes 21 rounds (pp. 5, 12). It
speaks of 21 rounds and 21 days interchangeably (p. 5), and states the
emission schedule per round with a half-life of 840 rounds, about two
years (p. 7).

**Unclear.** How long a round is on a testnet, who starts one, and whether
the schedule and the delays count time or rounds actually started. The
current protocol computes the round from the block number while the node
signs against the last round started, so a late start expires tickets the
node still holds.

**We build.** Round 1 starts at deployment. Anyone may start round R + 1
once round R has lasted RoundLength blocks, 50, of the block number the
node reads; a start before that is refused with `ROUND_NOT_OVER`. Until
someone starts it, a round just lasts longer, and no round number is ever
skipped. The emission index of round R is r = R - 1, so a deployment's
first round emits C(0) (R-05). RoundLength is a test value.

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
not by the calculator.

**Would change it.** Paper text fixing the schedule in calendar time, or a
rewards run where a stalled clock lets a node hold its claim window open
against validators. The settled testnet round length.

## R-19 Rounding

Origin: `Interpretation`. Delivery: `Decide now`.

**The paper says.** Nothing about units or rounding. The reward is a plain
ratio (pp. 3, 12), emission is a ceiling (p. 8), and the simple, secure
path is preferred (p. 11).

**Unclear.** Every unit and every rounding direction.

**We build.** Every amount is an integer in its smallest unit: USDC in
micro-USDC, LPT in wei. Shares are compared by cross-multiplying, never by
dividing. Each amount is one division, rounded down, with no share or
intermediate amount stored in between. LPT dust is never minted; the USDC
remainder goes to the network. Arithmetic is exact; a product that
overflows is outside this rule.

**Example.** `settle-odd`: the odd micro-USDC of 1,000,001 goes to the
network, 500,001. `round-thirds`: 313,333,333,333,333,333,333 wei each,
and `round-thirds` mints 999,999,999,999,999,999,999 wei: the dust is not
minted. `round-small-node`: T is paid
313,333,333,333,333 wei. `round-one-division`: 235,000,000,000,000,000,000
wei each, exactly. `round-chained`: D is paid 704,418,491,429,684,820,623
wei.

**Would change it.** Paper text giving a rounding rule; a demonstrated
need to store a fixed-precision share for another consumer.

## R-21 Payer account

Origin: `Interpretation`. Delivery: `Decide now`.

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
earmark less its pending release (R-22), each never below 0. A node refuses a payer that has set nothing aside for
it. That is the node's own check: it accepts a ticket only when the deposit and its
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

**Would change it.** A rewards run where earmarking before first use
measurably delays or blocks first jobs on nodes chosen by discovery, with
no gateway-side fix, or where a gateway using many small nodes must lock
far more than it spends; paper text choosing a reserve rule.

## R-22 Release and ticket life

Origin: `Placeholder`. Delivery: `Decide now`.

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
refused rather than clamped.

## R-23 No count

Origin: `Interpretation`, ours. Delivery: `Decide now`.

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
registration cost.
