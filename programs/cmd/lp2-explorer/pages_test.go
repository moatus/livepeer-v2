package main

import (
	"context"
	"html"
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

	"github.com/ethereum/go-ethereum/rpc"
	xhtml "golang.org/x/net/html"

	"lp2core/programs/chain"
	"lp2core/programs/index"
)

// htmlPaths are the pages: / and the five, one node's and one payer's.
var htmlPaths = []string{"/", "/network", "/orchestrators", "/orchestrators/" + nodeAt.Hex(), "/apps", "/apps/" + payerAt.Hex()}

// bannered says whether page shows the banner: the failure's category, the
// pointer to the instructions for getting an RPC URL, and the provider by
// scheme and host.
func bannered(page, category, provider string) bool {
	return strings.Contains(page, `<div class="banner" role="status">`) &&
		strings.Contains(page, html.EscapeString(category)) && strings.Contains(page, html.EscapeString(rpcHelp)) &&
		strings.Contains(page, ">"+provider+"<")
}

// A reference in a style: a url() or an @import.
var styleReferences = regexp.MustCompile(`(?i)url\(|@import`)

// The attributes that load or link to something.
var linking = map[string]bool{"href": true, "src": true, "srcset": true, "imagesrcset": true, "action": true, "formaction": true, "poster": true,
	"data": true, "background": true, "xlink:href": true, "cite": true, "ping": true, "manifest": true, "codebase": true}

// references is every reference out of the explorer a page makes: an
// attribute that links to something not a path of the explorer's own,
// whatever its quoting, a refresh that names a URL, or a reference in a
// style.
func references(page string) []string {
	var out []string
	z := xhtml.NewTokenizer(strings.NewReader(page))
	for {
		switch z.Next() {
		case xhtml.ErrorToken:
			return out
		case xhtml.StartTagToken, xhtml.SelfClosingTagToken:
			tok := z.Token()
			for _, a := range tok.Attr {
				k := strings.ToLower(a.Key)
				if a.Namespace != "" {
					k = a.Namespace + ":" + k
				}
				urls := []string{strings.TrimSpace(a.Val)}
				if k == "srcset" || k == "imagesrcset" {
					urls = candidates(a.Val)
				}
				switch {
				case linking[k] && slices.ContainsFunc(urls, outside):
					out = append(out, tok.Data+" "+k+"="+a.Val)
				case k == "content" && strings.Contains(strings.ToLower(a.Val), "url"):
					out = append(out, tok.Data+" "+k+"="+a.Val)
				case styleReferences.MatchString(a.Val):
					out = append(out, tok.Data+" "+k+"="+a.Val)
				}
			}
		}
	}
}

// outside says whether a URL is not a path of the explorer's own.
func outside(v string) bool {
	return !strings.HasPrefix(v, "/") || strings.HasPrefix(v, "//") || strings.HasPrefix(v, "/\\")
}

// candidates is each URL a source set names: its candidates are split at
// commas, and each URL is a candidate's first word. A comma inside a URL
// leaves a piece that is not a path, so it is counted as outside.
func candidates(srcset string) []string {
	out := []string{}
	for _, c := range strings.Split(srcset, ",") {
		if words := strings.Fields(c); len(words) > 0 {
			out = append(out, words[0])
		}
	}
	if len(out) == 0 {
		out = append(out, "")
	}
	return out
}

// The guard reads every candidate of a source set, not only the first.
func TestReferencesReadEverySourceSetCandidate(t *testing.T) {
	for page, want := range map[string]bool{
		`<img srcset="/one.png 1x, https://example.invalid/two.png 2x" alt="">`:   true,
		`<img srcset="/one.png 1x,//example.invalid/two.png 2x" alt="">`:          true,
		`<link rel="preload" imagesrcset="/a.png 1x, http://x.invalid/b.png 2x">`: true,
		`<img srcset="" alt="">`:                         true,
		`<img srcset="/one.png 1x, /two.png 2x" alt="">`: false,
	} {
		if got := len(references(page)) > 0; got != want {
			t.Errorf("%s: flagged %v, want %v", page, got, want)
		}
	}
}

