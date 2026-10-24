"""证据可采性判定原型；不执行远程请求，不授予重试权限。"""

from dataclasses import dataclass
from enum import Enum
import math


class EvidenceDecision(str, Enum):
    CONFIRMED = 'confirmed'
    WAIT = 'wait'
    ESCALATE = 'escalate'


@dataclass(frozen=True)
class EvidenceContract:
    tool: str
    business_ref: str
    payload_digest: str
    authority: str
    authority_epoch: int
    max_age_seconds: float
    min_version: int = 0

    def __post_init__(self):
        if not all((self.tool, self.business_ref, self.payload_digest, self.authority)):
            raise ValueError('证据契约必须绑定工具、业务、请求及权威来源')
        if (self.authority_epoch < 0 or self.min_version < 0
                or not math.isfinite(self.max_age_seconds) or self.max_age_seconds < 0):
            raise ValueError('无效的版本或有效期')


@dataclass(frozen=True)
class EvidenceObservation:
    """verified 与 final 只能由可信适配器设置；本原型尚未验证签名。"""

    tool: str
    business_ref: str
    payload_digest: str
    authority: str
    authority_epoch: int
    observed_at: float
    version: int
    outcome: str
    effect_id: str = ''
    verified: bool = False
    final: bool = False


@dataclass(frozen=True)
class EvidenceAssessment:
    decision: EvidenceDecision
    reason: str
    admissible: int = 0


def _admissible(contract: EvidenceContract, item: EvidenceObservation, now: float) -> bool:
    return (item.verified and item.final
            and item.tool == contract.tool
            and item.business_ref == contract.business_ref
            and item.payload_digest == contract.payload_digest
            and item.authority == contract.authority
            and item.authority_epoch == contract.authority_epoch
            and item.version >= contract.min_version
            and math.isfinite(item.observed_at)
            and 0 <= now - item.observed_at <= contract.max_age_seconds
            and item.outcome in {'committed', 'absent', 'unknown'}
            and (item.outcome != 'committed' or bool(item.effect_id)))


def assess_evidence(contract: EvidenceContract,
                    observations: tuple[EvidenceObservation, ...], now: float,
                    *, deadline: float | None = None) -> EvidenceAssessment:
    """只决定能否确认既有结果；绝不能根据本函数重发未知副作用。

    WAIT 只允许查询证据，不授权执行；absence 不排除晚提交。
    """
    if not math.isfinite(now) or (deadline is not None and not math.isfinite(deadline)):
        raise ValueError('时间必须为有限数值')
    accepted = tuple(o for o in observations if _admissible(contract, o, now))
    committed = {o.effect_id for o in accepted if o.outcome == 'committed'}
    newest_commit = max((o.version for o in accepted if o.outcome == 'committed'),
                        default=-1)
    conflicting_absence = any(o.outcome == 'absent' and o.version >= newest_commit
                              for o in accepted)
    absent = any(o.outcome == 'absent' for o in accepted)
    if len(committed) > 1 or (committed and conflicting_absence):
        return EvidenceAssessment(EvidenceDecision.ESCALATE,
                                  'conflicting_authoritative_evidence', len(accepted))
    if len(committed) == 1:
        return EvidenceAssessment(EvidenceDecision.CONFIRMED,
                                  'authoritative_commit', len(accepted))
    if deadline is not None and now >= deadline:
        return EvidenceAssessment(EvidenceDecision.ESCALATE,
                                  'evidence_deadline_exceeded', len(accepted))
    return EvidenceAssessment(EvidenceDecision.WAIT,
                              'absence_not_final' if absent else 'insufficient_evidence',
                              len(accepted))
