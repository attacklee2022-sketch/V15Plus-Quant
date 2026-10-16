# -*- coding: utf-8 -*-
"""
K 线数据新鲜度监控 + 自动扫描。
==================================
15:30 后每 15 分钟检查一次通达信 .day 文件，
一旦检测到今日 K 线数据已更新，自动启动 V15Plus 全流程扫描并上传云端。

用法：
    python scripts/kline_watcher.py                        # 全流程
    python scripts/kline_watcher.py --once                  # 只检查一次，不循环
    python scripts/kline_watcher.py --cloud-url http://xxx
"""
from __future__ import annotations
import os
import sys
import struct
import time
from datetime import datetime

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.dirname(_SCRIPT_DIR)
for p in [_APP_DIR]:
    if p not in sys.path:
        sys.path.insert(0, p)

TDX_ROOT = os.environ.get("TDX_ROOT_PATH", r"D:\通达信")
DEFAULT_CLOUD_URL = os.environ.get("CLOUD_URL", "http://your-server.example.com/research")
CHECK_INTERVAL = 900  # 15 分钟
CHECK_START = "15:30"  # 15:30 开始检查

# 检查用的样本股票（覆盖沪/深/主板/创业板/科创板）
CHECK_CODES = ["600519", "000001", "300308", "002230", "688981"]


def get_tdx_file_path(code: str) -> str:
    if code.startswith(("60", "68", "688")):
        return os.path.join(TDX_ROOT, "vipdoc", "sh", "lday", f"sh{code}.day")
    return os.path.join(TDX_ROOT, "vipdoc", "sz", "lday", f"sz{code}.day")


def get_latest_date() -> int | None:
    """检查样本股票，返回最新的 K 线日期（通达信格式）。"""
    latest = 0
    for code in CHECK_CODES:
        fp = get_tdx_file_path(code)
        if not os.path.exists(fp):
            continue
        try:
            with open(fp, "rb") as f:
                data = f.read()
            if len(data) < 32:
                continue
            last = data[-32:]
            date_code = struct.unpack("<I", last[0:4])[0]
            if date_code > latest:
                latest = date_code
        except Exception:
            continue
    return latest if latest > 0 else None


def wait_for_today_data() -> bool:
    """
    循环检查 K 线数据是否更新到今日。
    返回 True 表示检测到今日数据，False 表示超时（超过 21:00 不再等）。
    """
    today = int(datetime.now().strftime("%Y%m%d"))

    while True:
        now = datetime.now()
        # 超过 21:00 不再等，防通宵
        if now.hour >= 21:
            print(f"  [超时] 已过 21:00，今日扫描取消")
            return False

        latest = get_latest_date()
        if latest is None:
            print(f"  [{now:%H:%M}] 无法读取 K 线数据，等待中...")
        elif latest >= today:
            print(f"  [{now:%H:%M}] 检测到今日数据! (最新日期: {latest})")
            return True
        else:
            print(f"  [{now:%H:%M}] 最新 K 线日期: {latest}，今日({today})未更新，等待中...")

        time.sleep(CHECK_INTERVAL)


def run_scan(cloud_url: str):
    """执行 V15Plus 全流程扫描并上传。"""
    import subprocess
    subprocess.run([
        sys.executable, "scripts/v15plus_auto_pipeline.py",
        "--hybrid-url", cloud_url,
        "--phase", "close",
    ], cwd=_APP_DIR)


# ═══════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════

def main():
    import argparse
    parser = argparse.ArgumentParser(description="K 线监控 + V15Plus 自动扫描")
    parser.add_argument("--once", action="store_true", help="只检查一次")
    parser.add_argument("--cloud-url", default=DEFAULT_CLOUD_URL)
    args = parser.parse_args()

    print("=" * 50)
    print(f"  K 线监控 + V15Plus 自动扫描")
    print(f"  启动时间: {datetime.now():%Y-%m-%d %H:%M:%S}")
    print(f"  监控股票: {', '.join(CHECK_CODES)}")
    print(f"  检查间隔: {CHECK_INTERVAL//60} 分钟")
    print(f"  云端地址: {args.cloud_url}")
    print("=" * 50)

    if args.once:
        # 只检查一次
        latest = get_latest_date()
        today = int(datetime.now().strftime("%Y%m%d"))
        if latest and latest >= today:
            print(f"\n[检测] 今日数据已就绪 (最新: {latest})")
            print(f"\n[启动] V15Plus 全流程扫描...")
            run_scan(args.cloud_url)
        else:
            print(f"\n[检测] 最新 K 线日期: {latest}，今日尚未更新")
        return

    # 循环模式
    now = datetime.now()
    start_h, start_m = [int(x) for x in CHECK_START.split(":")]

    # 如果当前时间在 15:30 之前，等到 15:30
    if now.hour < start_h or (now.hour == start_h and now.minute < start_m):
        wait_seconds = (start_h - now.hour) * 3600 - now.minute * 60 + start_m * 60
        print(f"\n[等待] 当前 {now:%H:%M}，距离 {CHECK_START} 还有 {wait_seconds//60} 分钟")
        time.sleep(wait_seconds)

    # 开始监控
    print(f"\n[监控] 开始检查 K 线数据新鲜度...")
    if wait_for_today_data():
        print(f"\n[启动] V15Plus 全流程扫描上传...")
        run_scan(args.cloud_url)
        print(f"\n[完成] 全流程结束 {datetime.now():%Y-%m-%d %H:%M:%S}")
    else:
        print(f"\n[结束] 今日扫描已跳过")


if __name__ == "__main__":
    main()
