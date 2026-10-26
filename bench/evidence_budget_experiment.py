"""独立校准与分布反转的合成证据预算配对基准。"""

import argparse
import csv
from dataclasses import asdict, replace
from hashlib import sha256
import json
from pathlib import Path
from random import Random

from bench.evidence_budget import Budget, Source, calibration, query_budgeted
from bench.evidence_frontier import Context, Decision, Evidence, Kind

SOURCES = (
    Source("audit", 3, 3, frozenset({Kind.COMMITTED, Kind.ABSENT_READ})),
    Source("cheap", 1, 1, frozenset({Kind.COMMITTED, Kind.ABSENT_READ})),
    Source("quick", 2, 1, frozenset({Kind.COMMITTED, Kind.ABSENT_READ})),
)
PROFILES = {
    "matched": {"audit": .96, "cheap": .20, "quick": .82},
    "reversed": {"audit": .96, "cheap": .78, "quick": .10},
}
POLICIES = ("fixed", "cheap", "calibrated", "robust", "random")
CONTEXT = Context("intent", "scope", "fingerprint", "authority", 1, 0, False, False)
BUDGET = Budget(2, 2, 3)


def draw(seed, domain, count):
    """评估真值只在统计阶段可见，不传入查询策略。"""
    if domain not in PROFILES or count < 1:
        raise ValueError("环境或案例数非法")
    rng = Random(seed)
    rows = []
    for index in range(count):
        committed = rng.random() < .65
        proofs = {}
        for source in SOURCES:
            found = committed and rng.random() < PROFILES[domain][source.name]
            proofs[source.name] = Evidence(
                Kind.COMMITTED if found else Kind.ABSENT_READ,
                CONTEXT.identity, CONTEXT.scope, CONTEXT.fingerprint,
                CONTEXT.authority, CONTEXT.generation, 100,
                authenticated=True, final=found,
            )
        rows.append((f"{domain}-{index:05d}", committed, proofs))
    return rows


def check_pairs(rows, count):
    index = {}
    for item in rows:
        key = (item["case_id"], item["policy"])
        if key in index:
            raise ValueError("同一案例策略重复")
        index[key] = item
    cases = set(item["case_id"] for item in rows)
    if len(cases) != 2 * count or index.keys() != {
        (cid, strategy) for cid in cases for strategy in POLICIES
    }:
        raise ValueError("严格配对缺失")
    return index


def write_csv(path, rows):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0])
        writer.writeheader()
        writer.writerows(rows)


def run(train, test, seed, output):
    if not 1 <= train <= 10000 or not 1 <= test <= 10000:
        raise ValueError("训练与测试样本范围为 1-10000")
    calibrated = {}
    for offset, domain in enumerate(PROFILES):
        training = draw(seed + offset * 100001, domain, train)
        calibrated[domain] = calibration(
            [record[2] for record in training], SOURCES, CONTEXT
        )
    pessimistic = {
        source.name: min(calibrated[d][source.name] for d in PROFILES)
        for source in SOURCES
    }
    rows = []
    for offset, domain in enumerate(PROFILES):
        for number, (case_id, committed, proofs) in enumerate(
            draw(seed + 1000000 + offset * 100001, domain, test)
        ):
            for pnum, policy in enumerate(POLICIES):
                if policy == "robust":
                    planning, rates = "calibrated", pessimistic
                elif policy == "calibrated":
                    planning, rates = "calibrated", calibrated["matched"]
                else:
                    planning, rates = policy, None
                result = query_budgeted(
                    CONTEXT, SOURCES, BUDGET, proofs.get, policy=planning,
                    estimates=rates, rng=Random(seed + number * 10 + pnum),
                )
                rows.append({
                    "case_id": case_id, "domain": domain, "policy": policy,
                    "physical_committed": int(committed),
                    "action": result.verdict.action.value,
                    "safe_confirm": int(result.verdict.action == Decision.CONFIRM and committed),
                    "unsafe_authorization": int(result.verdict.action == Decision.CONFIRM and not committed
                                                or result.verdict.action == Decision.REISSUE),
                    "unresolved": int(result.verdict.action == Decision.STOP),
                    "cost": result.cost, "queries": result.queries,
                    "latency": result.latency, "queried": ";".join(result.sources),
                })
    pairs = check_pairs(rows, test)
    summary, differences = [], []
    for domain in PROFILES:
        for policy in POLICIES:
            subset = [r for r in rows if r["domain"] == domain and r["policy"] == policy]
            item = {"domain": domain, "policy": policy, "cases": len(subset)}
            for field in ("safe_confirm", "unsafe_authorization", "unresolved", "cost",
                          "queries", "latency"):
                item[field] = sum(row[field] for row in subset)
            summary.append(item)
        for number in range(test):
            cid = f"{domain}-{number:05d}"
            left = pairs[(cid, "calibrated")]
            for right_name in ("fixed", "cheap", "robust", "random"):
                right = pairs[(cid, right_name)]
                differences.append({
                    "case_id": cid, "comparison": right_name,
                    "safe_delta": left["safe_confirm"] - right["safe_confirm"],
                    "cost_delta": left["cost"] - right["cost"],
                })
    output.mkdir(parents=True, exist_ok=True)
    write_csv(output / "episodes.csv", rows)
    write_csv(output / "summary.csv", summary)
    write_csv(output / "paired.csv", differences)
    protocol = {
        "kind": "synthetic_offline_not_go_http", "seed": seed,
        "train_per_domain": train, "test_per_domain": test,
        "profiles": PROFILES, "budget": asdict(BUDGET),
        "observed_calibration": calibrated,
        "oracle_used_for_evaluation_only": True,
        "calibration_cost_excluded": True,
        "no_real_physical_execution": True,
    }
    (output / "protocol.json").write_text(
        json.dumps(protocol, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    report = [
        "# 证据预算合成实验", "",
        "校准集与评估集独立，数据为合成抽样，非真实 Go HTTP 测试。",
        "不计训练查询成本；不得据此宣称 RAGuard 优于 B5。",
        "",
        "| 环境 | 策略 | 案例 | 安全确认 | 不安全授权 | 未完成 | 总成本 |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for item in summary:
        report.append(
            f"| {item['domain']} | {item['policy']} | {item['cases']} | "
            f"{item['safe_confirm']} | {item['unsafe_authorization']} | "
            f"{item['unresolved']} | {item['cost']} |"
        )
    report += ["", "分布反转后的结果必须同时保留；本研究不声称最优算法。"]
    (output / "REPORT.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    names = ("episodes.csv", "summary.csv", "paired.csv", "protocol.json", "REPORT.md")
    digests = {p: sha256((output / p).read_bytes()).hexdigest() for p in names}
    (output / "SHA256SUMS.json").write_text(json.dumps(digests, indent=2) + "\n",
                                           encoding="utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser(description="证据预算离线配对基准")
    parser.add_argument("--train", type=int, default=400)
    parser.add_argument("--test", type=int, default=300)
    parser.add_argument("--seed", type=int, default=20261026)
    parser.add_argument("--output", type=Path, default=Path("results/evidence_budget"))
    args = parser.parse_args()
    run(args.train, args.test, args.seed, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
