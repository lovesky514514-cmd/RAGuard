"""纯恢复策略：证据有效性与重试预算，不执行网络和数据库操作。"""

from dataclasses import dataclass

from .ledger import Record
from .policy import ToolPolicy


@dataclass(frozen=True)
class RetryBudget:
    attempts: int = 2
    initial_delay_seconds: float = 0.02
    multiplier: float = 2.0

    def __post_init__(self):
        if (not 1 <= self.attempts <= 10 or self.initial_delay_seconds < 0
                or self.initial_delay_seconds > 30 or not 1 <= self.multiplier <= 10):
            raise ValueError('无效的重试预算')

    def delay(self, attempt: int) -> float:
        """返回安全准入后的退避秒数；是否可以重试由工具契约决定。"""
        if attempt < 0:
            raise ValueError('attempt 不能为负数')
        return self.initial_delay_seconds * self.multiplier ** attempt


def cached_evidence(policy: ToolPolicy, record: Record,
                    now: float, ttl_seconds: float) -> str | None:
    """只判断能否回放已缓存结果，不授权重新执行外部工具。"""
    if record.state != 'confirmed' or record.result is None:
        return None
    if not policy.readback:
        return record.evidence or 'http_receipt'
    trustworthy = record.evidence in {'positive_readback', 'idempotent_ack'}
    if trustworthy and 0 <= now - record.updated <= ttl_seconds:
        return 'receipt_only'
    return None
