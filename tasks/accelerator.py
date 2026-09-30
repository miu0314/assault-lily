# -*- coding: utf-8 -*-
"""启动加速器：识别指定的加速器进程，未运行则按配置路径启动它。"""

import os
import subprocess
import time
from pathlib import Path

from core.task import Task


def _no_window_kwargs():
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


class LaunchAccelerator(Task):
    """先启动游戏，再快速切到加速器连接，最后切回游戏。

    游戏进程从始至终不停，避免“先关游戏、连加速器、再重启游戏”的额外等待。
    """

    def __init__(self):
        super().__init__(name="启动加速器", pre_times=1, post_times=1)
        self.completed = False

    def on_run(self, ctx):
        self.completed = False
        path = str(ctx.config.get("accelerator_path", "") or "").strip()
        process = str(ctx.config.get("accelerator_process", "") or "").strip()
        package = str(ctx.config.get("accelerator_package", "") or "").strip()

        try:
            # 先让游戏开始加载，加速器只会临时占用前台。
            self._start_game(ctx)
            # 优先：模拟器内安装的加速器 App（按包名）
            if package:
                self.completed = self._launch_package(ctx, package)
                return self.completed

            if not path and not process:
                ctx.logger.info("未配置加速器（路径/进程名都为空），跳过")
                self.completed = True
                return True

            # 识别：进程已在运行则无需再启动
            if process and self._process_running(process):
                ctx.logger.info(f"加速器已在运行（{process}）")
                self.completed = True
                return True

            if not path:
                ctx.logger.warn("加速器未在运行，且未配置启动路径（accelerator_path），跳过")
                self.completed = False
                return False

            exe = Path(path)
            if not exe.exists():
                ctx.logger.warn(f"加速器程序不存在：{path}")
                self.completed = False
                return False

            ctx.logger.info(f"启动加速器：{path}")
            try:
                subprocess.Popen([str(exe)], cwd=str(exe.parent))
            except Exception as e:
                ctx.logger.warn(f"启动加速器失败：{e}")
                self.completed = False
                return False

            if process:
                for _ in range(12):
                    time.sleep(2)
                    if self._process_running(process):
                        ctx.logger.info(f"加速器已启动（{process}）")
                        self.completed = True
                        return True
                ctx.logger.warn("加速器进程未检测到（可能进程名与配置不一致）")
                self.completed = False
                return False
            self.completed = True
            return True
        finally:
            # 加速器只占用前台一小段时间；不关闭、不 force-stop 游戏。
            self._return_to_game(ctx)

    def post_condition(self, ctx):
        return self.completed

    @staticmethod
    def _start_game(ctx):
        """让游戏先进入启动状态；进程已在运行时不重启。"""
        if ctx.device.app_running():
            ctx.logger.info("游戏已在运行，保持游戏进程并打开加速器")
        else:
            ctx.logger.info("先启动游戏，随后立即打开加速器")
            ctx.device.start_app()
        # 给 Android 一个很短的启动时间，然后就切到加速器。
        time.sleep(1)

    @staticmethod
    def _return_to_game(ctx):
        """把游戏带回前台；正常情况下只息屏切换，不重启进程。"""
        try:
            was_running = ctx.device.app_running()
            ctx.device.start_app()
            if was_running:
                ctx.logger.info("加速器操作完成，已切回游戏（进程未重启）")
            else:
                ctx.logger.warn("切回时游戏进程不在，已重新拉起游戏")
            time.sleep(1)
        except Exception as e:
            ctx.logger.warn(f"切回游戏失败：{e}")

    def _launch_package(self, ctx, package):
        """在模拟器里打开加速器 App，并快速点击「连接」。"""
        installed = ctx.device._run(["shell", "pm", "list", "packages", package])
        if package not in installed.stdout:
            ctx.logger.warn(f"模拟器里未安装该加速器（{package}）")
            return False
        ctx.logger.info(f"在模拟器中打开加速器：{package}")
        result = ctx.device._run(
            ["shell", "monkey", "-p", package, "-c",
             "android.intent.category.LAUNCHER", "1"])
        if result.returncode != 0:
            ctx.logger.warn("打开加速器失败")
            return False
        ctx.logger.info("加速器已打开，快速检测连接按钮")

        # 先检查已连接状态，避免已连接时白等。
        for attempt in range(12):
            time.sleep(0.8 if attempt == 0 else 1)
            ctx.screenshot()
            if self._connected_button(ctx) is not None:
                ctx.logger.info("加速器已经连接，准备切回游戏")
                return True
            found = ctx.find("accelerator/btn_connect.png", threshold=0.85)
            if found is None:
                continue
            (bx, by), score = found
            if bx < 1000 or by < 600:
                continue
            ctx.logger.info(f"快速点击「连接」({bx},{by})")
            ctx.click(bx, by, sleeptime=0.8)
            ctx.logger.info("已点击连接，等待按钮变为「断开」")
            for _ in range(20):
                time.sleep(0.8)
                ctx.screenshot()
                if self._connected_button(ctx) is not None:
                    ctx.logger.info("加速器已连接，准备切回游戏")
                    return True
            ctx.logger.warn("点击「连接」后未确认到已连接状态，继续返回游戏")
            return False
        ctx.logger.info("未找到「连接」按钮（可能已连接或界面不同），返回游戏")
        return False

    @staticmethod
    def _connected_button(ctx):
        """找到「断开」按钮时返回坐标；这表示加速器已连接。"""
        found = ctx.find("accelerator/btn_disconnect.png", threshold=0.85)
        if found is None:
            return None
        (bx, by), _score = found
        if bx >= 1000 and by >= 600:
            return (bx, by)
        return None

    @staticmethod
    def _process_running(process):
        """按进程名识别指定软件是否在运行。"""
        name = str(process).strip().lower()
        if name.endswith(".exe"):
            name = name[:-4]
        if not name:
            return False
        try:
            cmd = (f"Get-Process -Name '{name}' -ErrorAction SilentlyContinue | "
                   "Measure-Object | Select-Object -ExpandProperty Count")
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", cmd],
                capture_output=True, text=True, errors="replace",
                timeout=30, **_no_window_kwargs())
            if result.returncode != 0:
                return False
            count = result.stdout.strip()
            try:
                return int(count) > 0
            except ValueError:
                return bool(count)
        except Exception:
            return False


