# -*- coding: utf-8 -*-
"""MuMu 模拟器唤醒：脚本启动时若模拟器未运行，自动用 mumu-cli 拉起并等待上线。"""

import json
import re
import subprocess
import time
from pathlib import Path

from core.adb import _no_window_kwargs

DEFAULT_CLI_PATHS = [
    r"D:\MuMuPlayer-12.0\nx_main\mumu-cli.exe",
]


def _run(cmd, timeout=60):
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              timeout=timeout, **_no_window_kwargs())
    except Exception:
        return None


def find_mumu_cli(cfg):
    """找到 mumu-cli 路径：优先用配置，否则常见安装路径。"""
    configured = str(cfg.get("mumu_cli_path", "") or "").strip()
    if configured:
        path = Path(configured)
        if path.exists():
            return str(path)
    for candidate in DEFAULT_CLI_PATHS:
        if Path(candidate).exists():
            return candidate
    return None


def _serial_port(serial):
    m = re.search(r":(\d+)$", str(serial))
    return int(m.group(1)) if m else None


def find_vmindex(cfg, cli):
    """按 adb 端口找对应的实例索引；找不到则用配置的索引（默认 0）。"""
    port = _serial_port(cfg.get("serial", ""))
    result = _run([cli, "info", "--vmindex", "all"], timeout=30)
    if result is not None and result.returncode == 0:
        try:
            data = json.loads(result.stdout)
            for index, info in data.items():
                if port is not None and info.get("adb_port") == port:
                    return str(index)
        except (ValueError, AttributeError):
            pass
    try:
        return str(int(cfg.get("mumu_vmindex", 0)))
    except (TypeError, ValueError):
        return "0"


def _vm_info(cli, index):
    """查询某个实例的信息；失败返回 None。"""
    result = _run([cli, "info", "--vmindex", index], timeout=30)
    if result is None or result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except ValueError:
        return None


def launch_mumu(cfg):
    """启动 MuMu 模拟器实例；返回是否成功发出启动指令。"""
    cli = find_mumu_cli(cfg)
    if not cli:
        return False
    index = find_vmindex(cfg, cli)
    result = _run([cli, "control", "--vmindex", index, "launch"], timeout=60)
    if result is None or result.returncode != 0:
        # 主程序可能没开，先拉起主程序再试一次
        _run([cli, "main", "launch"], timeout=30)
        time.sleep(3)
        result = _run([cli, "control", "--vmindex", index, "launch"], timeout=60)
    if result is None or result.returncode != 0:
        return False
    # control launch 偶尔会返回成功但实例没真正起来：确认进程已启动
    for _ in range(15):
        time.sleep(2)
        info = _vm_info(cli, index)
        if info is not None and info.get("is_process_started"):
            return True
    return True  # 没确认到也先返回，交给 wait_mumu_online 持续重试


def wait_mumu_online(cfg, device, timeout=180, on_status=None):
    """等待模拟器 adb 上线并连接；每 30 秒重新发一次启动指令，防止第一次没生效。"""
    deadline = time.time() + timeout
    last_launch = 0.0
    last_status = 0.0
    cli = find_mumu_cli(cfg)
    index = find_vmindex(cfg, cli) if cli else None
    while time.time() < deadline:
        device.disconnect()
        time.sleep(1)
        device.connect()
        time.sleep(1)
        if device.is_online():
            return True
        # 每 30 秒重新拉起一次实例
        now = time.time()
        if cli and index is not None and now - last_launch > 30:
            last_launch = now
            _run([cli, "control", "--vmindex", index, "launch"], timeout=60)
        if on_status is not None and now - last_status > 30:
            last_status = now
            on_status(int(now - (deadline - timeout)))
        time.sleep(4)
    return device.is_online()
