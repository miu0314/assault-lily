# -*- coding: utf-8 -*-
"""军团兑换：进入レギオンメダル交換所，按配置兑换物品。

流程：首页 → 右上角军团按钮 → レギオン主页 → 交換所
→ 按配置找到物品 → 用识别到的 +/-/すべて 设置数量 → 交換 → 确认 OK → 领取 OK → 回首页

配置示例（config.json）：
  "legion_exchange_plan": [
    {"item": "戦術盤I", "qty": "max"},
    {"item": "SKIP TICKET", "qty": 3}
  ]
"""

import json
import re
import time
from datetime import datetime
from pathlib import Path

import app_paths
from core.navigation import click_home_button
from core.ocr import read_text
from core.pages import is_page
from core.popups import close_content_popup, handle_download_popup
from core.task import Task

BASE_DIR = app_paths.base_dir()
CATALOG_PATH = BASE_DIR / "legion_shop_catalog.json"

# 商店按钮模板
TPL_PLUS = "legion/btn_plus.png"
TPL_MINUS = "legion/btn_minus.png"
TPL_ALL = "legion/btn_all.png"
TPL_RESET = "legion/btn_reset.png"
TPL_EXCHANGE = "legion/btn_exchange.png"
TPL_LEGION_HOME = "legion/btn_legion_home.png"


def _norm(text):
    """归一化用于匹配：去掉标点/空格，统一大小写。"""
    t = re.sub(r"[^0-9A-Za-z\u3040-\u30ff\u4e00-\u9fff]", "", str(text)).upper()
    # OCR 常把「戦術盤」读成「載術盤/術盤」，去掉这三个字让名称更好匹配
    t = t.replace("載", "").replace("戦", "").replace("戰", "")
    # OCR 有时把 III 读成「I川」
    return re.sub(r"I+川$", "III", t)


def _item_matches(ocr_name, target, aliases=()):
    tn = _norm(target)
    if not tn:
        return False
    n = _norm(ocr_name)

    def prefix_ok(short, long_):
        """允许短名作为长名的前缀，但剩余部分不能是 I/II/III 这类罗马数字后缀。"""
        if long_.startswith(short):
            rest = long_[len(short):]
            return not (rest and rest[0] in "IVX")
        return False

    if n and (n == tn or n.endswith(tn) or tn.endswith(n)
              or prefix_ok(n, tn) or prefix_ok(tn, n)):
        return True
    for alias in aliases:
        an = _norm(alias)
        if an and n and (n == an or n.endswith(an) or an.endswith(n)
                         or prefix_ok(n, an) or prefix_ok(an, n)):
            return True
    return False


def click_donate_tab(ctx):
    """点击军团页顶部的「寄付・恩恵」标签（有无红点都能识别）。"""
    ctx.screenshot()
    candidates = [
        ("legion/btn_donate.png", 0.85),
        ("legion/btn_donate_masked.png", 0.80),
        ("legion/btn_donate_left.png", 0.85),
    ]
    for template, threshold in candidates:
        result, scale = ctx.find_scale(template, threshold=threshold)
        if result is None or scale is None:
            continue
        (bx, by), score = result
        if 650 <= bx <= 1000 and 80 <= by <= 170:
            ctx.logger.info(f"点击「寄付・恩恵」({bx},{by})")
            ctx.click(bx, by, sleeptime=4)
            return True
    ctx.logger.warn("未识别到「寄付・恩恵」标签")
    return False


def click_legion_button(ctx):
    """点击首页右上角的军团按钮（带红点/不带红点都能识别）。"""
    ctx.screenshot()
    result = ctx.find(TPL_LEGION_HOME, threshold=0.85)
    if result is None:
        result = ctx.find("legion/btn_legion_home_nodot.png", threshold=0.85)
    if result is None:
        return False
    (bx, by), score = result
    if by < 100 and 900 <= bx <= 1200:
        ctx.logger.info(f"点击军团按钮 ({bx},{by})")
        ctx.click(bx, by, sleeptime=5)
        return True
    return False


def click_button_template(ctx, template, x_range, y_range, threshold=0.8):
    """在指定区域内用模板找按钮并点击。"""
    ctx.screenshot()
    result, scale = ctx.find_scale(template, threshold=threshold)
    if result is None:
        return False
    (bx, by), score = result
    if not (x_range[0] <= bx <= x_range[1] and y_range[0] <= by <= y_range[1]):
        return False
    ctx.logger.info(f"点击 {template} ({bx},{by})")
    ctx.click(bx, by, sleeptime=1)
    return True


