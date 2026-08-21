#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""抖音博主数据库 - 桌面 App 启动器"""
import os, socket, sys, threading, uvicorn
from pathlib import Path
import app

def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]

def icon_path():
    if getattr(sys, "frozen", False):
        return str(Path(getattr(sys, "_MEIPASS", ".")) / "icon.ico")
    return str(Path(__file__).resolve().parent / "icon.ico")

def main():
    print("=" * 52)
    print(" 抖音博主数据库 - 仅供个人学习 / 技术研究 / 个人备份")
    print(" 使用即表示遵守各平台服务条款与当地法律，风险自负。")
    print("=" * 52)
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    port = free_port()
    threading.Thread(
        target=lambda: uvicorn.run(app.app, host="127.0.0.1", port=port,
                                   log_level="warning", log_config=None),
        daemon=True,
    ).start()
    import webview
    webview.create_window(
        "抖音博主数据库",
        f"http://127.0.0.1:{port}",
        width=1280, height=860, min_size=(1024, 700),
    )
    webview.start(icon=icon_path())

if __name__ == "__main__":
    main()

