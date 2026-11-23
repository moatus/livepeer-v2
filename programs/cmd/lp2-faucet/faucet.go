package main

import (
	"context"
	"fmt"
	"math/big"
	"strings"
	"time"

	ethereum "github.com/ethereum/go-ethereum"
	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/core/types"
	"github.com/ethereum/go-ethereum/crypto"
)

var (
	nodeRegistered = crypto.Keccak256Hash([]byte("NodeRegistered(address,uint256)"))
	transferEvent  = crypto.Keccak256Hash([]byte("Transfer(address,address,uint256)"))
	transferSel    = crypto.Keccak256([]byte("transfer(address,uint256)"))[:4]
)

// transferData is the call data of the token's transfer(to, amount).
func transferData(to common.Address, amount *big.Int) []byte {
	return append(append(append([]byte(nil), transferSel...), common.LeftPadBytes(to.Bytes(), 32)...),
		common.LeftPadBytes(amount.Bytes(), 32)...)
}

// ledger is what the faucet asks of the chain. The live one is the chain
// through its JSON-RPC; the tests' one is a chain in memory.
type ledger interface {
	Head(ctx context.Context) (uint64, error)
	Logs(ctx context.Context, q ethereum.FilterQuery) ([]types.Log, error)
	// Nonce is the faucet's next nonce: of the newest block, or counting
	// its transactions waiting to be mined too.
	Nonce(ctx context.Context, pending bool) (uint64, error)
	LPT(ctx context.Context) (*big.Int, error) // the faucet's test LPT, in wei
	Gas(ctx context.Context) (*big.Int, error) // the faucet's ETH, in wei
	// Prepare signs transfer(to, amount) at nonce and gives its bytes, its
	// hash and the most gas it can cost. Nothing is sent.
	Prepare(ctx context.Context, nonce uint64, to common.Address, amount *big.Int) (raw []byte, hash common.Hash, maxCost *big.Int, err error)
	Broadcast(ctx context.Context, raw []byte) error
	Receipt(ctx context.Context, hash common.Hash) (*types.Receipt, error) // nil when there is none
}

// faucet grants each registered node test LPT once. Its record is the
// chain and the journal; what it holds in memory is rebuilt at start.
type faucet struct {
	l                     ledger
	chainID               uint64
	self, registry, token common.Address
	amount                *big.Int
	maxGrants             int
	firstBlock, page      uint64
	journal               string
	receiptWait, every    time.Duration // the longest wait for a receipt, and how often it is asked
	logf                  func(format string, args ...any)

	next    uint64                  // the next block to read
	nodes   []common.Address        // registered, in registration order
	known   map[common.Address]bool // in nodes
	sentTo  map[common.Address]bool // sent LPT by the faucet: a Transfer from it, or a grant it saw succeed
	waiting string                  // the wait last logged, so each is logged once when it begins
}

func (f *faucet) init() {
	f.next = f.firstBlock
	f.known = map[common.Address]bool{}
	f.sentTo = map[common.Address]bool{}
}

// pages splits the blocks from..to into ranges of at most size blocks.
func pages(from, to, size uint64) [][2]uint64 {
	var out [][2]uint64
	for a := from; a <= to; a += size {
		b := a + size - 1
		if b > to || b < a {
			b = to
		}
		out = append(out, [2]uint64{a, b})
		if b == to {
			break
		}
	}
	return out
}

// read reads the blocks from f.next to the head, a page at a time: the
// Registry's NodeRegistered logs, and the token's Transfers from the
// faucet. A page is taken whole or not at all, so a failed read is read
// again from where it failed.
func (f *faucet) read(ctx context.Context) error {
	head, err := f.l.Head(ctx)
	if err != nil {
		return err
	}
	if head < f.next {
		return nil
	}
	for _, p := range pages(f.next, head, f.page) {
		from, to := new(big.Int).SetUint64(p[0]), new(big.Int).SetUint64(p[1])
		regs, err := f.l.Logs(ctx, ethereum.FilterQuery{FromBlock: from, ToBlock: to, Addresses: []common.Address{f.registry},
			Topics: [][]common.Hash{{nodeRegistered}}})
		if err != nil {
			return fmt.Errorf("NodeRegistered logs of blocks %d to %d: %w", p[0], p[1], err)
		}
		sent, err := f.l.Logs(ctx, ethereum.FilterQuery{FromBlock: from, ToBlock: to, Addresses: []common.Address{f.token},
			Topics: [][]common.Hash{{transferEvent}, {common.BytesToHash(f.self.Bytes())}}})
		if err != nil {
			return fmt.Errorf("the faucet's Transfer logs of blocks %d to %d: %w", p[0], p[1], err)
		}
		for _, l := range regs {
			if l.Address != f.registry || len(l.Topics) != 2 || l.Topics[0] != nodeRegistered || l.Removed {
				continue
			}
			n := common.BytesToAddress(l.Topics[1].Bytes())
			if !f.known[n] {
				f.known[n] = true
				f.nodes = append(f.nodes, n)
				f.logf("registered: node %s in block %d, %d nodes registered", n, l.BlockNumber, len(f.nodes))
			}
		}
		for _, l := range sent {
			if to, ok := transferTo(l, f.token, f.self); ok {
				f.sentTo[to] = true
			}
		}
		f.next = p[1] + 1
	}
	return nil
}

