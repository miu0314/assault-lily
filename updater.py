# -*- coding: utf-8 -*-
"""启动器内置更新器：查 GitHub Release → 下载 → 覆盖更新。

两条更新路径：

- **程序更新**（完整包，约 100MB）：下载 zip → 生成一次性 bat → 关掉程序 →
  解压覆盖 exe 和 _internal → 重新启动。覆盖时会跳过 config.json /
  config_selected.json，用户设置不会被冲掉。
- **资源更新**（小包，1~2MB）：只替换 assets/ 识别模板，不用重启。

版本怎么比：程序版本用 app_paths.__version__；资源版本用程序目录下的
assets_version.txt（发布时由 package.py 按 assets 目录内容算出来）。
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

REPO = "miu0314/assault-lily"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
USER_AGENT = "AssaultLilyBot-updater"

FULL_PATTERN = re.compile(r"^AssaultLilyBot-v([\d.]+)-win-x64\.zip$", re.I)
ASSETS_PATTERN = re.compile(r"^AssaultLilyBot-assets-([A-Za-z0-9._-]+)\.zip$", re.I)

STAMP_FILE = "assets_version.txt"
# 覆盖更新时绝不动这些（用户自己的设置）
KEEP_FILES = ("config.json", "config_selected.json")


class UpdateGuard:
    """一次只允许一个更新动作（检查 / 下载），防止重复点击叠出多个弹窗。

    实测 2026-09-30：弹窗弹出时按钮又被设回可点，Tk 弹窗会跑嵌套事件循环，
    再点一下就会再起一次检查 → 又叠一层弹窗，点「否」只关掉最里面那层。
    """

    def __init__(self):
        self.state = "idle"

    @property
    def busy(self):
        return self.state != "idle"

    def try_begin(self, state):
        if self.busy:
            return False
        self.state = state
        return True

    def finish(self):
        self.state = "idle"


class UpdateError(Exception):
    """查更新 / 下载失败。"""


@dataclass
class Asset:
    name: str
    url: str
    size: int = 0


@dataclass
class ReleaseInfo:
    tag: str
    version: tuple
    page_url: str
    full: Asset = None
    assets: Asset = None
    assets_stamp: str = None
    raw: dict = field(default_factory=dict)


def parse_version(text):
    """'v1.2.3' → (1, 2, 3)。解析不出来就当 (0,)。"""
    numbers = re.findall(r"\d+", str(text))
    return tuple(int(n) for n in numbers) if numbers else ()


def is_newer(latest, current):
    """latest 是否比 current 新（长度不同时短的补 0）。"""
    latest = tuple(latest or ())
    current = tuple(current or ())
    length = max(len(latest), len(current))
    left = latest + (0,) * (length - len(latest))
    right = current + (0,) * (length - len(current))
    return left > right


def parse_release(data):
    info = ReleaseInfo(
        tag=str(data.get("tag_name") or ""),
        version=parse_version(data.get("tag_name")),
        page_url=str(data.get("html_url") or ""),
        raw=data,
    )
    for item in data.get("assets") or []:
        name = str(item.get("name") or "")
        asset = Asset(name=name,
                      url=str(item.get("browser_download_url") or ""),
                      size=int(item.get("size") or 0))
        match = FULL_PATTERN.match(name)
        if match:
            info.full = asset
            continue
        match = ASSETS_PATTERN.match(name)
        if match:
            info.assets = asset
            info.assets_stamp = match.group(1)
    return info


def local_assets_stamp(base_dir):
    """读程序目录里的 assets_version.txt；没有就返回空串。"""
    path = Path(base_dir) / STAMP_FILE
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def decide(release, current_version, local_stamp):
    """返回 (要做的事, 给人看的说明)。没得更新时第一项是 None。"""
    if release is None:
        return None, "检查失败"
    if is_newer(release.version, parse_version(current_version)):
        return "program", f"发现新版本 v{'.'.join(str(n) for n in release.version)}（当前 v{current_version}）"
    if release.assets_stamp and release.assets_stamp != (local_stamp or ""):
        return "assets", f"识别模板有更新（{local_stamp or '未记录'} → {release.assets_stamp}）"
    return None, "已是最新版本"


def fetch_latest(timeout=8):
    """查最新 Release；失败抛 UpdateError。"""
    request = urllib.request.Request(
        API_LATEST,
        headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise UpdateError(f"连接 GitHub 失败：{exc}") from exc
    return parse_release(payload)


CACHE_FILE = "update_cache.json"
CACHE_HOURS = 6


def _load_cache(path):
    """读缓存里的 Release（6 小时内不重复问 GitHub，省得被限流）。"""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if time.time() - float(data.get("checked_at") or 0) > CACHE_HOURS * 3600:
        return None
    release = data.get("release")
    return parse_release(release) if isinstance(release, dict) else None


def _save_cache(path, release):
    try:
        Path(path).write_text(json.dumps({"checked_at": time.time(),
                                          "release": release.raw},
                                         ensure_ascii=False),
                              encoding="utf-8")
    except OSError:
        pass


def _check_once(current_version, base_dir, timeout):
    try:
        release = fetch_latest(timeout=timeout)
    except UpdateError as exc:
        return None, None, str(exc)
    _save_cache(Path(base_dir) / CACHE_FILE, release)
    kind, message = decide(release, current_version, local_assets_stamp(base_dir))
    return release, kind, message


def check(current_version, base_dir, timeout=8, hard_timeout=None, force=False):
    """查一次更新，返回 (release, kind, message)。

    网络失败不抛异常；再用一个看门线程加硬超时——某些网络环境下 DNS 或代理
    会卡很久，不能让启动器一直停在「检查中」。
    """
    hard = hard_timeout or (timeout + 7)
    cache_path = Path(base_dir) / CACHE_FILE
    if not force:
        cached = _load_cache(cache_path)
        if cached is not None:
            kind, message = decide(cached, current_version,
                                    local_assets_stamp(base_dir))
            return cached, kind, message
    box = {}

    def worker():
        box["result"] = _check_once(current_version, base_dir, timeout)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(hard)
    if "result" in box:
        return box["result"]
    return None, None, f"查询超时（超过 {hard} 秒），请检查网络或代理设置"


def download(url, dest, on_progress=None, timeout=60):
    """下载到 dest；on_progress(已下载, 总大小) 用于显示进度。"""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    # 先写 .part 再改名：中途断线不会留下半个包被当成完整更新包去覆盖程序
    part = dest.with_name(dest.name + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response, \
                open(part, "wb") as handle:
            total = int(response.headers.get("Content-Length") or 0)
            done = 0
            while True:
                chunk = response.read(262144)
                if not chunk:
                    break
                handle.write(chunk)
                done += len(chunk)
                if on_progress:
                    on_progress(done, total)
    except (urllib.error.URLError, OSError) as exc:
        try:
            part.unlink()
        except OSError:
            pass
        raise UpdateError(f"下载失败：{exc}") from exc
    os.replace(part, dest)
    return dest


def build_update_script(zip_path, target_dir, exe_path, log_path,
                        folder_name="AssaultLilyBot"):
    """生成一次性更新脚本（bat）。

    运行中的 exe 没法自己覆盖自己，只能等进程退出后由外部脚本替换，
    所以这里写一个 bat：等退出 → 解压 → 覆盖（跳过用户配置）→ 重启 → 自删。
    脚本正文用英文，避免不同机器代码页下乱码。
    """
    exe_name = Path(exe_path).name
    keep = " ".join(KEEP_FILES)
    template = '''@echo off
chcp 65001 >nul
setlocal
set "ZIP=__ZIP__"
set "TARGET=__TARGET__"
set "EXE=__EXE__"
set "LOG=__LOG__"
set "TMP=__TMP__"
echo [update] %date% %time% waiting for app to exit... >> "%LOG%"
:wait
tasklist /FI "IMAGENAME eq __EXE_NAME__" 2>nul | find /I "__EXE_NAME__" >nul
if not errorlevel 1 (
  ping -n 2 127.0.0.1 >nul
  goto wait
)
echo [update] %date% %time% extracting "%ZIP%" >> "%LOG%"
if exist "%TMP%" rd /s /q "%TMP%"
mkdir "%TMP%"
where tar >nul 2>nul
if %errorlevel%==0 (
  tar -xf "%ZIP%" -C "%TMP%" >> "%LOG%" 2>&1
) else (
  powershell -NoProfile -ExecutionPolicy Bypass -Command "Expand-Archive -LiteralPath '%ZIP%' -DestinationPath '%TMP%' -Force" >> "%LOG%" 2>&1
)
set "SRC=%TMP%\\__FOLDER__"
if not exist "%SRC%" set "SRC=%TMP%"
echo [update] %date% %time% copying into "%TARGET%" >> "%LOG%"
robocopy "%SRC%" "%TARGET%" /E /XF __KEEP__ /NFL /NDL /NJH /NJS /NP >> "%LOG%" 2>&1
del /q "%ZIP%" >nul 2>nul
rd /s /q "%TMP%" >nul 2>nul
echo [update] %date% %time% done, restarting >> "%LOG%"
start "" "%EXE%"
endlocal
del "%~f0"
'''
    tmp_dir = Path(tempfile.gettempdir()) / "assault_lily_update"
    return (template
            .replace("__ZIP__", str(zip_path))
            .replace("__TARGET__", str(target_dir))
            .replace("__EXE__", str(exe_path))
            .replace("__LOG__", str(log_path))
            .replace("__TMP__", str(tmp_dir))
            .replace("__FOLDER__", str(folder_name))
            .replace("__EXE_NAME__", exe_name)
            .replace("__KEEP__", keep))


def apply_full_update(zip_path, target_dir, exe_path, log_path=None,
                      folder_name="AssaultLilyBot"):
    """写好 bat 并在后台启动；调用方随后应立即退出程序。"""
    target_dir = Path(target_dir)
    log_path = Path(log_path) if log_path else target_dir / "update.log"
    script = build_update_script(zip_path, target_dir, exe_path, log_path,
                                 folder_name=folder_name)
    bat_path = Path(tempfile.gettempdir()) / f"assault_lily_update_{os.getpid()}.bat"
    bat_path.write_text(script, encoding="utf-8")
    flags = 0
    for name in ("CREATE_NO_WINDOW", "CREATE_NEW_PROCESS_GROUP"):
        flags |= getattr(subprocess, name, 0)
    subprocess.Popen(["cmd", "/c", str(bat_path)], creationflags=flags,
                     close_fds=True)
    return bat_path


def _safe_members(archive):
    """过滤掉越界路径（zip slip）和目录项。"""
    for info in archive.infolist():
        if info.is_dir():
            continue
        name = info.filename.replace("\\", "/")
        if name.startswith("/") or re.match(r"^[A-Za-z]:", name):
            continue
        parts = [p for p in name.split("/") if p not in ("", ".", "..")]
        if not parts or ".." in name.split("/"):
            continue
        yield info, "/".join(parts)


def apply_assets_pack(pack_path, base_dir):
    """解压资源包到程序目录（assets/ + assets_version.txt），返回写入文件数。"""
    base_dir = Path(base_dir)
    written = 0
    with zipfile.ZipFile(pack_path) as archive:
        for info, name in _safe_members(archive):
            target = base_dir / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            written += 1
    return written


def pack_stamp(folder):
    """按目录内容算资源版本号（前 8 位），用于判断模板有没有变。"""
    digest = hashlib.sha256()
    root = Path(folder)
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name == STAMP_FILE:
            continue
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                # 行尾归一化：git 在不同机器上检出成 CRLF/LF，内容其实没变，
                # 不归一化会导致同一个资源在不同环境算出不同版本号。
                digest.update(chunk.replace(b"\r\n", b"\n"))
    return digest.hexdigest()[:8]