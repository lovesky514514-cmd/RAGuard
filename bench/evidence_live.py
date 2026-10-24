"""独立 Go 沙箱端到端配对：共享 Provider 证据适配器的 B5 与 RAGuard。"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import shutil
import subprocess
from pathlib import Path
import tempfile
import time
import urllib.request

from bench.analyze import write_csv
from bench.durable_baseline import DurableBaseline
from bench.oracle import EffectEvent, evaluate
from bench.runner import BUSINESS_KEY, local_sandbox, raw_call
from raguard.gateway import Gateway
from raguard.identity import BusinessIntentRegistry, VerifiedIdentity
from raguard.ledger import Ledger
from raguard.provider_evidence import LocalEvidenceClient
from scripts.sandbox_binary import ROOT

METHODS = ('b5_durable', 'raguard')
TOOLS = ('orders.create', 'tickets.create')
FAULTS = ('none', 'after_disconnect')
EVIDENCE_MODES = ('normal', 'wrong_business', 'wrong_epoch',
                  'wrong_digest', 'stale', 'not_final')


@dataclass(frozen=True)
class Episode:
    method: str
    tool: str
    fault: str
    evidence_mode: str
    no_readback: int
    repeat: int
    initial_status: int
    resume_status: int
    physical_goal: int
    false_confirmation: int
    duplicate_effects: int
    unsafe: int
    unresolved: int
    attempts: int
    readbacks: int
    admitted_proofs: int
    rejected_proofs: int
    elapsed_ms: float


def run_case(method: str, tool: str, fault: str, evidence_mode: str,
             no_readback: bool, binary: Path, *, repeat: int = 0) -> Episode:
    if (method not in METHODS or tool not in TOOLS or fault not in FAULTS
            or evidence_mode not in EVIDENCE_MODES or repeat < 0):
        raise ValueError('未注册的实验组合')
    selected = '' if fault == 'none' else f'{tool}:{fault}'
    with local_sandbox(binary, selected, no_readback, evidence_mode=evidence_mode) as origin:
        client = LocalEvidenceClient(origin, timeout=0.6,
                                     research_business_key=BUSINESS_KEY)
        raw = raw_call(tool)
        start = time.perf_counter()
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            if method == 'b5_durable':
                db = directory / 'durable.sqlite3'
                first, _ = DurableBaseline(db, client).call(raw, 'verified-operation')
                second, _ = DurableBaseline(db, client).call(raw, 'verified-operation')
            else:
                ledger = directory / 'ledger.sqlite3'
                registry = directory / 'identity.sqlite3'
                identity = VerifiedIdentity('verified-operation')
                first, _ = Gateway(Ledger(str(ledger)), client,
                                   identities=BusinessIntentRegistry(str(registry))).call(
                                       raw, identity=identity)
                second, _ = Gateway(Ledger(str(ledger)), client,
                                    identities=BusinessIntentRegistry(str(registry))).call(
                                        raw, identity=identity)
        elapsed = (time.perf_counter() - start) * 1000
        with urllib.request.urlopen(origin + '/v1/lab/effects', timeout=2) as answer:
            world = json.load(answer)
        effects = [EffectEvent(e['kind'], e['effect_id'], e['business_key'])
                   for e in world['events']]
        oracle = evaluate(effects, {BUSINESS_KEY: 1})
        physical = int(oracle.task_success)
        confirmed = int(second == 200)
        duplicates = int(oracle.duplicate_executions)
        return Episode(method, tool, fault, evidence_mode, int(no_readback), repeat,
                       first, second, physical,
                       int(confirmed and not physical), duplicates,
                       int(duplicates > 0 or oracle.unintended_executions > 0),
                       int(not confirmed), int(world['attempts']),
                       int(world['readbacks']), client.admitted, client.rejected,
                       round(elapsed, 3))


def matrix(binary: Path, repeats: int = 1) -> list[dict]:
    if not 1 <= repeats <= 10:
        raise ValueError('repeats 应介于 1 和 10 之间')
    return [asdict(run_case(method, tool, fault, mode, no_readback, binary, repeat=r))
            for r in range(repeats)
            for tool in TOOLS for fault in FAULTS
            for mode in EVIDENCE_MODES for no_readback in (False, True)
            for method in METHODS]


def build_current_mock(directory: Path) -> Path:
    """证据实验必须使用当前 Go 源码；旧 Windows 内置程序没有证据字段。"""
    go = shutil.which('go')
    if go is None:
        raise RuntimeError('证据集成实验需要 Go 1.21+ 编译新沙箱；旧版内置 exe 不适用')
    target = directory / ('evidence-mock.exe' if __import__('os').name == 'nt' else 'evidence-mock')
    subprocess.run([go, 'build', '-o', str(target), '.'], cwd=ROOT / 'mock',
                   check=True, capture_output=True, text=True, timeout=120)
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description='本地可信沙箱的完整恢复配对对照')
    parser.add_argument('--repeats', type=int, default=1)
    parser.add_argument('--output', type=Path, default=Path('results/evidence_live'))
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        binary = build_current_mock(Path(tmp))
        rows = matrix(binary, repeats=args.repeats)
    args.output.mkdir(parents=True, exist_ok=True)
    write_csv(args.output / 'episodes.csv', rows)
    summary = ['# 本地真实 Go 沙箱证据恢复（探索性）', '',
               '每个条件启用独立沙箱，B5 与 RAGuard 使用同一 Provider 证据适配器。',
               '本地回读没有远程密码学签名，不能外推到真实第三方服务。', '',
               '| 方法 | 案例 | 错误确认 | 不安全 | 未完成 | 查询次数 |',
               '| --- | ---: | ---: | ---: | ---: | ---: |']
    for method in METHODS:
        group = [row for row in rows if row['method'] == method]
        summary.append(f'| {method} | {len(group)} | '
                       f'{sum(z["false_confirmation"] for z in group)} | '
                       f'{sum(z["unsafe"] for z in group)} | '
                       f'{sum(z["unresolved"] for z in group)} | '
                       f'{sum(z["readbacks"] for z in group)} |')
    summary.extend(['', '未完成既不是错误确认，也不等于任务成功。',
                    '本实验不能证明算法独立优势，严格比较需分层看故障与成本。'])
    (args.output / 'REPORT.md').write_text('\n'.join(summary) + '\n', encoding='utf-8')
    print(f'已执行 {len(rows)} 条真实 Go 沙箱配对实验')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