// transferTo is the recipient of a Transfer log of token from `from`.
func transferTo(l types.Log, token, from common.Address) (common.Address, bool) {
	if l.Address != token || len(l.Topics) != 3 || l.Topics[0] != transferEvent || l.Removed ||
		common.BytesToAddress(l.Topics[1].Bytes()) != from {
		return common.Address{}, false
	}
	return common.BytesToAddress(l.Topics[2].Bytes()), true
}

// loadJournal reads the journal. A grant it shows succeeded counts as
// sent even before the token's logs are read.
func (f *faucet) loadJournal() (*entry, error) {
	e, err := readJournal(f.journal, f.chainID, f.token, f.self)
	if err != nil || e == nil {
		return nil, err
	}
	if e.Outcome == granted {
		f.sentTo[e.Node] = true
	}
	return e, nil
}

// tick is one poll: it resolves a grant left in flight, reads the new
// blocks and grants each node due, in registration order, one at a time.
func (f *faucet) tick(ctx context.Context) error {
	e, err := f.loadJournal()
	if err != nil {
		return err
	}
	if e != nil && e.Outcome == pending {
		f.logf("resolving: the journal holds a grant to %s in flight, nonce %d, tx %s", e.Node, e.Nonce, e.Tx)
		if done, err := f.resolve(ctx, e, false); !done || err != nil {
			return err
		}
	}
	if err := f.read(ctx); err != nil {
		return err
	}
	for _, n := range f.nodes {
		if f.sentTo[n] {
			continue
		}
		if done, err := f.grant(ctx, n); !done || err != nil {
			return err
		}
	}
	return nil
}

// grantedCount is how many registered nodes the faucet has sent LPT.
func (f *faucet) grantedCount() int {
	c := 0
	for _, n := range f.nodes {
		if f.sentTo[n] {
			c++
		}
	}
	return c
}

// wait logs why the faucet is waiting, once when the wait begins.
func (f *faucet) wait(kind, format string, args ...any) {
	if f.waiting != kind {
		f.waiting = kind
		f.logf(kind+": "+format, args...)
	}
}

// grant sends node its grant and waits for the receipt. done is false when
// the faucet must wait before granting anything more: the cap, an empty
// faucet, no gas, or a grant whose receipt has not come.
func (f *faucet) grant(ctx context.Context, node common.Address) (done bool, err error) {
	if c := f.grantedCount(); c >= f.maxGrants {
		f.wait("cap reached", "%d nodes granted, the most --max-grants allows; node %s and any later node are not granted", c, node)
		return false, nil
	}
	held, err := f.l.LPT(ctx)
	if err != nil {
		return false, err
	}
	if held.Cmp(f.amount) < 0 {
		f.wait("out of LPT", "the faucet %s holds %s wei of test LPT and a grant is %s; node %s waits until it is refilled",
			f.self, held, f.amount, node)
		return false, nil
	}
	head, err := f.l.Head(ctx)
	if err != nil {
		return false, err
	}
	nonce, err := f.l.Nonce(ctx, true)
	if err != nil {
		return false, err
	}
	gas, err := f.l.Gas(ctx)
	if err != nil {
		return false, err
	}
	raw, hash, cost, err := f.l.Prepare(ctx, nonce, node, f.amount)
	if err != nil && strings.Contains(err.Error(), "insufficient funds") {
		// A chain may refuse the estimate itself to an account with too
		// little ETH.
		f.wait("out of gas", "the faucet %s holds %s wei of ETH and the chain refused the grant's gas estimate (%v); node %s waits until it is funded",
			f.self, gas, err, node)
		return false, nil
	}
	if err != nil {
		return false, err
	}
	if gas.Cmp(cost) < 0 {
		f.wait("out of gas", "the faucet %s holds %s wei of ETH and a grant may cost %s; node %s waits until it is funded",
			f.self, gas, cost, node)
		return false, nil
	}
	f.waiting = ""
	e := &entry{ChainID: f.chainID, Token: f.token, Faucet: f.self, Node: node, Amount: f.amount.String(), Nonce: nonce,
		Block: head, Tx: hash, Raw: raw, Outcome: pending}
	if err := writeJournal(f.journal, e); err != nil {
		return false, fmt.Errorf("the journal: %w", err)
	}
	return f.resolve(ctx, e, true)
}

