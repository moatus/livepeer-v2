package index

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"math/big"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/common/hexutil"
	"github.com/ethereum/go-ethereum/core/types"
	"github.com/ethereum/go-ethereum/crypto"
	"github.com/ethereum/go-ethereum/rpc"
)

var (
	registryAt = common.HexToAddress("0xc1")
	clockAt    = common.HexToAddress("0xc2")
	brokerAt   = common.HexToAddress("0xc3")
	paramsAt   = common.HexToAddress("0xc4")
	claimsAt   = common.HexToAddress("0xc6")
	firstExec  = common.HexToAddress("0xe1")
	secondExec = common.HexToAddress("0xe2")
	nodeA      = common.HexToAddress("0xa1")
	nodeB      = common.HexToAddress("0xa2")
	payerP     = common.HexToAddress("0xb1")
	controller = common.HexToAddress("0xc0")
	contracts  = Contracts{Registry: registryAt, Broker: brokerAt, Claims: claimsAt, Clock: clockAt, Parameters: paramsAt}
)

// clock is a test's clock: sleeping moves it on at once.
type clock struct {
	mu    sync.Mutex
	now   time.Time
	slept []time.Duration
}

func (c *clock) Now() time.Time {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.now
}

func (c *clock) Sleep(_ context.Context, d time.Duration) error {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.now = c.now.Add(d)
	c.slept = append(c.slept, d)
	return nil
}

// rpcError is an error answer, as a provider gives one to a request.
type rpcError struct {
	code int
	msg  string
}

func (e rpcError) Error() string  { return e.msg }
func (e rpcError) ErrorCode() int { return e.code }

// fakeChain is a provider: blocks 0 to head, each with a hash, and logs.
// Blocks from fork on are on the branch named branch; changing branch
// reorganises them. maxRange refuses a wider eth_getLogs as too large;
// limited answers that many batches with HTTP 429; status answers every
// batch with that HTTP status.
type fakeChain struct {
	mu       sync.Mutex
	clock    *clock
	head     uint64
	fork     uint64
	branch   string
	logs     map[string][]types.Log // by branch; "" is the blocks before fork
	maxRange uint64
	limited  int
	status   int            // the HTTP status of every batch that asks for logs
	executor common.Address // the deployment's
	sent     []time.Time    // when each request element arrived
	asked    [][2]uint64    // each eth_getLogs range
	during   func()         // runs after each eth_getLogs is answered
}

func newChain(c *clock) *fakeChain {
	return &fakeChain{clock: c, head: 1000, fork: 1 << 62, logs: map[string][]types.Log{}, executor: firstExec}
}

func (f *fakeChain) hash(n uint64) common.Hash {
	if n >= f.fork {
		return crypto.Keccak256Hash([]byte(fmt.Sprintf("%s/%d", f.branch, n)))
	}
	return crypto.Keccak256Hash([]byte(fmt.Sprintf("/%d", n)))
}

// add puts a log at its block, on the branch the block is on now.
func (f *fakeChain) add(l types.Log) {
	b := ""
	if l.BlockNumber >= f.fork {
		b = f.branch
	}
	l.TxHash = crypto.Keccak256Hash([]byte(fmt.Sprintf("tx %s %d %d", b, l.BlockNumber, len(f.logs[b]))))
	f.logs[b] = append(f.logs[b], l)
}

func (f *fakeChain) BatchCallContext(_ context.Context, b []rpc.BatchElem) error {
	f.mu.Lock()
	for range b {
		f.sent = append(f.sent, f.clock.Now())
	}
	if f.limited > 0 {
		f.limited--
		f.mu.Unlock()
		return rpc.HTTPError{StatusCode: 429, Status: "429 Too Many Requests"}
	}
	if f.status != 0 && slices.ContainsFunc(b, func(e rpc.BatchElem) bool { return e.Method == "eth_getLogs" }) {
		f.mu.Unlock()
		return rpc.HTTPError{StatusCode: f.status, Status: fmt.Sprint(f.status)}
	}
	var after func()
	for i := range b {
		v, err := f.answer(b[i].Method, b[i].Args)
		if b[i].Method == "eth_getLogs" {
			after = f.during
		}
		if err != nil {
			b[i].Error = err
			continue
		}
		raw, _ := json.Marshal(v)
		if err := json.Unmarshal(raw, b[i].Result); err != nil {
			panic(err)
		}
	}
	f.mu.Unlock()
	if after != nil {
		after()
	}
	return nil
}

