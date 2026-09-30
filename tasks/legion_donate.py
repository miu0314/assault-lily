# -*- coding: utf-8 -*-
"""军团徽章贡献：进入寄付・恩惠页，按配置次数捐献レギオンバッジ。

流程：首页 → 军团按钮 → レギオン主页 → 寄付・恩惠 → 设置次数
→ 点寄付 → 等通知消失（约1秒）→ 回首页

配置：legion_donate_count（数字或 "max"=全部）
"""

import re
import time

from core.navigation import click_home_button
from core.ocr import read_text
from core.pages import is_page
from core.popups import close_content_popup, handle_download_popup
from core.task import Task

from tasks.legion_exchange import (click_button_template, click_donate_tab,
                                   click_legion_button)

TPL_MINUS = "legion/btn_donate_minus.png"
TPL_PLUS = "legion/btn_donate_plus.png"
TPL_ALL = "legion/btn_donate_all.png"
TPL_CONFIRM = "legion/btn_donate_confirm.png"

COUNT_X = (850, 980)
COUNT_Y = (235, 290)
CTRL_X = (800, 1280)
CTRL_Y = (230, 300)
CONFIRM_X = (600, 900)
CONFIRM_Y = (600, 700)


class LegionDonate(Task):
    """军团徽章贡献。"""

    def __init__(self):
        super().__init__(name="军团徽章贡献", pre_times=2, post_times=4)

    def pre_condition(self, ctx):
        return is_page(ctx, "home") or True

    def on_run(self, ctx):
        if not ctx.config.get_bool("legion_donate_enabled", False):
            ctx.logger.info("军团捐献已在设置中关闭，跳过")
            return True
        target = self._get_count(ctx)
        if target == 0:
            ctx.logger.info("军团捐献次数为 0，跳过")
            return True
        if not self._enter_donate_page(ctx):
            ctx.logger.warn("无法进入寄付・恩惠页面，跳过")
            return False
        if not self._set_count(ctx, target):
            ctx.logger.warn("设置捐献次数失败，跳过")
            self._back_home(ctx)
            return False
        if not self._click_donate(ctx):
            ctx.logger.warn("未找到寄付按钮，跳过")
            self._back_home(ctx)
            return False
        # 捐献成功没有弹窗，只有约1秒的通知，等它消失
        time.sleep(3)
        ctx.logger.info(f"军团徽章捐献完成（次数 {target}）")
        return self._back_home(ctx)

    @staticmethod
    def _get_count(ctx):
        value = str(ctx.config.get("legion_donate_count", "1") or "1").strip()
        if value.lower() in ("max", "all", "全部"):
            return "max"
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 1

    # ---------- 导航 ----------
    def _enter_donate_page(self, ctx):
        for _ in range(10):
            ctx.screenshot()
            if self._is_donate_page(ctx):
                return True
            if handle_download_popup(ctx):
                continue
            if close_content_popup(ctx):
                continue
            if is_page(ctx, "home"):
                if not click_legion_button(ctx):
                    ctx.logger.warn("未找到军团按钮")
                    return False
                time.sleep(3)
            elif "寄付" in "".join(t for t, *_ in read_text(ctx._last_screen)):
                if not click_donate_tab(ctx):
                    ctx.device.key("BACK")
                    time.sleep(2)
            else:
                if ctx.find("loading/loading_mark.png", threshold=0.8) is not None:
                    time.sleep(3)
                else:
                    ctx.device.key("BACK")
                    time.sleep(2)
        return self._is_donate_page(ctx)

    @staticmethod
    def _is_donate_page(ctx):
        joined = "".join(t for t, *_ in read_text(ctx._last_screen))
        return "寄付可能回数" in joined or ("寄付" in joined and "所持数" in joined)

    # ---------- 次数 ----------
    def _read_count(self, ctx):
        for text, cx, cy, _score in read_text(ctx._last_screen):
            if COUNT_X[0] <= cx <= COUNT_X[1] and COUNT_Y[0] <= cy <= COUNT_Y[1]:
                m = re.search(r"\d+", text)
                if m:
                    return int(m.group())
        return None

    def _set_count(self, ctx, target):
        if target == "max":
            if click_button_template(ctx, TPL_ALL, CTRL_X, CTRL_Y):
                return True
            ctx.logger.warn("未找到「全」按钮")
            return False

        # 先读当前次数，再点 + 或 - 调整到目标
        for _ in range(3):
            ctx.screenshot()
            current = self._read_count(ctx)
            if current is not None:
                break
            time.sleep(1)
        if current is None:
            ctx.logger.warn("读取不到当前捐献次数")
            return False

        diff = target - current
        if diff > 0:
            for _ in range(diff):
                if not click_button_template(ctx, TPL_PLUS, CTRL_X, CTRL_Y):
                    ctx.logger.warn("点 + 失败")
                    return False
        elif diff < 0:
            for _ in range(-diff):
                if not click_button_template(ctx, TPL_MINUS, CTRL_X, CTRL_Y):
                    ctx.logger.warn("点 - 失败")
                    return False
        return True

    def _click_donate(self, ctx):
        if click_button_template(ctx, TPL_CONFIRM, CONFIRM_X, CONFIRM_Y):
            ctx.logger.info("已点击寄付")
            return True
        return False

    # ---------- 返回 ----------
    def _back_home(self, ctx):
        for _ in range(8):
            ctx.screenshot()
            if is_page(ctx, "home"):
                return True
            if close_content_popup(ctx):
                continue
            if click_home_button(ctx):
                continue
            ctx.device.key("BACK")
            time.sleep(2)
        return is_page(ctx, "home")
