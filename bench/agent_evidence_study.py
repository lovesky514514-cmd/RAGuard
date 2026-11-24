"""两轮模型恢复实验：真实 Go 证据回读、独立评分、公平 B5 对照。"""

from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import random
import subprocess
import tempfile
import urllib.request

from bench.agent_replan_probe import replan, validate_endpoint
from bench.audited_recovery_http import AuditedRecoveryClient, FAULTS, ROOT
from bench.evidence_audit_http import read_receipt
from bench.runner import raw_call
from bench.window_live import METHODS, ORIGIN_SCOPE, WindowClient, engine_call, window_server
from bench.window_uncertainty import control
from raguard.model import Call
from raguard.transport import UncertainResult

EVIDENCE = ("visible", "unavailable")
FIRST = ("audit", "stop", "retry_same", "retry_replanned")
FINAL = ("confirm", "stop", "retry_same", "retry_replanned")
SYSTEM = ("You are an agent in a simulated order system. A write failed ambiguously. "
          "Timeout is not proof of non-execution. Return ONLY a JSON object with "
          "one key action. Your suggestion is not an execution authorization.")


@dataclass(frozen=True)
class Episode:
    source: str
    model: str
    repeat: int
    fault: str
    evidence_case: str
    engine: str
    first_action: str
    observed: str
    final_action: str
    model_calls: int
    decision_digest: str
    initial_status: int
    final_status: int
    target_effects: int
    other_effects: int
    duplicate_effects: int
    http_writes: int
    shared_observation_queries: int
    engine_audit_queries: int
    unsupported_confirm: int
    false_confirmation: int
    acknowledged_completion: int
    unresolved: int


def parse_action(content: str, allowed: tuple[str, ...]) -> str:
    """拒绝额外字段或无法解析的输出，不能静默跳过失败样本。"""
    try:
        data = json.loads(content)
    except (TypeError, ValueError) as exc:
        raise ValueError("模型输出不是严格 JSON") from exc
    if not isinstance(data, dict) or set(data) != {"action"} or data["action"] not in allowed:
        raise ValueError("模型返回未知动作或额外字段")
    return data["action"]


def ask(endpoint: str, model: str, messages: list[dict],
        allowed: tuple[str, ...], allow_remote: bool) -> tuple[str, str]:
    validate_endpoint(endpoint, allow_remote)
    token = os.environ.get("RAGUARD_LLM_API_KEY", "")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    payload = json.dumps({"model": model, "temperature": 0, "messages": messages},
                         ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(endpoint, data=payload, headers=headers, method="POST")

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    with urllib.request.build_opener(NoRedirect()).open(request, timeout=60) as response:
        raw = response.read(65537)
    if len(raw) > 65536:
        raise ValueError("模型响应超过大小限制")
    data = json.loads(raw)
    return parse_action(data["choices"][0]["message"]["content"], allowed), sha256(raw).hexdigest()


def observation(binary: Path, fault: str, evidence_case: str) -> str:
    """独立沙箱提供真实 HTTP 回读，模型不接触隐含的物理效果判分。"""
    if fault not in FAULTS or evidence_case not in EVIDENCE:
        raise ValueError("观察条件非法")
    raw = raw_call("orders.create")
    target = Call.parse(raw)
    with window_server(binary, ttl=6, extra_args=("--evidence-mode", "matched")) as origin:
        client = WindowClient(origin, drop_count=1, drop_point=fault, scope=ORIGIN_SCOPE)
        try:
            client.execute(target, None)
        except UncertainResult:
            pass
        else:
            raise AssertionError("模拟网络故障未触发")
        control(origin, "advance", {"ticks": 2})
        if evidence_case == "unavailable":
            return "unavailable"
        reply = read_receipt(origin, ORIGIN_SCOPE, target.stable_key, target.fingerprint)
        if reply is None:
            return "not_observed"
        if (reply.get("status"), reply.get("scope"), reply.get("key"),
                reply.get("fingerprint")) == (
                "committed", ORIGIN_SCOPE, target.stable_key, target.fingerprint):
            return "committed"
        return "invalid_receipt"


def suggest(binary: Path, mode: str, endpoint: str, model: str,
            allow_remote: bool, fault: str, evidence_case: str):
    """模型可请求回读工具，随后依据观察选择动作；两引擎共享建议。"""
    if mode == "scripted":
        first, digest1 = "audit", ""
    else:
        first, digest1 = ask(endpoint, model, [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": (
                "orders.create returned ambiguous 409. You do not know if it committed. "
                "Choose audit, stop, retry_same or retry_replanned.")}
        ], FIRST, allow_remote)
    observed = "not_requested"
    second, digest2 = first, ""
    calls = int(mode == "model")
    if first == "audit":
        observed = observation(binary, fault, evidence_case)
        if mode == "scripted":
            second = "confirm" if observed == "committed" else "stop"
        else:
            second, digest2 = ask(endpoint, model, [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": "The original write returned ambiguous status 409."},
                {"role": "user", "content": "Tool evidence.read returned: " + json.dumps({
                    "observation": observed,
                    "not_observed_is_not_nonexecution_proof": True,
                    "source": "trusted_local_loopback_not_crypto_signed"
                })},
                {"role": "user", "content": (
                    "Choose confirm, stop, retry_same or retry_replanned. "
                    "Do not confirm without a positive receipt.")}
            ], FINAL, allow_remote)
            calls += 1
    return first, observed, second, ";".join(x for x in (digest1, digest2) if x), calls


