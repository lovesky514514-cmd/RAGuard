"""离线影子调度器：只为既有证据选择查询时刻，绝不授权写入。"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
import math
from typing import Iterable

MAX_TICK = 12
CHECKS = 3


@dataclass(frozen=True)
class ObservationTrace:
    """研究训练数据；首份可接受正向回执的时间，None 表示不可确认。"""

    provider: str
    first_valid_tick: int | None

    def __post_init__(self):
        if not self.provider or (self.first_valid_tick is not None and
                                 (type(self.first_valid_tick) is not int or
                                  not 0 <= self.first_valid_tick <= MAX_TICK)):
            raise ValueError('无效的证据训练轨迹')


@dataclass(frozen=True)
class Schedule:
    ticks: tuple[int, ...]

    def __post_init__(self):
        if (len(self.ticks) != CHECKS or
                any(type(t) is not int or not 0 <= t <= MAX_TICK for t in self.ticks) or
                tuple(sorted(set(self.ticks))) != self.ticks):
            raise ValueError('时间必须是严格递增且不超预算的只读查询位置')


@dataclass(frozen=True)
class Scheduler:
    """训练阶段冻结决策，不在评估阶段读取样本的隐藏效果真值。"""

    pooled: Schedule
    by_provider: dict[str, Schedule]

    def choose(self, provider: str) -> Schedule:
        return self.by_provider.get(provider, self.pooled)


def score(schedule: Schedule, traces: Iterable[ObservationTrace], *,
          cost_per_query: float = 0.02, latency_cost: float = 0.12) -> float:
    """经验奖励：成功确认 - 回读开销 - 确认延迟，缺证据只会耗尽只读预算。"""
    rows = tuple(traces)
    if not rows or not all(math.isfinite(x) and x >= 0 for x in
                           (cost_per_query, latency_cost)):
        raise ValueError('需要非空训练集与有限非负成本')
    total = 0.0
    for item in rows:
        slot = next((i for i, t in enumerate(schedule.ticks)
                     if item.first_valid_tick is not None and t >= item.first_valid_tick), None)
        queries = CHECKS if slot is None else slot + 1
        confirmed = slot is not None
        t = schedule.ticks[slot] if confirmed else MAX_TICK
        total += float(confirmed) - queries * cost_per_query - t / MAX_TICK * latency_cost
    return total / len(rows)


def optimal_schedule(traces: Iterable[ObservationTrace]) -> Schedule:
    """强基线也可调用同一优化器；固定预算，确定性同分择优。"""
    rows = tuple(traces)
    if not rows:
        raise ValueError('不能对空样本拟合')
    candidates = (Schedule(ticks) for ticks in combinations(range(MAX_TICK + 1), CHECKS))
    return max(candidates, key=lambda s: (score(s, rows), tuple(-x for x in s.ticks)))


def fit_scheduler(traces: Iterable[ObservationTrace], *, min_group: int = 15) -> Scheduler:
    rows = tuple(traces)
    if not rows or type(min_group) is not int or min_group < 1:
        raise ValueError('需要有效训练数据和样本下界')
    pooled = optimal_schedule(rows)
    groups: dict[str, list[ObservationTrace]] = {}
    for item in rows:
        groups.setdefault(item.provider, []).append(item)
    return Scheduler(pooled, {p: optimal_schedule(g) for p, g in groups.items()
                              if len(g) >= min_group})
