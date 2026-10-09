# -*- coding: utf-8 -*-
"""活动任务回归测试。"""

import unittest
from unittest.mock import patch

from tasks.event import (ClearEventStages, ClearEventAll, ClearEventBattle,
                         ClearEventStory, DailyEventSkip, event_card_cells)


class _Logger:
    def __init__(self):
        self.lines = []

    def info(self, message, *args):
        self.lines.append(("INFO", str(message)))

    def warn(self, message, *args):
        self.lines.append(("WARN", str(message)))

    def joined(self):
        return " | ".join(message for _level, message in self.lines)


class _Context:
    def __init__(self):
        self.logger = _Logger()


class _EventStubs:
    """导航/视觉桩：只验证“每个活动怎么处理、结果怎么上报”。"""

    def __init__(self, kind="stage", tabs=("ストーリー", "バトル"),
                 modes=("NORMAL", "HARD"), battle_ok=True,
                 battle_blocked=False, ap_blocked=False):
        super().__init__()
        self.kind = kind
        self.tabs = list(tabs)
        self.modes = tuple(modes)
        self.battle_ok = battle_ok
        self.battle_blocked_flag = battle_blocked
        self.ap_blocked_flag = ap_blocked
        self.modes_seen = []
        self.story_tabs = []
        self.story_row_calls = 0
        self.recover_calls = 0
        self.battle_calls = 0
        self.reward_calls = 0
        self.pre_story_calls = 0

    # --- 导航桩 ---
    def _enter_event_list(self, ctx):
        return True

    def _back_to_event_list(self, ctx):
        return True

    def _process_event_cards(self, ctx):
        self.events_seen += 1
        self._handle_event_page(ctx)
        return True

    def _wait_event_kind(self, ctx, rounds=10):
        return self.kind

    def _detect_tabs(self, ctx):
        return list(self.tabs)

    def _goto_tab(self, ctx, tab):
        return True

    def _play_prestory(self, ctx):
        self.pre_story_calls += 1
        return True

    def _clear_story_cards(self, ctx, tab):
        self.story_tabs.append(tab)

    def _clear_story_stage_row(self, ctx):
        self.story_row_calls += 1
        return True

    def _recover_to_stage_list(self, ctx, rounds=4):
        self.recover_calls += 1
        return True

    def _available_modes(self, ctx):
        return self.modes

    def _set_mode(self, ctx, mode):
        self.modes_seen.append(mode)
        return True

    def _clear_battle(self, ctx, has_battle_tab=True):
        self.battle_calls += 1
        if self.ap_blocked_flag:
            self.ap_blocked = True
            return False
        if self.battle_blocked_flag:
            self.battle_blocked = True
            return False
        return self.battle_ok

    def _claim_event_reward(self, ctx):
        self.reward_calls += 1
        return True

    def _back_home(self, ctx):
        return True


class _EventProbe(_EventStubs, ClearEventAll):
    """限时活动（全部）。"""


class _BattleProbe(_EventStubs, ClearEventBattle):
    """只要战斗。"""


class _StoryProbe(_EventStubs, ClearEventStory):
    """只要剧情。"""


class _StageInfoContext:
    """模拟 _wait_stage_info 依赖的最小 context。"""

    def __init__(self):
        self.logger = _Logger()
        self._last_screen = object()
        self.downloaded = False

    def screenshot(self):
        return self._last_screen


class _WaitKindContext:
    """_wait_event_kind 依赖的最小 context。"""

    def __init__(self):
        self.logger = _Logger()
        self._last_screen = object()
        self.saved = []
        self.stage_ready = False

    def screenshot(self):
        return self._last_screen

    def save_screenshot(self, img, name=None):
        self.saved.append(name)
        return f"screenshots/{name}"


class ClaimEventRewardTest(unittest.TestCase):
    """领活动奖励（2026-09-20：「報酬」被 OCR 读成「赣州」导致 42 个奖励没领）。"""

    def test_claim_reward_uses_template_when_ocr_misses(self):
        """「報酬」两个字 OCR 读花时（实测读成「赣州」）要用模板认出按钮。

        实测 2026-09-20：左栏「報酬」上的红点写着 42，但 OCR 只读出「赣州」，
        `_claim_event_reward` 找不到关键词直接返回 False → 42 个奖励一直没领。
        """
        task = ClearEventStages()
        clicks = []

        class _C:
            def __init__(self):
                self.logger = _Logger()
                self._last_screen = object()

            def screenshot(self):
                return self._last_screen

            def click(self, x, y, sleeptime=1.0):
                clicks.append((x, y))

            def find(self, name, threshold=None):
                if name == "event/btn_event_reward.png":
                    return ((134, 587), 0.98)
                return None

        ctx = _C()
        task._find_text = lambda c, keys, region: None      # OCR 完全读不出
        task._is_target_stage_list = lambda c: True
        task._is_event_list = lambda c: False
        with patch("tasks.event.time.sleep"), \
                patch("tasks.event.handle_network_error", return_value=False), \
                patch("tasks.event._handle_download", return_value=False):
            ok = task._claim_event_reward(ctx)

        self.assertTrue(ok)
        self.assertEqual(clicks, [(134, 587)])               # 用模板坐标点了「報酬」

    def test_claim_reward_presses_claim_all_before_close(self):
        """奖励弹窗里要先点「一括受取」，不能直接关掉（否则奖励没领）。"""
        task = ClearEventStages()
        clicks = []

        class _C:
            def __init__(self):
                self.logger = _Logger()
                self._last_screen = object()

            def screenshot(self):
                return self._last_screen

            def click(self, x, y, sleeptime=1.0):
                clicks.append((x, y))

            def find(self, name, threshold=None):
                return None

            class _Dev:
                def key(self, code):
                    pass

            device = _Dev()

        ctx = _C()
        state = {"step": 0}

        def find_text(c, keys, region):
            if "報酬" in keys:
                if state["step"] == 0:
                    state["step"] = 1        # 点了「報酬」，奖励弹窗打开
                    return (134, 587)
                return None
            if "一括受取" in keys:
                if state["step"] == 1:
                    state["step"] = 2
                    return (900, 600)
                return None
            if "閉じる" in keys:
                if state["step"] == 2:
                    state["step"] = 3
                    return (700, 620)
                return None
            return None

        task._find_text = find_text
        task._is_target_stage_list = lambda c: state["step"] >= 3
        task._is_event_list = lambda c: False
        with patch("tasks.event.time.sleep"), \
                patch("tasks.event.handle_network_error", return_value=False), \
                patch("tasks.event._handle_download", return_value=False):
            ok = task._claim_event_reward(ctx)

        self.assertTrue(ok)
        self.assertEqual(clicks, [(134, 587), (900, 600), (700, 620)])


    def test_claim_reward_accepts_ocr_of_kaishu_as_huishu(self):
        """踏破イベント的「回収」会被 OCR 读成「回收」，关键词里也要认。

        实测 2026-10-07：サバイバルアタック 左栏「回収」上挂着 100 个未领奖励，
        但 OCR 输出的是「回收」—— 关键词只有「報酬 / 回収」→ 找不到按钮 →
        静默返回 False，奖励一直没领（同一天的正常活动「報酬」能正常领）。
        """
        task = ClearEventStages()
        clicks = []
        tokens = [("回收", 142, 596, 0.99)]          # 实机 OCR 输出

        class _C:
            def __init__(self):
                self.logger = _Logger()
                self._last_screen = object()

            def screenshot(self):
                return self._last_screen

            def click(self, x, y, sleeptime=1.0):
                clicks.append((x, y))

            def find(self, name, threshold=None):
                return None                          # 模板也认不出这个按钮

        ctx = _C()

        def find_text(c, keys, region):
            x0, x1, y0, y1 = region
            for t, cx, cy, s in tokens:
                if (x0 <= cx <= x1 and y0 <= cy <= y1
                        and any(k in t for k in keys)):
                    return (cx, cy)
            return None

        task._find_text = find_text
        task._is_target_stage_list = lambda c: True
        task._is_event_list = lambda c: False
        with patch("tasks.event.time.sleep"), \
                patch("tasks.event.handle_network_error", return_value=False), \
                patch("tasks.event._handle_download", return_value=False):
            ok = task._claim_event_reward(ctx)

        self.assertTrue(ok)
        self.assertEqual(clicks, [(142, 596)])


class _TabCtx:
    """_goto_tab / _recover_to_stage_list 依赖的最小 context。"""

    def __init__(self, in_story=True):
        self.logger = _Logger()
        self._last_screen = object()
        self.clicks = []
        self.in_story = in_story          # 还停在剧情播放页
        self.saved = []

    def screenshot(self):
        return self._last_screen

    def click(self, x, y, sleeptime=1.0):
        self.clicks.append((x, y))

    def find(self, template_name, threshold=None):
        return None          # 默认没有「タイトルに戻る」等模板命中

    def save_screenshot(self, img, name=None):
        self.saved.append(name)
        return f"screenshots/{name}"


