package main

import (
	"bytes"
	"context"
	"crypto/ecdsa"
	"encoding/base64"
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"math/big"
	"net"
	"net/http"
	"net/http/httptest"
	"net/netip"
	"os"
	"path/filepath"
	"runtime/debug"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
	"unicode"

	"github.com/ethereum/go-ethereum/accounts"
	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/common/hexutil"
	"github.com/ethereum/go-ethereum/crypto"
	"github.com/livepeer/node/destination"
)

// The node's packages the sender uses, its destination policy and its
// decoder of the payment parameters, are those of the node the demo runs.
func TestTheNodeModuleIsThePinnedNode(t *testing.T) {
	var manifest struct{ Node struct{ Commit string } }
	data, err := os.ReadFile("../../../deployments/rewards-local.json")
	if err != nil || json.Unmarshal(data, &manifest) != nil || len(manifest.Node.Commit) < 12 {
		t.Fatalf("the rewards manifest's node commit: %v", err)
	}
	info, ok := debug.ReadBuildInfo()
	if !ok {
		t.Fatal("no build information")
	}
	for _, d := range info.Deps {
		if d.Path == "github.com/livepeer/node" {
			if !strings.HasSuffix(d.Version, "-"+manifest.Node.Commit[:12]) {
				t.Fatalf("go.mod requires the node at %s, the manifest pins %s", d.Version, manifest.Node.Commit)
			}
			return
		}
	}
	t.Fatal("the sender does not use the node's module")
}

// --- the signed bytes and their signature -------------------------------------

// The example in the package comment, byte for byte.
func TestJoinMessageIsTheDocumentedBytes(t *testing.T) {
	got := joinMessage(big.NewInt(474747), common.HexToAddress("0x5FbDB2315678afecb367f032d93F642f64180aa3"),
		common.HexToAddress("0x70997970C51812dc3A010C7d01b50e0d17dc79C8"), "https://node.example:8935", 1795000000)
	want := "lp2-join:v1:474747:0x5fbdb2315678afecb367f032d93f642f64180aa3:0x70997970c51812dc3a010c7d01b50e0d17dc79c8:https://node.example:8935:1795000000"
	if string(got) != want {
		t.Fatalf("got  %s\nwant %s", got, want)
	}
	doc, err := os.ReadFile("main.go")
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Contains(doc, []byte("//\t"+want+"\n")) {
		t.Fatal("the package comment does not show the example's bytes")
	}
}

func personalSign(t *testing.T, key *ecdsa.PrivateKey, msg []byte) []byte {
	t.Helper()
	sig, err := crypto.Sign(accounts.TextHash(msg), key)
	if err != nil {
		t.Fatal(err)
	}
	sig[64] += 27
	return sig
}

func TestSignerRecoversThePersonalSignature(t *testing.T) {
	key, _ := crypto.GenerateKey()
	other, _ := crypto.GenerateKey()
	addr := crypto.PubkeyToAddress(key.PublicKey)
	msg := joinMessage(big.NewInt(1), common.Address{1}, addr, "http://a.example", 10)
	sig := personalSign(t, key, msg)
	if got, err := signer(msg, sig); err != nil || got != addr {
		t.Fatalf("v 27/28: got %s, %v; want %s", got, err, addr)
	}
	low := append([]byte(nil), sig...)
	low[64] -= 27
	if got, err := signer(msg, low); err != nil || got != addr {
		t.Fatalf("v 0/1: got %s, %v; want %s", got, err, addr)
	}
	// Another message, another key, a changed byte: not the node.
	if got, _ := signer(joinMessage(big.NewInt(1), common.Address{1}, addr, "http://a.example", 11), sig); got == addr {
		t.Fatal("a signature over other bytes recovered the node")
	}
	if got, _ := signer(msg, personalSign(t, other, msg)); got == addr {
		t.Fatal("another key's signature recovered the node")
	}
	bad := append([]byte(nil), sig...)
	bad[3] ^= 1
	if got, _ := signer(msg, bad); got == addr {
		t.Fatal("a changed signature recovered the node")
	}
	if _, err := signer(msg, sig[:64]); err == nil {
		t.Fatal("a 64-byte signature was read")
	}
	// Not the plain hash: the EIP-191 prefix is part of what is signed.
	plain, _ := crypto.Sign(crypto.Keccak256(msg), key)
	plain[64] += 27
	if got, _ := signer(msg, plain); got == addr {
		t.Fatal("a signature without the EIP-191 prefix recovered the node")
	}
}

