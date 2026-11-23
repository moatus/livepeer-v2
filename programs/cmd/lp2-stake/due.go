package main

import (
	"context"
	"fmt"
	"math/big"
	"os"
	"path/filepath"
	"slices"
	"strconv"
	"strings"

	"github.com/ethereum/go-ethereum/common"

	"lp2core/programs/chain"
)

const (
	// claimWindow is R-12's: round n is claimable in rounds n + d to n + d + 21.
	claimWindow = 21
	// maxDelay is the largest reward delay R-25 accepts for group 4.
	maxDelay = 840
	// groupRewardDelay is R-25's group 4.
	groupRewardDelay = 4
)

// roundFacts is what the chain says of node's earning round n, for the
// holder: what claim-due and withdraw-due decide from.
type roundFacts struct {
	n         uint64
	stake     *big.Int // StakeLedger stakeAt(node, n)
	fees      *big.Int // Broker feesOf(n, node)
	delay     uint64   // Parameters valuesAt(4, n): the reward delay in force for n
	claimed   bool     // Claims entitlementOf(node, n)
	part      *big.Int // Claims partOf(holder, node, n)
	withdrawn bool
}

// plan is what is due in round now: the rounds to claim, the rounds whose
// part to withdraw, why each other round is left, and the first round
// that may still need a claim or a withdrawal once the plan is done.
type plan struct {
	claim, withdraw []uint64
	left            map[uint64]string
	next            uint64
}

// choose decides, for each round, in round now: a round with no stake or
// no fees is never claimed; a claimed round's part is withdrawn when it is
// above zero and not yet withdrawn; an unclaimed round is claimed when now
// is in its window, n + d to n + d + 21, and left when now is before it
// (not yet claimable) or after it (expired: R-12 closes it). A round is
// settled for good when nothing more can come of it: over with no stake or
// no fees, expired, or claimed with its part withdrawn or zero. The rounds
// in withdraw count as withdrawn for next.
func choose(now uint64, rounds []roundFacts) plan {
	p := plan{left: map[uint64]string{}, next: now}
	open := func(n uint64) {
		if n < p.next {
			p.next = n
		}
	}
	for _, r := range rounds {
		switch {
		case r.n == 0:
			continue // round 0 is no round (R-18)
		case r.stake.Sign() == 0 || r.fees.Sign() == 0:
			if r.stake.Sign() == 0 {
				p.left[r.n] = "no stake"
			} else {
				p.left[r.n] = "no fees"
			}
			if r.n >= now {
				open(r.n) // fees of the current round may still come
			}
		case r.claimed:
			if r.part.Sign() > 0 && !r.withdrawn {
				p.withdraw = append(p.withdraw, r.n)
			} else if r.withdrawn {
				p.left[r.n] = "claimed and withdrawn"
			} else {
				p.left[r.n] = "claimed, part 0"
			}
		case now < r.n+r.delay:
			p.left[r.n] = fmt.Sprintf("not yet claimable: from round %d", r.n+r.delay)
			open(r.n)
		case now > r.n+r.delay+claimWindow:
			p.left[r.n] = fmt.Sprintf("expired after round %d", r.n+r.delay+claimWindow)
		default:
			p.claim = append(p.claim, r.n)
			open(r.n)
		}
	}
	return p
}

// firstRound is where a pass starts reading: the cursor file's round when
// it holds one, otherwise the first round that can still be open in round
// now, now - 861, or 1.
func firstRound(cursorFile string, now uint64) uint64 {
	from := uint64(1)
	if now > maxDelay+claimWindow {
		from = now - maxDelay - claimWindow
	}
	if cursorFile == "" {
		return from
	}
	raw, err := os.ReadFile(cursorFile)
	if err != nil {
		return from
	}
	n, err := strconv.ParseUint(strings.TrimSpace(string(raw)), 10, 64)
	if err != nil || n < from || n > now {
		return from
	}
	return n
}

// writeCursor replaces the cursor file's round, atomically.
func writeCursor(cursorFile string, n uint64) error {
	tmp, err := os.CreateTemp(filepath.Dir(cursorFile), ".cursor-*")
	if err != nil {
		return err
	}
	defer os.Remove(tmp.Name())
	if _, err := fmt.Fprintf(tmp, "%d\n", n); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	return os.Rename(tmp.Name(), cursorFile)
}

