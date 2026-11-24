# The demos

Each demo runs the real programs on a fresh local chain, with keys it makes
itself, and checks what it reads from the chain against the calculator.
`./dev demo` runs the rewards demo; `./dev demo <name>` runs another:
`payments`, `full`, `buyback`, `light`, `container` or `upgrade`.

## What a run needs

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

## One reward cycle with scripted validators

`./dev demo` runs one full reward cycle, the one the shared example
`ledger-cycle` describes, on a fresh local chain with four nodes running
the real node. Holders bond test LPT to the nodes, the nodes are paid in
mock USDC for real jobs, and scripted validators (`programs/validators`,
run from `demo/validators.script`) opt in, take the three seats and vote on
the nodes. Each node then claims its round's LPT reward at the median of
the votes, split between its payout address and its holders, and the
validators are paid for the rounds they sat. Along the way it shows the
refusals: a claim too early or by someone else, a second claim or
withdrawal, a vote by a validator not seated. It stops the keeper while a
round ends: anyone may start the next round, and the restarted keeper
mints what was missed, in full.

Every balance, stake, mint and refusal read from the chain is printed
beside the calculator's figure for the same steps and checked against
`ledger-cycle`'s worked figures. The run is then written as three files of
cases (`ledger-case.json`, `validators-case.json` and
`validator-pay-cases.json`), and `./calculator/check` runs on each. The
node, the keeper, `lp2-fund`, `lp2-stake` and the scripted validators send
every transaction; the demo itself only registers nodes, gives out test
LPT and mines empty blocks where it would otherwise wait for the chain.

What it does not show: a validator's score is the script's choice, not a
judgment of work, and the set changes only when someone calls Scores (here
an opt-in), not whenever stake moves.

## Paid jobs with no stake

`./dev demo payments` runs paid jobs against the payments rulebook. What it
does, in this order, printing each step:

1. Starts a fresh local chain, deploys the payments contracts, and starts the
   tiny model, the node's orchestrator for a node N, and a signer for each
   of two payers, P and P2.
2. **Refusal 1.** N is not registered, so the orchestrator will not issue a
   payment challenge (HTTP 502). N registers, for free and with no stake,
   and the same request gets a challenge (HTTP 402).
3. P puts 10 USDC of mock USDC in its deposit and sets 0.02 USDC aside for
   N (its earmark). The orchestrator checks each ticket alone against the
   deposit and the earmark, not everything it already holds unredeemed, so
   N may hold more in tickets than the earmark guarantees.
4. **Six real jobs**, each paid with one ticket of 10,001 micro-USDC, which
   the node redeems itself. Every balance read from the chain is printed
   beside the calculator's figure for the same steps, and then checked
   against the worked figures: P's deposit 9,939,994, the earmark
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

## Buyback, authority changes and settlement recovery

`./dev demo full` starts a fresh local chain and runs paid jobs through
the node. It shows an announced parameter change taking effect seven
rounds later, a withdrawn change leaving the old value in force, and a
new operator key claiming while the old key is refused. It restarts the
node during settlement and checks that each payment is collected once.
The keeper buys and burns test LPT. The demo checks the figures against
the calculator. It also runs `lp2-explorer` for the node and checks the
explorer's figures against the chain, and those of a claimed round against
the calculator.

## The buyback

