// lp2-sender sends paid requests to every node that joins the standing
// testnet, so that a node that registers and joins is paid with nobody
// acting. It is the payer: it holds the payer's keystore, the same file
// the payer's signer uses, and the signer runs beside it, listening on
// loopback only (--signer-url must be a loopback address), since the
// signer signs for any node the payer has earmarked and asks no caller who
// it is.
//
// # Joining
//
// A node joins with POST /join on --listen, a JSON body:
//
//	{"node": "0x…", "url": "https://…", "issuedAt": 1795000000, "signature": "0x…"}
//
// signature is the node key's EIP-191 personal signature (as eth_sign,
// cast wallet sign or personal_sign make it) over the UTF-8 bytes
//
//	lp2-join:v1:<chainId>:<registry>:<node>:<url>:<issuedAt>
//
// with chainId in decimal, registry the Registry's address (the Controller's
// BondingManager), node and registry in lowercase 0x hex, url exactly as in
// the body, and issuedAt in decimal Unix seconds. For example, on chain
// 474747:
//
//	lp2-join:v1:474747:0x5fbdb2315678afecb367f032d93f642f64180aa3:0x70997970c51812dc3a010c7d01b50e0d17dc79c8:https://node.example:8935:1795000000
//
// url is the base URL of the node's orchestrator: the sender asks
// <url>/discovery and the runner path discovery gives, under <url>. The
// join is accepted only when, in this order:
//
//  1. the signature recovers node (otherwise 400 BAD_SIGNATURE, which is
//     also the answer to a body that is not a join);
//  2. issuedAt is within 600 s of the sender's clock and newer than the
//     last join accepted for node (400 STALE: a replayed join is stale);
//  3. url is http or https with a host and no user, query or fragment,
//     in printable ASCII, at most 512 bytes, and every address its host
//     resolves to is public as the pinned node's destination policy
//     classifies it (destination.Public: not loopback, private,
//     link-local, shared, documentation or other reserved space) (400
//     URL_NOT_ALLOWED), unless the sender was started with
//     --allow-private-urls, which only the local demo uses;
//  4. the Registry has node registered (403 NOT_REGISTERED);
//  5. node is listed already, or fewer than --max-nodes are (503 FULL,
//     with Retry-After);
//  6. node's discovery at url lists a runner for --app, and the runner
//     answers a request with no payment with a 402 payment challenge,
//     following no redirect (422 UNREACHABLE);
//  7. the challenge's ticket pays node (403 RECIPIENT_MISMATCH).
//
// A refusal's body is {"code": "<one code above>", "detail": "<why>"}.
// 200 means node was added ({"status": "listed"}) or its URL updated
// ({"status": "updated"}). 503 SENDER_UNAVAILABLE means the sender could
// not read the chain or write its list; it says nothing about the join. A
// join grants nothing but paid requests.
//
// # Paying
//
// The list file (--nodes) is the only record: one line a node, "node url
// issuedAt paidRound", replaced atomically on every change, and read only
// at start. A join that is accepted is written to it and that node is
// tried at once. After that, every node on it is sent one paid request
// per round of the chain's round clock: the sender reads the round every
// --poll, and a node whose paidRound is below the current round is due.
// The round is written to the file before the paid request is sent, so a
// restarted sender does not send a node a second request in a round; a
// sender that stops between the write and the send skips that node for
// the round.
//
// One paid request is: the node's /discovery, for its runner; the request
// with no payment, which the node answers 402 with a challenge; a check
// that the challenge's ticket pays the node and is of the current round;
// a top-up when short, minting the payer's own mock USDC (an open-mint
// stand-in) into its wallet, then one approve and one fund of the deposit
// and the node's earmark (rulebook R-21); the challenge to the
// signer, which signs a ticket; and the request again with the ticket.
// Every connection to a node is dialled through the node's destination
// policy, which refuses a name that resolves to any address that is not
// public at the time, and the sender follows no redirect and uses no
// proxy. Each node is paid on its own, and its whole attempt is bounded
// by --node-timeout, so a node that accepts a connection and never answers
// delays no other node. Until the round is written down, a failed attempt
// is tried again after --retry; once the paid request is sent, the node is
// not sent another in that round, whatever the outcome.
//
// The log says "relay" when a node answered the paid request with 200,
// and "redeemed" when, later, the node redeemed the ticket on chain (a
// FeeSettled from the Broker naming this payer). The two are different
// events: the node takes the ticket before its runner answers, so a
// ticket can be redeemed for a request that was never answered.
//
// # What a node operator needs
//
// A URL the sender can reach for the node's orchestrator (on the public
// testnet a public http or https URL, so the orchestrator listens on a
// public interface or behind a proxy), a runner for --app, which is
// lp2-responder on the standing testnet, and test ETH for gas: one
// registration, and one redemption for each paid request, since every
// ticket wins. The node is sent about one paid request a round.
//
// Amounts are micro-USDC.
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"log"
	"math/big"
	"net"
	"net/http"
	"net/netip"
	"net/url"
	"os"
	"os/signal"
	"strings"
	"sync"
	"syscall"
	"time"

	ethereum "github.com/ethereum/go-ethereum"
	"github.com/ethereum/go-ethereum/accounts/abi"
	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/crypto"

	"lp2core/programs/chain"
)

