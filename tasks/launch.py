import time

from core.navigation import click_home_button
from core.ocr import read_text
from core.popups import (click_ok_by_ocr, close_content_popup,
                         handle_download_popup, handle_network_error)
from core.pages import is_page, is_title_screen
from core.task import Task
from tasks.navigation import GoHomeFirst

# 加载画面判定（实测 2026-09-16）：加载画面是右下角一个小六边形徽章
# （约 1150~1250, 590~680），旧模板 `loading/loading_mark.png`（大徽章）只匹配
# 0.665（阈值 0.8，且 ctx.find 是单尺度匹配）→ 加载判定全部失效。OCR 能稳定读出
# 「LOADING」(1.00)，所以以 OCR 为主、模板作兜底。
LOADING_KEY = "LOADING"
LOADING_MARK_TEMPLATE = "loading/loading_mark.png"
LOADING_MARK_THRESHOLD = 0.75
# 加载/下载中只等不点，也不让外层判超时去重启游戏（重启会把加载打断）。
# 这是累计加载等待上限（秒），可用配置 home_loading_budget 覆盖。
DEFAULT_LOADING_BUDGET = 900
LOADING_SLEEP = 3
# 全屏 OCR 很贵（约 2.5s、吃满多核），每 N 轮才做一次，中间用便宜模板兜底
LOADING_OCR_EVERY = 2
# 连续认不出画面、盲点这么多轮就存一张截图 + 打 OCR 摘要（下次能一眼看出卡在哪）
STUCK_LOG_EVERY = 20
# 阶段2 里点掉一个弹窗后会先停一会儿不盲点；连续这么多轮没进展就恢复连点。
# 实测 2026-10-02：以前点过弹窗后 `clicking` 再也不会变回 True，90 轮全程静默空转
# 7 分钟才报「等待首页超时」。
RESUME_TAP_AFTER = 6
# 认不出画面时每这么多轮打一行进度，避免日志一片空白看不出卡在哪
PROGRESS_LOG_EVERY = 10
TITLE_BTN = (640, 360)          # 标题画面点任意位置进入游戏


def is_loading(ctx, allow_ocr=True):
    """当前画面是不是加载/下载中。

    allow_ocr=False 时跳过昂贵的全屏 OCR，只用便宜的模板兜底（调用方按轮次限流用）。
    """
    """当前画面是不是加载/下载中。"""
    if ctx.find(LOADING_MARK_TEMPLATE, threshold=LOADING_MARK_THRESHOLD) is not None:
        return True
    if not allow_ocr:
        return False
    joined = "".join(t for t, *_ in read_text(ctx._last_screen))
    return LOADING_KEY in joined.upper().replace(" ", "")


def _title_screen(ctx):
    """标题画面：右上角「サポート」按钮（新版标题）或 TAP TO START（旧版）。"""
    return is_title_screen(ctx)


def _handle_startup_popups(ctx, accel_action):
    """处理启动阶段挡路的弹窗，返回是否处理过。

    顺序：MuMu 加速窗 → 关闭类弹窗 → 下载弹窗 → 通信失败。
    实测 2026-09-16：MuMu 加速弹窗盖在标题画面上时，标题的「サポート」按钮照样命中
    1.00，旧阶段2 没有这一步，标题分支就永远成立 → 每 6 秒点一次 (640,360) 全点在
    弹窗上，刷屏 90 次直到超时。
    """
    if accel_action != "ignore":
        result = ctx.find("popup/mumu_accel_title.png", threshold=0.85)
        if result is not None:
            btn = ctx.find("popup/btn_mumu_no_accel.png", threshold=0.85)
            if btn is not None:
                (bx, by), _score = btn
                ctx.logger.info(f"点击「不加速」({bx},{by})")
                ctx.click(bx, by, sleeptime=2)
            else:
                (tx, ty), _ = result
                ctx.click(tx - 120, ty + 158, sleeptime=2)
            return True
    if close_content_popup(ctx):
        return True
    if handle_download_popup(ctx):
        return True
    if handle_network_error(ctx):
        return True
    return False


