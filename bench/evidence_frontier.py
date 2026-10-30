"""独立研究候选：将证据能力映射到安全恢复权限。"""

from dataclasses import dataclass
from enum import Enum


class Decision(str, Enum):
    CONFIRM = "confirm"
    REISSUE = "reissue"
    STOP = "stop"


class Kind(str, Enum):
    COMMITTED = "committed"
    FINAL_NON_EXECUTION = "final_non_execution"
    PENDING = "pending"
    ABSENT_READ = "absent_read"


@dataclass(frozen=True)
class Evidence:
    kind: Kind
    identity: str
    scope: str
    fingerprint: str
    authority: str
    generation: int
    valid_until: int
    authenticated: bool = False
    final: bool = False


@dataclass(frozen=True)
class Context:
    identity: str
    scope: str
    fingerprint: str
    authority: str
    generation: int
    server_tick_upper: int
    service_fence: bool
    retry_contract: bool


@dataclass(frozen=True)
class Verdict:
    action: Decision
    reason: str


def decide(context: Context, evidence: tuple[Evidence, ...]) -> Verdict:
    """没有权威执行屏障时，非执行证据不授权重发。"""
    if not evidence:
        return Verdict(Decision.STOP, "无可用证据")

    # 不允许用投票或弱信号覆盖冲突、过期或错误身份。
    for item in evidence:
        if not item.authenticated or item.authority != context.authority:
            return Verdict(Decision.STOP, "发行主体不可信")
        if (item.identity != context.identity or item.scope != context.scope
                or item.fingerprint != context.fingerprint
                or item.generation != context.generation):
            return Verdict(Decision.STOP, "业务身份、作用域或代次不一致")
        if item.valid_until < context.server_tick_upper:
            return Verdict(Decision.STOP, "权威证据有效期无法覆盖当前时刻")

    committed = any(item.kind == Kind.COMMITTED and item.final for item in evidence)
    non_execution = any(
        item.kind == Kind.FINAL_NON_EXECUTION and item.final for item in evidence
    )
    if committed and non_execution:
        return Verdict(Decision.STOP, "权威证据结论冲突")
    if committed:
        return Verdict(Decision.CONFIRM, "可信终局提交证明")
    if non_execution and context.service_fence and context.retry_contract:
        return Verdict(Decision.REISSUE, "终局未执行证明及不可绕过的执行屏障")
    if non_execution:
        return Verdict(Decision.STOP, "旧请求未来提交风险尚未排除")
    return Verdict(Decision.STOP, "无法从回读空白或进行中推断执行结果")