type sender struct {
	s                      *chain.Session
	broker, usdc, registry common.Address
	clock                  common.Address
	signer, app, job       string
	depositLow, depositAdd *big.Int
	earmarkLow, earmarkAdd *big.Int
	mintAdd                *big.Int
	nodes                  *http.Client // to nodes: no redirect, public addresses unless allowed
	signerHTTP             *http.Client
	txMu                   sync.Mutex // one transaction at a time, for the payer's nonce
	signMu                 sync.Mutex
}

func main() {
	rpcURL := flag.String("rpc-url", "", "the chain's JSON-RPC URL")
	keystoreFile := flag.String("keystore-file", "", "the payer's keystore file")
	passwordFile := flag.String("keystore-password-file", "", "the file holding the keystore's password")
	controller := flag.String("controller-address", "", "the Controller the node is given")
	usdc := flag.String("usdc-address", "", "the mock USDC token")
	signerURL := flag.String("signer-url", "", "the payer's signer, on loopback, such as http://127.0.0.1:8937")
	nodesFile := flag.String("nodes", "", "the list file, which the sender alone writes")
	listen := flag.String("listen", "127.0.0.1:8940", "where to answer POST /join")
	app := flag.String("app", "light", "the app a node's runner serves, as its /discovery names it")
	job := flag.String("path", "echo", "the path to request under the runner's app URL")
	poll := flag.Duration("poll", 2*time.Second, "how often to read the round and look for nodes due")
	nodeTimeout := flag.Duration("node-timeout", 20*time.Second, "the most one node's attempt, or a join's checks, may take")
	retry := flag.Duration("retry", 10*time.Second, "how long to wait before trying a node again after a failure before its paid request")
	maxNodes := flag.Int("max-nodes", 100, "the most nodes the list holds")
	allowPrivate := flag.Bool("allow-private-urls", false, "accept node URLs on loopback, private and link-local addresses (the local demo only)")
	depositLow := flag.String("deposit-low", "2000000", "top up the deposit when it is below this")
	depositAdd := flag.String("deposit-add", "10000000", "how much to add to the deposit")
	earmarkLow := flag.String("earmark-low", "1000001", "top up a node's earmark when it is shown less than this")
	earmarkAdd := flag.String("earmark-add", "2000000", "how much to add to a node's earmark")
	mintAdd := flag.String("mint", "100000000", "how much mock USDC to mint to the payer's wallet when it is short")
	flag.Parse()
	if *rpcURL == "" || *keystoreFile == "" || *passwordFile == "" || *signerURL == "" || *nodesFile == "" ||
		!common.IsHexAddress(*controller) || !common.IsHexAddress(*usdc) {
		fail(errors.New("--rpc-url, --keystore-file, --keystore-password-file, --controller-address, --usdc-address, --signer-url and --nodes are required"))
	}
	if err := loopback(*signerURL); err != nil {
		fail(err)
	}
	if *maxNodes < 1 || *nodeTimeout <= 0 || *poll <= 0 || *retry < 0 {
		fail(errors.New("--max-nodes, --node-timeout and --poll must be above 0"))
	}
	var amounts []*big.Int
	for _, v := range []string{*depositLow, *depositAdd, *earmarkLow, *earmarkAdd, *mintAdd} {
		n, ok := new(big.Int).SetString(v, 10)
		if !ok || n.Sign() < 0 {
			fail(fmt.Errorf("%q is not a whole number of micro-USDC", v))
		}
		amounts = append(amounts, n)
	}
	log.SetFlags(log.Ldate | log.Ltime | log.Lmicroseconds | log.LUTC)

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
	chainID, err := s.Client.ChainID(ctx)
	if err != nil {
		fail(err)
	}
	ctl := common.HexToAddress(*controller)
	found := map[string]common.Address{}
	for _, name := range []string{"TicketBroker", "BondingManager", "RoundsManager"} {
		if found[name], err = s.Resolve(ctx, ctl, name); err != nil {
			fail(err)
		}
	}
	snd := &sender{s: s, broker: found["TicketBroker"], registry: found["BondingManager"], clock: found["RoundsManager"],
		usdc: common.HexToAddress(*usdc), signer: strings.TrimRight(*signerURL, "/"), app: *app,
		job: strings.TrimLeft(*job, "/"), depositLow: amounts[0], depositAdd: amounts[1], earmarkLow: amounts[2],
		earmarkAdd: amounts[3], mintAdd: amounts[4], nodes: nodeClient(nodePolicy(*allowPrivate), *nodeTimeout),
		signerHTTP: &http.Client{Timeout: *nodeTimeout}}
	b, err := openBook(*nodesFile, *maxNodes)
	if err != nil {
		fail(err)
	}
	kick := make(chan struct{}, 1)
	b.onAccept = func(common.Address) {
		select {
		case kick <- struct{}{}:
		default:
		}
	}
	j := &joiner{chainID: chainID, registry: snd.registry, allowPrivate: *allowPrivate, book: b, now: time.Now,
		timeout: *nodeTimeout, resolve: func(ctx context.Context, host string) ([]netip.Addr, error) {
			return net.DefaultResolver.LookupNetIP(ctx, "ip", host)
		},
		registered: func(ctx context.Context, n common.Address) (bool, error) {
			return s.Bool(ctx, snd.registry, "isRegistered", n)
		},
		probe: func(ctx context.Context, e entry) (*challenge, error) { return live{snd}.challenge(ctx, e) }}
	mux := http.NewServeMux()
	mux.Handle("/join", j)
	ln, err := net.Listen("tcp", *listen)
	if err != nil {
		fail(err)
	}
	srv := &http.Server{Handler: mux, ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 10 * time.Second,
		WriteTimeout: *nodeTimeout + 10*time.Second}
	go srv.Serve(ln)
	defer srv.Close()
	log.Printf("payer %s, chain %s, Registry %s, Broker %s; list %s holds %d nodes; joins on %s; private URLs allowed: %t",
		s.From, chainID, snd.registry, snd.broker, *nodesFile, len(b.list()), ln.Addr(), *allowPrivate)
	snd.run(ctx, b, kick, *poll, *nodeTimeout, *retry)
}