func (f *fakeChain) answer(method string, args []any) (any, error) {
	switch method {
	case "eth_getBlockByNumber":
		n := f.head
		if args[0] != "latest" {
			n, _ = hexutil.DecodeUint64(args[0].(string))
		}
		if n > f.head {
			return nil, nil
		}
		return map[string]any{"number": hexutil.Uint64(n), "hash": f.hash(n), "timestamp": hexutil.Uint64(1_795_000_000 + n)}, nil
	case "eth_getLogs":
		q := args[0].(map[string]any)
		from, _ := hexutil.DecodeUint64(q["fromBlock"].(string))
		to, _ := hexutil.DecodeUint64(q["toBlock"].(string))
		f.asked = append(f.asked, [2]uint64{from, to})
		if f.maxRange > 0 && to-from+1 > f.maxRange {
			return nil, rpcError{-32005, fmt.Sprintf("block range too large: %d blocks, the most is %d", to-from+1, f.maxRange)}
		}
		want := q["address"].([]common.Address)
		topics := q["topics"].([][]common.Hash)
		var out []types.Log
		for _, branch := range []string{"", f.branch} {
			if branch == "" && f.branch == "" && len(out) > 0 {
				break
			}
			for i, l := range f.logs[branch] {
				l.Index = uint(i) // its place in the block's order, whatever the filter
				visible := l.BlockNumber < f.fork
				if branch != "" {
					visible = l.BlockNumber >= f.fork
				}
				if !visible || l.BlockNumber < from || l.BlockNumber > to || l.BlockNumber > f.head ||
					!slices.Contains(want, l.Address) || !slices.Contains(topics[0], l.Topics[0]) {
					continue
				}
				l.BlockHash = f.hash(l.BlockNumber)
				out = append(out, l)
			}
		}
		slices.SortStableFunc(out, func(a, b types.Log) int { return int(a.BlockNumber) - int(b.BlockNumber) })
		return out, nil
	case "eth_call":
		call := args[0].(map[string]any)
		data := call["data"].(hexutil.Bytes)
		in, _ := ABI.Methods["valuesAt"].Inputs.Unpack(data[4:])
		if call["to"] != paramsAt || in[0].(*big.Int).Int64() != GroupExecutor {
			return nil, errors.New("execution reverted")
		}
		out, _ := ABI.Methods["valuesAt"].Outputs.Pack([]*big.Int{new(big.Int).SetBytes(f.executor.Bytes())})
		return hexutil.Bytes(out), nil
	}
	return nil, fmt.Errorf("the fake does not answer %s", method)
}

// event is a log of the named event at block b, emitted by at.
func event(at common.Address, name string, b uint64, args ...any) types.Log {
	ev := ABI.Events[name]
	var topics []common.Hash
	topics = append(topics, ev.ID)
	var data []any
	for i, in := range ev.Inputs {
		if in.Indexed {
			switch v := args[i].(type) {
			case common.Address:
				topics = append(topics, common.BytesToHash(v.Bytes()))
			case *big.Int:
				topics = append(topics, common.BigToHash(v))
			}
			continue
		}
		data = append(data, args[i])
	}
	packed, err := ev.Inputs.NonIndexed().Pack(data...)
	if err != nil {
		panic(err)
	}
	return types.Log{Address: at, BlockNumber: b, Topics: topics, Data: packed}
}

func n(v int64) *big.Int { return big.NewInt(v) }

func registered(node common.Address, b uint64, round int64) types.Log {
	return event(registryAt, "NodeRegistered", b, node, n(round))
}

func funded(payer common.Address, b uint64, deposit int64, node common.Address, earmark int64) types.Log {
	return event(brokerAt, "Funded", b, payer, n(deposit), node, n(earmark))
}

