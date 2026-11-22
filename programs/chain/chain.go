// Package chain is what the helper programs share: a connection to the
// chain, the operator's key, the contract calls they make, and the stable
// code of a refused call. It reads the contracts' public shape from
// contracts/interfaces and adds nothing to it.
package chain

import (
	"context"
	"crypto/ecdsa"
	"encoding/hex"
	"errors"
	"fmt"
	"math/big"
	"os"
	"strings"
	"time"

	ethereum "github.com/ethereum/go-ethereum"
	"github.com/ethereum/go-ethereum/accounts/abi"
	"github.com/ethereum/go-ethereum/accounts/abi/bind"
	"github.com/ethereum/go-ethereum/accounts/keystore"
	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/core/types"
	"github.com/ethereum/go-ethereum/crypto"
	"github.com/ethereum/go-ethereum/ethclient"
	"github.com/ethereum/go-ethereum/rpc"
)

// The calls the programs make, as declared in contracts/interfaces.
const contractsABI = `[
 {"type":"function","name":"getContract","stateMutability":"view","inputs":[{"name":"id","type":"bytes32"}],"outputs":[{"name":"","type":"address"}]},
 {"type":"function","name":"approve","stateMutability":"nonpayable","inputs":[{"name":"spender","type":"address"},{"name":"amount","type":"uint256"}],"outputs":[{"name":"","type":"bool"}]},
 {"type":"function","name":"balanceOf","stateMutability":"view","inputs":[{"name":"account","type":"address"}],"outputs":[{"name":"","type":"uint256"}]},
 {"type":"function","name":"fund","stateMutability":"nonpayable","inputs":[{"name":"deposit","type":"uint256"},{"name":"node","type":"address"},{"name":"earmark","type":"uint256"}],"outputs":[]},
 {"type":"function","name":"getSenderInfo","stateMutability":"view","inputs":[{"name":"payer","type":"address"}],"outputs":[{"name":"sender","type":"tuple","components":[{"name":"deposit","type":"uint256"},{"name":"withdrawRound","type":"uint256"}]},{"name":"reserve","type":"tuple","components":[{"name":"fundsRemaining","type":"uint256"},{"name":"claimedInCurrentRound","type":"uint256"}]}]},
 {"type":"function","name":"claimableReserve","stateMutability":"view","inputs":[{"name":"payer","type":"address"},{"name":"node","type":"address"}],"outputs":[{"name":"","type":"uint256"}]},
 {"type":"function","name":"initializeRound","stateMutability":"nonpayable","inputs":[],"outputs":[]},
 {"type":"function","name":"currentRound","stateMutability":"view","inputs":[],"outputs":[{"name":"","type":"uint256"}]},
 {"type":"function","name":"bond","stateMutability":"nonpayable","inputs":[{"name":"node","type":"address"},{"name":"amount","type":"uint256"}],"outputs":[]},
 {"type":"function","name":"unbond","stateMutability":"nonpayable","inputs":[{"name":"node","type":"address"},{"name":"amount","type":"uint256"}],"outputs":[]},
 {"type":"function","name":"transferBond","stateMutability":"nonpayable","inputs":[{"name":"from","type":"address"},{"name":"to","type":"address"},{"name":"amount","type":"uint256"}],"outputs":[]},
 {"type":"function","name":"unbondTransfer","stateMutability":"nonpayable","inputs":[{"name":"from","type":"address"},{"name":"to","type":"address"},{"name":"amount","type":"uint256"}],"outputs":[]},
 {"type":"function","name":"rebond","stateMutability":"nonpayable","inputs":[{"name":"node","type":"address"},{"name":"amount","type":"uint256"}],"outputs":[]},
 {"type":"function","name":"withdrawUnbonded","stateMutability":"nonpayable","inputs":[],"outputs":[]},
 {"type":"function","name":"setRewardCut","stateMutability":"nonpayable","inputs":[{"name":"node","type":"address"},{"name":"cut","type":"uint256"}],"outputs":[]},
 {"type":"function","name":"claim","stateMutability":"nonpayable","inputs":[{"name":"node","type":"address"},{"name":"round","type":"uint256"}],"outputs":[]},
 {"type":"function","name":"withdrawParts","stateMutability":"nonpayable","inputs":[{"name":"node","type":"address"},{"name":"rounds","type":"uint256[]"}],"outputs":[]},
 {"type":"function","name":"withdrawValidatorParts","stateMutability":"nonpayable","inputs":[{"name":"node","type":"address"},{"name":"rounds","type":"uint256[]"}],"outputs":[]},
 {"type":"function","name":"mintRoundShares","stateMutability":"nonpayable","inputs":[{"name":"round","type":"uint256"}],"outputs":[]},
 {"type":"function","name":"sharesMinted","stateMutability":"view","inputs":[{"name":"round","type":"uint256"}],"outputs":[{"name":"","type":"bool"}]},
 {"type":"function","name":"optIn","stateMutability":"nonpayable","inputs":[{"name":"node","type":"address"}],"outputs":[]},
 {"type":"function","name":"vote","stateMutability":"nonpayable","inputs":[{"name":"node","type":"address"},{"name":"target","type":"address"},{"name":"score","type":"uint256"}],"outputs":[]}
]`

