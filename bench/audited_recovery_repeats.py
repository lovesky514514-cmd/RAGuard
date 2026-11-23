"""重复运行真实 HTTP 审计恢复实验，核对完整配对与归档证据。"""

from __future__ import annotations

import argparse
import csv
from dataclasses import asdict
from hashlib import sha256
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import tempfile

from bench.audited_recovery_http import FAULTS, ROOT, one_case
from bench.evidence_audit_http import VARIANTS
from bench.window_live import METHODS


FIELDS = ("repeat", "order", "engine", "fault", "variant")


def schedule(repeats: int, seed: int) -> list[tuple[int, int, str, str, str]]:
    """只随机化实验顺序；每个独立沙箱的注入条件仍严格配对。"""
    if type(repeats) is not int or not 1 <= repeats <= 100 or type(seed) is not int:
        raise ValueError("重复次数须为 1–100，种子须为整数")
    rng = random.Random(seed)
    plan = []
    for repeat in range(1, repeats + 1):
        conditions = [(fault, variant) for fault in FAULTS for variant in VARIANTS]
        rng.shuffle(conditions)
        for fault, variant in conditions:
            engines = list(METHODS)
            rng.shuffle(engines)
            for engine in engines:
                plan.append((repeat, len(plan) + 1, engine, fault, variant))
    return plan


def audit(rows: list[dict], repeats: int) -> None:
    """独立于原始引擎判分，拒绝缺行、重复、越权确认与效果账不一致。"""
    expected = {(n, engine, fault, variant)
                for n in range(1, repeats + 1)
                for engine in METHODS for fault in FAULTS for variant in VARIANTS}
    keys = [(r["repeat"], r["engine"], r["fault"], r["variant"]) for r in rows]
    if len(keys) != len(set(keys)) or set(keys) != expected:
        raise ValueError("重复实验的完整配对缺失、重复或含非法条件")
    orders = [r["order"] for r in rows]
    if set(orders) != set(range(1, len(rows) + 1)) or len(orders) != len(set(orders)):
        raise ValueError("运行序号不完整或重复")
    index = dict(zip(keys, rows))
    for row in rows:
        effects = int(row["fault"] == "after_commit")
        confirmed = int(row["variant"] == "valid" and effects == 1)
        checks = {
            "initial_status": 409,
            "final_status": 200 if confirmed else 409,
            "audit_decision": "confirm" if confirmed else "stop",
            "physical_effects": effects,
            "safe_confirmation": confirmed,
            "unsafe_confirmation": 0,
            "duplicate_effects": 0,
            "added_execute_attempts": 0,
            "unresolved": 1 - confirmed,
            "queries": 1,
        }
        for name, expected_value in checks.items():
            if row[name] != expected_value:
                raise ValueError(f"独立判分不一致：{name}，条件={row['fault']}/{row['variant']}")
        if row["blocked_recovery_writes"] < 0 or row["real_execute_attempts"] < 0:
            raise ValueError("网络尝试与本地阻断计数不能为负")
        if not row["audit_reason"]:
            raise ValueError("缺少审计原因")
    for n in range(1, repeats + 1):
        for fault in FAULTS:
            for variant in VARIANTS:
                a, b = (index[(n, engine, fault, variant)] for engine in METHODS)
                if (a["final_status"], a["audit_decision"], a["physical_effects"]) != (
                        b["final_status"], b["audit_decision"], b["physical_effects"]):
                    raise ValueError("B5 与 RAGuard 同条件结果不一致")


