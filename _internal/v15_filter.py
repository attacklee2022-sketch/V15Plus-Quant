# -*- coding: utf-8 -*-
"""
V15 Alpha — 信号过滤与风控模块（阶段四）
==========================================
在场景引擎评分基础上应用多周期共振过滤、波动率过滤等规则，
提升信号质量，降低假信号和追高风险。

层级关系：
  V15评分(因子) → 场景引擎(场景感知) → 过滤层(信号净化)

使用方式：
  from v15_filter import compute_filtered_signal
  result = compute_filtered_signal(closes, highs, lows, volumes, last_close, last_volume)
"""
from __future__ import annotations
from typing import Optional
from v15_scenario_engine import compute_scenario_signal, SCENE_NONE
from v15_scorer import _ema


# ── 工具函数 ──────────────────────────────────

def _ma(vals: list[float], n: int) -> Optional[float]:
    if len(vals) < n:
        return None
    return sum(vals[-n:]) / n


def _safe_get(arr: list[float], idx: int) -> Optional[float]:
    if 0 <= idx < len(arr):
        return arr[idx]
    return None


# ══════════════════════════════════════════════════
# 1. 多周期共振过滤
# ══════════════════════════════════════════════════

MULTI_PERIOD_CONFIG = {
    "lookback_days": [5, 10, 20],
    "resonance_weight": 0.6,
    "current_weight": 0.4,
    "pulse_threshold": 1.15,
    "downgrade_gap": 10,
    "upgrade_bonus": 6,
    "severe_downgrade_gap": 20,
    "severe_downgrade_penalty": 15,
}


def _compute_resonance_score(
    closes: list[float],
    highs: list[float],
    lows: list[float],
    volumes: list[float],
    config: dict,
) -> dict:
    n = len(closes)
    lookback = config["lookback_days"]
    facts = []

    current_sig = compute_scenario_signal(
        closes, highs, lows, volumes,
        closes[-1], volumes[-1],
    )
    current_score = current_sig["score"]

    # ── 快速路径：低分股票跳过历史回溯 ──
    # 当前分 < 40（接近弱势）时，多周期回溯最多加 ~15 分（一致看好+8 + 趋势加分），
    # 仍难以越过 55（偏强）阈值。跳过 3 次完整场景评分，节省 ~60% 计算量。
    period_scores = {}
    valid_periods = 0
    if current_score >= 40:
        for days in lookback:
            idx = n - 1 - days
            if idx < 60:
                continue
            c = closes[: idx + 1]
            h = highs[: idx + 1]
            l_ = lows[: idx + 1]
            v = volumes[: idx + 1]
            try:
                sig = compute_scenario_signal(c, h, l_, v, closes[idx], volumes[idx])
                period_scores[days] = sig["score"]
                valid_periods += 1
            except Exception:
                continue
    else:
        facts.append(f"当前分{current_score:.0f}<40，跳过历史回溯（加不了多少分）")

    if valid_periods == 0:
        return {
            "resonance_score": current_score,
            "period_scores": {},
            "consistency": "数据不足",
            "direction": "中性",
            "facts": ["多周期数据不足，使用当前分"],
        }

    all_scores = [current_score] + list(period_scores.values())
    resonance_score = sum(all_scores) / len(all_scores)

    consistency = "一致"
    direction = "中性"
    pulse_detected = False

    if 5 in period_scores and 20 in period_scores:
        ratio = period_scores[5] / max(period_scores[20], 1)
        if ratio > config["pulse_threshold"] and period_scores[5] > 60 and period_scores[20] < 58:
            consistency = "短期脉冲"
            pulse_detected = True
            facts.append(
                f"短期脉冲: 5日前{period_scores[5]:.0f}分 >> "
                f"20日前{period_scores[20]:.0f}分(比{ratio:.2f})"
            )

    if len(all_scores) >= 3:
        mean_s = sum(all_scores) / len(all_scores)
        var_s = sum((s - mean_s) ** 2 for s in all_scores) / len(all_scores)
        std_s = var_s ** 0.5
        if std_s > 8:
            high_scores = [s for s in all_scores if s >= 50]
            low_scores = [s for s in all_scores if s < 50]
            if len(high_scores) >= len(all_scores) * 0.75:
                sub_consistency = "分歧偏多"
            elif len(low_scores) >= len(all_scores) * 0.75:
                sub_consistency = "分歧偏空"
            else:
                sub_consistency = "分歧"
            consistency = sub_consistency
            facts.append(f"多周期{consistency}(标准差{std_s:.1f}): {[f'{s:.0f}' for s in all_scores]}")

    if not pulse_detected:
        if consistency == "一致":
            if all(s >= 58 for s in all_scores):
                direction = "看好"
                facts.append(f"多周期一致看好: {[f'{s:.0f}' for s in all_scores]}")
            elif all(s < 48 for s in all_scores):
                direction = "看空"
                facts.append(f"多周期一致看空: {[f'{s:.0f}' for s in all_scores]}")
            else:
                facts.append(f"多周期一致: 当前{current_score:.0f} / 5日{period_scores.get(5, 0):.0f} / "
                             f"10日{period_scores.get(10, 0):.0f} / 20日{period_scores.get(20, 0):.0f}")

    if 5 in period_scores and 20 in period_scores:
        trend = current_score - period_scores[20]
        if trend > 12:
            facts.append(f"评分趋势明显上升({trend:.0f}分/20日)")
        elif trend > 5:
            facts.append(f"评分趋势微升({trend:.0f}分/20日)")
        elif trend < -12:
            facts.append(f"评分趋势明显下降({trend:.0f}分/20日)")
        elif trend < -5:
            facts.append(f"评分趋势微降({trend:.0f}分/20日)")
    elif 10 in period_scores:
        trend = current_score - period_scores[10]
        if trend > 8:
            facts.append(f"评分趋势上升({trend:.0f}分/10日)")
        elif trend < -8:
            facts.append(f"评分趋势下降({trend:.0f}分/10日)")

    if 5 in period_scores and 10 in period_scores and 20 in period_scores:
        accel_5 = current_score - period_scores[5]
        accel_10 = period_scores[5] - period_scores[10]
        accel_20 = period_scores[10] - period_scores[20]
        avg_accel = (accel_5 + accel_10 + accel_20) / 3
        if abs(avg_accel) > 5:
            facts.append(f"评分趋势加速度{avg_accel:+.1f}(近5日{accel_5:+.0f}/中段{accel_10:+.0f}/远段{accel_20:+.0f})")

    return {
        "resonance_score": round(resonance_score, 1),
        "current_score": round(current_score, 1),
        "period_scores": {k: round(v, 1) for k, v in period_scores.items()},
        "consistency": consistency,
        "direction": direction,
        "facts": facts,
    }