def execute(binary: Path, engine: str, fault: str, evidence_case: str,
            first: str, observed: str, final: str, digest: str,
            calls: int, repeat: int, source: str, model: str) -> Episode:
    """执行身份由原始引擎掌管；最终真值由独立 Go 效果日志给出。"""
    if engine not in METHODS or fault not in FAULTS or final not in FINAL:
        raise ValueError("无效研究条件")
    raw = raw_call("orders.create")
    target = Call.parse(raw)
    with window_server(binary, ttl=6, extra_args=("--evidence-mode", "matched")) as origin, \
            tempfile.TemporaryDirectory() as tmp:
        client = AuditedRecoveryClient(origin, fault, "valid")
        work = Path(tmp)
        initial = engine_call(engine, work, client, raw)
        if initial != 409:
            raise AssertionError("初始阶段未出现模糊失败")
        control(origin, "advance", {"ticks": 2})
        if final == "stop":
            status = initial
        elif final == "confirm":
            # 模型的确认不是证据，必须再经过引擎的只读权威回读。
            client.begin_recovery(2)
            status = engine_call(engine, work, client, raw)
        else:
            client.drop_count = 0
            status = engine_call(engine, work, client,
                                 raw if final == "retry_same" else replan(raw))
        truth = control(origin, "effects")
    effects = truth["effects"]
    matched = sum((e["scope"], e["key"], e["fingerprint"]) ==
                  (ORIGIN_SCOPE, target.stable_key, target.fingerprint) for e in effects)
    other = len(effects) - matched
    correct = int(status == 200 and matched == 1 and other == 0)
    return Episode(source, model if source == "real_llm" else "scripted", repeat,
                   fault, evidence_case, engine, first, observed, final, calls, digest,
                   initial, status, matched, other, max(0, len(effects)-1), truth["attempts"],
                   int(first == "audit" and observed != "unavailable"), client.audit_queries,
                   int(final == "confirm" and observed != "committed"),
                   int(status == 200 and not correct), correct, int(status != 200))


def validate(rows: list[Episode], repeats: int) -> None:
    """拒绝缺失配对、虚构成功或不一致的两套方法建议。"""
    expected = {(n, f, ev, m) for n in range(1, repeats + 1)
                for f in FAULTS for ev in EVIDENCE for m in METHODS}
    keys = [(r.repeat, r.fault, r.evidence_case, r.engine) for r in rows]
    if len(keys) != len(set(keys)) or set(keys) != expected:
        raise ValueError("实验配对不完整")
    lookup = dict(zip(keys, rows))
    for row in rows:
        correct = row.final_status == 200 and row.target_effects == 1 and row.other_effects == 0
        if (row.initial_status != 409 or row.final_status not in (200, 409)
                or row.acknowledged_completion != int(correct)
                or row.false_confirmation != int(row.final_status == 200 and not correct)
                or row.unresolved != int(row.final_status != 200)):
            raise ValueError("真实物理效果与状态计数不一致")
    for n in range(1, repeats + 1):
        for f in FAULTS:
            for ev in EVIDENCE:
                a, b = (lookup[(n, f, ev, m)] for m in METHODS)
                if (a.first_action, a.observed, a.final_action, a.decision_digest) != (
                        b.first_action, b.observed, b.final_action, b.decision_digest):
                    raise ValueError("B5/Gateway 模型建议不一致")


