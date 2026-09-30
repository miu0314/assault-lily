import time
import re

import cv2
import numpy as np

from core.pages import is_page, is_title_screen
from core.navigation import click_home_button, ensure_home
from core.ocr import read_text, read_text_boxes
from core.popups import (click_ok_by_ocr, close_content_popup,
                         handle_download_popup, handle_network_error)
from core.task import Task
from tasks.launch import WaitForHome

# 等战斗结算时的“卡住”判定：画面连续这么多轮没变化就认为战斗没开起来。
# 实测 2026-09-16：战斗没开起来，脚本按 60 轮 × 10s 一路空等 21 分钟才超时，
# 期间画面一动不动。真打起来的战斗画面是一直在动的。
BATTLE_STILL_ROUNDS = 5
BATTLE_STILL_MIN = 3


def battle_screen_still(prev, cur, threshold=BATTLE_STILL_MIN):
    """两帧画面是不是“没变化”（战斗没开起来时画面会一直不动）。"""
    if prev is None or cur is None:
        return False
    if prev.shape != cur.shape:
        return False
    return float(cv2.absdiff(prev, cur).mean()) < threshold


def _is_daily_page(ctx):
    """每日关卡列表：一括スキップ按钮必须出现在右下角，避免首页/出战页误匹配。"""
    ctx.screenshot()
    result = ctx.find("quest/btn_skip_all.png", threshold=0.85)
    if result is None:
        return False
    (bx, by), score = result
    return bx >= 1100 and by >= 600


def _find_popup_ok(ctx):
    """找确认弹窗底部的 OK 按钮（右下区域）。"""
    for text, cx, cy, score in read_text(ctx._last_screen):
        if text.strip().upper() == "OK" and 500 <= cx <= 900 and 550 <= cy <= 700 and score >= 0.8:
            return (cx, cy)
    return None


def _is_sweep_confirm_popup(ctx):
    """扫荡确认弹窗特征：出现「一括 / スキップ / 挑回数」等文字。"""
    for text, _cx, _cy, score in read_text(ctx._last_screen):
        t = text.upper()
        if score >= 0.6 and ("一括" in t or "スキップ" in t or "挑回数" in t or "挑戦回数" in t):
            return True
    return False


def _is_title_return_popup(ctx):
    """「返回标题」确认弹窗特征：出现「タイトル / 戻り」文字。"""
    for text, _cx, _cy, _score in read_text(ctx._last_screen):
        if "タイトル" in text or "戻り" in text or "戻ります" in text:
            return True
    return False


class ClearDailyQuests(Task):
    """扫荡每日关卡（一括スキップ），完成每日战斗任务。

    流程：首页 → 出撃 → イベント → デイリー → 一括スキップ
    → 确认 OK → 结果 OK → 回首页
    """

    def __init__(self):
        super().__init__(name="扫荡每日关卡", pre_times=2, post_times=4)
        self.completed = False

    def post_condition(self, ctx):
        return self.completed and is_page(ctx, "home")

    def pre_condition(self, ctx):
        return True

    def on_run(self, ctx):
        self.completed = False
        # 0. 先确保在首页：不在首页就尝试返回，回不去则重启游戏重新登录
        if not self._ensure_home(ctx):
            ctx.logger.warn("无法回到首页，重启游戏重新登录")
            try:
                ctx.device.start_app()
                WaitForHome().run(ctx)
            except Exception as e:
                ctx.logger.warn(f"重新登录失败: {e}")
            if not is_page(ctx, "home"):
                ctx.logger.warn("仍不在首页，跳过每日关卡扫荡")
                return

        # 1. 进入出撃界面
        if not _is_daily_page(ctx):
            if not self._enter_battle(ctx):
                ctx.logger.warn("无法进入出撃界面，跳过")
                return

        # 2. 进イベント → デイリー
        if not _is_daily_page(ctx):
            if not self._enter_event_daily(ctx):
                ctx.logger.warn("无法进入每日关卡列表，跳过")
                self._back_home(ctx)
                return

        # 已用完当天挑战次数时按钮仍能被模板识别，但点击不会弹确认框。
        # 先读取卡片左侧的「挑戦可能数」数值，避免无意义地重复点击。
        attempts = self._daily_attempts_available(ctx)
        if attempts is False:
            ctx.logger.info("每日关卡没有可用挑战次数，跳过扫荡")
            self.completed = self._back_home(ctx)
            return

        # 3. 点「一括スキップ」：按钮必须在右下角识别到才点，找不到就重试
        confirmed = False
        for _attempt in range(3):
            clicked = False
            for _ in range(6):
                ctx.screenshot()
                result = ctx.find("quest/btn_skip_all.png", threshold=0.85)
                if result is not None and result[0][0] >= 1100 and result[0][1] >= 600:
                    (sx, sy), score = result
                    ctx.logger.info(f"点击「一括スキップ」({sx},{sy})")
                    ctx.click(sx, sy, sleeptime=3)
                    clicked = True
                    break
                time.sleep(2)
            if not clicked:
                break
            # 等确认弹窗：必须出现扫荡确认框才点 OK；误触「返回标题」则取消重试
            for _ in range(4):
                time.sleep(2)
                ctx.screenshot()
                if _is_title_return_popup(ctx):
                    ctx.logger.warn("误触「返回标题」弹窗，按返回取消")
                    ctx.device.key("BACK")
                    time.sleep(2)
                    break
                ok_pos = _find_popup_ok(ctx)
                if ok_pos is not None:
                    if _is_sweep_confirm_popup(ctx):
                        ctx.logger.info(f"扫荡确认弹窗出现，点 OK ({ok_pos[0]},{ok_pos[1]})")
                        ctx.click(ok_pos[0], ok_pos[1], sleeptime=4)
                        confirmed = True
                        break
                    # 通信错误只由专用处理器确认；其它未知弹窗一律取消，
                    # 避免 OCR 漏掉「返回标题」后误点通用 OK。
                    if handle_network_error(ctx):
                        ctx.logger.info("已处理通信错误，重新尝试扫荡")
                        break
                    ctx.logger.warn("发现 OK 但不是扫荡确认弹窗，按返回取消")
                    ctx.device.key("BACK")
                    time.sleep(2)
                    break
            if confirmed:
                break
        if not confirmed:
            ctx.logger.warn("一括スキップ点击后没有确认弹窗（已重试），跳过本任务")
            self._back_home(ctx)
            return

        # 4. 等待扫荡结果并点 OK 关闭
        result_shown = False
        for _ in range(8):
            ctx.screenshot()
            if ctx.find("quest/btn_stage_clear_ok.png", threshold=0.8) is not None:
                (okx, oky), score = ctx.find("quest/btn_stage_clear_ok.png", threshold=0.8)
                ctx.logger.info(f"扫荡完成，点击结果 OK ({okx},{oky})")
                ctx.click(okx, oky, sleeptime=5)
                result_shown = True
                break
            if handle_download_popup(ctx):
                continue
            time.sleep(3)
        if not result_shown:
            ctx.logger.info("扫荡没有结果（可能已扫完），跳过")
            self._back_home(ctx)
            return

        # 5. 回首页
        self.completed = self._back_home(ctx)

    @staticmethod
    def _daily_attempts_available(ctx):
        """返回是否存在可扫荡次数；无法解析时返回 None 继续常规流程。"""
        pairs = []
        for text, cx, cy, _score in read_text(ctx._last_screen):
            if not (250 <= cx <= 1050 and 280 <= cy <= 610):
                continue
            # 每张卡片的 OCR 块通常同时包含挑战数和充能数；只取其中
            # 的数字对，后续按每行第一个数字对判断挑战次数。
            found = re.findall(r"(\d+)\s*/\s*(\d+)", str(text))
            if found:
                pairs.append((cx, cy, [(int(a), int(b)) for a, b in found]))
        if not pairs:
            return None
        by_row = {}
        for cx, cy, nums in pairs:
            key = min((340, 565), key=lambda y: abs(y - cy))
            by_row.setdefault(key, []).extend(nums)
        # 每行首个数字对是挑戦可能数；至少一张卡仍有次数才可扫荡。
        return any(nums and nums[0][0] > 0 and nums[0][1] > 0
                   for nums in by_row.values())

    # ---------- 工具 ----------
    def _enter_battle(self, ctx):
        for _ in range(8):
            ctx.screenshot()
            if is_page(ctx, "battle") or _is_daily_page(ctx):
                return True
            if handle_download_popup(ctx):
                continue
            if close_content_popup(ctx):
                continue
            if ctx.find("loading/loading_mark.png", threshold=0.8) is not None:
                time.sleep(3)
                continue
            if is_page(ctx, "home"):
                ctx.click(1175, 613, sleeptime=5)
            else:
                ctx.device.key("BACK")
                time.sleep(2)
        return is_page(ctx, "battle") or _is_daily_page(ctx)

    def _enter_event_daily(self, ctx):
        for _ in range(8):
            ctx.screenshot()
            if _is_daily_page(ctx):
                return True
            if handle_download_popup(ctx):
                continue
            if close_content_popup(ctx):
                continue
            if is_page(ctx, "battle"):
                ctx.click(921, 227, sleeptime=5)
            elif is_page(ctx, "event"):
                ctx.click(57, 167, sleeptime=5)
            else:
                ctx.device.key("BACK")
                time.sleep(2)
        return _is_daily_page(ctx)

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

    def _ensure_home(self, ctx):
        """确保在首页；返回是否成功。"""
        for _ in range(2):
            if self._back_home(ctx):
                return True
            time.sleep(2)
        return is_page(ctx, "home")


