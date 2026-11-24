package main

import (
	"context"
	"crypto/ecdsa"
	"errors"
	"fmt"
	"math/big"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	ethereum "github.com/ethereum/go-ethereum"
	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/core/types"
	"github.com/ethereum/go-ethereum/crypto"
)

// --- a chain in memory ------------------------------------------------------------

// fake is a chain with the Registry and the token: blocks of logs, the
// faucet's nonce, a pool of transactions waiting, receipts, balances. A
// transaction is the faucet's signed bytes, decoded as a node would: a
// transfer moves the token when the faucet holds enough and reverts when
// it does not. Mining is by hand (mine) or, with auto, when a receipt is
// asked for.
type fake struct {
	t                     *testing.T
	key                   *ecdsa.PrivateKey
	chainID               *big.Int
	self, registry, token common.Address
	head                  uint64
	logs                  []types.Log
	nonce                 uint64
	pool                  map[uint64]*types.Transaction
	receipts              map[common.Hash]*types.Receipt
	lpt                   map[common.Address]*big.Int
	eth                   *big.Int
	auto                  bool
	maxRange              uint64      // the most blocks one Logs answers, as a hosted endpoint caps it
	ranges                [][3]uint64 // each Logs asked: address kind (0 Registry, 1 token), from, to
	onBroadcast           func(tx *types.Transaction)
	estimateRefused       bool // the chain refuses the gas estimate to an account short of ETH
	broadcasts            []common.Hash
}

const gasPerGrant, feeCap = 60000, 1_000_000_000

func newFake(t *testing.T) *fake {
	key, _ := crypto.GenerateKey()
	c := &fake{t: t, key: key, chainID: big.NewInt(474747), self: crypto.PubkeyToAddress(key.PublicKey),
		registry: common.HexToAddress("0x5FbDB2315678afecb367f032d93F642f64180aa3"),
		token:    common.HexToAddress("0xe7f1725E7734CE288F8367e1Bb143E90bb3F0512"),
		pool:     map[uint64]*types.Transaction{}, receipts: map[common.Hash]*types.Receipt{},
		lpt: map[common.Address]*big.Int{}, eth: big.NewInt(1e18), auto: true, maxRange: 1 << 62}
	c.lpt[c.self] = new(big.Int).Mul(grant, big.NewInt(100))
	return c
}

var grant = new(big.Int).Mul(big.NewInt(1000), big.NewInt(1e18))

func node(i int) common.Address { return common.BigToAddress(big.NewInt(int64(0xA000 + i))) }

// register mines a block in which n registers.
func (c *fake) register(n common.Address) {
	c.head++
	c.logs = append(c.logs, types.Log{Address: c.registry, Topics: []common.Hash{nodeRegistered, common.BytesToHash(n.Bytes())},
		Data: make([]byte, 32), BlockNumber: c.head})
}

// emptyBlocks mines n blocks with nothing in them.
func (c *fake) emptyBlocks(n uint64) { c.head += n }

// transferLog mines a block with a Transfer of amount from `from` to `to`.
func (c *fake) transferLog(from, to common.Address, amount *big.Int) types.Log {
	return types.Log{Address: c.token, Topics: []common.Hash{transferEvent, common.BytesToHash(from.Bytes()),
		common.BytesToHash(to.Bytes())}, Data: common.LeftPadBytes(amount.Bytes(), 32), BlockNumber: c.head}
}

// mine mines each transaction in the pool whose turn it is, one a block.
func (c *fake) mine() {
	for {
		tx, ok := c.pool[c.nonce]
		if !ok {
			return
		}
		delete(c.pool, c.nonce)
		c.nonce++
		c.head++
		r := &types.Receipt{TxHash: tx.Hash(), BlockNumber: new(big.Int).SetUint64(c.head), Status: types.ReceiptStatusFailed}
		data := tx.Data()
		if tx.To() != nil && *tx.To() == c.token && len(data) == 68 && string(data[:4]) == string(transferSel) {
			to, amount := common.BytesToAddress(data[4:36]), new(big.Int).SetBytes(data[36:68])
			if held := c.balance(c.self); held.Cmp(amount) >= 0 {
				c.lpt[c.self] = new(big.Int).Sub(held, amount)
				c.lpt[to] = new(big.Int).Add(c.balance(to), amount)
				l := c.transferLog(c.self, to, amount)
				c.logs = append(c.logs, l)
				r.Logs = []*types.Log{&l}
				r.Status = types.ReceiptStatusSuccessful
			}
		}
		c.receipts[tx.Hash()] = r
	}
}

