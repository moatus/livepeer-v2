package main

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"html"
	"math/big"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/common/hexutil"
	"github.com/ethereum/go-ethereum/core/types"
	"github.com/ethereum/go-ethereum/crypto"
	"github.com/ethereum/go-ethereum/rpc"

	"lp2core/programs/chain"
	"lp2core/programs/index"
)

var (
	controllerAt = common.HexToAddress("0xc0")
	registryAt   = common.HexToAddress("0xc1")
	clockAt      = common.HexToAddress("0xc2")
	brokerAt     = common.HexToAddress("0xc3")
	paramsAt     = common.HexToAddress("0xc4")
	ledgerAt     = common.HexToAddress("0xc5")
	claimsAt     = common.HexToAddress("0xc6")
	usdcAt       = common.HexToAddress("0xc7")
	scoresAt     = common.HexToAddress("0xc8")
	executorAt   = common.HexToAddress("0xc9")
	lptAt        = common.HexToAddress("0xca")
	nodeAt       = common.HexToAddress("0xa1")
	payoutAt     = common.HexToAddress("0xa2")
	validatorAt  = common.HexToAddress("0xa3")
	payerAt      = common.HexToAddress("0xb1")
)

var testConfig = config{controller: controllerAt, params: paramsAt, ledger: ledgerAt, claims: claimsAt,
	usdc: usdcAt, lpt: &lptAt, node: &nodeAt, first: 100}

// fakeChain answers the JSON-RPC requests the explorer makes, from a
// deployment where node 0xa1 earned in round 2 and claimed it, in round
// 12. down makes every request fail, as an endpoint that does not answer;
// revert makes every call to the contracts it names fail.
type fakeChain struct {
	mu       sync.Mutex
	head     uint64
	round    uint64
	chainID  uint64
	executor func(round uint64) common.Address // valuesAt(10, round)
	score    map[common.Address]int64
	deposit  [2]int64 // earmarkOf(payer, 0): the amount and its pending release
	logs     []types.Log
	down     bool
	revert   map[common.Address]bool // calls to these contracts fail
	requests int                     // request elements answered
	// claimedOnly is a round each node's entitlement says is claimed, with
	// no Claimed event among the logs.
	claimedOnly map[common.Address]uint64
	stake       string // every stakeAt
	// Blocks from fork on have hashes salted by branch: a new branch is a
	// reorganisation from fork.
	fork, branch uint64
}

func newFake() *fakeChain {
	return &fakeChain{head: 1000, round: 12, chainID: 1337, executor: func(uint64) common.Address { return executorAt },
		stake: "3200000000000000000000", fork: 1 << 31,
		score: map[common.Address]int64{nodeAt: 500_000}, deposit: [2]int64{3_000_000, 0}, revert: map[common.Address]bool{}}
}

var errDown = errors.New("connection refused")

func (f *fakeChain) BatchCallContext(_ context.Context, b []rpc.BatchElem) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.down {
		return errDown
	}
	for i := range b {
		f.requests++
		v, err := f.answer(b[i].Method, b[i].Args)
		if err != nil {
			b[i].Error = err
			continue
		}
		raw, _ := json.Marshal(v)
		if err := json.Unmarshal(raw, b[i].Result); err != nil {
			panic(err)
		}
	}
	return nil
}

// hashOf is block n's hash on the fake's chain: its number, and the
// branch for a block from fork on.
func (f *fakeChain) hashOf(n uint64) common.Hash {
	v := new(big.Int).SetUint64(0xb10c<<32 + n)
	if n >= f.fork {
		v.Or(v, new(big.Int).Lsh(new(big.Int).SetUint64(f.branch), 64))
	}
	return common.BigToHash(v)
}

func (f *fakeChain) block(n uint64) any {
	if n > f.head {
		return nil
	}
	return map[string]any{"number": hexutil.Uint64(n), "hash": f.hashOf(n), "timestamp": hexutil.Uint64(1_795_000_000 + n)}
}

func (f *fakeChain) answer(method string, args []any) (any, error) {
	switch method {
	case "eth_chainId":
		return hexutil.Uint64(f.chainID), nil
	case "eth_getBalance":
		return hexutil.Big(*big.NewInt(2_000_000_000_000_000)), nil
	case "eth_getBlockByNumber":
		if args[0] == "latest" {
			return f.block(f.head), nil
		}
		n, _ := hexutil.DecodeUint64(args[0].(string))
		return f.block(n), nil
	case "eth_getBlockByHash":
		h := args[0].(common.Hash)
		n := new(big.Int).SetBytes(h[24:]).Uint64() - 0xb10c<<32
		if f.hashOf(n) != h {
			return nil, nil
		}
		return f.block(n), nil
	case "eth_getLogs":
		q := args[0].(map[string]any)
		from, _ := hexutil.DecodeUint64(q["fromBlock"].(string))
		to, _ := hexutil.DecodeUint64(q["toBlock"].(string))
		want := q["address"].([]common.Address)
		topics := q["topics"].([][]common.Hash)
		var out []types.Log
		for i, l := range f.logs {
			if l.BlockNumber < from || l.BlockNumber > to || !slices.Contains(want, l.Address) || !slices.Contains(topics[0], l.Topics[0]) {
				continue
			}
			l.BlockHash, l.Index = f.hashOf(l.BlockNumber), uint(i)
			out = append(out, l)
		}
		return out, nil
	case "eth_call":
		call := args[0].(map[string]any)
		return f.call(call["to"].(common.Address), call["data"].(hexutil.Bytes))
	}
	return nil, fmt.Errorf("the fake does not answer %s", method)
}

func n(v int64) *big.Int { return big.NewInt(v) }

func wei(s string) *big.Int { v, _ := new(big.Int).SetString(s, 10); return v }

