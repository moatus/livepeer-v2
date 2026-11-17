// lp2-keeper starts each round as soon as the round clock allows
// (rulebook R-18). Anyone may start a round; until someone does, a round
// just lasts longer, so a stopped keeper slows the clock and loses no
// money. It asks the clock by a call first and sends a transaction only
// when the call would succeed.
//
// It signs with a keystore file, as the node does, and finds the clock
// through the Controller under the name the node uses.
package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/ethereum/go-ethereum/common"

	"lp2core/programs/chain"
)

func main() {
	rpcURL := flag.String("rpc-url", "", "the chain's JSON-RPC URL")
	keystoreFile := flag.String("keystore-file", "", "the keeper's keystore file")
	passwordFile := flag.String("keystore-password-file", "", "the file holding the keystore's password")
	controller := flag.String("controller-address", "", "the Controller the node is given")
	every := flag.Duration("poll", 2*time.Second, "how often to ask the clock")
	once := flag.Bool("once", false, "try once and exit: 0 started a round, 3 the round is not over")
	flag.Parse()
	if *rpcURL == "" || *keystoreFile == "" || *passwordFile == "" || !common.IsHexAddress(*controller) {
		fail(fmt.Errorf("--rpc-url, --keystore-file, --keystore-password-file and --controller-address are required"))
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

	for {
		started, err := tick(ctx, s, clock)
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

// tick starts the next round if the current one is over.
func tick(ctx context.Context, s *chain.Session, clock common.Address) (bool, error) {
	code, err := s.Try(ctx, clock, "initializeRound")
	if err != nil {
		return false, err
	}
	if code == "ROUND_NOT_OVER" {
		return false, nil
	}
	if code != "" {
		return false, fmt.Errorf("initializeRound refused: %s", code)
	}
	receipt, err := s.Send(ctx, clock, "initializeRound")
	if err != nil {
		return false, err
	}
	round, err := s.Uint(ctx, clock, "currentRound")
	if err != nil {
		return true, err
	}
	log.Printf("started round %s at block %d", round, receipt.BlockNumber)
	return true, nil
}

func fail(err error) {
	fmt.Fprintln(os.Stderr, "lp2-keeper:", err)
	os.Exit(1)
}