class SweepRankUpStage(Task):
    """扫荡 RANK UP 关卡：按配置选择难度（初級/中級/上級/EX），用跳过券扫荡。

    流程：首页 → 出撃 → イベント → デイリー → RANK UP 卡片
    → ステージ選択选难度 → 详情 → ユニット選択
    → 主单位槽位 → 右侧选队伍 → スキップチケット → すべて → OK
    → 结果 OK → 回首页
    """

    def __init__(self):
        super().__init__(name="扫荡RANK UP关卡", pre_times=2, post_times=4)

    def pre_condition(self, ctx):
        return True

    def on_run(self, ctx):
        self.completed = False
        difficulty = self._get_difficulty(ctx)
        # 0. 先确保在首页（与每日扫荡一致，避免直接跳过）
        if not self._ensure_home(ctx):
            ctx.logger.warn("无法回到首页，重启游戏重新登录")
            try:
                ctx.device.start_app()
                WaitForHome().run(ctx)
            except Exception as e:
                ctx.logger.warn(f"重新登录失败: {e}")
            if not is_page(ctx, "home"):
                ctx.logger.warn("仍不在首页，跳过RANK UP扫荡")
                return self.skip("RANK UP 扫荡：无法回到首页，跳过", ctx)

        # 1. 进入每日关卡列表
        if not _is_daily_page(ctx):
            if not self._enter_quest_daily(ctx):
                ctx.logger.warn("无法进入每日关卡列表，跳过")
                self._back_home(ctx)
                return self.skip("RANK UP 扫荡：无法进入每日关卡列表，跳过", ctx)

        # 2. 点 RANK UP 卡片，进入ステージ選択
        ctx.screenshot()
        ctx.click(478, 234, sleeptime=7)
        if not self._wait_stage_selection(ctx):
            ctx.logger.warn("未进入 RANK UP 关卡选择页，跳过")
            self._back_home(ctx)
            return self.skip("RANK UP 扫荡：未进入关卡选择页，跳过", ctx)

        # 3. 选择用户配置的难度
        if not self._select_difficulty(ctx, difficulty):
            ctx.logger.warn(f"未找到难度「{difficulty}」，跳过")
            self._back_home(ctx)
            return self.skip(f"RANK UP 扫荡：未找到难度「{difficulty}」，跳过", ctx)

        # 4. 详情页点「ユニット選択」进入战斗准备
        if not self._wait_stage_info(ctx):
            # 2026-09-30 实测：没次数时点关卡行不进ステージ情報，而是弹
            # 「挑戦可能数チャージ確認」（花宝石把 0/20 充成 1/20）。旧日志只写
            # 「未进入关卡详情页」，看不出原因；而且那个弹窗的 OK 正好在
            # `_find_popup_ok` 的判定区间里，绝不能让后续流程点到它。
            if self._stage_charge_dialog(ctx):
                joined = "".join(t for t, *_ in read_text(ctx._last_screen))
                m = re.search(r"(\d+)\s*/\s*(\d+)", joined)
                left = f"{m.group(1)}/{m.group(2)}" if m else "0"
                ctx.logger.warn(
                    f"RANK UP 挑戦可能数不足（{left}），已取消「チャージ確認」并跳过")
                ctx.device.key("BACK")      # 等同于弹窗的「キャンセル」，不花宝石
                time.sleep(2)
            elif self._on_stage_selection(ctx):
                ctx.logger.warn(
                    f"RANK UP「{difficulty}」关卡点了没反应（可能未解锁），跳过")
            else:
                ctx.logger.warn("未进入关卡详情页，跳过")
            self._back_home(ctx)
            return self.skip("RANK UP 扫荡：未进入关卡详情页，跳过", ctx)
        ctx.click(1176, 621, sleeptime=6)
        if not self._wait_unit_select(ctx):
            ctx.logger.warn("未进入ユニット選択页，跳过")
            self._back_home(ctx)
            return self.skip("RANK UP 扫荡：未进入ユニット選択页，跳过", ctx)

        # 5. 主单位槽位为空时：点击 → 右侧选队伍
        ctx.screenshot()
        result, scale = ctx.find_scale("quest/btn_main_unit_select.png", threshold=0.7)
        if result is not None:
            (ux, uy), score = result
            ctx.logger.info(f"主单位槽位为空，点击选择队伍 ({ux},{uy})")
            ctx.click(ux, uy, sleeptime=4)
            ctx.screenshot()
            ctx.click(952, 268, sleeptime=4)
        else:
            ctx.logger.info("主单位已配置，跳过队伍选择")

        # 6. 点「スキップチケット」并确认扫荡设置弹窗
        if not self._do_sweep(ctx):
            ctx.logger.warn("RANK UP 扫荡未执行（没有出现扫荡设置弹窗），跳过")
            self._back_home(ctx)
            return self.skip("RANK UP 扫荡：没有出现扫荡设置弹窗，跳过", ctx)

        # 7. 等待结果并点 OK 关闭
        self._close_result(ctx)

        # 8. 回首页
        self.completed = True
        if not self._back_home(ctx):
            ctx.logger.warn("RANK UP 扫荡结束但未能回首页")
            return self.skip("RANK UP 扫荡：结算后未能回首页", ctx)
        return True

    # ---------- 工具 ----------
    @staticmethod
    def _get_difficulty(ctx):
        """读取配置的难度，兼容「初級/中級/上級/EX」和完整关卡名。"""
        value = str(ctx.config.get("rankup_stage_difficulty", "EX") or "EX").strip()
        value = value.replace("级", "級")
        for key in ("初級", "中級", "上級"):
            if key in value:
                return key
        return "EX"

    def _wait_stage_selection(self, ctx):
        """等待进入 RANK UP 的ステージ選択页。"""
        for _ in range(10):
            if self._on_stage_selection(ctx):
                return True
            time.sleep(2)
        return False

    @staticmethod
    def _on_stage_selection(ctx):
        """当前画面是不是 RANK UP 的ステージ選択页（单次判定，供失败诊断用）。"""
        ctx.screenshot()
        for text, _cx, _cy, _score in read_text(ctx._last_screen):
            t = text.upper().replace("级", "級")
            if "NO REWARD" in t:
                return True
            if "RANK" in t and any(k in t for k in ("初級", "中級", "上級", "EX")):
                return True
        return False

    @staticmethod
    def _stage_charge_dialog(ctx):
        """「挑戦可能数チャージ確認」弹窗：次数不足时问要不要花宝石买次数。

        实测 2026-09-30：RANK UP 挑戦可能数 0/20 时点关卡行不会进ステージ情報，
        而是弹这个框（マギジュエル 54,490 ▸ 54,440 / 挑戦可能数 0/20 ▸ 1/20 /
        チャージ可能数 0 ▸ 0，底部 キャンセル + OK）。点 OK 会花宝石，必须取消。
        弹窗 OCR 稳定读出「可能数」+「不足」（战字常丢），拿这两个词判定。
        """
        ctx.screenshot()
        joined = "".join(t for t, *_ in read_text(ctx._last_screen))
        return "可能数" in joined and "不足" in joined

    def _select_difficulty(self, ctx, difficulty):
        """按配置难度点击对应关卡行；找不到就滚动列表再找。"""
        for _ in range(6):
            ctx.screenshot()
            for text, cx, cy, score in read_text(ctx._last_screen):
                t = text.upper().replace("级", "級")
                if difficulty == "EX":
                    if "EX" in t and "RANK" in t and "EXP" not in t:
                        ctx.logger.info(f"选择难度 EX ({cx},{cy})")
                        ctx.click(900, cy, sleeptime=7)
                        return True
                elif difficulty in t:
                    ctx.logger.info(f"选择难度 {difficulty} ({cx},{cy})")
                    ctx.click(900, cy, sleeptime=7)
                    return True
            ctx.device.swipe(900, 500, 900, 200, 400)
            time.sleep(2)
        return False

    def _wait_stage_info(self, ctx):
        """等待进入所选难度的关卡详情页（ステージ情報）。"""
        for _ in range(10):
            ctx.screenshot()
            joined = "".join(t for t, *_ in read_text(ctx._last_screen))
            if "消費AP" in joined or "消費AP" in joined or "初回報酬" in joined or "ステージ情報" in joined:
                return True
            time.sleep(2)
        return False

    def _wait_unit_select(self, ctx):
        """等待进入ユニット選択（队伍选择）页。"""
        for _ in range(10):
            ctx.screenshot()
            joined = "".join(t for t, *_ in read_text(ctx._last_screen))
            if "AUTO" in joined and ("UNIT" in joined or "スキップ" in joined or "総戦闘力" in joined):
                return True
            time.sleep(2)
        return False

    def _do_sweep(self, ctx):
        """点「スキップチケット」，确认扫荡设置弹窗后选「すべて」并点 OK。"""
        ctx.screenshot()
        ctx.logger.info("点击「スキップチケット」(950,612)")
        ctx.click(950, 612, sleeptime=4)
        ok_pos = None
        for _ in range(5):
            time.sleep(2)
            ctx.screenshot()
            ok_pos = _find_popup_ok(ctx)
            if ok_pos is not None:
                break
        if ok_pos is None:
            return False
        ctx.screenshot()
        ctx.logger.info("点击「すべて」(905,413)")
        ctx.click(905, 413, sleeptime=3)
        ctx.screenshot()
        ctx.logger.info(f"点击 OK 确认扫荡 ({ok_pos[0]},{ok_pos[1]})")
        ctx.click(ok_pos[0], ok_pos[1], sleeptime=5)
        return True

    def _close_result(self, ctx):
        result_shown = False
        for _ in range(8):
            ctx.screenshot()
            result = ctx.find("quest/btn_stage_clear_ok.png", threshold=0.8)
            if result is not None:
                (okx, oky), score = result
                ctx.logger.info(f"扫荡完成，点击结果 OK ({okx},{oky})")
                ctx.click(okx, oky, sleeptime=5)
                result_shown = True
                break
            if handle_download_popup(ctx):
                continue
            time.sleep(3)
        if not result_shown:
            ctx.logger.info("RANK UP 扫荡没有结果（可能已扫完），跳过")

    def _enter_quest_daily(self, ctx):
        for _ in range(10):
            ctx.screenshot()
            if _is_daily_page(ctx):
                return True
            if handle_download_popup(ctx):
                continue
            if close_content_popup(ctx):
                continue
            if is_page(ctx, "home"):
                ctx.click(1175, 613, sleeptime=5)
            elif is_page(ctx, "battle"):
                ctx.click(921, 227, sleeptime=5)
            elif is_page(ctx, "event"):
                ctx.click(57, 167, sleeptime=5)
            else:
                if ctx.find("loading/loading_mark.png", threshold=0.8) is not None:
                    time.sleep(3)
                else:
                    ctx.device.key("BACK")
                    time.sleep(2)
        return _is_daily_page(ctx)

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

    def _ensure_home(self, ctx):
        for _ in range(2):
            if self._back_home(ctx):
                return True
            time.sleep(2)
        return is_page(ctx, "home")