class LaunchGame(Task):
    def __init__(self):
        super().__init__(name="启动游戏", pre_times=1, post_times=4)

    def on_run(self, ctx):
        if not ctx.device.app_running():
            ctx.logger.info("游戏未运行，正在启动")
            ctx.device.start_app()
            time.sleep(10)
        else:
            ctx.logger.info("游戏已在运行，不重启")
        ctx.device.start_app()
        time.sleep(3)

    def post_condition(self, ctx):
        return ctx.device.app_running()


class WaitForHome(Task):
    def __init__(self):
        super().__init__(name="等待首页", pre_times=1, post_times=8)

    def on_run(self, ctx):
        accel_action = ctx.config.get("mumu_accel_action", "no_accel")
        rounds1 = int(ctx.config.get("home_wait_rounds", 40))
        rounds2 = int(ctx.config.get("home_wait_rounds2", 90))
        loading_left = int(ctx.config.get("home_loading_budget",
                                          DEFAULT_LOADING_BUDGET))
        loading_logged = False

        def wait_loading():
            """加载中：只等，不点击。返回「加载已经等够预算」= True。"""
            nonlocal loading_left, loading_logged
            if loading_left <= 0:
                return True
            if not loading_logged:
                ctx.logger.info("检测到加载画面，等待加载完成（加载中不点击、不重启游戏）")
                loading_logged = True
            if loading_left % 60 == 0:
                ctx.logger.info(f"仍在加载中…（累计加载等待上限还剩 {loading_left}s）")
            loading_left -= LOADING_SLEEP
            time.sleep(LOADING_SLEEP)
            return False

        # 阶段1：处理 MuMu 弹窗 / 各类弹窗 / 加载 / 标题
        tapped_start = False
        unknown_streak = 0
        last_loading = False
        home_return_after = int(ctx.config.get("home_return_after", 3))
        for round_no in range(rounds1):
            ctx.screenshot()
            if is_page(ctx, "home"):
                return
            if _handle_startup_popups(ctx, accel_action):
                unknown_streak = 0
                continue
            # 加载中：只等。不能点、更不能让外层判超时去重启游戏
            # （重启会把加载/下载打断，实测就是这么循环掉两个多小时的）。
            # OCR 隔轮做：跳过的轮次沿用上一轮结论，免得把加载中的画面当成「认不出」。
            if round_no % LOADING_OCR_EVERY == 0:
                last_loading = is_loading(ctx)
            if last_loading:
                unknown_streak = 0
                if wait_loading():
                    ctx.logger.warn("加载等待超过预算，仍未进首页")
                    break
                continue
            if _title_screen(ctx):
                tapped_start = True
                unknown_streak = 0
                ctx.logger.info("检测到标题画面，点击任意位置进入游戏")
                ctx.click(TITLE_BTN[0], TITLE_BTN[1], sleeptime=5)
                continue
            if tapped_start:
                break
            unknown_streak += 1
            # 画面认不出来又一直没变化：主动回首页。
            # 实测 2026-09-30：停在外征关卡列表时干等了 4 分多钟、CPU 占满两核。
            if unknown_streak >= home_return_after:
                unknown_streak = 0
                if click_home_button(ctx):
                    ctx.logger.info("点主页按钮回首页")
                    continue
                ctx.logger.info("点主页按钮没反应，按返回键回首页")
                ctx.device.key("BACK")
                time.sleep(2)
                continue
            time.sleep(3)

        # 阶段2：每秒点一次屏幕（领签到奖励），直到出现活动弹窗/通知并关闭。
        # 加载中的轮次不消耗轮次预算：加载没结束就一直等，不算“等待超时”。
        ctx.logger.info("开始连点屏幕（处理签到奖励），等待活动弹窗出现...")
        home_rounds = 0
        clicking = True
        idle = 0
        rounds_used = 0
        round_no = 0
        last_loading = False
        while rounds_used < rounds2:
            round_no += 1
            ocr_this_round = round_no % LOADING_OCR_EVERY == 0
            ctx.screenshot()
            # 弹窗永远优先于标题：MuMu 加速弹窗会盖在标题上，
            # 标题的「サポート」按钮照样命中 → 之前就是这样死循环的。
            if _handle_startup_popups(ctx, accel_action):
                clicking = False
                home_rounds = 0
                idle = 0
                continue
            # 同阶段1：跳过的轮次沿用上一轮 OCR 结论，别把加载中的画面当成「到首页了」
            if ocr_this_round:
                last_loading = is_loading(ctx)
            if last_loading:
                if wait_loading():
                    ctx.logger.warn("加载等待超过预算，仍未进首页")
                    return
                continue
            rounds_used += 1
            if rounds_used % PROGRESS_LOG_EVERY == 0:
                ctx.logger.info(f"等待首页中…（已用 {rounds_used}/{rounds2} 轮）")
            # 通信恢复或返回标题后可能再次出现 TAP TO START，这里也要重新点
            if _title_screen(ctx):
                clicking = True
                home_rounds = 0
                idle = 0
                ctx.logger.info("再次检测到标题画面，点击进入游戏")
                ctx.click(TITLE_BTN[0], TITLE_BTN[1], sleeptime=5)
                continue
            if is_page(ctx, "home"):
                clicking = False  # 已见首页，不再连点
                home_rounds += 1
                idle = 0
                if home_rounds >= 2:
                    ctx.logger.info("已进入首页（连续确认）")
                    return
                time.sleep(2)
                continue
            home_rounds = 0
            # 下载/网络错误等带 OK 的弹窗
            if click_ok_by_ocr(ctx, y_min=300, y_max=700):
                clicking = False
                idle = 0
                continue
            if clicking:
                if not ocr_this_round:
                    # 这一轮没做加载判定，宁可不点，避免点在加载画面上
                    time.sleep(1)
                    continue
                # 还没到活动弹窗：每秒点一次屏幕（领签到奖励）
                ctx.click(TITLE_BTN[0], TITLE_BTN[1], sleeptime=1)
                idle += 1
                # 认不出画面又一直在盲点：存一张截图 + 打 OCR，别再静默十几分钟
                if idle % STUCK_LOG_EVERY == 0:
                    path = ctx.save_screenshot(ctx._last_screen,
                                               f"startup_stuck_{idle}.png")
                    joined = "".join(t for t, *_ in read_text(ctx._last_screen))[:120]
                    ctx.logger.warn(f"启动画面连续 {idle} 轮没进展，"
                                    f"截图已保存: {path}｜OCR: {joined}")
            else:
                # 实测 2026-10-02：这里原来只有 sleep(2)。点掉一次弹窗后 `clicking`
                # 就再也不会回到 True，于是 90 轮全程静默空转（约 7 分钟）才报超时，
                # 日志里一行都不打。现在连续几轮没进展就恢复连点。
                idle += 1
                if idle >= RESUME_TAP_AFTER:
                    ctx.logger.info("画面一直没有进展，恢复连点屏幕")
                    clicking = True
                time.sleep(2)
        ctx.logger.warn("等待首页超时")
        try:
            path = ctx.save_screenshot(ctx._last_screen, "startup_timeout.png")
            ctx.logger.info(f"等待首页超时，现场截图已保存: {path}")
        except Exception:
            pass


class LaunchToHome(Task):
    """三合一：启动游戏 → 等待登录进首页 → 确保在首页。"""

    def __init__(self):
        super().__init__(name="启动并进入首页", pre_times=1, post_times=8)

    def on_run(self, ctx):
        LaunchGame().on_run(ctx)
        WaitForHome().on_run(ctx)
        GoHomeFirst().on_run(ctx)

    def post_condition(self, ctx):
        return is_page(ctx, "home")
