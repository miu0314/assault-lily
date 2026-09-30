"""冷启动调试：运行 LaunchGame + WaitForHome，并保存每步截图。

用法：python scripts/cold_start_debug.py [config.json]
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.adb import AdbDevice
from core.config import Config
from core.context import GameContext
from core.logger import Logger
from tasks import get_task


def main():
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.json"
    cfg = Config(config_path)
    logger = Logger(cfg.get("lang", "zh_CN"))
    base_dir = Path(__file__).resolve().parents[1]
    device = AdbDevice(cfg.get("adb_path"), cfg.get("serial"),
                       cfg.get("package"), cfg.get("activity"))
    if not device.is_online():
        device.connect()
        time.sleep(1)

    ctx = GameContext(cfg, logger, device, base_dir=base_dir)
    shot_dir = ctx.screenshots_dir / "cold_debug"
    shot_dir.mkdir(parents=True, exist_ok=True)

    # 先强制退出游戏，模拟真正的冷启动
    from core.adb import _no_window_kwargs, subprocess
    subprocess.run([cfg.get("adb_path"), "-s", cfg.get("serial"),
                    "shell", "am", "force-stop", cfg.get("package")],
                   capture_output=True, timeout=30, **_no_window_kwargs())
    time.sleep(2)

    def save_debug():
        name = f"step_{int(time.time()):d}.png"
        import cv2
        cv2.imwrite(str(shot_dir / name), ctx._last_screen)
        print(f"  [debug] {name}", flush=True)

    # 保存每一帧截图：包装 screenshot 方法
    orig_screenshot = ctx.screenshot

    def debug_screenshot(save=False, name=None):
        img = orig_screenshot(save=save, name=name)
        import cv2
        fname = f"frame_{int(time.time()):d}.png"
        cv2.imwrite(str(shot_dir / fname), img)
        return img

    ctx.screenshot = debug_screenshot

    logger.info("开始冷启动调试")
    for task_name in cfg.get("task_list", ["LaunchGame", "WaitForHome"]):
        logger.info(f"执行任务: {task_name}")
        try:
            get_task(task_name).run(ctx)
        except Exception as e:
            logger.error(f"任务 {task_name} 异常: {e}")
            break

    logger.info("冷启动调试结束")


if __name__ == "__main__":
    main()
