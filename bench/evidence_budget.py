"""同预算证据查询调度：只依赖来源契约与独立校准记录。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from random import Random
from typing import Callable

from bench.evidence_frontier import Context, Decision, Evidence, Kind, Verdict, decide


@dataclass(frozen=True)
class Source:
    name: str
    cost: int
    latency: int
    capabilities: frozenset[Kind]


@dataclass(frozen=True)
class Budget:
    cost: int
    queries: int
    latency: int


@dataclass(frozen=True)
class QueryResult:
    verdict: Verdict
    cost: int
    queries: int
    latency: int
    sources: tuple[str, ...]


def calibration(samples: list[dict[str, Evidence | None]], sources: tuple[Source, ...],
                context: Context) -> dict[str, float]:
    """仅从训练集可观测的有效证明估计成功率，不访问评估真值。"""
    if not samples:
        raise ValueError("独立校准集不能为空")
    estimates = {}
    for source in sources:
        qualified = 0
        for sample in samples:
            item = sample.get(source.name)
            if item is None:
                continue
            if decide(context, (item,)).action in (Decision.CONFIRM, Decision.REISSUE):
                qualified += 1
        estimates[source.name] = qualified / len(samples)
    return estimates


def query_budgeted(context: Context, sources: tuple[Source, ...], budget: Budget,
                   fetch: Callable[[str], Evidence | None], *, policy: str,
                   estimates: dict[str, float] | None = None,
                   rng: Random | None = None) -> QueryResult:
    """策略无法看到未查询的证据；超预算只能停止，不得猜测派发。"""
    if min(budget.cost, budget.queries, budget.latency) < 0:
        raise ValueError("预算不能为负数")
    if policy not in ("fixed", "cheap", "calibrated", "random"):
        raise ValueError("未知的查询策略")
    if policy == "calibrated" and estimates is None:
        raise ValueError("证据调度必须提供独立训练集校准")
    if policy == "random" and rng is None:
        raise ValueError("随机策略需要显式随机数生成器")
    names = [source.name for source in sources]
    if len(names) != len(set(names)):
        raise ValueError("证据来源标识重复")
    if any(source.cost <= 0 or source.latency <= 0 for source in sources):
        raise ValueError("成本和延迟必须为正数")
    if estimates is not None and any(not 0 <= value <= 1 for value in estimates.values()):
        raise ValueError("校准值必须属于 [0, 1]")

    remaining = list(sources)
    received: list[Evidence] = []
    queried: list[str] = []
    total_cost = total_latency = 0
    verdict = Verdict(Decision.STOP, "未获取有效证据")
    while len(queried) < budget.queries:
        options = [source for source in remaining
                   if total_cost + source.cost <= budget.cost
                   and total_latency + source.latency <= budget.latency]
        if not options:
            break
        if policy == "fixed":
            source = options[0]
        elif policy == "cheap":
            source = min(options, key=lambda value: (value.cost, value.latency, value.name))
        elif policy == "random":
            source = rng.choice(options)
        else:
            # 这是简单启发式基线，不声称首创或数学最优。
            source = max(options, key=lambda value: (
                estimates.get(value.name, 0.0) / value.cost,
                -value.latency, -value.cost,
            ))
        remaining.remove(source)
        queried.append(source.name)
        total_cost += source.cost
        total_latency += source.latency
        observed = fetch(source.name)
        if observed is None:
            continue
        if observed.kind not in source.capabilities:
            verdict = Verdict(Decision.STOP, "来源返回未经声明的证据能力")
            break
        received.append(observed)
        adjusted = replace(context, server_tick_upper=context.server_tick_upper + total_latency)
        verdict = decide(adjusted, tuple(received))
        if verdict.action != Decision.STOP:
            break
        # 不可信、冲突、过期的证据不能以更多查询稀释。
        if verdict.reason not in ("无法从回读空白或进行中推断执行结果", "无可用证据"):
            break
    return QueryResult(verdict, total_cost, len(queried), total_latency, tuple(queried))