func (c *fake) balance(a common.Address) *big.Int {
	if b := c.lpt[a]; b != nil {
		return b
	}
	return new(big.Int)
}

// grants is each address's successful transfers from the faucet, read
// from the chain's logs.
func (c *fake) grants() map[common.Address]int {
	out := map[common.Address]int{}
	for _, l := range c.logs {
		if to, ok := transferTo(l, c.token, c.self); ok {
			out[to]++
		}
	}
	return out
}

func (c *fake) Head(context.Context) (uint64, error) { return c.head, nil }

func (c *fake) Logs(_ context.Context, q ethereum.FilterQuery) ([]types.Log, error) {
	from, to := q.FromBlock.Uint64(), q.ToBlock.Uint64()
	kind := uint64(0)
	if q.Addresses[0] == c.token {
		kind = 1
	}
	c.ranges = append(c.ranges, [3]uint64{kind, from, to})
	if to < from || to-from+1 > c.maxRange {
		return nil, fmt.Errorf("query returned more than %d blocks", c.maxRange)
	}
	var out []types.Log
	for _, l := range c.logs {
		if l.BlockNumber < from || l.BlockNumber > to || l.Address != q.Addresses[0] {
			continue
		}
		match := len(l.Topics) >= len(q.Topics)
		for i, want := range q.Topics {
			if match && len(want) > 0 {
				match = false
				for _, w := range want {
					match = match || l.Topics[i] == w
				}
			}
		}
		if match {
			out = append(out, l)
		}
	}
	return out, nil
}

func (c *fake) Nonce(_ context.Context, pending bool) (uint64, error) {
	n := c.nonce
	for pending && c.pool[n] != nil {
		n++
	}
	return n, nil
}

func (c *fake) LPT(context.Context) (*big.Int, error) {
	return new(big.Int).Set(c.balance(c.self)), nil
}
func (c *fake) Gas(context.Context) (*big.Int, error) { return new(big.Int).Set(c.eth), nil }

func (c *fake) Prepare(_ context.Context, nonce uint64, to common.Address, amount *big.Int) ([]byte, common.Hash, *big.Int, error) {
	if c.estimateRefused {
		return nil, common.Hash{}, nil, errors.New("insufficient funds for gas * price + value: have 0 want 60000000000000")
	}
	tx, err := types.SignTx(types.NewTx(&types.DynamicFeeTx{ChainID: c.chainID, Nonce: nonce, GasTipCap: big.NewInt(1),
		GasFeeCap: big.NewInt(feeCap), Gas: gasPerGrant, To: &c.token, Data: transferData(to, amount)}),
		types.LatestSignerForChainID(c.chainID), c.key)
	if err != nil {
		return nil, common.Hash{}, nil, err
	}
	raw, _ := tx.MarshalBinary()
	return raw, tx.Hash(), big.NewInt(gasPerGrant * feeCap), nil
}

func (c *fake) Broadcast(_ context.Context, raw []byte) error {
	tx := new(types.Transaction)
	if err := tx.UnmarshalBinary(raw); err != nil {
		return err
	}
	if c.onBroadcast != nil {
		c.onBroadcast(tx)
	}
	c.broadcasts = append(c.broadcasts, tx.Hash())
	switch {
	case tx.Nonce() < c.nonce:
		return errors.New("nonce too low")
	case c.pool[tx.Nonce()] != nil && c.pool[tx.Nonce()].Hash() == tx.Hash():
		return errors.New("transaction already imported")
	}
	c.pool[tx.Nonce()] = tx
	return nil
}

func (c *fake) Receipt(_ context.Context, h common.Hash) (*types.Receipt, error) {
	if c.auto {
		c.mine()
	}
	return c.receipts[h], nil
}

// --- running the faucet against it ----------------------------------------------

// crash is what a test's hook panics with: the process dies at that
// moment, and nothing after it runs.
type crash struct{}

type run struct {
	f    *faucet
	mu   sync.Mutex
	logs []string
}