class GotoTabRecoveryTest(unittest.TestCase):
    """读完剧情后残留画面导致「バトル」标签找不到（2026-09-18 实机踩到）。"""

    def test_find_next_stage_button_uses_template(self):
        """结算页的「▶次へ」用模板识别（字太小 OCR 读不出来）。

        实测 2026-09-20：STAGE CLEAR 页右下是「▶次へ / 再戦 / OK」，
        次へ 在 (890,658)。模板实测真按钮 1.000、其它画面最高 0.856，所以阈值 0.92。
        """
        task = ClearEventStages()

        class _C:
            def __init__(self, hit):
                self.hit = hit

            def find_scale(self, name, threshold=None):
                return (self.hit, 0.99) if self.hit else (None, None)

        self.assertEqual(task._find_next_stage_button(_C(((890, 658), 0.99))),
                         (890, 658))
        # 位置不对（不在结算页按钮区）就不认，避免误点
        self.assertIsNone(task._find_next_stage_button(_C(((300, 200), 0.99))))
        self.assertIsNone(task._find_next_stage_button(_C(None)))

    def test_settle_current_stage_clicks_next_then_ok(self):
        """结算页：有「次へ」先点它（进下一关）；没有时点 OK（回列表）。"""
        task = ClearEventStages()
        clicks = []

        class _C:
            def __init__(self):
                self.logger = _Logger()
                self._last_screen = object()

            def screenshot(self):
                return self._last_screen

            def click(self, x, y, sleeptime=1.0):
                clicks.append((x, y))

        ctx = _C()
        task._is_target_stage_list = lambda c: False
        task._back_to_stage_list = lambda c: False
        task._find_next_stage_button = lambda c: (890, 658)
        with patch("tasks.event.time.sleep"):
            moved = task._settle_current_stage(ctx)
        self.assertTrue(moved)                 # True = 进了下一关
        self.assertEqual(clicks, [(890, 658)])

        clicks.clear()
        state = {"ok": False}

        def find_result(c):
            if state["ok"]:
                return None
            state["ok"] = True
            return (1183, 656)

        task._find_next_stage_button = lambda c: None
        task._find_result_button = find_result
        task._is_target_stage_list = lambda c: state["ok"]   # 点完 OK 就回到关卡列表
        with patch("tasks.event.time.sleep"):
            moved = task._settle_current_stage(ctx)
        self.assertFalse(moved)                # False = 点了 OK 回列表
        self.assertEqual(clicks, [(1183, 656)])

    def test_clear_one_stage_chains_next_stage(self):
        """点「次へ」之后要在同一轮里接着打下一关（不用回列表再扫）。"""
        task = ClearEventStages()
        task.processed_count = 0
        fought = []
        task._wait_stage_info = lambda c, rounds=15: True
        task._wait_next_stage_info = lambda c, rounds=10: "info"
        task._ap_ok = lambda c: True
        task._fight_current_stage = lambda c: (fought.append(1), True)[1]
        task._back_to_stage_list = lambda c: True
        settle = [True, False]                 # 第一次有「次へ」，第二次点 OK 收工
        task._settle_current_stage = lambda c: settle.pop(0) if settle else False

        class _C:
            def __init__(self):
                self.logger = _Logger()
                self._last_screen = object()

            def screenshot(self):
                return self._last_screen

            def click(self, x, y, sleeptime=1.0):
                pass

        with patch("tasks.event.time.sleep"):
            ok = task._clear_one_stage(_C(), 300)

        self.assertTrue(ok)
        self.assertEqual(len(fought), 2)       # 连着打了两关
        self.assertEqual(task.processed_count, 1)   # 多打的那一关也计数

    def test_clear_battle_waits_for_next_stage_dot(self):
        """打完一关后要等下一关的粉点刷出来，不能立刻判「到底了」。

        实测 2026-09-20：「乙女のピンチ」打完 05 之后 06 其实已经解锁，
        但粉点还没刷新，脚本扫两遍就收工 → 一轮只推进 1~2 关。
        """
        task = ClearEventStages()
        cleared = []
        task.processed_count = 0
        task._tab_has_red_dot = lambda c, tab: True
        task._clear_one_stage = lambda c, y: (cleared.append(y), True)[1]
        task._back_to_stage_list = lambda c: True
        task._enter_event_stage_select = lambda c: True
        task._goto_tab = lambda c, tab: True
        task._scroll_event_down_small = staticmethod(lambda c: False)
        task._scroll_event_top = staticmethod(lambda c: None)

        # 打完 218 后，粉点要等到第 3 次轮询才刷出来；打完 378 之后就真的没有了
        seq = [[218], [], [], [378], [378], [], [], [], [], [], [], []]

        class _C:
            def __init__(self):
                self.logger = _Logger()
                self._last_screen = object()

            def screenshot(self):
                return self._last_screen

        ctx = _C()
        with patch("tasks.event.ClearEventStages._available_stage_rows",
                   side_effect=lambda c: seq.pop(0) if seq else []), \
                patch("tasks.event.time.sleep"):
            task._clear_battle(ctx, has_battle_tab=True)

        self.assertEqual(cleared, [218, 378])          # 两关都打了
        self.assertIn("已到活动战斗列表底部", ctx.logger.joined())

    def _task(self, ctx):
        task = ClearEventStages()
        task._is_target_stage_list = lambda c: not c.in_story
        return task

    def test_goto_tab_recovers_from_leftover_story_screen(self):
        """画面还停在剧情页时要先退回关卡页再重试，不能直接放弃战斗。

        实测 2026-09-18：活动 1 的 4 话剧情跳完后画面还在剧情里，脚本在剧情页上找
        「バトル」标签必然失败 → 旧逻辑直接放弃 → 整个活动的战斗被跳过，
        最后上报「未能完全清空 — 第 1 个活动（战斗）」。
        """
        ctx = _TabCtx(in_story=True)
        task = self._task(ctx)
        positions = {"/关": {"ストーリー": (636, 122), "バトル": (876, 122)}}

        def fake_positions(c):
            return {} if c.in_story else positions["/关"]

        with patch("tasks.event.ClearEventStages._tab_positions",
                   side_effect=fake_positions), \
                patch("tasks.event.ClearEventStages._detect_tabs",
                      side_effect=lambda c: [] if c.in_story
                      else ["ストーリー", "バトル"]), \
                patch("tasks.event.read_text", return_value=[]), \
                patch("tasks.event._is_story_screen",
                      side_effect=lambda c: c.in_story), \
                patch("tasks.event._skip_story",
                      side_effect=lambda c: setattr(c, "in_story", False)), \
                patch("tasks.event._is_reward_popup", return_value=False), \
                patch("tasks.event.handle_network_error", return_value=False), \
                patch("tasks.event.time.sleep"):
            ok = task._goto_tab(ctx, "バトル")

        self.assertTrue(ok)
        self.assertEqual(ctx.clicks, [(876, 122)])      # 退回关卡页后才点的标签
        self.assertIn("先退回关卡选择页", ctx.logger.joined())

    def test_goto_tab_still_gives_up_when_tab_really_missing(self):
        """真的是没有标签栏时（无标签活动）不能无限重试，也别去跳剧情。"""
        ctx = _TabCtx(in_story=False)
        task = self._task(ctx)

        with patch("tasks.event.ClearEventStages._tab_positions",
                   return_value={}), \
                patch("tasks.event.ClearEventStages._detect_tabs",
                      return_value=[]), \
                patch("tasks.event.read_text", return_value=[]), \
                patch("tasks.event._is_story_screen", return_value=False), \
                patch("tasks.event._skip_story") as skip_story, \
                patch("tasks.event._is_reward_popup", return_value=False), \
                patch("tasks.event.handle_network_error", return_value=False), \
                patch("tasks.event.time.sleep"):
            ok = task._goto_tab(ctx, "バトル")

        self.assertFalse(ok)
        self.assertEqual(ctx.clicks, [])
        self.assertFalse(skip_story.called)

    def test_recover_to_stage_list_dismisses_title_return_dialog(self):
        """停在「タイトルに戻る」确认框时要先按返回取消，再继续。

        实测 2026-09-18 19:29：返回键按过头弹出这个确认框，活动任务不认它 →
        后面既认不出关卡、也找不到卡片，整个活动遍历提前收工
        （`screenshots/event_list_empty.png` 截到的就是它）。
        """
        ctx = _TabCtx(in_story=False)
        task = ClearEventStages()
        state = {"cancelled": False}
        task._is_target_stage_list = lambda c: state["cancelled"]

        class _Dev:
            def key(self, code):
                state["cancelled"] = True

        ctx.device = _Dev()

        def fake_find(name, threshold=None):
            if name == "popup/title_return_title.png" and not state["cancelled"]:
                return ((640, 26), 0.98)
            return None

        ctx.find = fake_find
        with patch("tasks.event.read_text", return_value=[]), \
                patch("tasks.event._is_story_screen", return_value=False), \
                patch("tasks.event._is_reward_popup", return_value=False), \
                patch("tasks.event.handle_network_error", return_value=False), \
                patch("tasks.event.time.sleep"):
            ok = task._recover_to_stage_list(ctx)

        self.assertTrue(ok)
        self.assertTrue(state["cancelled"])
        self.assertIn("「タイトルに戻る」", ctx.logger.joined())


class IsTargetStageListTest(unittest.TestCase):
    """关卡列表判据：结算页不能被当成关卡列表（2026-10-09 实机踩到）。

    リリィスファンタジーゼロ ステージ05 的 STAGE CLEAR 页上有 MISSION / AP /
    「×94」这样的两位数掉落数量 —— 旧判据「关卡行数字 + MISSION/AP」直接命中，
    把结算页当成关卡列表 → 「次へ」不点、流程跑偏、整个活动只做了 1 个。
    """

    class _C:
        def __init__(self):
            self._last_screen = object()

        def screenshot(self):
            return self._last_screen

    def _check(self, texts):
        task = ClearEventStages()
        with patch("tasks.event.read_text", return_value=list(texts)):
            return task._is_target_stage_list(self._C())

    def test_stage_clear_page_is_not_stage_list(self):
        self.assertFalse(self._check([
            ("STAGE CLEAR", 640, 37, 0.95),
            ("NORMAL：リリィスファンタジーゼロステージ05", 640, 71, 0.9),
            ("MISSION", 705, 226, 0.9),
            ("COMPLETE", 1181, 280, 0.9),
            ("AP", 1106, 173, 0.9),
            ("170/160", 1180, 173, 0.9),
            ("94", 738, 489, 0.92),
            ("次へ", 890, 658, 0.85),
            ("OK", 1180, 658, 0.9),
        ]))

    def test_clear_bonus_page_is_not_stage_list(self):
        self.assertFalse(self._check([
            ("CLEAR BONUS", 640, 37, 0.95),
            ("初回クリア報酬獲得", 640, 120, 0.9),
            ("OK", 638, 618, 0.9),
        ]))

    def test_real_stage_list_still_detected(self):
        self.assertTrue(self._check([
            ("報酬受取期間：2026/10/29 22:59まで", 260, 181, 0.9),
            ("表示切替", 120, 662, 0.95),
            ("ステージ01", 640, 140, 0.9),
            ("AP", 610, 203, 0.9),
            ("MISSION", 730, 203, 0.9),
        ]))


