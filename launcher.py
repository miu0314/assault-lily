# -*- coding: utf-8 -*-
"""突击莉莉脚本启动器：勾选想跑的功能，点「开始运行」。"""

import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import traceback
from pathlib import Path

import app_paths

BASE_DIR = app_paths.base_dir()
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


def _startup_log(message):
    """pythonw 没有控制台：启动信息写到项目目录，方便事后排查。"""
    try:
        with (BASE_DIR / "launcher_error.log").open("a", encoding="utf-8") as fh:
            fh.write(str(message).rstrip() + "\n")
    except OSError:
        pass


def _report_startup_failure(exc_type, exc, tb):
    """启动崩溃时写日志并弹窗，避免双击后「什么都没发生」。"""
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    _startup_log(text)
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            None, text[-1200:], "突击莉莉脚本启动失败", 0x10)
    except Exception:
        pass


sys.excepthook = _report_startup_failure

# 运行环境更新会清掉第三方库：先挂上项目内的 deps，缺了自动补装。
import dep_bootstrap  # noqa: E402

dep_bootstrap.ensure_deps(log=_startup_log)

# Tk/Tcl must see these variables before tkinter imports _tkinter.  The bundled
# runtime resolves its library reliably from a relative path and runtime cwd.
_TK_RUNTIME = Path(sys.executable).parent
if (_TK_RUNTIME / "tcl" / "tcl8.6" / "init.tcl").exists():
    os.environ.setdefault("TCL_LIBRARY", "tcl/tcl8.6")
    os.environ.setdefault("TK_LIBRARY", "tcl/tk8.6")

import tkinter as tk
from tkinter import ttk

# 聊天定型文表（和 tasks/chat.py 共用一份，避免两边对不上）
from tasks.chat_phrases import all_phrases as _chat_phrases  # noqa: E402
CONFIG_DEFAULT = BASE_DIR / "config.json"
CONFIG_SELECTED = BASE_DIR / "config_selected.json"
CATALOG_PATH = BASE_DIR / "legion_shop_catalog.json"
PAUSE_FLAG = BASE_DIR / "pause.flag"

# (显示名, 任务名)
INFRA_TASKS = [
    ("启动并进入首页", "LaunchToHome"),
]

FUNC_TASKS = [
    ("免费扭蛋", "CollectFreeGacha"),
    ("礼物箱领取", "CollectGifts"),
    ("发送消息", "SendChatMessage"),
    ("任务领取", "CollectDailyMissions"),
    ("每日关卡扫荡", "ClearDailyQuests"),
    ("RANK UP 扫荡", "SweepRankUpStage"),
    ("军团徽章贡献", "LegionDonate"),
    ("军团兑换", "ExchangeLegionItems"),
    ("传奇战斗", "LegendaryBattle"),
    ("外征任务（全部）", "ClearLegionGekiha"),
    ("限时活动（全部）", "ClearEventAll"),
    ("每日活动一键跳过", "DailyEventSkip"),
    ("活动战斗（只清战斗）", "ClearEventBattle"),
    ("活动剧情（只清剧情）", "ClearEventStory"),
    ("推主剧情", "PushMainStory"),
]

ALL_TASKS = INFRA_TASKS + FUNC_TASKS
NAME_TO_LABEL = {name: label for label, name in ALL_TASKS}
LABEL_TO_NAME = {label: name for label, name in ALL_TASKS}
# 兼容旧流程：启动游戏/等待登录/确保在首页 已合并成「启动并进入首页」
for _old in ("LaunchGame", "WaitForHome", "GoHomeFirst"):
    NAME_TO_LABEL[_old] = "启动并进入首页"
NAME_TO_LABEL["ClearMelissaStages"] = "クリオン外征"
NAME_TO_LABEL["ClearEventStages"] = "活动任务"
LABEL_TO_NAME["启动并进入首页"] = "LaunchToHome"


def _pythonw_path():
    """返回可用的 pythonw.exe，避免误用缺少 DLL 的系统 Python。

    启动器有时会被用户从命令行/文件关联用另一套 Python 启动。若那套
    Python 的 ``python313.dll`` 已损坏，主进程虽然可能还能显示窗口，点
    ``开始运行`` 时启动子进程就会弹出系统错误。优先沿用当前解释器，
    但先检查同目录是否确实有 Python DLL；然后回退到 Codex 随项目使用的
    完整运行时。
    """
    def valid_python(exe, required_dll=None):
        try:
            exe = Path(exe)
            if not exe.exists() or not exe.is_file():
                return False
            # 当前解释器可精确检查对应版本 DLL；G:\python.exe 缺少
            # python313.dll 时即使旁边有一个通用 python3.dll 也不能使用。
            if required_dll:
                return (exe.parent / required_dll).is_file()
            # 其他候选至少需要同目录的 python3*.dll。
            return any(exe.parent.glob("python3*.dll"))
        except (OSError, TypeError):
            return False

    current = Path(sys.executable)
    if current.name.lower().startswith("python"):
        candidate = current.with_name("pythonw.exe")
        required = f"python{sys.version_info.major}{sys.version_info.minor}.dll"
        if valid_python(candidate, required):
            return str(candidate)
        if valid_python(current, required):
            # 只有 pythonw.exe 不存在时才退回控制台解释器。
            return str(current)

    # 项目交接文档记录的完整运行时位置。用用户目录拼接，避免把账号写死。
    bundled = (Path.home() / ".cache" / "codex-runtimes" /
               "codex-primary-runtime" / "dependencies" / "python" /
               "pythonw.exe")
    if valid_python(bundled):
        return str(bundled)

    # 最后尝试 PATH 中的 pythonw；仍需通过 DLL 检查。
    path_pythonw = shutil.which("pythonw.exe") or shutil.which("pythonw")
    if path_pythonw and valid_python(path_pythonw):
        return str(path_pythonw)
    return str(current)