func settled(b uint64, round int64, node, payer common.Address, gross, nodeAmount, network int64) types.Log {
	return event(brokerAt, "FeeSettled", b, n(round), node, payer, [32]byte{byte(b)}, n(gross), n(nodeAmount), n(network), node)
}

func started(b uint64, round int64) types.Log {
	return event(clockAt, "RoundStarted", b, n(round), n(int64(b)), [32]byte{})
}

func announced(b uint64, group int64, values ...*big.Int) types.Log {
	return event(paramsAt, "Announced", b, n(group), values, n(9))
}

func bought(at common.Address, b uint64, round, usdcIn int64) types.Log {
	return event(at, "Bought", b, n(round), n(0), n(usdcIn), n(2*usdcIn), n(2*usdcIn), n(0), n(0))
}

func open(t *testing.T, dir string, pageBlocks uint64) *Index {
	t.Helper()
	ix, err := Open(dir, 100, pageBlocks)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(ix.Close)
	if !ix.Begun() {
		if err := ix.Begin(Header{ChainID: 1337, Controller: controller, FirstBlock: 100}); err != nil {
			t.Fatal(err)
		}
	}
	return ix
}

func source(f *fakeChain, perSecond int) *Source {
	s := NewSource(f, perSecond)
	s.now, s.sleep = f.clock.Now, f.clock.Sleep
	return s
}

// busy is a deployment: two nodes, a payer, round starts, fees, an
// executor's buys and a second executor announced.
func busy(f *fakeChain) {
	for _, l := range []types.Log{registered(nodeA, 120, 1), registered(nodeB, 130, 1), started(150, 2),
		funded(payerP, 160, 5_000_000, common.Address{}, 0), funded(payerP, 161, 0, nodeA, 1_000_000),
		settled(170, 2, nodeA, payerP, 1_000_000, 500_000, 500_000), settled(171, 2, nodeB, payerP, 700_000, 350_000, 350_000),
		bought(firstExec, 400, 2, 10), started(450, 3), announced(500, GroupExecutor, new(big.Int).SetBytes(secondExec.Bytes())),
		bought(secondExec, 500, 3, 20), bought(secondExec, 900, 4, 30), settled(950, 4, nodeA, payerP, 1_000_001, 500_000, 500_001)} {
		f.add(l)
	}
}

func catchUp(t *testing.T, ix *Index, src *Source, head uint64) {
	t.Helper()
	if err := ix.CatchUp(context.Background(), src, contracts, head, nil); err != nil {
		t.Fatal(err)
	}
}

// same says whether two totals hold the same figures.
func same(a, b *Totals) bool {
	ja, _ := json.Marshal(a)
	jb, _ := json.Marshal(b)
	return string(ja) == string(jb)
}

// fresh is the totals of an index read from the first block, in a new
// folder, of what f holds now.
func fresh(t *testing.T, f *fakeChain) *Totals {
	ix := open(t, t.TempDir(), 5000)
	catchUp(t, ix, source(f, 1000), f.head)
	return ix.Totals()
}

// The index reads from the first block in pages, commits each with its
// checkpoint, and a reader opening the file again resumes after the last.
func TestIndexReadsPagesAndResumes(t *testing.T) {
	f := newChain(&clock{})
	busy(f)
	dir := t.TempDir()
	ix := open(t, dir, 300)
	catchUp(t, ix, source(f, 1000), 600)
	// The page that announces the second executor is read again with it.
	if !slices.Equal(f.asked, [][2]uint64{{100, 399}, {400, 600}, {400, 600}}) {
		t.Errorf("pages %v", f.asked)
	}
	if through, h, ok := ix.Through(); !ok || through != 600 || h != f.hash(600) {
		t.Errorf("checkpoint %d %s", through, h)
	}
	ix.Close()

	ix = open(t, dir, 300)
	if from := ix.ResumedFrom(); from == nil || *from != 601 {
		t.Fatalf("resumed from %v", from)
	}
	f.asked = nil
	catchUp(t, ix, source(f, 1000), 1000)
	if !slices.Equal(f.asked, [][2]uint64{{601, 900}, {901, 1000}}) {
		t.Errorf("after the restart, pages %v", f.asked)
	}
	if !same(ix.Totals(), fresh(t, f)) {
		t.Errorf("the resumed index's totals differ from a fresh index's")
	}
}