// call answers a view as the fake deployment holds it.
func (f *fakeChain) call(to common.Address, data []byte) (any, error) {
	if f.revert[to] {
		return nil, errors.New("execution reverted")
	}
	m, err := views.MethodById(data[:4])
	if err != nil {
		return nil, err
	}
	args, err := m.Inputs.Unpack(data[4:])
	if err != nil {
		return nil, err
	}
	var out []any
	switch {
	case to == controllerAt && m.Name == "getContract":
		id := common.Hash(args[0].([32]byte))
		named := map[common.Hash]common.Address{crypto.Keccak256Hash([]byte("BondingManager")): registryAt,
			crypto.Keccak256Hash([]byte("RoundsManager")): clockAt, crypto.Keccak256Hash([]byte("TicketBroker")): brokerAt}
		out = []any{named[id]}
	case to == clockAt && m.Name == "currentRound":
		out = []any{new(big.Int).SetUint64(f.round)}
	case to == clockAt && m.Name == "roundLength":
		out = []any{n(50)}
	case to == registryAt && m.Name == "operatorOf":
		out = []any{args[0]}
	case to == registryAt && m.Name == "payoutOf":
		out = []any{payoutAt}
	case to == paramsAt && m.Name == "valuesAt":
		switch args[0].(*big.Int).Int64() {
		case groupScoreSource:
			out = []any{[]*big.Int{new(big.Int).SetBytes(scoresAt.Bytes())}}
		case groupExecutor:
			out = []any{[]*big.Int{new(big.Int).SetBytes(f.executor(args[1].(*big.Int).Uint64()).Bytes())}}
		case groupRewardDelay:
			out = []any{[]*big.Int{n(7)}}
		}
	case to == ledgerAt && m.Name == "stakeAt":
		out = []any{wei(f.stake)}
	case to == ledgerAt && m.Name == "totalStakeAt":
		out = []any{wei("9600000000000000000000")}
	case to == ledgerAt && m.Name == "cutAt":
		out = []any{n(100_000)}
	case to == ledgerAt && m.Name == "bondAt":
		out = []any{wei("1000000000000000000")}
	case to == brokerAt && m.Name == "networkHeld":
		out = []any{n(1_500_000)}
	case to == brokerAt && m.Name == "earmarkOf":
		out = []any{n(f.deposit[0]), n(f.deposit[1]), n(0)}
	case to == claimsAt && m.Name == "entitlementOf":
		out = []any{false, n(0), n(0)}
		node, r := args[0].(common.Address), args[1].(*big.Int).Uint64()
		claimed := f.claimedOnly[node] == r
		for _, l := range f.logs {
			if l.Address == claimsAt && l.Topics[0] == index.ABI.Events["Claimed"].ID &&
				common.BytesToAddress(l.Topics[1].Bytes()) == node && new(big.Int).SetBytes(l.Topics[2].Bytes()).Uint64() == r {
				claimed = true
			}
		}
		if claimed {
			out = []any{true, wei("300551889676665523465"), wei("150275944838332761733")}
		}
	case to == claimsAt && m.Name == "partOf":
		out = []any{n(0), false}
		if args[2].(*big.Int).Int64() == 2 {
			out = []any{wei("46961232761978988041"), true}
		}
	case to == usdcAt && m.Name == "balanceOf":
		out = []any{n(1_500_000)}
	case to == lptAt && m.Name == "totalSupply":
		out = []any{wei("10000000000000000000000000")}
	case to == scoresAt && m.Name == "scoreOf":
		out = []any{n(f.score[args[0].(common.Address)])}
	case to == scoresAt && m.Name == "membersAt":
		out = []any{[]common.Address{validatorAt}}
	default:
		return hexutil.Bytes{}, nil // an address with no such code answers 0x
	}
	packed, err := m.Outputs.Pack(out...)
	return hexutil.Bytes(packed), err
}

// event is a log of the named event at block b, emitted by at, its
// arguments in the order its interface declares them.
func event(at common.Address, name string, b uint64, args ...any) types.Log {
	ev := index.ABI.Events[name]
	topics := []common.Hash{ev.ID}
	var data []any
	for i, in := range ev.Inputs {
		if !in.Indexed {
			data = append(data, args[i])
			continue
		}
		switch v := args[i].(type) {
		case common.Address:
			topics = append(topics, common.BytesToHash(v.Bytes()))
		case *big.Int:
			topics = append(topics, common.BigToHash(v))
		}
	}
	packed, err := ev.Inputs.NonIndexed().Pack(data...)
	if err != nil {
		panic(err)
	}
	return types.Log{Address: at, BlockNumber: b, TxHash: common.BigToHash(new(big.Int).SetUint64(b)), Topics: topics, Data: packed}
}

func u(v uint64) *big.Int { return new(big.Int).SetUint64(v) }

// bought is a Bought event of round r at block b.
func bought(at common.Address, r, b uint64, usdcIn int64) types.Log {
	return event(at, "Bought", b, u(r), n(usdcIn), n(usdcIn), n(usdcIn*2), n(usdcIn*2), n(0), n(0))
}

// claimedLog is a Claimed event of node's claim of round r at block b, at
// the given score.
func claimedLog(node common.Address, r, b uint64, score int64, paid string) types.Log {
	return event(claimsAt, "Claimed", b, node, u(r), payoutAt, n(score), wei(paid), n(0), wei(paid))
}

// started is a RoundStarted event of round r at block b.
func started(r, b uint64) types.Log {
	return event(clockAt, "RoundStarted", b, u(r), u(b), [32]byte{})
}

func registered(node common.Address, b uint64) types.Log {
	return event(registryAt, "NodeRegistered", b, node, n(1))
}

func settled(b, r uint64, node common.Address, gross, nodeAmount, network int64) types.Log {
	return event(brokerAt, "FeeSettled", b, u(r), node, payerAt, [32]byte{byte(b)}, n(gross), n(nodeAmount), n(network), payoutAt)
}

func funded(b uint64, deposit int64, node common.Address, earmark int64) types.Log {
	return event(brokerAt, "Funded", b, payerAt, n(deposit), node, n(earmark))
}

// claimOf2 is node 0xa1's claim of round 2, in round 9, at score 1,000,000:
// the score now, 500,000, is not the one the claim applied.
var claimOf2 = claimedLog(nodeAt, 2, 600, 1_000_000, "300551889676665523465")

