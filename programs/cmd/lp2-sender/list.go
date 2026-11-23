package main

import (
	"bufio"
	"bytes"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"

	"github.com/ethereum/go-ethereum/common"
)

// entry is one line of the list: a node that joined, the URL it signed,
// the issuedAt of its last accepted join, and the last round it was sent
// a paid request in (0: none yet).
type entry struct {
	Node      common.Address
	URL       string
	IssuedAt  int64
	PaidRound uint64
}

const listHeader = "# lp2-sender's list of nodes, written by lp2-sender alone: node url issuedAt paidRound\n"

// parseList reads the list file's lines: "node url issuedAt paidRound",
// with blank lines and # comments skipped. Any other line is an error,
// naming its line number: the sender is the list's only writer, so a
// line it cannot read is not dropped, which would lose it at the next
// write.
func parseList(data []byte) ([]entry, error) {
	var out []entry
	seen := map[common.Address]bool{}
	sc := bufio.NewScanner(bytes.NewReader(data))
	sc.Buffer(make([]byte, 0, 4096), 1<<20)
	for line := 1; sc.Scan(); line++ {
		text := strings.TrimSpace(sc.Text())
		if text == "" || strings.HasPrefix(text, "#") {
			continue
		}
		f := strings.Fields(text)
		if len(f) != 4 || !common.IsHexAddress(f[0]) {
			return nil, fmt.Errorf("line %d: want \"node url issuedAt paidRound\"", line)
		}
		issued, err := strconv.ParseInt(f[2], 10, 64)
		if err != nil || issued <= 0 {
			return nil, fmt.Errorf("line %d: issuedAt %q is not a positive whole number of seconds", line, f[2])
		}
		paid, err := strconv.ParseUint(f[3], 10, 64)
		if err != nil {
			return nil, fmt.Errorf("line %d: paidRound %q is not a whole number", line, f[3])
		}
		e := entry{Node: common.HexToAddress(f[0]), URL: f[1], IssuedAt: issued, PaidRound: paid}
		if seen[e.Node] {
			return nil, fmt.Errorf("line %d: node %s is listed twice", line, e.Node)
		}
		seen[e.Node] = true
		out = append(out, e)
	}
	return out, sc.Err()
}

// formatList is the list file's text for entries, in their order.
func formatList(entries []entry) []byte {
	var b bytes.Buffer
	b.WriteString(listHeader)
	for _, e := range entries {
		fmt.Fprintf(&b, "%s %s %d %d\n", strings.ToLower(e.Node.Hex()), e.URL, e.IssuedAt, e.PaidRound)
	}
	return b.Bytes()
}

// readList reads the list file; a file that does not exist is an empty list.
func readList(path string) ([]entry, error) {
	data, err := os.ReadFile(path)
	if os.IsNotExist(err) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	out, err := parseList(data)
	if err != nil {
		return nil, fmt.Errorf("%s: %w", path, err)
	}
	return out, nil
}

// writeList replaces the list file atomically: the new text goes to a
// file beside it, which is flushed to disk and then renamed over it. A
// reader, or a sender restarted after a crash, sees the old list or the
// new one, never part of one.
func writeList(path string, entries []entry) error {
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
	if _, err := tmp.Write(formatList(entries)); err != nil {
		return err
	}
	if err := tmp.Chmod(0o644); err != nil {
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
	if d, err := os.Open(dir); err == nil {
		d.Sync()
		d.Close()
	}
	return nil
}