# ══════════════════════════════════════════════════
# 2.5 MACD-价格背离检测
# ══════════════════════════════════════════════════

DIVERGENCE_CONFIG = {
    "lookback": 60,
    "price_pivot_min_gap": 3,
    "macd_pivot_min_gap": 3,
    "top_div_penalty": 12,
    "top_div_downgrade": 1,
    "bottom_div_bonus": 8,
    "hidden_top_penalty": 5,
    "hidden_bottom_bonus": 5,
}


def _compute_macd(closes: list[float]) -> tuple[list[float], list[float], list[float]]:
    e12 = _ema(closes, 12)
    e26 = _ema(closes, 26)
    macd_line = [a - b for a, b in zip(e12, e26)]
    sig = _ema(macd_line, 9)
    hist = [m - s for m, s in zip(macd_line, sig)]
    return macd_line, sig, hist


def _find_pivots(vals: list[float], lookback: int = 5) -> list[tuple[int, float, bool]]:
    pivots = []
    n = len(vals)
    for i in range(lookback, n - lookback):
        left = vals[i - lookback:i]
        right = vals[i + 1:i + lookback + 1]
        if not left or not right:
            continue
        if vals[i] >= max(left) and vals[i] >= max(right) and vals[i] > vals[i - 1]:
            pivots.append((i, vals[i], True))
        if vals[i] <= min(left) and vals[i] <= min(right) and vals[i] < vals[i - 1]:
            pivots.append((i, vals[i], False))
    return pivots


