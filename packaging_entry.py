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
}


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
            setattr(sys, name, stream)
            continue
        # 控制台可能是 cp1252/GBK：编不出来的字符替换掉，别让 print 崩掉整个程序
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass


def resolve_command(argv):
    """把命令行参数解析成 (动作, 剩余参数)；纯函数，方便单测。"""
    args = list(argv[1:])
    for flag, action in FLAGS.items():
        if flag in args:
            index = args.index(flag)
            return action, args[:index] + args[index + 1:]
    return "launcher", args


def run_main(args):
    import main as main_module

    sys.argv = [sys.argv[0]] + list(args)
    return main_module.main()


def run_scan_shop(args):
    from scripts import scan_legion_shop

    sys.argv = [sys.argv[0]] + list(args)
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


def main(argv=None):
    ensure_std_streams()
    argv = list(sys.argv if argv is None else argv)
    action, args = resolve_command(argv)
    if action == "main":
        return run_main(args)
    if action == "scan_shop":
        return run_scan_shop(args)
    if action == "selftest":
        return run_selftest(args)

    import launcher

    return launcher.main()


if __name__ == "__main__":
    raise SystemExit(main())
