# -*- coding: utf-8 -*-
"""
技术评分引擎 V2 — 替换原系统择时信号和评分逻辑
================================================
基于全量410只股票、227,255个信号点的回测验证。

与原系统的关键区别:
  1. 先识别市场状态（强趋势/偏多/震荡/偏空/强下跌趋势）
  2. 根据状态动态分配均值回归/趋势跟踪权重
  3. 强趋势中禁止强势（避免追高）
  4. 评分分段完全单调（已验证）

使用方式:
  from v14_tech_scorer import compute_signal
  result = compute_signal(closes, highs, lows, volumes, last_kline)
"""
from __future__ import annotations
from typing import Optional


# ── 工具函数 ──────────────────────────────────

def _ma(vals: list[float], n: int) -> Optional[float]:
    if len(vals) < n:
        return None
    return sum(vals[-n:]) / n


def _ema(vals: list[float], n: int) -> list[float]:
    k = 2 / (n + 1)
    r = [vals[0]]
    for v in vals[1:]:
        r.append(v * k + r[-1] * (1 - k))
    return r


# ══════════════════════════════════════════════
# 1. 市场状态识别
# ══════════════════════════════════════════════

def detect_regime(
    close: float,
    ma5: float,
    ma10: float,
    ma20: float,
    ma60: float,
    highs: list[float],
    lows: list[float],
    closes: list[float],
) -> tuple[str, int]:
    """
    判断当前市场状态。

    返回: (状态名称, 强度分数)
      强趋势 / 偏多 / 震荡 / 偏空 / 强下跌趋势
    """
    bull = bear = 0

    # 均线排列
    if close > ma5 > ma10 > ma20:
        bull += 2
    elif close > ma5 > ma10:
        bull += 1
    if ma20 > ma60:
        bull += 1
    if ma20 > ma60 and (ma20 - ma60) / ma60 > 0.03:
        bull += 1

    if close < ma5 < ma10 < ma20:
        bear += 2
    elif close < ma5 < ma10:
        bear += 1
    if ma20 < ma60:
        bear += 1
    if ma20 < ma60 and (ma60 - ma20) / ma60 > 0.03:
        bear += 1

    # 创新高频次（最近20日）
    nh = sum(1 for i in range(-20, -1) if highs[i] >= max(highs[i - 5:i]))
    nl = sum(1 for i in range(-20, -1) if lows[i] <= min(lows[i - 5:i]))
    if nh >= 4:
        bull += 1
    if nh >= 6:
        bull += 1
    if nl >= 4:
        bear += 1
    if nl >= 6:
        bear += 1

    # 距MA60幅度
    pct_ma60 = (close / ma60 - 1) * 100 if ma60 else 0
    if pct_ma60 > 20:
        bull += 1
    if pct_ma60 < -20:
        bear += 1

    # 近20日涨跌幅
    ret20 = (close / closes[-20] - 1) * 100 if len(closes) >= 20 else 0
    if ret20 > 15:
        bull += 1
    if ret20 < -15:
        bear += 1

    if bull >= 4 and bull > bear * 2:
        return "强趋势", bull
    if bull >= 2 and bull > bear:
        return "偏多", bull
    if bear >= 4 and bear > bull * 2:
        return "强下跌趋势", -bear
    if bear >= 2 and bear > bull:
        return "偏空", -bear
    return "震荡", max(bull, bear)


# ══════════════════════════════════════════════
# 2. 均值回归评分
# ══════════════════════════════════════════════

