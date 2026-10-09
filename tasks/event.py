# -*- coding: utf-8 -*-
"""活动任务：一次性全清限时活动（以「残光のティアドロップ」为模板）。

首页 → 出撃 → イベント → 期間限定 → ステージ選択
流程：清空 ストーリー（剧情全跳）→ 清空メモリアストーリー（若有）
      → 清空バトル NORMAL → 切 HARD 再清一遍 → 点「報酬」领奖励
      → 回首页。

战斗复用外征基类(_LegionGekihaTask)的「关卡行识别 / 滚动 / 单关编队出撃 / 结算」；
剧情复用 tasks.story 的「跳过剧情 / 首通奖励」。
"""

import re
import time

from core.navigation import ensure_home, click_home_button
from core.ocr import read_text
from core.pages import is_page, is_title_screen
from core.popups import (click_ok_by_ocr, close_content_popup,
                         handle_download_popup, handle_network_error)
from core.vision import find_red_dots

from tasks.story import (_handle_download, _is_reward_popup, _is_story_screen,
                         _skip_story)
from tasks.quests import _LegionGekihaTask


# 坐标（1280x720）
HOME_SORTIE = (1214, 643)   # 首页右下角 出撃
BATTLE_EVENT = (921, 227)   # 出撃页 → イベント(EVENT BATTLE)
TAB_STORY = (709, 122)      # ステージ選択 顶部 ストーリー
TAB_MEMORIA = (992, 122)    # メモリアストーリー
TAB_BATTLE = (1113, 122)    # バトル
TABS = {"ストーリー": TAB_STORY, "メモリアストーリー": TAB_MEMORIA, "バトル": TAB_BATTLE}
REWARD_BTN = (175, 628)     # 左栏 報酬
HARD_BTN = (312, 667)       # 左下 HARD / 模式切换
UNIT_SELECT_BTN = (1155, 655)   # ステージ情報 右下 ユニット選択
MAIN_UNIT_SLOT = (320, 355)     # ユニット選択 主单位空槽 (+)
SORTIE_BTN = (1180, 625)        # ユニット選択 出撃
AUTO_BTN = (54, 674)            # 战斗 AUTO
STAGE_ROW_X = 890               # 关卡列表行 x（卡片主体可点区）
_SCROLL_STEP = 90               # 小步滑动距离（约半张卡），一点一点滑，避免一步跨太多跳过关卡

STORY_CARD_X = (850, 1120)  # 剧情卡片两列
STORY_CARD_TOP = 180
STORY_CARD_BOTTOM = 690

# ---- イベント 一览页（限时活动列表）----
BACK_ARROW = (38, 36)               # 子页面左上角返回箭头
CATEGORY_RIGHT_X = 300              # 左侧分类栏的右边界
CAT_LIMITED = "期間限定"             # 限时活动分类
CAT_DAILY = "デイリー"               # 每日活动分类（带「一括スキップ」）
# 左侧分类栏的固定位置（OCR 认不出「デイリー」，用它兜底）
CATEGORY_COORDS = {"期間限定": (61, 105), "デイリー": (61, 173),
                   "常時開催": (61, 244), "鍵": (61, 313)}
EVENT_CARD_COL_X = (495, 995)       # 活动卡片两列的中心 x
EVENT_LIST_Y = (120, 690)           # 活动列表卡片可视区间
EVENT_LIST_SCROLL_X = 745           # 两列之间的空隙，滚动时用它，避免蹭到卡片
LIST_SCROLL_STEP = 300              # 活动列表每次向下滚多少像素（约一屏）
# 列表“真的滚动了”的最小像素差：实测滚一屏≈52，到底回弹≈9.7
LIST_MOVE_MIN = 25
DAILY_BULK_SKIP = (1210, 664)       # デイリー 右下角「一括スキップ」
AP_MIN = 10                         # 清关前 AP 低于这个值就停手（不花 AP 的关卡不受影响）
# 卡片下方那条信息行里会出现的关键词（用来把「行」切出来）
FOOTER_KEYS = ("終了", "細", "詳", "常時開催中", "挑戦可能数", "挑可能数",
               "チャージ可能数")

_STAGE_TAG = re.compile(r"ステージ\s*0*(\d+)")
_STORY_TAG = re.compile(r"(\d+)\s*話[:：]")
# 关卡号识别：普通 NORMAL/HARD 为 1~2 位数字（OCR 常把「06」读成「0」），
# HARD 附带 EX 关卡用「EX1~EX5」命名（OCR 有时把 EX 与数字拆成两个 token）。
# 战斗卡标题形如「ステージEX1」，OCR 有时会连「ステージ」前缀一起读出来，
# 故允许标题前有可选的「ステージ」(含常见误读)前缀，否则会误判“列表到底”而漏关卡。
# 实测（2026-09-12，はんこ 導入記念）OCR 还会把「ステージ01」读成只剩尾巴的「ジ01」，
# 所以前缀还要带上「ジ」「テージ」这类残片，否则整行关卡被漏掉。
_STAGE_PREFIX = r"(?:ステージ|ステ一ジ|ステータ|テージ|ジ)?\s*"
_STAGE_NUM_RE = re.compile(_STAGE_PREFIX + r"(?:EX\s*)?\d{1,2}", re.IGNORECASE)
_STAGE_EX_RE = re.compile(r"EX", re.IGNORECASE)

# 关卡卡“没被灰锁”的亮度下限（HSV 的 V）。实测可打的卡 V≈210~235，
# 锁定的灰卡 V≈120~140，取 170 能把两者分开。
STAGE_CARD_MIN_VALUE = 170
# 用亮度兜底时，关卡行下方要留出这么高的画面，确保卡片右上角的
# 「COMPLETE 章 / 粉红点」也在屏幕内（否则会把屏幕外看不到章的关当成可打）。
STAGE_CARD_BADGE_MARGIN = 90

# 关卡卡片标题行（「ステージ01」/「プレストーリー山内昇依編」）所在区域。
# 只取标题那一行：下面 60px 处是信息行（AP / MISSION / 推奨総戦闘力），别混进来。
STORY_ROW_TITLE_X = (560, 800)
STORY_ROW_TITLE_Y = (100, 175)
STORY_ROW_CLICK_DY = 45     # 标题往下 45px ≈ 卡片中部（标题和信息行之间）
_ROW_TITLE_SKIP = ("MODE", "AP", "MISSION", "推奨", "総戦闘力", "COMPLETE",
                   "EVENTMISSION", "表示切替")


# 动画播放页控件自动隐藏时画面几乎全黑：亮度低于这个值就当成“黑场”
BLANK_SCREEN_MAX_VALUE = 40
# 剧情/动画页“叫出按钮”要点的地方：右上角那个按钮位。
# 按钮隐藏时这里是 MENU（点一下就展开 AUTO/SKIP/LOG/SNS/CLOSE 一整条工具栏），
# 展开后同一个位置变成 CLOSE——但工具栏展开时 `_is_story_screen` 已经能认出 SKIP，
# 不会走到“叫出按钮”这一步（实测 2026-09-12 ふじ 食堂；点画面中间只会推进对话）。
STORY_REVEAL_BTN = (1203, 61)
# 「点一下叫出按钮」最多试几次：试完还是认不出就放弃这个剧情节点。
# 实测 2026-10-02：这条兜底没有上限时转过 18 轮 / 10 分 20 秒。
STORY_REVEAL_TRIES = 3


def _is_blank_screen(img, max_value=BLANK_SCREEN_MAX_VALUE):
    """画面是否几乎全黑（动画播放页控件自动隐藏 / 黑场）。

    实测 2026-09-12 ふじ 食堂：プレストーリー 的动画放几秒后控件会自己隐藏，
    此时整屏全黑、OCR 一个字都读不到，脚本会一直干等。点一下画面能把控件叫出来。
    """
    import cv2
    if img is None:
        return False
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    return float(hsv[:, :, 2].mean()) < max_value


def _pink_dots(img, min_area=6, max_size=45):
    """检测亮粉/玫红圆点（H 150~180）。返回 [(cx, cy, area)]，按面积降序。

    活动关卡「当前可打」那一关卡右上角有一颗这样的粉红点；`find_red_dots`
    只认正红(0-10/170-180)会漏掉它，故单独识别。
    """
    import cv2
    import numpy as np
    if img is None:
        return []
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (150, 110, 140), (180, 255, 255))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, _, stats, cents = cv2.connectedComponentsWithStats(mask, 8)
    dots = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if area >= min_area and max(w, h) <= max_size:
            dots.append((int(cents[i][0]), int(cents[i][1]), int(area)))
    dots.sort(key=lambda d: -d[2])
    return dots


def event_card_cells(img):
    """当前视野里活动卡片的点击点 [(x, y), ...]，按行自上而下、行内从左到右。

    卡片标题是美术字，OCR 读不出来；但每一行卡片下面都有一条信息行
    （「イベント終了まであと◯日　詳細 >」/「イベント常時開催中」/「挑戦可能数：…」），
    这些字 OCR 读得出来，用它们把「行」切出来，每行固定两张卡
    （左 x=495 / 右 x=995）。落点取信息行上方 130px：
    限时活动的大海报和デイリー 的横条卡都能落在卡面里。
    """
    if img is None:
        return []
    footers = []
    for t, cx, cy, s in read_text(img):
        if cy < EVENT_LIST_Y[0]:
            continue
        if any(k in t for k in FOOTER_KEYS):
            footers.append(cy)
    footers.sort()
    rows = []
    for cy in footers:
        if rows and abs(cy - rows[-1]) < 40:
            continue
        rows.append(cy)
    cells = []
    for fy in rows:
        click_y = max(fy - 130, EVENT_LIST_Y[0] + 10)
        for col_x in EVENT_CARD_COL_X:
            cells.append((col_x, click_y))
    return cells


def tab_pills(img, y0=104, y1=142, x0=500, x1=1220,
              min_width=120, max_width=340, frac=0.4, gap=12):
    """找关卡页顶部的标签按钮（圆角药丸），按 x 排序返回 [(x0, x1), ...]。

    标签文字是美术字，OCR 读不出来（实测「ストーリー」「バトル」都读不到），
    所以按形状/颜色找：**选中的标签是深蓝紫实心、未选中的是白色实心**。
    宽度过滤用来排除整块背景——背景也是浅色，但会连成一大片。
    标签顺序固定是 ストーリー(, メモリアストーリー), バトル。
    """
    if img is None:
        return []
    band = img[y0:y1, x0:x1].astype(int)
    b, g, r = band[:, :, 0], band[:, :, 1], band[:, :, 2]
    masks = [
        ((abs(r - 84) < 45) & (abs(g - 93) < 45) & (abs(b - 161) < 45)).mean(axis=0) > frac,
        ((r > 232) & (g > 236) & (b > 236)).mean(axis=0) > frac,
    ]
    raw = []
    for mask in masks:
        start = last = None
        for i, value in enumerate(mask):
            if value:
                if start is None:
                    start = i
                last = i
            elif start is not None and i - last > gap:
                raw.append((start + x0, last + x0))
                start = None
        if start is not None:
            raw.append((start + x0, last + x0))
    pills = []
    for left, right in sorted(raw):
        if not (min_width <= right - left <= max_width):
            continue
        if pills and left - pills[-1][1] < 30:      # 同一颗药丸被拆成两段
            pills[-1] = (pills[-1][0], right)
            continue
        pills.append((left, right))
    return pills


# 卡片指纹取样区：从点击点往上取 190px ≈ 卡面主体（信息行在上方 130px 处）。
CARD_FP_UP = 190
CARD_FP_DOWN = 6
CARD_FP_HALF_W = 150
# 判定阈值（实测 2026-09-12，活动列表 6 张卡）：
#   同一张卡在不同滚动位置（锚点错位 0~24px）：网格色 ≤416、aHash ≤37
#   不同卡之间：网格色 ≥760、aHash ≥45
# 两个描述子都要在阈值内才算同一张卡（阈值偏向“判成新卡”）：
# 重复进一个已清完的活动只是白跑一趟，漏掉一个活动才是真的少做内容。
CARD_COLOR_MAX = 540
CARD_HASH_MAX = 64


