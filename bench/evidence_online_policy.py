"""证据来源的滑动窗口 UCB 对照，不是新的 ECF 算法。"""

from __future__ import annotations

from dataclasses import dataclass
from math import log, sqrt


@dataclass(frozen=True)
class Choice:
    source: str
    cost: int


class SlidingWindowUCB:
    """只读取过去实际选择来源的正向证据反馈，不访问评估真值。"""

    def __init__(self, choices: tuple[Choice, ...], *, window: int = 8,
                 exploration: float = 0.4):
        if not choices or len({c.source for c in choices}) != len(choices):
            raise ValueError("来源必须非空且不重复")
        if any(c.cost <= 0 for c in choices):
            raise ValueError("每次查询成本必须为正")
        if window < len(choices) or not 0 <= exploration <= 2:
            raise ValueError("无效的滑动窗口或探索系数")
        self.choices = choices
        self.window = window
        self.exploration = exploration
        self.history: list[tuple[str, int]] = []

    def select(self) -> str:
        """不观察未来故障、物理真值或分布切换时刻。"""
        recent = self.history[-self.window:]
        counts = {c.source: 0 for c in self.choices}
        rewards = {c.source: 0 for c in self.choices}
        for name, observed in recent:
            counts[name] += 1
            rewards[name] += observed
        for item in self.choices:
            if counts[item.source] == 0:
                return item.source

        horizon = len(recent)
        def score(item: Choice) -> float:
            n = counts[item.source]
            # 成功回读/成本：负回读只作为调度反馈，绝不证明未执行。
            estimate = (rewards[item.source] / n) / item.cost
            bonus = self.exploration * sqrt(log(horizon + 1) / n)
            return estimate + bonus
        return max(self.choices, key=lambda item: score(item)).source

    def observe(self, source: str, positive_readback: bool) -> None:
        if source not in {c.source for c in self.choices}:
            raise ValueError("未知证据来源")
        if type(positive_readback) is not bool:
            raise ValueError("反馈必须来自已经执行的证据查询")
        self.history.append((source, int(positive_readback)))
