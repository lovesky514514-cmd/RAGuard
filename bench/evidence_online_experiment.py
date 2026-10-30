"""同序列、同预算的在线证据来源选择对照实验。"""

from __future__ import annotations

import argparse
import csv
from hashlib import sha256
import json
from pathlib import Path
from random import Random

from bench.evidence_online_policy import Choice, SlidingWindowUCB

CHOICES = (Choice("cheap", 1), Choice("quick", 2))
POLICIES = ("fixed_cheap", "fixed_quick", "alternate", "sliding_ucb")
PROFILES = {"matched": (.20, .82), "shifted": (.78, .10)}


def stream(seed: int, length: int) -> list[dict]:
    """同一随机场景可供各策略观察；隐藏未选择来源反馈。"""
    rng = Random(seed)
    result = []
    for index in range(length):
        phase = "matched" if index < length // 2 else "shifted"
        committed = rng.random() < .65
        outcome = {name: bool(committed and rng.random() < rate)
                   for name, rate in zip(("cheap", "quick"), PROFILES[phase])}
        result.append({"case": index, "phase": phase,
                       "committed": committed, "outcomes": outcome})
    return result


def run(seed: int, length: int, output: Path) -> dict:
    if not 40 <= length <= 10000 or length % 2:
        raise ValueError("长度必须是 40 至 10000 之间的偶数")
    episodes = stream(seed, length)
    records = []
    for policy in POLICIES:
        learner = SlidingWindowUCB(CHOICES, window=12, exploration=.35)
        for case in episodes:
            if policy == "fixed_cheap":
                chosen = "cheap"
            elif policy == "fixed_quick":
                chosen = "quick"
            elif policy == "alternate":
                chosen = ("cheap", "quick")[case["case"] % 2]
            else:
                chosen = learner.select()
            observed = case["outcomes"][chosen]
            if policy == "sliding_ucb":
                learner.observe(chosen, observed)
            cost = next(c.cost for c in CHOICES if c.source == chosen)
            records.append({
                "policy": policy, "case": case["case"], "phase": case["phase"],
                "chosen": chosen, "positive": int(observed),
                "safe_confirmation": int(observed and case["committed"]),
                "unsafe_confirmation": int(observed and not case["committed"]),
                "cost": cost,
            })
    for policy in POLICIES:
        cases = [r["case"] for r in records if r["policy"] == policy]
        if cases != list(range(length)):
            raise ValueError("配对样本缺失或顺序异常")
    summary = []
    for phase in PROFILES:
        for policy in POLICIES:
            subset = [r for r in records if r["phase"] == phase and r["policy"] == policy]
            summary.append({
                "phase": phase, "policy": policy, "cases": len(subset),
                "safe": sum(r["safe_confirmation"] for r in subset),
                "unsafe": sum(r["unsafe_confirmation"] for r in subset),
                "cost": sum(r["cost"] for r in subset),
            })
    output.mkdir(parents=True, exist_ok=True)
    for filename, rows in (("episodes.csv", records), ("summary.csv", summary)):
        with (output / filename).open("w", encoding="utf-8", newline="") as out:
            writer = csv.DictWriter(out, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
    (output / "protocol.json").write_text(json.dumps({
        "kind": "synthetic_online_policy_not_HTTP",
        "seed": seed, "length": length, "profiles": PROFILES,
        "feedback": "only_selected_source", "evaluation_truth_hidden": True,
        "costs": {c.source: c.cost for c in CHOICES},
        "nonstationary_bandit_is_prior_art": True,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = ["# 在线证据来源选择对照（合成）", "",
              "这是已有滑动窗口 UCB 的基线，不是 ECF 新算法；真实 HTTP 尚未接入。",
              "各策略每例只允许一次查询，成本记录为实际消耗。固定 quick 成本更高，不能直接宣称等成本优势。",
              "环境在中点突变，策略不知道切换时刻，训练没有使用 shifted 标签。", "",
              "| 阶段 | 策略 | 案例 | 安全确认 | 不安全确认 | 总成本 |",
              "| --- | --- | ---: | ---: | ---: | ---: |"]
    report += [f"| {r['phase']} | {r['policy']} | {r['cases']} | {r['safe']} | {r['unsafe']} | {r['cost']} |" for r in summary]
    (output / "REPORT.md").write_text("\n".join(report)+"\n",encoding="utf-8")
    names=("episodes.csv","summary.csv","protocol.json","REPORT.md")
    (output/"SHA256SUMS.json").write_text(json.dumps({
        name:sha256((output/name).read_bytes()).hexdigest() for name in names
    },indent=2)+"\n",encoding="utf-8")
    return {"rows":len(records),"summary":summary}


def main():
    parser=argparse.ArgumentParser(description="证据调度变化环境的在线策略对照")
    parser.add_argument("--seed",type=int,default=20261028)
    parser.add_argument("--length",type=int,default=400)
    parser.add_argument("--output",type=Path,default=Path("results/evidence_online"))
    args=parser.parse_args()
    run(args.seed,args.length,args.output)


if __name__=="__main__":
    main()