// start makes a faucet process over c and the state folder dir, as main
// does: a new one is a restart.
func start(c *fake, dir string, maxGrants int, page uint64, fromBlock uint64) *run {
	r := &run{}
	r.f = &faucet{l: c, chainID: c.chainID.Uint64(), self: c.self, registry: c.registry, token: c.token, amount: grant,
		maxGrants: maxGrants, firstBlock: fromBlock, page: page, journal: filepath.Join(dir, "journal.json"),
		receiptWait: 20 * time.Millisecond, every: time.Millisecond,
		logf: func(format string, args ...any) {
			r.mu.Lock()
			defer r.mu.Unlock()
			r.logs = append(r.logs, fmt.Sprintf(format, args...))
		}}
	r.f.init()
	return r
}

// tick runs one poll and reports whether the process died in it.
func (r *run) tick(t *testing.T) (died bool) {
	t.Helper()
	defer func() {
		if p := recover(); p != nil {
			if _, ok := p.(crash); !ok {
				panic(p)
			}
			died = true
		}
	}()
	if err := r.f.tick(context.Background()); err != nil {
		t.Fatalf("tick: %v", err)
	}
	return false
}

// lines is the log lines that begin with prefix.
func (r *run) lines(prefix string) []string {
	var out []string
	for _, l := range r.logs {
		if strings.HasPrefix(l, prefix) {
			out = append(out, l)
		}
	}
	return out
}

func (r *run) journal(t *testing.T) *entry {
	t.Helper()
	e, err := readJournal(r.f.journal, r.f.chainID, r.f.token, r.f.self)
	if err != nil {
		t.Fatal(err)
	}
	return e
}

func wantGrants(t *testing.T, c *fake, want map[common.Address]int) {
	t.Helper()
	got := c.grants()
	if fmt.Sprint(got) != fmt.Sprint(want) {
		t.Fatalf("grants on chain %v, want %v", got, want)
	}
}

// --- the journal -------------------------------------------------------------------

func signedEntry(t *testing.T, c *fake, n common.Address, nonce uint64) *entry {
	raw, h, _, err := c.Prepare(context.Background(), nonce, n, grant)
	if err != nil {
		t.Fatal(err)
	}
	return &entry{ChainID: c.chainID.Uint64(), Token: c.token, Faucet: c.self, Node: n, Amount: grant.String(), Nonce: nonce,
		Block: 7, Tx: h, Raw: raw, Outcome: pending}
}

func TestJournalReadsWhatItWrites(t *testing.T) {
	c := newFake(t)
	path := filepath.Join(t.TempDir(), "journal.json")
	if e, err := readJournal(path, 474747, c.token, c.self); e != nil || err != nil {
		t.Fatalf("with no file: %v, %v; want no entry", e, err)
	}
	want := signedEntry(t, c, node(1), 3)
	if err := writeJournal(path, want); err != nil {
		t.Fatal(err)
	}
	got, err := readJournal(path, 474747, c.token, c.self)
	if err != nil {
		t.Fatal(err)
	}
	if fmt.Sprint(*got) != fmt.Sprint(*want) {
		t.Fatalf("read %+v\nwrote %+v", *got, *want)
	}
	if info, _ := os.Stat(path); info.Mode().Perm() != 0o600 {
		t.Fatalf("the journal's mode is %v, want 0600", info.Mode().Perm())
	}
}

