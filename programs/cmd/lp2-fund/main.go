// lp2-fund puts a payer's mock USDC into its account at the Broker: it
// approves the Broker to take the amount, then funds a deposit and, when
// --earmark is above 0, an earmark for --node (rulebook R-21). It replaces
// the node's own funding command, which sends ETH and cannot fund a USDC
// account.
//
// It signs with the payer's keystore, the same file the payer's signer
// uses, and finds the Broker through the Controller, as the node does.
// Amounts are micro-USDC.
package main

import (
	"context"
	"flag"
	"fmt"
	"math/big"
	"os"
	"time"

	"github.com/ethereum/go-ethereum/accounts/abi"
	"github.com/ethereum/go-ethereum/common"

	"lp2core/programs/chain"
)

type request struct {
	deposit, earmark *big.Int
	node             common.Address
}

// parse checks the amounts and that an earmark names a node.
func parse(deposit, earmark, node string) (request, error) {
	var r request
	var ok bool
	if r.deposit, ok = new(big.Int).SetString(deposit, 10); !ok || r.deposit.Sign() < 0 {
		return r, fmt.Errorf("--deposit %q is not a whole number of micro-USDC", deposit)
	}
	if r.earmark, ok = new(big.Int).SetString(earmark, 10); !ok || r.earmark.Sign() < 0 {
		return r, fmt.Errorf("--earmark %q is not a whole number of micro-USDC", earmark)
	}
	if r.earmark.Sign() > 0 {
		if !common.IsHexAddress(node) {
			return r, fmt.Errorf("--earmark needs --node, the address of the node it is for")
		}
		r.node = common.HexToAddress(node)
	}
	if r.deposit.Sign() == 0 && r.earmark.Sign() == 0 {
		return r, fmt.Errorf("nothing to fund: give --deposit, --earmark or both")
	}
	return r, nil
}

func main() {
	rpcURL := flag.String("rpc-url", "", "the chain's JSON-RPC URL")
	keystoreFile := flag.String("keystore-file", "", "the payer's keystore file")
	passwordFile := flag.String("keystore-password-file", "", "the file holding the keystore's password")
	controller := flag.String("controller-address", "", "the Controller the node is given")
	usdc := flag.String("usdc-address", "", "the mock USDC token")
	deposit := flag.String("deposit", "0", "micro-USDC to add to the deposit")
	earmark := flag.String("earmark", "0", "micro-USDC to add to the earmark for --node")
	node := flag.String("node", "", "the node the earmark is for")
	flag.Parse()

	r, err := parse(*deposit, *earmark, *node)
	if err != nil {
		fail(err)
	}
	if *rpcURL == "" || *keystoreFile == "" || *passwordFile == "" ||
		!common.IsHexAddress(*controller) || !common.IsHexAddress(*usdc) {
		fail(fmt.Errorf("--rpc-url, --keystore-file, --keystore-password-file, --controller-address and --usdc-address are required"))
	}
	if err := run(r, *rpcURL, *keystoreFile, *passwordFile, common.HexToAddress(*controller), common.HexToAddress(*usdc)); err != nil {
		fail(err)
	}
}

func run(r request, rpcURL, keystoreFile, passwordFile string, controller, usdc common.Address) error {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Minute)
	defer cancel()
	key, err := chain.LoadKey(keystoreFile, passwordFile)
	if err != nil {
		return err
	}
	s, err := chain.Open(ctx, rpcURL, key)
	if err != nil {
		return err
	}
	broker, err := s.Resolve(ctx, controller, "TicketBroker")
	if err != nil {
		return err
	}
	total := new(big.Int).Add(r.deposit, r.earmark)
	approved, err := s.Send(ctx, usdc, "approve", broker, total)
	if err != nil {
		return err
	}
	fmt.Printf("approved: the Broker may take %s (block %d, tx %s)\n", total, approved.BlockNumber, approved.TxHash)
	receipt, err := s.Send(ctx, broker, "fund", r.deposit, r.node, r.earmark)
	if err != nil {
		return err
	}
	fmt.Printf("funded: payer %s deposit +%s earmark +%s for %s (block %d, tx %s)\n",
		s.From, r.deposit, r.earmark, r.node, receipt.BlockNumber, receipt.TxHash)

	info, err := s.Call(ctx, broker, "getSenderInfo", s.From)
	if err != nil {
		return err
	}
	var sender struct{ Deposit, WithdrawRound *big.Int }
	var reserve struct{ FundsRemaining, ClaimedInCurrentRound *big.Int }
	abi.ConvertType(info[0], &sender)
	abi.ConvertType(info[1], &reserve)
	fmt.Printf("shown to nodes: deposit %s, earmarks in all %s\n", sender.Deposit, reserve.FundsRemaining)
	if r.earmark.Sign() > 0 {
		claimable, err := s.Uint(ctx, broker, "claimableReserve", s.From, r.node)
		if err != nil {
			return err
		}
		fmt.Printf("shown to %s: earmark %s\n", r.node, claimable)
	}
	return nil
}

func fail(err error) {
	fmt.Fprintln(os.Stderr, "lp2-fund:", err)
	os.Exit(1)
}