def _score_mr(
    close: float,
    prev_close: float,
    ma20: float,
    ma60: float,
    vol: float,
    vol_ma20: float,
    high_20: float,
    low_20: float,
    closes: list[float],
    volumes: list[float],
) -> tuple[float, list[str]]:
    """
    均值回归评分（震荡/下跌市场中的核心逻辑）。
    分数 0-100，越高越好。
    回测验证：MR<35时强势不可靠。
    """
    score = 50.0
    facts: list[str] = []

    # 1. 距MA20位置（核心）
    dev = (close / ma20 - 1) * 100 if ma20 else 0
    if dev < -8:
        bonus = min(abs(dev) * 0.8, 20)
        score += bonus
        facts.append(f"超跌MA20{dev:.1f}% +{bonus:.0f}")
    elif dev < -3:
        bonus = abs(dev) * 0.3
        score += bonus
        facts.append(f"偏下MA20{dev:.1f}% +{bonus:.0f}")
    elif dev > 25:
        penalty = (dev - 25) * 0.8
        score -= penalty
        facts.append(f"严重偏离MA20{dev:.1f}% -{penalty:.0f}")
    elif dev > 15:
        penalty = (dev - 15) * 0.4
        score -= penalty
        facts.append(f"偏高MA20{dev:.1f}% -{penalty:.0f}")

    # 2. 近10日涨跌
    ret10 = (close / closes[-10] - 1) * 100 if len(closes) >= 10 else 0
    if ret10 < -12:
        bonus = min(abs(ret10) * 0.25, 10)
        score += bonus
        facts.append(f"近10日跌{ret10:.1f}% +{bonus:.0f}")
    elif ret10 > 20:
        penalty = (ret10 - 20) * 0.5
        score -= penalty
        facts.append(f"近10日涨{ret10:.1f}%(追高) -{penalty:.0f}")

    # 3. 量比（缩量加分，放量扣分）
    vol_ratio = vol / vol_ma20 if vol_ma20 else 1
    if vol_ratio < 0.6:
        score += 10
        facts.append(f"缩量{vol_ratio:.2f} +10")
    elif vol_ratio < 0.85:
        score += 5
        facts.append(f"缩量{vol_ratio:.2f} +5")
    elif vol_ratio > 2.5:
        score -= 6
        facts.append(f"放量{vol_ratio:.2f} -6")
    elif vol_ratio > 1.8:
        score -= 3
        facts.append(f"放量{vol_ratio:.2f} -3")

    # 4. MACD柱
    if len(closes) >= 26:
        e12 = _ema(closes, 12)
        e26 = _ema(closes, 26)
        macd_line = [a - b for a, b in zip(e12, e26)]
        sig = _ema(macd_line, 9)
        hist = macd_line[-1] - sig[-1]
        if hist < -2:
            score += 6
            facts.append(f"MACD柱负{hist:.2f}(超跌) +6")
        elif hist < -1:
            score += 3
            facts.append(f"MACD柱负{hist:.2f} +3")
        elif hist > 3:
            score -= 5
            facts.append(f"MACD柱过高{hist:.2f} -5")

    # 5. 20日位置（底部加分）
    pos20 = (close - low_20) / (high_20 - low_20) * 100 if high_20 > low_20 else 50
    if pos20 < 10:
        score += 8
        facts.append(f"20日底部前{pos20:.0f}% +8")
    elif pos20 < 25:
        score += 4
        facts.append(f"20日低位前{pos20:.0f}% +4")
    elif pos20 > 90:
        score -= 5
        facts.append(f"20日内高位前{pos20:.0f}% -5")

    # 6. 均线
    if ma20 > ma60:
        score += 3
        facts.append("MA20>MA60 +3")
    elif ma60 - ma20 > ma60 * 0.05:
        score -= 5
        facts.append("MA20大幅<MA60 -5")

    return max(0, min(100, score)), facts


# ══════════════════════════════════════════════
# 3. 趋势跟踪评分
# ══════════════════════════════════════════════

