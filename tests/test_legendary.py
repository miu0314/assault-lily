# -*- coding: utf-8 -*-
"""传奇战斗扫荡弹窗：数量读取与上限处理（2026-10-06 实机记录）。

实机事实（见 screenshots/lg_sweep_dialog_before.png / lg_sweep_dialog.png）：

- 弹窗默认数量 0，每点一次 + 加 1；
- 上限是「当前 BP」：BP 4/5 时配 5 次，+ 点到 4 就封顶（再多点不动）；
- 每扫荡 1 次消耗 1 张跳过券 + 1 BP（12,117→12,113，BP 4/5→0/5）。

旧代码点完 + 就打印「扫荡次数已设为 5」，和弹窗里的 4 不符 —— 这组测试
锁定按真实差值读数的行为。
"""

import unittest
from unittest.mock import patch

from tasks.legendary import LegendaryBattle, _selected_from_pairs


def _t(text, cx=640, cy=400):
    return (text, cx, cy, 0.9)


# 实机 OCR 片段（点 + 之前：数量 0）
BEFORE = [
    _t("スキップチケット", 445, 147),
    _t("12,117", 460, 536),
    _t("12,117", 640, 536),
    _t("BP:", 741, 537),
    _t("4/5", 900, 537),
    _t("4/5", 1063, 537),
    _t("OK", 757, 651),
]

# 实机 OCR 片段（点 5 次 + 之后：实际 4；券 12,117▸12,113、BP 4/5▸0/5）
AFTER = [
    _t("4枚、B4消4？", 640, 147),
    _t("12,117", 460, 536),
    _t("12,113", 640, 536),
    _t("BP:", 741, 537),
    _t("4/5", 900, 537),
    _t("0/5", 1063, 537),
    _t("OK", 757, 651),
]


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
    def __init__(self):
        self._last_screen = "screen"
        self.logger = _Logger()
        self.clicks = []
        self.saved = []

    def screenshot(self, *args, **kwargs):
        return self._last_screen

    def save_screenshot(self, img, name=None):
        self.saved.append(name)
        return f"screenshots/{name}"

    def click(self, x, y, sleeptime=0):
        self.clicks.append((x, y))


class SelectedCountTest(unittest.TestCase):
    """_selected_from_pairs：从「消费前 ▸ 消费后」两组数字的差值读数。"""

    def test_real_before_is_zero(self):
        self.assertEqual(_selected_from_pairs(BEFORE), 0)

    def test_real_after_is_four(self):
        self.assertEqual(_selected_from_pairs(AFTER), 4)

    def test_without_pairs_returns_none(self):
        self.assertIsNone(_selected_from_pairs([_t("OK", 757, 651)]))


class SweepCountTest(unittest.TestCase):
    """_sweep：点 + 之后要按弹窗真实数量汇报/决策。"""

    def _run(self, after_texts, count=5):
        task = LegendaryBattle()
        ctx = _Ctx()
        calls = {"n": 0}
        results = [BEFORE, BEFORE, after_texts]

        def fake_read_text(_img):
            idx = min(calls["n"], len(results) - 1)
            calls["n"] += 1
            return results[idx]

        with patch("tasks.legendary.read_text", side_effect=fake_read_text), \
                patch("tasks.legendary._click_in", return_value=True), \
                patch("tasks.legendary.time.sleep", return_value=None), \
                patch.object(LegendaryBattle, "_wait_battle_finish",
                             return_value=True):
            result = task._sweep(ctx, count)
        return ctx, result

    def test_capped_count_is_reported_and_sweep_continues(self):
        # 配置 5、BP 只够 4：日志要报「实际 4」，并且照常点 OK 扫 4 次
        ctx, result = self._run(AFTER, count=5)
        self.assertTrue(result)
        self.assertIn("扫荡弹窗实际数量 4", ctx.logger.joined())
        self.assertIn("被上限卡住", ctx.logger.joined())
        self.assertEqual(ctx.clicks, [(757, 651)])
        self.assertIn("lg_sweep_dialog.png", ctx.saved)

    def test_zero_count_cancels_without_clicking_ok(self):
        # 数量仍是 0（BP/券不足）：不许点 OK，直接取消
        ctx, result = self._run(BEFORE, count=5)
        self.assertFalse(result)
        self.assertIn("取消本次扫荡", ctx.logger.joined())
        self.assertEqual(ctx.clicks, [])

    def test_exact_count_logs_plainly(self):
        ctx, result = self._run(AFTER, count=4)
        self.assertTrue(result)
        self.assertIn("扫荡次数已设为 4", ctx.logger.joined())
        self.assertNotIn("被上限卡住", ctx.logger.joined())


if __name__ == "__main__":
    unittest.main()
