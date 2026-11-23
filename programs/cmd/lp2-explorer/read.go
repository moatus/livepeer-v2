package main

import (
	"context"
	"encoding/json"
	"fmt"
	"math/big"
	"strings"

	"github.com/ethereum/go-ethereum/accounts/abi"
	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/common/hexutil"
	"github.com/ethereum/go-ethereum/crypto"
	"github.com/ethereum/go-ethereum/rpc"
)

// The views and events the explorer reads, as contracts/interfaces declares
// them. A test holds each to its declaration there.
const viewsABI = `[
 {"type":"function","name":"getContract","stateMutability":"view","inputs":[{"name":"id","type":"bytes32"}],"outputs":[{"name":"","type":"address"}]},
 {"type":"function","name":"currentRound","stateMutability":"view","inputs":[],"outputs":[{"name":"","type":"uint256"}]},
 {"type":"function","name":"isRegistered","stateMutability":"view","inputs":[{"name":"a","type":"address"}],"outputs":[{"name":"","type":"bool"}]},
 {"type":"function","name":"operatorOf","stateMutability":"view","inputs":[{"name":"node","type":"address"}],"outputs":[{"name":"","type":"address"}]},
 {"type":"function","name":"payoutOf","stateMutability":"view","inputs":[{"name":"node","type":"address"}],"outputs":[{"name":"","type":"address"}]},
 {"type":"function","name":"valuesAt","stateMutability":"view","inputs":[{"name":"group","type":"uint256"},{"name":"round","type":"uint256"}],"outputs":[{"name":"","type":"uint256[]"}]},
 {"type":"function","name":"stakeAt","stateMutability":"view","inputs":[{"name":"node","type":"address"},{"name":"round","type":"uint256"}],"outputs":[{"name":"","type":"uint256"}]},
 {"type":"function","name":"bondAt","stateMutability":"view","inputs":[{"name":"holder","type":"address"},{"name":"node","type":"address"},{"name":"round","type":"uint256"}],"outputs":[{"name":"","type":"uint256"}]},
 {"type":"function","name":"feesOf","stateMutability":"view","inputs":[{"name":"round","type":"uint256"},{"name":"node","type":"address"}],"outputs":[{"name":"","type":"uint256"}]},
 {"type":"function","name":"totalFees","stateMutability":"view","inputs":[{"name":"round","type":"uint256"}],"outputs":[{"name":"","type":"uint256"}]},
 {"type":"function","name":"entitlementOf","stateMutability":"view","inputs":[{"name":"node","type":"address"},{"name":"round","type":"uint256"}],"outputs":[{"name":"claimed","type":"bool"},{"name":"paid","type":"uint256"},{"name":"pot","type":"uint256"}]},
 {"type":"function","name":"partOf","stateMutability":"view","inputs":[{"name":"holder","type":"address"},{"name":"node","type":"address"},{"name":"round","type":"uint256"}],"outputs":[{"name":"amount","type":"uint256"},{"name":"withdrawn","type":"bool"}]},
 {"type":"function","name":"scoreOf","stateMutability":"view","inputs":[{"name":"node","type":"address"}],"outputs":[{"name":"","type":"uint256"}]},
 {"type":"function","name":"membersAt","stateMutability":"view","inputs":[{"name":"round","type":"uint256"}],"outputs":[{"name":"","type":"address[]"}]},
 {"type":"function","name":"balanceOf","stateMutability":"view","inputs":[{"name":"account","type":"address"}],"outputs":[{"name":"","type":"uint256"}]},
 {"type":"event","name":"Bought","anonymous":false,"inputs":[{"name":"round","type":"uint256","indexed":true},{"name":"held","type":"uint256","indexed":false},{"name":"usdcIn","type":"uint256","indexed":false},{"name":"lptBought","type":"uint256","indexed":false},{"name":"burned","type":"uint256","indexed":false},{"name":"twapX112","type":"uint256","indexed":false},{"name":"maxIn","type":"uint256","indexed":false}]},
 {"type":"event","name":"Claimed","anonymous":false,"inputs":[{"name":"node","type":"address","indexed":true},{"name":"round","type":"uint256","indexed":true},{"name":"payout","type":"address","indexed":false},{"name":"score","type":"uint256","indexed":false},{"name":"paid","type":"uint256","indexed":false},{"name":"cut","type":"uint256","indexed":false},{"name":"pot","type":"uint256","indexed":false}]},
 {"type":"event","name":"RoundStarted","anonymous":false,"inputs":[{"name":"round","type":"uint256","indexed":true},{"name":"startBlock","type":"uint256","indexed":false},{"name":"blockHash","type":"bytes32","indexed":false}]}
]`

