# -*- coding: utf-8 -*-
"""
V15Alpha 全市场回测验证脚本
============================
遍历所有自选股的全部历史数据，在每个交易日：
  1. 检测技术形态（新旧两套引擎）
  2. 计算综合评分（旧系统 calc_composite_score）
  3. 计算择时信号（新系统 compute_signal）
  4. 检查未来 N 日收益
最后聚合统计验证有效性。

输出：控制台摘要 + backtest_report.txt
"""

from __future__ import annotations
import os
import sys
import struct
import time
import json
from datetime import datetime
from collections import defaultdict

import pandas as pd
import numpy as np

# ── 导入 V2 引擎（形态检测 + 技术评分） ──
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_SCRIPT_DIR, "..", "_internal"))
from v14_pattern_detector import detect_all_patterns as v2_detect_all
from v14_tech_scorer import compute_signal as v2_compute_signal
from v15_scorer import compute_v15_signal
from v15_scenario_engine import compute_scenario_signal
from v15_filter import compute_filtered_signal

# ── 配置 ──
APP_DIR = os.path.dirname(_SCRIPT_DIR)
TDX_ROOT = os.environ.get("TDX_ROOT_PATH", r"D:\通达信")
SELF_STOCK_FILE = os.path.join(APP_DIR, "self_stock.txt")
BACKTEST_YEARS = 3  # 只用最近3年数据
FORWARD_DAYS = [5, 10, 20, 60]  # 多周期验证
PROFIT_THRESHOLD = 0.03  # 盈利阈值 3%

# ============================================================
# 1. 通达信日线解析（复用 stock_gui_V14.py 逻辑）
# ============================================================

def _read_tdx_day_file(file_path: str) -> bytes | None:
    import time as _time
    for attempt in range(3):
        try:
            with open(file_path, "rb") as f:
                return f.read()
        except PermissionError as e:
            _time.sleep(0.3 * (attempt + 1))
        except Exception:
            raise
    return None


def parse_tdx_day(file_path):
    data = _read_tdx_day_file(file_path)
    if data is None:
        return None
    try:
        row_cnt = len(data) // 32
        rows = []
        for i in range(row_cnt):
            b = data[i * 32 : (i + 1) * 32]
            date_code = struct.unpack("<I", b[0:4])[0]
            open_p = struct.unpack("<I", b[4:8])[0] / 100
            high_p = struct.unpack("<I", b[8:12])[0] / 100
            low_p = struct.unpack("<I", b[12:16])[0] / 100
            close_p = struct.unpack("<I", b[16:20])[0] / 100
            vol = struct.unpack("<I", b[24:28])[0]
            rows.append([str(date_code), open_p, high_p, low_p, close_p, vol])

        df = pd.DataFrame(
            rows, columns=["date", "开盘", "最高", "最低", "收盘", "成交量"]
        )
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)
        df["ma5"] = df["收盘"].rolling(5).mean()
        df["ma10"] = df["收盘"].rolling(10).mean()
        df["ma20"] = df["收盘"].rolling(20).mean()
        df["ma60"] = df["收盘"].rolling(60).mean()
        df["vol_ma10"] = df["成交量"].rolling(10).mean()
        df["vol_ma20"] = df["成交量"].rolling(20).mean()
        df["macd"], df["macd_signal"], df["macd_hist"] = _calc_macd(df["收盘"])
        df["kdj_k"], df["kdj_d"] = _calc_kdj(df["最高"], df["最低"], df["收盘"])
        df["rsi"] = _calc_rsi(df["收盘"], 14)
        df["boll_mid"] = df["收盘"].rolling(20).mean()
        df["boll_std"] = df["收盘"].rolling(20).std()
        df["boll_up"] = df["boll_mid"] + 2 * df["boll_std"]
        df["boll_down"] = df["boll_mid"] - 2 * df["boll_std"]
        return df
    except Exception as e:
        print(f"[ERROR] 解析失败 {file_path}: {e}")
        return None


def _calc_macd(close, fast=12, slow=26, signal=9):
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd = ema_fast - ema_slow
    sig = macd.ewm(span=signal, adjust=False).mean()
    hist = macd - sig
    return macd, sig, hist


def _calc_kdj(high, low, close, n=9, m=3):
    ll = low.rolling(n).min()
    hh = high.rolling(n).max()
    rsv = (close - ll) / (hh - ll) * 100
    rsv = rsv.fillna(50)
    k = rsv.ewm(com=m - 1, adjust=False).mean()
    d = k.ewm(com=m - 1, adjust=False).mean()
    return k, d


def _calc_rsi(close, period=14):
    delta = close.diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)


def get_tdx_file(code):
    if code.startswith(("60", "68")):
        return os.path.join(TDX_ROOT, "vipdoc", "sh", "lday", f"sh{code}.day")
    else:
        return os.path.join(TDX_ROOT, "vipdoc", "sz", "lday", f"sz{code}.day")


def load_stock_tdx(code):
    fp = get_tdx_file(code)
    if not os.path.exists(fp):
        return None
    df = parse_tdx_day(fp)
    if df is not None:
        cut_date = datetime.now() - pd.Timedelta(days=365 * BACKTEST_YEARS)
        df = df[df["date"] >= cut_date].reset_index(drop=True)
    return df


# ============================================================
# 2. 旧系统（GUI版）形态检测（stock_gui_V14.py）
# ============================================================

