"""基于真实 HTTP 沙箱执行配对试验；所有基线使用相同网络适配器。"""

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import json
import os
import socket
import subprocess
import tempfile
import time
import urllib.request

from bench.oracle import EffectEvent, evaluate
from raguard.gateway import Gateway
from raguard.identity import BusinessIntentRegistry, VerifiedIdentity
from raguard.ledger import Ledger
from raguard.model import Call
from raguard.policy import TOOLS
from raguard.transport import SandboxClient, UncertainResult
from scripts.sandbox_binary import build_sandbox

ROOT = Path(__file__).resolve().parents[1]
METHODS = ('b0', 'b1', 'b2', 'b3', 'raguard')
BUSINESS_KEY = 'lab:target-operation'
TOOL_IDS = ('inventory.read', 'orders.create', 'tickets.create', 'notifications.send')
FAULTS = ('before_disconnect', 'before_429', 'before_503',
          'after_disconnect', 'after_invalid_json', 'after_tamper')
RATES = (0.0, 0.01, 0.05, 0.10, 0.20)


@dataclass(frozen=True)
class Trial:
    method: str
    tool: str
    fault: str
    rate: float
    repeat: int
    seed: int
    no_readback: bool = False


@dataclass(frozen=True)
class Result:
    method: str
    tool: str
    fault: str
    rate: float
    repeat: int
    seed: int
    fault_selected: int
    fault_fired: int
    status: int
    action: str
    acknowledged_success: int
    physical_goal: int
    duplicate_effects: int
    unsafe: int
    escalated: int
    attempts: int
    readbacks: int
    extra_calls: int
    latency_ms: float
    no_readback: int


def raw_call(tool: str) -> dict:
    """固定业务目标和参数；不会让语言模型随机改变试验对象。"""
    if tool == 'inventory.read':
        args = {'sku': 'S-1'}
    elif tool == 'orders.create':
        args = {'sku': 'S-1', 'quantity': 1}
    else:
        args = {'message': '研究沙箱测试'}
    return dict(tenant='lab', principal='test-user', task='episode',
                step='target', intent='target', tool=tool, args=args,
                dependencies=[])


def selected_fault(trial: Trial) -> bool:
    """同一故障网格和样本编号，对所有方法产生相同的故障选择。"""
    import hashlib
    payload = f'{trial.seed}:{trial.tool}:{trial.fault}:{trial.rate}:{trial.repeat}'
    score = int.from_bytes(hashlib.sha256(payload.encode()).digest()[:8], 'big') / 2**64
    return score < trial.rate


def execute_baseline(method: str, client: SandboxClient, raw: dict) -> tuple[int, str]:
    """五个公开定义的恢复基线；不访问真实效果判定器。"""
    if method not in METHODS[:-1]:
        raise ValueError('未知基线')
    policy = TOOLS[raw['tool']]
    original = Call.parse(raw)
    if method == 'b0':
        limit = 1
    elif method == 'b2':
        limit = 3
    else:
        limit = 2
    if method == 'b3' and not policy.retryable:
        limit = 1

    for attempt in range(limit):
        # B1/B2 模拟缺少幂等约束的重新规划；新意图会生成新服务端键。
        if method in {'b1', 'b2'} and attempt:
            changed = {**raw, 'step': f'target-{attempt}',
                       'intent': f'target-{attempt}'}
            call = Call.parse(changed)
        else:
            call = original
        try:
            client.execute(call, policy)
            return 200, 'execute' if attempt == 0 else 'retry'
        except UncertainResult:
            # 强基线同时使用稳定键和正向回读，绝不故意弱化。
            if method == 'b3' and policy.readback:
                evidence = client.readback(original, policy)
                if evidence is not None:
                    return 200, 'reconcile'
            if method == 'b2' and attempt + 1 < limit:
                time.sleep(0.001 * (2 ** attempt))
    return 409, 'stop'


@contextmanager
def local_sandbox(binary: Path, fault: str, no_readback: bool,
                  evidence_mode: str = "normal", evidence_delay_reads: int = 0):
    """每组试验启用全新 Go 进程，避免上一组世界状态污染。"""
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    origin = f'http://127.0.0.1:{port}'
    proc = subprocess.Popen([str(binary)], env={**os.environ,
        'MOCK_ADDR': f'127.0.0.1:{port}', 'MOCK_FAULT_ONCE': fault,
        'MOCK_DISABLE_READBACK': '1' if no_readback else '0',
        'MOCK_EVIDENCE_MODE': evidence_mode,
        'MOCK_EVIDENCE_DELAY_READS': str(evidence_delay_reads)},
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        for _ in range(100):
            try:
                with urllib.request.urlopen(origin + '/healthz', timeout=0.1):
                    break
            except OSError:
                if proc.poll() is not None:
                    raise RuntimeError('Go 沙箱启动失败')
                time.sleep(0.01)
        else:
            raise TimeoutError('Go 沙箱启动超时')
        yield origin
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)
        if proc.stderr:
            proc.stderr.close()


def trial_result(trial: Trial, binary: Path) -> Result:
    """结果只使用独立沙箱的效果真值，不能通过 HTTP 状态码替代。"""
    if trial.method not in METHODS or trial.tool not in TOOL_IDS or trial.fault not in FAULTS:
        raise ValueError('方法、工具或故障未注册')
    if not 0 <= trial.rate <= 1 or trial.repeat < 0:
        raise ValueError('故障概率或重复编号非法')
    enabled = selected_fault(trial)
    fault = f'{trial.tool}:{trial.fault}' if enabled else ''
    with local_sandbox(binary, fault, trial.no_readback) as origin:
        client = SandboxClient(origin, timeout=0.6, research_business_key=BUSINESS_KEY)
        raw = raw_call(trial.tool)
        start = time.perf_counter()
        with tempfile.TemporaryDirectory() as work:
            if trial.method == 'raguard':
                gateway = Gateway(Ledger(str(Path(work) / 'effects.sqlite3')), client,
                                  identities=BusinessIntentRegistry(str(Path(work) / 'identities.sqlite3')))
                status, payload = gateway.call(raw, identity=VerifiedIdentity('experiment-business-1'))
                action = payload['action']
            else:
                status, action = execute_baseline(trial.method, client, raw)
        elapsed = (time.perf_counter() - start) * 1000
        with urllib.request.urlopen(origin + '/v1/lab/effects', timeout=2) as response:
            world = json.load(response)
        events = [EffectEvent(x['kind'], x['effect_id'], x['business_key'])
                  for x in world['events']]
        expected = {} if trial.tool == 'inventory.read' else {BUSINESS_KEY: 1}
        outcome = evaluate(events, expected)
        attempts = int(world['attempts'])
        physical_goal = outcome.task_success
        return Result(method=trial.method, tool=trial.tool, fault=trial.fault,
                      rate=trial.rate, repeat=trial.repeat, seed=trial.seed,
                      fault_selected=int(enabled), fault_fired=int(world['fault_fired']),
                      status=status, action=action,
                      acknowledged_success=int(status == 200 and physical_goal),
                      physical_goal=int(physical_goal),
                      duplicate_effects=outcome.duplicate_executions,
                      unsafe=int(outcome.duplicate_executions > 0 or outcome.unintended_executions > 0),
                      escalated=int(status != 200), attempts=attempts,
                      readbacks=int(world['readbacks']), extra_calls=max(0, attempts-1),
                      latency_ms=round(elapsed, 3), no_readback=int(trial.no_readback))