var views = func() abi.ABI {
	a, err := abi.JSON(strings.NewReader(viewsABI))
	if err != nil {
		panic(err)
	}
	return a
}()

// R-25's groups the explorer reads.
const (
	groupRewardDelay = 4
	groupScoreSource = 6
	groupExecutor    = 10
)

// claimWindow is R-12's: round n is claimable in rounds n + d to n + d + 21.
const claimWindow = 21

// recentRounds is how many rounds before the current one the page lists:
// with the deployment's reward delay of 7, every round still claimable.
const recentRounds = 7 + claimWindow

// lowGasWei is the node key's ETH balance under which the page warns:
// 0.001 ETH, about what one public run of the team's node paid in fees.
var lowGasWei = big.NewInt(1_000_000_000_000_000)

// batchSize is the most requests sent in one JSON-RPC batch.
const batchSize = 20

// batcher is the chain's JSON-RPC endpoint; *rpc.Client is one.
type batcher interface {
	BatchCallContext(ctx context.Context, b []rpc.BatchElem) error
}

// send sends the requests in batches of batchSize. A batch that fails as a
// whole sets that error on each of its requests, so each figure is either
// read or carries the reason it was not.
func send(ctx context.Context, b batcher, reqs []rpc.BatchElem) {
	for start := 0; start < len(reqs); start += batchSize {
		part := reqs[start:min(start+batchSize, len(reqs))]
		if err := b.BatchCallContext(ctx, part); err != nil {
			for i := range part {
				part[i].Error = err
			}
		}
	}
}

// view is one contract view read at one block: what it is, for a message
// when it fails, the call, and its decoded outputs or its error.
type view struct {
	what   string
	to     common.Address
	method string
	args   []any
	out    []any
	err    error
}

// callViews reads every view at the given block (a number, or "latest").
func callViews(ctx context.Context, b batcher, block string, vs ...*view) {
	reqs := make([]rpc.BatchElem, len(vs))
	results := make([]hexutil.Bytes, len(vs))
	for i, v := range vs {
		data, err := views.Pack(v.method, v.args...)
		if err != nil {
			panic(err) // a call the code itself built wrongly
		}
		reqs[i] = rpc.BatchElem{Method: "eth_call", Args: []any{
			map[string]any{"to": v.to, "data": hexutil.Bytes(data)}, block}, Result: &results[i]}
	}
	send(ctx, b, reqs)
	for i, v := range vs {
		if reqs[i].Error != nil {
			v.err = reqs[i].Error
			continue
		}
		// A call to an address with no code answers 0x, which unpacks as
		// an error: unavailable, never zero.
		v.out, v.err = views.Unpack(v.method, results[i])
	}
}

// Int is a whole number read from the chain. JSON carries it as a decimal
// string, since wei amounts exceed what a float holds exactly.
type Int big.Int

func (n *Int) MarshalJSON() ([]byte, error) {
	return json.Marshal((*big.Int)(n).String())
}

func (n *Int) big() *big.Int { return (*big.Int)(n) }

// Status is everything the page shows. A figure whose read failed is nil
// (null in JSON) and its reason is in Unavailable: never a zero.
type Status struct {
	Block       *uint64  `json:"block"`
	BlockTime   *uint64  `json:"block_time"`
	Node        Node     `json:"node"`
	Testnet     Testnet  `json:"testnet"`
	Unavailable []string `json:"unavailable"`
}

// Node is the node shown.
type Node struct {
	Address    common.Address  `json:"address"`
	Registered *bool           `json:"registered"`
	Operator   *common.Address `json:"operator"`
	Payout     *common.Address `json:"payout"`
	Bond       *Int            `json:"bond_wei"`
	Stake      *Int            `json:"stake_wei"`
	ScoreNow   *Int            `json:"score_now"`
	PayoutUSDC *Int            `json:"payout_usdc_micro"`
	ETH        *Int            `json:"eth_wei"`
	LowGas     *bool           `json:"low_gas"`
	LowGasWei  *Int            `json:"low_gas_below_wei"`
	Rounds     []Round         `json:"rounds"`
}

