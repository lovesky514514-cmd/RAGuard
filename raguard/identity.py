"""可信业务引用：完成签名校验，并将同一业务动作绑定到首次意图。"""

from contextlib import closing
from dataclasses import dataclass, replace
from hashlib import sha256
import hmac
from pathlib import Path
import sqlite3

from .model import Call, SAFE_ID, canonical


class IdentityConflict(ValueError):
    """业务引用已登记，但请求的业务语义不一致。"""


@dataclass(frozen=True)
class VerifiedIdentity:
    """只有受信任的调用层才能创建并传给网关的业务引用。"""

    business_ref: str


@dataclass(frozen=True)
class IntentBinding:
    """业务引用首次登记的稳定逻辑意图。"""

    business_ref: str
    tenant: str
    tool: str
    principal: str
    task: str
    step: str
    intent: str
    semantic_digest: str

    def canonical_call(self, call: Call) -> Call:
        """将重命名的 Agent 步骤归一到首次执行的动作。"""
        return replace(call, task=self.task, step=self.step, intent=self.intent)


class IdentitySigner:
    """仅供可信编排器签名；网关使用 verify 验签，不暴露签名接口。"""

    def __init__(self, secret: str):
        if not isinstance(secret, str) or len(secret.encode('utf-8')) < 32:
            raise ValueError('业务签名密钥至少需要 32 字节')
        self._key = secret.encode('utf-8')

    @staticmethod
    def _message(business_ref: str, call: Call) -> bytes:
        if not isinstance(business_ref, str) or not SAFE_ID.fullmatch(business_ref):
            raise ValueError('业务引用必须是 1-80 位安全标识')
        # step、intent、task 不参与签名；允许 Agent 改名后仍识别同一业务动作。
        # 可信编排器负责保证 business_ref 对应一个真实业务动作，而非自由文本。
        return canonical({
            'version': 1, 'business_ref': business_ref,
            'tenant': call.tenant, 'principal': call.principal,
            'tool': call.tool, 'args': call.args,
            'dependencies': call.dependencies,
        }).encode('utf-8')

    def sign(self, business_ref: str, call: Call) -> str:
        """可信编排器签发；仅用于演示与受信任的业务服务。"""
        return hmac.new(self._key, self._message(business_ref, call), 'sha256').hexdigest()

    def verify(self, business_ref: str, signature: str, call: Call) -> VerifiedIdentity:
        """拒绝伪造、篡改或缺失的引用；使用恒定时间比较签名。"""
        if not isinstance(signature, str) or len(signature) != 64:
            raise IdentityConflict('业务签名缺失或格式错误')
        try:
            expected = self.sign(business_ref, call)
        except ValueError as exc:
            raise IdentityConflict(str(exc)) from exc
        if not hmac.compare_digest(expected, signature):
            raise IdentityConflict('业务引用签名校验失败')
        return VerifiedIdentity(business_ref)


class BusinessIntentRegistry:
    """持久化业务引用绑定；对相同引用的并发登记采用 SQLite 原子事务。"""

    def __init__(self, filename: str):
        self.filename = str(filename)
        Path(filename).parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db, db:
            db.execute('''CREATE TABLE IF NOT EXISTS business_intents (
                tenant TEXT NOT NULL,
                tool TEXT NOT NULL,
                business_ref TEXT NOT NULL,
                principal TEXT NOT NULL,
                task TEXT NOT NULL,
                step TEXT NOT NULL,
                intent TEXT NOT NULL,
                semantic_digest TEXT NOT NULL,
                PRIMARY KEY (tenant, tool, business_ref)
            )''')

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.filename, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA busy_timeout=10000')
        return db

    @staticmethod
    def _digest(call: Call) -> str:
        # 语义校验必须包含受益人、金额或其他业务参数，避免错误合并。
        return sha256(canonical({
            'principal': call.principal,
            'args': call.args,
            'dependencies': call.dependencies,
        }).encode('utf-8')).hexdigest()

    def _resolve(self, identity: VerifiedIdentity, call: Call,
                 *, allow_new: bool) -> IntentBinding:
        if not isinstance(identity, VerifiedIdentity):
            raise IdentityConflict('写操作需要可信业务引用')
        business_ref = identity.business_ref
        if not isinstance(business_ref, str) or not SAFE_ID.fullmatch(business_ref):
            raise IdentityConflict('业务引用格式不合法')
        digest = self._digest(call)
        with closing(self._connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute(
                'SELECT * FROM business_intents WHERE tenant=? AND tool=? AND business_ref=?',
                (call.tenant, call.tool, business_ref),
            ).fetchone()
            if row is not None:
                binding = IntentBinding(**dict(row))
                if binding.semantic_digest != digest:
                    raise IdentityConflict('相同业务引用的主体、参数或依赖发生改变')
                return binding
            if not allow_new:
                raise IdentityConflict('该业务引用尚无可补偿的已登记动作')
            binding = IntentBinding(
                business_ref, call.tenant, call.tool, call.principal,
                call.task, call.step, call.intent, digest,
            )
            db.execute('''INSERT INTO business_intents
                (business_ref,tenant,tool,principal,task,step,intent,semantic_digest)
                VALUES (?,?,?,?,?,?,?,?)''',
                (binding.business_ref, binding.tenant, binding.tool,
                 binding.principal, binding.task, binding.step,
                 binding.intent, binding.semantic_digest))
            return binding

    def bind(self, business_ref: str, call: Call) -> IntentBinding:
        """兼容可信内部调用；HTTP 等非可信入口必须先验签。"""
        return self._resolve(VerifiedIdentity(business_ref), call, allow_new=True)

    def resolve(self, identity: VerifiedIdentity, call: Call,
                *, allow_new: bool = True) -> Call:
        """将业务引用解析成账本唯一使用的规范化调用。"""
        return self._resolve(identity, call, allow_new=allow_new).canonical_call(call)