// deployment is the fake's events: the node registered and paid 1 USDC in
// round 2, which it claimed; another node's claim; the rounds' starts; a
// buy.
func deployment() []types.Log {
	return []types.Log{registered(nodeAt, 110), registered(validatorAt, 111), funded(120, 5_000_000, common.Address{}, 0),
		settled(150, 2, nodeAt, 1_000_000, 500_000, 500_000), claimOf2, claimedLog(validatorAt, 2, 610, 250_000, "1"),
		started(11, 900), bought(executorAt, 11, 950, 7), started(12, 990)}
}

// newTestExplorer is an explorer reading through c, with its index in a
// folder of its own.
func newTestExplorer(t *testing.T, c index.Caller, cfg config) *explorer {
	return explorerIn(t, t.TempDir(), c, cfg)
}

func explorerIn(t *testing.T, dir string, c index.Caller, cfg config) *explorer {
	t.Helper()
	ix, err := index.Open(dir, cfg.first, 5000)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(ix.Close)
	e := newExplorer(cfg, index.NewSource(c, 10_000), ix)
	e.stop = func(err error) { t.Fatalf("the explorer stopped: %v", err) }
	return e
}

func get(t *testing.T, h http.Handler, path string) string {
	t.Helper()
	w := httptest.NewRecorder()
	h.ServeHTTP(w, httptest.NewRequest("GET", path, nil))
	return w.Body.String()
}

func getJSON(t *testing.T, h http.Handler, path string) map[string]any {
	t.Helper()
	var m map[string]any
	if err := json.Unmarshal([]byte(get(t, h, path)), &m); err != nil {
		t.Fatalf("%s: %v", path, err)
	}
	return m
}

var nodePath = "/orchestrators/" + nodeAt.Hex() + ".json"

func TestReadsTheNodeAndTheTestnet(t *testing.T) {
	f := newFake()
	f.logs = deployment()
	e := newTestExplorer(t, f, testConfig)
	e.cycle(context.Background())
	s, _ := e.served().nodeDoc(nodeAt)
	if len(s.Unavailable) != 0 {
		t.Fatalf("unavailable: %v", s.Unavailable)
	}
	if *s.Block != 1000 || !*s.Node.Registered || *s.Node.Payout != payoutAt || s.Node.Stake.big().String() != "3200000000000000000000" ||
		s.Node.ScoreNow.big().Int64() != 500_000 || s.Node.PayoutUSDC.big().Int64() != 1_500_000 || *s.Node.LowGas {
		t.Errorf("node: %+v", s.Node)
	}
	// Rounds 12 down to 1: the current round and the 28 before it, none below 1.
	if len(s.Node.Rounds) != 12 || s.Node.Rounds[0].Round != 12 || s.Node.Rounds[11].Round != 1 {
		t.Fatalf("rounds: %d, first %d", len(s.Node.Rounds), s.Node.Rounds[0].Round)
	}
	// Round 2's claim applied 1,000,000, which the score now no longer is.
	r2 := s.Node.Rounds[10]
	if r2.Round != 2 || *r2.State != stateClaimed || r2.ClaimScore.big().Int64() != 1_000_000 || r2.Fees.big().Int64() != 1_000_000 ||
		r2.Paid.big().String() != "300551889676665523465" || r2.Part.big().String() != "46961232761978988041" || !*r2.PartWithdrawn {
		t.Errorf("round 2: %+v", r2)
	}
	if r5 := s.Node.Rounds[7]; r5.Round != 5 || *r5.State != stateClaimable || *r5.ClaimableUntil != 33 || r5.ClaimScore != nil {
		t.Errorf("round 5: %+v", r5)
	}
	if r12 := s.Node.Rounds[0]; *r12.State != stateWaiting || *r12.ClaimableFrom != 19 {
		t.Errorf("round 12: %+v", r12)
	}
	tn := s.Testnet
	if *tn.Round != 12 || *tn.RoundStartBlock != 990 || *tn.RoundStartTime != 1_795_000_990 || *tn.Executor != executorAt ||
		len(*tn.Validators) != 1 || len(*tn.Buys) != 1 || (*tn.Buys)[0].Burned.big().Int64() != 14 {
		t.Errorf("testnet: %+v", tn)
	}
	// Another node's claim of round 2, at another score, is not the node's.
	if c := *s.Claims; len(c) != 1 || c[0].Score.big().Int64() != 1_000_000 || (*s.FeesByPayer)[0].Address != payerAt {
		t.Errorf("claims %+v, fees by payer %+v", c, *s.FeesByPayer)
	}

	// A claimed round whose Claimed event the index has not found has no
	// claim score, and says so: never the score now, and never unclaimed.
	f = newFake()
	f.logs = slices.DeleteFunc(deployment(), func(l types.Log) bool { return l.BlockNumber == claimOf2.BlockNumber })
	f.claimedOnly = map[common.Address]uint64{nodeAt: 2}
	e = newTestExplorer(t, f, testConfig)
	e.cycle(context.Background())
	s, _ = e.served().nodeDoc(nodeAt)
	if r2 := s.Node.Rounds[10]; r2.ClaimScore != nil || len(s.Unavailable) != 1 || !strings.Contains(s.Unavailable[0], "round 2's claim score") ||
		!*r2.Claimed || *r2.State != stateClaimed || r2.Paid.big().String() != "300551889676665523465" || r2.Part.big().String() != "46961232761978988041" {
		t.Errorf("round 2 with no Claimed event: %+v, unavailable %v", r2, s.Unavailable)
	}
}

