# lp2-core

A working testnet of the October 2026 Livepeer 2.0 litepaper: its rules
written out to the exact number, an independent calculator that computes
them, the contracts, and the programs that run a real node against them.

This is an independent build for learning what the paper's rules do when
they run. It is not an official Livepeer release, and nothing here approves
a rule, a parameter or a mechanism for the Livepeer protocol. Every token
is a test token.

## What it does

- **Payments.** A registered node, with no stake, is paid in mock USDC for
  each job it serves, through the node's own ticket payments.
- **Rewards.** Holders bond test LPT to nodes, validators score the nodes,
  and each round's LPT reward is claimed seven rounds later and split
  between a node and its holders.
- **Buyback.** The network's share of fees buys test LPT from a pool at a
  time-weighted price and burns it.
- **Parameters and keys.** One authority changes a value with seven rounds'
  notice. A node hands the key that acts for it to another key.
- **Upgrades.** Every contract but the tokens, the test pool and the
  Controller sits behind a proxy. Its code is replaced at the same address,
  with its stored state kept, seven rounds after an upgrade key announces
  it, or at once while a pause key has stopped that side.
- **An explorer** of a deployment, read from the chain alone, for the whole
  network or for one node's operator.

The node is the unmodified upstream node
([livepeer/node](https://github.com/livepeer/node) at a pinned commit),
configured by flags. Nothing here forks it.

## Quick start

```sh
./dev doctor    # what this machine has and lacks
./dev verify    # the fast checks: builds, unit tests, every worked case
./dev demo      # one reward cycle on a local chain, with real nodes
```

You need `git`, Python 3.10 or later, [Foundry](https://getfoundry.sh) and
Go, at the versions `tools/versions` pins, and Docker for the demos that
run the node. `tools/setup foundry go` installs the pinned Foundry and Go
under `.cache/tools` (Linux on amd64) and prints the folders to put on
`PATH`:

```sh
export PATH="$(tools/setup foundry go | paste -sd:):$PATH"
```

The first `./dev demo` fetches and builds the node and a small model, so it
needs the internet. Later runs take about three minutes. A demo runs on a
local chain with keys it makes itself and never touches a public chain.

## Layout

| Path | Holds |
|---|---|
| `rulebook/` | The rules, each citing the litepaper's page. `october-main-text-v6.md` is current |
| `vectors/` | The worked cases: 104 of them, with the exact numbers the rules must produce |
| `calculator/` | An independent calculator in plain Python. It computes every worked case from the rulebook alone |
| `contracts/` | The contracts and their tests (Solidity 0.8.30, Foundry). `interfaces/` is each contract's public shape |
| `programs/` | What runs beside the node, in Go (table below) |
| `demo/` | The end-to-end demos, and `public-run` for a chain you do not control |
| `deployments/` | One manifest for each testnet version: what it contains and every stand-in it uses |
| `tools/`, `dev` | The checks behind `./dev` |

A rule is right when the calculator and the contracts, built apart, give the
same numbers for every worked case. `./dev verify` checks that on every
change, and each demo checks what it reads from the chain against the
calculator.

### Programs

| Program | Does |
|---|---|
| `lp2-keeper` | Starts each round, and buys and burns when a buy would succeed |
| `lp2-fund` | Puts a payer's mock USDC into its deposit and earmarks |
| `lp2-stake` | A holder's or a node's staking and reward actions: bond, claim, withdraw, rotate the operator key |
| `lp2-params` | Announces, withdraws and shows a parameter change, as the authority |
| `lp2-sender` | Sends a paid request each round to every node that joins |
| `lp2-faucet` | Grants each registered node test LPT, once |
| `lp2-responder` | A light job a node can serve in place of a model |
| `lp2-explorer` | The explorer. It holds no key and sends nothing |
| `validators` | Scripted validators: stand-ins that vote what a script says |
| `container/` | One image that runs a node on the testnet |

Each program's header comment says how to run it.

## Demos

| Command | Shows |
|---|---|
| `./dev demo` | One reward cycle: four nodes paid for real jobs, bonds, validator votes, claims, and the refusals along the way |
| `./dev demo payments` | Paid jobs with no stake, and what the Broker refuses |
| `./dev demo full` | The full system: a parameter change, an operator key rotation, a node restarted during settlement, a buy and burn |
| `./dev demo buyback` | The buyback step by step against the worked cases, with no node |
| `./dev demo light` | The standing services: a node joins and is paid each round with nobody acting |
| `./dev demo container` | The operator container, killed and restarted where a restart must be safe |
| `./dev demo upgrade` | A contract's code replaced on a running system |

Each ends with `DEMO PASSED`, `DEMO FAILED` naming what differed, or
`DEMO NOT RUN` naming what was missing. [`demo/README.md`](demo/README.md)
says what each one does, what it needs and where its logs are.

## Run a node

[`programs/container/`](programs/container/README.md) builds one image that
runs a node on the testnet from one command. It makes its keys, prints the
two addresses to fund with test ETH, then registers, bonds, joins and
claims without anyone acting. Its README is the operator's guide: what to
fund, which port must be reachable, how to get an RPC URL and how to read
the node's explorer.

## Explore a deployment

`lp2-explorer` serves a deployment's figures on `http://127.0.0.1:8080/`:
the network, every node, every payer and what each paid each node. Every
page has its JSON at the same path with `.json` added. It reads through
the RPC URL you give it and keeps a small index of the deployment's events
on disk, so a restart resumes where it stopped. The operator container
runs it for its own node.

## Deploy to a public test chain

`demo/public-run` deploys to a chain it does not control, such as Arbitrum
Sepolia, from an RPC URL and keystores you give it, runs a node through
paid jobs and a reward cycle, and checks every figure against the
calculator. `demo/public-run upgrade`, `stop` and `lift` replace a
contract's code and stop or restart a side of a running deployment.
[`demo/PUBLIC.md`](demo/PUBLIC.md) says what to set, what it costs and how
long it takes.

## Status

Everything above is built and shown on local chains. The rewards system has
also run on Arbitrum Sepolia
(`deployments/public-arbitrum-sepolia-2026-11-21.json`). The full system,
with the buyback and upgrades, has not yet been deployed to a public chain.

This is a testnet. Validators are scripted, the pool and both tokens are
stand-ins, and one key holds each authority. Each manifest in
`deployments/` names every stand-in its version uses.

## Contributing

`./dev verify` must pass. [`AGENTS.md`](AGENTS.md) holds the rules for the
code and for commit messages, and [`SEAMS.md`](SEAMS.md) names the places
where money moves and authority is used, which get the closest review.

The main repository is hosted privately and this one receives its history
from there. A pull request opened here is brought over, reviewed and
landed there, and appears here with the next push. It is not merged with
GitHub's button.

## Licence

MIT, see [`LICENSE`](LICENSE). One file,
`contracts/src/money/TestPool.sol`, is a port of the Uniswap v2 pair and
is GPL-3.0-or-later, as Uniswap v2 is.
