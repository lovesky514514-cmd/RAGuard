"""真实 Go HTTP 证据读取与 B5/RAGuard 同预算配对。"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request

from bench.evidence_budget import Budget, Source, query_budgeted
from bench.evidence_frontier import Context, Decision, Evidence, Kind
from bench.runner import raw_call
from bench.window_live import METHODS, ORIGIN_SCOPE, WindowClient, engine_call, window_server, write_csv
from bench.window_uncertainty import control
from raguard.model import Call
from raguard.transport import UncertainResult

ROOT = Path(__file__).resolve().parents[1]
SOURCES = (
    Source("cheap", 1, 1, frozenset({Kind.COMMITTED})),
    Source("quick", 2, 1, frozenset({Kind.COMMITTED})),
)
BUDGET = Budget(2, 2, 2)
METHOD_POLICIES = ("fixed", "fixed_quick", "calibrated")
PROFILES = ("matched", "shifted")
FAULTS = ("before_commit", "after_commit")
TICKS = (0, 1, 3)
# 仅用先验排序；未知 shifted 环境不得进入校准。
ESTIMATES = {"cheap": 0.2, "quick": 0.8}


class EvidenceClient(WindowClient):
    def __init__(self, origin: str, fault: str, strategy: str, tick: int):
        super().__init__(origin, drop_count=2, drop_point=fault, scope=ORIGIN_SCOPE)
        self.strategy = strategy
        self.tick = tick
        self.budget_result = None
        self.queried = False
        self.enabled = False
        self.blocked_recovery_writes = 0

    def execute(self, call, policy):
        if self.enabled:
            # 共享消融：排除原生重放导致的确认，显式记录被拦截的写入。
            self.blocked_recovery_writes += 1
            raise UncertainResult("仅证据恢复实验禁止二次写入")
        return super().execute(call, policy)

    def readback(self, call, policy):
        del policy
        if not self.enabled:
            return None
        if self.queried:
            return None
        self.queried = True
        context = Context(call.stable_key, self.scope, call.fingerprint,
                          "windowlab", 1, self.tick, False, False)

        def fetch(source):
            params = urllib.parse.urlencode({
                "source": source, "scope": self.scope, "key": call.stable_key,
                "fingerprint": call.fingerprint,
            })
            req = urllib.request.Request(self.origin + "/v1/evidence/view?" + params)
            try:
                with urllib.request.urlopen(req, timeout=1) as response:
                    data = json.load(response)
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    return None
                raise
            if (data.get("status") != "committed"
                    or data.get("source") != source
                    or not isinstance(data.get("effect_id"), str)
                    or not data["effect_id"]):
                return None
            return Evidence(
                Kind.COMMITTED, data["key"], data["scope"], data["fingerprint"],
                "windowlab", 1, data["valid_until"], authenticated=True, final=True,
            )

        # 固定 quick 对照与校准策略恰好使用相同来源、成本与延迟。
        sources = tuple(reversed(SOURCES)) if self.strategy == "fixed_quick" else SOURCES
        policy = "fixed" if self.strategy == "fixed_quick" else self.strategy
        self.budget_result = query_budgeted(
            context, sources, BUDGET, fetch, policy=policy,
            estimates=ESTIMATES if policy == "calibrated" else None,
        )
        if self.budget_result.verdict.action == Decision.CONFIRM:
            return {"effect_id": "verified-positive-readback"}
        return None


@dataclass(frozen=True)
class Episode:
    profile: str
    fault: str
    recovery_tick: int
    engine: str
    strategy: str
    final_status: int
    confirmed: int
    false_confirmation: int
    duplicate_effects: int
    wrong_scope_effects: int
    unresolved: int
    evidence_cost: int
    evidence_queries: int
    evidence_latency: int
    blocked_recovery_writes: int
    network_attempts: int


def run_one(binary: Path, profile: str, fault: str, tick: int,
            engine: str, strategy: str) -> Episode:
    with window_server(binary, ttl=6, extra_args=("--evidence-mode", profile)) as origin, tempfile.TemporaryDirectory() as tmp:
        raw = raw_call("orders.create")
        client = EvidenceClient(origin, fault, strategy, tick)
        work = Path(tmp)
        engine_call(engine, work, client, raw)
        control(origin, "advance", {"ticks": tick})
        # 恢复阶段才开放证据预算；前序故障不消耗研究预算。
        client.enabled = True
        client.queried = False
        client.drop_count = 0
        status = engine_call(engine, work, client, raw)
        truth = control(origin, "effects")
        result = client.budget_result
    effects = truth["effects"]
    wrong = sum(e["scope"] != ORIGIN_SCOPE for e in effects)
    duplicate = max(0, len(effects) - 1)
    confirmed = status == 200 and len(effects) == 1 and wrong == 0
    return Episode(profile, fault, tick, engine, strategy, status,
                   int(confirmed), int(status == 200 and not confirmed),
                   duplicate, wrong, int(status != 200),
                   result.cost if result else 0, result.queries if result else 0,
                   result.latency if result else 0,
                   client.blocked_recovery_writes, truth["attempts"])


def paired(rows):
    index = {}
    for row in rows:
        key = (row.profile, row.fault, row.recovery_tick, row.engine, row.strategy)
        if key in index:
            raise ValueError("重复试验身份")
        index[key] = row
    for profile in PROFILES:
        for fault in FAULTS:
            for tick in TICKS:
                for engine in METHODS:
                    for strategy in METHOD_POLICIES:
                        if (profile, fault, tick, engine, strategy) not in index:
                            raise ValueError("试验配对不完整")
    if len(index) != len(PROFILES)*len(FAULTS)*len(TICKS)*len(METHODS)*len(METHOD_POLICIES):
        raise ValueError("存在多余试验")
    return [{
        "profile": profile, "fault": fault, "tick": tick, "strategy": strategy,
        "b5_confirmed": index[(profile, fault, tick, "b5", strategy)].confirmed,
        "raguard_confirmed": index[(profile, fault, tick, "raguard", strategy)].confirmed,
    } for profile in PROFILES for fault in FAULTS for tick in TICKS
      for strategy in METHOD_POLICIES]


def main():
    parser = argparse.ArgumentParser(description="HTTP 正向证据下同预算强基线对照")
    parser.add_argument("--output", type=Path, default=Path("results/evidence_http"))
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        binary = Path(tmp)/("uncertainty.exe" if os.name=="nt" else "uncertainty")
        subprocess.run(["go","build","-o",str(binary),"./windowlab/cmd/uncertainty"],
                       cwd=ROOT/"mock",check=True,timeout=120,capture_output=True)
        rows = [run_one(binary,p,f,t,m,s) for p in PROFILES for f in FAULTS
                for t in TICKS for m in METHODS for s in METHOD_POLICIES]
    pairs = paired(rows)
    args.output.mkdir(parents=True,exist_ok=True)
    write_csv(args.output/"episodes.csv",[asdict(r) for r in rows])
    write_csv(args.output/"paired.csv",pairs)
    lines=["# 真实 HTTP 证据策略对照","",
           "仅隔离 Go 沙箱；B5 与 RAGuard 原有账本，不修改生产 Gateway。",
           "ECF 仅为共享证据预算调度器，不是第三套独立执行引擎。",
           "新增研究消融：双方恢复阶段经共同适配器禁止所有二次写入，不能当作原生 B5/Gateway 端到端指标。",
           "来源是同一物理日志的不同可见性视图，不能称为独立权威。",
           "校准排序是预置先验，并非从真实训练 HTTP 实验估计；不声称学习优势。",
           "fixed_quick 是相同花费的静态 quick 来源对照，防止把成本差误称算法收益。",
           "该测试只构成有限确定性场景，非统计独立样本。","",
           "| 环境 | 方法 | 策略 | 案例 | 安全确认 | 不安全确认 | 重复效果 | 未完成 | 证据查询 |",
           "|---|---|---|---:|---:|---:|---:|---:|---:|"]
    for p in PROFILES:
        for m in METHODS:
            for s in METHOD_POLICIES:
                group=[r for r in rows if (r.profile,r.engine,r.strategy)==(p,m,s)]
                totals=[sum(getattr(r,k) for r in group) for k in
                        ("confirmed","false_confirmation","duplicate_effects","unresolved","evidence_queries")]
                lines.append(f"| {p} | {m} | {s} | {len(group)} | "+ " | ".join(map(str,totals))+" |")
    (args.output/"REPORT.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    digest={p:sha256((args.output/p).read_bytes()).hexdigest() for p in
            ("episodes.csv","paired.csv","REPORT.md")}
    (args.output/"SHA256SUMS.json").write_text(json.dumps(digest,indent=2)+"\n",encoding="utf-8")
    print("\n".join(lines))


if __name__=="__main__":
    main()
