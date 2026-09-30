# Assault Lily Bot（突击莉莉 Last Bullet 自动化助手）

为《Assault Lily: Last Bullet》（日服，包名 `jp.co.allb`）编写的日常自动化脚本，运行在 MuMu 模拟器上。

目标功能：

- 每日签到 / 登录奖励
- 任务领取（ミッション / 実績 角标识别）
- 自动刷关卡（扫荡 / 重复战斗）
- 免费扭蛋（有红点提示时自动抽）
- 外征撃破任務：エアラム / クリオン
- 聊天发言：在レギオン / グループ / 個人频道用游戏自带定型文发一条消息（默认「ごきげんよう」）

## 下载即用（普通用户）

1. 到本仓库的 [Releases](../../releases) 下载最新的 `AssaultLilyBot-v*-win-x64.zip`
2. 解压到任意目录（**整个文件夹一起解压**，`_internal`、`assets` 都要留着）
3. 双击 `AssaultLilyBot.exe` 打开启动器，勾选要跑的功能，点「开始运行」

- Windows 可能提示「未知发布者」或杀软误报：本项目没有代码签名证书，添加信任即可。
- 升级时只覆盖 `AssaultLilyBot.exe` 和 `_internal`，自己的 `config.json` 不会被覆盖。
- 双击没反应？看同目录的 `launcher_error.log`；想确认环境正常可执行
  `AssaultLilyBot.exe --selftest`，结果写在 `selftest.log`。
- 启动器会自动检查新版本：程序更新走完整包（自动重启覆盖，你的 config 不会被动），
  识别模板更新走 1~2MB 的小包，不用重启。

## 使用方法（源码运行）

- **启动器（推荐）**：双击 `启动脚本.vbs`（或项目里的 `突击莉莉脚本启动器.lnk` 快捷方式），
  全程不会弹出命令行窗口；勾选要跑的功能，点「开始运行」。
  - **自定义流程**（主页面）：按顺序编排任务——添加/删除任务、↑↓调整顺序、单独启用/禁用，
    可新建多个流程切换；“RANK UP 扫荡难度”“发送消息频道 / 发言内容”也在这里设置。
  - **军团兑换**：勾选要兑换的物品并选数量；“军团捐献次数”也整合在这一页。
    页面顶部有「启用军团捐献（徽章贡献）」开关，**默认不勾选**——需要用才勾上，
    否则运行时会跳过捐献、不会消耗徽章。
  - **连接与设置**：模拟器地址、adb 路径、自动唤醒模拟器、加速器（打开并连接 / 结束后断开）。
  - `start.bat` 只是备用入口（双击会闪一下黑色窗口，属正常现象）。
- **命令行**：`python main.py config.json` 跑全部任务；
  或 `python main.py config.json --tasks CollectGifts,ClearDailyQuests` 只跑指定任务。
- **离线检查**：`python main.py config.json --check` 只校验配置和任务名，不连接模拟器；
  `python main.py config.json --dry-run` 只打印将要执行的任务。
- 任务失败默认记录到日志并跳过该任务、继续后面的流程；需要失败即停止时，
  可在配置里加 `"stop_on_task_error": true`，或使用 `--stop-on-error`。
- 运行日志显示在启动器下方，可随时点「停止」结束。

## 原理

- ADB 控制模拟器：截图、点击、滑动、按键。
- OpenCV 模板匹配 + 颜色检测识别画面。
- 任务 = 前置判断 → 执行 → 后置判断，按配置顺序运行。

## 安全设计

- 固定按钮用坐标 + 事后确认页面，不盲点。
- 模板匹配限制区域和缩放比例，防止误匹配。
- 不盲目按返回键，避免误触发退出确认。
- 每个功能先录素材、验证识别，再写执行逻辑。
- 任务的后置条件失败不会再被当成成功；最终日志会列出未完成的任务并返回非零退出码。

## 自己打包 / 发布

打包成「解压即用」的 Windows 绿色版（PyInstaller onedir，非单文件 exe）：

```bash
python -m venv build_venv
build_venv\Scripts\pip install -r requirements.txt -r requirements-build.txt
build_venv\Scripts\python package.py
```

产物在 `dist/`：`AssaultLilyBot/`（文件夹）和 `AssaultLilyBot-v<版本>-win-x64.zip`（发 Release 用）。
版本号写在 `app_paths.py` 的 `__version__`。

打 tag 推送后 GitHub Actions 会自动跑单测、打包并把 zip 传到 Release：

```bash
git tag v0.1.0
git push origin v0.1.0
```

约定：**资源（`assets/`）和配置永远放在 exe 外面**。游戏更新换识别模板时，
只要发一个新的 zip 覆盖资源即可，用户不用重装。

## 免责声明

本项目仅供学习交流与个人效率用途，请自行承担使用风险。
请勿用于商业用途，使用前请确认不违反游戏用户协议（ToS）。
游戏更新后识别模板可能失效，欢迎提 issue 或 PR 更新 `assets/`。