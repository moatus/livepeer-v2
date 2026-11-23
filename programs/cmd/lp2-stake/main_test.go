package main

import (
	"math/big"
	"testing"

	"github.com/ethereum/go-ethereum/common"
)

const node = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
const other = "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"

func TestParseGivesEachActionItsCall(t *testing.T) {
	for _, c := range []struct {
		words          []string
		target, method string
		args           int
	}{
		{[]string{"bond", node, "100"}, "ledger", "bond", 2},
		{[]string{"unbond", node, "100"}, "ledger", "unbond", 2},
		{[]string{"transfer", node, other, "100"}, "ledger", "transferBond", 3},
		{[]string{"unbond-transfer", node, other, "40"}, "ledger", "unbondTransfer", 3},
		{[]string{"rebond", node, "40"}, "ledger", "rebond", 2},
		{[]string{"withdraw-unbonded"}, "ledger", "withdrawUnbonded", 0},
		{[]string{"set-cut", node, "200000"}, "ledger", "setRewardCut", 2},
		{[]string{"claim", node, "2"}, "claims", "claim", 2},
		{[]string{"withdraw-parts", node, "2,3"}, "claims", "withdrawParts", 2},
		{[]string{"withdraw-validator-parts", node, "2"}, "claims", "withdrawValidatorParts", 2},
		{[]string{"mint-shares", "2"}, "claims", "mintRoundShares", 1},
		{[]string{"set-payout", node, other}, "registry", "setPayout", 2},
		{[]string{"set-operator", node, other}, "registry", "setOperator", 2},
		{[]string{"accept-operator", node}, "registry", "acceptOperator", 1},
		{[]string{"register"}, "registry", "register", 0},
		{[]string{"send-lpt", other, "100"}, "lpt", "transfer", 2},
		{[]string{"approve", "100"}, "lpt", "approve", 1},
		{[]string{"claim-due", node}, "", "", 1},
		{[]string{"withdraw-due", node}, "", "", 1},
		{[]string{"state", node, other}, "", "", 2},
		{[]string{"join", "http://127.0.0.1:8940", "https://node.example:8935"}, "", "", 0},
		{[]string{"new-key"}, "", "", 0},
	} {
		a, err := parse(c.words)
		if err != nil {
			t.Errorf("parse(%v): %v", c.words, err)
			continue
		}
		if a.target != c.target || a.method != c.method || len(a.args) != c.args {
			t.Errorf("parse(%v) = %s.%s with %d args", c.words, a.target, a.method, len(a.args))
		}
	}
}

func TestParseKeepsTheValues(t *testing.T) {
	a, err := parse([]string{"withdraw-parts", node, "2,2,9"})
	if err != nil {
		t.Fatal(err)
	}
	rounds := a.args[1].([]*big.Int)
	if a.args[0].(common.Address) != common.HexToAddress(node) || len(rounds) != 3 || rounds[2].Int64() != 9 {
		t.Fatalf("args %v", a.args)
	}
	b, err := parse([]string{"bond", node, "100000000000000000000"})
	if err != nil {
		t.Fatal(err)
	}
	if b.bond == nil || b.bond.String() != "100000000000000000000" {
		t.Fatalf("a bond approves its amount, got %v", b.bond)
	}
}

func TestParseRefuses(t *testing.T) {
	for _, words := range [][]string{
		{},
		{"stake", node, "1"},           // no such action
		{"bond", node},                 // too few
		{"bond", "N", "1"},             // not an address
		{"bond", node, "1.5"},          // not whole wei
		{"claim", node, "-1"},          // negative
		{"withdraw-parts", node, "2,"}, // an empty round
		{"withdraw-unbonded", "x"},     // too many
		{"set-payout", node},           // no payout
		{"set-operator", node, "K2"},   // not an address
		{"accept-operator"},            // no node
		{"register", node},             // registers only the key's own address
		{"send-lpt", other},            // no amount
		{"approve", node, "1"},         // StakeLedger is the only spender
		{"claim-due"},                  // no node
		{"withdraw-due", "N"},          // not an address
		{"state", node},                // no holder
		{"join", "127.0.0.1:8940", "https://node.example:8935"}, // not a URL
		{"new-key", node}, // takes nothing
	} {
		if _, err := parse(words); err == nil {
			t.Errorf("parse(%v) accepted", words)
		}
	}
}