class _LegionGekihaTask(Task):
    """外征撃破任務基类：进入阶段列表后按卡片顺序把每个阶段打一遍。

    新版界面没有ステージ编号和 X/1 完成标记，改为按信息行位置识别关卡卡，
    从上到下逐个战斗；打完当前屏后向下滚动继续。
    每关：点ステージ → ユニット選択 → 选队伍（槽位为空时）→ 出撃
    → 等倒计时自动开战 → 等战斗结算 → 点 OK（可能有多个结算页）→ 下一关
    """

    # 关卡编号：01/02/.../EX/EX2；OCR 偶尔会在数字前混入一两个杂字符（如「ジ03」）
    _STAGE_RE = re.compile(r"^(?:[^\d]{0,2})(\d{2}|EX\d*)$")

    def __init__(self, name, title_template):
        super().__init__(name=name, pre_times=2, post_times=4)
        self.title_template = title_template
        self.failed = False
        self.aborted = False
        self.processed_count = 0
        self.completed = False

    def pre_condition(self, ctx):
        return is_page(ctx, "home") or True

    def post_condition(self, ctx):
        """只有所有可见阶段都处理完且正常回到首页才算成功。"""
        return self.completed and not self.failed and not self.aborted and is_page(ctx, "home")

    def on_run(self, ctx):
        self.failed = False
        self.aborted = False
        self.processed_count = 0
        self.completed = False
        # 先确保在首页（游戏没启动会自动拉起并登录），避免在桌面/其他页面干等
        if not ensure_home(ctx):
            ctx.logger.warn("无法进入首页，跳过外征任务")
            return self.skip("无法进入首页，跳过外征任务", ctx)
        if not self._enter_legion_stage_list(ctx):
            ctx.logger.warn(f"无法进入{self.name}，跳过")
            self._back_home(ctx)
            return self.skip(f"无法进入{self.name}，跳过", ctx)

        self._scroll_stage_list_top(ctx)
        processed = set()
        for _ in range(10):
            rows = self._read_stage_rows(ctx)
            todo = [y for y in rows if y not in processed]
            if todo:
                line_y = todo[0]
                processed.add(line_y)
                self.processed_count += 1
                ctx.logger.info(f"=== 处理关卡 (y={line_y}) ===")
                ok = self._clear_one_stage_at(ctx, line_y)
                if self.failed:
                    ctx.logger.warn("战斗失败，已返回首页，结束外征任务")
                    self.status = self.STATUS_SKIP
                    break
                if not ok:
                    ctx.logger.warn(f"关卡 y={line_y} 未完成，结束外征任务")
                    self.status = self.STATUS_SKIP
                    break
                if not self._back_to_stage_list(ctx):
                    ctx.logger.warn("未能回到阶段列表")
                    self.status = self.STATUS_SKIP
                    break
                # 结算后滚动位置会漂移：回到顶部再用稳定坐标继续，避免重复打
                self._scroll_stage_list_top(ctx)
                continue
            if not self._scroll_stage_list_down(ctx):
                ctx.logger.info("已到列表底部，没有更多关卡")
                if self.processed_count > 0:
                    self.completed = True
                else:
                    self.status = self.STATUS_SKIP
                break
            if not any(y not in processed for y in self._read_stage_rows(ctx)):
                ctx.logger.info("滚动后没有新关卡，结束")
                if self.processed_count > 0:
                    self.completed = True
                else:
                    self.status = self.STATUS_SKIP
                break

        if not self._back_home(ctx):
            ctx.logger.warn("外征任务结束但未能回到首页")
            self.status = self.STATUS_SKIP

    # ---------- 工具 ----------
    def _find_banner(self, ctx):
        """用横幅左上角的标题文字区域找本任务卡片（避免整图误匹配）。"""
        # 新版横幅会因滚动/渲染产生约 0.70 的匹配分数；标题模板本身
        # 已裁剪到左上文字区域，降低门槛仍比整张横幅匹配安全。
        result, scale = ctx.find_scale(self.title_template, threshold=0.65)
        if result is None or scale is None:
            return None, None
        (tx, ty), score = result
        # 标题条约 300x58，位于 421x178 横幅左上角，换算到卡片中心便于点击
        return ((tx + 60, ty + 60), score), scale

    def _enter_legion_stage_list(self, ctx):
        for _ in range(12):
            ctx.screenshot()
            # ``_is_stage_list`` only checks the generic card layout.  All
            # three 外征任务 use that same layout, so accepting it here can
            # leave us on ポリタス (or the other task) after a BACK/OK.  The
            # title-specific check is intentionally required at every entry.
            if self._is_target_stage_list(ctx):
                return True
            if handle_download_popup(ctx):
                continue
            if close_content_popup(ctx):
                continue
            if is_page(ctx, "home"):
                ctx.click(1175, 613, sleeptime=5)
            elif is_page(ctx, "battle"):
                ctx.click(922, 597, sleeptime=5)
            elif self._is_legion_menu(ctx):
                # 只允许进入与本任务标题模板匹配的卡片，避免误打其他外征。
                found = self._try_open_target_stage_list(ctx)
                # 目标卡片可能因列表滚动而不可见：先回顶部，再向下扫描。
                if not found:
                    for _ in range(5):
                        ctx.device.swipe(900, 200, 900, 600, 400)
                        time.sleep(2)
                        ctx.screenshot()
                        if self._try_open_target_stage_list(ctx):
                            found = True
                            break
                if not found:
                    for _ in range(8):
                        ctx.device.swipe(900, 600, 900, 200, 400)
                        time.sleep(2)
                        ctx.screenshot()
                        if self._try_open_target_stage_list(ctx):
                            found = True
                            break
                if not found:
                    ctx.logger.warn(f"外征菜单里没有找到「{self.name}」，为避免误打已跳过")
                    return False
            else:
                if ctx.find("loading/loading_mark.png", threshold=0.8) is not None:
                    time.sleep(3)
                else:
                    ctx.device.key("BACK")
                    time.sleep(2)
        return self._is_target_stage_list(ctx)

    def _find_gekiha_cards(self, ctx):
        """找外征菜单里所有任务卡标题的中心 y（去重）。

        OCR 读日文标题不稳定（经常把「撃破任務」读成乱码），
        所以改为按「左侧标题栏文字块宽度 ≥80px」识别卡片行，不再依赖具体文字。
        """
        ctx.screenshot()
        ys = []
        for _text, x0, y0, x1, y1, _score in read_text_boxes(ctx._last_screen):
            cx = (x0 + x1) // 2
            cy = (y0 + y1) // 2
            w = x1 - x0
            # 卡片标题：左侧区域、宽度足够（HUGE/ANALYSIS 等小文字排除）
            if 280 <= cx <= 680 and 60 <= cy <= 700 and w >= 80:
                ys.append(cy)
        ys.sort()
        out = []
        for y in ys:
            if not out or y - out[-1] > 40:
                out.append(y)
        return out

    def _try_open_target_stage_list(self, ctx):
        """仅在找到本任务的标题模板时点击对应卡片。"""
        result, scale = self._find_banner(ctx)
        if result is None or scale is None or scale < 0.85:
            return False
        (gx, gy), score = result
        ctx.logger.info(
            f"找到「{self.name}」（匹配 {score:.2f}），点击 ({gx},{gy})")
        ctx.click(gx, gy, sleeptime=6)
        return True

    def _is_legion_menu(self, ctx):
        """外征任務主菜单：标题模板匹配（OCR 日文不稳定，模板优先）。"""
        ctx.screenshot()
        result = ctx.find_scale("legion/menu_title.png", threshold=0.8)
        if result is not None and result[1] is not None and result[1] >= 0.9:
            return True
        # 兜底：OCR 标题 + 卡片行
        has_title = False
        has_card = False
        for text, cx, cy, score in read_text(ctx._last_screen):
            if "外征" in text and cy < 150:
                has_title = True
        if len(self._find_gekiha_cards(ctx)) >= 1:
            has_card = True
        return has_title and has_card

    def _clear_one_stage_at(self, ctx, line_y):
        """根据卡片信息行位置点击该关卡，并完成一局战斗。"""
        # 1. 点卡片（信息行上方约 60px 为卡片主体）
        ctx.screenshot()
        ctx.click(890, line_y - 60, sleeptime=6)
        # 2. 点ユニット選択
        time.sleep(2)
        ctx.click(1179, 622, sleeptime=6)
        # 3. 主单位槽位为空则选队伍
        ctx.screenshot()
        result, scale = ctx.find_scale("quest/btn_main_unit_select.png", threshold=0.7)
        if result is not None:
            (ux, uy), score = result
            ctx.logger.info(f"选择队伍：点主单位槽位 ({ux},{uy})")
            ctx.click(ux, uy, sleeptime=4)
            ctx.screenshot()
            ctx.click(952, 268, sleeptime=4)
        else:
            ctx.logger.info("主单位已配置")
        # 4. 点出撃
        ctx.screenshot()
        ctx.click(1177, 612, sleeptime=5)
        ctx.logger.info("已点出撃")
        # 4b. 点「バトル開始」快速开战（不用等倒计时）
        # 出撃后可能有加载画面，等待时间要够长（最多约 40 秒）
        battle_start_ready = False
        battle_start_clicked = False
        for _ in range(8):
            time.sleep(3)
            ctx.screenshot()
            if self._dismiss_error_popup(ctx):
                continue
            if ctx.find("loading/loading_mark.png", threshold=0.8) is not None:
                continue
            # 优先点「開始/バトル開始」按钮（准备画面也有 AUTO，不能先判战斗）
            hit = self._find_battle_start(ctx)
            if hit is not None:
                bx, by = hit
                ctx.logger.info(f"点击「バトル開始」({bx},{by})")
                ctx.click(bx, by, sleeptime=3)
                battle_start_ready = True
                battle_start_clicked = True
                break
            # 新流程出撃后可能直接进战斗（有 WAVE 等战斗界面标记）
            if self._is_in_battle(ctx):
                battle_start_ready = True
                break
        if not battle_start_ready:
            ctx.logger.info("未识别到战斗开始按钮，继续等待战斗")
        # 4c. 点击新版中央按钮后会再出现确认 OK；给弹窗足够时间出现，不能首轮没找到就退出。
        if battle_start_clicked:
            self._wait_and_confirm_battle_start(ctx)
        # 5. 等战斗结算（最长约 9 分钟；画面长时间不动会提前放弃）
        if not self._wait_battle_result(ctx):
            return False
        # 6. 点 OK 直到回到阶段列表（可能有多个结算页）
        for _ in range(5):
            ctx.screenshot()
            if self._is_target_stage_list(ctx):
                return True
            hit = self._find_result_ok(ctx)
            if hit is None:
                # 新流程结算后回到编队页：按返回键回阶段列表
                break
            ox, oy = hit
            ctx.logger.info(f"点击结算 OK ({ox},{oy})")
            ctx.click(ox, oy, sleeptime=5)
        if not self._is_target_stage_list(ctx):
            return self._back_to_stage_list(ctx)
        return True

    def _wait_battle_result(self, ctx, max_rounds=60):
        """等战斗结算；返回是否等到了结算。

        战斗没开起来（出撃后卡在通信失败/加载）时画面一动不动，
        2026-09-16 就这样空等了 21 分钟（11:05:42 → 11:26:30）。
        现在连续 `BATTLE_STILL_ROUNDS` 轮画面没变化就提前放弃并留一张截图；
        只要还在战斗画面（`_is_in_battle`）就一直等，不误杀正常战斗。
        """
        last_screen = None
        still = 0
        for i in range(max_rounds):
            time.sleep(10)
            ctx.screenshot()
            if handle_download_popup(ctx) or self._dismiss_error_popup(ctx):
                still = 0
                last_screen = ctx._last_screen.copy()
                continue
            if is_title_screen(ctx):
                # 2026-09-30 实测：战斗中途游戏自己重启（「データダウンロード」→ 回标题），
                # 标题画面背景在动，`battle_screen_still` 永远不成立，一路空等 9 分钟才超时。
                path = ctx.save_screenshot(ctx._last_screen, "battle_title_back.png")
                ctx.logger.warn(
                    f"游戏已回到标题画面（战斗被重启/掉线打断），放弃等待结算；"
                    f"截图已保存: {path}")
                return False
            if self._find_result_ok(ctx) is not None:
                ctx.logger.info(f"战斗结算出现（{i*10}s）")
                return True
            if self._detect_and_handle_failure(ctx):
                ctx.logger.warn("本关战斗失败，已取消（不重试）")
                self.failed = True
                return False
            if self._is_in_battle(ctx):
                still = 0                      # 还在打，别误判成卡住
            elif battle_screen_still(last_screen, ctx._last_screen):
                still += 1
            else:
                still = 0
            last_screen = ctx._last_screen.copy()
            if still >= BATTLE_STILL_ROUNDS:
                path = ctx.save_screenshot(ctx._last_screen, "battle_stuck.png")
                ctx.logger.warn(
                    f"战斗画面连续 {still} 轮没变化（战斗可能没开起来），提前放弃；"
                    f"截图已保存: {path}")
                return False
        ctx.logger.warn("等待战斗结算超时")
        return False

    @staticmethod
    def _find_result_ok(ctx):
        """找战斗结算 OK：必须同时确认结果页文字和底部 OK。"""
        safe_regions = (
            # 旧版结算 OK 在底部中间；新版在右下角约 (1177, 642)。
            (400, 900, 590, 720),
            (1030, 1280, 600, 720),
        )
        items = read_text(ctx._last_screen)
        # OCR 常把标题读成 ``STAGE_CLEAR``、``BATTLE-FIN`` 等变体，
        # 去掉标点后再判断，避免结果页被误当成战斗中。
        joined = re.sub(r"[^A-Z0-9]", "", "".join(
            text for text, *_ in items).upper())
        if not any(marker in joined for marker in
                   ("STAGECLEAR", "BATTLEFINISH", "BATTLEFIN", "RESULT")):
            return None
        for text, cx, cy, score in items:
            if text.strip().upper() != "OK" or score < 0.8:
                continue
            if any(x0 <= cx <= x1 and y0 <= cy <= y1
                   for x0, x1, y0, y1 in safe_regions):
                return (cx, cy)
        return None

    @staticmethod
    def _find_template_in_region(ctx, template, x_min, x_max, y_min, y_max,
                                 threshold=0.8):
        """在裁剪区域内匹配模板，避免全屏最佳匹配落到相似按钮上。"""
        screen = ctx._last_screen
        if screen is None:
            screen = ctx.screenshot()
        height, width = screen.shape[:2]
        x0, x1 = max(0, x_min), min(width, x_max)
        y0, y1 = max(0, y_min), min(height, y_max)
        tpl, mask = ctx.template(template)
        region = screen[y0:y1, x0:x1]
        if region.shape[0] < tpl.shape[0] or region.shape[1] < tpl.shape[1]:
            return None
        if mask is not None:
            matched = cv2.matchTemplate(
                region, tpl, cv2.TM_CCORR_NORMED, mask=mask)
        else:
            matched = cv2.matchTemplate(region, tpl, cv2.TM_CCOEFF_NORMED)
        _min_score, max_score, _min_loc, max_loc = cv2.minMaxLoc(matched)
        if max_score < threshold:
            return None
        return (x0 + max_loc[0] + tpl.shape[1] // 2,
                y0 + max_loc[1] + tpl.shape[0] // 2)

    @staticmethod
    def _find_ok_in_region(ctx, x_min, x_max, y_min, y_max,
                           template_threshold=0.8):
        """用现有 OK 模板及 OCR 在指定安全区域内找按钮。"""
        for tpl in ("legion/btn_result_ok.png", "popup/btn_download_ok.png",
                    "legion/btn_error_ok.png"):
            result = ctx.find(tpl, threshold=template_threshold)
            if result is not None:
                (ox, oy), score = result
                if x_min <= ox <= x_max and y_min <= oy <= y_max:
                    return (ox, oy)
        for text, cx, cy, score in read_text(ctx._last_screen):
            if (text.strip().upper() == "OK" and score >= 0.8
                    and x_min <= cx <= x_max and y_min <= cy <= y_max):
                return (cx, cy)
        return None

    def _wait_and_confirm_battle_start(self, ctx):
        """等待「バトル開始」后的确认框并点 OK；加载较慢时持续重试。"""
        confirmed = False
        for _ in range(10):
            time.sleep(2)
            ctx.screenshot()
            if self._dismiss_error_popup(ctx):
                continue
            hit = self._find_ok_in_region(
                ctx, x_min=300, x_max=1000, y_min=300, y_max=700,
                template_threshold=0.95)
            if hit is not None:
                ox, oy = hit
                ctx.logger.info(f"点击战斗开始确认 OK ({ox},{oy})")
                ctx.click(ox, oy, sleeptime=3)
                confirmed = True
                continue
            if self._is_in_battle(ctx):
                return confirmed
        if not confirmed:
            ctx.logger.warn("等待战斗开始确认 OK 超时，继续观察战斗结果")
        return confirmed

    @staticmethod
    def _is_in_battle(ctx):
        """判断是否已经进入战斗画面（准备画面也有 AUTO，必须看 WAVE/MENU）。"""
        joined = "".join(t for t, *_ in read_text(ctx._last_screen))
        up = joined.upper()
        return "WAVE" in up or ("MENU" in up and "AUTO" in up)

    @staticmethod
    def _find_battle_start_by_ocr(ctx):
        """OCR 找准备画面的「開始」按钮（兼容中央新版和下方旧版）。"""
        for text, cx, cy, score in read_text(ctx._last_screen):
            if ("開始" in text and score >= 0.6
                    and 250 <= cy <= 700 and 450 <= cx <= 1000):
                return (cx, cy)
        return None

    @staticmethod
    def _find_battle_start(ctx):
        """找战斗开始按钮：先识别中央新版，再兼容下方旧版和 OCR。"""
        templates = (
            # 新版按钮中心约 (640, 360)，素材尺寸明显大于旧版。
            ("legion/btn_battle_start_large.png", 450, 830, 250, 470),
            ("legion/btn_battle_start.png", 450, 1000, 480, 700),
        )
        for tpl, x_min, x_max, y_min, y_max in templates:
            threshold = 0.9 if tpl.endswith("_large.png") else 0.85
            result, scale = ctx.find_scale(tpl, threshold=threshold)
            if result is None:
                continue
            (bx, by), score = result
            if x_min <= bx <= x_max and y_min <= by <= y_max:
                return (bx, by)
        return _LegionGekihaTask._find_battle_start_by_ocr(ctx)

    @staticmethod
    def _parse_stage_name(text):
        t = re.sub(r"[\s|｜丨]", "", str(text)).upper()
        m = _LegionGekihaTask._STAGE_RE.match(t)
        if not m:
            return None
        label = m.group(1)
        if label.isdigit():
            return label, int(label)
        digits = label[2:]
        return label, 4 + (int(digits) - 1 if digits else 0)

    def _read_stage_rows(self, ctx):
        """读取当前可见的关卡卡信息行 y（新版界面不依赖日文识别）。

        每张卡有两行横跨右侧的长文字（参加人数 / 報酬受取可能数），
        按“x0 在左侧、宽度≥300”收集后聚簇，取每组最下面一行作为卡片位置。
        """
        ctx.screenshot()
        ys = []
        for _t, x0, y0, x1, y1, _s in read_text_boxes(ctx._last_screen):
            # 新版信息行从 x≈764 开始，旧版则约 x≈520；两者都属于
            # 卡片右侧的大文本块，放宽 x 范围但仍要求足够宽度。
            if 480 <= x0 <= 1000 and 120 <= y0 <= 700 and (x1 - x0) >= 300:
                ys.append((y0 + y1) // 2)
        ys.sort()
        clusters = []
        for y in ys:
            if clusters and y - clusters[-1][-1] < 80:
                clusters[-1].append(y)
            else:
                clusters.append([y])
        rows = [c[-1] for c in clusters]
        if rows:
            ctx.logger.info("读取到关卡行: " + ", ".join(str(y) for y in rows))
        return rows

    def _scroll_stage_list_top(self, ctx):
        for _ in range(5):
            rows = self._read_stage_rows(ctx)
            if rows and rows[0] <= 260:
                return
            ctx.device.swipe(900, 200, 900, 600, 400)
            time.sleep(2)

    def _scroll_stage_list_down(self, ctx):
        ctx.screenshot()
        before = ctx._last_screen.copy()
        ctx.device.swipe(900, 600, 900, 200, 400)
        time.sleep(2)
        ctx.screenshot()
        diff = cv2.absdiff(before, ctx._last_screen).mean()
        return diff > 5

    def _back_to_stage_list(self, ctx):
        for _ in range(6):
            ctx.screenshot()
            if self._is_target_stage_list(ctx):
                return True
            if close_content_popup(ctx):
                continue
            ctx.device.key("BACK")
            time.sleep(3)
        return self._is_target_stage_list(ctx)

    @staticmethod
    def _dismiss_error_popup(ctx):
        """网络错误弹窗（ネットワークエラー）：点 OK 关闭。"""
        if handle_network_error(ctx):
            return True
        ctx.screenshot()
        result = ctx.find("legion/btn_error_ok.png", threshold=0.85)
        if result is not None:
            (ex, ey), score = result
            if 450 <= ex <= 800 and ey >= 550:
                ctx.logger.info(f"发现网络错误弹窗，点 OK 关闭 ({ex},{ey})")
                ctx.click(ex, ey, sleeptime=3)
                return True
        return False

    @staticmethod
    def _click_ok_by_ocr(ctx, y_min=0, y_max=720):
        """用 OCR 找屏幕上的「OK」文字并点击（通用确认框）。"""
        return click_ok_by_ocr(ctx, y_min=y_min, y_max=y_max)

    @staticmethod
    def _detect_and_handle_failure(ctx):
        """检测战斗失败：LOSE 标记或「失敗」文字出现时，点「出撃準備」返回并取消本关。"""
        ctx.screenshot()
        if ctx.find("legion/fail_lose.png", threshold=0.8) is not None:
            ctx.logger.warn("检测到战斗失败（LOSE 标记）")
            _LegionGekihaTask._click_fail_return(ctx)
            return True
        for text, cx, cy, score in read_text(ctx._last_screen):
            if "LOSE" in text.upper() or "失敗" in text or "FAIL" in text.upper():
                ctx.logger.warn(f"OCR 检测到战斗失败（{text}）")
                _LegionGekihaTask._click_fail_return(ctx)
                return True
        return False

    @staticmethod
    def _click_fail_return(ctx):
        """失败界面：点「出撃準備」返回准备界面，再点主页按钮回首页。"""
        result = ctx.find("legion/btn_fail_sortieprep.png", threshold=0.8)
        if result is not None:
            (fx, fy), score = result
            ctx.logger.info(f"点击「出撃準備」({fx},{fy})")
            ctx.click(fx, fy, sleeptime=5)
        else:
            ctx.logger.info("没找到「出撃準備」按钮，按固定位置点击")
            ctx.click(808, 532, sleeptime=5)
        # 等待返回准备界面，然后点右上角主页按钮回首页
        time.sleep(3)
        ctx.screenshot()
        if click_home_button(ctx):
            ctx.logger.info("已点主页按钮返回首页")
        else:
            ctx.logger.info("没找到主页按钮，按固定位置点击")
            ctx.click(1181, 35, sleeptime=5)

    def _find_target_stage_title(self, ctx):
        """在阶段页左侧标题栏中匹配当前任务标题。

        ``title_aram.png``/``title_clion.png`` 也能匹配菜单卡片标题。
        因此不能直接使用 ``ctx.find_scale`` 的全屏最佳结果：菜单滚动后
        目标卡片可能恰好出现在顶部，从而被当成阶段列表。阶段页标题固定
        在左上内容区（模板中心 x 约 150、y 约 165），这里只在该区域取
        最高匹配，明确排除菜单卡片（中心 x 约 440）。
        """
        screen = ctx._last_screen
        if screen is None:
            screen = ctx.screenshot()
        if screen is None:
            return None
        height, width = screen.shape[:2]

        try:
            template, mask = ctx.template(self.title_template)
        except (FileNotFoundError, OSError):
            # 素材缺失时不能放宽到通用阶段列表，否则可能误打其它外征。
            return None
        if template is None:
            return None

        # 阶段页标题与菜单横幅使用不同的布局。优先使用从实机阶段页
        # 提取的标题参考图，并只比较深色文字像素，避免几乎相同的浅色
        # 背景把波利塔斯/其它任务误判为当前任务。
        stage_title_templates = {
            "legion/title_aram.png": "legion/stage_title_aram.png",
            "legion/title_clion.png": "legion/stage_title_clion.png",
        }
        stage_name = stage_title_templates.get(self.title_template)
        if stage_name:
            try:
                stage_tpl, _stage_mask = ctx.template(stage_name)
                gray_tpl = cv2.cvtColor(stage_tpl, cv2.COLOR_BGR2GRAY)
                binary_tpl = (gray_tpl < 175).astype("uint8") * 255
                # 参考图裁剪自 (x=15..300, y=130..170)，允许少量加载帧偏移。
                x0, x1 = 0, min(width, 320)
                y0, y1 = 115, min(height, 205)
                region = screen[y0:y1, x0:x1]
                gray_region = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
                binary_region = (gray_region < 175).astype("uint8") * 255
                if (binary_region.shape[0] >= binary_tpl.shape[0]
                        and binary_region.shape[1] >= binary_tpl.shape[1]):
                    matched = cv2.matchTemplate(
                        binary_region, binary_tpl, cv2.TM_CCORR_NORMED)
                    _mn, max_score, _ml, max_loc = cv2.minMaxLoc(matched)
                    if max_score >= 0.93:
                        cx = x0 + max_loc[0] + binary_tpl.shape[1] // 2
                        cy = y0 + max_loc[1] + binary_tpl.shape[0] // 2
                        return (cx, cy), float(max_score), 1.0
            except (FileNotFoundError, OSError, cv2.error):
                # 旧安装没有参考图时继续走下面的兼容匹配逻辑。
                pass

        # 阶段页左侧标题的固定区域。保留少量上下余量以适应不同加载帧，
        # 但 x 方向只覆盖标题栏，避免菜单卡片标题（x≈291 起）。
        x0, x1 = 0, min(width, 320)
        y0, y1 = 80, min(height, 220)
        region = screen[y0:y1, x0:x1]
        if region.size == 0:
            return None

        best = None
        # 分辨率固定为 1280x720，通常 1.0 倍即可；保留同一组小范围
        # 缩放以兼容模拟器首次渲染时的 0.9/1.1 倍字体。每个候选都在
        # 左侧裁剪区内计算，不会被右侧相似按钮抢走最佳分数。
        for scale in (0.85, 0.9, 1.0, 1.1, 1.2):
            tw = max(1, int(template.shape[1] * scale))
            th = max(1, int(template.shape[0] * scale))
            if tw > region.shape[1] or th > region.shape[0]:
                continue
            interpolation = cv2.INTER_LINEAR if scale >= 1.0 else cv2.INTER_AREA
            resized = cv2.resize(template, (tw, th), interpolation=interpolation)
            resized_mask = None
            if mask is not None:
                resized_mask = cv2.resize(mask, (tw, th), interpolation=interpolation)
            if resized_mask is not None:
                matched = cv2.matchTemplate(
                    region, resized, cv2.TM_CCORR_NORMED, mask=resized_mask)
            else:
                matched = cv2.matchTemplate(region, resized, cv2.TM_CCOEFF_NORMED)
            _min_score, max_score, _min_loc, max_loc = cv2.minMaxLoc(matched)
            if not (max_score == max_score):  # NaN guard for blank frames
                continue
            candidate = (float(max_score), scale, x0 + max_loc[0] + tw // 2,
                         y0 + max_loc[1] + th // 2)
            if best is None or candidate[0] > best[0]:
                best = candidate

        # On the Politas stage screenshot the cross-task score is ~0.56;
        # 0.62 leaves room for antialiasing while rejecting that page.
        if best is None or best[0] < 0.62:
            return None

        # Aram and Clion headings share the same typography/background.  A
        # loose threshold alone can therefore make one template match the
        # other (especially on antialiased frames).  Compare the best score
        # against the sibling title in the same crop and require a small
        # margin.  This is deliberately conservative: a doubtful frame is
        # treated as "not our list" and the caller waits for the next frame.
        sibling = (
            "legion/title_clion.png"
            if self.title_template.endswith("title_aram.png")
            else "legion/title_aram.png"
        )
        try:
            sibling_template, sibling_mask = ctx.template(sibling)
        except (FileNotFoundError, OSError):
            sibling_template, sibling_mask = None, None
        sibling_best = None
        if sibling_template is not None:
            for scale in (0.85, 0.9, 1.0, 1.1, 1.2):
                tw = max(1, int(sibling_template.shape[1] * scale))
                th = max(1, int(sibling_template.shape[0] * scale))
                if tw > region.shape[1] or th > region.shape[0]:
                    continue
                interpolation = cv2.INTER_LINEAR if scale >= 1.0 else cv2.INTER_AREA
                resized = cv2.resize(
                    sibling_template, (tw, th), interpolation=interpolation)
                resized_mask = None
                if sibling_mask is not None:
                    resized_mask = cv2.resize(
                        sibling_mask, (tw, th), interpolation=interpolation)
                if resized_mask is not None:
                    matched = cv2.matchTemplate(
                        region, resized, cv2.TM_CCORR_NORMED, mask=resized_mask)
                else:
                    matched = cv2.matchTemplate(
                        region, resized, cv2.TM_CCOEFF_NORMED)
                _min_score, max_score, _min_loc, _max_loc = cv2.minMaxLoc(matched)
                if max_score == max_score and (
                        sibling_best is None or max_score > sibling_best):
                    sibling_best = float(max_score)
            if sibling_best is not None and best[0] - sibling_best < 0.035:
                return None
        score, scale, cx, cy = best
        return (cx, cy), score, scale

    def _is_target_stage_list(self, ctx):
        """判断是否为 *当前* 外征任务的阶段列表。

        通用阶段布局只证明“某个外征列表”存在，不能区分エアラム、
        クリオン和ポリタス。先复用通用结构判定，再要求左侧标题模板与
        ``self.title_template`` 匹配；这样进入、结算返回和 BACK 恢复都不会
        在错误任务页面继续点击关卡。
        """
        ctx.screenshot()
        if not self._stage_list_structure_present(ctx):
            return False
        title = self._find_target_stage_title(ctx)
        if title is None:
            return False
        (cx, cy), score, scale = title
        ctx.logger.info(
            f"确认{self.name}阶段列表：标题匹配 {score:.2f}（{cx},{cy}，缩放 {scale:.2f}）")
        return True

    @staticmethod
    def _stage_list_structure_present(ctx):
        """在已截图的画面上判断通用阶段列表结构。"""
        result, scale = ctx.find_scale("legion/btn_view_switch.png", threshold=0.8)
        if result is not None and scale is not None:
            return True
        ys = []
        for _text, x0, y0, x1, y1, _score in read_text_boxes(ctx._last_screen):
            if 480 <= x0 <= 1000 and 120 <= y0 <= 700 and (x1 - x0) >= 300:
                ys.append((y0 + y1) // 2)
        clusters = []
        for y in sorted(ys):
            if clusters and y - clusters[-1][-1] < 80:
                clusters[-1].append(y)
            else:
                clusters.append([y])
        return len(clusters) >= 3

    @staticmethod
    def _is_stage_list(ctx):
        """兼容旧调用：判断任意撃破任務阶段列表（不区分任务）。

        新的外征流程必须调用 ``_is_target_stage_list``；保留这个通用
        包装仅供旧外部代码/调试使用，避免 API 兼容性回归。
        """
        ctx.screenshot()
        return _LegionGekihaTask._stage_list_structure_present(ctx)

    def _back_home(self, ctx):
        for _ in range(8):
            ctx.screenshot()
            if is_page(ctx, "home"):
                return
            if close_content_popup(ctx):
                continue
            if click_home_button(ctx):
                continue
            ctx.device.key("BACK")
            time.sleep(2)


class ClearAramStages(_LegionGekihaTask):
    """エアラム撃破任務（原外征撃破任務）。"""

    def __init__(self):
        super().__init__(
            name="エアラム撃破任務",
            title_template="legion/title_aram.png",
        )


class ClearClionStages(_LegionGekihaTask):
    """クリオン撃破任務。"""

    def __init__(self):
        super().__init__(
            name="クリオン撃破任務",
            title_template="legion/title_clion.png",
        )


# 兼容旧配置和外部导入；新流程统一使用 ClearClionStages。
ClearMelissaStages = ClearClionStages


# ============================================================
# 外征任务（通用版）：不认 boss 名
# ============================================================
#
# 外征菜单里卡片标题（エアルム/クリオン/ファルクス…）会随活动轮换，用写死的
# 标题模板必然会失配。这里改为识别每张卡上完全相同的「撃破任務」logo
# （与 boss 名无关），遍历菜单里所有卡片，只清理「報酬受取可能数 = 1/1」
# 的可领奖关卡；没有奖励的练习任务（報酬 -/-）自然被跳过。boss 名怎么变都不用改代码。
GEKIHA_LOGO = "legion/gekiha_logo.png"
LEGION_VIEW_SWITCH = "legion/btn_view_switch.png"
TITLE_RETURN_TITLE = "popup/title_return_title.png"
_CARD_LOGO_MIN = 0.85


class ClearLegionGekiha(_LegionGekihaTask):
    """外征任务（通用）：按「撃破任務」logo 认卡片，与 boss 名无关。"""

    MAX_ROUNDS = 16

    def __init__(self):
        super().__init__(name="外征任务", title_template=GEKIHA_LOGO)

    # ---------- 页面判别 ----------
    @staticmethod
    def _is_stage_list_page(ctx):
        """关卡选择页左下角固定有「表示切替」按钮；外征菜单里没有。"""
        result = ctx.find(LEGION_VIEW_SWITCH, threshold=0.9)
        if result is None:
            return False
        (cx, cy), _score = result
        return cx <= 320 and cy >= 540

    def _is_target_stage_list(self, ctx):
        ctx.screenshot()
        return self._is_stage_list_page(ctx)

    @staticmethod
    def _is_title_return_dialog(ctx):
        """「タイトルに戻る」确认框：优先用标题栏模板，OCR 只作兜底
        （OCR 常常读不出该弹窗的「タイトル」，实测只读到「画面。」「？」）。"""
        ctx.screenshot()
        if ctx.find(TITLE_RETURN_TITLE, threshold=0.9) is not None:
            return True
        return _is_title_return_popup(ctx)

    def _dismiss_title_return(self, ctx):
        """外征菜单/列表里按返回键会弹出「タイトルに戻る」确认框；按返回取消。"""
        if self._is_title_return_dialog(ctx):
            ctx.logger.warn("检测到「返回标题」确认框，按返回取消")
            ctx.device.key("BACK")
            time.sleep(2)
            return True
        return False

    def _wait_stage_list_page(self, ctx, tries=8):
        """点击卡片后等待关卡列表页加载完成（进页面有多帧过渡）。"""
        for _ in range(tries):
            if self._dismiss_title_return(ctx):
                return False
            if handle_network_error(ctx) or handle_download_popup(ctx):
                continue
            if self._is_stage_list_page(ctx):
                return True
            time.sleep(1.5)
        return self._is_stage_list_page(ctx)

    def _is_legion_menu(self, ctx):
        """只用强信号判断是否在外征菜单：菜单标题模板命中且不在关卡列表页。

        基类的 OCR 兜底会把关卡列表页也当成菜单（列表页也有「外征任務」字样），
        通用任务必须区分，否则进不去/卡在列表页。
        """
        ctx.screenshot()
        if self._is_stage_list_page(ctx):
            return False
        result = ctx.find_scale("legion/menu_title.png", threshold=0.8)
        return (result is not None and result[1] is not None
                and result[1] >= 0.9)

    # ---------- 菜单卡片识别 ----------
    @staticmethod
    def _scan_cards(ctx):
        """扫描当前菜单视图，返回 [(cx, cy, 是否还有可领奖励)]（按 y 从上到下）。

        用「撃破任務」logo 定位卡片（与 boss 名无关）；再看卡片右侧
        「報酬受取可能ステージ数 X/Y」的分子是否 >0，判断是否还有奖励可领。
        """
        ctx.screenshot()
        screen = ctx._last_screen
        if screen is None:
            return []
        tpl, mask = ctx.template(GEKIHA_LOGO)
        if mask is not None:
            res = cv2.matchTemplate(screen, tpl, cv2.TM_CCORR_NORMED, mask=mask)
        else:
            res = cv2.matchTemplate(screen, tpl, cv2.TM_CCOEFF_NORMED)
        th, tw = tpl.shape[:2]
        ys, xs = np.where(res >= _CARD_LOGO_MIN)
        clusters = []
        for x, y in zip(xs.tolist(), ys.tolist()):
            score = float(res[y, x])
            cy, cx = y + th // 2, x + tw // 2
            # 卡片上的「撃破任務」logo 在左侧卡片区（x≈426）；
            # 关卡列表页左上的大 logo 在 x≈170，这里排除掉。
            if not (250 <= cx <= 720):
                continue
            for c in clusters:
                if abs(cy - c[0]) < 40:
                    if score > c[2]:
                        c[0], c[1], c[2] = cy, cx, score
                    break
            else:
                clusters.append([cy, cx, score])
        clusters.sort(key=lambda c: c[0])

        rewards = []
        for text, x0, y0, x1, y1, _s in read_text_boxes(screen):
            if x0 < 1040:
                continue
            m = re.search(r"(\d+)\s*/\s*(\d+)", text)
            if m:
                rewards.append(((y0 + y1) // 2, int(m.group(1))))

        out = []
        for cy, cx, _score in clusters:
            bottom = cy + th // 2
            claimable = False
            for ry, numerator in rewards:
                if bottom - 12 <= ry <= bottom + 190:
                    claimable = numerator > 0
                    break
            out.append((cx, cy, claimable))
        return out

    # ---------- 菜单滚动 ----------
    @staticmethod
    def _scroll_menu_top(ctx):
        # 外征菜单卡片不多，向下滑到底（回到顶部）几次即可
        for _ in range(4):
            ctx.device.swipe(900, 220, 900, 620, 400)
            time.sleep(1.2)

    @staticmethod
    def _scroll_menu_down(ctx):
        ctx.screenshot()
        before = ctx._last_screen.copy()
        ctx.device.swipe(900, 620, 900, 220, 400)
        time.sleep(1.5)
        ctx.screenshot()
        return cv2.absdiff(before, ctx._last_screen).mean() > 5

    # ---------- 菜单进出 ----------
    def _enter_legion_menu(self, ctx):
        for _ in range(12):
            ctx.screenshot()
            if self._is_legion_menu(ctx):
                return True
            if self._dismiss_title_return(ctx):
                continue
            if handle_network_error(ctx):
                continue
            if handle_download_popup(ctx):
                continue
            if close_content_popup(ctx):
                continue
            if is_page(ctx, "home"):
                ctx.click(1175, 613, sleeptime=5)
            elif is_page(ctx, "battle"):
                ctx.click(922, 597, sleeptime=5)
            elif ctx.find("loading/loading_mark.png", threshold=0.8) is not None:
                time.sleep(3)
            else:
                ctx.device.key("BACK")
                time.sleep(2)
        return self._is_legion_menu(ctx)

    def _back_to_menu(self, ctx):
        # 先在原地等一帧：可能已经在菜单
        for _ in range(3):
            ctx.screenshot()
            if self._is_legion_menu(ctx):
                return True
            time.sleep(1)
        # 只按一次返回（列表页 → 菜单），然后轮询等待，避免连续按返回触发「返回标题」
        ctx.device.key("BACK")
        for _ in range(10):
            time.sleep(1.5)
            ctx.screenshot()
            if self._is_legion_menu(ctx):
                return True
            if self._dismiss_title_return(ctx):
                time.sleep(1.5)
                continue
            if handle_network_error(ctx) or handle_download_popup(ctx):
                continue
            if close_content_popup(ctx):
                continue
            if self._is_stage_list_page(ctx):
                ctx.device.key("BACK")
        return self._is_legion_menu(ctx)

    # ---------- 只清理可领奖关卡 ----------
    def _read_claimable_rows(self, ctx):
        """返回当前关卡列表里「報酬受取可能数 X/1」且 X>0 的关卡行 y。"""
        ctx.screenshot()
        items = []
        for text, x0, y0, x1, y1, _s in read_text_boxes(ctx._last_screen):
            if 480 <= x0 <= 1000 and 120 <= y0 <= 700 and (x1 - x0) >= 300:
                items.append(((y0 + y1) // 2, text))
        items.sort()
        clusters = []
        for cy, text in items:
            if clusters and cy - clusters[-1][-1][0] < 80:
                clusters[-1].append((cy, text))
            else:
                clusters.append([(cy, text)])
        rows = []
        for c in clusters:
            joined = "".join(t for _cy, t in c)
            m = re.search(r"(\d+)\s*/\s*(\d+)", joined)
            if m and int(m.group(1)) > 0:
                rows.append(c[-1][0])
        return rows

    def _clear_claimable_stages(self, ctx):
        processed = set()
        for _ in range(12):
            self._scroll_stage_list_top(ctx)
            rows = [y for y in self._read_claimable_rows(ctx) if y not in processed]
            if not rows:
                return True
            line_y = rows[0]
            processed.add(line_y)
            self.processed_count += 1
            ctx.logger.info(f"=== 处理可领奖关卡 (y={line_y}) ===")
            ok = self._clear_one_stage_at(ctx, line_y)
            if self.failed:
                ctx.logger.warn("战斗失败，停止外征任务")
                return False
            if not ok:
                ctx.logger.warn(f"关卡 y={line_y} 未完成，停止当前卡片")
                return False
            if not self._back_to_stage_list(ctx):
                ctx.logger.warn("未能回到关卡列表")
                return False
        return True

    # ---------- 主流程 ----------
    def on_run(self, ctx):
        self.failed = False
        self.aborted = False
        self.processed_count = 0
        self.completed = False
        self.had_loss = False
        if not ensure_home(ctx):
            ctx.logger.warn("无法进入首页，结束外征任务")
            return self.skip("无法进入首页，结束外征任务", ctx)
        if not self._enter_legion_menu(ctx):
            ctx.logger.warn("无法进入外征菜单，结束外征任务")
            self._back_home(ctx)
            return self.skip("无法进入外征菜单，结束外征任务", ctx)

        tried = []
        for _ in range(self.MAX_ROUNDS):
            if not self._is_legion_menu(ctx):
                if not self._enter_legion_menu(ctx):
                    ctx.logger.warn("未能回到外征菜单，结束")
                    break
            found = self._find_next_claimable(ctx, tried)
            if found is None:
                ctx.logger.info("外征菜单已无未处理的可领奖任务，结束")
                break
            key, cx, cy = found
            tried.append(key)
            ctx.logger.info(f"=== 打开外征卡片 ({cx},{cy})，处理可领奖关卡 ===")
            ctx.click(cx, cy, sleeptime=6)
            if not self._wait_stage_list_page(ctx):
                ctx.logger.warn("未能进入关卡列表，跳过该卡片")
                self._back_to_menu(ctx)
                continue
            self._clear_claimable_stages(ctx)
            if self.failed:
                # 单关战斗失败：跳过该卡片，继续处理其它外征任务（失败关不重试）
                self.had_loss = True
                self.failed = False
                ctx.logger.warn("本关战斗失败，跳过并继续其它外征任务")
                continue
            if not self._back_to_menu(ctx):
                ctx.logger.warn("未能返回外征菜单")
                break

        self.completed = True
        if self.had_loss:
            ctx.logger.warn("注意：本次有外征关卡战斗失败（未重试）")
        if not self._back_home(ctx):
            ctx.logger.warn("外征任务结束但未能回到首页")
            self.status = self.STATUS_SKIP

    def _find_next_claimable(self, ctx, tried):
        """回到菜单顶部后向下扫描，返回最上方一张"可领奖且未处理过"的卡片。

        返回 (fp, cx, cy)。fp 是与滚动位置无关的卡片指纹，用于避免重复处理
        （尤其是"战斗失败但仍显示可领奖"的卡片，否则会反复重打）。
        """
        self._scroll_menu_top(ctx)
        for _ in range(6):
            for cx, cy, ok in self._scan_cards(ctx):
                if not ok:
                    continue
                fp = self._card_fp(ctx, cx, cy)
                if any(self._fp_same(fp, old) for old in tried):
                    continue
                return fp, cx, cy
            if not self._scroll_menu_down(ctx):
                break
        return None

    @staticmethod
    def _card_fp(ctx, cx, cy):
        """卡片指纹（与滚动位置无关）：以「撃破任務」logo 为中心裁一块
        （含卡片标题/插画，不含会变化的领奖数行），取 24x12 均值二值图。"""
        screen = ctx._last_screen
        if screen is None:
            return None
        h, w = screen.shape[:2]
        y0, y1 = max(0, cy - 110), min(h, cy + 30)
        x0, x1 = max(0, cx - 160), min(w, cx + 240)
        patch = screen[y0:y1, x0:x1]
        if patch.size == 0:
            return None
        small = cv2.resize(cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY), (24, 12))
        return (small > small.mean()).astype("uint8").flatten()

    @staticmethod
    def _fp_same(a, b, tol=12):
        """指纹是否同一张卡（实测：同卡汉明距离约 4，不同卡 24+）。"""
        if a is None or b is None:
            return False
        return int(np.count_nonzero(a != b)) <= tol
