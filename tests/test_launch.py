# -*- coding: utf-8 -*-
"""启动进首页流程回归测试（2026-09-16 启动卡死修复）。

实测背景：更新后启动阶段会长时间停在加载画面，旧脚本认不出来 →
每秒盲点屏幕 15 分钟 → 超时 → 重启游戏把加载打断 → 再来一遍，循环掉两个多小时。
"""

import unittest
from unittest.mock import patch

from tasks.launch import WaitForHome, is_loading


class _Logger:
    def __init__(self):
        self.lines = []

    def info(self, message, *args):
        self.lines.append(("INFO", str(message)))

    def warn(self, message, *args):
        self.lines.append(("WARN", str(message)))

    def error(self, message, *args):
        self.lines.append(("ERROR", str(message)))

    def joined(self):
        return " | ".join(m for _lv, m in self.lines)


class _Config:
    def __init__(self, data=None):
        self.data = {"mumu_accel_action": "no_accel"}
        self.data.update(data or {})

    def get(self, key, default=None):
        return self.data.get(key, default)

    def get_int(self, key, default=0):
        return int(self.data.get(key, default))


class _Screen:
    """一帧画面：模板命中表 + OCR 文本 + 是否首页。"""

    def __init__(self, finds=None, texts=(), home=False):
        self.finds = finds or {}
        self.texts = list(texts)
        self.home = home


class _Dev:
    """设备桩：记录按键和重启调用。"""

    def __init__(self):
        self.keys = []
        self.started = 0

    def key(self, code):
        self.keys.append(code)

    def start_app(self):
        self.started += 1


class _Ctx:
    def __init__(self, screens, config=None):
        self.screens = screens
        self.config = _Config(config)
        self.logger = _Logger()
        self.device = _Dev()
        self.clicks = []
        self.saved = []
        self.index = 0
        self.current = screens[0]
        self._last_screen = None

    def screenshot(self, save=False, name=None):
        self.current = self.screens[min(self.index, len(self.screens) - 1)]
        self.index += 1
        self._last_screen = self.current
        return self.current

    def find(self, template_name, threshold=None):
        return self.current.finds.get(template_name)

    def click(self, x, y, sleeptime=1.0):
        self.clicks.append((x, y))

    def save_screenshot(self, img, name=None):
        self.saved.append(name)
        return f"screenshots/{name}"


def _run_wait(ctx, **extra):
    """跑一遍 WaitForHome，外部依赖全部换成桩。

    extra 里的键会被 patch 到 tasks.launch（值为 patch 的参数，例如
    click_home_button={"return_value": True}），返回对应的 mock。
    """
    from contextlib import ExitStack

    mocks = {}
    with ExitStack() as stack:
        stack.enter_context(patch("tasks.launch.is_page",
                                  side_effect=lambda c, page: c.current.home))
        stack.enter_context(patch("tasks.launch.read_text",
                                  side_effect=lambda img: img.texts))
        stack.enter_context(patch("tasks.launch.close_content_popup", return_value=False))
        stack.enter_context(patch("tasks.launch.handle_download_popup", return_value=False))
        stack.enter_context(patch("tasks.launch.handle_network_error", return_value=False))
        stack.enter_context(patch("tasks.launch.click_ok_by_ocr", return_value=False))
        stack.enter_context(patch("tasks.launch.time.sleep"))
        for name, kwargs in extra.items():
            mocks[name] = stack.enter_context(patch(f"tasks.launch.{name}", **kwargs))
        WaitForHome().on_run(ctx)
    return mocks


def _is_loading(ctx):
    with patch("tasks.launch.read_text", side_effect=lambda img: img.texts):
        return is_loading(ctx)


class EnsureHomeTest(unittest.TestCase):
    def test_ensure_home_does_not_restart_while_loading(self):
        """回不去首页但画面还在加载时，不能重启游戏（重启会把加载/下载打断）。

        实测 2026-09-16：每次 15 分钟超时后 ensure_home 都重启游戏，
        正在进行的加载被打断 → 下次启动又要重来，如此循环两个多小时。
        """
        from core.navigation import ensure_home

        class _Dev:
            def __init__(self):
                self.started = 0

            def start_app(self):
                self.started += 1

            def key(self, code):
                pass

        class _C:
            def __init__(self):
                self.logger = _Logger()
                self.device = _Dev()

            def screenshot(self, *a, **k):
                return None

            def click(self, x, y, sleeptime=1.0):
                pass

        ctx = _C()
        with patch("core.pages.is_page", return_value=False), \
                patch("core.navigation.click_ok_by_ocr", return_value=False), \
                patch("core.navigation.click_home_button", return_value=False), \
                patch("core.navigation.close_chat_if_open", return_value=False), \
                patch("core.popups.close_content_popup", return_value=False), \
                patch("core.popups.handle_download_popup", return_value=False), \
                patch("core.popups.handle_network_error", return_value=False), \
                patch("core.navigation.time.sleep"), \
                patch("tasks.launch.is_loading", return_value=True), \
                patch("tasks.launch.WaitForHome") as wait_home:
            ensure_home(ctx)

        self.assertEqual(ctx.device.started, 0)          # 没有重启游戏
        self.assertTrue(wait_home.return_value.run.called)
        self.assertIn("加载", ctx.logger.joined())


