"""隔离恢复适配器将证据审计结果送入真实 B5/Gateway 持久账本。"""

from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import tempfile

from bench.evidence_audit_http import VARIANTS, audit_response, read_receipt
from bench.evidence_frontier import Context, Decision
from bench.runner import raw_call
from bench.window_live import METHODS, ORIGIN_SCOPE, WindowClient, engine_call, window_server
from bench.window_uncertainty import control
from raguard.model import Call
from raguard.transport import UncertainResult

ROOT = Path(__file__).resolve().parents[1]
FAULTS = ("before_commit", "after_commit")


class AuditedRecoveryClient(WindowClient):
    """双方使用相同只读审计和禁止二次写入的实验性执行屏障。"""

    def __init__(self, origin: str, fault: str, variant: str):
        if variant not in VARIANTS:
            raise ValueError("无效证据变体")
        super().__init__(origin, drop_count=2, drop_point=fault, scope=ORIGIN_SCOPE)
        self.variant = variant
        self.recovering = False
        self.tick = 0
        self.audit_queries = 0
        self.blocked_writes = 0
        self.audit_action: Decision | None = None
        self.audit_reason = ""

    def begin_recovery(self, tick: int) -> None:
        if self.recovering or type(tick) is not int or tick < 0:
            raise ValueError("恢复阶段时间非法或重复进入")
        self.recovering = True
        self.tick = tick
        self.drop_count = 0

    def execute(self, call, policy):
        if self.recovering:
            # 这个屏障是双方共同实验约束，不冒充生产级执行 fence。
            self.blocked_writes += 1
            raise UncertainResult("审计恢复阶段禁止再次写入外部系统")
        return super().execute(call, policy)

    def readback(self, call, policy):
        del policy
        if not self.recovering or self.audit_queries:
            return None
        self.audit_queries += 1
        reply = read_receipt(self.origin, self.scope, call.stable_key, call.fingerprint)
        context = Context(call.stable_key, self.scope, call.fingerprint,
                          "trusted-loopback", 1, self.tick, False, False)
        action, reason = audit_response(context, reply, self.variant, self.tick)
        self.audit_action, self.audit_reason = action, reason
        if action != Decision.CONFIRM or reply is None:
            return None
        return {"effect_id": reply["effect_id"]}


@dataclass(frozen=True)
class Episode:
    engine: str
    fault: str
    variant: str
    initial_status: int
    final_status: int
    audit_decision: str
    physical_effects: int
    safe_confirmation: int
    unsafe_confirmation: int
    duplicate_effects: int
    blocked_recovery_writes: int
    queries: int
    real_execute_attempts: int
    added_execute_attempts: int
    unresolved: int
    audit_reason: str


def one_case(binary: Path, engine: str, fault: str, variant: str) -> Episode:
    if engine not in METHODS or fault not in FAULTS or variant not in VARIANTS:
        raise ValueError("实验条件不在预先定义的组合中")
    with window_server(binary, ttl=6, extra_args=("--evidence-mode", "matched")) as origin, \
            tempfile.TemporaryDirectory() as folder:
        raw = raw_call("orders.create")
        call = Call.parse(raw)
        client = AuditedRecoveryClient(origin, fault, variant)
        work = Path(folder)
        initial = engine_call(engine, work, client, raw)
        before = control(origin, "effects")
        tick = control(origin, "advance", {"ticks": 2})["server_tick"]
        client.begin_recovery(tick)
        final = engine_call(engine, work, client, raw)
        after = control(origin, "effects")

        before_effects = before["effects"]
        effects = after["effects"]
        if before_effects != effects:
            raise AssertionError("恢复阶段出现新的物理效果")
        added_attempts = after["attempts"] - before["attempts"]
        if added_attempts != 0:
            raise AssertionError("恢复阶段出现真实 HTTP 写请求")
        if len(effects) != (1 if fault == "after_commit" else 0):
            raise AssertionError("物理提交与故障语义不一致")
        if any((item["key"], item["fingerprint"], item["scope"])
               != (call.stable_key, call.fingerprint, ORIGIN_SCOPE)
               for item in effects):
            raise AssertionError("跨作用域或跨身份物理效果")
        if client.audit_queries != 1:
            raise AssertionError("恢复没有执行恰好一次审计查询")

        expect_confirm = variant == "valid" and fault == "after_commit"
        safe = final == 200 and len(effects) == 1
        unsafe = final == 200 and len(effects) != 1
        if safe != expect_confirm or unsafe:
            raise AssertionError("恢复状态与独立物理真值或预设审计条件不符")
        if client.audit_action == Decision.REISSUE:
            raise AssertionError("缺少权威 fence 却获得重发授权")
        if final not in (200, 409):
            raise AssertionError("恢复返回不受支持的状态码")
        return Episode(
            engine, fault, variant, initial, final,
            client.audit_action.value if client.audit_action else "no_audit",
            len(effects), int(safe), int(unsafe), max(0, len(effects) - 1),
            client.blocked_writes, client.audit_queries, after["attempts"],
            added_attempts, int(final != 200), client.audit_reason,
        )