// A journal is the only record of a grant in flight: one the faucet cannot
// read, or one whose fields are not what its signed bytes say, or one of
// another deployment, stops the faucet rather than being dropped.
func TestJournalRefusesWhatItCannotTrust(t *testing.T) {
	c := newFake(t)
	dir := t.TempDir()
	path := filepath.Join(dir, "journal.json")
	other := newFake(t)
	cases := map[string]func(e *entry){
		"another node":    func(e *entry) { e.Node = node(2) },
		"another amount":  func(e *entry) { e.Amount = "1" },
		"another nonce":   func(e *entry) { e.Nonce = 4 },
		"another tx hash": func(e *entry) { e.Tx = common.Hash{1} },
		"a cut raw":       func(e *entry) { e.Raw = e.Raw[:len(e.Raw)-1] },
		"another signer": func(e *entry) {
			o := signedEntry(t, other, node(1), 3)
			e.Raw, e.Tx = o.Raw, o.Tx
		},
		"an outcome it never writes": func(e *entry) { e.Outcome = "done" },
	}
	for what, change := range cases {
		e := signedEntry(t, c, node(1), 3)
		change(e)
		if err := writeJournal(path, e); err != nil {
			t.Fatal(err)
		}
		if _, err := readJournal(path, 474747, c.token, c.self); err == nil {
			t.Errorf("a journal with %s was read", what)
		}
	}
	if err := writeJournal(path, signedEntry(t, c, node(1), 3)); err != nil {
		t.Fatal(err)
	}
	if _, err := readJournal(path, 1, c.token, c.self); err == nil || !strings.Contains(err.Error(), "one deployment") {
		t.Errorf("a journal of another chain: %v", err)
	}
	if _, err := readJournal(path, 474747, node(9), c.self); err == nil {
		t.Error("a journal of another token was read")
	}
	if _, err := readJournal(path, 474747, c.token, node(9)); err == nil {
		t.Error("a journal of another faucet was read")
	}
	for _, text := range []string{"", "{", `{"chainId": 474747, "extra": 1}`} {
		os.WriteFile(path, []byte(text), 0o600)
		if _, err := readJournal(path, 474747, c.token, c.self); err == nil {
			t.Errorf("the journal %q was read", text)
		}
	}
}

// The journal is written beside itself and renamed into place: no
// temporary file is left beside it, and a write that cannot finish leaves
// the old entry whole.
func TestJournalIsReplacedAtomically(t *testing.T) {
	c := newFake(t)
	dir := t.TempDir()
	path := filepath.Join(dir, "journal.json")
	versions := []*entry{signedEntry(t, c, node(1), 3), signedEntry(t, c, node(2), 4)}
	versions[1].Outcome = granted
	for _, v := range versions {
		if err := writeJournal(path, v); err != nil {
			t.Fatal(err)
		}
	}
	files, _ := os.ReadDir(dir)
	if len(files) != 1 {
		t.Fatalf("the folder holds %d files, want the journal alone", len(files))
	}
	// A write that cannot finish leaves the old entry whole.
	if err := os.Chmod(dir, 0o500); err != nil {
		t.Fatal(err)
	}
	defer os.Chmod(dir, 0o700)
	if os.Geteuid() != 0 {
		if err := writeJournal(path, versions[0]); err == nil {
			t.Fatal("a write into a folder that takes no file succeeded")
		}
		if e, err := readJournal(path, 474747, c.token, c.self); err != nil || e.Node != versions[1].Node {
			t.Fatalf("after a failed write: %v, %v; want the last entry whole", e, err)
		}
	}
}

// --- reading the logs in pages ---------------------------------------------------

func TestPages(t *testing.T) {
	for _, c := range []struct {
		from, to, size uint64
		want           string
	}{
		{0, 12, 5, "[[0 4] [5 9] [10 12]]"},
		{3, 3, 5, "[[3 3]]"},
		{3, 7, 5, "[[3 7]]"},
		{3, 8, 1, "[[3 3] [4 4] [5 5] [6 6] [7 7] [8 8]]"},
		{10, 19, 5, "[[10 14] [15 19]]"},
		{5, 4, 5, "[]"},
		{^uint64(0) - 2, ^uint64(0), 5000, fmt.Sprintf("[[%d %d]]", ^uint64(0)-2, ^uint64(0))},
	} {
		var got [][2]uint64
		eachPage(c.from, c.to, c.size, func(a, b uint64) (bool, error) {
			got = append(got, [2]uint64{a, b})
			return true, nil
		})
		if fmt.Sprint(got) != c.want {
			t.Errorf("eachPage(%d, %d, %d) = %v, want %s", c.from, c.to, c.size, got, c.want)
		}
	}
	// It stops at the first range its caller is done with, or fails on.
	var asked int
	eachPage(0, 12, 5, func(a, b uint64) (bool, error) { asked++; return a < 5, nil })
	if asked != 2 {
		t.Errorf("eachPage went on for %d ranges after its caller was done at the second", asked)
	}
}

