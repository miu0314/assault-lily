# -*- coding: utf-8 -*-
"""回首页相关回归测试（2026-09-17：主页按钮点了没反应还一直点）。"""

import unittest
from unittest.mock import patch

import numpy as np

from core.navigation import click_home_button, ensure_home


class _Logger:
    def __init__(self):
        self.lines = []

    def info(self, message, *args):
        self.lines.append(("INFO", str(message)))

    def warn(self, message, *args):
        self.lines.append(("WARN", str(message)))

    def joined(self):
        return " | ".join(message for _lv, message in self.lines)


class _Device:
    def __init__(self):
        self.keys = []
        self.started = 0

    def key(self, code):
        self.keys.append(code)

    def start_app(self):
        self.started += 1


class _Ctx:
    """screenshot 依次返回给定画面，返回完毕后固定最后一帧。"""

    def __init__(self, screens):
        self.screens = screens
        self.index = 0
        self._last_screen = screens[0]
        self.logger = _Logger()
        self.device = _Device()
        self.clicks = []
        self.saved = []

    def screenshot(self, *a, **k):
        self._last_screen = self.screens[min(self.index, len(self.screens) - 1)]
        self.index += 1
        return self._last_screen

    def find(self, template_name, threshold=None):
        if template_name == "home/btn_home_quest.png":
            return ((1190, 36), 0.95)
        return None

    def click(self, x, y, sleeptime=1.0):
        self.clicks.append((x, y))

    def save_screenshot(self, img, name=None):
        self.saved.append(name)
        return f"screenshots/{name}"


def _frame(value):
    return np.full((720, 1280, 3), value, np.uint8)


class ClickHomeButtonTest(unittest.TestCase):
    def test_returns_false_when_screen_does_not_change(self):
        """点了主页按钮但画面没变化 = 这次点击没用，要返回 False 让调用方改用返回键。

        实测 2026-09-17：弹窗挡住时点房子完全没反应，旧实现只要模板命中就返回 True，
        调用方（`_back_home` / `ensure_home`）就一直点它 —— 白点 8 次（约 2 分钟）
        才走重启分支。
        """
        ctx = _Ctx([_frame(200)] * 4)          # 前后画面一模一样

        with patch("core.navigation.time.sleep"):
            ctx.screenshot()
            moved = click_home_button(ctx)

        self.assertFalse(moved)
        self.assertEqual(ctx.clicks, [(1190, 36)])   # 还是点了一下，但结果如实上报

    def test_returns_true_when_screen_changes(self):
        ctx = _Ctx([_frame(200), _frame(60)])   # 点完画面明显变了

        with patch("core.navigation.time.sleep"):
            ctx.screenshot()
            moved = click_home_button(ctx)

        self.assertTrue(moved)


class EnsureHomeTest(unittest.TestCase):
    def test_ensure_home_dismisses_ok_dialog_before_home_button(self):
        """通用 OK 弹窗要先点掉（`_back_home`/`ensure_home` 以前都不处理它）。"""
        ctx = _Ctx([_frame(200)] * 4)
        order = []
        state = {"ok": 0}

        def fake_ok(c, **kwargs):
            state["ok"] += 1
            order.append("ok")
            return state["ok"] == 1        # 第一次点到 OK，之后就没有弹窗了

        with patch("core.pages.is_page", return_value=False), \
                patch("core.navigation.click_ok_by_ocr", side_effect=fake_ok), \
                patch("core.navigation.click_home_button",
                      side_effect=lambda c: order.append("home") or False), \
                patch("core.navigation.close_chat_if_open", return_value=False), \
                patch("core.popups.close_content_popup", return_value=False), \
                patch("core.popups.handle_download_popup", return_value=False), \
                patch("core.popups.handle_network_error", return_value=False), \
                patch("core.navigation.time.sleep"):
            ensure_home(ctx)

        self.assertIn("ok", order)
        self.assertIn("home", order)
        self.assertLess(order.index("ok"), order.index("home"))

    def test_ensure_home_falls_back_to_back_key(self):
        """主页按钮点了没用时要改用返回键，而不是一直点。"""
        ctx = _Ctx([_frame(200)] * 10)

        with patch("core.pages.is_page", return_value=False), \
                patch("core.navigation.click_ok_by_ocr", return_value=False), \
                patch("core.navigation.click_home_button", return_value=False), \
                patch("core.navigation.close_chat_if_open", return_value=False), \
                patch("core.popups.close_content_popup", return_value=False), \
                patch("core.popups.handle_download_popup", return_value=False), \
                patch("core.popups.handle_network_error", return_value=False), \
                patch("core.navigation.time.sleep"):
            ensure_home(ctx)

        self.assertIn("BACK", ctx.device.keys)


if __name__ == "__main__":
    unittest.main()
