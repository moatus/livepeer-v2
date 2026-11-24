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
	"github.com/ethereum/go-ethereum/rpc"

	"lp2core/programs/chain"
)

// The views the explorer reads, as contracts/interfaces declares them. A
// test holds each to its declaration there.
const viewsABI = `[
 {"type":"function","name":"getContract","stateMutability":"view","inputs":[{"name":"id","type":"bytes32"}],"outputs":[{"name":"","type":"address"}]},
 {"type":"function","name":"currentRound","stateMutability":"view","inputs":[],"outputs":[{"name":"","type":"uint256"}]},
 {"type":"function","name":"roundLength","stateMutability":"view","inputs":[],"outputs":[{"name":"","type":"uint256"}]},
 {"type":"function","name":"operatorOf","stateMutability":"view","inputs":[{"name":"node","type":"address"}],"outputs":[{"name":"","type":"address"}]},
 {"type":"function","name":"payoutOf","stateMutability":"view","inputs":[{"name":"node","type":"address"}],"outputs":[{"name":"","type":"address"}]},
 {"type":"function","name":"valuesAt","stateMutability":"view","inputs":[{"name":"group","type":"uint256"},{"name":"round","type":"uint256"}],"outputs":[{"name":"","type":"uint256[]"}]},
 {"type":"function","name":"stakeAt","stateMutability":"view","inputs":[{"name":"node","type":"address"},{"name":"round","type":"uint256"}],"outputs":[{"name":"","type":"uint256"}]},
 {"type":"function","name":"totalStakeAt","stateMutability":"view","inputs":[{"name":"round","type":"uint256"}],"outputs":[{"name":"","type":"uint256"}]},
 {"type":"function","name":"cutAt","stateMutability":"view","inputs":[{"name":"node","type":"address"},{"name":"round","type":"uint256"}],"outputs":[{"name":"","type":"uint256"}]},
 {"type":"function","name":"bondAt","stateMutability":"view","inputs":[{"name":"holder","type":"address"},{"name":"node","type":"address"},{"name":"round","type":"uint256"}],"outputs":[{"name":"","type":"uint256"}]},
 {"type":"function","name":"networkHeld","stateMutability":"view","inputs":[],"outputs":[{"name":"","type":"uint256"}]},
 {"type":"function","name":"earmarkOf","stateMutability":"view","inputs":[{"name":"payer","type":"address"},{"name":"node","type":"address"}],"outputs":[{"name":"amount","type":"uint256"},{"name":"pending","type":"uint256"},{"name":"readyRound","type":"uint256"}]},
 {"type":"function","name":"entitlementOf","stateMutability":"view","inputs":[{"name":"node","type":"address"},{"name":"round","type":"uint256"}],"outputs":[{"name":"claimed","type":"bool"},{"name":"paid","type":"uint256"},{"name":"pot","type":"uint256"}]},
 {"type":"function","name":"partOf","stateMutability":"view","inputs":[{"name":"holder","type":"address"},{"name":"node","type":"address"},{"name":"round","type":"uint256"}],"outputs":[{"name":"amount","type":"uint256"},{"name":"withdrawn","type":"bool"}]},
 {"type":"function","name":"scoreOf","stateMutability":"view","inputs":[{"name":"node","type":"address"}],"outputs":[{"name":"","type":"uint256"}]},
 {"type":"function","name":"membersAt","stateMutability":"view","inputs":[{"name":"round","type":"uint256"}],"outputs":[{"name":"","type":"address[]"}]},
 {"type":"function","name":"balanceOf","stateMutability":"view","inputs":[{"name":"account","type":"address"}],"outputs":[{"name":"","type":"uint256"}]},
 {"type":"function","name":"totalSupply","stateMutability":"view","inputs":[],"outputs":[{"name":"","type":"uint256"}]}
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

// Int is a whole number read from the chain. JSON carries it as a decimal
// string, since wei amounts exceed what a float holds exactly.
type Int big.Int

func (n *Int) MarshalJSON() ([]byte, error) {
	return json.Marshal((*big.Int)(n).String())
}

func (n *Int) big() *big.Int { return (*big.Int)(n) }

func newInt(n *big.Int) *Int {
	if n == nil {
		return nil
	}
	return (*Int)(new(big.Int).Set(n))
}

// view is one contract view read at one block: what it is, for a message
// when it fails, the call, and its decoded outputs or its error. A view
// whose answer is final (keep says so of its outputs) is read once and
// kept: one a round fixes, or a claim's part once withdrawn.
type view struct {
	what   string
	to     common.Address
	method string
	args   []any
	keep   func(out []any) bool
	out    []any
	err    error
}

func (v *view) key() string {
	data, err := views.Pack(v.method, v.args...)
	if err != nil {
		panic(err) // a call the code itself built wrongly
	}
	return v.to.Hex() + hexutil.Encode(data)
}

// always keeps a view's answer: it cannot change.
func always([]any) bool { return true }

// at names a block by its hash, so a view read at a block the chain no
// longer has fails rather than answering from another branch (EIP-1898).
func at(hash common.Hash) map[string]any {
	return map[string]any{"blockHash": hash, "requireCanonical": true}
}

// call reads every view at the block, each kept view from what was kept.
func (e *explorer) call(ctx context.Context, block any, vs ...*view) {
	var ask []*view
	for _, v := range vs {
		if out, ok := e.kept[v.key()]; ok {
			v.out, v.err = out, nil
			continue
		}
		ask = append(ask, v)
	}
	reqs := make([]rpc.BatchElem, len(ask))
	results := make([]hexutil.Bytes, len(ask))
	for i, v := range ask {
		data, _ := views.Pack(v.method, v.args...)
		reqs[i] = rpc.BatchElem{Method: "eth_call", Args: []any{
			map[string]any{"to": v.to, "data": hexutil.Bytes(data)}, block}, Result: &results[i]}
	}
	e.src.Send(ctx, "the views", reqs)
	for i, v := range ask {
		if reqs[i].Error != nil {
			v.err = reqs[i].Error
			continue
		}
		// A call to an address with no code answers 0x, which unpacks as
		// an error: unavailable, never zero.
		v.out, v.err = views.Unpack(v.method, results[i])
		if v.err == nil && v.keep != nil && v.keep(v.out) {
			e.kept[v.key()] = v.out
		}
	}
}

// Figures read and not: each figure that failed is nil, its reason added.
type reasons struct {
	list   []string
	rpcURL string
}

func (r *reasons) add(what string, err error) {
	r.list = append(r.list, what+": "+chain.Describe(err, r.rpcURL))
}

func (r *reasons) int(v *view) *Int {
	if v.err != nil {
		r.add(v.what, v.err)
		return nil
	}
	return newInt(v.out[0].(*big.Int))
}

func (r *reasons) address(v *view) *common.Address {
	if v.err != nil {
		r.add(v.what, v.err)
		return nil
	}
	a := v.out[0].(common.Address)
	return &a
}

// first is the first value of a group read with valuesAt.
func (r *reasons) first(v *view) *big.Int {
	if v.err == nil && len(v.out[0].([]*big.Int)) == 0 {
		v.err = fmt.Errorf("no values")
	}
	if v.err != nil {
		r.add(v.what, v.err)
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