// The Registry's and the token's logs are read from --from-block to the
// head in pages no larger than the endpoint takes, every block once, then
// only the new blocks; nodes registered before --from-block are not the
// deployment's and are not read.
func TestRegistrationsAreReadInPagesThenOnlyNewBlocks(t *testing.T) {
	c := newFake(t)
	c.maxRange = 5
	early := node(99)
	c.register(early) // block 1, before the deployment's first block
	c.emptyBlocks(2)
	var order []common.Address
	for i := 1; i <= 6; i++ {
		c.register(node(i))
		order = append(order, node(i))
		c.emptyBlocks(uint64(i))
	}
	head := c.head
	r := start(c, t.TempDir(), 100, 5, 4)
	if err := r.f.read(context.Background()); err != nil {
		t.Fatal(err)
	}
	checkRanges(t, c.ranges, 4, head, 5)
	if fmt.Sprint(r.f.nodes) != fmt.Sprint(order) {
		t.Fatalf("read nodes %v, want %v in registration order", r.f.nodes, order)
	}
	// The next read asks for the new blocks only.
	c.ranges = nil
	c.emptyBlocks(3)
	c.register(node(7))
	if err := r.f.read(context.Background()); err != nil {
		t.Fatal(err)
	}
	checkRanges(t, c.ranges, head+1, c.head, 5)
	if r.f.nodes[len(r.f.nodes)-1] != node(7) || len(r.f.nodes) != 7 {
		t.Fatalf("after the new blocks: %v", r.f.nodes)
	}
	// Nothing new: nothing asked.
	c.ranges = nil
	if err := r.f.read(context.Background()); err != nil || len(c.ranges) != 0 {
		t.Fatalf("with no new block it asked for %v (%v)", c.ranges, err)
	}
}

// A page that fails is read again from where it failed, and none is
// skipped: a page larger than the endpoint takes is an error, not an
// empty answer.
func TestAFailedPageIsReadAgain(t *testing.T) {
	c := newFake(t)
	c.maxRange = 5
	for i := 1; i <= 3; i++ {
		c.emptyBlocks(4)
		c.register(node(i))
	}
	r := start(c, t.TempDir(), 100, 10, 0)
	if err := r.f.read(context.Background()); err == nil {
		t.Fatal("a page of 10 blocks on an endpoint that takes 5 was read")
	}
	if r.f.next != 0 || len(r.f.nodes) != 0 {
		t.Fatalf("after a failed page the faucet reads on from block %d with %d nodes", r.f.next, len(r.f.nodes))
	}
	r.f.page = 5
	if err := r.f.read(context.Background()); err != nil || len(r.f.nodes) != 3 {
		t.Fatalf("read again: %v, %d nodes", err, len(r.f.nodes))
	}
}

// checkRanges holds the ranges asked of each contract to pages of at most
// size blocks that cover from..to once each, in order.
func checkRanges(t *testing.T, ranges [][3]uint64, from, to, size uint64) {
	t.Helper()
	for kind := uint64(0); kind < 2; kind++ {
		next := from
		for _, r := range ranges {
			if r[0] != kind {
				continue
			}
			if r[1] != next || r[2] < r[1] || r[2]-r[1]+1 > size {
				t.Fatalf("contract %d: asked for blocks %d to %d, want a page of at most %d from %d (%v)", kind, r[1], r[2], size, next, ranges)
			}
			next = r[2] + 1
		}
		if next != to+1 {
			t.Fatalf("contract %d: read to block %d, want to the head, %d (%v)", kind, next-1, to, ranges)
		}
	}
}

// --- once per node -----------------------------------------------------------------