// --- the URL policy -------------------------------------------------------------

func TestURLPolicy(t *testing.T) {
	names := map[string][]netip.Addr{
		"public.example":  {netip.MustParseAddr("93.184.216.34")},
		"mixed.example":   {netip.MustParseAddr("93.184.216.34"), netip.MustParseAddr("10.1.2.3")},
		"private.example": {netip.MustParseAddr("192.168.1.5")},
		"shared.example":  {netip.MustParseAddr("93.184.216.34"), netip.MustParseAddr("100.64.1.1")},
	}
	resolve := func(_ context.Context, host string) ([]netip.Addr, error) {
		if a, ok := names[host]; ok {
			return a, nil
		}
		return nil, errors.New("no such host")
	}
	cases := []struct {
		url          string
		allowPrivate bool
		want         string
	}{
		{"https://public.example", false, ""},
		{"http://public.example:8935/base/", false, ""},
		{"http://93.184.216.34:8935", false, ""},
		{"http://[2606:2800:220:1::1]:8935", false, ""},
		{"http://127.0.0.1:8935", false, codeURLNotAllowed},
		{"http://localhost.invalid:8935", false, codeUnreachable},
		{"http://10.0.0.1", false, codeURLNotAllowed},
		{"http://172.16.4.4", false, codeURLNotAllowed},
		{"http://192.168.0.10", false, codeURLNotAllowed},
		{"http://169.254.169.254", false, codeURLNotAllowed},
		{"http://0.0.0.0:8935", false, codeURLNotAllowed},
		{"http://[::1]:8935", false, codeURLNotAllowed},
		{"http://[fe80::1]:8935", false, codeURLNotAllowed},
		{"http://[fc00::1]:8935", false, codeURLNotAllowed},
		{"http://[::ffff:127.0.0.1]:8935", false, codeURLNotAllowed},
		{"http://private.example", false, codeURLNotAllowed},
		{"http://mixed.example", false, codeURLNotAllowed},
		// Not public as the node's destination policy has it, though no
		// loopback, private or link-local test says so: shared, benchmark,
		// documentation and reserved space, and IPv6 outside 2000::/3.
		{"http://100.64.0.1", false, codeURLNotAllowed},
		{"http://shared.example", false, codeURLNotAllowed},
		{"http://198.18.0.1", false, codeURLNotAllowed},
		{"http://192.0.2.1", false, codeURLNotAllowed},
		{"http://240.0.0.1", false, codeURLNotAllowed},
		{"http://[2001:db8::1]", false, codeURLNotAllowed},
		{"http://[64:ff9b::808:808]", false, codeURLNotAllowed},
		{"http://public.example/a\u0085b", false, codeURLNotAllowed},
		{"http://public.example/a\u00a0b", false, codeURLNotAllowed},
		{"ftp://public.example", false, codeURLNotAllowed},
		{"public.example:8935", false, codeURLNotAllowed},
		{"http://user:pw@public.example", false, codeURLNotAllowed},
		{"http://public.example/?x=1", false, codeURLNotAllowed},
		{"http://public.example/#x", false, codeURLNotAllowed},
		{"http://public.example/a b", false, codeURLNotAllowed},
		{"http:///nohost", false, codeURLNotAllowed},
		{"", false, codeURLNotAllowed},
		{"https://public.example/" + strings.Repeat("a", 600), false, codeURLNotAllowed},
		// The local demo's flag lets private addresses through, and nothing else.
		{"http://127.0.0.1:8935", true, ""},
		{"http://192.168.0.10", true, ""},
		{"ftp://127.0.0.1", true, codeURLNotAllowed},
		{"http://127.0.0.1/?x", true, codeURLNotAllowed},
	}
	for _, c := range cases {
		got, err := checkURL(context.Background(), c.url, c.allowPrivate, resolve)
		if got != c.want {
			t.Errorf("checkURL(%q, private %t) = %q (%v), want %q", c.url, c.allowPrivate, got, err, c.want)
		}
	}
}

