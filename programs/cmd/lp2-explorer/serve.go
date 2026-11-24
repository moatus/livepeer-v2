package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/url"
	"os"
	"strings"
	"sync"
	"time"

	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/common/hexutil"
	"github.com/ethereum/go-ethereum/crypto"
	"github.com/ethereum/go-ethereum/rpc"

	"lp2core/programs/index"
)

// cycleEvery is how often the loop reads: commits the pages since the
// last and then reads a snapshot.
const cycleEvery = 15 * time.Second

// config is what the explorer is given.
type config struct {
	controller, params, ledger, claims, usdc common.Address
	lpt                                      *common.Address
	node                                     *common.Address // the container's node, which / opens on
	first                                    uint64
	rpcURL                                   string // only to tell its failure by scheme and host (chain.Describe)
	labels                                   string
}

// The steps of a cycle, by which a failure is told until the step next
// succeeds.
const (
	stepNewest    = "the chain's newest block"
	stepContracts = "the contracts the Controller names"
	stepIndex     = "the index"
	stepSnapshot  = "the snapshot"
	stepLabels    = "the labels file"
)

var steps = []string{stepNewest, stepContracts, stepIndex, stepSnapshot, stepLabels}

// explorer runs the loop that reads the chain, and serves what the loop
// last published: HTTP requests never wait on the chain and never read it.
type explorer struct {
	cfg   config
	src   *index.Source
	ix    *index.Index
	every time.Duration
	// stop is called with a deployment the index refuses: the explorer
	// stops, rather than read another deployment into the folder.
	stop func(error)

	// The loop's own, untouched by requests.
	contracts *index.Contracts
	kept      map[string][]any  // views whose answer cannot change
	times     map[uint64]uint64 // block times read
	labels    labels
	rollbacks int // the index's rollbacks this loop has seen

	mu       sync.Mutex
	rpc      RPCState
	progress Progress
	failed   map[string]string // each step failing, with its reason
	snap     *snapshot
	scenario *string
}

func newExplorer(cfg config, src *index.Source, ix *index.Index) *explorer {
	e := &explorer{cfg: cfg, src: src, ix: ix, every: cycleEvery, stop: func(err error) { fail(err) },
		kept: map[string][]any{}, times: map[uint64]uint64{}, labels: labels{path: cfg.labels}, failed: map[string]string{}}
	e.rpc.Provider = provider(cfg.rpcURL)
	e.progress = Progress{FirstBlock: ix.First(), ResumedFrom: ix.ResumedFrom(), PageBlocks: ix.PageBlocks}
	if through, _, ok := ix.Through(); ok {
		e.progress.Through = &through
	}
	src.Seen = e.seen
	return e
}

// provider is the RPC URL's scheme and host, and nothing else it carries.
func provider(rpcURL string) string {
	u, err := url.Parse(rpcURL)
	if err != nil || u.Host == "" {
		return ""
	}
	return u.Scheme + "://" + u.Host
}

// seen publishes the provider's state after each batch: the failure's
// category from the first failed request until the next success.
func (e *explorer) seen(_ string, err error) {
	e.mu.Lock()
	defer e.mu.Unlock()
	answering := err == nil
	e.rpc.Answering = &answering
	if err != nil {
		why := describe(err, e.cfg.rpcURL)
		e.rpc.Failure = &why
	} else {
		e.rpc.Failure = nil
	}
}

// note publishes a step's failure, or with err nil its success.
func (e *explorer) note(step string, err error) {
	e.mu.Lock()
	defer e.mu.Unlock()
	if err == nil {
		delete(e.failed, step)
		return
	}
	e.failed[step] = step + ": " + describe(err, e.cfg.rpcURL)
}

// run reads every cycle until ctx ends.
func (e *explorer) run(ctx context.Context) {
	for {
		e.cycle(ctx)
		select {
		case <-ctx.Done():
			return
		case <-time.After(e.every):
		}
	}
}

