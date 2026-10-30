"""四维耦合校准与独立测试的配对基准，保留失败结果。"""

from __future__ import annotations

import argparse
import csv
from functools import lru_cache
from hashlib import sha256
import json
from pathlib import Path
from random import Random

from bench.evidence_audit import Label, Reading, Source, inspect
from bench.evidence_frontier import Context, Decision, Evidence, Kind
from bench.evidence_meta_planner import CapabilityPlanner, World

SOURCES = (
    Source("route", 1, 1),
    Source("weak", 1, 1),
    Source("fast", 2, 1, can_confirm=True, proof_valid_through=3),
    Source("regional", 2, 1, can_confirm=True, proof_valid_through=8),
    Source("arbiter", 2, 1, can_confirm=True, can_prove_nonexecution=True,
           proof_valid_through=9),
)
MODEL = (
    World(10, (("route", Label.ROUTE_A), ("fast", Label.COMMIT),
               ("weak", Label.COMMIT))),
    World(8, (("route", Label.ROUTE_A), ("fast", Label.ABSENT),
              ("arbiter", Label.COMMIT))),
    World(10, (("route", Label.ROUTE_B), ("regional", Label.COMMIT),
                ("weak", Label.COMMIT))),
    World(8, (("route", Label.ROUTE_B), ("regional", Label.ABSENT),
              ("arbiter", Label.COMMIT), ("weak", Label.COMMIT))),
    World(8, (("route", Label.SOFT_CONFLICT), ("arbiter", Label.COMMIT),
              ("weak", Label.COMMIT))),
    World(7, (("route", Label.SOFT_CONFLICT), ("arbiter", Label.HARD_CONFLICT))),
    World(6, (("route", Label.ROUTE_A), ("fast", Label.COMMIT),
              ("weak", Label.PENDING))),
    World(7, (("route", Label.ROUTE_B), ("regional", Label.COMMIT),
              ("weak", Label.PENDING))),
    World(6, (("route", Label.SOFT_CONFLICT), ("arbiter", Label.ABSENT))),
    World(6, (("route", Label.ROUTE_A), ("fast", Label.STALE),
              ("arbiter", Label.COMMIT))),
)
POLICIES = (
    "capability_plan", "fixed_fast", "fixed_regional", "authority_first",
    "route_rule", "rule_meta", "cheap_first", "myopic", "no_freshness",
    "no_authority", "no_conflict", "no_lookahead",
)
CONTEXT = Context("request", "tenant", "input", "provider", 2, 0, False, False)
PROOF = Evidence(Kind.COMMITTED, CONTEXT.identity, CONTEXT.scope, CONTEXT.fingerprint,
                 CONTEXT.authority, CONTEXT.generation, 30, authenticated=True, final=True)
NONEXEC = Evidence(Kind.FINAL_NON_EXECUTION, CONTEXT.identity, CONTEXT.scope,
                   CONTEXT.fingerprint, CONTEXT.authority, CONTEXT.generation,
                   30, authenticated=True, final=True)


def calibrate() -> tuple[World, ...]:
    """权重仅来自预先固定的校准模型，不读取评估标签。"""
    return MODEL


@lru_cache(maxsize=16)
def cached_planner(ablate: frozenset[str]) -> CapabilityPlanner:
    return CapabilityPlanner(SOURCES, calibrate(), ablate=ablate)


def choose_static(name: str, seen: tuple[str, ...], history: tuple[Label, ...],
                  start_tick: int) -> str | None:
    if name in ("fixed_fast", "fixed_regional", "authority_first"):
        source = {"fixed_fast": "fast", "fixed_regional": "regional",
                  "authority_first": "arbiter"}[name]
        return source if source not in seen else None
    if name == "cheap_first":
        return next((item for item in ("weak", "route", "regional")
                     if item not in seen), None)
    if name in ("route_rule", "rule_meta"):
        if not seen:
            return "route"
        if seen == ("route",):
            if (name == "rule_meta" and history[0] == Label.ROUTE_A
                    and start_tick + 2 > 3):
                return "arbiter"
            return {Label.ROUTE_A: "fast", Label.ROUTE_B: "regional",
                    Label.SOFT_CONFLICT: "arbiter"}.get(history[0])
    return None


