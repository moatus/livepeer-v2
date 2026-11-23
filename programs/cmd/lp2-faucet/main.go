// lp2-faucet gives each node the Registry shows registered one grant of
// test LPT, once, so that a node run by someone outside the team has LPT
// to bond before it starts. It holds the faucet account's key and runs
// beside the other standing services; nobody on the team acts.
//
// # Who is granted
//
// Every address with a NodeRegistered log in the Registry (the
// Controller's BondingManager): read with a filtered eth_getLogs from
// --from-block, the deployment's first block, in pages of at most
// --log-page-blocks blocks, then, every --poll, only the blocks that are
// new. Each node is granted --grant-wei (1,000 test LPT unless set), once,
// in registration order, until --max-grants nodes hold a grant. A node the
// faucet has already sent LPT to, a Transfer from the faucet's address to
// it in the token's logs, read with the same kind of filtered query, is
// never granted again, by this faucet or by one restarted.
//
// # A crash never grants twice and never loses a grant
//
// The faucet must be the only sender from its key, and it sends one grant
// at a time. Before it broadcasts a grant it writes the journal,
// --state-dir/journal.json, replaced atomically: the signed transaction's
// raw bytes, its nonce, the node and the amount. A grant is done only on a
// successful receipt, which holds the token's Transfer to the node. On
// start, and before it grants anything new, it settles the journal's
// grant: while its nonce is not used on chain it broadcasts the same raw
// bytes again, which can be mined at most once; once the nonce is used,
// the receipt says whether the grant happened, and with none (another
// transaction took the nonce) the chain's Transfers from the faucet to the
// node say so. A grant mined and reverted is recorded as not granted, and
// the node stays due. So a crash after the journal is written and before
// the broadcast is sent again, and a crash after the broadcast and before
// the receipt is read finds the grant mined, or sends the same bytes again.
// A journal for another chain, token or faucet key is refused at start, and
// a second faucet on the same --state-dir is refused while one runs.
//
// # Waiting
//
// Three conditions stop the faucet granting, each with its own log line,
// written once when the wait begins:
//
//	cap reached: <n> nodes granted, the most --max-grants allows; ...
//	out of LPT: the faucet <address> holds <wei> wei of test LPT and a grant is <wei>; ...
//	out of gas: the faucet <address> holds <wei> wei of ETH and a grant may cost <wei>; ...
//
// It then keeps polling, and grants the nodes due, in order, once the
// condition clears: test LPT or ETH sent to the faucet's address, or a
// restart with a larger --max-grants. Nothing else waits on the faucet:
// the keeper, the sender and every node run on. A failed read of the
// chain is logged and tried again at the next poll.
//
// The other lines: "registered:" for each node read, "sent:" for a grant
// broadcast, "sent again:" for the journal's grant broadcast again,
// "granted:" for a successful receipt, "reverted:" and "not granted:" for
// a grant that did not happen, whose node is still due, and "in flight:"
// when no receipt came within --receipt-timeout (the journal keeps the
// grant, and the next poll settles it).
//
// # What the operator sees
//
// The faucet answers nothing: it has no HTTP. An operator learns of its
// grant from its node's own balance of test LPT, which rises by the grant
// within about one poll and one block after the node's register() is
// mined:
//
//	cast call <token> "balanceOf(address)(uint256)" <node> --rpc-url <url>
//
// and then bonds it (lp2-stake bond). Only the node's own address is
// granted, not its payout or operator key.
//
// # Running it
//
// The deployer funds the faucet's address at deployment with test LPT from
// the genesis supply, enough for the grants it should make, and the
// faucet's address needs ETH for one token transfer per node. The faucet's
// keystore is made for the testnet alone and used by nothing else:
//
//	lp2-faucet --rpc-url <url> --keystore-file <faucet keystore> \
//	  --keystore-password-file <file> --controller-address <Controller> \
//	  --lpt-address <TestLPT> --state-dir <folder kept across restarts> \
//	  --from-block <the deployment's first block> --log-page-blocks 5000
//
// Restart it with the same --state-dir. A state directory belongs to one
// deployment: a new deployment starts with an empty one.
package main

