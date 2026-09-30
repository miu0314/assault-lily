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


if __name__ == "__main__":
    unittest.main()
