package main

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/ethereum/go-ethereum/common"
)

// challenge is what a node's runner asked for in its 402 answer: where
// the paid request goes, the payment parameters to sign, and, read from
// them, whom the ticket pays and the round it is created in.
type challenge struct {
	target    string
	params    string
	recipient common.Address
	round     uint64
}

// steps are the parts of one paid request, apart so that a test can stand
// in for the chain, the signer and the node.
type steps interface {
	// challenge finds the node's runner in its discovery and asks it,
	// with no payment, for a challenge.
	challenge(ctx context.Context, e entry) (*challenge, error)
	// fund tops up the payer's wallet, deposit and the node's earmark.
	fund(ctx context.Context, node common.Address) error
	// sign has the payer's signer sign a ticket for the challenge.
	sign(ctx context.Context, c *challenge) (payment, segment string, err error)
	// relay sends the paid request and returns the node's answer.
	relay(ctx context.Context, c *challenge, payment, segment string) (string, error)
}

// attempt is one node's work in one round, bounded by ctx. Until the
// round is written down as the node's, any failure leaves the node due,
// to be tried again after the retry wait. Once it is written, the paid
// request is sent once, and whatever its outcome the node is not sent
// another in that round.
func attempt(ctx context.Context, st steps, b *book, e entry, round uint64, roundSeen time.Time, retry time.Duration) {
	start := time.Now()
	sent := false
	defer func() { b.done(e.Node, !sent, retry) }()
	notPaid := func(format string, args ...any) {
		log.Printf("not paid: node %s round %d: %s", e.Node, round, fmt.Sprintf(format, args...))
	}
	c, err := st.challenge(ctx, e)
	if err != nil {
		notPaid("%v", err)
		return
	}
	// The recipient is checked before the earmark is funded and before
	// the ticket is signed, on this challenge, not only at the join.
	if c.recipient != e.Node {
		notPaid("%s: the challenge pays %s", codeRecipientMismatch, c.recipient)
		return
	}
	if c.round != round {
		notPaid("the challenge's ticket is of round %d; tried again shortly", c.round)
		return
	}
	if err := st.fund(ctx, e.Node); err != nil {
		notPaid("funding: %v", err)
		return
	}
	payment, segment, err := st.sign(ctx, c)
	if err != nil {
		notPaid("%v", err)
		return
	}
	if err := b.markPaid(e.Node, round); err != nil {
		notPaid("could not write the list: %v", err)
		return
	}
	sent = true
	sendAt := time.Now()
	answer, err := st.relay(ctx, c, payment, segment)
	if err != nil {
		notPaid("the paid request was sent and failed after %d ms, so the node is not sent another this round: %v",
			time.Since(sendAt).Milliseconds(), err)
		return
	}
	first := ""
	if d, ok := b.firstRelay(e.Node); ok {
		first = fmt.Sprintf(", its first relay, %.1f s after its join", d.Seconds())
	}
	log.Printf("relay: node %s round %d: answered in %d ms, %.1f s after the round was seen, %.1f s for the whole attempt%s: %s",
		e.Node, round, time.Since(sendAt).Milliseconds(), time.Since(roundSeen).Seconds(), time.Since(start).Seconds(), first, answer)
}

// live is the steps against the real node, signer and chain.
type live struct {
	snd *sender
}

func (n live) challenge(ctx context.Context, e entry) (*challenge, error) {
	snd := n.snd
	runner, err := snd.runnerURL(ctx, e)
	if err != nil {
		return nil, err
	}
	target := runner + "/" + snd.job
	payer := map[string]string{"Livepeer-Payer-Address": snd.s.From.Hex()}
	// The challenge is asked with no body: the node answers 402 without
	// reading one, and resets the connection when a large one is sent.
	code, body, err := post(ctx, snd.nodes, target, nil, payer)
	if err != nil {
		return nil, fmt.Errorf("the request: %w", err)
	}
	if code != http.StatusPaymentRequired {
		return nil, fmt.Errorf("the request: HTTP %d %s, want 402 with a challenge", code, clip(body))
	}
	var c struct {
		PaymentParams string `json:"payment_params"`
	}
	if err := json.Unmarshal(body, &c); err != nil || c.PaymentParams == "" {
		return nil, fmt.Errorf("the challenge has no payment_params: %s", clip(body))
	}
	recipient, round, err := readParams(c.PaymentParams)
	if err != nil {
		return nil, fmt.Errorf("the challenge's payment_params: %w", err)
	}
	return &challenge{target: target, params: c.PaymentParams, recipient: recipient, round: round}, nil
}

func (n live) fund(ctx context.Context, node common.Address) error { return n.snd.topUp(ctx, node) }

func (n live) sign(ctx context.Context, c *challenge) (string, string, error) {
	snd := n.snd
	snd.signMu.Lock()
	defer snd.signMu.Unlock()
	req, _ := json.Marshal(map[string]string{"orchestrator": c.params, "type": "fixed"})
	code, signed, err := post(ctx, snd.signerHTTP, snd.signer+"/generate-live-payment", req, nil)
	if err != nil {
		return "", "", fmt.Errorf("the signer: %w", err)
	}
	if code != http.StatusOK {
		return "", "", fmt.Errorf("the signer: HTTP %d %s", code, clip(signed))
	}
	var p struct {
		Payment  string `json:"payment"`
		SegCreds string `json:"segCreds"`
	}
	if err := json.Unmarshal(signed, &p); err != nil || p.Payment == "" {
		return "", "", fmt.Errorf("the signer's answer has no payment: %s", clip(signed))
	}
	return p.Payment, p.SegCreds, nil
}