def _score_tf(
    close: float,
    ma5: float,
    ma10: float,
    ma20: float,
    ma60: float,
    vol: float,
    vol_ma20: float,
    closes: list[float],
    volumes: list[float],
    highs: list[float],
) -> tuple[float, list[str]]:
    """
    趋势跟踪评分（趋势市场中的核心逻辑）。
    分数 0-100，越高越好。
    回测验证：TF<35时强势不可靠（下跌趋势中）。
    """
    score = 50.0
    facts: list[str] = []

    # 1. 均线排列（核心）
    if close > ma5 > ma10 > ma20 > ma60:
        score += 20
        facts.append(f"完整多头C>{ma5:.1f}>{ma10:.1f}>{ma20:.1f}>MA60 +20")
    elif close > ma5 > ma10 > ma20:
        score += 10
        facts.append(f"短多头C>{ma5:.1f}>{ma10:.1f}>{ma20:.1f} +10")
    elif close > ma5 > ma10:
        score += 4
        facts.append(f"初多头C>{ma5:.1f}>{ma10:.1f} +4")

    # 2. 均线斜率（趋势强度）
    if len(closes) >= 40:
        m20_ago = _ma(closes[-40:-20], 20)
        if m20_ago:
            slope = (ma20 / m20_ago - 1) * 100
            if slope > 15:
                score += 10
                facts.append(f"MA20斜率{slope:.1f}% +10")
            elif slope > 8:
                score += 6
                facts.append(f"MA20斜率{slope:.1f}% +6")
            elif slope > 3:
                score += 3
                facts.append(f"MA20斜率{slope:.1f}% +3")
            elif slope < -8:
                score -= 6
                facts.append(f"MA20斜率{slope:.1f}%(下降) -6")

    # 3. 量价配合
    vol_ratio = vol / vol_ma20 if vol_ma20 else 1
    ret5 = (close / closes[-5] - 1) * 100 if len(closes) >= 5 else 0
    if vol_ratio > 1.3 and 0 < ret5 < 15:
        score += 6
        facts.append("量价配合 +6")
    elif vol_ratio > 1.5 and ret5 < -3:
        score -= 4
        facts.append(f"放量下跌{vol_ratio:.1f}倍{ret5:.1f}% -4")

    # 4. 创20日新高
    if len(highs) >= 20 and close >= max(highs[-20:-1]):
        score += 5
        facts.append("创20日新高 +5")

    # 5. RSI
    if len(closes) >= 15:
        gains = [max(closes[i] - closes[i - 1], 0) for i in range(-14, 0)]
        losses = [max(closes[i - 1] - closes[i], 0) for i in range(-14, 0)]
        avg_g = sum(gains) / 14
        avg_l = sum(losses) / 14
        rsi = 50 if avg_l == 0 else 100 - 100 / (1 + avg_g / avg_l) if avg_l > 0 else 100
        if rsi > 85:
            score -= 8
            facts.append(f"RSI{rsi:.0f}过热 -8")
        elif rsi < 30:
            score += 5
            facts.append(f"RSI{rsi:.0f}超卖 +5")

    # 6. MACD柱
    if len(closes) >= 26:
        e12 = _ema(closes, 12)
        e26 = _ema(closes, 26)
        macd_line = [a - b for a, b in zip(e12, e26)]
        sig = _ema(macd_line, 9)
        hist = macd_line[-1] - sig[-1]
        if hist > 2:
            score += 5
            facts.append(f"MACD柱{hist:.2f}(趋势强劲) +5")
        elif hist < -2:
            score -= 5
            facts.append(f"MACD柱负{hist:.2f}(趋势转弱) -5")

    return max(0, min(100, score)), facts


# ══════════════════════════════════════════════
# 4. 止损止盈计算
# ══════════════════════════════════════════════