def _compute_divergence(closes, highs, lows, config) -> dict:
    result = {"divergence_type": "无", "adjustment": 0.0, "level_adjustment": 0, "facts": []}
    n = len(closes)
    lb = config["lookback"]
    if n < lb + 20:
        return result
    recent_range = (max(closes[-20:]) - min(closes[-20:])) / closes[-1] * 100
    if recent_range < 3.0:
        return result
    macd_line, _, _ = _compute_macd(closes)
    recent_closes = closes[-lb:]
    recent_macd = macd_line[-lb:]
    price_pivots = _find_pivots(recent_closes, config["price_pivot_min_gap"])
    macd_pivots = _find_pivots(recent_macd, config["macd_pivot_min_gap"])
    if len(price_pivots) < 2 or len(macd_pivots) < 2:
        return result
    price_highs = [(idx, val) for idx, val, is_high in price_pivots if is_high]
    price_lows = [(idx, val) for idx, val, is_high in price_pivots if not is_high]
    macd_highs = [(idx, val) for idx, val, is_high in macd_pivots if is_high]
    macd_lows = [(idx, val) for idx, val, is_high in macd_pivots if not is_high]
    adjustments = 0.0
    level_adj = 0
    divergence_found = None

    if len(price_highs) >= 2 and len(macd_highs) >= 2:
        p_last, p_val = price_highs[-1]
        p_prev, p_prev_val = price_highs[-2]
        macd_between = [(i, v) for i, v in macd_highs if p_prev <= i <= p_last]
        if len(macd_between) >= 2:
            m_peak = macd_between[-1][1]       # 距价格新高最近的MACD高点
            m_prev_peak = macd_between[0][1]
            if p_val > p_prev_val and m_peak < m_prev_peak:
                divergence_found = "顶背离"
                adjustments -= config["top_div_penalty"]
                level_adj = max(level_adj, config["top_div_downgrade"])
                result["facts"].append(
                    f"顶背离: 价格{p_prev_val:.2f}→{p_val:.2f}(新高), "
                    f"MACD{m_prev_peak:.2f}→{m_peak:.2f}(未新高) -{config['top_div_penalty']}分"
                )

    if not divergence_found and len(price_lows) >= 2 and len(macd_lows) >= 2:
        p_last, p_val = price_lows[-1]
        p_prev, p_prev_val = price_lows[-2]
        macd_between = [(i, v) for i, v in macd_lows if p_prev <= i <= p_last]
        if len(macd_between) >= 2:
            m_trough = macd_between[-1][1]      # 距价格新低最近的MACD低点
            m_prev_trough = macd_between[0][1]
            if p_val < p_prev_val and m_trough > m_prev_trough:
                divergence_found = "底背离"
                adjustments += config["bottom_div_bonus"]
                result["facts"].append(
                    f"底背离: 价格{p_prev_val:.2f}→{p_val:.2f}(新低), "
                    f"MACD{m_prev_trough:.2f}→{m_trough:.2f}(未新低) +{config['bottom_div_bonus']}分"
                )

    if not divergence_found and len(price_lows) >= 2 and len(macd_lows) >= 2:
        p_last, p_val = price_lows[-1]
        p_prev, p_prev_val = price_lows[-2]
        if p_val > p_prev_val:
            macd_between = [(i, v) for i, v in macd_lows if p_prev <= i <= p_last]
            if len(macd_between) >= 2:
                m_trough = macd_between[-1][1]      # 距价格新低最近的MACD低点
                m_prev_trough = macd_between[0][1]
                if m_trough < m_prev_trough:
                    divergence_found = "隐藏顶背离"
                    adjustments -= config["hidden_top_penalty"]
                    result["facts"].append(
                        f"隐藏顶背离: 价格未破前低({p_prev_val:.2f}→{p_val:.2f}), "
                        f"但MACD破前低({m_prev_trough:.2f}→{m_trough:.2f}) -{config['hidden_top_penalty']}分"
                    )

    if not divergence_found and len(price_highs) >= 2 and len(macd_highs) >= 2:
        p_last, p_val = price_highs[-1]
        p_prev, p_prev_val = price_highs[-2]
        if p_val < p_prev_val:
            macd_between = [(i, v) for i, v in macd_highs if p_prev <= i <= p_last]
            if len(macd_between) >= 2:
                m_peak = macd_between[-1][1]       # 距价格新高最近的MACD高点
                m_prev_peak = macd_between[0][1]
                if m_peak > m_prev_peak:
                    divergence_found = "隐藏底背离"
                    adjustments += config["hidden_bottom_bonus"]
                    result["facts"].append(
                        f"隐藏底背离: 价格未破前高({p_prev_val:.2f}→{p_val:.2f}), "
                        f"但MACD破前高({m_prev_peak:.2f}→{m_peak:.2f}) +{config['hidden_bottom_bonus']}分"
                    )

    if divergence_found:
        result["divergence_type"] = divergence_found
    result["adjustment"] = round(adjustments, 1)
    result["level_adjustment"] = level_adj
    return result


# ══════════════════════════════════════════════════
# 2.6 ROC动量背离检测
# ══════════════════════════════════════════════════

