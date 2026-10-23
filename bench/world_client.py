"""沙箱业务键绑定：只服务实验，保证所有对照共享同一业务目标。"""

from raguard.transport import SandboxClient


class WorkflowClient:
    def __init__(self, origin: str):
        self.origin = origin

    def _for(self, call):
        # step 来源于固定实验工作流，不读取或相信 Agent 的自由文本。
        return SandboxClient(self.origin, research_business_key=f'workflow:{call.step}')

    def execute(self, call, policy):
        return self._for(call).execute(call, policy)

    def readback(self, call, policy):
        return self._for(call).readback(call, policy)

    def compensate(self, call, result):
        return self._for(call).compensate(call, result)
