"""页面定义：识别素材放在 assets/ 下，页面由一组素材判定。"""

PAGE_DEFINITIONS = {
    "home": [
        "home/btn_gacha.png",
        "home/btn_story.png",
        "home/btn_battle.png",
        "home/btn_gift.png",
    ],
    "gacha": [
        "gacha/btn_free_banner.png",
        "gacha/btn_pull_one.png",
        "gacha/btn_back_gacha.png",
    ],
    "mission": [
        "mission/btn_mission_nav.png",
        "mission/tab_daily2.png",
        "mission/tab_daily_dark.png",
    ],
    "battle": [
        "battle/btn_event.png",
    ],
    "event": [
        "event/btn_daily_nav.png",
    ],
    "quest_daily": [
        "quest/btn_skip_all.png",
    ],
    "gift": [
        "gift/btn_claim_all.png",
    ],
}
POPUP_MARKERS = {}

# 标题画面（游戏重启 / 掉线回到登录前）：右上角「サポート」按钮（新版标题）
# 或中央「TAP TO START」（旧版）。2026-09-16 启动卡死修复时实机确认过这两个模板；
# 2026-09-30 日常实测里，活动战斗被游戏自身的「データダウンロード」重启打断后，
# 战斗等待又空等了 9 分钟才判超时 —— 所以战斗等待也认这个画面，尽早收工。
TITLE_SUPPORT_TEMPLATE = "home/title_support.png"
TAP_TO_START_TEMPLATE = "home/tap_to_start.png"


def is_title_screen(ctx, support_threshold=0.8, tap_threshold=0.7):
    """当前画面是不是游戏标题（登录前）画面。"""
    if ctx.find(TITLE_SUPPORT_TEMPLATE, threshold=support_threshold) is not None:
        return True
    return ctx.find(TAP_TO_START_TEMPLATE, threshold=tap_threshold) is not None


def is_page(ctx, page_name, threshold=None):
    if page_name == "home":
        for markers in POPUP_MARKERS.values():
            for tpl in markers:
                if ctx.find(tpl, threshold=threshold) is not None:
                    return False
    for tpl in PAGE_DEFINITIONS.get(page_name, []):
        if ctx.find(tpl, threshold=threshold) is not None:
            return True
    return False
