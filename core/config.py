import json
from pathlib import Path


class ConfigError(Exception):
    """配置文件读取/解析失败。"""


DEFAULTS = {
    "adb_path": "adb",
    "serial": "127.0.0.1:16384",
    "package": "",
    "activity": "",
    "screen_width": 1280,
    "screen_height": 720,
    "match_threshold": 0.8,
    "lang": "zh_CN",
    "legion_donate_enabled": False,
    "mumu_accel_action": "no_accel",
    "auto_close_popups": True,
    "stop_on_task_error": False,
    "task_list": [],
}


class Config:
    def __init__(self, path=None):
        self.path = Path(path) if path else None
        self.data = dict(DEFAULTS)
        if self.path:
            self.load(self.path)

    def load(self, path):
        path = Path(path)
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except FileNotFoundError as exc:
            raise ConfigError(f"配置文件不存在: {path}") from exc
        except OSError as exc:
            raise ConfigError(f"无法读取配置文件: {path}（{exc}）") from exc
        except json.JSONDecodeError as exc:
            raise ConfigError(f"配置文件不是有效 JSON: {path}（第 {exc.lineno} 行）") from exc
        if not isinstance(raw, dict):
            raise ConfigError(f"配置文件顶层必须是对象: {path}")
        self.data.update(raw)
        self.path = path
        return self

    def get(self, key, default=None):
        return self.data.get(key, default)

    def __getitem__(self, key):
        return self.data[key]

    def __setitem__(self, key, value):
        self.data[key] = value

    def get_int(self, key, default=0):
        """把配置值安全转为 int；非法值返回 default。"""
        try:
            return int(self.data.get(key, default))
        except (TypeError, ValueError):
            return default

    def get_bool(self, key, default=False):
        """把配置值安全转为 bool；仅接受真值/假值字符串，其余使用 default。"""
        value = self.data.get(key, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            low = value.strip().lower()
            if low in ("1", "true", "yes", "on", "是", "启用"):
                return True
            if low in ("0", "false", "no", "off", "否", "禁用"):
                return False
        return bool(value)
