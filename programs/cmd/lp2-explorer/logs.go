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

// scanner reads three contracts' events, a page at a time with two
// filtered eth_getLogs in one batch: the executor's Bought and the round
// clock's RoundStarted, and the Claimed events of Claims for the node
// alone, filtered by its indexed topic. It reads from the deployment's
// first block once, then only the blocks after the last it read. It keeps
// the latest buys, the latest round start and the node's claims by round,
// and nothing else.
type scanner struct {
	first, next     uint64
	claims, node    common.Address
	clock, executor common.Address
	buys            []Buy // oldest first, at most keepBuys
	start           roundStart
	claimed         map[uint64]*big.Int // the score each claim applied, by round
}

func newScanner(first uint64, claims, node common.Address) *scanner {
	return &scanner{first: first, next: first, claims: claims, node: node, claimed: map[uint64]*big.Int{}}
}

var (
	boughtTopic  = views.Events["Bought"].ID
	startedTopic = views.Events["RoundStarted"].ID
	claimedTopic = views.Events["Claimed"].ID
)

// advance reads the pages from the block after the last one read through
// head, at most maxPages of them. A new executor, or clock, starts the
// scan again from the first block. A page that fails stops the read with
// its error; what was read before it is kept, and the next read resumes
// at that page. It returns an error until the scan has reached head.
func (s *scanner) advance(ctx context.Context, b batcher, head uint64, clock, executor common.Address) error {
	if clock != s.clock || executor != s.executor {
		*s = scanner{first: s.first, next: s.first, claims: s.claims, node: s.node, clock: clock, executor: executor,
			claimed: map[uint64]*big.Int{}}
	}
	for pages := 0; s.next <= head; pages++ {
		if pages == maxPages {
			return fmt.Errorf("catching up: read blocks %d to %d of %d", s.first, s.next-1, head)
		}
		to := min(s.next+pageBlocks-1, head)
		var events, claims []types.Log
		query := func(address []common.Address, topics [][]common.Hash, out *[]types.Log) rpc.BatchElem {
			return rpc.BatchElem{Method: "eth_getLogs", Result: out, Args: []any{map[string]any{
				"fromBlock": hexutil.EncodeUint64(s.next),
				"toBlock":   hexutil.EncodeUint64(to),
				"address":   address,
				"topics":    topics,
			}}}
		}
		req := []rpc.BatchElem{
			query([]common.Address{clock, executor}, [][]common.Hash{{boughtTopic, startedTopic}}, &events),
			query([]common.Address{s.claims}, [][]common.Hash{{claimedTopic}, {common.BytesToHash(s.node.Bytes())}}, &claims),
		}
		send(ctx, b, req)
		for _, r := range req {
			if r.Error != nil {
				return fmt.Errorf("eth_getLogs from block %d: %w", s.next, r.Error)
			}
		}
		for _, l := range append(events, claims...) {
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

// take keeps one log: a buy of the executor, a start of a round, or a
// claim of the node.
func (s *scanner) take(l types.Log) error {
	if l.Removed || len(l.Topics) < 2 {
		return nil
	}
	if l.Address == s.claims && l.Topics[0] == claimedTopic && len(l.Topics) == 3 && l.Topics[1] == common.BytesToHash(s.node.Bytes()) {
		out, err := views.Unpack("Claimed", l.Data)
		if err != nil {
			return fmt.Errorf("a Claimed event in block %d: %w", l.BlockNumber, err)
		}
		s.claimed[new(big.Int).SetBytes(l.Topics[2].Bytes()).Uint64()] = out[1].(*big.Int)
		return nil
	}
	if len(l.Topics) != 2 {
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