// resolve settles a grant written in the journal. While its nonce is not
// used on chain it sends the journal's bytes (again, after a restart) and
// waits for the receipt. Once the nonce is used, the receipt says how it
// ended; with no receipt, another transaction took the nonce, and the
// chain's Transfers say whether the node was sent LPT. done is false
// while the grant is still in flight: the journal keeps it.
func (f *faucet) resolve(ctx context.Context, e *entry, first bool) (done bool, err error) {
	used, err := f.l.Nonce(ctx, false)
	if err != nil {
		return false, err
	}
	if used <= e.Nonce {
		err := f.l.Broadcast(ctx, e.Raw)
		word := "sent"
		if !first {
			word = "sent again"
		}
		why := ""
		if err != nil {
			why = fmt.Sprintf(" (the chain answered: %v)", err)
		}
		f.logf("%s: grant to %s, nonce %d, tx %s%s", word, e.Node, e.Nonce, e.Tx, why)
	}
	r, err := f.awaitReceipt(ctx, e.Tx)
	if err != nil {
		return false, err
	}
	if r == nil {
		if used, err = f.l.Nonce(ctx, false); err != nil || used <= e.Nonce {
			if err == nil && ctx.Err() == nil {
				f.logf("in flight: no receipt for tx %s within %s; the journal keeps it and it is sent again", e.Tx, f.receiptWait)
			}
			return false, err
		}
		// The nonce is used and not by this transaction, which can now
		// never be mined. Whether the node holds a grant is read from the
		// chain: a Transfer from the faucet to it since the grant was signed.
		sent, err := f.sentSince(ctx, e.Node, e.Block)
		if err != nil {
			return false, err
		}
		if sent {
			e.Outcome = granted
			f.sentTo[e.Node] = true
			f.logf("granted already: nonce %d went to another transaction, and the chain shows a Transfer from the faucet to %s", e.Nonce, e.Node)
		} else {
			e.Outcome = notGranted
			f.logf("not granted: nonce %d went to another transaction, not tx %s; node %s is still due", e.Nonce, e.Tx, e.Node)
		}
		return true, writeJournal(f.journal, e)
	}
	if r.Status != types.ReceiptStatusSuccessful {
		e.Outcome = reverted
		f.logf("reverted: grant to %s, nonce %d, tx %s was mined in block %d and reverted; not granted, node %s is still due",
			e.Node, e.Nonce, e.Tx, r.BlockNumber, e.Node)
		return true, writeJournal(f.journal, e)
	}
	if !movedTo(r, f.token, f.self, e.Node, e.amount()) {
		return false, fmt.Errorf("tx %s succeeded without a Transfer of %s wei from %s to %s: is %s the test LPT token?",
			e.Tx, e.Amount, f.self, e.Node, f.token)
	}
	e.Outcome = granted
	f.sentTo[e.Node] = true
	f.logf("granted: node %s %s wei of test LPT, nonce %d, tx %s, block %d", e.Node, e.Amount, e.Nonce, e.Tx, r.BlockNumber)
	return true, writeJournal(f.journal, e)
}

// awaitReceipt asks for the receipt every f.every until it comes, f.receiptWait
// passes or ctx ends; nil when none came.
func (f *faucet) awaitReceipt(ctx context.Context, h common.Hash) (*types.Receipt, error) {
	end := time.Now().Add(f.receiptWait)
	for {
		r, err := f.l.Receipt(ctx, h)
		if err != nil && ctx.Err() == nil {
			return nil, err
		}
		if r != nil || ctx.Err() != nil || !time.Now().Before(end) {
			return r, nil
		}
		select {
		case <-ctx.Done():
			return nil, nil
		case <-time.After(f.every):
		}
	}
}

// sentSince reads, a page at a time, whether the token logged a Transfer
// from the faucet to node in a block after `after`.
func (f *faucet) sentSince(ctx context.Context, node common.Address, after uint64) (bool, error) {
	head, err := f.l.Head(ctx)
	if err != nil || head <= after {
		return false, err
	}
	for _, p := range pages(after+1, head, f.page) {
		logs, err := f.l.Logs(ctx, ethereum.FilterQuery{FromBlock: new(big.Int).SetUint64(p[0]), ToBlock: new(big.Int).SetUint64(p[1]),
			Addresses: []common.Address{f.token},
			Topics:    [][]common.Hash{{transferEvent}, {common.BytesToHash(f.self.Bytes())}, {common.BytesToHash(node.Bytes())}}})
		if err != nil {
			return false, err
		}
		for _, l := range logs {
			if to, ok := transferTo(l, f.token, f.self); ok && to == node {
				return true, nil
			}
		}
	}
	return false, nil
}

// movedTo is whether the receipt logs the token's Transfer of amount from
// `from` to `to`.
func movedTo(r *types.Receipt, token, from, to common.Address, amount *big.Int) bool {
	for _, l := range r.Logs {
		if got, ok := transferTo(*l, token, from); ok && got == to && new(big.Int).SetBytes(l.Data).Cmp(amount) == 0 {
			return true
		}
	}
	return false
}
