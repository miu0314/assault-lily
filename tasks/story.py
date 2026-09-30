# -*- coding: utf-8 -*-
"""推主剧情：从当前进度开始，自动跳过剧情、AUTO 战斗，一直推到推不动为止。

导航：首页 -> 出撃 -> MAIN BATTLE -> 章選択（横向翻页）-> 話選択 -> 关卡列表。
关卡分两种：
- 剧情关：点击后直接播剧情，SKIP 跳过 -> 首通奖励 OK -> 完成；
- 战斗关：点击后进关卡详情页 -> ユニット選択 -> 编队（默认队伍一般已保留）-> 出撃
  -> 战斗中点 AUTO -> STAGE CLEAR -> OK -> 完成。
"""

import re
import time

import cv2

from core.navigation import click_home_button, ensure_home
from core.ocr import read_text
from core.pages import is_page
from core.popups import (close_content_popup, handle_download_popup,
                         handle_network_error)
from core.task import Task

# 坐标常量（1280x720）
HOME_SORTIE = (1214, 643)       # 首页右下角 出撃
MAIN_BATTLE = (664, 335)        # 出撃页 MAIN BATTLE
CHAPTER_CARD = (450, 400)       # 章選択页当前章卡片
CHAPTER_TAB = (245, 348)        # 章選択页左侧章节号
VIEW_SWITCH = (120, 662)        # 关卡列表页 表示切替
EPISODE_TO_PAGER = (97, 662)    # 話列表页左下角 章選択（回章节翻页页）
SKIP_BTN = (858, 61)            # 剧情页 SKIP
SKIP_CONFIRM_OK = (758, 650)    # 跳过确认 OK
REWARD_OK = (642, 658)          # 首通奖励弹窗 OK
STAGE_CLEAR_OK = (1183, 656)    # STAGE CLEAR 结算 OK
UNIT_SELECT_BTN = (1190, 650)   # 详情页 ユニット選択
UNIT_SLOT = (327, 408)          # 编队页主单位空槽
FIRST_UNIT_CARD = (800, 310)    # 单位列表第一张卡（兜底坐标）
SORTIE_BTN = (1182, 631)        # 编队页 出撃
AUTO_BTN = (54, 674)            # 战斗页 AUTO
EP_ROW_X = 581                  # 話列表行 x
STAGE_ROW_X = 654               # 关卡列表行 x


def _texts(ctx):
    return [(t, cx, cy, s) for t, cx, cy, s in read_text(ctx._last_screen)]


def _joined(ctx):
    return "".join(t for t, *_ in _texts(ctx))


def _has(ctx, *keywords):
    """忽略空格与大小写判断 OCR 文本是否包含关键词。"""
    joined = _joined(ctx).replace(" ", "").upper()
    return any(k.replace(" ", "").upper() in joined for k in keywords)


def _find_ok(ctx, x0, x1, y0, y1, score_min=0.75):
    for t, cx, cy, s in _texts(ctx):
        if t.strip().upper() == "OK" and x0 <= cx <= x1 and y0 <= cy <= y1 and s >= score_min:
            return (cx, cy)
    return None


def _row_bright(ctx, cy, x0=450):
    """判断行是否亮色（解锁）。锁定行是深灰遮罩，解锁行是白/亮底。"""
    img = ctx._last_screen
    if img is None:
        return False
    h, w = img.shape[:2]
    y0 = max(0, cy - 45)
    y1 = min(h, cy + 55)
    x1 = min(w, 1050)
    if x1 <= x0 or y1 <= y0:
        return False
    region = img[y0:y1, x0:x1]
    return float(region[:, :, 0].mean()) > 180.0


def _row_complete(ctx, cy):
    for t, cx, cy2, s in _texts(ctx):
        if "COMPLETE" in t.upper() and cx > 1100 and abs(cy2 - cy) <= 55:
            return True
    return False


def _episode_bar_complete(ctx, cy):
    """话列表右侧进度条：已完成的话进度条是亮色，未完成的是深蓝。"""
    img = ctx._last_screen
    if img is None:
        return None
    h, w = img.shape[:2]
    y0 = max(0, cy + 35)
    y1 = min(h, cy + 72)
    x1 = min(w, 1220)
    if y1 <= y0 or x1 <= 1100:
        return None
    bar = img[y0:y1, 1100:x1]
    return float(bar[:, :, 0].mean()) > 110.0


