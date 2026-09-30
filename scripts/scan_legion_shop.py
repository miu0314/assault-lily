# -*- coding: utf-8 -*-
"""扫描军团兑换所并更新目录（供启动器「刷新目录」按钮调用）。"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app_paths
from core.adb import AdbDevice
from core.config import Config
from core.context import GameContext
from core.logger import Logger
from tasks.legion_exchange import ExchangeLegionItems


def main():
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.json"
    cfg = Config(config_path)
    logger = Logger(cfg.get("lang", "zh_CN"))
    base_dir = app_paths.base_dir()
    device = AdbDevice(cfg.get("adb_path"), cfg.get("serial"),
                       cfg.get("package"), cfg.get("activity"))
    if not device.is_online():
        device.connect()
        time.sleep(1)
    ctx = GameContext(cfg, logger, device, base_dir=base_dir)
    task = ExchangeLegionItems()
    if not task._enter_shop(ctx):
        print("无法进入兑换所，刷新失败")
        return 1
    task._scan_and_record(ctx)
    task._back_home(ctx)
    print("兑换所目录已更新")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
