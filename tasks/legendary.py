# -*- coding: utf-8 -*-
"""传奇战斗（LEGENDARY BATTLE）：活动刚开始时先打一次解锁扫荡，之后用跳过券扫荡。"""

import time
import json
import re
from pathlib import Path

from core.navigation import click_home_button, ensure_home
from core.ocr import read_text
from core.pages import is_page
from core.popups import (close_content_popup, handle_download_popup,
                         handle_network_error)
from core.task import Task

TPL_ENTRY = "legendary/btn_entry.png"
TPL_CHALLENGE = "legendary/btn_challenge.png"
TPL_SKIP_GRAY = "legendary/btn_skip_ticket_gray.png"
TPL_SKIP_ACTIVE = "legendary/btn_skip_ticket_active.png"
TPL_FIRST_UNIT = "legendary/btn_first_unit.png"
TPL_SWEEP_PLUS = "legendary/btn_sweep_plus.png"
TPL_RESULT_OK = "legendary/btn_result_ok.png"
TPL_REWARD_OK = "legendary/btn_reward_ok.png"

# ユニット選択 界面固定坐标：主单位空槽的 +，及单位列表第一张卡的兜底位置。
MAIN_UNIT_SLOT = (330, 347)
FIRST_UNIT_CARD = (735, 265)


def _find_in(ctx, template, x_range, y_range, threshold=0.8):
    """在指定区域内找模板，返回 (中心x, 中心y, 分数) 或 None。"""
    ctx.screenshot()
    screen = ctx._last_screen
    x0, x1 = x_range
    y0, y1 = y_range
    x1 = min(x1, screen.shape[1])
    y1 = min(y1, screen.shape[0])
    if x1 <= x0 or y1 <= y0:
        return None
    region = screen[y0:y1, x0:x1]
    tpl, mask = ctx.template(template)
    import cv2
    if mask is not None:
        res = cv2.matchTemplate(region, tpl, cv2.TM_CCORR_NORMED, mask=mask)
    else:
        res = cv2.matchTemplate(region, tpl, cv2.TM_CCOEFF_NORMED)
    _, mx, _, loc = cv2.minMaxLoc(res)
    if mx < threshold:
        return None
    return (x0 + loc[0] + tpl.shape[1] // 2,
            y0 + loc[1] + tpl.shape[0] // 2,
            float(mx))


def _click_in(ctx, template, x_range, y_range, threshold=0.8, sleeptime=4):
    hit = _find_in(ctx, template, x_range, y_range, threshold)
    if hit is None:
        return False
    bx, by, score = hit
    ctx.logger.info(f"点击 {template} ({bx},{by})")
    ctx.click(bx, by, sleeptime=sleeptime)
    return True


def _ocr_has(ctx, keywords):
    joined = "".join(t for t, *_ in read_text(ctx._last_screen))
    return any(k in joined for k in keywords)


def _selected_from_pairs(texts):
    """从扫荡确认弹窗的 OCR 里读出「本次实际扫荡次数」。

    弹窗里有两组「消费前 ▸ 消费后」（跳过券 / BP，例如 4/5 ▸ 0/5），
    两组的差值就是次数；读不出成对数字时返回 None。

    实机（2026-10-06）：BP 4/5 时配 5 次，弹窗最大只到 4 —— 旧日志会
    误报「已设为 5」，靠这里读出的真实值修正。
    """
    pairs = []
    for text, *_ in texts:
        m = re.search(r"(\d+)\s*/\s*(\d+)", str(text))
        if m:
            pairs.append((int(m.group(1)), int(m.group(2))))
    for (cur, mx), (after, mx2) in zip(pairs, pairs[1:]):
        if mx == mx2 and cur >= after:
            return cur - after
    return None


