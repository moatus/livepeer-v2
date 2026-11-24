package index

import (
	"math/big"

	"github.com/ethereum/go-ethereum/common"
)

// Flow is what settled tickets moved: the gross the tickets paid (which can
// be below their face), and the node and network amounts as each
// FeeSettled recorded them.
type Flow struct {
	Gross, Node, Network *big.Int
}

func newFlow() *Flow { return &Flow{new(big.Int), new(big.Int), new(big.Int)} }

func (f *Flow) add(e *Event) {
	f.Gross.Add(f.Gross, e.Int("gross"))
	f.Node.Add(f.Node, e.Int("nodeAmount"))
	f.Network.Add(f.Network, e.Int("networkAmount"))
}

// Payer is one payer's sums: funded (each Funded's deposit and earmark),
// released (each Released's amount) and its fees.
type Payer struct {
	Funded, Released *big.Int
	Fees             *Flow
	ByNode           map[common.Address]*Flow
	ByRound          map[uint64]*Flow
}

// Claim is one Claimed event: the round claimed, the score it applied,
// what it minted, the cut and the pot.
type Claim struct {
	Round                 uint64
	Score, Paid, Cut, Pot *big.Int
	Block                 uint64
	Tx                    common.Hash
}

// Buy is one Bought event of an address the index watches; it counts only
// if its emitter is the executor in force for its round, which the reader
// checks against Parameters.
type Buy struct {
	Executor                  common.Address
	Round, Block              uint64
	Tx                        common.Hash
	USDCIn, LPTBought, Burned *big.Int
}

// Totals is everything derived from the events: built by folding each
// event once, in chain order, so a start and a rollback build it by the
// same path.
type Totals struct {
	Nodes      []common.Address          // in the order they registered
	Registered map[common.Address]uint64 // the round each registered in
	Payers     []common.Address          // in the order they first appear
	PayerOf    map[common.Address]*Payer
	// Fees is each round's fees by node: the sums of FeeSettled's gross,
	// node and network amounts.
	Fees     map[uint64]map[common.Address]*Flow
	NodeFees map[common.Address]*Flow // every round's
	Claims   map[common.Address]map[uint64]Claim
	Starts   map[uint64]uint64 // the block each round started in
	Buys     []Buy
	// Executors is every value announced for the buyback executor's group;
	// the deployment's own executor is read apart (Index.Watched).
	Executors map[common.Address]bool
}

func newTotals() *Totals {
	return &Totals{Registered: map[common.Address]uint64{}, PayerOf: map[common.Address]*Payer{},
		Fees: map[uint64]map[common.Address]*Flow{}, NodeFees: map[common.Address]*Flow{},
		Claims: map[common.Address]map[uint64]Claim{}, Starts: map[uint64]uint64{}, Executors: map[common.Address]bool{}}
}

func (t *Totals) payer(a common.Address) *Payer {
	p, ok := t.PayerOf[a]
	if !ok {
		p = &Payer{Funded: new(big.Int), Released: new(big.Int), Fees: newFlow(),
			ByNode: map[common.Address]*Flow{}, ByRound: map[uint64]*Flow{}}
		t.PayerOf[a] = p
		t.Payers = append(t.Payers, a)
	}
	return p
}

func flowOf[K comparable](m map[K]*Flow, k K) *Flow {
	f, ok := m[k]
	if !ok {
		f = newFlow()
		m[k] = f
	}
	return f
}

// fold adds one event.
func (t *Totals) fold(e *Event) {
	switch e.Name {
	case "NodeRegistered":
		n := e.Addr("node")
		if _, ok := t.Registered[n]; !ok {
			t.Registered[n] = e.Int("round").Uint64()
			t.Nodes = append(t.Nodes, n)
		}
	case "Funded":
		p := t.payer(e.Addr("payer"))
		p.Funded.Add(p.Funded, e.Int("deposit"))
		p.Funded.Add(p.Funded, e.Int("earmark"))
	case "Released":
		p := t.payer(e.Addr("payer"))
		p.Released.Add(p.Released, e.Int("amount"))
	case "FeeSettled":
		round, node := e.Int("round").Uint64(), e.Addr("node")
		p := t.payer(e.Addr("payer"))
		p.Fees.add(e)
		flowOf(p.ByNode, node).add(e)
		flowOf(p.ByRound, round).add(e)
		if t.Fees[round] == nil {
			t.Fees[round] = map[common.Address]*Flow{}
		}
		flowOf(t.Fees[round], node).add(e)
		flowOf(t.NodeFees, node).add(e)
	case "Claimed":
		node := e.Addr("node")
		if t.Claims[node] == nil {
			t.Claims[node] = map[uint64]Claim{}
		}
		r := e.Int("round").Uint64()
		t.Claims[node][r] = Claim{Round: r, Score: e.Int("score"), Paid: e.Int("paid"), Cut: e.Int("cut"), Pot: e.Int("pot"),
			Block: e.Block, Tx: e.Tx}
	case "RoundStarted":
		t.Starts[e.Int("round").Uint64()] = e.Block
	case "Announced":
		if e.Int("group").Cmp(big.NewInt(GroupExecutor)) == 0 {
			for _, v := range e.Fields["values"].([]*big.Int) {
				t.Executors[common.BigToAddress(v)] = true
			}
		}
	case "Bought":
		t.Buys = append(t.Buys, Buy{Executor: e.Address, Round: e.Int("round").Uint64(), Block: e.Block, Tx: e.Tx,
			USDCIn: e.Int("usdcIn"), LPTBought: e.Int("lptBought"), Burned: e.Int("burned")})
	}
}
