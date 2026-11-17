package main

import "testing"

func TestParseAcceptsADepositAndAnEarmark(t *testing.T) {
	r, err := parse("10000000", "20000", "0x70997970C51812dc3A010C7d01b50e0d17dc79C8")
	if err != nil {
		t.Fatal(err)
	}
	if r.deposit.String() != "10000000" || r.earmark.String() != "20000" ||
		r.node.Hex() != "0x70997970C51812dc3A010C7d01b50e0d17dc79C8" {
		t.Fatalf("parse = %+v", r)
	}
}

func TestParseFundsADepositAloneWithNoNode(t *testing.T) {
	r, err := parse("1000000", "0", "0x70997970C51812dc3A010C7d01b50e0d17dc79C8")
	if err != nil {
		t.Fatal(err)
	}
	if r.node.Hex() != "0x0000000000000000000000000000000000000000" {
		t.Fatalf("a deposit alone names no node, got %s", r.node.Hex())
	}
}

func TestParseRefuses(t *testing.T) {
	for _, c := range []struct{ deposit, earmark, node string }{
		{"0", "20000", ""}, // an earmark needs a node
		{"0", "0", ""},     // nothing to fund
		{"-1", "0", ""},    // negative
		{"1.5", "0", ""},   // not whole micro-USDC
		{"1", "x", "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"},
	} {
		if _, err := parse(c.deposit, c.earmark, c.node); err == nil {
			t.Errorf("parse(%q, %q, %q) accepted", c.deposit, c.earmark, c.node)
		}
	}
}
