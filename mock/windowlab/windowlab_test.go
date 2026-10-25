package windowlab

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"sync"
	"sync/atomic"
	"testing"
)

func newLab(t *testing.T, clock *atomic.Int64) *httptest.Server {
	t.Helper()
	s, err := NewServer(10, clock.Load)
	if err != nil {
		t.Fatal(err)
	}
	srv := httptest.NewServer(s)
	t.Cleanup(srv.Close)
	return srv
}

func execute(t *testing.T, origin, scope, key, fingerprint string) Receipt {
	t.Helper()
	b, _ := json.Marshal(Request{Scope: scope, Key: key, Fingerprint: fingerprint})
	res, err := http.Post(origin+"/v1/execute", "application/json", bytes.NewReader(b))
	if err != nil {
		t.Fatal(err)
	}
	defer res.Body.Close()
	if res.StatusCode != http.StatusOK {
		t.Fatalf("status %d", res.StatusCode)
	}
	var out Receipt
	if err := json.NewDecoder(res.Body).Decode(&out); err != nil {
		t.Fatal(err)
	}
	return out
}

func snapshot(t *testing.T, origin string) Snapshot {
	t.Helper()
	res, err := http.Get(origin + "/v1/lab/effects")
	if err != nil {
		t.Fatal(err)
	}
	defer res.Body.Close()
	var snap Snapshot
	if err := json.NewDecoder(res.Body).Decode(&snap); err != nil {
		t.Fatal(err)
	}
	return snap
}

func TestSameScopeBeforeExpiryReplaysSinglePhysicalEffect(t *testing.T) {
	var now atomic.Int64
	srv := newLab(t, &now)
	a := execute(t, srv.URL, "zone-a", "key-1", "digest")
	now.Store(9)
	b := execute(t, srv.URL, "zone-a", "key-1", "digest")
	if a.EffectID != b.EffectID || !b.Replayed {
		t.Fatalf("expected replay: %+v %+v", a, b)
	}
	s := snapshot(t, srv.URL)
	if s.Attempts != 2 || len(s.Effects) != 1 {
		t.Fatalf("bad physical truth %+v", s)
	}
}

func TestAtExpirySameKeyCreatesSecondEffect(t *testing.T) {
	var now atomic.Int64
	srv := newLab(t, &now)
	a := execute(t, srv.URL, "zone-a", "key-1", "digest")
	now.Store(10)
	b := execute(t, srv.URL, "zone-a", "key-1", "digest")
	if a.EffectID == b.EffectID || b.Replayed {
		t.Fatalf("expiry must not replay %+v %+v", a, b)
	}
	if len(snapshot(t, srv.URL).Effects) != 2 {
		t.Fatal("missing duplicate physical effects")
	}
}

func TestCrossScopeKeyDoesNotSuppressPhysicalEffect(t *testing.T) {
	var now atomic.Int64
	srv := newLab(t, &now)
	a := execute(t, srv.URL, "region-1", "key-1", "digest")
	b := execute(t, srv.URL, "region-2", "key-1", "digest")
	if a.EffectID == b.EffectID || len(snapshot(t, srv.URL).Effects) != 2 {
		t.Fatal("scope was ignored")
	}
}

func TestSameKeyDifferentFingerprintConflicts(t *testing.T) {
	var now atomic.Int64
	srv := newLab(t, &now)
	execute(t, srv.URL, "zone-a", "key-1", "digest")
	b, _ := json.Marshal(Request{Scope: "zone-a", Key: "key-1", Fingerprint: "changed"})
	res, err := http.Post(srv.URL+"/v1/execute", "application/json", bytes.NewReader(b))
	if err != nil {
		t.Fatal(err)
	}
	defer res.Body.Close()
	if res.StatusCode != http.StatusConflict || len(snapshot(t, srv.URL).Effects) != 1 {
		t.Fatal("fingerprint conflict not enforced")
	}
}

func TestConcurrentSameKeyOnlyOnePhysicalEffect(t *testing.T) {
	var now atomic.Int64
	srv := newLab(t, &now)
	const n = 8
	var wg sync.WaitGroup
	ids := make([]string, n)
	errors := make(chan error, n)
	for i := 0; i < n; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			body, _ := json.Marshal(Request{Scope: "zone-a", Key: "key-1", Fingerprint: "digest"})
			res, err := http.Post(srv.URL+"/v1/execute", "application/json", bytes.NewReader(body))
			if err != nil {
				errors <- err
				return
			}
			defer res.Body.Close()
			var result Receipt
			if res.StatusCode != http.StatusOK {
				errors <- fmt.Errorf("unexpected status %d", res.StatusCode)
				return
			}
			if err := json.NewDecoder(res.Body).Decode(&result); err != nil {
				errors <- err
				return
			}
			ids[i] = result.EffectID
		}(i)
	}
	wg.Wait()
	close(errors)
	for err := range errors {
		t.Error(err)
	}
	if t.Failed() {
		return
	}
	for _, id := range ids {
		if id != ids[0] {
			t.Fatal("concurrent effect duplicated")
		}
	}
	if len(snapshot(t, srv.URL).Effects) != 1 {
		t.Fatal("physical duplicate")
	}
}

func TestInvalidInputsAndSettingsCannotChangeServerClock(t *testing.T) {
	if _, err := NewServer(0, func() int64 { return 0 }); err == nil {
		t.Fatal("invalid TTL")
	}
	if _, err := NewServer(10, nil); err == nil {
		t.Fatal("invalid clock")
	}
	var now atomic.Int64
	srv := newLab(t, &now)
	res, err := http.Post(srv.URL+"/v1/execute", "application/json", bytes.NewBufferString(`{"scope":"","key":"a","fingerprint":"b","now":1000}`))
	if err != nil {
		t.Fatal(err)
	}
	defer res.Body.Close()
	if res.StatusCode != http.StatusBadRequest {
		t.Fatal("invalid scope accepted")
	}
	if snapshot(t, srv.URL).Attempts != 0 {
		t.Fatal("invalid payload counted as attempt")
	}
}
