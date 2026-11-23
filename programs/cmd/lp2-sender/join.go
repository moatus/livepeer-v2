package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"math/big"
	"net/http"
	"net/netip"
	"net/url"
	"strconv"
	"strings"
	"time"

	"github.com/ethereum/go-ethereum/accounts"
	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/common/hexutil"
	"github.com/ethereum/go-ethereum/crypto"
	"github.com/livepeer/node/destination"
)

// The join's answers. A refusal is a 4xx with exactly one of these codes.
const (
	codeBadSignature      = "BAD_SIGNATURE"
	codeStale             = "STALE"
	codeNotRegistered     = "NOT_REGISTERED"
	codeURLNotAllowed     = "URL_NOT_ALLOWED"
	codeUnreachable       = "UNREACHABLE"
	codeRecipientMismatch = "RECIPIENT_MISMATCH"
	codeFull              = "FULL"
	codeUnavailable       = "SENDER_UNAVAILABLE"
)

var codeStatus = map[string]int{
	codeBadSignature:      http.StatusBadRequest,
	codeStale:             http.StatusBadRequest,
	codeURLNotAllowed:     http.StatusBadRequest,
	codeNotRegistered:     http.StatusForbidden,
	codeRecipientMismatch: http.StatusForbidden,
	codeUnreachable:       http.StatusUnprocessableEntity,
	codeFull:              http.StatusServiceUnavailable,
	codeUnavailable:       http.StatusServiceUnavailable,
}

// joinWindow is how far a join's issuedAt may be from the sender's clock.
const joinWindow = 600

// joinMessage is the text a node signs to join: the UTF-8 bytes of
// lp2-join:v1:<chainId>:<registry>:<node>:<url>:<issuedAt>, with the
// addresses in lowercase 0x hex and issuedAt in decimal Unix seconds. The
// last colon splits url from issuedAt, which holds digits only, so two
// joins that differ sign different bytes.
func joinMessage(chainID *big.Int, registry, node common.Address, rawURL string, issuedAt int64) []byte {
	return []byte(fmt.Sprintf("lp2-join:v1:%s:%s:%s:%s:%d", chainID, strings.ToLower(registry.Hex()),
		strings.ToLower(node.Hex()), rawURL, issuedAt))
}

// signer recovers the address whose EIP-191 personal signature sig is of
// msg. sig is 65 bytes, r, s, then v as 27 or 28 (or 0 or 1).
func signer(msg []byte, sig []byte) (common.Address, error) {
	if len(sig) != 65 {
		return common.Address{}, fmt.Errorf("a signature is 65 bytes, not %d", len(sig))
	}
	s := append([]byte(nil), sig...)
	if s[64] >= 27 {
		s[64] -= 27
	}
	if s[64] > 1 {
		return common.Address{}, errors.New("the signature's v is not 27 or 28")
	}
	pub, err := crypto.SigToPub(accounts.TextHash(msg), s)
	if err != nil {
		return common.Address{}, err
	}
	return crypto.PubkeyToAddress(*pub), nil
}

// checkURL is the policy on a joining node's URL. The URL is http or https
// with a host, and nothing else: printable ASCII only (so no space, which
// separates the list file's fields), no user, query or fragment, at most
// 512 bytes. With allowPrivate false, every address its host resolves to
// is public as the node's own destination policy classifies it
// (destination.Public). It returns the refusal's code, or "" when the URL
// may be listed.
func checkURL(ctx context.Context, raw string, allowPrivate bool, resolve func(context.Context, string) ([]netip.Addr, error)) (string, error) {
	if len(raw) == 0 || len(raw) > 512 || strings.IndexFunc(raw, func(r rune) bool { return r <= ' ' || r > '~' }) >= 0 {
		return codeURLNotAllowed, errors.New("the URL is empty, longer than 512 bytes, or holds a character that is not printable ASCII")
	}
	u, err := url.Parse(raw)
	if err != nil {
		return codeURLNotAllowed, err
	}
	if (u.Scheme != "http" && u.Scheme != "https") || u.Host == "" || u.Hostname() == "" || u.User != nil ||
		u.RawQuery != "" || u.Fragment != "" || u.Opaque != "" || strings.Contains(raw, "#") || strings.Contains(raw, "?") {
		return codeURLNotAllowed, errors.New("the URL is not http or https with a host and no user, query or fragment")
	}
	if allowPrivate {
		return "", nil
	}
	host := u.Hostname()
	var addrs []netip.Addr
	if ip, err := netip.ParseAddr(host); err == nil {
		addrs = []netip.Addr{ip}
	} else if addrs, err = resolve(ctx, host); err != nil || len(addrs) == 0 {
		return codeUnreachable, fmt.Errorf("the host %s does not resolve: %v", host, err)
	}
	for _, ip := range addrs {
		if !destination.Public(ip) {
			return codeURLNotAllowed, fmt.Errorf("the host %s resolves to %s, which is not a public address", host, ip)
		}
	}
	return "", nil
}

// nodePolicy is the node's own destination policy for the sender's
// connections to nodes: every address a name resolves to when it is
// dialled must be public, or the connection is refused. allowPrivate (the
// local demo) grants every address.
func nodePolicy(allowPrivate bool) destination.Policy {
	var grants []string
	if allowPrivate {
		grants = []string{"0.0.0.0/0", "::/0"}
	}
	p, err := destination.New("node", grants)
	if err != nil {
		panic(err) // the grants above are constants
	}
	return p
}