// The client to nodes dials through the node's destination policy: a name
// that resolves to any address that is not public is refused when it is
// dialled, whatever the other addresses, and no redirect is followed.
func TestNodeClientRefusesPrivateAddressesAndRedirects(t *testing.T) {
	var hits atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hits.Add(1)
		if r.URL.Path == "/moved" {
			http.Redirect(w, r, "/discovery", http.StatusFound)
			return
		}
		w.Write([]byte("[]"))
	}))
	defer srv.Close()
	_, port, _ := strings.Cut(strings.TrimPrefix(srv.URL, "http://"), ":")
	p := nodePolicy(false)
	p.Lookup = func(_ context.Context, host string) ([]net.IPAddr, error) {
		switch host {
		case "loop.example":
			return []net.IPAddr{{IP: net.ParseIP("127.0.0.1")}}, nil
		case "mixed.example":
			return []net.IPAddr{{IP: net.ParseIP("93.184.216.34")}, {IP: net.ParseIP("127.0.0.1")}}, nil
		}
		return nil, errors.New("no such host")
	}
	for _, u := range []string{srv.URL, "http://loop.example:" + port, "http://mixed.example:" + port} {
		if _, err := nodeClient(p, time.Second).Get(u + "/discovery"); !errors.Is(err, destination.ErrDeniedAddress) {
			t.Errorf("%s without the flag: %v, want the policy's refusal", u, err)
		}
	}
	if hits.Load() != 0 {
		t.Fatalf("the loopback server was reached %d times without the flag", hits.Load())
	}
	resp, err := nodeClient(nodePolicy(true), time.Second).Get(srv.URL + "/moved")
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusFound {
		t.Fatalf("the redirect was followed: HTTP %d", resp.StatusCode)
	}
}

// --- the list file --------------------------------------------------------------

var (
	nodeA = common.HexToAddress("0x00000000000000000000000000000000000000aa")
	nodeB = common.HexToAddress("0x00000000000000000000000000000000000000bb")
)

func TestListParsesWhatItWrites(t *testing.T) {
	in := []entry{{nodeA, "http://a.example:8935", 100, 0}, {nodeB, "https://b.example/base", 200, 7}}
	out, err := parseList(formatList(in))
	if err != nil {
		t.Fatal(err)
	}
	if fmt.Sprint(out) != fmt.Sprint(in) {
		t.Fatalf("got %v, want %v", out, in)
	}
	text := "# comment\n\n  0x00000000000000000000000000000000000000AA http://a.example 100 3  \n"
	out, err = parseList([]byte(text))
	if err != nil || len(out) != 1 || out[0].Node != nodeA || out[0].URL != "http://a.example" || out[0].IssuedAt != 100 || out[0].PaidRound != 3 {
		t.Fatalf("got %v, %v", out, err)
	}
}

func TestListRefusesALineItCannotRead(t *testing.T) {
	for _, text := range []string{
		"0x00000000000000000000000000000000000000aa http://a.example 100\n",
		"0x00000000000000000000000000000000000000aa http://a.example 100 3 extra\n",
		"0xnotanaddress http://a.example 100 3\n",
		"0x00000000000000000000000000000000000000aa http://a.example -1 3\n",
		"0x00000000000000000000000000000000000000aa http://a.example 100 x\n",
		"# ok\n0x00000000000000000000000000000000000000aa http://a.example 100 3\n0x00000000000000000000000000000000000000AA http://b.example 101 3\n",
	} {
		if _, err := parseList([]byte(text)); err == nil || !strings.Contains(err.Error(), "line ") {
			t.Errorf("parseList(%q): %v, want an error naming the line", text, err)
		}
	}
}

// Every URL the join's policy accepts is read back from the list file as
// it was written, so an accepted join can never leave a list the sender
// cannot read when it restarts. Each Unicode character in turn is put in a
// URL the policy would otherwise accept.
func TestEveryAcceptedURLSurvivesTheListFile(t *testing.T) {
	accepted := 0
	for r := rune(0); r <= unicode.MaxRune; r++ {
		u := "http://127.0.0.1:8935/a" + string(r) + "b"
		if code, _ := checkURL(context.Background(), u, true, nil); code != "" {
			continue
		}
		accepted++
		got, err := parseList(formatList([]entry{{nodeA, u, 1, 0}}))
		if err != nil || len(got) != 1 || got[0].URL != u {
			t.Fatalf("U+%04X: accepted, and read back from the list as %v, %v", r, got, err)
		}
	}
	if accepted < 60 {
		t.Fatalf("only %d characters accepted in a URL path", accepted)
	}
}