import (
	"context"
	"crypto/ecdsa"
	"errors"
	"flag"
	"fmt"
	"log"
	"math/big"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"
	"time"

	ethereum "github.com/ethereum/go-ethereum"
	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/common/hexutil"
	"github.com/ethereum/go-ethereum/core/types"

	"lp2core/programs/chain"
)

func main() {
	rpcURL := flag.String("rpc-url", "", "the chain's JSON-RPC URL")
	keystoreFile := flag.String("keystore-file", "", "the faucet's keystore file")
	passwordFile := flag.String("keystore-password-file", "", "the file holding the keystore's password")
	controller := flag.String("controller-address", "", "the Controller, which names the Registry (BondingManager)")
	lpt := flag.String("lpt-address", "", "the test LPT token")
	stateDir := flag.String("state-dir", "", "the folder of the journal, kept across restarts; one deployment's")
	fromBlock := flag.Uint64("from-block", 0, "the deployment's first block: no node registered before it")
	page := flag.Uint64("log-page-blocks", 5000, "the most blocks asked for in one eth_getLogs")
	grantWei := flag.String("grant-wei", "1000000000000000000000", "each node's grant, in wei of test LPT")
	maxGrants := flag.Int("max-grants", 100, "the most nodes granted")
	poll := flag.Duration("poll", 5*time.Second, "how often to read new blocks and grant the nodes due")
	receiptWait := flag.Duration("receipt-timeout", 2*time.Minute, "how long to wait for a grant's receipt before settling it at a later poll")
	flag.Parse()
	if *rpcURL == "" || *keystoreFile == "" || *passwordFile == "" || *stateDir == "" ||
		!common.IsHexAddress(*controller) || !common.IsHexAddress(*lpt) {
		fail(errors.New("--rpc-url, --keystore-file, --keystore-password-file, --controller-address, --lpt-address and --state-dir are required"))
	}
	amount, ok := new(big.Int).SetString(*grantWei, 10)
	if !ok || amount.Sign() <= 0 {
		fail(fmt.Errorf("--grant-wei %q is not a positive whole number of wei", *grantWei))
	}
	if *page < 1 || *maxGrants < 0 || *poll <= 0 || *receiptWait <= 0 {
		fail(errors.New("--log-page-blocks, --poll and --receipt-timeout must be above 0, and --max-grants not below it"))
	}
	log.SetFlags(log.Ldate | log.Ltime | log.Lmicroseconds | log.LUTC)

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	if err := os.MkdirAll(*stateDir, 0o700); err != nil {
		fail(err)
	}
	unlock, err := lock(filepath.Join(*stateDir, "lock"))
	if err != nil {
		fail(err)
	}
	defer unlock()
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
	registry, err := s.Resolve(ctx, common.HexToAddress(*controller), "BondingManager")
	if err != nil {
		fail(err)
	}
	token := common.HexToAddress(*lpt)
	l := &live{s: s, key: key, chainID: chainID, token: token}
	f := &faucet{l: l, chainID: chainID.Uint64(), self: s.From, registry: registry, token: token, amount: amount,
		maxGrants: *maxGrants, firstBlock: *fromBlock, page: *page, journal: filepath.Join(*stateDir, "journal.json"),
		receiptWait: *receiptWait, every: time.Second, logf: log.Printf}
	f.init()
	if _, err := f.loadJournal(); err != nil {
		fail(err)
	}
	held, err := l.LPT(ctx)
	if err != nil {
		fail(fmt.Errorf("the faucet's balance of %s: %w", token, err))
	}
	gas, err := l.Gas(ctx)
	if err != nil {
		fail(err)
	}
	log.Printf("faucet %s, chain %s, Registry %s, token %s; grants %s wei to each registered node, at most %d nodes; "+
		"holds %s wei of test LPT and %s wei of ETH; reads from block %d, %d blocks a page; journal %s",
		s.From, chainID, registry, token, amount, *maxGrants, held, gas, *fromBlock, *page, f.journal)
	t := time.NewTicker(*poll)
	defer t.Stop()
	for {
		if err := f.tick(ctx); err != nil && ctx.Err() == nil {
			log.Printf("error: %v; trying again at the next poll", err)
		}
		select {
		case <-ctx.Done():
			log.Printf("stopping")
			return
		case <-t.C:
		}
	}
}

