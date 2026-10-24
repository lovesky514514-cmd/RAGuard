// 仅供本地试验的模拟工具，不发起真实支付、邮件或外部网络调用。
package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
)

type request struct {
	Tool        string         `json:"tool_id"`
	Intent      string         `json:"intent_key"`
	Idempotency string         `json:"idempotency_key"`
	Fingerprint string         `json:"fingerprint"`
	BusinessKey string         `json:"business_key"`
	Args        map[string]any `json:"args"`
}

type effect struct {
	ID          string
	Fingerprint string
	BusinessKey string
	Result      map[string]any
	Cancelled   bool
	CommittedAt time.Time
}

type sandbox struct {
	mu                 sync.Mutex
	effects            map[string][]effect
	commits            map[string]int
	fault              string
	faultFired         bool
	noReadback         bool
	attempts           int
	readbacks          int
	evidenceMode       string
	evidenceDelayReads int
	evidenceDelayTime  time.Duration
}

func newSandbox(fault string, noReadback bool, evidenceMode ...string) *sandbox {
	mode := "normal"
	if len(evidenceMode) > 0 {
		mode = evidenceMode[0]
	}
	return &sandbox{
		effects: make(map[string][]effect), commits: make(map[string]int),
		fault: fault, noReadback: noReadback, evidenceMode: mode,
	}
}

func respond(w http.ResponseWriter, status int, payload any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(payload)
}

func decode(w http.ResponseWriter, r *http.Request, target any) error {
	r.Body = http.MaxBytesReader(w, r.Body, 8192)
	decoder := json.NewDecoder(r.Body)
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(target); err != nil {
		return err
	}
	var trailing any
	if err := decoder.Decode(&trailing); !errors.Is(err, io.EOF) {
		return fmt.Errorf("unexpected trailing JSON")
	}
	return nil
}

func (s *sandbox) disconnect(w http.ResponseWriter) {
	// 主动断开连接，以区分响应丢失和服务器返回错误。
	if hijacker, ok := w.(http.Hijacker); ok {
		conn, _, err := hijacker.Hijack()
		if err == nil {
			_ = conn.Close()
		}
	}
}

// 注入点与故障行为分离；旧版 before/after 仍可用。
func (s *sandbox) inject(w http.ResponseWriter, tool, phase string) bool {
	if s.faultFired {
		return false
	}
	prefix := tool + ":" + phase
	if s.fault == prefix || s.fault == prefix+"_disconnect" {
		s.faultFired = true
		s.disconnect(w)
		return true
	}
	for _, status := range []struct {
		suffix string
		code   int
	}{
		{"_429", 429}, {"_503", 503},
	} {
		if s.fault == prefix+status.suffix {
			s.faultFired = true
			respond(w, status.code, map[string]any{"error": "injected_failure"})
			return true
		}
	}
	if s.fault == prefix+"_invalid_json" {
		s.faultFired = true
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(200)
		_, _ = w.Write([]byte(`{"ok":`))
		return true
	}
	if s.fault == prefix+"_tamper" {
		s.faultFired = true
		respond(w, 200, map[string]any{"ok": true, "intent_key": "wrong-key", "result": map[string]any{"fake": true}})
		return true
	}
	return false
}

func valid(call request) bool {
	switch call.Tool {
	case "inventory.read":
		sku, ok := call.Args["sku"].(string)
		return ok && sku != ""
	case "orders.create":
		sku, ok := call.Args["sku"].(string)
		quantity, okQty := call.Args["quantity"].(float64)
		return ok && sku != "" && okQty && quantity >= 1 && quantity <= 100 && quantity == float64(int(quantity))
	case "tickets.create", "notifications.send":
		msg, ok := call.Args["message"].(string)
		return ok && len(msg) > 0 && len(msg) <= 500
	default:
		return false
	}
}

// 旧演示兼容模式按 intent 归类；正式试验总是传入独立的业务键。
func businessKey(call request) string {
	if call.BusinessKey != "" {
		return call.BusinessKey
	}
	return call.Tool + ":" + call.Intent
}