// cycle reads the newest block, commits the index's pages up to it,
// publishing its progress after each, then reads the snapshot at the last
// checkpoint and publishes it once all of it is read.
func (e *explorer) cycle(ctx context.Context) {
	if err := e.labels.reload(); err != nil {
		e.note(stepLabels, err)
	} else {
		e.note(stepLabels, nil)
	}
	e.mu.Lock()
	e.scenario = e.labels.scenario
	e.mu.Unlock()

	var newest struct {
		Number hexutil.Uint64 `json:"number"`
		Time   hexutil.Uint64 `json:"timestamp"`
	}
	req := []rpc.BatchElem{{Method: "eth_getBlockByNumber", Args: []any{"latest", false}, Result: &newest}}
	e.src.Send(ctx, stepNewest, req)
	e.note(stepNewest, req[0].Error)
	if req[0].Error != nil {
		return
	}
	head, headTime := uint64(newest.Number), uint64(newest.Time)
	e.mu.Lock()
	e.rpc.NewestBlock, e.rpc.NewestBlockTime, e.progress.Newest = &head, &headTime, &head
	e.mu.Unlock()

	if e.contracts == nil {
		err := e.resolve(ctx)
		e.note(stepContracts, err)
		if err != nil {
			if errors.Is(err, index.ErrDeployment) {
				e.stop(err)
			}
			return
		}
	}
	err := e.ix.CatchUp(ctx, e.src, *e.contracts, head, e.published)
	e.published()
	e.note(stepIndex, err)
	if err != nil {
		return
	}
	through, hash, ok := e.ix.Through()
	if !ok {
		return
	}
	if e.ix.Rollbacks() != e.rollbacks {
		// Everything derived from the chain goes with the branch it came from.
		e.rollbacks, e.kept, e.times = e.ix.Rollbacks(), map[string][]any{}, map[uint64]uint64{}
	}
	s, err := e.read(ctx, through, hash)
	e.note(stepSnapshot, err)
	if err != nil {
		return
	}
	e.mu.Lock()
	e.snap = s
	e.mu.Unlock()
}

// published publishes the index's progress.
func (e *explorer) published() {
	e.mu.Lock()
	defer e.mu.Unlock()
	if through, _, ok := e.ix.Through(); ok {
		e.progress.Through = &through
	} else {
		e.progress.Through = nil
	}
	e.progress.ResumedFrom, e.progress.PageBlocks = e.ix.ResumedFrom(), e.ix.PageBlocks
	e.progress.CaughtUp = e.progress.Through != nil && e.progress.Newest != nil && *e.progress.Through >= *e.progress.Newest
}

// resolve reads the contracts the Controller names and the chain's id,
// and writes the index's first line or refuses a file of another
// deployment.
func (e *explorer) resolve(ctx context.Context) error {
	names := []string{"BondingManager", "RoundsManager", "TicketBroker"}
	var vs []*view
	for _, n := range names {
		vs = append(vs, &view{what: "the Controller's " + n, to: e.cfg.controller, method: "getContract", args: []any{crypto.Keccak256Hash([]byte(n))}})
	}
	e.call(ctx, "latest", vs...)
	var chainID hexutil.Uint64
	req := []rpc.BatchElem{{Method: "eth_chainId", Result: &chainID}}
	e.src.Send(ctx, "the chain's id", req)
	for _, v := range vs {
		if v.err == nil && v.out[0].(common.Address) == (common.Address{}) {
			v.err = errors.New("not named")
		}
		if v.err != nil {
			return errors.Join(errors.New(v.what), v.err)
		}
	}
	if req[0].Error != nil {
		return req[0].Error
	}
	if err := e.ix.Begin(index.Header{ChainID: uint64(chainID), Controller: e.cfg.controller, FirstBlock: e.cfg.first}); err != nil {
		return err
	}
	e.contracts = &index.Contracts{Registry: vs[0].out[0].(common.Address), Clock: vs[1].out[0].(common.Address),
		Broker: vs[2].out[0].(common.Address), Claims: e.cfg.claims, Parameters: e.cfg.params}
	return nil
}

// head is the Head every document starts with, from what was last
// published: the snapshot's block and reasons, with each step failing now.
func (e *explorer) head() (Head, *snapshot) {
	e.mu.Lock()
	defer e.mu.Unlock()
	h := Head{RPC: e.rpc, Index: e.progress, Scenario: e.scenario, Unavailable: []string{}}
	for _, s := range steps {
		if why, ok := e.failed[s]; ok {
			h.Unavailable = append(h.Unavailable, why)
		}
	}
	s := e.snap
	if s == nil {
		if len(h.Unavailable) == 0 {
			h.Unavailable = append(h.Unavailable, "every figure: not read yet")
		}
		return h, nil
	}
	block, t := s.block, s.time
	h.Block, h.BlockTime = &block, &t
	h.Unavailable = append(h.Unavailable, s.unavailable...)
	return h, s
}

// nodeDoc is the node's document, with every figure null before the first
// snapshot or for a node it does not hold.
func (e *explorer) nodeDoc(n common.Address) (*NodeDoc, bool) {
	h, s := e.head()
	if s != nil {
		if d, ok := s.nodes[n]; ok {
			doc := *d
			doc.Head = h
			return &doc, true
		}
	}
	named := e.labels.named(n)
	doc := &NodeDoc{Head: h, Label: named.Label, Simulated: named.Simulated,
		Node: Node{Address: n, LowGasWei: newInt(lowGasWei)}}
	return doc, s == nil
}

