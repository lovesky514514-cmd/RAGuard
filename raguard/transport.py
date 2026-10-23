"""仅连接本地 Go 沙箱的 HTTP 适配器，不接受外部目标地址。"""

import json
import urllib.error
import urllib.parse
import urllib.request

from .model import Call
from .policy import ToolPolicy, Effect


class UncertainResult(RuntimeError):
    """响应不可用时，上游操作仍有可能已经提交。"""


class SandboxClient:
    def __init__(self, origin: str = "http://127.0.0.1:8181", timeout: float = 1.5,
                 *, research_business_key: str | None = None):
        parsed = urllib.parse.urlsplit(origin)
        if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1"
                or not parsed.port or parsed.path not in {"", "/"}
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise ValueError("上游必须是 http://127.0.0.1:端口 本地沙箱")
        if timeout <= 0:
            raise ValueError("timeout 必须大于零")
        self.origin = origin.rstrip("/")
        self.timeout = timeout
        self.research_business_key = research_business_key

    def _json(self, path: str, payload: dict | None = None) -> dict:
        body = (json.dumps(payload, ensure_ascii=False).encode("utf-8")
                if payload is not None else None)
        request = urllib.request.Request(self.origin + path, body,
                                         {"Content-Type": "application/json"},
                                         method="POST" if body is not None else "GET")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as reply:
                if reply.status != 200:
                    raise UncertainResult("上游非成功状态")
                # 拒绝超大响应和非结构化数据，防止不受控内容落盘。
                raw = reply.read(65537)
                if len(raw) > 65536:
                    raise UncertainResult("响应过大")
                value = json.loads(raw)
                if not isinstance(value, dict):
                    raise UncertainResult("响应不是对象")
                return value
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise UncertainResult(type(exc).__name__) from exc

    def execute(self, call: Call, policy: ToolPolicy) -> dict:
        payload = {"tool_id": call.tool, "intent_key": call.stable_key,
                   "fingerprint": call.fingerprint, "args": call.args}
        if self.research_business_key is not None:
            payload["business_key"] = self.research_business_key
        if policy.effect == Effect.IDEMPOTENT:
            payload["idempotency_key"] = call.stable_key
        value = self._json("/v1/execute", payload)
        if (value.get("ok") is not True or value.get("intent_key") != call.stable_key
                or not isinstance(value.get("result"), dict)):
            raise UncertainResult("响应与业务意图不匹配")
        return value["result"]

    def readback(self, call: Call, policy: ToolPolicy) -> dict | None:
        """只有准确匹配的正向证据才能确认提交；查不到不等于没执行。"""
        if not policy.readback:
            return None
        query = urllib.parse.urlencode({"tool_id": call.tool,
                                        "intent_key": call.stable_key})
        try:
            value = self._json("/v1/effects?" + query)
        except UncertainResult:
            return None
        if (value.get("status") == "confirmed" and
                value.get("intent_key") == call.stable_key and
                value.get("fingerprint") == call.fingerprint and
                isinstance(value.get("result"), dict)):
            return value["result"]
        return None

    def compensate(self, call: Call, result: dict) -> bool:
        if "ticket_id" not in result:
            raise UncertainResult("缺少原工单的确认 ID")
        value = self._json("/v1/compensate", {
            "tool_id": call.tool, "intent_key": call.stable_key,
            "ticket_id": result["ticket_id"],
        })
        return value.get("status") == "compensated" and value.get("intent_key") == call.stable_key
