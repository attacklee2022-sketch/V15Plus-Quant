# -*- coding: utf-8 -*-
"""
V15 Alpha — 板块历史数据分析模块
=============================
基于本地通达信 .day 日线文件，提供行业板块的历史分位、资金流、趋势分析。
与 v15_sector_fetcher（实时东财API）互补，不修改任何现有模块。

数据来源: D:\通达信\vipdoc\sh\lday\sh880xxx.day / sh881xxx.day
对照表:   _internal\sector_map.json
"""

from __future__ import annotations
import json
import os
import struct
import time as _time
from datetime import datetime, timedelta
from typing import Optional

# ── 路径配置 ─────────────────────────────────────────────
_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
_MAP_FILE = os.path.join(_MODULE_DIR, "sector_map.json")

# TDX 根目录 — 优先环境变量，与 stock_gui_V14.py 一致
TDX_ROOT = os.environ.get("TDX_ROOT_PATH", r"D:\通达信")

# ── 缓存 ─────────────────────────────────────────────────
_sector_map: dict | None = None       # 延迟加载
# code -> (file_mtime, records) — 检测到文件更新后自动刷新
_day_cache: dict[str, tuple[float, list]] = {}
_CACHE_MAX = 200                      # 最多缓存多少个板块


def _load_sector_map() -> dict:
    """加载板块代码↔名称对照表（延迟加载，模块级单例）。"""
    global _sector_map
    if _sector_map is not None:
        return _sector_map
    try:
        with open(_MAP_FILE, "r", encoding="utf-8") as f:
            _sector_map = json.load(f)
    except Exception:
        _sector_map = {"code2name": {}, "name2core": {}}
    return _sector_map


def list_sectors(category: str = None) -> list[dict]:
    """列出所有可用板块。

    Args:
        category: 可选筛选 — '行业板块', '概念板块', '风格板块', '地域板块'
    Returns:
        [{"code": "sh880301", "name": "煤炭", "category": "行业板块", "records": 5104}, ...]
    """
    m = _load_sector_map()
    result = []
    for code, info in m.get("code2name", {}).items():
        if category and info.get("category") != category:
            continue
        result.append({
            "code": code,
            "name": info["name"],
            "category": info.get("category", ""),
            "records": info.get("records", 0),
            "start": info.get("start", ""),
            "end": info.get("end", ""),
        })
    return sorted(result, key=lambda x: x["name"])


def name_to_code(name: str) -> str:
    """板块名称 → TDX 代码。例如 '煤炭' → 'sh880301'。"""
    m = _load_sector_map()
    return m.get("name2code", {}).get(name, "")


def code_to_name(code: str) -> str:
    """TDX 代码 → 板块名称。例如 'sh880301' → '煤炭'。"""
    m = _load_sector_map()
    return m.get("code2name", {}).get(code, {}).get("name", "")


# ── .day 文件读取 ────────────────────────────────────────

def _read_day_file(file_path: str) -> bytes | None:
    """读取通达信 .day 文件，文件共享冲突时重试3次（与 stock_gui_V14.py 一致）。"""
    last_err = None
    for attempt in range(3):
        try:
            with open(file_path, "rb") as f:
                return f.read()
        except PermissionError as e:
            last_err = e
            _time.sleep(0.3 * (attempt + 1))
        except Exception:
            raise
    print(f"[WARN] 通达信文件读写冲突（3次重试均失败）: {file_path} — {last_err}")
    return None


def _get_sector_day_path(code: str) -> str:
    """获取板块日线文件路径。板块指数都在 sh/lday/ 下。"""
    return os.path.join(TDX_ROOT, "vipdoc", "sh", "lday", f"{code}.day")