// readRounds reads the facts of rounds from to now, for node and holder,
// each at the newest block. A round with no stake or no fees is read no
// further.
func readRounds(ctx context.Context, s *chain.Session, a map[string]common.Address, node, holder common.Address, from, now uint64) ([]roundFacts, error) {
	var out []roundFacts
	for n := from; n <= now; n++ {
		r := roundFacts{n: n, part: new(big.Int)}
		round := new(big.Int).SetUint64(n)
		var err error
		if r.fees, err = s.Uint(ctx, a["broker"], "feesOf", round, node); err != nil {
			return nil, err
		}
		if r.stake, err = s.Uint(ctx, a["ledger"], "stakeAt", node, round); err != nil {
			return nil, err
		}
		if r.fees.Sign() > 0 && r.stake.Sign() > 0 {
			values, err := s.Call(ctx, a["params"], "valuesAt", big.NewInt(groupRewardDelay), round)
			if err != nil {
				return nil, err
			}
			d, ok := values[0].([]*big.Int)
			if !ok || len(d) != 1 || !d[0].IsUint64() {
				return nil, fmt.Errorf("valuesAt(%d, %d) is not one reward delay", groupRewardDelay, n)
			}
			r.delay = d[0].Uint64()
			ent, err := s.Call(ctx, a["claims"], "entitlementOf", node, round)
			if err != nil {
				return nil, err
			}
			r.claimed = ent[0].(bool)
			if r.claimed {
				part, err := s.Call(ctx, a["claims"], "partOf", holder, node, round)
				if err != nil {
					return nil, err
				}
				r.part, r.withdrawn = part[0].(*big.Int), part[1].(bool)
			}
		}
		out = append(out, r)
	}
	return out, nil
}

// due is claim-due (claim true) or withdraw-due for node, as the session's
// key: the node's operator key claims; a holder at node withdraws.
func due(ctx context.Context, s *chain.Session, a map[string]common.Address, node common.Address, claim bool, cursorFile string) error {
	if err := settle(ctx, s); err != nil {
		return err
	}
	cur, err := s.Uint(ctx, a["clock"], "currentRound")
	if err != nil {
		return err
	}
	now := cur.Uint64()
	from := firstRound(cursorFile, now)
	rounds, err := readRounds(ctx, s, a, node, s.From, from, now)
	if err != nil {
		return err
	}
	p := choose(now, rounds)
	fmt.Printf("round %d: rounds %d to %d of %s read; %s\n", now, from, now, node, summary(p, claim))
	if claim {
		for _, n := range p.claim {
			round := new(big.Int).SetUint64(n)
			code, block, err := s.TryAt(ctx, a["claims"], "claim", node, round)
			if err != nil {
				return err
			}
			if code != "" {
				fmt.Printf("refused: %s at block %d (claim of round %d by %s)\n", code, block, n, s.From)
				continue
			}
			r, err := s.Send(ctx, a["claims"], "claim", node, round)
			if err != nil {
				return err
			}
			fmt.Printf("sent: claim of round %d by %s (block %d, tx %s)\n", n, s.From, r.BlockNumber, r.TxHash)
		}
		return nil
	}
	if len(p.withdraw) > 0 {
		var list []*big.Int
		for _, n := range p.withdraw {
			list = append(list, new(big.Int).SetUint64(n))
		}
		code, block, err := s.TryAt(ctx, a["claims"], "withdrawParts", node, list)
		if err != nil {
			return err
		}
		if code != "" {
			fmt.Printf("refused: %s at block %d (withdraw-parts of rounds %v by %s)\n", code, block, p.withdraw, s.From)
			return nil
		}
		r, err := s.Send(ctx, a["claims"], "withdrawParts", node, list)
		if err != nil {
			return err
		}
		fmt.Printf("sent: withdraw-parts of rounds %v by %s (block %d, tx %s)\n", p.withdraw, s.From, r.BlockNumber, r.TxHash)
	}
	if cursorFile != "" {
		return writeCursor(cursorFile, p.next)
	}
	return nil
}

// summary is one line of what the plan holds.
func summary(p plan, claim bool) string {
	var waiting, expired []uint64
	for n, why := range p.left {
		switch {
		case strings.HasPrefix(why, "not yet"):
			waiting = append(waiting, n)
		case strings.HasPrefix(why, "expired"):
			expired = append(expired, n)
		}
	}
	slices.Sort(waiting)
	slices.Sort(expired)
	if claim {
		return fmt.Sprintf("to claim %v, not yet claimable %v, expired unclaimed %v", list(p.claim), list(waiting), list(expired))
	}
	return fmt.Sprintf("parts to withdraw %v; rounds before %d settled", list(p.withdraw), p.next)
}

func list(xs []uint64) []uint64 {
	if xs == nil {
		return []uint64{}
	}
	return xs
}
