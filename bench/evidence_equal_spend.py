"""以相同累计成本完成所有证据查询，不让预算耗尽伪装成策略表现。"""

from __future__ import annotations


class ExactSpend:
    """每轮恰好一次查询，且始终保留完成全部剩余查询的预算。"""

    def __init__(self, costs: dict[str, int], turns: int, budget: int):
        if not costs or any(type(c) is not int or c <= 0 for c in costs.values()):
            raise ValueError("来源成本必须是正整数")
        if type(turns) is not int or turns <= 0 or type(budget) is not int:
            raise ValueError("案例数和预算必须是整数")
        smallest, largest = min(costs.values()), max(costs.values())
        if not turns * smallest <= budget <= turns * largest:
            raise ValueError("预算无法覆盖每轮恰好一次查询")
        self.costs = dict(costs)
        self.turns = turns
        self.remaining = budget
        self.forced = 0
        self.spent = 0

    def select(self, preferred: str) -> tuple[str, bool]:
        if preferred not in self.costs or self.turns == 0:
            raise ValueError("未知来源或查询次数已耗尽")
        next_turns = self.turns - 1
        low, high = min(self.costs.values()), max(self.costs.values())
        feasible = [name for name, cost in self.costs.items()
                    if next_turns * low <= self.remaining - cost <= next_turns * high]
        if not feasible:
            raise AssertionError("预算可行域内部错误")
        if preferred in feasible:
            chosen = preferred
        else:
            chosen = min(feasible, key=lambda name: (
                abs(self.costs[name] - self.costs[preferred]),
                self.costs[name], name,
            ))
        forced = chosen != preferred
        self.forced += int(forced)
        self.remaining -= self.costs[chosen]
        self.spent += self.costs[chosen]
        self.turns = next_turns
        return chosen, forced

    def verify(self) -> None:
        if self.turns != 0 or self.remaining != 0:
            raise ValueError("仍有未完成回合或未花完的目标预算")