// A reader sees the old list or the new one, never part of one, and no
// temporary file is left beside it.
func TestListIsWrittenAtomically(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "nodes.txt")
	versions := make([][]entry, 2)
	for v := range versions {
		for i := 0; i < 3000; i++ {
			versions[v] = append(versions[v], entry{common.BigToAddress(big.NewInt(int64(i + 1))), fmt.Sprintf("http://n%d.example", i), int64(v + 1), uint64(v)})
		}
	}
	if err := writeList(path, versions[0]); err != nil {
		t.Fatal(err)
	}
	var stop atomic.Bool
	var bad atomic.Value
	var wg sync.WaitGroup
	wg.Add(1)
	go func() {
		defer wg.Done()
		for !stop.Load() {
			got, err := readList(path)
			if err != nil || len(got) != 3000 {
				bad.Store(fmt.Sprintf("read %d entries, %v", len(got), err))
				return
			}
		}
	}()
	for i := 0; i < 60; i++ {
		if err := writeList(path, versions[i%2]); err != nil {
			t.Fatal(err)
		}
	}
	stop.Store(true)
	wg.Wait()
	if b := bad.Load(); b != nil {
		t.Fatalf("a reader saw part of a list: %s", b)
	}
	files, _ := os.ReadDir(dir)
	if len(files) != 1 {
		t.Fatalf("the folder holds %d files, want the list alone", len(files))
	}
}

// --- the once-per-round rule ----------------------------------------------------

func TestIsDueOncePerRound(t *testing.T) {
	for _, c := range []struct {
		paid, round uint64
		want        bool
	}{{0, 1, true}, {4, 5, true}, {5, 5, false}, {6, 5, false}} {
		if got := isDue(entry{PaidRound: c.paid}, c.round); got != c.want {
			t.Errorf("isDue(paid %d, round %d) = %t, want %t", c.paid, c.round, got, c.want)
		}
	}
}

// fake stands in for the node, the chain and the signer.
type fake struct {
	path                         string
	recipient                    common.Address
	round                        uint64
	challengeErr, relayErr       error
	funded, signed, relayed      int
	paidRoundOnFileAtRelay       uint64
	blockRelay                   bool
	challengeCalls, relayStarted atomic.Int32
}

func (f *fake) challenge(context.Context, entry) (*challenge, error) {
	f.challengeCalls.Add(1)
	if f.challengeErr != nil {
		return nil, f.challengeErr
	}
	return &challenge{recipient: f.recipient, round: f.round}, nil
}
func (f *fake) fund(context.Context, common.Address) error { f.funded++; return nil }
func (f *fake) sign(context.Context, *challenge) (string, string, error) {
	f.signed++
	return "p", "s", nil
}
func (f *fake) relay(ctx context.Context, _ *challenge, _, _ string) (string, error) {
	f.relayed++
	f.relayStarted.Add(1)
	if l, err := readList(f.path); err == nil && len(l) > 0 {
		f.paidRoundOnFileAtRelay = l[0].PaidRound
	}
	if f.blockRelay {
		<-ctx.Done()
		return "", ctx.Err()
	}
	return "ok", f.relayErr
}

func newBook(t *testing.T, entries ...entry) *book {
	t.Helper()
	path := filepath.Join(t.TempDir(), "nodes.txt")
	if err := writeList(path, entries); err != nil {
		t.Fatal(err)
	}
	b, err := openBook(path, 10)
	if err != nil {
		t.Fatal(err)
	}
	return b
}

