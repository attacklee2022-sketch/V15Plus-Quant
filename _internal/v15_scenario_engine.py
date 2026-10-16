# -*- coding: utf-8 -*-
"""
V15 Alpha — 场景感知引擎（阶段三）
=================================
基于V15评分因子，识别当前市场场景（超跌反弹/趋势接力/突破确认/无场景），
并为每个场景应用专属权重。

四个场景：
  1. 超跌反弹 — 反转因子权重加大
  2. 趋势接力 — MACD+量能权重加大
  3. 突破确认 — 安全结构+量能权重加大
  4. 无场景   — 使用默认权重
"""
from __future__ import annotations
from typing import Optional
from v15_scorer import compute_v15_signal, _ema, _ma


# 场景定义
SCENE_OVERSELL = "超跌反弹"      # 超跌反弹
SCENE_TREND = "趋势接力"         # 趋势接力
SCENE_BREAKOUT = "突破确认"      # 突破确认
SCENE_NONE = "无场景"            # 无场景

# 等级阈值
LEVEL_THRESHOLDS = [
    (70, "强势"),
    (55, "偏强"),
    (45, "中性"),
    (0, "弱势"),
]

# 各场景专属权重（覆盖V15默认权重）—— 偏离值减半，防止场景过拟合
# 默认权重: rev=0.35, macd=0.25, vol=0.25, safe=0.15
SCENE_WEIGHTS = {
    SCENE_OVERSELL: {"rev": 0.425, "macd": 0.20, "vol": 0.225, "safe": 0.15},
    SCENE_TREND:    {"rev": 0.25, "macd": 0.30, "vol": 0.275, "safe": 0.175},
    SCENE_BREAKOUT: {"rev": 0.25, "macd": 0.225, "vol": 0.30, "safe": 0.225},
    SCENE_NONE:     {"rev": 0.35, "macd": 0.25, "vol": 0.25, "safe": 0.15},
}


