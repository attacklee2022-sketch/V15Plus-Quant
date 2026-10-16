# -*- coding: utf-8 -*-
"""截图查看工具 — 读取系统截图目录中最新的文件，用 image-viewer MCP 描述内容。

用法：python screenshot_viewer.py [--question "你想问什么"]
"""
import os, sys, glob, json
from datetime import datetime

SCREENSHOT_DIR = os.path.expanduser(r"~\Pictures\Screenshots")

def latest_screenshot():
    """返回截图目录中修改时间最新的图片文件路径。"""
    if not os.path.exists(SCREENSHOT_DIR):
        return None
    files = []
    for ext in ("*.png", "*.jpg", "*.jpeg", "*.bmp", "*.gif"):
        files.extend(glob.glob(os.path.join(SCREENSHOT_DIR, ext)))
    if not files:
        return None
    return max(files, key=os.path.getmtime)

if __name__ == "__main__":
    img = latest_screenshot()
    if img is None:
        print("NO_SCREENSHOT_FOUND")
    else:
        mtime = datetime.fromtimestamp(os.path.getmtime(img))
        size_kb = os.path.getsize(img) / 1024
        print(f"LATEST_SCREENSHOT={img}")
        print(f"MODIFIED={mtime.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"SIZE={size_kb:.1f}KB")
