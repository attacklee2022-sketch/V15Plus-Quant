# -*- coding: utf-8 -*-
r"""
因子IC分析 — 阶段一：搞清楚每个因子到底有没有预测能力
==================================================
遍历自选股全量历史数据，在每个交易日计算（因子值, 未来N日收益）。
然后计算每个因子的IC（Information Coefficient）= 因子值与未来收益的秩相关性。

IC > 0  → 因子值越高，未来收益越高（正向预测）
IC < 0  → 因子值越高，未来收益越低（反向预测，反转因子）
IC ≈ 0  → 纯噪声，可以砍掉

输出：
  1. 单因子IC排名表（按IC绝对值排序）
  2. 各周期（5/10/20/60日）IC一致性
  3. 因子分组收益（最高组 vs 最低组收益差）
  4. 因子间相关性矩阵（避重复因子）

使用方式:
  cd <项目根目录>
  python scripts/backtest_factor_ic.py
  等待回测完成（约5-10分钟），查看 factor_ic_report.txt
"""

from __future__ import annotations
import os
import sys
import struct
import time
from datetime import datetime
from collections import defaultdict

import pandas as pd
import numpy as np

# ── 导入 V2 引擎 ──
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_SCRIPT_DIR, "..", "_internal"))
from v14_tech_scorer import compute_signal as v2_compute_signal

# ── 配置 ──
APP_DIR = os.path.dirname(_SCRIPT_DIR)
TDX_ROOT = os.environ.get("TDX_ROOT_PATH", r"D:\通达信")
SELF_STOCK_FILE = os.path.join(APP_DIR, "self_stock.txt")
BACKTEST_YEARS = 3
FORWARD_DAYS = [5, 10, 20, 60]
MIN_DATA_DAYS = 120  # 保证有足够数据计算所有因子


# ════════════════════════════════════════════════
# 1. 通达信数据加载（复用 backtest_all.py 逻辑）
# ════════════════════════════════════════════════

def _read_tdx_day_file(file_path: str) -> bytes | None:
    import time as _time
    for attempt in range(3):
        try:
            with open(file_path, "rb") as f:
                return f.read()
        except PermissionError:
            _time.sleep(0.3 * (attempt + 1))
        except Exception:
            raise
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


def parse_tdx_day(file_path):
    data = _read_tdx_day_file(file_path)
    if data is None:
        return None
    try:
        row_cnt = len(data) // 32
        rows = []
        for i in range(row_cnt):
            b = data[i * 32: (i + 1) * 32]
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

        # 技术指标
        df["ma5"] = df["收盘"].rolling(5).mean()
        df["ma10"] = df["收盘"].rolling(10).mean()
        df["ma20"] = df["收盘"].rolling(20).mean()
        df["ma60"] = df["收盘"].rolling(60).mean()
        df["vol_ma5"] = df["成交量"].rolling(5).mean()
        df["vol_ma10"] = df["成交量"].rolling(10).mean()
        df["vol_ma20"] = df["成交量"].rolling(20).mean()
        df["macd"], df["macd_signal"], df["macd_hist"] = _calc_macd(df["收盘"])
        df["rsi"] = _calc_rsi(df["收盘"], 14)
        df["boll_mid"] = df["收盘"].rolling(20).mean()
        df["boll_std"] = df["收盘"].rolling(20).std()

        # 最高最低
        df["hhv_5"] = df["最高"].rolling(5).max()
        df["llv_5"] = df["最低"].rolling(5).min()
        df["hhv_10"] = df["最高"].rolling(10).max()
        df["llv_10"] = df["最低"].rolling(10).min()
        df["hhv_20"] = df["最高"].rolling(20).max()
        df["llv_20"] = df["最低"].rolling(20).min()
        df["hhv_60"] = df["最高"].rolling(60).max()
        df["llv_60"] = df["最低"].rolling(60).min()

        return df
    except Exception as e:
        print(f"[ERROR] 解析失败 {file_path}: {e}")
        return None


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


# ════════════════════════════════════════════════
# 2. 因子计算——在 df 的第 idx 行计算所有候选因子
# ════════════════════════════════════════════════

