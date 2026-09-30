"""弹窗自动关闭工具。

游戏内的公告弹窗 / 网页样式不固定，但关闭按钮是固定的：
- 弹窗关闭按钮：深紫色 X、白底（popup/btn_close.png）
- 网页关闭按钮：浅紫色 X、深紫底（popup/btn_close_web.png）
- PICKUP 弹窗关闭按钮：白圆圈 + 灰 X（popup/btn_close_pickup.png）

两者都固定在屏幕右上角（x≥1000, y≤120），靠区域限制避免误点。
"""

from core.ocr import read_text


def close_content_popup(ctx):
    """关闭一个内容弹窗 / 网页；返回是否点击过关闭按钮。"""
    for _ in range(3):
        ctx.screenshot()
        # 底部「閉じる」类按钮（物品有效期确认等弹窗）
        result = ctx.find("popup/btn_close_dialog.png", threshold=0.85)
        if result is not None:
            (cx, cy), score = result
            if 450 <= cx <= 750 and 550 <= cy <= 700:
                ctx.logger.info(f"发现底部关闭按钮 ({score:.2f})，点击 ({cx},{cy})")
                ctx.click(cx, cy, sleeptime=2)
                return True
        # 右上角 X 类关闭按钮
        for name in ("popup/btn_close.png", "popup/btn_close_web.png", "popup/btn_close_pickup.png"):
            result, scale = ctx.find_scale(name, threshold=0.85)
            if result is not None:
                (cx, cy), score = result
                # 只认接近原尺寸的关闭按钮（0.8 缩放的误匹配会点到页面元素）
                if cx >= 1000 and cy <= 120 and scale is not None and scale >= 0.95:
                    ctx.logger.info(f"发现关闭按钮 {name} ({score:.2f}, 缩放 {scale:.1f})，点击 ({cx},{cy})")
                    ctx.click(cx, cy, sleeptime=2)
                    return True
    return False


def handle_download_popup(ctx):
    """检测下载数据弹窗，点 OK 同意下载；返回是否处理过。"""
    ctx.screenshot()
    download_popup = (
        ctx.find("popup/download_body.png", threshold=0.85) is not None
        or ctx.find("popup/download_body_v2.png", threshold=0.85) is not None
    )
    if not download_popup:
        return False
    if ctx.click_template("popup/btn_download_ok.png", threshold=0.85, sleeptime=4):
        ctx.logger.info("同意下载数据")
    else:
        ctx.logger.info("下载弹窗 OK 按钮未匹配，按固定位置点击")
        ctx.click(757, 635, sleeptime=4)
    return True


def handle_network_error(ctx):
    """通信失败弹窗（通信に失敗しました / 再接続）：点 OK 重连；找不到就点 タイトルへ。"""
    ctx.screenshot()
    # 1) 模板识别弹窗主体（OCR 文字常被读成乱码，模板最可靠）
    result = ctx.find("popup/network_error_dialog.png", threshold=0.82)
    if result is not None:
        (dx, dy), score = result
        if 300 <= dx <= 1000 and 200 <= dy <= 650:
            ctx.logger.info(f"识别到通信失败弹窗（模板 {score:.2f}），处理重连")
            return _click_network_error_button(ctx)
    # 2) OCR 宽松匹配
    items = read_text(ctx._last_screen)
    joined = "".join(t for t, *_ in items)
    # OCR 常把日文读成简体：通信失败 / 再接 / 通信に失敗 / 再接続
    has_text = (("通信" in joined and ("失敗" in joined or "再接" in joined))
                or "再接続" in joined
                or "ネットワーク" in joined
                or "再度お試し" in joined
                or "再度" in joined)
    if not has_text:
        return False
    return _click_network_error_button(ctx)


def _click_network_error_button(ctx):
    """点击通信失败弹窗的 OK（重连）或 タイトルへ（回标题）。"""
    items = read_text(ctx._last_screen)
    for text, cx, cy, score in items:
        t = text.strip().upper()
        if t == "OK" and 450 <= cx <= 800 and 550 <= cy <= 700:
            ctx.logger.info(f"通信失败弹窗：点 OK 重连 ({cx},{cy})")
            ctx.click(cx, cy, sleeptime=4)
            return True
        if "タイトル" in text and 300 <= cx <= 600 and 550 <= cy <= 700:
            ctx.logger.info("通信失败弹窗：点「タイトルへ」回标题")
            ctx.click(cx, cy, sleeptime=4)
            return True
    ctx.logger.info("通信失败弹窗：按位置点 OK 重连 (758,650)")
    ctx.click(758, 650, sleeptime=4)
    return True


def clear_popups(ctx, max_rounds=4):
    """清理当前画面上的公告弹窗 / 网页。"""
    for _ in range(max_rounds):
        ctx.screenshot()
        if handle_download_popup(ctx):
            continue
        if handle_network_error(ctx):
            continue
        if close_content_popup(ctx):
            continue
        break


def click_ok_by_ocr(ctx, y_min=0, y_max=720):
    """用 OCR 找屏幕上的「OK」文字并点击（通用确认框）。"""
    ctx.screenshot()
    for text, cx, cy, score in read_text(ctx._last_screen):
        if text.strip().upper() == "OK" and y_min <= cy <= y_max and score >= 0.8:
            ctx.logger.info(f"OCR 找到 OK ({cx},{cy})，点击")
            ctx.click(cx, cy, sleeptime=3)
            return True
    return False