// nodeClient is the HTTP client for nodes. It dials through p, so the
// address policy holds at every connection, and it follows no redirect and
// uses no proxy.
func nodeClient(p destination.Policy, timeout time.Duration) *http.Client {
	return &http.Client{
		Timeout:       timeout,
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
		Transport: &http.Transport{Proxy: nil, DialContext: p.DialContext, TLSHandshakeTimeout: timeout,
			ResponseHeaderTimeout: timeout, MaxIdleConnsPerHost: 2, IdleConnTimeout: time.Minute},
	}
}

// joinRequest is the body of POST /join.
type joinRequest struct {
	Node      string      `json:"node"`
	URL       string      `json:"url"`
	IssuedAt  json.Number `json:"issuedAt"`
	Signature string      `json:"signature"`
}

// refusal is a join's answer when it is not 200.
type refusal struct {
	code   string
	detail string
}

func (r *refusal) Error() string { return r.code + ": " + r.detail }

func refuse(code, format string, args ...any) *refusal {
	return &refusal{code, fmt.Sprintf(format, args...)}
}

// joiner checks joins and adds the nodes it accepts to the book.
type joiner struct {
	chainID      *big.Int
	registry     common.Address
	allowPrivate bool
	resolve      func(context.Context, string) ([]netip.Addr, error)
	registered   func(context.Context, common.Address) (bool, error)
	probe        func(context.Context, entry) (*challenge, error)
	book         *book
	now          func() time.Time
	timeout      time.Duration
}

// ServeHTTP answers POST /join.
func (j *joiner) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		w.Header().Set("Allow", http.MethodPost)
		http.Error(w, "POST a signed join", http.StatusMethodNotAllowed)
		return
	}
	var req joinRequest
	dec := json.NewDecoder(io.LimitReader(r.Body, 4096))
	dec.UseNumber()
	if err := dec.Decode(&req); err != nil {
		answer(w, refuse(codeBadSignature, "the body is not a join: %v", err), nil)
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), j.timeout)
	defer cancel()
	e, updated, ref := j.join(ctx, req)
	if ref != nil {
		log.Printf("join refused: %s (node %q, url %q)", ref, req.Node, req.URL)
		answer(w, ref, nil)
		return
	}
	status := "listed"
	if updated {
		status = "updated"
	}
	log.Printf("join accepted: node %s at %s, issuedAt %d (%s)", e.Node, e.URL, e.IssuedAt, status)
	answer(w, nil, map[string]any{"status": status, "node": strings.ToLower(e.Node.Hex()), "url": e.URL})
}

// join checks req and, when every check passes, lists the node or
// updates its URL. It reports whether the node was listed before.
func (j *joiner) join(ctx context.Context, req joinRequest) (entry, bool, *refusal) {
	// 1. The signature recovers the node.
	if !common.IsHexAddress(req.Node) || !strings.HasPrefix(req.Node, "0x") {
		return entry{}, false, refuse(codeBadSignature, "node is not a 0x address")
	}
	node := common.HexToAddress(req.Node)
	issued, err := strconv.ParseInt(req.IssuedAt.String(), 10, 64)
	if err != nil || issued <= 0 || req.IssuedAt.String() != strconv.FormatInt(issued, 10) {
		return entry{}, false, refuse(codeBadSignature, "issuedAt is not a positive whole number of seconds")
	}
	sig, err := hexutil.Decode(req.Signature)
	if err != nil {
		return entry{}, false, refuse(codeBadSignature, "signature is not 0x hex")
	}
	got, err := signer(joinMessage(j.chainID, j.registry, node, req.URL, issued), sig)
	if err != nil || got != node {
		return entry{}, false, refuse(codeBadSignature, "the signature does not recover the node")
	}
	// 2. Fresh, and newer than the last join accepted for the node.
	now := j.now().Unix()
	if issued < now-joinWindow || issued > now+joinWindow {
		return entry{}, false, refuse(codeStale, "issuedAt %d is more than %d s from the sender's clock, %d", issued, joinWindow, now)
	}
	if last, ok := j.book.lastJoin(node); ok && issued <= last {
		return entry{}, false, refuse(codeStale, "issuedAt %d is not newer than the last join accepted for the node, %d", issued, last)
	}
	// 3. The URL is allowed.
	if code, err := checkURL(ctx, req.URL, j.allowPrivate, j.resolve); code != "" {
		return entry{}, false, refuse(code, "%v", err)
	}
	// 4. The node is registered.
	ok, err := j.registered(ctx, node)
	if err != nil {
		return entry{}, false, refuse(codeUnavailable, "could not read the Registry: %v", err)
	}
	if !ok {
		return entry{}, false, refuse(codeNotRegistered, "%s is not registered", node)
	}
	// 5. There is room for a node not yet listed.
	if j.book.full(node) {
		return entry{}, false, refuse(codeFull, "the list holds its most nodes")
	}
	// 6. The node answers at the URL: its discovery lists a runner for
	// the app, and the runner asks for payment to the node.
	e := entry{Node: node, URL: req.URL, IssuedAt: issued}
	c, err := j.probe(ctx, e)
	if err != nil {
		return entry{}, false, refuse(codeUnreachable, "%v", err)
	}
	if c.recipient != node {
		return entry{}, false, refuse(codeRecipientMismatch, "the node at %s asks for payment to %s, not %s", req.URL, c.recipient, node)
	}
	updated, ref := j.book.accept(e)
	return e, updated, ref
}

func answer(w http.ResponseWriter, ref *refusal, ok map[string]any) {
	w.Header().Set("Content-Type", "application/json")
	if ref == nil {
		w.WriteHeader(http.StatusOK)
		json.NewEncoder(w).Encode(ok)
		return
	}
	if ref.code == codeFull || ref.code == codeUnavailable {
		w.Header().Set("Retry-After", "600")
	}
	w.WriteHeader(codeStatus[ref.code])
	json.NewEncoder(w).Encode(map[string]string{"code": ref.code, "detail": ref.detail})
}
