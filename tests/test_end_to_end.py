"""Python 网关与真实 Go HTTP 沙箱的跨语言集成测试。"""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import unittest
import urllib.error
import urllib.request

from raguard.gateway import Gateway
from raguard.identity import BusinessIntentRegistry, IdentitySigner
from raguard.http import create_server
from raguard.ledger import Ledger
from raguard.transport import SandboxClient
from tests.test_gateway import request
from tests.identity_helpers import trusted
from raguard.model import Call
from scripts.sandbox_binary import build_sandbox, sandbox_available

ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@contextmanager
def sandbox(binary: str, fault="", no_readback=False):
    port = free_port()
    process = subprocess.Popen([binary], env={**os.environ,
        "MOCK_ADDR": f"127.0.0.1:{port}", "MOCK_FAULT_ONCE": fault,
        "MOCK_DISABLE_READBACK": "1" if no_readback else "0"},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    origin = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                with urllib.request.urlopen(origin + "/healthz", timeout=0.2):
                    break
            except OSError:
                if process.poll() is not None:
                    raise RuntimeError("mock server exited during startup")
                time.sleep(0.03)
        else:
            raise RuntimeError("mock server readiness timeout")
        yield origin
    finally:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


def count(origin, name):
    with urllib.request.urlopen(origin + "/v1/counts", timeout=2) as response:
        return json.load(response).get(name, 0)


class EndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not sandbox_available(ROOT):
            raise unittest.SkipTest("需要 Go 或已校验的 Windows 沙箱程序")
        cls.binary_dir = tempfile.TemporaryDirectory()
        cls.binary = str(build_sandbox(Path(cls.binary_dir.name), ROOT))

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "binary_dir"):
            cls.binary_dir.cleanup()

    def gateway(self, origin):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return Gateway(Ledger(str(Path(tmp.name) / "ledger.sqlite3")), SandboxClient(origin),
                       identities=BusinessIntentRegistry(str(Path(tmp.name) / 'identities.sqlite3')))

    def test_order_response_lost_after_commit_does_not_duplicate(self):
        with sandbox(self.binary, "orders.create:after") as origin:
            gateway = self.gateway(origin)
            data = request()
            first = gateway.call(data, identity=trusted(data))
            second = gateway.call(data, identity=trusted(data))
            self.assertEqual((first[0], first[1]["action"]), (200, "replay"))
            self.assertEqual((second[0], second[1]["action"]), (200, "replay"))
            self.assertEqual(count(origin, "orders.create"), 1)

    def test_opaque_commit_without_receipt_blocks_retry(self):
        with sandbox(self.binary, "notifications.send:after") as origin:
            gateway = self.gateway(origin)
            msg = request("notifications.send")
            self.assertEqual(gateway.call(msg, identity=trusted(msg))[0], 409)
            self.assertEqual(gateway.call(msg, identity=trusted(msg))[0], 409)
            self.assertEqual(count(origin, "notifications.send"), 1)

    def test_idempotent_ack_can_confirm_when_readback_unavailable(self):
        with sandbox(self.binary, "orders.create:after", no_readback=True) as origin:
            gateway = self.gateway(origin)
            data = request()
            first = gateway.call(data, identity=trusted(data))
            self.assertEqual(first[0], 200)
            self.assertEqual(first[1]['evidence'], 'idempotent_ack')
            self.assertEqual(gateway.call(data, identity=trusted(data))[0], 200)
            self.assertEqual(count(origin, "orders.create"), 1)

    def test_before_commit_disconnect_safe_idempotent_retry(self):
        with sandbox(self.binary, "orders.create:before") as origin:
            gateway = self.gateway(origin)
            data = request()
            self.assertEqual(gateway.call(data, identity=trusted(data))[0], 200)
            self.assertEqual(count(origin, "orders.create"), 1)

    def test_http_credentials_and_end_to_end_dispatch(self):
        import threading
        with sandbox(self.binary) as origin:
            gateway = self.gateway(origin)
            secret = 'local-research-signature-secret-32bytes'
            server = create_server(gateway, port=0, api_key="token",
                                   approval_key="approval", identity_secret=secret)
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                endpoint = f"http://127.0.0.1:{server.server_port}"
                body = json.dumps(request()).encode()

                def post(path, headers):
                    req = urllib.request.Request(endpoint + path, body, headers=headers)
                    try:
                        with urllib.request.urlopen(req, timeout=3) as response:
                            return response.status
                    except urllib.error.HTTPError as exc:
                        return exc.code
                self.assertEqual(post("/v1/calls", {}), 401)
                self.assertEqual(post("/v1/calls", {"X-RAGuard-Key": "token"}), 403)
                sig = IdentitySigner(secret).sign('order-business-1', Call.parse(request()))
                self.assertEqual(post('/v1/calls', {
                    'X-RAGuard-Key': 'token',
                    'X-RAGuard-Business-Ref': 'order-business-1',
                    'X-RAGuard-Business-Signature': sig,
                }), 200)
                self.assertEqual(post("/v1/compensate", {"X-RAGuard-Key": "token"}), 403)
                self.assertEqual(count(origin, "orders.create"), 1)
            finally:
                server.shutdown()
                server.server_close()
                worker.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
