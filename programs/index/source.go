package index

import (
	"context"
	"errors"
	"sync"
	"time"

	"github.com/ethereum/go-ethereum/rpc"
)

// Caller is the chain's JSON-RPC endpoint; *rpc.Client is one.
type Caller interface {
	BatchCallContext(ctx context.Context, b []rpc.BatchElem) error
}

// maxBatch is the most requests sent in one JSON-RPC batch.
const maxBatch = 20

// requestTimeout bounds one batch sent to the provider.
const requestTimeout = 30 * time.Second

// The waits after an HTTP 429 that names no Retry-After: doubling from the
// first to the last.
const (
	firstWait = time.Second
	lastWait  = 60 * time.Second
)

// Source sends every request the explorer makes, the index's and the
// views', to the provider: at most PerSecond request elements in any one
// second, in batches of at most maxBatch, each answered within 30 seconds
// or failed, and after an HTTP 429 it waits
// (the provider's Retry-After, or doubling from 1 s to 60 s) and sends the
// same batch again, until it is answered or ctx ends. Every other failure
// of a batch is set on each of its requests, so each figure is read or
// carries the reason it was not.
type Source struct {
	rpc       Caller
	perSecond int
	// Retry, when set, is the wait the provider last asked for in a
	// Retry-After header, 0 when it asked for none.
	Retry func() time.Duration
	// Seen, when set, is told the outcome of each batch: nil when the
	// provider answered it, or its failure. what names the first request.
	Seen func(what string, err error)
	// Timeout bounds each batch: 30 seconds, shorter in a test.
	Timeout time.Duration
	// now and sleep are the clock; a test gives its own.
	now   func() time.Time
	sleep func(ctx context.Context, d time.Duration) error

	mu   sync.Mutex
	sent []time.Time // when each element of the last second was sent
}

// NewSource sends to c at most perSecond request elements a second.
func NewSource(c Caller, perSecond int) *Source {
	if perSecond < 1 {
		perSecond = 1
	}
	return &Source{rpc: c, perSecond: perSecond, now: time.Now, sleep: sleepCtx, Timeout: requestTimeout}
}

func sleepCtx(ctx context.Context, d time.Duration) error {
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-t.C:
		return nil
	}
}

// Send sends the requests. what names them for Seen.
func (s *Source) Send(ctx context.Context, what string, reqs []rpc.BatchElem) {
	size := min(maxBatch, s.perSecond)
	for start := 0; start < len(reqs); start += size {
		part := reqs[start:min(start+size, len(reqs))]
		err := s.batch(ctx, what, part)
		if err != nil {
			for i := range part {
				part[i].Error = err
			}
		}
	}
}

// batch sends one batch, waiting out each 429.
func (s *Source) batch(ctx context.Context, what string, part []rpc.BatchElem) error {
	wait := firstWait
	for {
		if err := s.take(ctx, len(part)); err != nil {
			return err
		}
		for i := range part {
			part[i].Error = nil
		}
		call, cancel := context.WithTimeout(ctx, s.Timeout)
		err := s.rpc.BatchCallContext(call, part)
		cancel()
		if s.Seen != nil {
			s.Seen(what, err)
		}
		if !limited(err) {
			return err
		}
		w := wait
		if s.Retry != nil {
			if asked := s.Retry(); asked > 0 {
				w = asked
			}
		}
		if err := s.sleep(ctx, w); err != nil {
			return err
		}
		wait = min(2*wait, lastWait)
	}
}

// limited says whether err is an HTTP 429.
func limited(err error) bool {
	var h rpc.HTTPError
	if errors.As(err, &h) {
		return h.StatusCode == 429
	}
	var hp *rpc.HTTPError
	return errors.As(err, &hp) && hp.StatusCode == 429
}

// take waits until n more elements can be sent without more than
// perSecond in any one second, and counts them as sent.
func (s *Source) take(ctx context.Context, n int) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	for {
		now := s.now()
		keep := s.sent[:0]
		for _, t := range s.sent {
			if now.Sub(t) < time.Second {
				keep = append(keep, t)
			}
		}
		s.sent = keep
		if len(s.sent)+n <= s.perSecond {
			for range n {
				s.sent = append(s.sent, now)
			}
			return nil
		}
		// The oldest elements leave the second first.
		free := s.sent[len(s.sent)+n-s.perSecond-1]
		if err := s.sleep(ctx, time.Second-now.Sub(free)); err != nil {
			return err
		}
	}
}
