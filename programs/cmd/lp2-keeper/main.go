// lp2-keeper starts each round as soon as the round clock allows
// (rulebook R-18). Anyone may start a round; until someone does, a round
// just lasts longer, so a stopped keeper slows the clock and loses no
// money. It asks the clock by a call first and sends a transaction only
// when the call would succeed.
//
// Given --claims-address, it also mints each ended round's validator and
// treasury amounts (R-10): after it starts round R, it mints R - 1's. It
// mints every ended round whose amounts nobody has minted yet, from round
// 1, not only R - 1, so a round it missed while stopped, or one somebody
// else started, is minted when it runs again. Anyone may mint them, once,
// and they never expire, so a late mint pays the same amounts.
//
// It signs with a keystore file, as the node does, and finds the clock
// through the Controller under the name the node uses.
package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"math/big"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/core/types"

	"lp2core/programs/chain"
)

// chainOps is what the keeper asks of the chain; *chain.Session has it.
type chainOps interface {
	Try(ctx context.Context, at common.Address, method string, args ...any) (string, error)
	Send(ctx context.Context, at common.Address, method string, args ...any) (*types.Receipt, error)
	Uint(ctx context.Context, at common.Address, method string, args ...any) (*big.Int, error)
	Bool(ctx context.Context, at common.Address, method string, args ...any) (bool, error)
}

func main() {
	rpcURL := flag.String("rpc-url", "", "the chain's JSON-RPC URL")
	keystoreFile := flag.String("keystore-file", "", "the keeper's keystore file")
	passwordFile := flag.String("keystore-password-file", "", "the file holding the keystore's password")
	controller := flag.String("controller-address", "", "the Controller the node is given")
	claimsAddr := flag.String("claims-address", "", "Claims (stage 3): also mint each ended round's validator and treasury amounts")
	every := flag.Duration("poll", 2*time.Second, "how often to ask the clock")
	once := flag.Bool("once", false, "try once and exit: 0 started a round, 3 the round is not over")
	flag.Parse()
	if *rpcURL == "" || *keystoreFile == "" || *passwordFile == "" || !common.IsHexAddress(*controller) {
		fail(fmt.Errorf("--rpc-url, --keystore-file, --keystore-password-file and --controller-address are required"))
	}
	if *claimsAddr != "" && !common.IsHexAddress(*claimsAddr) {
		fail(fmt.Errorf("--claims-address %q is not an address", *claimsAddr))
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	key, err := chain.LoadKey(*keystoreFile, *passwordFile)
	if err != nil {
		fail(err)
	}
	s, err := chain.Open(ctx, *rpcURL, key)
	if err != nil {
		fail(err)
	}
	clock, err := s.Resolve(ctx, common.HexToAddress(*controller), "RoundsManager")
	if err != nil {
		fail(err)
	}
	log.SetFlags(log.Ltime)
	log.Printf("keeper %s watching the round clock at %s", s.From, clock)
	k := &keeper{ops: s, clock: clock, next: 1}
	if *claimsAddr != "" {
		k.claims = common.HexToAddress(*claimsAddr)
		log.Printf("and minting each ended round's shares at Claims %s", k.claims)
	}

	for {
		started, err := k.tick(ctx)
		if err != nil {
			log.Printf("%v", err)
		}
		if *once {
			if err != nil {
				os.Exit(1)
			}
			if !started {
				os.Exit(3)
			}
			return
		}
		select {
		case <-ctx.Done():
			return
		case <-time.After(*every):
		}
	}
}

type keeper struct {
	ops    chainOps
	clock  common.Address
	claims common.Address // zero: stage 2, no shares to mint
	next   uint64         // the first round whose shares it has not seen minted
}

// tick starts the next round if the current one is over, then mints the
// shares of every ended round not yet minted.
func (k *keeper) tick(ctx context.Context) (bool, error) {
	started, err := k.startRound(ctx)
	if err != nil {
		return started, err
	}
	if k.claims != (common.Address{}) {
		err = k.mintDue(ctx)
	}
	return started, err
}

func (k *keeper) startRound(ctx context.Context) (bool, error) {
	code, err := k.ops.Try(ctx, k.clock, "initializeRound")
	if err != nil {
		return false, err
	}
	if code == "ROUND_NOT_OVER" {
		return false, nil
	}
	if code != "" {
		return false, fmt.Errorf("initializeRound refused: %s", code)
	}
	receipt, err := k.ops.Send(ctx, k.clock, "initializeRound")
	if err != nil {
		return false, err
	}
	round, err := k.ops.Uint(ctx, k.clock, "currentRound")
	if err != nil {
		return true, err
	}
	log.Printf("started round %s at block %d", round, receipt.BlockNumber)
	return true, nil
}

// mintDue mints the shares of each round from k.next up to the one before
// the current round, skipping any somebody else has minted. It stops at
// the first error and tries that round again on the next tick.
func (k *keeper) mintDue(ctx context.Context) error {
	current, err := k.ops.Uint(ctx, k.clock, "currentRound")
	if err != nil {
		return err
	}
	for ; k.next < current.Uint64(); k.next++ {
		r := new(big.Int).SetUint64(k.next)
		minted, err := k.ops.Bool(ctx, k.claims, "sharesMinted", r)
		if err != nil {
			return err
		}
		if minted {
			continue
		}
		code, err := k.ops.Try(ctx, k.claims, "mintRoundShares", r)
		if err != nil {
			return err
		}
		if code == "ALREADY_CLAIMED" {
			continue
		}
		if code != "" {
			return fmt.Errorf("mintRoundShares(%d) refused: %s", k.next, code)
		}
		receipt, err := k.ops.Send(ctx, k.claims, "mintRoundShares", r)
		if err != nil {
			return err
		}
		log.Printf("minted round %d's validator and treasury amounts at block %d", k.next, receipt.BlockNumber)
	}
	return nil
}

func fail(err error) {
	fmt.Fprintln(os.Stderr, "lp2-keeper:", err)
	os.Exit(1)
}