def _parse_day_records(data: bytes) -> list[dict]:
    """解析 .day 二进制数据为记录列表。
    每条记录 32 字节:
      0-3:  date   (uint32, YYYYMMDD)
      4-7:  open   (uint32, /100)
      8-11: high   (uint32, /100)
      12-15: low   (uint32, /100)
      16-19: close (uint32, /100)
      20-23: amount (float32, 成交额/元)
      24-27: volume (uint32, 成交量/股)
      28-31: reserved
    """
    n = len(data) // 32
    records = []
    for i in range(n):
        b = data[i * 32: (i + 1) * 32]
        date_code = struct.unpack("<I", b[0:4])[0]
        open_p = struct.unpack("<I", b[4:8])[0] / 100.0
        high_p = struct.unpack("<I", b[8:12])[0] / 100.0
        low_p = struct.unpack("<I", b[12:16])[0] / 100.0
        close_p = struct.unpack("<I", b[16:20])[0] / 100.0
        amount = struct.unpack("<f", b[20:24])[0]
        volume = struct.unpack("<I", b[24:28])[0]
        records.append({
            "date": date_code,
            "open": open_p, "high": high_p, "low": low_p, "close": close_p,
            "amount": amount, "volume": volume,
        })
    return records


def load_sector_history(code_or_name: str) -> list[dict] | None:
    """加载板块历史日线数据。

    Args:
        code_or_name: 板块代码 'sh880301' 或名称 '煤炭'
    Returns:
        [{date, open, high, low, close, amount, volume}, ...] 或 None
    """
    # 解析输入
    code = code_or_name if code_or_name.startswith("sh") else name_to_code(code_or_name)
    if not code:
        return None

    # 读文件
    path = _get_sector_day_path(code)
    if not os.path.exists(path):
        return None
    file_mtime = os.path.getmtime(path)

    # 查缓存 — 如果文件没变就返回缓存
    cached = _day_cache.get(code)
    if cached is not None:
        cached_mtime, cached_records = cached
        if abs(file_mtime - cached_mtime) < 1.0:  # 1秒内视为未变
            return cached_records

    data = _read_day_file(path)
    if not data:
        return None

    records = _parse_day_records(data)

    # 更新缓存（LRU: 超限删最旧）
    if len(_day_cache) >= _CACHE_MAX:
        oldest = next(iter(_day_cache))
        del _day_cache[oldest]
    _day_cache[code] = (file_mtime, records)
    return records


# ── 分析函数 ──────────────────────────────────────────────

def _date_to_int(d: datetime) -> int:
    return int(d.strftime("%Y%m%d"))


def _subset_by_window(records: list[dict], days: int) -> list[dict]:
    """按交易天数截取最近 N 条记录。"""
    if days <= 0 or days >= len(records):
        return records
    return records[-days:]


def calc_percentile(code_or_name: str, windows: list[int] = None) -> dict:
    """计算板块指数在多个时间窗口内的分位数。

    Args:
        code_or_name: 板块代码或名称
        windows: 时间窗口，如 [60, 120, 250, 750, 0]
                 0 表示全历史。默认 [60, 120, 250, 750, 1250, 0]
    Returns:
        {
            "code": "sh880301",
            "name": "煤炭",
            "latest_date": 20260608,
            "latest_close": 2119.76,
            "all_time_high": 2119.76,
            "all_time_low": 138.40,
            "percentiles": {
                "近60日": {"pct": 100.0, "high": 2119.76, "low": 1839.13},
                ...
            }
        }
    """
    if windows is None:
        windows = [60, 120, 250, 750, 1250, 0]
    window_labels = {60: "近60日", 120: "近半年", 250: "近1年",
                     750: "近3年", 1250: "近5年", 0: "全历史"}

    records = load_sector_history(code_or_name)
    if not records:
        return {}

    code = code_or_name if code_or_name.startswith("sh") else name_to_code(code_or_name)
    name = code_to_name(code) or code_or_name
    latest = records[-1]

    all_closes = [r["close"] for r in records]
    result = {
        "code": code,
        "name": name,
        "latest_date": latest["date"],
        "latest_close": latest["close"],
        "all_time_high": max(all_closes),
        "all_time_low": min(all_closes),
        "percentiles": {},
    }

    for w in windows:
        subset = _subset_by_window(records, w)
        closes = [r["close"] for r in subset]
        pct = (sum(1 for c in closes if c <= latest["close"]) / len(closes)) * 100
        label = window_labels.get(w, f"近{w}日")
        result["percentiles"][label] = {
            "pct": round(pct, 1),
            "high": round(max(closes), 2),
            "low": round(min(closes), 2),
        }

    return result