class LegendaryBattle(Task):
    """传奇战斗：解锁后每日用跳过券扫荡。"""

    def __init__(self):
        super().__init__(name="传奇战斗", pre_times=2, post_times=4)

    def pre_condition(self, ctx):
        return True

    def on_run(self, ctx):
        if not ensure_home(ctx):
            ctx.logger.warn("无法返回首页，终止传奇战斗")
            return False
        if not self._enter_legendary(ctx):
            ctx.logger.warn("无法进入传奇战斗，终止")
            return False
        sweep_count = self._read_sweep_count(ctx)

        # 解锁状态由实时按钮决定：亮色=可扫；灰色/识别不到=本次(活动/主题)尚未解锁，先挑战一次。
        if not self._has_active_skip(ctx):
            ctx.logger.info("扫荡未解锁，先挑战一次解锁")
            if not self._challenge_once(ctx):
                ctx.logger.warn("挑战失败，跳过")
                self._back_home(ctx)
                return False
            self._set_unlocked(ctx)
            ctx.logger.info("已挑战一次，扫荡已解锁")

        if not self._sweep(ctx, sweep_count):
            ctx.logger.warn("扫荡未执行或失败")
            self._back_home(ctx)
            return False
        return self._back_home(ctx)

    @staticmethod
    def _read_sweep_count(ctx):
        try:
            value = ctx.config.get("legendary_sweep_count", 1) or 1
            return max(1, min(20, int(value)))
        except (TypeError, ValueError):
            return 1

    # ---------- 进入 ----------
    def _enter_legendary(self, ctx):
        for _ in range(10):
            ctx.screenshot()
            if self._is_legendary_main(ctx):
                return True
            if handle_download_popup(ctx):
                continue
            if handle_network_error(ctx):
                continue
            if close_content_popup(ctx):
                continue
            if is_page(ctx, "home"):
                ctx.click(1175, 613, sleeptime=5)
            elif _find_in(ctx, TPL_ENTRY, (600, 800), (580, 660), 0.8):
                if not _click_in(ctx, TPL_ENTRY, (600, 800), (580, 660), sleeptime=6):
                    ctx.device.key("BACK")
                    time.sleep(2)
            else:
                ctx.device.key("BACK")
                time.sleep(2)
        return self._is_legendary_main(ctx)

    @staticmethod
    def _is_legendary_main(ctx):
        return _find_in(ctx, TPL_CHALLENGE, (850, 1280), (600, 710), 0.8) is not None

    def _skip_locked(self, ctx):
        return _find_in(ctx, TPL_SKIP_GRAY, (550, 900), (600, 710), 0.8) is not None

    def _has_active_skip(self, ctx):
        return _find_in(ctx, TPL_SKIP_ACTIVE, (550, 900), (600, 710), 0.8) is not None


    # ---------- 打一次解锁 ----------
    def _challenge_once(self, ctx):
        if not _click_in(ctx, TPL_CHALLENGE, (850, 1280), (600, 710), sleeptime=7):
            ctx.logger.warn("没找到挑战按钮")
            return False
        # 队伍选择：先进ユニット選択，点主单位空槽的 + 让单位列表弹出，再点第一张单位卡填满
        if not self._fill_formation(ctx):
            ctx.logger.warn("未能填充编队")
            return False
        # 出撃
        if not _click_in(ctx, "quest/btn_sortie.png", (1100, 1280), (580, 680), 0.8, 7):
            ctx.logger.info("没匹配到出撃模板，按固定位置点击")
            ctx.click(1180, 630, sleeptime=7)
        # 等战斗结束（BATTLE FINISH 或结果 OK）
        if not self._wait_battle_finish(ctx):
            ctx.logger.warn("等待战斗结算超时")
            return False
        # 关结算 OK + 可能出现的参加报酬弹窗
        self._close_results(ctx)
        return True

    def _fill_formation(self, ctx):
        """单位选择界面：主单位为空才填；填后确认可出击，不误点已填好的格子。"""
        ctx.screenshot()
        joined = "".join(t for t, *_ in read_text(ctx._last_screen))
        has_unit_screen = ("UNIT" in joined or "SUPPORT" in joined or "0/2" in joined)
        if has_unit_screen and "0/2" not in joined:
            # 主单位已填好，直接可出击
            return True
        if "0/2" not in joined:
            # 不在空编队页：可能识别不到或已跳到别处，交给 _ensure_formation 确认
            return self._ensure_formation(ctx)

        ctx.logger.info("主单位为空，点主单位空槽 + 打开单位列表")
        ctx.click(MAIN_UNIT_SLOT[0], MAIN_UNIT_SLOT[1], sleeptime=5)
        time.sleep(2)
        ctx.screenshot()
        cards = [(cx, cy) for t, cx, cy, s in read_text(ctx._last_screen)
                 if t.strip().upper().startswith("LV") and cx > 700 and 250 <= cy <= 420]
        if cards:
            cards.sort(key=lambda c: c[0])  # 取最左侧第一张
            cx, cy = cards[0]
            ctx.logger.info(f"点第一张单位卡 ({cx},{cy})")
        else:
            cx, cy = FIRST_UNIT_CARD
            ctx.logger.warn("没 OCR 到单位卡，用兜底坐标点")
        ctx.click(cx, cy, sleeptime=4)
        time.sleep(2)
        return self._ensure_formation(ctx)

    def _ensure_formation(self, ctx):
        """确认主单位已填充（0/2 消失）且仍在单位选择页；识别到网络/下载弹窗先关闭，
        不轻易按返回，防止误退编队页。"""
        for _ in range(8):
            ctx.screenshot()
            if handle_network_error(ctx) or handle_download_popup(ctx):
                continue
            joined = "".join(t for t, *_ in read_text(ctx._last_screen))
            has_unit_screen = ("UNIT" in joined or "SUPPORT" in joined or "0/2" in joined)
            if has_unit_screen and "0/2" not in joined:
                return True
            if has_unit_screen:
                time.sleep(1)
                continue
            ctx.device.key("BACK")
            time.sleep(2)
        return False

    # ---------- 扫荡 ----------
    def _sweep(self, ctx, count=1):
        if not _click_in(ctx, TPL_SKIP_ACTIVE, (550, 900), (600, 710), sleeptime=6):
            ctx.logger.warn("没找到扫荡按钮（可能仍未解锁）")
            return False
        # 等扫荡设置弹窗：点一次 + 设置数量 1，再点 OK
        ok = False
        for _ in range(6):
            time.sleep(2)
            ctx.screenshot()
            if _ocr_has(ctx, ("SKIP", "スキップ")):
                ok = True
                break
        if not ok:
            ctx.logger.warn("扫荡设置弹窗未出现")
            return False
        path = ctx.save_screenshot(ctx._last_screen, "lg_sweep_dialog_before.png")
        ctx.logger.info("扫荡弹窗初始文字: " + " | ".join(t for t, *_ in read_text(ctx._last_screen)))
        # 对话框默认 0，点几次 + 就是几次；上限是「当前 BP」（实机 2026-10-06：
        # BP 4/5 配 5 次时，+ 点到 4 就封顶，再多点也不动）。
        for i in range(count):
            if not _click_in(ctx, TPL_SWEEP_PLUS, (750, 850), (390, 440), sleeptime=2):
                ctx.logger.warn(f"没找到数量 + 按钮（第 {i + 1} 次）")
                return False
        ctx.screenshot()
        path = ctx.save_screenshot(ctx._last_screen, "lg_sweep_dialog.png")
        texts = read_text(ctx._last_screen)
        ctx.logger.info("扫荡弹窗设定后文字: " + " | ".join(t for t, *_ in texts))
        selected = _selected_from_pairs(texts)
        if selected is None:
            ctx.logger.warn(f"读不出弹窗里的数量，按配置 {count} 次继续")
        elif selected == 0:
            ctx.logger.warn("扫荡数量还是 0（BP / 跳过券不足），取消本次扫荡")
            return False
        elif selected != count:
            ctx.logger.warn(f"扫荡弹窗实际数量 {selected}（配置 {count}，被上限卡住）")
        else:
            ctx.logger.info(f"扫荡次数已设为 {selected}")
        for text, cx, cy, score in texts:
            if text.strip().upper() == "OK" and 450 <= cx <= 900 and 550 <= cy <= 700:
                ctx.logger.info(f"点击扫荡确认 OK ({cx},{cy})")
                ctx.click(cx, cy, sleeptime=5)
                break
        else:
            ctx.logger.info("没找到 OK，按位置点击")
            ctx.click(758, 650, sleeptime=5)
        if not self._wait_battle_finish(ctx):
            ctx.logger.warn("扫荡结算超时")
            return False
        self._close_results(ctx)
        return True

    # ---------- 公共 ----------
    def _wait_battle_finish(self, ctx, timeout=300):
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(8)
            ctx.screenshot()
            if handle_network_error(ctx):
                continue
            if _ocr_has(ctx, ("BATTLE FINISH", "BATTLE FIN", "FINISH")):
                return True
            if _find_in(ctx, TPL_RESULT_OK, (550, 700), (600, 700), 0.8) is not None:
                return True
            if _find_in(ctx, "popup/btn_download_ok.png", (400, 900), (500, 720), 0.8) is not None:
                return True
            for text, cx, cy, score in read_text(ctx._last_screen):
                if text.strip().upper() == "OK" and 400 <= cx <= 900 and 500 <= cy <= 720:
                    return True
        return False

    def _close_results(self, ctx):
        for _ in range(4):
            ctx.screenshot()
            if _click_in(ctx, TPL_RESULT_OK, (550, 700), (600, 700), 0.8, 4):
                continue
            if _click_in(ctx, TPL_REWARD_OK, (550, 700), (600, 700), 0.8, 4):
                continue
            if _click_in(ctx, "popup/btn_download_ok.png", (400, 900), (500, 720), 0.8, 4):
                continue
            if close_content_popup(ctx):
                continue
            break

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

    @staticmethod
    def _set_unlocked(ctx):
        """记录已解锁状态，活动期间之后每天直接扫荡。"""
        ctx.config["legendary_unlocked"] = True
        path = ctx.config.path
        if path is not None:
            try:
                data = json.loads(Path(path).read_text(encoding="utf-8"))
                data["legendary_unlocked"] = True
                Path(path).write_text(
                    json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception as e:
                ctx.logger.warn(f"保存解锁状态失败: {e}")