// Every page is served with no script and nothing from outside the
// explorer, its stylesheet and templates from the binary: the explorer
// runs from a folder that holds none of them.
func TestEveryPageIsServedFromTheBinaryAlone(t *testing.T) {
	t.Chdir(t.TempDir())
	for _, mine := range []bool{true, false} {
		cfg := testConfig
		if !mine {
			cfg.node = nil
		}
		f := newFake()
		f.logs = deployment()
		e := newTestExplorer(t, f, cfg)
		h := handler(e)
		for _, read := range []bool{false, true} {
			if read {
				e.cycle(context.Background())
			}
			for _, path := range htmlPaths {
				w := httptest.NewRecorder()
				h.ServeHTTP(w, httptest.NewRequest("GET", path, nil))
				page, low := w.Body.String(), strings.ToLower(w.Body.String())
				if w.Code != http.StatusOK || !strings.HasPrefix(w.Header().Get("Content-Type"), "text/html") {
					t.Errorf("%s (node %v, read %v): %d %s", path, mine, read, w.Code, page)
				}
				for _, bad := range []string{"<script", "javascript:", "<iframe", "<object", "<embed", "<style"} {
					if strings.Contains(low, bad) {
						t.Errorf("%s (node %v, read %v) carries %s", path, mine, read, bad)
					}
				}
				if regexp.MustCompile(`(?i)<[^>]*\son[a-z]+\s*=`).MatchString(page) {
					t.Errorf("%s (node %v, read %v) carries an event handler", path, mine, read)
				}
				for _, r := range references(page) {
					t.Errorf("%s (node %v, read %v) refers outside the explorer: %s", path, mine, read, r)
				}
				if !strings.Contains(page, `<link rel="stylesheet" href="/style.css">`) {
					t.Errorf("%s (node %v, read %v) does not load the stylesheet", path, mine, read)
				}
			}
		}
		w := httptest.NewRecorder()
		h.ServeHTTP(w, httptest.NewRequest("GET", "/style.css", nil))
		if w.Code != http.StatusOK || w.Header().Get("Content-Type") != "text/css; charset=utf-8" ||
			w.Body.String() != string(stylesheet) || len(stylesheet) == 0 || styleReferences.MatchString(w.Body.String()) {
			t.Errorf("the stylesheet: %d %s, %d bytes", w.Code, w.Header().Get("Content-Type"), w.Body.Len())
		}
	}
	if _, err := os.Stat("assets"); err == nil {
		t.Fatal("the test runs beside the assets")
	}
}

// The stylesheet holds the beta explorer's custom properties, with its
// licence, for both themes.
func TestTheStylesheetTakesTheBetasLook(t *testing.T) {
	css := string(stylesheet)
	for _, want := range []string{"Copyright (c) 2022 Livepeer Inc", "--green-bright: #40bf86;", "--panel: oklch(0.17 0 0);",
		"--hairline: oklch(1 0 0 / 8%);", "--series-1: #3987e5;", ".chart rect { fill: var(--green-fill); }", "@media (prefers-color-scheme: light)", "--panel: oklch(0.993 0.001 286);"} {
		if !strings.Contains(css, want) {
			t.Errorf("the stylesheet lacks %q", want)
		}
	}
}

// switching is a provider that can be swapped while the explorer reads.
type switching struct {
	mu sync.Mutex
	c  index.Caller
}

func (s *switching) BatchCallContext(ctx context.Context, b []rpc.BatchElem) error {
	s.mu.Lock()
	c := s.c
	s.mu.Unlock()
	return c.BatchCallContext(ctx, b)
}

func (s *switching) set(c index.Caller) {
	s.mu.Lock()
	s.c = c
	s.mu.Unlock()
}

// silent starts a provider that accepts connections and never answers,
// and returns its host.
func silent(t *testing.T) string {
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	var mu sync.Mutex
	var conns []net.Conn
	t.Cleanup(func() {
		l.Close()
		mu.Lock()
		for _, c := range conns {
			c.Close()
		}
		mu.Unlock()
	})
	go func() {
		for {
			c, err := l.Accept()
			if err != nil {
				return
			}
			mu.Lock()
			conns = append(conns, c)
			mu.Unlock()
		}
	}()
	return l.Addr().String()
}

