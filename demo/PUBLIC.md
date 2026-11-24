# The public run

`demo/public-run` deploys the reward system to a chain it does not control
and runs the team's node through one paid job and one full reward cycle in
real time. Then it reads every figure from the chain and prints it beside
the independent calculator's figure. It is the path for the first public
deployment (Arbitrum Sepolia). It uses the demo's code: the same node,
programs, steps and reconciliation. `demo/public-run full` does the same
for the full system and hands it over to services that keep it running;
see “Buyback, parameter changes and key rotation” below.

The run does the following, in order:

1. It deploys the ten contracts. Round 1 starts at deployment.
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
| `LP2_DEPLOYER_KEYSTORE` | The deployer's keystore file, outside the repository. Make it with `cast wallet new <dir>`, which asks for a password. Use a key made for the testnet alone, never one that has held real funds |
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

Before the run, the owner makes the deployer key and funds it from a faucet. Then:

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

## Buyback, parameter changes and key rotation

`demo/public-run full` deploys the full system once, from
`deployments/public-arbitrum-sepolia-full.json`, and shows the full system's two
public exit items in real time: test LPT bought with the network's share
of a fee and burned, and an announced parameter change that counts seven
rounds later. It then hands the deployment over to services that run
without the deployer's key. It uses the rewards run's code: the same
environment, chain allow-list, keys, steps, resume rule and fee table.

The run does the following, in order:

1. It deploys the full system's twelve contracts: the rewards contracts, the test pool,
   Parameters naming the Buyback as the buyback executor, and the Buyback,
   eight of them behind a proxy, then `Upgrades`, which holds U and S (see
   "Upgrades and stops"). Parameters' one authority is G, the key the owner
   gives the run. Round 1 starts at deployment.
2. The deployer pays gas money to five accounts the run makes: N, P, V,
   the keeper and the faucet. It seeds the pool with 500,000 mock USDC and
   100,000 test LPT, and grants the faucet 1,000,000 test LPT. Under
   `serve`, `lp2-faucet` hands it out.
3. N and V register and bond, and P funds, as in the rewards run. G
   announces the unbonding period of 42 rounds (21 at deployment), which
   counts seven rounds later.
4. The keeper starts, given Parameters' address. Each tick it starts the
   round when the clock allows, mints the ended round's shares, and buys
   or pokes at the executor in force when the call would succeed. Its
   first poke starts the pool's average price.
5. In round 2, N serves one paid relay and redeems its ticket. Half of the
   fee, 0.5 test USDC, is the network's share.
6. In the round before the change counts, the run reads it as not yet in
   force. In the round it counts from, the run reads it as in force. Once
   the average price is 3,600 s old, the keeper's buy spends the network
   share on test LPT and burns all of it. The run waits for all three, in
   whatever order they come.
7. Every figure is reconciled against the calculator:
   - the payment, the stake and each round's shares, as the rewards run
     reconciles them. A ledger step cannot hold a buy, so the share held
     and the LPT minted are shown with what the buys spent and burned
     added back, as their `Bought` events give it;
   - the buys, as a buyback case: the seed, every poke, job and buy, at
     the rounds and times the chain gave them. These figures include the
     USDC spent, the LPT bought and burned, the share held after, the
     pool's reserves and the LPT supply after the burn;
   - the change, as a parameters case: the round it counts from, and its
     value in the two rounds read.

   The run writes the cases into `ledger-case.json` and
   `full-cases.json`, and `./calculator/check` reproduces both files.