// The round is on file before the paid request is sent, so a sender
// restarted after it does not send another in that round.
func TestARoundIsWrittenBeforeThePaidRequestAndSurvivesARestart(t *testing.T) {
	b := newBook(t, entry{nodeA, "http://a.example", 1, 4})
	f := &fake{path: b.path, recipient: nodeA, round: 5}
	due := b.take(5, time.Now())
	if len(due) != 1 {
		t.Fatalf("%d nodes due in round 5, want 1", len(due))
	}
	if again := b.take(5, time.Now()); len(again) != 0 {
		t.Fatal("a node being paid was handed out again")
	}
	attempt(context.Background(), f, b, due[0], 5, time.Now(), 0)
	if f.relayed != 1 || f.paidRoundOnFileAtRelay != 5 {
		t.Fatalf("relayed %d times with paidRound %d on file at the time, want once with 5", f.relayed, f.paidRoundOnFileAtRelay)
	}
	if len(b.take(5, time.Now().Add(time.Hour))) != 0 {
		t.Fatal("the node was due again in the round it was paid in")
	}
	restarted, err := openBook(b.path, 10)
	if err != nil {
		t.Fatal(err)
	}
	if len(restarted.take(5, time.Now())) != 0 {
		t.Fatal("after a restart the node was due again in round 5")
	}
	if len(restarted.take(6, time.Now())) != 1 {
		t.Fatal("after a restart the node was not due in round 6")
	}
}

// A paid request that fails, or never answers, is not sent again in the round.
func TestAFailedPaidRequestIsNotRepeatedInTheRound(t *testing.T) {
	for _, f := range []*fake{{relayErr: errors.New("HTTP 500")}, {blockRelay: true}} {
		b := newBook(t, entry{nodeA, "http://a.example", 1, 0})
		f.path, f.recipient, f.round = b.path, nodeA, 3
		ctx, cancel := context.WithTimeout(context.Background(), 50*time.Millisecond)
		attempt(ctx, f, b, b.take(3, time.Now())[0], 3, time.Now(), 0)
		cancel()
		if len(b.take(3, time.Now().Add(time.Hour))) != 0 {
			t.Fatal("the node was due again in the round its paid request failed in")
		}
		if len(b.take(4, time.Now())) != 1 {
			t.Fatal("the node was not due in the next round")
		}
	}
}

// A failure before the round is written leaves the node due, after the wait.
func TestAFailureBeforeThePaidRequestIsTriedAgain(t *testing.T) {
	b := newBook(t, entry{nodeA, "http://a.example", 1, 0})
	f := &fake{path: b.path, challengeErr: errors.New("discovery: connection refused")}
	attempt(context.Background(), f, b, b.take(2, time.Now())[0], 2, time.Now(), time.Minute)
	if len(b.take(2, time.Now())) != 0 {
		t.Fatal("tried again before the retry wait")
	}
	if len(b.take(2, time.Now().Add(2*time.Minute))) != 1 {
		t.Fatal("not tried again after the retry wait")
	}
	if l := b.list(); l[0].PaidRound != 0 {
		t.Fatalf("paidRound %d on a node never sent a paid request", l[0].PaidRound)
	}
}

// The challenge's recipient is checked before anything is funded or signed.
func TestARecipientMismatchIsNeitherFundedNorPaid(t *testing.T) {
	b := newBook(t, entry{nodeA, "http://a.example", 1, 0})
	f := &fake{path: b.path, recipient: nodeB, round: 2}
	attempt(context.Background(), f, b, b.take(2, time.Now())[0], 2, time.Now(), 0)
	if f.funded+f.signed+f.relayed != 0 {
		t.Fatalf("funded %d, signed %d, relayed %d for a challenge paying another node", f.funded, f.signed, f.relayed)
	}
	if b.list()[0].PaidRound != 0 {
		t.Fatal("the round was written for a node not paid")
	}
}

// A challenge of another round than the sender's is not paid; the node
// stays due.
func TestAChallengeOfAnotherRoundIsNotPaid(t *testing.T) {
	b := newBook(t, entry{nodeA, "http://a.example", 1, 0})
	f := &fake{path: b.path, recipient: nodeA, round: 1}
	attempt(context.Background(), f, b, b.take(2, time.Now())[0], 2, time.Now(), 0)
	if f.funded+f.relayed != 0 || len(b.take(2, time.Now())) != 1 {
		t.Fatal("a challenge of round 1 was paid in round 2, or the node is no longer due")
	}
}

