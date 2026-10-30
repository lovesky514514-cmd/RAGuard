"""按证据能力与查询预算构造条件式恢复查询树。"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Callable

from bench.evidence_frontier import Context, Decision, Evidence, decide


@dataclass(frozen=True)
class Source:
    name: str
    cost: int
    positive_authority: bool = False


@dataclass(frozen=True)
class Scenario:
    """供规划器使用的独立校准模型，不代表真实测试世界。"""
    weight: int
    replies: tuple[tuple[str, str], ...]

    def reply(self, name: str) -> str:
        return dict(self.replies).get(name, "absent")


@dataclass(frozen=True)
class Reading:
    kind: str
    evidence: Evidence | None = None


@dataclass(frozen=True)
class Outcome:
    decision: Decision
    used_sources: tuple[str, ...]
    total_cost: int
    reason: str


class ClosurePlanner:
    """精确枚举有限校准模型；在线只有已查询的证据能授权确认。"""

    def __init__(self, sources: tuple[Source, ...], model: tuple[Scenario, ...]):
        if not sources or len({s.name for s in sources}) != len(sources):
            raise ValueError("查询来源必须非空且名称唯一")
        if any(s.cost <= 0 for s in sources) or not model or any(w.weight <= 0 for w in model):
            raise ValueError("来源成本与校准模型权重必须为正")
        allowed = {"route_a", "route_b", "commit", "absent", "pending"}
        names = {s.name for s in sources}
        for world in model:
            observed = dict(world.replies)
            if len(observed) != len(world.replies) or not set(observed) <= names:
                raise ValueError("模型包含未知或重复查询来源")
            if not set(observed.values()) <= allowed:
                raise ValueError("模型包含未知证据结果")
            if any(observed.get(s.name) == "commit" and not s.positive_authority
                   for s in sources):
                raise ValueError("不可信来源不能声明拥有正向确认能力")
        self.sources = sources
        self.model = model
        self.by_name = {s.name: s for s in sources}

    def choose(self, history: tuple[tuple[str, str], ...], budget: int) -> tuple[str | None, float]:
        """只用校准模型规划；安全授权由运行时的权威证据判定器完成。"""
        if budget < 0:
            raise ValueError("预算不能为负")
        used = {name for name, _ in history}
        if len(used) != len(history) or not used <= self.by_name.keys():
            raise ValueError("查询历史包含重复或未知来源")
        if any(value not in {"route_a", "route_b", "commit", "absent", "pending"}
               for _, value in history):
            raise ValueError("未知查询结果")
        if any(kind == "commit" for _, kind in history):
            return None, 1.0

        @lru_cache(None)
        def search(trace: tuple[tuple[str, str], ...], remaining: int) -> tuple[str | None, float]:
            possible = [w for w in self.model
                        if all(w.reply(name) == answer for name, answer in trace)]
            total = sum(w.weight for w in possible)
            if total == 0:
                return None, 0.0
            if any(answer == "commit" for _, answer in trace):
                return None, 1.0
            checked = {name for name, _ in trace}
            best: tuple[str | None, float] = (None, 0.0)
            for source in self.sources:
                if source.name in checked or source.cost > remaining:
                    continue
                branches = sorted({w.reply(source.name) for w in possible})
                value = 0.0
                for outcome in branches:
                    subset = sum(w.weight for w in possible if w.reply(source.name) == outcome)
                    if outcome == "commit":
                        fraction = 1.0
                    else:
                        _, fraction = search(trace + ((source.name, outcome),),
                                             remaining - source.cost)
                    value += subset / total * fraction
                if value > best[1] + 1e-12:
                    best = source.name, value
            return best

        return search(history, budget)

    def execute(self, context: Context, budget: int,
                fetch: Callable[[str], Reading]) -> Outcome:
        """模型只选查询；真正的确认必须由独立认证的正向证据驱动。"""
        history: tuple[tuple[str, str], ...] = ()
        spent = 0
        used: list[str] = []
        while True:
            name, _ = self.choose(history, budget - spent)
            if name is None:
                return Outcome(Decision.STOP, tuple(used), spent, "无可安全执行的后续查询")
            source = self.by_name[name]
            reading = fetch(name)
            spent += source.cost
            used.append(name)
            if not isinstance(reading, Reading) or reading.kind not in {
                    "route_a", "route_b", "commit", "absent", "pending"}:
                return Outcome(Decision.STOP, tuple(used), spent, "未识别的证据响应")
            if reading.kind == "commit":
                if not source.positive_authority or reading.evidence is None:
                    return Outcome(Decision.STOP, tuple(used), spent, "缺少可信提交权威")
                verdict = decide(context, (reading.evidence,))
                return Outcome(verdict.action if verdict.action == Decision.CONFIRM
                               else Decision.STOP, tuple(used), spent, verdict.reason)
            if reading.evidence is not None:
                return Outcome(Decision.STOP, tuple(used), spent, "非提交响应夹带意外证明")
            history += ((name, reading.kind),)
