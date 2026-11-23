package main

import (
	"fmt"
	"html/template"
	"io"
	"math/big"
	"strings"
	"time"

	"github.com/ethereum/go-ethereum/common"
)

// refreshSeconds is how often the browser loads the page again.
const refreshSeconds = 15

// units writes a whole number of base units as a decimal of the token,
// with no trailing zeros: 1500000 micro-USDC is "1.5".
func units(n *big.Int, decimals int) string {
	s := new(big.Int).Abs(n).String()
	if len(s) <= decimals {
		s = strings.Repeat("0", decimals-len(s)+1) + s
	}
	whole, frac := s[:len(s)-decimals], strings.TrimRight(s[len(s)-decimals:], "0")
	if n.Sign() < 0 {
		whole = "-" + whole
	}
	if frac == "" {
		return whole
	}
	return whole + "." + frac
}

const unavailable = "unavailable"

var funcs = template.FuncMap{
	"usdc": func(n *Int) string {
		if n == nil {
			return unavailable
		}
		return units(n.big(), 6) + " USDC"
	},
	"lpt": func(n *Int) string {
		if n == nil {
			return unavailable
		}
		return units(n.big(), 18) + " LPT"
	},
	"eth": func(n *Int) string {
		if n == nil {
			return unavailable
		}
		return units(n.big(), 18) + " ETH"
	},
	// score is out of 10^6: 1000000 is 100%.
	"score": func(n *Int) string {
		if n == nil {
			return unavailable
		}
		return units(new(big.Int).Mul(n.big(), big.NewInt(100)), 6) + "%"
	},
	"addr": func(a *common.Address) string {
		if a == nil {
			return unavailable
		}
		return a.Hex()
	},
	"num": func(n *uint64) string {
		if n == nil {
			return unavailable
		}
		return fmt.Sprint(*n)
	},
	"yes": func(b *bool) string {
		switch {
		case b == nil:
			return unavailable
		case *b:
			return "yes"
		}
		return "no"
	},
	"when": func(t *uint64) string {
		if t == nil {
			return unavailable
		}
		return time.Unix(int64(*t), 0).UTC().Format("2006-01-02 15:04:05 UTC")
	},
	"state": func(r Round) string {
		if r.State == nil {
			return unavailable
		}
		switch *r.State {
		case stateWaiting:
			return fmt.Sprintf("claimable from round %d", *r.ClaimableFrom)
		case stateClaimable:
			return fmt.Sprintf("claimable now, until round %d", *r.ClaimableUntil)
		case stateExpired:
			return fmt.Sprintf("expired after round %d, never minted", *r.ClaimableUntil)
		}
		return "claimed"
	},
	"low":     func(b *bool) bool { return b != nil && *b },
	"claimed": func(r Round) bool { return r.Claimed != nil && *r.Claimed },
	"refresh": func() int { return refreshSeconds },
}

var page = template.Must(template.New("page").Funcs(funcs).Parse(`<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="{{refresh}}">
<title>lp2-explorer: {{.Node.Address.Hex}}</title>
<style>
body { font-family: system-ui, sans-serif; margin: 2em; max-width: 72em; }
table { border-collapse: collapse; margin-bottom: 1.5em; }
th, td { text-align: left; padding: 0.2em 0.8em; border-bottom: 1px solid #ddd; }
td.n { text-align: right; font-variant-numeric: tabular-nums; }
.warn { color: #a00; font-weight: bold; }
.dim { color: #666; }
</style>
</head>
<body>
<h1>Your node and the testnet</h1>
<p class="dim">Read from the chain at block {{num .Block}} ({{when .BlockTime}}). This page loads again every {{refresh}} seconds. The same figures are at <a href="/status.json">/status.json</a>.</p>

<h2>Your node</h2>
<table>
<tr><th>Node</th><td>{{.Node.Address.Hex}}</td></tr>
<tr><th>Registered</th><td>{{yes .Node.Registered}}</td></tr>
<tr><th>Operator key</th><td>{{addr .Node.Operator}}</td></tr>
<tr><th>Payout address</th><td>{{addr .Node.Payout}}</td></tr>
<tr><th>Own bond this round</th><td class="n">{{lpt .Node.Bond}}</td></tr>
<tr><th>Stake this round</th><td class="n">{{lpt .Node.Stake}}</td></tr>
<tr><th>Score a claim made now reads</th><td class="n">{{score .Node.Score}}</td></tr>
<tr><th>Test USDC at the payout address</th><td class="n">{{usdc .Node.PayoutUSDC}}</td></tr>
<tr><th>Node key's ETH, for gas</th><td class="n">{{eth .Node.ETH}}{{if low .Node.LowGas}} <span class="warn">low: under {{eth .Node.LowGasWei}}</span>{{end}}</td></tr>
</table>

<h3>Recent rounds</h3>
<p class="dim">Fees are the USDC fees counted for the node in the round, not requests served. Stake is the stake at the round's start. Paid is what the claim minted, the node's cut and the pot together; part is the node's own part of the pot, as a holder of its own bond.</p>
<table>
<tr><th>Round</th><th>Fees</th><th>Stake</th><th>Reward</th><th>Paid</th><th>Pot</th><th>Your part</th><th>Withdrawn</th></tr>
{{range .Node.Rounds}}<tr><td class="n">{{.Round}}</td><td class="n">{{usdc .Fees}}</td><td class="n">{{lpt .Stake}}</td><td>{{state .}}</td><td class="n">{{if claimed .}}{{lpt .Paid}}{{end}}</td><td class="n">{{if claimed .}}{{lpt .Pot}}{{end}}</td><td class="n">{{if claimed .}}{{lpt .Part}}{{end}}</td><td>{{if claimed .}}{{yes .PartWithdrawn}}{{end}}</td></tr>
{{end}}</table>

<h2>The testnet</h2>
<table>
<tr><th>Current round</th><td>{{num .Testnet.Round}}</td></tr>
<tr><th>It started</th><td>block {{num .Testnet.RoundStartBlock}}, {{when .Testnet.RoundStartTime}}</td></tr>
<tr><th>Total fees this round</th><td class="n">{{usdc .Testnet.TotalFees}}</td></tr>
<tr><th>Validator set</th><td>{{if .Testnet.Validators}}{{range .Testnet.Validators}}{{.Hex}}<br>{{else}}none{{end}}{{else}}unavailable{{end}}</td></tr>
<tr><th>Buyback executor in force</th><td>{{addr .Testnet.Executor}}</td></tr>
</table>

<h3>Latest buys and burns</h3>
{{if .Testnet.Buys}}<table>
<tr><th>Round</th><th>Block</th><th>USDC spent</th><th>LPT bought</th><th>LPT burned</th><th>Transaction</th></tr>
{{range .Testnet.Buys}}<tr><td class="n">{{.Round}}</td><td class="n">{{.Block}}</td><td class="n">{{usdc .USDCIn}}</td><td class="n">{{lpt .LPTBought}}</td><td class="n">{{lpt .Burned}}</td><td>{{.Tx.Hex}}</td></tr>
{{else}}<tr><td colspan="6">none yet</td></tr>
{{end}}</table>
{{else}}<p>unavailable</p>
{{end}}
{{if .Unavailable}}<h2>Not read</h2>
<p>Each figure shown as unavailable could not be read this time; it is read again on the next load.</p>
<ul>{{range .Unavailable}}<li>{{.}}</li>{{end}}</ul>
{{end}}
</body>
</html>
`))

// render writes the page for s.
func render(w io.Writer, s *Status) error {
	return page.Execute(w, s)
}
