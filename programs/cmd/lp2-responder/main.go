// lp2-responder is the light responder a node serves on the standing
// testnet in place of a model (D-01): a job is one request and one
// response, so a node needs no heavy compute. The node's orchestrator
// forwards each paid request to it and returns its answer unchanged.
//
// GET /health answers 200, for the orchestrator's health check. Any other
// request is answered 200 with a small JSON object that echoes the path,
// the first bytes of the body and the session the orchestrator gave the
// request. It holds no state and reads no key.
package main

import (
	"encoding/json"
	"flag"
	"io"
	"log"
	"net/http"
	"time"
)

// echoed is the most of a request body the answer echoes.
const echoed = 256

func main() {
	listen := flag.String("listen", "127.0.0.1:9000", "the address to serve on")
	flag.Parse()
	mux := http.NewServeMux()
	mux.HandleFunc("GET /health", func(w http.ResponseWriter, _ *http.Request) {
		w.Write([]byte("ok\n"))
	})
	mux.HandleFunc("/", answer)
	srv := &http.Server{Addr: *listen, Handler: mux, ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout: 10 * time.Second, WriteTimeout: 10 * time.Second}
	log.Printf("lp2-responder: serving on %s", *listen)
	log.Fatal(srv.ListenAndServe())
}

func answer(w http.ResponseWriter, r *http.Request) {
	body, _ := io.ReadAll(io.LimitReader(r.Body, echoed))
	log.Printf("%s %s session %s (%d bytes echoed)", r.Method, r.URL.Path, r.Header.Get("Livepeer-Session-Id"), len(body))
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]any{
		"ok":      true,
		"path":    r.URL.Path,
		"echo":    string(body),
		"session": r.Header.Get("Livepeer-Session-Id"),
		"at":      time.Now().UTC().Format(time.RFC3339Nano),
	})
}