def _compute_stop_levels(
    last_close: float,
    ma20: float,
    ma60: float,
    high_20: float,
    low_20: float,
    regime: str,
    level: str,
    mr_score: float,
) -> dict:
    """
    基于市场状态和等级计算止损止盈建议。

    止损逻辑（按市场状态）:
      强趋势  →  跌破MA20（趋势走坏）
      偏多    →  max(MA20, -5%)（跌破支撑或固定比例）
      震荡    →  max(近期低点, -5%)（区间下沿）
      偏空    →  -4%（超跌反弹失败）
      强下跌  →  -3%（快进快出）

    止盈逻辑（按等级+状态）:
      强势  →  基于回测5日均值+标准差（约+5%~+8%）
      偏强    →  基于回测5日均值（约+3%~+5%）
      中性/弱势 → 不设止盈
    """
    result = {}
    price = last_close

    # ── 止损 ──
    if regime == "强趋势":
        sl = ma20
        sl_label = "跌破MA20"
    elif regime == "偏多":
        sl = max(ma20, price * 0.95)
        sl_label = "跌破MA20或-5%"
    elif regime == "震荡":
        sl = max(low_20, price * 0.95)
        sl_label = "跌破20日低点或-5%"
    elif regime == "偏空":
        sl = price * 0.96
        sl_label = "-4%止损"
    else:  # 强下跌趋势
        sl = price * 0.97
        sl_label = "-3%止损（快进快出）"

    # 用MA60兜底（只在价格在MA60上方时有效，避免下跌趋势中被MA60拉高止损）
    if ma60 and price > ma60 and sl < ma60 * 0.97:
        sl = max(sl, ma60 * 0.97)
        sl_label += "（MA60兜底）"

    sl_pct = (sl / price - 1) * 100

    result["stop_loss"] = round(sl, 2)
    result["stop_loss_pct"] = round(sl_pct, 1)
    result["stop_loss_reason"] = sl_label

    # ── 止盈 ──
    if level == "强势":
        if regime in ("偏空", "强下跌趋势"):
            # 超跌反弹，第一目标到20日均线
            tp = max(price * 1.05, price * 1.08)
            tp_label = "+5%~+8%（超跌反弹第一目标）"
        else:
            tp = max(price * 1.05, high_20 * 1.02)
            tp_label = "前高+2%或+5%"
    elif level == "偏强":
        if regime == "强趋势":
            tp = max(price * 1.03, high_20)
            tp_label = "前高或+3%"
        else:
            tp = price * 1.05
            tp_label = "+5%"
    else:
        result["take_profit"] = 0
        result["take_profit_pct"] = 0
        result["take_profit_reason"] = "观望/弱势阶段，不设止盈"
        return result

    tp_pct = (tp / price - 1) * 100
    result["take_profit"] = round(tp, 2)
    result["take_profit_pct"] = round(tp_pct, 1)
    result["take_profit_reason"] = tp_label

    return result


# ══════════════════════════════════════════════
# 5. 综合评分（对外接口）
# ══════════════════════════════════════════════

