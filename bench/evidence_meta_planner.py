"""预算内求解经审计的条件查询策略，授权与校准模型相互隔离。"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Callable

from bench.evidence_audit import Label, Reading, Source, inspect
from bench.evidence_frontier import Context, Decision, Evidence


@dataclass(frozen=True)
class World:
    weight: int
    outcomes: tuple[tuple[str, Label], ...]

    def reply(self, name: str) -> Label:
        return dict(self.outcomes).get(name, Label.ABSENT)


@dataclass(frozen=True)
class Result:
    action: Decision
    used: tuple[str, ...]
    cost: int
    trace: tuple[str, ...]
    reason: str


class CapabilityPlanner:
    """有限校准世界的精确动态规划；不声称算法首创。"""

    def __init__(self, sources: tuple[Source, ...], worlds: tuple[World, ...],
                 *, cost_penalty: float = 0.0,
                 ablate: frozenset[str] = frozenset()):
        names = [s.name for s in sources]
        if not sources or len(set(names)) != len(names) or not worlds:
            raise ValueError("来源与校准世界必须存在且不可重名")
        if any(s.cost <= 0 or s.latency <= 0 for s in sources):
            raise ValueError("来源成本、延迟必须为正")
        if not 0 <= cost_penalty <= 1:
            raise ValueError("成本惩罚必须在 [0,1] 内")
        for w in worlds:
            if w.weight <= 0 or len({n for n, _ in w.outcomes}) != len(w.outcomes):
                raise ValueError("世界权重和查询身份无效")
            if any(n not in names or not isinstance(label, Label)
                   for n, label in w.outcomes):
                raise ValueError("校准世界包含未知来源或标签")
        if not ablate <= {"freshness", "authority", "conflict", "lookahead"}:
            raise ValueError("未知的消融开关")
        self.ablate = ablate
        self.sources = sources
        self.worlds = worlds
        self.cost_penalty = cost_penalty
        self.by_name = {s.name: s for s in sources}

    @lru_cache(maxsize=4096)
    def choose(self, history: tuple[tuple[str, Label], ...], budget: int,
               *, elapsed: int = 0) -> tuple[str | None, float]:
        if budget < 0 or elapsed < 0 or len({name for name, _ in history}) != len(history):
            raise ValueError("查询历史或预算不合法")
        if any(name not in self.by_name or not isinstance(label, Label)
               for name, label in history):
            raise ValueError("存在未知的来源或响应")

        @lru_cache(None)
        def plan(trace: tuple[tuple[str, Label], ...], money: int,
                 tick: int) -> tuple[str | None, float]:
            possible = [
                w for w in self.worlds
                if all(self._label(w, self.by_name[name], at) == label
                       for name, label, at in timed_history(trace, tick))
            ]
            denominator = sum(w.weight for w in possible)
            if not denominator:
                return None, 0.0
            if any(label in (Label.COMMIT, Label.HARD_CONFLICT)
                   for _, label in trace):
                return None, 0.0
            tried = {name for name, _ in trace}
            best: tuple[str | None, float] = (None, 0.0)
            for source in self.sources:
                if source.name in tried or source.cost > money:
                    continue
                outcomes = {self._label(w, source, tick + source.latency)
                            for w in possible}
                gain = -self.cost_penalty * source.cost
                for outcome in outcomes:
                    weight = sum(
                        w.weight for w in possible
                        if self._label(w, source, tick + source.latency) == outcome
                    )
                    if outcome == Label.COMMIT and (
                            source.can_confirm or "authority" in self.ablate):
                        future = 1.0
                    elif outcome == Label.HARD_CONFLICT:
                        future = 0.0
                    elif "lookahead" in self.ablate:
                        future = 0.0
                    else:
                        future = plan(
                            trace + ((source.name, outcome),),
                            money - source.cost, tick + source.latency,
                        )[1]
                    gain += weight / denominator * future
                if gain > best[1] + 1e-10:
                    best = source.name, gain
            return best

        def timed_history(trace: tuple[tuple[str, Label], ...], tick: int):
            past = tick - sum(self.by_name[name].latency for name, _ in trace)
            for name, label in trace:
                past += self.by_name[name].latency
                yield name, label, past

        return plan(history, budget, elapsed)

    def _label(self, world: World, source: Source, now: int) -> Label:
        result = world.reply(source.name)
        if result == Label.HARD_CONFLICT and "conflict" in self.ablate:
            return Label.COMMIT
        if result == Label.COMMIT:
            if not source.can_confirm and "authority" not in self.ablate:
                return Label.PENDING
            if now > source.proof_valid_through and "freshness" not in self.ablate:
                return Label.STALE
        return result

    def run(self, context: Context, budget: int,
            fetch: Callable[[str], Reading]) -> Result:
        used: list[str] = []
        trace: list[str] = []
        history: tuple[tuple[str, Label], ...] = ()
        prior: tuple[Evidence, ...] = ()
        money, elapsed = budget, 0
        while True:
            name, _ = self.choose(
                history, money, elapsed=context.server_tick_upper + elapsed
            )
            if name is None:
                return Result(Decision.STOP, tuple(used), budget - money,
                              tuple(trace), "没有可行的安全证明查询")
            source = self.by_name[name]
            elapsed += source.latency
            money -= source.cost
            reading = fetch(name)
            audited = inspect(context, source, reading,
                              context.server_tick_upper + elapsed, prior)
            used.append(name)
            trace.append(audited.label.value)
            if audited.terminal:
                return Result(audited.action, tuple(used), budget - money,
                              tuple(trace), audited.reason)
            prior = audited.valid_proofs
            history += ((name, audited.label),)
