import os
import subprocess
import time
from pathlib import Path


def _no_window_kwargs():
    """Windows 下禁止子进程弹出命令行窗口（adb 是控制台程序）。"""
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


class AdbDevice:
    def __init__(self, adb_path="adb", serial="127.0.0.1:16384", package="", activity=""):
        self.adb_path = adb_path
        self.serial = serial
        self.package = package
        self.activity = activity

    def _run(self, args):
        return subprocess.run(
            [self.adb_path, "-s", self.serial] + args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            **_no_window_kwargs(),
        )

    def connect(self):
        return self._run(["connect", self.serial])

    def disconnect(self):
        return self._run(["disconnect", self.serial])

    def devices(self):
        result = self._run(["devices"])
        return [ln for ln in result.stdout.splitlines()[1:] if ln.strip()]

    def is_online(self):
        for line in self.devices():
            parts = line.split()
            if parts and parts[0] == self.serial and len(parts) > 1 and parts[1] == "device":
                return True
        return False

    def tap(self, x, y):
        return self._run(["shell", "input", "tap", str(int(x)), str(int(y))])

    def swipe(self, x1, y1, x2, y2, duration_ms=300):
        return self._run(["shell", "input", "swipe",
                          str(int(x1)), str(int(y1)), str(int(x2)), str(int(y2)), str(int(duration_ms))])

    def key(self, keycode):
        return self._run(["shell", "input", "keyevent", str(keycode)])

    def screencap_bytes(self, retries=4):
        """截屏并返回 PNG 字节；模拟器刚开机画面未就绪时重试。"""
        last_err = None
        for _ in range(retries):
            try:
                result = subprocess.run(
                    [self.adb_path, "-s", self.serial, "exec-out", "screencap", "-p"],
                    capture_output=True,
                    timeout=60,
                    **_no_window_kwargs(),
                )
            except Exception as e:
                last_err = e
                time.sleep(1)
                continue
            if result.returncode == 0 and result.stdout:
                return result.stdout
            last_err = RuntimeError(
                f"screencap 返回空数据/失败: {result.stderr[:200]}")
            time.sleep(1)
        raise RuntimeError(f"截屏失败（已重试 {retries} 次）: {last_err}")

    def screencap_file(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.screencap_bytes())
        return path

    def app_running(self):
        if not self.package:
            return False
        return bool(self._run(["shell", "pidof", self.package]).stdout.strip())

    def start_app(self):
        # 用标准启动器方式启动，am start -n 有时无法把 Unity 游戏带到前台
        if self.package:
            result = self._run(["shell", "monkey", "-p", self.package, "-c",
                                "android.intent.category.LAUNCHER", "1"])
            if result.returncode != 0 and self.activity:
                self._run(["shell", "am", "start", "-n", f"{self.package}/{self.activity}"])