8. The handover, only when every figure is equal. The run writes three
   files into the state directory. Its manifest gets none of them: it
   records the services, the faucet, the change and the first buy:
   - `deployment.json`, the testnet's deployment file, which operators
     give the node container (`programs/container/README.md`, "The
     deployment file"). It holds the deployment's name, the chain id, the
     first block, the Controller, Parameters, StakeLedger, Claims, test LPT
     and mock USDC, the node's price and ticket settings, and the sender's
     public URL, `LP2_SENDER_URL`. It names nothing private: no key, key
     file, RPC URL or host but the sender's. The run checks it against
     the fields the container reads, and the sender's URL as below;
   - `services.json`, for `serve`. It holds the chain id, the deployment's
     first block, every contract, the executor in force, G's address and
     the role key files, relative to the state directory. It holds no key
     or keystore path of the deployer's or G's;
   - `labels.json`, for the explorer `serve` runs. It names N, P, V, the
     keeper and the faucet by their roles, marks P simulated, since the
     sender's paid requests stand in for users' demand, and names the
     scenario: the sender pays each node that joins one request a round.

   A run with a figure that differs hands nothing over, and removes the
   first two files if an earlier run wrote them, so `serve` refuses the state
   directory. Every program the run started stops with it. Until the
   services start, a round just lasts longer.

### What to set

As for the rewards run, and also:

| Variable | What |
|---|---|
| `LP2_AUTHORITY_KEYSTORE` | G's keystore file, outside the repository. G is Parameters' one authority, fixed at deployment. Make it as the deployer's is, for the testnet alone, never a key that has held real funds |
| `LP2_AUTHORITY_PASSWORD_FILE` | A file that holds its password, outside the repository |
| `LP2_UPGRADER_KEYSTORE` | U's keystore file, the upgrade key, outside the repository, made as G's is. `Upgrades` names it at deployment; it alone announces, applies and withdraws new code for a contract and lifts a stop (R-27). The run reads only its address and sends nothing with it |
| `LP2_UPGRADER_PASSWORD_FILE` | A file that holds its password, outside the repository |
| `LP2_PAUSER_KEYSTORE` | S's keystore file, the pause key, outside the repository, made the same way. `Upgrades` names it at deployment; it sets a stop and does nothing else. The run reads only its address |
| `LP2_PAUSER_PASSWORD_FILE` | A file that holds its password, outside the repository |
| `LP2_SENDER_URL` | The sender's public URL, where operators' nodes join: `deployment.json` names it. It is an origin: `http://` or `https://`, a host name or address and an optional port, with no user, no path but `/`, no query and no fragment. An IPv6 address carries no zone (`%`), and the port is written as a plain number, so `lp2-stake` reads the URL as the run does. Nodes join at its `/join`, so a proxy in front of the sender serves `/join` at that origin. It is not the RPC endpoint: not the RPC URL's scheme, host and port (a port left out is the scheme's default). It may be on the RPC's host at another port or scheme. Required on a public chain, where its host must resolve, on the machine that runs the run, only to public addresses, by the rule `lp2-sender` applies to a node's URL. A name is read as it is, in any case and with or without its trailing dot. On a local chain it is by default the sender's loopback address |

The run refuses to start without the first six, before it makes or sends
anything, and refuses an upgrade or pause key that is G's, the deployer's
or the other one: the authority, U and S are three different keys. A
state directory records all three, and a run given another is refused. On a public chain it refuses to start without `LP2_SENDER_URL`,
before it sends anything. G sends one transaction, the announcement, so it needs gas money
of its own. `LP2_ROLE_GAS_WEI` now applies to five accounts.

### The services

```sh
export LP2_STATE_DIR=~/lp2-testnet/full    # the state directory the run handed over
demo/public-run serve
```

`serve` reads `services.json`, the role keys and their password file, and
nothing of the deployer's or G's. It needs neither the deployer's nor G's
variables, and their keystores may be absent. It refuses a state directory
no passing full system run has handed over. It uses the RPC URL the run used, kept in
the state directory, unless `LP2_RPC_URL` gives another for the same
chain. It needs no Docker and no model. It starts these services:

- N's node, the unmodified orchestrator, serving `lp2-responder`, the
  light responder, in place of the model;
- `lp2-keeper`, given Parameters' address. It starts each round, mints
  each ended round's shares, and buys and pokes at the executor in force.
  So it follows an executor that G moves by announcement, with no
  restart;
- `lp2-validators` for V, from a script `serve` writes. In it, V votes the
  full score, 1,000,000, for N in the round after the services start. That
  vote leaves every score as it was. A scripted validator acts only where
  its script says;
- `lp2-sender`, holding P's key, with P's signer on loopback. It pays each
  node that joins one paid request a round, and funds P's deposit and
  earmarks from mock USDC it mints. Its list is `sender-nodes.txt` in the
  state directory. It answers joins on `LP2_SENDER_LISTEN`, by default
  `127.0.0.1` and port 24 above `LP2_DEMO_PORT_BASE`. Set it to a public
  address for nodes outside the machine to join. Node URLs on private
  addresses are allowed on a local chain only;
- `lp2-faucet`, holding the faucet account's key, from the deployment's
  first block, with its journal in `faucet/` in the state directory, kept
  across restarts. It grants each node the Registry shows registered 1,000
  test LPT, once, up to 100 nodes. That includes N and V, at its first
  start. A node is never granted twice, by a faucet restarted or not
  (`programs/cmd/lp2-faucet/main.go`). It sends one transaction per grant
  from the faucet's gas money;
