package chain

import (
	"net/url"
	"sort"
	"strings"
)

// minHidden is the shortest piece of an RPC URL that Redact hides wherever
// it appears. Shorter pieces ("v3", "rpc") are path words, not keys, and
// hiding them everywhere would garble the message; they are still hidden
// as part of the URL.
const minHidden = 4

// Redact hides, in text, what rpcURL may carry as a credential: its user
// information, path, query and fragment. Describe applies it to every
// failure it tells, as a second guard behind the fixed categories. The URL
// itself, as given and as Go writes it, becomes scheme://host/…; then each piece of
// those parts (the user name and the password, each path segment, each
// query name and value, the fragment), raw or decoded, becomes "…"
// wherever else it appears, so that a provider that answers a rejected
// request by echoing its key does not reveal it either. The host stays, to
// say which endpoint failed.
func Redact(text, rpcURL string) string {
	if rpcURL == "" {
		return text
	}
	u, err := url.Parse(rpcURL)
	if err != nil || u.Host == "" {
		text = strings.ReplaceAll(text, rpcURL, "…")
		return hide(text, strings.FieldsFunc(rpcURL, func(r rune) bool { return strings.ContainsRune("/?#&=;:@ ", r) }), nil)
	}
	short := u.Scheme + "://" + u.Host + "/…"
	wholes := []string{rpcURL, u.String(), u.Redacted()}
	if _, ok := u.User.Password(); ok {
		// as net/http's errors quote it
		starred := url.User(u.User.Username()).String() + ":***@"
		wholes = append(wholes, strings.Replace(u.String(), u.User.String()+"@", starred, 1))
	}
	for _, whole := range wholes {
		text = strings.ReplaceAll(text, whole, short)
	}
	var pieces []string
	if u.User != nil {
		pw, _ := u.User.Password()
		pieces = append(pieces, u.User.String(), u.User.Username(), pw)
	}
	pieces = append(pieces, u.EscapedPath(), u.Path, u.RawQuery, u.Fragment, u.EscapedFragment())
	pieces = append(pieces, strings.Split(u.EscapedPath(), "/")...)
	pieces = append(pieces, strings.Split(u.Path, "/")...)
	pieces = append(pieces, strings.FieldsFunc(u.RawQuery, func(r rune) bool { return r == '&' || r == '=' || r == ';' })...)
	for name, values := range u.Query() {
		pieces = append(pieces, name)
		pieces = append(pieces, values...)
	}
	return hide(text, pieces, []string{u.Host, u.Hostname()})
}

// hide replaces each piece of minHidden characters or more with "…",
// longest first, leaving each of keep as it is.
func hide(text string, pieces, keep []string) string {
	sort.Slice(pieces, func(i, j int) bool { return len(pieces[i]) > len(pieces[j]) })
	held := make([]string, 0, len(keep))
	for i, k := range keep {
		if k == "" {
			continue
		}
		mark := "\x00" + string(rune('A'+i)) + "\x00"
		text = strings.ReplaceAll(text, k, mark)
		held = append(held, mark, k)
	}
	for _, p := range pieces {
		if len(p) >= minHidden {
			text = strings.ReplaceAll(text, p, "…")
		}
	}
	return strings.NewReplacer(held...).Replace(text)
}