// Round is one recent round of the node: its fees and stake as the claim
// reads them, and where its reward stands. A claimed round carries the
// score its claim applied and what it paid; an unclaimed one has no claim
// score, and the score a claim made now would read is the node's ScoreNow.
type Round struct {
	Round          uint64  `json:"round"`
	Fees           *Int    `json:"fees_usdc_micro"`
	Stake          *Int    `json:"stake_wei"`
	State          *string `json:"state"`
	ClaimableFrom  *uint64 `json:"claimable_from"`
	ClaimableUntil *uint64 `json:"claimable_until"`
	Claimed        *bool   `json:"claimed"`
	ClaimScore     *Int    `json:"claim_score"`
	Paid           *Int    `json:"paid_wei"`
	Pot            *Int    `json:"pot_wei"`
	Part           *Int    `json:"part_wei"`
	PartWithdrawn  *bool   `json:"part_withdrawn"`
}

// Testnet is what the page shows of the whole deployment.
type Testnet struct {
	Round           *uint64           `json:"round"`
	RoundStartBlock *uint64           `json:"round_start_block"`
	RoundStartTime  *uint64           `json:"round_start_time"`
	TotalFees       *Int              `json:"total_fees_usdc_micro"`
	Validators      *[]common.Address `json:"validators"`
	Executor        *common.Address   `json:"executor"`
	Buys            *[]Buy            `json:"buys"`
}

// The states of a round's reward (R-12).
const (
	stateWaiting   = "waiting"   // before its first claimable round
	stateClaimable = "claimable" // in its window and not claimed
	stateClaimed   = "claimed"
	stateExpired   = "expired" // its window passed unclaimed: never minted
)

// rewardState is where round n's reward stands in round now, with d the
// reward delay in force for n: claimable in rounds n + d to n + d + 21
// (R-12), from and until.
func rewardState(n, now, d uint64, claimed bool) (state string, from, until uint64) {
	from, until = n+d, n+d+claimWindow
	switch {
	case claimed:
		return stateClaimed, from, until
	case now < from:
		return stateWaiting, from, until
	case now <= until:
		return stateClaimable, from, until
	}
	return stateExpired, from, until
}

// config is what the explorer is given.
type config struct {
	controller, params, ledger, claims, usdc, node common.Address
	first                                          uint64
}

// explorer reads a Status on each request. It keeps only the three
// addresses the Controller names, which never change, and the log scan.
type explorer struct {
	cfg  config
	rpc  batcher
	scan *scanner

	registry, clock, broker *common.Address
}

func newExplorer(cfg config, b batcher) *explorer {
	return &explorer{cfg: cfg, rpc: b, scan: newScanner(cfg.first, cfg.claims, cfg.node)}
}

// unavailable records why a figure was not read.
func (s *Status) unavailable(what string, err error) {
	s.Unavailable = append(s.Unavailable, fmt.Sprintf("%s: %v", what, err))
}

func intOut(v *view, s *Status) *Int {
	if v.err != nil {
		s.unavailable(v.what, v.err)
		return nil
	}
	return (*Int)(v.out[0].(*big.Int))
}

func addressOut(v *view, s *Status) *common.Address {
	if v.err != nil {
		s.unavailable(v.what, v.err)
		return nil
	}
	a := v.out[0].(common.Address)
	return &a
}

// firstValue is the first value of a group read with valuesAt.
func firstValue(v *view, s *Status) *big.Int {
	if v.err == nil && len(v.out[0].([]*big.Int)) == 0 {
		v.err = fmt.Errorf("no values")
	}
	if v.err != nil {
		s.unavailable(v.what, v.err)
		return nil
	}
	return v.out[0].([]*big.Int)[0]
}

func asAddress(n *big.Int) *common.Address {
	if n == nil {
		return nil
	}
	a := common.BigToAddress(n)
	return &a
}

