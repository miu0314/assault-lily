import time

from core.navigation import click_home_button, close_chat_if_open
from core.pages import is_page
from core.popups import close_content_popup, handle_download_popup
from core.task import Task


class GoHomeFirst(Task):
    """确保脚本开始时在首页。

    不管当前画面在哪：关弹窗 → 点右上角主页按钮 → 必要时按返回键，
    直到确认回到首页。
    """

    def __init__(self):
        super().__init__(name="确保在首页", pre_times=1, post_times=4)

    def pre_condition(self, ctx):
        return True

    def on_run(self, ctx):
        mumu_ignore = str(ctx.config.get("mumu_accel_action", "no_accel")) == "ignore"
        for _ in range(12):
            ctx.screenshot()
            if is_page(ctx, "home"):
                ctx.logger.info("已在首页")
                return
            # 登录/加载过程：等待，不要按返回
            if ((not mumu_ignore
                 and ctx.find("popup/mumu_accel_title.png", threshold=0.85) is not None)
                    or ctx.find("home/title_support.png", threshold=0.8) is not None
                    or ctx.find("home/tap_to_start.png", threshold=0.7) is not None
                    or ctx.find("loading/loading_mark.png", threshold=0.8) is not None):
                time.sleep(3)
                continue
            # 弹窗：先关闭
            if close_content_popup(ctx):
                continue
            # 下载弹窗：同意下载
            if handle_download_popup(ctx):
                continue
            # 聊天界面：点聊天自己的 X（按返回键会把游戏弹出去）
            if close_chat_if_open(ctx):
                continue
            # 主页按钮（右上角房子图标）
            if click_home_button(ctx):
                continue
            # 都不行才按返回键（此时肯定不在首页）
            ctx.device.key("BACK")
            time.sleep(2)
        ctx.logger.warn("未能确保回到首页")

    def post_condition(self, ctx):
        return is_page(ctx, "home")
