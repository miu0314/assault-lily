# -*- coding: utf-8 -*-
"""免费扭蛋的安全回归测试。

2026-10-04 事故：这个任务真的花了用户的宝石（2 万多）。
根因是判定「免费」的方式：

    if "0個" in joined or "0消費" in joined:   # 旧代码

游戏付费确认弹窗写的是「マギジュエルを 2,500個 消費します」——
`"2,500個"` 里含有子串 `0個`，于是付费确认被当成免费确认，
接着 `_click_ok` 点了 OK，真扣宝石。

所以这里把「消费金额」当成必须严格解析的数字：只有正好 0 才算免费，
大于 0 一律按“要花钱”处理并取消；读不出来就什么都不点。
"""

import unittest
from unittest.mock import patch

from tasks.gacha import CollectFreeGacha, dialog_cost


def _fake_read_text(ctx):
    """把 gacha 模块里的 read_text 换成返回 ctx.texts 的桩。"""
    return patch("tasks.gacha.read_text", side_effect=lambda img: ctx.texts)


class _Logger:
    def __init__(self):
        self.lines = []

    def info(self, message, *args):
        self.lines.append(("INFO", str(message)))

    def warn(self, message, *args):
        self.lines.append(("WARN", str(message)))

    def joined(self):
        return " | ".join(m for _lv, m in self.lines)


class _Dev:
    def __init__(self):
        self.keys = []

    def key(self, code):
        self.keys.append(code)


class _Ctx:
    """最小上下文：给什么 OCR 文本就返回什么，模板命中表可控。"""

    def __init__(self, texts=(), finds=None):
        import numpy as np

        self.logger = _Logger()
        self._last_screen = np.zeros((720, 1280, 3), np.uint8)
        self.texts = list(texts)
        self.finds = dict(finds or {})
        self.clicks = []
        self.saved = []
        self.clicked_templates = []
        self.device = _Dev()

    def screenshot(self, *a, **k):
        return self._last_screen

    def find(self, name, threshold=None):
        return self.finds.get(name)

    def find_red_dots(self, *a, **k):
        return []

    def click(self, x, y, sleeptime=1.0):
        self.clicks.append((x, y))

    def click_template(self, name, threshold=None, sleeptime=1.5):
        self.clicked_templates.append(name)
        return False

    def save_screenshot(self, img, name=None):
        self.saved.append(name)
        return f"screenshots/{name}"


class DialogCostTest(unittest.TestCase):
    def test_paid_amount_parses_as_non_zero(self):
        self.assertEqual(
            dialog_cost([("マギジュエルを2,500個消費します。", 640, 300, 0.95)]),
            2500)

    def test_free_amount_parses_as_zero(self):
        self.assertEqual(
            dialog_cost([("マギジュエルを0個消費します。", 640, 300, 0.95)]), 0)

    def test_full_width_digits_are_normalized(self):
        """日文界面常用全角数字。"""
        self.assertEqual(
            dialog_cost([("マギジュエルを２，５００個消費します。", 640, 300, 0.95)]),
            2500)

    def test_unreadable_returns_none(self):
        self.assertIsNone(dialog_cost([("よろしいですか？", 640, 300, 0.5)]))

    def test_owned_count_keeps_it_strict(self):
        """弹窗里同时出现所持数和消费数时，宁可按“要花钱”处理。"""
        self.assertEqual(
            dialog_cost([("所持 50,800個", 400, 250, 0.9),
                         ("消費 0個", 640, 300, 0.9)]), 50800)


class ZeroConsumeRegressionTest(unittest.TestCase):
    """复现 2026-10-04 那次事故的判定路径。"""

    def test_substring_bug_is_real(self):
        self.assertIn("0個", "2,500個")      # 旧判定的坑：付费金额里含 "0個"

    def test_paid_dialog_is_paid(self):
        ctx = _Ctx(texts=[("マギジュエルを2,500個消費します。", 640, 300, 0.95)])
        with _fake_read_text(ctx):
            self.assertEqual(CollectFreeGacha._confirm_state(ctx), "paid")

    def test_zero_dialog_is_free(self):
        ctx = _Ctx(texts=[("マギジュエルを0個消費します。", 640, 300, 0.95)])
        with _fake_read_text(ctx):
            self.assertEqual(CollectFreeGacha._confirm_state(ctx), "free")

    def test_template_only_when_amount_unreadable(self):
        """金额读不出来时，才退回「0個」那一小段模板当证据。"""
        ctx = _Ctx(texts=[("よろしいですか？", 640, 300, 0.5)],
                   finds={"gacha/confirm_zero_consume.png": ((640, 300), 0.95)})
        with _fake_read_text(ctx):
            self.assertEqual(CollectFreeGacha._confirm_state(ctx), "free")

        ctx2 = _Ctx(texts=[("よろしいですか？", 640, 300, 0.5)])
        with _fake_read_text(ctx2):
            self.assertIsNone(CollectFreeGacha._confirm_state(ctx2))