NAN = float("nan")


def _safe(arr, idx, offset=0):
    """安全取值，越界返回 NaN"""
    pos = idx + offset
    if 0 <= pos < len(arr):
        return float(arr[pos])
    return NAN


def compute_factors(df, idx):
    """
    在 DataFrame df 的第 idx 行计算所有候选因子。

    返回 dict[str, float]：因子名→因子值
    若 idx 位置数据不足，因子值为 NaN（后续不会被计入IC计算）
    """
    f = {}

    # ── 基础价格数据 ──
    close = float(df["收盘"].iloc[idx])
    open_ = float(df["开盘"].iloc[idx])
    high = float(df["最高"].iloc[idx])
    low = float(df["最低"].iloc[idx])
    vol = float(df["成交量"].iloc[idx])

    closes = df["收盘"].values
    highs = df["最高"].values
    lows = df["最低"].values
    opens = df["开盘"].values
    volumes = df["成交量"].values

    # ═══════════════════════════════════════════════
    # 【动量系】— 价格变化率
    # ═══════════════════════════════════════════════
    for period in [1, 3, 5, 10, 20, 60]:
        if idx >= period:
            f[f"roc_{period}d"] = close / _safe(closes, idx - period) - 1
        else:
            f[f"roc_{period}d"] = NAN

    # 价格加速度（ROC的变化率）
    if idx >= 10:
        roc5_now = close / _safe(closes, idx - 5) - 1
        roc5_prev = _safe(closes, idx - 5) / _safe(closes, idx - 10) - 1
        f["accel_5d"] = roc5_now - roc5_prev
    else:
        f["accel_5d"] = NAN

    # ═══════════════════════════════════════════════
    # 【均线系】— 价格相对均线的位置
    # ═══════════════════════════════════════════════
    for ma_period in [5, 10, 20, 60]:
        ma_val = float(df[f"ma{ma_period}"].iloc[idx])
        if pd.notna(ma_val) and ma_val > 0:
            f[f"ma{ma_period}_ratio"] = close / ma_val - 1
        else:
            f[f"ma{ma_period}_ratio"] = NAN

    # 均线排列评分
    ma5 = float(df["ma5"].iloc[idx])
    ma10 = float(df["ma10"].iloc[idx])
    ma20 = float(df["ma20"].iloc[idx])
    ma60 = float(df["ma60"].iloc[idx])

    f["ma_full_bull"] = 1.0 if (pd.notna(ma5) and pd.notna(ma10) and
                                 pd.notna(ma20) and pd.notna(ma60) and
                                 close > ma5 > ma10 > ma20 > ma60) else 0.0
    f["ma_full_bear"] = 1.0 if (pd.notna(ma5) and pd.notna(ma10) and
                                  pd.notna(ma20) and pd.notna(ma60) and
                                  close < ma5 < ma10 < ma20 < ma60) else 0.0

    # MA20 vs MA60 关系
    if pd.notna(ma20) and pd.notna(ma60) and ma60 > 0:
        f["ma20_ma60_ratio"] = ma20 / ma60 - 1
    else:
        f["ma20_ma60_ratio"] = NAN

    # MA20 斜率（过去20日变化率）
    if idx >= 40:
        ma20_20ago = float(df["ma20"].iloc[idx - 20])
        if ma20_20ago > 0:
            f["ma20_slope_20d"] = ma20 / ma20_20ago - 1
        else:
            f["ma20_slope_20d"] = NAN
    else:
        f["ma20_slope_20d"] = NAN

    # ═══════════════════════════════════════════════
    # 【波动系】— ATR, 布林带
    # ═══════════════════════════════════════════════
    # ATR(14)
    if idx >= 14:
        tr_vals = []
        for j in range(idx - 13, idx + 1):
            h_val = highs[j]
            l_val = lows[j]
            c_prev = closes[j - 1]
            tr_vals.append(max(h_val - l_val, abs(h_val - c_prev), abs(l_val - c_prev)))
        f["atr_14"] = np.mean(tr_vals)
        f["atr_14_ratio"] = f["atr_14"] / np.mean(tr_vals[:-5]) if len(tr_vals) > 5 else 1.0
        f["atr_14_norm"] = f["atr_14"] / close  # 相对价格归一化
    else:
        f["atr_14"] = NAN
        f["atr_14_ratio"] = NAN
        f["atr_14_norm"] = NAN

    # 布林带位置
    boll_mid = float(df["boll_mid"].iloc[idx])
    boll_std = float(df["boll_std"].iloc[idx])
    if pd.notna(boll_std) and boll_std > 0:
        f["boll_pos"] = (close - boll_mid) / boll_std
        f["boll_width"] = 2 * boll_std / boll_mid if boll_mid > 0 else NAN
    else:
        f["boll_pos"] = NAN
        f["boll_width"] = NAN

    # ═══════════════════════════════════════════════
    # 【区间位置系】
    # ═══════════════════════════════════════════════
    for period in [5, 10, 20, 60]:
        hhv = float(df[f"hhv_{period}"].iloc[idx])
        llv = float(df[f"llv_{period}"].iloc[idx])
        if pd.notna(hhv) and pd.notna(llv) and hhv > llv:
            f[f"range_pos_{period}d"] = (close - llv) / (hhv - llv)
        else:
            f[f"range_pos_{period}d"] = NAN

        # 是否创阶段性新高/新低
        if pd.notna(hhv):
            f[f"near_high_{period}d"] = 1.0 if close >= hhv * 0.99 else 0.0
            f[f"near_low_{period}d"] = 1.0 if close <= llv * 1.01 else 0.0

    # 距前高/前低百分比
    if idx >= 60:
        high_60 = float(df["hhv_60"].iloc[idx])
        low_60 = float(df["llv_60"].iloc[idx])
        if high_60 > 0:
            f["dist_high_60d"] = close / high_60 - 1
        if low_60 > 0:
            f["dist_low_60d"] = close / low_60 - 1

    # ═══════════════════════════════════════════════
    # 【量能系】
    # ═══════════════════════════════════════════════
    for period in [5, 10, 20]:
        vol_ma = float(df[f"vol_ma{period}"].iloc[idx])
        if pd.notna(vol_ma) and vol_ma > 0:
            f[f"vol_ratio_{period}d"] = vol / vol_ma
        else:
            f[f"vol_ratio_{period}d"] = NAN

    # 量价配合
    if idx >= 5:
        ret5 = close / _safe(closes, idx - 5) - 1
        vol_ratio_5 = f.get("vol_ratio_5d", 1)
        f["vol_price_up"] = 1.0 if (vol_ratio_5 > 1.3 and 0 < ret5 < 0.15) else 0.0
        f["vol_price_down"] = 1.0 if (vol_ratio_5 > 1.3 and ret5 < -0.03) else 0.0
    else:
        f["vol_price_up"] = NAN
        f["vol_price_down"] = NAN

    # 量比趋势
    if idx >= 20:
        vol_ma5 = float(df["vol_ma5"].iloc[idx])
        vol_ma20 = float(df["vol_ma20"].iloc[idx])
        if pd.notna(vol_ma5) and pd.notna(vol_ma20) and vol_ma20 > 0:
            f["vol_trend"] = vol_ma5 / vol_ma20 - 1  # >0 放量趋势，<0 缩量趋势
        else:
            f["vol_trend"] = NAN
    else:
        f["vol_trend"] = NAN

    # ═══════════════════════════════════════════════
    # 【摆动指标系】— RSI, MACD, KDJ
    # ═══════════════════════════════════════════════
    rsi = float(df["rsi"].iloc[idx])
    if pd.notna(rsi):
        f["rsi_14"] = rsi
        f["rsi_overbought"] = 1.0 if rsi > 80 else 0.0
        f["rsi_oversold"] = 1.0 if rsi < 20 else 0.0
    else:
        f["rsi_14"] = NAN
        f["rsi_overbought"] = NAN
        f["rsi_oversold"] = NAN

    macd = float(df["macd"].iloc[idx])
    macd_hist = float(df["macd_hist"].iloc[idx])
    macd_sig = float(df["macd_signal"].iloc[idx])
    if pd.notna(macd):
        f["macd"] = macd
        f["macd_hist"] = macd_hist
        f["macd_signal"] = macd_sig
        f["macd_diff"] = macd - macd_sig  # DIF - DEA
        f["macd_bull"] = 1.0 if macd_hist > 0 else 0.0
    else:
        f["macd"] = NAN
        f["macd_hist"] = NAN
        f["macd_signal"] = NAN
        f["macd_diff"] = NAN
        f["macd_bull"] = NAN

    # ═══════════════════════════════════════════════
    # 【K线形态系】— 简单的单K线形态
    # ═══════════════════════════════════════════════
    body = abs(close - open_)
    upper_shadow = high - max(close, open_)
    lower_shadow = min(close, open_) - low
    total_range = high - low

    if total_range > 0:
        # 十字星
        f["doji"] = 1.0 if body / total_range < 0.1 else 0.0
        # 锤子线/上吊线（下影线长）
        f["hammer"] = 1.0 if (lower_shadow > body * 2 and upper_shadow < body * 0.5) else 0.0
        # 射击之星（上影线长）
        f["shooting_star"] = 1.0 if (upper_shadow > body * 2 and lower_shadow < body * 0.5) else 0.0
        # 实体大小（涨跌幅）
        f["body_ratio"] = body / total_range
    else:
        f["doji"] = NAN
        f["hammer"] = NAN
        f["shooting_star"] = NAN
        f["body_ratio"] = NAN

    # 是否为阳线/阴线
    f["bull_candle"] = 1.0 if close > open_ else 0.0

    # ═══════════════════════════════════════════════
    # 【V2系统派生的评分】— 看看现有引擎的产出是否有预测力
    # ═══════════════════════════════════════════════
    try:
        c_list = closes[:idx + 1].tolist()
        h_list = highs[:idx + 1].tolist()
        l_list = lows[:idx + 1].tolist()
        v_list = volumes[:idx + 1].tolist()
        sig = v2_compute_signal(c_list, h_list, l_list, v_list,
                                float(closes[idx]), float(volumes[idx]),
                                float(opens[idx]) if idx > 0 else float(closes[idx]))

        f["v2_score"] = sig["score"]
        f["v2_mr_score"] = sig["mr_score"]
        f["v2_tf_score"] = sig["tf_score"]
        f["v2_strength"] = sig["strength"]

        # 映射等级为数值
        level_map = {"强势": 4, "偏强": 3, "中性": 2, "弱势": 1}
        f["v2_level_num"] = level_map.get(sig["level"], 0)

        regime_map = {"强趋势": 5, "偏多": 4, "震荡": 3, "偏空": 2, "强下跌趋势": 1}
        f["v2_regime_num"] = regime_map.get(sig["regime"], 0)
    except Exception:
        f["v2_score"] = NAN
        f["v2_mr_score"] = NAN
        f["v2_tf_score"] = NAN
        f["v2_strength"] = NAN
        f["v2_level_num"] = NAN
        f["v2_regime_num"] = NAN

    return f


