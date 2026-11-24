package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/url"
	"os"
	"slices"
	"strings"
	"sync"
	"time"

	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/common/hexutil"
	"github.com/ethereum/go-ethereum/crypto"
	"github.com/ethereum/go-ethereum/rpc"

	"lp2core/programs/chain"
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
	now   func() time.Time // for the age of a block a page shows
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
}

func newExplorer(cfg config, src *index.Source, ix *index.Index) *explorer {
	e := &explorer{cfg: cfg, src: src, ix: ix, every: cycleEvery, now: time.Now, stop: func(err error) { fail(err) },
		kept: map[string][]any{}, times: map[uint64]uint64{}, labels: labels{path: cfg.labels}, failed: map[string]string{}}
	e.rpc.Provider = provider(cfg.rpcURL)
	e.progress = Progress{FirstBlock: ix.First(), ResumedFrom: ix.ResumedFrom(), PageBlocks: ix.PageBlocks}
	if through, _, ok := ix.Through(); ok {
		e.progress.Through = &through
	}
	src.Seen = e.seen
	e.readLabels()
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
		why := chain.Describe(err, e.cfg.rpcURL)
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
	e.failed[step] = step + ": " + chain.Describe(err, e.cfg.rpcURL)
}

// run starts a cycle every e.every, counted from the start of the last
// (one that takes longer is followed by the next at once), until ctx ends.
// So a provider that starts failing is asked again within one cycle, and
// the banner shows within a cycle and a request's timeout. The labels file
// is read every e.every on its own, so a change to it shows within a cycle
// whatever the provider does.
func (e *explorer) run(ctx context.Context) {
	done := make(chan struct{})
	go func() {
		defer close(done)
		for {
			select {
			case <-ctx.Done():
				return
			case <-time.After(e.every):
				e.readLabels()
			}
		}
	}()
	defer func() { <-done }()
	for {
		start := time.Now()
		e.cycle(ctx)
		select {
		case <-ctx.Done():
			return
		case <-time.After(e.every - time.Since(start)):
		}
	}
}

// readLabels reads the labels file, takes it again if it changed, and publishes
// whether it could.
func (e *explorer) readLabels() {
	e.note(stepLabels, e.labels.reload())
}

// cycle reads the newest block, commits the index's pages up to it,
// publishing its progress after each, then reads the snapshot at the last
// checkpoint and publishes it once all of it is read.
func (e *explorer) cycle(ctx context.Context) {
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

// served is what one response shows, taken at once: the Head every document
// starts with, the snapshot its figures come from (nil before the first),
// and the labels that name its accounts. A response takes one, so
// every figure on it is of the block its Head names.
type served struct {
	Head
	snap  *snapshot
	names *labelSet
}

// served is what was last published: the snapshot's block and
// reasons, with each step failing now, and the labels read last.
func (e *explorer) served() served {
	names := e.labels.now()
	e.mu.Lock()
	defer e.mu.Unlock()
	h := Head{RPC: e.rpc, Index: e.progress, Scenario: names.scenario, Unavailable: []string{}}
	for _, s := range steps {
		if why, ok := e.failed[s]; ok {
			h.Unavailable = append(h.Unavailable, why)
		}
	}
	s := e.snap
	if s == nil {
		if len(h.Unavailable) == 0 {
			why := "every figure: " + notRead
			if ix := h.Index; ix.Newest != nil && !ix.CaughtUp {
				why = "every figure: the index is catching up"
			}
			h.Unavailable = append(h.Unavailable, why)
		}
		return served{Head: h, names: names}
	}
	block, t := s.block, s.time
	h.Block, h.BlockTime = &block, &t
	h.Unavailable = append(h.Unavailable, s.unavailable...)
	return served{Head: h, snap: s, names: names}
}

// The documents of what is served, each account named by its labels. A document
// copies what it names: the snapshot is shared and never changed.

func (v served) networkDoc() NetworkDoc {
	var doc NetworkDoc
	if v.snap != nil {
		doc = v.snap.network
		if d := doc.Validators; d != nil {
			named := all(v.names, *d, func(n *Named) *Named { return n })
			doc.Validators = &named
		}
	}
	doc.Head = v.Head
	return doc
}

func (v served) orchestratorsDoc() OrchestratorsDoc {
	doc := OrchestratorsDoc{Head: v.Head}
	if v.snap != nil {
		rows := all(v.names, v.snap.orchestrators, func(r *NodeRow) *Named { return &r.Named })
		doc.Orchestrators = &rows
	}
	return doc
}

// nodeDoc is the node's document, with every figure null before the first
// snapshot or for a node it does not hold; known is false for a node the
// snapshot does not hold.
func (v served) nodeDoc(n common.Address) (doc *NodeDoc, known bool) {
	doc = &NodeDoc{Node: Node{Address: n, LowGasWei: newInt(lowGasWei)}}
	if v.snap != nil {
		d, ok := v.snap.nodes[n]
		if ok {
			c := *d
			doc = &c
			if p := doc.FeesByPayer; p != nil {
				named := all(v.names, *p, func(f *NamedFlow) *Named { return &f.Named })
				doc.FeesByPayer = &named
			}
		}
		known = ok
	} else {
		known = true
	}
	named := v.names.named(n)
	doc.Head, doc.Label, doc.Simulated = v.Head, named.Label, named.Simulated
	return doc, known
}

func (v served) appsDoc() AppsDoc {
	doc := AppsDoc{Head: v.Head}
	if v.snap != nil {
		rows := all(v.names, v.snap.apps, func(r *AppRow) *Named { return &r.Named })
		doc.Apps = &rows
	}
	return doc
}

// appDoc is the payer's document, with every figure null before the first
// snapshot; known is false for a payer the snapshot does not hold.
func (v served) appDoc(p common.Address) (doc *AppDoc, known bool) {
	doc = &AppDoc{}
	if v.snap != nil {
		d, ok := v.snap.payers[p]
		if !ok {
			return nil, false
		}
		c := *d
		doc = &c
		doc.ByNode = all(v.names, doc.ByNode, func(f *NamedFlow) *Named { return &f.Named })
	}
	doc.Head, doc.Named = v.Head, v.names.named(p)
	return doc, true
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
	// address is the address a path names, and whether it asks for JSON.
	address := func(r *http.Request) (common.Address, bool, bool) {
		name, json := strings.CutSuffix(r.PathValue("file"), ".json")
		if !common.IsHexAddress(name) {
			return common.Address{}, json, false
		}
		return common.HexToAddress(name), json, true
	}
	notFound := func(w http.ResponseWriter, what string) {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": what})
	}
	mux.HandleFunc("GET /style.css", serveStylesheet)
	mux.HandleFunc("GET /{$}", func(w http.ResponseWriter, r *http.Request) {
		if e.cfg.node != nil {
			e.nodePage(w, *e.cfg.node, true)
			return
		}
		e.networkPage(w)
	})
	mux.HandleFunc("GET /network", func(w http.ResponseWriter, r *http.Request) { e.networkPage(w) })
	mux.HandleFunc("GET /orchestrators", func(w http.ResponseWriter, r *http.Request) { e.orchestratorsPage(w) })
	mux.HandleFunc("GET /apps", func(w http.ResponseWriter, r *http.Request) { e.appsPage(w) })
	mux.HandleFunc("GET /network.json", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, http.StatusOK, e.served().networkDoc())
	})
	mux.HandleFunc("GET /orchestrators.json", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, http.StatusOK, e.served().orchestratorsDoc())
	})
	mux.HandleFunc("GET /orchestrators/{file}", func(w http.ResponseWriter, r *http.Request) {
		n, json, ok := address(r)
		if !json {
			if !ok {
				e.missing(w, e.frame(e.served(), "Not found", "orchestrators", "/orchestrators.json"), "Not a node's address.")
				return
			}
			e.nodePage(w, n, false)
			return
		}
		if !ok {
			notFound(w, "not a node's address")
			return
		}
		doc, known := e.served().nodeDoc(n)
		if !known {
			notFound(w, "no node registered at "+n.Hex())
			return
		}
		writeJSON(w, http.StatusOK, doc)
	})
	mux.HandleFunc("GET /apps.json", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, http.StatusOK, e.served().appsDoc())
	})
	mux.HandleFunc("GET /apps/{file}", func(w http.ResponseWriter, r *http.Request) {
		p, json, ok := address(r)
		if !json {
			if !ok {
				e.missing(w, e.frame(e.served(), "Not found", "apps", "/apps.json"), "Not a payer's address.")
				return
			}
			e.appPage(w, p)
			return
		}
		if !ok {
			notFound(w, "not a payer's address")
			return
		}
		doc, known := e.served().appDoc(p)
		if !known {
			notFound(w, "no payer at "+p.Hex())
			return
		}
		writeJSON(w, http.StatusOK, doc)
	})
	return mux
}

