"""严格配对校验：拒绝缺失、重复和跨条件错配。"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass

METHODS = ("b5", "raguard")
METRICS = (
    "safe_confirmation", "duplicate_effects", "wrong_scope_effects",
    "unsafe_confirmation", "unresolved", "network_attempts",
)


def condition_id(condition: tuple) -> str:
    fault, tick, offset, ttl, scope, guaranteed = condition
    return f"{fault}-{tick}-{offset}-{ttl}-{scope}-{int(guaranteed)}"


def paired_rows(rows: list, *, conditions: tuple,
                strategies: tuple, repeats: int) -> list[dict]:
    """每个条件与重复编号应恰好有 B5 和 RAGuard 各一条记录。"""
    if not conditions or not strategies or repeats < 1:
        raise ValueError("不合法的配对配置")
    expected = {
        (condition_id(condition), repeat, strategy, method)
        for repeat in range(repeats)
        for condition in conditions
        for strategy in strategies
        for method in METHODS
    }
    count = len(conditions) * repeats * len(strategies) * len(METHODS)
    if len(expected) != count:
        raise ValueError("实验条件自身存在重复")

    index = {}
    for row in rows:
        data = asdict(row) if is_dataclass(row) else dict(row)
        key = (data["condition"], data["repeat"], data["strategy"], data["method"])
        if key in index:
            raise ValueError(f"重复实验记录：{key}")
        index[key] = data
    if index.keys() != expected:
        missing = len(expected - index.keys())
        extra = len(index.keys() - expected)
        raise ValueError(f"实验配对不完整：缺少 {missing}，意外 {extra}")

    compared = []
    for condition in conditions:
        cid = condition_id(condition)
        for repeat in range(repeats):
            for strategy in strategies:
                b5 = index[(cid, repeat, strategy, "b5")]
                rag = index[(cid, repeat, strategy, "raguard")]
                record = {
                    "condition": cid, "repeat": repeat, "strategy": strategy,
                    "same_outcome": int(all(b5[m] == rag[m] for m in METRICS[:5])),
                }
                for metric in METRICS:
                    record[f"b5_{metric}"] = b5[metric]
                    record[f"raguard_{metric}"] = rag[metric]
                    record[f"delta_{metric}"] = rag[metric] - b5[metric]
                compared.append(record)
    return compared
