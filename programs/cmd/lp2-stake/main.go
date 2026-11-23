// lp2-stake sends one stage 3 action as the holder or node whose keystore
// it is given: bonds and the moves after them (rulebook R-15), a node's
// reward cut and its claim (R-24, R-12), a holder's withdrawal of its parts
// (R-24), a round's validator and treasury mint (R-10), and a node's payout
// and operator key (R-01, R-26). It also has what a node operator's
// container needs: a key, a registration, a move of test LPT, a reading of
// where the node stands, the signed join of lp2-sender, and the claims and
// withdrawals that are due.
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
//	lp2-stake [flags] register
//	lp2-stake [flags] send-lpt TO AMOUNT_WEI
//	lp2-stake [flags] approve AMOUNT_WEI
//	lp2-stake [flags] claim-due NODE
//	lp2-stake [flags] withdraw-due NODE
//	lp2-stake [flags] join SENDER_URL NODE_URL
//	lp2-stake [flags] state NODE HOLDER
//	lp2-stake [flags] new-key
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
// contract's answer holds after every transaction of that block. Before it
// asks, it waits until every transaction its key has already sent is
// mined, so that a transaction left by a run that was killed is part of
// the answer and is never sent a second time. Amounts are wei of test LPT.
//
// register registers the key's own address (R-01) and, when the Registry
// already has it, sends nothing and prints "done already". send-lpt
// transfers test LPT from the key to TO. approve lets StakeLedger take
// AMOUNT_WEI of the key's test LPT and sends nothing when it already may.
// A bond approves StakeLedger for the amount first, unless it already may
// take that much, and takes back an approval it made if the bond is
// refused.
//
// claim-due claims, as NODE's operator key, every round whose stake and
// fees for NODE are both above zero, that is in its claim window under the
// reward delay in force for that round (Parameters' group 4, R-12, R-25)
// and that is not yet claimed; it skips the rounds not yet claimable and
// the expired ones. withdraw-due withdraws, as a holder at NODE, its parts
// of NODE's claimed rounds not yet withdrawn, in one withdrawParts. Each
// reads the rounds from the one in --cursor-file, however old, or without
// one from the current round less 861 (the most a round can stay
// claimable: a reward delay of at most 840 and a window of 21), to the
// current round; withdraw-due writes to --cursor-file the first round that
// may still need a claim or a withdrawal. Rounds before it are settled for
// good. A claimed part never expires (R-24), so a cursor is followed back
// past any claim window.
//
// join signs, with the key, the join lp2-sender's documentation specifies
// for the key's address and NODE_URL, and posts it to SENDER_URL/join. It
// prints the sender's answer and exits 0 when the node is listed, 3 on a
// refusal and 1 when the sender could not be asked or could not answer.
//
// state prints, as name=value lines, where NODE and HOLDER stand once every
// transaction either has sent is mined: the chain id, the round, whether
// NODE is registered, its operator key and proposed key, each one's test
// LPT and ETH, HOLDER's bond staying at NODE and its bond counting for
// NODE this round. It needs no keystore.
//
// An error is printed as "lp2-stake <action>: <error>", with the
// --rpc-url's user information, path and query, where a provider puts its
// key, replaced by "…".
//
// new-key makes a new key and writes it to --keystore-file, encrypted with
// the password in --keystore-password-file, and prints its address. It
// refuses to replace a file that exists.
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"math/big"
	"net/url"
	"os"
	"strings"
	"time"

	"github.com/ethereum/go-ethereum/common"

	"lp2core/programs/chain"
)

// action is one parsed command: the contract it goes to and the call.
type action struct {
	name   string
	target string // "ledger", "claims", "registry", "lpt", or "" for the actions below that are not one call
	method string
	args   []any
	bond   *big.Int // a bond's amount, approved first
	words  []string // the arguments as given, for the actions that are not one call
}

// special actions are not one call to one contract.
var special = map[string]string{
	"claim-due": "a", "withdraw-due": "a", "join": "uu", "state": "aa", "new-key": "",
}

