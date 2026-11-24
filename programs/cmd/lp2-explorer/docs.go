package main

import (
	"github.com/ethereum/go-ethereum/common"
)

// Head is how every JSON document starts: the block every figure on it is
// read at and that block's time, the RPC provider's state, the index's
// progress, the scenario the labels file names, and why each figure that
// is null was not read. A figure not read is null, never 0.
type Head struct {
	Block       *uint64  `json:"block"`
	BlockTime   *uint64  `json:"block_time"`
	RPC         RPCState `json:"rpc"`
	Index       Progress `json:"index"`
	Scenario    *string  `json:"scenario"`
	Unavailable []string `json:"unavailable"`
}

// RPCState is the provider by scheme and host only, whether its last
// request was answered (null before the first), the failure's category
// from the first failed request until the next success, and the newest
// block it gave and that block's time.
type RPCState struct {
	Provider        string  `json:"provider"`
	Answering       *bool   `json:"answering"`
	Failure         *string `json:"failure"`
	NewestBlock     *uint64 `json:"newest_block"`
	NewestBlockTime *uint64 `json:"newest_block_time"`
}

// Progress is the index's: the deployment's first block, the last block
// committed, the newest block it is reading toward, the first block read
// after a start from a checkpoint, and whether it has caught up.
type Progress struct {
	FirstBlock  uint64  `json:"first_block"`
	Through     *uint64 `json:"through"`
	Newest      *uint64 `json:"newest"`
	ResumedFrom *uint64 `json:"resumed_from"`
	CaughtUp    bool    `json:"caught_up"`
	PageBlocks  uint64  `json:"page_blocks"`
}

// Named is an address with the name and the simulated mark the labels
// file gives it. Nothing on chain names an account.
type Named struct {
	Address   common.Address `json:"address"`
	Label     *string        `json:"label"`
	Simulated bool           `json:"simulated"`
}

// Flow is fees as settled tickets recorded them: the gross (what a ticket
// paid, which can be below its face), and the node and network amounts.
type Flow struct {
	Fees    *Int `json:"fees_usdc_micro"`
	Node    *Int `json:"node_usdc_micro"`
	Network *Int `json:"network_usdc_micro"`
}

// RoundFlow is a round's fees.
type RoundFlow struct {
	Round uint64 `json:"round"`
	Flow
}

// NamedFlow is the fees between a payer and a node.
type NamedFlow struct {
	Named
	Flow
}

// Buy is one Bought event of the executor in force for its round: its
// round, where it is on the chain, and what it spent, bought and burned.
type Buy struct {
	Round     uint64         `json:"round"`
	Block     uint64         `json:"block"`
	Tx        common.Hash    `json:"tx"`
	Executor  common.Address `json:"executor"`
	USDCIn    *Int           `json:"usdc_in_micro"`
	LPTBought *Int           `json:"lpt_bought_wei"`
	Burned    *Int           `json:"burned_wei"`
}

// ClaimRow is one Claimed event of a node.
type ClaimRow struct {
	Round uint64      `json:"round"`
	Score *Int        `json:"score"`
	Paid  *Int        `json:"paid_wei"`
	Cut   *Int        `json:"cut_wei"`
	Pot   *Int        `json:"pot_wei"`
	Block uint64      `json:"block"`
	Tx    common.Hash `json:"tx"`
}

// NetworkDoc is /network.json.
type NetworkDoc struct {
	Head
	Round           *uint64         `json:"round"`
	RoundStartBlock *uint64         `json:"round_start_block"`
	RoundStartTime  *uint64         `json:"round_start_time"`
	RoundLength     *uint64         `json:"round_length_blocks"`
	FeesByRound     *[]RoundFlow    `json:"fees_by_round"`
	TotalFees       *Int            `json:"total_fees_usdc_micro"`
	TotalStake      *Int            `json:"total_stake_wei"`
	LPTSupply       *Int            `json:"lpt_supply_wei"`
	NetworkHeld     *Int            `json:"network_held_usdc_micro"`
	Executor        *common.Address `json:"executor"`
	Buys            *[]Buy          `json:"buys"`
	Validators      *[]Named        `json:"validators"`
}

// NodeRow is one registered node in /orchestrators.json.
type NodeRow struct {
	Named
	RegisteredRound *uint64 `json:"registered_round"`
	Stake           *Int    `json:"stake_wei"`
	Cut             *Int    `json:"cut_millionths"`
	ScoreNow        *Int    `json:"score_now"`
	FeesThisRound   *Int    `json:"fees_this_round_usdc_micro"`
	Flow
	Claims  int  `json:"claims"`
	LPTPaid *Int `json:"lpt_paid_wei"`
}

