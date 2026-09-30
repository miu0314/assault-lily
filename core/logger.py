import sys
from datetime import datetime


class Logger:
    def __init__(self, lang="zh_CN"):
        self.lang = lang

    def _text(self, msg):
        if isinstance(msg, dict):
            return msg.get(self.lang, msg.get("zh_CN", str(msg)))
        return str(msg)

    def _log(self, level, msg):
        print(f"[{datetime.now():%H:%M:%S}] [{level}] {self._text(msg)}", flush=True)

    def info(self, msg):
        self._log("INFO", msg)

    def warn(self, msg):
        self._log("WARN", msg)

    def error(self, msg):
        self._log("ERROR", msg)
