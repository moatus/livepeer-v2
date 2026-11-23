package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"math/big"
	"os"
	"path/filepath"

	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/common/hexutil"
	"github.com/ethereum/go-ethereum/core/types"
)

// The outcomes a journal entry can hold. Only "pending" is resolved at
// start; the others record how the last grant ended.
const (
	pending    = "pending"     // signed and written down; not yet known to be mined
	granted    = "granted"     // mined with a successful receipt: the node holds its grant
	reverted   = "reverted"    // mined and reverted: not granted, the node is still due
	notGranted = "not granted" // the nonce went to another transaction: not granted, the node is still due
)

// entry is the journal: the faucet's one grant in flight, or the last one
// and how it ended. Raw is the signed transaction, byte for byte, so a
// grant sent again after a crash is the same transaction, with the same
// nonce, and can be mined at most once.
type entry struct {
	ChainID uint64         `json:"chainId"`
	Token   common.Address `json:"token"`
	Faucet  common.Address `json:"faucet"`
	Node    common.Address `json:"node"`
	Amount  string         `json:"amountWei"`
	Nonce   uint64         `json:"nonce"`
	Block   uint64         `json:"signedAtBlock"` // the head when it was signed: the grant is mined after it
	Tx      common.Hash    `json:"tx"`
	Raw     hexutil.Bytes  `json:"raw"`
	Outcome string         `json:"outcome"`
}

// check holds the entry to what its raw bytes say: the transaction's hash,
// nonce, chain, signer, and a call of the token's transfer(node, amount).
// A journal that fails it is refused, never repaired: it is the only
// record of a grant that may be in flight.
func (e *entry) check() error {
	switch e.Outcome {
	case pending, granted, reverted, notGranted:
	default:
		return fmt.Errorf("outcome %q is not one the faucet writes", e.Outcome)
	}
	amount, ok := new(big.Int).SetString(e.Amount, 10)
	if !ok || amount.Sign() <= 0 {
		return fmt.Errorf("amountWei %q is not a positive whole number", e.Amount)
	}
	tx := new(types.Transaction)
	if err := tx.UnmarshalBinary(e.Raw); err != nil {
		return fmt.Errorf("raw is not a signed transaction: %w", err)
	}
	from, err := types.Sender(types.LatestSignerForChainID(new(big.Int).SetUint64(e.ChainID)), tx)
	switch {
	case err != nil:
		return fmt.Errorf("raw's signature: %w", err)
	case tx.Hash() != e.Tx:
		return fmt.Errorf("raw is transaction %s, not %s", tx.Hash(), e.Tx)
	case tx.Nonce() != e.Nonce:
		return fmt.Errorf("raw has nonce %d, not %d", tx.Nonce(), e.Nonce)
	case from != e.Faucet:
		return fmt.Errorf("raw is signed by %s, not the faucet %s", from, e.Faucet)
	case tx.To() == nil || *tx.To() != e.Token || tx.Value().Sign() != 0 || !bytes.Equal(tx.Data(), transferData(e.Node, amount)):
		return errors.New("raw is not the token's transfer of amountWei to node")
	}
	return nil
}

// amount is the entry's amount, which check has read.
func (e *entry) amount() *big.Int {
	a, _ := new(big.Int).SetString(e.Amount, 10)
	return a
}

// readJournal reads the journal; with no file there is no entry. An entry
// for another chain, token or faucet is refused: the state directory
// belongs to one deployment and one key.
func readJournal(path string, chainID uint64, token, faucet common.Address) (*entry, error) {
	data, err := os.ReadFile(path)
	if os.IsNotExist(err) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	var e entry
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&e); err != nil {
		return nil, fmt.Errorf("%s: %w", path, err)
	}
	if err := e.check(); err != nil {
		return nil, fmt.Errorf("%s: %w", path, err)
	}
	if e.ChainID != chainID || e.Token != token || e.Faucet != faucet {
		return nil, fmt.Errorf("%s is for chain %d, token %s and faucet %s, not chain %d, token %s and faucet %s: "+
			"a state directory belongs to one deployment and one key", path, e.ChainID, e.Token, e.Faucet, chainID, token, faucet)
	}
	return &e, nil
}

// writeJournal replaces the journal atomically: the new text goes to a
// file beside it, which is flushed to disk and then renamed over it, and
// the folder is flushed. A faucet restarted after a crash at any moment
// reads the old entry or the new one, never part of one.
func writeJournal(path string, e *entry) error {
	data, err := json.MarshalIndent(e, "", "  ")
	if err != nil {
		return err
	}
	dir := filepath.Dir(path)
	tmp, err := os.CreateTemp(dir, "."+filepath.Base(path)+".*")
	if err != nil {
		return err
	}
	done := false
	defer func() {
		if !done {
			tmp.Close()
			os.Remove(tmp.Name())
		}
	}()
	if _, err := tmp.Write(append(data, '\n')); err != nil {
		return err
	}
	if err := tmp.Chmod(0o600); err != nil {
		return err
	}
	if err := tmp.Sync(); err != nil {
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	if err := os.Rename(tmp.Name(), path); err != nil {
		return err
	}
	done = true
	d, err := os.Open(dir)
	if err != nil {
		return err
	}
	defer d.Close()
	return d.Sync()
}
