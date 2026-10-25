"""有限幂等窗口：真实 B5/Gateway 调用链的公平 HTTP 配对实验。

服务端时钟、原始效果和故障注入只供测试监督器访问；恢复引擎不读真值。
本实验不连接任何支付、云资源或真实订单服务。
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import csv
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request

from bench.durable_baseline import DurableBaseline
from bench.runner import raw_call
from raguard.gateway import Gateway
from raguard.identity import BusinessIntentRegistry, VerifiedIdentity
from raguard.ledger import Ledger
from raguard.model import Call
from raguard.transport import SandboxClient, UncertainResult

ROOT = Path(__file__).resolve().parents[1]
BUSINESS_REF = 'window-study-operation'
ORIGIN_SCOPE = 'region-a'
TTL_TICKS = 4
METHODS = ('b5', 'raguard')
FAULTS = ('after_commit', 'before_commit')
RECOVERY_TICKS = (2, 4, 6)
SCOPES = ('same', 'moved')
ADMISSION = ('unaware', 'contract')


@dataclass(frozen=True)
class VerifiedWindow:
    """可信契约与最早可能接收时间；必须排除仍在途的旧请求。"""

    earliest_possible_accept_tick: int
    min_retention_ticks: int | None
    original_scope: str

    def permits(self, *, tick: int, scope: str) -> bool:
        # 使用最早可能接收时间作保守下界，避免臆测服务器精确接收时刻。
        # 该判定仅适用于旧请求已全部停止的实验条件，不能证明跨主机在途安全。
        return (self.min_retention_ticks is not None
                and type(self.min_retention_ticks) is int
                and self.min_retention_ticks > 0
                and self.earliest_possible_accept_tick <= tick
                and tick < self.earliest_possible_accept_tick + self.min_retention_ticks
                and scope == self.original_scope)


class WindowClient(SandboxClient):
    """B5 与 Gateway 共享的 HTTP 适配器；不提供虚构的正向回读。"""

    def __init__(self, origin: str, *, drop_count: int, drop_point: str, scope: str):
        super().__init__(origin, timeout=0.7)
        if drop_point not in FAULTS or drop_count < 0 or scope not in ('region-a', 'region-b'):
            raise ValueError('非法故障或作用域')
        self.drop_count = drop_count
        self.drop_point = drop_point
        self.scope = scope
        self.readback_checks = 0

    def execute(self, call: Call, policy) -> dict:
        del policy
        drop = self.drop_count > 0
        if drop:
            self.drop_count -= 1
            if self.drop_point == 'before_commit':
                raise UncertainResult('提交前网络断开')
        # 稳定业务键与指纹均来自引擎的原始 Call，严禁每次重试改写。
        receipt = self._json('/v1/execute', {
            'scope': self.scope, 'key': call.stable_key,
            'fingerprint': call.fingerprint,
        })
        effect_id = receipt.get('effect_id')
        if not isinstance(effect_id, str) or not effect_id:
            raise UncertainResult('服务端回执无有效效果标识')
        if drop:
            raise UncertainResult('提交后回执丢失')
        return {'effect_id': effect_id, 'replayed': receipt.get('replayed') is True}

    def readback(self, call: Call, policy) -> None:
        del call, policy
        # windowlab 未提供能证明原操作最终提交的独立证据 API。
        self.readback_checks += 1
        return None


@contextmanager
def window_server(binary: Path, ttl: int = TTL_TICKS):
    """独立子进程模拟服务端；仅研究监督器能推进服务器时钟。"""
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    origin = f'http://127.0.0.1:{port}'
    process = subprocess.Popen([str(binary), '--addr', f'127.0.0.1:{port}',
                                '--ttl', str(ttl)], cwd=ROOT,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                with urllib.request.urlopen(origin + '/healthz', timeout=0.1):
                    break
            except OSError:
                if process.poll() is not None:
                    raise RuntimeError('Go windowlab 提前退出')
                time.sleep(0.01)
        else:
            raise TimeoutError('Go windowlab 启动超时')
        yield origin
    finally:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)


def build_binary(directory: Path) -> Path:
    go = shutil.which('go')
    if not go:
        raise RuntimeError('真实 HTTP 配对实验需要 Go 1.21 以上版本')
    binary = directory / ('windowlab.exe' if os.name == 'nt' else 'windowlab')
    subprocess.run([go, 'build', '-o', str(binary), './windowlab/cmd/windowlab'],
                   cwd=ROOT / 'mock', check=True, timeout=120, capture_output=True)
    return binary


def advance(origin: str, ticks: int) -> None:
    """仅在两次恢复调用之间，由测试监督器推进服务端时间。"""
    payload = json.dumps({'ticks': ticks}).encode('utf-8')
    request = urllib.request.Request(origin + '/v1/lab/advance', data=payload,
                                     headers={'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(request, timeout=2) as reply:
        response = json.load(reply)
    if response.get('server_tick') != ticks:
        raise RuntimeError('模拟服务端时间未准确推进')


def engine_call(method: str, work: Path, client: WindowClient, raw: dict) -> int:
    """每次恢复新建实际引擎对象，持久数据库不变。"""
    if method == 'b5':
        return DurableBaseline(work / 'b5.sqlite3', client).call(raw, BUSINESS_REF)[0]
    if method == 'raguard':
        # 已知本轮旧请求已结束；0 租约只用于实验中的恢复窗口。
        gateway = Gateway(Ledger(str(work / 'ledger.sqlite3')), client,
                          lease_seconds=0,
                          identities=BusinessIntentRegistry(str(work / 'identity.sqlite3')))
        return gateway.call(raw, identity=VerifiedIdentity(BUSINESS_REF))[0]
    raise ValueError('未知引擎')


@dataclass(frozen=True)
class Episode:
    method: str
    fault: str
    recovery_tick: int
    scope: str
    admission: str
    guarantee: str
    initial_status: int
    final_status: int
    blocked: int
    physical_effects: int
    duplicate_effects: int
    wrong_scope_effects: int
    false_confirmation: int
    unsafe_confirmation: int
    acknowledged_completion: int
    unresolved: int
    network_attempts: int
    readback_checks: int
    recovery_latency_ms: float


def run_case(method: str, fault: str, tick: int, scope: str, admission: str,
             binary: Path, *, guaranteed: bool = True) -> Episode:
    if (method not in METHODS or fault not in FAULTS or tick not in RECOVERY_TICKS
            or scope not in SCOPES or admission not in ADMISSION
            or type(guaranteed) is not bool):
        raise ValueError('实验条件非法')
    with window_server(binary) as origin, tempfile.TemporaryDirectory() as temp:
        work = Path(temp)
        raw = raw_call('orders.create')
        client = WindowClient(origin, drop_count=2, drop_point=fault, scope=ORIGIN_SCOPE)
        initial = engine_call(method, work, client, raw)
        advance(origin, tick)
        next_scope = ORIGIN_SCOPE if scope == 'same' else 'region-b'
        client.scope = next_scope
        client.drop_count = 0
        trust = VerifiedWindow(earliest_possible_accept_tick=0,
                               min_retention_ticks=TTL_TICKS if guaranteed else None,
                               original_scope=ORIGIN_SCOPE)
        blocked = admission == 'contract' and not trust.permits(tick=tick, scope=next_scope)
        start = time.perf_counter()
        final_status = 409 if blocked else engine_call(method, work, client, raw)
        elapsed = round((time.perf_counter() - start) * 1000, 3)
        # 物理效果历史只用于独立评分；运行中的策略不可调用此接口。
        with urllib.request.urlopen(origin + '/v1/lab/effects', timeout=2) as reply:
            truth = json.load(reply)
    effects = truth['effects']
    correct = sum(item['scope'] == ORIGIN_SCOPE for item in effects)
    wrong = len(effects) - correct
    duplicate = max(0, len(effects) - 1)
    unsafe = bool(duplicate or wrong)
    acknowledged = final_status == 200 and correct == 1 and not unsafe
    return Episode(method, fault, tick, scope, admission,
                   'known' if guaranteed else 'unknown', initial, final_status,
                   int(blocked), len(effects), duplicate, wrong,
                   int(final_status == 200 and correct == 0),
                   int(final_status == 200 and unsafe),
                   int(acknowledged), int(final_status != 200),
                   int(truth['attempts']), client.readback_checks, elapsed)


def matrix(binary: Path, *, quick: bool = False) -> list[Episode]:
    conditions = [(f, tick, scope, admission, True)
                  for f in FAULTS for tick in RECOVERY_TICKS
                  for scope in SCOPES for admission in ADMISSION]
    # 未知保证期必须由双方同等处理；仅抽取两组特别有意义的异常条件。
    conditions += [('after_commit', 2, 'same', 'contract', False),
                   ('before_commit', 6, 'same', 'contract', False)]
    if quick:
        conditions = [c for c in conditions if c[0] == 'after_commit'
                      and c[1] in (2, 6) and c[2] == 'same'][:4]
    return [run_case(method, *condition[:4], binary,
                     guaranteed=condition[4]) for condition in conditions for method in METHODS]


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description='真实 B5/Gateway 幂等窗口配对研究')
    parser.add_argument('--output', type=Path, default=Path('results/window_live'))
    parser.add_argument('--quick', action='store_true', help='只执行 CI 冒烟子集')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as folder:
        rows = matrix(build_binary(Path(folder)), quick=args.quick)
    args.output.mkdir(parents=True, exist_ok=True)
    write_csv(args.output / 'episodes.csv', [asdict(r) for r in rows])
    summary = []
    for method in METHODS:
        for policy in ADMISSION:
            selected = [r for r in rows if r.method == method and r.admission == policy]
            summary.append({'method': method, 'admission': policy,
                            'cases': len(selected), 'acknowledged': sum(r.acknowledged_completion for r in selected),
                            'duplicates': sum(r.duplicate_effects for r in selected),
                            'wrong_scope': sum(r.wrong_scope_effects for r in selected),
                            'unsafe_confirmation': sum(r.unsafe_confirmation for r in selected),
                            'unresolved': sum(r.unresolved for r in selected),
                            'network_attempts': sum(r.network_attempts for r in selected)})
    write_csv(args.output / 'summary.csv', summary)
    report = ['# 有限幂等窗口真实调用链配对实验', '',
              '独立本机 Go 服务 + 原版 B5 SQLite 账本 + 原版 RAGuard Gateway；无生产 API。',
              '契约门禁是实验监督器共享的可信信息，**不是 RAGuard 独有算法**。',
              '服务器时间使用可控 tick 而非真实小时；原始物理效果历史仅供评分。', '',
              '| 方法 | 契约准入 | 案例 | 安全确认 | 重复效果 | 错作用域 | 不安全确认 | 未完成 |',
              '| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for s in summary:
        report.append(f'| {s["method"]} | {s["admission"]} | {s["cases"]} | '
                      f'{s["acknowledged"]} | {s["duplicates"]} | {s["wrong_scope"]} | '
                      f'{s["unsafe_confirmation"]} | {s["unresolved"]} |')
    report.append('\n该矩阵由预设条件确定，不能当作真实厂商风险发生频率；B5 与 RAGuard 的能力差异必须独立证明。')
    (args.output / 'REPORT.md').write_text('\n'.join(report) + '\n', encoding='utf-8')
    print('\n'.join(report))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
