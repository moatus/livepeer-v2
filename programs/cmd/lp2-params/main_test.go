package main

import (
	"math/big"
	"testing"

	"github.com/ethereum/go-ethereum/accounts/abi"
	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/core/types"
	"github.com/ethereum/go-ethereum/crypto"
)

const pool = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"

func TestParseNamesEachGroup(t *testing.T) {
	for i, n := range []string{"fee", "emission", "schedule", "reward-delay", "set-size", "score-source",
		"unbonding", "buyback-cuts", "buyback-values", "buyback-executor"} {
		c, err := parse([]string{"withdraw", n})
		if err != nil {
			t.Fatalf("withdraw %s: %v", n, err)
		}
		if c.group.Int64() != int64(i+1) || name(c.group) != n {
			t.Errorf("%s is group %v, named %s", n, c.group, name(c.group))
		}
	}
	// A number with no group reaches the contract, which refuses it.
	c, err := parse([]string{"announce", "0", "100"})
	if err != nil || c.group.Sign() != 0 || name(c.group) != "0" {
		t.Fatalf("announce 0 100: %v %v", c.group, err)
	}
}

func TestAnnounceKeepsTheValues(t *testing.T) {
	c, err := parse([]string{"announce", "emission", "900000", "10000", "90000"})
	if err != nil {
		t.Fatal(err)
	}
	if got := shown(c.group, c.values); got != "900000/10000/90000" {
		t.Fatalf("shown %s", got)
	}
	b, err := parse([]string{"announce", "buyback-values", pool, "7200", "50000", "2"})
	if err != nil {
		t.Fatal(err)
	}
	if common.BigToAddress(b.values[0]) != common.HexToAddress(pool) || b.values[3].Int64() != 2 {
		t.Fatalf("values %v", b.values)
	}
	if got := shown(b.group, b.values); got != common.HexToAddress(pool).Hex()+"/7200/50000/2" {
		t.Fatalf("shown %s", got)
	}
	x, err := parse([]string{"announce", "10", pool})
	if err != nil {
		t.Fatal(err)
	}
	if got := shown(x.group, x.values); name(x.group) != "buyback-executor" || got != common.HexToAddress(pool).Hex() {
		t.Fatalf("group 10 shown as %s %s", name(x.group), got)
	}
	s, err := parse([]string{"show", "schedule", "101"})
	if err != nil || s.round.Cmp(big.NewInt(101)) != 0 {
		t.Fatalf("show schedule 101: %v %v", s.round, err)
	}
}

func TestParseRefuses(t *testing.T) {
	for _, words := range [][]string{
		{},
		{"announce"},                          // no group
		{"announce", "emission"},              // no values
		{"announce", "round-length", "100"},   // no such group name
		{"announce", "fee", "1.5"},            // not whole
		{"announce", "fee", "-1"},             // negative
		{"announce", "buyback-values", "0x1"}, // not an address
		{"withdraw", "fee", "1"},              // too many
		{"show", "fee", "1", "2"},             // too many
		{"change", "fee", "1"},                // no such action
	} {
		if _, err := parse(words); err == nil {
			t.Errorf("parse(%v) accepted", words)
		}
	}
}

// receipt is a mined transaction's receipt holding the given logs.
func receipt(logs ...*types.Log) *types.Receipt {
	return &types.Receipt{BlockNumber: big.NewInt(240), TxHash: common.HexToHash("0xabc"), Logs: logs}
}

// eventLog is a log as Parameters emits it: the event's id, the group as
// its one indexed topic, and the rest ABI-encoded as data.
func eventLog(t *testing.T, at common.Address, event string, group int64, data ...any) *types.Log {
	t.Helper()
	sigs := map[string]string{"Announced": "Announced(uint256,uint256[],uint256)", "Withdrawn": "Withdrawn(uint256,uint256)"}
	var args abi.Arguments
	for _, d := range data {
		ty := "uint256"
		if _, ok := d.([]*big.Int); ok {
			ty = "uint256[]"
		}
		typ, err := abi.NewType(ty, "", nil)
		if err != nil {
			t.Fatal(err)
		}
		args = append(args, abi.Argument{Type: typ})
	}
	packed, err := args.Pack(data...)
	if err != nil {
		t.Fatal(err)
	}
	return &types.Log{Address: at, Data: packed,
		Topics: []common.Hash{crypto.Keccak256Hash([]byte(sigs[event])), common.BigToHash(big.NewInt(group))}}
}

func nums(v ...int64) []*big.Int {
	out := make([]*big.Int, len(v))
	for i, x := range v {
		out[i] = big.NewInt(x)
	}
	return out
}

// The round an announcement prints is the one its own Announced event
// records, whatever else the receipt holds and whatever pending reads
// after it: here a later change (or none) is pending, and the line has
// no state read to take it from.
func TestAnnouncedReadsTheTransactionsOwnEvent(t *testing.T) {
	at := common.HexToAddress("0x5FbDB2315678afecb367f032d93F642f64180aa3")
	other := common.HexToAddress("0xe7f1725E7734CE288F8367e1Bb143E90bb3F0512")
	c, err := parse([]string{"announce", "schedule", "50000000000000000", "1000"})
	if err != nil {
		t.Fatal(err)
	}
	r := receipt(
		eventLog(t, other, "Announced", 3, nums(1, 2, 3), big.NewInt(99)),           // another contract
		eventLog(t, at, "Announced", 2, nums(900000, 10000, 90000), big.NewInt(98)), // another group
		eventLog(t, at, "Announced", 3, nums(7, 1000, 17), big.NewInt(18)),
	)
	got, err := announced(c, at, r)
	if err != nil {
		t.Fatal(err)
	}
	want := "announced: schedule 50000000000000000/1000, counts from round 18 (recorded as position/half-life/start 7/1000/17) (block 240, tx 0x0000000000000000000000000000000000000000000000000000000000000abc)"
	if got != want {
		t.Fatalf("got  %s\nwant %s", got, want)
	}
	// No Announced event of its own, or two: the line is not made up.
	if _, err := announced(c, at, receipt(r.Logs[0], r.Logs[1])); err == nil {
		t.Error("printed an announcement its transaction did not log")
	}
	if _, err := announced(c, at, receipt(r.Logs[2], r.Logs[2])); err == nil {
		t.Error("printed one of two Announced events")
	}
}

func TestWithdrawnReadsTheTransactionsOwnEvent(t *testing.T) {
	at := common.HexToAddress("0x5FbDB2315678afecb367f032d93F642f64180aa3")
	c, err := parse([]string{"withdraw", "fee"})
	if err != nil {
		t.Fatal(err)
	}
	r := receipt(eventLog(t, at, "Withdrawn", 1, big.NewInt(18)))
	got, err := withdrawn(c, at, r)
	if err != nil {
		t.Fatal(err)
	}
	if want := "withdrawn: fee, the change that would have counted from round 18; it never counts (block 240, tx 0x0000000000000000000000000000000000000000000000000000000000000abc)"; got != want {
		t.Fatalf("got  %s\nwant %s", got, want)
	}
	// Another group's or another contract's Withdrawn is not this one.
	if _, err := withdrawn(c, at, receipt(eventLog(t, at, "Withdrawn", 2, big.NewInt(18)))); err == nil {
		t.Error("printed another group's withdrawal")
	}
	if _, err := withdrawn(c, at, receipt(eventLog(t, common.HexToAddress("0x01"), "Withdrawn", 1, big.NewInt(18)))); err == nil {
		t.Error("printed another contract's withdrawal")
	}
}
