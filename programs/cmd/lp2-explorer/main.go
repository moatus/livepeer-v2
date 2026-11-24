// lp2-explorer shows a deployment from the chain: every registered node,
// every payer and what moved between them, the round and the network's
// figures. It only reads: it holds no key, sends nothing and trusts no
// service but the RPC endpoint it is given.
//
//	lp2-explorer --rpc-url URL --controller-address ADDR \
//	  --parameters-address ADDR --stake-ledger-address ADDR \
//	  --claims-address ADDR --usdc-address ADDR --first-block N \
//	  --index-dir DIR [--node ADDR] [--lpt-address ADDR] [--labels FILE] \
//	  [--log-page-blocks 5000] [--requests-per-second 5] [--listen 127.0.0.1:8080]
//
// It keeps an index of the deployment's events in DIR (programs/index),
// so a restart reads only the blocks after its last checkpoint. One loop,
// every 15 seconds, commits the index's pages up to the newest block and
// then reads a snapshot: every view at one block, the index's last
// checkpoint, named by its hash. Requests are served from what the loop
// last published and never read the chain, so the explorer answers from
// the moment it starts, and its reads do not grow with its visitors.
// Every request element it sends counts against --requests-per-second, and
// an HTTP 429 is waited out (programs/index Source).
//
// Its JSON, each amount a decimal string in the token's base unit
// (micro-USDC, wei), and each figure that was not read null, never 0, with
// the reason in "unavailable":
//
//	/network.json                   the round, fees by round, total stake, LPT supply,
//	                                the network share held, every buy and burn, the validator set
//	/orchestrators.json             every registered node: stake and cut this round,
//	                                score now, fees this round and in all, claims and LPT paid
//	/orchestrators/<address>.json   one node: what its page shows, its fees by payer and by round, its claims
//	/apps.json                      every payer: deposit now and its pending release,
//	                                funded and released in all, fees, node and network amounts
//	/apps/<address>.json            one payer: fees by round and by node
//
// Each starts with the block every figure on it is read at and that
// block's time, the RPC provider's state (by scheme and host only), the
// index's progress (index.resumed_from is the first block read after a
// restart from a checkpoint), the scenario the labels file names, and
// "unavailable". A failure of the provider is told by a fixed category
// naming its scheme and host only (chain.Describe), and nothing the URL
// carries as a key is shown (chain.Redact); nor at start.
//
// Each JSON has its page at the same path without ".json" (/network,
// /orchestrators, /apps, one node's, one payer's): HTML with no
// JavaScript, which the browser loads again every 15 seconds, drawn on
// the server from what the loop last published, its stylesheet and
// templates embedded in the binary (assets/), loading nothing from
// elsewhere. Every page shows the provider by scheme and host, whether it
// answers, the newest block and its age, the index's progress ("block X
// of Y" while it catches up, each figure then shown as catching up), the
// round clock and the scenario; while the provider fails, a banner names
// the failure's category and where to read how to get an RPC URL. With
// --node, / is that node's page; without it, / is the Network page.
//
// The figures. Each payer and fee figure is a sum of events or one view at
// the snapshot's block, as its type in docs.go says; a node's fees in a
// round equal Broker feesOf. Nothing on chain lists the nodes (R-23): they
// are the NodeRegistered events. A claim's score, what it paid and its pot
// are its Claimed event (R-12 reads the score when the claim executes); a
// node's own part of the pot is Claims partOf. A round with no Claimed
// event that a claim could have executed in (its claim window opened) is
// read from Claims entitlementOf: if Claims says it is claimed, it shows as
// claimed with what it paid, and its claim score is unavailable, never 0.
// The score now, which a claim made now would read, is scoreOf of the score
// source in force for the round (R-11, R-20), read at every snapshot, since
// a validator's vote and a change of the validator set (R-13) both move it.
// Stake and cut are StakeLedger's for the round (R-15), read once a round.
// A buy counts only if its emitter is the executor Parameters names for the
// buy's round (R-25 group 10, R-16).
//
// The Controller names the Registry, the round clock and the Broker. It
// names no other contract, so Parameters, StakeLedger, Claims, the mock
// USDC and test LPT are given as flags; the score source and the executor
// are read from Parameters for the round. Names, and a mark on simulated
// accounts, come only from the labels file:
//
//	{"scenario": "name or null", "accounts": {"0x…": {"name": "…", "simulated": true}}}
//
// read again when it changes. lp2-explorer --check-labels FILE reads FILE
// as the explorer reads its labels file and does nothing else: it exits 0
// when the explorer would take it, and 1, saying why, when not. The
// container uses it to keep a labels file only once the explorer can read it.
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"log"
	"net/http"
	"os"
	"strconv"
	"time"

	"github.com/ethereum/go-ethereum/common"

	"lp2core/programs/chain"
	"lp2core/programs/index"
)

