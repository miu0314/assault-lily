# -*- coding: utf-8 -*-
"""运行库引导。

Codex 的运行环境（codex-primary-runtime）更新时会整体替换目录，以前用 pip
装进这台解释器的第三方库（cv2 / OCR 等）会被一起清掉，脚本就会直接启动失败
（启动器是 pythonw 无窗口运行，所以看不到报错）。

这里做两件事：

1. 把项目内的 deps 目录加到 sys.path 最前面 —— 它不随运行环境更新消失；
2. cv2 / rapidocr_onnxruntime 仍然缺失时，自动把它们安装到 deps。
"""

import importlib
import importlib.util
import subprocess
import sys
from pathlib import Path

import app_paths

BASE_DIR = app_paths.base_dir()
DEPS_DIR = BASE_DIR / "deps"
REQUIREMENTS = BASE_DIR / "requirements.txt"

# 缺了会直接崩的库（main.py / tasks 里 import cv2、rapidocr_onnxruntime）
REQUIRED_MODULES = ("cv2", "rapidocr_onnxruntime")


def add_local_deps():
    """把项目内 deps 放到 sys.path 最前面，优先使用项目内的库。"""
    if app_paths.is_frozen():
        return False
    if not DEPS_DIR.is_dir():
        return False
    path = str(DEPS_DIR)
    if path not in sys.path:
        sys.path.insert(0, path)
    importlib.invalidate_caches()
    return True


def missing_modules():
    return [name for name in REQUIRED_MODULES
            if importlib.util.find_spec(name) is None]


def install_local_deps(log=print, timeout=1800):
    """把 requirements.txt 装进项目内 deps（不写运行时目录）。"""
    if not REQUIREMENTS.is_file():
        log("找不到 %s，无法自动补装运行库。" % REQUIREMENTS)
        return False
    log("正在补装运行库（首次约 1-3 分钟，之后启动就快了）...")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    cmd = [sys.executable, "-m", "pip", "install",
           "--target", str(DEPS_DIR),
           "-r", str(REQUIREMENTS),
           "--quiet", "--no-warn-script-location"]
    try:
        result = subprocess.run(cmd, creationflags=flags, timeout=timeout)
    except Exception as exc:  # 安装失败只提示，不阻断后续流程
        log("运行库安装失败：%s" % exc)
        return False
    if result.returncode != 0:
        log("运行库安装失败（pip 退出码 %s）。" % result.returncode)
        return False
    add_local_deps()
    return True


def ensure_deps(log=print, auto_install=True):
    """保证 cv2 / OCR 可用；返回 True 表示可以正常导入。"""
    if app_paths.is_frozen():
        # 打包版已经把依赖收进 exe，不需要（也不能）再 pip 安装
        return True
    add_local_deps()
    missing = missing_modules()
    if not missing:
        return True
    if not auto_install:
        log("缺少运行库：%s" % missing)
        return False
    log("缺少运行库 %s，正在自动补装。" % missing)
    install_local_deps(log=log)
    add_local_deps()
    still = missing_modules()
    if still:
        log("运行库仍然缺失：%s" % still)
        return False
    log("运行库补装完成。")
    return True