// OrchestratorsDoc is /orchestrators.json.
type OrchestratorsDoc struct {
	Head
	Orchestrators *[]NodeRow `json:"orchestrators"`
}

// NodeDoc is /orchestrators/<address>.json: everything the node's page
// shows, as node and testnet, then its fees by payer and by round and its
// claims.
type NodeDoc struct {
	Head
	Label       *string      `json:"label"`
	Simulated   bool         `json:"simulated"`
	Node        Node         `json:"node"`
	Testnet     Testnet      `json:"testnet"`
	FeesByPayer *[]NamedFlow `json:"fees_by_payer"`
	FeesByRound *[]RoundFlow `json:"fees_by_round"`
	Claims      *[]ClaimRow  `json:"claims"`
}

// Node is the node shown.
type Node struct {
	Address    common.Address  `json:"address"`
	Registered *bool           `json:"registered"`
	Operator   *common.Address `json:"operator"`
	Payout     *common.Address `json:"payout"`
	Bond       *Int            `json:"bond_wei"`
	Stake      *Int            `json:"stake_wei"`
	Cut        *Int            `json:"cut_millionths"`
	ScoreNow   *Int            `json:"score_now"`
	PayoutUSDC *Int            `json:"payout_usdc_micro"`
	ETH        *Int            `json:"eth_wei"`
	LowGas     *bool           `json:"low_gas"`
	LowGasWei  *Int            `json:"low_gas_below_wei"`
	Rounds     []Round         `json:"rounds"`
}

// Round is one recent round of the node: its fees and stake as the claim
// reads them, and where its reward stands. A claimed round carries the
// score its claim applied and what it paid; an unclaimed one has no claim
// score, and the score a claim made now would read is the node's ScoreNow.
type Round struct {
	Round          uint64  `json:"round"`
	Fees           *Int    `json:"fees_usdc_micro"`
	Stake          *Int    `json:"stake_wei"`
	State          *string `json:"state"`
	ClaimableFrom  *uint64 `json:"claimable_from"`
	ClaimableUntil *uint64 `json:"claimable_until"`
	Claimed        *bool   `json:"claimed"`
	ClaimScore     *Int    `json:"claim_score"`
	Paid           *Int    `json:"paid_wei"`
	Pot            *Int    `json:"pot_wei"`
	Part           *Int    `json:"part_wei"`
	PartWithdrawn  *bool   `json:"part_withdrawn"`
}

// Testnet is what the node's page shows of the whole deployment.
type Testnet struct {
	Round           *uint64           `json:"round"`
	RoundStartBlock *uint64           `json:"round_start_block"`
	RoundStartTime  *uint64           `json:"round_start_time"`
	TotalFees       *Int              `json:"total_fees_usdc_micro"`
	Validators      *[]common.Address `json:"validators"`
	Executor        *common.Address   `json:"executor"`
	Buys            *[]Buy            `json:"buys"`
}

// AppRow is one payer in /apps.json: its deposit now and the release
// pending from it (earmarkOf(payer, address(0))), what it funded (each
// Funded's deposit and earmark) and had released, and its fees.
type AppRow struct {
	Named
	Deposit        *Int `json:"deposit_usdc_micro"`
	DepositPending *Int `json:"deposit_pending_usdc_micro"`
	Funded         *Int `json:"funded_usdc_micro"`
	Released       *Int `json:"released_usdc_micro"`
	Flow
}

// AppsDoc is /apps.json.
type AppsDoc struct {
	Head
	Apps *[]AppRow `json:"apps"`
}

// AppDoc is /apps/<address>.json: the payer's row, its fees by round, and
// by node.
type AppDoc struct {
	Head
	AppRow
	FeesByRound []RoundFlow `json:"fees_by_round"`
	ByNode      []NamedFlow `json:"by_node"`
}

// The states of a round's reward (R-12).
const (
	stateWaiting   = "waiting"   // before its first claimable round
	stateClaimable = "claimable" // in its window and not claimed
	stateClaimed   = "claimed"
	stateExpired   = "expired" // its window passed unclaimed: never minted
)

// claimWindow is R-12's: round n is claimable in rounds n + d to n + d + 21.
const claimWindow = 21

// recentRounds is how many rounds before the current one a node's page
// lists: with the deployment's reward delay of 7, every round still
// claimable.
const recentRounds = 7 + claimWindow

// rewardState is where round n's reward stands in round now, with d the
// reward delay in force for n: claimable in rounds n + d to n + d + 21
// (R-12), from and until.
func rewardState(n, now, d uint64, claimed bool) (state string, from, until uint64) {
	from, until = n+d, n+d+claimWindow
	switch {
	case claimed:
		return stateClaimed, from, until
	case now < from:
		return stateWaiting, from, until
	case now <= until:
		return stateClaimable, from, until
	}
	return stateExpired, from, until
}
