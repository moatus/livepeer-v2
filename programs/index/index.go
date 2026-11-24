// Package index keeps a deployment's events in one append-only file and
// the sums derived from them, so that a reader such as lp2-explorer starts
// again where it stopped instead of reading the deployment from its first
// block. It reads the chain through a Source, one eth_getLogs a page.
//
// The file, <dir>/events.jsonl, is JSON lines. The first says what the file
// belongs to:
//
//	{"format":1,"chain_id":…,"controller":"0x…","first_block":…}
//
// Then each event the index knows, decoded, with the field names its
// interface declares, and after each page a checkpoint:
//
//	{"block":…,"tx":"0x…","log":…,"address":"0x…","event":"FeeSettled","round":"…","node":"0x…",…}
//	{"through":N,"hash":"0x…"}
//
// A page and its checkpoint are written in one write and synced. On open
// the file is read to its last checkpoint and anything after it (a page cut
// short by a kill) is dropped. A lock file in the folder refuses a second
// reader. A file that belongs to another chain, Controller or first block
// is refused, never rebuilt.
package index

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math/big"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"syscall"

	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/common/hexutil"
	"github.com/ethereum/go-ethereum/core/types"
	"github.com/ethereum/go-ethereum/rpc"
)

// Format is the file's format; a reader refuses another.
const Format = 1

// FileName is the index's file in its folder.
const FileName = "events.jsonl"

// Header is the file's first line: what it belongs to.
type Header struct {
	Format     int            `json:"format"`
	ChainID    uint64         `json:"chain_id"`
	Controller common.Address `json:"controller"`
	FirstBlock uint64         `json:"first_block"`
}

// checkpoint is the line after each page: the last block read and its hash.
type checkpoint struct {
	Through *uint64      `json:"through"`
	Hash    *common.Hash `json:"hash"`
}

// Contracts are the addresses whose events the index reads, besides the
// buyback executors it finds itself.
type Contracts struct {
	Registry, Broker, Claims, Clock, Parameters common.Address
}

// Index is the events of one deployment and their sums.
type Index struct {
	path       string
	file, lock *os.File
	header     *Header
	headerEnd  int64 // the file's length with its first line alone

	totals  *Totals
	through *uint64 // the last checkpoint's block, nil before the first
	hash    common.Hash
	resumed *uint64 // the first block read after a start from a checkpoint

	first     uint64
	deployed  *common.Address // the deployment's executor, read once
	rollbacks int
	// PageBlocks is the most blocks one eth_getLogs asks for. An answer
	// that says the range or the result is too large halves it, down to 1.
	PageBlocks uint64
}

// ErrDeployment is a file that belongs to another deployment; ErrFormat
// one in a format this reader does not read.
var (
	ErrDeployment = errors.New("the index belongs to another deployment")
	ErrFormat     = errors.New("the index is in another format")
)

// Open opens the index in dir, creating the folder and the file when
// missing, takes its lock, and reads the file to its last checkpoint,
// dropping what follows it. first is the deployment's first block.
func Open(dir string, first, pageBlocks uint64) (*Index, error) {
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return nil, err
	}
	lock, err := os.OpenFile(filepath.Join(dir, "lock"), os.O_CREATE|os.O_RDWR, 0o644)
	if err != nil {
		return nil, err
	}
	if err := syscall.Flock(int(lock.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		lock.Close()
		return nil, fmt.Errorf("another reader is using the index in %s: %w", dir, err)
	}
	path := filepath.Join(dir, FileName)
	f, err := os.OpenFile(path, os.O_CREATE|os.O_RDWR, 0o644)
	if err != nil {
		lock.Close()
		return nil, err
	}
	ix := &Index{path: path, file: f, lock: lock, first: first, PageBlocks: max(pageBlocks, 1)}
	if err := ix.load(); err != nil {
		ix.Close()
		return nil, err
	}
	return ix, nil
}

// Close releases the file and the lock.
func (ix *Index) Close() {
	ix.file.Close()
	ix.lock.Close()
}

