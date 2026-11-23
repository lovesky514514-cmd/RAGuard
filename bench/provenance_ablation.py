"""真实 Go 效果日志上的证据来源反例与研究级单规则消融。"""

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
import urllib.request

from bench.audited_recovery_http import ROOT
from bench.evidence_audit import Label, Reading, Source, inspect
from bench.evidence_audit_http import audit_response, read_receipt
from bench.evidence_frontier import Context, Decision, Evidence, Kind
from bench.runner import raw_call
from bench.window_live import ORIGIN_SCOPE, window_server
from bench.window_uncertainty import control
from raguard.model import Call


CASES = ("valid", "absent", "wrong_identity", "wrong_scope", "wrong_fingerprint")
FIELDS = {"wrong_identity": "identity", "wrong_scope": "scope",
          "wrong_fingerprint": "fingerprint"}


@dataclass(frozen=True)
class Episode:
    repeat: int
    case: str
    baseline: str
    single_check_removed: str
    counterfactual: str
    target_effects: int
    decoy_effects: int
    false_confirmation_baseline: int
    false_confirmation_counterfactual: int
    actual_http_writes: int


def claim_for_case(target: Call, case: str) -> dict | None:
    """每次只变动一个证据绑定字段；假业务只写入隔离 Go 沙箱。"""
    if case not in CASES:
        raise ValueError("未知证据反例")
    if case == "absent":
        return None
    payload = {"scope": ORIGIN_SCOPE, "key": target.stable_key,
               "fingerprint": target.fingerprint}
    if case == "wrong_identity":
        payload["key"] += "-decoy"
    elif case == "wrong_scope":
        payload["scope"] = "region-b"
    elif case == "wrong_fingerprint":
        payload["fingerprint"] = "decoy-" + target.fingerprint
    return payload


def counterfactual(context: Context, receipt: dict | None,
                   case: str, tick: int) -> Decision:
    """只在研究层模拟忽略单个字段；绝不关闭生产 Gateway 的检查。"""
    if case not in CASES:
        raise ValueError("未知消融条件")
    if case not in FIELDS or receipt is None:
        return audit_response(context, receipt, "valid", tick)[0]
    proof = Evidence(Kind.COMMITTED, receipt["key"], receipt["scope"],
                     receipt["fingerprint"], context.authority, context.generation,
                     tick + 2, authenticated=True, final=True)
    field = FIELDS[case]
    # 将目标字段置为期待值，等价于反事实地绕过该字段比较。
    proof = replace(proof, **{field: getattr(context, field)})
    source = Source("trusted-loopback", 1, 1, can_confirm=True,
                    can_prove_nonexecution=True, proof_valid_through=tick + 2)
    return inspect(context, source, Reading(Label.COMMIT, (proof,)), tick).action


def send_decoy(origin: str, payload: dict) -> None:
    """写入原始 Go 服务端，不能把研究代理生成的回执冒充物理效果。"""
    request = urllib.request.Request(
        origin + "/v1/execute", data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=3) as response:
        reply = json.load(response)
    if not isinstance(reply.get("effect_id"), str) or reply.get("replayed"):
        raise AssertionError("隔离服务未创建独立效果")


def one_case(binary: Path, repeat: int, case: str) -> Episode:
    if type(repeat) is not int or repeat < 1:
        raise ValueError("重复编号非法")
    target = Call.parse(raw_call("orders.create"))
    claim = claim_for_case(target, case)
    with window_server(binary, ttl=6, extra_args=("--evidence-mode", "matched")) as origin:
        if claim:
            send_decoy(origin, claim)
        tick = 2
        context = Context(target.stable_key, ORIGIN_SCOPE, target.fingerprint,
                          "trusted-loopback", 1, tick, False, False)
        receipt = (read_receipt(origin, claim["scope"], claim["key"],
                                claim["fingerprint"]) if claim else None)
        baseline = audit_response(context, receipt, "valid", tick)[0]
        mutant = counterfactual(context, receipt, case, tick)
        # 真值只在两次判定结束后由评价器读取，绝不传给审计函数。
        truth = control(origin, "effects")
    effects = truth["effects"]
    target_effects = sum((effect["scope"], effect["key"], effect["fingerprint"])
                         == (ORIGIN_SCOPE, target.stable_key, target.fingerprint)
                         for effect in effects)
    decoy_effects = len(effects) - target_effects
    return Episode(repeat, case, baseline.value, FIELDS.get(case, "none"),
                   mutant.value, target_effects, decoy_effects,
                   int(baseline == Decision.CONFIRM and target_effects == 0),
                   int(mutant == Decision.CONFIRM and target_effects == 0),
                   int(truth["attempts"]))


