package windowlab

import (
 "encoding/json"
 "net/http"
 "net/http/httptest"
 "strings"
 "testing"
)

func TestPositiveEvidenceWithoutNonExecutionClaim(t *testing.T) {
 tick:=int64(0)
 server,err:=NewServer(3,func()int64{return tick})
 if err!=nil{t.Fatal(err)}
 query:="/v1/evidence/commit?scope=region-a&key=order-1&fingerprint=digest"
 read:=func() *httptest.ResponseRecorder {
  recorder:=httptest.NewRecorder()
  server.EvidenceHTTP(recorder,httptest.NewRequest(http.MethodGet,query,nil))
  return recorder
 }
 if got:=read().Code;got!=http.StatusNotFound{t.Fatalf("absent read: %d",got)}
 payload:=`{"scope":"region-a","key":"order-1","fingerprint":"digest"}`
 accepted:=httptest.NewRecorder()
 server.ServeHTTP(accepted,httptest.NewRequest(http.MethodPost,"/v1/execute",strings.NewReader(payload)))
 if accepted.Code!=http.StatusOK{t.Fatalf("execute: %d",accepted.Code)}
 tick=7
 result:=read()
 if result.Code!=http.StatusOK{t.Fatalf("committed evidence: %d",result.Code)}
 var proof EvidenceReceipt
 if err:=json.Unmarshal(result.Body.Bytes(),&proof);err!=nil{t.Fatal(err)}
 if proof.Status!="committed" || proof.EffectID=="" || proof.Scope!="region-a" {
  t.Fatalf("wrong evidence: %+v",proof)
 }
 for _,bad:=range []string{
  "/v1/evidence/commit?scope=region-b&key=order-1&fingerprint=digest",
  "/v1/evidence/commit?scope=region-a&key=order-1&fingerprint=other",
 }{
  got:=httptest.NewRecorder()
  server.EvidenceHTTP(got,httptest.NewRequest(http.MethodGet,bad,nil))
  if got.Code!=http.StatusNotFound {t.Errorf("scope/fingerprint leaked: %d",got.Code)}
 }
}