def calc_fund_flow(code_or_name: str, short: int = 5, long: int = 20) -> dict:
    """计算板块资金流向。

    Args:
        code_or_name: 板块代码或名称
        short: 短期窗口（默认5日）
        long:  长期窗口（默认20日）
    Returns:
        {
            "short_avg_amount": 262.2,   # 短期日均成交额(亿)
            "long_avg_amount": 164.0,    # 长期日均成交额(亿)
            "volume_ratio": 1.60,        # 量比
            "signal": "放量",            # 放量/缩量/持平
            "change_5d": 2.80,           # 近5日涨跌%
            "change_20d": 11.63,         # 近20日涨跌%
        }
    """
    records = load_sector_history(code_or_name)
    if not records or len(records) < max(short, long) + 1:
        return {}

    code = code_or_name if code_or_name.startswith("sh") else name_to_code(code_or_name)
    name = code_to_name(code) or code_or_name

    short_records = records[-short:]
    long_records = records[-long:]

    short_amt = sum(r["amount"] for r in short_records) / short / 1e8  # 亿
    long_amt = sum(r["amount"] for r in long_records) / long / 1e8
    ratio = round(short_amt / long_amt, 2) if long_amt > 0 else 0

    if ratio > 1.1:
        signal = "放量"
    elif ratio < 0.9:
        signal = "缩量"
    else:
        signal = "持平"

    chg_5d = round((records[-1]["close"] - records[-short - 1]["close"]) / records[-short - 1]["close"] * 100, 2)
    chg_20d = round((records[-1]["close"] - records[-long - 1]["close"]) / records[-long - 1]["close"] * 100, 2) if len(records) > long else 0

    return {
        "code": code,
        "name": name,
        "short_avg_amount": round(short_amt, 1),
        "long_avg_amount": round(long_amt, 1),
        "volume_ratio": ratio,
        "signal": signal,
        "change_5d": chg_5d,
        "change_20d": chg_20d,
    }


def calc_sector_ohlcv(code_or_name: str, lookback: int = 60) -> dict:
    """获取板块最近 N 日的 OHLCV 数据（用于画图）。

    Returns:
        {"code": "sh880301", "name": "煤炭",
         "dates": [...], "open": [...], "high": [...],
         "low": [...], "close": [...], "volume": [...], "amount": [...]}
    """
    records = load_sector_history(code_or_name)
    if not records:
        return {}

    code = code_or_name if code_or_name.startswith("sh") else name_to_code(code_or_name)
    name = code_to_name(code) or code_or_name
    subset = records[-lookback:] if lookback > 0 else records

    return {
        "code": code,
        "name": name,
        "dates": [r["date"] for r in subset],
        "open": [r["open"] for r in subset],
        "high": [r["high"] for r in subset],
        "low": [r["low"] for r in subset],
        "close": [r["close"] for r in subset],
        "volume": [r["volume"] for r in subset],
        "amount": [r["amount"] for r in subset],
    }


