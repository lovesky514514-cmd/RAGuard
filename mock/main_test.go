package main

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func post(t *testing.T, origin, raw string) int {
	t.Helper()
	resp, err := http.Post(origin+"/v1/execute", "application/json", strings.NewReader(raw))
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	return resp.StatusCode
}

func TestStableIdempotencyKey(t *testing.T) {
	server := httptest.NewServer(newSandbox("", false).routes())
	defer server.Close()
	raw := `{"tool_id":"orders.create","intent_key":"key","idempotency_key":"key","fingerprint":"f","args":{"sku":"s","quantity":1}}`
	if post(t, server.URL, raw) != 200 || post(t, server.URL, raw) != 200 {
		t.Fatal("valid stable-key writes must succeed")
	}
	counts, err := http.Get(server.URL + "/v1/counts")
	if err != nil {
		t.Fatal(err)
	}
	defer counts.Body.Close()
	var value map[string]int
	if err := json.NewDecoder(counts.Body).Decode(&value); err != nil {
		t.Fatal(err)
	}
	if value["orders.create"] != 1 {
		t.Fatalf("duplicated side effect: %v", value)
	}
	modified := strings.Replace(raw, `"fingerprint":"f"`, `"fingerprint":"other"`, 1)
	if post(t, server.URL, modified) != 409 {
		t.Fatal("identity conflict not rejected")
	}
}

func TestReadbackReturnsOnlyMatchingPositiveEvidence(t *testing.T) {
	server := httptest.NewServer(newSandbox("", false).routes())
	defer server.Close()
	raw := `{"tool_id":"tickets.create","intent_key":"i","fingerprint":"f","args":{"message":"hello"}}`
	if post(t, server.URL, raw) != 200 {
		t.Fatal("ticket create failed")
	}
	res, err := http.Get(server.URL + "/v1/effects?tool_id=tickets.create&intent_key=i")
	if err != nil {
		t.Fatal(err)
	}
	defer res.Body.Close()
	var value map[string]any
	if err := json.NewDecoder(res.Body).Decode(&value); err != nil {
		t.Fatal(err)
	}
	if value["status"] != "confirmed" || value["fingerprint"] != "f" {
		t.Fatal(value)
	}
}

// 研究判定器必须记录真实提交，而不是把损坏响应当作未执行。
func TestAfterCommitCorruptionHasGroundTruth(t *testing.T) {
	server := httptest.NewServer(newSandbox("tickets.create:after_invalid_json", false).routes())
	defer server.Close()
	raw := `{"tool_id":"tickets.create","intent_key":"i","fingerprint":"f","args":{"message":"hi"}}`
	if post(t, server.URL, raw) != 200 {
		t.Fatal("expected injected HTTP 200 with bad JSON")
	}
	response, err := http.Get(server.URL + "/v1/lab/effects")
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	var world struct {
		Events []struct {
			Kind     string `json:"kind"`
			EffectID string `json:"effect_id"`
		} `json:"events"`
		FaultFired bool `json:"fault_fired"`
		Attempts   int  `json:"attempts"`
	}
	if err := json.NewDecoder(response.Body).Decode(&world); err != nil {
		t.Fatal(err)
	}
	if !world.FaultFired || world.Attempts != 1 || len(world.Events) != 1 || world.Events[0].Kind != "commit" {
		t.Fatalf("wrong effect truth: %+v", world)
	}
}

func TestBeforeCommit503HasNoSideEffects(t *testing.T) {
	server := httptest.NewServer(newSandbox("tickets.create:before_503", false).routes())
	defer server.Close()
	raw := `{"tool_id":"tickets.create","intent_key":"i","fingerprint":"f","args":{"message":"hi"}}`
	if post(t, server.URL, raw) != 503 {
		t.Fatal("expected injected 503")
	}
	response, err := http.Get(server.URL + "/v1/lab/effects")
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	var world struct {
		Events []any `json:"events"`
	}
	if err := json.NewDecoder(response.Body).Decode(&world); err != nil {
		t.Fatal(err)
	}
	if len(world.Events) != 0 {
		t.Fatal("before-commit fault must not write")
	}
}

// 两笔相同类型的业务写入必须按可信业务键区分，而不是按工具名汇总。
func TestGroundTruthSeparatesBusinessOperations(t *testing.T) {
	server := httptest.NewServer(newSandbox("", false).routes())
	defer server.Close()
	for _, item := range []struct{ intent, business string }{
		{"intent-a", "order:one"}, {"intent-b", "order:one"}, {"intent-c", "order:two"},
	} {
		raw := fmt.Sprintf(`{"tool_id":"tickets.create","intent_key":%q,"fingerprint":"f","business_key":%q,"args":{"message":"test"}}`, item.intent, item.business)
		if post(t, server.URL, raw) != 200 {
			t.Fatal("failed to create", item)
		}
	}
	response, err := http.Get(server.URL + "/v1/lab/effects")
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	var world struct {
		Events []struct {
			BusinessKey string `json:"business_key"`
		} `json:"events"`
	}
	if err := json.NewDecoder(response.Body).Decode(&world); err != nil {
		t.Fatal(err)
	}
	if len(world.Events) != 3 {
		t.Fatal("expected three physical effects")
	}
	count := map[string]int{}
	for _, effect := range world.Events {
		count[effect.BusinessKey]++
	}
	if count["order:one"] != 2 || count["order:two"] != 1 {
		t.Fatal(count)
	}
}

func TestIdempotentConflictCannotRebindBusinessKey(t *testing.T) {
	server := httptest.NewServer(newSandbox("", false).routes())
	defer server.Close()
	one := `{"tool_id":"orders.create","intent_key":"key","idempotency_key":"key","fingerprint":"f","business_key":"order:one","args":{"sku":"s","quantity":1}}`
	if post(t, server.URL, one) != 200 {
		t.Fatal("initial write failed")
	}
	two := strings.Replace(one, `"order:one"`, `"order:two"`, 1)
	if post(t, server.URL, two) != 409 {
		t.Fatal("same idempotency key cannot be rebound to different operation")
	}
}

// 同一真实副作用的证据可见时间与业务提交时间分离。
// 测试直接调整内部提交时间，不依赖操作系统调度或实际 sleep。
func TestEvidenceVisibilityUsesElapsedTime(t *testing.T) {
	s := newSandbox("", false)
	s.evidenceDelayTime = time.Second
	server := httptest.NewServer(s.routes())
	defer server.Close()
	raw := `{"tool_id":"tickets.create","intent_key":"time","fingerprint":"f","args":{"message":"hi"}}`
	if post(t, server.URL, raw) != 200 {
		t.Fatal("effect write failed")
	}
	query := func() string {
		t.Helper()
		r, err := http.Get(server.URL + "/v1/effects?tool_id=tickets.create&intent_key=time")
		if err != nil {
			t.Fatal(err)
		}
		defer r.Body.Close()
		var response map[string]any
		if err := json.NewDecoder(r.Body).Decode(&response); err != nil {
			t.Fatal(err)
		}
		return response["status"].(string)
	}
	if state := query(); state != "unknown" {
		t.Fatalf("early evidence must remain hidden: %s", state)
	}
	s.mu.Lock()
	item := s.effects["tickets.create:time"][0]
	item.CommittedAt = time.Now().Add(-2 * time.Second)
	s.effects["tickets.create:time"][0] = item
	s.mu.Unlock()
	if state := query(); state != "confirmed" {
		t.Fatalf("mature evidence must be visible: %s", state)
	}
	if s.commits["tickets.create"] != 1 {
		t.Fatal("readback modified external effect count")
	}
}