# ════════════════════════════════════════════════
# 3. 因子IC计算
# ════════════════════════════════════════════════

def compute_rank_ic(factor_values, forward_returns):
    """
    计算一个因子的 Rank IC (Spearman Correlation)。
    返回: (ic, p_value)
    """
    if len(factor_values) < 30:
        return 0.0, 1.0
    try:
        arr = np.array(list(zip(factor_values, forward_returns)),
                       dtype=[("f", float), ("r", float)])
        arr = arr[~np.isnan(arr["f"]) & ~np.isnan(arr["r"])]
        if len(arr) < 30:
            return 0.0, 1.0

        # 秩转换
        f_rank = np.argsort(np.argsort(arr["f"]))
        r_rank = np.argsort(np.argsort(arr["r"]))

        n = len(arr)
        # Pearson 相关系数 on ranks = Spearman
        f_mean = np.mean(f_rank)
        r_mean = np.mean(r_rank)
        cov = np.sum((f_rank - f_mean) * (r_rank - r_mean))
        std_f = np.sqrt(np.sum((f_rank - f_mean) ** 2))
        std_r = np.sqrt(np.sum((r_rank - r_mean) ** 2))

        if std_f == 0 or std_r == 0:
            return 0.0, 1.0

        ic = cov / (std_f * std_r)
        if pd.isna(ic):
            return 0.0, 1.0
        return float(ic), 0.0
    except Exception:
        return 0.0, 1.0