// The sums: a ticket paid below its face counts its gross; a split with
// an odd micro-unit keeps the recorded node and network amounts; funding
// with an earmark counts deposit and earmark.
func TestIndexSums(t *testing.T) {
	f := newChain(&clock{})
	busy(f)
	t1 := fresh(t, f)
	p := t1.PayerOf[payerP]
	if p.Funded.Int64() != 6_000_000 || p.Released.Int64() != 0 {
		t.Errorf("funded %s released %s", p.Funded, p.Released)
	}
	// nodeB's ticket paid 700,000, below a face of 1,000,000: its gross.
	if g := t1.Fees[2][nodeB].Gross.Int64(); g != 700_000 {
		t.Errorf("node B's fees in round 2: %d", g)
	}
	if a := p.ByNode[nodeA]; a.Gross.Int64() != 2_000_001 || a.Node.Int64() != 1_000_000 || a.Network.Int64() != 1_000_001 {
		t.Errorf("payer to node A: %+v", a)
	}
	if !slices.Equal(t1.Nodes, []common.Address{nodeA, nodeB}) || t1.Starts[3] != 450 || len(t1.Buys) != 3 {
		t.Errorf("nodes %v starts %v buys %d", t1.Nodes, t1.Starts, len(t1.Buys))
	}
}

// The second executor is announced in the page that holds its first buy:
// the page is read again with it before it is committed, and every buy is
// counted once, whether the index read the deployment at once or
// stopped between the announcement and the later buy.
func TestIndexFindsAnAnnouncedExecutor(t *testing.T) {
	f := newChain(&clock{})
	busy(f)
	ix := open(t, t.TempDir(), 1000)
	catchUp(t, ix, source(f, 1000), 1000)
	if !slices.Equal(f.asked, [][2]uint64{{100, 1000}, {100, 1000}}) {
		t.Errorf("pages %v: the page announcing an executor is read again", f.asked)
	}
	var usdc []int64
	for _, b := range ix.Totals().Buys {
		usdc = append(usdc, b.USDCIn.Int64())
	}
	if !slices.Equal(usdc, []int64{10, 20, 30}) {
		t.Errorf("buys %v", usdc)
	}
	dir := t.TempDir()
	ix = open(t, dir, 500)
	catchUp(t, ix, source(f, 1000), 600)
	ix.Close()
	ix = open(t, dir, 500)
	catchUp(t, ix, source(f, 1000), 1000)
	if !same(ix.Totals(), fresh(t, f)) || !slices.Contains(ix.Watched(), secondExec) {
		t.Errorf("restarted after the announcement: watched %v, totals differ from a fresh index's", ix.Watched())
	}
}

// No second in which more than perSecond request elements are sent.
func TestSourceKeepsItsRate(t *testing.T) {
	c := &clock{now: time.Unix(1_795_000_000, 0)}
	f := newChain(c)
	busy(f)
	ix := open(t, t.TempDir(), 10)
	catchUp(t, ix, source(f, 5), 1000)
	if through, _, _ := ix.Through(); through != 1000 || len(f.sent) < 5*91 {
		t.Fatalf("reached block %d, %d elements sent", through, len(f.sent))
	}
	for i := range f.sent {
		j := i
		for j < len(f.sent) && f.sent[j].Sub(f.sent[i]) < time.Second {
			j++
		}
		if j-i > 5 {
			t.Fatalf("%d elements sent within one second from %s", j-i, f.sent[i])
		}
	}
}

