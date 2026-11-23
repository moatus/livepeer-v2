# Run a node: the operator container

One container runs a node on the Livepeer 2.0 testnet. Start it with one
command, fund the two addresses it prints with test ETH, and from then on
nobody needs to act: it registers the node, receives the testnet faucet's
grant of test LPT, bonds it, starts the unmodified node serving a light
responder, joins the testnet's sender, is sent one paid request a round,
and claims and withdraws each round's LPT reward when it becomes
claimable. A page on your own machine shows all of it, read from the
chain.

Everything here is a test network. The ETH is test ETH, the USDC is a mock
anyone can mint, and the LPT is test LPT with no value. Never send real
funds to the addresses the container prints.

## What you need

- A machine with Docker, where the container can run for days.
- A TCP port the internet can reach on that machine (8935 below). See
  "Ports" for what a home router or a mobile network does to that.
- A JSON-RPC URL for the testnet's chain (Arbitrum Sepolia for the public
  testnet), from any provider or your own node.
- Test ETH for that chain, from a public faucet, to fund two addresses.
- The testnet's deployment file: a URL (or a file) the testnet publishes.

## Build the image

From a checkout of this repository:

```sh
docker build -t lp2-node -f programs/container/Dockerfile programs
```

The build fetches the node's source at the commit the deployment names,
builds its orchestrator and the helper programs, and takes a few minutes
the first time. The image runs as user 10001, never as root.

## Run it

```sh
docker volume create lp2-node
docker run -d --name lp2-node --restart unless-stopped \
  -v lp2-node:/data \
  -p 8935:8935 -p 127.0.0.1:8080:8080 \
  -e LP2_DEPLOYMENT=https://<the testnet's deployment file> \
  -e LP2_RPC_URL=https://<your Arbitrum Sepolia RPC URL> \
  -e LP2_PUBLIC_URL=http://<your public IP address or host name>:8935 \
  lp2-node
docker logs -f lp2-node
```

The data folder, `/data` in the container, holds the two keys, the node's
payment database and the logs. A named volume, as above, starts owned by
the container's user. To keep it in a folder of your own instead, make the
folder writable by user 10001 (`sudo chown 10001:10001 <folder>`), or run
the container as yourself by adding `--user "$(id -u):$(id -g)"`, and give
`-v <folder>:/data`. Without either, the container stops with
"/data is not a folder that user 10001 can write".

One container runs on one data folder: a second one started on the same
folder stops at once with "another container is running on /data;
refusing to start". A data folder belongs to one deployment; for a new
deployment, use a new folder.

## What to fund

On its first start the container makes two keys and prints both addresses:

```
node key N 0x…; operator key H 0x…
fund both keys with test ETH on chain 421614: N 0x… holds 0 wei, H 0x… holds 0 wei
```

The keystores and their password stay in `/data/keys` and are never
printed. Send test ETH to both addresses. N is the node: it registers,
names H as its operator key, moves the faucet's grant to H, and after that
is used by the node alone, for one redemption of each paid request (every
ticket wins, so about one a round, each 145,000 to 219,000 gas). H is the
operator key: it accepts that role, bonds the LPT to N, and makes every
claim and withdrawal, about one of each a round. N spends the most: at a
gas price of 0.1 gwei a redemption costs about 0.00002 ETH, and there are
about 144 rounds a day. The explorer warns when N's ETH falls below 0.001.
When a key runs out, the step waits or fails and is tried again; fund it
and the container carries on.

## What happens, and how long it takes

| Step | When |
|---|---|
| N registers | as soon as both keys hold ETH |
| The faucet grants N 1,000 test LPT | within seconds of the registration |
| N names H its operator key, moves the grant to H, H bonds it to N | about a minute |
| The bond counts | from the next round: up to one round (about 10 minutes on the public testnet) |
| The node starts and joins the sender | at once; the sender sends the first paid request within seconds |
| Paid requests | one a round from then on |
| Fees count | in the round the node redeems the ticket, about 40 blocks after it |
| The first claim | 7 rounds after the first round with fees, about 70 to 90 minutes |
| Each withdrawal of H's part | within `LP2_POLL` seconds of the claim |

So the first claim comes about one and a half to two hours after the keys
are funded. Every step asks the chain first and does only what is not
done, so a restart at any moment (`docker restart lp2-node`, a reboot, a
crash) finishes the work without repeating a registration, a bond or a
claim. A program that exits stops the container, and `--restart
unless-stopped` starts it again.

The grant is a finite testnet subsidy: one grant to each registered node,
while the faucet holds test LPT and until it has granted the most nodes it
allows. A node registered after that waits for its grant; the container
says so in its log, and nothing else happens until the grant arrives.

## Ports

The testnet's sender must open a connection to `LP2_PUBLIC_URL`: it checks
the node there when it joins, and sends each paid request there. Inside
the container the node listens on all interfaces at the port that URL
names (8935 above, or 80 and 443 for a URL with no port), so publish that
port (`-p 8935:8935`). Any port from 1 to 65535 works except the
explorer's (`LP2_EXPLORER_LISTEN`, 8080), however either is written
(`08080` is 8080): the responder and the node's metrics listen on loopback
inside the container and move off the port the URL names. The container's
log says at start which ports it chose.