def gui_detect_all_patterns(df):
    """老版形态检测，返回 (hit_patterns列表, pat字典)"""
    pat = {}
    if len(df) < 60:
        return [], pat
    last = df.iloc[-1]
    pre1 = df.iloc[-2]
    pre5 = df.iloc[-5]

    pat["多头排列"] = last["ma5"] > last["ma10"] > last["ma20"] > last["ma60"]
    pat["空头排列"] = last["ma5"] < last["ma10"] < last["ma20"] < last["ma60"]
    pat["站稳60日线"] = last["收盘"] > last["ma60"]
    pat["跌破60日线"] = last["收盘"] < last["ma60"]

    pat["5日线金叉10日线"] = pre1["ma5"] < pre1["ma10"] and last["ma5"] > last["ma10"]
    pat["5日线死叉10日线"] = pre1["ma5"] > pre1["ma10"] and last["ma5"] < last["ma10"]
    pat["5日线金叉20日线"] = pre1["ma5"] < pre1["ma20"] and last["ma5"] > last["ma20"]
    pat["5日线死叉20日线"] = pre1["ma5"] > pre1["ma20"] and last["ma5"] < last["ma20"]

    pat["金针探底"] = (
        (last["最低"] < last["开盘"])
        and (last["收盘"] > last["最低"])
        and ((last["开盘"] - last["最低"]) > abs(last["收盘"] - last["开盘"]) * 2)
    )
    pat["阳包阴"] = (
        (last["收盘"] > last["开盘"])
        and (last["开盘"] < pre1["收盘"])
        and (last["收盘"] > pre1["开盘"])
    )
    pat["放量长阳"] = (
        last["收盘"] > last["开盘"]
        and last["成交量"] > last["vol_ma20"] * 1.5
    )
    pat["缩量回调"] = (
        last["收盘"] < pre1["收盘"]
        and last["成交量"] < last["vol_ma10"] * 0.7
    )

    recent_high = df["最高"].iloc[-20:].max()
    recent_low = df["最低"].iloc[-20:].min()
    pat["放量突破压力"] = (
        last["收盘"] >= recent_high
        and last["成交量"] > last["vol_ma20"] * 1.6
    )
    pat["缩量回踩支撑"] = (
        last["收盘"] <= recent_low * 1.02
        and last["成交量"] < last["vol_ma10"] * 0.65
    )

    pat["量价顶背离"] = (
        last["收盘"] > pre5["收盘"]
        and last["成交量"] < pre5["vol_ma20"]
    )
    pat["量价底背离"] = (
        last["收盘"] < pre5["收盘"]
        and last["成交量"] > pre5["vol_ma20"]
    )

    pat["布林上轨压力"] = (
        last["最高"] >= last["boll_up"] and last["收盘"] < last["boll_up"]
    )
    pat["布林下轨支撑"] = (
        last["最低"] <= last["boll_down"] and last["收盘"] > last["boll_down"]
    )

    # 老鸭头
    duck = _gui_detect_duck_head(df)
    if duck["valid"]:
        pat["老鸭头形态"] = True
    elif duck["status"] == "成形未突破":
        pat["老鸭头形态"] = True

    # 失效检测
    _gui_check_failures(df, pat)

    hit_patterns = [k for k, v in pat.items() if v]
    return hit_patterns, pat


def _gui_detect_duck_head(df):
    recent = df.tail(60).copy()
    result = {"valid": False, "status": "未成形", "fail_reasons": []}
    if len(recent) < 40:
        return result
    head_idx = recent["最高"].idxmax()
    head_price = recent.loc[head_idx, "最高"]
    if recent.index.get_loc(head_idx) > len(recent) // 2 + 5:
        return result
    post_head = recent.loc[head_idx:]
    if len(post_head) < 5:
        return result
    mouth_idx = post_head["最低"].idxmin()
    mouth_price = post_head.loc[mouth_idx, "最低"]
    mouth_ma60 = post_head.loc[mouth_idx, "ma60"]
    if pd.notna(mouth_ma60) and mouth_price < mouth_ma60:
        result["status"] = "成形失效"
        return result
    ma60_now = recent["ma60"].iloc[-1]
    ma60_10ago = recent["ma60"].iloc[-10] if len(recent) >= 10 else recent["ma60"].iloc[0]
    if pd.isna(ma60_now) or pd.isna(ma60_10ago):
        return result
    if ma60_now <= ma60_10ago:
        result["status"] = "成形失效"
        return result
    post_mouth = recent.loc[mouth_idx:]
    narrow_mask = (post_mouth["最高"] - post_mouth["最低"]) / post_mouth["收盘"] < 0.03
    if narrow_mask.any():
        nose_idx = post_mouth[narrow_mask].index[0]
    else:
        nose_idx = mouth_idx
    post_nose = recent.loc[nose_idx:]
    for idx, row in post_nose.iterrows():
        if row["收盘"] > head_price * 0.98:
            result["valid"] = True
            result["status"] = "成形有效"
            return result
    result["status"] = "成形未突破"
    return result


def _gui_check_failures(df, pat):
    if len(df) < 25:
        return
    last = df.iloc[-1]
    recent_20 = df.tail(20)
    recent_high = recent_20["最高"].max()
    recent_low = recent_20["最低"].min()

    # 假突破
    for i in range(1, min(7, len(df))):
        row = df.iloc[-(i + 1)]
        if row["收盘"] >= recent_high * 0.98:
            drop = (recent_high - last["收盘"]) / recent_high * 100
            if drop > 5:
                pat["假突破"] = True
                break
    # 破位
    if last["收盘"] < recent_low and last["成交量"] > last["vol_ma20"] * 1.2:
        pat["破位"] = True
    # 金针探底失效
    for i in range(1, min(15, len(df) - 1)):
        row = df.iloc[-(i + 1)]
        if (
            row["最低"] < row["开盘"]
            and row["收盘"] > row["最低"]
            and (row["开盘"] - row["最低"]) > abs(row["收盘"] - row["开盘"]) * 2
        ):
            if last["收盘"] < row["最低"]:
                pat["金针探底失效"] = True
                break
    # 阳包阴失效
    for i in range(1, min(7, len(df) - 2)):
        row = df.iloc[-(i + 1)]
        row_prev = df.iloc[-(i + 2)]
        if (
            row["收盘"] > row["开盘"]
            and row["开盘"] < row_prev["收盘"]
            and row["收盘"] > row_prev["开盘"]
        ):
            if last["收盘"] < row["最低"]:
                pat["阳包阴失效"] = True
                break


# ============================================================
# 3. 旧系统评分 (calc_composite_score) — 向量化版本
# ============================================================

def gui_calc_composite_score(hit_patterns):
    """纯基于形态得分的回测用评分（不用回测胜率修正，因为回测中比的就是未来收益）。"""
    bull_patterns = {
        "多头排列", "站稳60日线", "5日线金叉10日线", "5日线金叉20日线",
        "金针探底", "阳包阴", "放量长阳", "放量突破压力", "缩量回踩支撑",
        "量价底背离", "布林下轨支撑", "老鸭头形态",
    }
    bear_patterns = {
        "空头排列", "跌破60日线", "5日线死叉10日线", "5日线死叉20日线",
        "缩量回调", "量价顶背离", "布林上轨压力",
    }
    score = 50
    for p in hit_patterns:
        if p in bull_patterns:
            score += 8
        if p in bear_patterns:
            score -= 8
    return max(0, min(100, round(score, 1)))


