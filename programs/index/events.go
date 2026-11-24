package index

import (
	"bytes"
	"encoding/json"
	"fmt"
	"math/big"
	"strings"

	"github.com/ethereum/go-ethereum/accounts/abi"
	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/core/types"
)

// The events the index knows, and the views it reads, as
// contracts/interfaces declares them. A test holds each to its
// declaration there.
const eventsABI = `[
 {"type":"event","name":"NodeRegistered","anonymous":false,"inputs":[{"name":"node","type":"address","indexed":true},{"name":"round","type":"uint256","indexed":false}]},
 {"type":"event","name":"Funded","anonymous":false,"inputs":[{"name":"payer","type":"address","indexed":true},{"name":"deposit","type":"uint256","indexed":false},{"name":"node","type":"address","indexed":true},{"name":"earmark","type":"uint256","indexed":false}]},
 {"type":"event","name":"Released","anonymous":false,"inputs":[{"name":"payer","type":"address","indexed":true},{"name":"node","type":"address","indexed":true},{"name":"amount","type":"uint256","indexed":false}]},
 {"type":"event","name":"FeeSettled","anonymous":false,"inputs":[{"name":"round","type":"uint256","indexed":true},{"name":"node","type":"address","indexed":true},{"name":"payer","type":"address","indexed":true},{"name":"ticketHash","type":"bytes32","indexed":false},{"name":"gross","type":"uint256","indexed":false},{"name":"nodeAmount","type":"uint256","indexed":false},{"name":"networkAmount","type":"uint256","indexed":false},{"name":"payout","type":"address","indexed":false}]},
 {"type":"event","name":"Claimed","anonymous":false,"inputs":[{"name":"node","type":"address","indexed":true},{"name":"round","type":"uint256","indexed":true},{"name":"payout","type":"address","indexed":false},{"name":"score","type":"uint256","indexed":false},{"name":"paid","type":"uint256","indexed":false},{"name":"cut","type":"uint256","indexed":false},{"name":"pot","type":"uint256","indexed":false}]},
 {"type":"event","name":"RoundStarted","anonymous":false,"inputs":[{"name":"round","type":"uint256","indexed":true},{"name":"startBlock","type":"uint256","indexed":false},{"name":"blockHash","type":"bytes32","indexed":false}]},
 {"type":"event","name":"Announced","anonymous":false,"inputs":[{"name":"group","type":"uint256","indexed":true},{"name":"values","type":"uint256[]","indexed":false},{"name":"countsFrom","type":"uint256","indexed":false}]},
 {"type":"event","name":"Withdrawn","anonymous":false,"inputs":[{"name":"group","type":"uint256","indexed":true},{"name":"countsFrom","type":"uint256","indexed":false}]},
 {"type":"event","name":"Bought","anonymous":false,"inputs":[{"name":"round","type":"uint256","indexed":true},{"name":"held","type":"uint256","indexed":false},{"name":"usdcIn","type":"uint256","indexed":false},{"name":"lptBought","type":"uint256","indexed":false},{"name":"burned","type":"uint256","indexed":false},{"name":"twapX112","type":"uint256","indexed":false},{"name":"maxIn","type":"uint256","indexed":false}]},
 {"type":"function","name":"valuesAt","stateMutability":"view","inputs":[{"name":"group","type":"uint256"},{"name":"round","type":"uint256"}],"outputs":[{"name":"","type":"uint256[]"}]}
]`

// ABI is the events the index knows and the one view it reads.
var ABI = func() abi.ABI {
	a, err := abi.JSON(strings.NewReader(eventsABI))
	if err != nil {
		panic(err)
	}
	return a
}()

// GroupExecutor is R-25's group that names the buyback executor.
const GroupExecutor = 10

// Event is one event the index knows: where it is on the chain, who
// emitted it, its name, and its fields by the names its interface
// declares: a uint256 as *big.Int, an address as common.Address, a
// bytes32 as common.Hash and a uint256[] as []*big.Int.
type Event struct {
	Block   uint64
	Tx      common.Hash
	Log     uint
	Address common.Address
	Name    string
	Fields  map[string]any
}

// key is what makes an event one: its block, transaction and log index.
type key struct {
	block uint64
	tx    common.Hash
	log   uint
}

func (e *Event) key() key { return key{e.Block, e.Tx, e.Log} }

// Int is the uint256 field name.
func (e *Event) Int(name string) *big.Int { return e.Fields[name].(*big.Int) }

// Addr is the address field name.
func (e *Event) Addr(name string) common.Address { return e.Fields[name].(common.Address) }

