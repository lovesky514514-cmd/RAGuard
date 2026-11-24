"""Stage 5：幂等契约失效时真实 Go HTTP 物理效果的公平配对实验。"""

from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import random
from pathlib import Path
import tempfile

from bench.runner import raw_call
from bench.window_live import (METHODS, ORIGIN_SCOPE, WindowClient, VerifiedWindow,
                               build_binary, engine_call, window_server)
from bench.window_uncertainty import control
from raguard.model import Call
from raguard.transport import UncertainResult

STRATEGIES = ("unaware", "contract")


@dataclass(frozen=True)
class Condition:
    name: str
    fault: str
    ttl: int
    recovery_tick: int
    scope: str
    dedupe: bool
    known_guarantee: bool


CONDITIONS = (
    Condition("native_inside", "after_commit", 6, 2, "same", True, True),
    Condition("native_at_expiry", "after_commit", 6, 6, "same", True, True),
    Condition("native_outside", "after_commit", 6, 8, "same", True, True),
    Condition("native_cross_scope", "after_commit", 6, 2, "moved", True, True),
    Condition("no_dedupe", "after_commit", 6, 2, "same", False, False),
    Condition("unknown_guarantee", "after_commit", 6, 2, "same", True, False),
    Condition("before_commit_inside", "before_commit", 6, 2, "same", True, True),
    Condition("before_commit_no_dedupe", "before_commit", 6, 2, "same", False, False),
)


class SingleWriteFaultClient(WindowClient):
    """初始故障阶段只允许一次物理写入，屏蔽引擎内部重试的混杂因素。"""

    def __init__(self, origin: str, fault: str):
        super().__init__(origin, drop_count=1, drop_point=fault, scope=ORIGIN_SCOPE)
        self.initial = True
        self.initial_sent = False
        self.blocked_initial_attempts = 0

    def execute(self, call: Call, policy) -> dict:
        if self.initial:
            if self.initial_sent:
                self.blocked_initial_attempts += 1
                raise UncertainResult("初始链路已断开：研究适配器阻断后续发送")
            self.initial_sent = True
        return super().execute(call, policy)

    def resume(self, scope: str) -> None:
        if scope not in ("same", "moved") or not self.initial_sent:
            raise ValueError("恢复阶段或作用域非法")
        self.scope = ORIGIN_SCOPE if scope == "same" else "region-b"
        self.drop_count = 0
        self.initial = False


@dataclass(frozen=True)
class Episode:
    repeat: int
    condition: str
    strategy: str
    engine: str
    fault: str
    provider_dedupe: int
    known_guarantee: int
    ttl: int
    recovery_tick: int
    scope: str
    initial_status: int
    final_status: int
    policy_admitted: int
    initial_effects: int
    physical_effects: int
    duplicate_effects: int
    wrong_scope_effects: int
    false_confirmation: int
    unsafe_confirmation: int
    safe_confirmation: int
    unresolved: int
    http_writes_initial: int
    http_writes_recovery: int
    http_writes_total: int
    blocked_internal_attempts: int
    evidence_reads: int


def permitted(condition: Condition, strategy: str) -> bool:
    """两套恢复引擎共享同一契约准入策略，不允许读取评价器物理真值。"""
    if strategy not in STRATEGIES:
        raise ValueError("未知准入策略")
    if strategy == "unaware":
        return True
    contract = VerifiedWindow(
        earliest_possible_accept_tick=0,
        min_retention_ticks=condition.ttl if condition.known_guarantee and condition.dedupe else None,
        original_scope=ORIGIN_SCOPE,
    )
    next_scope = ORIGIN_SCOPE if condition.scope == "same" else "region-b"
    return contract.permits(tick=condition.recovery_tick, scope=next_scope)