// An attempt that starts only after the loop has seen a later round pays
// for the round it was taken in, with that round's timing: the loop hands
// it both as values. The attempt is queued, the round moves from 5 to 6,
// and then the attempt runs; the node's challenge is still of round 5.
func TestAnAttemptStartedAfterTheRoundMovesKeepsItsRound(t *testing.T) {
	b := newBook(t, entry{nodeA, "http://a.example", 1, 4})
	f := &fake{path: b.path, recipient: nodeA, round: 5}
	var round atomic.Uint64
	round.Store(5)
	var mu sync.Mutex
	var queue []func()
	spawned, passed := make(chan struct{}, 10), make(chan struct{}, 10)
	kick := make(chan struct{}, 1)
	ctx, cancel := context.WithCancel(context.Background())
	l := payLoop{b: b, st: f, poll: time.Hour, nodeTimeout: time.Minute, retry: time.Hour,
		readRound: func(context.Context) (uint64, error) { return round.Load(), nil },
		after:     func(context.Context) { passed <- struct{}{} },
		spawn: func(job func()) {
			mu.Lock()
			queue = append(queue, job)
			mu.Unlock()
			spawned <- struct{}{}
		}}
	done := make(chan struct{})
	go func() { l.run(ctx, kick); close(done) }()
	<-spawned
	<-passed
	round.Store(6)
	kick <- struct{}{}
	<-passed
	mu.Lock()
	jobs := queue
	mu.Unlock()
	if len(jobs) != 1 {
		t.Fatalf("%d attempts started, want the one for round 5", len(jobs))
	}
	jobs[0]()
	cancel()
	<-done
	if f.relayed != 1 || b.list()[0].PaidRound != 5 {
		t.Fatalf("relayed %d, paidRound %d on file: the attempt taken in round 5 did not pay for round 5", f.relayed, b.list()[0].PaidRound)
	}
}

// --- the challenge's payment parameters -----------------------------------------

func pbBytes(n uint64, b []byte) []byte {
	out := binary.AppendUvarint(nil, n<<3|2)
	out = binary.AppendUvarint(out, uint64(len(b)))
	return append(out, b...)
}
func pbVarint(n, v uint64) []byte {
	return binary.AppendUvarint(binary.AppendUvarint(nil, n<<3), v)
}

func params(recipient []byte, round uint64) string {
	expiration := append(pbVarint(1, round), pbBytes(2, make([]byte, 32))...)
	ticket := append(pbBytes(1, recipient), pbBytes(2, []byte{1})...)
	ticket = append(ticket, pbBytes(7, expiration)...)
	info := append(pbBytes(1, []byte("http://n.example")), pbBytes(2, ticket)...)
	info = append(info, pbBytes(4, recipient)...)
	return base64.StdEncoding.EncodeToString(info)
}

func TestReadParams(t *testing.T) {
	got, round, err := readParams(params(nodeA.Bytes(), 12))
	if err != nil || got != nodeA || round != 12 {
		t.Fatalf("got %s, %d, %v; want %s, 12", got, round, err, nodeA)
	}
	for _, p := range []string{params(nodeA.Bytes()[:19], 12), "not base64!", base64.StdEncoding.EncodeToString([]byte{0x12, 0x40})} {
		if _, _, err := readParams(p); err == nil {
			t.Errorf("readParams(%q) read a recipient", p)
		}
	}
}

// --- the join, through its HTTP answer ------------------------------------------

type joinFixture struct {
	j          *joiner
	key        *ecdsa.PrivateKey
	addr       common.Address
	now        time.Time
	registered map[common.Address]bool
	recipient  map[string]common.Address // by URL
	probed     int
}