// load reads the file: its header, then each page up to the last
// checkpoint. A line that cannot be read before the last checkpoint is an
// error; what follows the last checkpoint is cut off.
func (ix *Index) load() error {
	ix.totals, ix.through, ix.header, ix.headerEnd = newTotals(), nil, nil, 0
	if _, err := ix.file.Seek(0, io.SeekStart); err != nil {
		return err
	}
	r := bufio.NewReaderSize(ix.file, 1<<20)
	var (
		offset, keep int64
		page         []Event
		bad          error // a line that could not be read: dropped, unless a checkpoint follows it
		n            int
	)
	for {
		line, err := r.ReadBytes('\n')
		if err == io.EOF {
			break // a last line with no end is a write cut short: dropped
		}
		if err != nil {
			return err
		}
		n++
		offset += int64(len(line))
		if ix.header == nil {
			var h Header
			if err := json.Unmarshal(line, &h); err != nil {
				return fmt.Errorf("%s line 1 is not the index's first line: %w", ix.path, err)
			}
			if h.Format != Format {
				return fmt.Errorf("%w: %s is in format %d; this reader reads format %d: use a new folder", ErrFormat, ix.path, h.Format, Format)
			}
			ix.header, ix.headerEnd, keep = &h, offset, offset
			continue
		}
		if bytes.HasPrefix(line, []byte(`{"through"`)) {
			var cp checkpoint
			if err := json.Unmarshal(line, &cp); err != nil || cp.Through == nil || cp.Hash == nil {
				bad = fmt.Errorf("%s line %d is not a checkpoint", ix.path, n)
				continue
			}
			if bad != nil {
				return fmt.Errorf("%s, before its last checkpoint: %w", ix.path, bad)
			}
			for i := range page {
				ix.totals.fold(&page[i])
			}
			page = page[:0]
			through := *cp.Through
			ix.through, ix.hash, keep = &through, *cp.Hash, offset
			continue
		}
		var e Event
		if err := json.Unmarshal(line, &e); err != nil {
			if bad == nil {
				bad = fmt.Errorf("line %d: %w", n, err)
			}
			continue
		}
		page = append(page, e)
	}
	if err := ix.file.Truncate(keep); err != nil {
		return err
	}
	if _, err := ix.file.Seek(keep, io.SeekStart); err != nil {
		return err
	}
	if ix.through != nil {
		from := *ix.through + 1
		ix.resumed = &from
	}
	return nil
}

// Begin writes the file's first line, or refuses a file that belongs to
// another deployment.
func (ix *Index) Begin(h Header) error {
	h.Format = Format
	if ix.header != nil {
		if *ix.header != h {
			return fmt.Errorf("%w: %s holds chain %d, Controller %s from block %d, and this explorer reads chain %d, Controller %s from block %d; give a new folder",
				ErrDeployment, ix.path, ix.header.ChainID, ix.header.Controller.Hex(), ix.header.FirstBlock,
				h.ChainID, h.Controller.Hex(), h.FirstBlock)
		}
		return nil
	}
	line, err := json.Marshal(h)
	if err != nil {
		return err
	}
	if err := ix.write(append(line, '\n')); err != nil {
		return err
	}
	ix.header, ix.headerEnd = &h, int64(len(line)+1)
	return nil
}

func (ix *Index) write(b []byte) error {
	if _, err := ix.file.Write(b); err != nil {
		return err
	}
	return ix.file.Sync()
}

// commit writes a page and its checkpoint in one write, then adds them.
func (ix *Index) commit(events []Event, through uint64, hash common.Hash) error {
	var b bytes.Buffer
	for _, e := range events {
		line, err := json.Marshal(e)
		if err != nil {
			return err
		}
		b.Write(line)
		b.WriteByte('\n')
	}
	fmt.Fprintf(&b, "{\"through\":%d,\"hash\":%q}\n", through, hash.Hex())
	if err := ix.write(b.Bytes()); err != nil {
		return err
	}
	for i := range events {
		ix.totals.fold(&events[i])
	}
	ix.through, ix.hash = &through, hash
	return nil
}

