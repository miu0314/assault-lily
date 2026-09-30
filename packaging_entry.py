# -*- coding: utf-8 -*-
"""PyInstaller 打包入口。

打包版只有一个 exe（没有 python.exe 可以拿去跑 .py 文件），所以启动器要
拉起「主程序」「扫兑换所目录」时，是带隐藏参数重启自己。这里负责把参数
分发到对应入口，另外提供 ``--selftest`` 自检（确认打包进来的库和 OCR 模型
能正常加载）。

源码模式仍然直接跑 ``launcher.py`` / ``main.py``，不受影响。
"""

import io
import os
import sys

FLAGS = {
    "--run-main": "main",
    "--scan-shop": "scan_shop",
    "--selftest": "selftest",
    "--check-update": "check_update",
    "--update": "update",
}


class SafeStream:
    """写不出去就静音的输出流代理（包在真实 stdout / stderr 外面）。

    2026-09-30 实测：Codex 里用 `& AssaultLilyBot.exe --run-main ... | Tee-Object`
    时 Tee 先失败，管道读端没了 → exe 里第一次 `print` 抛
    `OSError: [Errno 22] Invalid argument` → 窗口版打包程序弹
    「Unhandled exception in script」、任务当场中断。
    日志/提示写不出去不该让程序崩，所以这里把写失败吞掉（之后一直静音）。
    """

    _DEAD_ERRORS = (OSError, ValueError)

    def __init__(self, stream):
        self._stream = stream
        self._broken = False

    def write(self, data):
        if self._broken:
            return 0
        try:
            return self._stream.write(data)
        except self._DEAD_ERRORS:
            self._broken = True
            return 0

    def writelines(self, lines):
        for line in lines:
            self.write(line)

    def flush(self):
        if self._broken:
            return
        try:
            self._stream.flush()
        except self._DEAD_ERRORS:
            self._broken = True

    def __getattr__(self, name):
        return getattr(self._stream, name)


def ensure_std_streams():
    """把 sys.stdout/stderr 接回真实句柄。

    ``--windowed`` 打包后 Python 拿不到控制台，sys.stdout 默认是 None，
    启动器用管道收日志时就什么都看不到。父进程给了管道的话这里能接回去；
    真的没有（用户直接双击）就丢到 devnull。
    """
    for name, fd in (("stdout", 1), ("stderr", 2)):
        stream = getattr(sys, name, None)
        if stream is None:
            try:
                stream = io.open(fd, "w", encoding="utf-8", errors="replace",
                                 closefd=False)
            except OSError:
                stream = open(os.devnull, "w", encoding="utf-8")
            setattr(sys, name, SafeStream(stream))
            continue
        # 控制台可能是 cp1252/GBK：编不出来的字符替换掉，别让 print 崩掉整个程序
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass
        setattr(sys, name, SafeStream(stream))


def resolve_command(argv):
    """把命令行参数解析成 (动作, 剩余参数)；纯函数，方便单测。"""
    args = list(argv[1:])
    for flag, action in FLAGS.items():
        if flag in args:
            index = args.index(flag)
            return action, args[:index] + args[index + 1:]
    return "launcher", args


def resolve_relative_path(value, base):
    """相对路径按「程序目录」解析。

    打包版可能从任意工作目录启动（快捷方式、别的盘符），按 cwd 找 config.json
    会直接报「配置文件不存在」，所以相对路径统一以 exe 所在目录为基准。
    """
    from pathlib import Path

    path = Path(value)
    if path.is_absolute():
        return value
    candidate = Path(base) / path
    return str(candidate) if candidate.exists() else value


def fix_argv(args):
    """把第一个参数（配置文件路径）里的相对路径补成绝对路径。"""
    import app_paths

    args = list(args)
    if args and not args[0].startswith("-"):
        args[0] = resolve_relative_path(args[0], app_paths.base_dir())
    return args


def run_main(args):
    import main as main_module

    sys.argv = [sys.argv[0]] + fix_argv(args)
    return main_module.main()


def run_scan_shop(args):
    from scripts import scan_legion_shop

    sys.argv = [sys.argv[0]] + fix_argv(args)
    return scan_legion_shop.main()


def run_selftest(args=None):
    """自检：库 + OCR 模型能不能用，结果写到程序目录的 selftest.log。"""
    import time
    from pathlib import Path

    import app_paths

    lines = [f"程序目录: {app_paths.base_dir()}", f"打包模式: {app_paths.is_frozen()}"]
    ok = True

    try:
        import cv2

        lines.append(f"opencv: {getattr(cv2, '__version__', '未知')}")
    except Exception as exc:  # noqa: BLE001 - 自检要如实记录失败原因
        ok = False
        lines.append(f"opencv 导入失败: {exc!r}")

    try:
        import numpy

        lines.append(f"numpy: {numpy.__version__}")
    except Exception as exc:  # noqa: BLE001
        ok = False
        lines.append(f"numpy 导入失败: {exc!r}")

    try:
        from PIL import Image, ImageDraw, ImageFont
        from rapidocr_onnxruntime import RapidOCR

        image = Image.new("RGB", (260, 100), "white")
        draw = ImageDraw.Draw(image)
        font = None
        for candidate in ("C:/Windows/Fonts/meiryo.ttc",
                          "C:/Windows/Fonts/msyh.ttc"):
            if Path(candidate).exists():
                font = ImageFont.truetype(candidate, 48)
                break
        draw.text((18, 22), "次へ", fill="black", font=font)
        probe = app_paths.base_dir() / "selftest_ocr.png"
        image.save(probe)

        start = time.time()
        engine = RapidOCR()
        result, _ = engine(str(probe))
        seconds = round(time.time() - start, 2)
        lines.append(f"rapidocr: 加载成功，识别耗时 {seconds}s")
        lines.append(f"ocr 结果: {result}")
        if not result:
            ok = False
            lines.append("OCR 没有识别出任何文字（模型可能没打包进来）")
    except Exception as exc:  # noqa: BLE001
        ok = False
        lines.append(f"OCR 自检失败: {exc!r}")

    lines.append("自检结果: " + ("通过" if ok else "失败"))
    text = "\n".join(lines)
    # 先落盘再打印：日志文件是权威结果，控制台编码问题不该影响自检结论。
    try:
        (app_paths.base_dir() / "selftest.log").write_text(text, encoding="utf-8")
    except OSError:
        pass
    try:
        print(text, flush=True)
    except Exception:
        pass
    return 0 if ok else 1


