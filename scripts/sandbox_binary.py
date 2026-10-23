"""选择可信的沙箱程序：Windows 使用内置程序，其余环境使用 Go 编译。"""

from __future__ import annotations

import hashlib
import hmac
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


class SandboxBinaryError(RuntimeError):
    """沙箱文件缺失、损坏或系统不兼容。"""


def bundled_windows_sandbox(root: Path = ROOT, *, system: str | None = None,
                            machine: str | None = None) -> Path | None:
    """只在 Windows x64 上使用已校验 SHA-256 的内置程序。"""
    system = system if system is not None else sys.platform
    machine = machine if machine is not None else platform.machine()
    if system != "win32" or machine.lower() not in {"amd64", "x86_64"}:
        return None

    executable = root / "bin" / "raguard-mock.exe"
    checksum = root / "bin" / "raguard-mock.exe.sha256"
    if not executable.is_file() or not checksum.is_file():
        return None

    expected = checksum.read_text(encoding="ascii").strip().lower()
    actual = hashlib.sha256(executable.read_bytes()).hexdigest()
    if len(expected) != 64 or not hmac.compare_digest(expected, actual):
        raise SandboxBinaryError("附带的 Go 沙箱文件校验失败，请重新解压完整压缩包。")
    return executable


def sandbox_available(root: Path = ROOT) -> bool:
    """用来决定是否运行真实沙箱测试；不能把缺 Go 当作安全测试通过。"""
    return bundled_windows_sandbox(root) is not None or shutil.which("go") is not None


def build_sandbox(directory: Path, root: Path = ROOT) -> Path:
    """优先使用 Windows 已签入包的程序，否则编译一次供整组实验复用。"""
    bundled = bundled_windows_sandbox(root)
    if bundled is not None:
        return bundled

    go = shutil.which("go")
    if go is None:
        raise SandboxBinaryError("当前平台未提供内置沙箱，也未安装 Go 1.21+，无法运行故障实验。")
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / ("sandbox.exe" if os.name == "nt" else "sandbox")
    subprocess.run([go, "build", "-o", str(target), "."], cwd=root / "mock",
                   capture_output=True, text=True, check=True, timeout=120)
    return target
