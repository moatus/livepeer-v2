// lp2-validators runs scripted validators: stand-ins that vote what a
// script says, not what they judged (rulebook R-11, R-13). Each line of the
// script is one act of one validator in one on-chain round:
//
//	# round validator target score: the validator votes score on target
//	4 V1 M 500000
//	# round validator opt-in: the validator opts itself in
//	3 V1 opt-in
//
// Names are read from a JSON file of name to address, and each validator
// signs with its own keystore (--keystore V1=path, once per validator).
// It watches the round clock and does each line once, in file order, as
// soon as the clock reaches the line's round. A line whose round has passed
// when it starts is done at once, late: a vote stands until replaced and
// opting in again changes nothing, so running a script again from the top
// leaves the same votes standing. A refused line is logged with its code
// and the block it was asked at, "refused CODE at block N", and not tried
// again: nothing is sent, and the answer holds after every transaction of
// that block.
package main

import (
	"bufio"
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"log"
	"math/big"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/ethereum/go-ethereum/common"

	"lp2core/programs/chain"
)

// line is one scripted act.
type line struct {
	number    int // in the file, for the log
	round     uint64
	validator string
	optIn     bool
	target    string
	score     *big.Int
}

// parseScript reads a script. Every name must be in names and every
// validator must have a key.
func parseScript(r io.Reader, names map[string]common.Address, keyed map[string]bool) ([]line, error) {
	var out []line
	scan := bufio.NewScanner(r)
	for n := 1; scan.Scan(); n++ {
		text := strings.TrimSpace(scan.Text())
		if text == "" || strings.HasPrefix(text, "#") {
			continue
		}
		f := strings.Fields(text)
		l := line{number: n}
		round, err := strconv.ParseUint(f[0], 10, 64)
		if err != nil || round == 0 {
			return nil, fmt.Errorf("line %d: %q is not a round (from 1)", n, f[0])
		}
		l.round = round
		switch {
		case len(f) == 3 && f[2] == "opt-in":
			l.optIn = true
		case len(f) == 4:
			l.target = f[2]
			s, ok := new(big.Int).SetString(f[3], 10)
			if !ok || s.Sign() < 0 {
				return nil, fmt.Errorf("line %d: %q is not a score", n, f[3])
			}
			l.score = s
			if _, ok := names[l.target]; !ok {
				return nil, fmt.Errorf("line %d: no address for %q", n, l.target)
			}
		default:
			return nil, fmt.Errorf("line %d: want \"round validator target score\" or \"round validator opt-in\", got %q", n, text)
		}
		l.validator = f[1]
		if _, ok := names[l.validator]; !ok {
			return nil, fmt.Errorf("line %d: no address for %q", n, l.validator)
		}
		if !keyed[l.validator] {
			return nil, fmt.Errorf("line %d: no keystore for validator %q", n, l.validator)
		}
		if len(out) > 0 && l.round < out[len(out)-1].round {
			return nil, fmt.Errorf("line %d: round %d comes after round %d", n, l.round, out[len(out)-1].round)
		}
		out = append(out, l)
	}
	return out, scan.Err()
}

// due returns the lines not yet done whose round has come, in file order,
// and marks them done.
func due(lines []line, done []bool, current uint64) []line {
	var out []line
	for i, l := range lines {
		if !done[i] && l.round <= current {
			done[i] = true
			out = append(out, l)
		}
	}
	return out
}

type keystores map[string]string

func (k keystores) String() string { return fmt.Sprint(map[string]string(k)) }
func (k keystores) Set(v string) error {
	name, path, ok := strings.Cut(v, "=")
	if !ok || name == "" || path == "" {
		return fmt.Errorf("want NAME=keystore-file, got %q", v)
	}
	k[name] = path
	return nil
}

