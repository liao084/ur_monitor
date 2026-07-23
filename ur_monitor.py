"""持续监控 UR 和牛进程，并在异常时执行最小恢复动作。"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import psutil
from pywinauto import Desktop


CONFIG_FILENAME = "monitor_config.json"
OPEN_SCRIPT_BUTTON_TITLE = "打开脚本"


def get_runtime_dir() -> Path:
    """返回 py 文件或打包后 exe 所在目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def configure_logger() -> logging.Logger:
    """日志只输出到控制台，正常轮询不产生日志。"""
    logger = logging.getLogger("ur_monitor")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    )
    logger.addHandler(handler)
    logger.propagate = False
    return logger


def load_config(runtime_dir: Path) -> dict[str, Any]:
    """从程序同目录读取监控配置。"""
    config_path = runtime_dir / CONFIG_FILENAME
    with config_path.open("r", encoding="utf-8-sig") as file:
        config = json.load(file)

    required_fields = (
        "ur_process_name",
        "collector_process_name",
        "ur_exe_path",
        "missing_timeout_seconds",
        "poll_interval_seconds",
    )
    missing_fields = [field for field in required_fields if field not in config]
    if missing_fields:
        raise ValueError(f"配置缺少字段: {', '.join(missing_fields)}")

    config["missing_timeout_seconds"] = float(
        config["missing_timeout_seconds"]
    )
    config["poll_interval_seconds"] = float(config["poll_interval_seconds"])

    if config["missing_timeout_seconds"] <= 0:
        raise ValueError("missing_timeout_seconds 必须大于 0")
    if config["poll_interval_seconds"] <= 0:
        raise ValueError("poll_interval_seconds 必须大于 0")

    return config


def find_processes(process_name: str) -> list[psutil.Process]:
    """按名称查找全部匹配进程，PID 始终动态获取。"""
    matches: list[psutil.Process] = []

    for process in psutil.process_iter(["pid", "name"]):
        try:
            name = process.info["name"] or ""
            if name.casefold() == process_name.casefold():
                matches.append(process)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    return matches


def start_ur(ur_exe_path: Path) -> None:
    """按照绝对路径启动 UR，并把工作目录设为 UR 所在目录。"""
    if not ur_exe_path.is_file():
        raise FileNotFoundError(f"UR 程序不存在: {ur_exe_path}")

    subprocess.Popen(
        [str(ur_exe_path)],
        cwd=str(ur_exe_path.parent),
    )


def invoke_open_script(pid: int) -> None:
    """使用 UIA 找到当前 UR 窗口，并调用“打开脚本”按钮的主要动作。"""
    windows = Desktop(backend="uia").windows(
        process=pid,
        visible_only=False,
    )

    for window in windows:
        buttons = window.descendants(
            title=OPEN_SCRIPT_BUTTON_TITLE,
            control_type="Button",
        )
        if buttons:
            buttons[0].invoke()
            return

    raise RuntimeError(
        f"PID {pid} 的窗口中未找到“{OPEN_SCRIPT_BUTTON_TITLE}”按钮"
    )


def run_monitor(config: dict[str, Any], logger: logging.Logger) -> None:
    ur_process_name = str(config["ur_process_name"])
    collector_process_name = str(config["collector_process_name"])
    ur_exe_path = Path(str(config["ur_exe_path"]))
    missing_timeout = float(config["missing_timeout_seconds"])
    poll_interval = float(config["poll_interval_seconds"])

    collector_missing_since: float | None = None
    is_clicked: bool = False

    while True:
        try:
            ur_processes = find_processes(ur_process_name)
            collector_processes = find_processes(collector_process_name)
            now = time.monotonic()
            # 如果没有找到 UR 进程，直接启动
            if not ur_processes:
                collector_missing_since = None
                logger.warning(
                    "未发现 %s，尝试启动: %s",
                    ur_process_name,
                    ur_exe_path,
                )
                try:
                    # UR进程不存在，尝试启动UR并重置点击打开脚本信号
                    is_clicked = False
                    start_ur(ur_exe_path)
                except Exception:
                    logger.exception("启动 %s 失败", ur_process_name)
            # 如果找到了 UR 进程和牛进程，重置全部状态
            elif collector_processes:
                collector_missing_since = None
                is_clicked = False
            # 如果没有找到牛进程，开始计时
            elif collector_missing_since is None:
                collector_missing_since = now
            # 如果牛进程已缺失超过阈值，执行打开脚本操作
            elif now - collector_missing_since >= missing_timeout and not is_clicked:
                ur_pid = ur_processes[0].pid
                logger.warning(
                    "%s 已连续缺失 %.0f 秒，调用 PID %s 的“%s”按钮",
                    collector_process_name,
                    missing_timeout,
                    ur_pid,
                    OPEN_SCRIPT_BUTTON_TITLE,
                )
                try:
                    invoke_open_script(ur_pid)
                    is_clicked = True
                except Exception:
                    logger.exception("调用“%s”按钮失败", OPEN_SCRIPT_BUTTON_TITLE)
                finally:
                    # 无论本次调用是否成功，都重新等待一个完整周期再尝试。
                    collector_missing_since = time.monotonic()
            # 如果已经执行打开脚本操作，牛进程再次超过阈值，执行kill UR进程
            elif now - collector_missing_since >= missing_timeout and is_clicked:
                ur_process = ur_processes[0]
                logger.warning(
                    "已执行点击 %s 按钮，超过 %.0f 秒，%s 仍然没有打开，执行kill %s",
                    OPEN_SCRIPT_BUTTON_TITLE,
                    missing_timeout,
                    collector_process_name,
                    ur_process_name,
                )
                try:
                    ur_process.kill()
                    ur_process.wait(timeout=10)
                except psutil.NoSuchProcess:
                    pass
                except (psutil.AccessDenied, psutil.TimeoutExpired):
                    logger.exception("强制结束 UR 失败，PID=%s", ur_process.pid)
                finally:
                    collector_missing_since = None
                    is_clicked = False

        except Exception:
            logger.exception("监控循环发生异常")

        time.sleep(poll_interval)


def main() -> None:
    runtime_dir = get_runtime_dir()
    logger = configure_logger()

    try:
        config = load_config(runtime_dir)
        run_monitor(config, logger)
    except KeyboardInterrupt:
        return
    except Exception:
        logger.exception("监控器启动失败")


if __name__ == "__main__":
    main()
