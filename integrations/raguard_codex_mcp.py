"""RAGuard 的本机 MCP 研究插件：只允许在隔离 Go 沙箱中测试恢复建议。"""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile

# 支持 Codex 以完整脚本路径从任意工作目录启动。
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bench.runner import raw_call
from bench.stage5_contract_boundary import (CONDITIONS, SingleWriteFaultClient,
                                            permitted)
from bench.window_live import (METHODS, ORIGIN_SCOPE, build_binary,
                               engine_call, window_server)
from bench.window_uncertainty import control
from raguard.model import Call

VERSION = "0.1.0"
PROTOCOLS = ("2025-11-25", "2025-06-18", "2024-11-05")
ACTIONS = ("retry_same", "stop")
GUARDS = ("off", "on")
CASES = {f"C{i:02d}": condition for i, condition in enumerate(CONDITIONS, 1)}

TOOLS = (
    {
        "name": "raguard_cases",
        "description": (
            "列出隔离 Go 沙箱中可测试的模糊工具写入条件与可观察契约；"
            "不泄露隐藏提交结果。只用于研究，不扫描 Codex 的其它工具。"
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "raguard_evaluate_choice",
        "description": (
            "先由 Agent 选择 retry_same 或 stop，再在独立的本机 Go HTTP 沙箱中"
            "执行指定 B5/RAGuard 引擎，返回实际物理效果和错误确认。"
            "guard=on 使用双方相同的保守契约准入，不是 RAGuard 新算法。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "case_id": {"type": "string", "enum": list(CASES)},
                "action": {"type": "string", "enum": list(ACTIONS)},
                "engine": {"type": "string", "enum": list(METHODS)},
                "guard": {"type": "string", "enum": list(GUARDS)},
            },
            "required": ["case_id", "action", "engine", "guard"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False},
    },
    {
        "name": "raguard_compare_case",
        "description": (
            "把同一 Agent 建议在 B5/RAGuard × 契约准入关闭/开启四种独立 Go 沙箱中复现，"
            "检测有无重复物理效果和错误确认；这是固定场景测试，非独立生产 Agent 证据。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "case_id": {"type": "string", "enum": list(CASES)},
                "action": {"type": "string", "enum": list(ACTIONS)},
            },
            "required": ["case_id", "action"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False},
    },
)


def _args(raw: object, required: set[str], allowed: dict[str, tuple[str, ...]]) -> dict:
    """只接收固定实验条件，不提供命令执行、外部地址或任意路径参数。"""
    if not isinstance(raw, dict) or set(raw) != required:
        raise ValueError("工具参数必须与冻结的必填字段完全一致")
    for field, choices in allowed.items():
        if type(raw[field]) is not str or raw[field] not in choices:
            raise ValueError(f"非法字段: {field}")
    return raw


def _public_case(case_id: str) -> dict:
    c = CASES[case_id]
    # 故障发生于提交前/后，以及实际物理效果，是评价器隐藏真值。
    return {
        "case_id": case_id,
        "tool": "orders.create",
        "observed_failure": "ambiguous_acknowledgement",
        "available_contract": {
            "idempotency_guarantee_verified": c.known_guarantee and c.dedupe,
            "minimum_retention_ticks": c.ttl if c.known_guarantee and c.dedupe else None,
            "earliest_possible_accept_tick": 0,
            "recovery_tick": c.recovery_tick,
            "same_idempotency_scope": c.scope == "same",
        },
        "allowed_agent_actions": list(ACTIONS),
    }


class ResearchLab:
    """进程级 Go 构建缓存；每次调用仍使用全新沙箱与独立账本。"""

    def __init__(self):
        self._tmp: tempfile.TemporaryDirectory | None = None
        self._binary: Path | None = None

    def close(self) -> None:
        if self._tmp is not None:
            self._tmp.cleanup()
            self._tmp = None
            self._binary = None

    def _ensure_binary(self) -> Path:
        if self._binary is None:
            self._tmp = tempfile.TemporaryDirectory(prefix="raguard-mcp-go-")
            try:
                self._binary = build_binary(Path(self._tmp.name))
            except BaseException:
                self.close()
                raise
        return self._binary

    def evaluate(self, case_id: str, action: str, engine: str, guard: str) -> dict:
        """先执行 Agent 选择，结束后才读取独立 Go 物理效果日志。"""
        _args({"case_id": case_id, "action": action, "engine": engine, "guard": guard},
              {"case_id", "action", "engine", "guard"},
              {"case_id": tuple(CASES), "action": ACTIONS,
               "engine": METHODS, "guard": GUARDS})
        c = CASES[case_id]
        enabled = action == "retry_same" and (guard == "off" or permitted(c, "contract"))
        extra = () if c.dedupe else ("--no-dedupe",)
        with window_server(self._ensure_binary(), ttl=c.ttl, extra_args=extra) as origin, \
                tempfile.TemporaryDirectory(prefix="raguard-mcp-ledger-") as folder:
            raw = raw_call("orders.create")
            call = Call.parse(raw)
            client = SingleWriteFaultClient(origin, c.fault)
            initial = engine_call(engine, Path(folder), client, raw)
            before = control(origin, "effects")
            if initial != 409 or not client.initial_sent or \
                    before["attempts"] != int(c.fault == "after_commit"):
                raise RuntimeError("初始故障条件不符合冻结协议")
            tick = control(origin, "advance", {"ticks": c.recovery_tick})["server_tick"]
            if tick != c.recovery_tick:
                raise RuntimeError("Go 时钟状态异常")
            if enabled:
                client.resume(c.scope)
                final = engine_call(engine, Path(folder), client, raw)
            else:
                final = 409
            truth = control(origin, "effects")
        effects = truth["effects"]
        matched = sum((effect["scope"], effect["key"], effect["fingerprint"]) ==
                      (ORIGIN_SCOPE, call.stable_key, call.fingerprint) for effect in effects)
        other = len(effects) - matched
        duplicate = max(0, len(effects) - 1)
        unsafe = int(final == 200 and (matched != 1 or duplicate > 0 or other > 0))
        safe = int(final == 200 and matched == 1 and duplicate == 0 and other == 0)
        outcome = {
            "case_id": case_id, "agent_action": action, "engine": engine,
            "contract_gate": guard, "reissue_admitted": enabled,
            "initial_status": initial, "final_status": final,
            "physical_effects": len(effects), "duplicate_effects": duplicate,
            "wrong_scope_effects": other, "unsafe_confirmation": unsafe,
            "safe_confirmation": safe, "unresolved": int(final != 200),
            "http_writes_initial": before["attempts"],
            "http_writes_recovery": truth["attempts"] - before["attempts"],
        }
        if (outcome["http_writes_recovery"] != 0 and not enabled) or \
                (safe and unsafe) or (matched < 0):
            raise RuntimeError("独立物理判分与准入不一致")
        return outcome

    def call(self, name: str, args: object) -> dict:
        if name == "raguard_cases":
            _args(args, set(), {})
            return {
                "source": "pre_registered_stage5_research_scenarios",
                "cases": [_public_case(case_id) for case_id in CASES],
                "limitations": "非真实第三方工具扫描；隐藏物理真值仅在执行之后返回",
            }
        if name == "raguard_evaluate_choice":
            x = _args(args, {"case_id", "action", "engine", "guard"},
                      {"case_id": tuple(CASES), "action": ACTIONS,
                       "engine": METHODS, "guard": GUARDS})
            result = self.evaluate(**x)
            return {"source": "isolated_real_go_http", "result": result,
                    "limitations": "不能推断其它 Codex 工具已受此插件拦截"}
        if name == "raguard_compare_case":
            x = _args(args, {"case_id", "action"},
                      {"case_id": tuple(CASES), "action": ACTIONS})
            rows = [self.evaluate(x["case_id"], x["action"], engine, guard)
                    for guard in GUARDS for engine in METHODS]
            return {
                "source": "four_independent_real_go_http_sandboxes",
                "case_id": x["case_id"], "agent_action": x["action"],
                "rows": rows,
                "summary": {
                    "gate_off_duplicate_effects": sum(r["duplicate_effects"] for r in rows if r["contract_gate"] == "off"),
                    "gate_on_duplicate_effects": sum(r["duplicate_effects"] for r in rows if r["contract_gate"] == "on"),
                    "gate_off_unsafe_confirmations": sum(r["unsafe_confirmation"] for r in rows if r["contract_gate"] == "off"),
                    "gate_on_unsafe_confirmations": sum(r["unsafe_confirmation"] for r in rows if r["contract_gate"] == "on"),
                    "gate_on_unresolved": sum(r["unresolved"] for r in rows if r["contract_gate"] == "on"),
                },
                "limitations": (
                    "B5/RAGuard 共享相同准入函数；这是已有 Stage5 机制在 MCP 插件中的复现，"
                    "不构成新算法贡献，也不表示 Codex 自动采用插件。"
                ),
            }
        raise ValueError("未知 MCP 工具")


def _reply(message_id: object, *, result: dict | None = None,
           code: int | None = None, error: str | None = None) -> dict:
    answer = {"jsonrpc": "2.0", "id": message_id}
    if code is not None:
        answer["error"] = {"code": code, "message": error or "协议错误"}
    else:
        answer["result"] = result or {}
    return answer


def dispatch(lab: ResearchLab, message: object) -> dict | None:
    """最小 stdio MCP JSON-RPC；日志和错误栈不得进入协议标准输出。"""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _reply(None, code=-32600, error="Invalid Request")
    method = message.get("method")
    if not isinstance(method, str):
        return _reply(message.get("id"), code=-32600, error="Invalid Request")
    if "id" not in message:
        # 客户端通知不可生成响应，避免污染 stdio 协议。
        return None
    message_id = message["id"]
    if type(message_id) not in (str, int):
        return _reply(None, code=-32600, error="Invalid Request")
    params = message.get("params", {})
    if not isinstance(params, dict):
        return _reply(message_id, code=-32602, error="Invalid params")
    if method == "initialize":
        requested = params.get("protocolVersion")
        version = requested if requested in PROTOCOLS else "2025-06-18"
        return _reply(message_id, result={
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "raguard-local-research-lab", "version": VERSION},
            "instructions": "仅在本机隔离 Go 沙箱中评测; 不能保护其它工具; 除非明确分析研究故障, 不要调用。",
        })
    if method == "ping":
        return _reply(message_id)
    if method == "tools/list":
        return _reply(message_id, result={"tools": list(TOOLS)})
    if method == "tools/call":
        if type(params.get("name")) is not str:
            return _reply(message_id, code=-32602, error="Invalid params")
        try:
            result = lab.call(params["name"], params.get("arguments", {}))
            return _reply(message_id, result={
                "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, sort_keys=True)}],
                "isError": False,
            })
        except Exception as exc:
            # 禁止返回绝对路径、进程命令、密钥或异常栈。
            return _reply(message_id, result={
                "content": [{"type": "text", "text": (
                    "研究工具调用失败: " + (str(exc) if isinstance(exc, ValueError)
                                           else type(exc).__name__))}],
                "isError": True,
            })
    return _reply(message_id, code=-32601, error="Method not found")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    lab = ResearchLab()
    try:
        for line in sys.stdin.buffer:
            if len(line) > 65536:
                answer = _reply(None, code=-32600, error="Request too large")
            else:
                try:
                    message = json.loads(line)
                except (ValueError, RecursionError):
                    answer = _reply(None, code=-32700, error="Parse error")
                else:
                    answer = dispatch(lab, message)
            if answer is not None:
                sys.stdout.write(json.dumps(answer, ensure_ascii=False, separators=(",", ":")) + "\n")
                sys.stdout.flush()
    finally:
        lab.close()


if __name__ == "__main__":
    main()
