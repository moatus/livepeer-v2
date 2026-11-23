package main

import (
	"bytes"
	"context"
	"crypto/ecdsa"
	"crypto/rand"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math/big"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/ethereum/go-ethereum/accounts"
	"github.com/ethereum/go-ethereum/accounts/keystore"
	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/common/hexutil"
	"github.com/ethereum/go-ethereum/crypto"

	"lp2core/programs/chain"
)

// joinMessage is the text a node signs to join, as lp2-sender's
// documentation specifies it: the UTF-8 bytes of
// lp2-join:v1:<chainId>:<registry>:<node>:<url>:<issuedAt>, chainId and
// issuedAt in decimal, the addresses in lowercase 0x hex, url as sent.
func joinMessage(chainID *big.Int, registry, node common.Address, url string, issuedAt int64) []byte {
	return []byte(fmt.Sprintf("lp2-join:v1:%s:%s:%s:%s:%d", chainID, strings.ToLower(registry.Hex()),
		strings.ToLower(node.Hex()), url, issuedAt))
}

// signJoin is the body of POST /join for the key's node at url, issued at
// issuedAt: the key's EIP-191 personal signature of joinMessage, v 27 or 28.
func signJoin(key *ecdsa.PrivateKey, chainID *big.Int, registry common.Address, url string, issuedAt int64) (map[string]any, error) {
	node := crypto.PubkeyToAddress(key.PublicKey)
	sig, err := crypto.Sign(accounts.TextHash(joinMessage(chainID, registry, node, url, issuedAt)), key)
	if err != nil {
		return nil, err
	}
	sig[64] += 27
	return map[string]any{"node": strings.ToLower(node.Hex()), "url": url, "issuedAt": issuedAt,
		"signature": hexutil.Encode(sig)}, nil
}

// join posts the key's signed join for nodeURL to the sender. It reports a
// refusal (a 4xx with a code) as refused, and fails when the sender could
// not be asked or answered otherwise.
func join(ctx context.Context, s *chain.Session, registry common.Address, senderURL, nodeURL string) (refused bool, err error) {
	id, err := s.Client.ChainID(ctx)
	if err != nil {
		return false, err
	}
	body, err := signJoin(s.Key(), id, registry, nodeURL, time.Now().Unix())
	if err != nil {
		return false, err
	}
	raw, _ := json.Marshal(body)
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, strings.TrimRight(senderURL, "/")+"/join", bytes.NewReader(raw))
	if err != nil {
		return false, err
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := (&http.Client{Timeout: time.Minute}).Do(req)
	if err != nil {
		return false, fmt.Errorf("join: %w", err)
	}
	defer resp.Body.Close()
	answer, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
	text := strings.TrimSpace(string(answer))
	switch {
	case resp.StatusCode == http.StatusOK:
		fmt.Printf("joined: %s at %s: %s\n", s.From, nodeURL, text)
		return false, nil
	case resp.StatusCode >= 400 && resp.StatusCode < 500:
		fmt.Printf("refused: join of %s at %s: HTTP %d %s\n", s.From, nodeURL, resp.StatusCode, text)
		return true, nil
	}
	return false, fmt.Errorf("join of %s at %s: HTTP %d %s", s.From, nodeURL, resp.StatusCode, text)
}

// newKey makes a key and writes it to file, encrypted with the password in
// passwordFile, as a keystore the node and the other programs read. It
// never replaces a file that exists.
func newKey(file, passwordFile string) (common.Address, error) {
	if file == "" || passwordFile == "" {
		return common.Address{}, errors.New("new-key needs --keystore-file and --keystore-password-file")
	}
	pw, err := os.ReadFile(passwordFile)
	if err != nil {
		return common.Address{}, err
	}
	if len(pw) == 0 {
		return common.Address{}, fmt.Errorf("%s is empty", passwordFile)
	}
	key, err := ecdsa.GenerateKey(crypto.S256(), rand.Reader)
	if err != nil {
		return common.Address{}, err
	}
	k := &keystore.Key{Address: crypto.PubkeyToAddress(key.PublicKey), PrivateKey: key}
	// The password file sits beside the keystore, so a slow key derivation
	// guards nothing: the light one keeps each start of a program quick.
	blob, err := keystore.EncryptKey(k, strings.TrimRight(string(pw), "\r\n"), keystore.LightScryptN, keystore.LightScryptP)
	if err != nil {
		return common.Address{}, err
	}
	tmp, err := os.CreateTemp(filepath.Dir(file), ".new-key-*")
	if err != nil {
		return common.Address{}, err
	}
	defer os.Remove(tmp.Name())
	if _, err := tmp.Write(blob); err != nil {
		tmp.Close()
		return common.Address{}, err
	}
	if err := tmp.Close(); err != nil {
		return common.Address{}, err
	}
	// A hard link fails when file exists, so a key is never replaced.
	if err := os.Link(tmp.Name(), file); err != nil {
		return common.Address{}, fmt.Errorf("new-key: %w", err)
	}
	return k.Address, nil
}