// read reads the whole Status at the chain's newest block. Every read is
// made fresh, so a figure that failed is read again on the next request.
func (e *explorer) read(ctx context.Context) *Status {
	c := e.cfg
	s := &Status{Node: Node{Address: c.node, LowGasWei: (*Int)(lowGasWei)}, Unavailable: []string{}}

	// The newest block, and the three addresses the Controller names.
	var head hexutil.Uint64
	reqs := []rpc.BatchElem{{Method: "eth_blockNumber", Result: &head}}
	send(ctx, e.rpc, reqs)
	if reqs[0].Error != nil {
		s.unavailable("the chain's newest block", reqs[0].Error)
		return s
	}
	block := uint64(head)
	at := hexutil.EncodeUint64(block)
	s.Block = &block
	if e.registry == nil {
		named := map[string]**common.Address{"BondingManager": &e.registry, "RoundsManager": &e.clock, "TicketBroker": &e.broker}
		var vs []*view
		names := []string{"BondingManager", "RoundsManager", "TicketBroker"}
		for _, n := range names {
			vs = append(vs, &view{what: "the Controller's " + n, to: c.controller, method: "getContract", args: []any{crypto.Keccak256Hash([]byte(n))}})
		}
		callViews(ctx, e.rpc, at, vs...)
		for _, v := range vs {
			if v.err == nil && v.out[0].(common.Address) == (common.Address{}) {
				v.err = fmt.Errorf("not named")
			}
			if v.err != nil {
				s.unavailable(v.what, v.err)
				return s
			}
		}
		for i, n := range names {
			a := vs[i].out[0].(common.Address)
			*named[n] = &a
		}
	}
	registry, clock, broker := *e.registry, *e.clock, *e.broker

	// What needs no round.
	round := &view{what: "the current round", to: clock, method: "currentRound"}
	registered := &view{what: "registered", to: registry, method: "isRegistered", args: []any{c.node}}
	operator := &view{what: "operator", to: registry, method: "operatorOf", args: []any{c.node}}
	payout := &view{what: "payout address", to: registry, method: "payoutOf", args: []any{c.node}}
	scores := &view{what: "the deployment's validator set contract", to: c.params, method: "valuesAt", args: []any{big.NewInt(groupScoreSource), big.NewInt(0)}}
	callViews(ctx, e.rpc, at, round, registered, operator, payout, scores)
	var eth hexutil.Big
	var header struct {
		Time hexutil.Uint64 `json:"timestamp"`
	}
	extra := []rpc.BatchElem{
		{Method: "eth_getBalance", Args: []any{c.node, at}, Result: &eth},
		{Method: "eth_getBlockByNumber", Args: []any{at, false}, Result: &header},
	}
	send(ctx, e.rpc, extra)
	if extra[0].Error == nil {
		s.Node.ETH = (*Int)(eth.ToInt())
		low := eth.ToInt().Cmp(lowGasWei) < 0
		s.Node.LowGas = &low
	} else {
		s.unavailable("the node key's ETH", extra[0].Error)
	}
	if extra[1].Error == nil {
		t := uint64(header.Time)
		s.BlockTime = &t
	} else {
		s.unavailable("the newest block's time", extra[1].Error)
	}
	if registered.err == nil {
		b := registered.out[0].(bool)
		s.Node.Registered = &b
	} else {
		s.unavailable(registered.what, registered.err)
	}
	s.Node.Operator = addressOut(operator, s)
	s.Node.Payout = addressOut(payout, s)
	validatorSet := asAddress(firstValue(scores, s))
	if round.err != nil {
		s.unavailable(round.what, round.err)
		return s
	}
	now := round.out[0].(*big.Int).Uint64()
	s.Testnet.Round = &now
	r := new(big.Int).SetUint64(now)

	// What needs the round.
	executor := &view{what: "the buyback executor in force", to: c.params, method: "valuesAt", args: []any{big.NewInt(groupExecutor), r}}
	source := &view{what: "the score source in force", to: c.params, method: "valuesAt", args: []any{big.NewInt(groupScoreSource), r}}
	bond := &view{what: "the node's own bond this round", to: c.ledger, method: "bondAt", args: []any{c.node, c.node, r}}
	total := &view{what: "total fees this round", to: broker, method: "totalFees", args: []any{r}}
	vs := []*view{executor, source, bond, total}
	var usdc, members *view
	if s.Node.Payout != nil {
		usdc = &view{what: "test USDC at the payout address", to: c.usdc, method: "balanceOf", args: []any{*s.Node.Payout}}
		vs = append(vs, usdc)
	}
	if validatorSet != nil {
		members = &view{what: "the validator set", to: *validatorSet, method: "membersAt", args: []any{r}}
		vs = append(vs, members)
	}
	type rowViews struct{ fees, stake, delay, ent, part *view }
	var rows []rowViews
	for n := now; n >= 1 && n+recentRounds >= now; n-- {
		rn := new(big.Int).SetUint64(n)
		w := fmt.Sprintf("round %d's ", n)
		row := rowViews{
			fees:  &view{what: w + "fees", to: broker, method: "feesOf", args: []any{rn, c.node}},
			stake: &view{what: w + "stake", to: c.ledger, method: "stakeAt", args: []any{c.node, rn}},
			delay: &view{what: w + "reward delay", to: c.params, method: "valuesAt", args: []any{big.NewInt(groupRewardDelay), rn}},
			ent:   &view{what: w + "entitlement", to: c.claims, method: "entitlementOf", args: []any{c.node, rn}},
			part:  &view{what: w + "part", to: c.claims, method: "partOf", args: []any{c.node, c.node, rn}},
		}
		rows = append(rows, row)
		vs = append(vs, row.fees, row.stake, row.delay, row.ent, row.part)
	}
	callViews(ctx, e.rpc, at, vs...)
	s.Testnet.Executor = asAddress(firstValue(executor, s))
	scoreSource := asAddress(firstValue(source, s))
	s.Node.Bond = intOut(bond, s)
	s.Testnet.TotalFees = intOut(total, s)
	if usdc != nil {
		s.Node.PayoutUSDC = intOut(usdc, s)
	}
	if members != nil {
		if members.err == nil {
			m := members.out[0].([]common.Address)
			s.Testnet.Validators = &m
		} else {
			s.unavailable(members.what, members.err)
		}
	}
	for i, row := range rows {
		out := Round{Round: now - uint64(i), Fees: intOut(row.fees, s), Stake: intOut(row.stake, s)}
		if row.ent.err == nil {
			claimed := row.ent.out[0].(bool)
			out.Claimed = &claimed
			out.Paid, out.Pot = (*Int)(row.ent.out[1].(*big.Int)), (*Int)(row.ent.out[2].(*big.Int))
		} else {
			s.unavailable(row.ent.what, row.ent.err)
		}
		if row.part.err == nil {
			withdrawn := row.part.out[1].(bool)
			out.Part, out.PartWithdrawn = (*Int)(row.part.out[0].(*big.Int)), &withdrawn
		} else {
			s.unavailable(row.part.what, row.part.err)
		}
		if d := firstValue(row.delay, s); d != nil && out.Claimed != nil {
			state, from, until := rewardState(out.Round, now, d.Uint64(), *out.Claimed)
			out.State, out.ClaimableFrom, out.ClaimableUntil = &state, &from, &until
		}
		s.Node.Rounds = append(s.Node.Rounds, out)
	}
	// The first row is the current round, whose stake is the node's stake
	// this round. Rounds start at 1, so a round 0 is a clock that is not one.
	if len(rows) > 0 {
		s.Node.Stake = s.Node.Rounds[0].Stake
	} else {
		s.unavailable("the node's stake this round", fmt.Errorf("the current round is %d", now))
	}

	// The score a claim made now reads, from the score source in force.
	if scoreSource != nil {
		score := &view{what: "the node's score", to: *scoreSource, method: "scoreOf", args: []any{c.node}}
		callViews(ctx, e.rpc, at, score)
		s.Node.ScoreNow = intOut(score, s)
	}

	// The round's start, the buys and the node's claims, from the clock's,
	// the executor's and Claims' events, read up to this block.
	if s.Testnet.Executor == nil {
		return s
	}
	if err := e.scan.advance(ctx, e.rpc, block, clock, *s.Testnet.Executor); err != nil {
		s.unavailable("the buys, the round's start and the scores the node's claims applied", err)
		return s
	}
	for i, row := range s.Node.Rounds {
		if row.Claimed == nil || !*row.Claimed {
			continue
		}
		if score, ok := e.scan.claimed[row.Round]; ok {
			s.Node.Rounds[i].ClaimScore = (*Int)(score)
		} else {
			s.unavailable(fmt.Sprintf("round %d's claim score", row.Round), fmt.Errorf("no Claimed event for the node from block %d", c.first))
		}
	}
	buys := e.scan.recent()
	s.Testnet.Buys = &buys
	if start := e.scan.start; start.round == now && start.block != 0 {
		s.Testnet.RoundStartBlock, s.Testnet.RoundStartTime = &start.block, &start.time
	} else {
		s.unavailable("the round's start", fmt.Errorf("no RoundStarted event for round %d from block %d", now, c.first))
	}
	return s
}