// 429s are waited out, doubling from 1 s, or for the provider's
// Retry-After, and the index reaches the newest block.
func TestSourceWaitsOut429s(t *testing.T) {
	c := &clock{now: time.Unix(1_795_000_000, 0)}
	f := newChain(c)
	busy(f)
	f.limited = 4
	ix := open(t, t.TempDir(), 100)
	catchUp(t, ix, source(f, 1000), 1000)
	if through, _, _ := ix.Through(); through != 1000 {
		t.Fatalf("reached block %d", through)
	}
	if !slices.Equal(c.slept, []time.Duration{time.Second, 2 * time.Second, 4 * time.Second, 8 * time.Second}) {
		t.Errorf("waits after four 429s: %v", c.slept)
	}
	if !same(ix.Totals(), fresh(t, f)) {
		t.Errorf("totals differ from a fresh index's")
	}
	f.limited, c.slept, f.head = 1, nil, 1001
	src := source(f, 1000)
	src.Retry = func() time.Duration { return 7 * time.Second }
	catchUp(t, ix, src, 1001)
	if !slices.Equal(c.slept, []time.Duration{7 * time.Second}) {
		t.Errorf("waits %v, want the provider's 7 s", c.slept)
	}
}

// stalled is a provider that never answers.
type stalled struct{}

func (stalled) BatchCallContext(ctx context.Context, _ []rpc.BatchElem) error {
	<-ctx.Done()
	return ctx.Err()
}

// A batch the provider does not answer fails at the timeout, with its
// reason on each request, and is told to Seen.
func TestSourceGivesUpOnABatchNotAnswered(t *testing.T) {
	src := NewSource(stalled{}, 5)
	src.Timeout = 20 * time.Millisecond
	var seen error
	src.Seen = func(_ string, err error) { seen = err }
	reqs := []rpc.BatchElem{{Method: "eth_blockNumber"}, {Method: "eth_chainId"}}
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	src.Send(ctx, "the newest block", reqs)
	if ctx.Err() != nil {
		t.Fatal("the batch was not given up at its timeout")
	}
	for _, r := range reqs {
		if !errors.Is(r.Error, context.DeadlineExceeded) {
			t.Errorf("%s: %v", r.Method, r.Error)
		}
	}
	if !errors.Is(seen, context.DeadlineExceeded) {
		t.Errorf("told %v", seen)
	}
}

// A provider that refuses ranges over 1,000 blocks halves the page, and the
// index still reaches the newest block; an authentication error leaves the
// page size as it was.
func TestIndexHalvesOnlyForARangeTooLarge(t *testing.T) {
	f := newChain(&clock{})
	busy(f)
	f.status = 401
	ix := open(t, t.TempDir(), 5000)
	err := ix.CatchUp(context.Background(), source(f, 1000), contracts, 1000, nil)
	var h rpc.HTTPError
	if !errors.As(err, &h) || h.StatusCode != 401 || ix.PageBlocks != 5000 || !slices.Equal(f.asked, nil) {
		t.Fatalf("an authentication error: %v, page %d", err, ix.PageBlocks)
	}
	f.status, f.maxRange, f.head = 0, 1000, 9000
	catchUp(t, ix, source(f, 1000), 9000)
	if through, _, _ := ix.Through(); through != 9000 || ix.PageBlocks != 625 {
		t.Errorf("reached %d with pages of %d", through, ix.PageBlocks)
	}
	if !same(ix.Totals(), fresh(t, f)) {
		t.Errorf("totals differ from a fresh index's")
	}
}

// A changed checkpoint hash makes the index read again from the first
// block and end with figures equal to a fresh index's, across a round's
// start and an executor's announcement on the branch left.
func TestIndexRereadsWhenItsCheckpointLeavesTheChain(t *testing.T) {
	f := newChain(&clock{})
	busy(f)
	ix := open(t, t.TempDir(), 1000)
	catchUp(t, ix, source(f, 1000), 1000)
	// Blocks from 440 are replaced: no round 3 start at 450 and no
	// announcement at 500; round 3 starts at 460 instead, and a buy of the
	// first executor.
	f.fork, f.branch = 440, "other"
	f.add(started(460, 3))
	f.add(bought(firstExec, 470, 3, 5))
	f.head = 1100
	catchUp(t, ix, source(f, 1000), 1100)
	if ix.Rollbacks() != 1 {
		t.Errorf("rollbacks %d", ix.Rollbacks())
	}
	want := fresh(t, f)
	if !same(ix.Totals(), want) {
		t.Errorf("after the rollback the totals differ from a fresh index's")
	}
	if ix.Totals().Starts[3] != 460 || slices.Contains(ix.Watched(), secondExec) {
		t.Errorf("starts %v watched %v", ix.Totals().Starts, ix.Watched())
	}
	raw, _ := os.ReadFile(filepath.Join(ix.path))
	if strings.Contains(string(raw), `"block":500`) {
		t.Errorf("an event of the branch left stayed in the file")
	}
}