func (s *sandbox) execute(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		respond(w, 405, map[string]string{"error": "method"})
		return
	}
	var call request
	if decode(w, r, &call) != nil || call.Intent == "" || call.Fingerprint == "" || call.Args == nil {
		respond(w, 400, map[string]string{"error": "invalid_request"})
		return
	}
	if !valid(call) {
		respond(w, 422, map[string]string{"error": "invalid_arguments"})
		return
	}
	if call.Tool == "orders.create" && call.Idempotency != call.Intent {
		respond(w, 400, map[string]string{"error": "idempotency_required"})
		return
	}

	s.mu.Lock()
	defer s.mu.Unlock()
	s.attempts++
	key := call.Tool + ":" + call.Intent
	// 服务端强制稳定幂等键是安全假设的一部分。
	if call.Tool == "orders.create" && len(s.effects[key]) > 0 {
		first := s.effects[key][0]
		if first.Fingerprint != call.Fingerprint || first.BusinessKey != businessKey(call) {
			respond(w, 409, map[string]string{"error": "key_conflict"})
			return
		}
		respond(w, 200, map[string]any{"ok": true, "intent_key": call.Intent, "result": first.Result})
		return
	}
	if s.inject(w, call.Tool, "before") {
		return
	}
	s.commits[call.Tool]++
	result := map[string]any{"tool_id": call.Tool}
	switch call.Tool {
	case "inventory.read":
		result["sku"] = call.Args["sku"]
		result["in_stock"] = true
	case "orders.create":
		result["order_id"] = fmt.Sprintf("order-%04d", s.commits[call.Tool])
	case "tickets.create":
		result["ticket_id"] = fmt.Sprintf("ticket-%04d", s.commits[call.Tool])
	case "notifications.send":
		result["delivered"] = true
	}
	if call.Tool != "inventory.read" {
		s.effects[key] = append(s.effects[key], effect{
			ID:          fmt.Sprintf("effect-%04d", s.commits[call.Tool]),
			Fingerprint: call.Fingerprint, BusinessKey: businessKey(call), Result: result,
			CommittedAt: time.Now(),
		})
	}
	// 提交后断连用于复现“外部已执行、调用方未知”的核心故障。
	if s.inject(w, call.Tool, "after") {
		return
	}
	respond(w, 200, map[string]any{"ok": true, "intent_key": call.Intent, "result": result})
}

func (s *sandbox) readback(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		respond(w, 405, map[string]string{"error": "method"})
		return
	}
	tool, intent := r.URL.Query().Get("tool_id"), r.URL.Query().Get("intent_key")
	if tool != "orders.create" && tool != "tickets.create" {
		respond(w, 403, map[string]string{"status": "unsupported"})
		return
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	s.readbacks++
	if s.noReadback {
		respond(w, 503, map[string]string{"status": "unavailable"})
		return
	}
	if s.readbacks <= s.evidenceDelayReads {
		respond(w, 200, map[string]string{"status": "unknown"})
		return
	}
	records := s.effects[tool+":"+intent]
	if len(records) != 1 || records[0].Cancelled {
		respond(w, 200, map[string]string{"status": "unknown"})
		return
	}
	// 用墙钟时间注入最终证据延迟，不改变实际提交时间或效果真值。
	if time.Since(records[0].CommittedAt) < s.evidenceDelayTime {
		respond(w, 200, map[string]string{"status": "unknown"})
		return
	}
	proof := map[string]any{
		"status": "confirmed", "intent_key": intent, "tool_id": tool,
		"fingerprint":  records[0].Fingerprint,
		"business_key": records[0].BusinessKey,
		"effect_id":    records[0].ID,
		"authority":    "local-go-sandbox", "authority_epoch": 1,
		"version": 1, "observed_at": float64(time.Now().UnixNano()) / 1e9,
		"final": true, "result": records[0].Result,
	}
	// 错误证据只改变回读元数据，不改变独立的真实效果历史。
	switch s.evidenceMode {
	case "wrong_business":
		proof["business_key"] = "foreign-business"
	case "wrong_epoch":
		proof["authority_epoch"] = 9
	case "wrong_digest":
		proof["fingerprint"] = "foreign-payload"
	case "stale":
		proof["observed_at"] = float64(time.Now().Add(-time.Hour).UnixNano()) / 1e9
	case "not_final":
		proof["final"] = false
	}
	respond(w, 200, proof)
}