// A read that fails shows its figures as unavailable, null in the JSON and
// never 0, and the next cycle after the endpoint answers again shows them.
func TestFailedReadIsUnavailableAndRecovers(t *testing.T) {
	f := newFake()
	f.logs = deployment()
	e := newTestExplorer(t, f, testConfig)
	h := handler(e)
	node := func(m map[string]any) map[string]any { return m["node"].(map[string]any) }

	f.down = true
	e.cycle(context.Background())
	m := getJSON(t, h, nodePath)
	if m["block"] != nil || node(m)["stake_wei"] != nil || node(m)["registered"] != nil || node(m)["eth_wei"] != nil ||
		m["testnet"].(map[string]any)["buys"] != nil || len(m["unavailable"].([]any)) == 0 {
		t.Errorf("with the endpoint down: %v", m)
	}
	if page := get(t, h, "/"); !strings.Contains(page, `<span class="v">not read yet</span>`) || !strings.Contains(page, errDown.Error()) {
		t.Errorf("the page does not show the figures unavailable:\n%s", page)
	}

	// Contracts failing leave the others read, and their own figures null.
	// A round with a Claimed event, and one waiting to be claimable, stay
	// read: neither needs Claims. One that could have been claimed with no
	// event is unknown.
	f.down, f.revert = false, map[common.Address]bool{claimsAt: true, ledgerAt: true}
	e.cycle(context.Background())
	m = getJSON(t, h, nodePath)
	round := node(m)["rounds"].([]any)[0].(map[string]any)
	r2 := node(m)["rounds"].([]any)[10].(map[string]any)
	r5 := node(m)["rounds"].([]any)[7].(map[string]any)
	if node(m)["stake_wei"] != nil || node(m)["bond_wei"] != nil || round["stake_wei"] != nil || round["claimed"] != false ||
		round["paid_wei"] != "0" || round["part_wei"] != nil || round["claim_score"] != nil || r2["part_wei"] != nil ||
		node(m)["registered"] != true || round["fees_usdc_micro"] != "0" || r2["fees_usdc_micro"] != "1000000" ||
		r5["claimed"] != nil || r5["paid_wei"] != nil || r5["state"] != nil || r2["claimed"] != true || r2["paid_wei"] != "300551889676665523465" {
		t.Errorf("with Claims and StakeLedger failing: %v, %v, %v", node(m), round, r5)
	}

	f.revert = map[common.Address]bool{}
	e.cycle(context.Background())
	m = getJSON(t, h, nodePath)
	r2 = node(m)["rounds"].([]any)[10].(map[string]any)
	if node(m)["stake_wei"] != "3200000000000000000000" || node(m)["registered"] != true || r2["claim_score"] != "1000000" ||
		node(m)["score_now"] != "500000" ||
		len(m["testnet"].(map[string]any)["buys"].([]any)) != 1 || len(m["unavailable"].([]any)) != 0 {
		t.Errorf("after the endpoint recovered: %v", m)
	}
	if page := get(t, h, "/"); strings.Contains(page, "unavailable") {
		t.Errorf("the recovered page still shows a figure unavailable")
	}
}

// Every document starts with the same keys, and answers before the
// explorer has read anything: each figure null, never 0.
func TestEveryDocumentStartsTheSame(t *testing.T) {
	f := newFake()
	f.logs = deployment()
	e := newTestExplorer(t, f, testConfig)
	h := handler(e)
	paths := []string{"/network.json", "/orchestrators.json", nodePath, "/apps.json", "/apps/" + payerAt.Hex() + ".json"}
	heads := regexp.MustCompile(`^\{\s*"block": [^\n]*,\s*"block_time": [^\n]*,\s*"rpc": \{`)
	for _, read := range []bool{false, true} {
		if read {
			e.cycle(context.Background())
		}
		for _, p := range paths {
			body := get(t, h, p)
			m := getJSON(t, h, p)
			if !heads.MatchString(body) || !strings.Contains(body, `"index": {`) || !strings.Contains(body, `"scenario": `) ||
				m["unavailable"] == nil || (m["block"] == nil) == read {
				t.Errorf("%s, read %v:\n%.400s", p, read, body)
			}
			if !read && strings.Contains(body, `"0"`) {
				t.Errorf("%s shows a 0 before anything was read:\n%s", p, body)
			}
		}
	}
	idx := getJSON(t, h, "/network.json")["index"].(map[string]any)
	if idx["through"] != 1000.0 || idx["caught_up"] != true || idx["first_block"] != 100.0 || idx["resumed_from"] != nil {
		t.Errorf("index: %v", idx)
	}
}

// Requests are served from what the loop published: a hundred of them, of
// every document, send the provider nothing.
func TestServingNeverReadsTheChain(t *testing.T) {
	f := newFake()
	f.logs = deployment()
	e := newTestExplorer(t, f, testConfig)
	h := handler(e)
	e.cycle(context.Background())
	before := f.requests
	paths := append([]string{"/network.json", "/orchestrators.json", nodePath, "/apps.json", "/apps/" + payerAt.Hex() + ".json"}, htmlPaths...)
	for i := range 100 {
		get(t, h, paths[i%len(paths)])
	}
	if f.requests != before {
		t.Errorf("a hundred requests sent the provider %d requests", f.requests-before)
	}
}

// A score that changes with no Voted event, as when the validator set
// changes, shows at the next snapshot.
func TestAScoreChangedWithNoVoteShows(t *testing.T) {
	f := newFake()
	f.logs = deployment()
	e := newTestExplorer(t, f, testConfig)
	h := handler(e)
	e.cycle(context.Background())
	score := func() any {
		return getJSON(t, h, "/orchestrators.json")["orchestrators"].([]any)[0].(map[string]any)["score_now"]
	}
	if s := score(); s != "500000" {
		t.Fatalf("score %v", s)
	}
	f.score[nodeAt], f.head = 250_000, 1001
	e.cycle(context.Background())
	if s := score(); s != "250000" {
		t.Errorf("after the change, score %v", s)
	}
}