MOMENTUM_DIVERGENCE_CONFIG = {
    "lookback": 60,
    "min_swing_pct": 2.5,
    "min_swing_gap": 3,
    "top_div_penalty": 16,
    "bottom_div_bonus": 5,
    "hidden_top_penalty": 4,
    "hidden_bottom_bonus": 4,
    "roc_period": 10,
}


def _compute_roc(vals: list[float], n: int) -> list[float]:
    if len(vals) <= n:
        return [0.0] * len(vals)
    roc = [0.0] * n
    for i in range(n, len(vals)):
        roc.append((vals[i] / vals[i - n] - 1) * 100)
    return roc


def _compute_momentum_divergence(closes, config) -> dict:
    result = {"divergence_type": "无", "adjustment": 0.0, "level_adjustment": 0, "facts": []}
    n = len(closes)
    lb = config["lookback"]
    if n < lb + config["roc_period"]:
        return result
    recent_range = (max(closes[-20:]) - min(closes[-20:])) / closes[-1] * 100
    if recent_range < 2.0:
        return result
    roc = _compute_roc(closes, config["roc_period"])
    recent_closes = closes[-lb:]
    recent_roc = roc[-lb:]
    price_pivots = _find_pivots(recent_closes, config["min_swing_gap"])
    if len(price_pivots) < 3:
        return result
    adjustments = 0.0
    level_adj = 0
    divergence_found = None
    price_highs = [(idx, val) for idx, val, is_high in price_pivots if is_high]
    price_lows = [(idx, val) for idx, val, is_high in price_pivots if not is_high]

    if len(price_highs) >= 2:
        p_last_idx, p_last = price_highs[-1]
        p_prev_idx, p_prev = price_highs[-2]
        p_change = (p_last - p_prev) / p_prev * 100
        mom_last = recent_roc[p_last_idx] if p_last_idx < len(recent_roc) else 0
        mom_prev = recent_roc[p_prev_idx] if p_prev_idx < len(recent_roc) else 0
        if p_change > config["min_swing_pct"] and mom_last < mom_prev * 0.8:
            divergence_found = "顶背离(动量)"
            adjustments -= config["top_div_penalty"]
            level_adj = max(level_adj, 1)
            result["facts"].append(
                f"动量顶背离: 价格{p_prev:.2f}→{p_last:.2f}(+{p_change:.1f}%), "
                f"ROC{mom_prev:.2f}→{mom_last:.2f} -{config['top_div_penalty']}分"
            )

    if not divergence_found and len(price_lows) >= 2:
        p_last_idx, p_last = price_lows[-1]
        p_prev_idx, p_prev = price_lows[-2]
        p_change = (p_last - p_prev) / p_prev * 100
        mom_last = recent_roc[p_last_idx] if p_last_idx < len(recent_roc) else 0
        mom_prev = recent_roc[p_prev_idx] if p_prev_idx < len(recent_roc) else 0
        if p_change < -config["min_swing_pct"] and mom_last > mom_prev * 1.2:
            divergence_found = "底背离(动量)"
            adjustments += config["bottom_div_bonus"]
            result["facts"].append(
                f"动量底背离: 价格{p_prev:.2f}→{p_last:.2f}({p_change:.1f}%), "
                f"ROC{mom_prev:.2f}→{mom_last:.2f} +{config['bottom_div_bonus']}分"
            )

    if not divergence_found and len(price_lows) >= 2:
        p_last_idx, p_last = price_lows[-1]
        p_prev_idx, p_prev = price_lows[-2]
        if p_last > p_prev:
            mom_last = recent_roc[p_last_idx] if p_last_idx < len(recent_roc) else 0
            mom_prev = recent_roc[p_prev_idx] if p_prev_idx < len(recent_roc) else 0
            if mom_last < mom_prev:
                divergence_found = "隐藏顶背离(动量)"
                adjustments -= config["hidden_top_penalty"]
                result["facts"].append(
                    f"动量隐藏顶背离: 低点抬高({p_prev:.2f}→{p_last:.2f}), "
                    f"但ROC走弱({mom_prev:.2f}→{mom_last:.2f}) -{config['hidden_top_penalty']}分"
                )

    if not divergence_found and len(price_highs) >= 2:
        p_last_idx, p_last = price_highs[-1]
        p_prev_idx, p_prev = price_highs[-2]
        if p_last < p_prev:
            mom_last = recent_roc[p_last_idx] if p_last_idx < len(recent_roc) else 0
            mom_prev = recent_roc[p_prev_idx] if p_prev_idx < len(recent_roc) else 0
            if mom_last > mom_prev:
                divergence_found = "隐藏底背离(动量)"
                adjustments += config["hidden_bottom_bonus"]
                result["facts"].append(
                    f"动量隐藏底背离: 高点降低({p_prev:.2f}→{p_last:.2f}), "
                    f"但ROC走强({mom_prev:.2f}→{mom_last:.2f}) +{config['hidden_bottom_bonus']}分"
                )

    if divergence_found:
        result["divergence_type"] = divergence_found
    result["adjustment"] = round(adjustments, 1)
    result["level_adjustment"] = level_adj
    return result


