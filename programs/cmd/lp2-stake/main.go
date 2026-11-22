// lp2-stake sends one stage 3 action as the holder or node whose keystore
// it is given: bonds and the moves after them (rulebook R-15), a node's
// reward cut and its claim (R-24, R-12), a holder's withdrawal of its parts
// (R-24), a round's validator and treasury mint (R-10), and a node's payout
// and operator key (R-01, R-26).
//
//	lp2-stake [flags] bond NODE AMOUNT_WEI
//	lp2-stake [flags] unbond NODE AMOUNT_WEI
//	lp2-stake [flags] transfer FROM TO AMOUNT_WEI
//	lp2-stake [flags] unbond-transfer FROM TO AMOUNT_WEI
//	lp2-stake [flags] rebond NODE AMOUNT_WEI
//	lp2-stake [flags] withdraw-unbonded
//	lp2-stake [flags] set-cut NODE CUT_MILLIONTHS
//	lp2-stake [flags] claim NODE ROUND
//	lp2-stake [flags] withdraw-parts NODE ROUND[,ROUND...]
//	lp2-stake [flags] withdraw-validator-parts NODE ROUND[,ROUND...]
//	lp2-stake [flags] mint-shares ROUND
//	lp2-stake [flags] set-payout NODE PAYOUT
//	lp2-stake [flags] set-operator NODE NEXT
//	lp2-stake [flags] accept-operator NODE
//
// set-cut, claim, set-payout and set-operator are the node's operator key's:
// the node's own key until another key accepts. set-operator proposes NEXT,
// replacing any proposal, and NEXT takes over only by its own
// accept-operator; proposing the current key clears the proposal. A
// rotation moves no bond and leaves the payout where it was.
//
// It asks the contract by a call first, at the newest block, and sends the
// transaction only when the call would succeed. A refusal is printed as
// "refused: <CODE> at block <n>" with exit 3, and nothing is sent: the
// contract's answer holds after every transaction of that block. A bond
// approves StakeLedger for exactly the amount first, and takes the
// approval back if the bond is refused. Amounts are wei of test LPT.
package main

import (
	"context"
	"flag"
	"fmt"
	"math/big"
	"os"
	"strings"
	"time"

	"github.com/ethereum/go-ethereum/common"

	"lp2core/programs/chain"
)

// action is one parsed command: the contract it goes to and the call.
type action struct {
	name   string
	target string // "ledger", "claims" or "registry"
	method string
	args   []any
	bond   *big.Int // a bond's amount, approved first
}

// parse turns the command line after the flags into one action.
func parse(words []string) (action, error) {
	if len(words) == 0 {
		return action{}, fmt.Errorf("no action given")
	}
	name, rest := words[0], words[1:]
	shapes := map[string]struct {
		target, method string
		kinds          string // a: address, n: whole number, l: list of rounds
	}{
		"bond":                     {"ledger", "bond", "an"},
		"unbond":                   {"ledger", "unbond", "an"},
		"transfer":                 {"ledger", "transferBond", "aan"},
		"unbond-transfer":          {"ledger", "unbondTransfer", "aan"},
		"rebond":                   {"ledger", "rebond", "an"},
		"withdraw-unbonded":        {"ledger", "withdrawUnbonded", ""},
		"set-cut":                  {"ledger", "setRewardCut", "an"},
		"claim":                    {"claims", "claim", "an"},
		"withdraw-parts":           {"claims", "withdrawParts", "al"},
		"withdraw-validator-parts": {"claims", "withdrawValidatorParts", "al"},
		"mint-shares":              {"claims", "mintRoundShares", "n"},
		"set-payout":               {"registry", "setPayout", "aa"},
		"set-operator":             {"registry", "setOperator", "aa"},
		"accept-operator":          {"registry", "acceptOperator", "a"},
	}
	shape, ok := shapes[name]
	if !ok {
		return action{}, fmt.Errorf("unknown action %q", name)
	}
	if len(rest) != len(shape.kinds) {
		return action{}, fmt.Errorf("%s takes %d arguments, got %d", name, len(shape.kinds), len(rest))
	}
	a := action{name: name, target: shape.target, method: shape.method}
	for i, k := range shape.kinds {
		w := rest[i]
		switch k {
		case 'a':
			if !common.IsHexAddress(w) {
				return action{}, fmt.Errorf("%s: %q is not an address", name, w)
			}
			a.args = append(a.args, common.HexToAddress(w))
		case 'n':
			n, err := whole(w)
			if err != nil {
				return action{}, fmt.Errorf("%s: %w", name, err)
			}
			a.args = append(a.args, n)
		case 'l':
			var rounds []*big.Int
			for _, part := range strings.Split(w, ",") {
				n, err := whole(part)
				if err != nil {
					return action{}, fmt.Errorf("%s: %w", name, err)
				}
				rounds = append(rounds, n)
			}
			a.args = append(a.args, rounds)
		}
	}
	if name == "bond" {
		a.bond = a.args[1].(*big.Int)
	}
	return a, nil
}

