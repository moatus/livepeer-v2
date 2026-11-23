// lp2-params announces, withdraws and shows a change to one group of
// Parameters (rulebook R-25), as the authority fixed in Parameters at
// deployment: the governance stand-in.
//
//	lp2-params [flags] announce GROUP VALUE...
//	lp2-params [flags] withdraw GROUP
//	lp2-params [flags] show GROUP [ROUND]
//
// GROUP is a group's name or its number: fee (1), emission (2), schedule
// (3), reward-delay (4), set-size (5), score-source (6), unbonding (7),
// buyback-cuts (8), buyback-values (9) and buyback-executor (10). A number
// with no group, such as 0, reaches the contract and is refused
// NOT_ADJUSTABLE. Each VALUE is a whole number or, for an address (the
// score source, a buyback pool, the buyback executor), an address. A schedule change is typed as (drop, half-life); Parameters
// records it as the triple (position, half-life, start) that counts.
//
// announce and withdraw ask the contract by a call first, at the newest
// block, and send the transaction only when the call would succeed. A
// refusal is printed as "refused: <CODE> at block <n>" with exit 3, and
// nothing is sent. An announcement prints the round it counts from (the
// current round + 7) as the transaction's own Announced event records it,
// and a withdrawal the group and round from its Withdrawn event, with no
// values, since the event carries none. show prints the
// values in force for ROUND (the round, read from --round-clock-address,
// when none is given) and any pending change; it signs nothing, so it
// needs no keystore.
package main

import (
	"context"
	"crypto/ecdsa"
	"flag"
	"fmt"
	"math/big"
	"os"
	"strings"
	"time"

	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/core/types"
	"github.com/ethereum/go-ethereum/crypto"

	"lp2core/programs/chain"
)

// groups are R-25's group names by number.
var groups = []string{1: "fee", 2: "emission", 3: "schedule", 4: "reward-delay", 5: "set-size",
	6: "score-source", 7: "unbonding", 8: "buyback-cuts", 9: "buyback-values", 10: "buyback-executor"}

// command is one parsed command line.
type command struct {
	verb   string // announce, withdraw or show
	group  *big.Int
	values []*big.Int
	round  *big.Int // show's round, nil for the current one
}

// parse turns the command line after the flags into one command.
func parse(words []string) (command, error) {
	if len(words) < 2 {
		return command{}, fmt.Errorf("give an action and a group")
	}
	c := command{verb: words[0]}
	g, err := group(words[1])
	if err != nil {
		return command{}, err
	}
	c.group = g
	rest := words[2:]
	switch c.verb {
	case "announce":
		if len(rest) == 0 {
			return command{}, fmt.Errorf("announce %s: no values given", words[1])
		}
		for _, w := range rest {
			v, err := value(w)
			if err != nil {
				return command{}, fmt.Errorf("announce %s: %w", words[1], err)
			}
			c.values = append(c.values, v)
		}
	case "withdraw":
		if len(rest) != 0 {
			return command{}, fmt.Errorf("withdraw takes a group only")
		}
	case "show":
		if len(rest) > 1 {
			return command{}, fmt.Errorf("show takes a group and at most one round")
		}
		if len(rest) == 1 {
			r, err := whole(rest[0])
			if err != nil {
				return command{}, fmt.Errorf("show: %w", err)
			}
			c.round = r
		}
	default:
		return command{}, fmt.Errorf("unknown action %q", c.verb)
	}
	return c, nil
}

// group is a group's number from its name or its digits.
func group(w string) (*big.Int, error) {
	for i, n := range groups {
		if n != "" && n == w {
			return big.NewInt(int64(i)), nil
		}
	}
	if n, err := whole(w); err == nil {
		return n, nil
	}
	return nil, fmt.Errorf("unknown group %q (%s, or a number)", w, strings.Join(groups[1:], ", "))
}

// value is a whole number, or an address taken as the number it is.
func value(w string) (*big.Int, error) {
	if strings.HasPrefix(w, "0x") || strings.HasPrefix(w, "0X") {
		if !common.IsHexAddress(w) {
			return nil, fmt.Errorf("%q is not an address", w)
		}
		return new(big.Int).SetBytes(common.HexToAddress(w).Bytes()), nil
	}
	return whole(w)
}

func whole(w string) (*big.Int, error) {
	n, ok := new(big.Int).SetString(w, 10)
	if !ok || n.Sign() < 0 {
		return nil, fmt.Errorf("%q is not a whole number", w)
	}
	return n, nil
}

// name is the group's name, or its number when it has none.
func name(g *big.Int) string {
	if g.IsInt64() && g.Int64() > 0 && g.Int64() < int64(len(groups)) {
		return groups[g.Int64()]
	}
	return g.String()
}

// shown lays values out as section 1 does, 900000/10000/90000, with an
// address where the group holds one.
func shown(g *big.Int, values []*big.Int) string {
	parts := make([]string, len(values))
	for i, v := range values {
		parts[i] = v.String()
		address := name(g) == "score-source" || name(g) == "buyback-executor" || (name(g) == "buyback-values" && i == 0)
		if address && v.BitLen() <= 160 {
			parts[i] = common.BigToAddress(v).Hex()
		}
	}
	return strings.Join(parts, "/")
}

