# -*- coding: utf-8 -*-
"""聊天发言：进游戏聊天频道，用定型文发一条消息。

流程：首页 → 右上角聊天键 → 选频道（レギオン / グループ / 個人）
      → 点左下「定型文」→ 翻到目标页 → 点目标定型文（点一下即发送）
      → 关掉聊天回首页

配置：
  chat_message_tab   发言频道，默认 レギオン
  chat_message_text  发言内容（必须落在游戏定型文里），默认 ごきげんよう

说明：游戏内输入日文走系统输入法，脚本没法可靠地敲任意文本，所以这里用
游戏自带的定型文（3 页 × 6 条）发言，点一下就直接发出去。
"""

import time

import cv2

from core.navigation import click_home_button, ensure_home
from core.pages import is_page
from core.popups import close_content_popup, handle_download_popup
from core.task import Task
from tasks.chat_phrases import PHRASE_PAGES

TPL_CHAT = "chat/btn_chat.png"        # 首页右上角聊天键
TPL_TITLE = "chat/title.png"          # 聊天页标题「チャット」
TPL_PHRASE = "chat/btn_phrase.png"    # 左下「定型文」按钮（聊天室开着才有）
TPL_CLOSE = "chat/btn_close.png"      # 右上角关闭聊天

MATCH = 0.9

# 聊天键在右上角，只在附近接受匹配，避免点到旁边的房子/菜单键
CHAT_BUTTON_REGION = (1080, 5, 1160, 70)
# 顶部频道标签
TAB_COORDS = {"レギオン": (443, 32), "グループ": (571, 32), "個人": (718, 32)}
# 聊天室列表第一行（グループ/個人 要先点开一个房间）
ROOM_FIRST_ROW = (150, 89)

# 每条定型文的位置（和 tasks/chat_phrases.py 里的页序一一对应）
PHRASE_CELLS = [(456, 334), (821, 334), (456, 432), (821, 432), (456, 530), (821, 530)]
# 面板下方页码点：亮的那个就是当前页（面板没开时这里是白的，三个点都识别不出）
PAGE_DOTS = ((614, 604), (640, 604), (665, 604))

# 定型文面板翻页手势（面板会记住上次页码，所以每次先拨回第 1 页）
SWIPE_Y = 430
SWIPE_X_FAR = 980
SWIPE_X_NEAR = 300
SWIPE_MS = 1200


