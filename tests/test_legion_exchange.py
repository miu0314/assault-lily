# -*- coding: utf-8 -*-
"""军团兑换页面判定加固：传奇战斗页的「メダル交換所」不能当成军团交换所。

2026-10-06 实机踩到：游戏停在传奇战斗主页时跑「军团兑换」，`_is_legion_page`
因为页面上有「メダル交換所」而判成军团页，随后固定坐标 (932,627) 落在
「グレードD バトルに挑む」上，直接进了编队页。
"""

import unittest
from unittest.mock import patch

from tasks.legion_exchange import ExchangeLegionItems


class _Ctx:
    def __init__(self):
        self._last_screen = object()


def _texts(*values):
    return [(v, 100, 100, 0.9) for v in values]


class IsLegionPageTest(unittest.TestCase):
    def _check(self, texts, expected):
        with patch("tasks.legion_exchange.read_text", return_value=list(texts)):
            self.assertEqual(ExchangeLegionItems._is_legion_page(_Ctx()), expected)

    def test_legendary_battle_page_is_not_legion_page(self):
        # 传奇战斗主页：标题 + メダル交換所 —— 就是这个组合骗过了旧判定
        self._check(_texts("レジェンダリーバトル", "ランキング", "メダル交換所"), False)

    def test_legendary_unit_select_is_not_legion_page(self):
        self._check(_texts("ユニット選択", "レジェンダリーバトル"), False)

    def test_legion_home_still_detected(self):
        self._check(_texts("レギオン", "外征任務", "寄付・恩恵"), True)

    def test_bare_exchange_label_still_detected(self):
        # 军团页 OCR 读不到「レギオン」时，靠「交換所」兜底的老行为保留
        self._check(_texts("交換所", "ミッション"), True)


if __name__ == "__main__":
    unittest.main()
