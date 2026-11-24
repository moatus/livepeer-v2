package main

import (
	"context"
	"errors"
	"fmt"
	"math/big"
	"slices"

	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/common/hexutil"
	"github.com/ethereum/go-ethereum/rpc"

	"lp2core/programs/index"
)

// lowGasWei is the node key's ETH balance under which the page warns:
// 0.001 ETH, about what one public run of the team's node paid in fees.
var lowGasWei = big.NewInt(1_000_000_000_000_000)

// keepBuys is how many of the latest buys a node's page shows.
const keepBuys = 5

// snapshot is every document at one block, the index's last checkpoint:
// views read at that block by its hash, sums from the index through it.
type snapshot struct {
	block, time   uint64
	unavailable   []string
	network       NetworkDoc
	orchestrators []NodeRow
	nodes         map[common.Address]*NodeDoc
	apps          []AppRow
	payers        map[common.Address]*AppDoc
}

// errLeft is a snapshot whose block left the chain while it was read: it
// is not published.
var errLeft = errors.New("the block left the chain while it was read")

// nodeViews are the views read for one node.
type nodeViews struct {
	operator, payout, stake, cut, bond, score, usdc *view
	eth                                             hexutil.Big
	ethReq                                          *rpc.BatchElem
	rows                                            []rowViews
}

type rowViews struct {
	round uint64
	stake *view
	part  *view // only for a claimed round
	// ent is the round's entitlement, read only for a round with no Claimed
	// event that a claim could have executed in: the index missing the
	// event never makes a claimed round unclaimed.
	ent *view
}