// loopback refuses a signer URL whose host is not a loopback address.
func loopback(raw string) error {
	u, err := url.Parse(raw)
	if err == nil {
		if ip, perr := netip.ParseAddr(u.Hostname()); perr == nil && ip.IsLoopback() {
			return nil
		}
	}
	return fmt.Errorf("--signer-url %q: the signer must listen on a loopback address, such as http://127.0.0.1:8937", raw)
}

// run is the sender's loop against the chain, the signer and the nodes.
func (snd *sender) run(ctx context.Context, b *book, kick <-chan struct{}, poll, nodeTimeout, retry time.Duration) {
	var from uint64
	if head, err := snd.s.Client.BlockNumber(ctx); err == nil {
		from = head + 1
	}
	payLoop{b: b, st: live{snd}, poll: poll, nodeTimeout: nodeTimeout, retry: retry,
		readRound: func(ctx context.Context) (uint64, error) {
			r, err := snd.s.Uint(ctx, snd.clock, "currentRound")
			if err != nil {
				return 0, err
			}
			return r.Uint64(), nil
		},
		after: func(ctx context.Context) { from = snd.redemptions(ctx, from) },
		spawn: func(f func()) { go f() },
	}.run(ctx, kick)
}

// payLoop is the sender's loop: each poll, and at once after a join, it
// reads the round and starts an attempt for each node due, then calls
// after (which logs the payer's redemptions since it last looked).
type payLoop struct {
	b                        *book
	st                       steps
	readRound                func(context.Context) (uint64, error)
	after                    func(context.Context)
	spawn                    func(func()) // starts an attempt: go f(), or a test's queue
	poll, nodeTimeout, retry time.Duration
}

func (l payLoop) run(ctx context.Context, kick <-chan struct{}) {
	var wg sync.WaitGroup
	defer wg.Wait()
	t := time.NewTicker(l.poll)
	defer t.Stop()
	var round uint64
	var seen time.Time
	for {
		r, err := l.readRound(ctx)
		if err != nil {
			log.Printf("the round: %v", err)
		} else if r != round {
			round, seen = r, time.Now()
			log.Printf("round %d seen: %d nodes listed", round, len(l.b.list()))
		}
		if round > 0 {
			for _, e := range l.b.take(round, time.Now()) {
				wg.Add(1)
				l.spawn(l.job(ctx, &wg, e, round, seen))
			}
		}
		l.after(ctx)
		select {
		case <-ctx.Done():
			log.Printf("stopping")
			return
		case <-t.C:
		case <-kick:
		}
	}
}

