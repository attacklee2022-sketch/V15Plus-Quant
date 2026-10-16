# -*- coding: utf-8 -*-
"""通达信5分钟K线（.lc5）读取模块 — V15Plus 专用。"""
from __future__ import annotations
import os
import struct
from datetime import datetime
import pandas as pd

TDX_ROOTS = [r"D:\通达信", r"D:\tdx", r"C:\通达信", r"C:\tdx"]
TDX_ROOT = None
for r in TDX_ROOTS:
    if os.path.exists(r):
        TDX_ROOT = r
        break


def _decode_date(raw: int) -> datetime:
    """(year-2004)*2048 + month*100 + day"""
    year = raw // 2048 + 2004
    remaining = raw % 2048
    month = remaining // 100
    day = remaining % 100
    return datetime(year, month, day)


def _minline_path(code: str) -> str | None:
    if TDX_ROOT is None:
        return None
    if code.startswith(("60", "68")):
        fp = os.path.join(TDX_ROOT, "vipdoc", "sh", "fzline", f"sh{code}.lc5")
    else:
        fp = os.path.join(TDX_ROOT, "vipdoc", "sz", "fzline", f"sz{code}.lc5")
    return fp if os.path.exists(fp) else None


def load_5min(code: str, recent_days: int = 10) -> pd.DataFrame | None:
    """加载某只股票近N天的5分钟K线。"""
    fp = _minline_path(code)
    if fp is None:
        return None
    try:
        with open(fp, "rb") as f:
            data = f.read()
    except Exception:
        return None

    n = len(data) // 32
    if n < 10:
        return None

    rows = []
    for i in range(n):
        b = data[i * 32 : (i + 1) * 32]
        d = _decode_date(struct.unpack("<H", b[0:2])[0])
        t = struct.unpack("<H", b[2:4])[0]
        d = d.replace(hour=t // 60, minute=t % 60)
        rows.append({
            "datetime": d,
            "开盘": round(struct.unpack("<f", b[4:8])[0], 2),
            "最高": round(struct.unpack("<f", b[8:12])[0], 2),
            "最低": round(struct.unpack("<f", b[12:16])[0], 2),
            "收盘": round(struct.unpack("<f", b[16:20])[0], 2),
            "成交额": struct.unpack("<f", b[20:24])[0],
            "成交量": struct.unpack("<I", b[24:28])[0],
        })

    df = pd.DataFrame(rows).sort_values("datetime").reset_index(drop=True)
    df["date"] = df["datetime"].dt.date

    dates = sorted(df["date"].unique(), reverse=True)
    keep = set(dates[:recent_days])
    df = df[df["date"].isin(keep)].reset_index(drop=True)
    return df if len(df) >= 10 else None


def aggregate(df5: pd.DataFrame, period: int) -> pd.DataFrame | None:
    """聚合成更长周期（3=15min, 6=30min, 12=60min）。"""
    if df5 is None or len(df5) < period:
        return None
    rows = []
    for i in range(0, len(df5) - period + 1, period):
        chunk = df5.iloc[i : i + period]
        rows.append({
            "datetime": chunk.iloc[0]["datetime"],
            "date": chunk.iloc[0]["date"],
            "开盘": chunk.iloc[0]["开盘"],
            "最高": chunk["最高"].max(),
            "最低": chunk["最低"].min(),
            "收盘": chunk.iloc[-1]["收盘"],
            "成交量": chunk["成交量"].sum(),
            "成交额": chunk["成交额"].sum(),
        })
    if len(rows) < 5:
        return None
    df = pd.DataFrame(rows)
    df["ma5"] = df["收盘"].rolling(5).mean()
    df["ma20"] = df["收盘"].rolling(20).mean()
    return df