func (s *sandbox) compensate(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		respond(w, 405, map[string]string{"error": "method"})
		return
	}
	var call struct {
		Tool     string `json:"tool_id"`
		Intent   string `json:"intent_key"`
		TicketID string `json:"ticket_id"`
	}
	if decode(w, r, &call) != nil || call.Tool != "tickets.create" {
		respond(w, 400, map[string]string{"error": "invalid_compensation"})
		return
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	key := call.Tool + ":" + call.Intent
	records := s.effects[key]
	if len(records) != 1 || records[0].Result["ticket_id"] != call.TicketID || records[0].Cancelled {
		respond(w, 409, map[string]string{"error": "not_compensatable"})
		return
	}
	records[0].Cancelled = true
	s.effects[key] = records
	respond(w, 200, map[string]string{"status": "compensated", "intent_key": call.Intent})
}

func (s *sandbox) counts(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		respond(w, 405, map[string]string{"error": "method"})
		return
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	respond(w, 200, s.commits)
}

// 只有本机研究沙箱暴露真值；网关恢复决策不能调用此接口。
func (s *sandbox) groundTruth(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		respond(w, 405, map[string]string{"error": "method"})
		return
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	events := make([]map[string]any, 0)
	// map 遍历不稳定，排序保证真值输出可复现。
	keys := make([]string, 0, len(s.effects))
	for key := range s.effects {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	for _, key := range keys {
		for _, item := range s.effects[key] {
			events = append(events, map[string]any{
				"kind": "commit", "effect_id": key + ":" + item.ID,
				"business_key": item.BusinessKey,
			})
			if item.Cancelled {
				events = append(events, map[string]any{
					"kind": "compensate", "effect_id": key + ":" + item.ID,
					"business_key": item.BusinessKey,
				})
			}
		}
	}
	respond(w, 200, map[string]any{"events": events, "attempts": s.attempts, "readbacks": s.readbacks, "fault_fired": s.faultFired})
}

func (s *sandbox) routes() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, r *http.Request) {
		respond(w, 200, map[string]string{"status": "ok"})
	})
	mux.HandleFunc("/v1/execute", s.execute)
	mux.HandleFunc("/v1/effects", s.readback)
	mux.HandleFunc("/v1/compensate", s.compensate)
	mux.HandleFunc("/v1/counts", s.counts) // 仅供本地测试的效果计数查询。
	mux.HandleFunc("/v1/lab/effects", s.groundTruth)
	return mux
}

func main() {
	address := os.Getenv("MOCK_ADDR")
	if address == "" {
		address = "127.0.0.1:8181"
	}
	if !strings.HasPrefix(address, "127.0.0.1:") {
		log.Fatal("mock only supports loopback address")
	}
	delay := 0
	if configured := os.Getenv("MOCK_EVIDENCE_DELAY_READS"); configured != "" {
		var err error
		delay, err = strconv.Atoi(configured)
		if err != nil || delay < 0 || delay > 20 {
			log.Fatal("invalid evidence delay read count")
		}
	}
	delayMS := 0
	if configured := os.Getenv("MOCK_EVIDENCE_DELAY_MS"); configured != "" {
		var err error
		delayMS, err = strconv.Atoi(configured)
		if err != nil || delayMS < 0 || delayMS > 2000 {
			log.Fatal("invalid evidence delay milliseconds")
		}
	}
	log.Printf("sandbox at %s", address)
	log.Fatal(http.ListenAndServe(address,
		func() http.Handler {
			s := newSandbox(os.Getenv("MOCK_FAULT_ONCE"), os.Getenv("MOCK_DISABLE_READBACK") == "1", os.Getenv("MOCK_EVIDENCE_MODE"))
			s.evidenceDelayReads = delay
			s.evidenceDelayTime = time.Duration(delayMS) * time.Millisecond
			return s.routes()
		}()))
}