// job is one node's attempt for round, which the loop saw begin at seen.
// Both are copied when the job is made, so an attempt that starts after
// the loop has seen a later round still pays for the round it was taken in.
func (l payLoop) job(ctx context.Context, wg *sync.WaitGroup, e entry, round uint64, seen time.Time) func() {
	return func() {
		defer wg.Done()
		actx, cancel := context.WithTimeout(ctx, l.nodeTimeout)
		defer cancel()
		attempt(actx, l.st, l.b, e, round, seen, l.retry)
	}
}

var feeSettled = crypto.Keccak256Hash([]byte("FeeSettled(uint256,address,address,bytes32,uint256,uint256,uint256,address)"))

// redemptions logs each ticket of this payer that a node redeemed in the
// blocks from `from` to the head, and returns the next block to read.
func (snd *sender) redemptions(ctx context.Context, from uint64) uint64 {
	head, err := snd.s.Client.BlockNumber(ctx)
	if err != nil || head < from {
		return from
	}
	logs, err := snd.s.Client.FilterLogs(ctx, ethereum.FilterQuery{
		FromBlock: new(big.Int).SetUint64(from), ToBlock: new(big.Int).SetUint64(head), Addresses: []common.Address{snd.broker},
		Topics: [][]common.Hash{{feeSettled}, nil, nil, {common.BytesToHash(snd.s.From.Bytes())}}})
	if err != nil {
		log.Printf("redemptions: %v", err)
		return from
	}
	for _, l := range logs {
		if len(l.Topics) != 4 || len(l.Data) != 5*32 {
			continue
		}
		word := func(i int) *big.Int { return new(big.Int).SetBytes(l.Data[32*i : 32*(i+1)]) }
		log.Printf("redeemed: node %s redeemed a ticket, settled in round %d: gross %s, node part %s, network part %s (block %d, tx %s)",
			common.BytesToAddress(l.Topics[2].Bytes()), new(big.Int).SetBytes(l.Topics[1].Bytes()), word(1), word(2), word(3),
			l.BlockNumber, l.TxHash)
	}
	return head + 1
}

// topUp funds the deposit and the node's earmark when either is short,
// first minting the payer's wallet's shortfall of mock USDC.
func (snd *sender) topUp(ctx context.Context, n common.Address) error {
	snd.txMu.Lock()
	defer snd.txMu.Unlock()
	info, err := snd.s.Call(ctx, snd.broker, "getSenderInfo", snd.s.From)
	if err != nil {
		return err
	}
	var held struct{ Deposit, WithdrawRound *big.Int }
	abi.ConvertType(info[0], &held)
	if held.Deposit == nil {
		return errors.New("getSenderInfo: no deposit in its answer")
	}
	shown, err := snd.s.Uint(ctx, snd.broker, "claimableReserve", snd.s.From, n)
	if err != nil {
		return err
	}
	deposit, earmark := new(big.Int), new(big.Int)
	if held.Deposit.Cmp(snd.depositLow) < 0 {
		deposit.Set(snd.depositAdd)
	}
	if shown.Cmp(snd.earmarkLow) < 0 {
		earmark.Set(snd.earmarkAdd)
	}
	total := new(big.Int).Add(deposit, earmark)
	if total.Sign() == 0 {
		return nil
	}
	wallet, err := snd.s.Uint(ctx, snd.usdc, "balanceOf", snd.s.From)
	if err != nil {
		return err
	}
	if wallet.Cmp(total) < 0 {
		mint := new(big.Int).Sub(total, wallet)
		if mint.Cmp(snd.mintAdd) < 0 {
			mint.Set(snd.mintAdd)
		}
		r, err := snd.s.Send(ctx, snd.usdc, "mint", snd.s.From, mint)
		if err != nil {
			return err
		}
		log.Printf("minted: %s mock USDC to the payer's wallet, which held %s (block %d, tx %s)", mint, wallet, r.BlockNumber, r.TxHash)
	}
	if _, err := snd.s.Send(ctx, snd.usdc, "approve", snd.broker, total); err != nil {
		return err
	}
	r, err := snd.s.Send(ctx, snd.broker, "fund", deposit, n, earmark)
	if err != nil {
		return err
	}
	log.Printf("funded: deposit %s +%s, earmark for %s %s +%s (block %d, tx %s)",
		held.Deposit, deposit, n, shown, earmark, r.BlockNumber, r.TxHash)
	return nil
}

func fail(err error) {
	fmt.Fprintln(os.Stderr, "lp2-sender:", err)
	os.Exit(1)
}