// A payer's pending release shows beside its deposit and leaves the
// deposit as earmarkOf gives it; funded counts each deposit and earmark;
// the fees are the gross as settled, with the node and network amounts as
// recorded.
func TestAPayersFigures(t *testing.T) {
	f := newFake()
	f.logs = append(deployment(), funded(700, 0, nodeAt, 2_000_000), settled(710, 9, nodeAt, 1_000_001, 500_000, 500_001),
		settled(720, 9, nodeAt, 400_000, 200_000, 200_000))
	f.deposit = [2]int64{3_000_000, 1_000_000}
	e := newTestExplorer(t, f, testConfig)
	h := handler(e)
	e.cycle(context.Background())
	row := getJSON(t, h, "/apps.json")["apps"].([]any)[0].(map[string]any)
	want := map[string]any{"deposit_usdc_micro": "3000000", "deposit_pending_usdc_micro": "1000000", "funded_usdc_micro": "7000000",
		"released_usdc_micro": "0", "fees_usdc_micro": "2400001", "node_usdc_micro": "1200000", "network_usdc_micro": "1200001"}
	for k, v := range want {
		if row[k] != v {
			t.Errorf("%s: %v, want %v", k, row[k], v)
		}
	}
	doc := getJSON(t, h, "/apps/"+payerAt.Hex()+".json")
	byNode := doc["by_node"].([]any)[0].(map[string]any)
	if len(doc["fees_by_round"].([]any)) != 2 || byNode["fees_usdc_micro"] != "2400001" || byNode["address"] != strings.ToLower(nodeAt.Hex()) {
		t.Errorf("one payer: %v", doc)
	}
}

// Each buy of the executor in force for its round counts once: buys in the
// page that announces a new executor and after it, across a restart
// between them, and none of an announcement withdrawn before it counted.
func TestBuysCountTheExecutorInForce(t *testing.T) {
	second, withdrawn := common.HexToAddress("0xe2"), common.HexToAddress("0xe3")
	f := newFake()
	f.executor = func(r uint64) common.Address {
		if r >= 6 {
			return second
		}
		return executorAt
	}
	announce := func(b uint64, a common.Address) types.Log {
		return event(paramsAt, "Announced", b, n(groupExecutor), []*big.Int{new(big.Int).SetBytes(a.Bytes())}, n(6))
	}
	f.logs = []types.Log{registered(nodeAt, 110), bought(executorAt, 3, 300, 1),
		announce(500, second), bought(second, 6, 510, 2), bought(executorAt, 6, 520, 99),
		bought(second, 7, 800, 3), announce(850, withdrawn), event(paramsAt, "Withdrawn", 860, n(groupExecutor), n(9)),
		bought(withdrawn, 8, 900, 98), bought(second, 8, 910, 4)}
	cfg := testConfig
	cfg.node = nil
	buys := func(e *explorer) []int64 {
		var out []int64
		for _, b := range getJSON(t, handler(e), "/network.json")["buys"].([]any) {
			v, _ := strconv.ParseInt(b.(map[string]any)["usdc_in_micro"].(string), 10, 64)
			out = append(out, v)
		}
		return out
	}
	e := newTestExplorer(t, f, cfg)
	e.cycle(context.Background())
	if got := buys(e); !slices.Equal(got, []int64{1, 2, 3, 4}) {
		t.Errorf("read at once: %v", got)
	}
	dir := t.TempDir()
	f.head = 600
	e = explorerIn(t, dir, f, cfg)
	e.cycle(context.Background())
	e.ix.Close()
	f.head = 1000
	e = explorerIn(t, dir, f, cfg)
	if from := e.ix.ResumedFrom(); from == nil || *from != 601 {
		t.Fatalf("resumed from %v", from)
	}
	e.cycle(context.Background())
	if got := buys(e); !slices.Equal(got, []int64{1, 2, 3, 4}) {
		t.Errorf("restarted between the announcement and the later buys: %v", got)
	}
}

