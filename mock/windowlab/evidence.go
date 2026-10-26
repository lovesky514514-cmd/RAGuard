package windowlab

import (
 "encoding/json"
 "net/http"
)

// EvidenceReceipt 是实验服务器的正向提交回读，不是加密签名或非执行证明。
type EvidenceReceipt struct {
 Scope string `json:"scope"`
 Key string `json:"key"`
 Fingerprint string `json:"fingerprint"`
 EffectID string `json:"effect_id"`
 ServerTick int64 `json:"server_tick"`
 Status string `json:"status"`
}

// EvidenceHTTP 只回读确实发生的物理效果；未找到不能证明未来不会提交。
func (s *Server) EvidenceHTTP(w http.ResponseWriter, r *http.Request) {
 if r.Method != http.MethodGet {
  http.Error(w, "method_not_allowed", http.StatusMethodNotAllowed)
  return
 }
 query := r.URL.Query()
 scope, key, fingerprint := query.Get("scope"), query.Get("key"), query.Get("fingerprint")
 if scope == "" || key == "" || fingerprint == "" || len(scope)+len(key)+len(fingerprint)>1024 {
  http.Error(w, "invalid_identity", http.StatusBadRequest)
  return
 }
 s.mu.Lock()
 var found *Effect
 // 读取物理日志而非 TTL 缓存，避免缓存过期后错误推断提交不存在。
 for i := len(s.effects)-1; i>=0; i-- {
  effect := s.effects[i]
  if effect.Scope==scope && effect.Key==key && effect.Fingerprint==fingerprint {
   found=&effect
   break
  }
 }
 s.mu.Unlock()
 if found==nil {
  // 404 只是当前快照未找到，不能用于授予重试权限。
  http.Error(w, "not_observed", http.StatusNotFound)
  return
 }
 w.Header().Set("Content-Type", "application/json; charset=utf-8")
 _ = json.NewEncoder(w).Encode(EvidenceReceipt{
  Scope:found.Scope, Key:found.Key, Fingerprint:found.Fingerprint,
  EffectID:found.ID, ServerTick:found.ServerTick, Status:"committed",
 })
}
