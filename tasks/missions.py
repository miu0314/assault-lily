import time

from core.navigation import click_home_button, ensure_home
from core.ocr import read_text
from core.pages import is_page
from core.popups import close_content_popup, handle_download_popup
from core.task import Task


# 任务页（ミッション・実績）在 1280x720 下的布局。
# 左侧两个分类行，右侧每种分类都有可领取的子页；红色数字角标位置固定。
LEFT_CATEGORIES = [
    ("ミッション", (68, 100), (136, 97)),
    ("実績", (68, 162), (136, 169)),
]

TOP_TABS = [
    ("初心者", (380, 104), (455, 91)),
    ("デイリー", (624, 104), (699, 91)),
    ("メイン", (862, 104), (941, 92)),
    ("イベント", (1116, 104), (1191, 91)),
]

CLAIM_TEMPLATE = "mission/btn_claim_all.png"
CLAIM_REGION_X = 1000
CLAIM_REGION_Y = 550
# 「実績達成」弹窗右下角关闭按钮；「報酬受取」弹窗用底部中央「閉じる」。
POPUP_DISMISS_RIGHT = (1150, 655)


class CollectDailyMissions(Task):
    """领取任务与実績奖励。

    从首页看到提示角标进入任务页，遍历每个有角标的分类和子标签，
    点击右下角「一括受取」，每批领取后关闭结果弹窗，直到没有新的领取弹窗为止。
    """

    def __init__(self):
        super().__init__(name="任务领取", pre_times=2, post_times=4)

    def pre_condition(self, ctx):
        return True

    def on_run(self, ctx):
        # 0. 先确保在首页（不在就返回/重启登录），避免直接跳过
        if not ensure_home(ctx):
            ctx.logger.warn("无法进入首页，跳过任务领取")
            return False

        # 1. 进入任务界面（首页左上角ノート图标）
        if not is_page(ctx, "mission"):
            if not self._enter_mission(ctx):
                ctx.logger.warn("无法进入任务界面，跳过")
                self._back_home(ctx)
                return False

        # 2. 遍历能领的分类
        claimed = self._claim_all_available(ctx)
        if not claimed:
            ctx.logger.info("任务界面没有可领取的奖励")

        # 3. 回首页
        return self._back_home(ctx)

    # ---------- 遍历领取 ----------
    def _claim_all_available(self, ctx):
        claimed_any = False
        for cat_label, cat_xy, cat_badge in LEFT_CATEGORIES:
            ctx.logger.info(f"切换到分类「{cat_label}」")
            ctx.click(cat_xy[0], cat_xy[1], sleeptime=3)
            ctx.screenshot()
            time.sleep(1)

            if cat_label == "ミッション":
                # 分类下有子标签：只进有角标的子页
                for tab_label, tab_xy, tab_badge in TOP_TABS:
                    ctx.screenshot()
                    if not self._has_red_badge(ctx, tab_badge):
                        continue
                    ctx.logger.info(f"「{tab_label}」有角标，进入领取")
                    ctx.click(tab_xy[0], tab_xy[1], sleeptime=4)
                    if self._claim_all(ctx):
                        claimed_any = True
            else:
                # 実績 等分类直接列出可领内容，无需子标签
                ctx.screenshot()
                if not self._has_red_badge(ctx, cat_badge):
                    continue
                if self._claim_all(ctx):
                    claimed_any = True
        return claimed_any

    def _claim_all(self, ctx):
        """在当前分类/标签页循环点「一括受取」，直到没有结果弹窗。"""
        claimed = 0
        MAX_ROUNDS = 8
        for round_no in range(1, MAX_ROUNDS + 1):
            ctx.screenshot()
            result = ctx.find(CLAIM_TEMPLATE, threshold=0.8)
            if result is None:
                ctx.logger.info("「一括受取」已消失，当前分类领取完成")
                break
            (cx, cy), score = result
            if not (cx >= CLAIM_REGION_X and cy >= CLAIM_REGION_Y):
                ctx.logger.info("「一括受取」不在右下角标准位置，停止")
                break
            ctx.logger.info(f"第 {round_no} 次点击「一括受取」({cx},{cy})")
            ctx.click(cx, cy, sleeptime=4)
            claimed += 1
            if not self._close_claim_popup(ctx):
                ctx.logger.info("点击后没有出现领取弹窗，判定当前分类已领完")
                break
        if claimed >= MAX_ROUNDS:
            ctx.logger.warn(f"已达单次上限 {MAX_ROUNDS} 次领取，停止避免异常重复")
        return claimed > 0

    @staticmethod
    def _has_red_badge(ctx, hotspot, radius=26):
        """在小范围角标区检测红点，避免页面其它红色装饰误判。"""
        hx, hy = hotspot
        for dx, dy, area in ctx.find_red_dots():
            if area >= 8 and abs(dx - hx) <= radius and abs(dy - hy) <= radius:
                return True
        return False

    def _close_claim_popup(self, ctx, rounds=6, silent_rounds=2):
        """关闭领取结果弹窗；连续 silent_rounds 轮识别不到弹窗时返回 False。"""
        popup_seen = False
        silent = 0
        for _ in range(rounds):
            time.sleep(2)
            ctx.screenshot()
            joined = "".join(t for t, *_ in read_text(ctx._last_screen))
            popup = any(marker in joined for marker in (
                "受け取りました", "以下の報酬", "実績達成",
                "Status UP", "閉じる"))
            if not popup:
                silent += 1
                if silent >= silent_rounds:
                    return popup_seen
                continue

            silent = 0
            popup_seen = True
            if self._click_dismiss(ctx):
                continue

            # 「実績達成」弹窗右下角关闭按钮
            if "実績達成" in joined or "Status UP" in joined:
                ctx.logger.info("実績弹窗：点击右下角关闭")
                ctx.click(POPUP_DISMISS_RIGHT[0], POPUP_DISMISS_RIGHT[1],
                          sleeptime=3)
                continue
        return popup_seen

    def _click_dismiss(self, ctx):
        """点击结果弹窗常见的关闭按钮（模板 / 闭じる / OK / 受取）。"""
        result = ctx.find("mission/btn_claim_popup_ok.png", threshold=0.8)
        if result is not None:
            (bx, by), score = result
            if 400 <= bx <= 900 and 550 <= by <= 720:
                ctx.logger.info(f"点击领取弹窗按钮 ({bx},{by})")
                ctx.click(bx, by, sleeptime=3)
                return True
        for text, cx, cy, score in read_text(ctx._last_screen):
            if score < 0.7:
                continue
            t = text.strip()
            if not (400 <= cx <= 900 and 550 <= cy <= 720):
                continue
            if ("閉じる" in t or "閉じ" in t or t.upper() == "OK"
                    or "受取" in t or "受領" in t or "確認" in t):
                ctx.logger.info(f"按文字点击领取弹窗按钮 ({cx},{cy})")
                ctx.click(cx, cy, sleeptime=3)
                return True
        return False

    # ---------- 导航 ----------
    def _enter_mission(self, ctx):
        for _ in range(8):
            ctx.screenshot()
            if is_page(ctx, "mission"):
                return True
            if handle_download_popup(ctx):
                continue
            if close_content_popup(ctx):
                continue
            if ctx.find("loading/loading_mark.png", threshold=0.8) is not None:
                time.sleep(3)
                continue
            if is_page(ctx, "home"):
                ctx.click(54, 57, sleeptime=5)
            else:
                ctx.device.key("BACK")
                time.sleep(2)
        return is_page(ctx, "mission")

    def _back_home(self, ctx):
        for _ in range(6):
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
