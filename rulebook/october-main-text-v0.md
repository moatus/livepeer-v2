# Rulebook `october-main-text-v0`

The rules the first slice is built and tested against. The profile follows
the main text of the October 2026 Livepeer 2.0 litepaper wherever the main
text and the appendix or a diagram disagree. Pages are PDF pages of that
paper. It is paraphrased here, never quoted.

The worked numbers live in `vectors/october-main-text-v0.json`, the shared
examples file. Each entry below names the cases that show it. The
calculator and the contracts are tested against those cases, and where
this text and a case disagree, the disagreement is a defect to be decided
against this text.

Where the paper is silent or unclear, the choice is ours and is labelled
"Ours". Each entry names what it rests on. The council rulings it names
(D-03, D-04, D-05, D-07, D-22, D-23) were made in the owner's absence, and
the owner keeps or changes each later. A council ruling is not an owner
decision.

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

A refusal is an outcome with a stable code. These are rule outcomes, not
the error names any contract must use.

| Code | When |
|---|---|
| `NOT_REGISTERED` | A payment is made to an address that is not a registered node (R-01) |
| `SCORE_OUT_OF_RANGE` | A claim reads a score above 10^6 (R-11) |
| `CLAIM_TOO_EARLY` | A claim is made before its round is claimable (R-12) |
| `ALREADY_CLAIMED` | An entitlement that has already paid is claimed again (R-12) |

Four moments are kept apart. The earning round fixes the ceiling, the
rules, and every registered node's recognized fees and stake; they are
fixed when that round closes. The claim reads the score. The claim's
execution checks whether it may pay now.

## The shared examples

`vectors/october-main-text-v0.json` holds the parameters above and 26
cases. Every number in it is a decimal string. A case has an `id`, a
`kind` (`settlement`, `ceiling`, `round` or `claim`), its `inputs`, and
`expected`: either exact amounts or one `code`. Removing every `expected`
leaves the inputs alone.

- A **settlement** case is one payment of `gross_micro_usdc` to an address
  that is or is not a registered node.
- A **ceiling** case is one round number.
- A **round** case is one round number and the round's registered nodes,
  each with its recognized fees, its stake and its score. The round's
  totals are taken over the nodes listed. `paid_wei` is each node's reward
  at that score. `minted_wei` is given only where every score is 10^6, so
  no case depends on where a withheld part goes (D-21, open).
- A **claim** case claims one node's reward from a round case
  (`earned_in`), at a claim round, with the score read at claim and
  whether that entitlement has already paid.

---

## R-01 Registration

Origin: `Paper`; ours for "only registered". Delivery: `Decide now`.
Rests on: D-23.

**The paper says.** Any address can register as a node at any time, and
no LPT bond is required (p. 3). There is no cap on the number of nodes
(pp. 3, 12). A new node has no ramp-up period before it can earn rewards
(p. 10).

**Unclear.** What happens to a payment sent to an address that has not
registered. The current protocol refuses fees to a recipient that is not
an active transcoder, a cap the paper removes.

**We build.** Any address registers, at any time, with no bond and no cap,
and can earn from the round in which it registers. Ours: only a registered
node can be paid. A payment to any other address is refused with
`NOT_REGISTERED`; nothing moves and nothing counts as a fee anywhere.

**Example.** `settle-even` pays a registered node 1,250,000 micro-USDC of
2,500,000. `settle-unregistered` sends the same 2,500,000 micro-USDC to an
address that is not registered and is refused with `NOT_REGISTERED`.

**Would change it.** A gateway design that must settle to an address that
is not a node (D-23).

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

Origin: `Paper`, rounding ours. Delivery: `Decide now`. Rests on: D-07,
D-23.

**The paper says.** Of the USDC fees a node earns, half goes to the node
and half to the network, which buys LPT and burns it (p. 6). The node cut
and the network fee together are all of the gross fee (p. 11).

**Unclear.** Which side takes an odd micro-USDC.

**We build.** node = floor(gross x 500,000 / 10^6); network = gross - node,
held for buyback. The two always sum to gross. Ours: the odd unit goes to
the network, so a node paying itself never gains from rounding.

**Example.** `settle-even`: 2,500,000 splits into 1,250,000 and 1,250,000.
`settle-odd`: 1,000,001 splits into 500,000 for the node and 500,001 for
the network. `settle-one`: 1 gives the node 0 and the network 1.

**Would change it.** A tiny-payment pattern where the odd unit matters
(D-23).

## R-04 Recognized fee

Origin: `Interpretation`. Delivery: `Decide now`. Rests on: D-05.

**The paper says.** Nothing defines the fee that a node's fee share is
counted in. It speaks of fees only as USDC paid to nodes and split (pp. 6,
11), and argues that a node faking volume pays half of it into the burn to
chase a fixed pool (p. 9).

