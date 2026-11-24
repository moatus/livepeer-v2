package main

import (
	"bytes"
	"crypto/sha256"
	"embed"
	"fmt"
	"html/template"
	"io"
	"math"
	"math/big"
	"net/http"
	"slices"
	"strings"
	"time"

	"github.com/ethereum/go-ethereum/common"
)

// refreshSeconds is how often the browser loads a page again.
const refreshSeconds = 15

// rpcHelp is where the banner sends an operator whose provider fails: the
// instructions for getting an RPC URL that works.
const rpcHelp = "“Getting an RPC URL” in the container's README (programs/container/README.md)"

// The pages, their stylesheet and their templates are in the binary: a
// page loads nothing from elsewhere, and the explorer reads no file to
// serve one.
//
//go:embed assets
var assets embed.FS

var stylesheet = func() []byte {
	b, err := assets.ReadFile("assets/style.css")
	if err != nil {
		panic(err)
	}
	return b
}()

var stylesheetTag = fmt.Sprintf(`"%x"`, sha256.Sum256(stylesheet))

// What a figure not read shows: before the first snapshot, while the index
// catches up, or not read since; never 0.
const (
	unavailable = "unavailable"
	catchingUp  = "catching up"
	notRead     = "not read yet"
)

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

// scorePercent writes a score, out of 10^6, as a percentage: 1000000 is 100%.
func scorePercent(n *Int) string {
	return units(new(big.Int).Mul(n.big(), big.NewInt(100)), 6) + "%"
}

// short is an address as its first and last four hex digits.
func short(a common.Address) string {
	h := a.Hex()
	return h[:6] + "…" + h[len(h)-4:]
}

var funcs = template.FuncMap{
	"refresh": func() int { return refreshSeconds },
	"short":   short,
	"tx": func(h common.Hash) string {
		x := h.Hex()
		return x[:10] + "…" + x[len(x)-8:]
	},
	"str": func(s *string) string {
		if s == nil {
			return ""
		}
		return *s
	},
	"low":     func(b *bool) bool { return b != nil && *b },
	"claimed": func(r Round) bool { return r.Claimed != nil && *r.Claimed },
	"ring":    ring,
	"bars":    bars,
}

// ring is the round clock: a circle whose arc is how far through the
// round the page's block is, drawn on the server.
func ring(size, stroke int, progress float64) template.HTML {
	progress = math.Max(0, math.Min(1, progress))
	r := float64(size-stroke) / 2
	c := 2 * math.Pi * r
	o := float64(size) / 2
	// Round ends add half the stroke at each end, so a short arc would draw
	// as a blob: square ends until the arc is long enough.
	cap := "butt"
	if c*progress > float64(2*stroke) {
		cap = "round"
	}
	return template.HTML(fmt.Sprintf(`<svg class="ring" width="%d" height="%d" viewBox="0 0 %d %d" aria-hidden="true">`+
		`<circle class="track" cx="%g" cy="%g" r="%.2f" fill="none" stroke-width="%d"/>`+
		`<circle class="arc" cx="%g" cy="%g" r="%.2f" fill="none" stroke-width="%d" stroke-linecap="%s" stroke-dasharray="%.2f" stroke-dashoffset="%.2f" transform="rotate(-90 %g %g)"/></svg>`,
		size, size, size, size, o, o, r, stroke, o, o, r, stroke, cap, c, c*(1-progress), o, o))
}

// bars is a small chart of fees per round, oldest first, drawn on the
// server; each bar names its round and figure.
func bars(rows []RoundFlow) template.HTML {
	if len(rows) == 0 {
		return ""
	}
	top := new(big.Int)
	for _, r := range rows {
		if r.Fees != nil && r.Fees.big().Cmp(top) > 0 {
			top = r.Fees.big()
		}
	}
	var b strings.Builder
	fmt.Fprintf(&b, `<svg class="chart" viewBox="0 0 %d 100" preserveAspectRatio="none" role="img" aria-label="Fees per round">`, len(rows)*10)
	for i, r := range rows {
		h := 0.0
		if r.Fees != nil && top.Sign() > 0 {
			h, _ = new(big.Rat).SetFrac(new(big.Int).Mul(r.Fees.big(), big.NewInt(100)), top).Float64()
		}
		fmt.Fprintf(&b, `<rect x="%d" y="%.2f" width="8" height="%.2f"><title>Round %d: %s USDC</title></rect>`,
			i*10+1, 100-h, h, r.Round, units(r.Fees.big(), 6))
	}
	b.WriteString(`</svg>`)
	return template.HTML(b.String())
}

