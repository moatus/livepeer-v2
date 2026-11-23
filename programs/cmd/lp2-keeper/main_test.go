package main

import (
	"context"
	"fmt"
	"log"
	"math/big"
	"os"
	"slices"
	"strings"
	"testing"

	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/core/types"
)

var (
	clockAt   = common.HexToAddress("0xc1")
	claimsAt  = common.HexToAddress("0xc2")
	buybackAt = common.HexToAddress("0xc3")
	paramsAt  = common.HexToAddress("0xc4")
	movedTo   = common.HexToAddress("0xc5")
)

// fakeChain is a round clock, Claims' share mint and Parameters' group 10,
// as the rulebook has them: a round starts only once it is over (R-18), a
// round's shares may be minted once, by anyone, any time after it has
// ended (R-10), and the executor in force is group 10's for the round
// (R-25): buybackAt, or movedTo from the round moveAt when it is set.
type fakeChain struct {
	round  uint64
	over   bool              // the current round has lasted its length
	minted map[uint64]bool   // rounds whose shares are minted
	sent   []string          // every transaction, in order
	refuse map[string]string // the executor's refusal of buy or poke, if any
	moveAt uint64            // the round movedTo is in force from; 0 never
	none   bool              // group 10 is address 0
}

// executorAt is group 10's value in round r.
func (f *fakeChain) executorAt(r uint64) common.Address {
	switch {
	case f.none:
		return common.Address{}
	case f.moveAt != 0 && r >= f.moveAt:
		return movedTo
	}
	return buybackAt
}

func (f *fakeChain) Try(_ context.Context, at common.Address, method string, args ...any) (string, error) {
	switch {
	case at == clockAt && method == "initializeRound":
		if !f.over {
			return "ROUND_NOT_OVER", nil
		}
		return "", nil
	case at == claimsAt && method == "mintRoundShares":
		r := args[0].(*big.Int).Uint64()
		if r >= f.round {
			return "CLAIM_TOO_EARLY", nil
		}
		if f.minted[r] {
			return "ALREADY_CLAIMED", nil
		}
		return "", nil
	case (at == buybackAt || at == movedTo) && (method == "buy" || method == "poke"):
		if at != f.executorAt(f.round) {
			return "NOT_AUTHORIZED", nil
		}
		return f.refuse[method], nil
	}
	return "", fmt.Errorf("unexpected Try %s", method)
}

func (f *fakeChain) Send(ctx context.Context, at common.Address, method string, args ...any) (*types.Receipt, error) {
	if code, err := f.Try(ctx, at, method, args...); err != nil || code != "" {
		return nil, fmt.Errorf("%s refused: %s %v", method, code, err)
	}
	switch method {
	case "initializeRound":
		f.round++
		f.over = false
		f.sent = append(f.sent, fmt.Sprintf("start %d", f.round))
	case "mintRoundShares":
		r := args[0].(*big.Int).Uint64()
		f.minted[r] = true
		f.sent = append(f.sent, fmt.Sprintf("mint %d", r))
	case "buy", "poke":
		f.sent = append(f.sent, fmt.Sprintf("%s %s", method, strings.ToLower(at.Hex()[40:])))
	}
	return &types.Receipt{BlockNumber: big.NewInt(1)}, nil
}

func (f *fakeChain) Uint(_ context.Context, at common.Address, method string, _ ...any) (*big.Int, error) {
	if at == clockAt && method == "currentRound" {
		return new(big.Int).SetUint64(f.round), nil
	}
	return nil, fmt.Errorf("unexpected Uint %s", method)
}

func (f *fakeChain) Call(_ context.Context, at common.Address, method string, args ...any) ([]any, error) {
	if at == paramsAt && method == "valuesAt" && args[0].(*big.Int).Cmp(big.NewInt(10)) == 0 {
		e := f.executorAt(args[1].(*big.Int).Uint64())
		return []any{[]*big.Int{new(big.Int).SetBytes(e.Bytes())}}, nil
	}
	return nil, fmt.Errorf("unexpected Call %s", method)
}

func (f *fakeChain) Bool(_ context.Context, at common.Address, method string, args ...any) (bool, error) {
	if at == claimsAt && method == "sharesMinted" {
		return f.minted[args[0].(*big.Int).Uint64()], nil
	}
	return false, fmt.Errorf("unexpected Bool %s", method)
}

func newKeeper(f *fakeChain, claims common.Address) *keeper {
	return &keeper{ops: f, clock: clockAt, claims: claims, next: 1}
}

func tick(t *testing.T, k *keeper) {
	t.Helper()
	if _, err := k.tick(context.Background()); err != nil {
		t.Fatal(err)
	}
}

func TestAfterStartingRoundRItMintsRMinus1(t *testing.T) {
	f := &fakeChain{round: 1, minted: map[uint64]bool{}}
	k := newKeeper(f, claimsAt)
	tick(t, k) // round 1 not over: nothing to do
	f.over = true
	tick(t, k)
	f.over = true
	tick(t, k)
	tick(t, k) // nothing new: nothing sent twice
	want := []string{"start 2", "mint 1", "start 3", "mint 2"}
	if !slices.Equal(f.sent, want) {
		t.Fatalf("sent %v, want %v", f.sent, want)
	}
}

