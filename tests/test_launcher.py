"""启动失败应有明确原因，且能正确识别常见本地环境冲突。"""

import socket
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import launcher
from scripts import sandbox_binary


class LauncherTests(unittest.TestCase):
    def test_go_missing_produces_clear_error(self):
        with patch.object(launcher.shutil, "which", return_value=None), \
             patch.object(launcher, "record"):
            with self.assertRaisesRegex(launcher.LaunchError, "没有找到 Go"):
                launcher.ensure_tools()

    def test_port_occupied_is_detected(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            self.assertTrue(launcher.port_is_busy(listener.getsockname()[1]))

    def test_exited_service_reports_reason(self):
        with subprocess.Popen([launcher.sys.executable, "-c", "import sys;sys.exit(7)"]) as process:
            process.wait()
            with self.assertRaisesRegex(launcher.LaunchError, "提前退出"):
                launcher.wait_ready(65534, process, seconds=1)

    def test_bundled_sandbox_hash_is_checked(self):
        import hashlib

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            folder = root / "bin"
            folder.mkdir()
            (folder / "raguard-mock.exe").write_bytes(b"test-binary")
            digest = hashlib.sha256(b"test-binary").hexdigest()
            (folder / "raguard-mock.exe.sha256").write_text(digest, encoding="ascii")
            with patch.object(launcher, "ROOT", root), \
                 patch.object(launcher.sys, "platform", "win32"), \
                 patch.object(sandbox_binary.platform, "machine", return_value="AMD64"):
                self.assertEqual(launcher.bundled_windows_sandbox(),
                                 folder / "raguard-mock.exe")
                (folder / "raguard-mock.exe").write_bytes(b"changed")
                with self.assertRaisesRegex(launcher.LaunchError, "校验失败"):
                    launcher.bundled_windows_sandbox()


    def test_without_go_normal_windows_check_uses_bundled_program(self):
        """在用户没有 Go 的情况下，自检不能卡在预先检查工具阶段。"""
        with patch.object(launcher, "bundled_windows_sandbox", return_value=Path("mock.exe")), \
             patch.object(launcher.shutil, "which", return_value=None), \
             patch.object(launcher, "command_output") as commands, \
             patch.object(launcher, "start") as start, \
             patch.object(launcher, "record") as record:
            launcher.check()
            self.assertEqual(commands.call_count, 4)
            start.assert_called_once_with(self_test=True)
            self.assertTrue(any("未执行" in item.args[0] for item in record.call_args_list))

    def test_without_go_windows_experiment_is_allowed(self):
        with patch.object(launcher, "bundled_windows_sandbox", return_value=Path("mock.exe")), \
             patch.object(launcher.shutil, "which", return_value=None), \
             patch.object(launcher, "command_output") as commands, \
             patch.object(launcher, "record"):
            launcher.experiment()
            self.assertEqual(commands.call_count, 2)

    def test_benchmark_uses_verified_windows_binary_without_go(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            folder = root / "bin"
            folder.mkdir()
            binary = folder / "raguard-mock.exe"
            binary.write_bytes(b"fixture")
            import hashlib
            (folder / "raguard-mock.exe.sha256").write_text(
                hashlib.sha256(binary.read_bytes()).hexdigest(), encoding="ascii")
            with patch.object(sandbox_binary.sys, "platform", "win32"), \
                 patch.object(sandbox_binary.platform, "machine", return_value="AMD64"), \
                 patch.object(sandbox_binary.shutil, "which", return_value=None):
                self.assertTrue(sandbox_binary.sandbox_available(root))
                self.assertEqual(sandbox_binary.build_sandbox(root / "temp", root), binary)
                binary.write_bytes(b"altered")
                with self.assertRaisesRegex(sandbox_binary.SandboxBinaryError, "校验失败"):
                    sandbox_binary.build_sandbox(root / "temp", root)

    def test_logging_is_utf8_and_does_not_require_console(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "运行日志.txt"
            with patch.object(launcher, "LOG_FILE", path):
                launcher.record("中文错误：启动失败")
                self.assertIn("中文错误", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