- `lp2-explorer`, with no node, so its `/` is the Network page, on
  `http://127.0.0.1:` and port 14 above `LP2_DEMO_PORT_BASE`. It keeps its
  index of the deployment's events in `index/` in the state directory and
  resumes it at each start, and names accounts from `labels.json`. On a
  local chain, once it has caught up, `serve` checks N's, V's and P's
  figures in it once: against the chain's views at the explorer's block,
  and, for the rounds of the run's ledger case and the run's bonds,
  against the case. It prints each check, and if one failed, says so
  again and exits 1 when it is stopped.

If `LP2_SENDER_URL` is set and differs from the URL `deployment.json`
names, `serve` writes the new one into it and says to publish it again.
It first checks the whole file as the run's handover does, the sender's
URL by the same rule. A file that fails is not written: `serve` refuses to
start and the file in the state directory stays as it was.

Then N joins the sender as any node does, with a join signed by N's key,
and is paid a request at once and then one a round. N's URL is
`LP2_NODE_URL`, the URL at which N's orchestrator is reached; on a local
chain it is by default its loopback address. On a public chain, N's
orchestrator listens on loopback, so N joins only when `LP2_NODE_URL`
gives a public URL that leads to it, such as a proxy on the machine the
services run on. Without one, `serve` says that N has not joined,
and the sender still pays every other node that joins.

`serve` prints each round as it starts, and each relay the sender logs,
and logs each program in `logs/`.
It runs until it gets SIGTERM or Ctrl-C, then stops its programs and exits
0, or 1 if a check of the explorer's figures failed. If a program exits, `serve` exits 1 and names that program and its log,
so a supervisor such as systemd can start it again. It holds the state
directory's lock as the run does, so the run and the services never run at
once. To read the run's figures again, stop the services. Then run
`demo/public-run full` with the run's variables set, and it sends
nothing. Once the sender has paid a node, a re-read is refused: the
sender's funding is not a step of the run. So is a re-read once the
faucet has granted a node. The run reconciled its figures at the
handover, and `public-run.log` keeps them.

### Against Arbitrum Sepolia

Before the run, the owner makes four keys, the deployer, G, U and S, each
for the testnet alone, and funds the deployer and G from a faucet. U needs
gas money only for an upgrade (`demo/public-run upgrade` deploys the new
code with it), and S only to set a stop. Then:

```sh
export LP2_RPC_URL=...                       # an Arbitrum Sepolia endpoint
export LP2_DEPLOYER_KEYSTORE=~/lp2-testnet/deployer.json
export LP2_DEPLOYER_PASSWORD_FILE=~/lp2-testnet/deployer.pw
export LP2_AUTHORITY_KEYSTORE=~/lp2-testnet/authority.json
export LP2_AUTHORITY_PASSWORD_FILE=~/lp2-testnet/authority.pw
export LP2_UPGRADER_KEYSTORE=~/lp2-testnet/upgrader.json
export LP2_UPGRADER_PASSWORD_FILE=~/lp2-testnet/upgrader.pw
export LP2_PAUSER_KEYSTORE=~/lp2-testnet/pauser.json
export LP2_PAUSER_PASSWORD_FILE=~/lp2-testnet/pauser.pw
export LP2_STATE_DIR=~/lp2-testnet/full
export LP2_SENDER_URL=https://...             # where operators' nodes will reach the sender
demo/public-run full
```

The run ends with `DEMO PASSED` and exit 0 when every figure is equal. Then
start the services from the same state directory with `demo/public-run
serve`. They need only the state directory. The deployer's and G's
keystores can be moved off the machine first. Copy the run's
`manifest.json` into `deployments/` as the record of the deployment.

### Publishing the deployment file

Operators start their node container with `LP2_DEPLOYMENT` set to the
URL of the testnet's deployment file. The owner publishes `deployment.json`
from the state directory unchanged, through a pull request, in the same
change that adds the dated manifest. It goes in `deployments/` beside the
manifest, named for it with `.operator.json` in place of `.json`, for
example `deployments/public-arbitrum-sepolia-full-<date>.operator.json`.
Operators are given the raw URL of that file on the repository's host,
which serves it over HTTPS. A new deployment gets a new file. If the
sender moves, `serve` writes its new URL into `deployment.json`, and the
owner publishes the file again under the same name.

### What it costs

These figures are estimates. The gas is from the receipts of a run on a
local chain mining one block a second. The price is the rewards run's on
Arbitrum Sepolia: its 45 transactions paid about 0.001 Sepolia ETH, the L1
data fee included, and the same steps use about 19.6 million gas locally.

