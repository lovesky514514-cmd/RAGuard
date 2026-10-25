package windowlab

import (
    "bytes"
    "io"
    "net/http"
    "sync"
)

// HoldGate 让首个 HTTP 写入在服务端排队，研究客户端超时后的晚提交。
// 控制调用只属于实验监督器，不能成为恢复策略的提交证据。
type HoldGate struct {
    mu       sync.Mutex
    armed    bool
    used     bool
    pending  int
    released bool
    signal   chan struct{}
}

// HoldState 是只含调度状态、不包含业务效果的研究观测。
type HoldState struct {
    Armed    bool `json:"armed"`
    Pending  int  `json:"pending"`
    Released bool `json:"released"`
}

func (g *HoldGate) Arm() bool {
    g.mu.Lock()
    defer g.mu.Unlock()
    if g.used {
        return false
    }
    g.armed = true
    g.used = true
    g.signal = make(chan struct{})
    return true
}

func (g *HoldGate) Release() bool {
    g.mu.Lock()
    defer g.mu.Unlock()
    if g.pending != 1 || g.released {
        return false
    }
    g.released = true
    close(g.signal)
    return true
}

func (g *HoldGate) State() HoldState {
    g.mu.Lock()
    defer g.mu.Unlock()
    return HoldState{g.armed, g.pending, g.released}
}

func (g *HoldGate) Wrap(next http.Handler) http.Handler {
    return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        g.mu.Lock()
        if g.armed && r.Method == http.MethodPost && r.URL.Path == "/v1/execute" {
            g.armed = false
            g.pending++
            release := g.signal
            g.mu.Unlock()

            // 先收妥请求体，避免客户端超时关闭连接后旧请求无法被服务端处理。
            payload, err := io.ReadAll(http.MaxBytesReader(w, r.Body, 4096))
            _ = r.Body.Close()
            if err != nil {
                g.mu.Lock()
                g.pending--
                g.mu.Unlock()
                http.Error(w, "invalid request body", http.StatusBadRequest)
                return
            }
            // 不将连接断开等同于提交取消；仅监督器能释放旧请求。
            <-release
            r.Body = io.NopCloser(bytes.NewReader(payload))
            next.ServeHTTP(w, r)

            g.mu.Lock()
            g.pending--
            g.mu.Unlock()
            return
        }
        g.mu.Unlock()
        next.ServeHTTP(w, r)
    })
}
