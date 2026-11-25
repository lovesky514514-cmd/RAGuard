"""Stage 6：旧请求仍在途时，多恢复者与错误时间假设的真实 HTTP 配对实验。"""

from __future__ import annotations

import argparse
import csv
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import random
import subprocess
import tempfile
import threading

from bench.runner import raw_call
from bench.window_live import METHODS, ORIGIN_SCOPE, WindowClient, engine_call, window_server
from bench.window_uncertainty import Contract, StudyClient, admitted, control, pending, release_held
from raguard.model import Call

ROOT = Path(__file__).resolve().parents[1]
STRATEGIES = ("unaware", "client_age", "verified")


@dataclass(frozen=True)
class Condition:
    name: str
    ttl: int
    recovery_tick: int
    release_tick: int
    clock_offset: int
    workers: int
    scope: str
    dedupe: bool
    known_guarantee: bool


CONDITIONS = (
    Condition("native_early_release", 6, 3, 5, 0, 2, "same", True, True),
    Condition("native_late_commit", 6, 3, 10, 0, 2, "same", True, True),
    Condition("native_clock_under", 6, 6, 12, -3, 2, "same", True, True),
    Condition("native_clock_over", 6, 3, 10, 3, 2, "same", True, True),
    Condition("native_scope_move", 6, 3, 10, 0, 2, "moved", True, True),
    Condition("no_dedupe_two", 6, 3, 10, 0, 2, "same", False, False),
    Condition("no_dedupe_four", 6, 3, 10, 0, 4, "same", False, False),
    Condition("native_four_workers", 6, 3, 10, 0, 4, "same", True, True),
    Condition("unknown_guarantee", 6, 3, 10, 0, 2, "same", True, False),
)


CONDITION_BY_NAME = {c.name: c for c in CONDITIONS}


@dataclass(frozen=True)
class Episode:
    repeat: int
    condition: str
    engine: str
    strategy: str
    workers: int
    provider_dedupe: int
    known_guarantee: int
    ttl: int
    recovery_tick: int
    release_tick: int
    client_clock_offset: int
    scope: str
    original_pending_before_recovery: int
    initial_status: int
    allowed_by_policy: int
    worker_successes: int
    worker_waits: int
    worker_unknowns: int
    worker_statuses: str
    physical_effects: int
    duplicate_effects: int
    wrong_scope_effects: int
    safe_completion: int
    unsafe_confirmation: int
    unresolved: int
    attempted_http_writes: int
    late_original_effects: int
    initial_http_writes: int
    recovery_http_writes: int


def can_recover(condition: Condition, strategy: str) -> bool:
    """两引擎获得同一保护期、时钟偏差、旧请求存活信息与域。"""
    if condition not in CONDITIONS or strategy not in STRATEGIES:
        raise ValueError("未知实验条件或策略")
    contract = Contract(
        minimum_ttl=condition.ttl if condition.known_guarantee and condition.dedupe else None,
        scope=ORIGIN_SCOPE,
        time_error_bound=2,
        old_request_finished=False,
    )
    scope = ORIGIN_SCOPE if condition.scope == "same" else "region-b"
    return admitted(strategy, condition.recovery_tick + condition.clock_offset,
                    scope, contract)


def run_one(binary: Path, condition: Condition, engine: str, strategy: str,
            repeat: int) -> Episode:
    """线程同时放行，但真实 HTTP 写入交错与 SQLite 争用由执行器决定。"""
    if engine not in METHODS or type(repeat) is not int or repeat < 1:
        raise ValueError("非法方法或重复编号")
    allow = can_recover(condition, strategy)
    extra = () if condition.dedupe else ("--no-dedupe",)
    with window_server(binary, ttl=condition.ttl, extra_args=extra) as origin, \
            tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        raw = raw_call("orders.create")
        expected = Call.parse(raw)
        control(origin, "hold", {})
        first = StudyClient(origin, "inflight")
        initial_status = engine_call(engine, work, first, raw)
        pending(origin, 1)
        initial = control(origin, "effects")
        if initial_status != 409 or len(initial["effects"]) != 0 or initial["attempts"] != 0:
            raise AssertionError("初始旧请求不是被保留在途的未知写入")
        tick = control(origin, "advance", {"ticks": condition.recovery_tick})["server_tick"]
        if tick != condition.recovery_tick:
            raise AssertionError("服务端模拟时钟失准")
        statuses = []
        if allow:
            target_scope = ORIGIN_SCOPE if condition.scope == "same" else "region-b"
            barrier = threading.Barrier(condition.workers + 1, timeout=10)

            def worker(_index: int) -> int:
                client = WindowClient(origin, drop_count=0, drop_point="after_commit",
                                      scope=target_scope)
                barrier.wait()
                return engine_call(engine, work, client, raw)

            with ThreadPoolExecutor(max_workers=condition.workers) as executor:
                futures = [executor.submit(worker, i) for i in range(condition.workers)]
                barrier.wait()
                statuses = [future.result(timeout=25) for future in futures]
        before_release = control(origin, "effects")
        if control(origin, "state")["pending"] != 1:
            raise AssertionError("旧请求在恢复判定前被意外释放")
        if condition.release_tick < tick:
            raise AssertionError("旧请求释放时间早于恢复")
        advanced = control(origin, "advance", {"ticks": condition.release_tick - tick})
        if advanced["server_tick"] != condition.release_tick:
            raise AssertionError("释放阶段时间错误")
        release_held(origin)
        pending(origin, 0)
        truth = control(origin, "effects")

    effects = truth["effects"]
    if not effects or truth["attempts"] != before_release["attempts"] + 1:
        raise AssertionError("晚提交请求未被服务端完整执行")
    matches = sum((e["scope"], e["key"], e["fingerprint"]) ==
                  (ORIGIN_SCOPE, expected.stable_key, expected.fingerprint) for e in effects)
    wrong = len(effects) - matches
    duplicates = max(0, len(effects) - 1)
    successes = statuses.count(200)
    waits = statuses.count(202)
    unknowns = len(statuses) - successes - waits
    if any(x not in (200, 202, 409) for x in statuses):
        raise ValueError("恢复请求产生未定义状态")
    safe = int(successes > 0 and matches == 1 and wrong == 0 and duplicates == 0)
    unsafe = int(successes > 0 and (matches != 1 or wrong > 0 or duplicates > 0))
    return Episode(
        repeat, condition.name, engine, strategy, condition.workers,
        int(condition.dedupe), int(condition.known_guarantee), condition.ttl,
        condition.recovery_tick, condition.release_tick, condition.clock_offset,
        condition.scope, 1, initial_status, int(allow), successes, waits, unknowns,
        json.dumps(sorted(statuses)), len(effects), duplicates, wrong, safe, unsafe,
        int(successes == 0), truth["attempts"],
        len(effects) - len(before_release["effects"]), initial["attempts"],
        before_release["attempts"] - initial["attempts"],
    )


