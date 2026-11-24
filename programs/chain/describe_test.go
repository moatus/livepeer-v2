package chain

import (
	"bufio"
	"context"
	"errors"
	"fmt"
	"net"
	"net/http"
	"net/url"
	"testing"
	"time"
)

// The password of the round 3 reproduction: a synthetic Basic-auth
// password with an escaped character, which a rejecting provider echoes as
// it is written in the URL.
const encodedPassword = "synthetic%40password"

// Each way the provider can fail is told by its fixed category, with the
// step wrapped around it and the provider's scheme and host, and nothing
// the provider wrote: the whole text is known in advance.
func TestDescribeTellsAProvidersFailureByItsCategoryOnly(t *testing.T) {
	down := closedPort(t)
	said := "synthetic-user " + encodedPassword + " synthetic@password synthetic-key"
	status := provider(t, "401 rejected "+said, `{"jsonrpc":"2.0","id":1,"error":{"code":-32001,"message":"invalid key `+said+`"}}`)
	coded := provider(t, "200 OK", `{"jsonrpc":"2.0","id":1,"error":{"code":-32001,"message":"invalid key `+said+`"}}`)
	garbled := provider(t, "200 OK", said)
	limited := provider(t, "429 Too Many Requests "+said, said)
	keyed := func(host string) string {
		return "http://synthetic-user:" + encodedPassword + "@" + host + "/v3/synthetic-key"
	}
	for _, c := range []struct{ rpc, want string }{
		{keyed(down), "read bondAt: the RPC provider http://" + down + " could not be reached"},
		{keyed(status), "read bondAt: the RPC provider http://" + status + " refused the request (HTTP 401)"},
		{keyed(coded), "read bondAt: the RPC provider http://" + coded + " answered with an error (code -32001)"},
		{keyed(garbled), "read bondAt: the RPC provider http://" + garbled + "'s answer could not be read"},
		{keyed(limited), "read bondAt: the RPC provider http://" + limited + " rate-limited the request (HTTP 429)"},
		{"ws://synthetic-user:" + encodedPassword + "@" + limited + "/ws/synthetic-key",
			"read bondAt: the RPC provider ws://" + limited + " rate-limited the request (HTTP 429)"},
		{"ws://synthetic-user:" + encodedPassword + "@" + status + "/ws/synthetic-key",
			"read bondAt: the RPC provider ws://" + status + " refused the request (HTTP 401)"},
	} {
		got := Describe(fmt.Errorf("read bondAt: %w", ask(t, c.rpc)), c.rpc)
		if got != c.want {
			t.Errorf("%s:\n got %q\nwant %q", c.rpc, got, c.want)
		}
	}

	// A batch of failures is told one by one; a failure of this program's
	// own, and one from another service, keep their words.
	rpcURL := keyed(status)
	joined := errors.Join(fmt.Errorf("bondAt: %w", ask(t, rpcURL)), errors.New("the current round is 3"))
	if got, want := Describe(joined, rpcURL), "bondAt: the RPC provider http://"+status+" refused the request (HTTP 401)\nthe current round is 3"; got != want {
		t.Errorf("a batch:\n got %q\nwant %q", got, want)
	}
	other := &url.Error{Op: "Post", URL: "http://sender.example/join", Err: errors.New("connection refused")}
	if got := Describe(fmt.Errorf("join: %w", other), rpcURL); got != "join: "+other.Error() {
		t.Errorf("another service's failure was told as the provider's: %q", got)
	}
	if got := Describe(errors.New("keystore k.json: could not decrypt"), rpcURL); got != "keystore k.json: could not decrypt" {
		t.Errorf("a local failure changed: %q", got)
	}
	if Describe(nil, rpcURL) != "" {
		t.Error("no error is told as something")
	}
}

// ask makes one call to the provider at rpcURL and returns its error.
func ask(t *testing.T, rpcURL string) error {
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	client, err := DialRPC(ctx, rpcURL)
	if err != nil {
		return err
	}
	defer client.Close()
	var block string
	if err := client.CallContext(ctx, &block, "eth_blockNumber"); err != nil {
		return err
	}
	t.Fatalf("%s answered %s", rpcURL, block)
	return nil
}

// closedPort is an address nothing listens on.
func closedPort(t *testing.T) string {
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer l.Close()
	return l.Addr().String()
}

// provider answers every request with statusLine and body, whatever they
// carry, and returns its host.
func provider(t *testing.T, statusLine, body string) string {
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
				fmt.Fprintf(conn, "HTTP/1.1 %s\r\nContent-Type: application/json\r\nContent-Length: %d\r\nConnection: close\r\n\r\n%s",
					statusLine, len(body), body)
			}()
		}
	}()
	return l.Addr().String()
}

// A provider that redirects is a failure told by its status, and its
// redirect is not followed: the second host, whose answer carries a marker
// in a Location that cannot be read (the review's probe), is never asked,
// and nothing it would write reaches the text. Over WebSocket a redirect is
// refused by the handshake, and told the same way.
func TestARedirectIsNotFollowed(t *testing.T) {
	asked := make(chan string, 8)
	second := respond(t, asked, "307 Temporary Redirect\r\nLocation: http://[provider-written-marker")
	first := respond(t, nil, "307 Temporary Redirect\r\nLocation: http://"+second+"/followed")
	moved := respond(t, nil, "302 Found\r\nLocation: http://"+second+"/followed")
	unreadable := respond(t, nil, "307 Temporary Redirect\r\nLocation: http://[provider-written-marker")
	for _, c := range []struct{ rpc, want string }{
		{"http://synthetic-user:" + encodedPassword + "@" + first + "/v3/synthetic-key",
			"read bondAt: the RPC provider http://" + first + " redirected the request (HTTP 307)"},
		{"http://" + moved + "/v3/synthetic-key",
			"read bondAt: the RPC provider http://" + moved + " redirected the request (HTTP 302)"},
		{"http://" + unreadable + "/v3/synthetic-key",
			"read bondAt: the RPC provider http://" + unreadable + " redirected the request (HTTP 307)"},
		{"ws://synthetic-user:" + encodedPassword + "@" + moved + "/ws/synthetic-key",
			"read bondAt: the RPC provider ws://" + moved + " redirected the request (HTTP 302)"},
	} {
		got := Describe(fmt.Errorf("read bondAt: %w", ask(t, c.rpc)), c.rpc)
		if got != c.want {
			t.Errorf("%s:\n got %q\nwant %q", c.rpc, got, c.want)
		}
	}
	select {
	case path := <-asked:
		t.Errorf("the redirect was followed to %s%s", second, path)
	default:
	}
}

// respond answers every request with the status line and headers in head
// and an empty body, sends the path asked for to asked (when it is not
// nil), and returns its host.
func respond(t *testing.T, asked chan<- string, head string) string {
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
				req, err := http.ReadRequest(bufio.NewReader(conn))
				if err == nil && asked != nil {
					asked <- req.URL.Path
				}
				fmt.Fprintf(conn, "HTTP/1.1 %s\r\nContent-Length: 0\r\nConnection: close\r\n\r\n", head)
			}()
		}
	}()
	return l.Addr().String()
}