// The labels file names accounts and marks the simulated ones, and names
// the scenario; a change to it shows once the file is read again, and a file that
// cannot be read keeps the labels it had and says so.
func TestLabelsNameAccounts(t *testing.T) {
	f := newFake()
	f.logs = deployment()
	labels := filepath.Join(t.TempDir(), "labels.json")
	write := func(body string) {
		if err := os.WriteFile(labels, []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	write(`{"scenario": "two payers", "accounts": {"` + nodeAt.Hex() + `": {"name": "N"}, "` + payerAt.Hex() + `": {"name": "P", "simulated": true}}}`)
	cfg := testConfig
	cfg.labels = labels
	e := newTestExplorer(t, f, cfg)
	h := handler(e)
	e.cycle(context.Background())
	app := getJSON(t, h, "/apps.json")["apps"].([]any)[0].(map[string]any)
	node := getJSON(t, h, "/orchestrators.json")["orchestrators"].([]any)[0].(map[string]any)
	if app["label"] != "P" || app["simulated"] != true || node["label"] != "N" || node["simulated"] != false ||
		getJSON(t, h, nodePath)["scenario"] != "two payers" {
		t.Errorf("labels: %v, %v", app, node)
	}
	write(`{"scenario": null, "accounts": {"` + payerAt.Hex() + `": {"name": "P2"}}}`)
	e.readLabels()
	app = getJSON(t, h, "/apps.json")["apps"].([]any)[0].(map[string]any)
	if app["label"] != "P2" || app["simulated"] != false || getJSON(t, h, "/network.json")["scenario"] != nil {
		t.Errorf("after the file changed: %v", app)
	}
	write(`{"accounts": `)
	e.readLabels()
	m := getJSON(t, h, "/apps.json")
	if m["apps"].([]any)[0].(map[string]any)["label"] != "P2" || !strings.Contains(fmt.Sprint(m["unavailable"]), "the labels file") {
		t.Errorf("a file that cannot be read: %v", m)
	}
}

// A rewrite of the labels file that keeps its size and its modification
// time still shows once the file is read again: the file is told changed by
// its contents.
func TestARewriteOfTheSameSizeAndTimeShows(t *testing.T) {
	f := newFake()
	f.logs = deployment()
	labels := filepath.Join(t.TempDir(), "labels.json")
	write := func(body string) {
		if err := os.WriteFile(labels, []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	file := func(scenario, name string) string {
		return `{"scenario": "` + scenario + `", "accounts": {"` + payerAt.Hex() + `": {"name": "` + name + `"}}}`
	}
	write(file("alpha", "P-alpha"))
	before, err := os.Stat(labels)
	if err != nil {
		t.Fatal(err)
	}
	cfg := testConfig
	cfg.labels = labels
	e := newTestExplorer(t, f, cfg)
	h := handler(e)
	e.cycle(context.Background())
	if getJSON(t, h, "/network.json")["scenario"] != "alpha" {
		t.Fatalf("first read: %v", getJSON(t, h, "/network.json")["scenario"])
	}
	write(file("bravo", "P-bravo"))
	if err := os.Chtimes(labels, before.ModTime(), before.ModTime()); err != nil {
		t.Fatal(err)
	}
	if after, err := os.Stat(labels); err != nil || after.Size() != before.Size() || !after.ModTime().Equal(before.ModTime()) {
		t.Fatalf("the rewrite changed the size or the time: %v, %v", after, err)
	}
	e.readLabels()
	app := getJSON(t, h, "/apps.json")["apps"].([]any)[0].(map[string]any)
	if getJSON(t, h, "/network.json")["scenario"] != "bravo" || app["label"] != "P-bravo" {
		t.Errorf("after a rewrite of the same size and time: %v, %v", getJSON(t, h, "/network.json")["scenario"], app["label"])
	}
}

// A rollback rebuilds everything derived from the branch left: the
// index's totals and the views the explorer kept, such as a round's stake.
func TestARollbackRebuildsWhatWasKept(t *testing.T) {
	f := newFake()
	f.logs = deployment()
	e := newTestExplorer(t, f, testConfig)
	h := handler(e)
	e.cycle(context.Background())
	stake := func() any { return getJSON(t, h, nodePath)["node"].(map[string]any)["stake_wei"] }
	if s := stake(); s != "3200000000000000000000" {
		t.Fatalf("stake %v", s)
	}
	// Blocks from 950 are replaced: the buy at 950 is not on the new branch,
	// and on it the node's stake this round is another.
	f.fork, f.branch, f.head, f.stake = 950, 1, 1001, "4200000000000000000000"
	f.logs = slices.DeleteFunc(f.logs, func(l types.Log) bool { return l.BlockNumber == 950 })
	e.cycle(context.Background())
	m := getJSON(t, h, nodePath)
	if s := stake(); s != "4200000000000000000000" || e.ix.Rollbacks() != 1 || len(m["testnet"].(map[string]any)["buys"].([]any)) != 0 {
		t.Errorf("after the rollback: stake %v, rollbacks %d, buys %v", s, e.ix.Rollbacks(), m["testnet"].(map[string]any)["buys"])
	}
}

// An index folder of another chain stops the explorer, naming both.
func TestAnotherChainsIndexStopsTheExplorer(t *testing.T) {
	f := newFake()
	f.logs = deployment()
	dir := t.TempDir()
	e := explorerIn(t, dir, f, testConfig)
	e.cycle(context.Background())
	e.ix.Close()
	f.chainID = 1
	e = explorerIn(t, dir, f, testConfig)
	var stopped error
	e.stop = func(err error) { stopped = err }
	e.cycle(context.Background())
	if !errors.Is(stopped, index.ErrDeployment) || !strings.Contains(stopped.Error(), "chain 1337") || !strings.Contains(stopped.Error(), "chain 1,") {
		t.Errorf("stopped with %v", stopped)
	}
}

// The node's page shows each figure of the node's JSON, a figure not read
// as unavailable, and each round's reward where it stands.
func TestTheNodesPage(t *testing.T) {
	yes, no := true, false
	block, at, round, from, until, start := uint64(1000), uint64(1_795_000_000), uint64(12), uint64(9), uint64(30), uint64(990)
	claimable, claimed, expired := stateClaimable, stateClaimed, stateExpired
	payout := payoutAt
	validators := []common.Address{validatorAt}
	buys := []Buy{{Round: 11, Block: 950, USDCIn: (*Int)(n(1_500_000)), LPTBought: (*Int)(wei("3000000000000000000")), Burned: (*Int)(wei("3000000000000000000"))}}
	s := &NodeDoc{Head: Head{Block: &block, BlockTime: &at, Unavailable: []string{"the node's own bond this round: execution reverted"}},
		Node: Node{Address: nodeAt, Registered: &yes, Payout: &payout, Stake: (*Int)(wei("4200000000000000000000")),
			ScoreNow: (*Int)(n(500_000)), PayoutUSDC: (*Int)(n(1_500_000)), ETH: (*Int)(n(1)), LowGas: &yes, LowGasWei: (*Int)(lowGasWei),
			Rounds: []Round{
				{Round: 4, State: &expired, ClaimableFrom: &from, ClaimableUntil: &until, Claimed: &no},
				{Round: 3, Fees: (*Int)(n(1_000_000)), Stake: (*Int)(wei("3200000000000000000000")), State: &claimable, ClaimableFrom: &from, ClaimableUntil: &until, Claimed: &no},
				{Round: 2, Fees: (*Int)(n(1_000_000)), State: &claimed, Claimed: &yes, ClaimScore: (*Int)(n(1_000_000)), Paid: (*Int)(wei("300551889676665523465")), Pot: (*Int)(n(0)), Part: (*Int)(n(0)), PartWithdrawn: &no},
			}},
		Testnet: Testnet{Round: &round, RoundStartBlock: &start, Validators: &validators, Buys: &buys}}
	var b bytes.Buffer
	f := frame{Head: s.Head, Mine: &nodeAt, Nav: "node", JSON: nodePath, Missing: unavailable, Now: time.Unix(int64(at)+30, 0)}
	if err := render(&b, "node", nodePage{frame: f, Doc: *s, Validators: []Named{{Address: validatorAt}}, Read: true}); err != nil {
		t.Fatal(err)
	}
	page := b.String()
	for _, want := range []string{
		`<meta http-equiv="refresh" content="15">`,
		"<dt>Stake this round</dt><dd>4200 LPT</dd>",
		"<td class=\"n\">3200 LPT</td>", // round 3's stake
		"<dt>Score a claim made now reads</dt><dd>50%</dd>",
		"<td>expired after round 30, never minted</td><td class=\"n\"></td>", // no score for an expired round
		"<td>claimable now, until round 30</td><td class=\"n\">50% (now)</td>",
		"<td>claimed</td><td class=\"n\">100%</td>",
		"1.5 USDC", // payout USDC and the buy
		`<span title="0.000000000000000001 ETH">&lt; 0.0001 ETH</span> <span class="warn">low: under 0.001 ETH</span>`,
		`<span title="300.551889676665523465 LPT">300.5519 LPT</span>`, // LPT rounded to read, exact in its title
		"<dd>block 990, unavailable</dd>", // a start block without its time
		validatorAt.Hex(),
		"<dt>Own bond this round</dt><dd>unavailable</dd>", // the bond, not read
		"the node&#39;s own bond this round: execution reverted",
		"Read at block 1000, 30 s ago",
		`<a href="/orchestrators/` + nodeAt.Hex() + `.json">JSON</a>`,
		`<a href="/" aria-current="page">Your node</a>`,
	} {
		if !strings.Contains(page, want) {
			t.Errorf("the page lacks %q", want)
		}
	}
}

// A page writes LPT and ETH to four decimals, rounded half away from 0,
// exact when it has no more, and a figure that rounds to 0 as under the
// last place.
func TestRoundUnits(t *testing.T) {
	for _, c := range []struct {
		n     string
		shown string
		exact bool
	}{
		{"300551889676665523465", "300.5519", false},
		{"1000050000000000000", "1.0001", false},
		{"1000049999999999999", "1", false},
		{"4200000000000000000000", "4200", true},
		{"1500000000000000", "0.0015", true},
		{"1", "< 0.0001", false},
		{"0", "0", true},
		{"-1000050000000000000", "-1.0001", false},
	} {
		shown, exact := roundUnits(wei(c.n), 18, 4)
		if shown != c.shown || exact != c.exact {
			t.Errorf("%s: %q, %v", c.n, shown, exact)
		}
	}
}

func TestUnits(t *testing.T) {
	for _, c := range []struct {
		n    *big.Int
		d    int
		want string
	}{{n(1_500_000), 6, "1.5"}, {n(0), 6, "0"}, {n(1), 6, "0.000001"}, {wei("300551889676665523465"), 18, "300.551889676665523465"}, {n(-25), 1, "-2.5"}} {
		if got := units(c.n, c.d); got != c.want {
			t.Errorf("units(%s, %d) = %s, want %s", c.n, c.d, got, c.want)
		}
	}
}

// rewardState's window agrees with every claim of the shared examples'
// ledger cases that reaches the timing checks: CLAIM_TOO_EARLY is a round
// still waiting, CLAIM_EXPIRED one expired, and a claim that pays, or is
// refused only for its score or for being claimed already, one in its
// window. (A claimed round past its window is refused CLAIM_EXPIRED, which
// R-12 checks first; the page shows it as claimed.)
func TestRewardStateMatchesTheSharedExamples(t *testing.T) {
	files, _ := filepath.Glob("../../../vectors/october-main-text-v*.json")
	version := func(f string) int {
		v, _ := strconv.Atoi(strings.TrimSuffix(strings.TrimPrefix(filepath.Base(f), "october-main-text-v"), ".json"))
		return v
	}
	slices.SortFunc(files, func(a, b string) int { return version(a) - version(b) })
	if len(files) == 0 {
		t.Fatal("no shared examples found")
	}
	var doc struct {
		Parameters map[string]string `json:"parameters"`
		Cases      []struct {
			ID     string `json:"id"`
			Kind   string `json:"kind"`
			Inputs struct {
				Steps []map[string]any `json:"steps"`
			} `json:"inputs"`
			Expected struct {
				Refused []struct{ Step, Code string } `json:"refused"`
			} `json:"expected"`
		} `json:"cases"`
	}
	raw, err := os.ReadFile(files[len(files)-1])
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(raw, &doc); err != nil {
		t.Fatal(err)
	}
	num := func(v any) uint64 { x, _ := strconv.ParseUint(v.(string), 10, 64); return x }
	notice, delay := num(doc.Parameters["notice_rounds"]), num(doc.Parameters["reward_delay_rounds"])
	want := map[string]string{"": stateClaimable, "SCORE_OUT_OF_RANGE": stateClaimable, "ALREADY_CLAIMED": stateClaimable,
		"CLAIM_TOO_EARLY": stateWaiting, "CLAIM_EXPIRED": stateExpired}
	checked := map[string]int{}
	for _, c := range doc.Cases {
		if c.Kind != "ledger" {
			continue
		}
		refused := map[string]string{}
		for _, r := range c.Expected.Refused {
			refused[r.Step] = r.Code
		}
		// The reward delay's changes: the round each counts from, and its value.
		type change struct{ from, delay uint64 }
		var changes []change
		var pending *change
		inForce := func(r uint64) uint64 {
			d := delay
			for _, ch := range changes {
				if ch.from <= r {
					d = ch.delay
				}
			}
			if pending != nil && pending.from <= r {
				d = pending.delay
			}
			return d
		}
		for _, st := range c.Inputs.Steps {
			round := num(st["round"])
			if pending != nil && pending.from <= round {
				changes, pending = append(changes, *pending), nil
			}
			switch st["action"] {
			case "announce":
				if st["group"] == strconv.Itoa(groupRewardDelay) {
					pending = &change{round + notice, num(st["values"].([]any)[0])}
				}
			case "withdraw":
				if st["group"] == strconv.Itoa(groupRewardDelay) {
					pending = nil
				}
			case "claim":
				code := refused[st["step"].(string)]
				w, ok := want[code]
				if !ok {
					continue // refused before the timing is checked
				}
				earned := num(st["earned_round"])
				got, from, until := rewardState(earned, round, inForce(earned), false)
				if got != w {
					t.Errorf("%s step %s: round %d in round %d (claimable %d to %d) is %s, the example says %s",
						c.ID, st["step"], earned, round, from, until, got, code)
				}
				checked[w]++
			}
		}
	}
	for _, s := range []string{stateWaiting, stateClaimable, stateExpired} {
		if checked[s] == 0 {
			t.Errorf("no example claim checked a %s round", s)
		}
	}
}

// Every view and event the explorer reads is declared, with the same
// argument types, in contracts/interfaces.
func TestViewsAreDeclared(t *testing.T) {
	files, _ := filepath.Glob("../../../contracts/interfaces/*.sol")
	var src strings.Builder
	for _, f := range files {
		b, err := os.ReadFile(f)
		if err != nil {
			t.Fatal(err)
		}
		src.Write(b)
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
	for _, m := range views.Methods {
		if !found[m.Sig] {
			t.Errorf("%s is not declared in contracts/interfaces", m.Sig)
		}
	}
	for _, e := range views.Events {
		if !found[e.Sig] {
			t.Errorf("event %s is not declared in contracts/interfaces", e.Sig)
		}
	}
}

// With the RPC provider down, rejecting the key and echoing it, or
// redirecting to a host whose answer cannot be read, neither the page, nor
// its JSON, nor the failure at start shows the key the URL carries, a
// password escaped as the URL writes it, or what the second host wrote;
// each says what could not be read, and why by the failure's fixed
// category.
func TestAnRPCFailureHidesTheProvidersKey(t *testing.T) {
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	down := l.Addr().String()
	l.Close() // nothing listens there now: the connection is refused
	echo := echoingProvider(t, "synthetic-key-in-the-path", "synthetic-user", "synthetic-password", "synthetic-key-in-the-query")
	escaped := echoingProvider(t, "synthetic%40password")
	second := answering(t, "307 Temporary Redirect\r\nLocation: http://[provider-written-marker")
	redirecting := answering(t, "307 Temporary Redirect\r\nLocation: http://"+second+"/followed")
	for url, category := range map[string]string{
		"http://synthetic-user:synthetic%40password@" + redirecting + "/rpc":                                                   "the RPC provider http://" + redirecting + " redirected the request (HTTP 307)",
		"http://" + down + "/v3/synthetic-key-in-the-path?apikey=synthetic-key-in-the-query":                                   "the RPC provider http://" + down + " could not be reached",
		"http://synthetic-user:synthetic-password@" + echo + "/v3/synthetic-key-in-the-path?apikey=synthetic-key-in-the-query": "the RPC provider http://" + echo + " refused the request (HTTP 401)",
		"http://synthetic-user:synthetic%40password@" + escaped + "/rpc":                                                       "the RPC provider http://" + escaped + " refused the request (HTTP 401)",
	} {
		client, err := chain.DialRPC(context.Background(), url)
		if err != nil {
			t.Fatal(err)
		}
		cfg := testConfig
		cfg.rpcURL = url
		e := newTestExplorer(t, client, cfg)
		e.cycle(context.Background())
		srv := httptest.NewServer(handler(e))
		jsonPaths := []string{"/orchestrators/" + nodeAt.Hex() + ".json", "/network.json", "/orchestrators.json", "/apps.json", "/apps/" + payerAt.Hex() + ".json"}
		for _, path := range append(jsonPaths, htmlPaths...) {
			r, err := http.Get(srv.URL + path)
			if err != nil {
				t.Fatal(err)
			}
			var b bytes.Buffer
			b.ReadFrom(r.Body)
			r.Body.Close()
			reason := "the chain's newest block: " + category
			if strings.Contains(b.String(), "synthetic") || strings.Contains(b.String(), "marker") ||
				!strings.Contains(b.String(), reason) && !strings.Contains(b.String(), html.EscapeString(reason)) {
				t.Errorf("%s, with the provider at %s failing:\n%s", path, url, b.String())
			}
			// A page shows the banner, with the category and the pointer,
			// and the provider by scheme and host.
			if !strings.HasSuffix(path, ".json") && !bannered(b.String(), category, provider(url)) {
				t.Errorf("%s shows no banner for %s:\n%s", path, category, b.String())
			}
		}
		srv.Close()
		client.Close()
	}
	for url, category := range map[string]string{
		"ws://" + down + "/ws/synthetic-key-for-websocket":                                                                   "the RPC provider ws://" + down + " could not be reached",
		"ws://synthetic-user:synthetic-password@" + echo + "/ws/synthetic-key-in-the-path?apikey=synthetic-key-in-the-query": "the RPC provider ws://" + echo + " refused the request (HTTP 401)",
		"ws://synthetic-user:synthetic%40password@" + escaped + "/ws":                                                        "the RPC provider ws://" + escaped + " refused the request (HTTP 401)",
	} {
		_, err := chain.DialRPC(context.Background(), url)
		if err == nil {
			t.Fatalf("%s: dialled", url)
		}
		if got := failure(err, url); got != "connect to the chain: "+category {
			t.Errorf("the failure at start is not the step and the category: %s", got)
		}
	}
}

// echoingProvider starts a provider that answers every request with 401,
// the keys it was given in its status line and its body, as a provider
// that rejects a key and quotes it does, and returns its host.
func echoingProvider(t *testing.T, keys ...string) string {
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { l.Close() })
	said := strings.Join(keys, " ")
	body := `{"jsonrpc":"2.0","id":1,"error":{"code":-32001,"message":"invalid key ` + said + `"}}`
	go func() {
		for {
			conn, err := l.Accept()
			if err != nil {
				return
			}
			go func() {
				defer conn.Close()
				http.ReadRequest(bufio.NewReader(conn))
				fmt.Fprintf(conn, "HTTP/1.1 401 rejected %s\r\nContent-Type: application/json\r\nContent-Length: %d\r\nConnection: close\r\n\r\n%s",
					said, len(body), body)
			}()
		}
	}()
	return l.Addr().String()
}

// answering starts a provider that answers every request with the status
// line and headers in head and no body, and returns its host.
func answering(t *testing.T, head string) string {
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { l.Close() })
	go func() {
		for {
			conn, err := l.Accept()
			if err != nil {
				return
			}
			go func() {
				defer conn.Close()
				http.ReadRequest(bufio.NewReader(conn))
				fmt.Fprintf(conn, "HTTP/1.1 %s\r\nContent-Length: 0\r\nConnection: close\r\n\r\n", head)
			}()
		}
	}()
	return l.Addr().String()
}
