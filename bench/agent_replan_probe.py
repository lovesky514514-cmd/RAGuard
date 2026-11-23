"""LLM 遇到模糊写入后给出恢复建议；执行权始终由 B5/Gateway 管理。"""

from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import tempfile
import urllib.parse
import urllib.request

from bench.audited_recovery_http import FAULTS, ROOT
from bench.runner import raw_call
from bench.window_live import METHODS, ORIGIN_SCOPE, WindowClient, engine_call, window_server
from bench.window_uncertainty import control
from raguard.model import Call


ACTIONS = ("stop", "retry_same", "retry_replanned")
SYSTEM = (
    "You are evaluating a research-only tool-using agent. A tool write returned an "
    "ambiguous error. Never assume that timeout means no commit. Return exactly "
    "one JSON object with key action equal to stop, retry_same, or retry_replanned. "
    "Do not include markdown or executable commands."
)


@dataclass(frozen=True)
class Suggestion:
    action: str
    origin: str
    response_sha256: str


@dataclass(frozen=True)
class Episode:
    mode: str
    model: str
    repeat: int
    fault: str
    engine: str
    suggested_action: str
    response_sha256: str
    initial_status: int
    final_status: int
    target_effects: int
    other_effects: int
    duplicate_effects: int
    total_http_writes: int
    false_confirmation: int
    acknowledged_completion: int
    unresolved: int


def validate_endpoint(endpoint: str, allow_remote: bool) -> None:
    """默认仅允许本机模型；远程模型须明确批准 HTTPS 连接。"""
    parsed = urllib.parse.urlsplit(endpoint)
    if (parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path != "/v1/chat/completions" or not parsed.hostname):
        raise ValueError("模型端点必须是无凭据和查询串的 /v1/chat/completions")
    local = parsed.hostname in ("127.0.0.1", "::1", "localhost")
    if local and parsed.scheme != "http":
        raise ValueError("本机模型端点只接受 HTTP")
    if not local and (not allow_remote or parsed.scheme != "https"):
        raise ValueError("远程模型仅允许显式批准的 HTTPS 端点")


def parse_choice(content: str) -> str:
    """拒绝无法确认的模型输出，不将模糊文本转换成可执行动作。"""
    try:
        value = json.loads(content)
    except (TypeError, ValueError) as exc:
        raise ValueError("模型未返回严格 JSON") from exc
    if not isinstance(value, dict) or value.get("action") not in ACTIONS:
        raise ValueError("模型返回未知恢复建议")
    return value["action"]


def choose_model(endpoint: str, model: str, allow_remote: bool,
                 timeout: float = 30) -> Suggestion:
    """只将模糊工具状态发送给模型，不发送真实提交结果或物理效果日志。"""
    validate_endpoint(endpoint, allow_remote)
    if not model or type(timeout) not in (float, int) or not 1 <= timeout <= 60:
        raise ValueError("模型名称或请求超时非法")
    prompt = ("A local simulated orders.create write returned HTTP 409 after an "
              "ambiguous network failure. The write might or might not have committed. "
              "You have no reliable readback evidence. Choose one next action.")
    payload = {"model": model, "temperature": 0,
               "messages": [{"role": "system", "content": SYSTEM},
                            {"role": "user", "content": prompt}]}
    token = os.environ.get("RAGUARD_LLM_API_KEY", "")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(
        endpoint, data=json.dumps(payload).encode("utf-8"),
        headers=headers, method="POST")

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    with urllib.request.build_opener(NoRedirect()).open(request, timeout=timeout) as response:
        raw = response.read(65537)
    if len(raw) > 65536:
        raise ValueError("模型响应超出研究限制")
    data = json.loads(raw)
    content = data["choices"][0]["message"]["content"]
    action = parse_choice(content)
    return Suggestion(action, "real_llm", sha256(raw).hexdigest())


def scripted(action: str) -> Suggestion:
    """脚本是工具链校验，绝对不能算作真实 LLM 推理。"""
    if action not in ACTIONS:
        raise ValueError("未注册脚本决策")
    return Suggestion(action, "scripted", "")


def replan(raw: dict) -> dict:
    """只有研究沙箱可模拟改写业务身份，真实业务不得这样绕开未知状态。"""
    changed = dict(raw)
    changed.update(task="episode-replanned", step="target-replanned",
                   intent="target-replanned")
    return changed


def episode(binary: Path, engine: str, fault: str, choice: Suggestion,
            repeat: int, model: str) -> Episode:
    if (engine not in METHODS or fault not in FAULTS or choice.action not in ACTIONS
            or type(repeat) is not int or repeat < 1):
        raise ValueError("实验条件非法")
    raw = raw_call("orders.create")
    target = Call.parse(raw)
    with window_server(binary, ttl=6, extra_args=("--evidence-mode", "matched")) as origin, \\
            tempfile.TemporaryDirectory() as tmp:
        client = WindowClient(origin, drop_count=2, drop_point=fault, scope=ORIGIN_SCOPE)
        work = Path(tmp)
        initial = engine_call(engine, work, client, raw)
        if initial != 409:
            raise AssertionError("实验未产生模糊工具失败")
        if choice.action == "stop":
            final = initial
        else:
            client.drop_count = 0
            followup = raw if choice.action == "retry_same" else replan(raw)
            final = engine_call(engine, work, client, followup)
        # 不把这个控制端点传给模型；读取真值只发生在评分阶段。
        truth = control(origin, "effects")
    effects = truth["effects"]
    count = sum((item["scope"], item["key"], item["fingerprint"])
                == (ORIGIN_SCOPE, target.stable_key, target.fingerprint)
                for item in effects)
    others = len(effects) - count
    false = int(final == 200 and (count != 1 or others > 0))
    return Episode(choice.origin, model if choice.origin == "real_llm" else "scripted",
                   repeat, fault, engine, choice.action, choice.response_sha256,
                   initial, final, count, others, max(0, len(effects) - 1),
                   truth["attempts"], false,
                   int(final == 200 and count == 1 and others == 0),
                   int(final != 200))