func newJoinFixture(t *testing.T, max int) *joinFixture {
	key, _ := crypto.GenerateKey()
	fx := &joinFixture{key: key, addr: crypto.PubkeyToAddress(key.PublicKey), now: time.Unix(1795000000, 0),
		registered: map[common.Address]bool{}, recipient: map[string]common.Address{}}
	fx.registered[fx.addr] = true
	path := filepath.Join(t.TempDir(), "nodes.txt")
	b, err := openBook(path, max)
	if err != nil {
		t.Fatal(err)
	}
	fx.j = &joiner{chainID: big.NewInt(474747), registry: common.HexToAddress("0x5FbDB2315678afecb367f032d93F642f64180aa3"),
		allowPrivate: false, book: b, now: func() time.Time { return fx.now }, timeout: time.Second,
		resolve: func(context.Context, string) ([]netip.Addr, error) {
			return []netip.Addr{netip.MustParseAddr("93.184.216.34")}, nil
		},
		registered: func(_ context.Context, n common.Address) (bool, error) { return fx.registered[n], nil },
		probe: func(_ context.Context, e entry) (*challenge, error) {
			fx.probed++
			r, ok := fx.recipient[e.URL]
			if !ok {
				return nil, errors.New("discovery: connection refused")
			}
			return &challenge{recipient: r}, nil
		}}
	return fx
}

func (fx *joinFixture) body(t *testing.T, key *ecdsa.PrivateKey, node common.Address, u string, issued int64) string {
	sig := personalSign(t, key, joinMessage(fx.j.chainID, fx.j.registry, node, u, issued))
	b, _ := json.Marshal(map[string]any{"node": strings.ToLower(node.Hex()), "url": u, "issuedAt": issued, "signature": hexutil.Encode(sig)})
	return string(b)
}

func (fx *joinFixture) post(t *testing.T, body string) (int, string, http.Header) {
	t.Helper()
	w := httptest.NewRecorder()
	fx.j.ServeHTTP(w, httptest.NewRequest(http.MethodPost, "/join", strings.NewReader(body)))
	var got struct{ Code, Status string }
	json.Unmarshal(w.Body.Bytes(), &got)
	if got.Code != "" {
		return w.Code, got.Code, w.Header()
	}
	return w.Code, got.Status, w.Header()
}

func TestJoinAnswers(t *testing.T) {
	fx := newJoinFixture(t, 2)
	healthy, other := "https://n.example", "https://m.example"
	fx.recipient[healthy] = fx.addr
	now := fx.now.Unix()
	listFile := func() string { b, _ := os.ReadFile(fx.j.book.path); return string(b) }
	before := listFile()
	stranger, _ := crypto.GenerateKey()
	strangerAddr := crypto.PubkeyToAddress(stranger.PublicKey)
	unregistered, _ := crypto.GenerateKey()

	for _, c := range []struct {
		name, body string
		status     int
		code       string
	}{
		{"not JSON", "{", 400, codeBadSignature},
		{"signed by another key", fx.body(t, stranger, fx.addr, healthy, now), 400, codeBadSignature},
		{"signed for another URL", strings.Replace(fx.body(t, fx.key, fx.addr, healthy, now), healthy, other, 1), 400, codeBadSignature},
		{"too old", fx.body(t, fx.key, fx.addr, healthy, now-601), 400, codeStale},
		{"too far ahead", fx.body(t, fx.key, fx.addr, healthy, now+601), 400, codeStale},
		{"a private URL", fx.body(t, fx.key, fx.addr, "http://127.0.0.1:8935", now), 400, codeURLNotAllowed},
		{"not registered", fx.body(t, unregistered, crypto.PubkeyToAddress(unregistered.PublicKey), healthy, now), 403, codeNotRegistered},
		{"unreachable", fx.body(t, fx.key, fx.addr, other, now), 422, codeUnreachable},
	} {
		status, code, _ := fx.post(t, c.body)
		if status != c.status || code != c.code {
			t.Errorf("%s: HTTP %d %s, want %d %s", c.name, status, code, c.status, c.code)
		}
	}
	// Node X signs a join for node Y's healthy endpoint.
	fx.registered[strangerAddr] = true
	if status, code, _ := fx.post(t, fx.body(t, stranger, strangerAddr, healthy, now)); status != 403 || code != codeRecipientMismatch {
		t.Errorf("X for Y's endpoint: HTTP %d %s, want 403 %s", status, code, codeRecipientMismatch)
	}
	if listFile() != before {
		t.Fatalf("a refused join changed the list:\n%s", listFile())
	}

	// Accepted, then replayed, then an older one, then a newer one.
	first := fx.body(t, fx.key, fx.addr, healthy, now-5)
	if status, s, _ := fx.post(t, first); status != 200 || s != "listed" {
		t.Fatalf("the join: HTTP %d %s, want 200 listed", status, s)
	}
	fx.j.book.markPaid(fx.addr, 9)
	for name, body := range map[string]string{"replayed": first, "older": fx.body(t, fx.key, fx.addr, healthy, now-6)} {
		if status, code, _ := fx.post(t, body); status != 400 || code != codeStale {
			t.Errorf("%s: HTTP %d %s, want 400 %s", name, status, code, codeStale)
		}
	}
	fx.recipient[other] = fx.addr
	if status, s, _ := fx.post(t, fx.body(t, fx.key, fx.addr, other, now)); status != 200 || s != "updated" {
		t.Fatalf("the newer join: HTTP %d %s, want 200 updated", status, s)
	}
	l := fx.j.book.list()
	if len(l) != 1 || l[0].URL != other || l[0].IssuedAt != now || l[0].PaidRound != 9 {
		t.Fatalf("the list after an update: %v; want the new URL and issuedAt, and paidRound kept", l)
	}
	if onFile, _ := readList(fx.j.book.path); fmt.Sprint(onFile) != fmt.Sprint(l) {
		t.Fatalf("the file holds %v, the sender %v", onFile, l)
	}

	// The list holds two at most: a third node is refused, a listed one is not.
	k2, _ := crypto.GenerateKey()
	a2 := crypto.PubkeyToAddress(k2.PublicKey)
	fx.registered[a2] = true
	fx.recipient["https://two.example"] = a2
	if status, _, _ := fx.post(t, fx.body(t, k2, a2, "https://two.example", now)); status != 200 {
		t.Fatalf("the second node: HTTP %d", status)
	}
	k3, _ := crypto.GenerateKey()
	a3 := crypto.PubkeyToAddress(k3.PublicKey)
	fx.registered[a3] = true
	fx.recipient["https://three.example"] = a3
	probed := fx.probed
	status, code, h := fx.post(t, fx.body(t, k3, a3, "https://three.example", now))
	if status != 503 || code != codeFull || h.Get("Retry-After") == "" {
		t.Fatalf("a third node: HTTP %d %s Retry-After %q, want 503 %s with Retry-After", status, code, h.Get("Retry-After"), codeFull)
	}
	if fx.probed != probed {
		t.Fatal("a join refused as FULL reached the node")
	}
	if status, s, _ := fx.post(t, fx.body(t, fx.key, fx.addr, other, now+1)); status != 200 || s != "updated" {
		t.Fatalf("a listed node's update when full: HTTP %d %s", status, s)
	}
}