// A checkpoint replaced by a branch of the same length, or of a shorter
// one, is found at the next catch-up with nothing new to read: the index
// reads again from the first block and ends equal to a fresh index, its
// checkpoint, totals and executors watched.
func TestIndexRereadsWhenItsCheckpointIsReplacedAtItsHeight(t *testing.T) {
	for _, head := range []uint64{1000, 900} {
		f := newChain(&clock{})
		busy(f)
		ix := open(t, t.TempDir(), 1000)
		catchUp(t, ix, source(f, 1000), 1000)
		f.fork, f.branch, f.head = 440, "other", head
		f.add(started(460, 3))
		f.add(bought(firstExec, 470, 3, 5))
		catchUp(t, ix, source(f, 1000), head)
		want := open(t, t.TempDir(), 5000)
		catchUp(t, want, source(f, 1000), head)
		through, h, _ := ix.Through()
		if ix.Rollbacks() != 1 || through != head || h != f.hash(head) || !same(ix.Totals(), want.Totals()) ||
			!slices.Equal(ix.Watched(), want.Watched()) {
			t.Errorf("head %d: rollbacks %d, checkpoint %d %s, watched %v and %v; totals equal a fresh index's: %v",
				head, ix.Rollbacks(), through, h, ix.Watched(), want.Watched(), same(ix.Totals(), want.Totals()))
		}
	}
}

// An executor announced in a page the chain changed under is not watched:
// it joins the index only with the page that announces it.
func TestIndexForgetsAnExecutorOfAPageDropped(t *testing.T) {
	f := newChain(&clock{})
	busy(f)
	ix := open(t, t.TempDir(), 2000)
	reorganised := false
	f.during = func() {
		if !reorganised {
			reorganised = true
			f.mu.Lock()
			f.fork, f.branch = 480, "other" // no announcement at 500 on this branch
			f.mu.Unlock()
		}
	}
	catchUp(t, ix, source(f, 1000), 1000)
	want := fresh(t, f)
	if slices.Contains(ix.Watched(), secondExec) || !same(ix.Totals(), want) {
		t.Errorf("watched %v after the page announcing %s was dropped", ix.Watched(), secondExec)
	}
}

// A page whose last block's hash changes while its logs are read is not
// written: it is read again, and no event of the branch left is kept.
func TestIndexDropsAPageTheChainChangedUnder(t *testing.T) {
	f := newChain(&clock{})
	busy(f)
	ix := open(t, t.TempDir(), 2000)
	reorganised := false
	f.during = func() {
		if !reorganised {
			reorganised = true
			f.mu.Lock()
			f.fork, f.branch = 700, "other"
			f.mu.Unlock()
		}
	}
	catchUp(t, ix, source(f, 1000), 1000)
	// Read, read again for the executor it announces, dropped; then read
	// afresh, announcement and all, since a dropped page leaves nothing.
	if !slices.Equal(f.asked, [][2]uint64{{100, 1000}, {100, 1000}, {100, 1000}, {100, 1000}}) {
		t.Errorf("pages %v", f.asked)
	}
	for _, b := range ix.Totals().Buys {
		if b.Block == 900 {
			t.Errorf("the buy at block 900, of the branch left, was kept")
		}
	}
	if _, h, _ := ix.Through(); h != f.hash(1000) {
		t.Errorf("checkpoint hash %s is not the chain's", h)
	}
}