class DisconnectAccelerator(Task):
    """断开加速器：打开加速器 App，点「暂停/断开」（仅已连接时点击）。"""

    def __init__(self):
        super().__init__(name="断开加速器", pre_times=1, post_times=1)
        self.completed = False

    def on_run(self, ctx):
        self.completed = False
        package = str(ctx.config.get("accelerator_package", "") or "").strip()
        if not package:
            ctx.logger.info("未配置加速器包名，跳过")
            self.completed = True
            return True
        installed = ctx.device._run(["shell", "pm", "list", "packages", package])
        if package not in installed.stdout:
            ctx.logger.warn(f"模拟器里未安装该加速器（{package}）")
            return False
        ctx.device._run(
            ["shell", "monkey", "-p", package, "-c",
             "android.intent.category.LAUNCHER", "1"])
        for _ in range(8):
            time.sleep(2)
            ctx.screenshot()
            found = ctx.find("accelerator/btn_disconnect.png", threshold=0.85)
            if found is not None:
                (bx, by), score = found
                if bx >= 1000 and by >= 600:
                    ctx.logger.info(f"点击「断开」({bx},{by})")
                    ctx.click(bx, by, sleeptime=3)
                    ctx.logger.info("已点击断开")
                    self.completed = True
                    return True
        ctx.logger.info("未找到「断开」按钮（可能已断开），跳过")
        self.completed = True
        return True

    def post_condition(self, ctx):
        return self.completed