class LoadingDetectionTest(unittest.TestCase):
    def test_is_loading_falls_back_to_ocr(self):
        """加载画面：旧模板只匹配 0.665（阈值 0.8），必须靠 OCR 的 LOADING 认出来。

        实测 2026-09-16：加载画面是右下角一个小六边形徽章，
        `loading/loading_mark.png`（旧版大徽章）只匹配 0.665，而 OCR 稳定读出
        「LOADING」(1.00) @ (1215,636)。认不出加载 → 脚本盲点 15 分钟再重启打断加载。
        """
        ctx = _Ctx([_Screen(texts=[("LOADING", 1215, 636, 1.00)])])
        ctx.screenshot()

        self.assertTrue(_is_loading(ctx))

    def test_is_loading_true_by_template_when_ocr_misses(self):
        """OCR 读不到时（画面糊/半透明）仍可用模板兜底。"""
        ctx = _Ctx([_Screen(
            finds={"loading/loading_mark.png": ((1180, 650), 0.86)})])
        ctx.screenshot()

        self.assertTrue(_is_loading(ctx))

    def test_is_loading_false_on_home(self):
        ctx = _Ctx([_Screen(home=True, texts=[
            ("RANK", 447, 23, 0.99), ("BATTLE", 1214, 643, 0.97)])])
        ctx.screenshot()

        self.assertFalse(_is_loading(ctx))


class WaitForHomeTest(unittest.TestCase):
    def test_wait_home_handles_mumu_dialog_before_title(self):
        """MuMu 加速弹窗盖在标题画面上时，要先点「不加速」，不能一直点标题。

        实测 2026-09-16：弹窗盖住标题时右上角「サポート」仍然命中 1.00，
        旧阶段2 的标题分支永远成立 → 每 6 秒点一次 (640,360) 全点在弹窗上，
        刷屏 90 次直到超时。
        """
        mumu = _Screen(finds={
            "popup/mumu_accel_title.png": ((619, 273), 1.00),
            "popup/btn_mumu_no_accel.png": ((499, 431), 0.95),
            "home/title_support.png": ((1190, 40), 1.00),
        })
        home = _Screen(home=True)
        ctx = _Ctx([mumu, home, home, home])

        _run_wait(ctx)

        self.assertIn((499, 431), ctx.clicks)          # 点了「不加速」
        self.assertNotIn((640, 360), ctx.clicks)       # 没有对着弹窗点标题

    def test_wait_home_handles_mumu_dialog_that_survives_into_phase2(self):
        """弹窗活到阶段2 时同样要被处理掉（9/16 就是死在阶段2 的标题分支上）。

        阶段1 提前 break（画面认不出来）后，MuMu 加速弹窗才出现 —— 旧代码阶段2
        没有这个判定，标题分支永远成立，一路刷屏到超时。
        """
        unknown = _Screen(texts=[("？？？", 640, 360, 0.5)])
        mumu = _Screen(finds={
            "popup/mumu_accel_title.png": ((619, 273), 1.00),
            "popup/btn_mumu_no_accel.png": ((499, 431), 0.95),
            "home/title_support.png": ((1190, 40), 1.00),
        })
        home = _Screen(home=True)
        ctx = _Ctx([unknown, unknown, mumu, mumu, home, home],
                   config={"home_wait_rounds": 2})

        _run_wait(ctx)

        self.assertIn((499, 431), ctx.clicks)          # 阶段2 里也点了「不加速」
        self.assertNotIn((640, 360), ctx.clicks)       # 没对着弹窗点标题

    def test_wait_home_waits_while_loading_without_clicking(self):
        """加载中只等、不点击；也不该判超时去重启（重启会把加载打断）。"""
        loading = _Screen(texts=[("LOADING", 1215, 636, 1.00)])
        home = _Screen(home=True)
        ctx = _Ctx([loading, loading, loading, home, home, home],
                   config={"home_loading_budget": 300})

        _run_wait(ctx)

        self.assertEqual(ctx.clicks, [])
        self.assertIn("加载", ctx.logger.joined())

    def test_wait_home_keeps_waiting_past_round_limit_while_loading(self):
        """加载时间超过阶段2 的轮次上限时，只要还在加载就继续等，不算超时。"""
        loading = _Screen(texts=[("LOADING", 1215, 636, 1.00)])
        home = _Screen(home=True)
        ctx = _Ctx([loading] * 12 + [home, home, home],
                   config={"home_wait_rounds": 2, "home_wait_rounds2": 4,
                           "home_loading_budget": 300})

        _run_wait(ctx)

        self.assertNotIn(("WARN", "等待首页超时"), ctx.logger.lines)
        self.assertEqual(ctx.clicks, [])

    def test_wait_home_saves_screenshot_when_screen_stays_unknown(self):
        """认不出画面又连续盲点时要存截图 + 打 OCR，别再静默十几分钟。"""
        unknown = _Screen(texts=[("？？？", 640, 360, 0.5)])
        ctx = _Ctx([unknown] * 400,
                   config={"home_wait_rounds": 1, "home_wait_rounds2": 60})

        _run_wait(ctx)

        self.assertTrue(ctx.saved)
        self.assertIn("OCR", ctx.logger.joined())

    def test_wait_home_stops_clicking_after_loading_budget(self):
        """加载预算用完后要如实报“仍在加载”，而不是无限等下去。"""
        loading = _Screen(texts=[("LOADING", 1215, 636, 1.00)])
        ctx = _Ctx([loading] * 400,
                   config={"home_wait_rounds": 2, "home_wait_rounds2": 10,
                           "home_loading_budget": 45})

        _run_wait(ctx)

        self.assertIn(("WARN", "加载等待超过预算，仍未进首页"), ctx.logger.lines)
        self.assertEqual(ctx.clicks, [])