def observation(world: World, source: Source) -> Reading:
    label = world.reply(source.name)
    if label == Label.COMMIT:
        return Reading(label, (PROOF,))
    if label == Label.HARD_CONFLICT:
        return Reading(label, (PROOF, NONEXEC))
    return Reading(label)


def evaluate(world: World, policy: str, budget: int, start_tick: int,
             *, model: tuple[World, ...] | None = None) -> dict:
    context = Context(CONTEXT.identity, CONTEXT.scope, CONTEXT.fingerprint,
                      CONTEXT.authority, CONTEXT.generation, start_tick, False, False)
    ablations = {
        "myopic": frozenset({"lookahead"}),
        "no_freshness": frozenset({"freshness"}),
        "no_authority": frozenset({"authority"}),
        "no_conflict": frozenset({"conflict"}),
        "no_lookahead": frozenset({"lookahead"}),
    }
    planner = (cached_planner(ablations.get(policy, frozenset()))
               if model is None else CapabilityPlanner(
                   SOURCES, model, ablate=ablations.get(policy, frozenset())))
    by_name = {s.name: s for s in SOURCES}
    used: list[str] = []
    trace: list[Label] = []
    elapsed = spent = 0
    prior: tuple[Evidence, ...] = ()
    action = Decision.STOP
    for _ in range(len(SOURCES)):
        if policy in ("capability_plan", *ablations):
            source_name, _ = planner.choose(tuple(zip(used, trace)), budget - spent,
                                            elapsed=start_tick + elapsed)
        else:
            source_name = choose_static(policy, tuple(used), tuple(trace), start_tick)
        if source_name is None:
            break
        source = by_name[source_name]
        if source_name in used or spent + source.cost > budget:
            break
        elapsed += source.latency
        spent += source.cost
        result = inspect(context, source, observation(world, source),
                         start_tick + elapsed, prior)
        used.append(source_name)
        trace.append(result.label)
        if result.terminal:
            action = result.action
            break
        prior = result.valid_proofs

    truth = any(world.reply(s.name) == Label.COMMIT and s.can_confirm for s in SOURCES)
    unsafe = action == Decision.CONFIRM and not truth
    return {
        "action": action.value,
        "safe_confirm": int(action == Decision.CONFIRM and truth),
        "unsafe": int(unsafe),
        "unresolved": int(action == Decision.STOP),
        "cost": spent, "queries": len(used), "sources": ";".join(used),
        "trace": ";".join(label.value for label in trace),
    }


def paired(rows: list[dict], seeds: tuple[int, ...], budgets: tuple[int, ...],
           cases: int, domains: tuple[str, ...]) -> None:
    expected = {(seed, domain, budget, case, policy)
                for seed in seeds for domain in domains
                for budget in budgets for case in range(cases)
                for policy in POLICIES}
    keys = [(r["seed"], r["domain"], r["budget"], r["case"], r["policy"])
            for r in rows]
    if len(keys) != len(set(keys)) or set(keys) != expected:
        raise ValueError("配对记录缺失、重复或出现未声明策略")


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


