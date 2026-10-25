"""SQLite 副作用账本：原子占位、状态持久化及代次隔离。"""

from contextlib import contextmanager
from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
import time

from .model import Call, canonical

SCHEMA = """
CREATE TABLE IF NOT EXISTS effects (
  tenant TEXT NOT NULL,
  task TEXT NOT NULL,
  intent TEXT NOT NULL,
  step TEXT NOT NULL,
  fingerprint TEXT NOT NULL,
  tool TEXT NOT NULL,
  dependencies TEXT NOT NULL,
  state TEXT NOT NULL,
  generation INTEGER NOT NULL,
  updated REAL NOT NULL,
  result TEXT,
  evidence TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (tenant, task, intent),
  UNIQUE (tenant, task, step)
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  at REAL NOT NULL,
  tenant TEXT NOT NULL,
  task TEXT NOT NULL,
  intent TEXT NOT NULL,
  event TEXT NOT NULL,
  evidence TEXT NOT NULL
);
"""


class Conflict(ValueError):
    """请求与已登记的逻辑动作不一致。"""


@dataclass(frozen=True)
class Record:
    state: str
    generation: int
    updated: float
    result: dict | None
    evidence: str


class Ledger:
    """集中管理原子执行权与补偿权，不在账本中发起网络请求。"""

    def __init__(self, filename: str):
        self.filename = filename
        Path(filename).parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.filename, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=10000")
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _event(db, call: Call, event: str, evidence: str = ""):
        # 事件日志不能保存业务参数、响应体或授权密钥。
        db.execute("INSERT INTO events(at,tenant,task,intent,event,evidence) VALUES(?,?,?,?,?,?)",
                   (time.time(), call.tenant, call.task, call.intent, event, evidence))

    @staticmethod
    def _record(row) -> Record:
        return Record(row["state"], row["generation"], row["updated"],
                      json.loads(row["result"]) if row["result"] else None,
                      row["evidence"])

    def claim(self, call: Call) -> tuple[Record, bool]:
        """只有首次请求获得执行权，后续请求复用同一账本记录。"""
        with self._db() as db:
            row = db.execute("SELECT * FROM effects WHERE tenant=? AND task=? AND intent=?",
                             (call.tenant, call.task, call.intent)).fetchone()
            if row:
                if row["fingerprint"] != call.fingerprint:
                    raise Conflict("相同意图的主体、参数、步骤或依赖已改变")
                return self._record(row), False
            other = db.execute("SELECT 1 FROM effects WHERE tenant=? AND task=? AND step=?",
                               (call.tenant, call.task, call.step)).fetchone()
            if other:
                raise Conflict("step 已绑定其他意图")
            for dependency in call.dependencies:
                prior = db.execute("SELECT state FROM effects WHERE tenant=? AND task=? AND step=?",
                                   (call.tenant, call.task, dependency)).fetchone()
                if not prior or prior["state"] != "confirmed":
                    raise Conflict(f"依赖未确认：{dependency}")
            now = time.time()
            db.execute("""INSERT INTO effects
                (tenant,task,intent,step,fingerprint,tool,dependencies,state,generation,updated)
                VALUES(?,?,?,?,?,?,?,'in_flight',1,?)""",
                (call.tenant, call.task, call.intent, call.step,
                 call.fingerprint, call.tool, canonical(call.dependencies), now))
            self._event(db, call, "dispatch", "new")
            return Record("in_flight", 1, now, None, ""), True

    def record(self, call: Call) -> Record | None:
        with self._db() as db:
            row = db.execute("SELECT * FROM effects WHERE tenant=? AND task=? AND intent=?",
                             (call.tenant, call.task, call.intent)).fetchone()
            if row and row["fingerprint"] != call.fingerprint:
                raise Conflict("意图语义不一致")
            return self._record(row) if row else None

    def complete(self, call: Call, generation: int, state: str,
                 result: dict | None, evidence: str) -> bool:
        """仅当前代次可提交结果；迟到执行者不可覆盖新代次记录。"""
        if state not in {"confirmed", "unknown"}:
            raise ValueError("invalid transition")
        with self._db() as db:
            payload = canonical(result) if result is not None else None
            count = db.execute("""UPDATE effects SET state=?,result=?,evidence=?,updated=?
                WHERE tenant=? AND task=? AND intent=? AND fingerprint=?
                AND generation=? AND state='in_flight'""",
                (state, payload, evidence, time.time(), call.tenant, call.task,
                 call.intent, call.fingerprint, generation)).rowcount
            if count:
                self._event(db, call, state, evidence)
            return bool(count)

    def reconcile(self, call: Call, verified_result: dict) -> bool:
        """正向业务证据可消除未知状态，无须再次执行工具。"""
        with self._db() as db:
            count = db.execute("""UPDATE effects SET state='confirmed',result=?,evidence=?,updated=?
                WHERE tenant=? AND task=? AND intent=? AND fingerprint=?
                AND state IN ('unknown','in_flight','confirmed')""",
                (canonical(verified_result), "positive_readback", time.time(),
                 call.tenant, call.task, call.intent, call.fingerprint)).rowcount
            if count:
                self._event(db, call, "reconcile", "positive_readback")
            return bool(count)

    def retry_claim(self, call: Call, generation: int, stale_after: float) -> int | None:
        """仅在租约到期或提交未知时，以 CAS 方式获取新的重试执行权。"""
        with self._db() as db:
            row = db.execute(
                "SELECT state,generation,updated FROM effects WHERE tenant=? AND task=? AND intent=?",
                (call.tenant, call.task, call.intent),
            ).fetchone()
            if not row or row["generation"] != generation:
                return None
            if row["state"] == "in_flight" and time.time() - row["updated"] < stale_after:
                return None
            if row["state"] not in {"in_flight", "unknown"}:
                return None
            count = db.execute("""UPDATE effects SET generation=generation+1,
                state='in_flight',updated=?,evidence=''
                WHERE tenant=? AND task=? AND intent=? AND generation=?
                AND state IN ('in_flight','unknown')""",
                (time.time(), call.tenant, call.task, call.intent, generation)).rowcount
            if not count:
                return None
            self._event(db, call, "retry_claim", "verified_safe_operation")
            return generation + 1

    def release_terminated(self, call: Call, generation: int) -> bool:
        """本地监督器确认旧进程终止后，以 CAS 只释放相同业务、相同代次。

        不授权外部副作用；是否可重新派发仍由 Gateway 的工具策略判断。
        """
        if type(generation) is not int or generation < 1:
            raise ValueError('非法执行代次')
        with self._db() as db:
            changed = db.execute("""UPDATE effects SET state='unknown',updated=?,
                evidence='observed_owner_exit'
                WHERE tenant=? AND task=? AND intent=? AND fingerprint=?
                AND generation=? AND state='in_flight'""",
                (time.time(), call.tenant, call.task, call.intent,
                 call.fingerprint, generation)).rowcount
            if changed:
                self._event(db, call, 'owner_exit', 'observed_owner_exit')
            return bool(changed)

    def mark_stale(self, call: Call, generation: int) -> None:
        """危险操作租约过期后只标记未知，绝不自动授权重试。"""
        with self._db() as db:
            changed = db.execute("""UPDATE effects SET state='unknown',updated=?,evidence='stale_owner'
                WHERE tenant=? AND task=? AND intent=? AND generation=? AND state='in_flight'""",
                (time.time(), call.tenant, call.task, call.intent, generation)).rowcount
            if changed:
                self._event(db, call, "unknown", "stale_owner")

    def claim_compensation(self, call: Call) -> tuple[int, dict] | None:
        """补偿只能认领一次；存在已执行下游时禁止单独补偿上游。"""
        with self._db() as db:
            row = db.execute("SELECT * FROM effects WHERE tenant=? AND task=? AND intent=?",
                             (call.tenant, call.task, call.intent)).fetchone()
            if not row or row["fingerprint"] != call.fingerprint or row["state"] != "confirmed":
                return None
            dependents = db.execute("""SELECT dependencies FROM effects
                WHERE tenant=? AND task=?
                AND state IN ('in_flight','confirmed','unknown','compensating','compensation_unknown')""",
                (call.tenant, call.task)).fetchall()
            if any(call.step in json.loads(d["dependencies"]) for d in dependents):
                raise Conflict("下游步骤可能已执行，不能单独补偿上游")
            generation = row["generation"] + 1
            db.execute("""UPDATE effects SET state='compensating',generation=?,updated=?
                WHERE tenant=? AND task=? AND intent=?""",
                (generation, time.time(), call.tenant, call.task, call.intent))
            self._event(db, call, "compensating", "approved")
            return generation, json.loads(row["result"])

    def finish_compensation(self, call: Call, generation: int, confirmed: bool) -> None:
        state = "compensated" if confirmed else "compensation_unknown"
        with self._db() as db:
            count = db.execute("""UPDATE effects SET state=?,updated=?,evidence=?
                WHERE tenant=? AND task=? AND intent=? AND generation=? AND state='compensating'""",
                (state, time.time(), "compensation_ack" if confirmed else "compensation_uncertain",
                 call.tenant, call.task, call.intent, generation)).rowcount
            if count:
                self._event(db, call, state)

    def events(self) -> list[dict]:
        with self._db() as db:
            return [dict(row) for row in db.execute("SELECT at,tenant,task,intent,event,evidence FROM events ORDER BY id")]
