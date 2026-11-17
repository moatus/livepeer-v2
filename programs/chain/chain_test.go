package chain

import (
	"os"
	"path/filepath"
	"regexp"
	"slices"
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

// Every custom error the contracts declare is a code the programs can
// name, so a refusal is never reported as an unknown revert.
func TestCodesCoverEveryInterfaceError(t *testing.T) {
	files, err := filepath.Glob("../../contracts/interfaces/*.sol")
	if err != nil || len(files) == 0 {
		t.Fatalf("no interface files found: %v", err)
	}
	declared := regexp.MustCompile(`error ([A-Z_]+)\(\)`)
	for _, f := range files {
		src, err := os.ReadFile(f)
		if err != nil {
			t.Fatal(err)
		}
		for _, m := range declared.FindAllStringSubmatch(string(src), -1) {
			if !slices.Contains(Codes, m[1]) {
				t.Errorf("%s declares %s, which Codes does not name", filepath.Base(f), m[1])
			}
		}
	}
}