def card_fingerprint(img, cell):
    """卡片指纹：卡面主体的 4x3 网格平均色 + 16x16 灰度 aHash。

    列表滚动位置会变、同一张卡会在不同 y 上再次出现，靠坐标去重不可靠。
    旧版只用点击点周边 ±150x±55 的 aHash，遇到两件麻烦事（实测 2026-09-12）：
    信息行 OCR 的 y 抖动十几像素就让同一张卡产生 23~39 位差异（阈值 20）→ 判成
    新卡、反复点同一张；而并排的两张卡只差 17 位 → 判成已进过、直接漏掉。
    改成整块卡面 + 两个独立描述子（粗网格平均色认美术、aHash 认结构）后，
    同卡抖动距离很小、不同卡距离很大。
    """
    import cv2
    if img is None:
        return None
    x, y = cell
    x0, x1 = max(x - CARD_FP_HALF_W, 0), min(x + CARD_FP_HALF_W, img.shape[1])
    y0 = max(y - CARD_FP_UP, 0)
    y1 = min(y + CARD_FP_DOWN, img.shape[0])
    crop = img[y0:y1, x0:x1]
    if crop.size == 0:
        return None
    grid = cv2.resize(crop, (4, 3), interpolation=cv2.INTER_AREA)
    small = cv2.resize(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), (16, 16),
                       interpolation=cv2.INTER_AREA)
    return grid.reshape(-1, 3).astype(int), small < small.mean()


def same_card(a, b):
    """两个卡片指纹是否算同一张卡（两个描述子都在阈值内才算）。"""
    if a is None or b is None:
        return False
    grid_a, hash_a = a
    grid_b, hash_b = b
    if int(abs(grid_a - grid_b).sum()) > CARD_COLOR_MAX:
        return False
    return int((hash_a != hash_b).sum()) <= CARD_HASH_MAX


