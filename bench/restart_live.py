"""独立 Go 沙箱中的真实进程崩溃及多进程恢复对照。

实验仅操作回环沙箱：先登记写入意图，再强杀一个子进程。
外部物理效果由隔离沙箱的独立 oracle 判分，不用于恢复决策。
B5 与 RAGuard 均使用同一个网络客户端、业务键和进程重启步骤。
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request

from bench.durable_baseline import DurableBaseline
from bench.oracle import EffectEvent, evaluate
from bench.runner import BUSINESS_KEY, local_sandbox, raw_call
from raguard.gateway import Gateway
from raguard.identity import BusinessIntentRegistry, VerifiedIdentity
from raguard.ledger import Ledger
from raguard.model import Call
from raguard.policy import TOOLS
from raguard.provider_evidence import LocalEvidenceClient
from scripts.sandbox_binary import ROOT

METHODS = ('b5_durable', 'raguard')
TOOLS_TESTED = ('orders.create', 'tickets.create', 'notifications.send')
BUSINESS_REF = 'restart-test-business'
CRASH_BEFORE = 72
CRASH_AFTER = 73


@dataclass(frozen=True)
class Episode:
    method: str
    tool: str
    crash_point: str
    no_readback: int
    contenders: int
    recovery_policy: str
    physical_goal: int
    duplicate_effects: int
    unsafe: int
    erroneous_confirmation: int
    resumed_successes: int
    final_status: int
    unresolved: int
    physical_writes: int
    network_attempts: int
    readbacks: int
    elapsed_ms: float


def _engine(method: str, workspace: Path, client: LocalEvidenceClient,
            *, owner_exit_observed: bool = False):
    if method == 'b5_durable':
        return DurableBaseline(workspace / 'durable.sqlite3', client)
    if method == 'raguard':
        return Gateway(Ledger(str(workspace / 'ledger.sqlite3')), client,
                       # 提前交接只在测试驱动确实 wait() 到旧工作者死亡后允许。
                       lease_seconds=0 if owner_exit_observed else 5,
                       identities=BusinessIntentRegistry(str(workspace / 'identity.sqlite3')))
    raise ValueError('未知方法')


def worker(method: str, tool: str, point: str, origin: str,
           workspace: Path, *, owner_exit_observed: bool = False) -> int:
    """只由独立 Python 进程调用；强制退出不会运行对象析构逻辑。"""
    client = LocalEvidenceClient(origin, research_business_key=BUSINESS_KEY)
    raw = raw_call(tool)
    engine = _engine(method, workspace, client,
                     owner_exit_observed=owner_exit_observed)
    if point == 'resume':
        if method == 'b5_durable':
            status, _ = engine.call(raw, BUSINESS_REF)
        else:
            status, _ = engine.call(raw, identity=VerifiedIdentity(BUSINESS_REF))
        print(json.dumps({'status': status}))
        return 0

    call = Call.parse(raw)
    if method == 'b5_durable':
        original, _state, _last, is_new = engine._claim(call, BUSINESS_REF)
        if not is_new:
            raise RuntimeError('崩溃前预置意图必须是新操作')
    else:
        original = engine.identities.resolve(VerifiedIdentity(BUSINESS_REF), call)
        _record, is_new = engine.ledger.claim(original)
        if not is_new:
            raise RuntimeError('崩溃前预置意图必须是新操作')

    # 真实进程崩溃点，而不是正常返回、try/except 或模拟抛出异常。
    if point == 'before':
        os._exit(CRASH_BEFORE)
    if point != 'after':
        raise ValueError('未知崩溃点')
    client.execute(original, TOOLS[tool])
    os._exit(CRASH_AFTER)


def _command(method: str, tool: str, point: str, origin: str, work: Path,
             owner_exit_observed: bool = False) -> list[str]:
    cmd = [sys.executable, '-m', 'bench.restart_live', 'worker',
            '--method', method, '--tool', tool, '--point', point,
            '--origin', origin, '--workspace', str(work)]
    return cmd + (['--owner-exit-observed'] if owner_exit_observed else [])


def _launch(command: list[str]) -> subprocess.Popen:
    return subprocess.Popen(command, cwd=ROOT, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def run_case(method: str, tool: str, point: str, no_readback: bool,
             contenders: int, binary: Path, *,
             recovery_policy: str = 'standard') -> Episode:
    if (method not in METHODS or tool not in TOOLS_TESTED or point not in ('before', 'after')
            or type(no_readback) is not bool or type(contenders) is not int
            or not 1 <= contenders <= 8
            or recovery_policy not in ('standard', 'dead_owner')):
        raise ValueError('非法实验条件')
    start = time.perf_counter()
    with local_sandbox(binary, '', no_readback) as origin, tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        first = subprocess.run(_command(method, tool, point, origin, work), cwd=ROOT,
                               capture_output=True, text=True, timeout=12, check=False)
        expected_exit = CRASH_BEFORE if point == 'before' else CRASH_AFTER
        if first.returncode != expected_exit:
            raise RuntimeError(f'崩溃注入未到达预期阶段：{first.returncode}')

        # 真正启动多个新的 Python 解释器，共享同一持久数据库和沙箱。
        processes = [_launch(_command(method, tool, 'resume', origin, work,
                                      owner_exit_observed=recovery_policy == 'dead_owner'))
                     for _ in range(contenders)]
        statuses = []
        for proc in processes:
            stdout, stderr = proc.communicate(timeout=15)
            if proc.returncode != 0:
                raise RuntimeError(f'恢复进程异常：{proc.returncode} {stderr[-250:]}')
            statuses.append(json.loads(stdout)['status'])

        # 并发窗口结束后再发一个只读/恢复请求，区分暂时 202 和永久未完成。
        final = subprocess.run(
            _command(method, tool, 'resume', origin, work,
                     owner_exit_observed=recovery_policy == 'dead_owner'),
            cwd=ROOT, capture_output=True, text=True, timeout=15, check=True,
        )
        final_status = json.loads(final.stdout)['status']

        # 仅评价器可访问效果历史；不能让 Gateway 或 B5 调用这个接口。
        with urllib.request.urlopen(origin + '/v1/lab/effects', timeout=2) as response:
            truth = json.load(response)
        events = [EffectEvent(item['kind'], item['effect_id'], item['business_key'])
                  for item in truth['events']]
        # 原生服务端幂等订单可在重启后安全完成，即使初始进程死于派发前。
        expected = {BUSINESS_KEY: 1} if point == 'after' or tool == 'orders.create' else {}
        outcome = evaluate(events, expected)
        physical_writes = sum(item['kind'] == 'commit' for item in truth['events'])
        false_confirmations = (sum(status == 200 for status in statuses)
                               + int(final_status == 200)) if not outcome.task_success else 0
        elapsed = round((time.perf_counter() - start) * 1000, 3)
        return Episode(method, tool, point, int(no_readback), contenders,
                       recovery_policy,
                       int(outcome.task_success),
                       int(outcome.duplicate_executions),
                       int(outcome.duplicate_executions > 0 or outcome.unintended_executions > 0),
                       false_confirmations, sum(status == 200 for status in statuses),
                       final_status, sum(status != 200 for status in statuses),
                       physical_writes,
                       int(truth['attempts']), int(truth['readbacks']), elapsed)


def matrix(binary: Path) -> list[Episode]:
    scenarios = (
        ('tickets.create', 'after', False),
        ('tickets.create', 'after', True),
        ('tickets.create', 'before', False),
        ('notifications.send', 'after', False),
        ('orders.create', 'after', True),
        ('orders.create', 'before', True),
    )
    return [run_case(method, tool, point, unreadable, 3, binary,
                     recovery_policy=policy)
            for method in METHODS
            for policy in (('standard',) if method == 'b5_durable'
                           else ('standard', 'dead_owner'))
            for tool, point, unreadable in scenarios]


def build_binary(workspace: Path) -> Path:
    import shutil
    go = shutil.which('go')
    if not go:
        raise RuntimeError('进程崩溃实验需要 Go 1.21+ 编译本地沙箱')
    target = workspace / ('restart-mock.exe' if os.name == 'nt' else 'restart-mock')
    subprocess.run([go, 'build', '-o', str(target), '.'], cwd=ROOT / 'mock',
                   capture_output=True, text=True, timeout=120, check=True)
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description='Go 沙箱真实进程崩溃与恢复安全对照')
    parser.add_argument('command', nargs='?', choices=('matrix', 'worker'), default='matrix')
    parser.add_argument('--method', choices=METHODS)
    parser.add_argument('--tool', choices=TOOLS_TESTED)
    parser.add_argument('--point', choices=('before', 'after', 'resume'))
    parser.add_argument('--origin')
    parser.add_argument('--workspace', type=Path)
    parser.add_argument('--owner-exit-observed', action='store_true')
    parser.add_argument('--output', type=Path, default=Path('results/restart_live'))
    args = parser.parse_args()
    if args.command == 'worker':
        if not all((args.method, args.tool, args.point, args.origin, args.workspace)):
            parser.error('worker 缺少必要的执行条件')
        return worker(args.method, args.tool, args.point, args.origin, args.workspace,
                      owner_exit_observed=args.owner_exit_observed)
    with tempfile.TemporaryDirectory() as temp:
        outcomes = matrix(build_binary(Path(temp)))
    args.output.mkdir(parents=True, exist_ok=True)
    import csv
    with (args.output / 'episodes.csv').open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(asdict(outcomes[0])))
        writer.writeheader()
        writer.writerows(asdict(o) for o in outcomes)
    report = [
        '# 真实进程崩溃及多进程恢复（Go 沙箱）', '',
        'B5 和 RAGuard 均先完成持久写前登记，再在外部效果前或效果后强制退出子进程。',
        '三个新 Python 进程并行恢复；物理真值独立于恢复策略。',
        '实验不连真实第三方服务，不能证明分布式 exactly-once。', '',
        '| 方法 | 交接策略 | 场景 | 不安全 | 错误确认 | 即时确认（请求） | 最终确认（场景） |',
        '| --- | --- | ---: | ---: | ---: | ---: | ---: |',
    ]
    for method in METHODS:
        for policy in (('standard',) if method == 'b5_durable'
                       else ('standard', 'dead_owner')):
            rows = [r for r in outcomes if r.method == method and r.recovery_policy == policy]
            report.append(f'| {method} | {policy} | {len(rows)} | '
                          f'{sum(r.unsafe for r in rows)} | '
                          f'{sum(r.erroneous_confirmation for r in rows)} | '
                          f'{sum(r.resumed_successes for r in rows)} | '
                          f'{sum(r.final_status == 200 for r in rows)} |')
    (args.output / 'REPORT.md').write_text('\n'.join(report) + '\n', encoding='utf-8')
    print(f'已完成 {len(outcomes)} 条进程崩溃及并发恢复对照')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
