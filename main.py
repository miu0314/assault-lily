import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

import app_paths

BASE_DIR = app_paths.base_dir()
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import dep_bootstrap  # noqa: E402

# 运行环境更新可能清掉第三方库：缺 cv2/OCR 时先自动补装，避免直接崩溃。
if not dep_bootstrap.ensure_deps():
    print("运行库不可用，请检查网络后重试（python -m pip install -r requirements.txt）。")
    sys.exit(1)
from core.adb import AdbDevice
from core.config import Config, ConfigError
from core.context import GameContext
from core.crash import GameCrashError, GameMonitor
from core.logger import Logger
from core.mumu import launch_mumu, wait_mumu_online
from core.task import Task
from tasks import TASK_REGISTRY, get_task
from core.popups import clear_popups as close_all_popups


def parse_args():
    parser = argparse.ArgumentParser(description="Assault Lily Bot")
    parser.add_argument("config", nargs="?", default="config.json")
    parser.add_argument("--screenshot", action="store_true", help="只截一张图")
    parser.add_argument("--tasks", default=None,
                        help="只运行指定任务，逗号分隔，如 --tasks CollectGifts,ClearDailyQuests")
    parser.add_argument("--check", action="store_true",
                        help="只校验配置和任务名，不连接模拟器")
    parser.add_argument("--dry-run", action="store_true",
                        help="只打印将要运行的任务，不连接模拟器")
    parser.add_argument("--stop-on-error", action="store_true",
                        help="任务失败时立即停止；默认跳过失败任务并继续")
    return parser.parse_args()


def _pid_exists(pid):
    """检查 PID 是否仍存在；Windows 下避免 os.kill 的权限误判。"""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=10, creationflags=flags,
            )
        except Exception:
            # 查不到进程信息时保守视为仍在运行，避免两个脚本抢同一台模拟器。
            return True
        if result.returncode != 0:
            return True
        return bool(result.stdout.strip()) and str(pid) in result.stdout
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def _acquire_lock(base_dir):
    """单实例锁：防止旧脚本进程还在运行时就再开一个，两个脚本抢同一台模拟器。"""
    lock = base_dir / "run.pid"
    if lock.exists():
        try:
            old = int(lock.read_text(encoding="utf-8").strip())
            if old != os.getpid() and _pid_exists(old):
                print(f"已有脚本在运行（PID {old}），为避免冲突本次退出")
                sys.exit(1)
        except (ValueError, OSError):
            pass
    lock.write_text(str(os.getpid()), encoding="utf-8")
    return lock


def _release_lock(lock):
    """释放单实例锁；启动前置步骤提前返回时也必须清理。"""
    try:
        if lock.exists() and lock.read_text(encoding="utf-8").strip() == str(os.getpid()):
            lock.unlink()
    except (OSError, ValueError):
        pass


def _validate_config(cfg):
    """校验配置文件中的任务列表；返回错误列表，空列表表示通过。"""
    problems = []
    task_list = cfg.get("task_list", [])
    if task_list is None:
        task_list = []
    if not isinstance(task_list, list):
        problems.append("task_list 必须是数组")
        task_list = []
    for name in task_list:
        if not isinstance(name, str) or name not in TASK_REGISTRY:
            problems.append(f"未知任务: {name!r}")
    flows = cfg.get("task_flows", {}) or {}
    if not isinstance(flows, dict):
        problems.append("task_flows 必须是对象")
    else:
        flow_map = flows.get("flows", {})
        if not isinstance(flow_map, dict):
            problems.append("task_flows.flows 必须是对象")
            flow_map = {}
        for flow_name, items in flow_map.items():
            if not isinstance(items, list):
                problems.append(f"流程 {flow_name!r} 不是数组")
                continue
            for item in items:
                name = item.get("task") if isinstance(item, dict) else None
                if name not in TASK_REGISTRY:
                    problems.append(f"流程 {flow_name!r} 包含未知任务: {name!r}")
    return problems


