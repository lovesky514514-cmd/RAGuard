package windowlab

import (
 "fmt"
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


// EvidenceView 用两种可控可见性协议观察同一份服务端日志。
// 这是仿真的读取层，不代表两个独立权威或可用于重试的非执行证明。
type EvidenceView struct {
    server *Server
    mode string
}

func NewEvidenceView(server *Server, mode string) (*EvidenceView,error) {
    if server==nil || (mode!="matched" && mode!="shifted") {
        return nil, fmt.Errorf("invalid evidence view mode")
    }
    return &EvidenceView{server:server,mode:mode},nil
}

func (view *EvidenceView) ServeHTTP(w http.ResponseWriter,r *http.Request) {
    if r.Method!=http.MethodGet {
        http.Error(w,"method_not_allowed",http.StatusMethodNotAllowed)
        return
    }
    query:=r.URL.Query()
    source:=query.Get("source")
    if source!="cheap" && source!="quick" && source!="audit" {
        http.Error(w,"invalid_source",http.StatusBadRequest)
        return
    }
    scope,key,fingerprint:=query.Get("scope"),query.Get("key"),query.Get("fingerprint")
    if scope=="" || key=="" || fingerprint=="" ||
       len(scope)+len(key)+len(fingerprint)>1024 {
        http.Error(w,"invalid_identity",http.StatusBadRequest)
        return
    }
    delay:=int64(0)
    if (view.mode=="matched" && source=="cheap") ||
       (view.mode=="shifted" && source=="quick") {
        delay=2
    }
    view.server.mu.Lock()
    now:=view.server.now()
    var found *Effect
    for i:=len(view.server.effects)-1;i>=0;i-- {
        effect:=view.server.effects[i]
        if effect.Scope==scope && effect.Key==key && effect.Fingerprint==fingerprint &&
           now>=effect.ServerTick+delay {
            found=&effect
            break
        }
    }
    view.server.mu.Unlock()
    if found==nil {
        http.Error(w,"not_observed",http.StatusNotFound)
        return
    }
    w.Header().Set("Content-Type","application/json; charset=utf-8")
    _=json.NewEncoder(w).Encode(struct {
        EvidenceReceipt
        Source string `json:"source"`
        ObservedAt int64 `json:"observed_at"`
        ValidUntil int64 `json:"valid_until"`
    }{
        EvidenceReceipt: EvidenceReceipt{
            Scope:found.Scope,Key:found.Key,Fingerprint:found.Fingerprint,
            EffectID:found.ID,ServerTick:found.ServerTick,Status:"committed",
        },
        Source:source,ObservedAt:now,ValidUntil:now+1,
    })
}
