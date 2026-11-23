package main

import (
	"math/big"
	"os"
	"path/filepath"
	"reflect"
	"testing"
)

// r is a round with stake and fees, unclaimed, under the deployment's
// reward delay of 7.
func r(n uint64) roundFacts {
	return roundFacts{n: n, stake: big.NewInt(1000), fees: big.NewInt(1_000_000), delay: 7, part: new(big.Int)}
}

func TestChooseClaimsOnlyRoundsWithStakeFeesInWindowAndUnclaimed(t *testing.T) {
	now := uint64(40)
	open := r(30) // window 37 to 58: claim
	noStake := r(31)
	noStake.stake = new(big.Int)
	noFees := r(32)
	noFees.fees = new(big.Int)
	early := r(34)    // window 41 to 62: not yet
	expired := r(11)  // window 18 to 39: expired in 40
	lastDay := r(12)  // window 19 to 40: its last round
	firstDay := r(33) // window 40 to 61: its first round
	claimed := r(29)
	claimed.claimed, claimed.part = true, big.NewInt(5)
	p := choose(now, []roundFacts{expired, lastDay, claimed, open, noStake, noFees, firstDay, early})
	if !reflect.DeepEqual(p.claim, []uint64{12, 30, 33}) {
		t.Errorf("claim %v, want [12 30 33]", p.claim)
	}
	if !reflect.DeepEqual(p.withdraw, []uint64{29}) {
		t.Errorf("withdraw %v, want [29]: a claimed round's part, not withdrawn", p.withdraw)
	}
	if !reflect.DeepEqual(p.waiting, []uint64{34}) || !reflect.DeepEqual(p.expired, []uint64{11}) {
		t.Errorf("not yet claimable %v, want [34]; expired %v, want [11]", p.waiting, p.expired)
	}
	if p.next != 12 {
		t.Errorf("next %d, want 12: the first round still to claim", p.next)
	}
}

func TestChooseReadsEachRoundsOwnRewardDelay(t *testing.T) {
	// A delay of 3 for round 20 opens it at 23; round 19 under 7 opens at 26.
	a, b := r(19), r(20)
	b.delay = 3
	p := choose(24, []roundFacts{a, b})
	if !reflect.DeepEqual(p.claim, []uint64{20}) || !reflect.DeepEqual(p.waiting, []uint64{19}) {
		t.Errorf("claim %v, not yet claimable %v", p.claim, p.waiting)
	}
	// Expiry follows the delay too: round 20 under 3 closes after 44,
	// round 19 under 7 after 47.
	p = choose(45, []roundFacts{a, b})
	if !reflect.DeepEqual(p.claim, []uint64{19}) || !reflect.DeepEqual(p.expired, []uint64{20}) {
		t.Errorf("claim %v, expired %v", p.claim, p.expired)
	}
}

func TestChooseNeverClaimsAClaimedRoundAndWithdrawsOnce(t *testing.T) {
	withdrawn := r(10)
	withdrawn.claimed, withdrawn.part, withdrawn.withdrawn = true, big.NewInt(9), true
	zero := r(11)
	zero.claimed = true // a claim at score 0 pays 0: nothing to withdraw
	due := r(12)
	due.claimed, due.part = true, big.NewInt(3)
	p := choose(20, []roundFacts{withdrawn, zero, due})
	if len(p.claim) != 0 {
		t.Errorf("claimed rounds claimed again: %v", p.claim)
	}
	if !reflect.DeepEqual(p.withdraw, []uint64{12}) {
		t.Errorf("withdraw %v, want [12]", p.withdraw)
	}
	if p.next != 20 {
		t.Errorf("next %d, want 20: every round settled once round 12 is withdrawn", p.next)
	}
}

func TestChooseKeepsTheCurrentRoundOpen(t *testing.T) {
	cur := r(20)
	cur.fees = new(big.Int) // its fees may still come
	past := r(19)
	past.fees = new(big.Int)
	p := choose(20, []roundFacts{past, cur})
	if len(p.claim) != 0 || p.next != 20 {
		t.Errorf("claim %v next %d, want none and 20", p.claim, p.next)
	}
	p = choose(20, []roundFacts{{n: 0, stake: big.NewInt(1), fees: big.NewInt(1), delay: 0, part: new(big.Int)}})
	if len(p.claim) != 0 {
		t.Errorf("round 0 was chosen: %v", p.claim)
	}
}

func TestFirstRoundReadsTheCursorWithinItsBounds(t *testing.T) {
	dir := t.TempDir()
	f := filepath.Join(dir, "cursor")
	if got := firstRound(f, 100); got != 1 {
		t.Errorf("no cursor file, round 100: from %d, want 1", got)
	}
	if got := firstRound(f, 2000); got != 2000-861 {
		t.Errorf("no cursor file, round 2000: from %d, want %d", got, 2000-861)
	}
	if err := writeCursor(f, 57); err != nil {
		t.Fatal(err)
	}
	if got := firstRound(f, 100); got != 57 {
		t.Errorf("cursor 57: from %d", got)
	}
	if got := firstRound(f, 50); got != 1 {
		t.Errorf("a cursor after the current round is not believed: from %d", got)
	}
	if got := firstRound(f, 2000); got != 57 {
		t.Errorf("a cursor older than any round still claimable: from %d, want 57, since a claimed part never expires", got)
	}
	os.WriteFile(f, []byte("x\n"), 0o600)
	if got := firstRound(f, 100); got != 1 {
		t.Errorf("an unreadable cursor: from %d", got)
	}
}

// A long outage: the cursor was left at round 12, whose part was claimed
// and not withdrawn, and the container comes back in round 874, when round
// 12 is long past any claim window (12 + 840 + 21 = 873). The part is
// still withdrawn, once.
func TestALongOutageStillWithdrawsAClaimedPart(t *testing.T) {
	f := filepath.Join(t.TempDir(), "cursor")
	if err := writeCursor(f, 12); err != nil {
		t.Fatal(err)
	}
	const now = 874
	facts := func(withdrawn bool) []roundFacts {
		var out []roundFacts
		for n := firstRound(f, now); n <= now; n++ {
			x := r(n)
			x.fees = new(big.Int)
			if n == 12 {
				x = r(12)
				x.claimed, x.part, x.withdrawn = true, big.NewInt(5), withdrawn
			}
			out = append(out, x)
		}
		return out
	}
	p := choose(now, facts(false))
	if !reflect.DeepEqual(p.withdraw, []uint64{12}) {
		t.Fatalf("withdraw %v, want [12]: round 12's part, claimed before the outage", p.withdraw)
	}
	if err := writeCursor(f, p.next); err != nil {
		t.Fatal(err)
	}
	if p = choose(now, facts(true)); len(p.withdraw) != 0 {
		t.Errorf("withdrawn again on the next pass: %v", p.withdraw)
	}
}