// reset keeps only the file's first line, and everything derived goes:
// the index reads again from the first block, by the same path as a start.
func (ix *Index) reset() error {
	if err := ix.file.Truncate(ix.headerEnd); err != nil {
		return err
	}
	if _, err := ix.file.Seek(ix.headerEnd, io.SeekStart); err != nil {
		return err
	}
	if err := ix.file.Sync(); err != nil {
		return err
	}
	ix.totals, ix.through, ix.resumed, ix.deployed = newTotals(), nil, nil, nil
	ix.rollbacks++
	return nil
}

// Totals is the sums of every event committed. The caller does not
// change them.
func (ix *Index) Totals() *Totals { return ix.totals }

// Through is the last checkpoint: its block and hash, ok false before the
// first page.
func (ix *Index) Through() (block uint64, hash common.Hash, ok bool) {
	if ix.through == nil {
		return 0, common.Hash{}, false
	}
	return *ix.through, ix.hash, true
}

// First is the deployment's first block, where the index starts.
func (ix *Index) First() uint64 { return ix.first }

// ResumedFrom is the first block read after this start, when it started
// from a checkpoint in the file.
func (ix *Index) ResumedFrom() *uint64 { return ix.resumed }

// Watched is every address whose Bought events the index reads.
func (ix *Index) Watched() []common.Address {
	var out []common.Address
	if ix.deployed != nil && *ix.deployed != (common.Address{}) {
		out = append(out, *ix.deployed)
	}
	for a := range ix.totals.Executors {
		if a != (common.Address{}) && !slices.Contains(out, a) {
			out = append(out, a)
		}
	}
	slices.SortFunc(out, func(a, b common.Address) int { return a.Cmp(b) })
	return out
}

// emitters is which contract may emit each event the index knows. Bought
// comes from any executor watched.
func emitters(c Contracts) map[string]common.Address {
	return map[string]common.Address{"NodeRegistered": c.Registry, "Funded": c.Broker, "Released": c.Broker,
		"FeeSettled": c.Broker, "Claimed": c.Claims, "RoundStarted": c.Clock, "Announced": c.Parameters,
		"Withdrawn": c.Parameters}
}

// errTooLarge is an answer that says the range or the result is too
// large; errChanged a chain that changed while a page was read; errBehind
// a provider that does not have the last checkpoint's block yet, as a
// backend of a load-balanced endpoint a block or two behind answers.
var (
	errTooLarge = errors.New("the provider refused the page as too large")
	errChanged  = errors.New("the chain changed while the page was read")
	errRollback = errors.New("the last checkpoint's block is no longer the chain's")
	errBehind   = errors.New("the provider does not have the last checkpoint's block yet")
)

// tooLarge says whether a failed eth_getLogs says that its range or its
// result is too large. An HTTP failure (authentication, 429, refused) or
// an unreachable provider is not.
func tooLarge(err error) bool {
	var h rpc.HTTPError
	if errors.As(err, &h) {
		return false
	}
	var re rpc.Error
	if !errors.As(err, &re) {
		return false
	}
	m := strings.ToLower(re.Error())
	if strings.Contains(m, "rate") {
		return false
	}
	for _, s := range []string{"range", "too large", "too many", "too big", "more than", "exceed"} {
		if strings.Contains(m, s) {
			return true
		}
	}
	return false
}

