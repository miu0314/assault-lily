import time
from pathlib import Path

from core import vision
from core.crash import GameCrashError


class GameContext:
    def __init__(self, config, logger, device, base_dir=None):
        self.config = config
        self.logger = logger
        self.device = device
        self.base_dir = Path(base_dir) if base_dir else Path.cwd()
        self.assets_dir = self.base_dir / config.get("assets_dir", "assets")
        self.screenshots_dir = self.base_dir / config.get("screenshots_dir", "screenshots")
        self._template_cache = {}
        self._last_screen = None
        self.monitor = None

    def check_pause(self):
        """启动器「暂停」按钮会生成 pause.flag；检测到就阻塞等待，直到继续。"""
        pause_path = self.base_dir / "pause.flag"
        if not pause_path.exists():
            return
        self.logger.info("检测到暂停请求，已暂停（点启动器「继续」恢复）")
        while pause_path.exists():
            time.sleep(1)
        self.logger.info("继续运行")

    def screenshot(self, save=False, name=None):
        self.check_pause()
        if self.monitor is not None and self.monitor.crash_event.is_set():
            raise GameCrashError("游戏进程消失（可能闪退）")
        img = None
        for _ in range(3):
            try:
                img = vision.decode_png(self.device.screencap_bytes())
            except Exception:
                img = None
            if img is not None:
                break
            time.sleep(1.5)
        if img is None:
            raise RuntimeError("截屏失败：模拟器画面未就绪")
        self._last_screen = img
        if save:
            self.save_screenshot(img, name)
        return img

    def save_screenshot(self, img, name=None):
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)
        if name is None:
            name = time.strftime("%Y%m%d_%H%M%S.png")
        path = self.screenshots_dir / name
        import cv2
        cv2.imwrite(str(path), img)
        return path

    def template(self, name):
        if name not in self._template_cache:
            path = self.assets_dir / name
            img, mask = vision.load_template_with_mask(path)
            if img is None:
                raise FileNotFoundError(f"识别素材不存在: {path}")
            self._template_cache[name] = (img, mask)
        return self._template_cache[name]

    def find(self, template_name, threshold=None, multi=False):
        if self._last_screen is None:
            self.screenshot()
        threshold = threshold if threshold is not None else self.config.get("match_threshold", 0.8)
        template, mask = self.template(template_name)
        if mask is not None:
            return vision.match_template_masked(self._last_screen, template, mask, threshold)
        return vision.match_template(self._last_screen, template, threshold)

    def find_scale(self, template_name, threshold=None):
        if self._last_screen is None:
            self.screenshot()
        threshold = threshold if threshold is not None else 0.7
        template, mask = self.template(template_name)
        return vision.match_template_scale(self._last_screen, template, mask, threshold)

    def find_any(self, template_names, threshold=None):
        for name in template_names:
            result = self.find(name, threshold=threshold)
            if result is not None:
                return result, name
        return None, None

    def click(self, x, y, sleeptime=1.0):
        self.device.tap(x, y)
        time.sleep(sleeptime)

    def click_template(self, template_name, threshold=None, sleeptime=1.5):
        self.screenshot()
        result = self.find(template_name, threshold=threshold)
        if result is None:
            return False
        (cx, cy), score = result
        self.logger.info(f"找到 {template_name} ({score:.2f})，点击 ({cx},{cy})")
        self.click(cx, cy, sleeptime)
        return True

    def find_red_dots(self, img=None):
        if img is None:
            if self._last_screen is None:
                self.screenshot()
            img = self._last_screen
        return vision.find_red_dots(img)

    def find_colored_blob(self, hsv_low, hsv_high, min_area=500, img=None):
        if img is None:
            if self._last_screen is None:
                self.screenshot()
            img = self._last_screen
        return vision.find_colored_blob(img, hsv_low, hsv_high, min_area)
