package main

import (
	"math/big"
	"testing"

	"github.com/ethereum/go-ethereum/common"
)

const pool = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"

func TestParseNamesEachGroup(t *testing.T) {
	for i, n := range []string{"fee", "emission", "schedule", "reward-delay", "set-size", "score-source",
		"unbonding", "buyback-cuts", "buyback-values"} {
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
