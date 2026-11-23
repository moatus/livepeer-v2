package main

import (
	"context"
	"fmt"
	"math/big"

	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/common/hexutil"
	"github.com/ethereum/go-ethereum/core/types"
	"github.com/ethereum/go-ethereum/rpc"
)

// pageBlocks is the most blocks one eth_getLogs asks for: hosted endpoints
// cap the range.
const pageBlocks = 5000

// maxPages is the most pages one read asks for. A scan further behind
// than that catches up over several reads, and until it has, the buys are
// shown as unavailable.
const maxPages = 50

// keepBuys is how many of the latest buys the page shows.
const keepBuys = 5

// Buy is one Bought event of the executor in force: its round, where it
// is on the chain, and what it spent, bought and burned.
type Buy struct {
	Round     uint64      `json:"round"`
	Block     uint64      `json:"block"`
	Tx        common.Hash `json:"tx"`
	USDCIn    *Int        `json:"usdc_in_micro"`
	LPTBought *Int        `json:"lpt_bought_wei"`
	Burned    *Int        `json:"burned_wei"`
}

// roundStart is the latest RoundStarted event: its round, the block it
// is in and that block's time.
type roundStart struct {
	round, block, time uint64
}

// scanner reads two contracts' events with one filtered eth_getLogs a
// page: the executor's Bought and the round clock's RoundStarted. It reads
// from the deployment's first block once, then only the blocks after the
// last it read. It keeps the latest buys and round start and nothing else.
type scanner struct {
	first, next     uint64
	clock, executor common.Address
	buys            []Buy // oldest first, at most keepBuys
	start           roundStart
}

func newScanner(first uint64) *scanner {
	return &scanner{first: first, next: first}
}

var (
	boughtTopic  = views.Events["Bought"].ID
	startedTopic = views.Events["RoundStarted"].ID
)

// advance reads the pages from the block after the last one read through
// head, at most maxPages of them. A new executor, or clock, starts the
// scan again from the first block. A page that fails stops the read with
// its error; what was read before it is kept, and the next read resumes
// at that page. It returns an error until the scan has reached head.
func (s *scanner) advance(ctx context.Context, b batcher, head uint64, clock, executor common.Address) error {
	if clock != s.clock || executor != s.executor {
		*s = scanner{first: s.first, next: s.first, clock: clock, executor: executor}
	}
	for pages := 0; s.next <= head; pages++ {
		if pages == maxPages {
			return fmt.Errorf("catching up: read blocks %d to %d of %d", s.first, s.next-1, head)
		}
		to := min(s.next+pageBlocks-1, head)
		var logs []types.Log
		req := []rpc.BatchElem{{Method: "eth_getLogs", Result: &logs, Args: []any{map[string]any{
			"fromBlock": hexutil.EncodeUint64(s.next),
			"toBlock":   hexutil.EncodeUint64(to),
			"address":   []common.Address{clock, executor},
			"topics":    [][]common.Hash{{boughtTopic, startedTopic}},
		}}}}
		send(ctx, b, req)
		if req[0].Error != nil {
			return fmt.Errorf("eth_getLogs from block %d: %w", s.next, req[0].Error)
		}
		for _, l := range logs {
			if err := s.take(l); err != nil {
				return err
			}
		}
		s.next = to + 1
	}
	if s.start.block != 0 && s.start.time == 0 {
		var header struct {
			Time hexutil.Uint64 `json:"timestamp"`
		}
		req := []rpc.BatchElem{{Method: "eth_getBlockByNumber", Args: []any{hexutil.EncodeUint64(s.start.block), false}, Result: &header}}
		send(ctx, b, req)
		if req[0].Error != nil {
			return fmt.Errorf("block %d's time: %w", s.start.block, req[0].Error)
		}
		s.start.time = uint64(header.Time)
	}
	return nil
}

// take keeps one log: a buy of the executor, or a start of a round.
func (s *scanner) take(l types.Log) error {
	if len(l.Topics) != 2 || l.Removed {
		return nil
	}
	round := new(big.Int).SetBytes(l.Topics[1].Bytes()).Uint64()
	switch {
	case l.Address == s.executor && l.Topics[0] == boughtTopic:
		out, err := views.Unpack("Bought", l.Data)
		if err != nil {
			return fmt.Errorf("a Bought event in block %d: %w", l.BlockNumber, err)
		}
		s.buys = append(s.buys, Buy{Round: round, Block: l.BlockNumber, Tx: l.TxHash,
			USDCIn: (*Int)(out[1].(*big.Int)), LPTBought: (*Int)(out[2].(*big.Int)), Burned: (*Int)(out[3].(*big.Int))})
		if len(s.buys) > keepBuys {
			s.buys = s.buys[len(s.buys)-keepBuys:]
		}
	case l.Address == s.clock && l.Topics[0] == startedTopic:
		if round >= s.start.round {
			s.start = roundStart{round: round, block: l.BlockNumber}
		}
	}
	return nil
}

// recent is the buys kept, newest first.
func (s *scanner) recent() []Buy {
	out := make([]Buy, len(s.buys))
	for i, b := range s.buys {
		out[len(s.buys)-1-i] = b
	}
	return out
}
