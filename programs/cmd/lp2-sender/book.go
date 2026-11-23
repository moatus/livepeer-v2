package main

import (
	"sync"
	"time"

	"github.com/ethereum/go-ethereum/common"
)

// book is the sender's state: the list, which is also the list file, and
// what it holds in memory only (which nodes are being paid now, when each
// may next be tried, when each joined). Every change to the list is
// written to the file before it is used.
type book struct {
	mu       sync.Mutex
	path     string
	max      int
	entries  []entry
	inFlight map[common.Address]bool
	nextTry  map[common.Address]time.Time
	joinedAt map[common.Address]time.Time
	onAccept func(common.Address)
}

func openBook(path string, max int) (*book, error) {
	entries, err := readList(path)
	if err != nil {
		return nil, err
	}
	return &book{path: path, max: max, entries: entries, inFlight: map[common.Address]bool{},
		nextTry: map[common.Address]time.Time{}, joinedAt: map[common.Address]time.Time{}}, nil
}

// isDue is the once-per-round rule: a node is sent a paid request in a
// round only if it has not been sent one in that round or a later one.
// The round a node was last sent one is written to the list file before
// the request is sent, so a restarted sender does not send it another.
func isDue(e entry, round uint64) bool {
	return e.PaidRound < round
}

func (b *book) find(node common.Address) int {
	for i, e := range b.entries {
		if e.Node == node {
			return i
		}
	}
	return -1
}

func (b *book) list() []entry {
	b.mu.Lock()
	defer b.mu.Unlock()
	return append([]entry(nil), b.entries...)
}

// lastJoin is the issuedAt of the last join accepted for node.
func (b *book) lastJoin(node common.Address) (int64, bool) {
	b.mu.Lock()
	defer b.mu.Unlock()
	if i := b.find(node); i >= 0 {
		return b.entries[i].IssuedAt, true
	}
	return 0, false
}

// full says whether node would be refused for want of room: it is not
// listed and the list holds its most.
func (b *book) full(node common.Address) bool {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.find(node) < 0 && len(b.entries) >= b.max
}

// accept lists e, or updates the URL and issuedAt of the node already
// listed, keeping the round it was last paid in, and writes the list.
// The freshness and room checks are made again here, under the lock, so
// two joins at once cannot both pass them.
func (b *book) accept(e entry) (bool, *refusal) {
	b.mu.Lock()
	i := b.find(e.Node)
	next := append([]entry(nil), b.entries...)
	if i >= 0 {
		if e.IssuedAt <= next[i].IssuedAt {
			b.mu.Unlock()
			return false, refuse(codeStale, "issuedAt %d is not newer than the last join accepted for the node, %d", e.IssuedAt, next[i].IssuedAt)
		}
		e.PaidRound = next[i].PaidRound
		next[i] = e
	} else {
		if len(next) >= b.max {
			b.mu.Unlock()
			return false, refuse(codeFull, "the list holds its most nodes")
		}
		next = append(next, e)
	}
	if err := writeList(b.path, next); err != nil {
		b.mu.Unlock()
		return false, refuse(codeUnavailable, "could not write the list: %v", err)
	}
	b.entries = next
	b.joinedAt[e.Node] = time.Now()
	delete(b.nextTry, e.Node)
	b.mu.Unlock()
	if b.onAccept != nil {
		b.onAccept(e.Node)
	}
	return i >= 0, nil
}

// take returns the nodes due in round that are not being paid now and
// whose retry wait has passed, and marks them as being paid.
func (b *book) take(round uint64, now time.Time) []entry {
	b.mu.Lock()
	defer b.mu.Unlock()
	var out []entry
	for _, e := range b.entries {
		if isDue(e, round) && !b.inFlight[e.Node] && !now.Before(b.nextTry[e.Node]) {
			b.inFlight[e.Node] = true
			out = append(out, e)
		}
	}
	return out
}

// markPaid records, in the list file, that node is sent its paid request
// for round. It is called before the request is sent.
func (b *book) markPaid(node common.Address, round uint64) error {
	b.mu.Lock()
	defer b.mu.Unlock()
	i := b.find(node)
	if i < 0 {
		return nil
	}
	next := append([]entry(nil), b.entries...)
	next[i].PaidRound = round
	if err := writeList(b.path, next); err != nil {
		return err
	}
	b.entries = next
	return nil
}

// done ends a node's attempt. With retry, it may be tried again in the
// same round after wait.
func (b *book) done(node common.Address, retry bool, wait time.Duration) {
	b.mu.Lock()
	defer b.mu.Unlock()
	delete(b.inFlight, node)
	if retry {
		b.nextTry[node] = time.Now().Add(wait)
	}
}

// firstRelay returns how long ago node's join was accepted, once: for
// the first relay after a join.
func (b *book) firstRelay(node common.Address) (time.Duration, bool) {
	b.mu.Lock()
	defer b.mu.Unlock()
	t, ok := b.joinedAt[node]
	if ok {
		delete(b.joinedAt, node)
	}
	return time.Since(t), ok
}
