"""仅本机 HTTP 入口；写操作须通过独立可信业务签名校验。"""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import os

from .gateway import Gateway
from .identity import BusinessIntentRegistry, IdentityConflict, IdentitySigner
from .ledger import Ledger
from .model import BadCall, Call
from .transport import SandboxClient


class Handler(BaseHTTPRequestHandler):
    gateway: Gateway
    api_key: str
    approval_key: str
    signer: IdentitySigner | None

    def log_message(self, fmt, *args):
        # 日志禁止记录请求体、密钥或可识别的业务编号。
        return

    def send_json(self, status: int, value: dict) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionResetError, BrokenPipeError):
            # 客户端断开可能意味着提交结果未知，不能据此判断操作失败。
            pass

    def do_GET(self):
        if self.path == "/healthz":
            return self.send_json(200, {"status": "ok"})
        return self.send_json(404, {"error": "not_found"})

    def do_POST(self):
        if self.path not in {"/v1/calls", "/v1/compensate"}:
            return self.send_json(404, {"error": "not_found"})
        if self.api_key and not hmac.compare_digest(
                self.headers.get("X-RAGuard-Key", ""), self.api_key):
            return self.send_json(401, {"error": "unauthorized"})
        if self.path == "/v1/compensate" and (
                not self.approval_key or not hmac.compare_digest(
                    self.headers.get("X-RAGuard-Approval", ""), self.approval_key)):
            return self.send_json(403, {"error": "approval_required"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 8192:
                return self.send_json(413, {"error": "body_size_limit"})
            raw = json.loads(self.rfile.read(length))
        except (TypeError, ValueError, UnicodeError):
            return self.send_json(400, {"error": "invalid_json"})
        try:
            # 签名只由可信编排器生成，Agent 的 JSON 不能直接自称业务引用。
            identity = None
            business_ref = self.headers.get('X-RAGuard-Business-Ref', '')
            signature = self.headers.get('X-RAGuard-Business-Signature', '')
            if business_ref or signature:
                if self.signer is None:
                    return self.send_json(403, {'error': 'identity_unconfigured'})
                try:
                    identity = self.signer.verify(business_ref, signature, Call.parse(raw))
                except (BadCall, IdentityConflict):
                    return self.send_json(403, {'error': 'invalid_identity_signature'})
            if self.path == "/v1/calls":
                status, response = self.gateway.call(raw, identity=identity)
            else:
                status, response = self.gateway.compensate(raw, approved=True,
                                                           identity=identity)
        except Exception as exc:
            # 只记录异常类型，不输出请求参数和业务敏感信息。
            print("gateway error:", type(exc).__name__, flush=True)
            return self.send_json(500, {"error": "internal_error"})
        return self.send_json(status, response)


def create_server(gateway: Gateway, *, host: str = "127.0.0.1",
                  port: int = 8088, api_key: str = "",
                  approval_key: str = "",
                  identity_secret: str = "") -> ThreadingHTTPServer:
    if host != "127.0.0.1":
        raise ValueError("研究原型只能监听 127.0.0.1")
    signer = IdentitySigner(identity_secret) if identity_secret else None
    handler = type("LocalHandler", (Handler,), {
        "gateway": gateway, "api_key": api_key, "approval_key": approval_key,
        "signer": signer,
    })
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server


def main():
    db = os.environ.get("RAGUARD_DB", "data/ledger.sqlite3")
    origin = os.environ.get("RAGUARD_UPSTREAM", "http://127.0.0.1:8181")
    registry = os.environ.get('RAGUARD_IDENTITY_DB', 'data/identities.sqlite3')
    gateway = Gateway(Ledger(db), SandboxClient(origin),
                      identities=BusinessIntentRegistry(registry))
    secret = os.environ.get('RAGUARD_IDENTITY_SECRET', '')
    if not secret and os.environ.get('RAGUARD_LOCAL_DEMO') == '1':
        from pathlib import Path
        secret = Path('data/local_demo_identity.key').read_text(encoding='ascii').strip()
    with create_server(gateway, port=int(os.environ.get("RAGUARD_PORT", "8088")),
                       api_key=os.environ.get("RAGUARD_API_KEY", ""),
                       approval_key=os.environ.get("RAGUARD_APPROVAL_KEY", ""),
                       identity_secret=secret) as server:
        print(f"RAGuard: http://127.0.0.1:{server.server_port}", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
