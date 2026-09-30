# -*- coding: utf-8 -*-
"""日志健壮性：写不出去的时候不能让程序崩。

2026-09-30 实测：Codex 里执行 `& AssaultLilyBot.exe --run-main ... | Tee-Object`
时 Tee 先失败（日志文件所在目录刚被重建删掉），管道读端没了 → exe 第一次
`print` 抛 `OSError: [Errno 22] Invalid argument` → 窗口版打包程序弹出
「Unhandled exception in script」，整轮任务当场中断、还留了个模态框在屏幕上。
用户运行中关掉启动器窗口也是同一条路径。
"""

import io
import unittest
from unittest.mock import patch

from core.logger import Logger


class _BrokenStream:
    """模拟断掉的管道：一写就抛 OSError 22。"""

    def __init__(self):
        self.calls = 0

    def write(self, data):
        self.calls += 1
        raise OSError(22, "Invalid argument")

    def flush(self):
        raise OSError(22, "Invalid argument")


class LoggerResilienceTest(unittest.TestCase):
    def test_broken_stdout_does_not_raise(self):
        """管道断了以后 info/warn/error 都不能抛异常。"""
        stream = _BrokenStream()
        logger = Logger()
        with patch("sys.stdout", stream):
            logger.info("第一条")
            logger.warn("第二条")
            logger.error("第三条")
        # 第一次发现写不了就静音，之后不再去碰 stdout（否则每行都抛一次）
        self.assertEqual(stream.calls, 1)

    def test_normal_stdout_still_works(self):
        buffer = io.StringIO()
        logger = Logger()
        with patch("sys.stdout", buffer):
            logger.info("配置检查通过")
        self.assertIn("配置检查通过", buffer.getvalue())

    def test_dict_message_uses_language(self):
        buffer = io.StringIO()
        logger = Logger(lang="ja_JP")
        with patch("sys.stdout", buffer):
            logger.info({"zh_CN": "中文", "ja_JP": "日本語"})
        self.assertIn("日本語", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