// read reads the snapshot at the checkpoint (through, hash).
func (e *explorer) read(ctx context.Context, through uint64, hash common.Hash) (*snapshot, error) {
	c, t := e.contracts, e.ix.Totals()
	r := &reasons{list: []string{}, rpcURL: e.cfg.rpcURL}
	blk := at(hash)

	var hdr struct {
		Hash common.Hash    `json:"hash"`
		Time hexutil.Uint64 `json:"timestamp"`
	}
	reqs := []rpc.BatchElem{{Method: "eth_getBlockByHash", Args: []any{hash, false}, Result: &hdr}}
	e.src.Send(ctx, "the snapshot's block", reqs)
	if reqs[0].Error != nil {
		return nil, fmt.Errorf("block %d: %w", through, reqs[0].Error)
	}
	if hdr.Hash != hash {
		return nil, errLeft
	}
	s := &snapshot{block: through, time: uint64(hdr.Time), nodes: map[common.Address]*NodeDoc{}, payers: map[common.Address]*AppDoc{}}

	// What needs no round.
	round := &view{what: "the current round", to: c.Clock, method: "currentRound"}
	length := &view{what: "the round length", to: c.Clock, method: "roundLength", keep: always}
	held := &view{what: "the network share held", to: c.Broker, method: "networkHeld"}
	validatorSet := &view{what: "the deployment's validator set contract", to: e.cfg.params, method: "valuesAt",
		args: []any{big.NewInt(groupScoreSource), big.NewInt(0)}, keep: always}
	vs := []*view{round, length, held, validatorSet}
	var supply *view
	if e.cfg.lpt != nil {
		supply = &view{what: "LPT's supply", to: *e.cfg.lpt, method: "totalSupply"}
		vs = append(vs, supply)
	}
	e.call(ctx, blk, vs...)
	if round.err != nil {
		return nil, fmt.Errorf("%s: %w", round.what, round.err)
	}
	now := round.out[0].(*big.Int).Uint64()
	rn := new(big.Int).SetUint64(now)
	net := &s.network
	net.Round = &now
	if v := r.int(length); v != nil {
		l := v.big().Uint64()
		net.RoundLength = &l
	}
	net.NetworkHeld = r.int(held)
	if supply != nil {
		net.LPTSupply = r.int(supply)
	} else {
		r.add("LPT's supply", errors.New("no --lpt-address given"))
	}
	setAddress := asAddress(r.first(validatorSet))

	// What needs the round.
	executor := &view{what: "the buyback executor in force", to: e.cfg.params, method: "valuesAt",
		args: []any{big.NewInt(groupExecutor), rn}, keep: always}
	source := &view{what: "the score source in force", to: e.cfg.params, method: "valuesAt",
		args: []any{big.NewInt(groupScoreSource), rn}, keep: always}
	totalStake := &view{what: "the total stake this round", to: e.cfg.ledger, method: "totalStakeAt", args: []any{rn}, keep: always}
	vs = []*view{executor, source, totalStake}
	delays := map[uint64]*view{}
	delay := func(n uint64) *view {
		if v, ok := delays[n]; ok {
			return v
		}
		v := &view{what: fmt.Sprintf("round %d's reward delay", n), to: e.cfg.params, method: "valuesAt",
			args: []any{big.NewInt(groupRewardDelay), new(big.Int).SetUint64(n)}, keep: always}
		delays[n] = v
		vs = append(vs, v)
		return v
	}
	inForce := map[uint64]*view{} // the executor named for each round with a buy
	for _, b := range t.Buys {
		if _, ok := inForce[b.Round]; !ok {
			v := &view{what: fmt.Sprintf("round %d's buyback executor", b.Round), to: e.cfg.params, method: "valuesAt",
				args: []any{big.NewInt(groupExecutor), new(big.Int).SetUint64(b.Round)}, keep: always}
			inForce[b.Round] = v
			vs = append(vs, v)
		}
	}
	nodes := slices.Clone(t.Nodes)
	if e.cfg.node != nil && !slices.Contains(nodes, *e.cfg.node) {
		nodes = append(nodes, *e.cfg.node)
	}
	nv := map[common.Address]*nodeViews{}
	var eths []rpc.BatchElem
	for _, n := range nodes {
		w := &nodeViews{
			operator: &view{what: "the operator of " + n.Hex(), to: c.Registry, method: "operatorOf", args: []any{n}},
			payout:   &view{what: "the payout address of " + n.Hex(), to: c.Registry, method: "payoutOf", args: []any{n}},
			stake:    &view{what: "the stake this round of " + n.Hex(), to: e.cfg.ledger, method: "stakeAt", args: []any{n, rn}, keep: always},
			cut:      &view{what: "the reward cut this round of " + n.Hex(), to: e.cfg.ledger, method: "cutAt", args: []any{n, rn}, keep: always},
			bond:     &view{what: "the own bond this round of " + n.Hex(), to: e.cfg.ledger, method: "bondAt", args: []any{n, n, rn}, keep: always},
		}
		vs = append(vs, w.operator, w.payout, w.stake, w.cut, w.bond)
		for k := now; k >= 1 && k+recentRounds >= now; k-- {
			kn := new(big.Int).SetUint64(k)
			row := rowViews{round: k, stake: &view{what: fmt.Sprintf("round %d's stake of %s", k, n.Hex()), to: e.cfg.ledger,
				method: "stakeAt", args: []any{n, kn}, keep: always}}
			vs = append(vs, row.stake)
			delay(k)
			if _, ok := t.Claims[n][k]; ok {
				row.part = partView(c.Claims, n, k)
				vs = append(vs, row.part)
			}
			w.rows = append(w.rows, row)
		}
		eths = append(eths, rpc.BatchElem{Method: "eth_getBalance", Args: []any{n, blk}, Result: &w.eth})
		nv[n] = w
	}
	deposits := map[common.Address]*view{}
	for _, p := range t.Payers {
		deposits[p] = &view{what: "the deposit of " + p.Hex(), to: c.Broker, method: "earmarkOf", args: []any{p, common.Address{}}}
		vs = append(vs, deposits[p])
	}
	e.call(ctx, blk, vs...)
	e.src.Send(ctx, "the nodes' ETH", eths)
	for i, n := range nodes {
		nv[n].ethReq = &eths[i]
	}
	start, started := t.Starts[now]
	var startTime *uint64
	if started {
		if tm, err := e.blockTime(ctx, start); err == nil {
			startTime = &tm
		} else {
			r.add(fmt.Sprintf("the time of block %d, where round %d started", start, now), err)
		}
		net.RoundStartBlock, net.RoundStartTime = &start, startTime
	} else {
		r.add("the round's start", fmt.Errorf("no RoundStarted event for round %d from block %d", now, e.cfg.first))
	}

	// What needs the score source, the validator set and the payouts.
	sourceAddr := asAddress(r.first(source))
	vs = nil
	var members *view
	if setAddress != nil {
		members = &view{what: "the validator set", to: *setAddress, method: "membersAt", args: []any{rn}}
		vs = append(vs, members)
	}
	for _, n := range nodes {
		w := nv[n]
		if sourceAddr != nil {
			w.score = &view{what: "the score of " + n.Hex(), to: *sourceAddr, method: "scoreOf", args: []any{n}}
			vs = append(vs, w.score)
		}
		if w.payout.err == nil {
			w.usdc = &view{what: "test USDC at the payout address of " + n.Hex(), to: e.cfg.usdc, method: "balanceOf",
				args: []any{w.payout.out[0].(common.Address)}}
			vs = append(vs, w.usdc)
		}
	}
	for _, n := range nodes {
		for i := range nv[n].rows {
			row := &nv[n].rows[i]
			if _, ok := t.Claims[n][row.round]; ok {
				continue
			}
			expired := false
			if d := delays[row.round]; d.err == nil && len(d.out[0].([]*big.Int)) > 0 {
				from := row.round + d.out[0].([]*big.Int)[0].Uint64()
				if now < from {
					continue // waiting: no claim of it can have executed
				}
				expired = now > from+claimWindow // no claim of it can execute any more
			}
			row.ent = &view{what: fmt.Sprintf("round %d's entitlement of %s", row.round, n.Hex()), to: c.Claims,
				method: "entitlementOf", args: []any{n, new(big.Int).SetUint64(row.round)},
				keep: func(out []any) bool { return out[0].(bool) || expired }}
			vs = append(vs, row.ent)
		}
	}
	e.call(ctx, blk, vs...)

	// A round Claims says is claimed, with no Claimed event in the index:
	// its part, which the first read did not ask for.
	vs = nil
	for _, n := range nodes {
		for i := range nv[n].rows {
			row := &nv[n].rows[i]
			if row.ent != nil && row.ent.err == nil && row.ent.out[0].(bool) {
				row.part = partView(c.Claims, n, row.round)
				vs = append(vs, row.part)
			}
		}
	}
	if len(vs) > 0 {
		e.call(ctx, blk, vs...)
	}

	// The snapshot's block is still the chain's: nothing read is from a
	// branch the chain left.
	if hs, err := e.hashOf(ctx, through); err != nil {
		return nil, err
	} else if hs != hash {
		return nil, errLeft
	}

	// The network.
	net.Executor = asAddress(r.first(executor))
	net.TotalStake = r.int(totalStake)
	var validators *[]common.Address
	if members != nil {
		if members.err == nil {
			m := members.out[0].([]common.Address)
			validators = &m
			named := make([]Named, len(m))
			for i, a := range m {
				named[i] = e.labels.named(a)
			}
			net.Validators = &named
		} else {
			r.add(members.what, members.err)
		}
	}
	var buys []Buy
	for _, b := range t.Buys {
		v := inForce[b.Round]
		named := asAddress(r.first(v))
		if named == nil || *named != b.Executor {
			continue // not the executor in force for its round
		}
		buys = append(buys, Buy{Round: b.Round, Block: b.Block, Tx: b.Tx, Executor: b.Executor,
			USDCIn: newInt(b.USDCIn), LPTBought: newInt(b.LPTBought), Burned: newInt(b.Burned)})
	}
	if buys == nil {
		buys = []Buy{}
	}
	net.Buys = &buys
	fees := []RoundFlow{}
	for k := range t.Fees {
		fees = append(fees, RoundFlow{Round: k, Flow: sumFlows(t.Fees[k])})
	}
	slices.SortFunc(fees, func(a, b RoundFlow) int { return int(a.Round) - int(b.Round) })
	net.FeesByRound = &fees
	net.TotalFees = feesOf(t, now, nil)
	latest := make([]Buy, 0, keepBuys)
	for i := len(buys) - 1; i >= 0 && len(latest) < keepBuys; i-- {
		latest = append(latest, buys[i])
	}
	testnet := Testnet{Round: &now, RoundStartBlock: net.RoundStartBlock, RoundStartTime: startTime,
		TotalFees: net.TotalFees, Validators: validators, Executor: net.Executor, Buys: &latest}

	// The nodes.
	for _, n := range nodes {
		w := nv[n]
		_, registered := t.Registered[n]
		node := Node{Address: n, Registered: &registered, Operator: r.address(w.operator), Payout: r.address(w.payout),
			Bond: r.int(w.bond), Stake: r.int(w.stake), Cut: r.int(w.cut), LowGasWei: newInt(lowGasWei)}
		if w.score != nil {
			node.ScoreNow = r.int(w.score)
		}
		if w.usdc != nil {
			node.PayoutUSDC = r.int(w.usdc)
		}
		if w.ethReq.Error == nil {
			node.ETH = newInt(w.eth.ToInt())
			low := w.eth.ToInt().Cmp(lowGasWei) < 0
			node.LowGas = &low
		} else {
			r.add("the ETH of "+n.Hex(), w.ethReq.Error)
		}
		for _, row := range w.rows {
			out := Round{Round: row.round, Fees: feesOf(t, row.round, &n), Stake: r.int(row.stake)}
			claim, claimed := t.Claims[n][row.round]
			known := true
			switch {
			case claimed:
				out.ClaimScore, out.Paid, out.Pot = newInt(claim.Score), newInt(claim.Paid), newInt(claim.Pot)
			case row.ent == nil:
				out.Paid, out.Pot = newInt(new(big.Int)), newInt(new(big.Int))
			case row.ent.err != nil:
				known = false
				r.add(row.ent.what, row.ent.err)
			case row.ent.out[0].(bool):
				// Claims says claimed, and the index has no Claimed event
				// for it: what it paid is the view's, its score is unknown.
				claimed = true
				out.Paid, out.Pot = newInt(row.ent.out[1].(*big.Int)), newInt(row.ent.out[2].(*big.Int))
				r.add(fmt.Sprintf("round %d's claim score of %s", row.round, n.Hex()),
					fmt.Errorf("no Claimed event for the node from block %d", e.cfg.first))
			default:
				out.Paid, out.Pot = newInt(new(big.Int)), newInt(new(big.Int))
			}
			if known {
				out.Claimed = &claimed
			}
			if claimed {
				if row.part.err == nil {
					withdrawn := row.part.out[1].(bool)
					out.Part, out.PartWithdrawn = newInt(row.part.out[0].(*big.Int)), &withdrawn
				} else {
					r.add(row.part.what, row.part.err)
				}
			}
			if d := r.first(delays[row.round]); d != nil {
				state, from, until := rewardState(row.round, now, d.Uint64(), claimed)
				out.ClaimableFrom, out.ClaimableUntil = &from, &until
				if known {
					out.State = &state
				}
			}
			node.Rounds = append(node.Rounds, out)
		}
		named := e.labels.named(n)
		doc := &NodeDoc{Label: named.Label, Simulated: named.Simulated, Node: node, Testnet: testnet}
		byPayer, byRound, claims := []NamedFlow{}, []RoundFlow{}, []ClaimRow{}
		for _, p := range t.Payers {
			if f, ok := t.PayerOf[p].ByNode[n]; ok {
				byPayer = append(byPayer, NamedFlow{Named: e.labels.named(p), Flow: flow(f)})
			}
		}
		for k, m := range t.Fees {
			if f, ok := m[n]; ok {
				byRound = append(byRound, RoundFlow{Round: k, Flow: flow(f)})
			}
		}
		slices.SortFunc(byRound, func(a, b RoundFlow) int { return int(a.Round) - int(b.Round) })
		paid := new(big.Int)
		for _, cl := range t.Claims[n] {
			claims = append(claims, ClaimRow{Round: cl.Round, Score: newInt(cl.Score), Paid: newInt(cl.Paid), Cut: newInt(cl.Cut),
				Pot: newInt(cl.Pot), Block: cl.Block, Tx: cl.Tx})
			paid.Add(paid, cl.Paid)
		}
		slices.SortFunc(claims, func(a, b ClaimRow) int { return int(a.Round) - int(b.Round) })
		doc.FeesByPayer, doc.FeesByRound, doc.Claims = &byPayer, &byRound, &claims
		s.nodes[n] = doc
		if !registered {
			continue
		}
		reg := t.Registered[n]
		var all Flow
		if f, ok := t.NodeFees[n]; ok {
			all = flow(f)
		} else {
			all = flow(nil)
		}
		s.orchestrators = append(s.orchestrators, NodeRow{Named: named, RegisteredRound: &reg, Stake: node.Stake, Cut: node.Cut,
			ScoreNow: node.ScoreNow, FeesThisRound: feesOf(t, now, &n), Flow: all, Claims: len(claims), LPTPaid: newInt(paid)})
	}
	if s.orchestrators == nil {
		s.orchestrators = []NodeRow{}
	}

	// The payers.
	for _, p := range t.Payers {
		pt := t.PayerOf[p]
		row := AppRow{Named: e.labels.named(p), Funded: newInt(pt.Funded), Released: newInt(pt.Released), Flow: flow(pt.Fees)}
		if v := deposits[p]; v.err == nil {
			row.Deposit, row.DepositPending = newInt(v.out[0].(*big.Int)), newInt(v.out[1].(*big.Int))
		} else {
			r.add(v.what, v.err)
		}
		doc := &AppDoc{AppRow: row, FeesByRound: []RoundFlow{}, ByNode: []NamedFlow{}}
		for k, f := range pt.ByRound {
			doc.FeesByRound = append(doc.FeesByRound, RoundFlow{Round: k, Flow: flow(f)})
		}
		slices.SortFunc(doc.FeesByRound, func(a, b RoundFlow) int { return int(a.Round) - int(b.Round) })
		for _, n := range t.Nodes {
			if f, ok := pt.ByNode[n]; ok {
				doc.ByNode = append(doc.ByNode, NamedFlow{Named: e.labels.named(n), Flow: flow(f)})
			}
		}
		s.apps = append(s.apps, row)
		s.payers[p] = doc
	}
	if s.apps == nil {
		s.apps = []AppRow{}
	}
	s.unavailable = r.list
	return s, nil
}