func whole(w string) (*big.Int, error) {
	n, ok := new(big.Int).SetString(w, 10)
	if !ok || n.Sign() < 0 {
		return nil, fmt.Errorf("%q is not a whole number", w)
	}
	return n, nil
}

func main() {
	rpcURL := flag.String("rpc-url", "", "the chain's JSON-RPC URL")
	keystoreFile := flag.String("keystore-file", "", "the sender's keystore file")
	passwordFile := flag.String("keystore-password-file", "", "the file holding the keystore's password")
	ledger := flag.String("stake-ledger-address", "", "StakeLedger")
	claims := flag.String("claims-address", "", "Claims")
	registry := flag.String("registry-address", "", "Registry, for a payout or an operator key")
	lpt := flag.String("lpt-address", "", "test LPT, for a bond's approval")
	flag.Parse()

	a, err := parse(flag.Args())
	if err != nil {
		fail(err)
	}
	if *rpcURL == "" || *keystoreFile == "" || *passwordFile == "" {
		fail(fmt.Errorf("--rpc-url, --keystore-file and --keystore-password-file are required"))
	}
	addr := map[string]string{"ledger": *ledger, "claims": *claims, "registry": *registry}
	if !common.IsHexAddress(addr[a.target]) {
		flags := map[string]string{"ledger": "stake-ledger-address", "claims": "claims-address", "registry": "registry-address"}
		fail(fmt.Errorf("%s needs --%s", a.name, flags[a.target]))
	}
	if a.bond != nil && !common.IsHexAddress(*lpt) {
		fail(fmt.Errorf("bond needs --lpt-address"))
	}
	refused, err := run(a, *rpcURL, *keystoreFile, *passwordFile, common.HexToAddress(addr[a.target]), common.HexToAddress(*lpt))
	if err != nil {
		fail(err)
	}
	if refused {
		os.Exit(3)
	}
}

func run(a action, rpcURL, keystoreFile, passwordFile string, target, lpt common.Address) (refused bool, err error) {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Minute)
	defer cancel()
	key, err := chain.LoadKey(keystoreFile, passwordFile)
	if err != nil {
		return false, err
	}
	s, err := chain.Open(ctx, rpcURL, key)
	if err != nil {
		return false, err
	}
	if a.bond != nil {
		r, err := s.Send(ctx, lpt, "approve", target, a.bond)
		if err != nil {
			return false, err
		}
		fmt.Printf("approved: StakeLedger may take %s (block %d, tx %s)\n", a.bond, r.BlockNumber, r.TxHash)
	}
	code, block, err := s.TryAt(ctx, target, a.method, a.args...)
	if err != nil {
		return false, err
	}
	if code != "" {
		fmt.Printf("refused: %s at block %d (%s by %s)\n", code, block, a.name, s.From)
		if a.bond != nil {
			if _, err := s.Send(ctx, lpt, "approve", target, new(big.Int)); err != nil {
				return true, err
			}
		}
		return true, nil
	}
	r, err := s.Send(ctx, target, a.method, a.args...)
	if err != nil {
		return false, err
	}
	fmt.Printf("sent: %s by %s (block %d, tx %s)\n", a.name, s.From, r.BlockNumber, r.TxHash)
	return false, nil
}

func fail(err error) {
	fmt.Fprintln(os.Stderr, "lp2-stake:", err)
	os.Exit(1)
}