def run_one(binary: Path, condition: Condition, engine: str, strategy: str,
            repeat: int) -> Episode:
    if (condition not in CONDITIONS or engine not in METHODS or strategy not in STRATEGIES
            or type(repeat) is not int or repeat < 1):
        raise ValueError("不在冻结协议内的条件")
    extra = () if condition.dedupe else ("--no-dedupe",)
    with window_server(binary, ttl=condition.ttl, extra_args=extra) as origin, \
            tempfile.TemporaryDirectory() as tmp:
        raw = raw_call("orders.create")
        call = Call.parse(raw)
        client = SingleWriteFaultClient(origin, condition.fault)
        initial = engine_call(engine, Path(tmp), client, raw)
        before = control(origin, "effects")
        if initial != 409 or not client.initial_sent:
            raise AssertionError("初始故障未按冻结协议出现")
        if before["attempts"] != int(condition.fault == "after_commit"):
            raise AssertionError("初始阶段额外写入污染了真实 HTTP 实验")
        if len(before["effects"]) != int(condition.fault == "after_commit"):
            raise AssertionError("独立效果账本与故障注入不符")
        tick = control(origin, "advance", {"ticks": condition.recovery_tick})["server_tick"]
        if tick != condition.recovery_tick:
            raise AssertionError("服务器时间推进异常")
        admission = permitted(condition, strategy)
        client.resume(condition.scope)
        final = engine_call(engine, Path(tmp), client, raw) if admission else 409
        truth = control(origin, "effects")
    effects = truth["effects"]
    matched = sum((e["scope"], e["key"], e["fingerprint"]) ==
                  (ORIGIN_SCOPE, call.stable_key, call.fingerprint) for e in effects)
    wrong = len(effects) - matched
    duplicates = max(0, len(effects) - 1)
    safe = int(final == 200 and matched == 1 and wrong == 0 and duplicates == 0)
    return Episode(
        repeat, condition.name, strategy, engine, condition.fault,
        int(condition.dedupe), int(condition.known_guarantee),
        condition.ttl, condition.recovery_tick, condition.scope,
        initial, final, int(admission), len(before["effects"]), len(effects),
        duplicates, wrong, int(final == 200 and matched != 1),
        int(final == 200 and (matched != 1 or duplicates > 0 or wrong > 0)),
        safe, int(final != 200), before["attempts"],
        truth["attempts"] - before["attempts"], truth["attempts"],
        client.blocked_initial_attempts, client.readback_checks,
    )


