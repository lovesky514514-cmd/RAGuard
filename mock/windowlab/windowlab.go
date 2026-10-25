// Package windowlab 提供有限时长、特定作用域的幂等键服务端实验模型。
// 此包仅用于隔离研究，不连接真实订单、云资源或支付接口。
package windowlab

import (
	"encoding/json"
	"fmt"
	"net/http"
	"sync"
)

// Clock 返回实验服务端的单调时间片。时钟由服务端持有，客户端无权指定。
type Clock func() int64

type Request struct {
	Scope       string `json:"scope"`
	Key         string `json:"key"`
	Fingerprint string `json:"fingerprint"`
}

type Receipt struct {
	EffectID string `json:"effect_id"`
	Replayed bool   `json:"replayed"`
}

type Effect struct {
	ID          string `json:"effect_id"`
	Scope       string `json:"scope"`
	Key         string `json:"key"`
	Fingerprint string `json:"fingerprint"`
	ServerTick  int64  `json:"server_tick"`
}

type Snapshot struct {
	Attempts int      `json:"attempts"`
	Effects  []Effect `json:"effects"`
}

type entry struct {
	fingerprint string
	effectID    string
	expiryTick  int64
}

// Server 按 scope+key 去重，TTL 到期后允许新物理效果。
// 这里的 TTL 是明确的模拟上界；不能把它当作厂商实际清理规则。
type Server struct {
	mu       sync.Mutex
	now      Clock
	ttlTicks int64
	records  map[string]entry
	effects  []Effect
	attempts int
}

// NewServer 不接受由 HTTP 客户端指定的时间或 TTL，保证物理真值独立。
func NewServer(ttlTicks int64, clock Clock) (*Server, error) {
	if ttlTicks < 1 || clock == nil {
		return nil, fmt.Errorf("invalid research TTL or server clock")
	}
	return &Server{now: clock, ttlTicks: ttlTicks, records: make(map[string]entry)}, nil
}

func replyJSON(w http.ResponseWriter, code int, payload any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(code)
	_ = json.NewEncoder(w).Encode(payload)
}

// ServeHTTP 只暴露实验执行与独立真值接口；研究调用者不得读取真值制定恢复策略。
func (s *Server) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path == "/v1/lab/effects" && r.Method == http.MethodGet {
		s.mu.Lock()
		copyEffects := append([]Effect{}, s.effects...)
		snap := Snapshot{Attempts: s.attempts, Effects: copyEffects}
		s.mu.Unlock()
		replyJSON(w, http.StatusOK, snap)
		return
	}
	if r.URL.Path != "/v1/execute" || r.Method != http.MethodPost {
		replyJSON(w, http.StatusNotFound, map[string]string{"error": "not_found"})
		return
	}
	defer r.Body.Close()
	var input Request
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 4096)).Decode(&input); err != nil ||
		input.Scope == "" || input.Key == "" || input.Fingerprint == "" {
		replyJSON(w, http.StatusBadRequest, map[string]string{"error": "bad_request"})
		return
	}
	s.mu.Lock()
	// 原子判定、重放与记录：并发请求不得抢出两个物理效果。
	s.attempts++
	now := s.now()
	id := input.Scope + "\x00" + input.Key
	previous, ok := s.records[id]
	if ok && now < previous.expiryTick {
		if previous.fingerprint != input.Fingerprint {
			s.mu.Unlock()
			replyJSON(w, http.StatusConflict, map[string]string{"error": "fingerprint_mismatch"})
			return
		}
		receipt := Receipt{EffectID: previous.effectID, Replayed: true}
		s.mu.Unlock()
		replyJSON(w, http.StatusOK, receipt)
		return
	}
	newID := fmt.Sprintf("effect-%d", len(s.effects)+1)
	effect := Effect{ID: newID, Scope: input.Scope, Key: input.Key,
		Fingerprint: input.Fingerprint, ServerTick: now}
	s.effects = append(s.effects, effect)
	s.records[id] = entry{fingerprint: input.Fingerprint, effectID: newID, expiryTick: now + s.ttlTicks}
	s.mu.Unlock()
	replyJSON(w, http.StatusOK, Receipt{EffectID: newID})
}
