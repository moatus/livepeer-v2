package chain

import (
	"crypto/tls"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/url"
	"regexp"
	"strings"

	"github.com/ethereum/go-ethereum/rpc"
	"github.com/gorilla/websocket"
)

// Describe is how every program that prints a failure involving the RPC
// provider at rpcURL tells it. What the provider wrote never reaches the
// text: each error the provider's answer, or the lack of one, produced
// becomes a fixed category, which names the provider by its scheme and
// host only:
//
//	the RPC provider <scheme://host> could not be reached
//	the RPC provider <scheme://host> did not answer in time
//	the RPC provider <scheme://host> refused the request (HTTP <status>)
//	the RPC provider <scheme://host> answered with an error (code <JSON-RPC code>)
//	the RPC provider <scheme://host>'s answer could not be read
//
// The text this program wrapped around the error (the step that failed)
// stays, as does an error of its own. Redact then runs over the whole, as a
// second guard.
func Describe(err error, rpcURL string) string {
	if err == nil {
		return ""
	}
	p := "the RPC provider"
	u, perr := url.Parse(rpcURL)
	if perr == nil && u.Host != "" {
		p += " " + u.Scheme + "://" + u.Host
	} else {
		u = &url.URL{}
	}
	return Redact(describe(err, rpcURL, u.Host, p), rpcURL)
}

// describe tells err with each error from the provider replaced by its
// category, keeping the text each wrapper adds before what it wraps.
func describe(err error, rpcURL, host, p string) string {
	if c, ok := category(err, rpcURL, host, p); ok {
		return c
	}
	switch w := err.(type) {
	case interface{ Unwrap() []error }:
		var told, was []string
		for _, e := range w.Unwrap() {
			if e != nil {
				told = append(told, describe(e, rpcURL, host, p))
				was = append(was, e.Error())
			}
		}
		if strings.Join(told, "\n") == strings.Join(was, "\n") {
			return err.Error()
		}
		return strings.Join(told, "\n")
	case interface{ Unwrap() error }:
		inner := w.Unwrap()
		if inner == nil {
			return err.Error()
		}
		told := describe(inner, rpcURL, host, p)
		if told == inner.Error() {
			return err.Error()
		}
		if before, ok := strings.CutSuffix(err.Error(), inner.Error()); ok {
			return before + told
		}
		return told
	}
	return err.Error()
}

var handshakeStatus = regexp.MustCompile(`\(HTTP status (\d{3})\b`)

// category is the fixed text for err itself when the provider produced it.
// It does not look inside err, so that the text a wrapper adds is kept.
func category(err error, rpcURL, host, p string) (string, bool) {
	unreachable := func(timeout bool) (string, bool) {
		if timeout {
			return p + " did not answer in time", true
		}
		return p + " could not be reached", true
	}
	switch e := err.(type) {
	case *url.Error:
		if e.Op == "parse" && e.URL == rpcURL {
			return "the RPC URL could not be read as a URL", true
		}
		if v, perr := url.Parse(e.URL); perr != nil || host == "" || v.Host != host {
			return err.Error(), true // a request to another service
		}
		return unreachable(e.Timeout())
	case rpc.HTTPError:
		return fmt.Sprintf("%s refused the request (HTTP %d)", p, e.StatusCode), true
	case *rpc.HTTPError:
		return fmt.Sprintf("%s refused the request (HTTP %d)", p, e.StatusCode), true
	case *tls.CertificateVerificationError, tls.RecordHeaderError, tls.AlertError:
		return unreachable(false)
	case *json.SyntaxError, *json.UnmarshalTypeError:
		return p + "'s answer could not be read", true
	}
	if e, ok := err.(rpc.Error); ok {
		return fmt.Sprintf("%s answered with an error (code %d)", p, e.ErrorCode()), true
	}
	if err == websocket.ErrBadHandshake || errors.Unwrap(err) == websocket.ErrBadHandshake {
		if m := handshakeStatus.FindStringSubmatch(err.Error()); m != nil {
			return fmt.Sprintf("%s refused the request (HTTP %s)", p, m[1]), true
		}
		return p + " refused the request", true
	}
	if e, ok := err.(net.Error); ok {
		return unreachable(e.Timeout())
	}
	if err == io.EOF || err == io.ErrUnexpectedEOF {
		return p + "'s answer could not be read", true
	}
	return "", false
}
