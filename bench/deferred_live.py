"""真实 Go 沙箱：可信证据延迟时的有界、只读恢复，对 B5/RAGuard 公平比较。"""

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

METHODS = ('b5_nowait', 'raguard_nowait', 'b5_deferred', 'raguard_deferred')
TOOLS = ('orders.create', 'tickets.create')
FAULTS = ('none', 'before_disconnect', 'after_disconnect')
EVIDENCE_MODES = ('normal', 'wrong_business', 'not_final')
DELAY_READS = (0, 2, 5, 20)
WAIT_BUDGET = EvidenceWaitBudget(checks=5, interval_seconds=0.002, deadline_seconds=3)


@dataclass(frozen=True)
class Episode:
    method: str
    tool: str
    fault: str
    evidence_mode: str
    evidence_delay_reads: int
    initial_status: int
    resume_status: int
    physical_goal: int
    acknowledged_success: int
    unsafe: int
    duplicate_effects: int
    false_confirmation: int
    unresolved: int
    write_attempts: int
    readbacks: int
    deferred_checks: int
    elapsed_ms: float


def run_case(method: str, tool: str, fault: str, evidence_mode: str,
             delay_reads: int, binary: Path) -> Episode:
    if (method not in METHODS or tool not in TOOLS or fault not in FAULTS
            or evidence_mode not in EVIDENCE_MODES
            or type(delay_reads) is not int or delay_reads not in DELAY_READS):
        raise ValueError('非法的对照条件')
    selected = '' if fault == 'none' else f'{tool}:{fault}'
    with local_sandbox(binary, selected, False, evidence_mode=evidence_mode,
                       evidence_delay_reads=delay_reads) as origin:
        client = LocalEvidenceClient(origin, timeout=0.6, research_business_key=BUSINESS_KEY)
        raw = raw_call(tool)
        call = Call.parse(raw)
        policy = POLICIES[tool]
        start = time.perf_counter()
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            if method.startswith('b5_'):
                db = folder / 'durable.sqlite3'

                def resume():
                    return DurableBaseline(db, client).call(raw, 'verified-operation')[0]
            else:
                ledger = folder / 'ledger.sqlite3'
                registry = folder / 'identity.sqlite3'
                identity = VerifiedIdentity('verified-operation')

                def resume():
                    return Gateway(Ledger(str(ledger)), client,
                                   identities=BusinessIntentRegistry(str(registry))).call(
                                       raw, identity=identity)[0]

            first = resume()
            second = resume()
            checks = 0
            if method.endswith('_deferred') and second != 200:
                # 仅只读等待；收到可信正向结果后，重进原引擎持久化确认。
                waiting = wait_for_evidence(
                    call, policy, client.readback, WAIT_BUDGET,
                    should_continue=lambda: client.last_evidence_state != 'contract_violation')
                checks = waiting.checks
                if waiting.confirmed:
                    second = resume()
        elapsed = (time.perf_counter() - start) * 1000
        # 仅测试评价器可读取真值。这里绝不把它反馈给恢复策略。
        with urllib.request.urlopen(origin + '/v1/lab/effects', timeout=2) as response:
            world = json.load(response)
        events = [EffectEvent(x['kind'], x['effect_id'], x['business_key'])
                  for x in world['events']]
        outcome = evaluate(events, {BUSINESS_KEY: 1})
        confirmed = second == 200
        return Episode(method, tool, fault, evidence_mode, delay_reads,
                       first, second, int(outcome.task_success),
                       int(confirmed and outcome.task_success),
                       int(outcome.duplicate_executions > 0 or outcome.unintended_executions > 0),
                       int(outcome.duplicate_executions),
                       int(confirmed and not outcome.task_success),
                       int(not confirmed), int(world['attempts']), int(world['readbacks']),
                       checks, round(elapsed, 3))


def matrix(binary: Path) -> list[dict]:
    return [asdict(run_case(method, tool, fault, mode, delay, binary))
            for tool in TOOLS for fault in FAULTS for mode in EVIDENCE_MODES
            for delay in DELAY_READS for method in METHODS]


def main() -> int:
    parser = argparse.ArgumentParser(description='真实 Go 沙箱延迟证据只读恢复对照')
    parser.add_argument('--output', type=Path, default=Path('results/deferred_live'))
    options = parser.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        binary = build_current_mock(Path(tmp))
        rows = matrix(binary)
    options.output.mkdir(parents=True, exist_ok=True)
    write_csv(options.output / 'episodes.csv', rows)
    lines = [
        '# 延迟证据有界恢复（本地 Go 沙箱）', '',
        'B5 和 RAGuard 使用相同可信适配器、故障、预算与独立真值；不使用真值授权恢复。',
        '延迟按 Go 沙箱的回读次数注入，不是生产网络延迟模型。', '',
        '| 方法 | 条数 | 已确认成功 | 未完成 | 错误确认 | 不安全 | 写入尝试 | 回读 |',
        '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |',
    ]
    for method in METHODS:
        group = [x for x in rows if x['method'] == method]
        lines.append(f'| {method} | {len(group)} | '
                     f'{sum(x["acknowledged_success"] for x in group)} | '
                     f'{sum(x["unresolved"] for x in group)} | '
                     f'{sum(x["false_confirmation"] for x in group)} | '
                     f'{sum(x["unsafe"] for x in group)} | '
                     f'{sum(x["write_attempts"] for x in group)} | '
                     f'{sum(x["readbacks"] for x in group)} |')
    lines += ['', '正向证据延迟时，仅允许等待/再次查询，不允许将未找到效果作为安全重发许可。',
              '所有比较均为受控沙箱探索性研究；B5 同样可采用此等待方法，不能据此声称独有创新。']
    (options.output / 'REPORT.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(f'已运行 {len(rows)} 条真实 Go 沙箱延迟证据实验')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
