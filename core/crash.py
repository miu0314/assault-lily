# -*- coding: utf-8 -*-
"""游戏闪退监控：后台检查游戏进程，消失时通知主流程恢复。"""

import threading
import time


class GameCrashError(Exception):
    """游戏进程消失（闪退）时抛出，由主流程捕获并恢复。"""


class GameMonitor:
    """后台线程监控游戏进程；闪退时置位 crash_event。"""

    def __init__(self, device, package, interval=8):
        self.device = device
        self.package = package
        self.interval = interval
        self.crash_event = threading.Event()
        self._stop = threading.Event()
        self._thread = None
        self._seen_running = False

    def start(self):
        if self._thread is not None:
            if self._thread.is_alive():
                return
            self._thread = None
        self._stop = threading.Event()
        self._seen_running = False
        self._thread = threading.Thread(target=self._run, args=(self._stop,), daemon=True)
        self._thread.start()

    def stop(self):
        stop_event = self._stop
        thread = self._thread
        stop_event.set()
        if thread is not None:
            thread.join(timeout=5)
        # adb 查询可能暂时阻塞；线程仍活着时保留句柄，避免 start()
        # 再开第二个监控线程。下一次 start 会在旧线程退出后接管。
        if self._thread is thread and not thread.is_alive():
            self._thread = None

    def clear(self):
        self.crash_event.clear()

    def _run(self, stop_event):
        while not stop_event.is_set():
            try:
                running = self._game_running()
                if running is True:
                    self._seen_running = True
                elif running is False and self._seen_running:
                    self.crash_event.set()
            except Exception:
                pass
            stop_event.wait(self.interval)

    def _game_running(self):
        """返回 True=运行中，False=进程不存在，None=adb异常无法判断。"""
        if not self.device.is_online():
            return None
        result = self.device._run(["shell", "pidof", self.package])
        if result is None:
            return None
        stderr = (result.stderr or "").lower()
        if result.returncode != 0 and stderr:
            # adb 临时失败/设备掉线不能当作游戏闪退。
            return None
        if result.returncode != 0:
            return False
        return bool(result.stdout.strip())
