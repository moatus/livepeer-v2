# Independent calculator

Implements `rulebook/october-main-text-v2.md` using Python 3.12 and its
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
No expected value enters the calculation. Ceiling calculations return the
ceiling and both fixed shares; the checker compares the output shape named
by the case.

The emission ceiling uses binary search for the largest integer satisfying
R-05's power inequality, then shifts for whole half-lives. Reward shares
are compared by cross multiplication and the complete reward product is
divided once, as R-08 and R-19 require. Claims recompute from the earning
round's inputs with the claim's score.

Ledger cases replay registration, funding, redemption, payout, observation and
release in fresh in-memory state. The default check covers 74 v2, 35 v1 and 26 v0 cases. No state survives a calculation. The check also runs ledger input regressions.
Decreasing rounds, conflicting details for one ticket ID (including refused
attempts), and observations without a node raise `ValueError` with a
`ledger input error` message, rather than returning a rule refusal code.
The ledger format has no Broker address, so it cannot express PAYOUT_INVALID.
Future tickets and simultaneous redemption refusals are reported as unsupported
inputs rather than inventing a stable code or refusal precedence.

This is a calculator, not a settlement or entitlement store.
It does not decide where withheld rewards go (D-21), so rounds with any
score below one million do not return `minted_wei`. When two refusals apply, they follow the general order in the rulebook's
units section (D-26, D-38, D-39, D-40).

LPT replay covers bonds, transfers, unbonds, rebonds, round-start stake and
cut snapshots, claims, share minting and holder withdrawals from per-round,
per-node pots. Bonds exceeding holder LPT are invalid inputs. Earning round
zero gets `NO_SUCH_ROUND`; unregistered self-claims and self-cuts get
`NOT_REGISTERED`. Among simultaneous LPT refusals, only a withdrawal that
is both `ALREADY_CLAIMED` and `NOT_CLAIMED` remains an input error.
