"""跨步骤恢复契约实验：验证依赖与补偿边界，不声称真实 LLM 泛化。"""

from dataclasses import asdict, dataclass
from pathlib import Path
import json
import tempfile
import time
import urllib.request

from bench.oracle import EffectEvent, evaluate
from bench.runner import build_sandbox, local_sandbox
from bench.saga import SagaExecutor, SagaJournal
from bench.world_client import WorkflowClient
from raguard.model import Call
from raguard.gateway import Gateway
from raguard.identity import BusinessIntentRegistry, VerifiedIdentity
from raguard.ledger import Ledger
from raguard.transport import SandboxClient


@dataclass(frozen=True)
class Step:
    name: str
    tool: str
    args: dict
    dependencies: tuple[str, ...] = ()


@dataclass(frozen=True)
class WorkflowResult:
    workflow: str
    method: str
    fault: str
    status: int
    acknowledged_success: int
    physical_goal: int
    unsafe: int
    compensation_allowed: int
    effects: int
    latency_ms: float


# 严格指定业务步骤，不依赖模型臆测“已经执行成功”。
WORKFLOWS: dict[str, tuple[Step, ...]] = {
    'ticket_notice': (
        Step('inventory', 'inventory.read', {'sku': 'S-1'}),
        Step('ticket', 'tickets.create', {'message': '维修请求'}, ('inventory',)),
        Step('notify', 'notifications.send', {'message': '通知操作员'}, ('ticket',)),
    ),
    'order_notice': (
        Step('inventory', 'inventory.read', {'sku': 'S-1'}),
        Step('order', 'orders.create', {'sku': 'S-1', 'quantity': 1}, ('inventory',)),
        Step('notify', 'notifications.send', {'message': '通知订单'}, ('order',)),
    ),
}


def business_key(step: Step) -> str:
    return f'workflow:{step.name}'


def prepare_call(step: Step) -> dict:
    return dict(tenant='lab', principal='test-user', task='workflow',
                step=step.name, intent=step.name, tool=step.tool,
                args=step.args, dependencies=list(step.dependencies))


def run_workflow(name: str, origin: str, *, check_compensation=False,
                 method: str = 'raguard',
                 abort_after: str | None = None) -> WorkflowResult:
    """首个不可确认步骤会中止整个工作流，不能把已完成步骤倒推为安全。"""
    steps = WORKFLOWS[name]
    if method not in {'b4', 'raguard'}:
        raise ValueError('不支持的工作流比较方法')
    if abort_after is not None and abort_after not in {step.name for step in steps} or (abort_after == 'inventory'):
        raise ValueError('终止点必须是完成写入后的步骤')
    with tempfile.TemporaryDirectory() as work:
        client = WorkflowClient(origin)
        start = time.perf_counter()
        compensation_allowed = 0
        limited_steps = []
        for step in steps:
            limited_steps.append(step)
            if step.name == abort_after:
                break
        planner_aborted = abort_after is not None
        if method == 'b4':
            saga = SagaExecutor(SagaJournal(str(Path(work) / 'saga.sqlite3')), client)
            calls = [Call.parse(prepare_call(step)) for step in limited_steps]
            result = saga.run(calls)
            last_status = 200 if result.state == 'confirmed' else 409
            last_completed = result.state == 'confirmed' and not planner_aborted
            if result.state == 'confirmed' and planner_aborted:
                compensated = saga.abort(calls)
                compensation_allowed = int(compensated.state == 'compensated')
                last_status = 409
        else:
            gateway = Gateway(Ledger(str(Path(work) / 'ledger.db')), client,
                              identities=BusinessIntentRegistry(str(Path(work) / 'identity.db')))
            last_status = 200
            last_completed = False
            for step in limited_steps:
                call = prepare_call(step)
                last_status, _ = gateway.call(call, identity=VerifiedIdentity('workflow-'+step.name))
                if last_status != 200:
                    break
            else:
                last_completed = not planner_aborted
            if planner_aborted and last_status == 200:
                # 明确未执行任何下游调用，才能从最后一个可补偿步骤撤销。
                last = limited_steps[-1]
                status, _ = gateway.compensate(prepare_call(last), approved=True,
                                               identity=VerifiedIdentity('workflow-'+last.name))
                compensation_allowed = int(status == 200)
                last_status = 409
            elif check_compensation and name == 'ticket_notice' and not last_completed:
                ticket_call = prepare_call(steps[1])
                status, _ = gateway.compensate(ticket_call, approved=True,
                                               identity=VerifiedIdentity('workflow-ticket'))
                compensation_allowed = int(status == 200)
        elapsed = (time.perf_counter() - start) * 1000
    with urllib.request.urlopen(origin+'/v1/lab/effects', timeout=2) as response:
        world = json.load(response)
    actual = [EffectEvent(row['kind'], row['effect_id'], row['business_key'])
              for row in world['events']]
    expected = {business_key(step): 1 for step in steps if step.tool != 'inventory.read'}
    outcome = evaluate(actual, expected)
    return WorkflowResult(name, method, 'configured_by_sandbox', last_status,
                          int(last_completed and outcome.task_success),
                          int(outcome.task_success),
                          int(outcome.duplicate_executions > 0 or outcome.unintended_executions > 0),
                          compensation_allowed,
                          sum(1 for event in actual if event.kind == 'commit'),
                          round(elapsed, 3))


def main():
    """独立工作流冒烟实验，输出两种不同故障位置的实际真值。"""
    output = Path('results/workflows.csv')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as build:
        binary = build_sandbox(Path(build))
        records = []
        for workflow, fault in (
                ('ticket_notice', 'notifications.send:after_disconnect'),
                ('ticket_notice', 'notifications.send:before_disconnect'),
                ('order_notice', 'orders.create:after_disconnect'),
                ('order_notice', 'notifications.send:after_disconnect')):
            for method in ('b4', 'raguard'):
                with local_sandbox(binary, fault, False) as origin:
                    result = run_workflow(workflow, origin, check_compensation=True, method=method)
                    records.append({**asdict(result), 'fault': fault})
    from bench.analyze import write_csv
    write_csv(output, records)
    print(f'已写入 {output}，共 {len(records)} 条工作流试验。')


if __name__ == '__main__':
    main()