# ══════════════════════════════════════════════════
# 3. 波动率过滤
# ══════════════════════════════════════════════════

VOLATILITY_CONFIG = {
    "atr_period": 14,
    "high_vol_threshold": 4.0,
    "mid_high_vol_threshold": 3.0,
    "low_vol_threshold": 0.6,
    "high_vol_penalty": 18,
    "mid_high_vol_penalty": 8,
    "low_vol_penalty": 8,
    "volume_surge_threshold": 3.0,
    "volume_mid_surge_threshold": 2.0,
    "volume_surge_penalty": 12,
    "volume_mid_surge_penalty": 5,
    "vol_shrink_threshold": 0.4,
    "vol_shrink_penalty": 6,
}


def _compute_volatility_filter(closes, highs, lows, volumes, config) -> dict:
    n = len(closes)
    facts = []
    vol_adjustment = 0.0
    level_adjustment = 0
    if n < config["atr_period"] + 5:
        return {"atr_norm": 0, "vol_adjustment": 0, "level_adjustment": 0, "facts": ["数据不足"]}
    tr_vals = []
    for j in range(-config["atr_period"], 0):
        h = highs[j]
        l_ = lows[j]
        c_prev = closes[j - 1]
        tr_vals.append(max(h - l_, abs(h - c_prev), abs(l_ - c_prev)))
    atr = sum(tr_vals) / config["atr_period"]
    atr_norm = atr / closes[-1] * 100

    if atr_norm > config["high_vol_threshold"]:
        vol_adjustment -= config["high_vol_penalty"]
        level_adjustment -= 1
        facts.append(f"高波动ATR={atr_norm:.1f}%(>{config['high_vol_threshold']:.0f}%) 扣{config['high_vol_penalty']}分")
    elif atr_norm > config["mid_high_vol_threshold"]:
        vol_adjustment -= config["mid_high_vol_penalty"]
        facts.append(f"中高波动ATR={atr_norm:.1f}%(3-4%) 扣{config['mid_high_vol_penalty']}分")
    elif atr_norm < config["low_vol_threshold"]:
        vol_adjustment -= config["low_vol_penalty"]
        level_adjustment -= 1
        facts.append(f"极低波动ATR={atr_norm:.1f}%(死股) 扣{config['low_vol_penalty']}分")
    else:
        facts.append(f"正常波动ATR={atr_norm:.1f}%")

    vol_ma20 = _ma(volumes, 20) or 0
    if vol_ma20 > 0:
        vol_ratio = volumes[-1] / vol_ma20
        if vol_ratio > config["volume_surge_threshold"]:
            vol_adjustment -= config["volume_surge_penalty"]
            level_adjustment -= 1
            facts.append(f"异常放量{vol_ratio:.1f}倍量比(>{config['volume_surge_threshold']:.0f}x) 扣{config['volume_surge_penalty']}分")
        elif vol_ratio > config["volume_mid_surge_threshold"]:
            vol_adjustment -= config["volume_mid_surge_penalty"]
            facts.append(f"中度放量{vol_ratio:.1f}倍量比({config['volume_mid_surge_threshold']:.0f}-{config['volume_surge_threshold']:.0f}x) 扣{config['volume_mid_surge_penalty']}分")
        elif vol_ratio < config["vol_shrink_threshold"]:
            vol_adjustment -= config["vol_shrink_penalty"]
            facts.append(f"极度缩量{vol_ratio:.1f}倍量比(<{config['vol_shrink_threshold']:.1f}x) 扣{config['vol_shrink_penalty']}分")

    if n >= config["atr_period"] + 10:
        tr_vals_recent = []
        for j in range(-5, 0):
            h = highs[j]
            l_ = lows[j]
            c_prev = closes[j - 1]
            tr_vals_recent.append(max(h - l_, abs(h - c_prev), abs(l_ - c_prev)))
        atr_recent = sum(tr_vals_recent) / 5
        atr_recent_norm = atr_recent / closes[-1] * 100
        tr_vals_early = []
        for j in range(-config["atr_period"] - 5, -5):
            h = highs[j]
            l_ = lows[j]
            c_prev = closes[j - 1]
            tr_vals_early.append(max(h - l_, abs(h - c_prev), abs(l_ - c_prev)))
        atr_early = sum(tr_vals_early) / config["atr_period"]
        atr_early_norm = atr_early / closes[-6] * 100 if len(closes) > 6 else atr_recent_norm
        if atr_early_norm > 0 and atr_recent_norm / atr_early_norm > 1.5:
            vol_adjustment -= 5
            facts.append(f"波动率快速上升({atr_early_norm:.1f}%→{atr_recent_norm:.1f}%) 扣5分")
        elif atr_early_norm > 0 and atr_recent_norm / atr_early_norm < 0.5:
            facts.append(f"波动率快速下降({atr_early_norm:.1f}%→{atr_recent_norm:.1f}%) 趋稳")

    return {"atr_norm": round(atr_norm, 2), "vol_adjustment": round(vol_adjustment, 1), "level_adjustment": level_adjustment, "facts": facts}


