import time

from core.pages import is_page


class Task:
    STATUS_SUCCESS = 0
    STATUS_ERROR = 1
    STATUS_SKIP = 2

    def __init__(self, name="default Task", pre_times=2, post_times=4):
        self.name = name
        self.pre_times = pre_times
        self.post_times = post_times
        self.status = self.STATUS_SUCCESS
        self.error_message = None

    def pre_condition(self, ctx):
        return True

    def on_run(self, ctx):
        pass

    def post_condition(self, ctx):
        return True

    def run(self, ctx):
        self.status = self.STATUS_SUCCESS
        self.error_message = None
        if not self._run_until(lambda: self.pre_condition(ctx), self.pre_times, 3):
            ctx.logger.warn(f"跳过任务: {self.name}")
            self.status = self.STATUS_SKIP
            return self
        ctx.logger.info(f"执行任务: {self.name}")
        result = self.on_run(ctx)
        if self.status == self.STATUS_SKIP:
            ctx.logger.warn(f"跳过任务: {self.name}")
            return self
        if result is False or self.status == self.STATUS_ERROR:
            self.status = self.STATUS_ERROR
            if not self.error_message:
                self.error_message = f"任务未完成: {self.name}"
            ctx.logger.warn(self.error_message)
            return self
        if self._run_until(lambda: self.post_condition(ctx), self.post_times, 3):
            ctx.logger.info(f"任务完成: {self.name}")
            self.status = self.STATUS_SUCCESS
        else:
            ctx.logger.warn(f"任务后置条件不成立: {self.name}")
            self.status = self.STATUS_ERROR
            self.error_message = f"任务后置条件不成立: {self.name}"
        return self

    def fail(self, message, ctx=None):
        """标记任务失败并返回 False，供 on_run 提前退出时使用。"""
        self.status = self.STATUS_ERROR
        self.error_message = message
        if ctx is not None:
            ctx.logger.warn(message)
        return False

    def skip(self, message=None, ctx=None):
        """标记任务为跳过（内容不存在/找不到/无需处理），不影响整轮退出码。"""
        self.status = self.STATUS_SKIP
        if message:
            self.error_message = message
        if ctx is not None and message:
            ctx.logger.warn(message)
        return None

    @staticmethod
    def _run_until(func, times, sleeptime):
        for _ in range(times):
            if func():
                return True
            time.sleep(sleeptime)
        return False

    @staticmethod
    def back_to_home(ctx, times=4):
        for _ in range(times):
            ctx.screenshot()
            if is_page(ctx, "home"):
                return True
            ctx.device.key("BACK")
            time.sleep(2)
        return is_page(ctx, "home")
