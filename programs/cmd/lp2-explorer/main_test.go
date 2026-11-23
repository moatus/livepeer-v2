package main

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
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
	"testing"

	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/common/hexutil"
	"github.com/ethereum/go-ethereum/core/types"
	"github.com/ethereum/go-ethereum/crypto"
	"github.com/ethereum/go-ethereum/rpc"
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
	nodeAt       = common.HexToAddress("0xa1")
	payoutAt     = common.HexToAddress("0xa2")
	validatorAt  = common.HexToAddress("0xa3")
)

var testConfig = config{controller: controllerAt, params: paramsAt, ledger: ledgerAt, claims: claimsAt,
	usdc: usdcAt, node: nodeAt, first: 100}

// fakeChain answers the JSON-RPC requests the explorer makes, from a
// deployment where node 0xa1 earned in round 2 and claimed it, in round
// 12. down makes every request fail, as an endpoint that does not answer;
// revert makes every call to the contracts it names fail.
type fakeChain struct {
	head     uint64
	round    uint64
	executor common.Address
	logs     []types.Log
	down     bool
	revert   map[common.Address]bool // calls to these contracts fail
	failLogs map[uint64]bool         // a getLogs from this block fails
	asked    [][2]uint64             // each getLogs range of the clock and executor, in order
	claimsQ  []map[string]any        // each getLogs of Claims, in order
}

func newFake() *fakeChain {
	return &fakeChain{head: 1000, round: 12, executor: executorAt, failLogs: map[uint64]bool{}, revert: map[common.Address]bool{}}
}

var errDown = errors.New("connection refused")

