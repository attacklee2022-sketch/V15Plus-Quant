# -*- coding: utf-8 -*-
"""
V15 Alpha — 核心评分引擎（阶段二）
==================================
在传统技术评分基础上重构，采用四因子加权评分：
  1. 反转评分 (35%) — 均值回归潜力
  2. MACD评分 (25%) — 趋势动量
  3. 量能评分 (25%) — 量价配合
  4. 安全结构评分 (15%) — 均线支撑/阻力

评分范围 0-100，按阈值分强势/偏强/中性/弱势四级。
"""
from __future__ import annotations
from typing import Optional


# ── 辅助函数 ──────────────────────────────────

def _ema(vals: list[float], n: int) -> list[float]:
    """EMA 计算，返回与输入等长的列表"""
    if not vals or n <= 0:
        return [0.0] * len(vals) if vals else []
    result = [0.0] * len(vals)
    k = 2.0 / (n + 1)
    # 前 n-1 个用 SMA 填充
    for i in range(min(n - 1, len(vals))):
        result[i] = sum(vals[:i+1]) / (i + 1)
    if len(vals) >= n:
        result[n-1] = sum(vals[:n]) / n
        for i in range(n, len(vals)):
            result[i] = (vals[i] - result[i-1]) * k + result[i-1]
    return result


def _ma(vals: list[float], n: int) -> Optional[float]:
    if len(vals) < n:
        return None
    return sum(vals[-n:]) / n


def _safe_div(a: float, b: float, default: float = 50.0) -> float:
    return a / b if b != 0 else default


# ══════════════════════════════════════════════════
# 1. 反转评分 (均值回归)
# ══════════════════════════════════════════════════

REV_CONFIG = {
    "rsi_period": 14,
    "rsi_oversold": 30,      # RSI < 30 → 超卖
    "rsi_overbought": 70,     # RSI > 70 → 超买
    "boll_band_std": 2,       # 布林带标准差倍数
    "boll_period": 20,
    "weight_deviation": 0.4,  # 布林偏离权重
    "weight_rsi": 0.3,        # RSI权重
    "weight_ma_gap": 0.3,     # 均线乖离权重
}


def _compute_rev_score(closes: list[float], config: dict) -> float:
    """反转评分 0-100。越高越好（超卖=高分，超买=低分）"""
    n = len(closes)
    if n < 30:
        return 50.0

    cfg = config
    score = 50.0

    # ── RSI ──
    rsi = 50.0
    if n >= cfg["rsi_period"] + 1:
        delta = [closes[i] - closes[i-1] for i in range(1, len(closes))]
        gains = [d for d in delta if d > 0]
        losses = [-d for d in delta if d < 0]
        avg_gain = sum(gains[-cfg["rsi_period"]:]) / cfg["rsi_period"] if len(gains) >= cfg["rsi_period"] else sum(gains) / max(len(gains), 1)
        avg_loss = sum(losses[-cfg["rsi_period"]:]) / cfg["rsi_period"] if len(losses) >= cfg["rsi_period"] else sum(losses) / max(len(losses), 1)
        if avg_loss == 0:
            rsi = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi = 100 - 100 / (1 + rs)

    if rsi < cfg["rsi_oversold"]:
        score += (cfg["rsi_oversold"] - rsi) * 1.5  # RSI越低分越高
    elif rsi > cfg["rsi_overbought"]:
        score -= (rsi - cfg["rsi_overbought"]) * 1.0

    # ── 布林带偏离 ──
    if n >= cfg["boll_period"]:
        boll_mid = sum(closes[-cfg["boll_period"]:]) / cfg["boll_period"]
        variance = sum((c - boll_mid) ** 2 for c in closes[-cfg["boll_period"]:]) / cfg["boll_period"]
        boll_std = variance ** 0.5
        last_close = closes[-1]
        if boll_std > 0:
            deviation = (boll_mid - last_close) / boll_std  # 正数=跌到下轨以下
            if deviation > 2:
                score += min(deviation * 5, 25)
            elif deviation < -2:
                score -= min(abs(deviation) * 3, 15)

    # ── 均线乖离 ──
    ma20 = _ma(closes, 20)
    if ma20 and ma20 > 0:
        gap = (closes[-1] - ma20) / ma20 * 100
        if gap < -10:
            score += min(abs(gap), 20)
        elif gap > 20:
            score -= min(gap, 15)

    return max(0, min(100, score))


