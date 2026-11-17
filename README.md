# lp2-core

A working testnet of the October 2026 Livepeer 2.0 litepaper, built in thin
end-to-end slices.

This is an independent build for learning what the paper's rules do when
they run. It is not an official Livepeer release, and nothing here approves
a rule, a parameter or a mechanism for the Livepeer protocol. Where the
paper is silent or unclear, the choice we build against is recorded in the
rulebook and labelled as ours.

## Status

Stage 3 is built: nodes are paid in mock USDC for real jobs, holders bond
test LPT to them, scripted validators score them, and each round's LPT
reward is claimed, split between a node and its holders, and minted under
the round's ceiling. The buyback of LPT and parameter changes come in
stage 4.

| Part | State |
|---|---|
| Rulebook and shared examples | `october-main-text-v2`: stage 3's rules and 74 worked cases (v1, stage 2's, is kept) |
| Independent calculator | Computes every shared example from the rulebook alone |
| Contracts | Stage 2: Controller, Registry, RoundClock, Broker, MockUSDC. Stage 3: TestLPT, StakeLedger, Scores, Claims |
| Node integration | The unmodified node, configured by flags alone; no fork |
| One-command demo | `./dev demo` (stage 3); `./dev demo stage-2` still runs stage 2's |

## Run the demo

```sh
./dev demo
```