if __name__ == "__main__":
    unittest.main()

class LoadingOcrThrottleTest(unittest.TestCase):
    def test_is_loading_can_skip_ocr(self):
        """允许跳过 OCR 时（省 CPU），不能再去调 read_text。"""
        ctx = _Ctx([_Screen(texts=[("LOADING", 1215, 636, 1.00)])])
        ctx.screenshot()

        with patch("tasks.launch.read_text") as reader:
            self.assertFalse(is_loading(ctx, allow_ocr=False))
        self.assertFalse(reader.called)

        with patch("tasks.launch.read_text", side_effect=lambda img: img.texts):
            self.assertTrue(is_loading(ctx))


class HomeReturnTest(unittest.TestCase):
    """画面认不出来时，不能干等：主动点主页按钮 / 按返回键回首页。

    实测 2026-09-30：游戏停在外征关卡列表，阶段1 每轮一次全屏 OCR + 模板匹配
    再 sleep 3 秒，空转 4 分多钟、CPU 占满两个核，画面一动不动。
    """

    def _unknown_ctx(self, **config):
        unknown = _Screen(texts=[("？？？", 640, 360, 0.5)])
        data = {"home_wait_rounds": 6, "home_wait_rounds2": 2,
                "home_return_after": 2}
        data.update(config)
        return _Ctx([unknown] * 400, config=data)

    def test_unknown_screen_clicks_home_button(self):
        ctx = self._unknown_ctx()
        mocks = _run_wait(ctx, click_home_button={"return_value": True})

        self.assertTrue(mocks["click_home_button"].called)
        self.assertIn("回首页", ctx.logger.joined())

    def test_unknown_screen_presses_back_when_home_button_useless(self):
        ctx = self._unknown_ctx()
        _run_wait(ctx, click_home_button={"return_value": False})

        self.assertIn("BACK", ctx.device.keys)
        self.assertIn("回首页", ctx.logger.joined())

    def test_home_button_not_clicked_when_streak_is_short(self):
        """只是偶尔认不出来（轮次不到阈值）时别乱点，避免误触。"""
        unknown = _Screen(texts=[("？？？", 640, 360, 0.5)])
        home = _Screen(home=True)
        ctx = _Ctx([unknown, home, home, home],
                   config={"home_wait_rounds": 4, "home_return_after": 3})
        mocks = _run_wait(ctx, click_home_button={"return_value": True})

        self.assertFalse(mocks["click_home_button"].called)


class Phase2ResumeTapTest(unittest.TestCase):
    """阶段2 点掉弹窗之后必须能恢复连点，不能一路静默空转到超时。

    实测 2026-10-02：闪退恢复后的 WaitForHome 阶段2 开头点掉一个 OK 弹窗，
    之后 `clicking` 一直是 False，只剩每轮 `sleep(2)`，90 轮（约 7 分钟）
    后才报「等待首页超时」，中间日志一片空白 —— 用户看不出卡在哪。
    """

    def test_resumes_tapping_after_popup_dismissed(self):
        unknown = _Screen(texts=[("？？？", 640, 360, 0.5)])
        ctx = _Ctx([unknown] * 80,
                   config={"home_wait_rounds": 1, "home_wait_rounds2": 12})
        pops = {"n": 0}

        def _ok(c, y_min=0, y_max=720):
            pops["n"] += 1
            return pops["n"] == 1          # 只有第一轮有弹窗可点

        _run_wait(ctx, click_ok_by_ocr={"side_effect": _ok})

        self.assertIn((640, 360), ctx.clicks)   # 弹窗点掉之后恢复了连点

    def test_logs_progress_while_waiting(self):
        """认不出画面时也要定期打一行进度，别让日志空 7 分钟。"""
        unknown = _Screen(texts=[("？？？", 640, 360, 0.5)])
        ctx = _Ctx([unknown] * 80,
                   config={"home_wait_rounds": 1, "home_wait_rounds2": 12})

        _run_wait(ctx)

        self.assertIn("等待首页中", ctx.logger.joined())