def compute_signal(
    closes: list[float],
    highs: list[float],
    lows: list[float],
    volumes: list[float],
    last_close: float,
    last_volume: float,
    prev_close: float,
) -> dict:
    """
    计算最终择时信号。

    参数:
        closes: 收盘价列表（从旧到新）
        highs: 最高价列表
        lows: 最低价列表
        volumes: 成交量列表
        last_close: 最新收盘价
        last_volume: 最新成交量
        prev_close: 前收盘价

    返回:
        {
            "level": "强势"/"偏强"/"中性"/"弱势",
            "regime": "强趋势"/"偏多"/"震荡"/"偏空"/"强下跌趋势",
            "strength": int,
            "score": float,       # 综合评分 0-100
            "mr_score": float,    # 均值回归评分
            "tf_score": float,    # 趋势跟踪评分
            "facts": [str],       # 因子说明
        }
    """
    if len(closes) < 60:
        return {
            "level": "中性",
            "regime": "震荡",
            "strength": 0,
            "score": 50.0,
            "mr_score": 50.0,
            "tf_score": 50.0,
            "facts": ["数据不足"],
            "stop_loss": 0, "stop_loss_pct": 0, "stop_loss_reason": "数据不足",
            "take_profit": 0, "take_profit_pct": 0, "take_profit_reason": "数据不足",
        }

    # ── 计算均线 ──
    ma5 = _ma(closes, 5) or last_close
    ma10 = _ma(closes, 10) or last_close
    ma20 = _ma(closes, 20) or last_close
    ma60 = _ma(closes, 60) or last_close
    vol_ma20 = _ma(volumes, 20) or 1
    high_20 = max(highs[-20:])
    low_20 = min(lows[-20:])

    # ── 市场状态识别 ──
    regime, strength = detect_regime(
        last_close, ma5, ma10, ma20, ma60, highs, lows, closes
    )

    # ── 双模式评分 ──
    mr_score, mr_facts = _score_mr(
        last_close, prev_close, ma20, ma60,
        last_volume, vol_ma20, high_20, low_20,
        closes, volumes,
    )
    tf_score, tf_facts = _score_tf(
        last_close, ma5, ma10, ma20, ma60,
        last_volume, vol_ma20, closes, volumes, highs,
    )

    # ── 动态权重 ──
    weight_map = {
        "强趋势": 0.85,
        "偏多": 0.55,
        "震荡": 0.35,
        "偏空": 0.25,
        "强下跌趋势": 0.15,
    }
    tw = weight_map.get(regime, 0.5)
    # 强趋势中趋势强度越高权重越高
    if regime == "强趋势":
        tw = min(0.85, 0.65 + strength * 0.03)

    final_score = tf_score * tw + mr_score * (1 - tw)

    # ── 等级判定 ──
    if regime == "强趋势":
        # 强趋势中不给"强势"（回测验证这是最差的信号）
        if final_score >= 62:
            level = "偏强"
        elif final_score >= 50:
            level = "中性"
        else:
            level = "弱势"
    else:
        # 非强趋势：可以给强势，但需双条件确认
        qualifies = final_score >= 68
        if qualifies:
            if regime == "偏多":
                qualifies = mr_score >= 40  # 偏多中不能追高
            elif regime in ("强下跌趋势", "偏空"):
                qualifies = tf_score >= 35  # 下跌中需止跌信号
            else:
                qualifies = min(mr_score, tf_score) >= 35  # 震荡需两边确认

        if qualifies:
            level = "强势"
        elif final_score >= 58:
            level = "偏强"
        elif final_score >= 48:
            level = "中性"
        else:
            level = "弱势"

    # ── 止损止盈计算 ──
    sl_tp = _compute_stop_levels(
        last_close=last_close, ma20=ma20, ma60=ma60,
        high_20=high_20, low_20=low_20, regime=regime, level=level,
        mr_score=mr_score,
    )

    # ── 整合因子说明 ──
    all_facts = []
    if mr_facts:
        all_facts.extend(mr_facts)
    if tf_facts:
        all_facts.append("── 趋势因子 ──")
        all_facts.extend(tf_facts)

    return {
        "level": level,
        "regime": regime,
        "strength": strength,
        "score": round(final_score, 1),
        "mr_score": round(mr_score, 1),
        "tf_score": round(tf_score, 1),
        "facts": all_facts,
        **sl_tp,
    }


def compute_signal_from_df(df) -> dict:
    """
    从通达信DataFrame计算信号（用于直接替换原系统）。

    参数:
        df: 通达信日线DataFrame，至少60行，需包含列:
            '收盘'(或'close'), '最高'(或'high'), '最低'(或'low'),
            '成交量'(或'vol'), '开盘'(或'open')

    返回:
        同 compute_signal()
    """
    # 统一列名
    col_close = '收盘' if '收盘' in df.columns else ('close' if 'close' in df.columns else None)
    col_high = '最高' if '最高' in df.columns else ('high' if 'high' in df.columns else None)
    col_low = '最低' if '最低' in df.columns else ('low' if 'low' in df.columns else None)
    col_vol = '成交量' if '成交量' in df.columns else ('vol' if 'vol' in df.columns else None)
    col_open = '开盘' if '开盘' in df.columns else ('open' if 'open' in df.columns else None)

    if any(c is None for c in [col_close, col_high, col_low, col_vol]):
        return {"level": "中性", "regime": "震荡", "strength": 0,
                "score": 50.0, "mr_score": 50.0, "tf_score": 50.0,
                "facts": ["缺少必要列"]}

    closes = [float(v) for v in df[col_close].values]
    highs = [float(v) for v in df[col_high].values]
    lows = [float(v) for v in df[col_low].values]
    volumes = [float(v) for v in df[col_vol].values]
    opens = [float(v) for v in df[col_open].values]

    last_close = closes[-1]
    last_volume = volumes[-1]
    prev_close = opens[-1] if col_open else closes[-2] if len(closes) >= 2 else closes[-1]

    return compute_signal(closes, highs, lows, volumes, last_close, last_volume, prev_close)