// The pages, each its content in the frame every page shares.
var pages = func() map[string]*template.Template {
	m := map[string]*template.Template{}
	for _, name := range []string{"network", "orchestrators", "node", "apps", "app", "missing"} {
		m[name] = template.Must(template.New(name).Funcs(funcs).ParseFS(assets, "assets/frame.html", "assets/"+name+".html"))
	}
	return m
}()

// crumb is one step of the path the page's header shows.
type crumb struct{ Label, Href string }

// frame is what every page shows around its content: the navigation, the
// provider's state, the index's progress, the round clock, the block every
// figure is read at, the scenario, and, while the provider fails, the
// banner. Its methods write figures, a figure not read as Missing.
type frame struct {
	Title   string
	Nav     string // the navigation entry the page is under
	Crumbs  []crumb
	JSON    string // the page's JSON
	Head    Head
	Net     *NetworkDoc     // the snapshot's, for the round clock; nil before the first
	Mine    *common.Address // the node / opens on
	Missing string
	Now     time.Time
}

func (f frame) figure(n *Int, decimals int, unit string) string {
	if n == nil {
		return f.Missing
	}
	return units(n.big(), decimals) + unit
}

func (f frame) USDC(n *Int) string { return f.figure(n, 6, " USDC") }
func (f frame) LPT(n *Int) string  { return f.figure(n, 18, " LPT") }
func (f frame) ETH(n *Int) string  { return f.figure(n, 18, " ETH") }

// Cut is a reward cut, in millionths, as a percentage.
func (f frame) Cut(n *Int) string { return f.figure(n, 4, "%") }

func (f frame) Score(n *Int) string {
	if n == nil {
		return f.Missing
	}
	return scorePercent(n)
}

func (f frame) Addr(a *common.Address) string {
	if a == nil {
		return f.Missing
	}
	return a.Hex()
}

func (f frame) Num(n *uint64) string {
	if n == nil {
		return f.Missing
	}
	return fmt.Sprint(*n)
}

func (f frame) Yes(b *bool) string {
	switch {
	case b == nil:
		return f.Missing
	case *b:
		return "yes"
	}
	return "no"
}

func (f frame) When(t *uint64) string {
	if t == nil {
		return f.Missing
	}
	return time.Unix(int64(*t), 0).UTC().Format("2006-01-02 15:04:05 UTC")
}

// Ago is how long before the page a block's time is.
func (f frame) Ago(t *uint64) string {
	if t == nil {
		return f.Missing
	}
	d := f.Now.Sub(time.Unix(int64(*t), 0)).Round(time.Second)
	switch {
	case d < 0:
		return "just now"
	case d < time.Minute:
		return fmt.Sprintf("%d s ago", int(d.Seconds()))
	case d < time.Hour:
		return fmt.Sprintf("%d min ago", int(d.Minutes()))
	case d < 48*time.Hour:
		return fmt.Sprintf("%.1f h ago", d.Hours())
	}
	return fmt.Sprintf("%d days ago", int(d.Hours()/24))
}

// Failure is the provider's failure, by its category; empty while it
// answers.
func (f frame) Failure() string {
	if f.Head.RPC.Failure == nil {
		return ""
	}
	return *f.Head.RPC.Failure
}

func (f frame) Help() string { return rpcHelp }

// Answering is the provider's state: "ok", "bad" or "" before its first
// answer.
func (f frame) Answering() string {
	switch a := f.Head.RPC.Answering; {
	case a == nil:
		return ""
	case *a:
		return "ok"
	}
	return "bad"
}

// Progress is the index's progress: "block X of Y" while it catches up.
func (f frame) Progress() string {
	ix := f.Head.Index
	x := ix.FirstBlock
	if ix.Through != nil {
		x = *ix.Through
	}
	switch {
	case ix.Newest == nil:
		return fmt.Sprintf("from block %d, not started", ix.FirstBlock)
	case ix.CaughtUp:
		return fmt.Sprintf("caught up at block %d", x)
	}
	return fmt.Sprintf("block %d of %d", x, *ix.Newest)
}

// clock is how far through its round the page's block is.
type clock struct {
	Round, Into, Length uint64
	Progress            float64
}

func (f frame) Clock() *clock {
	n := f.Net
	if n == nil || n.Round == nil || n.RoundStartBlock == nil || n.RoundLength == nil || *n.RoundLength == 0 ||
		f.Head.Block == nil || *f.Head.Block < *n.RoundStartBlock {
		return nil
	}
	into := *f.Head.Block - *n.RoundStartBlock
	return &clock{Round: *n.Round, Into: into, Length: *n.RoundLength, Progress: float64(into) / float64(*n.RoundLength)}
}