// labels is the labels file: a scenario's name and, by address, a name
// and whether the account is simulated. It is read every cycle and taken
// again when its contents changed; a file that cannot be read keeps the
// labels it had. What it last read is published whole, as one labelSet, and
// never changed after.
type labels struct {
	path string
	raw  []byte // the contents last taken

	mu  sync.Mutex
	set *labelSet
}

// labelSet is one reading of the labels file.
type labelSet struct {
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
	raw, err := os.ReadFile(l.path)
	if err != nil {
		return err
	}
	if l.raw != nil && bytes.Equal(raw, l.raw) {
		return nil
	}
	set, err := decodeLabels(raw)
	if err != nil {
		return err
	}
	l.raw = raw
	l.mu.Lock()
	l.set = set
	l.mu.Unlock()
	return nil
}

// decodeLabels reads the contents of a labels file, or says why they are
// not one. It is the only reading of the format: --check-labels uses it too.
func decodeLabels(raw []byte) (*labelSet, error) {
	var f struct {
		Scenario *string          `json:"scenario"`
		Accounts map[string]label `json:"accounts"`
	}
	if err := json.Unmarshal(raw, &f); err != nil {
		return nil, err
	}
	accounts := map[common.Address]label{}
	for a, v := range f.Accounts {
		if !common.IsHexAddress(a) {
			return nil, errors.New(a + " is not an address")
		}
		accounts[common.HexToAddress(a)] = v
	}
	return &labelSet{scenario: f.Scenario, accounts: accounts}, nil
}

// now is the labels last read; none before the file is.
func (l *labels) now() *labelSet {
	l.mu.Lock()
	defer l.mu.Unlock()
	if l.set == nil {
		return &labelSet{}
	}
	return l.set
}

func (s *labelSet) named(a common.Address) Named {
	out := Named{Address: a}
	if s == nil {
		return out
	}
	if v, ok := s.accounts[a]; ok {
		name := v.Name
		out.Label, out.Simulated = &name, v.Simulated
	}
	return out
}

// all is a copy of rows with the account each names named.
func all[T any](s *labelSet, rows []T, account func(*T) *Named) []T {
	out := slices.Clone(rows)
	for i := range out {
		n := account(&out[i])
		*n = s.named(n.Address)
	}
	return out
}
