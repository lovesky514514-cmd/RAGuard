"""逻辑工具调用模型及稳定身份计算。"""

from dataclasses import dataclass
from hashlib import sha256
import json
import re

SAFE_ID = re.compile(r"[A-Za-z0-9_.:-]{1,80}\Z")


class BadCall(ValueError):
    """工具调用参数缺失、格式错误或互相矛盾。"""


def canonical(value: object) -> str:
    """稳定 JSON 序列化；拒绝 NaN 和不支持的值。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


@dataclass(frozen=True)
class Call:
    tenant: str
    principal: str
    task: str
    step: str
    intent: str
    tool: str
    args: dict
    dependencies: tuple[str, ...]

    @classmethod
    def parse(cls, raw: object) -> "Call":
        if not isinstance(raw, dict):
            raise BadCall("请求必须是 JSON 对象")
        required = {"tenant", "principal", "task", "step", "intent", "tool", "args"}
        if not required <= raw.keys() or raw.keys() - (required | {"dependencies"}):
            raise BadCall("缺少必填字段或包含未知字段")
        for key in required - {"args"}:
            if not isinstance(raw[key], str) or not SAFE_ID.fullmatch(raw[key]):
                raise BadCall(f"{key} 需要 1-80 位安全标识")
        if not isinstance(raw["args"], dict):
            raise BadCall("args 必须是对象")
        try:
            encoded = canonical(raw["args"])
        except (TypeError, ValueError, OverflowError) as exc:
            raise BadCall("args 不是有效 JSON") from exc
        if len(encoded.encode("utf-8")) > 4096:
            raise BadCall("args 不能超过 4 KiB")
        dependencies = raw.get("dependencies", [])
        if (not isinstance(dependencies, list) or len(dependencies) > 32 or
                any(not isinstance(v, str) or not SAFE_ID.fullmatch(v) for v in dependencies) or
                len(set(dependencies)) != len(dependencies) or raw["step"] in dependencies):
            raise BadCall("dependencies 非法、重复或自引用")
        return cls(*(raw[k] for k in ("tenant", "principal", "task", "step", "intent", "tool")),
                   raw["args"], tuple(dependencies))

    @property
    def fingerprint(self) -> str:
        """不同主体、金额、接收方或依赖关系不能共用请求指纹。"""
        return sha256(canonical({
            "tenant": self.tenant, "principal": self.principal,
            "task": self.task, "step": self.step, "intent": self.intent,
            "tool": self.tool, "args": self.args, "dependencies": self.dependencies,
        }).encode("utf-8")).hexdigest()

    @property
    def stable_key(self) -> str:
        """根据规范化任务身份生成稳定键，不使用重试时的自由文本。"""
        return sha256(canonical([self.tenant, self.principal, self.task, self.intent])
                      .encode("utf-8")).hexdigest()