class NextStageHandoffTest(unittest.TestCase):
    """点「次へ」之后的三种去向（2026-10-07 踏破イベント实机踩到）。

    实测 サバイバルアタック ステージ05→06：点「次へ」**不经过ステージ情報**，
    保留上次队伍时游戏直接开打下一关（+3s 加载 → +10s 开场动画 → +35s 已经在
    打 WAVE 1/1）。旧代码只认「消費AP」，等 96 秒判失败 → 按 BACK 乱走，
    那天卡了 12 分钟、最后整个活动判「清关失败」。
    """

    class _C:
        def __init__(self):
            self.logger = _Logger()
            self._last_screen = object()
            self.saved = []
            self.clicks = []

        def screenshot(self):
            return self._last_screen

        def click(self, x, y, sleeptime=1.0):
            self.clicks.append((x, y))

        def save_screenshot(self, img, name=None):
            self.saved.append(name)
            return "screenshots/" + str(name)

    def _wait(self, texts):
        task = ClearEventStages()
        ctx = self._C()
        with patch("tasks.event.read_text", return_value=list(texts)), \
                patch("tasks.event.handle_network_error", return_value=False), \
                patch("tasks.event._handle_download", return_value=False), \
                patch("tasks.event.time.sleep"):
            state = task._wait_next_stage_info(ctx)
        return state

    def test_accepts_stage_info_page(self):
        state = self._wait([("ステージ情報", 100, 35, 0.95),
                            ("消費AP：", 60, 196, 0.9)])
        self.assertEqual(state, "info")

    def test_accepts_stage_info_title_without_ap_text(self):
        # 踏破活动 AP 栏是「—」；万一 OCR 没读到「消費AP」，标题也要认
        state = self._wait([("ステージ情報", 100, 35, 0.95),
                            ("推奨総戦闘力", 400, 200, 0.9)])
        self.assertEqual(state, "info")

    def test_detects_directly_started_battle(self):
        state = self._wait([("HOME", 42, 27, 0.9), ("WAVE 1/1", 700, 26, 0.9),
                            ("AUTO MODE", 800, 590, 0.9)])
        self.assertEqual(state, "battle")

    def test_clear_page_is_not_a_handoff_success(self):
        state = self._wait([("STAGE CLEAR", 640, 37, 0.95),
                            ("▶次へ", 890, 658, 0.9), ("OK", 1180, 658, 0.9)])
        self.assertFalse(state)

    def test_clear_bonus_page_is_not_a_handoff_success(self):
        state = self._wait([("CLEAR BONUS", 640, 37, 0.95),
                            ("初回クリア報酬獲得", 640, 120, 0.9),
                            ("OK", 638, 618, 0.9)])
        self.assertFalse(state)

    def test_clear_one_stage_follows_auto_started_battle(self):
        """「次へ」直接开打下一关：不再点ユニット選択，等结算后接着链下去。"""
        task = ClearEventStages()
        task.processed_count = 0
        fought = []
        settled = []
        task._wait_stage_info = lambda c, rounds=15: True
        task._ap_ok = lambda c: True
        task._fight_current_stage = lambda c: (fought.append(1), True)[1]
        task._wait_next_stage_info = lambda c, rounds=10: "battle"
        task._battle_and_clear = lambda c: True
        task._back_to_stage_list = lambda c: True
        settle = [True, False]   # 第一次「次へ」；自动开打的这关打完点 OK 收工

        def settle_once(c):
            settled.append(1)
            return settle.pop(0) if settle else False

        task._settle_current_stage = settle_once

        ctx = self._C()
        with patch("tasks.event.time.sleep"):
            ok = task._clear_one_stage(ctx, 300)

        self.assertTrue(ok)
        self.assertEqual(len(fought), 1)     # 只有第一关是主动开的
        self.assertEqual(len(settled), 2)    # 自动开打的那关也走了结算处理
        self.assertEqual(task.processed_count, 1)
        self.assertIn("直接开打", ctx.logger.joined())

    def test_clear_one_stage_saves_screenshot_when_handoff_lost(self):
        task = ClearEventStages()
        task.processed_count = 0
        task._wait_stage_info = lambda c, rounds=15: True
        task._ap_ok = lambda c: True
        task._fight_current_stage = lambda c: True
        task._settle_current_stage = lambda c: True
        task._wait_next_stage_info = lambda c, rounds=10: False
        task._back_to_stage_list = lambda c: False

        ctx = self._C()
        with patch("tasks.event.time.sleep"):
            ok = task._clear_one_stage(ctx, 300)

        self.assertFalse(ok)
        self.assertIn("event_next_stage_stuck.png", ctx.saved)
        self.assertIn("没等到下一关", ctx.logger.joined())


    def test_enter_event_stage_select_bails_out_on_frozen_screen(self):
        """连续多轮画面完全没变化：提前放弃并存 event_recover_stuck.png。"""
        import numpy as np

        task = ClearEventStages()
        shots = []

        class _C:
            def __init__(self):
                self.logger = _Logger()
                self._last_screen = np.zeros((720, 1280, 3), np.uint8)
                self.saved = []

            def screenshot(self):
                shots.append(1)
                return self._last_screen

            def save_screenshot(self, img, name=None):
                self.saved.append(name)
                return "screenshots/" + str(name)

            def find(self, *args, **kwargs):
                return None

        ctx = _C()
        task._is_target_stage_list = lambda c: False
        with patch("tasks.event.read_text", return_value=[]), \
                patch("tasks.event.handle_download_popup", return_value=False), \
                patch("tasks.event.handle_network_error", return_value=False), \
                patch("tasks.event.close_content_popup", return_value=False), \
                patch("tasks.event.is_page", return_value=False), \
                patch("tasks.event.time.sleep"):
            ok = task._enter_event_stage_select(ctx)

        self.assertFalse(ok)
        self.assertIn("event_recover_stuck.png", ctx.saved)
        self.assertIn("提前放弃", ctx.logger.joined())
        self.assertLess(len(shots), 40)          # 没跑满 40 轮


class WaitEventKindTest(unittest.TestCase):
    """活动卡点开后的页面判定（2026-09-16：先弹弹窗的活动会被判 unknown 跳过）。"""

    def _task(self, ctx):
        task = ClearEventStages()
        task.events_seen = 2
        task._is_event_list = lambda c: False
        task._is_target_stage_list = lambda c: c.stage_ready
        return task

    def test_wait_event_kind_closes_popup_before_giving_up(self):
        """卡片点开先弹带 X 的弹窗时要关掉再看，不能判成“没认出结构”直接跳过。

        实测 2026-09-16：3 张活动卡里有 2 张被判「没认出这个活动的页面结构，跳过」
        （每张白等约 3 分钟）。旧 `_wait_event_kind` 里既没有 `close_content_popup`
        也没有 `click_ok_by_ocr`，卡片只要先弹一个带关闭键的弹窗
        （詳細 / 特設 / お知らせ 之类），10 轮之后就只能判 unknown 跳过。
        """
        ctx = _WaitKindContext()
        task = self._task(ctx)

        def close_popup(c):
            if c.stage_ready:
                return False
            c.stage_ready = True        # 关掉弹窗后面就是关卡页
            return True

        with patch("tasks.event.read_text", return_value=[]), \
                patch("tasks.event._is_story_screen", return_value=False), \
                patch("tasks.event._is_reward_popup", return_value=False), \
                patch("tasks.event._handle_download", return_value=False), \
                patch("tasks.event.handle_download_popup", return_value=False), \
                patch("tasks.event.handle_network_error", return_value=False), \
                patch("tasks.event.close_content_popup", side_effect=close_popup), \
                patch("tasks.event.click_ok_by_ocr", return_value=False), \
                patch("tasks.event.time.sleep"):
            kind = task._wait_event_kind(ctx)

        self.assertEqual(kind, "stage")

    def test_wait_event_kind_clicks_ok_dialog_before_giving_up(self):
        """卡片点开先弹一个只有 OK 的确认框时，点掉再看（别白等 10 轮判 unknown）。"""
        ctx = _WaitKindContext()
        task = self._task(ctx)

        def click_ok(c, **kwargs):
            if c.stage_ready:
                return False
            c.stage_ready = True
            return True

        with patch("tasks.event.read_text", return_value=[]), \
                patch("tasks.event._is_story_screen", return_value=False), \
                patch("tasks.event._is_reward_popup", return_value=False), \
                patch("tasks.event._handle_download", return_value=False), \
                patch("tasks.event.handle_download_popup", return_value=False), \
                patch("tasks.event.handle_network_error", return_value=False), \
                patch("tasks.event.close_content_popup", return_value=False), \
                patch("tasks.event.click_ok_by_ocr", side_effect=click_ok), \
                patch("tasks.event.time.sleep"):
            kind = task._wait_event_kind(ctx)

        self.assertEqual(kind, "stage")

    def test_wait_event_kind_saves_screenshot_when_unknown(self):
        """真的认不出来时要留现场（截图 + OCR 摘要），别只是静默跳过。"""
        ctx = _WaitKindContext()
        task = self._task(ctx)

        with patch("tasks.event.read_text",
                   return_value=[("？？？", 640, 360, 0.6)]), \
                patch("tasks.event._is_story_screen", return_value=False), \
                patch("tasks.event._is_reward_popup", return_value=False), \
                patch("tasks.event._handle_download", return_value=False), \
                patch("tasks.event.handle_download_popup", return_value=False), \
                patch("tasks.event.handle_network_error", return_value=False), \
                patch("tasks.event.close_content_popup", return_value=False), \
                patch("tasks.event.click_ok_by_ocr", return_value=False), \
                patch("tasks.event.time.sleep"):
            kind = task._wait_event_kind(ctx, rounds=2)

        self.assertEqual(kind, "unknown")
        self.assertTrue(ctx.saved)
        self.assertIn("OCR", ctx.logger.joined())


