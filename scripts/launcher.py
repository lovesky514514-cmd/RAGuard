"""本地运行入口：检查环境、启动服务并保存可排查的错误日志。"""

from __future__ import annotations

import argparse
from datetime import datetime
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

# 兼容命令行直接执行 scripts/launcher.py 与作为包导入的单元测试。
if __package__:
    from .sandbox_binary import SandboxBinaryError, bundled_windows_sandbox as locate_bundled_sandbox
else:
    from sandbox_binary import SandboxBinaryError, bundled_windows_sandbox as locate_bundled_sandbox

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "logs"
LOG_FILE = LOG_DIR / "运行日志.txt"
MOCK_PORT = 8181
GATEWAY_PORT = 8088


class LaunchError(RuntimeError):
    """可向使用者解释的运行异常。"""


def record(message: str) -> None:
    """终端与日志同时输出，不记录签名密钥或业务请求体。"""
    print(message, flush=True)
    with LOG_FILE.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(message + "\n")


def command_output(command: list[str], *, cwd: Path = ROOT,
                   environment: dict[str, str] | None = None) -> None:
    """实时打印子进程信息，返回码异常时阻止后续步骤。"""
    record("执行：" + " ".join(command))
    try:
        process = subprocess.Popen(
            command, cwd=cwd, env=environment,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
        )
    except OSError as exc:
        raise LaunchError(f"无法启动 {command[0]}：{exc}") from exc
    assert process.stdout is not None
    for line in process.stdout:
        record(line.rstrip("\r\n"))
    code = process.wait()
    if code:
        raise LaunchError(f"命令退出码 {code}：{' '.join(command)}")


def ensure_tools(*, need_go: bool = True) -> None:
    if sys.version_info < (3, 11):
        raise LaunchError("需要 Python 3.11 或更新版本。")
    record("Python：" + sys.version.split()[0])
    if need_go:
        go = shutil.which("go")
        if not go:
            raise LaunchError("没有找到 Go。请安装 Go 1.21+，重开终端后再启动。")
        command_output([go, "version"])


def bundled_windows_sandbox() -> Path | None:
    """统一使用共享的内置程序校验，避免入口和实验采用不同规则。"""
    try:
        return locate_bundled_sandbox(ROOT)
    except SandboxBinaryError as exc:
        raise LaunchError(str(exc)) from exc


def port_is_busy(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
        client.settimeout(0.3)
        return client.connect_ex(("127.0.0.1", port)) == 0


def wait_ready(port: int, process: subprocess.Popen, *, seconds: int = 12) -> None:
    url = f"http://127.0.0.1:{port}/healthz"
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        if process.poll() is not None:
            raise LaunchError(f"{port} 端口对应的进程提前退出，退出码 {process.returncode}。")
        try:
            with urllib.request.urlopen(url, timeout=0.3) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError, OSError):
            pass
        time.sleep(0.15)
    raise LaunchError(f"服务在 {port} 端口启动超时，请检查防火墙或端口冲突。")


def stop(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def show_recent_log(lines: int = 20) -> None:
    if not LOG_FILE.exists():
        return
    recent = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]
    print("\n最近的运行日志：", flush=True)
    for line in recent:
        print(line, flush=True)


def start(*, self_test: bool = False) -> None:
    """一个窗口管理两个服务，退出时同时回收子进程。"""
    bundled = bundled_windows_sandbox()
    ensure_tools(need_go=bundled is None)
    for port in (MOCK_PORT, GATEWAY_PORT):
        if port_is_busy(port):
            raise LaunchError(f"端口 {port} 已被占用。请先关闭其他 RAGuard 实例。")

    data = ROOT / "data"
    data.mkdir(exist_ok=True)
    command_output([sys.executable, "-X", "utf8", "scripts/prepare_local_demo.py"])
    if bundled is not None:
        binary = bundled
        record("使用随包提供的 Windows 沙箱程序（已验证 SHA-256）。")
    else:
        suffix = ".exe" if os.name == "nt" else ""
        binary = data / ("raguard-mock" + suffix)
        command_output([shutil.which("go") or "go", "build", "-o", str(binary), "."],
                       cwd=ROOT / "mock")
    environment = os.environ.copy()
    environment.update({"RAGUARD_LOCAL_DEMO": "1", "PYTHONUTF8": "1"})
    mock = gateway = None
    # 使用追加模式，避免服务 stdout 与控制台互相阻塞。
    with LOG_FILE.open("a", encoding="utf-8", buffering=1) as stream:
        try:
            mock = subprocess.Popen([str(binary)], cwd=ROOT, env=environment,
                                    stdout=stream, stderr=subprocess.STDOUT)
            wait_ready(MOCK_PORT, mock)
            record("Go 沙箱就绪：http://127.0.0.1:8181")

            gateway = subprocess.Popen([sys.executable, "-X", "utf8", "-m", "raguard.http"],
                                       cwd=ROOT, env=environment,
                                       stdout=stream, stderr=subprocess.STDOUT)
            wait_ready(GATEWAY_PORT, gateway)
            record("网关就绪：http://127.0.0.1:8088")
            record("正在运行一次订单演示，验证端到端调用。")
            command_output([sys.executable, "-X", "utf8", "-m", "raguard.demo"],
                           environment=environment)
            if self_test:
                record("本地启动、演示与退出检查通过。")
                return
            record("运行成功。按 Ctrl+C 关闭服务；不要直接结束 Go 进程。")
            while True:
                for name, process in (("Go 沙箱", mock), ("Python 网关", gateway)):
                    if process.poll() is not None:
                        raise LaunchError(f"{name} 意外退出（代码 {process.returncode}），请查看运行日志。")
                time.sleep(0.5)
        except KeyboardInterrupt:
            record("收到退出操作，正在关闭服务。")
        finally:
            stop(gateway)
            stop(mock)
            record("服务已关闭。")