// lock takes an exclusive lock on path, so that one faucet at a time uses
// a state directory.
func lock(path string) (func(), error) {
	fd, err := os.OpenFile(path, os.O_CREATE|os.O_RDWR, 0o600)
	if err != nil {
		return nil, err
	}
	if err := syscall.Flock(int(fd.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		fd.Close()
		return nil, fmt.Errorf("%s: another lp2-faucet holds this state directory (%v)", path, err)
	}
	return func() { fd.Close() }, nil
}

// live is the ledger of a real chain, through its JSON-RPC.
type live struct {
	s       *chain.Session
	key     *ecdsa.PrivateKey
	chainID *big.Int
	token   common.Address
}

func (l *live) Head(ctx context.Context) (uint64, error) { return l.s.Client.BlockNumber(ctx) }

func (l *live) Logs(ctx context.Context, q ethereum.FilterQuery) ([]types.Log, error) {
	return l.s.Client.FilterLogs(ctx, q)
}

func (l *live) Nonce(ctx context.Context, pending bool) (uint64, error) {
	if pending {
		return l.s.Client.PendingNonceAt(ctx, l.s.From)
	}
	return l.s.Client.NonceAt(ctx, l.s.From, nil)
}

func (l *live) LPT(ctx context.Context) (*big.Int, error) {
	return l.s.Uint(ctx, l.token, "balanceOf", l.s.From)
}

func (l *live) Gas(ctx context.Context) (*big.Int, error) {
	return l.s.Client.BalanceAt(ctx, l.s.From, nil)
}

// Prepare signs an EIP-1559 transfer: the estimate's gas and a quarter
// more, a fee cap of twice the newest block's base fee plus the tip.
func (l *live) Prepare(ctx context.Context, nonce uint64, to common.Address, amount *big.Int) ([]byte, common.Hash, *big.Int, error) {
	data := transferData(to, amount)
	gas, err := l.s.Client.EstimateGas(ctx, ethereum.CallMsg{From: l.s.From, To: &l.token, Data: data})
	if err != nil {
		return nil, common.Hash{}, nil, fmt.Errorf("the grant's gas estimate: %w", err)
	}
	gas += gas / 4
	tip, err := l.s.Client.SuggestGasTipCap(ctx)
	if err != nil {
		return nil, common.Hash{}, nil, err
	}
	head, err := l.s.Client.HeaderByNumber(ctx, nil)
	if err != nil {
		return nil, common.Hash{}, nil, err
	}
	feeCap := new(big.Int).Add(tip, new(big.Int).Mul(head.BaseFee, big.NewInt(2)))
	tx, err := types.SignTx(types.NewTx(&types.DynamicFeeTx{ChainID: l.chainID, Nonce: nonce, GasTipCap: tip,
		GasFeeCap: feeCap, Gas: gas, To: &l.token, Data: data}), types.LatestSignerForChainID(l.chainID), l.key)
	if err != nil {
		return nil, common.Hash{}, nil, err
	}
	raw, err := tx.MarshalBinary()
	if err != nil {
		return nil, common.Hash{}, nil, err
	}
	return raw, tx.Hash(), new(big.Int).Mul(feeCap, new(big.Int).SetUint64(gas)), nil
}

func (l *live) Broadcast(ctx context.Context, raw []byte) error {
	return l.s.Client.Client().CallContext(ctx, nil, "eth_sendRawTransaction", hexutil.Encode(raw))
}

func (l *live) Receipt(ctx context.Context, h common.Hash) (*types.Receipt, error) {
	r, err := l.s.Client.TransactionReceipt(ctx, h)
	if errors.Is(err, ethereum.NotFound) {
		return nil, nil
	}
	return r, err
}

func fail(err error) {
	fmt.Fprintln(os.Stderr, "lp2-faucet:", err)
	os.Exit(1)
}