def study(seeds: tuple[int, ...], budgets: tuple[int, ...], cases: int,
          output: Path) -> list[dict]:
    if (not seeds or len(set(seeds)) != len(seeds) or not budgets
            or len(set(budgets)) != len(budgets) or min(budgets) < 0
            or max(budgets) > 20 or cases < 1 or cases > 3000):
        raise ValueError("研究样本和预算配置非法")
    rows = []
    domains = ("calibrated", "shifted")
    for seed in seeds:
        rng = Random(seed)
        weights = [w.weight for w in MODEL]
        for domain in domains:
            if domain == "shifted":
                weights = [w.weight for w in reversed(MODEL)]
            sampled = rng.choices(MODEL, weights=weights, k=cases)
            for budget in budgets:
                for index, world in enumerate(sampled):
                    start_tick = 0 if index % 2 == 0 else 2
                    for policy in POLICIES:
                        rows.append({
                            "seed": seed, "domain": domain, "budget": budget,
                            "case": index, "start_tick": start_tick, "policy": policy,
                            **evaluate(world, policy, budget, start_tick),
                        })
    paired(rows, seeds, budgets, cases, domains)
    summary = []
    differences = []
    for domain in domains:
        for budget in budgets:
            for policy in POLICIES:
                subset = [r for r in rows if r["domain"] == domain and
                          r["budget"] == budget and r["policy"] == policy]
                summary.append({
                    "domain": domain, "budget": budget, "policy": policy,
                    "seeds": len(seeds), "episodes": len(subset),
                    "safe": sum(r["safe_confirm"] for r in subset),
                    "unsafe": sum(r["unsafe"] for r in subset),
                    "cost": sum(r["cost"] for r in subset),
                    "unresolved": sum(r["unresolved"] for r in subset),
                })
    index = {(r["seed"], r["domain"], r["budget"], r["case"], r["policy"]): r
             for r in rows}
    for seed in seeds:
        for domain in domains:
            for budget in budgets:
                for baseline in ("route_rule", "rule_meta", "myopic"):
                    a = [index[(seed, domain, budget, i, "capability_plan")]
                         for i in range(cases)]
                    b = [index[(seed, domain, budget, i, baseline)]
                         for i in range(cases)]
                    differences.append({
                        "seed": seed, "domain": domain,
                        "budget": budget, "baseline": baseline,
                        "delta_safe": sum(x["safe_confirm"] - y["safe_confirm"]
                                          for x, y in zip(a, b)),
                        "delta_cost": sum(x["cost"] - y["cost"]
                                          for x, y in zip(a, b)),
                    })
    output.mkdir(parents=True, exist_ok=True)
    write_csv(output / "episodes.csv", rows)
    write_csv(output / "summary.csv", summary)
    write_csv(output / "per_seed_differences.csv", differences)
    protocol = {
        "kind": "synthetic_meta_aware_not_go_http",
        "seeds": seeds, "budgets": budgets, "cases_per_seed": cases,
        "calibration_support_equals_test_support": True,
        "shifted_test_weights_hidden_from_planner": True,
        "test_domains": domains,
        "no_model_novelty_claim": True,
        "runtime_authorization_ignores_model_probability": True,
        "four_dimensions": [
            "proof_validity", "source_capability",
            "factual_and_inferential_conflicts", "hard_budget",
        ],
    }
    (output / "protocol.json").write_text(
        json.dumps(protocol, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# 四维耦合条件证据规划研究", "",
        "结果是合成受控研究，不能代表厂商 HTTP 服务或独立外部样本。",
        "规划器采用标准有限世界动态规划，不能仅凭结果声称全新算法。",
        "安全动作仅由审计器授权；模型预测不授予确认或重发权限。",
        "所有方法具有相同预算上限，不等于相同实际开销。", "",
        "| 预算 | 策略 | 案例 | 安全确认 | 不安全确认 | 成本 |",
        "|---:|---|---:|---:|---:|---:|",
    ]
    lines.extend(
        f"| {r['budget']} | {r['domain']}/{r['policy']} | {r['episodes']} | "
        f"{r['safe']} | {r['unsafe']} | {r['cost']} |"
        for r in summary
    )
    (output / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    names = ("episodes.csv", "summary.csv", "per_seed_differences.csv",
             "protocol.json", "REPORT.md")
    (output / "SHA256SUMS.json").write_text(
        json.dumps({name: sha256((output / name).read_bytes()).hexdigest()
                    for name in names}, indent=2) + "\n", encoding="utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser(description="四维证据审计与条件规划耦合实验")
    parser.add_argument("--seeds", default="71,72,73,74,75")
    parser.add_argument("--budgets", default="2,3,4")
    parser.add_argument("--cases", type=int, default=80)
    parser.add_argument("--output", type=Path, default=Path("results/evidence_meta"))
    args = parser.parse_args()
    study(tuple(int(v) for v in args.seeds.split(",")),
          tuple(int(v) for v in args.budgets.split(",")), args.cases, args.output)


if __name__ == "__main__":
    main()
