# -*- coding: utf-8 -*-
"""
V15 Plus+ — 多周期共振评分模块
============================
从5分钟K线构建15/30/60分钟周期，检测多周期趋势共振。
独立插件，fail-safe，不修改核心引擎。
"""
from __future__ import annotations
import numpy as np


def _tf_trend_score(df, period_name: str) -> dict:
    """单周期趋势评分。

    返回:
        {"score": float(0-100), "direction": "多"|"空"|"震荡",
         "facts": [str]}
    """
    if df is None or len(df) < 10:
        return {"score": 50, "direction": "震荡", "facts": []}

    closes = df["收盘"].values
    last = closes[-1]

    # 1. 短期均线方向 (MA5 vs MA20)
    ma5 = df["ma5"].values[-1] if not np.isnan(df["ma5"].values[-1]) else last
    ma5_prev = df["ma5"].values[-6] if len(df) >= 6 and not np.isnan(df["ma5"].values[-6]) else ma5
    ma20 = df["ma20"].values[-1] if not np.isnan(df["ma20"].values[-1]) else last

    ma5_slope = (ma5 - ma5_prev) / ma5_prev * 100 if ma5_prev > 0 else 0
    ma_cross = ma5 > ma20  # 短期在长期之上 = 多头

    # 2. 近期动量 (近5根K线涨跌幅)
    if len(closes) >= 5:
        mom_5 = (closes[-1] / closes[-5] - 1) * 100
    else:
        mom_5 = 0

    # 3. 当前价格在近20根K线的位置
    if len(closes) >= 20:
        h20 = max(closes[-20:])
        l20 = min(closes[-20:])
        position = (last - l20) / (h20 - l20) * 100 if h20 > l20 else 50
    else:
        position = 50

    # ── 评分 ──
    score = 50.0

    # 均线方向 ±15
    if ma5_slope > 0.3 and ma_cross:
        score += 15
    elif ma5_slope > 0.1:
        score += 8
    elif ma5_slope < -0.3 and not ma_cross:
        score -= 15
    elif ma5_slope < -0.1:
        score -= 8

    # 动量 ±10
    if mom_5 > 1.0:
        score += 10
    elif mom_5 > 0.3:
        score += 5
    elif mom_5 < -1.0:
        score -= 10
    elif mom_5 < -0.3:
        score -= 5

    # 位置 ±10
    if position > 75:
        score += 10
    elif position > 60:
        score += 5
    elif position < 25:
        score -= 10
    elif position < 40:
        score -= 5

    score = max(0, min(100, score))

    if score >= 60:
        direction = "多"
    elif score <= 40:
        direction = "空"
    else:
        direction = "震荡"

    return {
        "score": round(score, 1),
        "direction": direction,
        "facts": [
            f"[{period_name}] 方向:{direction} 评分:{score:.0f} "
            f"MA5斜率:{ma5_slope:+.1f}% 动量:{mom_5:+.1f}% 位置:{position:.0f}%"
        ],
    }


def compute_resonance(code: str) -> dict:
    """多周期共振评分主入口。

    从5分钟数据构建15/30/60分钟三周期，
    检测趋势是否同向共振。

    返回:
        {"adjustment": float, "resonance_type": str, "facts": [str], "detail": {...}}
    """
    result = {"adjustment": 0.0, "resonance_type": "无数据", "facts": [], "detail": {}}

    try:
        from _plus_.minline_reader import load_5min, aggregate

        df5 = load_5min(code, recent_days=10)
        if df5 is None:
            result["facts"] = ["[多周期] 无5分钟数据"]
            return result

        # 聚合到三个周期
        tf15 = aggregate(df5, 3)   # 15分钟
        tf30 = aggregate(df5, 6)   # 30分钟
        tf60 = aggregate(df5, 12)  # 60分钟

        # 各周期独立评分
        s15 = _tf_trend_score(tf15, "15分钟")
        s30 = _tf_trend_score(tf30, "30分钟")
        s60 = _tf_trend_score(tf60, "60分钟")

        scores = [s15, s30, s60]
        directions = [s["direction"] for s in scores]
        avg_score = np.mean([s["score"] for s in scores])

        result["detail"] = {
            "tf15": s15,
            "tf30": s30,
            "tf60": s60,
            "avg_score": round(avg_score, 1),
        }

        # 收集所有 facts
        for s in scores:
            result["facts"].extend(s["facts"])

        # ── 共振判定 ──
        long_count = directions.count("多")
        short_count = directions.count("空")
        shake_count = directions.count("震荡")

        adj = 0.0

        if long_count == 3:
            # 三周期同向多 → 共振加分
            adj = 6 + (avg_score - 50) * 0.1
            result["resonance_type"] = "多周期共振↑"
            result["facts"].append(f"[多周期] 三周期共振做多 +{adj:.1f}")
        elif short_count == 3:
            # 三周期同向空 → 共振减分
            adj = -(6 + (50 - avg_score) * 0.1)
            result["resonance_type"] = "多周期共振↓"
            result["facts"].append(f"[多周期] 三周期共振做空 {adj:+.1f}")
        elif long_count == 2 and shake_count == 1:
            # 两多一震荡 → 温和偏多
            adj = 2 + (avg_score - 50) * 0.05
            result["resonance_type"] = "偏多共振"
            result["facts"].append(f"[多周期] 两周期偏多共振 +{adj:.1f}")
        elif short_count == 2 and shake_count == 1:
            # 两空一震荡 → 温和偏空
            adj = -(2 + (50 - avg_score) * 0.05)
            result["resonance_type"] = "偏空共振"
            result["facts"].append(f"[多周期] 两周期偏空共振 {adj:+.1f}")
        elif long_count == 1 and short_count == 1 and shake_count == 1:
            # 多空分歧 → 中性（但多空分歧本身是风险信号）
            result["resonance_type"] = "多空分歧"
            result["facts"].append("[多周期] 多空分歧，周期不共振 中性")
        elif shake_count >= 2:
            result["resonance_type"] = "震荡"
            result["facts"].append("[多周期] 多周期震荡，方向不明确 中性")
        else:
            # 2多1空 → 偏多但需谨慎
            adj = 1.0
            result["resonance_type"] = "偏多(有分歧)"
            result["facts"].append(f"[多周期] 偏多但有分歧 +{adj:.1f}")

        # 限制调整范围 ±8
        adj = max(-8.0, min(8.0, adj))
        result["adjustment"] = round(adj, 1)

        return result

    except ImportError:
        result["facts"] = ["[多周期] 模块不可用"]
        return result
    except Exception as e:
        result["facts"] = [f"[多周期] 异常: {e}"]
        return result