func (f *fakeChain) BatchCallContext(_ context.Context, b []rpc.BatchElem) error {
	if f.down {
		return errDown
	}
	for i := range b {
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

func (f *fakeChain) answer(method string, args []any) (any, error) {
	switch method {
	case "eth_blockNumber":
		return hexutil.Uint64(f.head), nil
	case "eth_getBalance":
		return hexutil.Big(*big.NewInt(2_000_000_000_000_000)), nil
	case "eth_getBlockByNumber":
		n, _ := hexutil.DecodeUint64(args[0].(string))
		return map[string]any{"timestamp": hexutil.Uint64(1_795_000_000 + n)}, nil
	case "eth_getLogs":
		q := args[0].(map[string]any)
		from, _ := hexutil.DecodeUint64(q["fromBlock"].(string))
		to, _ := hexutil.DecodeUint64(q["toBlock"].(string))
		want := q["address"].([]common.Address)
		if slices.Contains(want, claimsAt) {
			f.claimsQ = append(f.claimsQ, q)
		} else {
			f.asked = append(f.asked, [2]uint64{from, to})
		}
		if f.failLogs[from] {
			return nil, errors.New("rate limited")
		}
		topics := q["topics"].([][]common.Hash)
		var out []types.Log
	logs:
		for _, l := range f.logs {
			if l.BlockNumber < from || l.BlockNumber > to || !slices.Contains(want, l.Address) {
				continue
			}
			for i, any := range topics {
				if len(any) > 0 && (i >= len(l.Topics) || !slices.Contains(any, l.Topics[i])) {
					continue logs
				}
			}
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
	case to == registryAt && m.Name == "isRegistered":
		out = []any{args[0] == nodeAt}
	case to == registryAt && m.Name == "operatorOf":
		out = []any{args[0]}
	case to == registryAt && m.Name == "payoutOf":
		out = []any{payoutAt}
	case to == paramsAt && m.Name == "valuesAt":
		switch args[0].(*big.Int).Int64() {
		case groupScoreSource:
			out = []any{[]*big.Int{new(big.Int).SetBytes(scoresAt.Bytes())}}
		case groupExecutor:
			out = []any{[]*big.Int{new(big.Int).SetBytes(f.executor.Bytes())}}
		case groupRewardDelay:
			out = []any{[]*big.Int{n(7)}}
		}
	case to == ledgerAt && m.Name == "stakeAt":
		out = []any{wei("3200000000000000000000")}
	case to == ledgerAt && m.Name == "bondAt":
		out = []any{wei("1000000000000000000")}
	case to == brokerAt && m.Name == "feesOf":
		fee := n(0)
		if args[0].(*big.Int).Int64() == 2 {
			fee = n(1_000_000)
		}
		out = []any{fee}
	case to == brokerAt && m.Name == "totalFees":
		out = []any{n(3_000_000)}
	case to == claimsAt && m.Name == "entitlementOf":
		if args[1].(*big.Int).Int64() == 2 {
			out = []any{true, wei("300551889676665523465"), wei("150275944838332761733")}
		} else {
			out = []any{false, n(0), n(0)}
		}
	case to == claimsAt && m.Name == "partOf":
		out = []any{n(0), false}
		if args[2].(*big.Int).Int64() == 2 {
			out = []any{wei("46961232761978988041"), true}
		}
	case to == usdcAt && m.Name == "balanceOf":
		out = []any{n(1_500_000)}
	case to == scoresAt && m.Name == "scoreOf":
		out = []any{n(500_000)}
	case to == scoresAt && m.Name == "membersAt":
		out = []any{[]common.Address{validatorAt}}
	default:
		return hexutil.Bytes{}, nil // an address with no such code answers 0x
	}
	packed, err := m.Outputs.Pack(out...)
	return hexutil.Bytes(packed), err
}

// bought is a Bought event of round r at block b.
func bought(at common.Address, r, b uint64, usdcIn int64) types.Log {
	data, err := views.Events["Bought"].Inputs.NonIndexed().Pack(n(usdcIn), n(usdcIn), n(usdcIn*2), n(usdcIn*2), n(0), n(0))
	if err != nil {
		panic(err)
	}
	return types.Log{Address: at, BlockNumber: b, TxHash: common.BigToHash(new(big.Int).SetUint64(b)),
		Topics: []common.Hash{boughtTopic, common.BigToHash(new(big.Int).SetUint64(r))}, Data: data}
}

// claimedLog is a Claimed event of node's claim of round r at block b, at
// the given score.
func claimedLog(node common.Address, r, b uint64, score int64, paid string) types.Log {
	data, err := views.Events["Claimed"].Inputs.NonIndexed().Pack(payoutAt, n(score), wei(paid), n(0), wei(paid))
	if err != nil {
		panic(err)
	}
	return types.Log{Address: claimsAt, BlockNumber: b,
		Topics: []common.Hash{claimedTopic, common.BytesToHash(node.Bytes()), common.BigToHash(new(big.Int).SetUint64(r))}, Data: data}
}

// claimOf2 is node 0xa1's claim of round 2, in round 9, at score 1,000,000:
// the score now, 500,000, is not the one the claim applied.
var claimOf2 = claimedLog(nodeAt, 2, 600, 1_000_000, "300551889676665523465")

// started is a RoundStarted event of round r at block b.
func started(r, b uint64) types.Log {
	data, _ := views.Events["RoundStarted"].Inputs.NonIndexed().Pack(n(int64(b)), [32]byte{})
	return types.Log{Address: clockAt, BlockNumber: b,
		Topics: []common.Hash{startedTopic, common.BigToHash(new(big.Int).SetUint64(r))}, Data: data}
}

func TestReadsTheNodeAndTheTestnet(t *testing.T) {
	f := newFake()
	// Another node's claim of round 2, at another score, is not the node's.
	f.logs = []types.Log{claimOf2, claimedLog(validatorAt, 2, 610, 250_000, "1"),
		started(11, 900), bought(executorAt, 11, 950, 7), started(12, 990)}
	s := newExplorer(testConfig, f).read(context.Background())
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

	// A claimed round whose Claimed event the scan has not found has no
	// claim score, and says so: never the score now.
	f.logs = []types.Log{started(12, 990)}
	s = newExplorer(testConfig, f).read(context.Background())
	if r2 := s.Node.Rounds[10]; r2.ClaimScore != nil || len(s.Unavailable) != 1 || !strings.Contains(s.Unavailable[0], "round 2's claim score") {
		t.Errorf("round 2 with no Claimed event: score %v, unavailable %v", r2.ClaimScore, s.Unavailable)
	}
}

// A read that fails shows its figures as unavailable, null in the JSON and
// never 0, and the next read after the endpoint answers again shows them.
func TestFailedReadIsUnavailableAndRecovers(t *testing.T) {
	f := newFake()
	f.logs = []types.Log{claimOf2, started(12, 990), bought(executorAt, 12, 995, 7)}
	srv := httptest.NewServer(handler(newExplorer(testConfig, f)))
	defer srv.Close()
	get := func(path string) string {
		r, err := http.Get(srv.URL + path)
		if err != nil {
			t.Fatal(err)
		}
		defer r.Body.Close()
		var b bytes.Buffer
		b.ReadFrom(r.Body)
		return b.String()
	}
	status := func() map[string]any {
		var m map[string]any
		if err := json.Unmarshal([]byte(get("/status.json")), &m); err != nil {
			t.Fatal(err)
		}
		return m
	}
	node := func(m map[string]any) map[string]any { return m["node"].(map[string]any) }

	f.down = true
	m := status()
	if m["block"] != nil || node(m)["stake_wei"] != nil || node(m)["registered"] != nil || node(m)["eth_wei"] != nil ||
		m["testnet"].(map[string]any)["buys"] != nil || len(m["unavailable"].([]any)) == 0 {
		t.Errorf("with the endpoint down: %v", m)
	}
	if page := get("/"); !strings.Contains(page, "<td class=\"n\">unavailable</td>") || !strings.Contains(page, errDown.Error()) {
		t.Errorf("the page does not show the figures unavailable:\n%s", page)
	}

	// Contracts failing leave the others read, and their own figures null.
	f.down, f.revert = false, map[common.Address]bool{claimsAt: true, ledgerAt: true}
	m = status()
	round := node(m)["rounds"].([]any)[0].(map[string]any)
	if node(m)["stake_wei"] != nil || node(m)["bond_wei"] != nil || round["stake_wei"] != nil || round["claimed"] != nil ||
		round["paid_wei"] != nil || round["part_wei"] != nil || round["state"] != nil || round["claim_score"] != nil ||
		node(m)["registered"] != true || round["fees_usdc_micro"] != "0" {
		t.Errorf("with Claims and StakeLedger failing: %v, %v", node(m), round)
	}

	f.revert = map[common.Address]bool{}
	m = status()
	r2 := node(m)["rounds"].([]any)[10].(map[string]any)
	if node(m)["stake_wei"] != "3200000000000000000000" || node(m)["registered"] != true || r2["claim_score"] != "1000000" ||
		node(m)["score_now"] != "500000" ||
		len(m["testnet"].(map[string]any)["buys"].([]any)) != 1 || len(m["unavailable"].([]any)) != 0 {
		t.Errorf("after the endpoint recovered: %v", m)
	}
	if page := get("/"); strings.Contains(page, "unavailable") {
		t.Errorf("the recovered page still shows a figure unavailable")
	}
}

// The page is plain HTML that the browser reloads; it runs no script.
func TestPageRendersWithoutJavaScript(t *testing.T) {
	yes, no := true, false
	block, at, round, from, until, start := uint64(1000), uint64(1_795_000_000), uint64(12), uint64(9), uint64(30), uint64(990)
	claimable, claimed, expired := stateClaimable, stateClaimed, stateExpired
	payout := payoutAt
	validators := []common.Address{validatorAt}
	buys := []Buy{{Round: 11, Block: 950, USDCIn: (*Int)(n(1_500_000)), LPTBought: (*Int)(wei("3000000000000000000")), Burned: (*Int)(wei("3000000000000000000"))}}
	s := &Status{Block: &block, BlockTime: &at,
		Node: Node{Address: nodeAt, Registered: &yes, Payout: &payout, Stake: (*Int)(wei("4200000000000000000000")),
			ScoreNow: (*Int)(n(500_000)), PayoutUSDC: (*Int)(n(1_500_000)), ETH: (*Int)(n(1)), LowGas: &yes, LowGasWei: (*Int)(lowGasWei),
			Rounds: []Round{
				{Round: 4, State: &expired, ClaimableFrom: &from, ClaimableUntil: &until, Claimed: &no},
				{Round: 3, Fees: (*Int)(n(1_000_000)), Stake: (*Int)(wei("3200000000000000000000")), State: &claimable, ClaimableFrom: &from, ClaimableUntil: &until, Claimed: &no},
				{Round: 2, Fees: (*Int)(n(1_000_000)), State: &claimed, Claimed: &yes, ClaimScore: (*Int)(n(1_000_000)), Paid: (*Int)(wei("300551889676665523465")), Pot: (*Int)(n(0)), Part: (*Int)(n(0)), PartWithdrawn: &no},
			}},
		Testnet:     Testnet{Round: &round, RoundStartBlock: &start, Validators: &validators, Buys: &buys},
		Unavailable: []string{"the node's own bond this round: execution reverted"}}
	var b bytes.Buffer
	if err := render(&b, s); err != nil {
		t.Fatal(err)
	}
	page := b.String()
	if strings.Contains(strings.ToLower(page), "<script") || strings.Contains(strings.ToLower(page), "javascript") {
		t.Error("the page carries script")
	}
	for _, want := range []string{
		`<meta http-equiv="refresh" content="15">`,
		"<tr><th>Stake this round</th><td class=\"n\">4200 LPT</td></tr>",
		"<td class=\"n\">3200 LPT</td>", // round 3's stake
		"<tr><th>Score a claim made now reads</th><td class=\"n\">50%</td></tr>",
		"<td>expired after round 30, never minted</td><td class=\"n\"></td>", // no score for an expired round
		"<td>claimable now, until round 30</td><td class=\"n\">50% (now)</td>",
		"<td>claimed</td><td class=\"n\">100%</td>",
		"1.5 USDC", // payout USDC and the buy
		"0.000000000000000001 ETH <span class=\"warn\">low: under 0.001 ETH</span>",
		"claimable now, until round 30",
		"300.551889676665523465 LPT",
		"<td>block 990, unavailable</td>", // a start block without its time
		validatorAt.Hex(),
		"<td class=\"n\">unavailable</td>", // the bond, not read
		"the node&#39;s own bond this round: execution reverted",
	} {
		if !strings.Contains(page, want) {
			t.Errorf("the page lacks %q", want)
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

// The scan reads from the first block in pages of at most pageBlocks, then
// only the blocks after the last it read; a failed page is read again
// next time, and a new executor starts the scan again.
func TestScannerReadsBoundedPagesOnce(t *testing.T) {
	f := newFake()
	f.logs = []types.Log{bought(executorAt, 3, 150, 1), started(4, 5200), bought(executorAt, 4, 5300, 2),
		bought(common.HexToAddress("0xdead"), 4, 5301, 99), bought(executorAt, 5, 12_000, 3),
		claimedLog(nodeAt, 2, 5400, 1_000_000, "7"), claimedLog(nodeAt, 3, 11_000, 750_000, "8")}
	s := newScanner(100, claimsAt, nodeAt)
	ctx := context.Background()

	f.failLogs[5100] = true
	if err := s.advance(ctx, f, 12_345, clockAt, executorAt); err == nil || !strings.Contains(err.Error(), "rate limited") {
		t.Fatalf("a failed page: %v", err)
	}
	delete(f.failLogs, 5100)
	if err := s.advance(ctx, f, 12_345, clockAt, executorAt); err != nil {
		t.Fatal(err)
	}
	want := [][2]uint64{{100, 5099}, {5100, 10_099}, {5100, 10_099}, {10_100, 12_345}}
	if !slices.Equal(f.asked, want) {
		t.Errorf("pages asked %v, want %v", f.asked, want)
	}
	if got := s.recent(); len(got) != 3 || got[0].Round != 5 || got[2].Round != 3 || s.start != (roundStart{4, 5200, 1_795_005_200}) {
		t.Errorf("kept %+v, start %+v", got, s.start)
	}
	// Claims is read over the same pages, filtered by the node's topic.
	var claimPages [][2]uint64
	for _, q := range f.claimsQ {
		from, _ := hexutil.DecodeUint64(q["fromBlock"].(string))
		to, _ := hexutil.DecodeUint64(q["toBlock"].(string))
		claimPages = append(claimPages, [2]uint64{from, to})
		if topics := q["topics"].([][]common.Hash); len(topics) != 2 || !slices.Equal(topics[1], []common.Hash{common.BytesToHash(nodeAt.Bytes())}) {
			t.Errorf("a Claims query not filtered by the node: %v", topics)
		}
	}
	if !slices.Equal(claimPages, want) || len(s.claimed) != 2 || s.claimed[2].Int64() != 1_000_000 || s.claimed[3].Int64() != 750_000 {
		t.Errorf("Claims pages %v, claims kept %+v", claimPages, s.claimed)
	}

	f.asked = nil
	if err := s.advance(ctx, f, 12_345, clockAt, executorAt); err != nil || len(f.asked) != 0 {
		t.Errorf("no new block, asked %v (%v)", f.asked, err)
	}
	if err := s.advance(ctx, f, 12_400, clockAt, executorAt); err != nil || !slices.Equal(f.asked, [][2]uint64{{12_346, 12_400}}) {
		t.Errorf("new blocks, asked %v (%v)", f.asked, err)
	}

	// Another executor in force: from the first block again, its buys only.
	f.asked = nil
	other := common.HexToAddress("0xdead")
	if err := s.advance(ctx, f, 12_400, clockAt, other); err != nil || f.asked[0][0] != 100 || len(s.recent()) != 1 || s.recent()[0].USDCIn.big().Int64() != 99 {
		t.Errorf("a new executor: asked %v, kept %+v (%v)", f.asked, s.recent(), err)
	}
}

// A page that fails to decode keeps nothing: the buy read before its bad
// Claimed event is not kept, so the page read again shows it once.
func TestFailedPageKeepsNothing(t *testing.T) {
	f := newFake()
	bad := claimedLog(nodeAt, 2, 160, 1_000_000, "7")
	bad.Data = bad.Data[:1]
	f.logs = []types.Log{bought(executorAt, 3, 150, 1), bad}
	s := newScanner(100, claimsAt, nodeAt)
	ctx := context.Background()
	if err := s.advance(ctx, f, 200, clockAt, executorAt); err == nil || !strings.Contains(err.Error(), "Claimed event in block 160") {
		t.Fatalf("a malformed claim: %v", err)
	}
	if len(s.buys) != 0 || len(s.claimed) != 0 || s.next != 100 {
		t.Errorf("a failed page kept buys %+v, claims %+v, next %d", s.buys, s.claimed, s.next)
	}
	f.logs[1] = claimedLog(nodeAt, 2, 160, 1_000_000, "7")
	if err := s.advance(ctx, f, 200, clockAt, executorAt); err != nil {
		t.Fatal(err)
	}
	if got := s.recent(); len(got) != 1 || got[0].Block != 150 || s.claimed[2].Int64() != 1_000_000 {
		t.Errorf("the page read again: buys %+v, claims %+v", got, s.claimed)
	}
}

// A scan further behind than maxPages catches up over several reads, and
// says so until it has; the page keeps only the latest keepBuys buys.
func TestScannerCatchesUpOverReads(t *testing.T) {
	f := newFake()
	for b := uint64(0); b < 8; b++ {
		f.logs = append(f.logs, bought(executorAt, b+1, 100+b*pageBlocks*7, int64(b+1)))
	}
	s := newScanner(100, claimsAt, nodeAt)
	head := uint64(100 + (maxPages+10)*pageBlocks)
	err := s.advance(context.Background(), f, head, clockAt, executorAt)
	if err == nil || !strings.Contains(err.Error(), "catching up") || len(f.asked) != maxPages {
		t.Fatalf("first read: %d pages (%v)", len(f.asked), err)
	}
	if err := s.advance(context.Background(), f, head, clockAt, executorAt); err != nil || len(f.asked) != maxPages+11 {
		t.Fatalf("second read: %d pages (%v)", len(f.asked), err)
	}
	if got := s.recent(); len(got) != keepBuys || got[0].Round != 8 || got[keepBuys-1].Round != 4 {
		t.Errorf("kept %+v", got)
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

// With the RPC provider down, or rejecting the key and echoing it, neither
// the page, nor its JSON, nor the failure at start shows the key the URL
// carries, and each says what could not be read.
func TestAnRPCFailureHidesTheProvidersKey(t *testing.T) {
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	down := l.Addr().String()
	l.Close() // nothing listens there now: the connection is refused
	echo := echoingProvider(t, "synthetic-key-in-the-path", "synthetic-user", "synthetic-password", "synthetic-key-in-the-query")
	for _, url := range []string{
		"http://" + down + "/v3/synthetic-key-in-the-path?apikey=synthetic-key-in-the-query",
		"http://synthetic-user:synthetic-password@" + echo + "/v3/synthetic-key-in-the-path?apikey=synthetic-key-in-the-query",
	} {
		client, err := rpc.Dial(url)
		if err != nil {
			t.Fatal(err)
		}
		cfg := testConfig
		cfg.rpcURL = url
		srv := httptest.NewServer(handler(newExplorer(cfg, client)))
		for _, path := range []string{"/status.json", "/"} {
			r, err := http.Get(srv.URL + path)
			if err != nil {
				t.Fatal(err)
			}
			var b bytes.Buffer
			b.ReadFrom(r.Body)
			r.Body.Close()
			if strings.Contains(b.String(), "synthetic") || !strings.Contains(b.String(), "the chain&#39;s newest block") &&
				!strings.Contains(b.String(), "the chain's newest block") {
				t.Errorf("%s, with the provider at %s failing:\n%s", path, url, b.String())
			}
		}
		srv.Close()
		client.Close()
	}
	for _, url := range []string{"ws://" + down + "/ws/synthetic-key-for-websocket",
		"ws://synthetic-user:synthetic-password@" + echo + "/ws/synthetic-key-in-the-path?apikey=synthetic-key-in-the-query"} {
		_, err := rpc.Dial(url)
		if err == nil {
			t.Fatalf("%s: dialled", url)
		}
		if got := failure(err, url); strings.Contains(got, "synthetic") {
			t.Errorf("the failure at start shows the key: %s", got)
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
