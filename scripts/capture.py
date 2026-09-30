import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.adb import AdbDevice
from core.config import Config


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("name", nargs="?", default=None)
    parser.add_argument("--config", default="config.json")
    args = parser.parse_args()
    cfg = Config(args.config)
    device = AdbDevice(cfg.get("adb_path"), cfg.get("serial"))
    if not device.is_online():
        device.connect()
        time.sleep(1)
    out_dir = Path("screenshots")
    out_dir.mkdir(exist_ok=True)
    name = args.name or time.strftime("%Y%m%d_%H%M%S.png")
    path = device.screencap_file(out_dir / name)
    print(f"截图已保存: {path.resolve()}")


if __name__ == "__main__":
    main()