# ══════════════════════════════════════════════════
# 2. MACD 评分（趋势动量）
# ══════════════════════════════════════════════════

MACD_CONFIG = {
    "fast": 12,
    "slow": 26,
    "signal": 9,
    "hist_boost": 0.5,
    "cross_bonus": 10,
}


def _compute_macd_score(closes: list[float], config: dict) -> float:
    """MACD评分 0-100。越高越好（金叉多头=高分）"""
    n = len(closes)
    if n < config["slow"] + config["signal"] + 5:
        return 50.0

    e12 = _ema(closes, config["fast"])
    e26 = _ema(closes, config["slow"])
    macd = [a - b for a, b in zip(e12, e26)]
    sig = _ema(macd, config["signal"])
    hist = [m - s for m, s in zip(macd, sig)]

    score = 50.0

    # MACD 值
    macd_val = macd[-1] if macd else 0
    if macd_val > 0:
        score += min(macd_val * 50, 20)
    else:
        score -= min(abs(macd_val) * 30, 10)

    # 柱值
    hist_val = hist[-1] if hist else 0
    if hist_val > 0:
        score += min(hist_val * 30, 10)
    else:
        score -= min(abs(hist_val) * 20, 8)

    # 金叉/死叉
    if len(hist) >= 2:
        if hist[-1] > 0 and hist[-2] <= 0:
            score += config["cross_bonus"]
        elif hist[-1] < 0 and hist[-2] >= 0:
            score -= config["cross_bonus"]

    return max(0, min(100, score))


# ══════════════════════════════════════════════════
# 3. 量能评分（量价配合）
# ══════════════════════════════════════════════════

VOL_CONFIG = {
    "vol_ma_period": 20,
    "price_ma_period": 10,
    "surging_threshold": 2.5,
    "shrinking_threshold": 0.5,
    "price_rise_bonus": 5,
}


def _compute_vol_score(closes: list[float], volumes: list[float], config: dict) -> float:
    """量能评分 0-100"""
    n = len(closes)
    if n < max(config["vol_ma_period"], config["price_ma_period"]) + 5:
        return 50.0

    score = 50.0

    # 量比
    vol_ma = _ma(volumes, config["vol_ma_period"]) or 1
    vol_ratio = volumes[-1] / vol_ma

    if vol_ratio > config["surging_threshold"]:
        # 放量
        price_change = (closes[-1] - closes[-2]) / closes[-2]
        if price_change > 0:
            score += config["price_rise_bonus"]  # 放量上涨=好
            score += min((vol_ratio - 2) * 5, 10)
        else:
            score -= 10  # 放量下跌=不好
    elif vol_ratio > 0.8:
        # 正常量
        score += 5
    elif vol_ratio < config["shrinking_threshold"]:
        score -= 5  # 极度缩量

    # 量价趋势一致性
    if n >= 5:
        price_up = sum(1 for i in range(-4, 0) if closes[i] > closes[i-1])
        vol_up = sum(1 for i in range(-4, 0) if volumes[i] > volumes[i-1])
        if price_up >= 3 and vol_up >= 3:
            score += 5  # 一致上涨
        elif price_up <= 1 and vol_up <= 1:
            score -= 5  # 一致下跌

    return max(0, min(100, score))


# ══════════════════════════════════════════════════
# 4. 安全结构评分（均线支撑/阻力）
# ══════════════════════════════════════════════════

SAFE_CONFIG = {
    "mas": [5, 10, 20, 60],
    "alignment_bonus": 8,
    "ma60_support_bonus": 10,
    "far_from_ma60_penalty": 8,
}


