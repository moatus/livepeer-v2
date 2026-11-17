package main

import (
	"context"
	"fmt"
	"math/big"
	"slices"
	"testing"

	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/core/types"
)

var (
	clockAt  = common.HexToAddress("0xc1")
	claimsAt = common.HexToAddress("0xc2")
)

// fakeChain is a round clock and Claims' share mint, as the rulebook has
// them: a round starts only once it is over (R-18), and a round's shares
// may be minted once, by anyone, any time after it has ended (R-10).
type fakeChain struct {
	round  uint64
	over   bool            // the current round has lasted its length
	minted map[uint64]bool // rounds whose shares are minted
	sent   []string        // every transaction, in order
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
	}
	return &types.Receipt{BlockNumber: big.NewInt(1)}, nil
}

func (f *fakeChain) Uint(_ context.Context, at common.Address, method string, _ ...any) (*big.Int, error) {
	if at == clockAt && method == "currentRound" {
		return new(big.Int).SetUint64(f.round), nil
	}
	return nil, fmt.Errorf("unexpected Uint %s", method)
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