def validate(rows: list[Episode], repeats: int) -> None:
    """每轮必须含全部反例，真实效果与对照结论分别检查。"""
    expected = {(repeat, case) for repeat in range(1, repeats + 1) for case in CASES}
    keys = [(row.repeat, row.case) for row in rows]
    if len(keys) != len(set(keys)) or set(keys) != expected:
        raise ValueError("消融数据缺行、重复或包含未知条件")
    for row in rows:
        valid = row.case == "valid"
        absent = row.case == "absent"
        checks = (
            (row.target_effects, int(valid)),
            (row.decoy_effects, int(not valid and not absent)),
            (row.actual_http_writes, int(not absent)),
            (row.baseline, "confirm" if valid else "stop"),
            (row.counterfactual, "stop" if absent else "confirm"),
            (row.false_confirmation_baseline, 0),
            (row.false_confirmation_counterfactual, int(not valid and not absent)),
        )
        if any(actual != expected for actual, expected in checks):
            raise ValueError(f"真值或独立规则对照不一致：{row.repeat}/{row.case}")


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def run(output: Path, *, repeats: int = 3) -> list[Episode]:
    if type(repeats) is not int or not 1 <= repeats <= 50:
        raise ValueError("重复次数须为 1–50")
    with tempfile.TemporaryDirectory() as tmp:
        binary = Path(tmp) / ("uncertainty.exe" if os.name == "nt" else "uncertainty")
        subprocess.run(["go", "build", "-o", str(binary), "./windowlab/cmd/uncertainty"],
                       cwd=ROOT / "mock", timeout=120, check=True, capture_output=True)
        rows = [one_case(binary, repeat, case)
                for repeat in range(1, repeats + 1) for case in CASES]
    validate(rows, repeats)
    output.mkdir(parents=True, exist_ok=True)
    write_csv(output / "episodes.csv", [asdict(row) for row in rows])
    protocol = {
        "kind": "live_go_provenance_ablation",
        "cases": CASES, "repeats": repeats,
        "real_http_effects_from_go": True,
        "oracle_visible_to_evaluator_only": True,
        "counterfactual_is_research_only": True,
        "no_production_policy_mutation": True,
        "no_real_llm_or_external_provider": True,
        "limitation": "decoy receipts are local loopback evidence; field bypass is simulated",
    }
    (output / "protocol.json").write_text(
        json.dumps(protocol, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = ["# 真实 Go 证据来源消融", "",
              f"完成 {len(rows)} 个隔离方法级场景；其中 "
              f"{sum(r.false_confirmation_baseline for r in rows)} 个默认审计错误确认、"
              f"{sum(r.false_confirmation_counterfactual for r in rows)} 个反事实错误确认。", "",
              "反事实错误确认仅来自研究层绕过单个字段的模拟，不是生产 RAGuard 漏洞。",
              "全部证据来自真实本地 Go HTTP 物理效果；无真实模型和外部 Provider。",
              "该试验证明来源绑定检查对特定反例的必要性，不证明优于 B5。"]
    (output / "REPORT.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    names = ("episodes.csv", "protocol.json", "REPORT.md")
    (output / "SHA256SUMS.json").write_text(json.dumps(
        {name: sha256((output / name).read_bytes()).hexdigest() for name in names
    }, indent=2) + "\n", encoding="utf-8")
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="真实 Go 证据来源单规则消融")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--output", type=Path, default=Path("results/provenance_ablation"))
    args = parser.parse_args()
    run(args.output, repeats=args.repeats)


if __name__ == "__main__":
    main()