// Codes are the custom errors of contracts/interfaces, by name. A test
// reads the interface files and fails when one is missing here.
var Codes = []string{
	"NOT_REGISTERED", "PAYOUT_INVALID", "ROUND_NOT_OVER", "TICKET_EXPIRED", "TICKET_USED",
	"TICKET_INVALID", "NOTHING_TO_PAY", "AMOUNT_TOO_LARGE", "RELEASE_NOT_READY",
	"NOT_AUTHORIZED", "UNBOND_NOT_READY", "CUT_OUT_OF_RANGE", "SAME_NODE",
	"CLAIM_TOO_EARLY", "CLAIM_EXPIRED", "ALREADY_CLAIMED", "SCORE_OUT_OF_RANGE", "NOT_CLAIMED", "NO_SUCH_ROUND",
	"NOT_OPTED_IN", "STAKE_TOO_LOW", "NOT_VALIDATOR",
	"NOT_ADJUSTABLE", "SCHEDULE_RAISED", "VALUE_OUT_OF_RANGE", "IDENTITY_NOT_WHOLE",
	"NOTHING_PENDING", "OPERATOR_INVALID",
}

var parsedABI = func() abi.ABI {
	a, err := abi.JSON(strings.NewReader(contractsABI))
	if err != nil {
		panic(err)
	}
	return a
}()

// Code is the stable code a call was refused with: a custom error's name,
// a revert text, or "" when the revert data is neither.
func Code(revertData []byte) string {
	if len(revertData) == 4 {
		for _, c := range Codes {
			if string(crypto.Keccak256([]byte(c + "()"))[:4]) == string(revertData) {
				return c
			}
		}
	}
	if text, err := abi.UnpackRevert(revertData); err == nil {
		return text
	}
	return ""
}

// RefusedCode is the stable code in an error from a call or a gas
// estimate, or "" when the error is not a refusal.
func RefusedCode(err error) string {
	var de rpc.DataError
	if !errors.As(err, &de) {
		return ""
	}
	s, ok := de.ErrorData().(string)
	if !ok {
		return ""
	}
	data, decodeErr := hex.DecodeString(strings.TrimPrefix(s, "0x"))
	if decodeErr != nil {
		return ""
	}
	return Code(data)
}

// LoadKey decrypts a keystore file, the same files the node takes.
func LoadKey(keystoreFile, passwordFile string) (*ecdsa.PrivateKey, error) {
	blob, err := os.ReadFile(keystoreFile)
	if err != nil {
		return nil, err
	}
	pw, err := os.ReadFile(passwordFile)
	if err != nil {
		return nil, err
	}
	k, err := keystore.DecryptKey(blob, strings.TrimRight(string(pw), "\r\n"))
	if err != nil {
		return nil, fmt.Errorf("keystore %s: %w", keystoreFile, err)
	}
	return k.PrivateKey, nil
}

// Session is one operator's connection: the chain, its key, and the
// contracts it found through the Controller.
type Session struct {
	Client *ethclient.Client
	From   common.Address
	auth   *bind.TransactOpts
}

// Open dials the chain and prepares to sign as key.
func Open(ctx context.Context, rpcURL string, key *ecdsa.PrivateKey) (*Session, error) {
	client, err := ethclient.DialContext(ctx, rpcURL)
	if err != nil {
		return nil, err
	}
	id, err := client.ChainID(ctx)
	if err != nil {
		return nil, err
	}
	auth, err := bind.NewKeyedTransactorWithChainID(key, id)
	if err != nil {
		return nil, err
	}
	return &Session{Client: client, From: auth.From, auth: auth}, nil
}

func (s *Session) contract(at common.Address) *bind.BoundContract {
	return bind.NewBoundContract(at, parsedABI, s.Client, s.Client, s.Client)
}

// Resolve finds a contract by the name the node uses ("TicketBroker",
// "RoundsManager", "BondingManager"), as the node does.
func (s *Session) Resolve(ctx context.Context, controller common.Address, name string) (common.Address, error) {
	var out []any
	id := crypto.Keccak256Hash([]byte(name))
	if err := s.contract(controller).Call(&bind.CallOpts{Context: ctx}, &out, "getContract", id); err != nil {
		return common.Address{}, err
	}
	addr := out[0].(common.Address)
	if addr == (common.Address{}) {
		return addr, fmt.Errorf("the Controller at %s has no %s", controller, name)
	}
	return addr, nil
}

// Call reads a view and returns its outputs.
func (s *Session) Call(ctx context.Context, at common.Address, method string, args ...any) ([]any, error) {
	var out []any
	err := s.contract(at).Call(&bind.CallOpts{Context: ctx, From: s.From}, &out, method, args...)
	return out, err
}

// Try runs a transaction as a call at the latest block, without sending
// it. It returns the stable code when the contract would refuse it.
func (s *Session) Try(ctx context.Context, at common.Address, method string, args ...any) (code string, err error) {
	data, err := parsedABI.Pack(method, args...)
	if err != nil {
		return "", err
	}
	_, err = s.Client.CallContract(ctx, ethereum.CallMsg{From: s.From, To: &at, Data: data}, nil)
	if err != nil {
		if c := RefusedCode(err); c != "" {
			return c, nil
		}
		return "", err
	}
	return "", nil
}

// TryAt is Try at the chain's newest block, which it names, so a refusal
// can be placed among the transactions around it: the answer holds after
// every transaction of that block.
func (s *Session) TryAt(ctx context.Context, at common.Address, method string, args ...any) (code string, block uint64, err error) {
	data, err := parsedABI.Pack(method, args...)
	if err != nil {
		return "", 0, err
	}
	head, err := s.Client.HeaderByNumber(ctx, nil)
	if err != nil {
		return "", 0, err
	}
	block = head.Number.Uint64()
	_, err = s.Client.CallContract(ctx, ethereum.CallMsg{From: s.From, To: &at, Data: data}, head.Number)
	if err != nil {
		if c := RefusedCode(err); c != "" {
			return c, block, nil
		}
		return "", block, err
	}
	return "", block, nil
}

// Bool reads a view that returns one boolean.
func (s *Session) Bool(ctx context.Context, at common.Address, method string, args ...any) (bool, error) {
	out, err := s.Call(ctx, at, method, args...)
	if err != nil {
		return false, err
	}
	return out[0].(bool), nil
}

// Send signs and sends a transaction, waits until it is mined and fails
// unless it succeeded. A refusal is reported by its stable code.
func (s *Session) Send(ctx context.Context, at common.Address, method string, args ...any) (*types.Receipt, error) {
	opts := *s.auth
	opts.Context = ctx
	tx, err := s.contract(at).Transact(&opts, method, args...)
	if err != nil {
		if c := RefusedCode(err); c != "" {
			return nil, fmt.Errorf("%s refused: %s", method, c)
		}
		return nil, fmt.Errorf("%s: %w", method, err)
	}
	wait, cancel := context.WithTimeout(ctx, 2*time.Minute)
	defer cancel()
	receipt, err := bind.WaitMined(wait, s.Client, tx)
	if err != nil {
		return nil, fmt.Errorf("%s %s: %w", method, tx.Hash(), err)
	}
	if receipt.Status != types.ReceiptStatusSuccessful {
		return receipt, fmt.Errorf("%s %s reverted", method, tx.Hash())
	}
	return receipt, nil
}

// Uint reads a view that returns one integer.
func (s *Session) Uint(ctx context.Context, at common.Address, method string, args ...any) (*big.Int, error) {
	out, err := s.Call(ctx, at, method, args...)
	if err != nil {
		return nil, err
	}
	return out[0].(*big.Int), nil
}
