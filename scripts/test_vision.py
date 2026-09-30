import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.adb import AdbDevice
from core.config import Config
from core.context import GameContext
from core.logger import Logger


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("templates", nargs="*")
    parser.add_argument("--config", default="config.json")
    args = parser.parse_args()
    cfg = Config(args.config)
    logger = Logger(cfg.get("lang"))
    device = AdbDevice(cfg.get("adb_path"), cfg.get("serial"))
    if not device.is_online():
        device.connect()
    ctx = GameContext(cfg, logger, device, base_dir=Path(__file__).resolve().parents[1])
    ctx.screenshot(save=True, name="vision_test.png")
    home_dir = ctx.assets_dir / "home"
    names = args.templates or [p.relative_to(ctx.assets_dir).as_posix() for p in home_dir.glob("*.png")]
    print(f"画面: {ctx._last_screen.shape[1]}x{ctx._last_screen.shape[0]}")
    for name in names:
        r = ctx.find(name)
        if r is None:
            print(f"  [FAIL] {name}")
        else:
            (x, y), score = r
            print(f"  [OK] {name}: {score:.3f} ({x},{y})")


if __name__ == "__main__":
    main()
