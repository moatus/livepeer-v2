package chain

import (
	"strings"
	"testing"
)

// Each piece of an RPC URL that can carry a key is hidden wherever it
// appears, quoted in the URL or echoed bare by a provider; the scheme and
// the host stay.
func TestRedactHidesEveryPieceOfTheURLThatCanCarryAKey(t *testing.T) {
	for _, c := range []struct{ rpc, text string }{
		{"https://arb.example.io/v3/synthetic0path0key",
			`Post "https://arb.example.io/v3/synthetic0path0key": 401 invalid project id synthetic0path0key`},
		{"https://user:synthetic0password@arb.example.io/rpc?apikey=synthetic0query0key&x=1",
			`Post "https://user:***@arb.example.io/rpc?apikey=synthetic0query0key&x=1": bad key synthetic0query0key, password synthetic0password`},
		{"https://synthetic0user0key@arb.example.io/",
			`401 Unauthorized: unknown user synthetic0user0key`},
		{"https://arb.example.io/rpc?synthetic0bare0key",
			`rejected synthetic0bare0key`},
		{"https://arb.example.io/rpc/synthetic%2Fescaped0key",
			`Post "https://arb.example.io/rpc/synthetic%2Fescaped0key": unknown synthetic/escaped0key`},
		{"https://arb.example.io/rpc#synthetic0fragment",
			`fragment synthetic0fragment`},
		{"not a url synthetic0garbage",
			`dial not a url synthetic0garbage: synthetic0garbage`},
	} {
		got := Redact(c.text, c.rpc)
		if strings.Contains(got, "synthetic") {
			t.Errorf("%s: the key is in %q", c.rpc, got)
		}
		if strings.Contains(c.text, "arb.example.io") && !strings.Contains(got, "arb.example.io") {
			t.Errorf("%s: the host is gone from %q", c.rpc, got)
		}
	}
	// Path words shorter than a key, and the host, are left as they are.
	got := Redact("read rpc v3 on arb.example.io: refused", "https://arb.example.io/v3/rpc")
	if got != "read rpc v3 on arb.example.io: refused" {
		t.Errorf("short words were hidden: %q", got)
	}
	if Redact("text", "") != "text" {
		t.Error("an empty URL changed the text")
	}
}