// Each registered node is granted once, in registration order; a node
// registered later is granted at the next poll; nothing is granted again,
// by the same faucet or by one restarted; a node the faucet sent LPT to
// before is never granted.
func TestEachNodeIsGrantedOnceInRegistrationOrder(t *testing.T) {
	c := newFake(t)
	dir := t.TempDir()
	for i := 1; i <= 3; i++ {
		c.register(node(i))
	}
	c.head++
	c.logs = append(c.logs, c.transferLog(c.self, node(4), big.NewInt(5))) // sent LPT before it registered
	c.register(node(4))
	r := start(c, dir, 100, 5000, 0)
	r.tick(t)
	wantGrants(t, c, map[common.Address]int{node(1): 1, node(2): 1, node(3): 1, node(4): 1})
	var granted []string
	for _, l := range r.lines("granted: ") {
		granted = append(granted, strings.Fields(l)[2])
	}
	if fmt.Sprint(granted) != fmt.Sprint([]common.Address{node(1), node(2), node(3)}) {
		t.Fatalf("granted in the order %v", granted)
	}
	c.register(node(5))
	r.tick(t)
	r.tick(t)
	wantGrants(t, c, map[common.Address]int{node(1): 1, node(2): 1, node(3): 1, node(4): 1, node(5): 1})
	// A restart with the journal's last grant, and one with no journal at
	// all, read the chain and grant nobody again.
	for _, wipe := range []bool{false, true} {
		if wipe {
			os.Remove(filepath.Join(dir, "journal.json"))
		}
		again := start(c, dir, 100, 5000, 0)
		again.tick(t)
		again.tick(t)
		wantGrants(t, c, map[common.Address]int{node(1): 1, node(2): 1, node(3): 1, node(4): 1, node(5): 1})
		if len(again.lines("sent")) != 0 {
			t.Fatalf("a restarted faucet sent %v", again.lines("sent"))
		}
	}
	if c.nonce != 4 {
		t.Fatalf("the faucet sent %d transactions, want 4", c.nonce)
	}
}

// --- a crash at any moment ---------------------------------------------------------

// Killed after the journal is written and before the bytes leave: the
// chain never saw the grant. The restarted faucet sends the journal's
// bytes, the same transaction, and the node is granted once.
func TestKilledBeforeTheBroadcast(t *testing.T) {
	c := newFake(t)
	dir := t.TempDir()
	c.register(node(1))
	c.onBroadcast = func(*types.Transaction) { panic(crash{}) }
	r := start(c, dir, 100, 5000, 0)
	if !r.tick(t) {
		t.Fatal("the faucet did not reach the broadcast")
	}
	e := r.journal(t)
	if e == nil || e.Outcome != pending || e.Node != node(1) || len(c.pool) != 0 || c.nonce != 0 {
		t.Fatalf("after the kill: journal %+v, %d waiting, nonce %d", e, len(c.pool), c.nonce)
	}
	c.onBroadcast = nil
	again := start(c, dir, 100, 5000, 0)
	again.tick(t)
	again.tick(t)
	wantGrants(t, c, map[common.Address]int{node(1): 1})
	if len(c.broadcasts) != 1 || c.broadcasts[0] != e.Tx {
		t.Fatalf("broadcast %v, want the journal's tx %s once", c.broadcasts, e.Tx)
	}
	if len(again.lines("sent again: grant to "+node(1).Hex())) != 1 || again.journal(t).Outcome != granted {
		t.Fatalf("the restart's log: %v", again.logs)
	}
}

// Killed after the broadcast reached the chain and before the receipt was
// read: the grant is mined, or still waiting, when the faucet restarts.
// Either way the node is granted once.
func TestTheAcknowledgementIsLost(t *testing.T) {
	for _, minedBeforeRestart := range []bool{true, false} {
		t.Run(fmt.Sprintf("mined before the restart: %t", minedBeforeRestart), func(t *testing.T) {
			c := newFake(t)
			dir := t.TempDir()
			c.register(node(1))
			c.register(node(2))
			c.onBroadcast = func(tx *types.Transaction) {
				c.pool[tx.Nonce()] = tx
				if minedBeforeRestart {
					c.mine()
				}
				panic(crash{})
			}
			r := start(c, dir, 100, 5000, 0)
			if !r.tick(t) {
				t.Fatal("the faucet did not reach the broadcast")
			}
			if e := r.journal(t); e.Outcome != pending || e.Node != node(1) {
				t.Fatalf("after the kill the journal holds %+v", e)
			}
			c.onBroadcast = nil
			again := start(c, dir, 100, 5000, 0)
			again.tick(t)
			again.tick(t)
			wantGrants(t, c, map[common.Address]int{node(1): 1, node(2): 1})
			if c.nonce != 2 {
				t.Fatalf("the faucet's transactions mined: %d, want 2", c.nonce)
			}
			if !minedBeforeRestart && !strings.Contains(strings.Join(again.lines("sent again"), "\n"), "already imported") {
				t.Fatalf("the restart's log: %v", again.logs)
			}
		})
	}
}

