"""重复调用同一业务引用的本地订单演示。"""

import json
import os
import urllib.error
import urllib.request
from uuid import uuid4

from .identity import IdentitySigner
from .model import Call


def request(tool: str, step: str, args: dict, dependencies=()) -> dict:
    return {"tenant": "demo", "principal": "researcher",
            "task": os.environ["RAGUARD_DEMO_TASK"],
            "step": step, "intent": step, "tool": tool,
            "args": args, "dependencies": list(dependencies)}


def send(value: dict) -> dict:
    data = json.dumps(value).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if os.environ.get("RAGUARD_API_KEY"):
        headers["X-RAGuard-Key"] = os.environ["RAGUARD_API_KEY"]
    if value['tool'] != 'inventory.read':
        # 演示中由本机可信编排器签名；实际部署不得把密钥暴露给 Agent。
        secret = os.environ.get('RAGUARD_IDENTITY_SECRET')
        if not secret:
            # 本地沙箱演示可读取临时密钥；真实 Agent 不得访问该文件。
            from pathlib import Path
            secret = Path('data/local_demo_identity.key').read_text(encoding='ascii').strip()
        call = Call.parse(value)
        reference = os.environ['RAGUARD_DEMO_TASK'] + '-order'
        headers['X-RAGuard-Business-Ref'] = reference
        headers['X-RAGuard-Business-Signature'] = IdentitySigner(secret).sign(reference, call)
    req = urllib.request.Request("http://127.0.0.1:8088/v1/calls", data,
                                 headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        return json.load(exc)


def main():
    # 固定 RAGUARD_DEMO_TASK 后可重复调用同一业务动作，便于检查去重。
    os.environ.setdefault("RAGUARD_DEMO_TASK", "demo-" + uuid4().hex[:10])
    inventory = request("inventory.read", "inventory", {"sku": "sku-21"})
    order = request("orders.create", "order", {"sku": "sku-21", "quantity": 1}, ["inventory"])
    for name, value in (("inventory", inventory), ("order", order), ("repeated order", order)):
        print(name, json.dumps(send(value), ensure_ascii=False))


if __name__ == "__main__":
    main()