# ══════════════════════════════════════════════════
# 3. 板块共振过滤
# ══════════════════════════════════════════════════

SECTOR_FILTER_CONFIG = {
    "high_heat_bonus": 10,
    "mid_heat_bonus": 5,
    "low_heat_penalty": 8,
    "strong_breadth_threshold": 65,
    "weak_breadth_threshold": 30,
    "breadth_bonus": 5,
    "breadth_penalty": 5,
}


def _compute_sector_resonance(code=None, name=None, sector_map=None, config=None) -> dict:
    result = {"sector": "", "heat": 50.0, "breadth": 50.0, "adjustment": 0.0, "available": False, "facts": []}
    cfg = dict(SECTOR_FILTER_CONFIG)
    if config:
        cfg.update(config)
    if not sector_map or not code or not name:
        result["facts"] = ["板块数据不可用，跳过板块过滤"]
        return result
    try:
        from v14_deep_engine import _best_sector_match
        sector = _best_sector_match(sector_map, code, name)
    except ImportError:
        result["facts"] = ["板块匹配模块不可用"]
        return result
    except Exception:
        sector = "通用"
    sector_info = sector_map.get(sector, sector_map.get("通用"))
    if not sector_info:
        result["facts"] = [f"未找到板块{sector}数据"]
        return result
    heat = float(sector_info.get("heat", 50))
    breadth = float(sector_info.get("breadth", 50))
    result["sector"] = sector
    result["heat"] = heat
    result["breadth"] = breadth
    result["available"] = True
    facts = [f"板块: {sector} (热度{heat:.0f}/扩散度{breadth:.0f})"]
    if heat >= 80:
        bonus = cfg["high_heat_bonus"]
        result["adjustment"] += bonus
        facts.append(f"板块热度高(heat={heat:.0f}) +{bonus}分")
    elif heat >= 60:
        bonus = cfg["mid_heat_bonus"]
        result["adjustment"] += bonus
        facts.append(f"板块热度中高({heat:.0f}) +{bonus}分")
    elif heat < 40:
        penalty = cfg["low_heat_penalty"]
        result["adjustment"] -= penalty
        facts.append(f"板块热度低({heat:.0f}) -{penalty}分")
    if breadth >= cfg["strong_breadth_threshold"]:
        bonus = cfg["breadth_bonus"]
        result["adjustment"] += bonus
        facts.append(f"板块共振强(扩散度{breadth:.0f}) +{bonus}分")
    elif breadth <= cfg["weak_breadth_threshold"]:
        penalty = cfg["breadth_penalty"]
        result["adjustment"] -= penalty
        facts.append(f"板块共振弱(扩散度{breadth:.0f}) -{penalty}分")
    result["facts"] = facts
    return result


# ══════════════════════════════════════════════════
# 4. 综合过滤（对外接口）
# ══════════════════════════════════════════════════

LEVEL_ORDER = ["强势", "偏强", "中性", "弱势"]


def _downgrade_level(level: str, steps: int) -> str:
    if level not in LEVEL_ORDER:
        return level
    idx = LEVEL_ORDER.index(level)
    new_idx = max(0, min(len(LEVEL_ORDER) - 1, idx + steps))
    return LEVEL_ORDER[new_idx]