class _ClearEventBase(_LegionGekihaTask):
    """活动任务公共基类：入口识别 + 战斗/剧情共用工具。

    战斗按“只打带粉红红点的那一关”一关一关清；剧情全跳。两类逻辑拆成独立任务：
    战斗 = ClearEventBattle，剧情 = ClearEventStory；原 ClearEventStages 为兼容一并做。
    """

    def __init__(self, name="活动任务"):
        super().__init__(name=name, title_template="")  # 入口/页面识别被覆盖，模板不用
        self.aborted = False
        self.failed = False
        self.enter_failed = False       # 本次“点击关卡但进不去”（可能未解锁/加载失败）
        self.battle_blocked = False     # 战斗清关因进入不了关卡而受阻
        self.stopped_at_mode = None     # 受阻的难度（NORMAL/HARD）
        self.ap_blocked = False         # 因 AP 不足停下
        self.events_seen = 0            # 本次遍历过的活动卡片数
        self.incomplete = []            # 没能清干净的活动（如实上报用）
        self.current_card = None        # 当前活动的卡片坐标（重进时用）
        # 子类可以只做一半：ClearEventBattle 只清战斗、ClearEventStory 只清剧情
        self.do_battle = True
        self.do_story = True
        self.claim_reward = True

    # ---------- 入口 ----------
    def pre_condition(self, ctx):
        return True

    def _reset_state(self):
        self.failed = False
        self.aborted = False
        self.enter_failed = False
        self.battle_blocked = False
        self.stopped_at_mode = None
        self.ap_blocked = False
        self.events_seen = 0
        self.incomplete = []
        self.current_card = None
        self.processed_count = 0
        self.completed = False

    def _enter_event(self, ctx):
        """兼容旧调用：回到首页并进入活动ステージ選択；返回是否成功。"""
        if not ensure_home(ctx):
            ctx.logger.warn("无法进入首页，跳过活动任务")
            self.skip("无法进入首页，跳过活动任务", ctx)
            return False
        return self._enter_event_stage_select(ctx)

    def _run_all_events(self, ctx, what):
        """公共外壳：进活动一览 → 遍历所有活动卡 → 回首页收尾。"""
        self._reset_state()
        if not self._enter_event_list(ctx):
            return None
        self._process_event_cards(ctx)
        return self._finish(ctx, what)

    def _finish(self, ctx, what):
        """回首页、标记完成；若战斗受阻则如实上报“未完全清空”。"""
        if not self._back_home(ctx):
            ctx.logger.warn(f"{what}结束但未能回到首页")
            self.status = self.STATUS_SKIP
            return False
        self.completed = True
        if self.failed:
            return self.fail(
                f"{what}：有活动的战斗没打过去（共处理 {self.processed_count} 个节点）", ctx)
        if self.incomplete:
            return self.fail(
                f"{what}：未能完全清空 — {'、'.join(self.incomplete)}"
                f"（可能未解锁 / AP 不足），其余已清完"
                f"（共处理 {self.processed_count} 个节点）", ctx)
        ctx.logger.info(f"{what}完成，共处理 {self.processed_count} 个节点"
                        f"，遍历活动 {self.events_seen} 个")
        return True

    # ---------- 页面识别 ----------
    def _is_target_stage_list(self, ctx):
        """是不是活动的ステージ選択页（关卡选择页）。

        页面标题「ステージ選択」是白字美术字、OCR 读不出来，所以改用页面里的
        稳定信息判断：左侧信息栏 / 底部「表示切替」/ 关卡行的 AP・MISSION 信息。
        """
        ctx.screenshot()
        items = read_text(ctx._last_screen)
        joined = "".join(t for t, *_ in items).replace(" ", "")
        # 结算页（STAGE CLEAR / CLEAR BONUS）上也有 MISSION / AP / 两位数的掉落数量，
        # 会被下面「关卡行」兜底误判成关卡列表（2026-10-09 リリィスファンタジーゼロ
        # ステージ05：结算页被当成列表 → 「次へ」没点、整个活动只做了 1 个）。
        flat = re.sub(r"[^A-Z0-9]", "", joined.upper())
        if any(m in flat for m in ("STAGECLEAR", "CLEARBONUS", "BATTLEFIN")):
            return False
        if any(k in joined for k in ("MODE:", "報酬受取期間", "EVENTMISSION",
                                     "表示切替", "部隧構成", "部隊構成")):
            return True
        # 关卡行（ステージ01 / ジ01 / 01 / EX1）+ 关卡信息栏
        rows = [t for t, cx, cy, s in items
                if ClearEventStages._is_stage_label(t) and 560 <= cx <= 800
                and 90 <= cy <= 715]
        if rows and ("MISSION" in joined.upper() or "AP" in joined):
            return True
        stage_count = sum(
            1 for t, cx, cy, s in items
            if re.fullmatch(r"\d{2}", t.strip())
            and 560 <= cx <= 780 and 90 <= cy <= 710)
        if stage_count >= 2:
            return True
        return False

    def _enter_event_stage_select(self, ctx, rounds=40):
        """从任意画面回到活动关卡选择页。

        2026-10-07 加：每 5 轮打进度日志；连续 8 轮画面几乎没变化（且不是加载页）
        就提前放弃并存 `event_recover_stuck.png` —— 之前这种状态会静默空转
        40 轮（当天实测约 12 分钟）才放弃，日志里只有一片空白。
        """
        last = None
        same = 0
        for i in range(rounds):
            ctx.screenshot()
            if self._is_target_stage_list(ctx):
                return True
            if handle_download_popup(ctx) or handle_network_error(ctx) or close_content_popup(ctx):
                last, same = None, 0
                continue
            if i and i % 5 == 0:
                ctx.logger.info(f"重新进入活动页重试中（{i + 1}/{rounds}）")
            if ctx.find("loading/loading_mark.png", threshold=0.8) is not None:
                # 还在加载：不算“卡住”，等它
                last, same = None, 0
                time.sleep(2)
                continue
            if last is not None and _screens_same(last, ctx._last_screen):
                same += 1
                if same >= 8:
                    path = ctx.save_screenshot(ctx._last_screen, "event_recover_stuck.png")
                    ctx.logger.warn(
                        f"重新进入活动页连续 {same} 轮画面几乎没变化，提前放弃；"
                        f"截图已保存: {path}")
                    return False
            else:
                same = 0
            last = None if ctx._last_screen is None else ctx._last_screen.copy()
            if is_page(ctx, "home"):
                ctx.logger.info("首页点出撃")
                ctx.click(HOME_SORTIE[0], HOME_SORTIE[1], sleeptime=5)
                time.sleep(2)
            elif self._on_battle_menu(ctx):
                # 出撃页：等イベント按钮渲染后再点，过渡加载时不按返回
                if is_page(ctx, "battle"):
                    ctx.logger.info("出撃页点イベント")
                    ctx.click(BATTLE_EVENT[0], BATTLE_EVENT[1], sleeptime=5)
                    time.sleep(2)
                else:
                    time.sleep(2)
            elif self._on_event_menu(ctx):
                # イベント页：先保证「期間限定」分类被选中，再点活动卡
                self._click_text(ctx, "期間限定")
                if not self._click_event_card(ctx):
                    ctx.logger.warn("未找到活动卡，按返回")
                    ctx.device.key("BACK")
                    time.sleep(2)
                    continue
                # 点卡后给进入ステージ選択留加载时间，别在过渡时按返回
                time.sleep(5)
                continue
            else:
                # 未知画面/加载过渡：短暂等待，避免误按返回弹出去
                time.sleep(2)
        return self._is_target_stage_list(ctx)

    @staticmethod
    def _on_battle_menu(ctx):
        """出撃页：顶栏标题「出撃」在左上角。

        日文 OCR 常把「撃」读成「擎/撃」等，精确比较会漏判，改用「出」前缀匹配。
        首页/其它页左上角没有「出..」标题，不会误判。
        """
        for t, cx, cy, s in read_text(ctx._last_screen):
            if t.strip().startswith("出") and cy < 80:
                return True
        return False

    @staticmethod
    def _on_event_menu(ctx):
        """イベント页：只要看到「期間限定」菜单或「残光」活动卡就算。"""
        for t, cx, cy, s in read_text(ctx._last_screen):
            if "期間限定" in t or "残光" in t:
                return True
        return False

    def _click_event_card(self, ctx):
        """进入活动：点卡面中部（标题是美术字，不是可点区）。

        记着上一次点的是哪张卡（`current_card`），中途被打断需要重进时
        回到同一个活动，别跑偏到别的活动上去。
        """
        cell = self.current_card or (400, 300)
        ctx.logger.info(f"点击活动卡卡面 ({cell[0]},{cell[1]})")
        ctx.click(cell[0], cell[1], sleeptime=6)
        return True

    @staticmethod
    def _click_text(ctx, keyword):
        """在 OCR 文本里找 keyword 并点击；只点左侧(或全屏)区域。"""
        for t, cx, cy, s in read_text(ctx._last_screen):
            if keyword in t:
                ctx.logger.info(f"点击「{keyword}」@({cx},{cy})")
                ctx.click(cx, cy, sleeptime=4)
                return True
        return False

    def _has_tab(self, ctx, tab):
        ctx.screenshot()
        for t, cx, cy, s in read_text(ctx._last_screen):
            if t.strip() == tab and cy < 160:
                return True
        return False

    def _goto_tab(self, ctx, tab):
        """切到指定标签。

        标签文字始终都在顶部，无法靠 OCR 判断选中态，所以直接点一次
        （点已选中的标签也无害）。**位置按 OCR 找**：各活动的标签数量不同
        （残光 3 个、風紀 2 个），写死坐标会点空。

        实测 2026-09-12：はんこ 導入記念 / サバイバルアタック 这类活动**根本没有
        标签栏**。此时绝不能退回写死坐标 TABS —— (1113,122) 正落在第一张关卡卡上，
        一点就进了「ステージ情報」，后面的关卡识别全部落空、整个活动被当成“已清完”。
        所以只认 OCR/药丸识别出来的标签位置；一个都认不出来就不点。
        """
        for attempt in (1, 2):
            ctx.screenshot()
            positions = self._tab_positions(ctx)
            if not positions:
                # 页面没有标签栏：如果“当前标签”本来就是靠页面内容推断出来的，
                # 说明已经在目标页上，不需要点击。
                if tab in self._detect_tabs(ctx):
                    ctx.logger.info(f"页面没有标签栏（{tab} 由页面内容判定），无需切换")
                    return True
            else:
                pos = positions.get(tab)
                if pos is not None:
                    ctx.logger.info(f"切换到标签「{tab}」@({pos[0]},{pos[1]})")
                    ctx.click(pos[0], pos[1], sleeptime=3)
                    time.sleep(1)
                    return True
            # 找不到标签：可能是画面还停在剧情/结算页。实测 2026-09-18：活动 1 的 4 话
            # 剧情跳完后画面还在剧情里，在剧情页上找「バトル」标签必然失败，旧逻辑直接
            # 放弃 → 整个活动的战斗被跳过（上报「未能完全清空」）。先退回关卡选择页再试一次。
            if attempt == 1:
                ctx.logger.info(f"页面上暂时没有「{tab}」标签，先退回关卡选择页再试")
                if self._recover_to_stage_list(ctx):
                    continue
            break
        ctx.logger.warn(f"页面上没有「{tab}」标签")
        return False

    TITLE_RETURN_TITLE = "popup/title_return_title.png"

    @classmethod
    def _is_title_return_dialog(cls, ctx):
        """「タイトルに戻る」确认框：优先认标题栏模板，OCR 作兜底。

        OCR 常读不出标题里的「タイトル」，所以模板优先（外征任务同款素材）。
        """
        if ctx.find(cls.TITLE_RETURN_TITLE, threshold=0.9) is not None:
            return True
        joined = "".join(t for t, *_ in read_text(ctx._last_screen))
        return any(k in joined for k in ("タイトル", "戻ります", "戻り"))

    def _dismiss_title_return(self, ctx):
        """取消「タイトルに戻る」确认框（按返回键＝取消）；返回是否处理过。"""
        if not self._is_title_return_dialog(ctx):
            return False
        ctx.logger.warn("检测到「タイトルに戻る」确认框，按返回取消")
        ctx.device.key("BACK")
        time.sleep(2)
        return True

    def _recover_to_stage_list(self, ctx, rounds=2):
        """从残留的剧情/结算画面回到关卡选择页；返回是否已经站在关卡选择页。

        实测 2026-09-18：读完活动剧情后画面可能还停在剧情播放页（第 4 话 SKIP 之后
        还在播后续），这时去点「バトル」标签必然找不到 → 整个活动的战斗被跳过。

        轮次要克制：内层 `_back_to_stage_list` 自己最多转 8 轮，外层 4 轮叠起来
        实测一次要 6 分钟（2026-10-02 一天里「页面上没有「X」标签」出现 4 次 ≈ 25 分钟）。
        现在外层 2 轮，失败时存一张现场图再交给调用方。
        """
        for _ in range(rounds):
            ctx.screenshot()
            # 返回键按过头会弹「タイトルに戻る」确认框（实测 2026-09-18：活动遍历就是
            # 卡在这个框上——认不出关卡、找不到卡片，最后只处理了 1 个活动就收工）。
            if self._dismiss_title_return(ctx):
                continue
            if self._is_target_stage_list(ctx):
                return True
            if _is_story_screen(ctx):
                _skip_story(ctx)            # 残留剧情：接着跳完
                continue
            if _is_reward_popup(ctx):
                self._tap_ok(ctx, 500, 800, 590, 690)
                continue
            if handle_network_error(ctx):
                continue
            if self._back_to_stage_list(ctx):
                return True
        if not self._is_target_stage_list(ctx):
            path = ctx.save_screenshot(ctx._last_screen, "event_recover_stuck.png")
            ctx.logger.warn(f"退回关卡选择页失败，截图已保存: {path}")
        return self._is_target_stage_list(ctx)

    @staticmethod
    def _tab_positions(ctx):
        """当前关卡页顶部实际存在的标签及其坐标。

        标签文字 OCR 读不出来（美术字），所以先试 OCR（有些活动能读出来），
        读不到就用 `tab_pills` 按形状识别药丸按钮，再按固定顺序对号入座。
        """
        found = {}
        for t, cx, cy, s in read_text(ctx._last_screen):
            name = t.strip()
            if name in TABS and cy < 160:
                found[name] = (cx, cy)
        if found:
            return found
        pills = tab_pills(ctx._last_screen)
        if not pills:
            return {}
        if len(pills) >= 3:
            names = ("ストーリー", "メモリアストーリー", "バトル")
        elif len(pills) == 2:
            names = ("ストーリー", "バトル")
        else:
            names = ("ストーリー",)
        for (left, right), name in zip(pills, names):
            found[name] = ((left + right) // 2, 122)
        return found

    @staticmethod
    def _detect_tabs(ctx):
        """当前页面顶部有哪些标签（按固定顺序返回）。"""
        order = ("ストーリー", "メモリアストーリー", "バトル")
        positions = ClearEventStages._tab_positions(ctx)
        if positions:
            return [name for name in order if name in positions]
        # 一颗药丸都没认出来：页面里有「◯話」就是剧情页
        joined = "".join(t for t, *_ in read_text(ctx._last_screen))
        if re.search(r"\d+\s*話", joined):
            return ["ストーリー"]
        # 只剩关卡列表、没有标签栏 = 迷你/踏破活动（はんこ 導入記念、サバイバルアタック）。
        # 这里要老实返回“没有标签”，别假装有「バトル」标签：一假装就会去点写死坐标，
        # 点到关卡卡上把整段流程带偏（实测 2026-09-12）。
        return []

    # ---------- 活动一览（遍历「期間限定」里的所有活动） ----------
    @staticmethod
    def _is_event_list(ctx):
        """イベント 一览页：左侧分类栏（期間限定 / 常時開催）或右上「マル設定」。"""
        for t, cx, cy, s in read_text(ctx._last_screen):
            if any(k in t for k in ("期間限定", "常時開催", "マル設定", "于設定")):
                return True
        return False

    def _select_category(self, ctx, name):
        """点左侧的类别（期間限定 / デイリー / 常時開催）。"""
        ctx.screenshot()
        for t, cx, cy, s in read_text(ctx._last_screen):
            if name in t and cx <= CATEGORY_RIGHT_X:
                ctx.logger.info(f"选择活动类别「{name}」@({cx},{cy})")
                ctx.click(cx, cy, sleeptime=4)
                return True
        # 分类名是竖排小字，OCR 有时读不出来（实测「デイリー」读不到）→ 退回固定坐标
        pos = CATEGORY_COORDS.get(name)
        if pos is None:
            return False
        ctx.logger.info(f"没读到「{name}」，按固定位置点 {pos}")
        ctx.click(pos[0], pos[1], sleeptime=4)
        return True

    def _enter_event_list(self, ctx):
        """首页 → 出撃 → イベント → 「期間限定」列表。"""
        if not ensure_home(ctx):
            ctx.logger.warn("无法进入首页，跳过活动任务")
            self.skip("无法进入首页，跳过活动任务", ctx)
            return False
        for _ in range(40):
            ctx.screenshot()
            if self._is_event_list(ctx):
                self._select_category(ctx, CAT_LIMITED)   # 默认就是它，点了也无害
                return True
            if handle_download_popup(ctx) or handle_network_error(ctx) or close_content_popup(ctx):
                continue
            if is_page(ctx, "home"):
                ctx.logger.info("首页点出撃")
                ctx.click(HOME_SORTIE[0], HOME_SORTIE[1], sleeptime=5)
                time.sleep(2)
            elif self._on_battle_menu(ctx):
                if is_page(ctx, "battle"):
                    ctx.logger.info("出撃页点イベント")
                    ctx.click(BATTLE_EVENT[0], BATTLE_EVENT[1], sleeptime=5)
                    time.sleep(2)
                else:
                    time.sleep(2)
            else:
                time.sleep(2)
        ctx.logger.warn("无法进入活动一览页")
        return False

    @staticmethod
    def _scroll_list_top(ctx):
        """把活动列表滚到顶（滚不动就是到顶了）。"""
        import cv2
        for _ in range(10):
            before = ctx._last_screen.copy()
            ctx.device.swipe(EVENT_LIST_SCROLL_X, 200, EVENT_LIST_SCROLL_X, 520, 500)
            time.sleep(2)
            ctx.screenshot()
            if float(cv2.absdiff(before, ctx._last_screen).mean()) <= 5:
                break

    @staticmethod
    def _scroll_list_down(ctx, step):
        """活动列表向下滚 step 像素；返回画面是否**真的**滚动了。

        实测 2026-09-12：列表滚到底时会回弹一点点（4px，像素差≈9.7），而真正滚
        一屏是≈52。旧阈值 5 会把回弹当成滚动成功，于是同一屏被当成新一屏、
        反复重算坐标、循环空转。用「明显变化」才算滚动，回弹即视为到底。
        """
        import cv2
        before = ctx._last_screen.copy()
        target = max(120, 620 - int(step))
        ctx.device.swipe(EVENT_LIST_SCROLL_X, 620, EVENT_LIST_SCROLL_X, target, 500)
        time.sleep(2.5)
        ctx.screenshot()
        return float(cv2.absdiff(before, ctx._last_screen).mean()) > LIST_MOVE_MIN

    def _process_event_cards(self, ctx):
        """遍历「期間限定」上的所有活动卡：一次只进一张，每次都按当前画面重算坐标。

        两个坑都要避开：
        1. **退回一览时游戏会把列表滚回顶部**（实测 2026-09-12）：按“一屏同时点两张卡”
           的老写法，第二张卡的坐标是上一屏算的，一点就落到空处（日志里的
           “这张卡点不开（可能已结束或不可点）”），所以改成每次点击前重新截图找卡。
        2. 列表滚动会让同一张卡的坐标变化：用**卡片指纹**去重，同一张卡这次只进一次。
        3. **滚到底那一屏往往还有一行卡**（6 张卡 = 3 行，一屏看得到 2 行）：所以要把
           本屏所有没进过的卡都处理完再往下滚。旧逻辑只处理“本屏最上面那一行”就去滚屏，
           滚不动（已到底）直接判“到底了”，第 3 行的两张卡永远轮不到（实测漏 2 张）。
        """
        self._scroll_list_top(ctx)
        seen = []
        for _ in range(30):
            ctx.screenshot()
            cells = event_card_cells(ctx._last_screen)
            if not cells:
                # 网络错误弹窗会把整页变成错误页，怎么滚都没用（实测 2026-09-19 13:04
                # 就是这样放弃遍历的：整页「ネットワークエラー…」）→ 先点 OK 重连再看。
                if handle_network_error(ctx):
                    time.sleep(3)
                    ctx.screenshot()
                    cells = event_card_cells(ctx._last_screen)
                # 列表也可能只是滚到了“两张卡的信息行都不在可视区”的位置（信息行要落在
                # y 120~690 才认得出）。实测 2026-09-18 就因为这个只处理了 1 张卡，
                # 而列表里其实有 4 张。所以先滚回顶部重扫一遍，再下“没有更多卡片”的结论。
                if not cells:
                    cells = self._rescan_event_list(ctx)
                if not cells:
                    # 也可能是上一步压根没退回一览（停在活动内的某个页面）：
                    # 再退一次，然后重扫（实测 2026-09-18 处理完活动 1 后就是这样）。
                    ctx.logger.info("活动列表看不到卡片，先退回活动一览再重扫")
                    if self._back_to_event_list(ctx):
                        cells = self._rescan_event_list(ctx)
                if not cells:
                    path = ctx.save_screenshot(ctx._last_screen,
                                               "event_list_empty.png")
                    ctx.logger.warn("活动列表里没有更多卡片了（已滚动重扫），"
                                    f"截图已保存: {path}")
                    return True
            target = None
            target_fp = None
            # 本屏从上到下、从左到右，找第一张没进过的卡
            for cell in sorted(cells, key=lambda c: (c[1], c[0])):
                fp = card_fingerprint(ctx._last_screen, cell)
                if any(same_card(fp, old) for old in seen):
                    continue
                target, target_fp = cell, fp
                break
            if target is not None:
                seen.append(target_fp)
                self._open_and_handle_card(ctx, target)
                continue
            # 本屏的卡都进过了：往下滚一屏找下一批；滚不动就是到底了
            if not self._scroll_list_down(ctx, LIST_SCROLL_STEP):
                ctx.logger.info("已到活动列表底部")
                return True
        return True

    def _rescan_event_list(self, ctx, rounds=4):
        """列表看不到卡片时：滚回顶部再往下扫几屏；找到就返回卡片坐标，找不到返回 []。

        实测 2026-09-18：从活动退回一览时列表位置不巧落在两张卡之间
        （信息行要落在 y 120~690 才认得出），`event_card_cells` 返回空，
        旧逻辑立刻判定「没有更多卡片了」→ 只处理了 1 张卡（实际列表里有 4 张）。
        """
        self._scroll_list_top(ctx)
        for _ in range(rounds):
            ctx.screenshot()
            cells = event_card_cells(ctx._last_screen)
            if cells:
                return cells
            if not self._scroll_list_down(ctx, LIST_SCROLL_STEP):
                break
        return []

    def _open_and_handle_card(self, ctx, cell):
        """点开一张活动卡，按结构处理，再退回活动列表。"""
        self.current_card = cell
        self.events_seen += 1
        ctx.logger.info(f"=== 活动 {self.events_seen}：点卡片 ({cell[0]},{cell[1]}) ===")
        ctx.click(cell[0], cell[1], sleeptime=6)
        for _ in range(6):
            ctx.screenshot()
            if handle_network_error(ctx) or handle_download_popup(ctx) or close_content_popup(ctx):
                continue
            break
        self._handle_event_page(ctx)
        self._back_to_event_list(ctx)

    def _handle_event_page(self, ctx):
        """进入活动后按页面结构分派：带标签的关卡页 / 纯关卡列表 / 纯剧情。"""
        self.stopped_at_mode = None
        kind = self._wait_event_kind(ctx)
        if kind == "list":
            ctx.logger.info("这张卡点不开（可能已结束或不可点），跳过")
            return
        if kind == "story":
            self._play_prestory(ctx)
            return
        if kind != "stage":
            ctx.logger.info("没认出这个活动的页面结构，跳过")
            return
        tabs = self._detect_tabs(ctx)
        ctx.logger.info("活动页面标签：" + ("、".join(tabs) if tabs else "（无，直接是关卡列表）"))
        if self.do_story:
            for tab in tabs:
                if tab == "バトル":
                    continue
                if self._goto_tab(ctx, tab):
                    self._clear_story_cards(ctx, tab)
            if not tabs:
                # 没有标签栏的活动：整页可能只有一行“没有编号的剧情关”
                # （プレストーリー 型），战斗识别认不出它，这里单独处理。
                self._clear_story_stage_row(ctx)
            # 剧情做完后画面可能还停在剧情/结算页，先确认回到关卡选择页再进战斗，
            # 否则下面找「バトル」标签会失败、整个活动的战斗被跳过（2026-09-18 实测）。
            self._recover_to_stage_list(ctx)
        if self.do_battle:
            if not self._run_battle_any(ctx, tabs):
                self.incomplete.append(
                    f"第 {self.events_seen} 个活动（{self.stopped_at_mode or '战斗'}）")
        if self.claim_reward:
            self._claim_event_reward(ctx)

    def _wait_event_kind(self, ctx, rounds=10):
        """等卡片打开后的页面出现，判断类型：stage / story / list / unknown。"""
        for _ in range(rounds):
            ctx.screenshot()
            if self._is_event_list(ctx):
                return "list"
            if self._is_target_stage_list(ctx):
                return "stage"
            joined = "".join(t for t, *_ in read_text(ctx._last_screen))
            if _is_story_screen(ctx) or _is_reward_popup(ctx):
                return "story"
            # プレストーリー 型活动（山内 昇依編 / ふじの食堂）：点开卡片立刻弹
            # 「ストーリー再生確認」（要下载剧情数据再播放）。标题 OCR 会读花成
            # 「卜一一再生確」，下面那几个关键词匹配不上；复用剧情任务的下载弹窗
            # 判定（再生 / 自動削除 / ダウンロード / MB + 底部 OK），命中就按剧情处理。
            if _handle_download(ctx):
                return "story"
            if any(k in joined for k in ("ストーリー再生", "再生確認", "ボイスなし", "再生します")):
                return "story"
            if handle_download_popup(ctx) or handle_network_error(ctx):
                continue
            # 卡片点开先弹的弹窗先关掉再看：詳細 / 特設 / お知らせ 这类带关闭键的弹窗，
            # 以前 `_wait_event_kind` 完全没处理 → 10 轮后判 unknown 跳过整张卡
            # （2026-09-16 那期 3 张卡里有 2 张就是这么被跳过的）。
            if close_content_popup(ctx):
                ctx.logger.info("活动卡点开后先弹了弹窗，关掉再看")
                continue
            # 只有 OK 的确认框（活动已结束 / 需要确认之类）也点掉再看
            if click_ok_by_ocr(ctx, y_min=300, y_max=700):
                ctx.logger.info("活动卡点开后先弹了确认框，点 OK 再看")
                continue
            time.sleep(1.5)
        # 真认不出来时留现场：截图 + OCR 摘要。2026-09-16 就是因为日志里只有一句
        # “没认出这个活动的页面结构，跳过”，查不出到底是哪一屏，只能靠复现。
        path = ctx.save_screenshot(ctx._last_screen,
                                   f"event_unknown_{self.events_seen}.png")
        joined = "".join(t for t, *_ in read_text(ctx._last_screen))[:120]
        ctx.logger.warn(f"认不出这个活动的页面结构，截图已保存: {path}｜OCR: {joined}")
        return "unknown"

    def _play_prestory(self, ctx):
        """纯剧情活动（プレストーリー）：点开直接进剧情，全跳完回列表。"""
        ctx.logger.info("识别为剧情型活动，全跳过")
        for _ in range(80):
            time.sleep(1)
            ctx.screenshot()
            if self._is_event_list(ctx):
                return True
            if handle_network_error(ctx):
                continue
            if _handle_download(ctx):       # 「ストーリー再生確認」→ 点 OK 下载并播放
                continue
            if _is_story_screen(ctx):
                _skip_story(ctx)
                self.processed_count += 1
                continue
            if _is_reward_popup(ctx):
                self._tap_ok(ctx, 500, 800, 590, 690)
                continue
            if close_content_popup(ctx):
                continue
        return False

    def _back_to_event_list(self, ctx):
        """退出当前活动，回到活动一览页。"""
        for _ in range(8):
            ctx.screenshot()
            if self._dismiss_title_return(ctx):
                continue
            if self._is_event_list(ctx):
                return True
            if _is_story_screen(ctx):
                _skip_story(ctx)
                continue
            if handle_network_error(ctx) or handle_download_popup(ctx) or close_content_popup(ctx):
                continue
            if is_page(ctx, "home"):
                return self._enter_event_list(ctx)
            if self._on_battle_menu(ctx) and is_page(ctx, "battle"):
                ctx.click(BATTLE_EVENT[0], BATTLE_EVENT[1], sleeptime=5)
                time.sleep(2)
                continue
            ctx.click(BACK_ARROW[0], BACK_ARROW[1], sleeptime=4)
            time.sleep(1)
        return self._is_event_list(ctx)

    def _claim_event_reward(self, ctx):
        """点左栏「報酬」/「回収」把活动任务奖励领掉（尽力而为，不阻塞清关）。"""
        ctx.screenshot()
        # 「回収」会被 OCR 读成中文的「回收」（实测 2026-10-07 踏破イベント
        # サバイバルアタック：挂着 100 个未领奖励，被这个拼写差异一直跳过）。
        hit = self._find_text(ctx, ("報酬", "回収", "回收"), (0, 520, 540, 700))
        if hit is None:
            # 「報酬」两个字经常被 OCR 读花（实测 2026-09-20 读成「赣州」），只认文字会
            # 直接放弃 —— 那次「報酬」上挂着 42 个未领奖励，脚本连着几天都没领。用模板兜底。
            result = ctx.find(self.REWARD_BUTTON_TEMPLATE, threshold=0.9)
            if result is not None:
                (rx, ry), _score = result
                if rx <= 540 and ry >= 520:
                    hit = (rx, ry)
        if hit is None:
            return False
        ctx.logger.info(f"点活动奖励按钮 ({hit[0]},{hit[1]})")
        ctx.click(hit[0], hit[1], sleeptime=4)
        for _ in range(8):
            time.sleep(2)
            ctx.screenshot()
            if handle_network_error(ctx) or _handle_download(ctx):
                continue
            # 奖励弹窗里要先领：通常是「一括受取」，领完会有第二个弹窗（OK 关掉）
            # 实测 2026-09-20：「一括受取」在右下角 (1180,655)，搜索范围必须覆盖到右边。
            # 注意顺序：奖励页上有一堆数字，`_is_target_stage_list` 会**误判成关卡列表**，
            # 所以必须先找「一括受取」，最后才判“是不是已经回到关卡页”。
            claim = self._find_text(ctx, ("一括受取", "受け取る", "受取る"),
                                    (300, 1280, 480, 715))
            if claim is None:
                # 这个按钮的 OCR 也不稳（实测读不出来），用模板兜底
                result = ctx.find(self.CLAIM_ALL_TEMPLATE, threshold=0.9)
                if result is not None:
                    (cx2, cy2), _score = result
                    if cx2 >= 900 and cy2 >= 560:
                        claim = (cx2, cy2)
            if claim is not None:
                ctx.logger.info(f"点奖励弹窗「一括受取」({claim[0]},{claim[1]})")
                ctx.click(claim[0], claim[1], sleeptime=4)
                continue
            close = self._find_text(ctx, ("閉じる", "とじる", "OK"), (350, 1100, 500, 720))
            if close is not None:
                ctx.click(close[0], close[1], sleeptime=3)
                continue
            if self._is_target_stage_list(ctx) or self._is_event_list(ctx):
                return True
        ctx.device.key("BACK")
        time.sleep(2)
        return True

    @staticmethod
    def _find_text(ctx, keywords, region):
        """在指定区域里按 OCR 找关键词，返回它的坐标。"""
        x0, x1, y0, y1 = region
        for t, cx, cy, s in read_text(ctx._last_screen):
            if not (x0 <= cx <= x1 and y0 <= cy <= y1):
                continue
            if any(k in t for k in keywords):
                return (cx, cy)
        return None

    # ---------- 剧情卡片（全跳） ----------
    def _clear_story_cards(self, ctx, tab):
        if not self._goto_tab(ctx, tab):
            ctx.logger.warn(f"无法进入「{tab}」标签，跳过")
            return
        # 标签没有红点 = 该标签已无未看内容，直接跳过（别再傻点）
        if not self._tab_has_red_dot(ctx, tab):
            # 兜底：标签红点可能因颜色差异/未刷新没被识别，先看当前视野有没有未看完的剧情卡；
            # 若有就照常处理，避免“明明有剧情却当成全部看完”。
            if not self._available_story_cards(ctx):
                ctx.logger.info(f"「{tab}」标签无红点且当前视野无未看剧情卡，跳过")
                return
            ctx.logger.info(f"「{tab}」标签无红点，但当前视野仍有未看剧情卡，继续处理")
        processed = []
        no_progress = 0
        for _ in range(30):
            ctx.screenshot()
            # 剧情与战斗一致：只点“当前可读”（右上角带粉红点）的那一张，别乱点已读完/未解锁卡
            cards = self._available_story_cards(ctx)
            # 已处理过的卡按位置聚簇去重（OCR 坐标抖动不再重复打）
            todo = [c for c in cards
                    if not any(abs(c[0] - px) < 60 and abs(c[1] - py) < 60
                               for px, py in processed)]
            if not todo:
                # 读完一话后游戏要一小会儿才把下一话的粉点刷出来；先原地等待+重扫几次，
                # 再小步向下滚动，避免“一没看到点就急着滚”把刚要刷出来的点滚过去。
                no_progress += 1
                if no_progress < 3:
                    time.sleep(4)
                    continue
                if not self._scroll_event_down(ctx):
                    break
                no_progress = 0
                continue
            no_progress = 0
            cx, cy = todo[0]
            processed.append((cx, cy))
            ctx.logger.info(f"处理剧情卡片 ({cx},{cy})")
            # 卡片的「N話:」文字在卡底部，可点区在卡面中部，往上偏一点
            ctx.click(cx, max(cy - 60, 200), sleeptime=5)
            if self._play_story_node(ctx):
                self.processed_count += 1
            else:
                # 没回到列表（异常）：切回标签兜底
                if not self._goto_tab(ctx, tab) and not self._is_target_stage_list(ctx):
                    break
            # 读完一话，游戏一般就在当前视野里把下一话的粉点刷出来，直接重截图找下一张即可；
            # 不要滚回顶部（会把刚可见的下一话滚出去，再滚回来纯属浪费时间）。
            continue

    @staticmethod
    def _visible_story_cards(ctx):
        """识别"还没看完"的剧情卡片：卡片区以数字开头的文字，并跳过右上角带 COMPLETE 的。"""
        markers = ClearEventStages._complete_markers(ctx)
        pts = []
        for t, cx, cy, s in read_text(ctx._last_screen):
            if not re.match(r"^\d", t.strip()):
                continue
            if 560 <= cx <= 1280 and STORY_CARD_TOP <= cy <= STORY_CARD_BOTTOM:
                if ClearEventStages._is_card_complete(markers, cx, cy):
                    continue  # 已看完，跳过
                pts.append((cx, cy))
        pts.sort(key=lambda p: (p[1], p[0]))
        out = []
        for cx, cy in pts:
            if any(abs(cx - ox) < 150 and abs(cy - oy) < 90 for ox, oy in out):
                continue
            out.append((cx, cy))
        return out

    @staticmethod
    def _available_story_cards(ctx):
        """只返回“当前可读”的剧情卡：卡片右上角带粉红点的那一话（与战斗按红点选关一致）。

        剧情卡按顺序解锁：只有当前可读那一话在卡右上角有亮粉红点；已读完的卡带 COMPLETE，
        未解锁的卡无点。脚本应只点带粉红点的卡，否则会“乱点”已读完/未解锁的卡。

        注意：双列卡上 COMPLETE 标记会对相邻卡互相误判，故此处不依赖 COMPLETE。
        粉红点位于卡片“右上角”，约在标签右上方 (lx+160, ly-190)；据此把点归到所在卡，
        并过滤掉卡片立绘里的粉红元素（面积阈 + 位置均不符合）。
        """
        dots = [(cx, cy) for cx, cy, a in _pink_dots(ctx._last_screen)
                if cx > 850 and 150 <= cy <= 700 and a >= 40]
        if not dots:
            return []
        labels = [(cx, cy) for t, cx, cy, s in read_text(ctx._last_screen)
                  if t.strip()[:1].isdigit()
                  and 560 <= cx <= 1280 and STORY_CARD_TOP <= cy <= STORY_CARD_BOTTOM]
        avail = []
        for lx, ly in labels:
            for dx, dy in dots:
                if abs(dx - (lx + 160)) < 110 and abs(dy - (ly - 190)) < 110:
                    avail.append((lx, ly))
                    break
        avail.sort(key=lambda p: (p[1], p[0]))
        out = []
        for cx, cy in avail:
            if any(abs(cx - ox) < 150 and abs(cy - oy) < 90 for ox, oy in out):
                continue
            out.append((cx, cy))
        return out

    @staticmethod
    def _complete_markers(ctx):
        """返回画面中所有 COMPLETE 标记的中心点（已完成卡片的标识）。"""
        return [(cx, cy) for t, cx, cy, s in read_text(ctx._last_screen)
                if t.strip().upper() == "COMPLETE"]

    @staticmethod
    def _is_card_complete(markers, cx, cy):
        """卡片 (cx,cy) 是否已完成：右上角 COMPLETE 与该卡在同行（y 差 < 卡高一半）。"""
        for mx, my in markers:
            if abs(mx - cx) < 300 and abs(my - cy) < 120:
                return True
        return False

    @staticmethod
    def _tab_has_red_dot(ctx, tab):
        """顶部标签是否带红点。红点 = 该标签还有没看/没打的内容；无红点 = 全清完。"""
        ctx.screenshot()
        # 标签位置按 OCR 找（各活动标签数量/位置不同），读不到才退回旧默认值
        positions = ClearEventStages._tab_positions(ctx) or dict(TABS)
        if tab not in positions:
            return False
        dots = []
        # 事件界面的“当前可打/未看”提示常是亮粉红点（HSV≈150-180）；`find_red_dots`
        # 只认正红(H 0-10/170-180)会漏掉粉红点，导致误判“无红点→全清完”而跳过本标签。
        # 故同时用 _pink_dots 兜底识别粉红点，只在标签行(y 85~135)内匹配。
        for dx, dy, area in find_red_dots(ctx._last_screen, min_area=6):
            if 85 <= dy <= 135:
                dots.append((dx, dy, area))
        for dx, dy, area in _pink_dots(ctx._last_screen):
            if 85 <= dy <= 135:
                dots.append((dx, dy, area))
        for dx, dy, area in dots:
            if area < 8:
                continue
            # 该红点归属水平方向上最近的标签，避免相邻标签区域重叠串味
            nearest = min(positions, key=lambda t: abs(positions[t][0] - dx))
            if nearest == tab:
                return True
        return False

    def _play_story_node(self, ctx):
        """进入剧情节点后：全跳 + 领首通奖励，直到回到列表。返回是否完成过。"""
        idle = 0
        reveal_tries = 0
        for _ in range(40):
            time.sleep(1)
            ctx.screenshot()
            if handle_network_error(ctx):
                idle = 0
                reveal_tries = 0
                continue
            if _handle_download(ctx):
                idle = 0
                reveal_tries = 0
                continue
            if _is_story_screen(ctx):
                _skip_story(ctx)
                idle = 0
                reveal_tries = 0
                continue
            if _is_blank_screen(ctx._last_screen):
                # 动画播放页的控件会自动隐藏（整屏全黑、OCR 读不到 SKIP）：
                # 点一下画面把控件叫出来，下一轮就能认出 SKIP 并跳过。
                ctx.logger.info("画面全黑（动画控件已隐藏），点一下叫出控件")
                ctx.click(STORY_REVEAL_BTN[0], STORY_REVEAL_BTN[1], sleeptime=1)
                idle = 0
                reveal_tries = 0
                continue
            if _is_reward_popup(ctx):
                self._tap_ok(ctx, 500, 800, 590, 690)
                idle = 0
                reveal_tries = 0
                continue
            if self._is_target_stage_list(ctx):
                return True
            # 剧情/动画页的按钮几秒后会自己隐藏（画面上只剩 MENU 之类），
            # 这时哪个判据都认不出来 → 只能干等到超时。连着两轮没进展就点一下画面，
            # 把按钮叫出来再判（实测 2026-09-12 ふじ 食堂 的プレストーリー）。
            idle += 1
            if idle >= 2:
                # 但「点一下叫出按钮」不是万能的：实测 2026-10-02 剧情跳过后遇通信失败，
                # 画面卡在一个谁也认不出的页面上，这条兜底转了 18 轮 / 10 分 20 秒
                # （另一处 12:51 又转了 8 分钟），而且什么都没留下。
                # 所以点几次还是认不出就放弃，存一张现场图 + OCR 交给上层退回关卡列表。
                if reveal_tries >= STORY_REVEAL_TRIES:
                    path = ctx.save_screenshot(ctx._last_screen, "story_stuck.png")
                    joined = "".join(t for t, *_ in read_text(ctx._last_screen))[:120]
                    ctx.logger.warn(
                        f"点了 {reveal_tries} 次「叫出按钮」仍然认不出画面，放弃这个剧情节点；"
                        f"截图已保存: {path}｜OCR: {joined}")
                    return False
                reveal_tries += 1
                ctx.logger.info("没认出当前画面（剧情/动画按钮可能已隐藏），点一下叫出按钮")
                ctx.click(STORY_REVEAL_BTN[0], STORY_REVEAL_BTN[1], sleeptime=1)
                idle = 0
        return False

    @staticmethod
    def _tap_ok(ctx, x0, x1, y0, y1):
        for t, cx, cy, s in read_text(ctx._last_screen):
            if t.strip().upper() == "OK" and x0 <= cx <= x1 and y0 <= cy <= y1:
                ctx.click(cx, cy, sleeptime=4)
                return True
        ctx.click((x0 + x1) // 2, (y0 + y1) // 2, sleeptime=4)
        return False

    # ---------- 战斗（复用外征单关流程） ----------
    def _clear_battle(self, ctx, has_battle_tab=True):
        # バトル标签没有红点 = 没有未打关卡，直接跳过（别瞎滚/瞎点）
        if has_battle_tab and not self._tab_has_red_dot(ctx, "バトル"):
            # 兜底：标签红点可能因颜色差异/未刷新没被识别，也可能活动本身就不给标签打红点，
            # 先看当前视野有没有“当前可打”的关卡；有就照常清，避免“明明没打却当成打完”。
            if not self._available_stage_rows(ctx):
                ctx.logger.info("バトル标签无红点且当前视野无粉点关，判断已清完")
                return True
            ctx.logger.info("バトル标签无红点，但当前视野仍有可打关卡，继续清关")
        ctx.logger.info("开始清空活动战斗关卡")
        # 进入バトル页后默认视野一般就能看到“当前可打”关（带红点），
        # 先看当前视野，不立刻滚到顶，避免把带红点的关滚出视野。
        processed = set()
        down_sweeps = 0
        for i in range(45):
            ctx.screenshot()
            if i and i % 10 == 0:
                ctx.logger.info(f"继续扫描活动关卡列表（{i}/45）")
            # 活动关卡必须一关一关按顺序打；只有“当前可打”那一关右上角带红点、
            # 且真的能点开，后面的是锁定灰卡。所以只认带红点的关卡行。
            rows = self._available_stage_rows(ctx)
            todo = [y for y in rows if y not in processed]
            if todo:
                y = todo[0]
                processed.add(y)
                self.processed_count += 1
                ctx.logger.info(f"=== 活动战斗关卡 (y={y}) ===")
                if not self._clear_one_stage(ctx, y):
                    if self.failed:
                        return False
                    if self.enter_failed or self.ap_blocked:
                        # 关卡点了进不去（未解锁/未加载）：停止该难度清关，
                        # 不再“跳过继续”后假装已完成。
                        self.battle_blocked = True
                        return False
                    ctx.logger.warn(f"战斗关卡 y={y} 未能完成，跳过继续")
                    if not self._back_to_stage_list(ctx) and \
                            not self._enter_event_stage_select(ctx):
                        return False
                    if has_battle_tab:
                        self._goto_tab(ctx, "バトル")
                    continue
                if not self._back_to_stage_list(ctx) and \
                        not self._enter_event_stage_select(ctx):
                    return False
                # 打完一关后重进「バトル」标签，让“当前可打”红点刷新到下一关。
                # （纯关卡列表的活动没有标签栏，别去点。）
                if has_battle_tab:
                    self._goto_tab(ctx, "バトル")
                # 下一关的粉点要等游戏向服务器确认进度后才刷出来（实测几秒）。
                # 2026-09-20「乙女のピンチ」就因为没等它，打完 05 后没看到 06 的粉点，
                # 扫两遍就判「已到活动战斗列表底部」→ 一轮只推进 1~2 关。
                if self._wait_next_available_stage(ctx):
                    ctx.logger.info("下一关已解锁，继续清关")
                continue
            # 没有红点关：用小步长向下滚动，避免大滚跳过“当前可打”那一关。
            if not self._scroll_event_down_small(ctx):
                # 滚到底且向下过程没找到红点：可能起点在可打关下方，向上滚会错过。
                # 回顶部再向下扫一遍兜底；第二遍仍没有才算“没有更多”。
                down_sweeps += 1
                if down_sweeps >= 2:
                    ctx.logger.info("已到活动战斗列表底部，没有更多带红点的关卡")
                    return True
                self._scroll_event_top(ctx)
                continue
        return True

    def _wait_next_available_stage(self, ctx, rounds=5, interval=3):
        """打完一关后等下一关的粉点刷出来；返回是否等到了新的可打关。

        实测 2026-09-20「乙女のピンチ」：打完 05 之后 06 其实已经解锁，
        但粉点还没刷新，脚本扫两遍就判「已到活动战斗列表底部」→ 一轮只推进 1~2 关。
        这里原地等几轮，中途重进一次「バトル」标签（刷新页面有时才出粉点）。
        """
        for i in range(rounds):
            time.sleep(interval)
            ctx.screenshot()
            if self._available_stage_rows(ctx):
                return True
            if i == 1:
                self._goto_tab(ctx, "バトル")
        return False

    @staticmethod
    def _is_stage_label(text):
        """判断 OCR token 是否像“关卡号”：普通关为纯数字，EX 关带 EX 前缀。

        - `06` / `0`：普通 NORMAL/HARD 关卡号（OCR 常把 06 读成单个 0）
        - `EX1` / `ex1` / `EX 1`：HARD 的 EX 关卡（EX1~EX5）
        - `ステージEX1` / `ステージ1`：战斗卡标题连「ステージ」前缀一起被 OCR 读出时
        - `ジ01`：OCR 把「ステージ01」读成只剩尾巴「ジ01」（实测 はんこ 導入記念）
        - `EX`：OCR 有时把 EX 与数字拆成两个 token，单独一个 EX 也视为关卡行
        """
        t = text.strip()
        if not t:
            return False
        if re.fullmatch(_STAGE_NUM_RE, t):
            return True
        if re.fullmatch(_STAGE_EX_RE, t):
            return True
        return False

    @staticmethod
    def _event_stage_rows(ctx):
        """活动战斗关卡行：识别关卡号标签的中心 y，聚簇去重。

        OCR 常把「06」读成单个「0」，放宽到 1~2 位数字；该识别区域只有关卡号，
        不会误匹配 AP/10、12/30 等带文字的数字。关卡号恒定在 x≈653，
        而信息栏「AP|10」的 10 在 x≈638 → 收紧到 x≥645 可排除信息栏数字，
        避免顶部被裁切的卡片（只露信息栏）被当成可打关卡。
        EX 关卡用「EX1~EX5」命名，不带纯数字前缀，需额外支持 EX 前缀识别。
        已通关的关卡会打上 COMPLETE（卡片右上角），同一行 y 带宽(±120px)内有
        COMPLETE 即视为已通关，跳过——避免重复点已清的关，让 NORMAL 能真正清完解锁 HARD。
        """
        markers = ClearEventStages._complete_markers(ctx)
        ys = []
        for t, cx, cy, s in read_text(ctx._last_screen):
            if ClearEventStages._is_stage_label(t) and 645 <= cx <= 800 and 90 <= cy <= 715:
                if any(abs(my - cy) < 120 for mx, my in markers):
                    continue  # 已通关（COMPLETE），跳过
                ys.append(cy)
        ys.sort()
        out = []
        for cy in ys:
            # 同一张卡"关卡号→信息栏"约 66px，相邻卡片约 157px；
            # 用 80px 聚簇可把信息栏的 AP|10 等数字合并进本卡，避免多出一行。
            if out and cy - out[-1] < 80:
                continue
            out.append(cy)
        return out

    @staticmethod
    def _available_stage_rows(ctx):
        """只返回“当前可打”的关卡行。

        首选信号是右上角的粉红红点：活动关卡按顺序一关一关解锁，只有带红点的
        那一关能点开，其余是锁定的灰卡；红点位于卡片右上角（x>1150），略高于
        关卡号文字（y 差约 20~50px），用关卡行与红点做 y 聚类匹配。

        但**不是所有活动都给当前关卡打红点**：实测 2026-09-12 的「はんこ 導入記念」
        和「サバイバルアタック」，当前可打的关卡右上角没有粉红点（进入过一次后
        红点会消失），此时若只认红点，一关都找不到 → 整段战斗被当成“已清完”跳过。
        所以画面里**一个红点都没有**时，退回按“卡片有没有被灰锁”判断（`_stage_card_unlocked`）。
        画面里有红点时仍按红点走，保持原有“只打当前那一关”的保守行为。
        """
        rows = ClearEventStages._event_stage_rows(ctx)
        if not rows:
            return []
        dots = ClearEventStages._card_red_dots(ctx)
        if dots:
            avail = [row for row in rows
                     if any(abs(dy - row) < 90 for dx, dy in dots)]
            avail.sort()
            return avail
        img = ctx._last_screen
        avail = []
        for row in rows:
            # 只对“整张卡都在画面里”的关卡用亮度兜底：贴着屏幕下沿的卡看不到
            # 右上角的 COMPLETE 章，会被误判成可打。实测（2026-09-12）残光 的
            # ステージ05 正好卡在下沿，脚本把已通关的关又打了一遍、白花 AP。
            # 关卡列表是逐格滚动的，真·可打的关滚上来后会被下一次扫描看到。
            if img is not None and row + STAGE_CARD_BADGE_MARGIN > img.shape[0]:
                continue
            if ClearEventStages._stage_card_unlocked(img, row):
                avail.append(row)
        return sorted(avail)

    @staticmethod
    def _stage_card_unlocked(img, row_y, min_value=STAGE_CARD_MIN_VALUE):
        """关卡卡是不是“没被锁”：锁定灰卡整体偏暗，可打/已完成的卡很亮。

        取关卡行上下各 70px 的卡面主体（x 600~1140，避开右侧掉落物图标与
        左侧信息栏）算 HSV 的 V 均值。实测可打卡 V≈210~235、锁定灰卡 V≈120~140。
        已通关(COMPLETE)的卡也亮，但 `_event_stage_rows` 已按 COMPLETE 标记排除。
        """
        import cv2
        if img is None:
            return False
        y0 = max(int(row_y) - 70, 0)
        y1 = min(int(row_y) + 70, img.shape[0])
        band = img[y0:y1, 600:1140]
        if band.size == 0:
            return False
        hsv = cv2.cvtColor(band, cv2.COLOR_BGR2HSV)
        return float(hsv[:, :, 2].mean()) >= min_value

    @staticmethod
    def _card_red_dots(ctx):
        """在关卡列表的右侧卡片区（x>1150、y 150~715）找粉红红点中心。"""
        dots = _pink_dots(ctx._last_screen)
        return [(cx, cy) for cx, cy, _a in dots
                if cx > 1150 and 150 <= cy <= 715]

    # ---------- 没有编号的「剧情关卡」（プレストーリー 型活动） ----------
    def _story_stage_row(self, ctx):
        """找“标题没有关卡编号”的关卡行；没有就返回 None。

        实测 2026-09-12 山内 昇依編 / ふじ 食堂（プレストーリー ミニイベント）：
        卡片点开先弹「ストーリー再生確認」，进关卡页后**整页只有一行**
        「プレストーリー山内昇依編」——没有ステージ编号、也没有红点，
        `_event_stage_rows` 认不出来，于是这一行被当成“没有关卡”直接跳过，
        剧情和首通奖励都拿不到。这类活动的行标题是文字标题（不是编号），
        所以按“标题行里出现了非编号的文字”把它找出来。

        有编号关卡的活动不会命中：先要求 `_event_stage_rows` 为空，
        标题又必须不是 `_is_stage_label`（全 COMPLETE 的编号关靠这条排除）。
        """
        ctx.screenshot()
        if ClearEventStages._event_stage_rows(ctx):
            return None
        # 读完的剧情行右上角会盖上 COMPLETE 章，别再点开重播一遍
        done_ys = [cy for t, cx, cy, s in read_text(ctx._last_screen)
                   if "COMPLETE" in t.upper()]
        for t, cx, cy, s in read_text(ctx._last_screen):
            if not (STORY_ROW_TITLE_X[0] <= cx <= STORY_ROW_TITLE_X[1]
                    and STORY_ROW_TITLE_Y[0] <= cy <= STORY_ROW_TITLE_Y[1]):
                continue
            if ClearEventStages._is_stage_label(t):
                continue
            title = t.strip().upper()
            if not title or any(k in title for k in _ROW_TITLE_SKIP):
                continue
            if any(abs(done_y - cy) <= 60 for done_y in done_ys):
                continue
            return (STAGE_ROW_X, min(cy + STORY_ROW_CLICK_DY, 240))
        return None

    def _clear_story_stage_row(self, ctx):
        """点开这一行剧情关 → 全跳 → 领首通奖励 → 回关卡列表。"""
        row = self._story_stage_row(ctx)
        if row is None:
            return False
        ctx.logger.info(f"发现没有编号的剧情关卡行，点开跳过剧情 ({row[0]},{row[1]})")
        ctx.click(row[0], row[1], sleeptime=6)
        if not self._play_story_node(ctx):
            ctx.logger.warn("剧情关卡行点开后没等到剧情/结算，返回关卡列表")
            if not self._back_to_stage_list(ctx) and not self._back_home(ctx):
                return False
            return False
        self.processed_count += 1
        return True

    # ---------- 单关战斗 ----------
    def _clear_one_stage(self, ctx, row_y):
        """事件单关：关卡行 → ステージ情報 → 打完一关 → 结算。

        结算页（STAGE CLEAR）右下角有「▶次へ / 再戦 / OK」三个按钮，**点「次へ」会直接
        进下一关**（只有最后一关没有它）。所以这里打完一关就顺着「次へ」接着打，
        不用退回关卡列表重新扫、也不用等粉点刷新（2026-09-20 用户指出，实机确认）。
        """
        # 关卡卡的可点击区域在左右两侧略有差异：固定 x=890 有时点不开。
        # 先按主坐标点一次，等不到ステージ情報就换 x=800 再点一次，
        # 仍失败才判定为“未解锁/无法进入”，避免坐标差异导致关卡被误跳过。
        ctx.logger.info(f"点关卡行 (y={row_y})")
        ctx.click(STAGE_ROW_X, row_y, sleeptime=6)
        if not self._wait_stage_info(ctx):
            ctx.logger.info(f"主坐标未进入ステージ情報，改用备用坐标重试 (y={row_y})")
            # 先尝试返回（可能误点到空白），再换 x=800 点卡。
            if self._is_target_stage_list(ctx):
                ctx.click(800, row_y, sleeptime=6)
            if not self._wait_stage_info(ctx):
                ctx.logger.warn("未能进入ステージ情報，该关卡可能未解锁或加载异常")
                self.enter_failed = True
                return False
        settled = False   # True=这一关已经打完、直接进结算处理（「次へ」直接开打时用）
        while True:
            if settled:
                settled = False
            else:
                if not self._ap_ok(ctx):
                    # AP 不够：退出ステージ情報，停止本活动的清关
                    self._back_to_stage_list(ctx)
                    return False
                if not self._fight_current_stage(ctx):
                    return False
            next_stage = self._settle_current_stage(ctx)
            if next_stage is None:
                return self._is_target_stage_list(ctx)
            if not next_stage:
                return True                 # 点了 OK，已经回到关卡列表
            # 点了「次へ」：一般是到下一关的ステージ情報；踏破活动会直接开打。
            self.processed_count += 1
            state = self._wait_next_stage_info(ctx)
            if state == "battle":
                # 实测 2026-10-07：踏破イベント保留上次队伍，点「次へ」直接进战斗；
                # 别再点ユニット選択，等这场结算，循环里接着走结算处理。
                ctx.logger.info("下一关直接开打了，等它结算")
                if not self._battle_and_clear(ctx):
                    return False
                settled = True
                continue
            if not state:
                ctx.logger.warn("点了「次へ」之后没等到下一关（ステージ情報和战斗画面都没出现）")
                path = ctx.save_screenshot(ctx._last_screen, "event_next_stage_stuck.png")
                ctx.logger.info(f"已存截图：{path}")
                return self._back_to_stage_list(ctx)

    def _fight_current_stage(self, ctx):
        """当前ステージ情報页 → ユニット選択 → 编队 → 出撃 → 打完。"""
        ctx.logger.info("点ユニット選択")
        ctx.click(UNIT_SELECT_BTN[0], UNIT_SELECT_BTN[1], sleeptime=6)
        # ユニット選択页判据：AUTO周回（普通活动）、選択可能数、UNIT CHOICE（页脚英文）。
        # 实测 2026-09-12 サバイバルアタック（踏破活动）这页**没有 AUTO周回**，
        # 「選択可能数」又被 OCR 读成「遥职可能数」，只认前两个关键词会误判“没进来”
        # 而整关放弃；页脚的「UNIT CHOICE / UNIT」读得很稳（0.93~0.99），拿它兜底。
        mode = self._wait_unit_select_or_battle(ctx)
        if mode == "battle":
            # 从「次へ」进来的下一关保留上次队伍时，这一点就直接开打了（实测 2026-09-20）
            ctx.logger.info("这一关保留了上次队伍，直接开打了；等结算")
            return self._battle_and_clear(ctx)
        if mode != "unit":
            ctx.logger.warn("未能进入ユニット選択，回到关卡列表继续")
            if not self._back_to_stage_list(ctx):
                self._back_home(ctx)
            return False
        if not self._fill_main_unit(ctx):
            if not self._back_to_stage_list(ctx):
                self._back_home(ctx)
            return False
        ctx.logger.info("点出撃")
        ctx.click(SORTIE_BTN[0], SORTIE_BTN[1], sleeptime=8)
        return self._battle_and_clear(ctx)

    NEXT_STAGE_TEMPLATE = "event/btn_next_stage.png"
    REWARD_BUTTON_TEMPLATE = "event/btn_event_reward.png"
    CLAIM_ALL_TEMPLATE = "event/btn_claim_all.png"

    def _wait_unit_select_or_battle(self, ctx, rounds=12):
        """点「ユニット選択」之后等页面。

        返回 "unit"=进了编队页；"battle"=这一点直接把这一关开打了（从「次へ」进来的
        下一关会保留上次队伍，按钮其实可能是「出撃」）；None=都没出现。
        """
        for _ in range(rounds):
            time.sleep(1)
            ctx.screenshot()
            if _handle_download(ctx) or handle_network_error(ctx):
                continue
            joined = "".join(t for t, *_ in read_text(ctx._last_screen)).upper()
            if "WAVE" in joined or ("HOME" in joined and "AUTO" in joined):
                return "battle"          # 已经在打了
            if any(k in joined for k in ("AUTO", "選択可能数", "UNIT")):
                return "unit"
        return None

    def _wait_next_stage_info(self, ctx, rounds=10):
        """点「次へ」之后等下一关；返回 "info" / "battle" / False。

        - "info"：到了下一关的ステージ情報页（普通活动都是这样）；
        - "battle"：下一关已经**直接开打**（见下），交给上层等结算；
        - False：都没等到。

        实测 2026-09-20：STAGE CLEAR 页本来就有 DROP / 初回報酬 字样，
        用 `_wait_stage_info` 会立刻在结算页上返回 True → 接着点 (1155,655)
        其实点到结算页的按钮上，把流程带偏。所以这里必须排除结算页。

        实测 2026-10-07（踏破イベント「サバイバルアタック」05→06）：点「次へ」
        后**根本不经过ステージ情報**——保留上次队伍时游戏直接开打下一关
        （+3s 加载 → +10s 战斗开场动画 → +35s 已经在 WAVE 1/1 里）。旧代码只认
        「消費AP」，等了 96 秒判失败 → 按 BACK 乱走 12 分钟 → 活动清关失败。
        所以这里把“战斗已经开打”也当作成功的一种。
        """
        for _ in range(rounds):
            time.sleep(1.5)
            ctx.screenshot()
            if handle_network_error(ctx) or _handle_download(ctx):
                continue
            joined = "".join(t for t, *_ in read_text(ctx._last_screen)).replace(" ", "")
            up = joined.upper()
            if "STAGECLEAR" in up or "CLEARBONUS" in up:
                continue                 # 还停在结算页
            if "WAVE" in up or ("HOME" in up and "AUTO" in up):
                return "battle"          # 下一关直接开打了（战斗 UI）
            if "消費AP" in joined or "消费AP" in joined or "ステージ情報" in joined:
                return "info"
        return False

    def _find_next_stage_button(self, ctx):
        """结算页的「▶次へ」按钮；找不到返回 None。

        实测 2026-09-20：STAGE CLEAR 页右下是「▶次へ / 再戦 / OK」，次へ 在 (890,658)。
        按钮字太小 OCR 读不出来（所以旧代码的 OCR 分支永远落空、一路 fallback 到 OK，
        回列表再扫）。模板：真按钮 1.000，其它画面最高 0.856 → 阈值 0.92 + 位置限定。
        """
        result, _scale = ctx.find_scale(self.NEXT_STAGE_TEMPLATE, threshold=0.92)
        if result is None:
            return None
        (bx, by), _score = result
        if not (600 <= bx <= 1100 and by >= 560):
            return None
        return (bx, by)

    def _settle_current_stage(self, ctx):
        """处理战斗结算页。

        返回 True=点了「次へ」（进下一关）；False=点了 OK（回到关卡列表）；
        None=异常/处理不了。
        """
        for _ in range(8):
            ctx.screenshot()
            if self._is_target_stage_list(ctx):
                return False
            hit = self._find_next_stage_button(ctx)
            if hit is not None:
                ctx.logger.info(f"结算页出现「次へ」({hit[0]},{hit[1]})，直接进下一关")
                ctx.click(hit[0], hit[1], sleeptime=6)
                return True
            hit = self._find_result_button(ctx)
            if hit is not None:
                ctx.logger.info(f"点结算按钮 ({hit[0]},{hit[1]})")
                ctx.click(hit[0], hit[1], sleeptime=5)
                continue
            if not self._back_to_stage_list(ctx):
                return None
        return None

    def _wait_stage_info(self, ctx, rounds=15):
        """点关卡后，处理「再生/リソースダウンロード」确认弹窗，再等ステージ情報标记。

        点击关卡行时游戏常先弹「再生データをダウンロード」确认框（带 MB + OK），
        必须先点 OK 关掉，否则永远等不到 BOSS/DROP/初回報酬/消費AP 标记。
        """
        for _ in range(rounds):
            time.sleep(1)
            ctx.screenshot()
            if handle_network_error(ctx):
                continue
            if _handle_download(ctx):
                continue
            joined = "".join(t for t, *_ in read_text(ctx._last_screen))
            if any(k in joined for k in ("BOSS", "DROP", "初回報酬", "消費AP")):
                return True
        return False

    @staticmethod
    def _wait_text(ctx, keyword, rounds=10):
        for _ in range(rounds):
            time.sleep(1)
            ctx.screenshot()
            joined = "".join(t for t, *_ in read_text(ctx._last_screen))
            if keyword in joined:
                return True
        return False

    @staticmethod
    def _wait_marker(ctx, keywords, rounds=12):
        """等 OCR 出现任一关键词（英文/常见词，避开读不准的日文标题）。"""
        for _ in range(rounds):
            time.sleep(1)
            ctx.screenshot()
            joined = "".join(t for t, *_ in read_text(ctx._last_screen))
            if any(k in joined for k in keywords):
                return True
        return False

    @staticmethod
    def _unit_slot_empty(ctx):
        ctx.screenshot()
        joined = "".join(t for t, *_ in read_text(ctx._last_screen))
        return "0/2" in joined

    def _fill_main_unit(self, ctx):
        """主单位空槽：点槽 -> 点第一张单位卡(约730,250) -> 回到编队页。"""
        ctx.logger.info("选择主单位")
        ctx.click(MAIN_UNIT_SLOT[0], MAIN_UNIT_SLOT[1], sleeptime=5)
        time.sleep(2)
        ctx.click(730, 250, sleeptime=4)
        time.sleep(2)
        return self._ensure_formation(ctx)

    def _ensure_formation(self, ctx):
        """确认回到编队页（有 出撃 / AUTO / スキップチケット），否则按返回。"""
        for _ in range(5):
            ctx.screenshot()
            joined = "".join(t for t, *_ in read_text(ctx._last_screen))
            # 「UNIT」（UNIT CHOICE 页脚）也认：踏破活动这页没有 AUTO周回，
            # 「出撃」还常被 OCR 读成「出擎」，只用原来的三个关键词会误按 BACK 退出。
            if any(k in joined for k in ("出撃", "AUTO", "スキップチケット", "UNIT")):
                return True
            ctx.device.key("BACK")
            time.sleep(2)
        return False

    def _battle_and_clear(self, ctx):
        """等待战斗结算；返回是否成功。"""
        deadline = time.time() + 540
        while time.time() < deadline:
            time.sleep(5)
            ctx.screenshot()
            if handle_network_error(ctx) or handle_download_popup(ctx):
                continue
            if is_title_screen(ctx):
                # 2026-09-30 实测：游戏自己重启回标题后（「データダウンロード」→ 标题），
                # 这里会一直等到 540 秒上限。标题＝战斗肯定没了，直接收工交给上层恢复。
                path = ctx.save_screenshot(ctx._last_screen, "event_battle_title_back.png")
                ctx.logger.warn(
                    f"游戏已回到标题画面（战斗被重启/掉线打断），放弃等待结算；"
                    f"截图已保存: {path}")
                self.failed = True
                return False
            # 「出撃確認」等弹窗（イベント特効メモリア未編成）：底部 キャンセル+OK -> 点 OK
            if self._dismiss_confirmation(ctx):
                continue
            hit = self._find_result_button(ctx)
            if hit is not None:
                ctx.logger.info("战斗结算出现")
                return True
            joined = "".join(t for t, *_ in read_text(ctx._last_screen)).upper()
            if "LOSE" in joined or "敗北" in joined:
                ctx.logger.warn("战斗失败")
                self.failed = True
                return False
        ctx.logger.warn("等待战斗结算超时")
        self.failed = True
        return False

    @staticmethod
    def _dismiss_confirmation(ctx):
        """出撃確認等底部「キャンセル + OK」确认弹窗：点右侧 OK。

        OCR 常把 キャンセル 读成 十七儿/≠十七儿，用「儿/セル」在左下角(底栏)粗略匹配。
        结算/战斗页左下角没有这类取消按钮，不会误触。
        """
        items = read_text(ctx._last_screen)
        has_cancel = False
        for t, cx, cy, s in items:
            tt = t.strip()
            if ("セル" in tt or "儿" in tt or "キャンセル" in tt) and cx < 650 and 550 <= cy <= 700:
                has_cancel = True
                break
        if not has_cancel:
            return False
        for t, cx, cy, s in items:
            if t.strip().upper() == "OK" and 450 <= cx <= 900 and 550 <= cy <= 700:
                ctx.logger.info("出撃確認弹窗：点 OK 进入战斗")
                ctx.click(cx, cy, sleeptime=4)
                return True
        return False

    @staticmethod
    def _find_result_button(ctx):
        """结算页按钮：有 CLEAR 标记时优先「▶次へ」，再「OK」。"""
        items = read_text(ctx._last_screen)
        joined = re.sub(r"[^A-Z0-9]", "", "".join(
            t for t, *_ in items).upper())
        if not any(marker in joined for marker in
                   ("STAGECLEAR", "BATTLEFINISH", "BATTLEFIN", "RESULT", "CLEAR")):
            return None
        for t, cx, cy, s in items:
            if "次へ" in t and cy > 550 and s >= 0.7:
                return (cx, cy)
        for t, cx, cy, s in items:
            if t.strip().upper() == "OK" and cy > 550 and s >= 0.8:
                return (cx, cy)
        return None

    # ---------- 模式切换 / 報酬 ----------
    def _switch_mode(self, ctx, mode):
        """切到指定难度：先进战斗页，多点几次左下的模式按钮直到 MODE 变化。"""
        # 网络错误/返回可能把画面弹回イベント菜单页，先确保回到关卡选择页再切难度。
        if not self._is_target_stage_list(ctx):
            ctx.logger.info("当前不在活动关卡选择页，先重新进入")
            if not self._enter_event_stage_select(ctx):
                ctx.logger.warn("无法重新进入活动关卡选择页，无法切换难度")
                return False
        self._goto_tab(ctx, "バトル")
        for _ in range(4):
            ctx.screenshot()
            if self._mode_is(ctx, mode):
                return True
            ctx.click(HARD_BTN[0], HARD_BTN[1], sleeptime=3)
            time.sleep(2)
        return self._mode_is(ctx, mode)

    @staticmethod
    def _mode_is(ctx, mode):
        joined = "".join(t for t, *_ in read_text(ctx._last_screen))
        joined = joined.replace(" ", "").replace("：", ":")
        return f"MODE:{mode}" in joined

    @staticmethod
    def _read_ap(ctx):
        """读顶栏 AP（形如 160/160）。返回当前 AP；读不到返回 None。"""
        for t, cx, cy, s in read_text(ctx._last_screen):
            if cy > 90:
                continue
            m = re.search(r"(\d{1,3})\s*/\s*(\d{1,3})", t)
            if m and 380 <= cx <= 820:
                return int(m.group(1))
        return None

    def _ap_ok(self, ctx):
        """要花 AP 的关卡：AP 不够就别开打（不花 AP 的关卡直接放行）。

        踏破类活动（サバイバルアタック）的关卡不消耗 AP，ステージ情報里没有
        「消費AP」，所以不会因为 AP 见底被误停。
        """
        joined = "".join(t for t, *_ in read_text(ctx._last_screen)).replace(" ", "")
        if "消費AP" not in joined:
            return True
        current = self._read_ap(ctx)
        if current is None or current >= AP_MIN:
            return True
        ctx.logger.warn(f"AP 不足（当前 {current}），停止清关")
        self.ap_blocked = True
        return False

    def _available_modes(self, ctx):
        """这个活动有没有 NORMAL / HARD 两档难度。返回 ("NORMAL","HARD") 或 (None,)。"""
        ctx.screenshot()
        if self._mode_is(ctx, "NORMAL") or self._mode_is(ctx, "HARD"):
            return ("NORMAL", "HARD")
        # 没有 MODE 标签时，看左下角有没有 HARD / NORMAL 切换按钮
        if self._find_text(ctx, ("HARD", "NORMAL"), (0, 520, 600, 720)):
            return ("NORMAL", "HARD")
        return (None,)

    def _set_mode(self, ctx, mode):
        """切到指定难度：左下的模式按钮按 OCR 找（找不到才退回旧坐标）。"""
        for _ in range(4):
            ctx.screenshot()
            if self._mode_is(ctx, mode):
                return True
            hit = self._find_text(ctx, ("HARD", "NORMAL"), (0, 520, 600, 720))
            ctx.click(*(hit or HARD_BTN), sleeptime=3)
            time.sleep(2)
        ctx.screenshot()
        return self._mode_is(ctx, mode)

    def _run_battle_any(self, ctx, tabs):
        """清空这个活动的战斗：有 NORMAL/HARD 就两档都清，没有就清一遍。"""
        has_tab = "バトル" in tabs
        if has_tab and not self._goto_tab(ctx, "バトル"):
            ctx.logger.warn("无法进入「バトル」标签，跳过战斗清关")
            return False
        for mode in self._available_modes(ctx):
            if mode is not None:
                if not self._set_mode(ctx, mode):
                    ctx.logger.info(f"这个活动没有 {mode} 难度，跳过")
                    continue
                if has_tab:
                    self._goto_tab(ctx, "バトル")
            if not self._clear_battle(ctx, has_battle_tab=has_tab):
                if self.battle_blocked or self.ap_blocked:
                    self.stopped_at_mode = mode or "当前"
                    ctx.logger.warn(
                        f"{mode or '当前'}难度清关遇到无法继续的关卡，停在这里")
                    return False
                self.failed = True
                ctx.logger.warn(f"{mode or '当前'}难度清关失败，结束该活动")
                return False
        return True

    def _claim_reward(self, ctx):
        ctx.screenshot()
        ctx.click(REWARD_BTN[0], REWARD_BTN[1], sleeptime=4)
        for _ in range(8):
            time.sleep(2)
            ctx.screenshot()
            if handle_network_error(ctx):
                continue
            # 领完即关：找 閉じる / OK
            hit = None
            for t, cx, cy, s in read_text(ctx._last_screen):
                if "閉じる" in t or t.strip().upper() == "OK":
                    if 500 <= cx <= 1000 and 550 <= cy <= 700:
                        hit = (cx, cy)
                        break
            if hit is not None:
                ctx.logger.info(f"关闭報酬弹窗 ({hit[0]},{hit[1]})")
                ctx.click(hit[0], hit[1], sleeptime=3)
                if self._is_target_stage_list(ctx):
                    return
            elif self._is_target_stage_list(ctx):
                return

    # ---------- 滚动 ----------
    @staticmethod
    def _scroll_event_down(ctx):
        """小步向下滚动（约 1 张卡），避免一步跨太多跳过目标。"""
        before = ctx._last_screen.copy()
        ctx.device.swipe(900, 620, 900, 620 - _SCROLL_STEP, 250)
        time.sleep(2)
        ctx.screenshot()
        import cv2
        return float(cv2.absdiff(before, ctx._last_screen).mean()) > 5

    @staticmethod
    def _scroll_event_down_small(ctx):
        """小步向下滚动（约 1 张卡），用于逐屏扫描“当前可打”的红点关。"""
        before = ctx._last_screen.copy()
        ctx.device.swipe(900, 620, 900, 620 - _SCROLL_STEP, 250)
        time.sleep(2)
        ctx.screenshot()
        import cv2
        return float(cv2.absdiff(before, ctx._last_screen).mean()) > 5

    @staticmethod
    def _scroll_event_top(ctx):
        """小步向上滚动到顶：每次一小格，直到画面不再变化（已到顶）。"""
        for _ in range(22):
            before = ctx._last_screen.copy()
            ctx.device.swipe(900, 200, 900, 200 + _SCROLL_STEP, 250)
            time.sleep(1.2)
            ctx.screenshot()
            import cv2
            if float(cv2.absdiff(before, ctx._last_screen).mean()) <= 5:
                break

    def _back_home(self, ctx):
        for _ in range(8):
            ctx.screenshot()
            if is_page(ctx, "home"):
                return True
            if self._dismiss_title_return(ctx):
                continue
            if close_content_popup(ctx) or handle_network_error(ctx):
                continue
            if click_home_button(ctx):
                continue
            ctx.device.key("BACK")
            time.sleep(2)
        return is_page(ctx, "home")


class ClearEventAll(_ClearEventBase):
    """限时活动（全部）：遍历「期間限定」里的每个活动，剧情全跳 + 战斗清空 + 领奖励。

    每个活动的页面结构不一样（有的带 ストーリー/バトル 标签、有的只有关卡列表、
    有的点开直接进剧情），进入后按结构分派；战斗、剧情、标签位置都按 OCR 定位，
    所以游戏更新活动也不用改坐标。
    """

    def __init__(self):
        super().__init__(name="限时活动（全部）")

    def on_run(self, ctx):
        return self._run_all_events(ctx, "限时活动")


class ClearEventBattle(ClearEventAll):
    """活动战斗：只看战斗（遍历所有限时活动）。"""

    def __init__(self):
        super().__init__()
        self.name = "活动战斗"
        self.do_story = False


class ClearEventStory(ClearEventAll):
    """活动剧情：只看剧情 + 领奖励（遍历所有限时活动）。"""

    def __init__(self):
        super().__init__()
        self.name = "活动剧情"
        self.do_battle = False


def _screens_same(a, b, tol=2.0):
    """两张画面是否几乎没变化（用于“卡在认不出的画面”的提前放弃判断）。"""
    if a is None or b is None:
        return False
    if getattr(a, "shape", None) != getattr(b, "shape", None):
        return False
    import cv2
    return float(cv2.absdiff(a, b).mean()) <= tol


class ClearEventStages(ClearEventAll):
    """兼容旧配置：剧情 + 战斗一起做。"""

    def __init__(self):
        super().__init__()
        self.name = "活动任务"


class DailyEventSkip(_ClearEventBase):
    """每日活动：进「デイリー」分类，点一次「一括スキップ」把当天能跳的都跳掉。"""

    def __init__(self):
        super().__init__(name="每日活动一键跳过")

    def on_run(self, ctx):
        self._reset_state()
        if not self._enter_event_list(ctx):
            return None
        if not self._select_category(ctx, CAT_DAILY):
            self._back_home(ctx)
            return self.skip("活动一览里没有「デイリー」分类", ctx)
        if not self._bulk_skip(ctx):
            self._back_home(ctx)
            return self.skip("デイリー 里没有可跳过的关卡（可能今天已经做完了）", ctx)
        return self._finish(ctx, "每日活动")

    def _bulk_skip(self, ctx):
        """点右下角「一括スキップ」并确认。点不动＝今天没得跳。"""
        import cv2
        for _ in range(4):
            ctx.screenshot()
            # OCR 经常只读出「括」一个字，所以按文字找不到就退回固定坐标
            hit = (self._find_text(ctx, ("一括スキップ", "一括", "スキップ", "括"),
                                   (860, 1280, 600, 720))
                   or DAILY_BULK_SKIP)
            ctx.logger.info(f"点「一括スキップ」({hit[0]},{hit[1]})")
            before = ctx._last_screen.copy()
            ctx.click(hit[0], hit[1], sleeptime=4)
            ctx.screenshot()
            if float(cv2.absdiff(before, ctx._last_screen).mean()) <= 5:
                if handle_download_popup(ctx) or close_content_popup(ctx):
                    continue
                ctx.logger.info("「一括スキップ」点了没反应（今天没得跳 / 还不在デイリー页）")
                return False
            if self._skip_dialogs(ctx):
                return True
        return False

    def _skip_dialogs(self, ctx):
        """处理一括スキップ之后的确认/结算弹窗，直到回到活动一览。"""
        for _ in range(12):
            time.sleep(2)
            ctx.screenshot()
            if self._is_event_list(ctx):
                return True
            if handle_network_error(ctx) or _handle_download(ctx):
                continue
            if _is_reward_popup(ctx):
                self._tap_ok(ctx, 400, 900, 550, 700)
                continue
            hit = self._find_text(ctx, ("OK", "閉じる", "はい", "スキップ", "受け取", "受取"),
                                  (300, 1100, 480, 715))
            if hit is not None:
                ctx.click(hit[0], hit[1], sleeptime=3)
                continue
            if close_content_popup(ctx):
                continue
        return self._is_event_list(ctx)