`./dev demo buyback` runs the buyback's walk on a fresh local chain,
with no node or model. It deploys the rewards demo's contracts, then the test
pool, Parameters, which names the Buyback as the one executor that may
draw the network share, and the Buyback. It sends 7 test LPT to the Buyback beforehand. Each of the walk's
22 steps (paid jobs, the pool's seeding, trades, pokes and buys) is a
transaction at the walk's own time, and each line is checked against
the shared examples: every buy's USDC in, LPT bought and burned, average price and
bound, and every refusal by its code and arguments, step 20's `SLIPPAGE`
included, printed as expected. It then checks that supply fell by exactly
the LPT the Bought events burned, that the 7 LPT sent beforehand is still
there, writes the run as a `buyback` case into a copy of the shared
examples and runs `./calculator/check` on it. It runs `lp2-keeper
--once --parameters-address` twice: after round 9's buy it logs
`ALREADY_BOUGHT` as expected and sends nothing; in round 10 its call would
succeed, so it buys. Last, it moves the buyback without redeploying: with
`lp2-keeper` left running, the governance stand-in announces a second
Buyback as the executor and a second test pool as the pool. In the round
before the change counts, the second Buyback's buy is refused at the
Broker and the first one's succeeds. From the round it counts, the
running keeper pokes and buys at the second Buyback, whose buy has the
walk's first buy's figures, and the first Buyback's buy is refused at the
Broker with nothing moved. Its stand-ins are named in
`deployments/full-local.json`.

## The standing services: a node joins and is paid

`./dev demo light` runs, on a fresh local chain with the rewards demo's contracts
and no model or Docker, the services that keep a testnet's traffic going.
Each node runs the unmodified node's orchestrator in front of
`lp2-responder`, a light responder that answers each paid request with a
small JSON echo. `lp2-sender` holds the payer's key, beside the payer's
signer on loopback, and answers joins. A registered node joins by posting
to the sender's `/join`:

```json
{"node": "0x…", "url": "https://…", "issuedAt": 1795000000, "signature": "0x…"}
```

`signature` is the node key's EIP-191 personal signature (`cast wallet
sign`) over the bytes
`lp2-join:v1:<chainId>:<registry>:<node>:<url>:<issuedAt>`, with the
Registry's and the node's addresses in lowercase 0x hex and `issuedAt` in
Unix seconds. The answer is 200 when the node is listed or its URL
updated, and otherwise a refusal with one stable code. The checks a join
must pass, in order, each refusal's code and status, and how the sender
pays are in the header of
[`programs/cmd/lp2-sender/main.go`](programs/cmd/lp2-sender/main.go). A
node operator needs an orchestrator reachable at a public URL, its runner,
and test ETH for gas: one registration, then one redemption for each paid
request, about one a round, since every ticket wins.

The demo sends each join that should be refused and checks its code. It
then shows nodes N and M paid once a round with nobody acting, a node whose
runner never answers given up at the 10 s bound while they are still paid,
and a sender restarted within a round paying nobody twice, and it reads
each node's tickets and payouts from the chain. A run takes about three
and a half minutes, and uses 14 local ports, from 47700 to 24 above it.

Beside the sender runs `lp2-faucet`, which holds a faucet account's key and
gives each node the Registry shows registered one grant of test LPT (1,000
unless set), once, so that a node has LPT to bond before it starts. It
answers nothing: an operator sees the grant in its node's own balance of
test LPT. Before it broadcasts a grant it writes the signed transaction to
a journal, so a crash at any moment never grants a node twice and never
loses a grant; the header of
[`programs/cmd/lp2-faucet/main.go`](programs/cmd/lp2-faucet/main.go) says
how, the log lines it writes, and how to run it. The demo kills it three
times with a grant in flight, and the token's Transfer logs then show every
registered node granted exactly once.

## The operator container: a node from one command

[`programs/container/`](programs/container/README.md) builds one image
that runs a node on the testnet: the unmodified node's orchestrator,
`lp2-responder`, `lp2-stake`, `lp2-explorer` and an entrypoint over them.
Started with one command, it makes a node key and an operator key, prints
both addresses to be funded with test ETH, and from then on needs nobody:
it registers the node, waits for the faucet's grant, makes the operator
key the node's operator, bonds the grant to the node, starts the node once
the bond counts, joins the sender, and each round claims and withdraws the
LPT reward that is due. Each step asks the chain first and does only what
is not done, so a restart at any moment repeats nothing. Its
[README](programs/container/README.md) is the operator's instructions:
how to run it, what to fund, which port must be reachable, how long each
step takes and how to read its explorer.

`./dev demo container` builds the image and runs it on the full system's
contracts on a local chain mining a block a second, with the services
running as `./dev demo light` runs them. After it funds the two keys, the
demo only kills and restarts the container, at the moments a restart must
survive, and starts others beside it: a second on the same data folder,
which refuses to start, and three whose RPC provider fails. It then reads
from the chain that nothing was done twice, writes the run as a ledger case
that `./calculator/check` reproduces, and checks the container's explorer
against it. The pool is not seeded, so no buy
draws the network share or burns LPT and the case holds every LPT and USDC
movement. A run takes about 12 minutes once the image is built. It uses
seven local ports, from `LP2_DEMO_PORT_BASE` (47700 unless set) to 30 above
it; the chain and the sender also listen on Docker's bridge address, where
the container reaches them.

## Replacing a contract's code in place

`./dev demo upgrade` replaces the Broker's code on a running full system,
on a local chain, with `BrokerWithTicketCount` (the Broker with a ticket
counter), by the same command a team runs on the public testnet,
`demo/public-run upgrade`. It shows the command's gates refusing before
anything is sent, the command killed and run again at each step with one
announcement and one apply in all, every view reading the same across the
apply, and a stop drill, with every figure reconciled against the
calculator; `demo/upgrade.py` describes each step. A run takes about four
minutes; it uses the rewards demo's ports and Docker.

## Run results

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

Every stand-in the payments and rewards demos use (the local chain, mock USDC, the fixed price,
every ticket winning, 50-block rounds, the tiny model, Parameters with no
key (so every value is the deployment's) and no buyback, and in the rewards demo
test LPT and its genesis supply, the treasury address, the scripted
validators, three validator seats for the paper's 33 and the keeper) is
named in its manifest,
`deployments/rewards-local.json` or `deployments/payments-local.json`, which
it reads its settings from.