def detect_scenario(closes: list[float], volumes: list[float]) -> tuple[str, dict]:
    """
    检测当前场景。

    返回:
        (scenario_name, scenario_data_dict)
    """
    n = len(closes)
    if n < 60:
        return SCENE_NONE, {"reason": "数据不足"}

    reasons = []
    last_close = closes[-1]

    # ── RSI 超卖检测 ──
    delta = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [d for d in delta if d > 0]
    losses = [-d for d in delta if d < 0]
    rsi_period = 14
    avg_gain = sum(gains[-rsi_period:]) / rsi_period if len(gains) >= rsi_period else sum(gains) / max(len(gains), 1)
    avg_loss = sum(losses[-rsi_period:]) / rsi_period if len(losses) >= rsi_period else sum(losses) / max(len(losses), 1)
    rsi = 50.0
    if avg_loss > 0:
        rsi = 100 - 100 / (1 + avg_gain / avg_loss)
    elif avg_gain > 0:
        rsi = 100.0

    # ── MACD ──
    e12 = _ema(closes, 12)
    e26 = _ema(closes, 26)
    macd_line = [a - b for a, b in zip(e12, e26)]
    macd_sig = _ema(macd_line, 9)
    macd_hist = [m - s for m, s in zip(macd_line, macd_sig)]
    macd_val = macd_line[-1] if macd_line else 0
    hist_val = macd_hist[-1] if macd_hist else 0

    # ── 均线 ──
    ma5 = _ma(closes, 5) or last_close
    ma10 = _ma(closes, 10) or last_close
    ma20 = _ma(closes, 20) or last_close
    ma60 = _ma(closes, 60) or last_close
    is_bullish = ma5 > ma10 > ma20 > ma60
    support_ma60 = last_close > ma60

    # ── 近20日涨跌幅 ──
    pct_20d = (last_close / closes[-20] - 1) * 100 if n >= 20 else 0
    # 近5日涨跌幅
    pct_5d = (last_close / closes[-5] - 1) * 100 if n >= 5 else 0

    # ── 成交量 ──
    vol_ma20 = _ma(volumes, 20) or 1
    vol_ratio = volumes[-1] / vol_ma20

    # 1. 超跌反弹检测
    oversold_cond = rsi < 35 and pct_20d < -8
    oversold_rev_cond = rsi < 40 and pct_20d < -5 and pct_5d > -2 and hist_val > 0
    if oversold_cond or oversold_rev_cond:
        reasons.append(f"RSI={rsi:.0f},20日跌幅{pct_20d:.1f}%")
        return SCENE_OVERSELL, {"reason": "; ".join(reasons), "rsi": rsi, "pct_20d": pct_20d}

    # 2. 突破确认检测
    if n >= 30:
        high_20d = max(closes[-20:])
        close_to_high = last_close / high_20d - 1

        # 三日验证：过去3天最低价 >= 20日最高 × 0.97（站稳高位，非单根脉冲）
        three_day_stand = True
        if n >= 3:
            low_3d = min(closes[-3:])
            three_day_stand = low_3d >= high_20d * 0.97

        # 布林确认：收盘 >= 布林上轨 × 0.99（突破在通道顶附近，非区间内波动）
        boll_confirm = True
        if n >= 20:
            boll_mid = _ma(closes, 20) or last_close
            sq_sum = sum((c - boll_mid) ** 2 for c in closes[-20:])
            boll_std = (sq_sum / 20) ** 0.5
            boll_up = boll_mid + 2 * boll_std
            boll_confirm = last_close >= boll_up * 0.99

        breakout_cond = (
            close_to_high >= -0.005  # 现价 >= 20日最高 * 0.995（真正突破）
            and vol_ratio > 1.5      # 放量 > 1.5x
            and hist_val > 0
            and support_ma60
            and pct_20d > -8         # 不在超跌区
            and three_day_stand       # 三日站稳高位
            and boll_confirm          # 布林通道配合
        )
        if breakout_cond:
            reasons.append(f"近20日高{high_20d:.2f},量比{vol_ratio:.1f}")
            return SCENE_BREAKOUT, {"reason": "; ".join(reasons), "high_20d": high_20d, "vol_ratio": vol_ratio}

    # 3. 趋势接力检测
    # MA60斜率：20日前的MA60 vs 当前MA60
    ma60_slope = 0.0
    if n >= 80:
        ma60_20d_ago = _ma(closes[:-20], 60) or ma60
        ma60_slope = (ma60 - ma60_20d_ago) / ma60_20d_ago * 100 if ma60_20d_ago > 0 else 0
    # 5日均量
    vol_5avg = sum(volumes[-5:]) / 5 if n >= 5 else vol_ma20
    trend_cond = (
        macd_val > 0
        and hist_val > 0
        and is_bullish
        and support_ma60
        and ma60_slope > 0           # MA60必须向上
        and vol_5avg / vol_ma20 > 0.8  # 量能不能太萎缩
    )
    if trend_cond:
        reasons.append(f"MACD>0,多头排列")
        return SCENE_TREND, {"reason": "; ".join(reasons), "macd": macd_val, "hist": hist_val}

    # 默认：无场景
    return SCENE_NONE, {"reason": "未匹配任何场景"}


def compute_scenario_signal(
    closes: list[float],
    highs: list[float],
    lows: list[float],
    volumes: list[float],
    last_close: float,
    last_volume: float,
    config: Optional[dict] = None,
) -> dict:
    """
    场景感知评分 — 主入口（阶段三）

    流程：
    1. 计算V15原始评分
    2. 检测当前场景
    3. 应用场景专属权重重新评分

    返回:
        {
            "score": float,      # 场景调整后评分
            "level": str,        # 等级
            "scenario": str,     # 检测到的场景
            "raw_v15_score": float, # 原始V15评分
            "rev_score": float,
            "macd_score": float,
            "vol_score": float,
            "safe_score": float,
        }
    """
    # 计算V15评分
    v15 = compute_v15_signal(closes, highs, lows, volumes, last_close, last_volume, config)
    raw_score = v15["score"]

    # 检测场景
    scene_name, scene_data = detect_scenario(closes, volumes)

    # 应用场景权重
    weights = SCENE_WEIGHTS.get(scene_name, SCENE_WEIGHTS[SCENE_NONE])

    rev = v15["rev_score"]
    macd = v15["macd_score"]
    vol = v15["vol_score"]
    safe = v15["safe_score"]

    adjusted = rev * weights["rev"] + macd * weights["macd"] + vol * weights["vol"] + safe * weights["safe"]
    adjusted = round(max(0, min(100, adjusted)), 1)

    # 等级
    level = "弱势"
    for threshold, lvl in LEVEL_THRESHOLDS:
        if adjusted >= threshold:
            level = lvl
            break

    return {
        "score": adjusted,
        "level": level,
        "scenario": scene_name,
        "scenario_data": scene_data,
        "raw_v15_score": raw_score,
        "rev_score": rev,
        "macd_score": macd,
        "vol_score": vol,
        "safe_score": safe,
    }
