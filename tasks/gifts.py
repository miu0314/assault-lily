import time

from core.ocr import read_text
from core.pages import is_page
from core.navigation import click_home_button, ensure_home
from core.popups import close_content_popup, handle_download_popup
from core.task import Task


class CollectGifts(Task):
    """领取首页礼物箱（プレゼントボックス）奖励。

    流程：首页点ギフト(50,124) → 礼物箱 → 重复点「一括受取」，每批领取后关闭结果弹窗
    → 若某次点击后不再出现结果弹窗则判定领完 → 回首页
    """

    def __init__(self):
        super().__init__(name="领取礼物箱", pre_times=2, post_times=4)

    def pre_condition(self, ctx):
        return True

    def on_run(self, ctx):
        # 0. 先确保在首页，避免直接跳过
        if not ensure_home(ctx):
            ctx.logger.warn("无法进入首页，跳过礼物箱")
            return False

        # 1. 进入礼物箱
        if not is_page(ctx, "gift"):
            if not self._enter_gift(ctx):
                ctx.logger.warn("无法进入礼物箱，跳过")
                self._back_home(ctx)
                return False

        # 2. 重复领取：礼物多时「一括受取」会再次出现，需要点到按钮消失为止。
        claimed = self._claim_all(ctx)
        if not claimed:
            ctx.logger.warn("礼物箱没有可领取的奖励（未找到「一括受取」）")
            self._back_home(ctx)
            return False

        # 3. 回首页
        return self._back_home(ctx)

    # ---------- 工具 ----------
    def _claim_all(self, ctx):
        """循环点「一括受取」，直到按钮消失。返回是否成功领取过至少一次。"""
        claimed = 0
        MAX_ROUNDS = 8
        for round_no in range(1, MAX_ROUNDS + 1):
            ctx.screenshot()
            button = self._find_claim_button(ctx)
            if button is None and claimed == 0:
                # 首次进入时模板可能因加载帧没匹配上；只兜底一次，避免没奖励时反复点。
                ctx.logger.info("「一括受取」未匹配到正确位置，本次按固定位置点击")
                button = (1180, 638)
            if button is None:
                ctx.logger.info("「一括受取」已消失，礼物领取完成")
                break
            cx, cy = button
            ctx.logger.info(f"第 {round_no} 次点击「一括受取」({cx},{cy})")
            ctx.click(cx, cy, sleeptime=4)
            claimed += 1
            # 每次领取后关掉结果弹窗；若点击后没有出现领取结果弹窗，
            # 说明这一批已经没东西可领，即使按钮仍在也停止，避免空点。
            if not self._close_claim_result(ctx):
                ctx.logger.info("点击后没有出现领取结果弹窗，判断已领取完成")
                break
        if claimed >= MAX_ROUNDS:
            ctx.logger.warn(f"已达单次上限 {MAX_ROUNDS} 次领取，停止避免异常重复")
        return claimed > 0

    @staticmethod
    def _find_claim_button(ctx):
        """只在右下角标准区域找「一括受取」按钮，避免误匹配其它页面元素。"""
        result = ctx.find("gift/btn_claim_all.png", threshold=0.8)
        if result is None:
            return None
        (cx, cy), score = result
        if cx >= 1100 and cy >= 600:
            return (cx, cy)
        return None

    @staticmethod
    def _close_claim_result(ctx, rounds=6, silent_rounds=2):
        """关闭领取后的结果弹窗。

        返回 True 表示本批领取出现过结果弹窗并已关闭；
        连续 silent_rounds 轮识别不到弹窗时返回 False（视为已领完）。
        """
        silent = 0
        popup_seen = False
        for _ in range(rounds):
            time.sleep(2)
            ctx.screenshot()
            if close_content_popup(ctx):
                popup_seen = True
                silent = 0
                continue
            for text, cx, cy, score in read_text(ctx._last_screen):
                if (score >= 0.8 and 450 <= cx <= 900 and 550 <= cy <= 700
                        and (text.strip().upper() == "OK"
                             or "受取" in text or "受領" in text or "確認" in text)):
                    ctx.logger.info(f"点击领取结果 OK ({cx},{cy})")
                    ctx.click(cx, cy, sleeptime=3)
                    popup_seen = True
                    silent = 0
                    break
            else:
                silent += 1
                if silent >= silent_rounds:
                    return popup_seen
        return popup_seen

    def _enter_gift(self, ctx):
        for _ in range(6):
            ctx.screenshot()
            if is_page(ctx, "gift"):
                return True
            if handle_download_popup(ctx):
                continue
            if close_content_popup(ctx):
                continue
            if is_page(ctx, "home"):
                ctx.click(50, 124, sleeptime=5)
            else:
                ctx.device.key("BACK")
                time.sleep(2)
        return is_page(ctx, "gift")

    def _back_home(self, ctx):
        for _ in range(6):
            ctx.screenshot()
            if is_page(ctx, "home"):
                return True
            if close_content_popup(ctx):
                continue
            if ctx.click_template("gift/btn_back.png", threshold=0.8, sleeptime=3):
                continue
            if click_home_button(ctx):
                continue
            ctx.device.key("BACK")
            time.sleep(2)
        return is_page(ctx, "home")