def run_update(args=None):
    """命令行一键更新：检查 → 下载 → 应用（程序更新会重启自己）。

    给「不想开界面」和自动化验证用；GUI 里点按钮走的是同一套函数。
    """
    import tempfile
    from pathlib import Path

    import app_paths
    import updater

    release, kind, message = updater.check(app_paths.__version__,
                                           app_paths.base_dir(), force=True)
    print(f"当前版本: v{app_paths.__version__}", flush=True)
    if release:
        print(f"最新版本: {release.tag}", flush=True)
    print(f"结论: {message}", flush=True)
    if kind is None:
        return 0

    asset = release.full if kind == "program" else release.assets
    if asset is None:
        print("这个 Release 里没有对应的包，请到网页手动下载。", flush=True)
        return 1

    dest = Path(tempfile.gettempdir()) / asset.name
    state = {"last": -10}

    def on_progress(done, total):
        percent = int(done * 100 / total) if total else 0
        if percent >= state["last"] + 10:
            state["last"] = percent
            print(f"  已下载 {percent}%", flush=True)

    print(f"开始下载 {asset.name}（{asset.size / 1024 / 1024:.1f} MB）...", flush=True)
    updater.download(asset.url, dest, on_progress=on_progress)

    if kind == "assets":
        count = updater.apply_assets_pack(dest, app_paths.base_dir())
        print(f"识别模板已更新（{count} 个文件），不用重启。", flush=True)
        return 0

    updater.apply_full_update(dest, app_paths.base_dir(),
                              Path(sys.executable),
                              app_paths.base_dir() / "update.log")
    print("已启动更新脚本，本进程马上退出以完成覆盖 ...", flush=True)
    return 0


def run_check_update(args=None):
    """命令行查一次更新（打包版排查用，也方便脚本调用）。"""
    import urllib.request

    import app_paths
    import updater

    print(f"程序目录: {app_paths.base_dir()}", flush=True)
    try:
        print(f"系统代理: {urllib.request.getproxies()}", flush=True)
    except Exception as exc:  # noqa: BLE001 - 只用于排查
        print(f"系统代理: 读取失败 {exc!r}", flush=True)
    print("正在查询 GitHub ...", flush=True)
    release, kind, message = updater.check(app_paths.__version__,
                                           app_paths.base_dir())
    print(f"当前版本: v{app_paths.__version__}")
    print(f"最新版本: {release.tag if release else '（查询失败）'}")
    print(f"结论: {message}")
    if kind == "program" and release and release.full:
        print(f"完整包: {release.full.name}（{release.full.size / 1024 / 1024:.0f} MB）")
    if release and release.assets:
        print(f"资源包: {release.assets.name}（{release.assets.size / 1024 / 1024:.1f} MB）")
    return 0


def main(argv=None):
    ensure_std_streams()
    argv = list(sys.argv if argv is None else argv)
    action, args = resolve_command(argv)
    try:
        return _dispatch(action, args)
    except Exception:
        if action == "launcher":
            # 启动器自己有 sys.excepthook → launcher_error.log + 弹窗，保持原样
            raise
        # 命令行入口（--run-main 等）崩了不该弹模态框：记进 run_error.log 再退出，
        # 子进程的退出码仍然是 1，启动器那边能看到「进程退出」。
        import traceback

        text = traceback.format_exc()
        _write_run_error(text)
        last = text.strip().splitlines()[-1] if text.strip() else "未知错误"
        try:
            print(f"运行失败：{last}（详见程序目录的 run_error.log）", flush=True)
        except Exception:
            pass
        return 1


def _dispatch(action, args):
    if action == "main":
        return run_main(args)
    if action == "scan_shop":
        return run_scan_shop(args)
    if action == "selftest":
        return run_selftest(args)
    if action == "check_update":
        return run_check_update(args)
    if action == "update":
        return run_update(args)

    import launcher

    return launcher.main()


def _write_run_error(text):
    """把命令行入口的崩溃堆栈写到程序目录（写不了就算了，别再抛）。"""
    try:
        import time

        import app_paths

        path = app_paths.base_dir() / "run_error.log"
        stamp = f"===== {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n"
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(stamp + text + "\n")
    except Exception:
        pass


if __name__ == "__main__":
    raise SystemExit(main())