def validate(rows: list[Episode], repeats: int) -> None:
    expected = {(n, c.name, strategy, engine)
                for n in range(1, repeats + 1) for c in CONDITIONS
                for strategy in STRATEGIES for engine in METHODS}
    keys = [(r.repeat, r.condition, r.strategy, r.engine) for r in rows]
    if len(keys) != len(set(keys)) or set(keys) != expected:
        raise ValueError("缺少配对条件、重复行或非法条件")
    for row in rows:
        c = next(c for c in CONDITIONS if c.name == row.condition)
        admission = permitted(c, row.strategy)
        expected_initial = int(c.fault == "after_commit")
        matched = row.physical_effects - row.wrong_scope_effects
        if (row.initial_status != 409 or row.final_status not in (200, 409)
                or row.policy_admitted != int(admission)
                or row.initial_effects != expected_initial
                or row.http_writes_initial != expected_initial
                or row.http_writes_total != row.http_writes_initial + row.http_writes_recovery
                or row.http_writes_recovery < 0
                or row.physical_effects < row.initial_effects
                or row.duplicate_effects != max(0, row.physical_effects - 1)
                or row.wrong_scope_effects < 0 or row.wrong_scope_effects > row.physical_effects
                or row.false_confirmation != int(row.final_status == 200 and matched != 1)
                or row.unsafe_confirmation != int(row.final_status == 200 and
                           (matched != 1 or row.duplicate_effects > 0 or row.wrong_scope_effects > 0))
                or row.safe_confirmation != int(row.final_status == 200 and
                           row.physical_effects == 1 and row.wrong_scope_effects == 0)
                or row.unresolved != int(row.final_status != 200)):
            raise ValueError(f"物理真值判分不一致：{row.condition}/{row.engine}")
        if not admission and (row.http_writes_recovery or row.final_status != 409):
            raise ValueError("契约阻断之后出现未经批准的写入")
    for repeat in range(1, repeats + 1):
        for c in CONDITIONS:
            for strategy in STRATEGIES:
                paired = [r for r in rows if (r.repeat, r.condition, r.strategy) ==
                          (repeat, c.name, strategy)]
                if set(r.engine for r in paired) != set(METHODS):
                    raise ValueError("B5/Gateway 配对不完整")


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def run(output: Path, *, repeats: int = 3, seed: int = 20261124) -> list[Episode]:
    if type(repeats) is not int or not 1 <= repeats <= 30 or type(seed) is not int:
        raise ValueError("重复次数或随机种子非法")
    with tempfile.TemporaryDirectory() as tmp:
        binary = build_binary(Path(tmp))
        rng = random.Random(seed)
        rows = []
        for repeat in range(1, repeats + 1):
            conditions = [(c, strategy) for c in CONDITIONS for strategy in STRATEGIES]
            rng.shuffle(conditions)
            for condition, strategy in conditions:
                methods = list(METHODS)
                rng.shuffle(methods)
                for engine in methods:
                    row = run_one(binary, condition, engine, strategy, repeat)
                    rows.append(row)
                    print(f"[{repeat}/{repeats}] {condition.name}/{strategy}/{engine}: "
                          f"effects={row.physical_effects}, duplicate={row.duplicate_effects}, "
                          f"status={row.final_status}", flush=True)
    validate(rows, repeats)
    output.mkdir(parents=True, exist_ok=True)
    write_csv(output / "episodes.csv", [asdict(r) for r in rows])
    grouped = []
    for c in CONDITIONS:
        for strategy in STRATEGIES:
            for engine in METHODS:
                part = [r for r in rows if (r.condition, r.strategy, r.engine) ==
                        (c.name, strategy, engine)]
                grouped.append({
                    "condition": c.name, "strategy": strategy, "engine": engine,
                    "episodes": len(part),
                    "duplicates": sum(r.duplicate_effects for r in part),
                    "wrong_scope": sum(r.wrong_scope_effects for r in part),
                    "unsafe_confirmation": sum(r.unsafe_confirmation for r in part),
                    "safe_confirmation": sum(r.safe_confirmation for r in part),
                    "unresolved": sum(r.unresolved for r in part),
                    "http_writes": sum(r.http_writes_total for r in part),
                })
    write_csv(output / "summary.csv", grouped)
    contract = {
        "kind": "stage5_provider_contract_boundary",
        "conditions": [asdict(c) for c in CONDITIONS],
        "strategies": list(STRATEGIES), "paired_methods": list(METHODS),
        "repeats": repeats, "order_seed": seed,
        "native_default_dedupe_unchanged": True,
        "no_dedupe_research_flag_only": True,
        "first_fault_one_network_submission_max": True,
        "old_initial_request_finished_before_recovery": True,
        "contract_gate_shared_for_b5_and_raguard": True,
        "contract_guarantee_is_harness_simulation_not_vendor_attestation": True,
        "physics_evaluator_only": True,
        "no_real_llm_in_this_study": True,
        "not_actual_third_party_provider": True,
        "not_estimate_of_production_failure_probability": True,
    }
    (output / "fault_contracts.json").write_text(
        json.dumps(contract, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# Stage 5：幂等契约边界配对实验", "",
             f"{len(CONDITIONS)} 个固定条件 × {len(STRATEGIES)} 策略 × "
             f"{len(METHODS)} 引擎 × {repeats} 次重复＝{len(rows)} 条方法级执行。", "",
             "| 契约条件 | 策略 | 引擎 | 运行 | 重复效果 | 错作用域 | 不安全确认 | 安全确认 | 未解决 |",
             "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for x in grouped:
        lines.append(f"| {x['condition']} | {x['strategy']} | {x['engine']} | "
                     f"{x['episodes']} | {x['duplicates']} | {x['wrong_scope']} | "
                     f"{x['unsafe_confirmation']} | {x['safe_confirmation']} | "
                     f"{x['unresolved']} |")
    lines += ["", "真实 HTTP 写入仅涉及本机隔离 Go 服务；默认 Go Server 去重行为不变。",
              "禁用去重使用研究模式；契约准入在 B5 和 Gateway 上完全相同。",
              "旧请求已结束与可信契约属于本试验前提；没有验证旧请求仍在途及不确定时钟。",
              "固定场景重复不表示真实故障概率，也不能证明 RAGuard 独自优于 B5。"]
    (output / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    names = ("episodes.csv", "summary.csv", "fault_contracts.json", "REPORT.md")
    (output / "SHA256SUMS.json").write_text(json.dumps({
        name: sha256((output / name).read_bytes()).hexdigest() for name in names
    }, indent=2) + "\n", encoding="utf-8")
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage 5 TTL/作用域/无去重研究")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20261124)
    parser.add_argument("--output", type=Path, default=Path("results/stage5_contract"))
    args = parser.parse_args()
    run(args.output, repeats=args.repeats, seed=args.seed)


if __name__ == "__main__":
    main()
