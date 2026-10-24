"""使用相同证据准入策略的 B5/RAGuard 离线公平性校验。

本实验只允许观察、等待、确认、升级；不执行任何外部写入，也不测量真实网络。
用独立的隐藏物理真值给出误确认统计，避免只凭策略响应声称成功。
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass, replace
from pathlib import Path

from raguard.evidence import (EvidenceAssessment, EvidenceContract,
                              EvidenceDecision, EvidenceObservation, assess_evidence)

ENGINES = ("b5_durable", "raguard")
TICKS = (100.0, 101.0, 102.0, 103.0, 104.0, 105.0)
DEADLINE = 105.0


@dataclass(frozen=True)
class Scenario:
    name: str
    physical_committed: bool
    # (availability_tick, trusted or rejected provider observation)
    arrivals: tuple[tuple[float, EvidenceObservation], ...]


def fixtures() -> tuple[Scenario, ...]:
    """所有方法共享同一独立真值和观测日程；不是实际第三方 Provider 数据。"""
    good = EvidenceObservation("tickets.create", "business-1", "digest-1", "provider-1",
                               2, 100, 4, "committed", effect_id="effect-1",
                               verified=True, final=True)
    absent = replace(good, outcome="absent", effect_id="")
    return (
        Scenario("immediate_commit", True, ((100, good),)),
        Scenario("delayed_commit", True, ((102, replace(good, observed_at=102)),)),
        Scenario("late_before_deadline", True, ((104, replace(good, observed_at=104)),)),
        Scenario("commit_unobservable", True, ()),
        Scenario("never_committed", False, ()),
        Scenario("absence_not_final", False, ((100, absent),)),
        Scenario("early_absence_then_commit", True,
                 ((100, absent), (102, replace(good, observed_at=102, version=5)))),
        Scenario("wrong_business", True,
                 ((100, replace(good, business_ref="another-business")),)),
        Scenario("unverified_claim", False,
                 ((100, replace(good, verified=False)),)),
        Scenario("stale_receipt", True,
                 ((100, replace(good, observed_at=90)),)),
        Scenario("wrong_authority_epoch", True,
                 ((100, replace(good, authority_epoch=1)),)),
        Scenario("wrong_payload", True,
                 ((100, replace(good, payload_digest="other-request")),)),
        Scenario("conflicting_effect_ids", True,
                 ((100, good), (100, replace(good, effect_id="effect-2")))),
        Scenario("commit_absence_conflict", True,
                 ((100, good), (100, absent))),
        Scenario("non_final", True,
                 ((100, replace(good, final=False)),)),
        Scenario("authority_mismatch", True,
                 ((100, replace(good, authority="agent-output")),)),
    )


def contract() -> EvidenceContract:
    return EvidenceContract("tickets.create", "business-1", "digest-1",
                            "provider-1", 2, max_age_seconds=8, min_version=4)


def replay_trace(scenario: Scenario, *, engine: str) -> dict:
    """共享严格相同的纯证据规则；engine 只是独立报告标签，并非真实网关集成。"""
    if engine not in ENGINES:
        raise ValueError("未注册的对照方法")
    checks = 0
    verdict = EvidenceAssessment(EvidenceDecision.WAIT, "not_checked")
    elapsed = 0.0
    for now in TICKS:
        checks += 1
        available = tuple(o for arrival, o in scenario.arrivals if arrival <= now)
        verdict = assess_evidence(contract(), available, now, deadline=DEADLINE)
        elapsed = now - TICKS[0]
        if verdict.decision is not EvidenceDecision.WAIT:
            break
    accepted = verdict.decision is EvidenceDecision.CONFIRMED
    return {
        "scenario": scenario.name,
        "engine": engine,
        "physical_committed": int(scenario.physical_committed),
        "decision": verdict.decision.value,
        "reason": verdict.reason,
        "confirmed": int(accepted),
        "false_confirmation": int(accepted and not scenario.physical_committed),
        "unresolved": int(not accepted),
        "evidence_queries": checks,
        "virtual_wait_seconds": elapsed,
        "write_dispatches": 0,
    }


def run_matrix() -> list[dict]:
    cases = fixtures()
    if len({case.name for case in cases}) != len(cases):
        raise ValueError("重复的实验键")
    return [replay_trace(case, engine=engine)
            for case in cases for engine in ENGINES]


def write_results(rows: list[dict], output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    with (output / "episodes.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    lines = [
        "# 证据准入规则公平性核验（合成轨迹）", "",
        "两组方法都调用相同的纯 `assess_evidence` 判据；**并未连接真实 B5/Gateway 的工具执行路径**。",
        "隐藏物理真值由测试夹具独立指定，观察数据是合成的，并非 Go 或外部 API 结果。",
        "不发送新写入；不存在所谓通过新恢复方法提升成功率的结论。", "",
        "| 方法 | 场景 | 错误确认 | 已确认 | 未完成 | 证据查询次数 | 总虚拟等待秒数 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for engine in ENGINES:
        subset = [r for r in rows if r["engine"] == engine]
        lines.append(f"| {engine} | {len(subset)} | "
                     f"{sum(r['false_confirmation'] for r in subset)} | "
                     f"{sum(r['confirmed'] for r in subset)} | "
                     f"{sum(r['unresolved'] for r in subset)} | "
                     f"{sum(r['evidence_queries'] for r in subset)} | "
                     f"{sum(r['virtual_wait_seconds'] for r in subset):.1f} |")
    lines += ["", "不能据此证明新的算法优势；下一步必须接入具有可信来源的 Provider adapter，",
              "并在隔离 Go 沙箱中比较真实效果、墙钟开销和异步状态恢复。", ""]
    (output / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="同证据策略的 B5/RAGuard 影子模式配对核验")
    parser.add_argument("--output", type=Path, default=Path("results/evidence_parity"))
    args = parser.parse_args()
    rows = run_matrix()
    write_results(rows, args.output)
    print(f"已核验 {len(rows)} 条模拟轨迹，未发送任何外部写入")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
