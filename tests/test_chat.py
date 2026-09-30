# -*- coding: utf-8 -*-
"""聊天发言任务回归测试。"""

import unittest
from unittest.mock import patch

from tasks.chat import SendChatMessage


class _Logger:
    def __init__(self):
        self.warns = []
        self.infos = []

    def info(self, message, *args):
        self.infos.append(str(message))

    def warn(self, message, *args):
        self.warns.append(str(message))


class _Config(dict):
    def get_bool(self, key, default=False):
        return bool(self.get(key, default))

    def get_int(self, key, default=0):
        try:
            return int(self.get(key, default))
        except (TypeError, ValueError):
            return default


class _Context:
    def __init__(self, config=None):
        self.config = _Config(config or {})
        self.logger = _Logger()


class _ChatProbe(SendChatMessage):
    """替掉视觉识别，用一个状态机模拟聊天室和定型文面板。"""

    def __init__(self, panel_page=1, room_open=True, phrase_sent=True):
        super().__init__()
        self.panel_open = False
        self.panel_page = panel_page
        self.room_open = room_open
        self.phrase_sent = phrase_sent
        self.opened_chat = 0
        self.tab = None
        self.swipes = []          # -1=翻下一页，1=翻上一页
        self.tapped_cells = []
        self.closed_chat = 0

    def _enter_chat(self, ctx):
        self.opened_chat += 1
        return True

    def _close_chat(self, ctx):
        self.closed_chat += 1
        return True

    def _select_tab(self, ctx, tab):
        self.tab = tab
        return self.room_open

    def _open_phrase_panel(self, ctx):
        self.panel_open = True
        return True

    def _read_panel_page(self, ctx):
        return self.panel_page if self.panel_open else None

    def _swipe_page(self, ctx, step):
        self.swipes.append(step)
        self.panel_page = max(1, min(3, self.panel_page - step))

    def _tap_phrase(self, ctx, cell):
        self.tapped_cells.append(cell)
        if not self.phrase_sent:
            return False
        self.panel_open = False
        return True


def _run(probe, config=None):
    ctx = _Context(config)
    with patch("tasks.chat.ensure_home", return_value=True):
        probe.run(ctx)
    return ctx


class ChatTaskTest(unittest.TestCase):
    def test_position_lookup(self):
        self.assertEqual(SendChatMessage.phrase_position("ごきげんよう"), (1, (456, 334)))
        self.assertEqual(SendChatMessage.phrase_position("了解！"), (1, (821, 530)))
        self.assertEqual(SendChatMessage.phrase_position("またね！"), (3, (456, 530)))
        self.assertEqual(SendChatMessage.phrase_position("ドンマイ！"), (2, (821, 530)))
        self.assertIsNone(SendChatMessage.phrase_position("不存在的台词"))

    def test_send_default_phrase_on_first_page(self):
        probe = _ChatProbe(panel_page=1)
        ctx = _run(probe, {"chat_message_tab": "レギオン",
                           "chat_message_text": "ごきげんよう"})
        self.assertEqual(probe.status, SendChatMessage.STATUS_SUCCESS)
        self.assertEqual(probe.tab, "レギオン")
        self.assertEqual(probe.tapped_cells, [(456, 334)])
        self.assertEqual(probe.swipes, [])          # 已经在第 1 页，不用翻
        self.assertEqual(probe.closed_chat, 1)
        self.assertEqual(ctx.logger.warns, [])

    def test_rewinds_to_first_page_before_sending(self):
        """面板会记住上次页码：从第 3 页翻回第 1 页再发。"""
        probe = _ChatProbe(panel_page=3)
        _run(probe, {"chat_message_tab": "レギオン",
                     "chat_message_text": "ごきげんよう"})
        self.assertEqual(probe.status, SendChatMessage.STATUS_SUCCESS)
        self.assertEqual(probe.swipes, [1, 1])      # 两次“上一页”
        self.assertEqual(probe.panel_page, 1)
        self.assertEqual(probe.tapped_cells, [(456, 334)])

    def test_swipes_forward_for_later_page(self):
        probe = _ChatProbe(panel_page=1)
        _run(probe, {"chat_message_tab": "レギオン",
                     "chat_message_text": "レギオンメンバー募集！"})
        self.assertEqual(probe.status, SendChatMessage.STATUS_SUCCESS)
        self.assertEqual(probe.swipes, [-1, -1])    # 两次“下一页”
        self.assertEqual(probe.tapped_cells, [(821, 432)])

    def test_unknown_phrase_fails_without_touching_game(self):
        probe = _ChatProbe()
        _run(probe, {"chat_message_tab": "レギオン", "chat_message_text": "自定义台词"})
        self.assertEqual(probe.status, SendChatMessage.STATUS_ERROR)
        self.assertIn("定型文里没有", probe.error_message)
        self.assertEqual(probe.opened_chat, 0)

    def test_empty_room_skips(self):
        probe = _ChatProbe(room_open=False)
        _run(probe, {"chat_message_tab": "グループ", "chat_message_text": "よろしく！"})
        self.assertEqual(probe.status, SendChatMessage.STATUS_SKIP)
        self.assertEqual(probe.tapped_cells, [])
        self.assertEqual(probe.closed_chat, 1)

    def test_panel_still_open_means_send_failed(self):
        probe = _ChatProbe(phrase_sent=False)
        _run(probe, {"chat_message_tab": "レギオン", "chat_message_text": "ごきげんよう"})
        self.assertEqual(probe.status, SendChatMessage.STATUS_ERROR)
        self.assertIn("没有关闭", probe.error_message)
        self.assertEqual(probe.closed_chat, 1)

    def test_unknown_tab_falls_back_to_legion(self):
        probe = _ChatProbe()
        _run(probe, {"chat_message_tab": "全員", "chat_message_text": "ごきげんよう"})
        self.assertEqual(probe.status, SendChatMessage.STATUS_SUCCESS)
        self.assertEqual(probe.tab, "レギオン")


if __name__ == "__main__":
    unittest.main()