def validate(rows: list[Episode]) -> None:
    expected = {(engine, fault, variant)
                for engine in METHODS for fault in FAULTS for variant in VARIANTS}
    keys = [(row.engine, row.fault, row.variant) for row in rows]
    if len(set(keys)) != len(keys) or set(keys) != expected:
        raise ValueError("B5/Gateway 审计恢复配对不完整")
    for fault in FAULTS:
        for variant in VARIANTS:
            left, right = (next(row for row in rows if row.engine == engine
                                and row.fault == fault and row.variant == variant)
                           for engine in METHODS)
            if (left.final_status, left.audit_decision, left.physical_effects) != (
                    right.final_status, right.audit_decision, right.physical_effects):
                raise ValueError("公平配对出现引擎结论差异")


def run(output: Path) -> list[Episode]:
    with tempfile.TemporaryDirectory() as temp:
        binary = Path(temp) / ("uncertainty.exe" if os.name == "nt" else "uncertainty")
        subprocess.run(["go", "build", "-o", str(binary), "./windowlab/cmd/uncertainty"],
                       cwd=ROOT / "mock", check=True, timeout=120, capture_output=True)
        rows = [one_case(binary, engine, fault, variant)
                for engine in METHODS for fault in FAULTS for variant in VARIANTS]
    validate(rows)
    output.mkdir(parents=True, exist_ok=True)
    with (output / "episodes.csv").open("w", newline="", encoding="utf-8") as target:
        writer = csv.DictWriter(target, fieldnames=Episode.__dataclass_fields__)
        writer.writeheader()
        writer.writerows(asdict(row) for row in rows)
    protocol = {
        "kind": "isolated_audit_in_actual_persistent_recovery_call",
        "engines": METHODS,
        "no_production_gateway_changes": True,
        "both_engines_block_recovery_writes": True,
        "failure_variants_injected_at_python_adapter": True,
        "positive_receipts_from_real_go_http_physical_log": True,
        "authentication": "trusted_loopback_only_no_signature",
        "no_fencing_or_safe_reissue_proof": True,
        "physical_truth_accessible_to_evaluator_only": True,
    }
    (output / "protocol.json").write_text(
        json.dumps(protocol, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = [
        "# 隔离恢复执行链中的真实 HTTP 证据审计", "",
        f"场景：{len(rows)}；安全确认：{sum(x.safe_confirmation for x in rows)}；"
        f"不安全确认：{sum(x.unsafe_confirmation for x in rows)}；"
        f"真实新增写入尝试：{sum(x.added_execute_attempts for x in rows)}。", "",
        "双方共享研究适配器的禁止二次写入屏障；它不是服务端执行屏障或 Gateway 原生安全改进。",
        "仅本地可信 Go HTTP 沙箱，回执没有数字签名；异常证据由 Python 实验端注入。",
        "业务状态最终由原版 B5 和 Gateway 的持久恢复调用生成，物理真值仅用于评分。",
    ]
    (output / "REPORT.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    files = ("episodes.csv", "protocol.json", "REPORT.md")
    (output / "SHA256SUMS.json").write_text(json.dumps(
        {name: sha256((output / name).read_bytes()).hexdigest() for name in files},
        indent=2) + "\n", encoding="utf-8")
    return rows


def main():
    parser = argparse.ArgumentParser(description="真实 Go HTTP 下的隔离审计恢复链")
    parser.add_argument("--output", type=Path, default=Path("results/audited_recovery_http"))
    args = parser.parse_args()
    run(args.output)


if __name__ == "__main__":
    main()