// parse turns the command line after the flags into one action.
func parse(words []string) (action, error) {
	if len(words) == 0 {
		return action{}, fmt.Errorf("no action given")
	}
	name, rest := words[0], words[1:]
	shapes := map[string]struct {
		target, method string
		kinds          string // a: address, n: whole number, l: list of rounds, u: URL
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
		"register":                 {"registry", "register", ""},
		"send-lpt":                 {"lpt", "transfer", "an"},
		"approve":                  {"lpt", "approve", "n"},
	}
	for n, kinds := range special {
		shapes[n] = struct{ target, method, kinds string }{"", "", kinds}
	}
	shape, ok := shapes[name]
	if !ok {
		return action{}, fmt.Errorf("unknown action %q", name)
	}
	if len(rest) != len(shape.kinds) {
		return action{}, fmt.Errorf("%s takes %d arguments, got %d", name, len(shape.kinds), len(rest))
	}
	a := action{name: name, target: shape.target, method: shape.method, words: rest}
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
		case 'u':
			if !strings.HasPrefix(w, "http://") && !strings.HasPrefix(w, "https://") {
				return action{}, fmt.Errorf("%s: %q is not an http or https URL", name, w)
			}
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

// config is the flags: where the chain and the contracts are.
type config struct {
	rpcURL, keystoreFile, passwordFile, cursorFile string
	ledger, claims, registry, lpt, params          string
	controller                                     string
}

func main() {
	var c config
	flag.StringVar(&c.rpcURL, "rpc-url", "", "the chain's JSON-RPC URL")
	flag.StringVar(&c.keystoreFile, "keystore-file", "", "the sender's keystore file")
	flag.StringVar(&c.passwordFile, "keystore-password-file", "", "the file holding the keystore's password")
	flag.StringVar(&c.ledger, "stake-ledger-address", "", "StakeLedger")
	flag.StringVar(&c.claims, "claims-address", "", "Claims")
	flag.StringVar(&c.registry, "registry-address", "", "Registry, for a payout, an operator key, a registration or a join; found through --controller-address when not given")
	flag.StringVar(&c.lpt, "lpt-address", "", "test LPT, for a bond's approval or a move of LPT")
	flag.StringVar(&c.params, "parameters-address", "", "Parameters, for the reward delay in force for each round (claim-due, withdraw-due)")
	flag.StringVar(&c.controller, "controller-address", "", "the Controller, which names the Registry, the round clock and the Broker")
	flag.StringVar(&c.cursorFile, "cursor-file", "", "claim-due and withdraw-due: the file that keeps the first round not settled for good")
	flag.Parse()

	a, err := parse(flag.Args())
	if err != nil {
		fail("", err, "")
	}
	if a.name == "new-key" {
		addr, err := newKey(c.keystoreFile, c.passwordFile)
		if err != nil {
			fail(a.name, err, "")
		}
		fmt.Printf("address: %s\n", addr.Hex())
		return
	}
	if c.rpcURL == "" || (a.name != "state" && (c.keystoreFile == "" || c.passwordFile == "")) {
		fail(a.name, fmt.Errorf("--rpc-url, --keystore-file and --keystore-password-file are required"), "")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Minute)
	defer cancel()
	refused, err := dispatch(ctx, a, c)
	if err != nil {
		fail(a.name, err, c.rpcURL)
	}
	if refused {
		os.Exit(3)
	}
}

// dispatch opens the session the action needs and runs it.
func dispatch(ctx context.Context, a action, c config) (refused bool, err error) {
	var s *chain.Session
	if a.name == "state" {
		s, err = chain.Dial(ctx, c.rpcURL)
	} else {
		key, kerr := chain.LoadKey(c.keystoreFile, c.passwordFile)
		if kerr != nil {
			return false, kerr
		}
		s, err = chain.Open(ctx, c.rpcURL, key)
	}
	if err != nil {
		return false, err
	}
	addrs, err := resolve(ctx, s, a, c)
	if err != nil {
		return false, err
	}
	switch a.name {
	case "state":
		return false, state(ctx, s, addrs, a.args[0].(common.Address), a.args[1].(common.Address))
	case "join":
		return join(ctx, s, addrs["registry"], a.words[0], a.words[1])
	case "claim-due", "withdraw-due":
		return false, due(ctx, s, addrs, a.args[0].(common.Address), a.name == "claim-due", c.cursorFile)
	}
	if err := settle(ctx, s); err != nil {
		return false, err
	}
	return run(ctx, s, a, addrs)
}

// needs is the contracts each action reads or sends to, beyond its target.
var needs = map[string][]string{
	"bond":         {"lpt"},
	"approve":      {"ledger"},
	"join":         {"registry"},
	"state":        {"registry", "clock", "lpt", "ledger"},
	"claim-due":    {"clock", "broker", "ledger", "claims", "params"},
	"withdraw-due": {"clock", "broker", "ledger", "claims", "params"},
}

// resolve gives every contract the action needs its address: from its
// flag, or for the Registry, the round clock and the Broker from the
// Controller, by the names the node uses.
func resolve(ctx context.Context, s *chain.Session, a action, c config) (map[string]common.Address, error) {
	given := map[string]string{"ledger": c.ledger, "claims": c.claims, "registry": c.registry, "lpt": c.lpt, "params": c.params}
	flags := map[string]string{"ledger": "stake-ledger-address", "claims": "claims-address", "registry": "registry-address",
		"lpt": "lpt-address", "params": "parameters-address", "clock": "controller-address", "broker": "controller-address"}
	byController := map[string]string{"registry": "BondingManager", "clock": "RoundsManager", "broker": "TicketBroker"}
	want := append([]string{}, needs[a.name]...)
	if a.target != "" {
		want = append(want, a.target)
	}
	out := map[string]common.Address{}
	for _, k := range want {
		if v := given[k]; common.IsHexAddress(v) {
			out[k] = common.HexToAddress(v)
			continue
		}
		name, ok := byController[k]
		if !ok || !common.IsHexAddress(c.controller) {
			return nil, fmt.Errorf("%s needs --%s", a.name, flags[k])
		}
		at, err := s.Resolve(ctx, common.HexToAddress(c.controller), name)
		if err != nil {
			return nil, err
		}
		out[k] = at
	}
	return out, nil
}

// settle waits until every transaction the key has sent is mined: the
// chain's pending nonce for it is its mined one. A transaction a killed
// run broadcast is then part of what the chain answers.
func settle(ctx context.Context, s *chain.Session) error {
	return settled(ctx, s, s.From)
}

// settled waits until every transaction each address has sent is mined.
func settled(ctx context.Context, s *chain.Session, who ...common.Address) error {
	said := map[common.Address]bool{}
	for _, a := range who {
		for {
			mined, err := s.Client.NonceAt(ctx, a, nil)
			if err != nil {
				return err
			}
			pending, err := s.Client.PendingNonceAt(ctx, a)
			if err != nil {
				return err
			}
			if pending <= mined {
				break
			}
			if !said[a] {
				fmt.Fprintf(os.Stderr, "waiting: %d transactions of %s sent and not yet mined\n", pending-mined, a)
				said[a] = true
			}
			select {
			case <-ctx.Done():
				return fmt.Errorf("transactions of %s sent and not mined: %w", a, ctx.Err())
			case <-time.After(2 * time.Second):
			}
		}
	}
	return nil
}

// run sends one call: asked first, then sent when it would succeed.
func run(ctx context.Context, s *chain.Session, a action, addrs map[string]common.Address) (refused bool, err error) {
	target := addrs[a.target]
	args := a.args
	switch a.name {
	case "register":
		done, err := s.Bool(ctx, target, "isRegistered", s.From)
		if err != nil {
			return false, err
		}
		if done {
			fmt.Printf("done already: %s is registered\n", s.From)
			return false, nil
		}
	case "approve":
		args = []any{addrs["ledger"], a.args[0]}
		may, err := s.Uint(ctx, target, "allowance", s.From, addrs["ledger"])
		if err != nil {
			return false, err
		}
		if may.Cmp(a.args[0].(*big.Int)) == 0 {
			fmt.Printf("done already: StakeLedger may take %s of %s's test LPT\n", may, s.From)
			return false, nil
		}
	}
	approved := false
	if a.bond != nil {
		may, err := s.Uint(ctx, addrs["lpt"], "allowance", s.From, target)
		if err != nil {
			return false, err
		}
		if may.Cmp(a.bond) < 0 {
			r, err := s.Send(ctx, addrs["lpt"], "approve", target, a.bond)
			if err != nil {
				return false, err
			}
			approved = true
			fmt.Printf("approved: StakeLedger may take %s (block %d, tx %s)\n", a.bond, r.BlockNumber, r.TxHash)
		}
	}
	code, block, err := s.TryAt(ctx, target, a.method, args...)
	if err != nil {
		return false, err
	}
	if code != "" {
		fmt.Printf("refused: %s at block %d (%s by %s)\n", code, block, a.name, s.From)
		if approved {
			if _, err := s.Send(ctx, addrs["lpt"], "approve", target, new(big.Int)); err != nil {
				return true, err
			}
		}
		return true, nil
	}
	r, err := s.Send(ctx, target, a.method, args...)
	if err != nil {
		return false, err
	}
	fmt.Printf("sent: %s by %s (block %d, tx %s)\n", a.name, s.From, r.BlockNumber, r.TxHash)
	return false, nil
}

// state prints where node and holder stand, one name=value a line.
func state(ctx context.Context, s *chain.Session, addrs map[string]common.Address, node, holder common.Address) error {
	if err := settled(ctx, s, node, holder); err != nil {
		return err
	}
	head, err := s.Client.HeaderByNumber(ctx, nil)
	if err != nil {
		return err
	}
	at := head.Number
	id, err := s.Client.ChainID(ctx)
	if err != nil {
		return err
	}
	var errs []error
	call := func(to common.Address, method string, args ...any) []any {
		out, err := s.CallAt(ctx, at, to, method, args...)
		if err != nil {
			errs = append(errs, fmt.Errorf("%s: %w", method, err))
			return []any{new(big.Int), false, common.Address{}}
		}
		return out
	}
	round := call(addrs["clock"], "currentRound")[0]
	r, _ := round.(*big.Int)
	if r == nil {
		r = new(big.Int)
	}
	eth := func(a common.Address) *big.Int {
		b, err := s.Client.BalanceAt(ctx, a, at)
		if err != nil {
			errs = append(errs, err)
			return new(big.Int)
		}
		return b
	}
	lines := []struct {
		name  string
		value any
	}{
		{"chain_id", id},
		{"round", r},
		{"registered", call(addrs["registry"], "isRegistered", node)[0]},
		{"operator", call(addrs["registry"], "operatorOf", node)[0]},
		{"proposed", call(addrs["registry"], "proposedOperatorOf", node)[0]},
		{"node_lpt", call(addrs["lpt"], "balanceOf", node)[0]},
		{"holder_lpt", call(addrs["lpt"], "balanceOf", holder)[0]},
		{"staying", call(addrs["ledger"], "stayingOf", holder, node)[0]},
		{"bond_now", call(addrs["ledger"], "bondAt", holder, node, r)[0]},
		{"node_eth", eth(node)},
		{"holder_eth", eth(holder)},
	}
	if len(errs) > 0 {
		return errors.Join(errs...)
	}
	for _, l := range lines {
		v := l.value
		if a, ok := v.(common.Address); ok {
			v = strings.ToLower(a.Hex())
		}
		fmt.Printf("%s=%v\n", l.name, v)
	}
	return nil
}

func fail(name string, err error, rpcURL string) {
	fmt.Fprintln(os.Stderr, message(name, err, rpcURL))
	os.Exit(1)
}

// message is the line fail prints: the action and the error, with what
// rpcURL may carry as a credential hidden.
func message(name string, err error, rpcURL string) string {
	if name != "" {
		name = " " + name
	}
	return redact(fmt.Sprintf("lp2-stake%s: %v", name, err), rpcURL)
}

// redact replaces, in text, the user information, path, query and fragment
// of rpcURL, in each form an error may quote them, with "…". The scheme
// and the host stay, to say which endpoint failed.
func redact(text, rpcURL string) string {
	if rpcURL == "" {
		return text
	}
	u, err := url.Parse(rpcURL)
	if err != nil || u.Host == "" {
		return strings.ReplaceAll(text, rpcURL, "…")
	}
	var hidden []string
	for _, piece := range []string{u.User.String(), u.EscapedPath(), u.Path, u.RawQuery, u.Query().Encode(),
		u.Fragment, u.EscapedFragment()} {
		if piece != "" && piece != "/" {
			hidden = append(hidden, piece)
		}
	}
	if pw, ok := u.User.Password(); ok && pw != "" {
		hidden = append(hidden, pw)
	}
	if len(hidden) == 0 {
		return text
	}
	for _, whole := range []string{rpcURL, u.String(), u.Redacted()} {
		text = strings.ReplaceAll(text, whole, u.Scheme+"://"+u.Host+"/…")
	}
	for _, h := range hidden {
		text = strings.ReplaceAll(text, h, "…")
	}
	return text
}