def run(output: Path, *, repeats: int = 3, seed: int = 20261124,
        mode: str = "scripted", endpoint: str = "", model: str = "",
        allow_remote: bool = False) -> list[Episode]:
    if type(repeats) is not int or not 1 <= repeats <= 30 or type(seed) is not int:
        raise ValueError("重复次数或种子非法")
    if mode not in ("scripted", "model"):
        raise ValueError("未知实验模式")
    if mode == "model":
        validate_endpoint(endpoint, allow_remote)
        if not model:
            raise ValueError("真实模型模式需填写模型名")
    with tempfile.TemporaryDirectory() as tmp:
        binary = Path(tmp) / ("uncertainty.exe" if os.name == "nt" else "uncertainty")
        subprocess.run(["go", "build", "-o", str(binary), "./windowlab/cmd/uncertainty"],
                       cwd=ROOT / "mock", check=True, timeout=120, capture_output=True)
        rows = []
        rng = random.Random(seed)
        for n in range(1, repeats + 1):
            conditions = [(f, ev) for f in FAULTS for ev in EVIDENCE]
            rng.shuffle(conditions)
            for fault, ev in conditions:
                print(f"[{n}/{repeats}] {fault}/{ev}: 正在获取模型建议", flush=True)
                first, obs, final, digest, calls = suggest(
                    binary, mode, endpoint, model, allow_remote, fault, ev)
                for engine in METHODS:
                    row = execute(binary, engine, fault, ev, first, obs, final,
                                  digest, calls, n,
                                  "real_llm" if mode == "model" else "scripted", model)
                    rows.append(row)
                    print(f"  {engine}: {final} -> {row.final_status}, effects="
                          f"{row.target_effects}+{row.other_effects}", flush=True)
    validate(rows, repeats)
    output.mkdir(parents=True, exist_ok=True)
    with (output / "episodes.csv").open("w", newline="", encoding="utf-8") as dest:
        writer = csv.DictWriter(dest, fieldnames=Episode.__dataclass_fields__)
        writer.writeheader()
        writer.writerows(asdict(row) for row in rows)
    protocol = {
        "kind": "two_turn_evidence_conditioned_agent_go",
        "mode": "real_llm" if mode == "model" else "scripted_not_llm",
        "model": model if mode == "model" else None, "repeats": repeats,
        "order_seed": seed, "faults": FAULTS, "evidence_profiles": EVIDENCE,
        "independent_observation_go_sandbox": True,
        "same_model_decision_for_both_engines": True,
        "oracle_evaluator_only": True,
        "confirmation_requires_real_recovery_audit": True,
        "simulated_provider_native_idempotency": True,
        "not_a_non_idempotent_or_real_vendor_study": True
    }
    (output / "protocol.json").write_text(
        json.dumps(protocol, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = [
        "# 两轮证据条件化 Agent 恢复研究", "",
        f"方法级实验：{len(rows)}；运行模式：{protocol['mode']}。", "",
        "模型无法读取故障真值或物理效果日志；两套方法共享模型建议。",
        "提交证明来自独立本地 Go 服务；未观察到或不可用均不是未执行证明。",
        "模拟服务仍具有原生幂等性，不外推到无幂等契约的第三方 API。"
    ]
    (output / "REPORT.md").write_text("\n".join(summary) + "\n", encoding="utf-8")
    names = ("episodes.csv", "protocol.json", "REPORT.md")
    (output / "SHA256SUMS.json").write_text(json.dumps({
        p: sha256((output / p).read_bytes()).hexdigest() for p in names
    }, indent=2) + "\n", encoding="utf-8")
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="证据条件化两轮 Agent/Go 恢复研究")
    parser.add_argument("--mode", choices=("scripted", "model"), default="scripted")
    parser.add_argument("--endpoint", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20261124)
    parser.add_argument("--allow-remote", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("results/agent_evidence_study"))
    args = parser.parse_args()
    run(args.output, repeats=args.repeats, seed=args.seed, mode=args.mode,
        endpoint=args.endpoint, model=args.model, allow_remote=args.allow_remote)


if __name__ == "__main__":
    main()
