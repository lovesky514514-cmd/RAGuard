"""通过独立 stdio MCP 客户端核验插件发现、真实 Go HTTP 执行及 A/B 物理结果。"""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def exchange(proc: subprocess.Popen, message: dict) -> dict:
    """此测试客户端只发送一个请求并逐条读取响应，未使用任何模型 API。"""
    assert proc.stdin is not None and proc.stdout is not None
    proc.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
    proc.stdin.flush()
    raw = proc.stdout.readline()
    if not raw:
        raise RuntimeError("MCP 服务端提前关闭或未输出合法响应")
    result = json.loads(raw.decode("utf-8"))
    if result.get("id") != message["id"]:
        raise RuntimeError("JSON-RPC 响应 ID 与请求不一致")
    if "error" in result:
        raise RuntimeError("MCP 协议报错")
    return result["result"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Codex MCP 插件独立协议/真实 Go 冒烟验收")
    parser.add_argument("--output", type=Path,
                        default=Path("results/codex_mcp_wire_smoke.json"))
    args = parser.parse_args()
    child = subprocess.Popen([sys.executable, str(ROOT / "integrations/raguard_codex_mcp.py")],
                             cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE)
    try:
        handshake = exchange(child, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                    "params": {"protocolVersion": "2025-06-18",
                                               "capabilities": {},
                                               "clientInfo": {"name": "raguard-wire-test", "version": "1"}}})
        tools = exchange(child, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        compare_response = exchange(child, {"jsonrpc": "2.0", "id": 3,
                                            "method": "tools/call",
                                            "params": {"name": "raguard_compare_case",
                                                       "arguments": {"case_id": "C05",
                                                                     "action": "retry_same"}}})
        if compare_response.get("isError"):
            raise RuntimeError("MCP 返回研究工具调用错误")
        comparison = json.loads(compare_response["content"][0]["text"])
        summary = comparison["summary"]
        if (summary["gate_off_duplicate_effects"],
            summary["gate_on_duplicate_effects"],
            summary["gate_on_unresolved"]) != (2, 0, 2):
            raise RuntimeError("Go 物理效果未达到预注册 A/B 反例")
        if len(comparison["rows"]) != 4 or len(tools["tools"]) != 3:
            raise RuntimeError("MCP 工具数量或公平配对行数异常")
        evidence = {
            "source": "direct_stdio_mcp_client_and_real_go_sandbox",
            "model_invoked": False,
            "codex_agent_invoked": False,
            "plugin_intercepts_other_tools": False,
            "protocol_version": handshake["protocolVersion"],
            "mcp_tools": [t["name"] for t in tools["tools"]],
            "case_id": "C05", "agent_action": "retry_same",
            "comparison": comparison,
            "interpretation": (
                "证明 MCP 可调用与既有 Stage5 可复现的契约边界；"
                "不证明 Codex 自主使用、不证明新缺陷、不证明 RAGuard 独有优势。"
            ),
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(evidence, indent=2, ensure_ascii=False) + "\n"
        args.output.write_text(content, encoding="utf-8")
        args.output.with_suffix(args.output.suffix + ".sha256").write_text(
            sha256(content.encode("utf-8")).hexdigest() + "  " + args.output.name + "\n",
            encoding="utf-8")
        print("PASS: MCP stdio + 4 paired Go HTTP episodes; no model executed")
        print("Evidence:", args.output)
        return 0
    finally:
        if child.stdin:
            child.stdin.close()
        try:
            child.wait(timeout=8)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=4)
        if child.stderr:
            child.stderr.close()
        if child.stdout:
            child.stdout.close()


if __name__ == "__main__":
    raise SystemExit(main())