func main() {
	rpcURL := flag.String("rpc-url", "", "the chain's JSON-RPC URL")
	keystoreFile := flag.String("keystore-file", "", "the authority's keystore file (announce, withdraw)")
	passwordFile := flag.String("keystore-password-file", "", "the file holding the keystore's password")
	params := flag.String("parameters-address", "", "Parameters")
	clock := flag.String("round-clock-address", "", "RoundClock, for show without a round")
	flag.Parse()

	c, err := parse(flag.Args())
	if err != nil {
		fail(err)
	}
	if *rpcURL == "" || !common.IsHexAddress(*params) {
		fail(fmt.Errorf("--rpc-url and --parameters-address are required"))
	}
	if c.verb != "show" && (*keystoreFile == "" || *passwordFile == "") {
		fail(fmt.Errorf("%s needs --keystore-file and --keystore-password-file", c.verb))
	}
	if c.verb == "show" && c.round == nil && !common.IsHexAddress(*clock) {
		fail(fmt.Errorf("show without a round needs --round-clock-address"))
	}
	refused, err := run(c, *rpcURL, *keystoreFile, *passwordFile, common.HexToAddress(*params), common.HexToAddress(*clock))
	if err != nil {
		fail(err)
	}
	if refused {
		os.Exit(3)
	}
}

func run(c command, rpcURL, keystoreFile, passwordFile string, at, clock common.Address) (refused bool, err error) {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Minute)
	defer cancel()
	var key *ecdsa.PrivateKey
	if c.verb == "show" {
		// show only reads; a key made for the run names the caller.
		key, err = crypto.GenerateKey()
	} else {
		key, err = chain.LoadKey(keystoreFile, passwordFile)
	}
	if err != nil {
		return false, err
	}
	s, err := chain.Open(ctx, rpcURL, key)
	if err != nil {
		return false, err
	}
	if c.verb == "show" {
		return show(ctx, s, c, at, clock)
	}
	args := []any{c.group}
	if c.verb == "announce" {
		args = append(args, c.values)
	}
	code, block, err := s.TryAt(ctx, at, c.verb, args...)
	if err != nil {
		return false, err
	}
	if code != "" {
		fmt.Printf("refused: %s at block %d (%s %s by %s)\n", code, block, c.verb, name(c.group), s.From)
		return true, nil
	}
	if c.verb == "withdraw" {
		r, err := s.Send(ctx, at, "withdraw", c.group)
		if err != nil {
			return false, err
		}
		line, err := withdrawn(c, at, r)
		if err != nil {
			return false, err
		}
		fmt.Println(line)
		return false, nil
	}
	r, err := s.Send(ctx, at, "announce", args...)
	if err != nil {
		return false, err
	}
	line, err := announced(c, at, r)
	if err != nil {
		return false, err
	}
	fmt.Println(line)
	return false, nil
}

// announced is the line an announcement prints, from the Announced event
// in the transaction's own receipt: the round it counts from and, for the
// schedule, the triple Parameters recorded.
func announced(c command, at common.Address, r *types.Receipt) (string, error) {
	ev, err := chain.Logged(r, at, "Announced", common.BigToHash(c.group))
	if err != nil {
		return "", err
	}
	recorded, from := ev[0].([]*big.Int), ev[1].(*big.Int)
	note := ""
	if name(c.group) == "schedule" {
		note = fmt.Sprintf(" (recorded as position/half-life/start %s)", shown(c.group, recorded))
	}
	return fmt.Sprintf("announced: %s %s, counts from round %s%s (block %d, tx %s)",
		name(c.group), shown(c.group, c.values), from, note, r.BlockNumber, r.TxHash), nil
}

// withdrawn is the line a withdrawal prints, from the Withdrawn event in the
// transaction's own receipt: the group and the round the change would have
// counted from. The event carries no values, and a read before sending can
// name another change (the authority may replace its pending change for the
// same round in between), so the line names none.
func withdrawn(c command, at common.Address, r *types.Receipt) (string, error) {
	ev, err := chain.Logged(r, at, "Withdrawn", common.BigToHash(c.group))
	if err != nil {
		return "", err
	}
	from := ev[0].(*big.Int)
	return fmt.Sprintf("withdrawn: %s, the change that would have counted from round %s; it never counts (block %d, tx %s)",
		name(c.group), from, r.BlockNumber, r.TxHash), nil
}

// show prints the values in force for the round and any pending change.
func show(ctx context.Context, s *chain.Session, c command, at, clock common.Address) (bool, error) {
	round := c.round
	if round == nil {
		var err error
		if round, err = s.Uint(ctx, clock, "currentRound"); err != nil {
			return false, err
		}
	}
	code, block, err := s.TryAt(ctx, at, "valuesAt", c.group, round)
	if err != nil {
		return false, err
	}
	if code != "" {
		fmt.Printf("refused: %s at block %d (show %s)\n", code, block, name(c.group))
		return true, nil
	}
	out, err := s.Call(ctx, at, "valuesAt", c.group, round)
	if err != nil {
		return false, err
	}
	fmt.Printf("%s in round %s: %s\n", name(c.group), round, shown(c.group, out[0].([]*big.Int)))
	p, err := s.Call(ctx, at, "pending", c.group)
	if err != nil {
		return false, err
	}
	if from := p[1].(*big.Int); from.Sign() != 0 {
		fmt.Printf("pending: %s, counts from round %s\n", shown(c.group, p[0].([]*big.Int)), from)
	} else {
		fmt.Println("pending: none")
	}
	return false, nil
}

func fail(err error) {
	fmt.Fprintln(os.Stderr, "lp2-params:", err)
	os.Exit(1)
}