func main() {
	keys := keystores{}
	rpcURL := flag.String("rpc-url", "", "the chain's JSON-RPC URL")
	passwordFile := flag.String("keystore-password-file", "", "the file holding the keystores' password")
	controller := flag.String("controller-address", "", "the Controller the node is given, to find the round clock")
	scoresAddr := flag.String("scores-address", "", "Scores, the validator set")
	scriptFile := flag.String("script", "", "the script")
	namesFile := flag.String("names", "", "a JSON file of name to address")
	flag.Var(keys, "keystore", "NAME=keystore-file for each validator (repeat)")
	every := flag.Duration("poll", 2*time.Second, "how often to ask the clock")
	once := flag.Bool("once", false, "do the lines due now and exit")
	flag.Parse()
	if *rpcURL == "" || *passwordFile == "" || !common.IsHexAddress(*controller) || !common.IsHexAddress(*scoresAddr) ||
		*scriptFile == "" || *namesFile == "" || len(keys) == 0 {
		fail(fmt.Errorf("--rpc-url, --keystore-password-file, --controller-address, --scores-address, --script, --names and --keystore are required"))
	}

	names := map[string]common.Address{}
	raw := map[string]string{}
	blob, err := os.ReadFile(*namesFile)
	if err != nil {
		fail(err)
	}
	if err := json.Unmarshal(blob, &raw); err != nil {
		fail(fmt.Errorf("%s: %w", *namesFile, err))
	}
	for n, a := range raw {
		if !common.IsHexAddress(a) {
			fail(fmt.Errorf("%s: %q is not an address", *namesFile, a))
		}
		names[n] = common.HexToAddress(a)
	}
	keyed := map[string]bool{}
	for n := range keys {
		keyed[n] = true
	}
	f, err := os.Open(*scriptFile)
	if err != nil {
		fail(err)
	}
	lines, err := parseScript(f, names, keyed)
	f.Close()
	if err != nil {
		fail(fmt.Errorf("%s: %w", *scriptFile, err))
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	sessions := map[string]*chain.Session{}
	for n, path := range keys {
		key, err := chain.LoadKey(path, *passwordFile)
		if err != nil {
			fail(err)
		}
		s, err := chain.Open(ctx, *rpcURL, key)
		if err != nil {
			fail(err)
		}
		if s.From != names[n] {
			fail(fmt.Errorf("the keystore for %s is %s, not %s's address %s", n, s.From, n, names[n]))
		}
		sessions[n] = s
	}
	var first *chain.Session
	for _, s := range sessions {
		first = s
		break
	}
	clock, err := first.Resolve(ctx, common.HexToAddress(*controller), "RoundsManager")
	if err != nil {
		fail(err)
	}
	scores := common.HexToAddress(*scoresAddr)
	log.SetFlags(log.Ltime)
	log.Printf("scripted validators %d lines, Scores at %s", len(lines), scores)

	done := make([]bool, len(lines))
	for {
		current, err := first.Uint(ctx, clock, "currentRound")
		if err != nil {
			log.Printf("%v", err)
		} else {
			for _, l := range due(lines, done, current.Uint64()) {
				act(ctx, sessions[l.validator], scores, names, l, current.Uint64())
			}
		}
		if *once {
			return
		}
		select {
		case <-ctx.Done():
			return
		case <-time.After(*every):
		}
	}
}

// act does one line. A refusal or an error is logged; the line is done.
func act(ctx context.Context, s *chain.Session, scores common.Address, names map[string]common.Address, l line, current uint64) {
	method, args, what := "optIn", []any{names[l.validator]}, l.validator+" opts in"
	if !l.optIn {
		method, args = "vote", []any{names[l.validator], names[l.target], l.score}
		what = fmt.Sprintf("%s votes %s on %s", l.validator, l.score, l.target)
	}
	late := ""
	if l.round < current {
		late = fmt.Sprintf(" (late: scripted for round %d)", l.round)
	}
	code, block, err := s.TryAt(ctx, scores, method, args...)
	if err != nil {
		log.Printf("round %d line %d: %s: %v", current, l.number, what, err)
		return
	}
	if code != "" {
		log.Printf("round %d line %d: %s: refused %s at block %d%s", current, l.number, what, code, block, late)
		return
	}
	r, err := s.Send(ctx, scores, method, args...)
	if err != nil {
		log.Printf("round %d line %d: %s: %v", current, l.number, what, err)
		return
	}
	log.Printf("round %d line %d: %s (block %d, tx %s)%s", current, l.number, what, r.BlockNumber, r.TxHash, late)
}

func fail(err error) {
	fmt.Fprintln(os.Stderr, "lp2-validators:", err)
	os.Exit(1)
}
