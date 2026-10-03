# -*- coding: utf-8 -*-
"""打包脚本的前置检查。

2026-10-03 事故：打包时 dist 里的启动器还开着，PyInstaller「先删 dist
再重建」删到被占用的 `_internal\cv2\cv2.pyd` 就 PermissionError 中止，
留下一个 exe 还在、`_internal` 被删了一半的安装 —— 用户第二天双击直接
弹「突击莉莉脚本启动失败」。所以打包前必须先确认本程序没在运行。
"""

import unittest
from unittest.mock import patch

import package


class RunningInstancesTest(unittest.TestCase):
    def test_parses_tasklist_csv(self):
        out = ('"AssaultLilyBot.exe","20660","Console","1","120,000 K"\n'
               '"chrome.exe","1234","Console","1","80,000 K"\n')
        with patch("package.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = out
            self.assertEqual(package.running_instances(), [20660])

    def test_returns_empty_when_nothing_running(self):
        with patch("package.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = "信息: 没有运行的任务匹配指定标准。\n"
            self.assertEqual(package.running_instances(), [])

    def test_returns_empty_when_tasklist_missing(self):
        with patch("package.subprocess.run", side_effect=OSError("no tasklist")):
            self.assertEqual(package.running_instances(), [])


class BuildGuardTest(unittest.TestCase):
    def test_build_aborts_without_touching_dist(self):
        """程序还在跑时必须直接收工，不能进 PyInstaller（那会删坏安装）。"""
        with patch("package.running_instances", return_value=[20660]), \
                patch("package.subprocess.run") as run:
            self.assertFalse(package.build())
        run.assert_not_called()

    def test_build_runs_when_nothing_holds_the_folder(self):
        with patch("package.running_instances", return_value=[]), \
                patch("package.subprocess.run") as run:
            self.assertTrue(package.build())
        self.assertTrue(run.called)


if __name__ == "__main__":
    unittest.main()