def compute_quantile_spread(factor_values, forward_returns, n_quantiles=5):
    """
    按因子值分档，计算各档的平均/中位收益。
    返回: [(quantile_label, mean_ret, median_ret, count), ...]
    """
    if len(factor_values) < 50:
        return []

    arr = np.array(list(zip(factor_values, forward_returns)),
                   dtype=[("f", float), ("r", float)])
    arr = arr[~np.isnan(arr["f"])]
    if len(arr) < 50:
        return []

    # 分档
    order = np.argsort(arr["f"])
    sorted_arr = arr[order]
    n = len(sorted_arr)
    chunk = n // n_quantiles
    results = []
    for q in range(n_quantiles):
        start = q * chunk
        end = (q + 1) * chunk if q < n_quantiles - 1 else n
        group = sorted_arr["r"][start:end]
        results.append({
            "quantile": q + 1,
            "n": len(group),
            "mean_ret": float(np.mean(group)),
            "median_ret": float(np.median(group)),
            "win_rate": float(np.sum(group > 0) / len(group) * 100),
        })

    # 最高档 - 最低档
    if len(results) >= 2:
        spread = results[-1]["mean_ret"] - results[0]["mean_ret"]
    else:
        spread = 0

    return results, spread


# ════════════════════════════════════════════════
# 4. 主回测 + IC分析
# ════════════════════════════════════════════════