func (n live) relay(ctx context.Context, c *challenge, payment, segment string) (string, error) {
	snd := n.snd
	headers := map[string]string{"Livepeer-Payer-Address": snd.s.From.Hex(),
		"Livepeer-Payment": payment, "Livepeer-Segment": segment}
	code, answer, err := post(ctx, snd.nodes, c.target, []byte(`{"hello":"lp2"}`), headers)
	if err != nil {
		return "", err
	}
	if code != http.StatusOK {
		return "", fmt.Errorf("HTTP %d %s", code, clip(answer))
	}
	return clip(answer), nil
}

// runnerURL is the app URL of the node's runner for --app: the path its
// /discovery gives, under the URL on the list. The host in discovery is
// the node's --service-url, which defaults to its listen address, so a
// node listening on all interfaces would advertise one nobody can reach.
func (snd *sender) runnerURL(ctx context.Context, e entry) (string, error) {
	base := strings.TrimRight(e.URL, "/")
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, base+"/discovery", nil)
	if err != nil {
		return "", err
	}
	resp, err := snd.nodes.Do(req)
	if err != nil {
		return "", fmt.Errorf("discovery: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return "", fmt.Errorf("discovery: HTTP %d", resp.StatusCode)
	}
	var entries []struct {
		Runners []struct {
			URL string `json:"url"`
			App string `json:"app"`
		} `json:"runners"`
	}
	if err := json.NewDecoder(io.LimitReader(resp.Body, 1<<20)).Decode(&entries); err != nil {
		return "", fmt.Errorf("discovery: %w", err)
	}
	for _, d := range entries {
		for _, r := range d.Runners {
			if r.App == snd.app {
				u, err := url.Parse(r.URL)
				if err != nil {
					return "", fmt.Errorf("discovery: runner URL %q: %w", r.URL, err)
				}
				return base + strings.TrimRight(u.EscapedPath(), "/"), nil
			}
		}
	}
	return "", fmt.Errorf("discovery lists no runner for app %q", snd.app)
}

// readParams reads, from a challenge's payment_params (base64 of the
// node's OrchestratorInfo, protobuf), the ticket's recipient (field 2,
// TicketParams, then its field 1) and its creation round (TicketParams
// field 7, ExpirationParams, then its field 1). As in protobuf, a field
// given twice takes its last value.
func readParams(b64 string) (common.Address, uint64, error) {
	raw, err := base64.StdEncoding.DecodeString(b64)
	if err != nil {
		return common.Address{}, 0, err
	}
	var recipient []byte
	var round uint64
	err = fields(raw, func(n uint64, val []byte, _ uint64) error {
		if n != 2 || val == nil {
			return nil
		}
		return fields(val, func(n uint64, val []byte, _ uint64) error {
			switch {
			case n == 1 && val != nil:
				recipient = val
			case n == 7 && val != nil:
				return fields(val, func(n uint64, val []byte, v uint64) error {
					if n == 1 && val == nil {
						round = v
					}
					return nil
				})
			}
			return nil
		})
	})
	if err != nil {
		return common.Address{}, 0, err
	}
	if len(recipient) != common.AddressLength {
		return common.Address{}, 0, fmt.Errorf("the ticket's recipient is %d bytes, not an address", len(recipient))
	}
	return common.BytesToAddress(recipient), round, nil
}

// fields walks a protobuf message: fn gets each field's number and either
// its bytes (length-delimited) or its integer (varint; val is nil).
// Fixed-width fields are skipped.
func fields(b []byte, fn func(n uint64, val []byte, v uint64) error) error {
	bad := errors.New("not a protobuf message")
	for len(b) > 0 {
		tag, k := binary.Uvarint(b)
		if k <= 0 {
			return bad
		}
		b = b[k:]
		switch tag & 7 {
		case 0:
			v, k := binary.Uvarint(b)
			if k <= 0 {
				return bad
			}
			b = b[k:]
			if err := fn(tag>>3, nil, v); err != nil {
				return err
			}
		case 2:
			l, k := binary.Uvarint(b)
			if k <= 0 || l > uint64(len(b)-k) {
				return bad
			}
			val := b[k : k+int(l)]
			b = b[k+int(l):]
			if err := fn(tag>>3, append([]byte{}, val...), 0); err != nil {
				return err
			}
		case 1:
			if len(b) < 8 {
				return bad
			}
			b = b[8:]
		case 5:
			if len(b) < 4 {
				return bad
			}
			b = b[4:]
		default:
			return bad
		}
	}
	return nil
}

func post(ctx context.Context, client *http.Client, target string, body []byte, headers map[string]string) (int, []byte, error) {
	var r io.Reader
	if body != nil {
		r = bytes.NewReader(body)
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, target, r)
	if err != nil {
		return 0, nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	resp, err := client.Do(req)
	if err != nil {
		return 0, nil, err
	}
	defer resp.Body.Close()
	out, err := io.ReadAll(io.LimitReader(resp.Body, 1<<16))
	return resp.StatusCode, out, err
}

func clip(b []byte) string {
	s := strings.TrimSpace(string(b))
	if len(s) > 160 {
		s = s[:160] + "..."
	}
	return s
}
