"""通过独立 Go HTTP 物理效果日志核对证据审计与对抗回读。"""

from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, dataclass, replace
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request

from bench.evidence_audit import Label, Reading, Source, inspect
from bench.evidence_frontier import Context, Decision, Evidence, Kind

ROOT = Path(__file__).resolve().parents[1]
ENGINES = ("b5", "raguard")
FAULTS = ("before_commit", "after_commit")
VARIANTS = (
    "valid", "expired_receipt", "expired_source", "wrong_scope",
    "wrong_fingerprint", "wrong_identity", "untrusted", "weak_source",
    "conflicting", "clock_rollback", "malformed",
)


@dataclass(frozen=True)
class Episode:
    engine: str
    fault: str
    variant: str
    initial_status: int
    decision: str
    physical_effects: int
    safe_confirmation: int
    unsafe_confirmation: int
    duplicate_effects: int
    unresolved: int
    queries: int
    audit_reason: str
    wire_injected: int


def read_receipt(origin: str, scope: str, key: str, fingerprint: str) -> dict | None:
    params = urllib.parse.urlencode({"scope": scope, "key": key, "fingerprint": fingerprint})
    req = urllib.request.Request(origin + "/v1/evidence/commit?" + params)
    try:
        with urllib.request.urlopen(req, timeout=2) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def audit_response(context: Context, reply: dict | None, variant: str,
                   tick: int) -> tuple[Decision, str]:
    """对抗变体由研究代理层注入，绝不冒充 Go 服务端原生事实。"""
    if variant not in VARIANTS:
        raise ValueError("未知对抗变体")
    source = Source("trusted-loopback", 1, 1, can_confirm=True,
                    can_prove_nonexecution=True, proof_valid_through=tick + 2)
    if reply is None:
        verdict = inspect(context, source, Reading(Label.ABSENT), tick)
        return verdict.action, verdict.reason

    if not isinstance(reply, dict) or reply.get("status") != "committed":
        return Decision.STOP, "提交回读格式无效"
    if not isinstance(reply.get("effect_id"), str) or not reply["effect_id"]:
        return Decision.STOP, "物理效果标识缺失"
    if not all(isinstance(reply.get(name), str) and reply[name]
               for name in ("scope", "key", "fingerprint")):
        return Decision.STOP, "身份字段缺失"

    # authenticated=True 仅表示受控回环服务器来源受信，绝非密码学签名。
    proof = Evidence(Kind.COMMITTED, reply["key"], reply["scope"],
                     reply["fingerprint"], context.authority, context.generation,
                     tick + 2, authenticated=True, final=True)
    now = tick
    label = Label.COMMIT
    if variant == "expired_receipt":
        proof = replace(proof, valid_until=tick - 1)
    elif variant == "expired_source":
        source = replace(source, proof_valid_through=tick - 1)
    elif variant == "wrong_scope":
        proof = replace(proof, scope="injected-other-scope")
    elif variant == "wrong_fingerprint":
        proof = replace(proof, fingerprint="injected-other-fingerprint")
    elif variant == "wrong_identity":
        proof = replace(proof, identity="injected-other-identity")
    elif variant == "untrusted":
        proof = replace(proof, authenticated=False)
    elif variant == "weak_source":
        source = replace(source, can_confirm=False)
    elif variant == "clock_rollback":
        now = tick - 1
    elif variant == "malformed":
        return Decision.STOP, "响应传输被对抗性破坏"
    claims = (proof,)
    if variant == "conflicting":
        label = Label.HARD_CONFLICT
        claims = (proof, replace(proof, kind=Kind.FINAL_NON_EXECUTION))
    result = inspect(context, source, Reading(label, claims), now)
    return result.action, result.reason