# ============================================================
# 4. 新系统 V2 的向量化遍历（回测用）
# ============================================================

def v2_backtest_on_df(df):
    """
    遍历 df 的每个交易日，调用 V2 引擎的 detect_all_patterns + compute_signal。
    返回 records: list of dict，每个元素是某日的信号 + 未来收益。
    """
    records = []
    n = len(df)
    max_fwd = max(FORWARD_DAYS)
    if n < 60 + max_fwd:
        return records

    closes = df["收盘"].values.tolist()
    highs = df["最高"].values.tolist()
    lows = df["最低"].values.tolist()
    opens = df["开盘"].values.tolist()
    volumes = df["成交量"].values.tolist()
    dates = df["date"].dt.strftime("%Y-%m-%d").values.tolist()

    for i in range(60, n - max_fwd):
        # ── 切片到当前日 ──
        c = closes[: i + 1]
        h = highs[: i + 1]
        l_ = lows[: i + 1]
        o = opens[: i + 1]
        v = volumes[: i + 1]
        d = dates[: i + 1]

        current_close = closes[i]
        current_vol = volumes[i]
        prev_close = opens[i] if i > 0 else closes[i]

        # 形态检测
        v2_patterns, v2_base_score = v2_detect_all(d, o, h, l_, c, v)

        # 择时信号
        try:
            sig = v2_compute_signal(c, h, l_, v, current_close, current_vol, prev_close)
        except Exception:
            continue

        # 未来收益
        fwd_ret = {}
        for fd in FORWARD_DAYS:
            future_p = closes[i + fd]
            fwd_ret[fd] = (future_p - current_close) / current_close

        records.append({
            "date": d[-1],
            "close": current_close,
            "v2_patterns": [p.name for p in v2_patterns],
            "v2_base_score": v2_base_score,
            "v2_level": sig["level"],
            "v2_regime": sig["regime"],
            "v2_score": sig["score"],
            "v2_mr": sig["mr_score"],
            "v2_tf": sig["tf_score"],
            "fwd_ret": fwd_ret,
        })

    return records


def gui_backtest_on_df(df):
    """
    遍历 df 的每个交易日，调用 GUI 旧版的 detect_all_patterns + calc_composite_score。
    """
    records = []
    n = len(df)
    max_fwd = max(FORWARD_DAYS)
    if n < 60 + max_fwd:
        return records

    for i in range(60, n - max_fwd):
        sub = df.iloc[: i + 1].copy()
        current_close = float(sub["收盘"].iloc[-1])

        hit_patterns, _ = gui_detect_all_patterns(sub)
        score = gui_calc_composite_score(hit_patterns)

        fwd_ret = {}
        for fd in FORWARD_DAYS:
            future_p = float(df["收盘"].iloc[i + fd])
            fwd_ret[fd] = (future_p - current_close) / current_close

        records.append({
            "date": str(sub["date"].iloc[-1].date()),
            "close": current_close,
            "gui_patterns": hit_patterns,
            "gui_score": score,
            "fwd_ret": fwd_ret,
        })

    return records


# ============================================================
# 4b. V15 新引擎的向量化遍历（阶段二）
# ============================================================

def v15_backtest_on_df(df):
    """
    遍历 df 的每个交易日，调用 V15 引擎的 compute_v15_signal。
    返回 records: list of dict
    """
    records = []
    n = len(df)
    max_fwd = max(FORWARD_DAYS)
    if n < 60 + max_fwd:
        return records

    closes = df["收盘"].values.tolist()
    highs = df["最高"].values.tolist()
    lows = df["最低"].values.tolist()
    volumes = df["成交量"].values.tolist()
    dates = df["date"].dt.strftime("%Y-%m-%d").values.tolist()

    for i in range(60, n - max_fwd):
        # ── 切片到当前日 ──
        c = closes[: i + 1]
        h = highs[: i + 1]
        l_ = lows[: i + 1]
        v = volumes[: i + 1]

        current_close = closes[i]
        current_vol = volumes[i]

        # V15 信号
        try:
            sig = compute_v15_signal(c, h, l_, v, current_close, current_vol)
        except Exception:
            continue

        # 未来收益
        fwd_ret = {}
        for fd in FORWARD_DAYS:
            future_p = closes[i + fd]
            fwd_ret[fd] = (future_p - current_close) / current_close

        records.append({
            "date": dates[i],
            "close": current_close,
            "v15_score": sig["score"],
            "v15_level": sig["level"],
            "v15_rev": sig["rev_score"],
            "v15_macd": sig["macd_score"],
            "v15_vol": sig["vol_score"],
            "v15_safe": sig["safe_score"],
            "fwd_ret": fwd_ret,
        })

    return records


# ============================================================
# 4c. V15 场景引擎的向量化遍历（阶段三）
# ============================================================

def scenario_backtest_on_df(df):
    """
    遍历 df 的每个交易日，调用 V15 场景引擎的 compute_scenario_signal。
    返回 records: list of dict
    """
    records = []
    n = len(df)
    max_fwd = max(FORWARD_DAYS)
    if n < 60 + max_fwd:
        return records

    closes = df["收盘"].values.tolist()
    highs = df["最高"].values.tolist()
    lows = df["最低"].values.tolist()
    volumes = df["成交量"].values.tolist()
    dates = df["date"].dt.strftime("%Y-%m-%d").values.tolist()

    for i in range(60, n - max_fwd):
        c = closes[: i + 1]
        h = highs[: i + 1]
        l_ = lows[: i + 1]
        v = volumes[: i + 1]

        current_close = closes[i]
        current_vol = volumes[i]

        try:
            sig = compute_scenario_signal(c, h, l_, v, current_close, current_vol)
        except Exception:
            continue

        fwd_ret = {}
        for fd in FORWARD_DAYS:
            future_p = closes[i + fd]
            fwd_ret[fd] = (future_p - current_close) / current_close

        records.append({
            "date": dates[i],
            "close": current_close,
            "scenario_score": sig["score"],
            "scenario_level": sig["level"],
            "scenario_name": sig["scenario"],
            "raw_v15_score": sig["raw_v15_score"],
            "fwd_ret": fwd_ret,
        })

    return records


# ============================================================
# 4d. V15 过滤引擎的向量化遍历（阶段四）
# ============================================================