| Account | Transactions | What it sends | Gas, local run |
|---|---|---|---|
| Deployer | 33 | 21 contract creations (the twelve contracts, a proxy for eight of them, and `Upgrades`); 5 gas payments; 3 to seed the pool (a mock USDC mint, a test LPT transfer, the pool's mint); 3 test LPT grants, to N, V and the faucet; 1 mock USDC mint to P | 25.8 million |
| G | 1 | the announcement | 0.12 million |
| N | 4 | register; approve and bond; the ticket redemption (sent by the node) | 0.65 million |
| P | 2 | approve and fund | 0.19 million |
| V | 4 | register; approve and bond; opt in | 0.5 million |
| Keeper | 2 a round, and the first poke and the buy | `initializeRound` (56,000 gas) and `mintRoundShares` (182,000) each round; a poke (164,000) and the buy (224,000) | about 2.4 million for Arbitrum Sepolia's 8 or 9 rounds; 16.3 million for the local run's 67 |
| Faucet | 0 | nothing during the run; under `serve`, one token transfer per grant, 52,449 gas on the local chain | 0 |

The whole run is about 30 million gas, about 0.0015 to 0.0025 Sepolia ETH
in fees. Most of it is the 21 contract creations. Fund
the deployer with 0.03 Sepolia ETH: 0.015 of it goes to the five role
accounts as gas money. Fund G with 0.001. An upgrade deploys the new code
and sends two transactions with U, the announcement and the apply: about 3
million gas for the Broker, most of it the new code's creation (2.8
million on a local chain), about 0.00015 Sepolia ETH at the rewards run's
price. Fund U with 0.002, and S with 0.001 for its stops.

The keeper keeps spending after the run. It sends two transactions a
round, one to start the round and one to mint the ended round's shares,
about 240,000 gas, and a poke or a buy when one is due. On Arbitrum
Sepolia, at 144 rounds a day, that is about 34 million gas a day, roughly
0.002 Sepolia ETH at the rewards run's price, and 0.0035 at 0.1 gwei. The
default gas money of 0.003 lasts it one to two days. Send its address,
which `services.json` names, more as it goes. `serve` says how many
transactions the keeper can still pay for once fewer than 100 are left.
The run stops with the same message if the keeper runs out during it. On a
local chain at 1 gwei, ten times Arbitrum Sepolia's price, give the run
`LP2_ROLE_GAS_WEI=50000000000000000` (0.05 each), or the keeper runs out in
about 14 rounds.

### How long it takes

A round is 50 blocks of the node's clock (R-18): about 10 minutes on
Arbitrum Sepolia and 50 seconds on a local chain that mines one block a
second. Two waits make up most of the run, and they overlap:

- the change, announced in round 1, counts from round 8. The run reads it
  in round 7 and again in round 8, about 60 and 70 minutes after the
  deployment on Arbitrum Sepolia, and 6 and 7 minutes after it locally;
- the price window: the keeper's first poke, just after the deployment,
  starts the pool's average. The first buy comes once that average is
  3,600 s old, with the job's share held since round 2 or 3. That is about
  an hour after the deployment on either chain, because the window is
  counted in seconds, not rounds.

Against Arbitrum Sepolia the run takes about 80 to 90 minutes, as the
rewards run does. The deployment takes a few minutes of that, and the
reconciliation a few more. On a local chain mining one block a second it
took 61 minutes, a 2-minute stop included. Most of that is the hour of the
price window; the change counted 7 minutes after the deployment. Nothing
in the run moves time. The window is Parameters' value at deployment,
3,600 s, fixed by the contract.
R-25 allows a window as short as 1,800 s, but that needs an announcement
and seven rounds' wait, which on Arbitrum Sepolia is longer than the half
hour it would save.

### A second run, or an interrupted one

The rewards run's rules hold for every full system step. An interrupted run,
started again with the same command, resumes. A finished one sends
nothing and reconciles every figure again. A step stopped while it was
being sent is refused. The paid job is never sent twice. Two waits are not
steps, because the run sends nothing in them: the wait for the change's
two rounds and the wait for the buy. A run stopped during either is
started again. The keeper, node and validator then restart with the same
keys and data and pick up where the chain is. If the run comes back after
the round before the change has ended, it reads that round's value late.
A round that has started keeps its values, and the run says when a read
was late. Once V has opted in, a restarted validator is given nothing to
send. Its script's opt-in line would otherwise be sent again, late, since
the contract takes a second opt-in.