def validate_row(row: Episode) -> None:
    if row.condition not in CONDITION_BY_NAME or row.engine not in METHODS or row.strategy not in STRATEGIES:
        raise ValueError("非法已归档条件")
    condition = CONDITION_BY_NAME[row.condition]
    states = json.loads(row.worker_statuses)
    successes = states.count(200)
    waits = states.count(202)
    wrong = row.wrong_scope_effects
    duplicates = max(0, row.physical_effects - 1)
    if (row.workers != condition.workers or row.provider_dedupe != int(condition.dedupe)
            or row.initial_status != 409 or row.original_pending_before_recovery != 1
            or row.initial_http_writes != 0 or row.allowed_by_policy != int(can_recover(condition, row.strategy))
            or len(states) != (condition.workers if row.allowed_by_policy else 0)
            or row.worker_successes != successes or row.worker_waits != waits
            or row.worker_unknowns != len(states) - successes - waits
            or row.physical_effects < 1 or row.late_original_effects not in (0, 1)
            or row.late_original_effects > row.physical_effects
            or row.duplicate_effects != duplicates
            or row.attempted_http_writes != row.recovery_http_writes + 1
            or wrong < 0 or wrong > row.physical_effects
            or row.safe_completion != int(successes > 0 and row.physical_effects == 1 and wrong == 0)
            or row.unsafe_confirmation != int(successes > 0 and
                (row.physical_effects > 1 or wrong > 0 or row.physical_effects - wrong != 1))
            or row.unresolved != int(successes == 0)):
        raise ValueError("物理真值或并发结果自检失败")

