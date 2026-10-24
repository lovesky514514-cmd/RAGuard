"""有界证据等待：只做正向回读，绝不决定重发外部写入。"""

from dataclasses import dataclass
import math
import time
from typing import Callable

from .model import Call
from .policy import ToolPolicy


@dataclass(frozen=True)
class EvidenceWaitBudget:
    checks: int = 4
    interval_seconds: float = 0.01
    deadline_seconds: float = 0.2

    def __post_init__(self) -> None:
        if (type(self.checks) is not int or not 1 <= self.checks <= 32
                or not math.isfinite(self.interval_seconds)
                or not 0 <= self.interval_seconds <= 30
                or not math.isfinite(self.deadline_seconds)
                or not 0 < self.deadline_seconds <= 300):
            raise ValueError('无效的证据等待预算')


@dataclass(frozen=True)
class EvidenceWaitResult:
    confirmed: bool
    checks: int
    reason: str


def wait_for_evidence(call: Call, policy: ToolPolicy,
                      readback: Callable[[Call, ToolPolicy], dict | None],
                      budget: EvidenceWaitBudget, *,
                      should_continue: Callable[[], bool] | None = None,
                      clock: Callable[[], float] = time.monotonic,
                      sleep: Callable[[float], None] = time.sleep) -> EvidenceWaitResult:
    """有界只读检查；成功仅表示证据可供调用方后续持久化复核。

    真正确认状态必须重新进入原引擎的持久恢复入口；不能将这个函数
    的返回值当作任何业务重新执行许可，尤其不能在 'absence' 时重试。
    """
    if not policy.readback:
        return EvidenceWaitResult(False, 0, 'readback_unsupported')
    start = clock()
    if not math.isfinite(start):
        raise ValueError('时钟必须是有限数值')
    deadline = start + budget.deadline_seconds
    checked = 0
    for n in range(budget.checks):
        now = clock()
        if not math.isfinite(now):
            raise ValueError('时钟必须是有限数值')
        if now >= deadline:
            return EvidenceWaitResult(False, checked, 'deadline_exceeded')
        checked += 1
        # readback 必须是可信适配器。错误、未知或回读异常都不能作为成功证据。
        try:
            evidence = readback(call, policy)
        except (ConnectionError, TimeoutError, OSError):
            evidence = None
        if evidence is not None:
            if not isinstance(evidence, dict):
                raise TypeError('回读适配器必须返回 dict 或 None')
            return EvidenceWaitResult(True, checked, 'positive_evidence')
        if should_continue is not None and not should_continue():
            return EvidenceWaitResult(False, checked, 'contract_violation')
        if n + 1 < budget.checks:
            remaining = deadline - clock()
            if remaining <= 0:
                break
            sleep(min(budget.interval_seconds, remaining))
    return EvidenceWaitResult(False, checked, 'budget_exhausted')