// A join whose URL holds a character the list file would read as a field
// separator is refused, and every join accepted leaves a list file the
// sender reads again when it restarts.
func TestAJoinLeavesAListTheSenderRestartsWith(t *testing.T) {
	fx := newJoinFixture(t, 10)
	issued := fx.now.Unix() - 100
	for _, sep := range []string{" ", "\t", "\u0085", "\u00a0", "\u1680", "\u2000", "\u2028", "\u2029", "\u202f", "\u3000", "\ufeff", "\x7f", "é"} {
		u := "https://n.example/a" + sep + "b"
		fx.recipient[u] = fx.addr
		issued++
		status, code, _ := fx.post(t, fx.body(t, fx.key, fx.addr, u, issued))
		if status == 200 {
			t.Errorf("a URL holding %q was accepted", sep)
		} else if code != codeURLNotAllowed {
			t.Errorf("a URL holding %q: HTTP %d %s, want %s", sep, status, code, codeURLNotAllowed)
		}
		if _, err := openBook(fx.j.book.path, 10); err != nil {
			t.Fatalf("after the join with %q, the sender cannot restart: %v", sep, err)
		}
	}
	u := "https://n.example/a-b_c~d%20e"
	fx.recipient[u] = fx.addr
	if status, s, _ := fx.post(t, fx.body(t, fx.key, fx.addr, u, issued+1)); status != 200 {
		t.Fatalf("a printable URL: HTTP %d %s", status, s)
	}
	restarted, err := openBook(fx.j.book.path, 10)
	if err != nil {
		t.Fatalf("the sender cannot restart: %v", err)
	}
	if l := restarted.list(); len(l) != 1 || l[0].URL != u {
		t.Fatalf("after a restart the list holds %v, want %s", l, u)
	}
}