FACTOR_CATEGORIES = {
    "动量系": ["roc_1d", "roc_3d", "roc_5d", "roc_10d", "roc_20d", "roc_60d", "accel_5d"],
    "均线系": ["ma5_ratio", "ma10_ratio", "ma20_ratio", "ma60_ratio",
               "ma_full_bull", "ma_full_bear", "ma20_ma60_ratio", "ma20_slope_20d"],
    "波动系": ["atr_14", "atr_14_ratio", "atr_14_norm", "boll_pos", "boll_width"],
    "区间位置": ["range_pos_5d", "range_pos_10d", "range_pos_20d", "range_pos_60d",
                "near_high_5d", "near_low_5d", "near_high_20d", "near_low_20d",
                "dist_high_60d", "dist_low_60d"],
    "量能系": ["vol_ratio_5d", "vol_ratio_10d", "vol_ratio_20d",
              "vol_price_up", "vol_price_down", "vol_trend"],
    "摆动指标": ["rsi_14", "rsi_overbought", "rsi_oversold",
                "macd", "macd_hist", "macd_signal", "macd_diff", "macd_bull"],
    "K线形态": ["doji", "hammer", "shooting_star", "body_ratio", "bull_candle"],
    "V2系统": ["v2_score", "v2_mr_score", "v2_tf_score",
              "v2_strength", "v2_level_num", "v2_regime_num"],
}


