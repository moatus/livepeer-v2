# The public run

`demo/public-run` deploys the stage 3 system to a chain it does not control
and runs the team's node through one paid job and one full reward cycle in
real time. Then it reads every figure from the chain and prints it beside
the independent calculator's figure. It is the path for the first public
deployment (Arbitrum Sepolia, D-14). It uses the demo's code: the same node,
programs, steps and reconciliation.

The run does the following, in order:

1. It deploys the nine contracts. Round 1 starts at deployment.
2. The deployer pays gas money to four accounts that the run makes: the
   node N, the payer P, the validator V and the keeper.
3. N registers with no stake. N and V each bond 1,000 test LPT to
   themselves. P funds 1 USDC and earmarks 1 USDC for N.
4. V opts in during round 2 and is the only validator from round 3.
5. In round 2, N serves one paid relay of the tiny model. N redeems the
   ticket once 40 blocks of its clock have passed, and is paid test USDC.
6. Seven rounds after the round in which the fee counts, N claims that
   round's LPT and withdraws its part.
7. Every figure is reconciled. The run writes a ledger case that
   `./calculator/check` reproduces, and the run's manifest.

The path never sends an `anvil_*` or `evm_*` call. It does not mine, move
time or set a balance. The keeper starts each round when the chain's clock
allows it.

## What to set

| Variable | What |
|---|---|
| `LP2_RPC_URL` | The chain's JSON-RPC URL. On Arbitrum Sepolia, use a provider's keyed endpoint. The free public endpoints rate-limit hard enough to stall a run that lasts more than an hour. No archive node is needed |
| `LP2_DEPLOYER_KEYSTORE` | The deployer's keystore file, outside the repository. Make it with `cast wallet new <dir>`, which asks for a password. Use a key made for the testnet alone, never one that has held real funds (D-45) |
| `LP2_DEPLOYER_PASSWORD_FILE` | A file that holds that password, outside the repository |
| `LP2_STATE_DIR` | A directory outside the repository. It holds the keys the run makes, the addresses, the steps done, the logs, the node's data and the run's `manifest.json`. Keep it: N's key is the team node's identity |
| `LP2_ROLE_GAS_WEI` | Optional. The gas money the deployer gives each of N, P, V and the keeper. The default is 0.003 ETH |
| `LP2_DEMO_PORT_BASE` | Optional. Moves the local ports of the node, the signer and the model (default 47700) |
| `LP2_RPC_BATCH_SIZE` | Optional. The most calls in one JSON-RPC batch (default 100) |
| `LP2_LOG_PAGE_BLOCKS` | Optional. The most blocks asked for in one `eth_getLogs` (default 5000). Lower it for an endpoint that caps the range below that |

A read the endpoint refuses with HTTP 429, or drops, is sent again after a
short wait, up to six times.

The run refuses any chain whose id is not on its allow-list: 31337, 1337
and 474747 (local chains) and 421614 (Arbitrum Sepolia). It also refuses a
key path or state directory inside the repository, and a symlink anywhere
in the state directory's paths for keys, passwords and the RPC URL. No key
appears on a command line, in a log or in the repository. One limit: the
RPC URL is passed on the helper programs' command lines, so a URL that
carries a provider's API key can be seen by other users of the machine.
The node itself reads the URL from a file.

The run needs the same tools as `./dev demo`: git, Python, Foundry, Go and
Docker.

## Against Arbitrum Sepolia

Before the run, the owner makes the deployer key and funds it from a faucet
(D-45). Then:

```sh
export LP2_RPC_URL=...                       # an Arbitrum Sepolia endpoint
export LP2_DEPLOYER_KEYSTORE=~/lp2-testnet/deployer.json
export LP2_DEPLOYER_PASSWORD_FILE=~/lp2-testnet/deployer.pw
export LP2_STATE_DIR=~/lp2-testnet/run-1
demo/public-run
```

The run ends with `DEMO PASSED` and exit 0 when every figure is equal. It
ends with exit 1 when any figure differs, and with exit 2 when it could not
run or was refused. When it passes, copy `manifest.json` from the state
directory into `deployments/` as the record of the deployment.

### What it costs

The first run on Arbitrum Sepolia sent 45 transactions and paid about
0.001 Sepolia ETH in fees, the L1 data fee included. Fund the deployer with
0.02 Sepolia ETH: 0.012 of it goes to the four role accounts as gas money,
and most of that is still theirs at the end. The run prints its fees by
account at the end: the deployer's from the receipts of what it sent, and
each role account's as its gas money less what it holds now.

| Account | Transactions | What it sends |
|---|---|---|
| Deployer | 16 | 9 contract creations, 4 gas payments, 2 test LPT grants, 1 mock USDC mint |
| N | 6 | register; approve and bond; the ticket redemption (sent by the node); the claim; the withdrawal |
| P | 2 | approve and fund |
| V | 4 | register; approve and bond; opt in |
| Keeper | 16 to 18 | `initializeRound` and `mintRoundShares` for each round to the claim |

### How long it takes

A round is 50 blocks of `block.number`, a constant in `RoundClock`. On
Arbitrum, `block.number` is the L1 block number, about one every 12
seconds, so a round lasts about 10 minutes (10 to 11 on the first run).
Counted from the deployment, where round 1 starts:

- the paid job is sent at the start of round 2, about 10 minutes in;
- the node redeems the ticket once 40 blocks of its clock have passed,
  about 8 minutes later. The Broker accepts a ticket in its creation round
  and the next two, so the margin is about 20 minutes;
- the claim may be sent seven rounds after the round the fee counts in,
  round 9 or 10: about 80 to 90 minutes after the deployment.

On a local chain that mines one block a second, a round is 50 seconds and
the whole run takes about 7 minutes, with the node and the model already
built.

## A second run, or an interrupted one

The run keeps its progress in `state.json` in the state directory. It marks
each step before sending it and saves it as done once every transaction it
sent has a receipt.

- **A finished run, started again,** sends nothing. It reconciles every
  figure again from the chain and prints the fees again.
- **A run stopped between steps,** for example during the long waits for
  rounds, resumes. It restarts the node, the keeper and the validator with
  the same keys and data. They ask the chain before each send, so they do
  not repeat a round start or a share mint.
- **A run stopped while a step was being sent** is refused, and the run
  names the step. Whether that step landed is unknown, and sending it again
  could pay twice. Check the sender's transactions, then use a new state
  directory. A run stopped during the deployment is refused the same way.
- **The paid job is never sent twice.** The job goes to the node, not to
  the chain, so the run saves that it was sent before it sends it. After a
  stop, the restarted node redeems the ticket from its own database. If no
  ticket settles before it expires (two rounds after the job's round), the
  run fails and says so.
- **Only one run at a time** may use a state directory. Every program the
  run starts holds the directory's lock too, so a run killed outright
  (`kill -9`) leaves the directory held while its node, keeper or validator
  still runs. The refusal names them; stop them, then resume.
- A state directory used on another chain, or with another deployer, is
  refused, and so is a chain where its contracts have no code.
- The run's `manifest.json` names the commit, rulebook and node the
  contracts were deployed from, saved at the deployment, even when a later
  checkout resumes it.