// decode reads a log as the event its first topic names.
func decode(l types.Log) (Event, error) {
	if len(l.Topics) == 0 {
		return Event{}, fmt.Errorf("a log with no topic in block %d", l.BlockNumber)
	}
	ev, err := ABI.EventByID(l.Topics[0])
	if err != nil {
		return Event{}, fmt.Errorf("a log of an unknown event in block %d", l.BlockNumber)
	}
	fields := map[string]any{}
	var indexed abi.Arguments
	for _, in := range ev.Inputs {
		if in.Indexed {
			indexed = append(indexed, in)
		}
	}
	if len(l.Topics) != 1+len(indexed) {
		return Event{}, fmt.Errorf("a %s event in block %d has %d topics", ev.Name, l.BlockNumber, len(l.Topics))
	}
	if err := abi.ParseTopicsIntoMap(fields, indexed, l.Topics[1:]); err != nil {
		return Event{}, fmt.Errorf("a %s event in block %d: %w", ev.Name, l.BlockNumber, err)
	}
	if err := ev.Inputs.UnpackIntoMap(fields, l.Data); err != nil {
		return Event{}, fmt.Errorf("a %s event in block %d: %w", ev.Name, l.BlockNumber, err)
	}
	for k, v := range fields {
		if h, ok := v.([32]byte); ok {
			fields[k] = common.Hash(h)
		}
	}
	return Event{Block: l.BlockNumber, Tx: l.TxHash, Log: l.Index, Address: l.Address, Name: ev.Name, Fields: fields}, nil
}

// MarshalJSON writes the event as one line of the index's file: where it
// is, then its fields by name, each number a decimal string.
func (e Event) MarshalJSON() ([]byte, error) {
	var b bytes.Buffer
	fmt.Fprintf(&b, `{"block":%d,"tx":"%s","log":%d,"address":"%s","event":"%s"`, e.Block, e.Tx.Hex(), e.Log, e.Address.Hex(), e.Name)
	for _, in := range ABI.Events[e.Name].Inputs {
		v, err := json.Marshal(text(e.Fields[in.Name]))
		if err != nil {
			return nil, err
		}
		fmt.Fprintf(&b, `,"%s":%s`, in.Name, v)
	}
	b.WriteByte('}')
	return b.Bytes(), nil
}

// text is a field as the file writes it.
func text(v any) any {
	switch x := v.(type) {
	case *big.Int:
		return x.String()
	case common.Address:
		return x.Hex()
	case common.Hash:
		return x.Hex()
	case []*big.Int:
		out := make([]string, len(x))
		for i, n := range x {
			out[i] = n.String()
		}
		return out
	}
	return v
}

// UnmarshalJSON reads a line of the file, refusing a field missing or of
// the wrong type.
func (e *Event) UnmarshalJSON(raw []byte) error {
	var m map[string]json.RawMessage
	if err := json.Unmarshal(raw, &m); err != nil {
		return err
	}
	var head struct {
		Block   *uint64         `json:"block"`
		Tx      *common.Hash    `json:"tx"`
		Log     *uint           `json:"log"`
		Address *common.Address `json:"address"`
		Event   string          `json:"event"`
	}
	if err := json.Unmarshal(raw, &head); err != nil {
		return err
	}
	ev, ok := ABI.Events[head.Event]
	if !ok || head.Block == nil || head.Tx == nil || head.Log == nil || head.Address == nil {
		return fmt.Errorf("not an event the index knows")
	}
	*e = Event{Block: *head.Block, Tx: *head.Tx, Log: *head.Log, Address: *head.Address, Name: ev.Name, Fields: map[string]any{}}
	for _, in := range ev.Inputs {
		f, ok := m[in.Name]
		if !ok {
			return fmt.Errorf("a %s event without %s", ev.Name, in.Name)
		}
		var err error
		switch in.Type.T {
		case abi.UintTy:
			e.Fields[in.Name], err = number(f)
		case abi.AddressTy:
			var a common.Address
			err = json.Unmarshal(f, &a)
			e.Fields[in.Name] = a
		case abi.FixedBytesTy:
			var h common.Hash
			err = json.Unmarshal(f, &h)
			e.Fields[in.Name] = h
		case abi.SliceTy:
			var ss []json.RawMessage
			err = json.Unmarshal(f, &ss)
			list := make([]*big.Int, len(ss))
			for i := range ss {
				if err == nil {
					list[i], err = number(ss[i])
				}
			}
			e.Fields[in.Name] = list
		default:
			err = fmt.Errorf("type %s", in.Type)
		}
		if err != nil {
			return fmt.Errorf("a %s event's %s: %w", ev.Name, in.Name, err)
		}
	}
	return nil
}

func number(raw json.RawMessage) (*big.Int, error) {
	var s string
	if err := json.Unmarshal(raw, &s); err != nil {
		return nil, err
	}
	n, ok := new(big.Int).SetString(s, 10)
	if !ok || n.Sign() < 0 {
		return nil, fmt.Errorf("%q is not a whole number", s)
	}
	return n, nil
}
