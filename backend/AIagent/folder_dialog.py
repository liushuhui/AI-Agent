"""在服务端所在机器上弹出系统「选择文件夹」对话框。

为什么弹框放在服务端：
  浏览器出于安全不会把本地真实路径交给网页（showDirectoryPicker() 只给一个
  目录句柄，拿不到 D:\\code\\demo 这种绝对路径），而 Agent 要读写代码必须拿到
  真实路径。本地开发时前后端在同一台机器上，所以由前端点按钮、服务端弹框，
  效果就是「在页面里选本机文件夹」。

实现要点：
  Tk 必须在自己的主线程里跑，直接放进 Flask 的工作线程容易卡住甚至崩，
  所以用一个子进程执行脚本，把选中的路径打到 stdout 再读回来。
  标题/起始目录用环境变量传，避免把外部字符串拼进代码里。
"""

import os
import subprocess
import sys

_SCRIPT = """
import os
import tkinter as tk
from tkinter import filedialog

root = tk.Tk()
root.withdraw()
# 不加 topmost 的话，对话框可能被浏览器窗口挡在后面，看起来像没反应
root.attributes("-topmost", True)
path = filedialog.askdirectory(
    title=os.environ.get("PICK_TITLE") or "选择文件夹",
    initialdir=os.environ.get("PICK_INITIAL") or None,
    mustexist=True,
)
print(path or "")
root.destroy()
"""


class FolderDialogError(Exception):
    """弹框失败（环境无 Tk、启动不了、等待超时等），message 可直接展示。"""


def pick_folder(
    title: str = "选择工作目录", initial_dir: str | None = None, timeout: int = 300
) -> str | None:
    """弹出系统文件夹选择框，返回选中的绝对路径；用户取消则返回 None。

    注意这是「阻塞」调用：对话框关掉之前不会返回，所以调用方的 HTTP 请求
    会一直挂着（Flask 是多线程的，不影响其它请求）。
    """
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"  # 路径可能含中文，避免按 GBK 输出乱码
    env["PICK_TITLE"] = title
    env["PICK_INITIAL"] = initial_dir or ""

    try:
        proc = subprocess.Popen(
            [sys.executable, "-c", _SCRIPT],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            # 不额外弹出控制台黑框（Tk 的对话框不受影响）
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError as exc:
        raise FolderDialogError(f"无法启动选择框：{exc}") from exc

    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        # 超时要连子进程一起收掉，否则对话框会一直挂在用户桌面上
        proc.kill()
        proc.communicate()
        raise FolderDialogError(f"等待选择超过 {timeout} 秒已放弃，请重试") from None

    if proc.returncode != 0:
        lines = [line for line in (stderr or "").splitlines() if line.strip()]
        detail = lines[-1] if lines else f"退出码 {proc.returncode}"
        raise FolderDialogError(f"选择框打开失败：{detail}")

    return (stdout or "").strip() or None
