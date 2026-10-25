"""真实 HTTP 在途旧请求的 B5/RAGuard 严格配对实验。"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, asdict
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import urllib.request

from bench.runner import raw_call
from bench.window_live import METHODS, ORIGIN_SCOPE, WindowClient, engine_call, window_server, write_csv
from raguard.model import Call
from raguard.transport import UncertainResult

# 每个条件单独启动服务端，保证两套恢复者不能共享物理效果。
CONDITIONS = (
    ("after_commit", 3, 0, 6, "same", True),
    ("after_commit", 6, 0, 6, "same", True),
    ("before_commit", 3, 0, 6, "same", True),
    ("inflight", 3, 0, 6, "same", True),
    ("inflight", 3, 0, 9, "same", True),
    ("after_commit", 3, 1, 6, "moved", True),
    ("after_commit", 3, 0, 6, "same", False),
)
STRATEGIES = ("unaware", "client_age", "verified")


@dataclass(frozen=True)
class Contract:
    minimum_ttl: int | None
    scope: str
    time_error_bound: int
    old_request_finished: bool

    def permits(self, client_time: int, target_scope: str) -> bool:
        if self.minimum_ttl is None or not self.old_request_finished or target_scope != self.scope:
            return False
        return client_time - self.time_error_bound >= 0 and client_time + self.time_error_bound < self.minimum_ttl


def admitted(strategy: str, client_time: int, scope: str, contract: Contract) -> bool:
    if strategy == "unaware":
        return True
    if strategy == "client_age":
        return 0 <= client_time < 6
    if strategy == "verified":
        return contract.permits(client_time, scope)
    raise ValueError("未知准入策略")


class StudyClient(WindowClient):
    def __init__(self, origin: str, fault: str):
        super().__init__(origin, drop_count=2 if fault != "inflight" else 0,
                         drop_point="after_commit" if fault == "inflight" else fault,
                         scope=ORIGIN_SCOPE)
        self.fault = fault
        self.initial = True
        self.sent = False
        self.timeout = 0.10

    def execute(self, call: Call, policy) -> dict:
        if self.fault == "inflight" and self.initial:
            if self.sent:
                # 首次连接已经中断，不能把新的本地请求伪装成旧在途请求。
                raise UncertainResult("实验链路已断开")
            self.sent = True
        return super().execute(call, policy)


def control(origin: str, endpoint: str, payload: dict | None = None) -> dict:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(origin + "/v1/lab/" + endpoint, data=body,
                                 headers={"Content-Type": "application/json"},
                                 method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=2) as response:
        return json.load(response)


def pending(origin: str, target: int) -> None:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if control(origin, "state")["pending"] == target:
            return
        time.sleep(0.005)
    raise TimeoutError("服务端在途状态超时")


@dataclass(frozen=True)
class Episode:
    condition: str
    method: str
    strategy: str
    final_status: int
    blocked: int
    physical_effects: int
    duplicate_effects: int
    wrong_scope_effects: int
    unsafe_confirmation: int
    safe_confirmation: int
    unresolved: int
    network_attempts: int


def run_case(binary: Path, method: str, strategy: str, condition: tuple) -> Episode:
    fault, tick, offset, ttl, scope, guaranteed = condition
    condition_id = f"{fault}-{tick}-{offset}-{ttl}-{scope}-{int(guaranteed)}"
    with window_server(binary, ttl=ttl) as origin, tempfile.TemporaryDirectory() as work:
        client = StudyClient(origin, fault)
        raw = raw_call("orders.create")
        workdir = Path(work)
        if fault == "inflight":
            control(origin, "hold", {})
        engine_call(method, workdir, client, raw)
        if fault == "inflight":
            pending(origin, 1)
        if control(origin, "advance", {"ticks": tick})["server_tick"] != tick:
            raise RuntimeError("服务器时间推进不一致")
        target_scope = ORIGIN_SCOPE if scope == "same" else "region-b"
        contract = Contract(6 if guaranteed else None, ORIGIN_SCOPE, 1, fault != "inflight")
        permitted = admitted(strategy, tick + offset, target_scope, contract)
        client.initial = False
        client.scope = target_scope
        client.drop_count = 0
        final = engine_call(method, workdir, client, raw) if permitted else 409
        if fault == "inflight":
            control(origin, "advance", {"ticks": ttl})
            control(origin, "release", {})
            pending(origin, 0)
        truth = control(origin, "effects")
    effects = truth["effects"]
    correct = sum(e["scope"] == ORIGIN_SCOPE for e in effects)
    wrong = len(effects) - correct
    duplicate = max(0, len(effects) - 1)
    unsafe = bool(wrong or duplicate)
    return Episode(condition_id, method, strategy, final, int(not permitted),
                   len(effects), duplicate, wrong, int(final == 200 and unsafe),
                   int(final == 200 and correct == 1 and not unsafe),
                   int(final != 200), truth["attempts"])


def main() -> int:
    parser = argparse.ArgumentParser(description="B5/RAGuard 不确定性 HTTP 配对")
    parser.add_argument("--output", type=Path, default=Path("results/window_uncertainty"))
    args = parser.parse_args()
    if not shutil.which("go"):
        raise RuntimeError("需要 Go 1.21+")
    with tempfile.TemporaryDirectory() as tmp:
        binary = Path(tmp) / ("uncertainty.exe" if os.name == "nt" else "uncertainty")
        subprocess.run(["go", "build", "-o", str(binary), "./windowlab/cmd/uncertainty"],
                       cwd=Path(__file__).resolve().parents[1] / "mock",
                       check=True, timeout=120, capture_output=True)
        rows = [run_case(binary, method, strategy, condition)
                for condition in CONDITIONS
                for strategy in STRATEGIES for method in METHODS]
    args.output.mkdir(parents=True, exist_ok=True)
    write_csv(args.output / "episodes.csv", [asdict(row) for row in rows])
    summaries = []
    for strategy in STRATEGIES:
        for method in METHODS:
            part = [r for r in rows if r.strategy == strategy and r.method == method]
            summaries.append({
                "strategy": strategy, "method": method, "cases": len(part),
                "safe_confirmation": sum(r.safe_confirmation for r in part),
                "duplicates": sum(r.duplicate_effects for r in part),
                "wrong_scope": sum(r.wrong_scope_effects for r in part),
                "unsafe_confirmation": sum(r.unsafe_confirmation for r in part),
                "unresolved": sum(r.unresolved for r in part),
                "network_attempts": sum(r.network_attempts for r in part),
            })
    write_csv(args.output / "summary.csv", summaries)
    (args.output / "fault_contracts.json").write_text(json.dumps({
        "conditions": CONDITIONS, "strategies": STRATEGIES,
        "actual_ttl_not_visible_to_recovery": True,
        "oracle_not_visible_to_recovery": True,
        "paired_methods": METHODS,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# 不确定性配对实验", "",
             "受控时间与晚提交压力用例；不代表真实厂商事故率或独立统计样本。",
             "准入条件完全共享，不能归因于 RAGuard 独有算法。", "",
             "| 方法 | 策略 | 案例 | 安全确认 | 重复效果 | 错作用域 | 不安全确认 | 未完成 |",
             "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for item in summaries:
        lines.append(f"| {item['method']} | {item['strategy']} | {item['cases']} | "
                     f"{item['safe_confirmation']} | {item['duplicates']} | "
                     f"{item['wrong_scope']} | {item['unsafe_confirmation']} | {item['unresolved']} |")
    lines.append("\n重复效果和错作用域可能重叠，不可相加。")
    (args.output / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