def filtered_backtest_on_df(df):
    """
    遍历 df 的每个交易日，调用 V15 过滤引擎的 compute_filtered_signal。
    返回 records: list of dict
    """
    records = []
    n = len(df)
    max_fwd = max(FORWARD_DAYS)
    if n < 60 + max_fwd:
        return records

    closes = df["收盘"].values.tolist()
    highs = df["最高"].values.tolist()
    lows = df["最低"].values.tolist()
    volumes = df["成交量"].values.tolist()
    dates = df["date"].dt.strftime("%Y-%m-%d").values.tolist()

    for i in range(60, n - max_fwd):
        c = closes[: i + 1]
        h = highs[: i + 1]
        l_ = lows[: i + 1]
        v = volumes[: i + 1]

        current_close = closes[i]
        current_vol = volumes[i]

        # 多周期过滤需要至少 60 + 20 天数据，所以 i >= 80 才启用全部过滤
        # 但即使数据不足，过滤引擎也能给出合理结果（降级为场景引擎原始分）
        try:
            sig = compute_filtered_signal(c, h, l_, v, current_close, current_vol)
        except Exception:
            continue

        fwd_ret = {}
        for fd in FORWARD_DAYS:
            future_p = closes[i + fd]
            fwd_ret[fd] = (future_p - current_close) / current_close

        records.append({
            "date": dates[i],
            "close": current_close,
            "filtered_score": sig["score"],
            "filtered_level": sig["level"],
            "scenario_name": sig["scenario"],
            "scenario_score": sig["scenario_score"],
            "resonance_consistency": sig["resonance"]["consistency"],
            "resonance_direction": sig["resonance"].get("direction", "中性"),
            "divergence_type": sig.get("divergence", {}).get("type", "无"),
            "divergence_adj": sig.get("divergence", {}).get("adjustment", 0),
            "momentum_divergence_type": sig.get("momentum_divergence", {}).get("type", "无"),
            "momentum_divergence_adj": sig.get("momentum_divergence", {}).get("adjustment", 0),
            "atr_norm": sig["volatility"]["atr_norm"],
            "filter_adjustment": sig["filter_adjustment"],
            "fwd_ret": fwd_ret,
        })

    return records


# ============================================================
# 5. 统计聚合
# ============================================================

def aggregate_by_group(records, key_fn, label_fn, forward_day=10):
    """按 key_fn 分组，统计每组 N 日后的收益。"""
    groups = defaultdict(list)
    for r in records:
        key = key_fn(r)
        groups[key].append(r["fwd_ret"][forward_day])

    results = []
    for key in sorted(groups.keys(), key=lambda k: str(k)):
        rets = groups[key]
        n = len(rets)
        mean_ret = np.mean(rets)
        median_ret = np.median(rets)
        win_rate = sum(1 for x in rets if x >= PROFIT_THRESHOLD) / n * 100
        loss_rate = sum(1 for x in rets if x <= -PROFIT_THRESHOLD) / n * 100
        std_ret = np.std(rets)
        sharpe = mean_ret / std_ret * np.sqrt(252) if std_ret > 0 else 0

        results.append({
            "label": label_fn(key),
            "count": n,
            "mean_ret_pct": round(mean_ret * 100, 2),
            "median_ret_pct": round(median_ret * 100, 2),
            "win_rate_pct": round(win_rate, 2),
            "loss_rate_pct": round(loss_rate, 2),
            "std_pct": round(std_ret * 100, 2),
            "sharpe": round(sharpe, 2),
            # 盈亏比
            "avg_win": round(
                np.mean([r for r in rets if r >= 0]) * 100, 2
            ) if any(r >= 0 for r in rets) else 0,
            "avg_loss": round(
                abs(np.mean([r for r in rets if r < 0])) * 100, 2
            ) if any(r < 0 for r in rets) else 0,
            "profit_loss_ratio": round(
                abs(np.mean([r for r in rets if r >= 0]) / np.mean([r for r in rets if r < 0])), 2
            ) if any(r >= 0 for r in rets) and any(r < 0 for r in rets) else 0,
        })

    return results


def aggregate_forward_periods(records, key_fn, label_fn):
    """多周期聚合，返回 {forward_day: [results]}"""
    return {fd: aggregate_by_group(records, key_fn, label_fn, fd) for fd in FORWARD_DAYS}


def print_results_table(results_list, title, top_n=30):
    """打印结果表格。"""
    lines = [f"\n{'='*100}", f"  {title}", f"{'='*100}"]
    header = (
        f"{'组别':<20} {'次数':>6} {'均值%':>7} {'中位%':>7} "
        f"{'胜率%':>7} {'败率%':>7} {'盈亏比':>6} {'夏普':>6}"
    )
    lines.append(header)
    lines.append("-" * 100)

    for r in results_list[:top_n]:
        lines.append(
            f"{r['label']:<20} {r['count']:>6} {r['mean_ret_pct']:>7.2f} {r['median_ret_pct']:>7.2f} "
            f"{r['win_rate_pct']:>7.2f} {r['loss_rate_pct']:>7.2f} "
            f"{r['profit_loss_ratio']:>6.2f} {r['sharpe']:>6.2f}"
        )

    return "\n".join(lines)


# ============================================================
# 6. 主回测流程
# ============================================================

