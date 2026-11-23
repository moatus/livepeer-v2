package main

import (
	"bufio"
	"context"
	"fmt"
	"math/big"
	"net"
	"net/http"
	"strings"
	"testing"
	"time"

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

// An RPC request that fails, to a provider that is down or to one that
// rejects the key and echoes it in its answer, is reported with the action
// and the provider's host, and with none of what its URL carries as a key:
// the user information, the path or the query.
func TestAnRPCFailureHidesTheProvidersKey(t *testing.T) {
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	down := l.Addr().String()
	l.Close() // nothing listens there now: the connection is refused
	for _, c := range []struct{ host, rpc string }{
		{down, "http://" + down + "/v3/synthetic-key-in-the-path"},
		{down, "https://someone:synthetic-password@" + down + "/rpc?apikey=synthetic-key-in-the-query"},
		{down, "ws://" + down + "/ws/synthetic-key-for-websocket"},
		echoingProvider(t, "http://%s/v3/synthetic-key-in-the-path", "synthetic-key-in-the-path"),
		echoingProvider(t, "http://synthetic-user:synthetic-password@%s/rpc?apikey=synthetic-key-in-the-query",
			"synthetic-user", "synthetic-password", "synthetic-key-in-the-query"),
		echoingProvider(t, "ws://%s/ws/synthetic-key-for-websocket", "synthetic-key-for-websocket"),
	} {
		ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
		a, err := parse([]string{"state", node, other})
		if err != nil {
			t.Fatal(err)
		}
		_, err = dispatch(ctx, a, config{rpcURL: c.rpc, controller: other})
		cancel()
		if err == nil {
			t.Fatalf("%s: no error from a provider that is down or rejects the key", c.rpc)
		}
		got := message(a.name, err, c.rpc)
		if strings.Contains(got, "synthetic") {
			t.Errorf("the key is printed: %s", got)
		}
		if !strings.HasPrefix(got, "lp2-stake state: ") || !strings.Contains(got, c.host) {
			t.Errorf("the failure does not name the action and the host: %s", got)
		}
		t.Logf("%s: %s", c.rpc, got)
	}
}

// echoingProvider starts a provider that answers every request with 401,
// the keys it was given in its status line and its body, as a provider
// that rejects a key and quotes it does. It returns its host and urlFormat
// with the host filled in.
func echoingProvider(t *testing.T, urlFormat string, keys ...string) struct{ host, rpc string } {
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
	host := l.Addr().String()
	return struct{ host, rpc string }{host, fmt.Sprintf(urlFormat, host)}
}