def falsification_checks(rows: list[dict], repeats: int) -> list[dict]:
    """只篡改输出副本以检验判分器，不将这些反例记作新实验。"""
    def modify(field, value):
        sample = [dict(r) for r in rows]
        sample[0][field] = value
        return sample

    variants = (
        ("missing_pair", [dict(r) for r in rows[:-1]]),
        ("duplicate_pair", [dict(r) for r in rows] + [dict(rows[0])]),
        ("false_confirmation", modify("final_status", 200 if rows[0]["final_status"] == 409 else 409)),
        ("hidden_extra_write", modify("added_execute_attempts", 1)),
        ("oracle_effect_tamper", modify("physical_effects", 1 - rows[0]["physical_effects"])),
        ("unsafe_label_tamper", modify("unsafe_confirmation", 1)),
        ("duplicate_order", modify("order", rows[1]["order"])),
    )
    output = []
    for name, changed in variants:
        try:
            audit(changed, repeats)
        except ValueError as exc:
            output.append({"mutation": name, "detected": 1, "reason": str(exc)})
        else:
            output.append({"mutation": name, "detected": 0, "reason": "未被识别"})
    if not all(item["detected"] for item in output):
        raise AssertionError("离线篡改检测未全部通过")
    return output


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def run(output: Path, *, repeats: int, seed: int) -> list[dict]:
    plan = schedule(repeats, seed)
    if not shutil.which("go"):
        raise RuntimeError("真实 HTTP 实验需要 Go 1.21+ 工具链")
    with tempfile.TemporaryDirectory() as tmp:
        binary = Path(tmp) / ("uncertainty.exe" if os.name == "nt" else "uncertainty")
        subprocess.run(["go", "build", "-o", str(binary), "./windowlab/cmd/uncertainty"],
                       cwd=ROOT / "mock", check=True, timeout=120, capture_output=True)
        rows = []
        for repeat, order, engine, fault, variant in plan:
            # 每次 one_case 都重启 Go 服务并创建独立的 SQLite 状态。
            episode = one_case(binary, engine, fault, variant)
            rows.append({"repeat": repeat, "order": order, **asdict(episode)})
    audit(rows, repeats)
    mutations = falsification_checks(rows, repeats)
    output.mkdir(parents=True, exist_ok=True)
    write_csv(output / "episodes.csv", rows)
    write_csv(output / "falsification.csv", mutations)
    protocol = {
        "kind": "repeated_persistent_recovery_http_audit",
        "repeats": repeats, "order_seed": seed,
        "per_repeat_conditions": len(METHODS) * len(FAULTS) * len(VARIANTS),
        "randomization": "execution_order_only",
        "fresh_go_process_and_sqlite_per_episode": True,
        "both_engines_share_no_reissue_adapter": True,
        "positive_receipts_from_real_go_http": True,
        "physical_truth_evaluator_only": True,
        "proofs_are_not_cryptographically_signed": True,
        "falsification_is_offline_not_new_http_episodes": True,
        "warning": "fixed conditions repeated; not random fault diversity, LLM or external provider validation",
    }
    (output / "protocol.json").write_text(json.dumps(
        protocol, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    per_method = {name: [r for r in rows if r["engine"] == name] for name in METHODS}
    report = ["# 重复 HTTP 审计恢复实验", "",
              f"原协议 {len(METHODS) * len(FAULTS) * len(VARIANTS)} 个固定条件，"
              f"每条件独立重复 {repeats} 次，共 {len(rows)} 次方法级执行。", "",
              "| 方法 | 执行 | 安全确认 | 未解决 | 新增真实 HTTP 写入 |",
              "| --- | ---: | ---: | ---: | ---: |"]
    for method, group in per_method.items():
        report.append(f"| {method} | {len(group)} | "
                      f"{sum(r['safe_confirmation'] for r in group)} | "
                      f"{sum(r['unresolved'] for r in group)} | "
                      f"{sum(r['added_execute_attempts'] for r in group)} |")
    report += ["", "运行顺序由给定种子随机化；故障类型和证据变体不随种子变化。",
               "B5 与 Gateway 共享禁止恢复阶段二次写入的研究适配器。",
               "离线篡改仅检验报表校验器，不构成真实攻击、真实 LLM 或独立 Provider 实验。",
               "本报告不是统计显著性或算法优越性的证明。"]
    (output / "REPORT.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    names = ("episodes.csv", "falsification.csv", "protocol.json", "REPORT.md")
    (output / "SHA256SUMS.json").write_text(json.dumps({
        name: sha256((output / name).read_bytes()).hexdigest() for name in names
    }, indent=2) + "\n", encoding="utf-8")
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="重复 44 条真实 HTTP 审计恢复配对协议")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20261123)
    parser.add_argument("--output", type=Path, default=Path("results/audited_recovery_repeats"))
    args = parser.parse_args()
    run(args.output, repeats=args.repeats, seed=args.seed)


if __name__ == "__main__":
    main()