// The deadlines of the banner, scaled down from a 15-second cycle and a
// 30-second request timeout: 45 seconds is cycle + timeout. A deadline
// checked is that, and a tenth more for the test's own loads of every page
// that see it.
const (
	testCycle   = 300 * time.Millisecond
	testTimeout = 600 * time.Millisecond
	testWithin  = (testCycle + testTimeout) * 11 / 10
)

// running is an explorer reading through c in its loop, every testCycle,
// each batch timed out after testTimeout, a 429 waited out for 50 ms as a
// provider's Retry-After asks; it stops when the test ends.
func running(t *testing.T, c index.Caller, cfg config) (*explorer, http.Handler) {
	e := newTestExplorer(t, c, cfg)
	e.every, e.src.Timeout = testCycle, testTimeout
	e.src.Retry = func() time.Duration { return 50 * time.Millisecond }
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() { e.run(ctx); close(done) }()
	t.Cleanup(func() { cancel(); <-done })
	return e, handler(e)
}

// waitPages loads a page until it satisfies ok, then every page, which
// must too, and fails the test if that takes longer than within. Every load
// must answer at once: well before one request to the provider could time
// out.
func waitPages(t *testing.T, h http.Handler, what string, within time.Duration, ok func(page string) bool) time.Duration {
	t.Helper()
	load := func(path string) string {
		at := time.Now()
		w := httptest.NewRecorder()
		h.ServeHTTP(w, httptest.NewRequest("GET", path, nil))
		if took := time.Since(at); took > testTimeout/4 || w.Code != http.StatusOK {
			t.Fatalf("%s answered %d in %v", path, w.Code, took)
		}
		return w.Body.String()
	}
	start := time.Now()
	for {
		page := load(htmlPaths[0])
		took := time.Since(start)
		if ok(page) {
			for _, path := range htmlPaths {
				if page := load(path); !ok(page) {
					t.Fatalf("%s: %s is not yet:\n%s", what, path, page)
				}
			}
			return took
		}
		if took > within {
			t.Fatalf("%s: not within %v:\n%s", what, within, page)
		}
		time.Sleep(2 * time.Millisecond)
	}
}

// With the provider unreachable, silent, or answering 429 to everything,
// from the start and after it had answered, every page answers at once and
// shows the banner within one cycle and one request's timeout; once the
// provider answers again the banner goes within a cycle of its first answer.
func TestTheBannerShowsWithinACycleAndATimeout(t *testing.T) {
	down := closedHost(t)
	keyed := func(host string) string {
		return "http://synthetic-user:synthetic-password@" + host + "/v3/synthetic-key?apikey=synthetic-key"
	}
	for _, c := range []struct{ name, url, category string }{
		{"unreachable", keyed(down), "could not be reached"},
		{"silent", keyed(silent(t)), "did not answer in time"},
		{"rate-limited", keyed(answering(t, "429 Too Many Requests")), "rate-limited the request (HTTP 429)"},
	} {
		t.Run(c.name, func(t *testing.T) {
			category := "the RPC provider " + provider(c.url) + " " + c.category
			shows := func(page string) bool {
				return bannered(page, category, provider(c.url)) && !strings.Contains(page, "synthetic")
			}
			failing, err := chain.DialRPC(context.Background(), c.url)
			if err != nil {
				t.Fatal(err)
			}
			t.Cleanup(failing.Close)
			cfg := testConfig
			cfg.rpcURL = c.url

			// From the start.
			_, h := running(t, failing, cfg)
			took := waitPages(t, h, "the banner from the start", testWithin, shows)
			t.Logf("from the start: the banner in %v", took)

			// After the provider answered, then failed, then answered again.
			f := newFake()
			f.logs = deployment()
			sw := &switching{c: f}
			_, h = running(t, sw, cfg)
			waitPages(t, h, "the first snapshot", testWithin, func(page string) bool {
				return strings.Contains(page, "Read at block 1000") && !strings.Contains(page, `class="banner"`)
			})
			sw.set(failing)
			took = waitPages(t, h, "the banner after an answer", testWithin, func(page string) bool {
				return shows(page) && strings.Contains(page, "The figures below are as of block 1000")
			})
			t.Logf("after an answer: the banner in %v", took)
			// Recovery is timed from the provider's first answer.
			back := &firstAnswer{Caller: f}
			sw.set(back)
			waitPages(t, h, "the banner cleared", testWithin, func(page string) bool {
				return !strings.Contains(page, `class="banner"`)
			})
			cleared := time.Now()
			first := back.at()
			if first.IsZero() || cleared.Sub(first) > testCycle {
				t.Fatalf("the banner cleared %v after the provider's first answer (%v), more than a cycle", cleared.Sub(first), first)
			}
			t.Logf("recovered: the banner cleared within %v of the first answer", cleared.Sub(first))
		})
	}
}