// flow is the index's sums as figures; none is zero.
func flow(f *index.Flow) Flow {
	if f == nil {
		return Flow{newInt(new(big.Int)), newInt(new(big.Int)), newInt(new(big.Int))}
	}
	return Flow{newInt(f.Gross), newInt(f.Node), newInt(f.Network)}
}

func sumFlows(m map[common.Address]*index.Flow) Flow {
	sum := &index.Flow{Gross: new(big.Int), Node: new(big.Int), Network: new(big.Int)}
	for _, f := range m {
		sum.Gross.Add(sum.Gross, f.Gross)
		sum.Node.Add(sum.Node, f.Node)
		sum.Network.Add(sum.Network, f.Network)
	}
	return flow(sum)
}

// feesOf is a round's fees, the sum of FeeSettled.gross: a node's, or with
// node nil every node's.
func feesOf(t *index.Totals, round uint64, node *common.Address) *Int {
	sum := new(big.Int)
	for n, f := range t.Fees[round] {
		if node == nil || n == *node {
			sum.Add(sum, f.Gross)
		}
	}
	return newInt(sum)
}

// blockTime is a block's time, read once.
func (e *explorer) blockTime(ctx context.Context, n uint64) (uint64, error) {
	if tm, ok := e.times[n]; ok {
		return tm, nil
	}
	var h struct {
		Time hexutil.Uint64 `json:"timestamp"`
	}
	req := []rpc.BatchElem{{Method: "eth_getBlockByNumber", Args: []any{hexutil.EncodeUint64(n), false}, Result: &h}}
	e.src.Send(ctx, "a block's time", req)
	if req[0].Error != nil {
		return 0, req[0].Error
	}
	e.times[n] = uint64(h.Time)
	return uint64(h.Time), nil
}

// hashOf is the hash the chain has at block n.
func (e *explorer) hashOf(ctx context.Context, n uint64) (common.Hash, error) {
	var h struct {
		Hash common.Hash `json:"hash"`
	}
	req := []rpc.BatchElem{{Method: "eth_getBlockByNumber", Args: []any{hexutil.EncodeUint64(n), false}, Result: &h}}
	e.src.Send(ctx, "the snapshot's block", req)
	if req[0].Error != nil {
		return common.Hash{}, fmt.Errorf("block %d: %w", n, req[0].Error)
	}
	return h.Hash, nil
}

// partView is node's own part of its round's pot, kept once withdrawn.
func partView(claims, node common.Address, round uint64) *view {
	return &view{what: fmt.Sprintf("round %d's part of %s", round, node.Hex()), to: claims, method: "partOf",
		args: []any{node, node, new(big.Int).SetUint64(round)}, keep: func(out []any) bool { return out[1].(bool) }}
}