// handler serves the documents. The page at / is the node's when the
// explorer was given one.
func handler(e *explorer) http.Handler {
	mux := http.NewServeMux()
	writeJSON := func(w http.ResponseWriter, status int, v any) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(status)
		enc := json.NewEncoder(w)
		enc.SetIndent("", " ")
		enc.Encode(v)
	}
	address := func(r *http.Request) (common.Address, bool) {
		name, ok := strings.CutSuffix(r.PathValue("file"), ".json")
		if !ok || !common.IsHexAddress(name) {
			return common.Address{}, false
		}
		return common.HexToAddress(name), true
	}
	notFound := func(w http.ResponseWriter, what string) {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": what})
	}
	mux.HandleFunc("GET /{$}", func(w http.ResponseWriter, r *http.Request) {
		var body bytes.Buffer
		var err error
		if e.cfg.node != nil {
			doc, _ := e.nodeDoc(*e.cfg.node)
			err = render(&body, doc)
		} else {
			err = renderIndex(&body)
		}
		if err != nil {
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		w.Write(body.Bytes())
	})
	mux.HandleFunc("GET /network.json", func(w http.ResponseWriter, r *http.Request) {
		h, s := e.head()
		var doc NetworkDoc
		if s != nil {
			doc = s.network
		}
		doc.Head = h
		writeJSON(w, http.StatusOK, doc)
	})
	mux.HandleFunc("GET /orchestrators.json", func(w http.ResponseWriter, r *http.Request) {
		h, s := e.head()
		doc := OrchestratorsDoc{Head: h}
		if s != nil {
			doc.Orchestrators = &s.orchestrators
		}
		writeJSON(w, http.StatusOK, doc)
	})
	mux.HandleFunc("GET /orchestrators/{file}", func(w http.ResponseWriter, r *http.Request) {
		n, ok := address(r)
		if !ok {
			notFound(w, "not a node's address")
			return
		}
		doc, held := e.nodeDoc(n)
		if !held && (e.cfg.node == nil || *e.cfg.node != n) {
			notFound(w, "no node registered at "+n.Hex())
			return
		}
		writeJSON(w, http.StatusOK, doc)
	})
	mux.HandleFunc("GET /apps.json", func(w http.ResponseWriter, r *http.Request) {
		h, s := e.head()
		doc := AppsDoc{Head: h}
		if s != nil {
			doc.Apps = &s.apps
		}
		writeJSON(w, http.StatusOK, doc)
	})
	mux.HandleFunc("GET /apps/{file}", func(w http.ResponseWriter, r *http.Request) {
		p, ok := address(r)
		if !ok {
			notFound(w, "not a payer's address")
			return
		}
		h, s := e.head()
		if s == nil {
			writeJSON(w, http.StatusOK, AppDoc{Head: h, AppRow: AppRow{Named: e.labels.named(p)}})
			return
		}
		d, ok := s.payers[p]
		if !ok {
			notFound(w, "no payer at "+p.Hex())
			return
		}
		doc := *d
		doc.Head = h
		writeJSON(w, http.StatusOK, doc)
	})
	return mux
}

// labels is the labels file: a scenario's name and, by address, a name
// and whether the account is simulated. It is read again when it changes;
// a file that cannot be read keeps the labels it had.
type labels struct {
	path     string
	mu       sync.Mutex
	mod      time.Time
	size     int64
	scenario *string
	accounts map[common.Address]label
}

type label struct {
	Name      string `json:"name"`
	Simulated bool   `json:"simulated"`
}

func (l *labels) reload() error {
	if l.path == "" {
		return nil
	}
	info, err := os.Stat(l.path)
	if err != nil {
		return err
	}
	l.mu.Lock()
	same := info.ModTime().Equal(l.mod) && info.Size() == l.size
	l.mu.Unlock()
	if same {
		return nil
	}
	raw, err := os.ReadFile(l.path)
	if err != nil {
		return err
	}
	var f struct {
		Scenario *string          `json:"scenario"`
		Accounts map[string]label `json:"accounts"`
	}
	if err := json.Unmarshal(raw, &f); err != nil {
		return err
	}
	accounts := map[common.Address]label{}
	for a, v := range f.Accounts {
		if !common.IsHexAddress(a) {
			return errors.New(a + " is not an address")
		}
		accounts[common.HexToAddress(a)] = v
	}
	l.mu.Lock()
	l.mod, l.size, l.scenario, l.accounts = info.ModTime(), info.Size(), f.Scenario, accounts
	l.mu.Unlock()
	return nil
}

func (l *labels) named(a common.Address) Named {
	l.mu.Lock()
	defer l.mu.Unlock()
	out := Named{Address: a}
	if v, ok := l.accounts[a]; ok {
		name := v.Name
		out.Label, out.Simulated = &name, v.Simulated
	}
	return out
}