**Unclear.** Whether a fee is money actually settled or value reported or
signed, and, for tickets, which round a fee counts in.

**We build.** A node's fees for round r are the gross USDC settled to it
on-chain in round r: the amount actually moved, before the split. A ticket
counts in the round it is redeemed, not the round it was signed. Signed
but unsettled value counts for nothing. A node paying itself counts. Only
a registered node's settlements count (R-01).

**Example.** In `settle-even`, `settle-odd` and `settle-one` the
recognized fee is the gross: 2,500,000, 1,000,001 and 1 micro-USDC. In
`round-self-payer`, S settles 250,000,000 micro-USDC to itself and it
counts in the round's total.

**Would change it.** Paper text defining the fee share by signed or
expected value; a run where nodes gain materially by timing redemptions
into thin rounds (D-05).

## R-05 Emission ceiling

Origin: `Interpretation`. Delivery: `Decide now`. Rests on: D-03, D-23.

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
is the current design (D-03). A contract at the real EmissionBase that
cannot reproduce all 840 residues within acceptable gas, which would make
a table derived from the exact floor the rule; a stage that must continue
a schedule across deployments (D-23).

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

Origin: `Interpretation`. Delivery: `Decide now`. Rests on: D-04, D-07,
D-22, D-23.

**The paper says.** A node's reward share is the smaller of its share of
network fees and its share of network stake (pp. 3, 12), so stake sets a
reward ceiling (p. 4). The median score sets what fraction of that
capped reward the node receives (p. 4). The capped part is not minted
(p. 8). The parameter table on p. 13 instead describes the node share as
divided by share of work, weighted by score, with no stake term.

**Unclear.** p. 13 against pp. 3, 4, 8 and 12 (D-04). Whether a node
later scored at zero still counts in the network total (D-22). The
integer form and where it rounds (D-07). What a share is when a total is
zero (D-23). The rejected forms on the cases below: flooring the 94 %
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

**Would change it.** Paper text that drops the stake cap (D-04); paper
text defining the network total as score-weighted or excluding zero-scored
nodes, or a run showing self-paid dilution driving honest nodes off at a
scale the stake cap does not bound (D-22); an authoritative arithmetic
requirement, or implementation limits that make exact arithmetic
impractical (D-07).

## R-09 Unminted

Origin: `Paper`; ours for zero totals. Delivery: `Decide now`. Rests on:
D-04, D-07, D-23.

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

**Would change it.** Paper text that redistributes the capped part
(D-04). A run where most of the pool stays unminted and stake does not
move is evidence about the design for the owner, not about what the
paper says.

## R-10 Validators and treasury

Origin: `Paper`. Delivery: `Decide now`. Rests on: no ruling; the paper
states it. How the validator amount divides among validators is D-10,
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
stage 3`. Rests on: D-23; D-09 and D-21 are open.

**The paper says.** Each active validator scores each node from 0.0 to
1.0; the median across active validators sets the fraction of the capped
reward the node receives, 1.0 meaning all of it and 0.0 none (p. 4). The
contract that produces scores can be swapped by governance (pp. 5, 12).

**Unclear.** Missing votes, quorum and even counts (D-09). Where the part
of a reward that a score below 10^6 withholds goes (D-21). What a claim
does with a score outside the range, since the scorer can be swapped.

**We build.** A score is an integer from 0 to 10^6, the median of active
validators. This profile takes it as an input to the claim and applies it
inside R-08's one division. Ours: a claim that reads a score above 10^6 is
refused with `SCORE_OUT_OF_RANGE`; the entitlement stays intact and no
other node is affected. A score of exactly 10^6 pays in full. No example
here decides D-09 or D-21.

**Example.** `claim-open` at score 1,000,000 pays 188,000,000,000,000,000,000
wei. `claim-rescored` at score 500,000 pays 94,000,000,000,000,000,000 wei.
`round-one-division` at score 750,000 pays 235,000,000,000,000,000,000 wei
a node. `claim-score-range` at score 1,000,001 is refused with
`SCORE_OUT_OF_RANGE`.

**Would change it.** S-06 enforcing the score range where scores are
produced, at the same seam as the swap (D-23); the rulings on D-09 and
D-21.

## R-12 Claim

Origin: `Paper`; ours for paying once. Delivery: `Decide before stage 3`.
Rests on: roadmap section 7.2 for paying once; D-08 is open.

**The paper says.** A round's rewards are not claimable at once: round n
becomes claimable at round n + 7, and the score applied is the median
current when the claim is made (pp. 6, 12).

**Unclear.** Who claims, where it pays, whether a zero score uses up the
entitlement, and whether a claim expires (D-08). Not decided here.

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

**Would change it.** The ruling on D-08.

## R-19 Rounding

Origin: `Interpretation`. Delivery: `Decide now`. Rests on: D-07, D-23.

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
need to store a fixed-precision share for another consumer (D-07).