def main():
    args = parse_args()
    # 统一日志为 UTF-8，避免中文乱码
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    try:
        cfg = Config(args.config)
    except ConfigError as exc:
        print(f"配置错误: {exc}", flush=True)
        return 1
    logger = Logger(cfg.get("lang", "zh_CN"))

    if args.check or args.dry_run:
        problems = _validate_config(cfg)
        if problems:
            for problem in problems:
                logger.error(problem)
            return 1
        task_list = cfg.get("task_list", []) or []
        if not task_list:
            logger.error("任务列表为空，请至少配置一个任务")
            return 1
        if args.dry_run:
            print("待运行任务:")
            for task_name in task_list:
                print(f"  - {task_name}")
        else:
            logger.info(f"配置检查通过，共 {len(task_list)} 个任务")
        return 0

    task_list = []
    if not args.screenshot:
        task_list = cfg.get("task_list", [])
        if args.tasks:
            task_list = [t.strip() for t in args.tasks.split(",") if t.strip()]
            if not task_list:
                logger.error("--tasks 为空，请用逗号分隔任务名")
                return 1
            unknown = [t for t in task_list if t not in TASK_REGISTRY]
            if unknown:
                logger.error(f"未知任务: {', '.join(unknown)}")
                return 1
        if not task_list:
            logger.error("任务列表为空，请至少配置一个任务或使用 --tasks")
            return 1

    base_dir = app_paths.base_dir()
    lock = _acquire_lock(base_dir)
    device = AdbDevice(cfg.get("adb_path"), cfg.get("serial"),
                       cfg.get("package"), cfg.get("activity"))
    stop_on_error = bool(args.stop_on_error or cfg.get_bool("stop_on_task_error", False))
    failed_tasks = []
    skipped_tasks = []
    monitor = None

    try:
        if not device.is_online():
            if cfg.get_bool("mumu_auto_wake", True):
                logger.info("模拟器未启动，尝试唤醒 MuMu ...")
                if not launch_mumu(cfg):
                    logger.error("无法启动 MuMu，请检查 mumu_cli_path 配置")
                    return 1
                logger.info("等待模拟器启动并连接 adb ...")
                if not wait_mumu_online(cfg, device,
                                        timeout=cfg.get_int("mumu_wait_timeout", 180),
                                        on_status=lambda s: logger.info(
                                            f"模拟器还在启动中（已等待 {s} 秒），继续等待 ...")):
                    logger.error("模拟器启动超时，请手动打开模拟器后重试")
                    return 1
            else:
                logger.info(f"连接设备 {cfg.get('serial')} ...")
                device.connect()
                time.sleep(1)
                if not device.is_online():
                    logger.error("无法连接设备，请检查模拟器和 adb_path")
                    return 1

        ctx = GameContext(cfg, logger, device, base_dir=base_dir)

        if args.screenshot:
            path = ctx.save_screenshot(ctx.screenshot(), "screenshot.png")
            logger.info(f"截图已保存: {path}")
            return 0

        logger.info(f"开始运行，共 {len(task_list)} 个任务")
        monitor = None
        if cfg.get_bool("game_crash_recover", True):
            monitor = GameMonitor(device, cfg.get("package", ""))
            ctx.monitor = monitor
            monitor.start()
        for task_name in task_list:
            max_attempts = max(1, cfg.get_int("game_crash_retry", 3))
            for attempt in range(max_attempts):
                if monitor is not None:
                    monitor.clear()
                try:
                    if cfg.get_bool("auto_close_popups", True):
                        close_all_popups(ctx)
                    task_obj = get_task(task_name)
                    task_obj.run(ctx)
                    if task_obj.status == Task.STATUS_ERROR:
                        failed_tasks.append(task_name)
                        logger.error(
                            f"任务 {task_name} 未成功完成: "
                            f"{task_obj.error_message or '未知原因'}")
                        if stop_on_error:
                            return 1
                    elif task_obj.status == Task.STATUS_SKIP:
                        skipped_tasks.append(task_name)
                        logger.warn(f"任务 {task_name} 跳过（内容不存在 / 无法领取）")
                    break
                except GameCrashError:
                    if task_name in ("LaunchGame", "WaitForHome", "LaunchToHome",
                                     "LaunchAccelerator"):
                        # 这些任务会启动或切换游戏，启动期进程短暂不存在属正常
                        logger.warn("游戏进程检测异常（启动阶段），继续")
                        if monitor is not None:
                            monitor.clear()
                        continue
                    logger.warn(f"任务 {task_name} 进行中检测到游戏闪退，正在恢复 ...")
                    try:
                        _recover_game(ctx)
                    except Exception as e:
                        logger.warn(f"恢复过程异常: {e}")
                    if attempt >= max_attempts - 1:
                        logger.warn(f"游戏恢复后任务 {task_name} 仍异常，跳过该任务")
                        failed_tasks.append(task_name)
                        break
                    logger.info(f"重新执行任务 {task_name}")
                except Exception as e:
                    logger.error(f"任务 {task_name} 异常: {e}")
                    failed_tasks.append(task_name)
                    try:
                        ctx.save_screenshot(ctx.screenshot(), "error.png")
                    except Exception:
                        pass
                    if stop_on_error:
                        return 1
                    break
    except Exception as exc:
        logger.error(f"运行异常: {exc}")
        return 1
    finally:
        _release_lock(lock)
        if monitor is not None:
            monitor.stop()
        try:
            device.disconnect()
        except Exception:
            pass

    if failed_tasks:
        logger.warn(f"运行结束，但有 {len(failed_tasks)} 个任务未成功完成: "
                    f"{', '.join(failed_tasks)}")
        return 1
    if skipped_tasks:
        logger.info(f"运行结束，{len(skipped_tasks)} 个任务因内容不存在/无法领取而跳过: "
                    f"{', '.join(skipped_tasks)}")
    logger.info("全部任务成功结束")
    return 0


def _recover_game(ctx):
    """闪退恢复：重启游戏进程并重新登录到首页。"""
    logger = ctx.logger
    monitor = ctx.monitor
    if monitor is not None:
        monitor.stop()
        monitor.clear()  # 清掉残留的闪退标记，避免恢复流程误报
    logger.info("重启游戏进程 ...")
    try:
        ctx.device.start_app()
    except Exception as e:
        logger.warn(f"启动游戏失败: {e}")
    time.sleep(3)
    # 恢复期间不监控（游戏刚开始启动，进程短暂不存在属正常）；
    # 用较短的等待预算，避免长时间傻等
    old1 = ctx.config.get("home_wait_rounds")
    old2 = ctx.config.get("home_wait_rounds2")
    ctx.config["home_wait_rounds"] = 10
    ctx.config["home_wait_rounds2"] = 25
    try:
        get_task("WaitForHome").run(ctx)
    except Exception as e:
        logger.warn(f"恢复登录流程异常: {e}")
    finally:
        if old1 is not None:
            ctx.config["home_wait_rounds"] = old1
        if old2 is not None:
            ctx.config["home_wait_rounds2"] = old2
    # 恢复完成后再开监控，避免把启动中的游戏误判为闪退
    if monitor is not None:
        monitor.clear()
        monitor.start()


if __name__ == "__main__":
    raise SystemExit(main())