def check() -> None:
    """无 Go 的 Windows 也能检查 Python、编码和内置沙箱运行。"""
    bundled = bundled_windows_sandbox()
    ensure_tools(need_go=bundled is None)
    python = [sys.executable, "-X", "utf8"]
    commands = [
        python + ["scripts/check_utf8.py"],
        python + ["-m", "compileall", "-q", "raguard", "tests", "bench", "audit", "scripts"],
        python + ["-m", "unittest", "discover", "-s", "tests", "-v"],
        python + ["-m", "unittest", "discover", "-s", "bench", "-p", "test_*.py", "-v"],
    ]
    for command in commands:
        command_output(command)

    go = shutil.which("go")
    if go:
        for arguments in (["test", "./..."], ["vet", "./..."]):
            command_output([go, *arguments], cwd=ROOT / "mock")
    else:
        # 已提供的 EXE 能用于实际实验，但不代替源码编译、Go 单测和 vet。
        record("未安装 Go：Go 源码测试和 vet 未执行；继续验证内置沙箱的真实启动。")
        if bundled is None:
            raise LaunchError("没有可验证的沙箱程序，无法完成自检。")
    start(self_test=True)
    record("本地可执行检查完成；未执行的项目已在上方明确标注。")


def experiment() -> None:
    """Windows x64 使用内置程序运行故障试验，无须安装 Go。"""
    bundled = bundled_windows_sandbox()
    ensure_tools(need_go=bundled is None)
    if bundled is not None:
        record("实验使用已校验的 Windows 沙箱程序，无须安装 Go。")
    command_output([sys.executable, "-X", "utf8", "-m", "bench.faultmatrix",
                    "--mode", "smoke", "--repeats", "2", "--output", "results/smoke"])
    command_output([sys.executable, "-X", "utf8", "-m", "bench.workflows"])
    record("实验结束，结果见 results/smoke/ 与 results/workflows.csv。")


def main() -> int:
    parser = argparse.ArgumentParser(description="RAGuard 本地启动与检查")
    parser.add_argument("action", nargs="?", choices=["start", "check", "experiment"],
                        default="start")
    parser.add_argument("--self-test", action="store_true", help="演示成功后自动退出")
    args = parser.parse_args()
    LOG_DIR.mkdir(exist_ok=True)
    # 连续启动只保留一个日志文件，同时保留前次失败的诊断上下文。
    if LOG_FILE.exists() and LOG_FILE.stat().st_size > 2_000_000:
        LOG_FILE.write_bytes(LOG_FILE.read_bytes()[-500_000:])
    with LOG_FILE.open("a", encoding="utf-8") as stream:
        stream.write(f"\n[{datetime.now().isoformat(timespec='seconds')}] {args.action}\n")
    try:
        if args.action == "start":
            start(self_test=args.self_test)
        elif args.action == "check":
            check()
        else:
            experiment()
        return 0
    except (LaunchError, OSError, subprocess.SubprocessError) as exc:
        record("运行失败：" + str(exc))
        show_recent_log()
        print(f"\n请把 {LOG_FILE.relative_to(ROOT)} 发给开发者。", flush=True)
        return 1
    except Exception as exc:
        # 未预期的异常记录完整调用栈，方便查出真实根因。
        import traceback
        trace = "".join(traceback.format_exception(exc))
        with LOG_FILE.open("a", encoding="utf-8") as stream:
            stream.write(trace)
        print("发生未预期的错误，详细调用栈已保存到 logs/运行日志.txt。", flush=True)
        show_recent_log()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
