package windowlab

import (
 "bytes"
 "encoding/json"
 "net/http"
 "net/http/httptest"
 "sync/atomic"
 "testing"
 "time"
)

func TestLateCommitAfterClientTimeout(t *testing.T) {
 var tick atomic.Int64
 server,err:=NewServer(6,tick.Load)
 if err!=nil {t.Fatal(err)}
 gate:=&HoldGate{}
 if !gate.Arm() || gate.Arm() {t.Fatal("arm must be one-shot")}
 httpServer:=httptest.NewServer(gate.Wrap(server))
 defer httpServer.Close()
 body:=[]byte(`{"scope":"region-a","key":"k","fingerprint":"digest"}`)
 client:=&http.Client{Timeout:40*time.Millisecond}
 result,err:=client.Post(httpServer.URL+"/v1/execute","application/json",bytes.NewReader(body))
 if err==nil {result.Body.Close();t.Fatal("held request must time out")}
 if gate.State().Pending!=1 {t.Fatal("old request not held")}
 tick.Store(3)
 result,err=http.Post(httpServer.URL+"/v1/execute","application/json",bytes.NewReader(body))
 if err!=nil {t.Fatal(err)}
 result.Body.Close()
 if result.StatusCode!=200 {t.Fatal("recovery failed")}
 tick.Store(9)
 if !gate.Release() || gate.Release(){t.Fatal("release must be one-shot")}
 deadline:=time.Now().Add(time.Second)
 for gate.State().Pending!=0 && time.Now().Before(deadline){time.Sleep(time.Millisecond)}
 if gate.State().Pending!=0 {t.Fatal("old request not committed")}
 response,err:=http.Get(httpServer.URL+"/v1/lab/effects")
 if err!=nil {t.Fatal(err)}
 defer response.Body.Close()
 var snap Snapshot
 if err:=json.NewDecoder(response.Body).Decode(&snap);err!=nil{t.Fatal(err)}
 if len(snap.Effects)!=2 || snap.Effects[0].ServerTick!=3 || snap.Effects[1].ServerTick!=9 {
  t.Fatalf("late duplicate missing: %+v",snap)
 }
}

func TestReleaseWithoutHeldRequest(t *testing.T) {
 gate:=&HoldGate{}
 if gate.Release(){t.Fatal("unheld release accepted")}
 if !gate.Arm(){t.Fatal("cannot arm")}
 if gate.Release(){t.Fatal("released before request")}
}