// CatchUp reads and commits the pages from the block after the last
// checkpoint through head, calling progress after each page committed.
// A page is committed only if its logs and its checkpoint hash come from
// one chain, the one the previous checkpoint is on; when the previous
// checkpoint's block has another hash, the file keeps only its first line
// and the index reads again from the first block. A provider that does not
// have the checkpoint's block yet ends the catch-up with the checkpoint
// kept, to be read again next time.
func (ix *Index) CatchUp(ctx context.Context, src *Source, c Contracts, head uint64, progress func()) error {
	changed := 0
	for {
		if ix.deployed == nil {
			a, err := ix.deploymentExecutor(ctx, src, c)
			if err != nil {
				return err
			}
			ix.deployed = &a
		}
		next := ix.first
		if ix.through != nil {
			next = *ix.through + 1
		}
		if next > head {
			// Nothing to read, and the checkpoint is still checked: a
			// branch of the same length replaces it too. A provider without
			// the checkpoint's block is behind it, or on a shorter branch
			// that is found once it reaches the checkpoint's height.
			if ix.through == nil {
				return nil
			}
			hs, err := hashes(ctx, src, *ix.through)
			if err != nil {
				return err
			}
			if hs[0] == ix.hash || hs[0] == (common.Hash{}) {
				return nil
			}
			if err := ix.reset(); err != nil {
				return err
			}
			continue
		}
		to := min(next+ix.PageBlocks-1, head)
		events, hash, err := ix.readPage(ctx, src, c, next, to)
		switch {
		case errors.Is(err, errTooLarge):
			if ix.PageBlocks == 1 {
				return fmt.Errorf("eth_getLogs of block %d alone: %w", next, err)
			}
			ix.PageBlocks /= 2
			continue
		case errors.Is(err, errRollback):
			if err := ix.reset(); err != nil {
				return err
			}
			continue
		case errors.Is(err, errBehind):
			return nil
		case errors.Is(err, errChanged):
			if changed++; changed == 5 {
				return fmt.Errorf("blocks %d to %d: %w, five times", next, to, err)
			}
			continue
		case err != nil:
			return err
		}
		if err := ix.commit(events, to, hash); err != nil {
			return err
		}
		if progress != nil {
			progress()
		}
	}
}

// Rollbacks counts the times the index read again from the first block.
func (ix *Index) Rollbacks() int { return ix.rollbacks }

// deploymentExecutor is the executor the deployment named for its first
// round: the round clock starts at round 1 (R-18).
func (ix *Index) deploymentExecutor(ctx context.Context, src *Source, c Contracts) (common.Address, error) {
	data, err := ABI.Pack("valuesAt", big.NewInt(GroupExecutor), big.NewInt(1))
	if err != nil {
		panic(err)
	}
	var out hexutil.Bytes
	req := []rpc.BatchElem{{Method: "eth_call", Args: []any{map[string]any{"to": c.Parameters, "data": hexutil.Bytes(data)}, "latest"}, Result: &out}}
	src.Send(ctx, "the deployment's buyback executor", req)
	if req[0].Error != nil {
		return common.Address{}, fmt.Errorf("the deployment's buyback executor: %w", req[0].Error)
	}
	v, err := ABI.Unpack("valuesAt", out)
	if err != nil {
		return common.Address{}, fmt.Errorf("the deployment's buyback executor: %w", err)
	}
	if vs := v[0].([]*big.Int); len(vs) > 0 {
		return common.BigToAddress(vs[0]), nil
	}
	return common.Address{}, nil
}

// header is what the index reads of a block.
type header struct {
	Hash common.Hash `json:"hash"`
}

// hashes reads the hash of each block: the zero hash for a block the
// chain does not have.
func hashes(ctx context.Context, src *Source, blocks ...uint64) ([]common.Hash, error) {
	out := make([]header, len(blocks))
	reqs := make([]rpc.BatchElem, len(blocks))
	for i, b := range blocks {
		reqs[i] = rpc.BatchElem{Method: "eth_getBlockByNumber", Args: []any{hexutil.EncodeUint64(b), false}, Result: &out[i]}
	}
	src.Send(ctx, "the hashes of the index's blocks", reqs)
	hs := make([]common.Hash, len(blocks))
	for i := range reqs {
		if reqs[i].Error != nil {
			return nil, fmt.Errorf("block %d: %w", blocks[i], reqs[i].Error)
		}
		hs[i] = out[i].Hash
	}
	return hs, nil
}

