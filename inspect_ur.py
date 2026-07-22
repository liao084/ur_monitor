"""在业务 Windows 环境中检查 UR/牛进程及 UR 窗口控件。

此脚本只读取状态，不会启动进程，也不会点击任何按钮。
"""

from __future__ import annotations

import io
import sys
import traceback
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import psutil
from pywinauto import Desktop


UR_PROCESS_NAME = "UR实时采集.exe"
COLLECTOR_PROCESS_NAME = "牛.exe"
RESULT_FILENAME = "inspect_ur_result.txt"


def get_runtime_dir() -> Path:
    """返回 py 文件或打包后 exe 所在目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def find_processes(process_name: str) -> list[psutil.Process]:
    """按进程名查找进程；PID 每次启动都会变化，所以不写死 PID。"""
    matches: list[psutil.Process] = []

    for process in psutil.process_iter(["pid", "name", "exe"]):
        try:
            name = process.info["name"] or ""
            if name.casefold() == process_name.casefold():
                matches.append(process)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    return matches


def inspect_windows(pid: int, backend: str) -> None:
    """输出指定 PID 在某个 pywinauto 后端下可识别的窗口和有文字控件。"""
    print(f"\n[{backend}] 检查 PID {pid} 的窗口控件")
    windows = Desktop(backend=backend).windows(process=pid, visible_only=False)

    if not windows:
        print("  未发现窗口")
        return

    for window_index, window in enumerate(windows, start=1):
        try:
            print(
                f"  窗口 {window_index}: title={window.window_text()!r}, "
                f"class={window.class_name()!r}, handle={window.handle}"
            )

            for control in window.descendants():
                text = control.window_text().strip()
                control_type = getattr(control.element_info, "control_type", "")
                if text or control_type == "Button":
                    print(
                        f"    text={text!r}, type={control_type!r}, "
                        f"class={control.class_name()!r}, handle={control.handle}"
                    )
        except Exception as error:
            print(f"  读取窗口失败: {error}")


def print_processes(label: str, processes: list[psutil.Process]) -> None:
    print(f"\n{label}: {len(processes)} 个")
    for process in processes:
        try:
            print(
                f"  PID={process.pid}, name={process.name()!r}, "
                f"exe={process.exe()!r}"
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied) as error:
            print(f"  PID={process.pid}, 无法读取完整信息: {error}")


def main() -> None:
    ur_processes = find_processes(UR_PROCESS_NAME)
    collector_processes = find_processes(COLLECTOR_PROCESS_NAME)

    print_processes("UR 进程", ur_processes)
    print_processes("牛进程", collector_processes)

    if not ur_processes:
        print("\n未找到 UR 进程，请先确认 UR 已经运行以及进程名是否完全一致。")
        return

    for process in ur_processes:
        for backend in ("win32", "uia"):
            try:
                inspect_windows(process.pid, backend)
            except Exception as error:
                print(f"\n[{backend}] 检查 PID {process.pid} 失败: {error}")


if __name__ == "__main__":
    result_path = get_runtime_dir() / RESULT_FILENAME
    output = io.StringIO()

    # 先把全部诊断输出收集到内存，结束后同时写入终端和 txt 文件。
    with redirect_stdout(output), redirect_stderr(output):
        print(f"诊断结果文件: {result_path}")
        try:
            main()
        except Exception:
            print("\n诊断脚本发生未处理异常：")
            traceback.print_exc()

    result_text = output.getvalue()
    result_path.write_text(result_text, encoding="utf-8-sig")
    print(result_text, end="")
