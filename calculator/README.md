# Independent calculator

Implements `rulebook/october-main-text-v6.md` using Python 3.12 and its
standard library. It was built from the rulebook and shared examples,
without reading contract implementations. Python's arbitrary-size integers
allow exact products and powers without dependencies or floating point.

From the repository root:

```sh
./calculator/check
./calculator/check /path/to/scratch-vectors.json
./dev verify
```

The check reports every case ID, compares every expected amount and refusal
code, and fails on unknown kinds. It exits 0 on agreement, 1 on a mismatch
or invalid case, and 2 when Python or the input file is unavailable. A
scratch copy lets a reviewer change an expectation and see the check fail
without editing the frozen vectors.

`reference.py` exposes `Parameters.from_strings(parameters)` and
`calculate(kind, inputs, parameters, rounds)`. The `rounds` argument maps
round case IDs to their **inputs only**, for claim lookups. Returned amounts
are integers; the checker converts them to the vectors' decimal strings.
No expected value enters the calculation: the checker refuses a case with
any key besides `id`, `kind`, `inputs` and `expected`, and passes only
`inputs` to `calculate`. Ceiling calculations return the
ceiling and both fixed shares; the checker compares the output shape named
by the case.

The emission ceiling uses binary search for the largest integer satisfying
R-05's power inequality, then shifts for whole half-lives. Reward shares
are compared by cross multiplication and the complete reward product is
divided once, as R-08 and R-19 require. Claims recompute from the earning
round's inputs with the claim's score.

Ledger cases replay registration, funding, redemption, payout, observation and
release in fresh in-memory state. The default check covers 104 v6, 98 v5, 97 v4, 93 v3, 74 v2, 35 v1 and 26 v0 cases. No state survives a calculation. The check also runs ledger input regressions.
Decreasing rounds, conflicting details for one ticket ID (including refused
attempts), and observations without a node raise `ValueError` with a
`ledger input error` message, rather than returning a rule refusal code.
The ledger format has no Broker address, so it cannot express PAYOUT_INVALID.
Future tickets and simultaneous redemption refusals are reported as unsupported
inputs rather than inventing a stable code or refusal precedence.

This is a calculator, not a settlement or entitlement store.
It does not decide where withheld rewards go, so rounds with any
score below one million do not return `minted_wei`. When two refusals apply, they follow the general order in the rulebook's
units section.

LPT replay covers bonds, transfers, unbonds, rebonds, round-start stake and
cut snapshots, claims, share minting and holder withdrawals from per-round,
per-node pots. Bonds exceeding holder LPT are invalid inputs. Earning round
zero gets `NO_SUCH_ROUND`; unregistered self-claims and self-cuts get
`NOT_REGISTERED`. Among simultaneous LPT refusals, only a withdrawal that
is both `ALREADY_CLAIMED` and `NOT_CLAIMED` remains an input error.

From v3, `Settings` keeps R-25's parameter history: each group's changes in
order, each counting from its announce round plus 7, with at most one
pending. A ledger round reads the values of the round the rulebook names:
the fee cut of the redemption round, the shares, ceiling and reward delay of
the earning round, the unbonding period of the unbond's round, the score
source of the claim's round, and the set size of the round a seat call
runs in.
The ceiling after a schedule change is the deployment's ceiling at the
triple's position (R-05, x3-grid), so the same exact floor serves every
schedule. `Operators` keeps R-26's operator keys and one-slot proposals.
A `parameters` case runs announcements, withdrawals and reads alone.

From v4, a `buyback` case replays R-16 against a fresh pool and Buyback:
`job` adds R-03's network share, `seed` and `swap` move the pool's reserves
after v2 updates its cumulative price, and `poke` and `buy` follow rules 2
to 8 with rule 11's refusal order. The case's times may start anywhere: only
their differences enter a price, so a run at a chain's own block times is
checked the same way. The pool keeps time as v2 does, a uint32: the seconds
since its last update count modulo 2^32, so a gap of 2^32 s or more adds
only its remainder to the cumulative, while the Buyback's elapsed is the
full difference (rule 3). A step that breaks the case's shape (an unknown action
or field, a repeated step, a round below 1, a round or time that decreases,
a swap on an empty pool or one that would receive nothing, a reserve past
v2's 112 bits) raises `ValueError` with a `buyback input error` message,
since these are not rule refusals. No case changes the pool, so
`POOL_INVALID` never arises, and an honest pool never gives `LPT_SHORT`.

From v5, group 10 holds the executor. Pool announcements ask the pending
executor when present, otherwise the executor in force. Other executors
use the case’s accepted-pool list. The checker selects ten groups for v5
and nine for older profiles; direct callers may pass parameter_groups=9
to Parameters.from_strings for older rules. The buyback replay is unchanged.

Upgrade cases replay R-27 notices, stops and pause-key succession. Calls on
other contracts check only their own stop gate, ignoring `meets` as the
format requires. Steps sort by their numeric labels; duplicate labels,
round zero and decreasing rounds are invalid inputs. No code or token
execution is simulated by an application.