class FreeButtonCostTest(unittest.TestCase):
    """模板只用来定位：真正的判据是抽卡按钮上的消费数字等于 0。

    实测 2026-10-04：`btn_free_one.png` 在阈值 0.6 下命中了付费的
    「11回ガチャ 1,500」按钮（(1137,638)），于是脚本去点了付费入口。
    """

    def test_paid_button_is_rejected_even_if_template_matches(self):
        ctx = _Ctx(texts=[("11回ガチャ", 1160, 632, 0.95),
                          ("1,500", 1213, 661, 0.95)],
                   finds={"gacha/btn_free_one.png": ((1137, 638), 0.72)})
        with _fake_read_text(ctx):
            self.assertIsNone(CollectFreeGacha._free_button_on_screen(ctx))

    def test_single_paid_button_is_rejected(self):
        ctx = _Ctx(texts=[("1回ガチャ", 924, 632, 0.95),
                          ("150", 998, 661, 0.95)],
                   finds={"gacha/btn_free_one.png": ((950, 630), 0.95)})
        with _fake_read_text(ctx):
            self.assertIsNone(CollectFreeGacha._free_button_on_screen(ctx))

    def test_free_button_with_zero_cost_is_accepted(self):
        ctx = _Ctx(texts=[("1回ガチャ", 924, 632, 0.95),
                          ("0", 998, 661, 0.95)],
                   finds={"gacha/btn_free_one.png": ((950, 630), 0.95)})
        with _fake_read_text(ctx):
            self.assertEqual(CollectFreeGacha._free_button_on_screen(ctx),
                             (950, 630))

    def test_unreadable_cost_is_rejected(self):
        """读不出消费数字时按“不可免费”处理（宁可漏抽，也不能花钱）。"""
        ctx = _Ctx(texts=[("1回ガチャ", 924, 632, 0.95)],
                   finds={"gacha/btn_free_one.png": ((950, 630), 0.95)})
        with _fake_read_text(ctx):
            self.assertIsNone(CollectFreeGacha._free_button_on_screen(ctx))

    def test_free_single_is_recognized_next_to_paid_eleven(self):
        """同一行里「1回 0（免费）」+「11回 1,500（付费）」并存时，只认免费那个。"""
        ctx = _Ctx(texts=[("1回ガチャ", 924, 632, 0.95),
                          ("0", 998, 661, 0.95),
                          ("11回ガチャ", 1160, 632, 0.95),
                          ("1,500", 1213, 661, 0.95)],
                   finds={"gacha/btn_free_one.png": ((950, 630), 0.95)})
        with _fake_read_text(ctx):
            self.assertEqual(CollectFreeGacha._free_button_on_screen(ctx),
                             (950, 630))

    def test_eleven_button_still_rejected_when_free_single_present(self):
        """反过来：模板误命中付费的 11 回按钮时，不能被旁边的 0 洗白。"""
        ctx = _Ctx(texts=[("1回ガチャ", 924, 632, 0.95),
                          ("0", 998, 661, 0.95),
                          ("11回ガチャ", 1160, 632, 0.95),
                          ("1,500", 1213, 661, 0.95)],
                   finds={"gacha/btn_free_one.png": ((1137, 638), 0.95)})
        with _fake_read_text(ctx):
            self.assertIsNone(CollectFreeGacha._free_button_on_screen(ctx))


class ConfirmAndPullTest(unittest.TestCase):
    def test_paid_dialog_is_cancelled_without_clicking_ok(self):
        ctx = _Ctx(texts=[("マギジュエルを2,500個消費します。", 640, 300, 0.95),
                          ("キャンセル", 520, 640, 0.95),
                          ("OK", 760, 640, 0.95)])
        task = CollectFreeGacha()

        with _fake_read_text(ctx):
            ok = task._confirm_and_pull(ctx)

        self.assertFalse(ok)
        self.assertTrue(task.paid_detected)
        self.assertIn((520, 640), ctx.clicks)        # 点了「キャンセル」
        self.assertNotIn((760, 640), ctx.clicks)     # 绝对没点 OK
        self.assertIn("gacha_paid_dialog.png", ctx.saved)

    def test_no_dialog_clicks_nothing(self):
        """弹窗读不出、模板也没命中时，一个按钮都不能点。

        旧代码在这条路上会去点 gacha/btn_pull_one.png（付费 1 回按钮）。
        """
        ctx = _Ctx(texts=[("よろしいですか？", 640, 300, 0.5)])
        task = CollectFreeGacha()

        with _fake_read_text(ctx):
            self.assertFalse(task._confirm_and_pull(ctx))
        self.assertEqual(ctx.clicks, [])
        self.assertEqual(ctx.clicked_templates, [])


if __name__ == "__main__":
    unittest.main()