def _handle_download(ctx):
    """剧情数据下载/再生确认弹窗：文字带 再生/自動削除/MB 且底部有 OK。"""
    ok = _find_ok(ctx, 650, 850, 610, 690)
    if _has(ctx, "再生", "自動削除", "ダウンロード", "MB"):
        if ok is not None:
            ctx.logger.info(f"剧情数据下载/再生确认弹窗，点 OK ({ok[0]},{ok[1]})")
            ctx.click(ok[0], ok[1], sleeptime=5)
            return True
        # 没有 OK：可能是语音包选择弹窗（ボイスなし/ボイスあり），
        # 按钮文字 OCR 常读不出，但底部会有两个蓝色按钮，点左边=无语音
        buttons = _bottom_blue_buttons(ctx)
        if len(buttons) >= 2:
            bx, by = buttons[0]
            ctx.logger.info(f"语音包弹窗：点「ボイスなし」({bx},{by})")
            ctx.click(bx, by, sleeptime=5)
            return True
    return False


def _bottom_blue_buttons(ctx):
    """识别弹窗底部一行蓝色按钮的中心点列表（用于语音包选择）。"""
    img = ctx._last_screen
    if img is None:
        return []
    h, w = img.shape[:2]
    x0, x1 = 350, min(w, 1000)
    y0, y1 = 610, min(h, 700)
    if y1 <= y0 or x1 <= x0:
        return []
    region = img[y0:y1, x0:x1]
    hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (90, 40, 40), (150, 255, 255))
    n, _, stats, cents = cv2.connectedComponentsWithStats(mask, 8)
    buttons = []
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if area > 1500 and bw > 100 and 30 <= bh <= 75:
            buttons.append((int(x + x0 + bw // 2), int(y + y0 + bh // 2)))
    return sorted(buttons)


# ---------- 页面识别 ----------

def _is_battle_menu(ctx):
    return _has(ctx, "MAIN BATTLE", "GIGANT")


def _is_chapter_pager(ctx):
    """章選択翻页页：有 CHAPTERSELECT，或顶部标题是 章選/章遥（不是话列表）。"""
    if _has(ctx, "CHAPTERSELECT"):
        return True
    joined = _joined(ctx).replace(" ", "").upper()
    if "STORYMISSION" in joined or "表示切替" in joined:
        return False
    for t, cx, cy, s in _texts(ctx):
        if "章" in t and cy < 120:
            return True
    return False


def _is_episode_page(ctx):
    """話列表页：顶部 話選 标题，或左下角有 章選択 按钮。"""
    if _has(ctx, "話選", "話遥"):
        return True
    for t, cx, cy, s in _texts(ctx):
        if "章" in t and 50 <= cx <= 170 and 630 <= cy <= 700:
            return True
    return False


def _is_stage_view(ctx):
    """关卡列表视图：右侧有至少两个独立的两位数字（关卡编号）。"""
    count = 0
    for t, cx, cy, s in _texts(ctx):
        if re.fullmatch(r"\d{2}", t.strip()) and 580 <= cx <= 730 and 90 <= cy <= 680:
            count += 1
    return count >= 2


def _is_story_screen(ctx):
    """剧情/动画播放页：顶部有 SKIP 按钮。

    实测 2026-09-12 ふじ 食堂 的「プレストーリー」是一段动画：SKIP 在右上角
    (1221,63)，普通剧情页在 (858,61)。以前只认 x 700~950，动画页会被漏掉，
    脚本干等到超时（画面全黑、控件还自动隐藏）。
    """
    for t, cx, cy, s in _texts(ctx):
        if t.strip().upper() == "SKIP" and 600 <= cx <= 1280 and 40 <= cy <= 90:
            return True
    return False


def _is_reward_popup(ctx):
    ok = _find_ok(ctx, 500, 800, 590, 690)
    if ok is None:
        return False
    return _has(ctx, "CLEAR", "報酬", "獲得", "BONUS")


def _is_stage_clear(ctx):
    ok = _find_ok(ctx, 1100, 1260, 610, 690)
    if ok is None:
        return False
    return _has(ctx, "CLEAR", "DROP", "EXP")


def _is_stage_detail(ctx):
    return (_has(ctx, "BOSSINFORMATION", "FIRST REWARD", "PARTAKE")
            or (_has(ctx, "BOSS") and _has(ctx, "消費AP", "消费AP")))


def _is_unit_select(ctx):
    return _has(ctx, "UNIT CHOICE")


def _is_battle(ctx):
    has_home = any(t.strip().upper() == "HOME" and cx < 200 and cy < 80
                   for t, cx, cy, s in _texts(ctx))
    has_auto = any(t.strip().upper() == "AUTO" and cx < 200 and cy > 600
                   for t, cx, cy, s in _texts(ctx))
    return has_home and has_auto


# ---------- 视图切换与滚动 ----------

def _ensure_stage_view(ctx, max_rounds=5):
    for _ in range(max_rounds):
        ctx.screenshot()
        if _is_stage_view(ctx):
            return True
        ctx.click(VIEW_SWITCH[0], VIEW_SWITCH[1], sleeptime=2)
    return _is_stage_view(ctx)


def _scroll_top(ctx, times=6):
    for _ in range(times):
        ctx.device.swipe(640, 180, 640, 620, 300)
        time.sleep(1.0)


def _scroll_down(ctx, times=1):
    for _ in range(times):
        ctx.device.swipe(640, 620, 640, 180, 400)
        time.sleep(2.0)


# ---------- 关卡列表 ----------

def _visible_stage_rows(ctx):
    """当前屏幕的关卡行：[(编号, y, 已完成, 解锁)]。"""
    cands = []
    for t, cx, cy, s in _texts(ctx):
        m = re.search(r"(\d{2})", t)
        if m and 580 <= cx <= 730 and 90 <= cy <= 680:
            cands.append((int(m.group(1)), cy))
    rows = []
    for num, cy in sorted(cands, key=lambda r: r[1]):
        if any(abs(cy - y2) < 45 for _n, y2, _c, _b in rows):
            continue
        # 过滤 AP 消耗数字（在其关卡编号下方 45~115px）
        if any(y2 < cy and 45 <= cy - y2 <= 115 for _n, y2, _c, _b in rows):
            continue
        rows.append((num, cy, _row_complete(ctx, cy), _row_bright(ctx, cy)))
    return rows


def _collect_stages(ctx):
    """滚动收集关卡列表全部关卡状态，然后回到顶部。"""
    _scroll_top(ctx)
    collected = {}
    for _ in range(8):
        ctx.screenshot()
        for num, cy, complete, bright in _visible_stage_rows(ctx):
            if num not in collected:
                collected[num] = {"num": num, "complete": complete, "bright": bright}
            else:
                collected[num]["complete"] = collected[num]["complete"] or complete
                collected[num]["bright"] = collected[num]["bright"] or bright
        _scroll_down(ctx)
    _scroll_top(ctx)
    return [collected[k] for k in sorted(collected)]


def _click_stage_num(ctx, num):
    _scroll_top(ctx)
    for _ in range(6):
        ctx.screenshot()
        for rnum, cy, complete, bright in _visible_stage_rows(ctx):
            if rnum == num:
                ctx.logger.info(f"点击关卡 {num:02d} ({STAGE_ROW_X},{cy})")
                ctx.click(STAGE_ROW_X, cy, sleeptime=4)
                return True
        _scroll_down(ctx)
    return False


# ---------- 話列表 ----------

def _visible_episode_rows(ctx):
    """当前屏幕的話行：[(编号, y, 已完成, 解锁)]。"""
    cands = []
    for t, cx, cy, s in _texts(ctx):
        m = re.search(r"(\d+)\s*[話话]", t)
        if m and 500 <= cx <= 700 and 90 <= cy <= 680:
            cands.append((int(m.group(1)), cy))
    rows = []
    for num, cy in sorted(cands, key=lambda r: r[1]):
        if any(abs(cy - y2) < 45 for _n, y2, _c, _b in rows):
            continue
        bar_complete = _episode_bar_complete(ctx, cy)
        if bar_complete is not None:
            complete = bar_complete
        else:
            # 进度条区域取不到时退回 OCR 数字判断
            complete = False
            for t2, cx2, cy2, s2 in _texts(ctx):
                m2 = re.search(r"(\d+)/(\d+)", t2)
                if m2 and cx2 > 1000 and 0 <= cy2 - cy <= 90:
                    x, y = int(m2.group(1)), int(m2.group(2))
                    complete = (y > 0 and x >= y) or (x == 0 and y == 0)
                    break
        bright = _row_bright(ctx, cy, x0=400)
        rows.append((num, cy, complete, bright))
    return rows


def _collect_episodes(ctx):
    """滚动收集全部話状态（进度多次采样取多数），然后回到顶部。"""
    _scroll_top(ctx)
    collected = {}
    for _ in range(8):
        ctx.screenshot()
        for num, cy, complete, bright in _visible_episode_rows(ctx):
            if num not in collected:
                collected[num] = {"num": num, "samples": [], "bright": False}
            collected[num]["samples"].append(complete)
            collected[num]["bright"] = collected[num]["bright"] or bright
        _scroll_down(ctx)
    _scroll_top(ctx)
    result = []
    for k in sorted(collected):
        samples = collected[k]["samples"]
        result.append({
            "num": k,
            "complete": sum(samples) >= len(samples) / 2,
            "bright": collected[k]["bright"],
        })
    return result


def _click_episode_num(ctx, num):
    _scroll_top(ctx)
    for _ in range(6):
        ctx.screenshot()
        for rnum, cy, complete, bright in _visible_episode_rows(ctx):
            if rnum == num:
                ctx.logger.info(f"点击 {num}話 ({EP_ROW_X},{cy + 25})")
                ctx.click(EP_ROW_X, cy + 25, sleeptime=4)
                return True
        _scroll_down(ctx)
    return False


# ---------- 单关流程 ----------

def _skip_story(ctx):
    """点 SKIP 跳过剧情/动画，再确认「スキップしますか？」。

    普通剧情页的 SKIP 在 (858,61)，动画播放页在右上角 (1221,63)，
    所以位置按 OCR 找，读不到才退回默认坐标。
    """
    hit = None
    for t, cx, cy, s in _texts(ctx):
        if t.strip().upper() == "SKIP" and 600 <= cx <= 1280 and 40 <= cy <= 90 \
                and s >= 0.7:
            hit = (cx, cy)
            break
    ctx.logger.info(f"检测到剧情，点 SKIP 跳过 {hit or SKIP_BTN}")
    ctx.click(*(hit or SKIP_BTN), sleeptime=2)
    ctx.screenshot()
    ok = _find_ok(ctx, 500, 900, 550, 700)
    if ok is not None:
        ctx.click(ok[0], ok[1], sleeptime=4)
    else:
        ctx.click(SKIP_CONFIRM_OK[0], SKIP_CONFIRM_OK[1], sleeptime=4)


def _fill_team(ctx):
    """编队页队伍为空时：点主单位槽 -> 点单位列表第一张卡。"""
    ctx.logger.info("队伍为空，选择单位")
    ctx.click(UNIT_SLOT[0], UNIT_SLOT[1], sleeptime=3)
    ctx.screenshot()
    cards = [(cx, cy) for t, cx, cy, s in _texts(ctx)
             if t.strip().upper().startswith("LV") and cx > 700 and 250 <= cy <= 420]
    if cards:
        cx, cy = cards[0]
        ctx.logger.info(f"点击第一张单位卡 ({cx},{cy})")
        ctx.click(cx, cy, sleeptime=3)
    else:
        ctx.click(FIRST_UNIT_CARD[0], FIRST_UNIT_CARD[1], sleeptime=3)


def _sortie_enabled(ctx):
    """出撃按钮是否为可用（蓝紫色）而不是灰色。"""
    img = ctx._last_screen
    if img is None:
        return True
    x, y = SORTIE_BTN
    if y >= img.shape[0] or x >= img.shape[1]:
        return True
    b, g, r = img[y, x]
    return b > 90 and b >= r


def _prepare_and_sortie(ctx):
    ctx.screenshot()
    if "0/2" in _joined(ctx):
        _fill_team(ctx)
    ctx.screenshot()
    if not _sortie_enabled(ctx):
        ctx.logger.warn("出撃按钮不可用（可能 AP 不足或编队未完成），停止推剧情")
        return "blocked"
    ctx.logger.info("点击出撃开始战斗")
    ctx.click(SORTIE_BTN[0], SORTIE_BTN[1], sleeptime=6)
    ctx.screenshot()
    if _is_unit_select(ctx):
        # 点了出撃但仍在编队页：很可能是 AP 不足被拒绝
        time.sleep(4)
        ctx.screenshot()
        if _is_unit_select(ctx):
            ctx.logger.warn("点击出撃后没有进入战斗（可能 AP 不足），停止推剧情")
            return "blocked"
    return "ok"


def _auto_battle(ctx):
    """战斗中：点 AUTO 后等待结算。"""
    ctx.screenshot()
    if _is_battle(ctx):
        ctx.logger.info("开启 AUTO 自动战斗")
        ctx.click(AUTO_BTN[0], AUTO_BTN[1], sleeptime=2)
    deadline = time.time() + 480
    while time.time() < deadline:
        time.sleep(5)
        ctx.screenshot()
        if handle_network_error(ctx):
            continue
        if _is_stage_clear(ctx) or _is_reward_popup(ctx):
            return True
        joined = _joined(ctx).upper()
        if "LOSE" in joined or "敗北" in joined or "FAIL" in joined:
            ctx.logger.warn("战斗失败，退出战斗")
            ok = _find_ok(ctx, 500, 900, 550, 700)
            if ok is not None:
                ctx.click(ok[0], ok[1], sleeptime=3)
            return False
    ctx.logger.warn("等待战斗结算超时")
    return False


def _play_stage(ctx):
    """处理单个关卡，直到回到关卡列表。返回 'done'/'failed'/'ap_blocked'。"""
    for _ in range(50):
        time.sleep(1)
        ctx.screenshot()
        if handle_network_error(ctx):
            continue
        if _handle_download(ctx):
            continue
        if _is_story_screen(ctx):
            _skip_story(ctx)
            continue
        if _is_reward_popup(ctx):
            ok = _find_ok(ctx, 500, 800, 590, 690)
            if ok is not None:
                ctx.logger.info("首通奖励弹窗，点 OK")
                ctx.click(ok[0], ok[1], sleeptime=4)
            continue
        if _is_stage_clear(ctx):
            ok = _find_ok(ctx, 1100, 1260, 610, 690)
            if ok is not None:
                ctx.logger.info("STAGE CLEAR 结算，点 OK")
                ctx.click(ok[0], ok[1], sleeptime=4)
            continue
        if _is_stage_detail(ctx):
            ctx.logger.info("进入关卡详情页，点 ユニット選択")
            ctx.click(UNIT_SELECT_BTN[0], UNIT_SELECT_BTN[1], sleeptime=4)
            continue
        if _is_unit_select(ctx):
            r = _prepare_and_sortie(ctx)
            if r == "blocked":
                return "ap_blocked"
            continue
        if _is_battle(ctx):
            if not _auto_battle(ctx):
                return "failed"
            continue
        if _is_stage_view(ctx):
            return "done"
    ctx.logger.warn("单关处理超时，回到列表重试")
    return "done"


# ---------- 章节 / 話 处理 ----------

def _enter_episode(ctx, num):
    if not _click_episode_num(ctx, num):
        ctx.logger.warn(f"找不到 {num}話")
        return False
    for _ in range(12):
        time.sleep(2)
        ctx.screenshot()
        if _is_stage_view(ctx):
            return True
        if _handle_download(ctx):
            continue
        if handle_network_error(ctx):
            continue
    return _is_stage_view(ctx)


def _process_episode(ctx):
    """把一个話内所有未通关关卡打完。返回 'empty'/'played'/'blocked'。"""
    failed_stage = None
    found_target = False
    for _ in range(15):
        if not _ensure_stage_view(ctx):
            ctx.logger.warn("无法回到关卡列表视图，结束本遍")
            break
        stages = _collect_stages(ctx)
        target = None
        for st in stages:
            if st["bright"] and not st["complete"]:
                target = st["num"]
                break
        if target is None:
            ctx.logger.info("本遍没有可打的关卡")
            break
        found_target = True
        if target == failed_stage:
            ctx.logger.warn(f"关卡 {target:02d} 连续失败，跳过")
            break
        ctx.logger.info(f"开始打 {target:02d} 号关卡")
        if not _click_stage_num(ctx, target):
            break
        ok = _play_stage(ctx)
        if ok == "ap_blocked":
            return "blocked"
        if ok == "failed":
            failed_stage = target
    return "played" if found_target else "empty"


def _back_to_episode_list(ctx):
    """从关卡列表/任意子页返回話列表。"""
    for _ in range(5):
        ctx.screenshot()
        if _is_episode_page(ctx):
            return True
        ctx.device.key("BACK")
        time.sleep(2)
    return _is_episode_page(ctx)


def _process_chapter(ctx):
    """把当前章节所有未完成話打完。返回 'ok' / 'stop' / 'blocked'。"""
    done_eps = set()
    # 初始扫一次话列表，按编号顺序处理所有“解锁（亮色）”的话
    eps = _collect_episodes(ctx)
    queue = [e["num"] for e in eps if e["bright"]]
    idx = 0

    while True:
        # 跳过已核对过的话
        while idx < len(queue) and queue[idx] in done_eps:
            idx += 1
        if idx >= len(queue):
            # 队列耗尽：可能刚打完前一話解锁了新話，补扫一遍
            ctx.screenshot()
            if not _is_episode_page(ctx):
                break
            found = None
            for _ in range(6):
                ctx.screenshot()
                for num, cy, complete, bright in _visible_episode_rows(ctx):
                    if bright and num not in done_eps and num not in queue:
                        found = num
                        break
                if found is not None:
                    break
                _scroll_down(ctx)
            _scroll_top(ctx)
            if found is None:
                break
            queue.append(found)
            continue

        ep_num = queue[idx]
        idx += 1
        ctx.logger.info(f"处理 {ep_num}話（进度未满）")
        if not _enter_episode(ctx, ep_num):
            ctx.logger.warn(f"无法进入 {ep_num}話")
            done_eps.add(ep_num)
            continue
        r = _process_episode(ctx)
        if r == "blocked":
            return "blocked"
        # 有的話会在星星打满后解锁收尾剧情关（如 07 号），
        # 所以打过的話再回列表确认一遍，确认到没关可打为止。
        confirmed = (r == "empty")
        for _pass in range(2):
            if confirmed:
                break
            if not _back_to_episode_list(ctx):
                break
            if not _enter_episode(ctx, ep_num):
                break
            r2 = _process_episode(ctx)
            if r2 == "blocked":
                return "blocked"
            if r2 == "empty":
                confirmed = True
        if confirmed:
            done_eps.add(ep_num)
        # 无论哪种情况，下一轮前都要回到話列表
        if not _back_to_episode_list(ctx):
            break
    return "ok"


# ---------- 主流程 ----------

def _enter_visible_chapter(ctx):
    """点击章選択页当前可见的章节卡片，进入話列表。返回是否成功。"""
    ctx.click(CHAPTER_CARD[0], CHAPTER_CARD[1], sleeptime=5)
    for _ in range(10):
        ctx.screenshot()
        if _is_episode_page(ctx):
            return True
        if _handle_download(ctx):
            continue
        if handle_network_error(ctx):
            continue
        time.sleep(2)
    return False


def _advance_pager(ctx):
    """从章選択页向前翻一章；已在最后一章（翻不动）时返回 False。"""
    ctx.screenshot()
    before = ctx._last_screen.copy()
    ctx.device.swipe(900, 400, 150, 400, 500)
    time.sleep(3)
    ctx.screenshot()
    after = ctx._last_screen
    if before is None or after is None or before.shape != after.shape:
        return True
    diff = float(cv2.absdiff(before, after).mean())
    return diff > 4.0


def _goto_first_chapter(ctx):
    """在章選択页向右翻（上一章）直到到达第一章。"""
    for _ in range(6):
        ctx.screenshot()
        before = ctx._last_screen.copy()
        ctx.device.swipe(150, 400, 900, 400, 500)
        time.sleep(2.5)
        ctx.screenshot()
        diff = float(cv2.absdiff(before, ctx._last_screen).mean())
        if diff <= 4.0:
            return


def _goto_chapter_number(ctx, target):
    """从章選択页翻到指定章节（1~5），翻不动则停在最后一章。"""
    _goto_first_chapter(ctx)
    for _ in range(target - 1):
        if not _advance_pager(ctx):
            ctx.logger.warn(f"已翻到最后一章（未达到第 {target} 章），从当前章继续")
            return


def _enter_main_battle(ctx):
    for _ in range(8):
        ctx.screenshot()
        if _is_battle_menu(ctx):
            ctx.logger.info("已到出撃菜单")
            break
        if is_page(ctx, "home"):
            ctx.logger.info("在首页，点出撃 (1214,643)")
            ctx.click(HOME_SORTIE[0], HOME_SORTIE[1], sleeptime=5)
            continue
        if handle_download_popup(ctx) or handle_network_error(ctx) or close_content_popup(ctx):
            continue
        ctx.logger.info("未知页面，按返回")
        ctx.device.key("BACK")
        time.sleep(2)
    if not _is_battle_menu(ctx):
        ctx.logger.warn("未能到达出撃菜单")
        return False
    ctx.logger.info("点击 MAIN BATTLE")
    ctx.click(MAIN_BATTLE[0], MAIN_BATTLE[1], sleeptime=6)
    for _ in range(8):
        ctx.screenshot()
        if _is_chapter_pager(ctx):
            ctx.logger.info("已到章選択页")
            return True
        if _is_episode_page(ctx):
            ctx.logger.info("MAIN BATTLE 直接回到話列表，点章選択回翻页页")
            ctx.click(EPISODE_TO_PAGER[0], EPISODE_TO_PAGER[1], sleeptime=4)
            continue
        if _is_stage_view(ctx):
            ctx.logger.info("在关卡列表，先返回話列表")
            ctx.device.key("BACK")
            time.sleep(3)
            continue
        if _handle_download(ctx) or handle_network_error(ctx):
            continue
        if _is_battle_menu(ctx):
            ctx.logger.info("仍在出撃菜单，重试 MAIN BATTLE")
            ctx.click(MAIN_BATTLE[0], MAIN_BATTLE[1], sleeptime=6)
            continue
        ctx.logger.info(f"等待章選択页（{_joined(ctx)[:60]}）")
        time.sleep(2)
    ctx.logger.warn("未能到达章選択页")
    return _is_chapter_pager(ctx)


class PushMainStory(Task):
    """推主剧情：自动跳过剧情，AUTO 战斗，一直推到推不动为止。"""

    def __init__(self):
        super().__init__(name="推主剧情", pre_times=2, post_times=4)

    def pre_condition(self, ctx):
        return True

    def on_run(self, ctx):
        if not ensure_home(ctx):
            ctx.logger.warn("无法回到首页，跳过推主剧情")
            return False
        if not _enter_main_battle(ctx):
            ctx.logger.warn("无法进入主剧情战斗，跳过")
            return False

        start_ch = int(ctx.config.get("story_start_chapter", 0) or 0)
        if 1 <= start_ch <= 5:
            ctx.logger.info(f"按设置跳到第 {start_ch} 章开始推剧情")
            _goto_chapter_number(ctx, start_ch)

        for step in range(5):
            if not _enter_visible_chapter(ctx):
                ctx.logger.warn("当前章节无法进入（可能未开放），停止推剧情")
                break
            ctx.logger.info(f"==== 处理第 {step + 1} 个可进入章节 ====")
            result = _process_chapter(ctx)
            if result == "blocked":
                ctx.logger.warn("AP 不足或无法出撃，停止推剧情（恢复 AP 后再跑）")
                break
            if result == "stop":
                break
            # 返回出撃菜单，准备下一章
            for _ in range(3):
                ctx.screenshot()
                if _is_battle_menu(ctx):
                    break
                ctx.device.key("BACK")
                time.sleep(2)
            if not _enter_main_battle(ctx):
                break
            if not _advance_pager(ctx):
                ctx.logger.info("已翻到最后一章，剧情推进结束")
                break

        # 回首页
        for _ in range(6):
            ctx.screenshot()
            if is_page(ctx, "home"):
                return True
            if close_content_popup(ctx) or handle_network_error(ctx):
                continue
            if click_home_button(ctx):
                continue
            ctx.device.key("BACK")
            time.sleep(2)
        return False