// A last line cut short by a kill is dropped, and the page before it kept.
func TestIndexDropsACutOffLastLine(t *testing.T) {
	f := newChain(&clock{})
	busy(f)
	dir := t.TempDir()
	ix := open(t, dir, 450)
	catchUp(t, ix, source(f, 1000), 549)
	ix.Close()
	path := filepath.Join(dir, FileName)
	whole, _ := os.ReadFile(path)
	cut := append(slices.Clone(whole), []byte(`{"block":600,"tx":"0x12`)...)
	os.WriteFile(path, cut, 0o644)
	ix = open(t, dir, 450)
	if from := ix.ResumedFrom(); from == nil || *from != 550 {
		t.Fatalf("resumed from %v", from)
	}
	if got, _ := os.ReadFile(path); string(got) != string(whole) {
		t.Errorf("the cut-off line was not dropped")
	}
	// An event with no checkpoint after it is dropped too.
	ix.Close()
	page := regexp.MustCompile(`(?m)^\{"through":549.*\n`).ReplaceAll(whole, nil)
	os.WriteFile(path, page, 0o644)
	ix = open(t, dir, 450)
	if from := ix.ResumedFrom(); from != nil {
		t.Errorf("without its checkpoint, the last page was kept: resumed from %d", *from)
	}
	if got, _ := os.ReadFile(path); strings.Count(string(got), "\n") != 1 {
		t.Errorf("the page with no checkpoint was not dropped:\n%s", got)
	}
}

// A file that names another Controller, chain or first block is refused,
// and its events stay as they were.
func TestIndexRefusesAnotherDeployment(t *testing.T) {
	f := newChain(&clock{})
	busy(f)
	dir := t.TempDir()
	ix := open(t, dir, 5000)
	catchUp(t, ix, source(f, 1000), 1000)
	ix.Close()
	before, _ := os.ReadFile(filepath.Join(dir, FileName))
	ix, err := Open(dir, 100, 5000)
	if err != nil {
		t.Fatal(err)
	}
	other := common.HexToAddress("0xc0ffee")
	if err := ix.Check(other, 100); !errors.Is(err, ErrDeployment) || !strings.Contains(err.Error(), other.Hex()) ||
		!strings.Contains(err.Error(), controller.Hex()) {
		t.Errorf("another Controller: %v", err)
	}
	if err := ix.Begin(Header{ChainID: 1, Controller: controller, FirstBlock: 100}); !errors.Is(err, ErrDeployment) {
		t.Errorf("another chain: %v", err)
	}
	if err := ix.Check(controller, 101); !errors.Is(err, ErrDeployment) || !strings.Contains(err.Error(), "from block 100") ||
		!strings.Contains(err.Error(), "from block 101") {
		t.Errorf("another first block: %v", err)
	}
	if err := ix.Begin(Header{ChainID: 1337, Controller: controller, FirstBlock: 101}); !errors.Is(err, ErrDeployment) {
		t.Errorf("another first block, at Begin: %v", err)
	}
	if err := ix.Check(controller, 100); err != nil {
		t.Errorf("its own deployment: %v", err)
	}
	if _, err := Open(dir, 100, 5000); err == nil || !strings.Contains(err.Error(), "another reader") {
		t.Errorf("a second reader of the folder: %v", err)
	}
	ix.Close()
	if after, _ := os.ReadFile(filepath.Join(dir, FileName)); string(after) != string(before) {
		t.Errorf("the refused file changed")
	}
}

// A file in another format is refused, and stays as it was.
func TestIndexRefusesAnotherFormat(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, FileName)
	other := []byte(`{"format":2,"chain_id":1337,"controller":"0x00000000000000000000000000000000000000c0","first_block":100}` + "\n" +
		`{"through":200,"hash":"0x01"}` + "\n")
	os.WriteFile(path, other, 0o644)
	if _, err := Open(dir, 100, 5000); !errors.Is(err, ErrFormat) || !strings.Contains(err.Error(), "format 2") {
		t.Errorf("format 2: %v", err)
	}
	if after, _ := os.ReadFile(path); string(after) != string(other) {
		t.Errorf("the refused file changed")
	}
}