// A grant mined and reverted is not a grant: the node stays due, and is
// granted once the faucet can, once.
func TestAMinedRevertLeavesTheNodeDue(t *testing.T) {
	c := newFake(t)
	dir := t.TempDir()
	c.register(node(1))
	c.onBroadcast = func(tx *types.Transaction) {
		c.pool[tx.Nonce()] = tx
		c.lpt[c.self] = new(big.Int) // the faucet's LPT is gone before the grant is mined
		c.mine()
		panic(crash{})
	}
	r := start(c, dir, 100, 5000, 0)
	if !r.tick(t) {
		t.Fatal("the faucet did not reach the broadcast")
	}
	c.onBroadcast = nil
	again := start(c, dir, 100, 5000, 0)
	again.tick(t)
	if e := again.journal(t); e.Outcome != reverted || e.Node != node(1) {
		t.Fatalf("the journal holds %+v, want the revert", e)
	}
	if len(again.lines("reverted: grant to "+node(1).Hex())) != 1 || len(again.lines("out of LPT: ")) != 1 {
		t.Fatalf("the log: %v", again.logs)
	}
	wantGrants(t, c, map[common.Address]int{})
	c.lpt[c.self] = new(big.Int).Set(grant) // refilled
	again.tick(t)
	again.tick(t)
	wantGrants(t, c, map[common.Address]int{node(1): 1})
}

// A grant that reverts while the faucet runs leaves its node due and in
// its place: with one grant left under the cap, no later node takes it,
// and the node is granted once the faucet can pay.
func TestARevertedNodeKeepsItsPlace(t *testing.T) {
	c := newFake(t)
	c.register(node(1))
	c.register(node(2))
	c.onBroadcast = func(tx *types.Transaction) {
		c.onBroadcast = nil
		held := c.lpt[c.self]
		c.pool[tx.Nonce()] = tx
		c.lpt[c.self] = new(big.Int) // the faucet's LPT is gone when the grant is mined
		c.mine()
		c.lpt[c.self] = held // and back before anything else is sent
	}
	r := start(c, t.TempDir(), 1, 5000, 0)
	for i := 0; i < 3; i++ {
		r.tick(t)
	}
	if len(r.lines("reverted: grant to "+node(1).Hex())) != 1 {
		t.Fatalf("the log: %v", r.logs)
	}
	wantGrants(t, c, map[common.Address]int{node(1): 1})
	if cap := r.lines("cap reached: "); len(cap) != 1 || !strings.Contains(cap[0], node(2).Hex()) {
		t.Fatalf("the cap's line: %v", r.logs)
	}
}

// A nonce another transaction took leaves the grant not granted, read
// from the chain, and the node due.
func TestANonceTakenByAnotherTransaction(t *testing.T) {
	c := newFake(t)
	dir := t.TempDir()
	c.register(node(1))
	c.onBroadcast = func(tx *types.Transaction) {
		// Another transaction from the key, a transfer elsewhere, is mined at the nonce.
		raw, _, _, _ := c.Prepare(context.Background(), tx.Nonce(), node(50), big.NewInt(1))
		other := new(types.Transaction)
		other.UnmarshalBinary(raw)
		c.pool[tx.Nonce()] = other
		c.mine()
		panic(crash{})
	}
	r := start(c, dir, 100, 5000, 0)
	r.tick(t)
	c.onBroadcast = nil
	again := start(c, dir, 100, 5000, 0)
	again.tick(t)
	again.tick(t)
	if len(again.lines("not granted: nonce 0")) != 1 {
		t.Fatalf("the log: %v", again.logs)
	}
	wantGrants(t, c, map[common.Address]int{node(50): 1, node(1): 1})
}

// --- the waits ---------------------------------------------------------------------

