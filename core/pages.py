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
