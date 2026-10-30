"""跨随机种子及预算档位的真实 HTTP 配对统计。"""

from __future__ import annotations

import argparse
import csv
from hashlib import sha256
import json
from pathlib import Path
from statistics import mean, stdev

from bench.evidence_online_http import METHODS, POLICIES, PROFILES, run


def aggregate(records: list[dict], seeds: tuple[int, ...], caps: tuple[int, ...]) -> list[dict]:
    """种子才是统计单元；单个 HTTP 回合不能冒充独立重复。"""
    index = {}
    for row in records:
        key = (row["seed"], row["cap"], row["phase"], row["engine"], row["policy"])
        if key in index:
            raise ValueError("重复的统计单元")
        index[key] = row
    expected = {(seed, cap, phase, engine, policy)
                for seed in seeds for cap in caps for phase in PROFILES
                for engine in METHODS for policy in POLICIES}
    if set(index) != expected:
        raise ValueError("跨种子配对缺失")
    summary = []
    for cap in caps:
        for phase in PROFILES:
            for engine in METHODS:
                for policy in POLICIES:
                    group = [index[(seed, cap, phase, engine, policy)] for seed in seeds]
                    values = [item["safe"] for item in group]
                    costs = [item["cost"] for item in group]
                    summary.append({
                        "cap": cap, "phase": phase, "engine": engine, "policy": policy,
                        "seeds": len(seeds), "mean_safe": round(mean(values), 4),
                        "sd_safe": round(stdev(values), 4) if len(values) > 1 else 0,
                        "min_safe": min(values), "max_safe": max(values),
                        "mean_cost": round(mean(costs), 4),
                        "mean_skipped": round(mean(item["skipped_queries"] for item in group), 4),
                        "unsafe_total": sum(item["unsafe"] for item in group),
                    })
    return summary



def paired_differences(records: list[dict], seeds: tuple[int, ...],
                       caps: tuple[int, ...]) -> list[dict]:
    """同一个种子、预算、故障阶段和引擎内计算配对差值。"""
    index = {}
    for item in records:
        key = (item["seed"], item["cap"], item["phase"], item["engine"], item["policy"])
        if key in index:
            raise ValueError("重复策略记录")
        index[key] = item
    expected = {(seed, cap, phase, engine, policy)
                for seed in seeds for cap in caps for phase in PROFILES
                for engine in METHODS for policy in POLICIES}
    if set(index) != expected:
        raise ValueError("跨种子配对不完整")
    result = []
    for seed in seeds:
        for cap in caps:
            for phase in PROFILES:
                for engine in METHODS:
                    reference = index[(seed, cap, phase, engine, "fixed_cheap")]
                    for policy in POLICIES:
                        if policy == "fixed_cheap":
                            continue
                        observed = index[(seed, cap, phase, engine, policy)]
                        result.append({
                            "seed": seed, "cap": cap, "phase": phase, "engine": engine,
                            "policy": policy, "reference": "fixed_cheap",
                            "delta_safe": observed["safe"] - reference["safe"],
                            "delta_cost": observed["cost"] - reference["cost"],
                            "delta_unresolved": observed["unresolved"] - reference["unresolved"],
                            "delta_skipped": observed["skipped_queries"] - reference["skipped_queries"],
                        })
    return result


def write(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0])
        writer.writeheader()
        writer.writerows(rows)


def study(seeds: tuple[int, ...], caps: tuple[int, ...], length: int, output: Path) -> list[dict]:
    if not seeds or not caps or len(set(seeds)) != len(seeds) or len(set(caps)) != len(caps):
        raise ValueError("随机种子和预算不可为空或重复")
    if any(type(x) is not int or x < 0 for x in caps) or any(x > 2 * length for x in caps):
        raise ValueError("非法的预算档位")
    if not 8 <= length <= 1000 or length % 2:
        raise ValueError("案例数必须为合法偶数")
    output.mkdir(parents=True, exist_ok=True)
    records = []
    for seed in seeds:
        for cap in caps:
            path = output / f"seed_{seed}_cap_{cap}"
            for item in run(seed, length, path, budget_cap=True, cap_units=cap):
                records.append({"seed": seed, "cap": cap, **item})
    summary = aggregate(records, seeds, caps)
    differences = paired_differences(records, seeds, caps)
    write(output / "per_seed.csv", records)
    write(output / "summary.csv", summary)
    write(output / "paired_differences.csv", differences)
    (output / "protocol.json").write_text(json.dumps({
        "kind": "independent_seed_real_go_http_budget_sensitivity",
        "seeds": seeds, "caps": caps, "cases_per_seed": length,
        "correlated_episodes_within_seed": True,
        "no_claim_of_statistical_significance": True,
        "all_policies_share_same_seed_and_budget_cap": True,
        "forced_source_substitution": False,
        "sandbox_truth_used_only_for_scoring": True,
    }, indent=2) + "\n", encoding="utf-8")
    lines = ["# 多种子真实 HTTP 预算敏感性", "",
             "均值、标准差和范围以独立随机种子为单位；不把回合数作为独立样本。",
             "仅受控 Go 沙箱，方法增益和可推广性仍未证明。",
             "paired_differences.csv 以固定 cheap 为共同参照，每组按相同种子配对。", "",
             "| 上限 | 阶段 | 引擎 | 策略 | 种子 | 平均确认 | 标准差 | 平均成本 |",
             "|---:|---|---|---|---:|---:|---:|---:|"]
    for x in summary:
        lines.append(f"| {x['cap']} | {x['phase']} | {x['engine']} | {x['policy']} | "
                     f"{x['seeds']} | {x['mean_safe']} | {x['sd_safe']} | {x['mean_cost']} |")
    (output / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    names = ("per_seed.csv", "summary.csv", "paired_differences.csv",
             "protocol.json", "REPORT.md")
    (output / "SHA256SUMS.json").write_text(json.dumps({
        name: sha256((output / name).read_bytes()).hexdigest() for name in names
    }, indent=2) + "\n", encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="配对 HTTP 多种子预算敏感性")
    parser.add_argument("--seeds", default="41,42,43")
    parser.add_argument("--caps", default="12,18,24")
    parser.add_argument("--length", type=int, default=12)
    parser.add_argument("--output", type=Path, default=Path("results/evidence_sensitivity"))
    args = parser.parse_args()
    study(tuple(int(x) for x in args.seeds.split(",")),
          tuple(int(x) for x in args.caps.split(",")),
          args.length, args.output)


if __name__ == "__main__":
    main()
