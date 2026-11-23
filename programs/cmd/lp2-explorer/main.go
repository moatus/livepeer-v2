// lp2-explorer serves one page, on the operator's own machine, that shows
// from the chain whether their node and the testnet are working. It only
// reads: it holds no key, sends nothing and trusts no service but the RPC
// endpoint it is given.
//
//	lp2-explorer --rpc-url URL --controller-address ADDR \
//	  --parameters-address ADDR --stake-ledger-address ADDR \
//	  --claims-address ADDR --usdc-address ADDR \
//	  --first-block N --node ADDR [--listen 127.0.0.1:8080]
//
// GET / is the page: HTML with no JavaScript, which the browser loads
// again every 15 seconds. GET /status.json is the same figures as JSON,
// each amount a decimal string in the token's base unit (micro-USDC, wei)
// and each figure that could not be read null, never 0, with the reason in
// "unavailable". Each request reads the chain afresh, every view at one
// block, the newest, so a figure that failed is read again on the next
// load. A reason never quotes the RPC provider: its failure is told by a
// fixed category naming its scheme and host only (chain.Describe), and
// nothing the URL carries as a key is shown (chain.Redact); nor at start.
//
// The node: whether it is registered (R-01), its operator key (R-26) and
// payout address; its own bond and its stake in the current round
// (StakeLedger bondAt and stakeAt, R-15); the score a claim made now
// would read, from the score source in force for the current round
// (R-11, R-20); test USDC at its payout address; and the node key's ETH,
// with a warning under 0.001 ETH. Then, for the current round and the 28
// before it, every round the deployment's reward delay of 7 still leaves
// claimable: the fees counted for the node (Broker feesOf: fees from
// settled tickets, not requests answered), its stake at the round's
// start, and its reward (R-12): claimable from round n + d to n + d + 21,
// d the reward delay in force for round n (R-25 group 4); claimed or not;
// for a claimed round, the score its claim applied (its Claimed event:
// R-12 reads the score when the claim executes, and a later vote can
// change the score now); what the claim minted and the pot (Claims
// entitlementOf); and the node's own part of that pot, as a holder of its
// own bond, and whether it is withdrawn (partOf). A round not yet claimed
// is shown with the score now, marked as such.
//
// The testnet: the current round, and the block and time it started; the
// total fees counted this round; the validator set this round (membersAt
// of the deployment's score source, R-13); the buyback executor in force
// for the round (R-25 group 10); and its latest buys and burns (R-16).
//
// The Controller names the Registry, the round clock and the Broker. It
// names no other contract, so Parameters, StakeLedger, Claims and the
// mock USDC are given as flags; the score source and the executor are
// read from Parameters for the round.
//
// Every figure is a contract view except three, which come from events:
// the executor's Bought events (the buys and burns), the round clock's
// RoundStarted (when the round started; on Arbitrum the clock's stored
// block hash is not a block's, so no view gives it) and Claims' Claimed
// events for the node (the score each claim applied; no view keeps it).
// They are read by two filtered eth_getLogs a page, sent in one batch:
// one on the executor's and the clock's addresses, one on Claims for the
// node's indexed topic. Pages are 5,000 blocks: from --first-block, the
// deployment's first block, on the first read, and after that only the
// blocks since the last one read. A read that would take more than 50
// pages stops there and goes on at the next load; until it reaches the
// newest block the buys and the claims' scores show as unavailable. A new
// executor in force starts the scan again from the first block. Logs are
// read up to the newest block, and nothing is written to disk: a restart
// reads from the first block again.
package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"log"
	"net/http"
	"os"
	"strconv"
	"sync"
	"time"

	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/rpc"

	"lp2core/programs/chain"
)

// readTimeout bounds one read of the chain.
const readTimeout = 30 * time.Second

func main() {
	rpcURL := flag.String("rpc-url", "", "the chain's JSON-RPC URL")
	controller := flag.String("controller-address", "", "the Controller the node is given")
	params := flag.String("parameters-address", "", "Parameters")
	ledger := flag.String("stake-ledger-address", "", "StakeLedger")
	claims := flag.String("claims-address", "", "Claims")
	usdc := flag.String("usdc-address", "", "the mock USDC token")
	first := flag.String("first-block", "", "the deployment's first block, where the event scan starts")
	node := flag.String("node", "", "the node to show")
	listen := flag.String("listen", "127.0.0.1:8080", "the address to serve the page on")
	flag.Parse()

	addresses := map[string]string{"--controller-address": *controller, "--parameters-address": *params,
		"--stake-ledger-address": *ledger, "--claims-address": *claims, "--usdc-address": *usdc, "--node": *node}
	for name, v := range addresses {
		if !common.IsHexAddress(v) {
			fail(fmt.Errorf("%s %q is not an address", name, v))
		}
	}
	if *rpcURL == "" {
		fail(fmt.Errorf("--rpc-url is required"))
	}
	firstBlock, err := strconv.ParseUint(*first, 10, 64)
	if err != nil {
		fail(fmt.Errorf("--first-block %q is not a block number", *first))
	}
	client, err := rpc.Dial(*rpcURL)
	if err != nil {
		fail(errors.New(failure(err, *rpcURL)))
	}
	cfg := config{controller: common.HexToAddress(*controller), params: common.HexToAddress(*params),
		ledger: common.HexToAddress(*ledger), claims: common.HexToAddress(*claims),
		usdc: common.HexToAddress(*usdc), node: common.HexToAddress(*node), first: firstBlock, rpcURL: *rpcURL}
	log.Printf("serving %s's page on http://%s/", cfg.node.Hex(), *listen)
	srv := &http.Server{Addr: *listen, Handler: handler(newExplorer(cfg, client)), ReadHeaderTimeout: 10 * time.Second}
	fail(srv.ListenAndServe())
}

// failure is how a failure to reach the chain at start is told: the step,
// and the provider's failure by its fixed category (chain.Describe).
func failure(err error, rpcURL string) string {
	return "connect to the chain: " + chain.Describe(err, rpcURL)
}

// handler serves the page and its JSON. Reads run one at a time, since
// they share the event scan.
func handler(e *explorer) http.Handler {
	var mu sync.Mutex
	status := func(r *http.Request) *Status {
		mu.Lock()
		defer mu.Unlock()
		ctx, cancel := context.WithTimeout(r.Context(), readTimeout)
		defer cancel()
		s := e.read(ctx)
		for i, why := range s.Unavailable {
			s.Unavailable[i] = chain.Redact(why, e.cfg.rpcURL)
		}
		return s
	}
	mux := http.NewServeMux()
	mux.HandleFunc("GET /{$}", func(w http.ResponseWriter, r *http.Request) {
		var body bytes.Buffer
		if err := render(&body, status(r)); err != nil {
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		w.Write(body.Bytes())
	})
	mux.HandleFunc("GET /status.json", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		enc := json.NewEncoder(w)
		enc.SetIndent("", " ")
		enc.Encode(status(r))
	})
	return mux
}

func fail(err error) {
	fmt.Fprintln(os.Stderr, "lp2-explorer:", err)
	os.Exit(1)
}
