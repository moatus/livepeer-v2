package chain

import (
	"testing"

	"github.com/ethereum/go-ethereum/crypto"
)

func TestCodeNamesEachCustomError(t *testing.T) {
	for _, c := range Codes {
		if got := Code(crypto.Keccak256([]byte(c + "()"))[:4]); got != c {
			t.Errorf("Code(selector of %s) = %q", c, got)
		}
	}
}

func TestCodeReadsTheKeptRevertText(t *testing.T) {
	// Error(string) with the text the node treats as final.
	data := append(crypto.Keccak256([]byte("Error(string)"))[:4], make([]byte, 0)...)
	text := "ticket creationRound does not have a block hash"
	word := func(n int) []byte { b := make([]byte, 32); b[31] = byte(n); return b }
	data = append(data, word(32)...)
	data = append(data, word(len(text))...)
	padded := make([]byte, 64)
	copy(padded, text)
	data = append(data, padded...)
	if got := Code(data); got != text {
		t.Errorf("Code(Error(string)) = %q, want %q", got, text)
	}
}

func TestCodeIsEmptyForAnythingElse(t *testing.T) {
	for _, data := range [][]byte{nil, {1, 2, 3, 4}, crypto.Keccak256([]byte("OTHER()"))[:4]} {
		if got := Code(data); got != "" {
			t.Errorf("Code(%x) = %q, want empty", data, got)
		}
	}
}
