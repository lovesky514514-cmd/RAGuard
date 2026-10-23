# 网关接口速查

`POST http://127.0.0.1:8088/v1/calls`，JSON 示例：

```json
{
  "tenant": "demo",
  "principal": "researcher",
  "task": "task-001",
  "step": "order",
  "intent": "order-001",
  "tool": "orders.create",
  "args": {"sku": "sku-21", "quantity": 1},
  "dependencies": []
}
```

- `200`：`execute` / `retry` / `replay`，结果已确认或按登记契约返回成功。
- `202`：`wait`，可能仍有执行者；用**同一请求**稍后查询。
- `400/403`：参数不合法或工具/审批不允许。
- `409`：`stop`，冲突或证据不足；**不能生成新意图以绕开阻断**。

模拟副作用类型：`inventory.read` 只读、`orders.create` 稳定幂等写、`tickets.create` 可补偿写、`notifications.send` 不透明写。上游 Go 沙箱仅接受假数据。

## 故障注入

必须在**启动服务前**设置变量 `MOCK_FAULT_ONCE`，可用值示例：

- `orders.create:after`：已创建订单但执行响应断开。
- `orders.create:before`：执行前断开。
- `notifications.send:after`：通知已发生但响应断开，网关必须停止。
- `tickets.create:after`：工单已创建但响应断开，通过正向回读恢复。

`MOCK_DISABLE_READBACK=1` 用于演示证据不可用时的保守停止。每次 Go 沙箱启动都重置内存；需要新实验时，关掉两端服务并同步重置本地测试账本。

## 运行参数

- `RAGUARD_DB`：SQLite 数据路径，默认 `data/ledger.sqlite3`。
- `RAGUARD_PORT`：网关端口，默认 `8088`。
- `RAGUARD_UPSTREAM`：仅允许 `http://127.0.0.1:端口`。
- `RAGUARD_API_KEY`：可选本机请求头 `X-RAGuard-Key`。
- `RAGUARD_APPROVAL_KEY`：开启 `POST /v1/compensate` 手工审批头 `X-RAGuard-Approval`；未设置则禁用补偿路由。
- `RAGUARD_DEMO_TASK`：稳定演示 Task ID；默认每次演示生成新的 Task ID。

只有在操作属于 `tickets.create` 且能确认原副作用、无未解决的下游步骤时才能人工补偿。审批是**人工意图演示**，不是生产级 RBAC。