// readPage reads blocks from to to: the hashes of the previous
// checkpoint's block and of the page's last block, the logs, then the two
// hashes again. A page that announces an executor not yet watched is read
// again with it; the executor joins the index's when the page is committed.
func (ix *Index) readPage(ctx context.Context, src *Source, c Contracts, from, to uint64) ([]Event, common.Hash, error) {
	blocks := []uint64{to}
	if ix.through != nil {
		blocks = append(blocks, *ix.through)
	}
	before, err := hashes(ctx, src, blocks...)
	if err != nil {
		return nil, common.Hash{}, err
	}
	if ix.through != nil && before[1] == (common.Hash{}) {
		return nil, common.Hash{}, errBehind
	}
	if ix.through != nil && before[1] != ix.hash {
		return nil, common.Hash{}, errRollback
	}
	if before[0] == (common.Hash{}) {
		return nil, common.Hash{}, fmt.Errorf("block %d: %w", to, errChanged)
	}
	emitter := emitters(c)
	var topics []common.Hash
	for _, ev := range ABI.Events {
		topics = append(topics, ev.ID)
	}
	slices.SortFunc(topics, func(a, b common.Hash) int { return a.Cmp(b) })
	var events []Event
	var found []common.Address // executors this page announces, not yet the index's
	for {
		watched := append(ix.Watched(), found...)
		addresses := append([]common.Address{c.Registry, c.Broker, c.Claims, c.Clock, c.Parameters}, watched...)
		var logs []types.Log
		req := []rpc.BatchElem{{Method: "eth_getLogs", Result: &logs, Args: []any{map[string]any{
			"fromBlock": hexutil.EncodeUint64(from), "toBlock": hexutil.EncodeUint64(to),
			"address": addresses, "topics": [][]common.Hash{topics}}}}}
		src.Send(ctx, "the index's events", req)
		if err := req[0].Error; err != nil {
			if tooLarge(err) {
				return nil, common.Hash{}, errTooLarge
			}
			return nil, common.Hash{}, fmt.Errorf("eth_getLogs from block %d to %d: %w", from, to, err)
		}
		events = events[:0]
		atBlock := map[uint64]common.Hash{to: before[0]}
		announced := false
		for _, l := range logs {
			if l.Removed {
				return nil, common.Hash{}, errChanged
			}
			if l.BlockNumber < from || l.BlockNumber > to {
				return nil, common.Hash{}, fmt.Errorf("eth_getLogs from block %d to %d answered a log of block %d", from, to, l.BlockNumber)
			}
			if h, ok := atBlock[l.BlockNumber]; ok && h != l.BlockHash {
				return nil, common.Hash{}, errChanged
			}
			atBlock[l.BlockNumber] = l.BlockHash
			e, err := decode(l)
			if err != nil {
				return nil, common.Hash{}, err
			}
			if want, ok := emitter[e.Name]; ok && e.Address != want {
				continue // the same event of a contract the index does not read
			}
			if e.Name == "Bought" && !slices.Contains(watched, e.Address) {
				continue
			}
			if e.Name == "Announced" && e.Int("group").Cmp(big.NewInt(GroupExecutor)) == 0 {
				for _, v := range e.Fields["values"].([]*big.Int) {
					if a := common.BigToAddress(v); a != (common.Address{}) && !slices.Contains(watched, a) {
						found = append(found, a)
						announced = true
					}
				}
			}
			events = append(events, e)
		}
		if !announced {
			break
		}
	}
	slices.SortFunc(events, func(a, b Event) int {
		if a.Block != b.Block {
			return int(a.Block) - int(b.Block)
		}
		return int(a.Log) - int(b.Log)
	})
	after, err := hashes(ctx, src, blocks...)
	if err != nil {
		return nil, common.Hash{}, err
	}
	if !slices.Equal(before, after) {
		return nil, common.Hash{}, errChanged
	}
	return events, before[0], nil
}
