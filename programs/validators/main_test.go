package main

import (
	"strings"
	"testing"

	"github.com/ethereum/go-ethereum/common"
)

var names = map[string]common.Address{
	"V1": common.HexToAddress("0x01"), "V2": common.HexToAddress("0x02"),
	"M": common.HexToAddress("0x0a"), "K": common.HexToAddress("0x0b"),
}
var keyed = map[string]bool{"V1": true, "V2": true}

func TestParseScriptReadsVotesAndOptIns(t *testing.T) {
	lines, err := parseScript(strings.NewReader(`
# opt in, then vote
3 V1 opt-in
3 V2 opt-in
4 V1 M 500000
4 V2 K 0
`), names, keyed)
	if err != nil {
		t.Fatal(err)
	}
	if len(lines) != 4 || !lines[0].optIn || lines[0].round != 3 || lines[2].optIn ||
		lines[2].target != "M" || lines[2].score.Int64() != 500000 || lines[3].score.Sign() != 0 {
		t.Fatalf("lines %+v", lines)
	}
}

func TestParseScriptRefuses(t *testing.T) {
	for _, s := range []string{
		"0 V1 opt-in",             // no round 0
		"x V1 opt-in",             // not a round
		"3 V3 opt-in",             // no address for V3
		"3 M opt-in",              // M has no keystore
		"4 V1 X 0",                // no address for X
		"4 V1 M -1",               // not a score
		"4 V1 M",                  // neither shape
		"4 V1 M 1 2",              // neither shape
		"4 V1 M 0\n3 V1 opt-in\n", // rounds out of order
	} {
		if _, err := parseScript(strings.NewReader(s), names, keyed); err == nil {
			t.Errorf("parseScript(%q) accepted", s)
		}
	}
}

func TestEachLineIsDoneOnceWhenItsRoundComes(t *testing.T) {
	lines, err := parseScript(strings.NewReader("3 V1 opt-in\n4 V1 M 500000\n4 V2 K 0\n6 V1 M 0\n"), names, keyed)
	if err != nil {
		t.Fatal(err)
	}
	done := make([]bool, len(lines))
	got := func(current uint64) []int {
		var out []int
		for _, l := range due(lines, done, current) {
			out = append(out, l.number)
		}
		return out
	}
	steps := []struct {
		round uint64
		want  []int
	}{{2, nil}, {3, []int{1}}, {3, nil}, {5, []int{2, 3}}, {5, nil}, {9, []int{4}}, {9, nil}}
	for _, s := range steps {
		if g := got(s.round); len(g) != len(s.want) || (len(g) > 0 && g[0] != s.want[0]) || (len(g) > 1 && g[1] != s.want[1]) {
			t.Fatalf("round %d: lines %v, want %v", s.round, g, s.want)
		}
	}
}