def main():
    # ── 读取自选股 ──
    stocks = []
    with open(SELF_STOCK_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split(" ", 1)
            stocks.append((parts[0], line))

    print(f"[IC] 自选股总数：{len(stocks)}")
    print(f"[IC] 回测周期：最近 {BACKTEST_YEARS} 年")
    print(f"[IC] 验证周期：{FORWARD_DAYS} 交易日")
    print(f"[IC] 因子数量：{sum(len(fs) for fs in FACTOR_CATEGORIES.values())}")
    print()

    # ── 遍历股票，收集因子-收益对 ──
    all_factor_names = set()
    # {factor_name: {forward_day: [(value, forward_return), ...]}}
    factor_data: dict[str, dict[int, list[tuple[float, float]]]] = defaultdict(
        lambda: {fd: [] for fd in FORWARD_DAYS}
    )

    stock_ok = 0
    stock_fail = 0
    t0 = time.time()

    for idx, (code, name_full) in enumerate(stocks):
        if (idx + 1) % 20 == 0 or idx == 0:
            elapsed = time.time() - t0
            print(f"  进度 {idx+1}/{len(stocks)} | 成功{stock_ok} 失败{stock_fail} | "
                  f"耗时{elapsed:.0f}s")

        df = load_stock_tdx(code)
        if df is None or df.empty or len(df) < MIN_DATA_DAYS:
            stock_fail += 1
            continue

        n = len(df)
        closes = df["收盘"].values
        max_fwd = max(FORWARD_DAYS)

        for i in range(MIN_DATA_DAYS, n - max_fwd):
            # 计算因子
            factors = compute_factors(df, i)
            if not factors:
                continue

            all_factor_names.update(factors.keys())

            # 未来收益
            current_close = float(closes[i])
            for fd in FORWARD_DAYS:
                future_close = float(closes[i + fd])
                fwd_ret = (future_close - current_close) / current_close

                for fname, fval in factors.items():
                    if pd.notna(fval):
                        factor_data[fname][fd].append((fval, fwd_ret))

        stock_ok += 1

    elapsed = time.time() - t0
    print(f"\n[OK] 回测完成！有效股票 {stock_ok}/{len(stocks)}，耗时 {elapsed:.0f}s")
    print(f"  有效因子数：{len(all_factor_names)}")
    print(f"  总数据点（5日）：{sum(len(factor_data[f][5]) for f in all_factor_names)}")
    print()

    # ════════════════════════════════════════════════
    # IC 分析 & 输出报告
    # ════════════════════════════════════════════════
    report_lines = [
        "=" * 120,
        "  因子 IC 分析报告 — V15Alpha",
        f"  生成时间：{datetime.now():%Y-%m-%d %H:%M:%S}",
        f"  股票数量：{stock_ok} 只",
        f"  有效因子：{len(all_factor_names)} 个",
        f"  分析周期：{BACKTEST_YEARS} 年",
        "=" * 120,
    ]

    # ── 计算每个因子在每个周期的 IC ──
    factor_ics = {}  # {factor_name: {fd: ic_value}}
    for fname in sorted(all_factor_names):
        factor_ics[fname] = {}
        for fd in FORWARD_DAYS:
            pairs = factor_data[fname][fd]
            if len(pairs) < 100:  # 数据点太少
                factor_ics[fname][fd] = (0.0, 1.0, 0)
                continue
            values = [p[0] for p in pairs]
            returns = [p[1] for p in pairs]
            ic, p = compute_rank_ic(values, returns)
            factor_ics[fname][fd] = (ic, p, len(pairs))

    # ── 按类别输出 ──
    for category, factor_list in FACTOR_CATEGORIES.items():
        lines = [
            f"\n{'─' * 120}",
            f"  【{category}】 因子数量: {len(factor_list)}",
            f"{'─' * 120}",
        ]

        # 只显示有数据且匹配当前类别的因子
        active = [f for f in factor_list if f in all_factor_names]
        if not active:
            report_lines.extend(lines)
            report_lines.append("  (无有效因子数据)\n")
            continue

        header = (f"  {'因子名':<22} {'5日IC':>8} {'10日IC':>8} {'20日IC':>8} "
                  f"{'60日IC':>8} {'|IC|均值':>8} {'方向':>6} {'样本量':>8}")
        lines.append(header)
        lines.append("  " + "-" * 80)

        rows = []
        for fname in active:
            ics = []
            for fd in FORWARD_DAYS:
                ic_val, p_val, n_samples = factor_ics[fname].get(fd, (0, 1, 0))
                if abs(ic_val) > 0.01 and p_val < 0.05:
                    ics.append(f"{ic_val:>+7.3f}*")  # 显著标记
                else:
                    ics.append(f"{ic_val:>+7.3f} ")

            # |IC|均值
            ic_vals = [factor_ics[fname][fd][0] for fd in FORWARD_DAYS
                       if factor_ics[fname][fd][2] >= 100]
            mean_abs_ic = np.mean([abs(v) for v in ic_vals]) if ic_vals else 0
            mean_ic = np.mean(ic_vals) if ic_vals else 0
            direction = "+" if mean_ic > 0 else "-" if mean_ic < 0 else "0"
            n_samples = max(factor_ics[fname][fd][2] for fd in FORWARD_DAYS)

            rows.append((mean_abs_ic, fname, ics, mean_abs_ic, direction, n_samples))

        # 按 |IC|均值 排序
        rows.sort(key=lambda r: -r[0])
        for _, fname, ics, mabs, direction, ns in rows[:30]:
            lines.append(
                f"  {fname:<22} {' '.join(ics):<36} {mabs:>7.3f}  {direction:>4} {ns:>8}"
            )

        report_lines.extend(lines)

    # ── 按 |IC| 绝对值排序的因子总排名（10日收益）──
    report_lines.extend([
        f"\n{'=' * 120}",
        "  因子总排名（按10日 IC 绝对值排序）",
        "  说明：IC > 0 = 正向预测（因子值越大收益越高）",
        "         IC < 0 = 反向预测（因子值越大收益越低 - 可反转使用）",
        "         IC ≈ 0 = 纯噪声，直接砍掉",
        "         * 标记 = p<0.05 统计显著",
        f"{'=' * 120}",
    ])
    header = (f"  {'排名':>4} {'因子名':<24} {'5日IC':>8} {'10日IC':>8} {'20日IC':>8} "
              f"{'60日IC':>8} {'|IC|均值':>8} {'信号数':>8} {'类别':<10}")
    report_lines.append(header)
    report_lines.append("  " + "-" * 106)

    # 反向映射因子→类别
    fname_to_cat = {}
    for cat, flist in FACTOR_CATEGORIES.items():
        for fname in flist:
            fname_to_cat[fname] = cat

    all_rankings = []
    for fname in all_factor_names:
        ic_10 = factor_ics[fname].get(10, (0, 1, 0))
        if ic_10[2] < 100:
            continue
        all_rankings.append((abs(ic_10[0]), fname, ic_10[0], ic_10[2]))

    all_rankings.sort(key=lambda r: -r[0])

    for rank, (abs_ic, fname, ic_val, n_samp) in enumerate(all_rankings, 1):
        if rank > 60:
            break
        ics_str = []
        for fd in FORWARD_DAYS:
            ic_v, p_v, _ = factor_ics[fname].get(fd, (0, 1, 0))
            if abs(ic_v) > 0.01 and p_v < 0.05:
                ics_str.append(f"{ic_v:>+7.3f}*")
            else:
                ics_str.append(f"{ic_v:>+7.3f} ")

        mean_abs = np.mean([abs(factor_ics[fname][fd][0]) for fd in FORWARD_DAYS
                            if factor_ics[fname][fd][2] >= 100])
        cat = fname_to_cat.get(fname, "未知")

        report_lines.append(
            f"  {rank:>4} {fname:<24} {' '.join(ics_str):<36} {mean_abs:>7.3f} {n_samp:>8} {cat:<10}"
        )

    # ── 最优分档 vs 最差分档收益差（10日，仅前20个因子）──
    report_lines.extend([
        f"\n{'=' * 120}",
        "  因子分档收益差（10日）：最高组 vs 最低组的平均收益差",
        "  说明：差值越大，因子的实际区分能力越强",
        f"{'=' * 120}",
    ])
    header = (f"  {'因子名':<24} {'Q1(最低)均值%':>14} {'Q5(最高)均值%':>14} "
              f"{'Q5-Q1差值%':>12} {'高组胜率%':>10}")
    report_lines.append(header)
    report_lines.append("  " + "-" * 80)

    spread_results = []
    for fname in list(all_factor_names)[:60]:
        pairs = factor_data[fname].get(10, [])
        if len(pairs) < 500:
            continue
        values = [p[0] for p in pairs]
        returns = [p[1] for p in pairs]
        result = compute_quantile_spread(values, returns)
        if result:
            groups, spread = result
            if len(groups) >= 5:
                spread_results.append((
                    abs(spread), fname,
                    groups[0]["mean_ret"] * 100,
                    groups[-1]["mean_ret"] * 100,
                    spread * 100,
                    groups[-1]["win_rate"],
                    groups[-1]["n"],
                ))

    spread_results.sort(key=lambda r: -r[0])
    for _, fname, q1_mean, q5_mean, spread, wr, n in spread_results[:20]:
        report_lines.append(
            f"  {fname:<24} {q1_mean:>13.2f} {q5_mean:>13.2f} "
            f"{spread:>11.2f} {wr:>9.1f}%"
        )

    # ── 因子相关性矩阵缩略（只列前15个高IC因子）──
    top_factors = [r[1] for r in all_rankings[:15]]
    if len(top_factors) >= 3:
        report_lines.extend([
            f"\n{'=' * 120}",
            "  高IC因子间相关性矩阵（Spearman）",
            "  说明：相关系数>0.7的两个因子高度重复，可以只留一个",
            f"{'=' * 120}",
        ])

        # 构造 DataFarme
        corr_data = {}
        for fname in top_factors:
            pairs = factor_data[fname].get(10, [])
            if pairs:
                # 采样每个因子的值（取所有样本的均值，只为了看趋势）
                vals = [p[0] for p in pairs[:10000]]  # 最多1万个样本
                corr_data[fname] = vals

        if len(corr_data) >= 3:
            cf = pd.DataFrame(dict(
                (k, pd.Series(v)) for k, v in corr_data.items()
            ))
            corr_mat = cf.corr(method="spearman")

            # 格式化输出
            col_width = 12
            header_line = "  " + " " * col_width
            for col in corr_mat.columns:
                header_line += f"{col[:10]:>{col_width}}"
            report_lines.append(header_line)

            for row_name in corr_mat.index:
                line = f"  {row_name[:10]:>{col_width}}"
                for col_name in corr_mat.columns:
                    val = corr_mat.loc[row_name, col_name]
                    line += f"{val:>{col_width}.2f}"
                report_lines.append(line)

    # ── 结论建议 ──
    report_lines.extend([
        f"\n{'=' * 120}",
        "  初步结论与建议",
        f"{'=' * 120}",
    ])

    # 找出正向IC > 0.02 和 负向IC < -0.02 的因子
    pos_factors = []
    neg_factors = []
    noise_factors = []
    for fname in all_factor_names:
        ic_10 = factor_ics[fname].get(10, (0, 1, 0))
        if ic_10[2] < 100:
            noise_factors.append(fname)
            continue
        if ic_10[0] > 0.02:
            pos_factors.append((ic_10[0], fname))
        elif ic_10[0] < -0.02:
            neg_factors.append((ic_10[0], fname))
        else:
            noise_factors.append(fname)

    pos_factors.sort(key=lambda r: -r[0])
    neg_factors.sort(key=lambda r: r[0])

    report_lines.append(f"\n  ✅ 正向预测因子（IC > 0.02, 值越高收益越高）:")
    for ic_v, fn in pos_factors[:10]:
        cat = fname_to_cat.get(fn, "未知")
        report_lines.append(f"    +{ic_v:.3f}  {fn}  ({cat})")

    report_lines.append(f"\n  🔄 反向预测因子（IC < -0.02, 反转使用可能有效）:")
    for ic_v, fn in neg_factors[:10]:
        cat = fname_to_cat.get(fn, "未知")
        report_lines.append(f"    {ic_v:.3f}  {fn}  ({cat})")

    report_lines.append(f"\n  ❌ 噪声因子（|IC| < 0.02, 建议砍掉）:")
    report_lines.append(f"    共 {len(noise_factors)} 个")

    # V2系统整体评价
    report_lines.append(f"\n  📊 V2系统评分IC:")
    for fname in ["v2_score", "v2_mr_score", "v2_tf_score", "v2_level_num"]:
        if fname in factor_ics:
            ic_10 = factor_ics[fname].get(10, (0, 1, 0))
            report_lines.append(
                f"    {fname}: 10日IC = {ic_10[0]:+.3f} (p={ic_10[1]:.4f}, n={ic_10[2]})"
            )

    # ── 写入文件 ──
    report = "\n".join(report_lines)
    report_path = os.path.join(APP_DIR, "factor_ic_report.txt")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)

    print(f"\n[OK] 因子IC报告已写入：{report_path}")
    print(report[:2000])

    return report


if __name__ == "__main__":
    main()