def _compute_safe_score(closes: list[float], config: dict) -> float:
    """安全结构评分 0-100。均线多头排列+站稳60日线=高分"""
    n = len(closes)
    if n < 60:
        return 50.0

    score = 50.0
    last_close = closes[-1]

    # 均线值
    ma5 = _ma(closes, 5)
    ma10 = _ma(closes, 10)
    ma20 = _ma(closes, 20)
    ma60 = _ma(closes, 60)
    mas = {"ma5": ma5, "ma10": ma10, "ma20": ma20, "ma60": ma60}

    if any(v is None for v in mas.values()):
        return 50.0

    # 多头排列
    if ma5 > ma10 > ma20 > ma60:
        score += config["alignment_bonus"]
    elif ma5 < ma10 < ma20 < ma60:
        score -= config["alignment_bonus"]

    # 60日线支撑
    if last_close > ma60:
        score += config["ma60_support_bonus"]
    else:
        score -= config["ma60_support_bonus"]

    # 远离60日线惩罚（涨太多）
    if ma60 > 0:
        pct_from_ma60 = (last_close - ma60) / ma60 * 100
        if pct_from_ma60 > 30:
            score -= config["far_from_ma60_penalty"]

    return max(0, min(100, score))


# ══════════════════════════════════════════════════
# 5. 综合评分
# ══════════════════════════════════════════════════

SCORE_WEIGHTS = {
    "rev": 0.35,
    "macd": 0.25,
    "vol": 0.25,
    "safe": 0.15,
}

LEVEL_THRESHOLDS = [
    (70, "强势"),
    (55, "偏强"),
    (45, "中性"),
    (0, "弱势"),
]


def compute_v15_signal(
    closes: list[float],
    highs: list[float],
    lows: list[float],
    volumes: list[float],
    last_close: float,
    last_volume: float,
    config: Optional[dict] = None,
) -> dict:
    """
    核心评分函数 — V15 四因子加权评分。

    返回:
        {
            "score": float,      # 综合评分 0-100
            "level": str,        # 强势/偏强/中性/弱势
            "rev_score": float,  # 反转评分
            "macd_score": float, # MACD评分
            "vol_score": float,  # 量能评分
            "safe_score": float, # 安全结构评分
            "weights": dict,     # 权重
        }
    """
    rev_cfg = dict(REV_CONFIG)
    macd_cfg = dict(MACD_CONFIG)
    vol_cfg = dict(VOL_CONFIG)
    safe_cfg = dict(SAFE_CONFIG)
    weights = dict(SCORE_WEIGHTS)

    if config:
        if "rev" in config: rev_cfg.update(config["rev"])
        if "macd" in config: macd_cfg.update(config["macd"])
        if "vol" in config: vol_cfg.update(config["vol"])
        if "safe" in config: safe_cfg.update(config["safe"])
        if "weights" in config: weights.update(config["weights"])

    rev = _compute_rev_score(closes, rev_cfg)
    macd = _compute_macd_score(closes, macd_cfg)
    vol = _compute_vol_score(closes, volumes, vol_cfg)
    safe = _compute_safe_score(closes, safe_cfg)

    score = rev * weights["rev"] + macd * weights["macd"] + vol * weights["vol"] + safe * weights["safe"]
    score = round(max(0, min(100, score)), 1)

    level = "弱势"
    for threshold, lvl in LEVEL_THRESHOLDS:
        if score >= threshold:
            level = lvl
            break

    return {
        "score": score,
        "level": level,
        "rev_score": round(rev, 1),
        "macd_score": round(macd, 1),
        "vol_score": round(vol, 1),
        "safe_score": round(safe, 1),
        "weights": weights,
    }


def compute_v15_signal_from_df(df) -> dict:
    """从通达信DataFrame计算V15评分"""
    col_close = '收盘' if '收盘' in df.columns else ('close' if 'close' in df.columns else None)
    col_high = '最高' if '最高' in df.columns else ('high' if 'high' in df.columns else None)
    col_low = '最低' if '最低' in df.columns else ('low' if 'low' in df.columns else None)
    col_vol = '成交量' if '成交量' in df.columns else ('vol' if 'vol' in df.columns else None)
    if any(c is None for c in [col_close, col_high, col_low, col_vol]):
        return {"level": "中性", "score": 50.0}
    closes = [float(v) for v in df[col_close].values]
    highs = [float(v) for v in df[col_high].values]
    lows = [float(v) for v in df[col_low].values]
    volumes = [float(v) for v in df[col_vol].values]
    return compute_v15_signal(closes, highs, lows, volumes, closes[-1], volumes[-1])
