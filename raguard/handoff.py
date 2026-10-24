"""本地实验的旧执行者终止凭证；只能由可信监督器提供。"""

from dataclasses import dataclass

from .ledger import Record
from .model import Call


@dataclass(frozen=True)
class OwnerTermination:
    """绑定初始执行身份与代次；并非密码学凭证，不能接受 Agent 自报。"""

    tenant: str
    task: str
    intent: str
    fingerprint: str
    generation: int
    exit_code: int

    def __post_init__(self):
        if (not all((self.tenant, self.task, self.intent, self.fingerprint))
                or type(self.generation) is not int or self.generation < 1
                or type(self.exit_code) is not int or self.exit_code == 0):
            raise ValueError('终止凭证必须绑定已退出的执行者和有效代次')

    def covers(self, call: Call, record: Record) -> bool:
        return (record.state == 'in_flight'
                and self.generation == record.generation
                and self.tenant == call.tenant
                and self.task == call.task
                and self.intent == call.intent
                and self.fingerprint == call.fingerprint)
