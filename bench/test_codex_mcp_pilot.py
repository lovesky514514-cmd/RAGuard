"""Codex MCP 本机研究插件的协议、真实 HTTP 反例与拒绝非法请求测试。"""

import json
from pathlib import Path
import subprocess
import sys
import unittest

from integrations.raguard_codex_mcp import ResearchLab, dispatch


class MCPProtocolTests(unittest.TestCase):
    def setUp(self):
        self.lab = ResearchLab()

    def tearDown(self):
        self.lab.close()

    def test_handshake_and_discovery(self):
        initial = dispatch(self.lab, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                     "params": {"protocolVersion": "2025-06-18"}})
        self.assertEqual(initial["result"]["protocolVersion"], "2025-06-18")
        listed = dispatch(self.lab, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        self.assertEqual(set(x["name"] for x in listed["result"]["tools"]),
                         {"raguard_cases", "raguard_evaluate_choice", "raguard_compare_case"})
        self.assertIsNone(dispatch(self.lab, {"jsonrpc": "2.0", "method": "notifications/initialized"}))

    def test_refuses_external_targets_unknown_tool_and_malformed_args(self):
        for name, args in (
            ("raguard_compare_case", {"case_id": "C05", "action": "retry_same", "url": "https://example.com"}),
            ("raguard_evaluate_choice", {"case_id": "C05", "action": "shell", "engine": "b5", "guard": "off"}),
            ("raguard_compare_case", {"case_id": "no-dedupe", "action": "retry_same"}),
            ("run_shell", {"command": "something"}),
        ):
            with self.subTest(name=name, args=args):
                r = dispatch(self.lab, {"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                                        "params": {"name": name, "arguments": args}})
                self.assertTrue(r["result"]["isError"])
        missing = dispatch(self.lab, {"jsonrpc": "2.0", "id": 6, "method": "unknown"})
        self.assertEqual(missing["error"]["code"], -32601)
        self.assertIsNone(self.lab._binary)

    def test_case_catalog_excludes_hidden_commit_truth(self):
        response = self.lab.call("raguard_cases", {})
        self.assertEqual(len(response["cases"]), 8)
        self.assertEqual(response["cases"][4]["case_id"], "C05")
        self.assertNotIn("fault", json.dumps(response))
        self.assertNotIn("physical_effects", json.dumps(response))

    def test_stdio_subprocess_produces_only_json_rpc(self):
        root = Path(__file__).resolve().parents[1]
        script = root / "integrations" / "raguard_codex_mcp.py"
        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                        "clientInfo": {"name": "pilot-client", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "raguard_cases", "arguments": {}}},
        ]
        proc = subprocess.run([sys.executable, str(script)], cwd=root,
                              input=("\n".join(json.dumps(x) for x in messages)+"\n").encode(),
                              capture_output=True, timeout=20)
        self.assertEqual(proc.returncode, 0, proc.stderr.decode(errors="replace"))
        replies = [json.loads(s) for s in proc.stdout.decode("utf-8").splitlines()]
        self.assertEqual([r["id"] for r in replies], [1, 2, 3])
        self.assertEqual(len(replies[1]["result"]["tools"]), 3)
        self.assertFalse(replies[2]["result"]["isError"])


class RealGoToolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lab = ResearchLab()

    @classmethod
    def tearDownClass(cls):
        cls.lab.close()

    def test_no_dedupe_detects_duplicate_and_gate_stops_both_engines(self):
        # 真实 HTTP、真 SQLite：并非从预期策略合成结果。
        comparison = self.lab.call("raguard_compare_case", {"case_id": "C05",
                                                           "action": "retry_same"})
        self.assertEqual(len(comparison["rows"]), 4)
        for engine in ("b5", "raguard"):
            baseline = next(r for r in comparison["rows"] if r["engine"] == engine and r["contract_gate"] == "off")
            guarded = next(r for r in comparison["rows"] if r["engine"] == engine and r["contract_gate"] == "on")
            self.assertEqual((baseline["duplicate_effects"], baseline["unsafe_confirmation"]), (1, 1))
            self.assertEqual((guarded["duplicate_effects"], guarded["unsafe_confirmation"], guarded["unresolved"]), (0, 0, 1))
            self.assertEqual(guarded["http_writes_recovery"], 0)
        self.assertEqual(comparison["summary"]["gate_off_duplicate_effects"], 2)
        self.assertEqual(comparison["summary"]["gate_on_duplicate_effects"], 0)

    def test_verified_same_scope_ttl_allows_reissue(self):
        outcome = self.lab.evaluate("C01", "retry_same", "b5", "on")
        self.assertTrue(outcome["reissue_admitted"])
        self.assertEqual(outcome["safe_confirmation"], 1)
        self.assertEqual(outcome["duplicate_effects"], 0)

    def test_agent_can_choose_stop_without_fabricated_completion(self):
        for guard in ("off", "on"):
            outcome = self.lab.evaluate("C05", "stop", "raguard", guard)
            self.assertEqual((outcome["final_status"], outcome["unresolved"]), (409, 1))
            self.assertEqual(outcome["http_writes_recovery"], 0)
            self.assertEqual(outcome["safe_confirmation"], 0)

    def test_scope_mismatch_is_detected_independent_of_success_reply(self):
        x = self.lab.evaluate("C04", "retry_same", "raguard", "off")
        self.assertEqual(x["final_status"], 200)
        self.assertEqual((x["wrong_scope_effects"], x["unsafe_confirmation"]), (1, 1))
        y = self.lab.evaluate("C04", "retry_same", "raguard", "on")
        self.assertEqual((y["wrong_scope_effects"], y["unresolved"]), (0, 1))


if __name__ == "__main__":
    unittest.main()