// Every event the index knows, and the view it reads, is declared, with the
// same argument types, in contracts/interfaces.
func TestEventsAreDeclared(t *testing.T) {
	files, _ := filepath.Glob("../../contracts/interfaces/*.sol")
	var src strings.Builder
	for _, f := range files {
		b, err := os.ReadFile(f)
		if err != nil {
			t.Fatal(err)
		}
		src.Write(b)
	}
	if len(files) == 0 {
		t.Fatal("no interfaces found")
	}
	declared := regexp.MustCompile(`(?s)(function|event) (\w+)\(([^)]*)\)`)
	found := map[string]bool{}
	for _, m := range declared.FindAllStringSubmatch(src.String(), -1) {
		var types []string
		for _, p := range strings.Split(m[3], ",") {
			if p = strings.TrimSpace(p); p != "" {
				types = append(types, strings.Fields(p)[0])
			}
		}
		found[m[2]+"("+strings.Join(types, ",")+")"] = true
	}
	for _, e := range ABI.Events {
		if !found[e.Sig] {
			t.Errorf("event %s is not declared in contracts/interfaces", e.Sig)
		}
	}
	for _, m := range ABI.Methods {
		if !found[m.Sig] {
			t.Errorf("%s is not declared in contracts/interfaces", m.Sig)
		}
	}
}

// BenchmarkReadADayBack writes a day of a testnet's events at the scale
// it is sized for (100 nodes, each paid once and claiming once a round,
// 144 rounds, a round start and a buy each round, a checkpoint every 5,000
// blocks at four blocks a second) and opens it, as a start does. It
// reports the file's size per event and the time to read it back.
//
//	go test -run '^$' -bench ReadADayBack ./index
func BenchmarkReadADayBack(b *testing.B) {
	dir := b.TempDir()
	ix, err := Open(dir, 0, 5000)
	if err != nil {
		b.Fatal(err)
	}
	if err := ix.Begin(Header{ChainID: 421614, Controller: controller, FirstBlock: 0}); err != nil {
		b.Fatal(err)
	}
	const rounds, nodes, blocksPerRound = 144, 100, 4 * 600
	paid, _ := new(big.Int).SetString("25549751601781267146", 10)
	var page []Event
	events, through := 0, uint64(0)
	flush := func(to uint64) {
		if err := ix.commit(page, to, crypto.Keccak256Hash([]byte(fmt.Sprint(to)))); err != nil {
			b.Fatal(err)
		}
		events += len(page)
		page, through = nil, to
	}
	add := func(l types.Log) {
		l.TxHash = crypto.Keccak256Hash([]byte(fmt.Sprint(l.BlockNumber, len(page))))
		l.Index = uint(len(page))
		e, err := decode(l)
		if err != nil {
			b.Fatal(err)
		}
		page = append(page, e)
	}
	for r := range int64(rounds) {
		start := uint64(r) * blocksPerRound
		add(started(start, r+1))
		for i := range int64(nodes) {
			node := common.BigToAddress(big.NewInt(0xa000 + i))
			add(settled(start+uint64(i)*20+1, r+1, node, payerP, 1_000_000, 500_000, 500_000))
			add(event(claimsAt, "Claimed", start+uint64(i)*20+2, node, n(r+1), node, n(1_000_000), paid, n(0), paid))
		}
		add(bought(firstExec, start+2300, r+1, 1_000_000))
		if end := start + blocksPerRound - 1; end/5000 != through/5000 || r == rounds-1 {
			flush(end)
		}
	}
	ix.Close()
	info, _ := os.Stat(filepath.Join(dir, FileName))
	b.ResetTimer()
	for range b.N {
		again, err := Open(dir, 0, 5000)
		if err != nil {
			b.Fatal(err)
		}
		if len(again.Totals().Nodes) != 0 || len(again.Totals().Buys) != rounds {
			b.Fatalf("read back %d buys", len(again.Totals().Buys))
		}
		again.Close()
	}
	b.ReportMetric(float64(info.Size())/float64(events), "bytes/event")
	b.ReportMetric(float64(events), "events/day")
	b.ReportMetric(float64(info.Size())/1e6, "MB/day")
}