class SendChatMessage(Task):
    """聊天发言（用游戏自带定型文）。"""

    def __init__(self):
        super().__init__(name="发送消息", pre_times=1, post_times=4)

    def pre_condition(self, ctx):
        return True

    def post_condition(self, ctx):
        return True

    # ---------- 主流程 ----------
    def on_run(self, ctx):
        tab = str(ctx.config.get("chat_message_tab", "レギオン") or "レギオン").strip()
        text = str(ctx.config.get("chat_message_text", "ごきげんよう") or "").strip()
        if tab not in TAB_COORDS:
            ctx.logger.warn(f"未知的发言频道 {tab!r}，改用 レギオン")
            tab = "レギオン"
        if not text:
            return self.skip("没有配置发言内容", ctx)
        position = self.phrase_position(text)
        if position is None:
            return self.fail(
                f"定型文里没有「{text}」，无法发送（只能发游戏自带定型文）", ctx)
        page, cell = position

        if not self._enter_chat(ctx):
            return self.fail("未能打开聊天窗口", ctx)
        if not self._select_tab(ctx, tab):
            self._close_chat(ctx)
            return self.skip(f"「{tab}」里没有可以发言的房间", ctx)
        if not self._open_phrase_panel(ctx):
            self._close_chat(ctx)
            return self.fail("未能打开定型文面板", ctx)
        if self._goto_page(ctx, page) != page:
            self._close_chat(ctx)
            return self.fail(f"定型文没有翻到第 {page} 页", ctx)
        if not self._tap_phrase(ctx, cell):
            self._close_chat(ctx)
            return self.fail(
                f"点了「{text}」但定型文面板没有关闭，消息可能没发出去", ctx)

        ctx.logger.info(f"已在「{tab}」发送定型文：{text}")
        if not self._close_chat(ctx):
            ctx.logger.warn("聊天窗口没能自动关闭，请手动回首页")
        return True

    # ---------- 定型文表 ----------
    @staticmethod
    def phrase_position(text):
        """按定型文内容返回 (页码, 格子坐标)；找不到返回 None。"""
        target = str(text).strip()
        for page_index, phrases in enumerate(PHRASE_PAGES):
            for cell_index, phrase in enumerate(phrases):
                if phrase == target:
                    return page_index + 1, PHRASE_CELLS[cell_index]
        return None

    # ---------- 进入 / 退出聊天 ----------
    def _in_chat(self, ctx):
        return ctx.find(TPL_TITLE, threshold=MATCH) is not None

    def _click_chat_button(self, ctx):
        result = ctx.find(TPL_CHAT, threshold=MATCH)
        if result is None:
            return False
        (cx, cy), score = result
        if not (CHAT_BUTTON_REGION[0] <= cx <= CHAT_BUTTON_REGION[2]
                and CHAT_BUTTON_REGION[1] <= cy <= CHAT_BUTTON_REGION[3]):
            return False
        ctx.logger.info(f"点击右上角聊天键 ({cx},{cy})，匹配 {score:.2f}")
        ctx.click(cx, cy, sleeptime=4)
        return True

    def _enter_chat(self, ctx):
        """进聊天页：已经开着就直接用，否则从当前界面/首页点聊天键进去。"""
        for attempt in range(8):
            ctx.screenshot()
            if self._in_chat(ctx):
                if attempt == 0:
                    ctx.logger.info("聊天窗口已经开着，直接用")
                return True
            if close_content_popup(ctx) or handle_download_popup(ctx):
                continue
            if self._click_chat_button(ctx):
                continue
            if click_home_button(ctx):
                continue
            if attempt == 3:
                # 既不在首页也没有聊天键：退回通用「回首页」兜底一次
                ensure_home(ctx)
                continue
            time.sleep(2)
        return self._in_chat(ctx)

    def _close_chat(self, ctx):
        for _ in range(6):
            ctx.screenshot()
            if is_page(ctx, "home"):
                return True
            if ctx.click_template(TPL_CLOSE, threshold=MATCH, sleeptime=4):
                continue
            if click_home_button(ctx):
                continue
            time.sleep(2)
        return is_page(ctx, "home")

    # ---------- 频道 ----------
    def _select_tab(self, ctx, tab):
        """切到目标频道；グループ/個人 还要在左边列表点开一个房间。

        返回聊天室是否可用（能用时左下角会出现「定型文」按钮）。
        """
        x, y = TAB_COORDS[tab]
        ctx.click(x, y, sleeptime=2.5)
        for _ in range(6):
            ctx.screenshot()
            if ctx.find(TPL_PHRASE, threshold=MATCH) is not None:
                return True
            if tab == "レギオン":
                time.sleep(2)
                continue
            ctx.click(ROOM_FIRST_ROW[0], ROOM_FIRST_ROW[1], sleeptime=2)
        return ctx.find(TPL_PHRASE, threshold=MATCH) is not None

    # ---------- 定型文面板 ----------
    def _open_phrase_panel(self, ctx):
        for _ in range(6):
            if self._read_panel_page(ctx) is not None:
                return True
            if not ctx.click_template(TPL_PHRASE, threshold=MATCH, sleeptime=1.5):
                time.sleep(1.5)
        return self._read_panel_page(ctx) is not None

    def _read_panel_page(self, ctx):
        """读定型文面板当前页码：亮的那个点就是当前页；面板没开返回 None。"""
        img = ctx.screenshot()
        if img is None:
            return None
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        active = []
        for index, (cx, cy) in enumerate(PAGE_DOTS, start=1):
            patch = hsv[cy - 2:cy + 3, cx - 2:cx + 3].reshape(-1, 3)
            saturation = float(patch[:, 1].mean())
            value = float(patch[:, 2].mean())
            if saturation >= 100 and value >= 180:
                active.append(index)
        if len(active) != 1:
            return None
        return active[0]

    def _swipe_page(self, ctx, step):
        """step<0 翻下一页（手指左移），step>0 翻上一页。"""
        if step < 0:
            ctx.device.swipe(SWIPE_X_FAR, SWIPE_Y, SWIPE_X_NEAR, SWIPE_Y, SWIPE_MS)
        else:
            ctx.device.swipe(SWIPE_X_NEAR, SWIPE_Y, SWIPE_X_FAR, SWIPE_Y, SWIPE_MS)
        time.sleep(1.2)

    def _goto_page(self, ctx, target):
        """把面板翻到目标页；返回最终页码（不在面板上返回 None）。"""
        page = self._read_panel_page(ctx)
        for _ in range(4):
            if page is None or page == target:
                return page
            self._swipe_page(ctx, -1 if page < target else 1)
            page = self._read_panel_page(ctx)
        return page

    def _tap_phrase(self, ctx, cell):
        """点定型文（点一下即发送）；发送后面板会自动关掉。"""
        ctx.click(cell[0], cell[1], sleeptime=2.5)
        return self._read_panel_page(ctx) is None