def rank_sectors(
    metric: str = "change_5d",
    category: str = None,
    top_n: int = 10,
) -> list[dict]:
    """板块排名。

    Args:
        metric: 排名指标 — 'change_5d', 'change_20d', 'percentile_1y', 'volume_ratio'
        category: 筛选分类 — '行业板块', '概念板块', '风格板块', '地域板块'
        top_n: 返回前N名（正数=涨幅榜，负数=跌幅榜）
    Returns:
        [{"code": "sh880301", "name": "煤炭", "value": 2.80}, ...]
    """
    sectors = list_sectors(category=category)
    results = []

    for s in sectors:
        code = s["code"]
        name = s["name"]

        if metric in ("change_5d", "change_20d", "volume_ratio"):
            flow = calc_fund_flow(code)
            if not flow:
                continue
            val = flow.get(metric, 0)
        elif metric == "percentile_1y":
            pct = calc_percentile(code, windows=[250])
            if not pct:
                continue
            val = pct["percentiles"].get("近1年", {}).get("pct", 50)
        else:
            continue

        results.append({"code": code, "name": name, "value": val})

    reverse = metric != "percentile_1y"  # 分位不是越高越好，别的指标是
    results.sort(key=lambda x: x["value"], reverse=reverse)

    if top_n > 0:
        return results[:top_n]
    else:
        return results[top_n:]  # 负数为跌幅榜


def get_sector_snapshot(code_or_name: str) -> dict:
    """板块快照：分位 + 资金流 + 趋势，一个接口全返回。"""
    name = code_or_name if not code_or_name.startswith("sh") else code_to_name(code_or_name)
    code = code_or_name if code_or_name.startswith("sh") else name_to_code(code_or_name)

    if not code:
        return {}

    pct = calc_percentile(code)
    flow = calc_fund_flow(code)
    ohlcv = calc_sector_ohlcv(code, lookback=60)

    if not pct or not flow:
        return {}

    return {
        "code": code,
        "name": name,
        "latest": {
            "date": pct["latest_date"],
            "close": pct["latest_close"],
            "all_time_high": pct["all_time_high"],
            "all_time_low": pct["all_time_low"],
        },
        "percentiles": pct["percentiles"],
        "fund_flow": {
            "volume_ratio": flow["volume_ratio"],
            "signal": flow["signal"],
            "short_avg_amount": flow["short_avg_amount"],
            "long_avg_amount": flow["long_avg_amount"],
        },
        "change": {
            "d5": flow["change_5d"],
            "d20": flow["change_20d"],
        },
        "ohlcv": ohlcv,
    }


# ── 批量预加载（加速） ────────────────────────────────────

def preload_main_sectors() -> dict[str, list]:
    """预加载 56 个主流行业板块数据到缓存。首次调用约需 1-2 秒。"""
    sectors = list_sectors(category="行业板块(56个)")
    loaded = {}
    for s in sectors:
        records = load_sector_history(s["code"])
        if records:
            loaded[s["code"]] = records
    return loaded


def clear_cache():
    """清空日线缓存。zip 下载或软件更新数据后调用。"""
    _day_cache.clear()
    _sector_map_cache = None  # force reload sector_map too


# ── 自测 ──────────────────────────────────────────────────
if __name__ == "__main__":
    print("=== 板块历史分析模块 — 自测 ===\n")

    # 1. 列出行业板块
    sectors = list_sectors(category="行业板块(56个)")
    print(f"主流行业板块: {len(sectors)} 个")
    for s in sectors[:5]:
        print(f"  {s['code']} {s['name']} ({s['records']}条)")

    # 2. 名称↔代码互查
    print(f"\n'煤炭' -> {name_to_code('煤炭')}")
    print(f"'sh880301' -> {code_to_name('sh880301')}")

    # 3. 板块快照
    print("\n--- 煤炭 快照 ---")
    snap = get_sector_snapshot("煤炭")
    for k, v in snap.items():
        if k == "ohlcv":
            print(f"  {k}: {len(v.get('close',[]))} 条K线")
        else:
            print(f"  {k}: {v}")

    # 4. 涨幅榜
    print("\n--- 近5日涨幅榜 Top5 ---")
    top5 = rank_sectors(metric="change_5d", category="行业板块(56个)", top_n=5)
    for r in top5:
        print(f"  {r['name']}: {r['value']:+.2f}%")

    print("\n自测完成 - OK")
