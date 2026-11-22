package chain

import (
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strings"
	"testing"

	"github.com/ethereum/go-ethereum/crypto"
)

func TestCodeNamesEachCustomError(t *testing.T) {
	for _, c := range Codes {
		data := crypto.Keccak256([]byte(c + "(" + codeArgs[c] + ")"))[:4]
		data = append(data, make([]byte, 32*argCount(codeArgs[c]))...)
		if got := Code(data); got != c {
			t.Errorf("Code(%s with its arguments) = %q", c, got)
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
	declared := regexp.MustCompile(`error ([A-Z_]+)\(([^)]*)\)`)
	for _, f := range files {
		src, err := os.ReadFile(f)
		if err != nil {
			t.Fatal(err)
		}
		for _, m := range declared.FindAllStringSubmatch(string(src), -1) {
			if !slices.Contains(Codes, m[1]) {
				t.Errorf("%s declares %s, which Codes does not name", filepath.Base(f), m[1])
			}
			var types []string
			for _, p := range strings.Split(m[2], ",") {
				if p = strings.TrimSpace(p); p != "" {
					types = append(types, strings.Fields(p)[0])
				}
			}
			if got := strings.Join(types, ","); got != codeArgs[m[1]] {
				t.Errorf("%s declares %s(%s), but codeArgs has (%s)", filepath.Base(f), m[1], got, codeArgs[m[1]])
			}
		}
	}
}