// firstAnswer is a provider that notes when it first answered.
type firstAnswer struct {
	index.Caller
	mu    sync.Mutex
	first time.Time
}

func (a *firstAnswer) BatchCallContext(ctx context.Context, b []rpc.BatchElem) error {
	err := a.Caller.BatchCallContext(ctx, b)
	a.mu.Lock()
	if err == nil && a.first.IsZero() {
		a.first = time.Now()
	}
	a.mu.Unlock()
	return err
}

func (a *firstAnswer) at() time.Time {
	a.mu.Lock()
	defer a.mu.Unlock()
	return a.first
}

// closedHost is a host where nothing listens: a connection is refused.
func closedHost(t *testing.T) string {
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	host := l.Addr().String()
	l.Close()
	return host
}

// gated answers requests for logs only as many times as the test allows.
type gated struct {
	*fakeChain
	mu      sync.Mutex
	cond    *sync.Cond
	allowed int // requests for logs it may answer; -1 any number
}

func (g *gated) BatchCallContext(ctx context.Context, b []rpc.BatchElem) error {
	for _, r := range b {
		if r.Method != "eth_getLogs" {
			continue
		}
		g.mu.Lock()
		for g.allowed == 0 && ctx.Err() == nil {
			g.cond.Wait()
		}
		if g.allowed > 0 {
			g.allowed--
		}
		g.mu.Unlock()
		break
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	return g.fakeChain.BatchCallContext(ctx, b)
}

func (g *gated) allow(n int) {
	g.mu.Lock()
	g.allowed = n
	g.mu.Unlock()
	g.cond.Broadcast()
}

// A figure shown as 0.
var zero = regexp.MustCompile(`>0(\.0*)?( USDC| LPT| ETH|%)?<`)

var progressShown = regexp.MustCompile(`block (\d+) of 1000`)

// While the index catches up through a provider that answers slowly, every
// page shows "block X of Y", X rising between two loads, and each figure
// as catching up, never 0.
func TestPagesShowTheIndexCatchingUp(t *testing.T) {
	f := newFake()
	f.logs = deployment()
	g := &gated{fakeChain: f}
	g.cond = sync.NewCond(&g.mu)
	ix, err := index.Open(t.TempDir(), testConfig.first, 100)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(ix.Close)
	e := newExplorer(testConfig, index.NewSource(g, 10_000), ix)
	e.stop = func(err error) { t.Errorf("the explorer stopped: %v", err) }
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() { e.run(ctx); close(done) }()
	t.Cleanup(func() { cancel(); g.cond.Broadcast(); <-done })
	h := handler(e)

	through := func() uint64 {
		hd := e.served().Head
		if hd.Index.Through == nil {
			return 0
		}
		return *hd.Index.Through
	}
	// step lets the index read one page's logs and commit it.
	step := func() {
		before := through()
		g.allow(1)
		for deadline := time.Now().Add(5 * time.Second); through() == before; time.Sleep(time.Millisecond) {
			if time.Now().After(deadline) {
				t.Fatal("the index committed no page")
			}
		}
	}
	shown := func() uint64 {
		var x uint64
		for i, path := range htmlPaths {
			page := get(t, h, path)
			m := progressShown.FindStringSubmatch(page)
			if m == nil || !strings.Contains(page, ">"+catchingUp+"<") || zero.MatchString(page) {
				t.Fatalf("%s while the index catches up:\n%s", path, page)
			}
			v, _ := strconv.ParseUint(m[1], 10, 64)
			if i > 0 && v != x {
				t.Fatalf("%s shows block %d, another page %d", path, v, x)
			}
			x = v
		}
		return x
	}
	step()
	first := shown()
	step()
	if second := shown(); second <= first || first < testConfig.first {
		t.Errorf("the index's progress shown: %d, then %d", first, second)
	}
	g.allow(-1)
	waitPages(t, h, "the index caught up", 5*time.Second, func(page string) bool {
		return strings.Contains(page, "caught up at block 1000") && !strings.Contains(page, catchingUp)
	})
}

// A labelled address shows its name on every page it is on, a simulated
// one is marked there, the scenario shows on every page, and a changed
// labels file shows at the next cycle.
func TestLabelsShowOnEveryPage(t *testing.T) {
	f := newFake()
	f.logs = deployment()
	labels := filepath.Join(t.TempDir(), "labels.json")
	write := func(body string) {
		if err := os.WriteFile(labels, []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	write(`{"scenario": "two payers", "accounts": {"` + nodeAt.Hex() + `": {"name": "Node-label"}, "` +
		payerAt.Hex() + `": {"name": "Payer-label", "simulated": true}, "` + validatorAt.Hex() + `": {"name": "Validator-label"}}}`)
	cfg := testConfig
	cfg.labels = labels
	e := newTestExplorer(t, f, cfg)
	h := handler(e)
	e.cycle(context.Background())
	node, payer, validator := "/orchestrators/"+nodeAt.Hex(), "/apps/"+payerAt.Hex(), "/network"
	on := map[string][]string{
		"Node-label":      {"/", "/orchestrators", node, payer},
		"Payer-label":     {"/", node, "/apps", payer},
		"Validator-label": {"/", node, "/orchestrators", validator},
	}
	simulated := `Payer-label <span class="mono dim" title="` + payerAt.Hex() + `">` + short(payerAt) + `</span><span class="badge sim">simulated</span>`
	for name, paths := range on {
		for _, path := range paths {
			page := get(t, h, path)
			if !strings.Contains(page, name) {
				t.Errorf("%s does not name %s", path, name)
			}
			if name == "Payer-label" && !strings.Contains(page, simulated) && !strings.Contains(page, `<h1>Payer-label</h1>`) {
				t.Errorf("%s does not mark the payer simulated", path)
			}
		}
	}
	if page := get(t, h, payer); !strings.Contains(page, `<span class="badge sim">simulated</span>`) {
		t.Errorf("the payer's page does not mark it simulated")
	}
	for _, path := range htmlPaths {
		if page := get(t, h, path); !strings.Contains(page, `<span class="badge">Scenario: two payers</span>`) {
			t.Errorf("%s does not show the scenario", path)
		}
	}

	// Every address a page shows is named: the payout address, the
	// operator key (the node's own here) and the buyback executor too.
	write(`{"accounts": {"` + payoutAt.Hex() + `": {"name": "Payout-label"}, "` + executorAt.Hex() + `": {"name": "Executor-label", "simulated": true}, "` +
		nodeAt.Hex() + `": {"name": "Node-label"}}}`)
	e.readLabels()
	for path, want := range map[string][]string{
		node: {`<dt>Payout address</dt><dd>Payout-label <span class="mono dim">` + payoutAt.Hex(),
			`<dt>Operator key</dt><dd>Node-label <span class="mono dim">` + nodeAt.Hex(),
			`<dt>Buyback executor in force</dt><dd>Executor-label <span class="mono dim">` + executorAt.Hex() + `</span><span class="badge sim">simulated</span>`},
		"/network": {`<th>Buyback executor in force</th><td>Executor-label <span class="mono dim">` + executorAt.Hex() + `</span><span class="badge sim">simulated</span>`},
	} {
		page := get(t, h, path)
		for _, w := range want {
			if !strings.Contains(page, w) {
				t.Errorf("%s lacks %q:\n%s", path, w, page)
			}
		}
	}
}

// A changed labels file shows within one cycle on every page, the names and
// the scenario together, while the provider answers 429 to everything: the
// labels are read on their own, not with the chain.
func TestARenameShowsWithinACycleWhileTheProviderFails(t *testing.T) {
	labels := filepath.Join(t.TempDir(), "labels.json")
	write := func(body string) {
		if err := os.WriteFile(labels, []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	write(`{"scenario": "Before", "accounts": {"` + payerAt.Hex() + `": {"name": "Before-payer"}, "` + nodeAt.Hex() + `": {"name": "Before-node"}}}`)
	url := "http://" + answering(t, "429 Too Many Requests")
	limited, err := chain.DialRPC(context.Background(), url)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(limited.Close)
	f := newFake()
	f.logs = deployment()
	sw := &switching{c: f}
	cfg := testConfig
	cfg.labels, cfg.rpcURL = labels, url
	_, h := running(t, sw, cfg)
	waitPages(t, h, "the first snapshot", testWithin, func(page string) bool {
		return strings.Contains(page, "Read at block 1000") && strings.Contains(page, "Scenario: Before")
	})
	sw.set(limited)
	waitPages(t, h, "the banner", testWithin, func(page string) bool { return strings.Contains(page, "HTTP 429") })

	write(`{"scenario": "After", "accounts": {"` + payerAt.Hex() + `": {"name": "After-payer"}, "` + nodeAt.Hex() + `": {"name": "After-node"}}}`)
	took := waitPages(t, h, "the rename", testCycle*11/10, func(page string) bool {
		after := strings.Contains(page, "Scenario: After")
		if after && strings.Contains(page, "Before-") || !after && strings.Contains(page, "After-") {
			t.Fatalf("a page of two labels files:\n%s", page)
		}
		return after && strings.Contains(page, "HTTP 429")
	})
	t.Logf("the rename shown in %v", took)
	for path, names := range map[string][]string{"/": {"After-payer", "After-node"}, "/orchestrators/" + nodeAt.Hex(): {"After-payer", "After-node"},
		"/orchestrators": {"After-node"}, "/apps": {"After-payer"}, "/apps/" + payerAt.Hex(): {"After-payer", "After-node"}} {
		page := get(t, h, path)
		for _, name := range names {
			if !strings.Contains(page, name) {
				t.Errorf("%s after the rename lacks %s:\n%s", path, name, page)
			}
		}
	}
}

// A page is of one block: a snapshot published while the page is written
// shows on the next page, not in part of this one.
func TestAPageIsOfOneBlock(t *testing.T) {
	f := newFake()
	f.logs = deployment()
	e := newTestExplorer(t, f, testConfig)
	h := handler(e)
	e.cycle(context.Background())
	first := e.snap
	f.head = 1001
	f.logs = append(f.logs, settled(1001, 12, nodeAt, 4_321_000, 2_000_000, 2_321_000))
	e.cycle(context.Background())
	next := e.snap
	if next == first || next.block != 1001 || next.network.TotalFees.big().Int64() < 4_321_000 {
		t.Fatalf("no second snapshot: %d", next.block)
	}
	for _, path := range htmlPaths {
		e.mu.Lock()
		e.snap = first
		e.mu.Unlock()
		// The loop publishes the next snapshot while the page is written.
		e.now = func() time.Time {
			e.mu.Lock()
			e.snap = next
			e.mu.Unlock()
			return time.Now()
		}
		page := get(t, h, path)
		if !strings.Contains(page, "Read at block 1000") || strings.Contains(page, "4.321") || strings.Contains(page, "2.321") {
			t.Errorf("%s mixes two blocks:\n%s", path, page)
		}
	}
}
