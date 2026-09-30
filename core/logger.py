import sys
from datetime import datetime


class Logger:
    def __init__(self, lang="zh_CN"):
        self.lang = lang
        # stdout 写不出去之后就别再试了（每行都抛一次异常会把日志刷爆）
        self._console_broken = False

    def _text(self, msg):
        if isinstance(msg, dict):
            return msg.get(self.lang, msg.get("zh_CN", str(msg)))
        return str(msg)

    def _log(self, level, msg):
        if self._console_broken:
            return
        line = f"[{datetime.now():%H:%M:%S}] [{level}] {self._text(msg)}"
        try:
            print(line, flush=True)
        except (OSError, ValueError):
            # 2026-09-30 实测：读日志的那一头没了（启动器被关掉、管道断开、
            # Codex 里 `& exe ... | Tee-Object` 的 Tee 先失败）之后，往 stdout
            # 写会抛 OSError [Errno 22] Invalid argument；窗口版打包程序会因此
            # 弹「Unhandled exception in script」并把整轮任务直接打断。
            # 日志只是诊断手段，写不出去就静音，不能让它崩掉正在跑的任务。
            self._console_broken = True

    def info(self, msg):
        self._log("INFO", msg)

    def warn(self, msg):
        self._log("WARN", msg)

    def error(self, msg):
        self._log("ERROR", msg)