class ClearEventStagesOrderTest(unittest.TestCase):
    @patch("tasks.event.ensure_home", return_value=True)
    def test_story_runs_before_battle_and_normal_before_hard(self, _ensure_home):
        """带标签的活动：先清剧情（跳过バトル标签），再按 NORMAL → HARD 清战斗。"""
        task = _EventProbe()

        result = task.on_run(_Context())

        self.assertTrue(result)
        self.assertEqual(task.story_tabs, ["ストーリー"])
        self.assertEqual(task.modes_seen, ["NORMAL", "HARD"])
        self.assertEqual(task.reward_calls, 1)

    @patch("tasks.event.ensure_home", return_value=True)
    def test_event_without_tabs_clears_battle_once(self, _ensure_home):
        """迷你/踏破活动只有一个关卡列表：没有标签、没有难度，清一遍就行。"""
        task = _EventProbe(tabs=(), modes=(None,))

        result = task.on_run(_Context())

        self.assertTrue(result)
        self.assertEqual(task.story_tabs, [])
        self.assertEqual(task.modes_seen, [])
        self.assertEqual(task.battle_calls, 1)

    @patch("tasks.event.ensure_home", return_value=True)
    def test_prestory_event_is_skipped_as_story(self, _ensure_home):
        """纯剧情活动（プレストーリー）：点开就是剧情，不当作关卡页处理。"""
        task = _EventProbe(kind="story")

        result = task.on_run(_Context())

        self.assertTrue(result)
        self.assertEqual(task.pre_story_calls, 1)
        self.assertEqual(task.battle_calls, 0)
        self.assertEqual(task.story_tabs, [])

    @patch("tasks.event.ensure_home", return_value=True)
    def test_unknown_page_is_skipped_without_error(self, _ensure_home):
        task = _EventProbe(kind="unknown")

        result = task.on_run(_Context())

        self.assertTrue(result)
        self.assertEqual(task.battle_calls, 0)
        self.assertEqual(task.pre_story_calls, 0)

    @patch("tasks.event.ensure_home", return_value=True)
    def test_battle_failure_is_reported(self, _ensure_home):
        task = _EventProbe(battle_ok=False)

        result = task.on_run(_Context())

        self.assertFalse(result)
        self.assertEqual(task.status, task.STATUS_ERROR)
        self.assertTrue(task.failed)
        self.assertIn("战斗没打过去", task.error_message)

    @patch("tasks.event.ensure_home", return_value=True)
    def test_battle_blocked_is_not_reported_as_done(self, _ensure_home):
        """关卡进不去（未解锁）时应上报“未完全清空”，而不是假装“完成”。"""
        task = _EventProbe(battle_blocked=True)

        result = task.on_run(_Context())

        self.assertFalse(result)
        self.assertEqual(task.status, task.STATUS_ERROR)
        self.assertIsNotNone(task.stopped_at_mode)
        self.assertIn("未能完全清空", task.error_message)

    @patch("tasks.event.ensure_home", return_value=True)
    def test_ap_blocked_is_reported_as_incomplete(self, _ensure_home):
        """AP 不够停手时同样要如实上报，不能报“完成”。"""
        task = _EventProbe(ap_blocked=True)

        result = task.on_run(_Context())

        self.assertFalse(result)
        self.assertIn("未能完全清空", task.error_message)
        self.assertIn("AP", task.error_message)

    @patch("tasks.event.ensure_home", return_value=True)
    def test_battle_task_does_not_run_story(self, _ensure_home):
        """活动战斗任务只清战斗，不跑剧情。"""
        task = _BattleProbe()

        result = task.on_run(_Context())

        self.assertTrue(result)
        self.assertEqual(task.name, "活动战斗")
        self.assertEqual(task.story_tabs, [])
        self.assertTrue(task.modes_seen)

    @patch("tasks.event.ensure_home", return_value=True)
    def test_event_returns_to_stage_list_before_battle(self, _ensure_home):
        """剧情做完、进战斗前要先确认回到关卡选择页（2026-09-18 实测的坑）。"""
        task = _EventProbe()

        result = task.on_run(_Context())

        self.assertTrue(result)
        self.assertGreaterEqual(task.recover_calls, 1)

    @patch("tasks.event.ensure_home", return_value=True)
    def test_story_task_does_not_run_battle(self, _ensure_home):
        """活动剧情任务只清剧情，不跑战斗。"""
        task = _StoryProbe()

        result = task.on_run(_Context())

        self.assertTrue(result)
        self.assertEqual(task.name, "活动剧情")
        self.assertEqual(task.battle_calls, 0)
        self.assertEqual(task.story_tabs, ["ストーリー"])

    def test_event_card_cells_from_footer_rows(self):
        """活动卡靠「下方信息行」切行：每行两张（左 495 / 右 995）。"""
        tokens = [
            ("期間限定", 61, 105, 0.99),
            ("終了11日", 411, 480, 0.63),        # 第 1 行信息行
            ("細", 1189, 478, 0.63),
            ("炊事当番", 500, 900, 0.7),          # 第 2 行（超出可视区）
        ]
        with patch("tasks.event.read_text", return_value=tokens):
            cells = event_card_cells(object())

        self.assertEqual(cells, [(495, 348), (995, 348)])

    def test_event_card_cells_ignores_card_titles(self):
        """卡片标题（美术字）不该被当成信息行，否则会点到卡片外面。"""
        tokens = [
            ("風紀の道を一直線", 600, 275, 0.68),
            ("はじめてのアサルトリリイ", 999, 360, 0.82),
        ]
        with patch("tasks.event.read_text", return_value=tokens):
            self.assertEqual(event_card_cells(object()), [])

    def test_process_event_cards_recomputes_position_before_each_click(self):
        """每次进活动前按当前画面重算坐标。

        实测 2026-09-12：从活动退回一览时游戏会把列表滚回顶部，按“一屏同时点两张卡”
        的老写法，第二张卡用的是上一屏的坐标 → 点到空处（“这张卡点不开”）。
        """
        from unittest.mock import patch

        task = ClearEventStages()
        opened = []
        task._open_and_handle_card = lambda ctx, cell: opened.append(cell)
        task._scroll_list_top = lambda ctx: None

        top = [(495, 348), (995, 348)]
        row2 = [(495, 171), (995, 171)]
        # 每进一次活动，列表就回到顶部；滚一屏才看得到第二行
        screens = [top, top, top, row2, top, row2, top, row2]
        scrolls = [True, True, True, False]

        class _C:
            def __init__(self):
                self.logger = _Logger()
                self._last_screen = object()

            def screenshot(self):
                return self._last_screen

            def save_screenshot(self, img, name=None):
                return f"screenshots/{name}"

        ctx = _C()
        with patch("tasks.event.event_card_cells",
                   side_effect=lambda img: screens.pop(0) if screens else []), \
                patch("tasks.event.card_fingerprint",
                      side_effect=lambda img, cell: cell), \
                patch("tasks.event.same_card", side_effect=lambda a, b: a == b), \
                patch.object(task, "_scroll_list_down",
                             side_effect=lambda c, step: scrolls.pop(0)
                             if scrolls else False):
            task._process_event_cards(ctx)

        self.assertEqual(opened, [(495, 348), (995, 348),
                                  (495, 171), (995, 171)])

    def test_process_event_cards_rescans_when_no_cards_visible(self):
        """列表滚到“看不到任何信息行”的位置时，要先滚回顶部重扫，不能直接收工。

        实测 2026-09-18：从活动退回一览后列表位置不巧落在两张卡之间，
        `event_card_cells` 返回空 → 旧逻辑立刻判定「没有更多卡片了」→ 只处理了 1 张卡
        （实际列表里有 4 张，日志里是 `遍历活动 1 个`）。
        """
        task = ClearEventStages()
        opened = []
        task._open_and_handle_card = lambda ctx, cell: opened.append(cell)
        task._scroll_list_top = lambda ctx: None

        # 第一屏空（卡在两张卡之间）→ 重扫后拿到顶部那一行的两张卡
        screens = [[], [], [(495, 348)], [(995, 348)]]
        scrolls = [True, False]
        task._back_to_event_list = lambda ctx: False   # 这次不需要“退回一览”那条兜底

        class _C:
            def __init__(self):
                self.logger = _Logger()
                self._last_screen = object()

            def screenshot(self):
                return self._last_screen

            def save_screenshot(self, img, name=None):
                return f"screenshots/{name}"

        ctx = _C()
        with patch("tasks.event.event_card_cells",
                   side_effect=lambda img: screens.pop(0) if screens else []), \
                patch("tasks.event.card_fingerprint",
                      side_effect=lambda img, cell: cell), \
                patch("tasks.event.same_card", side_effect=lambda a, b: a == b), \
                patch("tasks.event.handle_network_error", return_value=False), \
                patch.object(task, "_scroll_list_down",
                             side_effect=lambda c, step: scrolls.pop(0)
                             if scrolls else False):
            task._process_event_cards(ctx)

        self.assertEqual(opened, [(495, 348), (995, 348)])

    def test_process_event_cards_goes_back_to_list_when_still_no_cards(self):
        """重扫还是看不到卡片时，先退回活动一览再重扫（可能上一步没退回列表）。

        实测 2026-09-18 13:51~13:56：处理完活动 1 后 `_back_to_event_list` 没把画面
        带回一览，`event_card_cells` 当然找不到卡片 → 旧逻辑直接收工（只遍历 1 个活动）。
        """
        task = ClearEventStages()
        opened = []
        task._open_and_handle_card = lambda ctx, cell: opened.append(cell)
        task._scroll_list_top = lambda ctx: None
        task._back_to_event_list = lambda ctx: True

        screens = [[], [], [], [(495, 348)]]
        scrolls = [False, True]

        class _C:
            def __init__(self):
                self.logger = _Logger()
                self._last_screen = object()

            def screenshot(self):
                return self._last_screen

            def save_screenshot(self, img, name=None):
                return f"screenshots/{name}"

        ctx = _C()
        with patch("tasks.event.event_card_cells",
                   side_effect=lambda img: screens.pop(0) if screens else []), \
                patch("tasks.event.card_fingerprint",
                      side_effect=lambda img, cell: cell), \
                patch("tasks.event.same_card", side_effect=lambda a, b: a == b), \
                patch("tasks.event.handle_network_error", return_value=False), \
                patch.object(task, "_scroll_list_down",
                             side_effect=lambda c, step: scrolls.pop(0)
                             if scrolls else False):
            task._process_event_cards(ctx)

        self.assertEqual(opened, [(495, 348)])
        self.assertIn("退回活动一览", ctx.logger.joined())

    def test_process_event_cards_handles_network_error_before_giving_up(self):
        """看不到卡片时先看是不是网络错误弹窗（点 OK 重连），再重扫。

        实测 2026-09-19 13:04：整页变成「ネットワークエラーが発生しました。
        再度お試しください。」，脚本看不到卡片就直接放弃遍历
        （截图 `screenshots/event_list_empty.png` 就是它，乙女のピンチ 因此没被打到）。
        """
        task = ClearEventStages()
        opened = []
        task._open_and_handle_card = lambda ctx, cell: opened.append(cell)
        task._scroll_list_top = lambda ctx: None
        task._back_to_event_list = lambda ctx: False
        state = {"first": True}

        def fake_network(ctx):
            if state["first"]:
                state["first"] = False
                return True          # 第一次：网络错误弹窗，点 OK 重连
            return False

        class _C:
            def __init__(self):
                self.logger = _Logger()
                self._last_screen = object()

            def screenshot(self):
                return self._last_screen

            def save_screenshot(self, img, name=None):
                return f"screenshots/{name}"

        ctx = _C()
        # 网络错误页上怎么看都没有卡片；点掉 OK 之后才看得到（这就是这条兜底的意义）
        with patch("tasks.event.event_card_cells",
                   side_effect=lambda img: [] if state["first"] else [(495, 348)]), \
                patch("tasks.event.card_fingerprint",
                      side_effect=lambda img, cell: cell), \
                patch("tasks.event.same_card", side_effect=lambda a, b: a == b), \
                patch("tasks.event.handle_network_error", side_effect=fake_network), \
                patch("tasks.event.time.sleep"), \
                patch.object(task, "_scroll_list_down",
                             side_effect=lambda c, step: False):
            task._process_event_cards(ctx)

        self.assertEqual(opened, [(495, 348)])

    def test_daily_bulk_skip_without_button_returns_false(self):
        """デイリー 里没有「一括スキップ」（今天没得跳）时不要卡住。"""
        import numpy as np

        class _C:
            def __init__(self):
                self.logger = _Logger()
                self._last_screen = np.zeros((720, 1280, 3), np.uint8)
                self.clicks = []

            def screenshot(self):
                return self._last_screen

            def click(self, x, y, sleeptime=1.0):
                self.clicks.append((x, y))

        with patch("tasks.event.read_text", return_value=[]), \
                patch("tasks.event.handle_download_popup", return_value=False), \
                patch("tasks.event.close_content_popup", return_value=False):
            self.assertFalse(DailyEventSkip()._bulk_skip(_C()))

    @patch("tasks.event.ensure_home", return_value=True)
    def test_clear_one_stage_marks_enter_failed_when_info_missing(self, _ensure_home):
        """点关卡行但等不到ステージ情報：enter_failed 应置位并返回 False。"""
        from unittest.mock import patch

        task = _EventProbe()
        ctx = _Context()
        ctx.click = lambda *a, **k: None

        with patch("tasks.event._ClearEventBase._wait_stage_info",
                   return_value=False), \
                patch("tasks.event._ClearEventBase._is_target_stage_list",
                      return_value=False):
            result = task._clear_one_stage(ctx, 300)

        self.assertFalse(result)
        self.assertTrue(task.enter_failed)

    def test_available_stage_rows_only_red_dot_cards(self):
        """只返回「当前可打」的那一关：右上角带粉红红点的卡。"""
        from unittest.mock import patch

        task = _EventProbe()

        class _C:
            def __init__(self):
                self._last_screen = object()

        ctx = _C()
        with patch("tasks.event.ClearEventStages._card_red_dots",
                   return_value=[(1240, 184)]), \
                patch("tasks.event.ClearEventStages._event_stage_rows",
                      return_value=[219, 378, 536, 694]):
            # 红点 y=184 落在第一关(219) ±90 内，其余锁定关被过滤掉
            rows = task._available_stage_rows(ctx)

        self.assertEqual(rows, [219])

    def test_available_stage_rows_falls_back_to_unlocked_cards(self):
        """画面里一个粉红点都没有时，退回“没被灰锁”的卡（迷你/踏破活动没有红点）。

        实测 2026-09-12：はんこ 導入記念、サバイバルアタック 的当前可打关卡
        右上角没有粉红点，锁定关是灰卡。旧逻辑返回空 → 整段战斗被跳过。
        """
        from unittest.mock import patch

        import numpy as np

        task = _EventProbe()

        class _C:
            def __init__(self, img):
                self._last_screen = img

        # 造一张 720x1280 的图：137 行（第一关）卡面很亮，其余关卡区域是灰锁
        img = np.full((720, 1280, 3), 30, np.uint8)
        img[137 - 70:137 + 70, 600:1140] = 235
        ctx = _C(img)

        with patch("tasks.event.ClearEventStages._card_red_dots",
                   return_value=[]), \
                patch("tasks.event.ClearEventStages._event_stage_rows",
                      return_value=[137, 296, 453, 613]):
            rows = task._available_stage_rows(ctx)

        self.assertEqual(rows, [137])

    def test_available_stage_rows_keeps_red_dot_priority(self):
        """有粉红点时不启用亮度兜底：仍只返回带红点的那一关（保持保守行为）。"""
        from unittest.mock import patch

        import numpy as np

        task = _EventProbe()

        class _C:
            def __init__(self, img):
                self._last_screen = img

        # 整屏都很亮（亮度兜底会认为 4 关全可打），但有红点时必须只返回红点那关
        img = np.full((720, 1280, 3), 235, np.uint8)
        ctx = _C(img)

        with patch("tasks.event.ClearEventStages._card_red_dots",
                   return_value=[(1240, 184)]), \
                patch("tasks.event.ClearEventStages._event_stage_rows",
                      return_value=[219, 378, 536, 694]):
            rows = task._available_stage_rows(ctx)

        self.assertEqual(rows, [219])

    def test_stage_card_unlocked_separates_bright_from_locked(self):
        """灰锁卡判定阈值：可打卡很亮(V≥170)，锁定灰卡明显偏暗。"""
        import numpy as np

        from tasks.event import ClearEventStages

        img = np.full((720, 1280, 3), 0, np.uint8)
        img[150:290, :] = (210, 209, 209)     # 亮卡（V≈210）
        img[300:440, :] = (120, 122, 128)     # 灰锁卡（V≈128）

        self.assertTrue(ClearEventStages._stage_card_unlocked(img, 220))
        self.assertFalse(ClearEventStages._stage_card_unlocked(img, 370))

    def test_available_stage_rows_skips_cards_at_screen_bottom(self):
        """贴着屏幕下沿的关不用亮度兜底：看不到右上角的 COMPLETE 章。

        实测 2026-09-12 残光：NORMAL 的ステージ05 已通关(COMPLETE)，但那一行
        正好卡在屏幕下沿、章在画面外，亮度兜底把它当成可打 → 又打一遍白花 AP。
        """
        from unittest.mock import patch

        import numpy as np

        task = _EventProbe()

        class _C:
            def __init__(self, img):
                self._last_screen = img

        img = np.full((720, 1280, 3), 30, np.uint8)
        img[137 - 70:137 + 70, 600:1140] = 235     # 可打的关（完整可见）
        img[694 - 70:720, 600:1140] = 235          # 下沿的关（卡被裁掉）
        ctx = _C(img)

        with patch("tasks.event.ClearEventStages._card_red_dots",
                   return_value=[]), \
                patch("tasks.event.ClearEventStages._event_stage_rows",
                      return_value=[137, 694]):
            rows = task._available_stage_rows(ctx)

        self.assertEqual(rows, [137])

    def test_detect_tabs_empty_for_plain_stage_list(self):
        """没有标签栏的纯关卡列表要返回“没有标签”，别假装有「バトル」标签。"""
        from unittest.mock import patch

        class _C:
            def __init__(self):
                self._last_screen = object()

        tokens = [("ステージ01", 654, 137, 0.9), ("AP|10", 618, 203, 0.9)]
        with patch("tasks.event.ClearEventStages._tab_positions",
                   return_value={}), \
                patch("tasks.event.read_text", return_value=tokens):
            self.assertEqual(ClearEventStages._detect_tabs(_C()), [])

    def test_goto_tab_never_clicks_hardcoded_position(self):
        """没有标签栏时不能按写死坐标点（会点到关卡卡上、把流程带偏）。"""
        from unittest.mock import patch

        class _C:
            def __init__(self):
                self.logger = _Logger()
                self._last_screen = object()
                self.clicks = []

            def screenshot(self):
                return self._last_screen

            def click(self, x, y, sleeptime=1.0):
                self.clicks.append((x, y))

        task = ClearEventStages()      # 不要用 _EventProbe：它把 _goto_tab 桩掉了
        ctx = _C()
        with patch("tasks.event._ClearEventBase._tab_positions",
                   return_value={}), \
                patch("tasks.event._ClearEventBase._detect_tabs",
                      return_value=[]), \
                patch.object(task, "_recover_to_stage_list",
                             return_value=False):
            result = task._goto_tab(ctx, "バトル")

        self.assertFalse(result)
        self.assertEqual(ctx.clicks, [])

    def test_goto_tab_clicks_detected_position(self):
        """有标签栏时按识别到的位置点（各活动标签数量/位置不同）。"""
        from unittest.mock import patch

        class _C:
            def __init__(self):
                self.logger = _Logger()
                self._last_screen = object()
                self.clicks = []

            def screenshot(self):
                return self._last_screen

            def click(self, x, y, sleeptime=1.0):
                self.clicks.append((x, y))

        task = ClearEventStages()
        ctx = _C()
        with patch("tasks.event._ClearEventBase._tab_positions",
                   return_value={"ストーリー": (876, 122), "バトル": (1113, 122)}), \
                patch("tasks.event.time.sleep"):
            result = task._goto_tab(ctx, "バトル")

        self.assertTrue(result)
        self.assertEqual(ctx.clicks, [(1113, 122)])

    def test_is_stage_label_recognizes_ex_stages(self):
        """关卡行识别要支持 EX 前缀（EX1~EX5），否则 EX 关会被漏掉。"""
        from tasks.event import ClearEventStages

        for label in ("1", "06", "0", "10", "EX1", "ex1", "Ex1", "EX 1", "EX",
                      "ステージEX1", "ステージ1", "ステージ 01", "ステージ EX1"):
            self.assertTrue(ClearEventStages._is_stage_label(label), label)
        for label in ("AP", "COMPLETE", "EXP", "バトル", "EX 1た", "ステージ"):
            self.assertFalse(ClearEventStages._is_stage_label(label), label)

    def test_is_stage_label_accepts_truncated_stage_prefix(self):
        """OCR 把「ステージ01」读成只剩尾巴的「ジ01」时也要认出来。

        实测 2026-09-12：はんこ 導入記念 的关卡行 OCR 结果是「ジ01」(654,137)，
        旧正则不匹配 → `_event_stage_rows` 返回空 → 该活动被当成“没有关卡”跳过。
        """
        from tasks.event import ClearEventStages

        for label in ("ジ01", "ジ 01", "ジ1", "テージ02", "ステージ03"):
            self.assertTrue(ClearEventStages._is_stage_label(label), label)
        for label in ("ジ", "テージ", "AP|10", "×10", "ジAP"):
            self.assertFalse(ClearEventStages._is_stage_label(label), label)

    def test_event_stage_rows_returns_ex_rows_after_complete(self):
        """HARD 清完编号关后，EX 关应被识别为关卡行；已通关(COMPLETE)的编号关跳过。"""
        from unittest.mock import patch

        class _C:
            def __init__(self):
                self._last_screen = object()

        ctx = _C()
        tokens = [
            ("COMPLETE", 1170, 219, 0.9),
            ("COMPLETE", 1170, 378, 0.9),
            ("01", 653, 219, 0.8),
            ("02", 653, 378, 0.8),
            ("EX1", 653, 536, 0.8),
            ("EX2", 653, 694, 0.8),
        ]
        with patch("tasks.event.read_text", return_value=tokens):
            rows = _EventProbe._event_stage_rows(ctx)

        # 01/02 因右上角 COMPLETE 被跳过，只剩 EX1/EX2
        self.assertEqual(rows, [536, 694])

    def test_pink_dots_detect_magenta_round_dot(self):
        """_pink_dots 能识别粉红圆点，且筛掉非圆点色块。"""
        from tasks.event import _pink_dots
        import numpy as np
        import cv2

        img = np.zeros((100, 100, 3), np.uint8)
        # 画一个亮粉(HSV 170,175,255)圆点
        hsv_center = (170, 175, 255)
        hsv = np.zeros((100, 100, 3), np.uint8)
        cv2.circle(hsv, (50, 50), 8, hsv_center, -1)
        bgr = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)

        dots = _pink_dots(bgr)
        self.assertTrue(any(abs(cx - 50) < 6 and abs(cy - 50) < 6
                            for cx, cy, _a in dots))

    def test_wait_stage_info_handles_download_first(self):
        """点关卡后先处理「再生/下载」弹窗，再等到ステージ情報标记。"""
        from unittest.mock import patch

        task = _EventProbe()
        ctx = _StageInfoContext()

        def fake_download(c):
            if not c.downloaded:
                c.downloaded = True
                return True
            return False

        def fake_read_text(img):
            # 未下载时是下载弹窗文案；下载完成后出现关卡信息标记
            return ([("再生", 0, 0, 0), ("MB", 0, 0, 0)]
                    if not ctx.downloaded else [("BOSS", 0, 0, 0)])

        with patch("tasks.event.handle_network_error", return_value=False), \
                patch("tasks.event._handle_download", side_effect=fake_download), \
                patch("tasks.event.read_text", side_effect=fake_read_text), \
                patch("tasks.event.time.sleep"):
            result = task._wait_stage_info(ctx, rounds=10)

        self.assertTrue(result)
        self.assertTrue(ctx.downloaded)

    def test_visible_story_cards_skips_complete(self):
        """已看完（右上角 COMPLETE）的剧情卡跳过，只返回未看完的卡。"""
        from unittest.mock import patch

        class _C:
            def __init__(self):
                self._last_screen = object()

        ctx = _C()

        def fake_read_text(img):
            return [
                ("COMPLETE", 806, 220, 0.9),
                ("COMPLETE", 1170, 218, 0.9),
                ("2話:まどろみの世界-1", 697, 250, 0.7),
                ("2話:まどろみの世界-3", 697, 377, 0.7),
                ("3話:希望を喰らしいモノ-1", 1061, 377, 0.7),
            ]

        with patch("tasks.event.read_text", side_effect=fake_read_text):
            cards = _EventProbe._visible_story_cards(ctx)

        self.assertEqual(cards, [(697, 377), (1061, 377)])

    def test_available_story_cards_only_pink_dot_card(self):
        """只返回“当前可读”（右上角带粉红点）的剧情卡，不点已读完(COMPLETE)或未解锁卡。"""
        from unittest.mock import patch

        class _C:
            def __init__(self):
                self._last_screen = object()

        ctx = _C()
        # 复现用户截图：4話-1 已读(COMPLETE)，4話-2 当前可读(右上角粉红点)，5話 未解锁
        tokens = [
            ("COMPLETE", 806, 233, 0.98),
            ("4話:夢幻泡影-1", 696, 390, 0.92),
            ("4話:夢幻泡影-2", 1061, 390, 0.92),
            ("5話:光射す場所へ-1", 695, 624, 0.89),
            ("5話:光射す場所へ-2", 1061, 624, 0.89),
        ]
        with patch("tasks.event.read_text", return_value=tokens), \
                patch("tasks.event._pink_dots", return_value=[(1227, 200, 181)]):
            avail = _EventProbe._available_story_cards(ctx)

        self.assertEqual(avail, [(1061, 390)])

    def test_tab_red_dot_assigns_to_nearest_tab(self):
        """标签红点归属最近的标签，避免相邻标签范围重叠串味。"""
        from unittest.mock import patch
        from tasks.event import TABS

        import numpy as np

        class _C:
            def __init__(self):
                self._last_screen = np.zeros((720, 1280, 3), np.uint8)
            def screenshot(self):
                return self._last_screen

        ctx = _C()

        def fake_dots(img, min_area=5):
            return [(710, 109, 17), (1188, 109, 12)]

        with patch("tasks.event.find_red_dots", side_effect=fake_dots), \
                patch("tasks.event._pink_dots", return_value=[]), \
                patch("tasks.event.read_text", return_value=[]):
            self.assertTrue(_EventProbe._tab_has_red_dot(ctx, "ストーリー"))
            self.assertFalse(_EventProbe._tab_has_red_dot(ctx, "メモリアストーリー"))
            self.assertTrue(_EventProbe._tab_has_red_dot(ctx, "バトル"))

    def test_tab_red_dot_uses_ocr_tab_positions(self):
        """各活动标签位置不同：红点按 OCR 找到的标签位置归属，不能再用旧坐标。"""
        from unittest.mock import patch

        class _C:
            def __init__(self):
                self._last_screen = object()

            def screenshot(self):
                return None

        ctx = _C()
        # 風紀活动：ストーリー@642、バトル@874（没有 メモリアストーリー）
        tokens = [("ストーリー", 642, 122, 0.9), ("バトル", 874, 122, 0.9)]

        def fake_dots(img, min_area=5):
            return [(870, 100, 20)]

        with patch("tasks.event.find_red_dots", side_effect=fake_dots), \
                patch("tasks.event._pink_dots", return_value=[]), \
                patch("tasks.event.read_text", return_value=tokens):
            self.assertTrue(_EventProbe._tab_has_red_dot(ctx, "バトル"))
            self.assertFalse(_EventProbe._tab_has_red_dot(ctx, "ストーリー"))

    def test_wait_event_kind_recognizes_story_download_dialog(self):
        """プレストーリー 型活动点开卡片立刻弹「ストーリー再生確認」，要判成剧情。

        实测 2026-09-12 山内 昇依編 / ふじの食堂：弹窗标题被 OCR 读花成
        「卜一一再生確」，旧关键词（ストーリー再生 / 再生確認）全部落空 →
        判成 unknown → 整个活动被跳过，剧情和奖励都拿不到。
        """
        from unittest.mock import patch

        class _C:
            def __init__(self):
                self.logger = _Logger()
                self._last_screen = object()
                self.clicks = []

            def screenshot(self):
                return self._last_screen

            def click(self, x, y, sleeptime=1.0):
                self.clicks.append((x, y))

        # 真实弹窗的 OCR 结果（截图 2026-09-12 ふじの食堂 卡片点开后）
        tokens = [
            ("卜一一再生確", 160, 51, 0.89),
            ("再必要。", 634, 254, 0.75),
            ("「」場合、開始。", 640, 276, 0.79),
            ("（Wi-Fi接統推）", 639, 297, 0.89),
            ("廿：6.3MB", 640, 335, 0.92),
            ("自動削除定变更行。", 635, 373, 0.84),
            ("十儿", 522, 650, 0.66),
            ("OK", 758, 650, 0.97),
        ]
        ctx = _C()
        # 用真实类：这段判定本来就没有被测试桩替换，直接用真实截图文本喂进去
        task = ClearEventStages()

        with patch("tasks.event.read_text", return_value=tokens), \
                patch("tasks.story.read_text", return_value=tokens), \
                patch("tasks.event.handle_download_popup", return_value=False), \
                patch("tasks.event.handle_network_error", return_value=False), \
                patch("tasks.event.time.sleep"):
            kind = task._wait_event_kind(ctx, rounds=2)

        self.assertEqual(kind, "story")
        self.assertEqual(ctx.clicks, [(758, 650)])   # 点 OK 开始下载剧情数据

    def test_process_event_cards_handles_second_row_on_last_screen(self):
        """滚到底那一屏还有第二行卡时不能直接收工。

        实测 2026-09-12：活動一覧共 6 张卡（3 行 × 2 列）。滚到底后同一屏上
        还留着第 3 行，旧逻辑只处理“本屏最上面那一行”就去滚屏，滚不动→判“到底了”，
        第 3 行的两张卡（山内 昇依編 / ふじ 食堂）永远轮不到。
        """
        from unittest.mock import patch

        task = ClearEventStages()
        opened = []
        task._open_and_handle_card = lambda ctx, cell: opened.append(cell)
        task._scroll_list_top = lambda ctx: None

        top = [(495, 348), (995, 348)]
        bottom = [(495, 148), (995, 148), (495, 505), (995, 505)]
        screens = [top, top, top, bottom, bottom, bottom, bottom, bottom]
        scrolls = [True, False]        # 滚一屏到底后，再滚就不动了

        class _C:
            def __init__(self):
                self.logger = _Logger()
                self._last_screen = object()

            def screenshot(self):
                return self._last_screen

        ctx = _C()
        with patch("tasks.event.event_card_cells",
                   side_effect=lambda img: screens.pop(0) if screens else []), \
                patch("tasks.event.card_fingerprint",
                      side_effect=lambda img, cell: cell), \
                patch("tasks.event.same_card", side_effect=lambda a, b: a == b), \
                patch.object(task, "_scroll_list_down",
                             side_effect=lambda c, step: scrolls.pop(0)
                             if scrolls else False):
            task._process_event_cards(ctx)

        self.assertEqual(opened, [(495, 348), (995, 348),
                                  (495, 148), (995, 148),
                                  (495, 505), (995, 505)])

    def test_scroll_list_down_ignores_settle_bounce(self):
        """列表到底时会回弹一点点（实测像素差≈9.7），不能算“滚动成功”。

        真滚一屏实测像素差≈52；旧阈值 5 会把回弹当成功，于是同一屏被当成新一屏。
        """
        from unittest.mock import patch

        import numpy as np

        base = np.full((720, 1280, 3), 235, np.uint8)
        for y in range(150, 700, 40):            # 造几条“文字行”
            base[y:y + 12, 360:1180] = 60
        bounce = np.roll(base, 4, axis=0)        # 到底回弹：只挪 4px
        moved = np.roll(base, 300, axis=0)       # 真滚一屏

        class _Dev:
            def swipe(self, *a, **k):
                pass

        class _C:
            def __init__(self, shots):
                self._shots = shots
                self._last_screen = None
                self.device = _Dev()

            def screenshot(self):
                self._last_screen = self._shots.pop(0)
                return self._last_screen

        task = ClearEventStages()
        with patch("tasks.event.time.sleep"):
            ctx = _C([base, bounce])
            ctx.screenshot()
            self.assertFalse(task._scroll_list_down(ctx, 300))
            ctx = _C([base, moved])
            ctx.screenshot()
            self.assertTrue(task._scroll_list_down(ctx, 300))

    def test_card_fingerprint_tolerates_anchor_jitter(self):
        """同一张卡在不同滚动位置（锚点错位十几像素）必须判成同一张卡。

        实测 2026-09-12：只用 ±150x±55 的 aHash 时，同一张卡错位 12px 就会
        产生 23~39 位差异（阈值 20）→ 判成新卡重复点；而相邻两张卡只差 17 位
        → 判成已进过而漏掉。真机卡面的实测值：错位 0~24px 时同卡
        网格色 ≤416 / aHash ≤37，不同卡 ≥760 / ≥45。
        这里用「渐变底 + 细文字条」模拟卡面美术（比真机更硬一点）。
        """
        import numpy as np

        from tasks.event import card_fingerprint, same_card

        img = np.full((720, 1280, 3), 235, np.uint8)
        for x0, color in ((345, (120, 90, 60)), (845, (60, 90, 130))):
            card = np.zeros((210, 300, 3), np.uint8)
            for row in range(210):                      # 竖向渐变，模拟美术底图
                card[row, :] = (np.array(color) * (0.6 + 0.8 * row / 209)).clip(0, 255)
            for line in (25, 60, 95, 150, 180):         # 细文字条
                card[line:line + 6, 20:280] = 235
            img[120:330, x0:x0 + 300] = card

        left = card_fingerprint(img, (495, 250))
        right = card_fingerprint(img, (995, 250))
        self.assertFalse(same_card(left, right))

        # 锚点抖动：卡片没动，但信息行 OCR 出来的 y 偏了 12px
        jittered = card_fingerprint(img, (495, 262))
        self.assertTrue(same_card(left, jittered))

    def test_clear_story_stage_row_clicks_unnumbered_row(self):
        """プレストーリー 型活动：关卡页只有一行“没有编号的剧情关”，要能点开跳剧情。

        实测 2026-09-12 山内 昇依編 / ふじ 食堂：点开活动卡 → 下载确认 →
        关卡页只有一行「プレストーリー山内昇依編」（没有ステージ编号、也没有红点），
        `_event_stage_rows` 认不出来 → 整行被忽略，剧情和首通奖励都拿不到。
        """
        from unittest.mock import patch

        class _C:
            def __init__(self):
                self.logger = _Logger()
                self._last_screen = object()
                self.clicks = []

            def screenshot(self):
                return self._last_screen

            def click(self, x, y, sleeptime=1.0):
                self.clicks.append((x, y))

        # 真实页面 OCR（截图 2026-09-12 山内 昇依編 关卡页）
        tokens = [
            ("山内昇依", 718, 135, 0.79),
            ("MODE:NORMAL", 405, 150, 0.97),
            ("EVENTMISSION.", 113, 206, 0.96),
            ("0/0", 473, 207, 0.97),
            ("API", 598, 203, 0.81),
            ("MISSION", 721, 204, 0.99),
            ("推力", 927, 203, 0.99),
            ("x10", 1205, 204, 0.91),
            ("表示切替", 120, 662, 0.99),
        ]
        ctx = _C()
        task = ClearEventStages()
        played = []
        task._play_story_node = lambda c: (played.append(True), True)[1]

        with patch("tasks.event.read_text", return_value=tokens):
            self.assertTrue(task._clear_story_stage_row(ctx))

        self.assertEqual(ctx.clicks, [(890, 180)])     # 点这一行的卡面
        self.assertEqual(played, [True])

    def test_story_stage_row_ignores_numbered_stage_list(self):
        """有编号关卡（哪怕全 COMPLETE）的活动不能被当成“一行剧情关”去点。"""
        from unittest.mock import patch

        class _C:
            def __init__(self):
                self.logger = _Logger()
                self._last_screen = object()

            def screenshot(self):
                return self._last_screen

        tokens = [("ジ01", 654, 137, 0.80), ("COMPLETE", 1180, 169, 0.99)]
        with patch("tasks.event.read_text", return_value=tokens):
            self.assertIsNone(ClearEventStages()._story_stage_row(_C()))

    def test_story_stage_row_skips_complete_row(self):
        """已经读完（COMPLETE）的剧情关卡行不要重复点开重播。

        实测 2026-09-12：山内 昇依編 读完后那一行右上角会出现 COMPLETE(1180,169)，
        标题在 (718,135)——必须按这个章把该行排除，否则每次都白重播一遍。
        """
        from unittest.mock import patch

        class _C:
            def __init__(self):
                self.logger = _Logger()
                self._last_screen = object()

            def screenshot(self):
                return self._last_screen

        tokens = [
            ("山内昇依", 718, 135, 0.79),
            ("MODE:NORMAL", 405, 150, 0.97),
            ("COMPLETE", 1180, 169, 0.99),
            ("表示切替", 120, 662, 0.99),
        ]
        with patch("tasks.event.read_text", return_value=tokens):
            self.assertIsNone(ClearEventStages()._story_stage_row(_C()))

    @patch("tasks.event.ensure_home", return_value=True)
    def test_event_without_tabs_handles_story_row(self, _ensure_home):
        """没有标签栏的活动，剧情任务要顺手处理“一行剧情关”。"""
        task = _EventProbe(tabs=(), modes=(None,))

        result = task.on_run(_Context())

        self.assertTrue(result)
        self.assertEqual(task.story_row_calls, 1)

    def test_is_story_screen_accepts_movie_layout(self):
        """动画播放页的 SKIP 在右上角(1221,63)，也要认成剧情页。

        实测 2026-09-12 ふじ 食堂：プレストーリー 是一段动画，SKIP 在右上角，
        旧判据只认 x 700~950 → 认不出 → 干等到 40 轮超时、任务卡死。
        """
        from unittest.mock import patch

        from tasks.story import _is_story_screen

        class _C:
            def __init__(self):
                self._last_screen = object()

        tokens = [("SKIP", 1221, 63, 0.98)]
        with patch("tasks.story.read_text", return_value=tokens):
            self.assertTrue(_is_story_screen(_C()))
        # 普通剧情页（858,61）当然也要认
        with patch("tasks.story.read_text",
                   return_value=[("SKIP", 858, 61, 0.98)]):
            self.assertTrue(_is_story_screen(_C()))

    def test_skip_story_clicks_ocr_skip_position(self):
        """SKIP 位置按 OCR 找：普通剧情 (858,61) / 动画 (1221,63) 都要点对。"""
        from unittest.mock import patch

        from tasks.story import _skip_story

        class _C:
            def __init__(self):
                self.logger = _Logger()
                self._last_screen = object()
                self.clicks = []

            def screenshot(self):
                return self._last_screen

            def click(self, x, y, sleeptime=1.0):
                self.clicks.append((x, y))

        ctx = _C()
        with patch("tasks.story.read_text",
                   return_value=[("SKIP", 1221, 63, 0.98)]):
            _skip_story(ctx)

        self.assertEqual(ctx.clicks[0], (1221, 63))

    def test_play_story_node_taps_blank_screen_to_reveal_controls(self):
        """动画控件自动隐藏时全黑：先点一下叫出控件，再点 SKIP 跳过。"""
        from unittest.mock import patch

        import numpy as np

        task = ClearEventStages()
        state = {"i": 0}
        shots = [np.zeros((720, 1280, 3), np.uint8) for _ in range(3)]
        shots[2] = np.full((720, 1280, 3), 235, np.uint8)   # 最后回到关卡列表（不黑）
        toks = {id(shots[0]): [],
                id(shots[1]): [("SKIP", 1221, 63, 0.98), ("OK", 758, 650, 0.97)],
                id(shots[2]): []}

        class _C:
            def __init__(self):
                self.logger = _Logger()
                self._last_screen = None
                self.clicks = []

            def screenshot(self):
                self._last_screen = shots[min(state["i"], 2)]
                state["i"] += 1
                return self._last_screen

            def click(self, x, y, sleeptime=1.0):
                self.clicks.append((x, y))

        ctx = _C()
        task._is_target_stage_list = lambda c: state["i"] >= 3
        with patch("tasks.event.read_text",
                   side_effect=lambda img: toks.get(id(img), [])), \
                patch("tasks.story.read_text",
                      side_effect=lambda img: toks.get(id(img), [])), \
                patch("tasks.event.handle_network_error", return_value=False), \
                patch("tasks.event._handle_download", return_value=False), \
                patch("tasks.event._is_reward_popup", return_value=False), \
                patch("tasks.event.time.sleep"):
            ok = task._play_story_node(ctx)

        self.assertTrue(ok)
        self.assertIn((1203, 61), ctx.clicks)      # 点一下叫出动画控件
        self.assertIn((1221, 63), ctx.clicks)      # 点右上角 SKIP

    def test_play_story_node_taps_when_screen_unrecognized(self):
        """剧情页按钮会自动隐藏（只剩 MENU）：连续认不出画面时也要点一下叫出控件。

        实测 2026-09-12 ふじ 食堂：动画跳过之后进入剧情页，几秒后 AUTO/SKIP/LOG
        这些按钮自动隐藏，只留右上角 MENU → 认不出剧情页 → 干等到超时。
        """
        from unittest.mock import patch

        import numpy as np

        task = ClearEventStages()
        bright = np.full((720, 1280, 3), 235, np.uint8)
        hidden = bright.copy()                              # 控件已隐藏：只读得到 MENU
        shown = bright.copy()                               # 点一下之后：SKIP 出现
        stage = bright.copy()                               # 回到关卡列表
        toks = {id(hidden): [("MENU", 1203, 61, 0.98)],
                id(shown): [("SKIP", 858, 61, 0.98), ("OK", 758, 650, 0.97)],
                id(stage): []}

        class _C:
            def __init__(self):
                self.logger = _Logger()
                self._last_screen = hidden
                self.clicks = []
                self.revealed = False
                self.skipped = False

            def screenshot(self):
                if self.skipped:
                    self._last_screen = stage
                elif self.revealed:
                    self._last_screen = shown
                else:
                    self._last_screen = hidden
                return self._last_screen

            def click(self, x, y, sleeptime=1.0):
                self.clicks.append((x, y))
                if (x, y) == (1203, 61):
                    self.revealed = True                    # 点画面 → 控件出现
                if (x, y) == (858, 61):
                    self.skipped = True                      # 点 SKIP → 回列表

        ctx = _C()
        task._is_target_stage_list = lambda c: c.skipped
        with patch("tasks.event.read_text",
                   side_effect=lambda img: toks.get(id(img), [])), \
                patch("tasks.story.read_text",
                      side_effect=lambda img: toks.get(id(img), [])), \
                patch("tasks.event.handle_network_error", return_value=False), \
                patch("tasks.event._handle_download", return_value=False), \
                patch("tasks.event._is_reward_popup", return_value=False), \
                patch("tasks.event.time.sleep"):
            ok = task._play_story_node(ctx)

        self.assertTrue(ok)
        self.assertIn((1203, 61), ctx.clicks)      # 认不出画面 → 点一下叫出控件
        self.assertIn((858, 61), ctx.clicks)       # 叫出来后点 SKIP


    def test_play_story_node_bails_out_when_screen_stays_unrecognized(self):
        """认不出画面又点不出来时要尽快收工，别死转 40 轮。

        实测 2026-10-02：剧情跳过后遇到通信失败，画面变成脚本不认识的页面，
        `_play_story_node` 就「点一下叫出按钮 → 还是认不出 → 再点」循环了
        18 轮 / 10 分 20 秒（12:51 那次又转了 8 分钟），而且什么都不留。
        改成：连续认不出到上限 → 存图 + 报错 → 交给上层退回列表。
        """
        from unittest.mock import patch

        import numpy as np

        task = ClearEventStages()
        weird = np.full((720, 1280, 3), 200, np.uint8)

        class _C:
            def __init__(self):
                self.logger = _Logger()
                self._last_screen = weird
                self.clicks = []
                self.saved = []

            def screenshot(self):
                return self._last_screen

            def click(self, x, y, sleeptime=1.0):
                self.clicks.append((x, y))

            def save_screenshot(self, img, name=None):
                self.saved.append(name)
                return f"screenshots/{name}"

            def find(self, *a, **k):
                return None

        ctx = _C()
        task._is_target_stage_list = lambda c: False
        with patch("tasks.event.read_text",
                   return_value=[("MENU", 1203, 61, 0.9)]), \
                patch("tasks.story.read_text",
                      return_value=[("MENU", 1203, 61, 0.9)]), \
                patch("tasks.event.handle_network_error", return_value=False), \
                patch("tasks.event._handle_download", return_value=False), \
                patch("tasks.event._is_reward_popup", return_value=False), \
                patch("tasks.event.time.sleep"):
            ok = task._play_story_node(ctx)

        self.assertFalse(ok)
        self.assertLessEqual(len(ctx.clicks), 4)         # 没有一直点下去
        self.assertTrue(any("story_stuck" in str(n) for n in ctx.saved))
        self.assertIn("认不出", ctx.logger.joined())

    def test_recover_to_stage_list_gives_up_quickly_with_evidence(self):
        """标签恢复：找不到标签时最多试 2 轮，并留下现场截图。

        实测 2026-10-02：一次日常里「页面上没有「X」标签」出现 4 次，
        每次约 6 分钟（`_recover_to_stage_list` 4 轮 × 内层
        `_back_to_stage_list` 最多 8 轮）。合计约 25 分钟全花在这里。
        """
        from unittest.mock import patch

        import numpy as np

        task = ClearEventStages()
        screen = np.full((720, 1280, 3), 120, np.uint8)
        calls = {"back": 0, "shots": 0, "saved": []}

        class _C:
            def __init__(self):
                self.logger = _Logger()
                self._last_screen = screen

            def screenshot(self):
                calls["shots"] += 1
                return self._last_screen

            def save_screenshot(self, img, name=None):
                calls["saved"].append(name)
                return f"screenshots/{name}"

            def find(self, *a, **k):
                return None

            def key(self, code):
                pass

        def _back(ctx):
            calls["back"] += 1
            return False

        ctx = _C()
        task._is_target_stage_list = lambda c: False
        task._dismiss_title_return = lambda c: False
        task._back_to_stage_list = _back

        with patch("tasks.event.read_text", return_value=[]), \
                patch("tasks.event._is_story_screen", return_value=False), \
                patch("tasks.event._is_reward_popup", return_value=False), \
                patch("tasks.event.handle_network_error", return_value=False):
            ok = task._recover_to_stage_list(ctx)

        self.assertFalse(ok)
        self.assertLessEqual(calls["back"], 2)           # 不再 4 轮 × 内层 8 轮
        self.assertLessEqual(calls["shots"], 3)
        self.assertIn("event_recover_stuck.png", calls["saved"])


