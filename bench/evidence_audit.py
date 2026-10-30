"""把证据命题、来源权限和时效分开审计；概率不能授予写权限。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum

from bench.evidence_frontier import Context, Decision, Evidence, Kind, decide


class Label(str, Enum):
    ROUTE_A = "route_a"
    ROUTE_B = "route_b"
    ABSENT = "absent"
    PENDING = "pending"
    SOFT_CONFLICT = "soft_conflict"
    COMMIT = "commit"
    STALE = "stale"
    HARD_CONFLICT = "hard_conflict"


@dataclass(frozen=True)
class Source:
    name: str
    cost: int
    latency: int
    can_confirm: bool = False
    can_prove_nonexecution: bool = False
    # 此上限约束的是读取结果的可用性，不否认历史事件已经发生。
    proof_valid_through: int = 100


@dataclass(frozen=True)
class Reading:
    label: Label
    proofs: tuple[Evidence, ...] = ()


@dataclass(frozen=True)
class Audit:
    label: Label
    action: Decision
    terminal: bool
    reason: str
    valid_proofs: tuple[Evidence, ...] = ()


def inspect(context: Context, source: Source, reading: Reading, now: int,
            previous: tuple[Evidence, ...] = ()) -> Audit:
    """冲突或来源越权一律停止；普通空回读和推断分歧不能证明未执行。"""
    if not isinstance(reading, Reading) or not isinstance(reading.label, Label):
        return Audit(Label.PENDING, Decision.STOP, True, "不认识的证据响应")
    if reading.label == Label.HARD_CONFLICT and len(reading.proofs) < 2:
        return Audit(reading.label, Decision.STOP, True, "冲突缺少可核验事实")
    if reading.label == Label.COMMIT and len(reading.proofs) != 1:
        return Audit(reading.label, Decision.STOP, True, "提交回执数量异常")
    if reading.label not in (Label.COMMIT, Label.HARD_CONFLICT) and reading.proofs:
        return Audit(reading.label, Decision.STOP, True, "非证明响应夹带证明")
    if source.proof_valid_through < now and reading.proofs:
        return Audit(Label.STALE, Decision.STOP, False, "来源证明授权窗口已过期")

    for proof in reading.proofs:
        if proof.kind == Kind.COMMITTED and not source.can_confirm:
            return Audit(reading.label, Decision.STOP, True, "来源没有提交证明能力")
        if proof.kind == Kind.FINAL_NON_EXECUTION and not source.can_prove_nonexecution:
            return Audit(reading.label, Decision.STOP, True, "来源没有未执行证明能力")
        if proof.kind not in (Kind.COMMITTED, Kind.FINAL_NON_EXECUTION) or not proof.final:
            return Audit(reading.label, Decision.STOP, True, "证明不具备终局性")
        validation = decide(replace(context, server_tick_upper=now), (proof,))
        if validation.reason in ("发行主体不可信", "业务身份、作用域或代次不一致"):
            return Audit(reading.label, Decision.STOP, True, validation.reason)
        if validation.reason == "权威证据有效期无法覆盖当前时刻":
            return Audit(Label.STALE, Decision.STOP, False, validation.reason)

    merged = previous + reading.proofs
    kinds = {proof.kind for proof in merged}
    if Kind.COMMITTED in kinds and Kind.FINAL_NON_EXECUTION in kinds:
        return Audit(Label.HARD_CONFLICT, Decision.STOP, True, "同一请求出现矛盾的权威终局事实")
    if reading.label == Label.HARD_CONFLICT:
        return Audit(reading.label, Decision.STOP, True, "冲突事实无法验证或互斥")
    if reading.label == Label.COMMIT:
        return Audit(reading.label, Decision.CONFIRM, True, "权威提交已核验", merged)
    return Audit(reading.label, Decision.STOP, False,
                 "尚无可授权确认的事实", merged)