def main():
    # 读取自选股
    stocks = []
    with open(SELF_STOCK_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split(" ", 1)
            stocks.append((parts[0], line))

    print(f"[BT] 自选股总数：{len(stocks)}")
    print(f"[BT] 回测周期：最近 {BACKTEST_YEARS} 年")
    print(f"[BT] 盈利阈值：>={PROFIT_THRESHOLD*100:.0f}%")
    print(f"[BT] 验证周期：{FORWARD_DAYS} 交易日")

    all_gui = []  # GUI版记录
    all_v2 = []  # V2引擎记录
    all_v15 = []  # V15引擎记录（阶段二）
    all_scenario = []  # V15场景引擎记录（阶段三）
    all_filtered = []  # V15过滤引擎记录（阶段四）
    stock_ok = 0
    stock_fail = 0

    t0 = time.time()

    for idx, (code, name_full) in enumerate(stocks):
        if (idx + 1) % 50 == 0 or idx == 0:
            elapsed = time.time() - t0
            print(
                f"  进度 {idx+1}/{len(stocks)} | "
                f"成功{stock_ok} 失败{stock_fail} | "
                f"耗时{elapsed:.0f}s"
            )
            import sys; sys.stdout.flush()

        df = load_stock_tdx(code)
        if df is None or df.empty or len(df) < 80:
            stock_fail += 1
            continue

        # GUI 旧版
        try:
            gui_recs = gui_backtest_on_df(df)
            for r in gui_recs:
                r["code"] = code
                r["name"] = name_full
            all_gui.extend(gui_recs)
        except Exception as e:
            pass

        # V2 新版
        try:
            v2_recs = v2_backtest_on_df(df)
            for r in v2_recs:
                r["code"] = code
                r["name"] = name_full
            all_v2.extend(v2_recs)
        except Exception as e:
            pass

        # V15 新引擎（阶段二）
        try:
            v15_recs = v15_backtest_on_df(df)
            for r in v15_recs:
                r["code"] = code
                r["name"] = name_full
            all_v15.extend(v15_recs)
        except Exception as e:
            pass

        # V15 场景引擎（阶段三）
        try:
            sce_recs = scenario_backtest_on_df(df)
            for r in sce_recs:
                r["code"] = code
                r["name"] = name_full
            all_scenario.extend(sce_recs)
        except Exception as e:
            pass

        # V15 过滤引擎（阶段四）
        try:
            fil_recs = filtered_backtest_on_df(df)
            for r in fil_recs:
                r["code"] = code
                r["name"] = name_full
            all_filtered.extend(fil_recs)
        except Exception as e:
            pass

        stock_ok += 1

    elapsed = time.time() - t0
    print(f"\n[OK] 回测完成！有效股票 {stock_ok}/{len(stocks)}，耗时 {elapsed:.0f}s")
    print(f"   GUI版记录数：{len(all_gui)}")
    print(f"   V2版记录数：{len(all_v2)}")
    print(f"   V15版记录数：{len(all_v15)}")
    print(f"   场景版记录数：{len(all_scenario)}")
    print(f"   过滤版记录数：{len(all_filtered)}")

    # ── 聚合分析 ──
    report_lines = [
        "=" * 100,
        "  V15Alpha 全市场回测验证报告",
        f"  生成时间：{datetime.now():%Y-%m-%d %H:%M:%S}",
        f"  股票数量：{stock_ok} 只",
        f"  回测周期：最近 {BACKTEST_YEARS} 年",
        f"  盈利阈值：>{PROFIT_THRESHOLD*100:.0f}%",
        f"  GUI版信号点：{len(all_gui)}",
        f"  V2版信号点：{len(all_v2)}",
        f"  V15版信号点：{len(all_v15)}",
        f"  场景版信号点：{len(all_scenario)}",
        f"  过滤版信号点：{len(all_filtered)}",
        "=" * 100,
    ]

    # ── A. GUI旧版：按评分分组 ──
    for fd in FORWARD_DAYS:
        title = f"[BT] GUI旧版评分分组（{fd}日后收益）"
        gui_score_groups = aggregate_by_group(
            all_gui,
            key_fn=lambda r: (
                0 if r["gui_score"] < 30
                else 1 if r["gui_score"] < 45
                else 2 if r["gui_score"] < 55
                else 3 if r["gui_score"] < 70
                else 4 if r["gui_score"] < 80
                else 5
            ),
            label_fn=lambda k: (
                ["<30(强势偏空)", "30-45(温和偏空)", "45-55(中性震荡)",
                 "55-70(温和偏多)", "70-80(强势偏多)", ">=80(强势偏多)"][k]
            ),
            forward_day=fd,
        )
        report_lines.append(print_results_table(gui_score_groups, title))

    # ── B. V2引擎：按择时等级分组 ──
    for fd in FORWARD_DAYS:
        title = f"[BT] V2引擎择时等级（{fd}日后收益）"
        v2_level_groups = aggregate_by_group(
            all_v2,
            key_fn=lambda r: {"强势": 0, "偏强": 1, "中性": 2, "弱势": 3}.get(
                r["v2_level"], 4
            ),
            label_fn=lambda k: {
                0: "强势", 1: "偏强", 2: "中性", 3: "弱势", 4: "未知"
            }.get(k, "未知"),
            forward_day=fd,
        )
        report_lines.append(print_results_table(v2_level_groups, title))

    # ── C. V2引擎：按市场状态分组 ──
    for fd in FORWARD_DAYS:
        title = f"[BT] V2引擎市场状态（{fd}日后收益）"
        v2_regime_groups = aggregate_by_group(
            all_v2,
            key_fn=lambda r: {
                "强趋势": 0, "偏多": 1, "震荡": 2, "偏空": 3, "强下跌趋势": 4
            }.get(r["v2_regime"], 5),
            label_fn=lambda k: {
                0: "强趋势", 1: "偏多", 2: "震荡", 3: "偏空", 4: "强下跌趋势", 5: "未知"
            }.get(k, "未知"),
            forward_day=fd,
        )
        report_lines.append(print_results_table(v2_regime_groups, title))

    # ── D. V2引擎：按综合评分区间分组 ──
    for fd in FORWARD_DAYS:
        title = f"[BT] V2引擎综合评分区间（{fd}日后收益）"
        v2_score_groups = aggregate_by_group(
            all_v2,
            key_fn=lambda r: (
                0 if r["v2_score"] < 40
                else 1 if r["v2_score"] < 48
                else 2 if r["v2_score"] < 58
                else 3 if r["v2_score"] < 68
                else 4
            ),
            label_fn=lambda k: [
                "<40(弱势)", "40-48(弱势边缘)", "48-58(中性)",
                "58-68(偏强)", ">=68(强势)"
            ][k],
            forward_day=fd,
        )
        report_lines.append(print_results_table(v2_score_groups, title))

    # ── E. V2引擎：MR评分区间（均值回归） ──
    for fd in FORWARD_DAYS:
        title = f"[BT] V2引擎MR评分（{fd}日后收益）"
        v2_mr_groups = aggregate_by_group(
            all_v2,
            key_fn=lambda r: (
                0 if r["v2_mr"] < 35
                else 1 if r["v2_mr"] < 50
                else 2 if r["v2_mr"] < 68
                else 3 if r["v2_mr"] < 80
                else 4
            ),
            label_fn=lambda k: [
                "<35(不可靠)", "35-50(偏弱)", "50-68(中性)",
                "68-80(较好)", ">=80(超跌)"
            ][k],
            forward_day=fd,
        )
        report_lines.append(print_results_table(v2_mr_groups, title))

    # ── F. V2引擎：TF评分区间（趋势跟踪） ──
    for fd in FORWARD_DAYS:
        title = f"[BT] V2引擎TF评分（{fd}日后收益）"
        v2_tf_groups = aggregate_by_group(
            all_v2,
            key_fn=lambda r: (
                0 if r["v2_tf"] < 35
                else 1 if r["v2_tf"] < 50
                else 2 if r["v2_tf"] < 68
                else 3 if r["v2_tf"] < 80
                else 4
            ),
            label_fn=lambda k: [
                "<35(不可靠)", "35-50(偏弱)", "50-68(中性)",
                "68-80(较好)", ">=80(强劲趋势)"
            ][k],
            forward_day=fd,
        )
        report_lines.append(print_results_table(v2_tf_groups, title))

    # ── G. V2引擎：强势 vs 弱势 对比（核心验证） ──
    for fd in FORWARD_DAYS:
        title = f"[BT] V2引擎：强势 vs 弱势（{fd}日后收益）"
        extreme_groups = aggregate_by_group(
            all_v2,
            key_fn=lambda r: (
                0 if r["v2_level"] == "强势"
                else 1 if r["v2_level"] == "弱势"
                else 2
            ),
            label_fn=lambda k: {0: "强势", 1: "弱势", 2: "其他"}.get(k, "其他"),
            forward_day=fd,
        )
        # 只取强势和弱势
        extreme_groups = [g for g in extreme_groups if g["label"] in ("强势", "弱势")]
        report_lines.append(print_results_table(extreme_groups, title))

    # ═══════════════════════════════════════════════════
    #  V15 新引擎分析（阶段二）
    # ═══════════════════════════════════════════════════

    if all_v15:
        # ── I. V15引擎：按择时等级分组 ──
        for fd in FORWARD_DAYS:
            title = f"[BT] V15引擎择时等级（{fd}日后收益）"
            v15_level_groups = aggregate_by_group(
                all_v15,
                key_fn=lambda r: {"强势": 0, "偏强": 1, "中性": 2, "弱势": 3}.get(
                    r["v15_level"], 4
                ),
                label_fn=lambda k: {
                    0: "强势", 1: "偏强", 2: "中性", 3: "弱势", 4: "未知"
                }.get(k, "未知"),
                forward_day=fd,
            )
            report_lines.append(print_results_table(v15_level_groups, title))

        # ── J. V15引擎：按综合评分区间分组 ──
        for fd in FORWARD_DAYS:
            title = f"[BT] V15引擎综合评分区间（{fd}日后收益）"
            v15_score_groups = aggregate_by_group(
                all_v15,
                key_fn=lambda r: (
                    0 if r["v15_score"] < 40
                    else 1 if r["v15_score"] < 48
                    else 2 if r["v15_score"] < 58
                    else 3 if r["v15_score"] < 68
                    else 4 if r["v15_score"] < 80
                    else 5
                ),
                label_fn=lambda k: [
                    "<40(弱势)", "40-48(弱势边缘)", "48-58(中性)",
                    "58-68(偏强)", "68-80(较优)", ">=80(强势)"
                ][k],
                forward_day=fd,
            )
            report_lines.append(print_results_table(v15_score_groups, title))

        # ── K. V15引擎：反转评分区间 ──
        for fd in FORWARD_DAYS:
            title = f"[BT] V15反转评分区间（{fd}日后收益）"
            v15_rev_groups = aggregate_by_group(
                all_v15,
                key_fn=lambda r: (
                    0 if r["v15_rev"] < 30
                    else 1 if r["v15_rev"] < 50
                    else 2 if r["v15_rev"] < 70
                    else 3
                ),
                label_fn=lambda k: [
                    "<30(上涨风险)", "30-50(中性)", "50-70(反转机会)", ">=70(强反转)"
                ][k],
                forward_day=fd,
            )
            report_lines.append(print_results_table(v15_rev_groups, title))

        # ── L. V15引擎：MACD评分区间 ──
        for fd in FORWARD_DAYS:
            title = f"[BT] V15 MACD评分区间（{fd}日后收益）"
            v15_macd_groups = aggregate_by_group(
                all_v15,
                key_fn=lambda r: (
                    0 if r["v15_macd"] < 30
                    else 1 if r["v15_macd"] < 50
                    else 2 if r["v15_macd"] < 70
                    else 3
                ),
                label_fn=lambda k: [
                    "<30(空头)", "30-50(偏弱)", "50-70(偏强)", ">=70(多头强势)"
                ][k],
                forward_day=fd,
            )
            report_lines.append(print_results_table(v15_macd_groups, title))

        # ── M. V15引擎：量能评分区间 ──
        for fd in FORWARD_DAYS:
            title = f"[BT] V15量能评分区间（{fd}日后收益）"
            v15_vol_groups = aggregate_by_group(
                all_v15,
                key_fn=lambda r: (
                    0 if r["v15_vol"] < 30
                    else 1 if r["v15_vol"] < 50
                    else 2 if r["v15_vol"] < 70
                    else 3
                ),
                label_fn=lambda k: [
                    "<30(缩量/天量)", "30-50(量能平庸)", "50-70(温和放量)", ">=70(量价配合)"
                ][k],
                forward_day=fd,
            )
            report_lines.append(print_results_table(v15_vol_groups, title))

        # ── N. V15引擎：强势 vs 弱势 对比（核心验证） ──
        for fd in FORWARD_DAYS:
            title = f"[BT] V15引擎：强势 vs 弱势（{fd}日后收益）——核心验证"
            v15_extreme = aggregate_by_group(
                all_v15,
                key_fn=lambda r: (
                    0 if r["v15_level"] == "强势"
                    else 1 if r["v15_level"] == "弱势"
                    else 2
                ),
                label_fn=lambda k: {0: "强势", 1: "弱势", 2: "其他"}.get(k, "其他"),
                forward_day=fd,
            )
            v15_extreme = [g for g in v15_extreme if g["label"] in ("强势", "弱势")]
            report_lines.append(print_results_table(v15_extreme, title))

    else:
        report_lines.append("\n  [V15] 无有效记录（需检查数据）")

    # ═══════════════════════════════════════════════════
    #  V15 场景引擎分析（阶段三）
    # ═══════════════════════════════════════════════════

    if all_scenario:
        # ── O. 场景引擎：按场景分组 ──
        for fd in FORWARD_DAYS:
            title = f"[BT] 场景引擎：按场景分组（{fd}日后收益）"
            sce_scene_groups = aggregate_by_group(
                all_scenario,
                key_fn=lambda r: {
                    "超跌反弹": 0, "趋势接力": 1, "突破确认": 2, "无场景": 3
                }.get(r["scenario_name"], 4),
                label_fn=lambda k: {
                    0: "超跌反弹", 1: "趋势接力", 2: "突破确认", 3: "无场景", 4: "未知"
                }.get(k, "未知"),
                forward_day=fd,
            )
            report_lines.append(print_results_table(sce_scene_groups, title))

        # ── P. 场景引擎：按择时等级分组 ──
        for fd in FORWARD_DAYS:
            title = f"[BT] 场景引擎择时等级（{fd}日后收益）"
            sce_level_groups = aggregate_by_group(
                all_scenario,
                key_fn=lambda r: {"强势": 0, "偏强": 1, "中性": 2, "弱势": 3}.get(
                    r["scenario_level"], 4
                ),
                label_fn=lambda k: {
                    0: "强势", 1: "偏强", 2: "中性", 3: "弱势", 4: "未知"
                }.get(k, "未知"),
                forward_day=fd,
            )
            report_lines.append(print_results_table(sce_level_groups, title))

        # ── Q. 场景引擎：按场景综合评分区间 ──
        for fd in FORWARD_DAYS:
            title = f"[BT] 场景引擎综合评分区间（{fd}日后收益）"
            sce_score_groups = aggregate_by_group(
                all_scenario,
                key_fn=lambda r: (
                    0 if r["scenario_score"] < 40
                    else 1 if r["scenario_score"] < 48
                    else 2 if r["scenario_score"] < 58
                    else 3 if r["scenario_score"] < 68
                    else 4 if r["scenario_score"] < 80
                    else 5
                ),
                label_fn=lambda k: [
                    "<40(弱势)", "40-48(弱势边缘)", "48-58(中性)",
                    "58-68(偏强)", "68-80(较优)", ">=80(强势)"
                ][k],
                forward_day=fd,
            )
            report_lines.append(print_results_table(sce_score_groups, title))

        # ── R. 场景引擎：vs V15默认权重对比 ──
        for fd in FORWARD_DAYS:
            title = f"[BT] 场景引擎 vs V15默认（{fd}日后收益）——对比验证"
            sce_vs_v15 = aggregate_by_group(
                all_scenario,
                key_fn=lambda r: (
                    0 if r["scenario_level"] == "强势" and r["raw_v15_score"] < 68
                    else 1 if r["scenario_level"] == "强势" and r["raw_v15_score"] >= 68
                    else 2 if r["scenario_level"] != "强势" and r["raw_v15_score"] >= 68
                    else 3
                ),
                label_fn=lambda k: {
                    0: "场景提升至强势(V15<68)", 1: "双确认强势(V15+场景)",
                    2: "V15强势但场景降级", 3: "其他"
                }.get(k, "其他"),
                forward_day=fd,
            )
            # 只取有意义的行
            sce_vs_v15 = [g for g in sce_vs_v15 if g["label"] in (
                "场景提升至强势(V15<68)", "双确认强势(V15+场景)", "V15强势但场景降级"
            )]
            report_lines.append(print_results_table(sce_vs_v15, title))

        # ── S. 场景引擎：强势 vs 弱势（核心验证） ──
        for fd in FORWARD_DAYS:
            title = f"[BT] 场景引擎：强势 vs 弱势（{fd}日后收益）——核心验证"
            sce_extreme = aggregate_by_group(
                all_scenario,
                key_fn=lambda r: (
                    0 if r["scenario_level"] == "强势"
                    else 1 if r["scenario_level"] == "弱势"
                    else 2
                ),
                label_fn=lambda k: {0: "强势", 1: "弱势", 2: "其他"}.get(k, "其他"),
                forward_day=fd,
            )
            sce_extreme = [g for g in sce_extreme if g["label"] in ("强势", "弱势")]
            report_lines.append(print_results_table(sce_extreme, title))

    else:
        report_lines.append("\n  [场景] 无有效记录（需检查数据）")

    # ═══════════════════════════════════════════════════
    #  V15 过滤引擎分析（阶段四）
    # ═══════════════════════════════════════════════════

    if all_filtered:
        # ── T. 过滤引擎：按等级分组 ──
        for fd in FORWARD_DAYS:
            title = f"[BT] 过滤引擎等级分组（{fd}日后收益）"
            fil_level_groups = aggregate_by_group(
                all_filtered,
                key_fn=lambda r: {"强势": 0, "偏强": 1, "中性": 2, "弱势": 3}.get(
                    r["filtered_level"], 4
                ),
                label_fn=lambda k: {
                    0: "强势", 1: "偏强", 2: "中性", 3: "弱势", 4: "未知"
                }.get(k, "未知"),
                forward_day=fd,
            )
            report_lines.append(print_results_table(fil_level_groups, title))

        # ── U. 过滤引擎：按评分区间 ──
        for fd in FORWARD_DAYS:
            title = f"[BT] 过滤引擎评分区间（{fd}日后收益）"
            fil_score_groups = aggregate_by_group(
                all_filtered,
                key_fn=lambda r: (
                    0 if r["filtered_score"] < 40
                    else 1 if r["filtered_score"] < 48
                    else 2 if r["filtered_score"] < 58
                    else 3 if r["filtered_score"] < 68
                    else 4 if r["filtered_score"] < 80
                    else 5
                ),
                label_fn=lambda k: [
                    "<40(弱势)", "40-48(弱势边缘)", "48-58(中性)",
                    "58-68(偏强)", "68-80(较优)", ">=80(强势)"
                ][k],
                forward_day=fd,
            )
            report_lines.append(print_results_table(fil_score_groups, title))

        # ── V. 过滤引擎 vs 场景引擎对比 ──
        for fd in FORWARD_DAYS:
            title = f"[BT] 过滤 vs 场景对比（{fd}日后收益）——核心验证"
            fil_vs_sce = aggregate_by_group(
                all_filtered,
                key_fn=lambda r: (
                    0 if r["filtered_level"] == "强势" and r["scenario_score"] < 68
                    else 1 if r["filtered_level"] == "强势" and r["scenario_score"] >= 68
                    else 2 if r["filtered_level"] != "强势" and r["scenario_score"] >= 68
                    else 3
                ),
                label_fn=lambda k: {
                    0: "过滤提升至强势(场景<68)",
                    1: "双确认强势(过滤+场景)",
                    2: "场景强势但过滤降级",
                    3: "其他"
                }.get(k, "其他"),
                forward_day=fd,
            )
            fil_vs_sce = [g for g in fil_vs_sce if g["label"] in (
                "过滤提升至强势(场景<68)", "双确认强势(过滤+场景)", "场景强势但过滤降级"
            )]
            report_lines.append(print_results_table(fil_vs_sce, title))

        # ── W. 过滤引擎：强势 vs 弱势（核心验证） ──
        for fd in FORWARD_DAYS:
            title = f"[BT] 过滤引擎：强势 vs 弱势（{fd}日后收益）——核心验证"
            fil_extreme = aggregate_by_group(
                all_filtered,
                key_fn=lambda r: (
                    0 if r["filtered_level"] == "强势"
                    else 1 if r["filtered_level"] == "弱势"
                    else 2
                ),
                label_fn=lambda k: {0: "强势", 1: "弱势", 2: "其他"}.get(k, "其他"),
                forward_day=fd,
            )
            fil_extreme = [g for g in fil_extreme if g["label"] in ("强势", "弱势")]
            report_lines.append(print_results_table(fil_extreme, title))

        # ── X. 过滤引擎：按共振一致性分组 ──
        for fd in FORWARD_DAYS:
            title = f"[BT] 过滤引擎共振一致性分组（{fd}日后收益）"
            fil_resonance_groups = aggregate_by_group(
                all_filtered,
                key_fn=lambda r: {
                    "一致": 0, "分歧": 1, "短期脉冲": 2, "数据不足": 3
                }.get(r["resonance_consistency"], 3),
                label_fn=lambda k: {
                    0: "多周期一致", 1: "多周期分歧",
                    2: "短期脉冲", 3: "数据不足/其他"
                }.get(k, "其他"),
                forward_day=fd,
            )
            report_lines.append(print_results_table(fil_resonance_groups, title))

        # ── Y. 过滤引擎：按波动率分组 ──
        for fd in FORWARD_DAYS:
            title = f"[BT] 过滤引擎波动率分组（{fd}日后收益）"
            fil_vol_groups = aggregate_by_group(
                all_filtered,
                key_fn=lambda r: (
                    0 if r["atr_norm"] > 5.0
                    else 1 if r["atr_norm"] > 3.0
                    else 2 if r["atr_norm"] > 1.0
                    else 3
                ),
                label_fn=lambda k: [
                    "高波动(ATR>5%)", "中高波动(ATR3-5%)",
                    "正常波动(ATR1-3%)", "低波动(ATR<1%)"
                ][k] if k < 4 else "未知",
                forward_day=fd,
            )
            report_lines.append(print_results_table(fil_vol_groups, title))

        # ── Z. 过滤引擎：按MACD背离分组（新增） ──
        for fd in FORWARD_DAYS:
            title = f"[BT] 过滤引擎MACD背离分组（{fd}日后收益）"
            fil_div_groups = aggregate_by_group(
                all_filtered,
                key_fn=lambda r: {
                    "顶背离": 0, "底背离": 1, "隐藏顶背离": 2, "隐藏底背离": 3, "无": 4
                }.get(r["divergence_type"], 4),
                label_fn=lambda k: {
                    0: "顶背离", 1: "底背离", 2: "隐藏顶背离",
                    3: "隐藏底背离", 4: "无背离"
                }.get(k, "其他"),
                forward_day=fd,
            )
            report_lines.append(print_results_table(fil_div_groups, title))

        # ── AA. 过滤引擎：按ROC动量背离分组（新增） ──
        for fd in FORWARD_DAYS:
            title = f"[BT] 过滤引擎动量背离分组（{fd}日后收益）"
            fil_mom_div_groups = aggregate_by_group(
                all_filtered,
                key_fn=lambda r: {
                    "顶背离(动量)": 0, "底背离(动量)": 1,
                    "隐藏顶背离(动量)": 2, "隐藏底背离(动量)": 3, "无": 4
                }.get(r["momentum_divergence_type"], 4),
                label_fn=lambda k: {
                    0: "动量顶背离", 1: "动量底背离",
                    2: "动量隐藏顶背离", 3: "动量隐藏底背离", 4: "无背离"
                }.get(k, "其他"),
                forward_day=fd,
            )
            report_lines.append(print_results_table(fil_mom_div_groups, title))

    else:
        report_lines.append("\n  [过滤] 无有效记录（需检查数据）")

    # ── H. 按形态胜率排序 ──
    for fd in FORWARD_DAYS:
        # 从 V2 记录按形态group
        pat_records = []
        for r in all_v2:
            for p in r["v2_patterns"]:
                pat_records.append({"pattern": p, "fwd": r["fwd_ret"][fd]})
        pat_groups = defaultdict(list)
        for pr in pat_records:
            pat_groups[pr["pattern"]].append(pr["fwd"])

        pat_results = []
        for p, rets in pat_groups.items():
            n = len(rets)
            wr = sum(1 for x in rets if x >= PROFIT_THRESHOLD) / n * 100
            pat_results.append({
                "label": p,
                "count": n,
                "mean_ret_pct": round(np.mean(rets) * 100, 2),
                "win_rate_pct": round(wr, 2),
            })
        pat_results.sort(key=lambda x: (-x["win_rate_pct"], -x["count"]))

        lines = [
            f"\n{'='*100}",
            f"  [BT] V2形态胜率排名（{fd}日后）",
            f"{'='*100}",
            f"{'形态':<20} {'触发次数':>8} {'均值%':>7} {'胜率%':>7}",
            "-" * 100,
        ]
        for pr in pat_results[:20]:
            lines.append(
                f"{pr['label']:<20} {pr['count']:>8} {pr['mean_ret_pct']:>7.2f} {pr['win_rate_pct']:>7.2f}"
            )
        report_lines.append("\n".join(lines))

    # ── 写入报告 ──
    report = "\n\n".join(report_lines)
    report_path = os.path.join(APP_DIR, "backtest_report.txt")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)

    print(f"\n[OK] 回测报告已写入：{report_path}")
    print(report[:3000])

    return report, all_gui, all_v2


if __name__ == "__main__":
    main()