class BattleWaitTitleGuardTest(unittest.TestCase):
    """2026-09-30 实测：活动战斗中途游戏自己重启回标题，等待循环白等 9 分钟。

    标题画面背景一直在动，「画面是否静止」那条兜底判不出来；现在直接认标题收工。
    """

    def test_event_battle_gives_up_when_back_to_title(self):
        import numpy as np

        class _C:
            def __init__(self):
                self.logger = _Logger()
                self._last_screen = np.zeros((720, 1280, 3), np.uint8)
                self.saved = []
                self.shots = 0

            def screenshot(self, *a, **k):
                self.shots += 1
                return self._last_screen

            def save_screenshot(self, img, name=None):
                self.saved.append(name)
                return f"screenshots/{name}"

        ctx = _C()
        task = ClearEventBattle()
        with patch("tasks.event.time.sleep"), \
                patch("tasks.event.handle_network_error", return_value=False), \
                patch("tasks.event.handle_download_popup", return_value=False), \
                patch("tasks.event.is_title_screen", return_value=True):
            ok = task._battle_and_clear(ctx)

        self.assertFalse(ok)
        self.assertTrue(task.failed)
        self.assertLessEqual(ctx.shots, 2)                 # 没有等到 540 秒上限
        self.assertIn("event_battle_title_back.png", ctx.saved)
        self.assertIn("标题画面", ctx.logger.joined())


if __name__ == "__main__":
    unittest.main()
