# lp2-core

A working testnet of the October 2026 Livepeer 2.0 litepaper, built in thin
end-to-end slices.

This is an independent build for learning what the paper's rules do when
they run. It is not an official Livepeer release, and nothing here approves
a rule, a parameter or a mechanism for the Livepeer protocol. Where the
paper is silent or unclear, the choice we build against is recorded in the
rulebook and labelled as ours.

## Status

Stage 2 is built: nodes are paid in mock USDC for real jobs, with no stake.
LPT, stake, scores, rewards and buyback come in later stages.

| Part | State |
|---|---|
| Rulebook and shared examples | `october-main-text-v1`: stage 2's rules and 35 worked cases |
| Independent calculator | Computes every shared example from the rulebook alone |
| Contracts | Stage 2: Controller, Registry, RoundClock, Broker, MockUSDC |
| Node integration | The unmodified node, configured by flags alone; no fork |
| One-command demo | `./dev demo` |

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
`.cache/demo/`, so later runs start at once. A run takes about two minutes.

It uses eight local ports from 47700 (`LP2_DEMO_PORT_BASE=48000 ./dev demo`
moves them) and stops everything it started when it ends, including on
Ctrl-C. It never touches a public chain: everything runs on a local chain
with throwaway keys made by the run.

What it does, in this order, printing each step:

1. Starts a fresh local chain, deploys the five contracts, and starts the
   tiny model, the node's orchestrator for a node N, and a signer for each
   of two payers, P and P2.
2. **Refusal 1.** N is not registered, so the orchestrator will not issue a
   payment challenge (HTTP 502). N registers, for free and with no stake,
   and the same request gets a challenge (HTTP 402).
3. P puts 10 USDC of mock USDC in its deposit and sets 0.02 USDC aside for
   N (its earmark).
4. **Six real jobs**, each paid with one ticket of 10,001 micro-USDC, which
   the node redeems itself. Every balance read from the chain is printed
   beside the calculator's figure for the same steps, and then checked
   against the stage's worked figures: P's deposit 9,939,994, the earmark
   20,000, N's wallet 30,000, the network share 30,006, N's fees 60,006.
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

It ends with `DEMO PASSED` and exit 0 when every figure is equal and every
behaviour was seen, `DEMO FAILED` and exit 1 naming each figure or
behaviour that was not, or `DEMO NOT RUN` and exit 2 when a tool, a
download or a port was missing. The log, the contract addresses, each
process's output and the case are in `.cache/demo/run/`; check the case
again with `./calculator/check .cache/demo/run/ledger-case.json`.

Every stand-in the demo uses (the local chain, mock USDC, the fixed price,
every ticket winning, 50-block rounds, the tiny model, no governance and no
buyback) is named in `deployments/stage-2-local.json`, which the demo reads
its settings from.

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
- `programs/` holds the funding program and the round keeper that run
  beside the node, and `demo/` the one-command demo.
- `deployments/` names what each testnet version contains.
- `SEAMS.md` names the places where something important crosses (rules,
  contract interfaces, money, authority, the node) and what enforces each.
- `AGENTS.md` is the guide for agents working here.
