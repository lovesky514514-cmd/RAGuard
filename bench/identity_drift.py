"""业务动作在 Agent 改名后的副作用安全对照；仅用于受控 Go 沙箱。"""

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import tempfile
import time
import urllib.request

from bench.analyze import write_csv
from bench.oracle import EffectEvent, evaluate
from bench.runner import BUSINESS_KEY, execute_baseline, local_sandbox, raw_call
from raguard.gateway import Gateway
from raguard.identity import BusinessIntentRegistry, VerifiedIdentity
from raguard.ledger import Ledger
from raguard.transport import SandboxClient
from scripts.sandbox_binary import build_sandbox

METHODS = ('b3_step', 'b3_business', 'raguard')
TOOLS = ('orders.create', 'tickets.create', 'notifications.send')
FAULTS = ('none', 'after_disconnect', 'after_invalid_json')


@dataclass(frozen=True)
class DriftResult:
    method: str
    tool: str
    fault: str
    no_readback: int
    repeat: int
    first_status: int
    renamed_status: int
    physical_goal: int
    duplicate_effects: int
    unsafe: int
    fault_fired: int
    attempts: int
    readbacks: int
    elapsed_ms: float


def renamed_call(original: dict) -> dict:
    """仅改变代理任务与步骤标识；业务语义和参数保持不变。"""
    return {**original, 'task': 'replanned-task',
            'step': 'replanned-step', 'intent': 'replanned-intent'}


def run_case(method: str, tool: str, fault: str, no_readback: bool,
             binary: Path, *, repeat: int = 0) -> DriftResult:
    """先执行，再模拟重规划/进程重启；真值只从独立沙箱读取。"""
    if method not in METHODS or tool not in TOOLS or fault not in FAULTS or repeat < 0:
        raise ValueError('未注册的实验条件')
    injection = '' if fault == 'none' else f'{tool}:{fault}'
    with local_sandbox(binary, injection, no_readback) as origin:
        client = SandboxClient(origin, timeout=0.6, research_business_key=BUSINESS_KEY)
        first = raw_call(tool)
        renamed = renamed_call(first)
        start = time.perf_counter()
        if method == 'b3_step':
            # 强幂等恢复，但将新步骤当作新意图；不是拥有业务键存储的 B3。
            first_status, _ = execute_baseline('b3', client, first)
            second_status, _ = execute_baseline('b3', client, renamed)
        elif method == 'b3_business':
            # 反事实加强：两次都使用相同外部业务身份，但不增添持久执行账本。
            first_status, _ = execute_baseline('b3', client, first)
            second_status, _ = execute_baseline('b3', client, first)
        else:
            with tempfile.TemporaryDirectory() as folder:
                ledger_file = str(Path(folder) / 'ledger.sqlite3')
                identities_file = str(Path(folder) / 'identity.sqlite3')
                reference = VerifiedIdentity('external-business-ref')
                gateway = Gateway(Ledger(ledger_file), client,
                                  identities=BusinessIntentRegistry(identities_file))
                first_status, _ = gateway.call(first, identity=reference)
                # 新进程式地重新创建账本和身份注册对象，验证数据库恢复。
                resumed = Gateway(Ledger(ledger_file), client,
                                  identities=BusinessIntentRegistry(identities_file))
                second_status, _ = resumed.call(renamed, identity=reference)
        elapsed = (time.perf_counter() - start) * 1000
        with urllib.request.urlopen(origin + '/v1/lab/effects', timeout=2) as response:
            world = json.load(response)
        events = [EffectEvent(event['kind'], event['effect_id'], event['business_key'])
                  for event in world['events']]
        outcome = evaluate(events, {BUSINESS_KEY: 1})
        return DriftResult(method, tool, fault, int(no_readback), repeat,
                           first_status, second_status, int(outcome.task_success),
                           outcome.duplicate_executions,
                           int(outcome.duplicate_executions > 0 or
                               outcome.unintended_executions > 0),
                           int(world['fault_fired']), int(world['attempts']),
                           int(world['readbacks']), round(elapsed, 3))


def main() -> int:
    parser = argparse.ArgumentParser(description='业务改名与证据失效对照实验')
    parser.add_argument('--output', type=Path, default=Path('results/identity_drift'))
    parser.add_argument('--repeats', type=int, default=1)
    args = parser.parse_args()
    if not 1 <= args.repeats <= 100:
        parser.error('重复次数需要在 1 到 100 之间')
    with tempfile.TemporaryDirectory() as work:
        binary = build_sandbox(Path(work))
        rows = [asdict(run_case(method, tool, fault, no_readback, binary, repeat=i))
                for tool in TOOLS for fault in FAULTS for no_readback in (False, True)
                for i in range(args.repeats) for method in METHODS]
    args.output.mkdir(parents=True, exist_ok=True)
    write_csv(args.output / 'episodes.csv', rows)
    lines = ['# 业务改名实验（探索性）', '',
             '同一业务只应有一次物理副作用；两个请求使用相同参数，第二次改变任务/步骤名。',
             '每组使用隔离的 Go 沙箱，故障只注入首个请求；同条件各方法严格配对。',
             'b3_step：步骤级稳定键；b3_business：业务级稳定键，但无持久执行账本。',
             'RAGuard：持久业务绑定 + 账本；B3 不是完整商业系统复现，不能据此宣称普适优势。', '',
             '| 方法 | 样本 | 重复副作用 | 安全停止（第二次） |',
             '|---|---:|---:|---:|']
    for method in METHODS:
        subset = [row for row in rows if row['method'] == method]
        lines.append(f"| {method} | {len(subset)} | {sum(row['unsafe'] for row in subset)} | "
                     f"{sum(row['renamed_status'] != 200 for row in subset)} |")
    lines += ['', '安全停止会减少可用性，不能等同成功；要投稿仍需与拥有同等业务身份和持久账本的强基线对照。']
    (args.output / 'REPORT.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    print(f'完成 {len(rows)} 个隔离试验：{args.output / "episodes.csv"}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
