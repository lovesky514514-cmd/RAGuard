"""原版 B5/RAGuard 的跨回合 HTTP 证据调度对照。"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
from random import Random
import subprocess
import tempfile

from bench.evidence_http_live import EvidenceClient
from bench.evidence_online_experiment import POLICIES
from bench.evidence_online_policy import Choice, SlidingWindowUCB
from bench.evidence_equal_spend import ExactSpend
from bench.runner import raw_call
from bench.window_live import METHODS, ORIGIN_SCOPE, engine_call, window_server, write_csv
from bench.window_uncertainty import control
from raguard.model import Call

ROOT = Path(__file__).resolve().parents[1]
CHOICES = (Choice("cheap", 1), Choice("quick", 2))
PROFILES = ("matched", "shifted")


@dataclass(frozen=True)
class Scenario:
    number: int
    phase: str
    committed: bool
    recovery_delay: int


@dataclass(frozen=True)
class Episode:
    number: int
    phase: str
    engine: str
    policy: str
    source: str
    injected_commit: int
    physical_effects: int
    status: int
    safe_confirm: int
    unsafe_confirm: int
    duplicate_effects: int
    unresolved: int
    cost: int
    queries: int
    blocked_writes: int
    forced_choice: int = 0


def scenarios(seed: int, length: int) -> list[Scenario]:
    """预先固定双方相同故障，不将物理真值暴露给查询策略。"""
    if length < 8 or length > 1000 or length % 2:
        raise ValueError("案例数必须是 8 至 1000 的偶数")
    rng = Random(seed)
    return [
        Scenario(i, PROFILES[int(i >= length // 2)], rng.random() < .65,
                 rng.choice((0, 1, 2)))
        for i in range(length)
    ]


def choice(policy: str, number: int, learner: SlidingWindowUCB) -> str:
    if policy == "fixed_cheap":
        return "cheap"
    if policy == "fixed_quick":
        return "quick"
    if policy == "alternate":
        return ("cheap", "quick")[number % 2]
    if policy == "sliding_ucb":
        return learner.select()
    raise ValueError("未知在线查询策略")


def run_engine(binary: Path, cases: list[Scenario], engine: str,
               policy: str, *, exact_budget: bool = False) -> list[Episode]:
    """每个方案单独执行等价故障；学习器只能看到实际所选来源。"""
    learner = SlidingWindowUCB(CHOICES, window=12, exploration=.35)
    tracker = (ExactSpend({"cheap": 1, "quick": 2}, len(cases),
                          len(cases) * 3 // 2) if exact_budget else None)
    results = []
    for phase in PROFILES:
        segment = [case for case in cases if case.phase == phase]
        # 每个案例独立启动 Go 沙箱，避免服务端时钟和物理日志相互污染。
        for case in segment:
            with window_server(binary, ttl=6,
                               extra_args=("--evidence-mode", phase)) as origin, \
                    tempfile.TemporaryDirectory() as folder:
                work = Path(folder)
                preferred = choice(policy, case.number, learner)
                source, forced = tracker.select(preferred) if tracker else (preferred, False)
                raw = raw_call("orders.create")
                raw["task"] = f"online-{case.number}"
                raw["intent"] = f"operation-{case.number}"
                call = Call.parse(raw)
                client = EvidenceClient(
                    origin, "after_commit" if case.committed else "before_commit",
                    "fixed_quick" if source == "quick" else "fixed", 0,
                )
                case_dir = work / f"case-{case.number}"
                case_dir.mkdir()
                engine_call(engine, case_dir, client, raw)

                # 恢复只能读一次正向证据，不得借服务端幂等重放绕开实验。
                response = control(origin, "advance", {"ticks": case.recovery_delay})
                client.tick = response["server_tick"]
                client.enabled = True
                client.queried = False
                client.drop_count = 0
                status = engine_call(engine, case_dir, client, raw)
                budget = client.budget_result
                if budget is None or budget.queries != 1 or budget.sources != (source,):
                    raise AssertionError("证据查询未遵守单来源同预算协议")
                if budget.cost != next(item.cost for item in CHOICES if item.source == source):
                    raise AssertionError("证据成本未正确计费")

                # 独立评估器才允许读取服务端物理效果日志。
                truth = control(origin, "effects")
                effects = [
                    item for item in truth["effects"]
                    if item["key"] == call.stable_key and
                    item["fingerprint"] == call.fingerprint and
                    item["scope"] == ORIGIN_SCOPE
                ]
                if len(effects) != int(case.committed):
                    raise AssertionError("故障注入和物理提交不一致")
                safe = status == 200 and len(effects) == 1
                unsafe = status == 200 and len(effects) != 1
                observed = budget.verdict.action.value == "confirm"
                if policy == "sliding_ucb":
                    learner.observe(source, observed)
                results.append(Episode(
                    case.number, phase, engine, policy, source, int(case.committed),
                    len(effects), status, int(safe), int(unsafe),
                    max(0, len(effects) - 1), int(status != 200),
                    budget.cost, budget.queries, client.blocked_recovery_writes,
                    int(forced),
                ))
    if tracker:
        tracker.verify()
    return results


def validate(rows: list[Episode], length: int, *, exact_budget: bool = False) -> None:
    expected = {(engine, policy, index)
                for engine in METHODS for policy in POLICIES
                for index in range(length)}
    actual = [(row.engine, row.policy, row.number) for row in rows]
    if len(actual) != len(set(actual)) or set(actual) != expected:
        raise ValueError("独立 HTTP 配对缺失或重复")
    by_case = {}
    for row in rows:
        key = (row.number, row.phase)
        previous = by_case.setdefault(key, row.physical_effects)
        if previous != row.physical_effects:
            raise ValueError("不同策略的物理故障不等价")
        if row.queries != 1 or row.cost not in (1, 2):
            raise ValueError("证据预算违反协议")
    if exact_budget:
        target = length * 3 // 2
        for engine in METHODS:
            for policy in POLICIES:
                if sum(r.cost for r in rows if r.engine == engine and r.policy == policy) != target:
                    raise ValueError("跨策略累计成本不相等")


def run(seed: int, length: int, output: Path, *, exact_budget: bool = False) -> list[dict]:
    cases = scenarios(seed, length)
    with tempfile.TemporaryDirectory() as folder:
        binary = Path(folder) / ("uncertainty.exe" if os.name == "nt" else "uncertainty")
        subprocess.run(["go", "build", "-o", str(binary), "./windowlab/cmd/uncertainty"],
                       cwd=ROOT / "mock", check=True, timeout=120, capture_output=True)
        rows = [row for engine in METHODS for policy in POLICIES
                for row in run_engine(binary, cases, engine, policy,
                                      exact_budget=exact_budget)]
    validate(rows, length, exact_budget=exact_budget)

    summary = []
    for phase in PROFILES:
        for engine in METHODS:
            for policy in POLICIES:
                part = [r for r in rows if (r.phase, r.engine, r.policy)
                        == (phase, engine, policy)]
                summary.append({
                    "phase": phase, "engine": engine, "policy": policy,
                    "cases": len(part),
                    "safe": sum(r.safe_confirm for r in part),
                    "unsafe": sum(r.unsafe_confirm for r in part),
                    "unresolved": sum(r.unresolved for r in part),
                    "cost": sum(r.cost for r in part),
                    "blocked_writes": sum(r.blocked_writes for r in part),
                    "forced_choices": sum(r.forced_choice for r in part),
                })
    output.mkdir(parents=True, exist_ok=True)
    write_csv(output / "episodes.csv", [asdict(row) for row in rows])
    write_csv(output / "summary.csv", summary)
    (output / "protocol.json").write_text(json.dumps({
        "kind": "real_loopback_go_http_online_source_selection",
        "seed": seed, "cases": length, "engines": METHODS, "policies": POLICIES,
        "feedback": "only_selected_source_positive_receipt",
        "supervisor_truth_not_available_to_agent": True,
        "all_methods_block_recovery_writes": True,
        "single_query_per_case": True,
        "query_cost_units": {item.source: item.cost for item in CHOICES},
        "source_views_share_one_authority": True,
        "not_real_vendor_or_independent_distribution": True,
        "sliding_ucb_is_prior_art": True,
        "exact_cumulative_spend": exact_budget,
        "target_spend_per_method_policy": length * 3 // 2 if exact_budget else None,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = [
        "# 多回合 Go HTTP 证据选择对照", "",
        "原版 B5/Gateway、独立 Go HTTP 进程与真实物理日志；仅本地受控案例。",
        "双方共同禁止恢复写入，确认只能依赖所选来源的一次正向回读。",
        "UCB 是已有算法；学习器不读取环境标签、未选证据或物理真值。",
        "来源为同一服务端日志的两种可见性，不是独立权威。",
        "默认协议实际成本可能不同；--exact-budget 强制相同累计成本。",
        "强制来源改选次数须与收益一起报告。", "",
        "| 阶段 | 引擎 | 策略 | 案例 | 安全确认 | 不安全确认 | 未完成 | 实际成本 | 强制改选 |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in summary:
        report.append(f"| {item['phase']} | {item['engine']} | {item['policy']} | "
                      f"{item['cases']} | {item['safe']} | {item['unsafe']} | "
                      f"{item['unresolved']} | {item['cost']} | {item['forced_choices']} |")
    (output / "REPORT.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    names = ("episodes.csv", "summary.csv", "protocol.json", "REPORT.md")
    (output / "SHA256SUMS.json").write_text(json.dumps({
        name: sha256((output / name).read_bytes()).hexdigest() for name in names
    }, indent=2) + "\n", encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="多回合真实 HTTP 在线证据选择")
    parser.add_argument("--seed", type=int, default=20261028)
    parser.add_argument("--length", type=int, default=40)
    parser.add_argument("--output", type=Path, default=Path("results/evidence_online_http"))
    parser.add_argument("--exact-budget", action="store_true")
    args = parser.parse_args()
    run(args.seed, args.length, args.output, exact_budget=args.exact_budget)


if __name__ == "__main__":
    main()
