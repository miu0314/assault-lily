# -*- coding: utf-8 -*-
"""打包相关逻辑测试：路径解析 / 子进程重启 / 打包入口分发。"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import app_paths
import dep_bootstrap
import launcher
import packaging_entry


class AppPathsTest(unittest.TestCase):
    def test_source_mode_uses_project_dir(self):
        project_dir = Path(app_paths.__file__).resolve().parent
        self.assertFalse(app_paths.is_frozen())
        self.assertEqual(app_paths.base_dir(), project_dir)

    def test_frozen_mode_uses_exe_dir(self):
        exe = r"C:\Users\someone\Desktop\AssaultLilyBot\AssaultLilyBot.exe"
        with patch.object(sys, "frozen", True, create=True), \
                patch.object(sys, "executable", exe):
            self.assertTrue(app_paths.is_frozen())
            self.assertEqual(app_paths.base_dir(), Path(exe).resolve().parent)


class DepBootstrapTest(unittest.TestCase):
    def test_frozen_skips_auto_install(self):
        with patch.object(sys, "frozen", True, create=True), \
                patch("subprocess.run") as run, \
                patch.object(dep_bootstrap, "missing_modules", return_value=["cv2"]):
            self.assertTrue(dep_bootstrap.ensure_deps())
        run.assert_not_called()


class LauncherChildCommandTest(unittest.TestCase):
    def test_source_mode_uses_script_path(self):
        with patch.object(launcher, "_pythonw_path", return_value=r"C:\py\pythonw.exe"):
            command = launcher.child_command("main.py", "cfg.json")
        self.assertEqual(command, [r"C:\py\pythonw.exe",
                                   str(launcher.BASE_DIR / "main.py"), "cfg.json"])

    def test_frozen_mode_restarts_self(self):
        exe = r"C:\app\AssaultLilyBot.exe"
        with patch.object(sys, "frozen", True, create=True), \
                patch.object(sys, "executable", exe):
            self.assertEqual(launcher.child_command("main.py", "cfg.json"),
                             [exe, "--run-main", "cfg.json"])
            self.assertEqual(
                launcher.child_command("scripts/scan_legion_shop.py", "cfg.json"),
                [exe, "--scan-shop", "cfg.json"])


class PackagingEntryTest(unittest.TestCase):
    def test_default_is_launcher(self):
        self.assertEqual(packaging_entry.resolve_command(["app.exe"]),
                         ("launcher", []))

    def test_dispatch_flags(self):
        self.assertEqual(
            packaging_entry.resolve_command(["app.exe", "--run-main", "config.json"]),
            ("main", ["config.json"]))
        self.assertEqual(
            packaging_entry.resolve_command(["app.exe", "--scan-shop", "config.json"]),
            ("scan_shop", ["config.json"]))
        self.assertEqual(packaging_entry.resolve_command(["app.exe", "--selftest"]),
                         ("selftest", []))
        self.assertEqual(
            packaging_entry.resolve_command(["app.exe", "--check-update"]),
            ("check_update", []))


class RelativePathTest(unittest.TestCase):
    def test_relative_path_resolves_against_app_dir(self):
        import tempfile
        import packaging_entry

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "config.json").write_text("{}", encoding="utf-8")
            self.assertEqual(packaging_entry.resolve_relative_path("config.json", base),
                             str(base / "config.json"))

    def test_missing_and_absolute_paths_kept(self):
        import tempfile
        import packaging_entry

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            self.assertEqual(packaging_entry.resolve_relative_path("nope.json", base),
                             "nope.json")
            absolute = str(base / "config.json")
            self.assertEqual(packaging_entry.resolve_relative_path(absolute, base),
                             absolute)


class FixArgvTest(unittest.TestCase):
    def test_flag_only_argv_untouched(self):
        import packaging_entry

        self.assertEqual(packaging_entry.fix_argv(["--check"]), ["--check"])
        self.assertEqual(packaging_entry.fix_argv([]), [])

if __name__ == "__main__":
    unittest.main()

class LauncherAutoCheckTest(unittest.TestCase):
    """回归：自动检查更新只能触发一次。

    实测 2026-09-30：`after(4000, _auto_check_update)` 被误插进每 100ms 跑一次的
    `_poll_queue`，于是每 4 秒就发起一次检查 —— 按钮在「检查更新 / 检查中...」之间
    狂闪，还会不停弹更新窗（点「否」也关不完）。
    """

    def test_auto_check_scheduled_only_once_in_source(self):
        from pathlib import Path

        source = (Path(__file__).resolve().parents[1] / "launcher.py").read_text(
            encoding="utf-8")
        self.assertEqual(
            source.count("after(4000, self._auto_check_update)"), 1,
            "自动检查更新的定时器只能排一次（曾因为多排导致按钮狂闪）")

    def test_auto_check_runs_single_time(self):
        import types

        import launcher

        calls = []
        fake = types.SimpleNamespace(
            _auto_checked=False,
            _check_update=lambda silent=False: calls.append(silent))
        with patch.object(launcher.app_paths, "is_frozen", return_value=True):
            launcher.LauncherApp._auto_check_update(fake)
            launcher.LauncherApp._auto_check_update(fake)
            launcher.LauncherApp._auto_check_update(fake)
        self.assertEqual(calls, [True])