It needs `git`, Python 3.10 or later, [Foundry](https://getfoundry.sh)
(`forge`, `anvil`, `cast`), Go 1.27 or later and Docker, whose daemon your
user can reach. Go fetches the exact toolchain the node names if yours is
older. `./dev doctor` checks all but Docker. The first run also
needs the internet: it fetches the node's pinned commit from GitHub and
builds it, its Go modules, the tiny model (about 145 MB, checked against its
sha256) and the model server's image (pinned by digest). These are kept in
`.cache/demo/`, so later runs start at once. A run takes about three minutes.

It uses fourteen local ports from 47700 (`LP2_DEMO_PORT_BASE=48000 ./dev demo`
moves them) and stops everything it started when it ends, including on
Ctrl-C. Two runs at once need separate checkouts as well as separate ports,
since each run starts by clearing `.cache/demo/run/`. It never touches a
public chain: everything runs on a local chain with throwaway keys made by
the run.

### Stage 3: one reward cycle and the scripted validators

`./dev demo` runs one full reward cycle, the one the shared example
`ledger-cycle` describes, and around it the validator set and its pay. What
it does, in this order, printing each step:

1. Starts a fresh local chain and deploys the nine contracts, with a
   genesis supply of 10,000,000 test LPT held by the deployer. It starts
   the tiny model, the node's orchestrator for each of four nodes, N, M, K
   and L, and a signer for the payer P.
2. **Round 1.** N, M, K and L register; N pays to its wallet W and takes a
   20 % reward cut. Four holders are given test LPT and bond it: O 100 and
   D 300 to N, E 600 to M, F 250 to K. P deposits 5 USDC and sets 1 USDC
   aside for each node. Four scripted validators, V1 to V4, register. The
   keeper starts round 2 and mints round 1's validator and treasury
   amounts.
3. **Round 2.** Five paid relays of 1 USDC each, three for N and one each
   for M and K, which the nodes redeem in round 2. O bonds a further 100
   to N. V1 bonds 1,000 to itself and a holder, H, 4,000 to V1, which takes
   a 10 % reward cut; V2, V3 and V4 bond 3,000, 2,000 and 6,000 to
   themselves (cut 0), and L 1,000 (cut 100 %). All of it counts from round
   3, so round 2's stake is N 400, M 600, K 250 and its figures are
   `ledger-cycle`'s.
4. **The keeper stopped.** It is stopped while round 2 ends: the round
   lasts two round lengths, and its validator and treasury amounts cannot
   be minted yet (`CLAIM_TOO_EARLY`). Then another account, printed as
   `Anyone` (a throwaway key that is neither a node nor a holder), starts
   round 3, as anyone may: round 2's amounts are due and nobody has minted
   them. Run again, the keeper finds them unminted and mints them in full, so a
   stopped keeper delays them and loses nothing; a second mint is refused
   (`ALREADY_CLAIMED`). From then on the keeper starts every round.
5. **Round 3.** The validators (`programs/validators`, run from
   `demo/validators.script`) V1, V2 and V3 opt in and are members from
   round 4.
6. **Round 4.** V1 and V2 vote M 500,000 and K 0, and V1 alone votes L 0.
   Nobody votes on N. V4 opts in: there are only three seats
   (ValidatorSetSize 3, for the paper's 33), and with 6,000 LPT it takes
   the seat of V3, the smallest member, from round 5. Its vote in round 4
   is refused (`NOT_VALIDATOR`): a node votes only in a round it is a
   member of. P deposits 1 USDC more, and L serves one paid relay. The
   keeper is stopped meanwhile, so that round 4 lasts until L's node
   redeems the ticket, 41 blocks after the relay.
7. **Round 5.** Round 4's validator amount is divided among V1, V2 and V3
   by their stake, 5,000, 3,000 and 2,000; what the division leaves goes
   to the treasury. V1's 10 % cut goes to its payout address and the rest,
   its pot, to Claims, from which V1 withdraws its part and H its part,
   four fifths; H's second withdrawal is refused (`ALREADY_CLAIMED`). V2
   and V3 leave their pay in Claims for themselves to withdraw. Every
   later round pays V1, V2 and V4 the same way.
8. **Round 8.** N's claim for round 2 is refused as too early
   (`CLAIM_TOO_EARLY`), and M's claim for N as not M's (`NOT_AUTHORIZED`).
9. **Round 9.** A rival's and a delegator's claims for N are refused
   (`NOT_AUTHORIZED`). Each claim is paid at the median of the members'
   votes read at the claim, a member that has not voted counting as
   1,000,000. N claims at 1,000,000: W receives the cut,
   60.110377935333104693 LPT, and Claims the pot, 240.441511741332418772,
   from which O and D withdraw their parts, 60.110377935333104693 and
   180.331133805999314079. N's second claim and D's second withdrawal are
   refused (`ALREADY_CLAIMED`). M claims at 500,000: 93.922465523957976083
   LPT goes to Claims as E's part, and as much again is never minted. K
   claims at 0, is paid 0 and its entitlement closes. F starts to move 100
   LPT from K to M, which would arrive in round 30.
10. **Rounds 10 to 12.** F unbonds 40 of the move before it arrives, then
    rebonds the 40: to M it is refused (`AMOUNT_TOO_LARGE`), to K it is
    taken. K's stake reads 250, 210 and 250 LPT in rounds 10, 11 and 12. In
    round 11 L claims its round 4 reward at 1,000,000: one vote of 0 cannot
    cut a node.
11. Prints every balance read from the chain beside the calculator's figure
    for the same steps: the USDC of every wallet, the test LPT of every
    address the run names, Claims and StakeLedger among them, each round's
    validator and treasury amounts, the LPT minted for each round
    (454.424865109532845983 for round 2) and in all, every stake shown and
    every refusal. It checks them against `ledger-cycle`'s worked figures,
    and prints the 544.750300038956261284 LPT never minted for round 2.
    Claims holds `ledger-cycle`'s figure, E's part, plus the validator pots
    not yet withdrawn, and the run checks exactly that.
12. **What the validators show** (the stage's section on validators):
    beside the calculator's figure, the four claims, each with the votes
    cast on the node, the median read at the claim and what it was paid:
    N with no votes, M with two of 500,000, L with V1's one 0, K with two
    0s; the members of round 4 (V1, V2, V3) and of round 5 (V1, V2, V4);
    and round 4's validator mint: each member's pay, V1's cut and pot, the
    part of it V1 and H each withdrew, and the dust left in Claims.
13. Writes the whole run as three files of cases, each built from what was
    sent and what the chain's views show, and runs `./calculator/check` on
    each: `ledger-case.json`, the run as a `ledger` case;
    `validators-case.json`, every opt-in and vote the validators sent, the
    refused one included, with each validator's stake in every round, and
    what each claim read; and `validator-pay-cases.json`, one
    `validator-pay` case for each round whose shares were minted.

The node, the keeper, `lp2-fund`, `lp2-stake` (which sends the bonds,
claims, withdrawals, transfers, unbonds and rebonds with each person's
throwaway key) and the scripted validators are the only things that send
transactions; the demo itself only registers nodes, gives out test LPT and
mines empty blocks where it would otherwise wait for the chain.

What it does not show: a validator's score is the script's choice, not a
judgment of work, and the set changes only when someone calls Scores (here
an opt-in), not whenever stake moves.

### Stage 2: paid jobs with no stake

`./dev demo stage-2` runs stage 2's demo against stage 2's rulebook. What it
does, in this order, printing each step:

1. Starts a fresh local chain, deploys the five contracts, and starts the
   tiny model, the node's orchestrator for a node N, and a signer for each
   of two payers, P and P2.
2. **Refusal 1.** N is not registered, so the orchestrator will not issue a
   payment challenge (HTTP 502). N registers, for free and with no stake,
   and the same request gets a challenge (HTTP 402).
3. P puts 10 USDC of mock USDC in its deposit and sets 0.02 USDC aside for
   N (its earmark). The orchestrator checks each ticket alone against the
   deposit and the earmark, not everything it already holds unredeemed, so
   N may hold more in tickets than the earmark guarantees (D-24).
4. **Six real jobs**, each paid with one ticket of 10,001 micro-USDC, which
   the node redeems itself. Every balance read from the chain is printed
   beside the calculator's figure for the same steps, and then checked
   against the stage's worked figures: P's deposit 9,939,994, the earmark
   20,000, N's wallet 30,000, the network share 30,006, N's fees 60,006.
   The USDC the Broker actually holds is checked too: it must equal the
   deposits, earmarks and network share, and all mock USDC in existence
   must equal what the demo minted for the payers.
5. **Refusal 3.** Ticket 1's redemption is sent again: the Broker refuses
   it with `TICKET_USED`.
6. **Refusal 2.** P2 sets nothing aside for N: its signer will not sign,
   and when it signs while an earmark is there and P2 then asks for the
   earmark back, the orchestrator itself refuses the payment.
7. **999 junk registrations**, then a job that moves the same amounts.
8. **The keeper stopped for five round lengths** (250 blocks): a job is
   still paid, in the same round, and the restarted keeper starts one
   round, skipping none.
9. **A release requested right after a job.** P asks for its whole deposit
   and earmark back while N holds the job's ticket. N is shown 0 at once,
   the ticket is still paid in full, and three rounds later P receives the
   rest.
10. Writes the whole run as a `ledger` case, built from what the demo sent
    and what the chain's views show, and runs `./calculator/check` on it.

The demo mines empty blocks where it would otherwise wait for the chain,
for example the 40 blocks before the node redeems a ticket.

### Both

Each ends with `DEMO PASSED` and exit 0 when every figure is equal and every
behaviour was seen, `DEMO FAILED` and exit 1 naming each figure or
behaviour that was not, or `DEMO NOT RUN` and exit 2 when a tool, a
download or a port was missing. If a node's orchestrator or signer exits
or does not answer as it starts, the run stops there, naming it, the last
line it wrote and its log. The run's log (`demo.log`), the contract
addresses, the cases and, in `logs/`, one file of output for each process it
started (`orchestrator-N.log`, `keeper.log`, `validators.log`, ...) are in
`.cache/demo/run/`; check a case file again with, for example,
`./calculator/check .cache/demo/run/validators-case.json`.

Every stand-in a demo uses (the local chain, mock USDC, the fixed price,
every ticket winning, 50-block rounds, the tiny model, no governance and no
buyback, and from stage 3 test LPT and its genesis supply, the treasury
address, the scripted validators, three validator seats for the paper's 33
and the keeper) is named in its manifest,
`deployments/stage-3-local.json` or `deployments/stage-2-local.json`, which
it reads its settings from.

## What else you can run

```sh
./dev doctor    # what this machine has and lacks
./dev verify    # the fast checks
./dev review    # the change report for the current work
```

## How the code is arranged

- `rulebook/` holds the rules, each citing the litepaper's page, and
  `vectors/` the worked cases with the exact numbers they must produce.
- `calculator/` computes every worked case from the rulebook alone, and
  `contracts/` runs every one through the contracts.
- `programs/` holds what runs beside the node: the funding program, the
  round keeper, `lp2-stake` for holders and nodes, and the scripted
  validators; `demo/` holds the one-command demo.
- `deployments/` names what each testnet version contains.
- `SEAMS.md` names the places where something important crosses (rules,
  contract interfaces, money, authority, the node) and what enforces each.
- `AGENTS.md` is the guide for agents working here.
