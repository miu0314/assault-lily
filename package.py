# -*- coding: utf-8 -*-
"""打包成 Windows 绿色版（PyInstaller onedir）并压成 zip。

用法：
    pip install -r requirements.txt -r requirements-build.txt
    python package.py

产物：
    dist/AssaultLilyBot/                      解压即用的文件夹
    dist/AssaultLilyBot-v<版本>-win-x64.zip   发 Release 用

为什么用 onedir 而不是 onefile：onefile 每次启动都要把 200MB+ 解压到临时
目录，启动慢，杀软也更容易拦。assets / config 放在 exe 外面，游戏更新后
换模板不用重新打包。
"""

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DIST_DIR = BASE_DIR / "dist"
BUILD_DIR = BASE_DIR / "build"
APP_NAME = "AssaultLilyBot"

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app_paths import __version__  # noqa: E402

USAGE_TEXT = """突击莉莉脚本 v{version}（Windows 绿色版）
========================================

1. 解压整个文件夹，别只把 exe 拖出来（同目录的 _internal、assets 都要留着）
2. 双击 AssaultLilyBot.exe 打开启动器
3. 第一次用先到「连接与设置」确认：模拟器 adb 路径、设备地址（默认 MuMu 的 127.0.0.1:16384）
4. 勾选要跑的功能，点「开始运行」

出问题怎么办
------------
- 双击没反应：看同目录的 launcher_error.log
- 想确认环境是否正常：命令行执行  AssaultLilyBot.exe --selftest  然后看 selftest.log
- Windows 提示「未知发布者」或杀软误报：本项目没有买代码签名证书，添加信任即可
- 游戏更新后如果卡在某个界面，多半是识别模板过期，到 Releases 下载新版本

配置文件
--------
config.json 是你的设置，config_selected.json 是启动器当前使用的流程；
两个文件都可以直接编辑，升级时覆盖 exe 和 _internal 就行，别覆盖 config。
"""


def check_pyinstaller():
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("缺少 PyInstaller：先执行 pip install -r requirements-build.txt")
        return False
    return True


def build():
    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onedir",
        "--windowed",
        "--name", APP_NAME,
        "--distpath", str(DIST_DIR),
        "--workpath", str(BUILD_DIR / "pyinstaller"),
        "--specpath", str(BUILD_DIR),
        "--collect-all", "rapidocr_onnxruntime",
        "--collect-all", "onnxruntime",
        str(BASE_DIR / "packaging_entry.py"),
    ]
    print("开始打包：")
    print("  " + " ".join(args))
    subprocess.run(args, check=True, cwd=str(BASE_DIR))


def stage(app_dir):
    """把 exe 外面必须随包一起给的文件放进去。"""
    shutil.copytree(BASE_DIR / "assets", app_dir / "assets", dirs_exist_ok=True)
    for name in ("legion_shop_catalog.json", "README.md"):
        shutil.copy2(BASE_DIR / name, app_dir / name)
    shutil.copy2(BASE_DIR / "config.example.json", app_dir / "config.json")
    (app_dir / "使用说明.txt").write_text(
        USAGE_TEXT.format(version=__version__), encoding="utf-8")


def make_zip(app_dir):
    zip_path = DIST_DIR / f"{APP_NAME}-v{__version__}-win-x64.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in sorted(app_dir.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(app_dir.parent))
    return zip_path


def folder_size(path):
    total = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    return f"{total / 1024 / 1024:.1f} MB"


def main():
    if not check_pyinstaller():
        return 1
    build()
    app_dir = DIST_DIR / APP_NAME
    exe_path = app_dir / f"{APP_NAME}.exe"
    if not exe_path.exists():
        print(f"打包失败：找不到 {exe_path}")
        return 1
    stage(app_dir)
    zip_path = make_zip(app_dir)
    print("")
    print(f"文件夹：{app_dir}（{folder_size(app_dir)}）")
    print(f"压缩包：{zip_path}（{zip_path.stat().st_size / 1024 / 1024:.1f} MB）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