// State is where a round's reward stands.
func (f frame) State(r Round) string {
	if r.State == nil {
		return f.Missing
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
}

// RoundScore is a claimed round's applied score, or for a round still to
// be claimed, the score a claim made now would read, marked as such.
func (f frame) RoundScore(r Round, now *Int) string {
	switch {
	case r.Claimed == nil || r.State == nil:
		return f.Missing
	case *r.Claimed:
		if r.ClaimScore == nil {
			return f.Missing
		}
		return scorePercent(r.ClaimScore)
	case *r.State == stateExpired:
		return ""
	case now == nil:
		return f.Missing + " (now)"
	}
	return scorePercent(now) + " (now)"
}

// Recent is the last n rounds of rows, oldest first, for a chart.
func (f frame) Recent(rows []RoundFlow, n int) []RoundFlow {
	return rows[max(0, len(rows)-n):]
}

// Newest is the last n rounds of rows, newest first, for a table.
func (f frame) Newest(rows []RoundFlow, n int) []RoundFlow {
	out := slices.Clone(f.Recent(rows, n))
	slices.Reverse(out)
	return out
}

// sum is the sum of figures, or nil if one was not read.
func sum(ns ...*Int) *Int {
	s := new(big.Int)
	for _, n := range ns {
		if n == nil {
			return nil
		}
		s.Add(s, n.big())
	}
	return newInt(s)
}

// The pages' own figures, besides the frame.
type networkPage struct {
	frame
	Doc        NetworkDoc
	Fees       []RoundFlow
	Buys       []Buy
	Validators []Named
	Read       bool // the snapshot's lists are there
}

type orchestratorsPage struct {
	frame
	Rows                            []NodeRow
	Read                            bool
	FeesThisRound, FeesAll, LPTPaid *Int
}

type nodePage struct {
	frame
	Doc                             NodeDoc
	Validators                      []Named
	Read                            bool
	ByPayer                         []NamedFlow
	Fees                            []RoundFlow
	Claims                          []ClaimRow
	FeesThisRound, FeesAll, LPTPaid *Int
}

type appsPage struct {
	frame
	Rows                            []AppRow
	Read                            bool
	Deposits, Funded, Fees, Network *Int
}

type appPage struct {
	frame
	Doc  AppDoc
	Read bool
}

type missingPage struct {
	frame
	What string
}

// writePage writes a page.
func writePage(w http.ResponseWriter, status int, name string, data any) {
	var b bytes.Buffer
	if err := render(&b, name, data); err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.WriteHeader(status)
	w.Write(b.Bytes())
}

func render(w io.Writer, name string, data any) error {
	return pages[name].ExecuteTemplate(w, "frame", data)
}

// frame is the frame of a page from what the loop last published, with
// the snapshot it shows.
func (e *explorer) frame(title, nav, json string, crumbs ...crumb) (frame, *snapshot) {
	h, s := e.head()
	f := frame{Title: title, Nav: nav, JSON: json, Crumbs: crumbs, Head: h, Mine: e.cfg.node, Now: e.now(), Missing: unavailable}
	switch {
	case s != nil:
		n := s.network
		f.Net = &n
	case h.Index.Newest != nil && !h.Index.CaughtUp:
		f.Missing = catchingUp
	default:
		f.Missing = notRead
	}
	return f, s
}

func (e *explorer) networkPage(w http.ResponseWriter) {
	f, s := e.frame("Network", "network", "/network.json", crumb{Label: "Network"})
	p := networkPage{frame: f}
	if s != nil {
		p.Doc, p.Read = s.network, true
		if d := s.network.FeesByRound; d != nil {
			p.Fees = *d
		}
		if d := s.network.Buys; d != nil {
			p.Buys = slices.Clone(*d)
			slices.Reverse(p.Buys)
		}
		if d := s.network.Validators; d != nil {
			p.Validators = *d
		}
	}
	writePage(w, http.StatusOK, "network", p)
}

func (e *explorer) orchestratorsPage(w http.ResponseWriter) {
	f, s := e.frame("Orchestrators", "orchestrators", "/orchestrators.json", crumb{Label: "Orchestrators"})
	p := orchestratorsPage{frame: f}
	if s != nil {
		p.Rows, p.Read = s.orchestrators, true
		var this, all, paid []*Int
		for _, r := range s.orchestrators {
			this, all, paid = append(this, r.FeesThisRound), append(all, r.Fees), append(paid, r.LPTPaid)
		}
		p.FeesThisRound, p.FeesAll, p.LPTPaid = sum(this...), sum(all...), sum(paid...)
	}
	writePage(w, http.StatusOK, "orchestrators", p)
}

// nodePage writes a node's page; mine is the page at /.
func (e *explorer) nodePage(w http.ResponseWriter, n common.Address, mine bool) {
	crumbs := []crumb{{Label: "Orchestrators", Href: "/orchestrators"}, {Label: short(n)}}
	nav := "orchestrators"
	if mine {
		crumbs, nav = []crumb{{Label: "Your node"}}, "node"
	}
	f, s := e.frame(short(n), nav, "/orchestrators/"+n.Hex()+".json", crumbs...)
	doc, held := e.nodeDoc(n)
	if !held && !mine && (e.cfg.node == nil || *e.cfg.node != n) {
		e.missing(w, f, "No node is registered at "+n.Hex()+".")
		return
	}
	p := nodePage{frame: f, Doc: *doc, Read: s != nil}
	if v := doc.Testnet.Validators; v != nil {
		named := map[common.Address]Named{}
		if s != nil && s.network.Validators != nil {
			for _, n := range *s.network.Validators {
				named[n.Address] = n
			}
		}
		for _, a := range *v {
			if n, ok := named[a]; ok {
				p.Validators = append(p.Validators, n)
			} else {
				p.Validators = append(p.Validators, e.labels.named(a))
			}
		}
	}
	if doc.Label != nil {
		p.Title = *doc.Label
	}
	if d := doc.FeesByPayer; d != nil {
		p.ByPayer = *d
	}
	if d := doc.FeesByRound; d != nil {
		p.Fees = *d
		var all []*Int
		for _, r := range *d {
			all = append(all, r.Fees)
		}
		p.FeesAll = sum(all...)
	}
	if d := doc.Claims; d != nil {
		p.Claims = slices.Clone(*d)
		slices.Reverse(p.Claims)
		var paid []*Int
		for _, c := range *d {
			paid = append(paid, c.Paid)
		}
		p.LPTPaid = sum(paid...)
	}
	if r := doc.Testnet.Round; r != nil {
		for _, row := range doc.Node.Rounds {
			if row.Round == *r {
				p.FeesThisRound = row.Fees
			}
		}
	}
	writePage(w, http.StatusOK, "node", p)
}

func (e *explorer) appsPage(w http.ResponseWriter) {
	f, s := e.frame("Apps", "apps", "/apps.json", crumb{Label: "Apps"})
	p := appsPage{frame: f}
	if s != nil {
		p.Rows, p.Read = s.apps, true
		var deposits, funded, fees, network []*Int
		for _, r := range s.apps {
			deposits, funded = append(deposits, r.Deposit), append(funded, r.Funded)
			fees, network = append(fees, r.Fees), append(network, r.Network)
		}
		p.Deposits, p.Funded, p.Fees, p.Network = sum(deposits...), sum(funded...), sum(fees...), sum(network...)
	}
	writePage(w, http.StatusOK, "apps", p)
}

func (e *explorer) appPage(w http.ResponseWriter, a common.Address) {
	f, s := e.frame(short(a), "apps", "/apps/"+a.Hex()+".json", crumb{Label: "Apps", Href: "/apps"}, crumb{Label: short(a)})
	p := appPage{frame: f, Doc: AppDoc{AppRow: AppRow{Named: e.labels.named(a)}}}
	if s != nil {
		d, ok := s.payers[a]
		if !ok {
			e.missing(w, f, "No payer has funded or paid from "+a.Hex()+".")
			return
		}
		p.Doc, p.Read = *d, true
	}
	if l := p.Doc.Label; l != nil {
		p.Title = *l
	}
	writePage(w, http.StatusOK, "app", p)
}

func (e *explorer) missing(w http.ResponseWriter, f frame, what string) {
	f.Title = "Not found"
	writePage(w, http.StatusNotFound, "missing", missingPage{frame: f, What: what})
}

// serveStylesheet serves the stylesheet from the binary, unchanged while
// the binary is.
func serveStylesheet(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "text/css; charset=utf-8")
	w.Header().Set("ETag", stylesheetTag)
	w.Header().Set("Cache-Control", "no-cache")
	http.ServeContent(w, r, "style.css", time.Time{}, bytes.NewReader(stylesheet))
}
