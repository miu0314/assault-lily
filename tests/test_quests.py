# -*- coding: utf-8 -*-
"""外征任务战斗等待回归测试（2026-09-17：战斗没开起来时空等 21 分钟）。"""

import unittest
from unittest.mock import patch

import numpy as np

from tasks.quests import ClearLegionGekiha, battle_screen_still


class _Logger:
    def __init__(self):
        self.lines = []

    def info(self, message, *args):
        self.lines.append(("INFO", str(message)))

    def warn(self, message, *args):
        self.lines.append(("WARN", str(message)))

    def joined(self):
        return " | ".join(message for _lv, message in self.lines)


class _Ctx:
    def __init__(self, screens):
        self.screens = screens
        self.index = 0
        self._last_screen = screens[0]
        self.logger = _Logger()
        self.saved = []
        self.shots = 0

    def screenshot(self, *a, **k):
        self._last_screen = self.screens[min(self.index, len(self.screens) - 1)]
        self.index += 1
        self.shots += 1
        return self._last_screen

    def save_screenshot(self, img, name=None):
        self.saved.append(name)
        return f"screenshots/{name}"


def _frame(value):
    return np.full((720, 1280, 3), value, np.uint8)


class BattleScreenStillTest(unittest.TestCase):
    def test_identical_frames_are_still(self):
        self.assertTrue(battle_screen_still(_frame(120), _frame(120)))

    def test_different_frames_are_not_still(self):
        self.assertFalse(battle_screen_still(_frame(120), _frame(20)))

    def test_none_is_not_still(self):
        self.assertFalse(battle_screen_still(None, _frame(120)))


class WaitBattleResultTest(unittest.TestCase):
    """`_wait_battle_result`：等战斗结算，画面长时间不动就提前放弃。"""

    def _task(self):
        task = ClearLegionGekiha()
        task._dismiss_error_popup = lambda ctx: False
        task._find_result_ok = lambda ctx: None
        task._detect_and_handle_failure = lambda ctx: False
        task._is_in_battle = lambda ctx: False
        return task

    def test_gives_up_early_when_screen_frozen(self):
        """战斗没开起来、画面一直不动时要提前放弃，不能空等 20 分钟。

        实测 2026-09-16：`11:05:42 未识别到战斗开始按钮` → `11:26:30 等待战斗结算超时`，
        中间 21 分钟画面毫无变化，纯白等。
        """
        ctx = _Ctx([_frame(150)] * 60)
        task = self._task()

        with patch("tasks.quests.time.sleep"), \
                patch("tasks.quests.handle_download_popup", return_value=False):
            ok = task._wait_battle_result(ctx)

        self.assertFalse(ok)
        self.assertTrue(ctx.saved)                  # 存了现场截图
        self.assertLess(ctx.shots, 20)              # 没有跑满 60 轮
        self.assertIn("没变化", ctx.logger.joined())

    def test_keeps_waiting_while_battle_is_running(self):
        """战斗画面在动（真的在打）时要继续等，结算出现就返回 True。"""
        screens = [_frame(150 + i * 7) for i in range(4)] + [_frame(255)] * 10
        ctx = _Ctx(screens)
        task = self._task()
        task._is_in_battle = lambda ctx: True
        task._find_result_ok = lambda ctx: (640, 360) if ctx.shots >= 4 else None

        with patch("tasks.quests.time.sleep"), \
                patch("tasks.quests.handle_download_popup", return_value=False):
            ok = task._wait_battle_result(ctx)

        self.assertTrue(ok)
        self.assertIn("战斗结算出现", ctx.logger.joined())

    def test_still_counter_resets_while_in_battle(self):
        """只要还在战斗画面里，静止计数就要清零（别把正常战斗误判成卡住）。"""
        ctx = _Ctx([_frame(150)] * 40)
        task = self._task()
        task._is_in_battle = lambda ctx: True          # 一直是战斗画面（静止但没卡）
        task._find_result_ok = lambda ctx: (640, 360) if ctx.shots >= 30 else None

        with patch("tasks.quests.time.sleep"), \
                patch("tasks.quests.handle_download_popup", return_value=False):
            ok = task._wait_battle_result(ctx)

        self.assertTrue(ok)
        self.assertEqual(ctx.saved, [])                # 没有误判成卡住


if __name__ == "__main__":
    unittest.main()
