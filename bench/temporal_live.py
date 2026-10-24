"""同等查询预算下，连续轮询与按时间间隔轮询的真实 Go 沙箱对照。

并不声称间隔轮询具有新颖性；主要验证延迟证据恢复的安全性与成本边界。
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import tempfile
import time
import urllib.request

from bench.analyze import write_csv
from bench.durable_baseline import DurableBaseline
from bench.evidence_live import build_current_mock
from bench.oracle import EffectEvent, evaluate
from bench.runner import BUSINESS_KEY, local_sandbox, raw_call
from raguard.deferred import EvidenceWaitBudget, wait_for_evidence
from raguard.gateway import Gateway
from raguard.identity import BusinessIntentRegistry, VerifiedIdentity
from raguard.ledger import Ledger
from raguard.model import Call
from raguard.policy import TOOLS as POLICIES
from raguard.provider_evidence import LocalEvidenceClient

ENGINES = ('b5', 'raguard')
SCHEDULES = ('burst', 'spaced')
TOOLS = ('orders.create', 'tickets.create')
FAULTS = ('after_disconnect', 'before_disconnect')
VISIBILITY_DELAYS_MS = (0, 120, 450)
INTERVAL_SECONDS = 0.105
WAIT_DEADLINE_SECONDS = 0.35
MAX_POLL_CHECKS = 3


@dataclass(frozen=True)
class Episode:
    engine: str
    schedule: str
    tool: str
    fault: str
    delay_ms: int
    status: int
    physical_goal: int
    false_confirmation: int
    unsafe: int
    unresolved: int
    writes: int
    readbacks: int
    deferred_checks: int
    latency_ms: float


def run_case(engine: str, schedule: str, tool: str, fault: str,
             delay_ms: int, binary: Path) -> Episode:
    if (engine not in ENGINES or schedule not in SCHEDULES or tool not in TOOLS
            or fault not in FAULTS or delay_ms not in VISIBILITY_DELAYS_MS):
        raise ValueError('未经登记的对照条件')
    with local_sandbox(binary, f'{tool}:{fault}', False,
                       evidence_delay_ms=delay_ms) as origin:
        raw = raw_call(tool)
        call = Call.parse(raw)
        client = LocalEvidenceClient(origin, timeout=0.6,
                                     research_business_key=BUSINESS_KEY)
        start = time.perf_counter()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            if engine == 'b5':
                db = root / 'durable.sqlite3'

                def resume():
                    return DurableBaseline(db, client).call(raw, 'verified-operation')[0]
            else:
                db = root / 'ledger.sqlite3'
                ids = root / 'identity.sqlite3'
                identity = VerifiedIdentity('verified-operation')

                def resume():
                    return Gateway(Ledger(str(db)), client,
                                   identities=BusinessIntentRegistry(str(ids))).call(
                                       raw, identity=identity)[0]

            first = resume()
            second = resume()
            checks = 0
            if second != 200:
                budget = EvidenceWaitBudget(
                    checks=MAX_POLL_CHECKS,
                    interval_seconds=0 if schedule == 'burst' else INTERVAL_SECONDS,
                    deadline_seconds=WAIT_DEADLINE_SECONDS)
                waited = wait_for_evidence(
                    call, POLICIES[tool], client.readback, budget,
                    should_continue=lambda: client.last_evidence_state != 'contract_violation')
                checks = waited.checks
                if waited.confirmed:
                    second = resume()  # 真正的确认仍由原引擎写入持久账本。
        elapsed_ms = round((time.perf_counter() - start) * 1000, 3)
        with urllib.request.urlopen(origin + '/v1/lab/effects', timeout=2) as response:
            physical = json.load(response)
        events = [EffectEvent(x['kind'], x['effect_id'], x['business_key'])
                  for x in physical['events']]
        oracle = evaluate(events, {BUSINESS_KEY: 1})
        committed = int(oracle.task_success)
        confirmed = int(second == 200)
        return Episode(
            engine, schedule, tool, fault, delay_ms, second, committed,
            int(confirmed and not committed),
            int(oracle.duplicate_executions > 0 or oracle.unintended_executions > 0),
            int(not confirmed), int(physical['attempts']), int(physical['readbacks']),
            checks, elapsed_ms)


def matrix(binary: Path) -> list[dict]:
    return [asdict(run_case(engine, schedule, tool, fault, delay_ms, binary))
            for tool in TOOLS for fault in FAULTS for delay_ms in VISIBILITY_DELAYS_MS
            for engine in ENGINES for schedule in SCHEDULES]


def main() -> int:
    parser = argparse.ArgumentParser(description='墙钟延迟证据与相同回读预算对照')
    parser.add_argument('--output', type=Path, default=Path('results/temporal_live'))
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as temp:
        rows = matrix(build_current_mock(Path(temp)))
    args.output.mkdir(parents=True, exist_ok=True)
    write_csv(args.output / 'episodes.csv', rows)
    lines = [
        '# Go 沙箱墙钟延迟证据实验', '',
        '固定同一上限：3 次额外证据回读，0.35 秒等待期限；两组均调用共享证据适配器。',
        'burst：立即连续查询；spaced：每次间隔约 105 毫秒。',
        '两类策略均不能依据阴性查询重新写入。',
        'Go 沙箱用提交时间戳控制证据可见性，是真实进程时间模型，不是公网服务。', '',
        '| 引擎 | 调度 | 条数 | 已确认 | 未完成 | 错误确认 | 不安全 | 回读 | 平均策略耗时(ms) |',
        '| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |',
    ]
    for engine in ENGINES:
        for schedule in SCHEDULES:
            group = [r for r in rows if r['engine'] == engine and r['schedule'] == schedule]
            count = sum(r['unresolved'] for r in group)
            lines.append(
                f'| {engine} | {schedule} | {len(group)} | {len(group) - count} | {count} | '
                f'{sum(r["false_confirmation"] for r in group)} | '
                f'{sum(r["unsafe"] for r in group)} | '
                f'{sum(r["readbacks"] for r in group)} | '
                f'{sum(r["latency_ms"] for r in group) / len(group):.1f} |')
    lines.extend(['',
                  '读次相同不等于成本相同；必须同时报告墙钟时延。',
                  '合成 Provider 延迟受机器调度影响，任何单轮差异都只属探索性质。',
                  'B5 可采用相同间隔策略，因此不构成 RAGuard 独有优势。'])
    (args.output / 'REPORT.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(f'已完成 {len(rows)} 条真实 Go 沙箱墙钟实验')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