class ExchangeLegionItems(Task):
    """军团兑换。"""

    def __init__(self):
        super().__init__(name="军团兑换", pre_times=2, post_times=4)

    def pre_condition(self, ctx):
        return is_page(ctx, "home") or True

    def on_run(self, ctx):
        plan = self._get_plan(ctx)
        if not plan:
            ctx.logger.info("未配置兑换计划（legion_exchange_plan），跳过")
            return True
        if not self._enter_shop(ctx):
            ctx.logger.warn("无法进入军团兑换所，跳过")
            return False

        self._scan_and_record(ctx)

        done = 0
        for item in plan:
            if self._exchange_one(ctx, item):
                done += 1
            else:
                ctx.logger.warn(f"兑换「{item.get('item', '?')}」失败，继续下一个")
        # 兑换完重新扫描，让目录里的数量保持最新
        self._scan_and_record(ctx)
        ctx.logger.info(f"军团兑换完成：成功 {done}/{len(plan)} 项")
        if done < len(plan):
            ctx.logger.warn("军团兑换存在失败项，任务标记为未完成")
            self._back_home(ctx)
            return False
        return self._back_home(ctx)

    # ---------- 配置 ----------
    @staticmethod
    def _get_plan(ctx):
        plan = ctx.config.get("legion_exchange_plan", []) or []
        result = []
        for item in plan:
            if not isinstance(item, dict):
                continue
            name = str(item.get("item", "")).strip()
            qty = item.get("qty", 1)
            if not name:
                continue
            if isinstance(qty, str) and qty.strip().lower() in ("max", "all", "全部"):
                qty = "max"
            else:
                try:
                    qty = max(0, int(qty))
                except (TypeError, ValueError):
                    qty = 1
            result.append({"item": name, "qty": qty})
        return result

    # ---------- 导航 ----------
    def _enter_shop(self, ctx):
        for _ in range(10):
            ctx.screenshot()
            if self._is_shop(ctx):
                return True
            if handle_download_popup(ctx):
                continue
            if close_content_popup(ctx):
                continue
            if is_page(ctx, "home"):
                if not self._click_legion_button(ctx):
                    ctx.logger.warn("未找到首页军团按钮")
                    return False
                time.sleep(4)
            elif self._is_legion_page(ctx):
                if not self._click_shop_entry(ctx):
                    ctx.logger.warn("未找到交换所入口")
                    return False
                time.sleep(6)
            else:
                if ctx.find("loading/loading_mark.png", threshold=0.8) is not None:
                    time.sleep(3)
                else:
                    ctx.device.key("BACK")
                    time.sleep(2)
        return self._is_shop(ctx)

    def _click_legion_button(self, ctx):
        return click_legion_button(ctx)

    @staticmethod
    def _is_legion_page(ctx):
        joined = "".join(t for t, *_ in read_text(ctx._last_screen))
        # 传奇战斗页面也有「メダル交換所」按钮：先排除，避免把
        # 「グレードD バトルに挑む」当成交换所入口点到（2026-10-06 实机踩到）。
        if "レジェンダリ" in joined or "LEGENDARY" in joined.upper():
            return False
        return "交換所" in joined or "レギオン" in joined or "外征任務" in joined

    def _click_shop_entry(self, ctx):
        ctx.screenshot()
        # 优先按文字找「交換所」入口（底部区域）
        for text, cx, cy, score in read_text(ctx._last_screen):
            if "交換所" in text and cx > 700 and cy > 500:
                ctx.logger.info(f"点击交换所入口 ({cx},{cy})")
                ctx.click(cx, cy, sleeptime=5)
                return True
        # 固定位置兜底
        ctx.logger.info("按固定位置点击交换所入口 (932,627)")
        ctx.click(932, 627, sleeptime=5)
        return True

    @staticmethod
    def _is_shop(ctx):
        """兑换所页面：右侧有 0/N 数量列，且顶部标题为交换所。"""
        for text, cx, cy, _score in read_text(ctx._last_screen):
            if "交換所" in text and cy < 80:
                return True
        return False

    # ---------- 行识别 ----------
    def _read_rows(self, ctx):
        """读取当前可见的物品行：数量、最大数、名称、价格。"""
        items = read_text(ctx._last_screen)
        counts = []
        for text, cx, cy, _score in items:
            m = re.search(r"(\d+)\s*/\s*(\d+)", text)
            if m and 800 <= cx <= 1100 and 100 <= cy <= 560:
                counts.append((int(m.group(1)), int(m.group(2)), cy))
        rows = []
        for cur, mx, cy in counts:
            name, name_dy = "", 999
            for text, cx2, cy2, _s in items:
                if cx2 < 700 and abs(cy2 - cy) < 45 and "交換" not in text and "所持" not in text:
                    if abs(cy2 - cy) < name_dy:
                        name, name_dy = text, abs(cy2 - cy)
            price = None
            for text, cx2, cy2, _s in items:
                if cx2 < 700 and cy + 25 <= cy2 <= cy + 140:
                    pm = re.search(r"(\d[\d,]*)", text)
                    if pm:
                        price = int(pm.group(1).replace(",", ""))
                        break
            rows.append({"name": name, "current": cur, "max": mx, "y": cy, "price": price})
        return rows

    def _find_row(self, ctx, target, aliases=()):
        """在当前屏找目标物品行；找不到返回 None。"""
        rows = self._read_rows(ctx)
        for row in rows:
            if _item_matches(row["name"], target, aliases):
                return row
        # 名称识别不到时，按价格兜底（仅当该价格在可见行中唯一）
        price = self._catalog_price(target)
        if price is not None:
            matched = [row for row in rows if row["price"] == price]
            if len(matched) == 1:
                ctx.logger.info(f"按价格 {price} 匹配到物品行 (y={matched[0]['y']})")
                return matched[0]
        return None

    def _scroll_shop_down(self, ctx):
        before = ctx._last_screen.copy()
        ctx.device.swipe(900, 500, 900, 150, 400)
        time.sleep(2)
        ctx.screenshot()
        import cv2
        return cv2.absdiff(before, ctx._last_screen).mean() > 5

    # ---------- 按钮 ----------
    def _click_button_in(self, ctx, template, x_range, y_range, threshold=0.8):
        """只在指定区域内找按钮并点击（避免被其它行的按钮抢匹配）。"""
        ctx.screenshot()
        screen = ctx._last_screen
        x0, x1 = x_range
        y0, y1 = y_range
        x1 = min(x1, screen.shape[1])
        y1 = min(y1, screen.shape[0])
        if x1 <= x0 or y1 <= y0:
            return False
        region = screen[y0:y1, x0:x1]
        tpl, mask = ctx.template(template)
        import cv2
        if mask is not None:
            res = cv2.matchTemplate(region, tpl, cv2.TM_CCORR_NORMED, mask=mask)
        else:
            res = cv2.matchTemplate(region, tpl, cv2.TM_CCOEFF_NORMED)
        _, mx, _, loc = cv2.minMaxLoc(res)
        if mx < threshold:
            return False
        bx = x0 + loc[0] + tpl.shape[1] // 2
        by = y0 + loc[1] + tpl.shape[0] // 2
        ctx.logger.info(f"点击 {template} ({bx},{by})")
        ctx.click(bx, by, sleeptime=1)
        return True

    def _set_quantity(self, ctx, row, qty):
        """设置某行兑换数量：qty='max' 点すべて；数字则重置后点 + 相应次数。"""
        y0 = row["y"] + 5
        y1 = row["y"] + 80
        x0, x1 = 700, 1280
        current = max(0, min(int(row.get("current", 0)), int(row["max"])))
        if qty == "max":
            if self._click_button_in(ctx, TPL_ALL, (x0, x1), (y0, y1)):
                return True
            # 部分物品（如上限 1）没有「すべて」，改用点 + 到上限
            ctx.logger.warn("未找到「すべて」按钮，改用点 + 到上限")
            reset = self._click_button_in(ctx, TPL_RESET, (x0, x1), (y0, y1))
            start = 0 if reset else current
            for _ in range(row["max"] - start):
                if not self._click_button_in(ctx, TPL_PLUS, (x0, x1), (y0, y1)):
                    return False
            return True
        target = min(qty, row["max"])
        if target <= 0:
            ctx.logger.info("目标数量为 0，跳过")
            return False
        if target != qty:
            ctx.logger.warn(f"目标数量 {qty} 超过该物品上限 {row['max']}，按上限 {target} 兑换")
        reset = self._click_button_in(ctx, TPL_RESET, (x0, x1), (y0, y1))
        start = 0 if reset else current
        if not reset and current:
            ctx.logger.warn(f"未找到重置按钮，从当前数量 {current} 继续设置")
        if target < start:
            ctx.logger.warn(f"当前数量 {start} 大于目标 {target}，无法精确设置，跳过兑换")
            return False
        for i in range(target - start):
            if not self._click_button_in(ctx, TPL_PLUS, (x0, x1), (y0, y1)):
                ctx.logger.warn(f"第 {i + 1} 次点 + 失败，中止")
                return False
        return True

    # ---------- 单个物品兑换 ----------
    def _exchange_one(self, ctx, item):
        target = item["item"]
        qty = item["qty"]
        aliases = self._catalog_aliases(target)
        row = None
        # 刚扫描过目录时列表可能停在底部，先滚回顶部再找
        for _ in range(5):
            ctx.device.swipe(900, 200, 900, 560, 400)
            time.sleep(1.5)
        for _ in range(6):
            ctx.screenshot()
            row = self._find_row(ctx, target, aliases)
            if row is not None:
                break
            if not self._scroll_shop_down(ctx):
                break
        if row is None:
            ctx.logger.warn(f"在兑换所中未找到物品「{target}」")
            return False
        ctx.logger.info(f"找到物品「{target}」：当前 {row['current']}/{row['max']} (y={row['y']})")

        if not self._set_quantity(ctx, row, qty):
            return False

        # 点交換
        if not self._click_button_in(ctx, TPL_EXCHANGE, (900, 1280), (580, 700)):
            ctx.logger.warn("未找到交換按钮")
            return False
        # 确认弹窗 OK
        if not self._click_dialog_ok(ctx):
            ctx.logger.warn("确认弹窗未出现")
            return False
        # 领取弹窗 OK
        if not self._click_dialog_ok(ctx, keyword=("受取", "獲得")):
            ctx.logger.warn("领取弹窗未出现")
            return False
        # 等回到兑换所
        for _ in range(8):
            time.sleep(2)
            ctx.screenshot()
            if self._is_shop(ctx):
                ctx.logger.info(f"「{target}」兑换完成")
                return True
        return self._is_shop(ctx)

    def _click_dialog_ok(self, ctx, keyword=None, timeout=10):
        for _ in range(timeout):
            time.sleep(1)
            ctx.screenshot()
            texts = read_text(ctx._last_screen)
            if keyword:
                if not any(k in t for t, *_ in texts for k in keyword):
                    continue
            for text, cx, cy, score in texts:
                if text.strip().upper() == "OK" and 500 <= cx <= 900 and 550 <= cy <= 700 and score >= 0.8:
                    ctx.logger.info(f"点击弹窗 OK ({cx},{cy})")
                    ctx.click(cx, cy, sleeptime=2)
                    return True
        return False

    # ---------- 目录 ----------
    def _catalog_aliases(self, target):
        try:
            data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        except Exception:
            return ()
        tn = _norm(target)
        for item in data.get("items", []):
            names = [item.get("name", "")] + list(item.get("aliases", []))
            if any(_norm(n) == tn for n in names):
                return item.get("aliases", [])
        return ()

    def _catalog_price(self, target):
        try:
            data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        except Exception:
            return None
        tn = _norm(target)
        for item in data.get("items", []):
            names = [item.get("name", "")] + list(item.get("aliases", []))
            if any(_norm(n) == tn for n in names):
                return item.get("price")
        return None

    def _scan_and_record(self, ctx):
        """扫描兑换所当前可见物品并更新目录文件。"""
        try:
            old = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
            old_items = old.get("items", [])
        except Exception:
            old_items = []

        # 先滚回列表顶部，避免漏掉上面的物品
        for _ in range(5):
            ctx.device.swipe(900, 200, 900, 560, 400)
            time.sleep(1.5)

        fresh = []
        for _ in range(6):
            ctx.screenshot()
            for row in self._read_rows(ctx):
                hit = None
                for f in fresh:
                    if _item_matches(row["name"], f["name"], f.get("aliases", [])):
                        hit = f
                        break
                if hit is None:
                    fresh.append({"name": row["name"], "aliases": [],
                                  "price": row["price"], "max": row["max"],
                                  "ocr": row["name"]})
                else:
                    if len(row["name"]) > len(hit["name"]):
                        hit["name"] = row["name"]
                    hit["max"] = max(hit["max"], row["max"])
                    if row["price"] is not None:
                        hit["price"] = row["price"]
            if not self._scroll_shop_down(ctx):
                break

        # 从旧目录保留别名和缺失的价格
        items = []
        for f in fresh:
            aliases = []
            for old_item in old_items:
                matched = _item_matches(f["name"], old_item.get("name", ""),
                                        old_item.get("aliases", []))
                # 名称 OCR 读不出时（如「コイン」），按价格匹配保留规范名
                if (not matched and f["name"] in ("", "?")
                        and f["price"] is not None
                        and f["price"] == old_item.get("price")):
                    matched = True
                if matched:
                    aliases = list(old_item.get("aliases", []))
                    # 目录里的规范名称优先，避免显示 OCR 乱码
                    if old_item.get("name"):
                        f["name"] = old_item["name"]
                    if f["price"] is None and old_item.get("price") is not None:
                        f["price"] = old_item.get("price")
                    break
            items.append({
                "name": f["name"] or "?",
                "aliases": aliases,
                "price": f["price"],
                "max": f["max"],
                "ocr": f["ocr"],
            })
        if not items:
            return
        data = {"updated_at": datetime.now().isoformat(timespec="seconds"),
                "currency": "レギオンメダル", "items": items}
        try:
            CATALOG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
            ctx.logger.info(f"已更新兑换所目录：{len(items)} 件 -> {CATALOG_PATH.name}")
        except Exception as e:
            ctx.logger.warn(f"保存目录失败: {e}")

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
