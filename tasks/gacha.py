import re
import time

from core.navigation import click_home_button, ensure_home
from core.ocr import read_text
from core.popups import (close_content_popup, handle_download_popup,
                         handle_network_error)
from core.pages import is_page
from core.task import Task


FREE_TEMPLATES = ("gacha/btn_free_one.png", "gacha/btn_free_eleven.png",
                  "gacha/btn_free_pull.png")
# 「…ジュエルを0個…」那一小段的截图，只在金额 OCR 读不出来时当证据用
CONFIRM_ZERO_TEMPLATE = "gacha/confirm_zero_consume.png"
# 日文界面常用全角数字（２，５００個），解析前先归一化
_FULLWIDTH = str.maketrans("０１２３４５６７８９，", "0123456789,")


def dialog_cost(texts):
    """从抽卡确认弹窗的 OCR 文本里解析要消费的宝石数；读不出来返回 None。

    0 = 确认免费（「マギジュエルを0個消費します」），>0 = 要花宝石
    （例如「2,500個」），None = 这一屏读不出金额。

    **2026-10-04 事故**：旧代码用 `"0個" in joined` 判定免费，而付费弹窗里的
    「2,500個」正好**含子串「0個」** → 付费确认被当成免费确认 → 点 OK 真扣宝石
    （用户被扣 2 万多）。所以这里必须解析出真实数字，不能用子串匹配。
    """
    amounts = []
    for item in texts:
        text = str(item[0]) if item else ""
        if not text:
            continue
        normalized = text.translate(_FULLWIDTH)
        for match in re.finditer(r"([0-9][0-9,]*)\s*[個个]", normalized):
            digits = match.group(1).replace(",", "")
            if digits.isdigit():
                amounts.append(int(digits))
    if not amounts:
        return None
    # 弹窗里同时出现所持数/消费数时取最大的：宁可不抽，也不能花宝石
    return max(amounts)


def parse_amount(text):
    """把一段 OCR 文本里的第一个数字读出来（支持全角、千分位）；读不出返回 None。"""
    normalized = str(text).translate(_FULLWIDTH)
    match = re.search(r"[0-9][0-9,]*", normalized)
    if match is None:
        return None
    digits = match.group(0).replace(",", "")
    return int(digits) if digits.isdigit() else None