A state directory records which system it holds. `demo/public-run` refuses
a full system state directory, and `demo/public-run full` refuses a rewards
one, including the dated deployment. A state directory also records G's address, and
a run given another authority key is refused.

## Upgrades and stops

Every deployment puts Parameters, Registry, RoundClock, the Broker,
StakeLedger, Claims, Scores and the Buyback behind a `Proxy` whose admin is
`Upgrades` (rulebook R-27). Their addresses are the ones everyone uses, and
`deployment.json` names `Upgrades` too, so an operator can see a stop. The
run's `manifest.json` names each one's code in force as its
`implementation`, read from the chain each time the file is written, and
the three keys under `keys`. Three commands act on a running deployment,
beside `demo/public-run serve`, from the same state directory:

```sh
demo/public-run upgrade Broker contracts/test/BrokerWithTicketCount.sol:BrokerWithTicketCount \
    --call 'initializeTicketCount(uint256)' 1000
demo/public-run stop usdc|lpt|both     # with the pause key S
demo/public-run lift usdc|lpt|both     # with the upgrade key U
```

They need `LP2_RPC_URL`, `LP2_STATE_DIR`, and `LP2_UPGRADER_KEYSTORE` with
`LP2_UPGRADER_PASSWORD_FILE` (`upgrade`, `lift`) or `LP2_PAUSER_KEYSTORE`
with `LP2_PAUSER_PASSWORD_FILE` (`stop`), never the deployer's or G's. Each
holds a lock of its own in the state directory, one for U's commands and
one for S's, not the run's, so the services keep running. Each sends only
to a chain on the allow-list and the chain the state directory belongs to,
and only with the key `Upgrades` holds now.

`upgrade CONTRACT SOURCE [--replace] [--call SIGNATURE ARG...]` replaces
the code of one of the eight contracts with the contract `SOURCE` names, a
file under `contracts/` and the contract's name. Before it sends anything
it refuses:

- new code whose constructor does not take the code in force's parameter
  types in the same order; the new code is built with the argument values
  the code in force was built with, as the run recorded them;
- new code whose storage layout does not begin with the code in force's,
  compared entry by entry (name, slot, offset and type) and each type in
  full: a struct's members, a mapping's key and value, an array's element;
- code in force the run has no record of: one U put in force directly,
  not through this command, cannot be checked, so it is not upgraded by
  it.

These gates guard the command. They do not show that the new code does
with each argument what the old did, or that it does nothing the proxy
cannot stop (R-27 rule 1): that is for the review of the new code before it
is announced. The run records, in `state.json`, the source, constructor
arguments and layout of each code it deployed, and the command records the
code it deploys in `upgrades.json`.

It then decides from the chain:

- the code in force is already the new code: it writes the manifest and
  sends nothing;
- an announcement of the same code and call is pending and has not
  lapsed: it waits for it and applies it;
- a different one is pending: refused, unless `--replace`, which announces
  over it and restarts the notice;
- none, or one that lapsed: it deploys the new code with U's key, announces
  it, and prints `applies from round A + 7, lapses at round A + 28`.

It waits for that round, which the keeper under `serve` starts, or applies
at once while the contract's side is stopped (the Broker under the USDC
stop; StakeLedger, Claims, Scores and the Buyback under the LPT stop;
Parameters, Registry and RoundClock only under both). After the apply it
checks that the proxy runs the new code, `Upgrades` is still its admin and
every address and the Controller's names are unchanged, and writes the
manifest, whole, to a temporary file it renames over the old one. A
command stopped at any point and started again sends no second
announcement and no second apply: at worst it deploys the new code a
second time, which costs gas only. The notice is 7 rounds: about 70
minutes on Arbitrum Sepolia and 6 minutes on a local chain mining a block
a second.

`stop SIDE` sets the USDC stop (the Broker's payments), the LPT stop
(bonding, claims, withdrawals of parts and the buy) or both, in force from
its round through six more unless U lifts it; set again while in force, it
restarts. Time runs under a stop: a ticket that expires in it is lost, and
no claim window is extended. `lift SIDE` lifts it; a lift of a side not in
force is refused `NOT_STOPPED`.

Every command asks by a call before it sends, so a refusal sends nothing.
Each ends with exit 0 when done, 1 when a check after a send failed, and 2
when it was refused or could not run, with `REFUSED:` and the reason. It
logs to `upgrades.log` in the state directory. `./dev demo upgrade` runs the
same commands on a local chain.
