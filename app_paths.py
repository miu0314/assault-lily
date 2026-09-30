# -*- coding: utf-8 -*-
"""统一的路径解析：源码运行 vs PyInstaller 打包出来的 exe。

打包后代码被收进 exe 的 ``_internal`` 目录，``Path(__file__)`` 指向的位置
不再是项目根目录。assets / config / screenshots 都必须按「程序所在目录」
来找，所以统一走这里，不要在别处直接写 ``Path(__file__)``。
"""

import sys
from pathlib import Path

__version__ = "0.2.0"


def is_frozen() -> bool:
    """是否运行在 PyInstaller 打包出来的 exe 里。"""
    return bool(getattr(sys, "frozen", False))


def base_dir() -> Path:
    """程序根目录：源码运行时是项目目录，打包后是 exe 所在目录。"""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent
