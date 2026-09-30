"""通用导航工具。"""

import time

from core.popups import click_ok_by_ocr

HOME_BUTTON_REGION = (1150, 0, 1240, 80)
# 聊天窗口的关闭键也在右上角，位置比房子更靠边
CHAT_CLOSE_REGION = (1200, 0, 1280, 80)
# 点完主页按钮，画面至少要有这么多像素差才算“这次点击有用”
HOME_CLICK_MOVE_MIN = 5


def close_chat_if_open(ctx):
    """在游戏聊天界面时，点右上角 X 退出聊天。

    聊天里没有房子按钮，硬按返回键容易把游戏整个弹出去；回首页前先走这里。
    """
    if ctx.find("chat/title.png", threshold=0.85) is None:
        return False
    result = ctx.find("chat/btn_close.png", threshold=0.9)
    if result is None:
        return False
    (cx, cy), _score = result
    if not (CHAT_CLOSE_REGION[0] <= cx <= CHAT_CLOSE_REGION[2]
            and CHAT_CLOSE_REGION[1] <= cy <= CHAT_CLOSE_REGION[3]):
        return False
    ctx.logger.info(f"关闭聊天窗口 ({cx},{cy})")
    ctx.click(cx, cy, sleeptime=4)
    return True


def click_home_button(ctx):
    """点击子界面右上角的主页房子按钮；返回这次点击是否真的起作用（画面有没有变）。

    只接受右上角精确区域（x 1150~1240, y 0~80）的匹配，
    避免误匹配/误点旁边的聊天等图标。

    实测 2026-09-17：弹窗挡住时点房子完全没反应，但旧实现只要模板命中就返回 True，
    调用方就一直点它（白点 8 次 ≈ 2 分钟）才走重启分支。现在点完对比一下画面，
    没变化就返回 False，调用方自然会改用返回键。
    """
    import cv2
    for name in ("home/btn_home_quest.png", "home/btn_home_off.png", "home/btn_home_on.png"):
        result = ctx.find(name, threshold=0.85)
        if result is None:
            continue
        (hx, hy), score = result
        if HOME_BUTTON_REGION[0] <= hx <= HOME_BUTTON_REGION[2] and HOME_BUTTON_REGION[1] <= hy <= HOME_BUTTON_REGION[3]:
            before = ctx._last_screen.copy()
            ctx.logger.info(f"点击主页按钮 ({hx},{hy})")
            ctx.click(hx, hy, sleeptime=5)
            ctx.screenshot()
            moved = float(cv2.absdiff(before, ctx._last_screen).mean()) > HOME_CLICK_MOVE_MIN
            if not moved:
                ctx.logger.info("点主页按钮后画面没变化（可能被弹窗挡住），改用返回键")
            return moved
    return False


def ensure_home(ctx, max_rounds=8):
    """确保在首页；返回是否成功。回不去时自动重启游戏并重新登录。"""
    from core.pages import is_page
    from core.popups import (close_content_popup, handle_download_popup,
                             handle_network_error)
    from tasks.launch import WaitForHome, is_loading

    for _ in range(max_rounds):
        ctx.screenshot()
        if is_page(ctx, "home"):
            return True
        if handle_download_popup(ctx):
            continue
        if handle_network_error(ctx):
            continue
        if close_content_popup(ctx):
            continue
        # 聊天界面没有房子按钮，先点聊天自己的 X，别用返回键
        if close_chat_if_open(ctx):
            continue
        # 只有 OK 的弹窗（一括スキップ 结果 / 活动结算之类）要先点掉：
        # 它会把右上角的房子按钮挡在后面，点了没反应（实测 2026-09-17）。
        if click_ok_by_ocr(ctx, y_min=300, y_max=700):
            continue
        if click_home_button(ctx):
            continue
        ctx.device.key("BACK")
        time.sleep(2)
    # 画面还在加载/下载时**不要**重启游戏：重启会把加载打断，下次启动又从头来，
    # 实测 2026-09-16 就是这样循环掉两个多小时的。这里改成继续等。
    if is_loading(ctx):
        ctx.logger.warn("画面仍在加载中，先不重启游戏，继续等待 ...")
        try:
            WaitForHome().run(ctx)
        except Exception as e:
            ctx.logger.warn(f"继续等待加载失败: {e}")
        return is_page(ctx, "home")
    # 回不去：重启游戏重新登录
    ctx.logger.warn("无法回到首页，重启游戏重新登录")
    try:
        ctx.device.start_app()
        WaitForHome().run(ctx)
    except Exception as e:
        ctx.logger.warn(f"重新登录失败: {e}")
    return is_page(ctx, "home")