class CollectFreeGacha(Task):
    """免费扭蛋：有红点（可免费抽）时自动抽一次。

    流程：首页 → ガチャ → 免费横幅（带红点）→ 详情页 1回ガチャ
    → 确认 0 消费 → OK → 跳过动画 → 关 SNS 弹窗 → 结果 OK → 回首页
    """

    def __init__(self):
        super().__init__(name="免费扭蛋", pre_times=2, post_times=4)
        # 是否撞上过「要花宝石」的确认弹窗（撞上就停整个任务，别再瞎点）
        self.paid_detected = False

    def pre_condition(self, ctx):
        return True

    def on_run(self, ctx):
        self.paid_detected = False
        # 0. 先确保在首页（不在就返回/重启登录），避免直接跳过
        if not ensure_home(ctx):
            ctx.logger.warn("无法进入首页，跳过免费扭蛋")
            return False
        # 遍历扭蛋页左侧所有 banner，抽掉每个有「残1回」的免费入口
        pulled = self._try_free_banners(ctx)
        if pulled is False:
            return False
        return self._back_home(ctx)

    # ---------- 遍历左侧栏 banner ----------
    def _try_free_banners(self, ctx):
        if not is_page(ctx, "home"):
            self._back_home(ctx)
        if not is_page(ctx, "gacha"):
            if not self._enter_gacha(ctx):
                ctx.logger.warn("无法进入扭蛋界面，跳过免费扭蛋")
                return False
        if not self._wait_gacha_list_ready(ctx):
            ctx.logger.warn("扭蛋页加载超时，跳过免费扭蛋")
            return False

        pulled = 0
        MAX_FREE_PULLS = 2  # 一般每天免费就两次，抽满直接结束
        for _round in range(8):
            if pulled >= MAX_FREE_PULLS:
                break
            # 抽完一次后左侧列表会重排（抽过的排到最后），每次从头重新扫描
            self._reset_left_list(ctx)
            if not self._wait_gacha_list_ready(ctx):
                break
            found = False
            for by in self._free_banner_rows(ctx):
                ctx.logger.info(f"扫描左侧 banner 行 y={by}")
                ctx.click(100, by, sleeptime=5)
                btn = None
                for attempt in range(2):
                    btn = self._wait_banner_ready(ctx)
                    if btn is not None or self._banner_pane_loaded(ctx):
                        break
                    ctx.logger.info(f"banner y={by} 详情加载慢，第 {attempt + 1} 次重试")
                    time.sleep(3)
                if btn is None:
                    ctx.logger.info(f"左侧第 {by} 行没有免费入口（已抽过或无免费），跳过")
                    if not self._back_to_gacha_list(ctx):
                        return pulled
                    continue
                bx, by2 = btn
                ctx.logger.info(f"找到免费抽卡按钮 ({bx},{by2})，点击")
                ctx.click(bx, by2, sleeptime=5)
                if self._confirm_and_pull(ctx):
                    pulled += 1
                    found = True
                    # 抽完会重排，回列表再找下一个免费入口
                    if not self._back_to_gacha_list(ctx):
                        return pulled
                    break
                if self.paid_detected:
                    # 撞上付费确认弹窗（已取消）：别再在扭蛋页继续点，收工
                    ctx.logger.warn("检测到付费确认弹窗，停止免费扭蛋任务")
                    return pulled
                # 确认失败（可能是误判的红点 / 付费项），回到列表换下一个候选
                ctx.logger.info(f"banner y={by} 确认非免费，换下一个候选")
                if not self._back_to_gacha_list(ctx):
                    return pulled
            if not found:
                ctx.logger.info("没有更多免费入口，结束免费扭蛋")
                break
        return pulled

    def _back_to_gacha_list(self, ctx):
        """确保回到扭蛋页 banner 列表；失败则尝试回首页重进。返回是否仍在列表中。"""
        if self._ensure_gacha_list(ctx):
            return True
        self._back_home(ctx)
        return self._enter_gacha(ctx)

    def _ensure_gacha_list(self, ctx):
        """确保回到扭蛋页左侧 banner 列表并等它稳定；返回是否已就绪。"""
        for _ in range(8):
            if self._wait_gacha_list_ready(ctx, rounds=4):
                return True
            ctx.screenshot()
            if handle_network_error(ctx):
                continue
            result = ctx.find("gacha/btn_back_gacha.png", threshold=0.8)
            if result is not None:
                ctx.click(result[0][0], result[0][1], sleeptime=3)
                continue
            if ctx.find("loading/loading_mark.png", threshold=0.8) is not None:
                time.sleep(2)
                continue
            ctx.device.key("BACK")
            time.sleep(2)
        return False

    @staticmethod
    def _wait_gacha_list_ready(ctx, rounds=8):
        """等左侧 banner 列表稳定：无加载且连续 2 帧能扫到 3 行以上。"""
        stable = 0
        for _ in range(rounds):
            ctx.screenshot()
            if handle_network_error(ctx):
                stable = 0
                continue
            if ctx.find("loading/loading_mark.png", threshold=0.8) is not None:
                stable = 0
                time.sleep(2)
                continue
            rows = CollectFreeGacha._left_banner_rows(ctx)
            if len(rows) >= 3:
                stable += 1
                if stable >= 2:
                    return True
            else:
                stable = 0
            time.sleep(1.5)
        return False

    @staticmethod
    def _banner_pane_loaded(ctx):
        """右侧 banner 详情已加载：右下角出现抽卡按钮区文字或按钮模板。"""
        for text, cx, cy, score in read_text(ctx._last_screen):
            if 800 <= cx <= 1280 and 460 <= cy <= 700:
                if "回" in text or "ガチャ" in text or "残" in text or "無料" in text:
                    return True
        for tpl in ("gacha/btn_free_one.png", "gacha/btn_free_eleven.png",
                    "gacha/btn_free_pull.png", "gacha/btn_pull_one.png"):
            result = ctx.find(tpl, threshold=0.6)
            if result is not None and result[0][0] >= 900 and result[0][1] >= 460:
                return True
        return False

    @staticmethod
    def _wait_banner_ready(ctx, rounds=6):
        """等右侧详情加载完成，返回免费按钮坐标；已加载但没免费返回 None。"""
        stable = 0
        last_state = None
        last_btn = None
        for _ in range(rounds):
            time.sleep(2)
            ctx.screenshot()
            if handle_network_error(ctx):
                stable = 0
                continue
            if ctx.find("loading/loading_mark.png", threshold=0.8) is not None:
                stable = 0
                continue
            if not CollectFreeGacha._banner_pane_loaded(ctx):
                stable = 0
                continue
            btn = CollectFreeGacha._free_button_on_screen(ctx)
            if btn is not None:
                same_button = (last_state == "button" and last_btn is not None
                               and abs(btn[0] - last_btn[0]) <= 12
                               and abs(btn[1] - last_btn[1]) <= 12)
                if same_button:
                    stable += 1
                    if stable >= 2:
                        return btn
                else:
                    stable = 1
                last_state = "button"
                last_btn = btn
            else:
                stable = stable + 1 if last_state == "none" else 1
                last_state = "none"
                last_btn = None
                if stable >= 2:
                    return None
        return None

    @staticmethod
    def _cost_in_strip(ctx):
        """读抽卡按钮那一行上的「消费数量」；读不到返回 None。

        只看真正的金额（0 / 150 / 1,500），跳过「11回ガチャ」「残り1回」这类
        含数字但不是消费的标签，避免把「11」当成金额。
        """
        amounts = []
        x0, x1, y0, y1 = CollectFreeGacha.COST_STRIP
        for text, cx, cy, score in read_text(ctx._last_screen):
            if not (x0 <= cx <= x1 and y0 <= cy <= y1):
                continue
            t = str(text)
            if any(k in t for k in ("回", "ガチャ", "残", "所持", "ボーナス", "期間")):
                continue
            cost = parse_amount(t)
            if cost is not None:
                amounts.append(cost)
        if not amounts:
            return None
        return max(amounts)

    # 金额数字相对「按钮模板命中点」的位置（从素材量出来的）：
    # 1回ガチャ/11回ガチャ 的消费数字都印在按钮右下角，约 (+30~+130, +5~+55)。
    # 只取这个框里的数字，才能区分同一行里的「1回 0（免费）」和「11回 1,500（付费）」。
    COST_BOX = (30, 130, 5, 55)
    # 没有按钮坐标可参考时（OCR 兜底）看这一条按钮带
    COST_STRIP = (940, 1280, 620, 700)
    # 这些词里有数字但不是「消费数量」，要排除（11回ガチャ / 残り1回 / 所持 1 之类）
    COST_SKIP_WORDS = ("回", "ガチャ", "残", "所持", "ボーナス", "期間")

    @staticmethod
    def _cost_near_button(ctx, bx, by):
        """读「被命中按钮」右下角那个消费数字；读不到返回 None。

        模板只能说明“这里有个按钮”，付费和免费按钮长得几乎一样，
        所以必须看这个数字是不是 0。
        """
        dx0, dx1, dy0, dy1 = CollectFreeGacha.COST_BOX
        amounts = []
        for text, cx, cy, score in read_text(ctx._last_screen):
            if not (bx + dx0 <= cx <= bx + dx1 and by + dy0 <= cy <= by + dy1):
                continue
            t = str(text)
            if any(k in t for k in CollectFreeGacha.COST_SKIP_WORDS):
                continue
            cost = parse_amount(t)
            if cost is not None:
                amounts.append(cost)
        if not amounts:
            return None
        return max(amounts)

    @staticmethod
    def _free_button_on_screen(ctx):
        """从当前截图识别免费抽卡按钮；没有免费剩余返回 None。

        **2026-10-04 事故后**：模板只当「这里有个按钮」的定位用，
        真正判免费必须看按钮上的消费数字是不是 0 ——
        付费按钮和免费按钮长得几乎一样（实测阈值 0.6 时 `btn_free_one.png`
        会命中「11回ガチャ 1,500」），光靠模板等于把宝石往外送。
        """
        # 1) 模板定位 + 消费必须是 0
        for tpl in FREE_TEMPLATES:
            result = ctx.find(tpl, threshold=0.6)
            if result is None:
                continue
            (bx, by) = result[0]
            if by < 480:
                continue
            cost = CollectFreeGacha._cost_near_button(ctx, bx, by)
            if cost is None:
                ctx.logger.info(f"按钮 {tpl} 附近读不到消费数字，按不可免费处理 ({bx},{by})")
                continue
            if cost > 0:
                ctx.logger.info(f"按钮 {tpl} 的消费是 {cost}（不是 0），跳过 ({bx},{by})")
                continue
            ctx.logger.info(f"模板命中免费按钮 {tpl} ({bx},{by})")
            return (bx, by)
        # 2) OCR 兜底：明确读到「無料」**且**消费是 0 才认
        texts = read_text(ctx._last_screen)
        has_free_word = any("無料" in str(t) for t, *_ in texts)
        if has_free_word and CollectFreeGacha._cost_in_strip(ctx) == 0:
            for text, cx, cy, score in texts:
                if ("ガチャ" in str(text) or "回" in str(text)) and cx >= 1000 and cy >= 560:
                    btn = (min(cx + 45, 1250), cy)
                    ctx.logger.info(f"OCR 识别到免费入口 ({btn[0]},{btn[1]})")
                    return btn
        return None

    @staticmethod
    def _zero_remain_near(ctx, bx, by):
        """按钮附近若 OCR 读到 0回，说明已抽完，不应再点。"""
        for text, cx, cy, score in read_text(ctx._last_screen):
            if abs(cx - bx) <= 160 and abs(cy - by) <= 60 and "0回" in text:
                return True
        return False

    @staticmethod
    def _near_button_free(ctx, bx, by):
        """判断按钮附近是否确有免费剩余：
            - 读到「無料」或「残り/残 + 数字>0」-> True（确定免费）
            - 读到「残り0回」-> False（确定已耗尽）
            - 读不到明确信号 -> None（交由 0消费 确认兜底，避免漏抽）
        """
        found_free = False
        found_zero = False
        for text, cx, cy, score in read_text(ctx._last_screen):
            if not (abs(cx - bx) <= 200 and abs(cy - by) <= 110):
                continue
            if "無料" in text:
                found_free = True
                continue
            if "残り" in text or "残" in text:
                m = re.search(r"(\d+)", text)
                if m:
                    if int(m.group(1)) > 0:
                        found_free = True
                    else:
                        found_zero = True
                else:
                    found_free = True
        if found_free:
            return True
        if found_zero:
            return False
        return None

    @staticmethod
    def _reset_left_list(ctx):
        """把左侧 banner 列表滚回顶部（最多 2 次，滚不动就停）。"""
        for _ in range(2):
            ctx.screenshot()
            before = CollectFreeGacha._left_banner_rows(ctx)
            ctx.device.swipe(100, 250, 100, 560, 400)
            time.sleep(2)
            ctx.screenshot()
            after = CollectFreeGacha._left_banner_rows(ctx)
            if after == before:
                break

    @staticmethod
    def _left_banner_rows(ctx):
        """OCR 识别左侧栏 banner 条目中心 y（按 ~100px 聚类）。"""
        ys = []
        for text, cx, cy, score in read_text(ctx._last_screen):
            if cx < 190 and 70 <= cy <= 680:
                ys.append(cy)
        ys.sort()
        groups = []
        for y in ys:
            if groups and y - groups[-1][-1] < 45:
                groups[-1].append(y)
            else:
                groups.append([y])
        return [int(sum(g) / len(g)) for g in groups]

    @staticmethod
    def _free_banner_rows(ctx):
        """免费入口一般在列表最前面，只查前两行，避免翻遍所有 banner。"""
        rows = CollectFreeGacha._left_banner_rows(ctx)
        if len(rows) > 2:
            ctx.logger.info(f"左栏共 {len(rows)} 行，只检查前两行：{rows[:2]}")
        return rows[:2]

    @staticmethod
    def _find_free_pull_button(ctx):
        """在右下角找「残1回」+ 抽卡按钮；有免费剩余才返回按钮坐标。"""
        ctx.screenshot()
        return CollectFreeGacha._free_button_on_screen(ctx)

    @staticmethod
    def _confirm_state(ctx):
        """抽卡确认弹窗的状态。

        'free' = 确认消费 0（免费抽）；'paid' = 要花宝石；None = 没弹窗/读不出。
        金额优先（严格解析），只有在金额读不出来时才退回「0個」那一小段模板。
        """
        ctx.screenshot()
        cost = dialog_cost(read_text(ctx._last_screen))
        if cost == 0:
            return "free"
        if cost is not None and cost > 0:
            return "paid"
        if ctx.find(CONFIRM_ZERO_TEMPLATE, threshold=0.85) is not None:
            return "free"
        return None

    @staticmethod
    def _wait_confirm_state(ctx, rounds=4):
        for _ in range(rounds):
            state = CollectFreeGacha._confirm_state(ctx)
            if state is not None:
                return state
            handle_network_error(ctx)
            time.sleep(2)
        return None

    @staticmethod
    def _cancel_dialog(ctx):
        """取消确认弹窗：优先点「キャンセル」，否则按返回键。"""
        ctx.screenshot()
        for text, cx, cy, score in read_text(ctx._last_screen):
            t = text.strip()
            if ("キャンセル" in t or "やめる" in t) and cy >= 500:
                ctx.logger.info(f"点「{t}」取消 ({cx},{cy})")
                ctx.click(cx, cy, sleeptime=3)
                return True
        ctx.logger.info("没读到「キャンセル」，按返回键取消")
        ctx.device.key("BACK")
        time.sleep(2)
        return False

    def _abort_paid(self, ctx):
        """撞上「要花宝石」的确认弹窗：存证 + 取消 + 停手。"""
        self.paid_detected = True
        path = ctx.save_screenshot(ctx._last_screen, "gacha_paid_dialog.png")
        ctx.logger.warn(f"确认弹窗要花宝石（不是免费抽）→ 取消，本次不再抽卡；"
                        f"截图已保存: {path}")
        self._cancel_dialog(ctx)

    def _click_ok(self, ctx, x0=500, x1=900, y0=550, y1=700, fallback=(758, 650)):
        """OCR 找 OK 点击。点之前再复查一次弹窗金额：只有 0 消费才点。"""
        state = CollectFreeGacha._confirm_state(ctx)
        if state == "paid":
            self._abort_paid(ctx)
            return False
        if state != "free":
            ctx.logger.warn("点 OK 前复查：确认弹窗不是 0 消费，未点击")
            return False
        for text, cx, cy, score in read_text(ctx._last_screen):
            if text.strip().upper() == "OK" and x0 <= cx <= x1 and y0 <= cy <= y1:
                ctx.click(cx, cy, sleeptime=4)
                return True
        if ctx.find(CONFIRM_ZERO_TEMPLATE, threshold=0.85) is None:
            ctx.logger.warn("确认弹窗已消失，未点击固定位置 OK")
            return False
        ctx.click(fallback[0], fallback[1], sleeptime=4)
        return True

    def _confirm_and_pull(self, ctx):
        """等 0消費确认弹窗 → OK → 跳过动画 → 关结果页；返回是否完成。

        2026-10-04 起：**只有确认是 0 消费才点 OK**。
        读到要花宝石（付费弹窗）→ 取消并停手；读不出来 → 什么都不点。
        旧代码在这里还会去点 `gacha/btn_pull_one.png`（付费 1 回按钮），已删除。
        """
        state = self._wait_confirm_state(ctx)
        if state == "paid":
            self._abort_paid(ctx)
            return False
        if state != "free":
            ctx.logger.warn("没等到「0 消费」确认弹窗，不抽（不点任何抽卡按钮）")
            return False
        ctx.logger.info("确认免费（0個），点 OK")
        if not self._click_ok(ctx, 500, 900, 550, 700, fallback=(758, 650)):
            return False
        if not self._skip_animation(ctx):
            ctx.logger.warn("抽卡动画未完成，未计入成功")
            return False
        # 关 SNS 分享弹窗
        ctx.screenshot()
        joined = "".join(t for t, *_ in read_text(ctx._last_screen))
        if "キャンセル" in joined or "SNS" in joined.upper():
            ctx.click(520, 635, sleeptime=2)
        # 结果页可能有多段展示，点跳过 / OK 直到回到扭蛋入口页
        list_frames = 0
        returned_to_list = False
        for _ in range(15):
            ctx.screenshot()
            if handle_network_error(ctx):
                list_frames = 0
                continue
            skips = [(cx, cy) for t, cx, cy, s in read_text(ctx._last_screen)
                     if ("SKIP" in t.upper() or "OPEN" in t.upper())
                     and cx >= 1000 and cy <= 130]
            if skips:
                list_frames = 0
                ctx.click(skips[0][0], skips[0][1], sleeptime=2)
                continue
            ok = [(cx, cy) for t, cx, cy, s in read_text(ctx._last_screen)
                  if t.strip().upper() == "OK" and cy > 550]
            if ok:
                list_frames = 0
                ctx.click(ok[0][0], ok[0][1], sleeptime=3)
                continue
            # 回到扭蛋入口页：左侧列表出现且连续两帧确认，避免在结果页时误判
            rows = self._left_banner_rows(ctx)
            if len(rows) >= 3:
                list_frames += 1
                if list_frames >= 2:
                    returned_to_list = True
                    break
            else:
                list_frames = 0
                ctx.click(640, 360, sleeptime=2)
        if not returned_to_list:
            ctx.logger.warn("抽卡结果页未确认返回扭蛋列表，未计入成功")
            return False
        ctx.logger.info("抽卡结果已关闭")
        return True

    # ---------- 工具 ----------
    @staticmethod
    def _wait_gacha_stable(ctx, times=3):
        """等待扭蛋页加载稳定：无加载标志且出现扭蛋页特征文字。"""
        stable = 0
        for _ in range(12):
            ctx.screenshot()
            if handle_network_error(ctx):
                stable = 0
                continue
            if ctx.find("loading/loading_mark.png", threshold=0.8) is not None:
                stable = 0
                time.sleep(2)
                continue
            joined = "".join(t for t, *_ in read_text(ctx._last_screen))
            found = ("ガチャ" in joined or "回" in joined or "期間" in joined)
            if found:
                stable += 1
                if stable >= times:
                    return True
            else:
                stable = 0
            time.sleep(1.5)
        return False

    @staticmethod
    def _wait_confirm_zero(ctx, rounds=4):
        for _ in range(rounds):
            ctx.screenshot()
            if handle_network_error(ctx):
                continue
            if ctx.find("gacha/confirm_zero_consume.png", threshold=0.85) is not None:
                return True
            joined = "".join(t for t, *_ in read_text(ctx._last_screen))
            if "0個" in joined or "0消費" in joined:
                return True
            time.sleep(2)
        return False

    def _enter_gacha(self, ctx):
        for _ in range(20):
            ctx.screenshot()
            if is_page(ctx, "gacha"):
                return True
            # 下载数据弹窗：点 OK
            if handle_download_popup(ctx):
                continue
            if handle_network_error(ctx):
                continue
            # 内容弹窗：关闭
            if close_content_popup(ctx):
                continue
            # 加载中：等待
            if ctx.find("loading/loading_mark.png", threshold=0.8) is not None:
                time.sleep(3)
                continue
            if is_page(ctx, "home"):
                ctx.click(474, 613, sleeptime=4)
            else:
                ctx.device.key("BACK")
                time.sleep(2)
        return is_page(ctx, "gacha")

    @staticmethod
    def _has_red_dot(ctx, cx, cy, tw, th):
        """检查按钮/横幅右上角区域有没有红点。"""
        ctx.screenshot()
        dots = ctx.find_red_dots()
        for dx, dy, area in dots:
            # 右上角区域：x 在中心右侧，y 在中心上方
            if 0 <= dx - cx <= tw // 2 and -th // 2 <= dy - cy <= 10 and area >= 8:
                return True
        return False

    @staticmethod
    def _skip_animation(ctx):
        """抽卡动画跳过：先点屏幕推进，识别右上角 OPEN ALL / ALL SKIP 并点击，直到结果页。"""
        for i in range(30):
            ctx.screenshot()
            if handle_network_error(ctx):
                continue
            # 到结果页（有 OK / 獲得 / SNS）就结束
            joined = "".join(t for t, *_ in read_text(ctx._last_screen))
            if "獲得" in joined or "SNS" in joined.upper():
                for text, cx, cy, score in read_text(ctx._last_screen):
                    if text.strip().upper() == "OK" and cy > 550:
                        return True
            # 模板优先：右上角 ALL SKIP / OPEN ALL
            clicked = False
            for tpl in ("gacha/btn_all_skip.png", "gacha/btn_open_all.png"):
                result = ctx.find(tpl, threshold=0.7)
                if result is not None and result[0][0] >= 1000 and result[0][1] <= 140:
                    ctx.logger.info(f"点击跳过按钮（{tpl}）({result[0][0]},{result[0][1]})")
                    ctx.click(result[0][0], result[0][1], sleeptime=2)
                    clicked = True
                    break
            if not clicked:
                # OCR 兜底：右上角 SKIP / OPEN / スキップ 文字
                for text, cx, cy, score in read_text(ctx._last_screen):
                    up = text.upper()
                    if any(k in up for k in ("SKIP", "OPEN", "スキップ")) and cx >= 1000 and cy <= 140:
                        ctx.logger.info(f"OCR 找到跳过/打开按钮（{text}），点击 ({cx},{cy})")
                        ctx.click(cx, cy, sleeptime=2)
                        clicked = True
                        break
            if not clicked:
                # 先点屏幕推进动画；几轮后还没识别到就按位置点右上角
                ctx.click(640, 360, sleeptime=1.5)
                if i >= 5:
                    ctx.click(1207, 32, sleeptime=1.5)
        ctx.logger.warn("跳过动画超时，继续尝试后续步骤")
        return False

    @staticmethod
    def _back_home(ctx):
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