def compute_filtered_signal(
    closes: list[float],
    highs: list[float],
    lows: list[float],
    volumes: list[float],
    last_close: float,
    last_volume: float,
    config: Optional[dict] = None,
    code: Optional[str] = None,
    name: Optional[str] = None,
    sector_map: Optional[dict] = None,
) -> dict:
    resonance_cfg = dict(MULTI_PERIOD_CONFIG)
    vol_cfg = dict(VOLATILITY_CONFIG)
    sector_cfg = dict(SECTOR_FILTER_CONFIG)
    div_cfg = dict(DIVERGENCE_CONFIG)
    mom_div_cfg = dict(MOMENTUM_DIVERGENCE_CONFIG)
    if config:
        if "resonance" in config: resonance_cfg.update(config["resonance"])
        if "volatility" in config: vol_cfg.update(config["volatility"])
        if "sector" in config: sector_cfg.update(config["sector"])
        if "divergence" in config: div_cfg.update(config["divergence"])
        if "momentum_divergence" in config: mom_div_cfg.update(config["momentum_divergence"])

    sig = compute_scenario_signal(closes, highs, lows, volumes, last_close, last_volume)
    scenario_score = sig["score"]
    scenario_level = sig["level"]
    scenario = sig["scenario"]

    resonance = _compute_resonance_score(closes, highs, lows, volumes, resonance_cfg)
    resonance_score = resonance["resonance_score"]
    resonance_consistency = resonance["consistency"]

    volatility = _compute_volatility_filter(closes, highs, lows, volumes, vol_cfg)
    vol_adj = volatility["vol_adjustment"]
    vol_level_adj = volatility["level_adjustment"]

    divergence = _compute_divergence(closes, highs, lows, div_cfg)
    div_adj = divergence.get("adjustment", 0.0)
    div_level_adj = divergence.get("level_adjustment", 0)

    mom_div = _compute_momentum_divergence(closes, mom_div_cfg)
    mom_div_adj = mom_div.get("adjustment", 0.0)
    if divergence.get("divergence_type") == "无":
        div_adj += mom_div_adj
        if mom_div_adj < 0:
            div_level_adj = max(div_level_adj, 1)

    sector = _compute_sector_resonance(code, name, sector_map, sector_cfg)
    sector_adj = sector.get("adjustment", 0.0)

    base_for_filter = scenario_score
    resonance_direction = resonance.get("direction", "中性")

    if resonance_consistency == "短期脉冲":
        blended = resonance_score * 0.6 + base_for_filter * 0.4
        facts_extra = ["短期脉冲:评分快速上升趋势"]
    elif resonance_consistency == "分歧偏空":
        blended = min(base_for_filter, resonance_score) - 5
        facts_extra = ["分歧偏空降级 -5分"]
    elif resonance_consistency == "分歧偏多":
        blended = min(base_for_filter, resonance_score)
        facts_extra = ["分歧偏多采用保守分"]
    elif resonance_consistency == "分歧":
        blended = min(base_for_filter, resonance_score) - 3
        facts_extra = ["分歧降级 -3分"]
    elif resonance_consistency == "一致":
        if resonance_direction == "看好":
            bonus = resonance_cfg.get("upgrade_bonus", 8)
            blended = base_for_filter + bonus
            facts_extra = [f"多周期一致看好 +{bonus}分"]
        elif resonance_direction == "看空":
            blended = min(base_for_filter, resonance_score) - 10
            facts_extra = ["多周期一致看空 -10分"]
        else:
            blended = (
                base_for_filter * resonance_cfg["current_weight"]
                + resonance_score * resonance_cfg["resonance_weight"]
            )
            facts_extra = []
    else:
        blended = (
            base_for_filter * resonance_cfg["current_weight"]
            + resonance_score * resonance_cfg["resonance_weight"]
        )
        facts_extra = []

    severe_gap = resonance_cfg.get("severe_downgrade_gap", 20)
    severe_penalty = resonance_cfg.get("severe_downgrade_penalty", 15)
    if resonance_score < base_for_filter - severe_gap:
        blended -= severe_penalty
        facts_extra.append(f"共振分远低于原始分({resonance_score:.0f}<<{base_for_filter:.0f}) -{severe_penalty}分")

    filtered_score = blended + vol_adj + div_adj + sector_adj

    # ── V15 扩展过滤器（相对强度/盈亏比/分时确认/跨市场锚点）──
    extra_adj = 0.0
    extra_facts = []
    extra_detail = {}
    try:
        from v15_filter_extras import compute_extra_filters
        extras = compute_extra_filters(
            closes, highs, lows, volumes, last_close,
            code=code, name=name, sector_map=sector_map,
            atr_norm=volatility.get("atr_norm", 1.5))
        extra_adj = extras.get("adjustment", 0.0)
        extra_facts = extras.get("facts", [])
        extra_detail = extras
    except Exception:
        pass

    filtered_score += extra_adj
    filtered_score = max(0, min(100, filtered_score))

    from v15_scenario_engine import LEVEL_THRESHOLDS
    level = "弱势"
    for threshold, lvl in LEVEL_THRESHOLDS:
        if filtered_score >= threshold:
            level = lvl
            break

    extra_downgrade = 0
    if resonance_consistency in ("分歧偏空", "分歧"):
        extra_downgrade = max(extra_downgrade, 1)
    if resonance.get("direction") == "看空":
        extra_downgrade = max(extra_downgrade, 1)
    if vol_level_adj < 0:
        extra_downgrade = max(extra_downgrade, abs(vol_level_adj))
    if div_level_adj != 0:
        extra_downgrade = max(extra_downgrade, div_level_adj)
    if extra_downgrade > 0:
        level = _downgrade_level(level, extra_downgrade)

    all_facts = []
    all_facts.append(f"══ 原始场景引擎评分: {scenario_score:.0f}分 / {scenario_level} ══")
    all_facts.append(f"── 多周期共振({resonance_consistency}) ──")
    all_facts.extend(resonance["facts"])
    all_facts.append(f"── 波动率过滤(调整{vol_adj:+.0f}分) ──")
    all_facts.extend(volatility["facts"])
    if divergence.get("divergence_type") != "无":
        all_facts.append(f"── MACD背离检测({divergence['divergence_type']},调整{div_adj:+.0f}分) ──")
        all_facts.extend(divergence["facts"])
    elif mom_div.get("divergence_type") != "无":
        all_facts.append(f"── 动量背离检测({mom_div['divergence_type']},调整{mom_div_adj:+.0f}分) ──")
        all_facts.extend(mom_div["facts"])
    if sector["available"]:
        all_facts.append(f"── 板块共振(调整{sector_adj:+.0f}分) ──")
        all_facts.extend(sector["facts"])
    if extra_facts:
        all_facts.append(f"── 扩展过滤器(调整{extra_adj:+.0f}分) ──")
        all_facts.extend(extra_facts)
    all_facts.append(f"── 过滤结果 ──")
    if facts_extra:
        all_facts.extend(facts_extra)
    all_facts.append(
        f"原始{scenario_score:.0f}→共振{resonance_score:.0f}→"
        f"调整{blended:.0f}→波动{vol_adj:+.0f}→"
        f"背离{div_adj:+.0f}→板块{sector_adj:+.0f}→"
        f"扩展{extra_adj:+.0f}→过滤{filtered_score:.0f}分"
    )
    if extra_downgrade > 0:
        all_facts.append(f"等级降级{extra_downgrade}级: {scenario_level} → {level}")

    return {
        "level": level,
        "score": round(filtered_score, 1),
        "scenario": scenario,
        "scenario_score": round(scenario_score, 1),
        "raw_v15_score": sig["raw_v15_score"],
        "rev_score": sig["rev_score"],
        "macd_score": sig["macd_score"],
        "vol_score": sig["vol_score"],
        "safe_score": sig["safe_score"],
        "resonance": resonance,
        "divergence": {"type": divergence.get("divergence_type", "无"), "adjustment": div_adj},
        "momentum_divergence": {"type": mom_div.get("divergence_type", "无"), "adjustment": mom_div_adj},
        "volatility": {"atr_norm": volatility["atr_norm"], "vol_adjustment": volatility["vol_adjustment"]},
        "sector": sector,
        "filter_adjustment": round(filtered_score - scenario_score, 1),
        "extras": {"adjustment": extra_adj, "detail": extra_detail},
        "facts": all_facts,
    }


def compute_filtered_signal_from_df(df) -> dict:
    col_close = '收盘' if '收盘' in df.columns else ('close' if 'close' in df.columns else None)
    col_high = '最高' if '最高' in df.columns else ('high' if 'high' in df.columns else None)
    col_low = '最低' if '最低' in df.columns else ('low' if 'low' in df.columns else None)
    col_vol = '成交量' if '成交量' in df.columns else ('vol' if 'vol' in df.columns else None)
    if any(c is None for c in [col_close, col_high, col_low, col_vol]):
        return {"level": "中性", "score": 50.0, "facts": ["缺少必要列"]}
    closes = [float(v) for v in df[col_close].values]
    highs = [float(v) for v in df[col_high].values]
    lows = [float(v) for v in df[col_low].values]
    volumes = [float(v) for v in df[col_vol].values]
    return compute_filtered_signal(closes, highs, lows, volumes, closes[-1], volumes[-1])