def _runtime_env():
    """为 Tk 子进程补齐 Tcl/Tk 相对路径。

    该 Python 运行时的 Tcl 目录是随解释器一起提供的。使用相对路径并
    把工作目录设为运行时目录，可以让 Tcl_Init 在受限环境和普通双击启动
    下都找到 init.tcl；绝对路径环境变量在部分嵌入式运行时中反而会被忽略。
    """
    env = os.environ.copy()
    runtime = Path(_pythonw_path()).parent
    if (runtime / "tcl" / "tcl8.6" / "init.tcl").exists():
        env["TCL_LIBRARY"] = "tcl/tcl8.6"
        env["TK_LIBRARY"] = "tcl/tk8.6"
    return env, runtime


def _prepare_tk_runtime():
    """在创建 Tk 根窗口前，让 Tcl/Tk 按解释器目录加载库文件。"""
    runtime = Path(sys.executable).parent
    tcl_dir = runtime / "tcl" / "tcl8.6"
    tk_dir = runtime / "tcl" / "tk8.6"
    if not (tcl_dir / "init.tcl").exists():
        return
    # Tcl 在此运行时中以相对路径查找 init.tcl；工作目录必须是解释器目录。
    os.environ["TCL_LIBRARY"] = "tcl/tcl8.6"
    os.environ["TK_LIBRARY"] = "tcl/tk8.6"
    try:
        os.chdir(runtime)
    except OSError:
        pass
# 打包版只有一个 exe：子进程改成「带隐藏参数重启自己」，参数由 packaging_entry 分发。
FROZEN_CHILD_FLAGS = {
    "main.py": "--run-main",
    "scripts/scan_legion_shop.py": "--scan-shop",
}


def child_command(script, *args):
    """拼「主程序 / 扫兑换所」子进程的命令行。

    源码模式：用 pythonw 跑脚本文件。
    打包模式：exe 里没有 .py 文件可以跑，重启自身并带上隐藏参数。
    """
    if app_paths.is_frozen():
        flag = FROZEN_CHILD_FLAGS.get(str(script).replace("\\", "/"))
        if flag is None:
            raise ValueError(f"打包模式不支持运行 {script}")
        return [sys.executable, flag, *args]
    return [_pythonw_path(), str(BASE_DIR / script), *args]


def write_selected_config(serial, adb_path, rankup_difficulty, donate_count,
                          donate_enabled,
                          exchange_plan, task_flows, mumu_auto_wake,
                          accelerator_path, accelerator_process, accelerator_package,
                          accelerator_connect, accelerator_disconnect, task_list,
                          story_start_chapter=0,
                          mumu_accel_action="no_accel",
                          legendary_sweep_count=1,
                          chat_message_tab="レギオン",
                          chat_message_text="ごきげんよう"):
    cfg = json.loads(CONFIG_DEFAULT.read_text(encoding="utf-8"))
    cfg["serial"] = serial.strip()
    cfg["adb_path"] = adb_path.strip()
    cfg["rankup_stage_difficulty"] = rankup_difficulty.strip()
    cfg["legion_donate_count"] = donate_count.strip()
    cfg["legion_donate_enabled"] = bool(donate_enabled)
    cfg["legion_exchange_plan"] = exchange_plan
    cfg["use_custom_flow"] = True
    cfg["task_flows"] = task_flows
    cfg["mumu_auto_wake"] = mumu_auto_wake
    cfg["accelerator_path"] = accelerator_path.strip()
    cfg["accelerator_process"] = accelerator_process.strip()
    cfg["accelerator_package"] = accelerator_package.strip()
    cfg["accelerator_connect"] = accelerator_connect
    cfg["accelerator_disconnect"] = accelerator_disconnect
    cfg["mumu_accel_action"] = mumu_accel_action
    try:
        cfg["legendary_sweep_count"] = max(1, min(20, int(legendary_sweep_count)))
    except (TypeError, ValueError):
        cfg["legendary_sweep_count"] = 1
    cfg["task_list"] = task_list
    cfg["story_start_chapter"] = int(story_start_chapter)
    cfg["chat_message_tab"] = str(chat_message_tab).strip() or "レギオン"
    cfg["chat_message_text"] = str(chat_message_text).strip() or "ごきげんよう"
    CONFIG_SELECTED.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return CONFIG_SELECTED


def _norm_name(text):
    """物品名归一化，和兑换模块的匹配规则保持一致。"""
    t = re.sub(r"[^0-9A-Za-z\u3040-\u30ff\u4e00-\u9fff]", "", str(text)).upper()
    t = t.replace("載", "").replace("戦", "").replace("戰", "")
    return re.sub(r"I+川$", "III", t)


def load_catalog():
    """读取兑换所目录，返回 [{name, max}]。"""
    try:
        data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    items = []
    for it in data.get("items", []):
        name = str(it.get("name", "")).strip()
        if not name or name == "?":
            continue
        try:
            mx = max(0, int(it.get("max") or 0))
        except (TypeError, ValueError):
            mx = 0
        items.append({"name": name, "max": mx})
    return items


class LauncherApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"突击莉莉脚本启动器 v{app_paths.__version__}")
        self.geometry("1240x720")
        self.minsize(1000, 640)
        self.configure(bg="#f2f4f8")

        self.proc = None
        self.msg_queue = queue.Queue()
        self.exchange_rows = []
        self.flow_rows = []
        self.flows_data = {"active": "", "flows": {}}
        self.flow_dirty = False
        self._refresh_mode = False

        self._build_ui()
        self._load_config_values()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._poll_queue)

    # ---------- 界面 ----------
    def _build_ui(self):
        style = ttk.Style(self)
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        style.configure("TLabel", background="#f2f4f8",
                        font=("Microsoft YaHei UI", 10))
        style.configure("TLabelframe.Label", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("TButton", font=("Microsoft YaHei UI", 10))
        style.configure("TCheckbutton", background="#f2f4f8",
                        font=("Microsoft YaHei UI", 10))
        style.configure("Accent.TButton", font=("Microsoft YaHei UI", 12, "bold"))

        pad = {"padx": 14, "pady": 6}

        header = ttk.Label(self, text="突击莉莉自动脚本",
                           font=("Microsoft YaHei UI", 15, "bold"))
        header.grid(row=0, column=0, columnspan=2, sticky="w",
                    padx=14, pady=(10, 0))
        ttk.Label(self, text="配置好任务流程后点「开始运行」；运行中可随时暂停或停止",
                  foreground="#666666").grid(row=1, column=0, columnspan=2,
                                             sticky="w", padx=14, pady=(0, 6))

        notebook = ttk.Notebook(self)
        notebook.grid(row=2, column=0, sticky="nsew", padx=(12, 6), pady=4)
        self.notebook = notebook

        # ---------- 标签页1：自定义流程（主页面） ----------
        tab_flow = ttk.Frame(notebook, padding=8)
        notebook.add(tab_flow, text="  自定义流程  ")
        flow_top1 = ttk.Frame(tab_flow)
        flow_top1.pack(fill="x", pady=(0, 2))
        ttk.Label(flow_top1, text="流程：").pack(side="left")
        self.flow_selector = ttk.Combobox(flow_top1, state="readonly", width=14)
        self.flow_selector.pack(side="left", padx=(4, 8))
        self.flow_selector.bind("<<ComboboxSelected>>", self._on_flow_selected)
        self.flow_new_btn = ttk.Button(flow_top1, text="新建", command=self._new_flow)
        self.flow_new_btn.pack(side="left", padx=4)
        self.flow_del_btn = ttk.Button(flow_top1, text="删除", command=self._delete_flow)
        self.flow_del_btn.pack(side="left", padx=4)
        self.flow_save_btn = ttk.Button(flow_top1, text="保存流程",
                                        command=self._save_flow_clicked)
        self.flow_save_btn.pack(side="left", padx=(12, 4))
        self.flow_save_lbl = ttk.Label(flow_top1, text="已保存",
                                       foreground="#1a7f37")
        self.flow_save_lbl.pack(side="right", padx=(6, 2))

        flow_top2 = ttk.Frame(tab_flow)
        flow_top2.pack(fill="x", pady=(0, 6))
        flow_set_row1 = ttk.Frame(flow_top2)
        flow_set_row1.pack(fill="x")
        ttk.Label(flow_set_row1, text="RANK UP 扫荡难度：").pack(side="left")
        self.rankup_diff_var = tk.StringVar(value="EX")
        self.rankup_diff_box = ttk.Combobox(flow_set_row1, textvariable=self.rankup_diff_var,
                                            values=["EX", "上級", "中級", "初級"], width=8,
                                            state="readonly")
        self.rankup_diff_box.pack(side="left", padx=(2, 14))
        ttk.Label(flow_set_row1, text="推主剧情起始章节：").pack(side="left", padx=(4, 0))
        self.story_start_chapter_var = tk.StringVar(value="自动（从断点继续）")
        self.story_start_chapter_box = ttk.Combobox(
            flow_set_row1, textvariable=self.story_start_chapter_var, width=20,
            state="readonly")
        self.story_start_chapter_box["values"] = [
            "自动（从断点继续）", "第 1 章", "第 2 章", "第 3 章",
            "第 4 章", "第 5 章"]
        self.story_start_chapter_box.pack(side="left", padx=(2, 14))
        flow_set_row2 = ttk.Frame(flow_top2)
        flow_set_row2.pack(fill="x", pady=(2, 0))
        ttk.Label(flow_set_row2, text="传奇扫荡次数：").pack(side="left")
        self.legendary_sweep_var = tk.StringVar(value="1")
        self.legendary_sweep_box = ttk.Combobox(
            flow_set_row2, textvariable=self.legendary_sweep_var,
            values=[str(i) for i in range(1, 6)], width=5,
            state="readonly")
        self.legendary_sweep_box.pack(side="left", padx=(2, 14))

        flow_set_row3 = ttk.Frame(flow_top2)
        flow_set_row3.pack(fill="x", pady=(2, 0))
        ttk.Label(flow_set_row3, text="发送消息频道：").pack(side="left")
        self.chat_tab_var = tk.StringVar(value="レギオン")
        self.chat_tab_box = ttk.Combobox(
            flow_set_row3, textvariable=self.chat_tab_var,
            values=["レギオン", "グループ", "個人"], width=10, state="readonly")
        self.chat_tab_box.pack(side="left", padx=(2, 14))
        ttk.Label(flow_set_row3, text="发言内容（游戏定型文）：").pack(side="left", padx=(4, 0))
        self.chat_text_var = tk.StringVar(value="ごきげんよう")
        self.chat_text_box = ttk.Combobox(
            flow_set_row3, textvariable=self.chat_text_var,
            values=_chat_phrases(), width=18, state="readonly")
        self.chat_text_box.pack(side="left", padx=(2, 14))

        flow_list_box = ttk.LabelFrame(tab_flow, text="任务执行顺序（↑↓ 排序，可增删 / 启用）")
        flow_list_box.pack(fill="both", expand=True)
        flow_list_head = ttk.Frame(flow_list_box)
        flow_list_head.pack(fill="x", padx=6, pady=(4, 0))
        self.flow_add_btn = ttk.Button(flow_list_head, text="＋ 添加任务",
                                       command=self._add_flow_task)
        self.flow_add_btn.pack(side="left")
        self.flow_sel_btn = ttk.Button(flow_list_head, text="全选",
                                       command=self._flow_select_all)
        self.flow_sel_btn.pack(side="left", padx=4)
        self.flow_unsel_btn = ttk.Button(flow_list_head, text="全不选",
                                         command=self._flow_clear_all)
        self.flow_unsel_btn.pack(side="left", padx=4)
        self.flow_canvas = tk.Canvas(flow_list_box, bg="#f2f4f8", highlightthickness=0)
        flow_scroll = ttk.Scrollbar(flow_list_box, orient="vertical",
                                    command=self.flow_canvas.yview)
        self.flow_inner = ttk.Frame(self.flow_canvas)
        self.flow_inner.bind(
            "<Configure>",
            lambda e: self.flow_canvas.configure(
                scrollregion=self.flow_canvas.bbox("all")))
        self.flow_canvas.create_window((0, 0), window=self.flow_inner, anchor="nw")
        self.flow_canvas.configure(yscrollcommand=flow_scroll.set)
        flow_scroll.pack(side="right", fill="y")
        self.flow_canvas.pack(fill="both", expand=True, padx=6, pady=6)

        # ---------- 标签页2：军团兑换 ----------
        tab_plan = ttk.Frame(notebook, padding=8)
        notebook.add(tab_plan, text="  军团兑换  ")
        plan_opts = ttk.Frame(tab_plan)
        plan_opts.pack(fill="x", pady=(0, 4))
        self.donate_enabled_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(plan_opts, text="启用军团捐献（徽章贡献）",
                        variable=self.donate_enabled_var).pack(side="left")
        ttk.Label(plan_opts, text="军团捐献次数：").pack(side="left")
        self.donate_count_var = tk.StringVar(value="1")
        ttk.Combobox(plan_opts, textvariable=self.donate_count_var,
                     values=["max"] + [str(i) for i in range(1, 31)],
                     width=8, state="readonly").pack(side="left", padx=(2, 10))
        ttk.Label(plan_opts, text="（用于“军团徽章贡献”，max=全部）",
                  foreground="#888888").pack(side="left")
        self.refresh_btn = ttk.Button(plan_opts, text="刷新目录",
                                      command=self._refresh_catalog)
        self.refresh_btn.pack(side="left", padx=10)
        plan_box = ttk.LabelFrame(
            tab_plan, text="军团兑换计划（勾选要兑换的物品，数量可选手动或全部）")
        plan_box.pack(fill="both", expand=True)
        self.plan_canvas = tk.Canvas(plan_box, height=260, bg="#f2f4f8",
                                     highlightthickness=0)
        plan_scroll = ttk.Scrollbar(plan_box, orient="vertical",
                                    command=self.plan_canvas.yview)
        self.plan_inner = ttk.Frame(self.plan_canvas)
        self.plan_inner.bind(
            "<Configure>",
            lambda e: self.plan_canvas.configure(
                scrollregion=self.plan_canvas.bbox("all")))
        self.plan_canvas.create_window((0, 0), window=self.plan_inner, anchor="nw")
        self.plan_canvas.configure(yscrollcommand=plan_scroll.set)
        plan_scroll.pack(side="right", fill="y")
        self.plan_canvas.pack(fill="both", expand=True, padx=8, pady=4)
        ttk.Label(plan_box, text="目录来自 legion_shop_catalog.json，跑一次「军团兑换」会自动更新",
                  foreground="#888888").pack(anchor="w", padx=8, pady=(0, 4))
        self._rebuild_plan_rows()

        # ---------- 标签页3：连接与设置 ----------
        tab_set = ttk.Frame(notebook, padding=12)
        notebook.add(tab_set, text="  连接与设置  ")

        set_sim = ttk.LabelFrame(tab_set, text="模拟器")
        set_sim.pack(fill="x", pady=(0, 10))
        ttk.Label(set_sim, text="模拟器地址：").grid(row=0, column=0, sticky="e", padx=6, pady=4)
        self.serial_var = tk.StringVar()
        ttk.Entry(set_sim, textvariable=self.serial_var, width=24).grid(
            row=0, column=1, sticky="w", padx=4, pady=4)
        ttk.Label(set_sim, text="adb 路径：").grid(row=1, column=0, sticky="e", padx=6, pady=4)
        self.adb_var = tk.StringVar()
        ttk.Entry(set_sim, textvariable=self.adb_var, width=58).grid(
            row=1, column=1, sticky="w", padx=4, pady=4)
        self.mumu_wake_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(set_sim, text="模拟器未启动时自动唤醒（推荐勾选）",
                        variable=self.mumu_wake_var).grid(
            row=2, column=0, columnspan=2, sticky="w", padx=4, pady=4)
        self.close_mumu_popup_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(set_sim, text="启动时自动关闭 MuMu 弹窗（点「不加速」）",
                        variable=self.close_mumu_popup_var).grid(
            row=3, column=0, columnspan=2, sticky="w", padx=4, pady=4)

        set_acc = ttk.LabelFrame(tab_set, text="加速器")
        set_acc.pack(fill="x")
        ttk.Label(set_acc, text="程序路径：").grid(row=0, column=0, sticky="e", padx=6, pady=4)
        self.accelerator_path_var = tk.StringVar()
        ttk.Entry(set_acc, textvariable=self.accelerator_path_var, width=58).grid(
            row=0, column=1, sticky="w", padx=4, pady=4)
        ttk.Label(set_acc, text="进程名：").grid(row=1, column=0, sticky="e", padx=6, pady=4)
        self.accelerator_process_var = tk.StringVar()
        ttk.Entry(set_acc, textvariable=self.accelerator_process_var, width=24).grid(
            row=1, column=1, sticky="w", padx=4, pady=4)
        ttk.Label(set_acc, text="例如进程名填 uu.exe / 迅游加速器.exe",
                  foreground="#888888").grid(row=1, column=2, sticky="w", padx=4)
        ttk.Label(set_acc, text="包名（模拟器内App）：").grid(row=2, column=0, sticky="e", padx=6, pady=4)
        self.accelerator_package_var = tk.StringVar()
        ttk.Entry(set_acc, textvariable=self.accelerator_package_var, width=24).grid(
            row=2, column=1, sticky="w", padx=4, pady=4)
        ttk.Label(set_acc, text="例如 FIClash 填 com.follow.clash；填了包名就优先用模拟器内App",
                  foreground="#888888").grid(row=2, column=2, sticky="w", padx=4)
        self.accelerator_connect_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(set_acc, text="启动加速器（自动打开并连接）",
                        variable=self.accelerator_connect_var).grid(
            row=3, column=0, columnspan=2, sticky="w", padx=4, pady=4)
        self.accelerator_disconnect_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(set_acc, text="运行结束后断开加速器",
                        variable=self.accelerator_disconnect_var).grid(
            row=4, column=0, columnspan=2, sticky="w", padx=4, pady=4)
        ttk.Label(tab_set, text="提示：一般只需设置一次，改动会保存到本次运行配置。",
                  foreground="#888888").pack(anchor="w", pady=(8, 0))

        btn_row = ttk.Frame(self)
        btn_row.grid(row=3, column=0, columnspan=2, sticky="ew",
                     padx=12, pady=6)
        self.start_btn = ttk.Button(btn_row, text="▶ 开始运行",
                                    style="Accent.TButton", command=self._start)
        self.start_btn.pack(side="left", padx=(2, 8), pady=2)
        self.stop_btn = ttk.Button(btn_row, text="停止", command=self._stop)
        self.stop_btn.pack(side="left", padx=4)
        self.pause_btn = ttk.Button(btn_row, text="⏸ 暂停",
                                    command=self._toggle_pause, state="disabled")
        self.pause_btn.pack(side="left", padx=4)
        self.summary_lbl = ttk.Label(btn_row, text="", foreground="#555555")
        self.summary_lbl.pack(side="left", padx=(14, 4))
        self.status_var = tk.StringVar(value="未运行")
        self.status_lbl = ttk.Label(btn_row, textvariable=self.status_var,
                                    foreground="#1a7f37")
        self.status_lbl.pack(side="right", padx=8)

        log_box = ttk.LabelFrame(self, text="运行日志")
        log_box.grid(row=2, column=1, sticky="nsew", padx=(6, 12), pady=4)
        log_head = ttk.Frame(log_box)
        log_head.pack(fill="x", padx=8, pady=(4, 0))
        self.auto_scroll_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(log_head, text="自动滚动",
                        variable=self.auto_scroll_var).pack(side="left")
        ttk.Button(log_head, text="复制日志", command=self._copy_log).pack(side="left", padx=4)
        ttk.Button(log_head, text="清空日志", command=self._clear_log).pack(side="left", padx=4)
        log_inner = ttk.Frame(log_box)
        log_inner.pack(fill="both", expand=True, padx=8, pady=6)
        self.log_text = tk.Text(log_inner, height=16, width=46, state="disabled",
                                font=("Consolas", 9), bg="#1e1e1e", fg="#dcdcdc",
                                wrap="word")
        scroll = ttk.Scrollbar(log_inner, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.log_text.pack(side="left", fill="both", expand=True)

        self.grid_rowconfigure(2, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, minsize=360)

    def _load_config_values(self):
        try:
            cfg = json.loads(CONFIG_DEFAULT.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
        self.serial_var.set(cfg.get("serial", "127.0.0.1:16384"))
        self.adb_var.set(cfg.get("adb_path", ""))
        self.rankup_diff_var.set(cfg.get("rankup_stage_difficulty", "EX"))
        self.donate_enabled_var.set(bool(cfg.get("legion_donate_enabled", False)))
        self.donate_count_var.set(str(cfg.get("legion_donate_count", "1")))
        start_ch = int(cfg.get("story_start_chapter", 0) or 0)
        if 1 <= start_ch <= 5:
            self.story_start_chapter_var.set(f"第 {start_ch} 章")
        else:
            self.story_start_chapter_var.set("自动（从断点继续）")
        self.mumu_wake_var.set(bool(cfg.get("mumu_auto_wake", True)))
        self.accelerator_path_var.set(cfg.get("accelerator_path", ""))
        self.accelerator_process_var.set(cfg.get("accelerator_process", ""))
        self.accelerator_package_var.set(cfg.get("accelerator_package", ""))
        self.accelerator_connect_var.set(bool(cfg.get("accelerator_connect", True)))
        self.accelerator_disconnect_var.set(bool(cfg.get("accelerator_disconnect", False)))
        self.legendary_sweep_var.set(str(cfg.get("legendary_sweep_count", 1)))
        chat_tab = str(cfg.get("chat_message_tab", "レギオン") or "レギオン")
        if chat_tab not in ("レギオン", "グループ", "個人"):
            chat_tab = "レギオン"
        self.chat_tab_var.set(chat_tab)
        chat_text = str(cfg.get("chat_message_text", "ごきげんよう") or "").strip()
        if chat_text not in _chat_phrases():
            chat_text = "ごきげんよう"
        self.chat_text_var.set(chat_text)
        self.close_mumu_popup_var.set(
            str(cfg.get("mumu_accel_action", "no_accel")) != "ignore")
        geometry = cfg.get("launcher_geometry")
        if geometry and re.match(r"^\d+x\d+[+-]\d+[+-]\d+$", str(geometry)):
            m = re.match(r"^(\d+)x(\d+)([+-]\d+[+-]\d+)$", str(geometry))
            if m:
                w, h, pos = int(m.group(1)), int(m.group(2)), m.group(3)
                # 旧版本遗留的 1000x640 会让新布局挤出底部按钮；至少保证默认尺寸，保留位置。
                if w < 1240 or h < 720:
                    w, h = 1240, 720
                self.geometry(f"{w}x{h}{pos}")
            else:
                self.geometry(str(geometry))
        self._update_summary()
        self._load_flows(cfg)
        self._rebuild_flow_ui()
        for item in cfg.get("legion_exchange_plan", []) or []:
            if not isinstance(item, dict):
                continue
            target = _norm_name(item.get("item", ""))
            qty = item.get("qty", 1)
            for name, check, qv in self.exchange_rows:
                if target and _norm_name(name) == target:
                    check.set(True)
                    if isinstance(qty, str) and qty.strip().lower() in ("max", "all", "全部"):
                        qv.set("max")
                    else:
                        qv.set(str(qty))

    def _rebuild_plan_rows(self):
        for child in self.plan_inner.winfo_children():
            child.destroy()
        self.exchange_rows = []
        items = load_catalog()
        if not items:
            ttk.Label(self.plan_inner,
                      text="尚未记录商店目录，请先跑一次「军团兑换」生成目录").pack(
                anchor="w", padx=8, pady=4)
            return
        for item in items:
            row = ttk.Frame(self.plan_inner)
            row.pack(fill="x", pady=1)
            check = tk.BooleanVar(value=False)
            qty = tk.StringVar(value="1")
            ttk.Checkbutton(row, text=f"{item['name']}（剩余 {item['max']}）",
                            variable=check).pack(side="left", padx=6)
            ttk.Label(row, text="数量：").pack(side="left")
            values = ["max"] + [str(i) for i in range(1, min(item["max"], 99) + 1)]
            ttk.Combobox(row, textvariable=qty, values=values, width=6,
                         state="readonly").pack(side="left", padx=4)
            self.exchange_rows.append((item["name"], check, qty))

    def _collect_plan(self):
        plan = []
        for name, check, qv in self.exchange_rows:
            if not check.get():
                continue
            q = qv.get().strip()
            if q.lower() == "max":
                plan.append({"item": name, "qty": "max"})
            else:
                try:
                    plan.append({"item": name, "qty": int(q)})
                except ValueError:
                    plan.append({"item": name, "qty": 1})
        return plan

    def _refresh_catalog(self):
        """进游戏扫描兑换所，更新目录并刷新列表。"""
        if self.proc is not None and self.proc.poll() is None:
            self._append_log("有任务正在运行，请先停止再刷新目录。\n")
            return
        self._append_log("开始刷新兑换所目录（需要进游戏扫描，约1分钟）...\n")
        env, runtime_cwd = _runtime_env()
        env["PYTHONIOENCODING"] = "utf-8"
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self._refresh_mode = True
            self.proc = subprocess.Popen(
                child_command("scripts/scan_legion_shop.py", str(CONFIG_DEFAULT)),
                cwd=str(runtime_cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=env,
                creationflags=flags,
            )
        except Exception as e:
            self._refresh_mode = False
            self._append_log(f"启动刷新失败：{e}\n")
            return
        self._set_status("刷新目录中...")
        self._set_running_ui(True)
        threading.Thread(target=self._read_output, daemon=True).start()

    # ---------- 自定义流程 ----------
    def _load_flows(self, cfg):
        data = cfg.get("task_flows", {}) or {}
        flows = data.get("flows", {})
        if not flows:
            flows = {"日常流程": [
                {"task": name, "enabled": True} for _label, name in ALL_TASKS]}
        active = data.get("active") or next(iter(flows))
        if active not in flows:
            active = next(iter(flows))
        # 加速器相关任务已改为“连接与设置”里控制，不再出现在流程里
        for name in list(flows):
            flows[name] = [it for it in flows[name]
                           if it.get("task") not in ("LaunchAccelerator",
                                                     "DisconnectAccelerator")]
            # 旧流程三个基础任务合并成「启动并进入首页」；连续出现时去重
            merged = []
            for it in flows[name]:
                task = it.get("task")
                if task in ("LaunchGame", "WaitForHome", "GoHomeFirst"):
                    task = "LaunchToHome"
                elif task in ("ClearMelissaStages", "ClearAramStages",
                              "ClearClionStages", "ClearLegionStages"):
                    # 外征任务改为通用版：按「撃破任務」logo 识别，不再绑定 boss 名
                    task = "ClearLegionGekiha"
                item = dict(it)
                item["task"] = task
                if (merged and merged[-1]["task"] == task
                        and task in ("LaunchToHome", "ClearLegionGekiha")):
                    merged[-1]["enabled"] = bool(merged[-1].get("enabled", True)) or bool(
                        item.get("enabled", True))
                    continue
                merged.append(item)
            flows[name] = merged
        self.flows_data = {"active": active, "flows": flows}

    def _rebuild_flow_ui(self):
        if not hasattr(self, "flow_selector"):
            return
        self.flow_selector["values"] = list(self.flows_data["flows"].keys())
        self.flow_selector.set(self.flows_data["active"])
        self._render_flow_rows()

    def _render_flow_rows(self):
        for child in self.flow_inner.winfo_children():
            child.destroy()
        self.flow_rows = []
        tasks = self.flows_data["flows"].get(self.flows_data["active"], [])
        if not tasks:
            ttk.Label(self.flow_inner,
                      text="流程为空，点「添加 任务」加入任务").pack(
                anchor="w", padx=10, pady=8)
            return
        for i, item in enumerate(tasks):
            row = ttk.Frame(self.flow_inner)
            row.pack(fill="x", pady=1)
            enabled = tk.BooleanVar(value=bool(item.get("enabled", True)))
            task_var = tk.StringVar(
                value=NAME_TO_LABEL.get(item.get("task", ""), item.get("task", "")))
            ttk.Button(row, text="↑", width=3,
                       command=lambda idx=i: self._move_flow_task(idx, -1)
                       ).pack(side="left", padx=2)
            ttk.Button(row, text="↓", width=3,
                       command=lambda idx=i: self._move_flow_task(idx, 1)
                       ).pack(side="left", padx=2)
            cb = ttk.Combobox(row, textvariable=task_var,
                              values=list(LABEL_TO_NAME.keys()), width=20,
                              state="readonly")
            cb.pack(side="left", padx=4)
            cb.bind("<<ComboboxSelected>>",
                    lambda _e, idx=i: self._flow_edit(idx))
            ttk.Checkbutton(row, text="启用", variable=enabled,
                            command=lambda idx=i: self._flow_edit(idx)
                            ).pack(side="left", padx=4)
            ttk.Button(row, text="添加",
                       command=lambda idx=i: self._insert_flow_task(idx)
                       ).pack(side="left", padx=2)
            ttk.Button(row, text="删除",
                       command=lambda idx=i: self._delete_flow_task(idx)
                       ).pack(side="left", padx=2)
            self.flow_rows.append((item, enabled, task_var))
        ttk.Button(self.flow_inner, text="＋ 添加 任务（追加到末尾）",
                   command=self._add_flow_task).pack(anchor="w", padx=8, pady=(6, 2))
        self._update_summary()

    def _current_flow_items(self):
        return self.flows_data["flows"].setdefault(self.flows_data["active"], [])

    def _on_flow_selected(self, _event=None):
        name = self.flow_selector.get()
        if name in self.flows_data["flows"]:
            self._sync_flow_rows()
            self.flows_data["active"] = name
            self._render_flow_rows()
            self._save_flows()

    def _flow_edit(self, _index):
        """任务下拉框或启用勾选被修改：同步内存并标记未保存。"""
        self._sync_flow_rows()
        self.flow_dirty = True
        self._update_summary()
        if hasattr(self, "flow_save_lbl"):
            self.flow_save_lbl.configure(text="未保存修改", foreground="#b8860b")

    def _sync_flow_rows(self):
        """把界面上的任务类型/启用状态写回内存数据。"""
        items = self._current_flow_items()
        for idx, (item, enabled, task_var) in enumerate(self.flow_rows):
            if idx >= len(items):
                continue
            label = task_var.get()
            item["task"] = LABEL_TO_NAME.get(label, label)
            item["enabled"] = bool(enabled.get())

    def _save_flow_clicked(self):
        self._save_flows()
        self._append_log("流程已保存。\n")

    def _add_flow_task(self):
        self._current_flow_items().append({"task": "CollectGifts", "enabled": True})
        self._render_flow_rows()
        self._save_flows()

    def _insert_flow_task(self, index):
        """在指定任务行之后插入一个新任务（默认复制该行任务类型）。"""
        items = self._current_flow_items()
        if not (0 <= index < len(items)):
            return
        new_task = items[index].get("task", "CollectGifts")
        items.insert(index + 1, {"task": new_task, "enabled": True})
        self._render_flow_rows()
        self._save_flows()

    def _delete_flow_task(self, index):
        items = self._current_flow_items()
        if 0 <= index < len(items):
            items.pop(index)
            self._render_flow_rows()
            self._save_flows()

    def _move_flow_task(self, index, delta):
        items = self._current_flow_items()
        target = index + delta
        if 0 <= index < len(items) and 0 <= target < len(items):
            items[index], items[target] = items[target], items[index]
            self._render_flow_rows()
            self._save_flows()

    def _new_flow(self):
        names = list(self.flows_data["flows"].keys())
        n = 1
        while f"流程{n}" in names:
            n += 1
        new_name = f"流程{n}"
        self.flows_data["flows"][new_name] = [
            {"task": name, "enabled": True} for _label, name in ALL_TASKS]
        self.flows_data["active"] = new_name
        self._rebuild_flow_ui()
        self._save_flows()

    def _delete_flow(self):
        names = list(self.flows_data["flows"].keys())
        if len(names) <= 1:
            self._append_log("至少保留一个流程。\n")
            return
        active = self.flows_data["active"]
        del self.flows_data["flows"][active]
        self.flows_data["active"] = next(iter(self.flows_data["flows"]))
        self._rebuild_flow_ui()
        self._save_flows()

    def _flow_select_all(self):
        for _item, enabled, _var in self.flow_rows:
            enabled.set(True)
        self._flow_edit(0)

    def _flow_clear_all(self):
        for _item, enabled, _var in self.flow_rows:
            enabled.set(False)
        self._flow_edit(0)

    def _collect_flow(self):
        tasks = []
        for _item, enabled, task_var in self.flow_rows:
            if not enabled.get():
                continue
            label = task_var.get()
            name = LABEL_TO_NAME.get(label, label)
            if name:
                tasks.append(name)
        return tasks

    def _save_flows(self):
        self._sync_flow_rows()
        self.flow_dirty = False
        if hasattr(self, "flow_save_lbl"):
            self.flow_save_lbl.configure(text="已保存", foreground="#1a7f37")
        try:
            cfg = json.loads(CONFIG_DEFAULT.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
        cfg["use_custom_flow"] = True
        cfg["task_flows"] = {
            "active": self.flows_data["active"],
            "flows": self.flows_data["flows"],
        }
        try:
            CONFIG_DEFAULT.write_text(
                json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            self._append_log(f"保存流程失败：{e}\n")

    # ---------- 操作 ----------
    def _start(self):
        if self.proc is not None and self.proc.poll() is None:
            self._append_log("已有任务在运行，请先点「停止」。\n")
            return
        try:
            PAUSE_FLAG.unlink()
        except OSError:
            pass

        tasks = self._collect_flow()
        if not tasks:
            self._append_log("自定义流程为空，请先在「自定义流程」页添加任务。\n")
            return
        # 加速器按“连接与设置”里的开关自动插入：游戏与加速器连续启动，连接后切回游戏
        if self.accelerator_connect_var.get():
            if "LaunchToHome" in tasks:
                tasks.remove("LaunchToHome")
            tasks.insert(0, "LaunchAccelerator")
            tasks.insert(1, "LaunchToHome")  # 前一任务已先启动游戏，这里只负责等待进首页
        if self.accelerator_disconnect_var.get():
            tasks.append("DisconnectAccelerator")

        try:
            write_selected_config(self.serial_var.get(), self.adb_var.get(),
                                  self.rankup_diff_var.get(),
                                  self.donate_count_var.get(),
                                  bool(self.donate_enabled_var.get()),
                                  self._collect_plan(),
                                  self.flows_data,
                                  bool(self.mumu_wake_var.get()),
                                  self.accelerator_path_var.get(),
                                  self.accelerator_process_var.get(),
                                  self.accelerator_package_var.get(),
                                  bool(self.accelerator_connect_var.get()),
                                  bool(self.accelerator_disconnect_var.get()),
                                  tasks,
                                  story_start_chapter=self._story_start_chapter(),
                                  mumu_accel_action=(
                                      "no_accel" if self.close_mumu_popup_var.get() else "ignore"),
                                  legendary_sweep_count=self.legendary_sweep_var.get(),
                                  chat_message_tab=self.chat_tab_var.get(),
                                  chat_message_text=self.chat_text_var.get())
        except Exception as e:
            self._append_log(f"写入配置失败：{e}\n")
            return

        names = "、".join(NAME_TO_LABEL.get(t, t) for t in tasks)
        self._append_log(f"开始运行：{names}\n")

        env, runtime_cwd = _runtime_env()
        env["PYTHONIOENCODING"] = "utf-8"
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self.proc = subprocess.Popen(
                child_command("main.py", str(CONFIG_SELECTED)),
                cwd=str(runtime_cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=env,
                creationflags=flags,
            )
        except Exception as e:
            self._append_log(f"启动失败：{e}\n")
            self.proc = None
            return

        self._set_status("运行中（可暂停 / 停止）")
        self._set_running_ui(True)
        threading.Thread(target=self._read_output, daemon=True).start()

    def _story_start_chapter(self):
        """从下拉框解析起始章节编号（0=自动）。"""
        value = self.story_start_chapter_var.get()
        for ch in range(1, 6):
            if value == f"第 {ch} 章":
                return ch
        return 0

    def _stop(self):
        if self.proc is not None and self.proc.poll() is None:
            self._append_log("已发送停止请求，正在退出当前任务...\n")
            try:
                self.proc.terminate()
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
        else:
            self._append_log("当前没有运行中的任务。\n")

    def _toggle_pause(self):
        """暂停/继续：通过 pause.flag 通知后台脚本。"""
        if PAUSE_FLAG.exists():
            try:
                PAUSE_FLAG.unlink()
            except OSError:
                pass
            self.pause_btn.config(text="⏸ 暂停")
            self._append_log("已发送「继续」，脚本恢复运行。\n")
        else:
            try:
                PAUSE_FLAG.write_text("pause", encoding="utf-8")
            except OSError as e:
                self._append_log(f"暂停失败：{e}\n")
                return
            self.pause_btn.config(text="▶ 继续")
            self._append_log("已发送「暂停」，脚本会在当前步骤结束后停下来。\n")

    def _set_running_ui(self, running):
        if running:
            self.pause_btn.config(state="normal", text="⏸ 暂停")
            state = "disabled"
        else:
            self.pause_btn.config(state="disabled", text="⏸ 暂停")
            state = "normal"
        for btn in (self.start_btn,
                    self.flow_new_btn, self.flow_del_btn, self.flow_save_btn,
                    self.flow_add_btn, self.flow_sel_btn, self.flow_unsel_btn,
                    self.refresh_btn):
            btn.config(state=state)
        self.flow_selector.config(state="disabled" if running else "readonly")
        self.rankup_diff_box.config(state=state)
        self.story_start_chapter_box.config(state=state)

    # ---------- 日志/线程 ----------
    def _read_output(self):
        try:
            for line in self.proc.stdout:
                self.msg_queue.put(("log", line.rstrip()))
        except Exception:
            pass
        code = self.proc.wait()
        self.msg_queue.put(("done", code))

    def _poll_queue(self):
        try:
            while True:
                kind, payload = self.msg_queue.get_nowait()
                if kind == "log":
                    self._append_log(payload + "\n")
                else:
                    self._append_log(f"\n运行结束，退出码 {payload}\n")
                    self.proc = None
                    self._set_status("已结束")
                    self._set_running_ui(False)
                    try:
                        PAUSE_FLAG.unlink()
                    except OSError:
                        pass
                    if self._refresh_mode:
                        self._refresh_mode = False
                        self._append_log("目录已刷新，重新载入物品列表。\n")
                        self._rebuild_plan_rows()
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    def _append_log(self, text):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text)
        if self.auto_scroll_var.get():
            self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _copy_log(self):
        try:
            content = self.log_text.get("1.0", "end-1c")
            self.clipboard_clear()
            self.clipboard_append(content)
            self._append_log("日志已复制到剪贴板。\n")
        except Exception as e:
            self._append_log(f"复制日志失败：{e}\n")

    def _update_summary(self):
        try:
            enabled = sum(1 for _it, e, _v in self.flow_rows if e.get())
            total = len(self.flow_rows)
            self.summary_lbl.config(
                text=f"自定义流程：已启用 {enabled}/{total} 个任务")
        except Exception:
            pass

    def _set_status(self, text):
        color = "#b8860b" if ("运行中" in text or "刷新" in text) else "#1a7f37"
        self.status_var.set(text)
        self.status_lbl.configure(foreground=color)

    def _on_close(self):
        if self.flow_dirty:
            try:
                self._save_flows()
            except Exception:
                pass
        try:
            cfg = json.loads(CONFIG_DEFAULT.read_text(encoding="utf-8"))
            cfg["launcher_geometry"] = self.geometry()
            CONFIG_DEFAULT.write_text(
                json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
        if self.proc is not None and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except Exception:
                pass
        try:
            PAUSE_FLAG.unlink()
        except OSError:
            pass
        self.destroy()


def main():
    if "--dry-run" in sys.argv:
        print([name for _label, name in ALL_TASKS])
        return 0

    _prepare_tk_runtime()
    app = LauncherApp()
    if "--smoke" in sys.argv:
        app.after(3000, app.destroy)
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