def validate(rows: list[Episode], repeats: int) -> None:
    expected = {(r, c.name, method, strategy) for r in range(1, repeats + 1)
                for c in CONDITIONS for method in METHODS for strategy in STRATEGIES}
    keys = [(r.repeat, r.condition, r.engine, r.strategy) for r in rows]
    if len(keys) != len(set(keys)) or set(keys) != expected:
        raise ValueError("实验矩阵不完整、存在重复或非预注册条件")
    for row in rows:
        validate_row(row)
    for repeat in range(1, repeats + 1):
        for condition in CONDITIONS:
            for strategy in STRATEGIES:
                pair = [x for x in rows if (x.repeat, x.condition, x.strategy) ==
                        (repeat, condition.name, strategy)]
                if {x.engine for x in pair} != set(METHODS):
                    raise ValueError("强基线配对缺失")


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def run(output: Path, repeats: int = 2, seed: int = 20261124,
        quick: bool = False) -> list[Episode]:
    if type(repeats) is not int or not 1 <= repeats <= 20 or type(seed) is not int:
        raise ValueError("重复次数或种子非法")
    with tempfile.TemporaryDirectory() as folder:
        binary = Path(folder) / ("uncertainty.exe" if os.name == "nt" else "uncertainty")
        subprocess.run(["go", "build", "-o", str(binary), "./windowlab/cmd/uncertainty"],
                       cwd=ROOT / "mock", timeout=120, check=True, capture_output=True)
        conditions = list(CONDITIONS[:3] if quick else CONDITIONS)
        matrix = [(c, strategy, method) for c in conditions
                  for strategy in STRATEGIES for method in METHODS]
        rng = random.Random(seed)
        rows = []
        for repeat in range(1, repeats + 1):
            rng.shuffle(matrix)
            for condition, strategy, method in matrix:
                result = run_one(binary, condition, method, strategy, repeat)
                rows.append(result)
                print(f"[{repeat}/{repeats}] {condition.name}/{strategy}/{method}: "
                      f"recovery_writes={result.recovery_http_writes}, "
                      f"effects={result.physical_effects}, success={result.worker_successes}",
                      flush=True)
    # 快速模式只使用子矩阵，不能混作完整研究结果。
    if not quick:
        validate(rows, repeats)
    output.mkdir(parents=True, exist_ok=True)
    write_csv(output / "episodes.csv", [asdict(row) for row in rows])
    lookup = {(r.repeat, r.condition, r.strategy, r.engine): r for r in rows}
    paired = []
    compare = ("worker_successes", "safe_completion", "unsafe_confirmation",
               "unresolved", "physical_effects", "duplicate_effects",
               "wrong_scope_effects", "attempted_http_writes")
    for repeat in range(1, repeats + 1):
        for c in (CONDITIONS[:3] if quick else CONDITIONS):
            for strategy in STRATEGIES:
                b5 = lookup[(repeat, c.name, strategy, "b5")]
                rag = lookup[(repeat, c.name, strategy, "raguard")]
                record = {"repeat": repeat, "condition": c.name, "strategy": strategy}
                for field in compare:
                    record[f"b5_{field}"] = getattr(b5, field)
                    record[f"raguard_{field}"] = getattr(rag, field)
                    record[f"delta_{field}"] = getattr(rag, field) - getattr(b5, field)
                paired.append(record)
    write_csv(output / "paired_summary.csv", paired)
    grouped = []
    for strategy in STRATEGIES:
        for method in METHODS:
            relevant = [r for r in rows if r.strategy == strategy and r.engine == method]
            grouped.append({
                "strategy": strategy, "method": method, "episodes": len(relevant),
                "duplicate_effects": sum(r.duplicate_effects for r in relevant),
                "wrong_scope_effects": sum(r.wrong_scope_effects for r in relevant),
                "unsafe_confirmations": sum(r.unsafe_confirmation for r in relevant),
                "safe_completions": sum(r.safe_completion for r in relevant),
                "unresolved": sum(r.unresolved for r in relevant),
                "http_writes": sum(r.attempted_http_writes for r in relevant),
            })
    write_csv(output / "summary.csv", grouped)
    protocol = {
        "kind": "stage6_inflight_concurrent_recovery",
        "repeats": repeats, "order_seed": seed, "quick_subset_not_full": quick,
        "conditions": [asdict(x) for x in (CONDITIONS[:3] if quick else CONDITIONS)],
        "strategies": STRATEGIES, "methods": METHODS,
        "initial_old_request_kept_inflight": True,
        "old_client_timed_out_before_server_commit": True,
        "parallel_workers_barrier_synchronized_not_guaranteed_simultaneous_http": True,
        "contract_info_available_equally_to_both_engines": True,
        "no_evaluator_oracle_in_policy": True,
        "server_ticks_synthetic": True,
        "not_a_new_model_run": True,
        "not_vendor_ttl_or_statistical_incidence": True,
    }
    (output / "fault_contracts.json").write_text(
        json.dumps(protocol, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = ["# Stage 6 仍在途旧请求与多恢复者争用", "",
              "真实 Go HTTP 沙箱，真实 B5/Gateway 持久化接口。实验由研究线程放行屏障触发，非真实厂商案例。", "",
              "| 策略 | 引擎 | 方法级执行 | 重复效果 | 错作用域 | 不安全确认 | 安全完成 | 未解决 |",
              "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for x in grouped:
        report.append(f"| {x['strategy']} | {x['method']} | {x['episodes']} | "
                      f"{x['duplicate_effects']} | {x['wrong_scope_effects']} | "
                      f"{x['unsafe_confirmations']} | {x['safe_completions']} | "
                      f"{x['unresolved']} |")
    report.extend(["", "重复效果与不安全确认可重叠；不允许累加当独立事故。",
                   "旧请求在所有条件中均处于未结束的真实 Go HoldGate；安全策略共享同一事实。",
                   "研究中的 client_age 是不安全的仅客户端时间准入对照，不属于强基线本身。",
                   "保护期与时钟偏差来自预设模拟条件，不可外推到厂商 SLA。",
                   "结果不构成新的 exactly-once 算法、不声称 RAGuard 强于 B5，也不代表 LLM 推理。"])
    (output / "REPORT.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    file_names = ("episodes.csv", "paired_summary.csv", "summary.csv",
                  "fault_contracts.json", "REPORT.md")
    (output / "SHA256SUMS.json").write_text(json.dumps({
        name: sha256((output / name).read_bytes()).hexdigest() for name in file_names
    }, indent=2) + "\n", encoding="utf-8")
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage 6 多恢复者、在途旧请求与时钟偏差")
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--seed", type=int, default=20261124)
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("results/stage6_concurrent"))
    args = parser.parse_args()
    run(args.output, repeats=args.repeats, seed=args.seed, quick=args.quick)


if __name__ == "__main__":
    main()