def run_episode(binary: Path, engine: str, fault: str, variant: str) -> Episode:
    from bench.runner import raw_call
    from bench.window_live import ORIGIN_SCOPE, WindowClient, engine_call, window_server
    from bench.window_uncertainty import control
    from raguard.model import Call

    if engine not in ENGINES or fault not in FAULTS or variant not in VARIANTS:
        raise ValueError("非法实验条件")
    with window_server(binary, ttl=6, extra_args=("--evidence-mode", "matched")) as origin, \
            tempfile.TemporaryDirectory() as tmp:
        raw = raw_call("orders.create")
        call = Call.parse(raw)
        client = WindowClient(origin, drop_count=2, drop_point=fault, scope=ORIGIN_SCOPE)
        initial = engine_call(engine, Path(tmp), client, raw)
        tick = control(origin, "advance", {"ticks": 2})["server_tick"]
        context = Context(call.stable_key, ORIGIN_SCOPE, call.fingerprint,
                          "trusted-loopback", 1, tick, False, False)
        receipt = read_receipt(origin, ORIGIN_SCOPE, call.stable_key, call.fingerprint)
        action, reason = audit_response(context, receipt, variant, tick)
        snapshot = control(origin, "effects")
        matched = [effect for effect in snapshot["effects"]
                   if (effect["scope"], effect["key"], effect["fingerprint"])
                   == (ORIGIN_SCOPE, call.stable_key, call.fingerprint)]
        if len(matched) != (1 if fault == "after_commit" else 0):
            raise AssertionError("故障注入与独立物理效果不一致")
        if len(snapshot["effects"]) != len(matched):
            raise AssertionError("存在其他作用域的物理效果")
        safe = action == Decision.CONFIRM and len(matched) == 1
        unsafe = action == Decision.CONFIRM and len(matched) != 1
        if (variant == "valid" and fault == "after_commit") != safe:
            raise AssertionError("正向确认的期望被破坏")
        if unsafe or action == Decision.REISSUE:
            raise AssertionError("审计产生未经证明的授权")
        return Episode(engine, fault, variant, initial, action.value,
                       len(matched), int(safe), int(unsafe),
                       max(0, len(matched) - 1), int(action == Decision.STOP),
                       1, reason, int(variant != "valid"))


def validate_pairs(rows: list[Episode]) -> None:
    expected = {(e, f, v) for e in ENGINES for f in FAULTS for v in VARIANTS}
    keys = [(row.engine, row.fault, row.variant) for row in rows]
    if len(keys) != len(set(keys)) or set(keys) != expected:
        raise ValueError("真实 HTTP 对照配对不完整")
    for fault in FAULTS:
        for variant in VARIANTS:
            a, b = (next(r for r in rows if r.engine == e and r.fault == fault
                         and r.variant == variant) for e in ENGINES)
            if (a.physical_effects, a.decision, a.unsafe_confirmation) != (
                    b.physical_effects, b.decision, b.unsafe_confirmation):
                raise ValueError("B5 与 RAGuard 在相同条件下结果不同")


def run(output: Path) -> list[Episode]:
    with tempfile.TemporaryDirectory() as tmp:
        binary = Path(tmp) / ("uncertainty.exe" if os.name == "nt" else "uncertainty")
        subprocess.run(["go", "build", "-o", str(binary), "./windowlab/cmd/uncertainty"],
                       cwd=ROOT / "mock", check=True, timeout=120, capture_output=True)
        rows = [run_episode(binary, e, f, v) for e in ENGINES for f in FAULTS
                for v in VARIANTS]
    validate_pairs(rows)
    output.mkdir(parents=True, exist_ok=True)
    with (output / "episodes.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=Episode.__dataclass_fields__.keys())
        writer.writeheader()
        writer.writerows(asdict(item) for item in rows)
    protocol = {
        "research_mode": "isolated_go_http_post_fault_audit",
        "physical_truth_for_scoring_only": True,
        "authentication_model": "trusted_loopback_assumption_not_cryptographic_signature",
        "threat_injections": "python_adapter_after_actual_http_response",
        "no_recovery_reissue": True,
        "limit": "no production Gateway audit integration or independent vendor",
    }
    (output / "protocol.json").write_text(
        json.dumps(protocol, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = ("# Go HTTP 物理效果与证据审计联测\n\n"
              f"场景：{len(rows)}；安全确认：{sum(x.safe_confirmation for x in rows)}；"
              f"不安全确认：{sum(x.unsafe_confirmation for x in rows)}。\n\n"
              "恶意证据在研究客户端注入，Go 服务端没有签发冲突或未执行证明。\n"
              "本联测为执行故障后的只读审计，不是 Gateway 内部的端到端安全恢复。\n")
    (output / "REPORT.md").write_text(report, encoding="utf-8")
    names = ("episodes.csv", "protocol.json", "REPORT.md")
    (output / "SHA256SUMS.json").write_text(json.dumps({
        name: sha256((output / name).read_bytes()).hexdigest() for name in names
    }, indent=2) + "\n", encoding="utf-8")
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="B5/RAGuard 原始调用后的真实 HTTP 证据审计")
    parser.add_argument("--output", type=Path, default=Path("results/evidence_audit_http"))
    args = parser.parse_args()
    run(args.output)


if __name__ == "__main__":
    main()
