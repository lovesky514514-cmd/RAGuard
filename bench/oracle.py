"""业务状态真值判定器：不把 HTTP 200 当作任务成功。

它独立于网关判断；每个新的 effect_id 对应一次真实写入。
同一 effect_id 的重复日志不重复计算物理副作用。
"""
from dataclasses import dataclass
from collections import defaultdict


@dataclass(frozen=True)
class EffectEvent:
    kind: str  # commit | compensate
    effect_id: str
    business_key: str


@dataclass(frozen=True)
class Outcome:
    task_success: bool
    exactly_once_success: bool
    duplicate_executions: int
    unintended_executions: int
    missing_effects: int


def evaluate(events: list[EffectEvent], expected: dict[str, int]) -> Outcome:
    """根据物理写入与补偿判断最终状态；补偿不能抹除历史重复提交。"""
    if any(not isinstance(v, int) or isinstance(v, bool) or v < 0 for v in expected.values()):
        raise ValueError("expected counts must be nonnegative integers")
    committed: dict[str, str] = {}
    compensated: set[str] = set()
    for event in events:
        if not event.effect_id or not event.business_key:
            raise ValueError("missing stable effect identifier")
        if event.kind == "commit":
            if event.effect_id in committed:
                if committed[event.effect_id] != event.business_key:
                    raise ValueError("effect id mapped to two business keys")
                continue
            committed[event.effect_id] = event.business_key
        elif event.kind == "compensate":
            if event.effect_id not in committed:
                raise ValueError("compensation without a known commit")
            if committed[event.effect_id] != event.business_key:
                raise ValueError("compensation business key mismatch")
            compensated.add(event.effect_id)
        else:
            raise ValueError(f"unknown event kind: {event.kind}")
    ever = defaultdict(int)
    live = defaultdict(int)
    for effect_id, key in committed.items():
        ever[key] += 1
        if effect_id not in compensated:
            live[key] += 1
    duplicates = sum(max(0, ever[k] - n) for k, n in expected.items())
    unintended = sum(n for k, n in ever.items() if k not in expected)
    missing = sum(max(0, n - live[k]) for k, n in expected.items())
    task_success = not unintended and all(live[k] == n for k, n in expected.items())
    exactly_once = task_success and duplicates == 0
    return Outcome(task_success, exactly_once, duplicates, unintended, missing)
