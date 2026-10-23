"""管理员登记的工具副作用契约，不接受 LLM 自行声明安全属性。"""

from dataclasses import dataclass
from enum import Enum


class Effect(str, Enum):
    READ = "read"
    IDEMPOTENT = "idempotent_write"
    REVERSIBLE = "reversible_write"
    OPAQUE = "opaque_write"


@dataclass(frozen=True)
class ToolPolicy:
    effect: Effect
    readback: bool = False
    # 只有经过部署方核实的服务端稳定键与成功回执才能启用此契约。
    authoritative_receipt: bool = False

    @property
    def retryable(self) -> bool:
        # 沙箱会强制执行稳定幂等键；真实服务必须单独验证这一前提。
        return self.effect in (Effect.READ, Effect.IDEMPOTENT)


# 只有网关管理员能够修改工具副作用分类。
TOOLS: dict[str, ToolPolicy] = {
    "inventory.read": ToolPolicy(Effect.READ),
    "orders.create": ToolPolicy(Effect.IDEMPOTENT, readback=True, authoritative_receipt=True),
    "tickets.create": ToolPolicy(Effect.REVERSIBLE, readback=True),
    "notifications.send": ToolPolicy(Effect.OPAQUE),
}
