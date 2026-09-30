"""任务注册表。"""

# 先挂上项目内 deps（cv2 / OCR），否则 Codex 运行环境更新后这里会直接崩。
try:
    import dep_bootstrap

    dep_bootstrap.add_local_deps()
except Exception:
    pass


from tasks.accelerator import DisconnectAccelerator, LaunchAccelerator
from tasks.launch import LaunchGame, LaunchToHome, WaitForHome
from tasks.navigation import GoHomeFirst
from tasks.gacha import CollectFreeGacha
from tasks.gifts import CollectGifts
from tasks.chat import SendChatMessage
from tasks.legion_donate import LegionDonate
from tasks.missions import CollectDailyMissions
from tasks.legion_exchange import ExchangeLegionItems
from tasks.legendary import LegendaryBattle
from tasks.quests import (ClearAramStages, ClearClionStages, ClearDailyQuests,
                          ClearLegionGekiha, SweepRankUpStage)
from tasks.story import PushMainStory
from tasks.event import (ClearEventAll, ClearEventBattle, ClearEventStages,
                         ClearEventStory, DailyEventSkip)


TASK_REGISTRY = {
    "LaunchGame": LaunchGame,
    "LaunchToHome": LaunchToHome,
    "LaunchAccelerator": LaunchAccelerator,
    "DisconnectAccelerator": DisconnectAccelerator,
    "GoHomeFirst": GoHomeFirst,
    "WaitForHome": WaitForHome,
    "CollectFreeGacha": CollectFreeGacha,
    "CollectGifts": CollectGifts,
    "SendChatMessage": SendChatMessage,
    "CollectDailyMissions": CollectDailyMissions,
    "ClearDailyQuests": ClearDailyQuests,
    "SweepRankUpStage": SweepRankUpStage,
    "ClearLegionStages": ClearAramStages,
    "ClearLegionGekiha": ClearLegionGekiha,
    "ClearAramStages": ClearAramStages,
    "ClearClionStages": ClearClionStages,
    # 兼容旧配置：原第二个外征任务名已改为クリオン。
    "ClearMelissaStages": ClearClionStages,
    "ExchangeLegionItems": ExchangeLegionItems,
    "LegionDonate": LegionDonate,
    "LegendaryBattle": LegendaryBattle,
    "PushMainStory": PushMainStory,
    "ClearEventStages": ClearEventStages,
    "ClearEventAll": ClearEventAll,
    "ClearEventBattle": ClearEventBattle,
    "ClearEventStory": ClearEventStory,
    "DailyEventSkip": DailyEventSkip,
}


def get_task(name):
    if name not in TASK_REGISTRY:
        raise KeyError(f"未知任务: {name}，可用: {list(TASK_REGISTRY)}")
    return TASK_REGISTRY[name]()
