// 不确定性研究专用 HTTP 服务：旧请求可在客户端超时后晚提交。
package main

import (
 "encoding/json"
 "flag"
 "log"
 "net"
 "net/http"
 "sync/atomic"

 "raguard-sandbox/windowlab"
)

func send(w http.ResponseWriter, value any) {
 w.Header().Set("Content-Type", "application/json; charset=utf-8")
 _ = json.NewEncoder(w).Encode(value)
}

func handler(ttl int64, mode string) (http.Handler,error) {
 var clock atomic.Int64
 server,err:=windowlab.NewServer(ttl,clock.Load)
 if err!=nil{return nil,err}
 view,err:=windowlab.NewEvidenceView(server,mode)
 if err!=nil{return nil,err}
 gate:=&windowlab.HoldGate{}
 mux:=http.NewServeMux()
 mux.HandleFunc("/healthz",func(w http.ResponseWriter,r *http.Request) {
  if r.Method!=http.MethodGet {http.Error(w,"method",405);return}
  w.WriteHeader(http.StatusOK)
 })
 mux.HandleFunc("/v1/lab/advance",func(w http.ResponseWriter,r *http.Request) {
  if r.Method!=http.MethodPost {http.Error(w,"method",405);return}
  defer r.Body.Close()
  var req struct {Ticks int64 `json:"ticks"`}
  if json.NewDecoder(http.MaxBytesReader(w,r.Body,256)).Decode(&req)!=nil || req.Ticks<0 || req.Ticks>1000 {
   http.Error(w,"invalid ticks",400);return
  }
  send(w,map[string]int64{"server_tick":clock.Add(req.Ticks)})
 })
 mux.HandleFunc("/v1/lab/hold",func(w http.ResponseWriter,r *http.Request) {
  if r.Method!=http.MethodPost {http.Error(w,"method",405);return}
  if !gate.Arm(){http.Error(w,"already armed",409);return}
  send(w,gate.State())
 })
 mux.HandleFunc("/v1/lab/release",func(w http.ResponseWriter,r *http.Request) {
  if r.Method!=http.MethodPost {http.Error(w,"method",405);return}
  if !gate.Release(){http.Error(w,"nothing held",409);return}
  send(w,gate.State())
 })
 mux.HandleFunc("/v1/lab/state",func(w http.ResponseWriter,r *http.Request) {
  if r.Method!=http.MethodGet {http.Error(w,"method",405);return}
  send(w,gate.State())
 })
 mux.HandleFunc("/v1/evidence/commit",server.EvidenceHTTP)
 mux.Handle("/v1/evidence/view",view)
 mux.Handle("/v1/lab/effects",server)
 mux.Handle("/v1/execute",gate.Wrap(server))
 return mux,nil
}

func main() {
 addr:=flag.String("addr","127.0.0.1:8187","loopback address")
 ttl:=flag.Int64("ttl",6,"retention ticks")
 mode:=flag.String("evidence-mode","matched","evidence visibility profile")
 flag.Parse()
 host,_,err:=net.SplitHostPort(*addr)
 if err!=nil || host!="127.0.0.1" {log.Fatal("must bind 127.0.0.1")}
 h,err:=handler(*ttl,*mode)
 if err!=nil {log.Fatal(err)}
 log.Fatal(http.ListenAndServe(*addr,h))
}
