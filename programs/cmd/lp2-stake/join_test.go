package main

import (
	"math/big"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/ethereum/go-ethereum/accounts"
	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/common/hexutil"
	"github.com/ethereum/go-ethereum/crypto"

	"lp2core/programs/chain"
)

// The example in lp2-sender's documentation, exactly.
func TestJoinMessageIsTheSendersExample(t *testing.T) {
	got := string(joinMessage(big.NewInt(474747), common.HexToAddress("0x5FbDB2315678afecb367f032d93F642f64180aa3"),
		common.HexToAddress("0x70997970C51812dc3A010C7d01b50e0d17dc79C8"), "https://node.example:8935", 1795000000))
	want := "lp2-join:v1:474747:0x5fbdb2315678afecb367f032d93f642f64180aa3:0x70997970c51812dc3a010c7d01b50e0d17dc79c8:https://node.example:8935:1795000000"
	if got != want {
		t.Fatalf("join message\n got %s\nwant %s", got, want)
	}
}

// The signature is the node key's EIP-191 personal signature of the
// message, v 27 or 28, and recovers the node, as the sender checks it.
func TestSignJoinRecoversTheNode(t *testing.T) {
	key, err := crypto.HexToECDSA("59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d") // a well-known local chain development key
	if err != nil {
		t.Fatal(err)
	}
	registry := common.HexToAddress("0x5FbDB2315678afecb367f032d93F642f64180aa3")
	body, err := signJoin(key, big.NewInt(474747), registry, "http://127.0.0.1:8935", 1795000000)
	if err != nil {
		t.Fatal(err)
	}
	if body["node"] != "0x70997970c51812dc3a010c7d01b50e0d17dc79c8" || body["issuedAt"] != int64(1795000000) {
		t.Fatalf("body %v", body)
	}
	sig := hexutil.MustDecode(body["signature"].(string))
	if len(sig) != 65 || (sig[64] != 27 && sig[64] != 28) {
		t.Fatalf("signature %x: want 65 bytes with v 27 or 28", sig)
	}
	sig[64] -= 27
	msg := joinMessage(big.NewInt(474747), registry, common.HexToAddress(body["node"].(string)), "http://127.0.0.1:8935", 1795000000)
	pub, err := crypto.SigToPub(accounts.TextHash(msg), sig)
	if err != nil || crypto.PubkeyToAddress(*pub) != crypto.PubkeyToAddress(key.PublicKey) {
		t.Fatalf("the signature does not recover the node: %v", err)
	}
}

func TestNewKeyWritesAKeystoreOnceAndNeverReplacesIt(t *testing.T) {
	dir := t.TempDir()
	pw := filepath.Join(dir, "password")
	if err := os.WriteFile(pw, []byte("not-printed"), 0o600); err != nil {
		t.Fatal(err)
	}
	file := filepath.Join(dir, "node.json")
	addr, err := newKey(file, pw)
	if err != nil {
		t.Fatal(err)
	}
	key, err := chain.LoadKey(file, pw)
	if err != nil {
		t.Fatal(err)
	}
	if crypto.PubkeyToAddress(key.PublicKey) != addr {
		t.Fatalf("the keystore holds another key than the address printed")
	}
	blob, _ := os.ReadFile(file)
	if !strings.Contains(string(blob), strings.ToLower(addr.Hex()[2:])) {
		t.Errorf("the keystore has no address field the node can read")
	}
	if info, _ := os.Stat(file); info.Mode().Perm() != 0o600 {
		t.Errorf("the keystore's mode is %v, want 0600", info.Mode().Perm())
	}
	if _, err := newKey(file, pw); err == nil {
		t.Fatal("new-key replaced a keystore that exists")
	}
	again, _ := os.ReadFile(file)
	if string(again) != string(blob) {
		t.Fatal("a refused new-key changed the keystore")
	}
	if entries, _ := os.ReadDir(dir); len(entries) != 2 {
		t.Errorf("new-key left %d files, want the password and the keystore", len(entries))
	}
}