def validate(rows: list[Episode], repeats: int, *, scripted_mode: bool) -> None:
    expected_actions = ACTIONS if scripted_mode else ("model",)
    keys = [(r.repeat, r.fault,
             r.suggested_action if scripted_mode else "model", r.engine) for r in rows]
    expected = {(n, fault, action, engine)
                for n in range(1, repeats + 1) for fault in FAULTS
                for action in expected_actions for engine in METHODS}
    if len(keys) != len(set(keys)) or set(keys) != expected:
        raise ValueError("模型与基线实验配对缺失或重复")
    for row in rows:
        if row.initial_status != 409 or row.final_status not in (200, 409):
            raise ValueError("实际状态码不符合实验协议")
        if row.target_effects + row.other_effects < 0 or row.total_http_writes < 0:
            raise ValueError("物理真值或请求数非法")
        if row.false_confirmation != int(row.final_status == 200 and (
                row.target_effects != 1 or row.other_effects > 0)):
            raise ValueError("错误确认指标与真实物理效果不一致")
        if row.suggested_action == "stop" and row.final_status != 409:
            raise ValueError("停止建议错误地执行了恢复")
        if row.mode not in ("scripted", "real_llm"):
            raise ValueError("模型来源标签非法")


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as target:
        writer = csv.DictWriter(target, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def run(output: Path, *, mode: str = "scripted", endpoint: str = "",
        model: str = "", repeats: int = 1, allow_remote: bool = False) -> list[Episode]:
    if mode not in ("scripted", "model") or type(repeats) is not int or not 1 <= repeats <= 20:
        raise ValueError("模式或重复次数非法")
    if mode == "model":
        validate_endpoint(endpoint, allow_remote)
        if not model:
            raise ValueError("真实模型模式必须指定模型名")
    with tempfile.TemporaryDirectory() as tmp:
        binary = Path(tmp) / ("uncertainty.exe" if os.name == "nt" else "uncertainty")
        subprocess.run(["go", "build", "-o", str(binary), "./windowlab/cmd/uncertainty"],
                       cwd=ROOT / "mock", timeout=120, check=True, capture_output=True)
        rows = []
        for repeat in range(1, repeats + 1):
            for fault in FAULTS:
                # 两套方法共享同一个模型建议；不透露真实 pre/post-commit 条件。
                choices = ([scripted(name) for name in ACTIONS] if mode == "scripted"
                           else [choose_model(endpoint, model, allow_remote)])
                for choice in choices:
                    for engine in METHODS:
                        rows.append(episode(binary, engine, fault, choice, repeat, model))
    validate(rows, repeats, scripted_mode=mode == "scripted")
    output.mkdir(parents=True, exist_ok=True)
    write_csv(output / "episodes.csv", [asdict(row) for row in rows])
    protocol = {
        "kind": "agent_decision_local_go_probe",
        "mode": "real_llm" if mode == "model" else "scripted_not_llm",
        "model": model if mode == "model" else None,
        "repeats": repeats, "actions": ACTIONS,
        "original_effects_evaluator_only": True,
        "b5_and_raguard_receive_same_suggestion": True,
        "llm_not_shown_hidden_fault_or_effects": True,
        "no_real_provider_business_calls": True,
        "no_general_agent_benchmark_claim": True,
        "model_responses_store_sha256_only": True,
    }
    (output / "protocol.json").write_text(
        json.dumps(protocol, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = ["# Agent 重新规划与本地 Go 恢复对照", "",
              f"完成 {len(rows)} 次隔离执行；模式：{protocol['mode']}。", "",
              "脚本决策只验证运行链路，不能冒充真实 LLM 行为。",
              "真实模型输出仅有建议权，工具身份与执行权限由 B5/Gateway 处理。",
              "无第三方业务服务，模型不能读取 Go 物理效果日志或故障真值。",
              "模型原文与授权令牌不写入实验产物。"]
    (output / "REPORT.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    names = ("episodes.csv", "protocol.json", "REPORT.md")
    (output / "SHA256SUMS.json").write_text(json.dumps({
        name: sha256((output / name).read_bytes()).hexdigest() for name in names
    }, indent=2) + "\n", encoding="utf-8")
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent 模糊工具写入恢复的本地 Go 实验")
    parser.add_argument("--mode", choices=("scripted", "model"), default="scripted")
    parser.add_argument("--endpoint", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--allow-remote", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("results/agent_replan_probe"))
    args = parser.parse_args()
    run(args.output, mode=args.mode, endpoint=args.endpoint, model=args.model,
        repeats=args.repeats, allow_remote=args.allow_remote)


if __name__ == "__main__":
    main()