The node answers plain HTTP. An `https://` URL needs something in front of
it, such as a reverse proxy or a tunnel, that holds the certificate, ends
TLS and forwards plain HTTP to the container's port.

- The URL must resolve to a public address: the sender refuses private,
  loopback and other reserved addresses.
- **A home router** sends nothing in by default. Forward the TCP port from
  the router to the machine (port forwarding), and use the router's public
  address in the URL. A firewall on the machine must also let it in.
- **Carrier-grade NAT** (many mobile and some home connections: the
  router's own internet address is in 100.64.0.0/10 or another private
  range) cannot be reached from outside at all, and forwarding does not
  help. Run the container on a machine with a public address, such as a
  rented server, or behind a tunnel that gives you a public URL and
  forwards it to the container's port.
- Until the node can be reached, the join is refused with `UNREACHABLE`
  and the container tries again every `LP2_POLL` seconds; the log shows the
  sender's answer.

The explorer is published on the host's loopback only (`-p
127.0.0.1:8080:8080`): it is for you, not the internet. On a remote
server, reach it through an SSH tunnel (`ssh -L 8080:127.0.0.1:8080
<server>`).

## Read the explorer

Open <http://127.0.0.1:8080/>; it reloads itself every 15 seconds, and
`/status.json` holds the same figures. Every figure is read from the chain
when the page loads; one that could not be read says "unavailable".

- **The node**: registered or not; its operator key, which is H once the
  rotation is done; its payout address, N itself; its stake this round,
  which is H's bond once it counts; the score a claim made now would read;
  its test USDC, the node's half of each paid request; and N's ETH.
- **Each recent round**: the fees counted for the node and its stake, and
  where the round's reward stands: waiting (not yet claimable), claimable
  from and until which round, claimed (with the score the claim applied,
  what it paid and the pot for the node's holders), or expired. "Own bond"
  and "own part" are N's own, and stay 0: the bond and the parts are H's.
  H's withdrawn parts arrive as test LPT in H's own balance.
- **The testnet**: the round, when it started, the fees this round, the
  validator set, and the buyback's latest buys and burns.

The container's own log (`docker logs lp2-node`) says each step it takes,
and each claim and withdrawal it sends. The node's, the responder's and
the explorer's logs are in `/data/logs`. When the RPC provider cannot be
reached or refuses a request, the container's log names the provider's
host, and nothing in it, in `/data/logs` or in the explorer's page and
JSON shows the rest of `LP2_RPC_URL`, where a provider puts its key: not
the URL, and not a piece of it that a provider echoes back. A key
in the host name itself, which few providers use, is not hidden.

## The deployment file

`LP2_DEPLOYMENT` is a URL or a path inside the container (mount it with
`-v`). It names nothing private: a testnet publishes it for every
operator.

```json
{
 "testnet": "a name for the deployment",
 "chain_id": 421614,
 "first_block": 331292296,
 "contracts": {
  "Controller": "0x…", "Parameters": "0x…", "StakeLedger": "0x…",
  "Claims": "0x…", "TestLPT": "0x…", "MockUSDC": "0x…"
 },
 "node": {
  "job_price_usd": "0.999999", "price_unit": "fixed",
  "wei_per_usd": "1000000", "ticket_face_value": "1000000",
  "ticket_win_prob": "115792089237316195423570985008687907853269984665640564039457584007913129639934"
 },
 "sender_url": "https://…"
}
```

| Field | Is |
|---|---|
| `testnet` | The deployment's name, shown in the log |
| `chain_id` | The chain's id; the container stops if `LP2_RPC_URL` is another chain |
| `first_block` | The deployment's first block, where the explorer starts reading events |
| `contracts` | The Controller the node is given, which names the Registry, the round clock and the Broker; and Parameters, StakeLedger, Claims, test LPT and mock USDC, which it does not |
| `node` | The node's price and tickets: the price of one request in USD, its unit, the fixed rate of the chain's token units to the dollar, each ticket's face value and its win probability |
| `sender_url` | The sender's public URL, where the node joins (`POST /join`) |

## Settings

| Variable | Is |
|---|---|
| `LP2_DEPLOYMENT` | The deployment file: a URL or a path. Required |
| `LP2_RPC_URL` | The chain's JSON-RPC URL. Required. It is kept in `/data/node/rpc-url` for the node |
| `LP2_PUBLIC_URL` | The node's URL as the sender reaches it, `http://` or `https://`. Required |
| `LP2_POLL` | Seconds between checks, 30 unless set |
| `LP2_EXPLORER_LISTEN` | Where the explorer listens inside the container, `0.0.0.0:8080` unless set |
| `LP2_TEST_HOLD` | For tests only: `approved`, `started` or `claimed` stops the run after that step, so a test can kill it there |

## Try it on a local chain

`./dev demo container` builds this image and runs it against a local chain
with the testnet's services, kills and restarts it at three moments, and
checks every step against the chain and the independent calculator (the
repository's README).