// A keeper that was stopped while somebody else started rounds 3 and 4
// mints rounds 2 and 3 when it runs again: the shares were delayed, not
// lost. One that minted only R - 1 after starting R itself would mint
// neither.
func TestARestartedKeeperMintsEveryRoundItMissed(t *testing.T) {
	f := &fakeChain{round: 4, minted: map[uint64]bool{1: true}}
	k := newKeeper(f, claimsAt)
	tick(t, k)
	want := []string{"mint 2", "mint 3"}
	if !slices.Equal(f.sent, want) {
		t.Fatalf("sent %v, want %v", f.sent, want)
	}
	if !f.minted[2] || !f.minted[3] || f.minted[4] {
		t.Fatalf("minted %v, want 1 to 3 and not the current round", f.minted)
	}
}

func TestSharesSomebodyElseMintedAreNotSentAgain(t *testing.T) {
	f := &fakeChain{round: 3, minted: map[uint64]bool{1: true, 2: true}}
	k := newKeeper(f, claimsAt)
	tick(t, k)
	if len(f.sent) != 0 {
		t.Fatalf("sent %v, want nothing", f.sent)
	}
}

func TestWithoutClaimsItOnlyStartsRounds(t *testing.T) {
	f := &fakeChain{round: 1, over: true, minted: map[uint64]bool{}}
	k := newKeeper(f, common.Address{})
	tick(t, k)
	if !slices.Equal(f.sent, []string{"start 2"}) {
		t.Fatalf("sent %v, want only the round start", f.sent)
	}
}

func newBuybackKeeper(f *fakeChain) *keeper {
	return &keeper{ops: f, clock: clockAt, next: 1, params: paramsAt}
}

// logged runs fn and returns what it logged.
func logged(fn func()) string {
	var b strings.Builder
	log.SetOutput(&b)
	defer log.SetOutput(os.Stderr)
	fn()
	return b.String()
}

func TestItBuysAndPokesWhenBothWouldSucceed(t *testing.T) {
	f := &fakeChain{round: 1, refuse: map[string]string{}}
	tick(t, newBuybackKeeper(f))
	if !slices.Equal(f.sent, []string{"buy c3", "poke c3"}) {
		t.Fatalf("sent %v, want buy then poke", f.sent)
	}
}

// In a backlog, SLIPPAGE between capped buys is a wait, not a fault: the
// keeper sends nothing it would see refused, still pokes when poke would
// succeed, and logs the wait once, not every tick.
func TestSlippageIsAnExpectedWaitLoggedOnce(t *testing.T) {
	f := &fakeChain{round: 1, refuse: map[string]string{"buy": "SLIPPAGE", "poke": "NOT_DUE"}}
	k := newBuybackKeeper(f)
	out := logged(func() {
		tick(t, k)
		tick(t, k)
		f.refuse["poke"] = ""
		tick(t, k)
	})
	if !slices.Equal(f.sent, []string{"poke c3"}) {
		t.Fatalf("sent %v, want only the poke that would succeed", f.sent)
	}
	if n := strings.Count(out, "buy refused SLIPPAGE, as expected: waiting for the average to follow"); n != 1 {
		t.Fatalf("logged the SLIPPAGE wait %d times, want once:\n%s", n, out)
	}
	if n := strings.Count(out, "poke refused NOT_DUE, as expected"); n != 1 {
		t.Fatalf("logged NOT_DUE %d times, want once:\n%s", n, out)
	}
}

// A pool the Buyback finds invalid, or one that delivers short, is a
// fault: the tick fails, naming the code, and nothing is sent.
func TestAFaultyPoolIsAnError(t *testing.T) {
	for _, code := range []string{"POOL_INVALID", "LPT_SHORT"} {
		f := &fakeChain{round: 1, refuse: map[string]string{"buy": code}}
		_, err := newBuybackKeeper(f).tick(context.Background())
		if err == nil || !strings.Contains(err.Error(), code) {
			t.Fatalf("%s: tick returned %v, want an error naming it", code, err)
		}
		if len(f.sent) != 0 {
			t.Fatalf("%s: sent %v, want nothing", code, f.sent)
		}
	}
}

// A keeper that is running follows a change of executor (D-51): from the
// round group 10 names another, it buys and pokes there and no longer at
// the executor it started with, whose buy the Broker would refuse.
func TestARunningKeeperFollowsAMoveOfTheExecutor(t *testing.T) {
	f := &fakeChain{round: 1, refuse: map[string]string{}, moveAt: 2}
	k := newBuybackKeeper(f)
	out := logged(func() {
		tick(t, k)
		f.over = true
		tick(t, k)
		tick(t, k)
	})
	want := []string{"buy c3", "poke c3", "start 2", "buy c5", "poke c5", "buy c5", "poke c5"}
	if !slices.Equal(f.sent, want) {
		t.Fatalf("sent %v, want %v", f.sent, want)
	}
	if n := strings.Count(out, "buying and poking at executor"); n != 2 {
		t.Fatalf("logged the executor %d times, want once each:\n%s", n, out)
	}
}

// With group 10 address 0, a deployment with no executor, it calls nothing.
func TestWithNoExecutorItCallsNothing(t *testing.T) {
	f := &fakeChain{round: 1, refuse: map[string]string{}, none: true}
	tick(t, newBuybackKeeper(f))
	if len(f.sent) != 0 {
		t.Fatalf("sent %v, want nothing", f.sent)
	}
}
