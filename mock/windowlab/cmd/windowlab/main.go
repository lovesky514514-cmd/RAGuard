// windowlab 是供 Python 配对实验启动的本机受控 HTTP 服务。
// 时间推进与物理真值接口仅属于研究监督器，不得作为 Agent 的证据来源。
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"net"
	"net/http"
	"sync/atomic"

	"raguard-sandbox/windowlab"
)

func main() {
	addr := flag.String("addr", "127.0.0.1:8186", "research loopback listen address")
	ttl := flag.Int64("ttl", 4, "simulated idempotency retention ticks")
	flag.Parse()
	host, _, err := net.SplitHostPort(*addr)
	if err != nil || host != "127.0.0.1" {
		log.Fatal("research server must bind 127.0.0.1")
	}
	var tick atomic.Int64
	server, err := windowlab.NewServer(*ttl, tick.Load)
	if err != nil {
		log.Fatal(err)
	}
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			http.Error(w, "method", http.StatusMethodNotAllowed)
			return
		}
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("ok"))
	})
	mux.HandleFunc("/v1/lab/advance", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "method", http.StatusMethodNotAllowed)
			return
		}
		defer r.Body.Close()
		var data struct {
			Ticks int64 `json:"ticks"`
		}
		if json.NewDecoder(http.MaxBytesReader(w, r.Body, 256)).Decode(&data) != nil || data.Ticks < 0 || data.Ticks > 1000 {
			http.Error(w, "invalid ticks", http.StatusBadRequest)
			return
		}
		next := tick.Add(data.Ticks)
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]int64{"server_tick": next})
	})
	mux.Handle("/v1/", server)
	fmt.Println("windowlab ready", *addr)
	log.Fatal(http.ListenAndServe(*addr, mux))
}