// The cap, an empty faucet and a faucet without gas each log their own
// line, once, grant nothing and write no journal; the faucet grants the
// nodes due once the condition clears.
func TestTheWaitsEachLogTheirOwnLineAndGrantNothing(t *testing.T) {
	kinds := []string{"cap reached", "out of LPT", "out of gas"}
	waits := map[string]struct {
		kind       string
		set, clear func(c *fake)
		maxGrants  int
	}{
		"the cap": {"cap reached", func(*fake) {}, nil, 2},
		"an empty faucet": {"out of LPT", func(c *fake) { c.lpt[c.self] = new(big.Int).Sub(grant, big.NewInt(1)) },
			func(c *fake) { c.lpt[c.self] = new(big.Int).Mul(grant, big.NewInt(9)) }, 100},
		"too little gas": {"out of gas", func(c *fake) { c.eth = big.NewInt(gasPerGrant*feeCap - 1) },
			func(c *fake) { c.eth = big.NewInt(1e18) }, 100},
		"an estimate refused for gas": {"out of gas", func(c *fake) { c.eth, c.estimateRefused = new(big.Int), true },
			func(c *fake) { c.eth, c.estimateRefused = big.NewInt(1e18), false }, 100},
	}
	for what, w := range waits {
		kind := w.kind
		t.Run(what, func(t *testing.T) {
			c := newFake(t)
			dir := t.TempDir()
			c.register(node(1))
			c.register(node(2))
			r := start(c, dir, w.maxGrants, 5000, 0)
			r.tick(t)
			wantGrants(t, c, map[common.Address]int{node(1): 1, node(2): 1})
			before := r.journal(t)
			c.register(node(3))
			c.register(node(4))
			w.set(c)
			for i := 0; i < 3; i++ {
				r.tick(t)
			}
			wantGrants(t, c, map[common.Address]int{node(1): 1, node(2): 1})
			for _, other := range kinds {
				want := 0
				if other == kind {
					want = 1
				}
				if got := len(r.lines(other + ": ")); got != want {
					t.Fatalf("%q lines: %d, want %d (%v)", other, got, want, r.logs)
				}
			}
			if line := r.lines(kind + ": ")[0]; !strings.Contains(line, node(3).Hex()) {
				t.Fatalf("the line does not name the node waiting: %s", line)
			}
			if after := r.journal(t); after.Tx != before.Tx || len(c.pool) != 0 {
				t.Fatalf("waiting, the faucet wrote %+v or sent %d", after, len(c.pool))
			}
			if w.clear == nil {
				return
			}
			w.clear(c)
			r.tick(t)
			wantGrants(t, c, map[common.Address]int{node(1): 1, node(2): 1, node(3): 1, node(4): 1})
		})
	}
}

// A receipt that does not come in time leaves the grant in the journal;
// the next poll sends the same bytes and settles it, once.
func TestAGrantWithNoReceiptYetIsSettledAtTheNextPoll(t *testing.T) {
	c := newFake(t)
	c.auto = false
	c.register(node(1))
	r := start(c, t.TempDir(), 100, 5000, 0)
	r.tick(t)
	if e := r.journal(t); e.Outcome != pending || len(r.lines("in flight: ")) != 1 {
		t.Fatalf("journal %+v, log %v", e, r.logs)
	}
	r.tick(t) // sent again: already imported
	c.mine()
	r.tick(t)
	wantGrants(t, c, map[common.Address]int{node(1): 1})
	hashes := map[common.Hash]bool{}
	for _, h := range c.broadcasts {
		hashes[h] = true
	}
	if len(hashes) != 1 || r.journal(t).Outcome != granted {
		t.Fatalf("broadcast %d different transactions, want the one", len(hashes))
	}
}

// A successful receipt that holds no Transfer to the node is a fault,
// not a grant: the token address is not the test LPT token.
func TestASuccessWithoutTheTransferIsAFault(t *testing.T) {
	c := newFake(t)
	c.register(node(1))
	r := start(c, t.TempDir(), 100, 5000, 0)
	c.onBroadcast = func(tx *types.Transaction) {
		c.pool[tx.Nonce()] = tx
		c.mine()
		c.receipts[tx.Hash()].Logs = nil
	}
	if err := r.f.tick(context.Background()); err == nil || !strings.Contains(err.Error(), "without a Transfer") {
		t.Fatalf("tick: %v", err)
	}
	if r.journal(t).Outcome != pending {
		t.Fatal("the grant was recorded")
	}
}

func TestTransferDataIsTheTokensCall(t *testing.T) {
	got := fmt.Sprintf("%x", transferData(common.HexToAddress("0x70997970C51812dc3A010C7d01b50e0d17dc79C8"), grant))
	want := "a9059cbb00000000000000000000000070997970c51812dc3a010c7d01b50e0d17dc79c8" +
		"00000000000000000000000000000000000000000000003635c9adc5dea00000"
	if got != want {
		t.Fatalf("got  %s\nwant %s", got, want)
	}
}
