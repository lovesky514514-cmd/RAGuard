"""可信本地监督器：持有原 Python 子进程句柄才允许提前释放对应执行代次。

不接受 HTTP 请求、Agent JSON 或从命令行自报的死亡凭证。
这只证明本地直接子进程已退出，不覆盖分布式租约或恶意宿主机。
"""

from dataclasses import dataclass
from subprocess import Popen

from .ledger import Ledger
from .model import Call
from .policy import Effect, ToolPolicy


@dataclass(frozen=True)
class ObservedOwner:
    """由可信父监督器将其启动的 Popen 与原始动作、执行代次绑定。"""

    process: Popen
    call: Call
    generation: int

    def __post_init__(self):
        if not isinstance(self.process, Popen) or not isinstance(self.call, Call):
            raise TypeError('必须跟踪本地 Python 子进程对象和已解析业务请求')
        if type(self.generation) is not int or self.generation < 1:
            raise ValueError('执行代次必须为正整数')

    def release(self, ledger: Ledger, policy: ToolPolicy) -> bool:
        """旧子进程退出 + 原生服务端幂等 + 精确账本代次，三者缺一不可。"""
        if self.process.poll() is None:
            return False
        if not (policy.effect == Effect.IDEMPOTENT
                and policy.authoritative_receipt and policy.retryable):
            return False
        return ledger.release_terminated(self.call, self.generation)