func main() {
	rpcURL := flag.String("rpc-url", "", "the chain's JSON-RPC URL")
	controller := flag.String("controller-address", "", "the Controller the node is given")
	params := flag.String("parameters-address", "", "Parameters")
	ledger := flag.String("stake-ledger-address", "", "StakeLedger")
	claims := flag.String("claims-address", "", "Claims")
	usdc := flag.String("usdc-address", "", "the mock USDC token")
	lpt := flag.String("lpt-address", "", "test LPT, for its supply (optional)")
	first := flag.String("first-block", "", "the deployment's first block, where the index starts")
	indexDir := flag.String("index-dir", "", "the folder the index of events is kept in")
	node := flag.String("node", "", "the node / opens on (optional: without it, / is the Network page)")
	labelsFile := flag.String("labels", "", "a labels file naming accounts and the scenario (optional)")
	checkLabels := flag.String("check-labels", "", "only check that FILE is a labels file the explorer takes, and exit")
	pageBlocks := flag.Uint64("log-page-blocks", 5000, "the most blocks one eth_getLogs asks for")
	perSecond := flag.Int("requests-per-second", 5, "the most request elements sent to the provider in one second")
	listen := flag.String("listen", "127.0.0.1:8080", "the address to serve on")
	flag.Parse()
	if *checkLabels != "" {
		raw, err := os.ReadFile(*checkLabels)
		if err == nil {
			_, err = decodeLabels(raw)
		}
		if err != nil {
			fail(fmt.Errorf("%s is not a labels file: %w", *checkLabels, err))
		}
		return
	}

	addresses := map[string]string{"--controller-address": *controller, "--parameters-address": *params,
		"--stake-ledger-address": *ledger, "--claims-address": *claims, "--usdc-address": *usdc}
	if *node != "" {
		addresses["--node"] = *node
	}
	if *lpt != "" {
		addresses["--lpt-address"] = *lpt
	}
	for name, v := range addresses {
		if !common.IsHexAddress(v) {
			fail(fmt.Errorf("%s %q is not an address", name, v))
		}
	}
	if *rpcURL == "" {
		fail(errors.New("--rpc-url is required"))
	}
	if *indexDir == "" {
		fail(errors.New("--index-dir is required"))
	}
	if *perSecond < 1 || *pageBlocks < 1 {
		fail(errors.New("--requests-per-second and --log-page-blocks are at least 1"))
	}
	firstBlock, err := strconv.ParseUint(*first, 10, 64)
	if err != nil {
		fail(fmt.Errorf("--first-block %q is not a block number", *first))
	}
	cfg := config{controller: common.HexToAddress(*controller), params: common.HexToAddress(*params),
		ledger: common.HexToAddress(*ledger), claims: common.HexToAddress(*claims),
		usdc: common.HexToAddress(*usdc), first: firstBlock, rpcURL: *rpcURL, labels: *labelsFile}
	if *node != "" {
		n := common.HexToAddress(*node)
		cfg.node = &n
	}
	if *lpt != "" {
		a := common.HexToAddress(*lpt)
		cfg.lpt = &a
	}
	ix, err := index.Open(*indexDir, firstBlock, *pageBlocks)
	if err != nil {
		fail(err)
	}
	if err := ix.Check(cfg.controller, firstBlock); err != nil {
		fail(err)
	}
	waits := &chain.RetryAfter{}
	client, err := chain.DialRPCWaits(context.Background(), *rpcURL, waits)
	if err != nil {
		fail(errors.New(failure(err, *rpcURL)))
	}
	src := index.NewSource(client, *perSecond)
	src.Retry = waits.Take
	e := newExplorer(cfg, src, ix)
	if from := ix.ResumedFrom(); from != nil {
		log.Printf("index in %s: resumed from block %d", *indexDir, *from)
	} else {
		log.Printf("index in %s: reading from block %d", *indexDir, firstBlock)
	}
	go e.run(context.Background())
	log.Printf("serving on http://%s/", *listen)
	srv := &http.Server{Addr: *listen, Handler: handler(e), ReadHeaderTimeout: 10 * time.Second}
	fail(srv.ListenAndServe())
}

// failure is how a failure to reach the chain at start is told: the step,
// and the provider's failure by its fixed category (chain.Describe).
func failure(err error, rpcURL string) string {
	return "connect to the chain: " + chain.Describe(err, rpcURL)
}

func fail(err error) {
	fmt.Fprintln(os.Stderr, "lp2-explorer:", err)
	os.Exit(1)
}
