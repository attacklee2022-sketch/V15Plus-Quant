# -*- coding: utf-8 -*-
"""
K线形态检测引擎 V3 — 形态降级为辅助标签
========================================
回测结论（2026-06-06, 410只股票全量历史）:
  16种蜡烛图形态胜率全在 45-52%，不具独立择时能力。
  形态引擎现只输出形态名称，用于 K线图点位标注和 AI 报告上下文，
  base_score 固定为 50，不再参与任何评分决策。

检测范围:
  单根K线: 十字星、锤子线/上吊线、射击之星/倒锤子
  双根K线: 阳包阴、阴包阳、孕线、刺透形态、乌云盖顶
  趋势形态: 多头排列、站稳MA60
  量价形态: 放量长阳、缩量回踩
  背离形态: MACD底背离、量价底背离(标准)

标准参考: 日本蜡烛图技术 (史蒂夫·尼森)
"""
from __future__ import annotations
from typing import List, Optional, Tuple
from dataclasses import dataclass, field


@dataclass
class PatternResult:
    name: str
    date: str = ""
    strength: str = "中"
    details: str = ""


# ── 工具函数 ──────────────────────────────────

def _body(open_p: float, close: float) -> float:
    return abs(close - open_p)


def _upper(open_p: float, high: float, close: float) -> float:
    return high - max(open_p, close)


def _lower(open_p: float, low: float, close: float) -> float:
    return min(open_p, close) - low


def _trange(high: float, low: float) -> float:
    return high - low


def _is_yang(open_p: float, close: float) -> bool:
    return close > open_p


def _is_yin(open_p: float, close: float) -> bool:
    return close < open_p


def _ma(vals: List[float], n: int) -> Optional[float]:
    if len(vals) < n:
        return None
    return sum(vals[-n:]) / n


def _ema(vals: List[float], n: int) -> List[float]:
    k = 2 / (n + 1)
    r = [vals[0]]
    for v in vals[1:]:
        r.append(v * k + r[-1] * (1 - k))
    return r


# ══════════════════════════════════════════════
# 单根K线形态
# ══════════════════════════════════════════════

def detect_doji(open_p: float, high: float, low: float, close: float) -> Optional[str]:
    """十字星: 实体 ≤ 振幅的5%"""
    t = _trange(high, low)
    if t == 0:
        return None
    if _body(open_p, close) / t <= 0.05 and _body(open_p, close) <= 0.3:
        return "十字星"
    return None


def detect_hammer(open_p: float, high: float, low: float, close: float) -> Optional[str]:
    """
    锤子线/上吊线:
    1. 下影线 ≥ 实体 × 2
    2. 上影线 < 实体的30%
    """
    b = _body(open_p, close)
    u = _upper(open_p, high, close)
    lw = _lower(open_p, low, close)
    if b == 0:
        return None
    if lw >= b * 2 and u < b * 0.3:
        return "锤子线" if _is_yang(open_p, close) else "上吊线"
    return None


def detect_shooting_star(open_p: float, high: float, low: float, close: float) -> Optional[str]:
    """
    射击之星/倒锤子:
    1. 上影线 ≥ 实体 × 2
    2. 下影线 < 实体的30%
    """
    b = _body(open_p, close)
    u = _upper(open_p, high, close)
    lw = _lower(open_p, low, close)
    if b == 0:
        return None
    if u >= b * 2 and lw < b * 0.3:
        return "射击之星" if _is_yin(open_p, close) else "倒锤子"
    return None


# ══════════════════════════════════════════════
# 双K线形态
# ══════════════════════════════════════════════

def detect_bullish_engulfing(
    o1: float, c1: float, h1: float, l1: float,
    o2: float, c2: float, h2: float, l2: float,
) -> Optional[PatternResult]:
    """
    阳包阴 (标准定义 ★ 已修复 ★)

    原系统bug: 跳过了Day1阴线检查
    修复: 强制检查 Day1 = 阴线

    条件:
    1. Day1 = 阴线 (close < open)
    2. Day2 = 阳线 (close > open)
    3. Day2开盘 ≤ Day1收盘
    4. Day2收盘 ≥ Day1开盘
    5. Day2实体 > Day1实体 × 0.9
    """
    if not _is_yin(o1, c1):  # ← 原系统漏掉的条件
        return None
    if not _is_yang(o2, c2):
        return None
    if not (o2 <= c1 and c2 >= o1):
        return None
    b1 = _body(o1, c1)
    b2 = _body(o2, c2)
    if b2 < b1 * 0.9:
        return None

    coverage = b2 / b1 * 100
    strength = "强" if coverage >= 150 else ("中" if coverage >= 120 else "弱")
    return PatternResult(
        name="阳包阴",
        strength=strength,
        details=f"覆盖{coverage:.0f}% {strength}",
    )


def detect_bearish_engulfing(
    o1: float, c1: float, h1: float, l1: float,
    o2: float, c2: float, h2: float, l2: float,
) -> Optional[PatternResult]:
    """阴包阳 (标准定义)"""
    if not _is_yang(o1, c1):
        return None
    if not _is_yin(o2, c2):
        return None
    if not (o2 >= c1 and c2 <= o1):
        return None
    if _body(o2, c2) < _body(o1, c1) * 0.9:
        return None
    return PatternResult(name="阴包阳")


def detect_harami(
    o1: float, c1: float,
    o2: float, c2: float,
) -> Optional[PatternResult]:
    """
    孕线 (Harami):
    Day2实体完全在Day1实体内部
    """
    b1 = _body(o1, c1)
    b2 = _body(o2, c2)
    if b2 >= b1 * 0.6:
        return None
    upper1 = max(o1, c1)
    lower1 = min(o1, c1)
    upper2 = max(o2, c2)
    lower2 = min(o2, c2)
    if not (upper2 < upper1 and lower2 > lower1):
        return None
    t1 = "阴" if _is_yin(o1, c1) else "阳"
    t2 = "阴" if _is_yin(o2, c2) else "阳"
    return PatternResult(
        name=f"{t1}孕{t2}线",
        details=f"实体{b2 / b1 * 100:.0f}%",
    )


def detect_piercing(
    o1: float, c1: float, l1: float,
    o2: float, c2: float,
) -> Optional[PatternResult]:
    """
    刺透形态 (Piercing):
    下跌趋势中，Day1阴线，Day2阳线
    Day2开盘低于Day1最低，收盘在Day1实体50%以上
    """
    if not _is_yin(o1, c1) or not _is_yang(o2, c2):
        return None
    if not (o2 <= l1 and c2 > (o1 + c1) / 2):
        return None
    return PatternResult(
        name="刺透形态",
        details=f"穿透{_body(o2, c2) / _body(o1, c1) * 100:.0f}%",
    )


def detect_dark_cloud(
    o1: float, c1: float, h1: float,
    o2: float, c2: float,
) -> Optional[PatternResult]:
    """
    乌云盖顶 (Dark Cloud Cover):
    上涨趋势中，Day1阳线，Day2阴线
    Day2开盘高于Day1最高，收盘在Day1实体50%以下
    """
    if not _is_yang(o1, c1) or not _is_yin(o2, c2):
        return None
    if not (o2 >= h1 and c2 < (o1 + c1) / 2):
        return None
    return PatternResult(name="乌云盖顶")


# ══════════════════════════════════════════════
# 量价形态
# ══════════════════════════════════════════════

def detect_volume_surge(
    dates: List[str],
    closes: List[float],
    opens: List[float],
    volumes: List[float],
) -> List[PatternResult]:
    """
    放量长阳: 涨幅≥5% + 量≥20日均量1.5倍

    已删除原系统的"放量长阳失效"形态（历史触发0次，无意义）
    """
    results = []
    if len(closes) < 20:
        return results
    v20 = _ma(volumes, 20)
    if not v20 or v20 <= 0:
        return results
    recent = list(zip(dates[-20:], opens[-20:], closes[-20:], volumes[-20:]))
    for dt, op, cl, vl in recent:
        if _is_yang(op, cl):
            pct = (cl - op) / op * 100
            vr = vl / v20
            if pct >= 5 and vr >= 1.5:
                results.append(PatternResult(
                    name="放量长阳",
                    date=dt,
                    details=f"+{pct:.1f}% 量{vr:.1f}倍",
                ))
    return results


def detect_shrink_pullback(
    dates: List[str],
    opens: List[float],
    closes: List[float],
    volumes: List[float],
) -> List[PatternResult]:
    """
    缩量回踩（新增加形态）:
    放量上涨后，回调缩量至均量以下 → 洗盘确认信号
    """
    results = []
    if len(closes) < 20:
        return results
    v20 = _ma(volumes, 20)
    if not (v20 and v20 > 0):
        recent = list(zip(dates[-10:], opens[-10:], closes[-10:], volumes[-10:]))
        for i in range(1, len(recent)):
            dt, op, cl, vl = recent[i]
            if _is_yin(op, cl) and vl < v20 * 0.8:
                pct = (cl - op) / op * 100
                if -5 < pct < 0:
                    prev_dt, prev_op, prev_cl, prev_vl = recent[i - 1]
                    if _is_yang(prev_op, prev_cl) and prev_vl > v20:
                        results.append(PatternResult(
                            name="缩量回踩",
                            date=dt,
                            details=f"前量{prev_vl / v20:.1f}倍→现量{vl / v20:.1f}倍",
                        ))
    return results


# ══════════════════════════════════════════════
# 背离形态
# ══════════════════════════════════════════════

def detect_volume_price_divergence(
    dates: List[str],
    closes: List[float],
    volumes: List[float],
) -> Tuple[List[PatternResult], List[PatternResult]]:
    """
    量价关系检测（已修复 ★ 区分标准底背离 vs 缩量调整）

    原系统bug: 把缩量下跌标为"底背离"
    修复: 标准底背离=价跌量增，缩量调整=价跌量缩

    返回: (标准底背离列表, 缩量调整列表)
    """
    std = []
    shrink = []
    if len(closes) < 40:
        return std, shrink

    recent_c = closes[-40:]
    recent_v = volumes[-40:]
    recent_d = dates[-40:]
    mid = 20

    hi_k = max(recent_c[:mid])
    lo_k = min(recent_c[mid:])
    hi_idx = max(i for i, v in enumerate(recent_c[:mid]) if v == hi_k)
    lo_idx = mid + min(i for i, v in enumerate(recent_c[mid:]) if v == lo_k)

    if lo_idx <= hi_idx or lo_idx - hi_idx < 2:
        return std, shrink

    drop = (hi_k - lo_k) / hi_k * 100
    if drop < 3:
        return std, shrink

    vol_chg = (recent_v[lo_idx] - recent_v[hi_idx]) / max(recent_v[hi_idx], 1) * 100

    if vol_chg > 15:
        std.append(PatternResult(
            name="量价底背离(标准)",
            date=recent_d[lo_idx],
            details=f"价{hi_k:.2f}→{lo_k:.2f}(-{drop:.1f}%) 量增{vol_chg:.0f}%",
        ))
    elif vol_chg < -15:
        shrink.append(PatternResult(
            name="缩量调整",
            date=recent_d[lo_idx],
            details=f"价{hi_k:.2f}→{lo_k:.2f}(-{drop:.1f}%) 量缩{abs(vol_chg):.0f}%",
        ))
    return std, shrink


def detect_macd_divergence(
    dates: List[str],
    closes: List[float],
) -> Optional[PatternResult]:
    """
    MACD底背离:
    价格创新低但MACD柱(histogram)未创新低
    """
    if len(closes) < 40:
        return None
    recent_c = closes[-40:]
    recent_d = dates[-40:]

    e12 = _ema(recent_c, 12)
    e26 = _ema(recent_c, 26)
    macd_line = [a - b for a, b in zip(e12, e26)]
    sig = _ema(macd_line, 9)
    hist = [m - s for m, s in zip(macd_line, sig)]

    # 找价格最低点
    lo_idx = min(range(20, len(recent_c)), key=lambda i: recent_c[i])
    # 往前找前一个谷
    prev_val = float('inf')
    prev_idx = None
    for i in range(10, lo_idx):
        if recent_c[i] < recent_c[i - 1] and recent_c[i] < recent_c[i + 1]:
            if recent_c[i] < prev_val:
                prev_val = recent_c[i]
                prev_idx = i
    if prev_idx is None:
        return None
    if recent_c[lo_idx] >= prev_val * 0.98:
        return None  # 没有创新低
    if hist[lo_idx] > hist[prev_idx] * 1.05:
        return PatternResult(
            name="MACD底背离",
            date=recent_d[lo_idx],
            details=f"价{prev_val:.2f}→{recent_c[lo_idx]:.2f} MACD柱{hist[prev_idx]:.3f}→{hist[lo_idx]:.3f}",
        )
    return None


# ══════════════════════════════════════════════
# 统一检测入口
# ══════════════════════════════════════════════

def detect_all_patterns(
    dates: List[str],
    opens: List[float],
    highs: List[float],
    lows: List[float],
    closes: List[float],
    volumes: List[float],
) -> Tuple[List[PatternResult], float]:
    """
    全形态检测统一入口。

    参数:
        所有K线列表（从旧到新，至少60根）

    返回:
        (patterns, base_score)
        patterns: PatternResult 列表（用于K线标注和AI报告）
        base_score: 固定50（形态已降级为辅助标签，不参与评分决策）
    """
    patterns: List[PatternResult] = []
    n = len(closes)

    if n < 5:
        return patterns, 50.0

    # ── 单根K线形态 (最近10根) ──
    for i in range(max(0, n - 10), n):
        dt = dates[i]
        op, hi, lo, cl = opens[i], highs[i], lows[i], closes[i]

        d = detect_doji(op, hi, lo, cl)
        if d:
            patterns.append(PatternResult(name=d, date=dt))

        h = detect_hammer(op, hi, lo, cl)
        if h:
            patterns.append(PatternResult(name=h, date=dt))

        s = detect_shooting_star(op, hi, lo, cl)
        if s:
            patterns.append(PatternResult(name=s, date=dt))

    # ── 双K线形态 (最近30根) ──
    start = max(0, n - 30)
    for i in range(start, n - 1):
        o1, c1, h1, l1 = opens[i], closes[i], highs[i], lows[i]
        o2, c2, h2, l2 = opens[i + 1], closes[i + 1], highs[i + 1], lows[i + 1]
        dt2 = dates[i + 1]

        be = detect_bullish_engulfing(o1, c1, h1, l1, o2, c2, h2, l2)
        if be:
            be.date = dt2
            patterns.append(be)

        ae = detect_bearish_engulfing(o1, c1, h1, l1, o2, c2, h2, l2)
        if ae:
            ae.date = dt2
            patterns.append(ae)

        h = detect_harami(o1, c1, o2, c2)
        if h:
            h.date = dt2
            patterns.append(h)

        p = detect_piercing(o1, c1, l1, o2, c2)
        if p:
            p.date = dt2
            patterns.append(p)

        dc = detect_dark_cloud(o1, c1, h1, o2, c2)
        if dc:
            dc.date = dt2
            patterns.append(dc)

    # ── 量价形态 ──
    patterns.extend(detect_volume_surge(dates, closes, opens, volumes))
    patterns.extend(detect_shrink_pullback(dates, opens, closes, volumes))

    # ── 背离形态 ──
    vp_std, vp_shrink = detect_volume_price_divergence(dates, closes, volumes)
    patterns.extend(vp_std)
    patterns.extend(vp_shrink)

    md = detect_macd_divergence(dates, closes)
    if md:
        patterns.append(md)

    # ── 趋势形态 ──
    if n >= 60:
        c = closes
        m5 = _ma(c, 5)
        m10 = _ma(c, 10)
        m20 = _ma(c, 20)
        m60 = _ma(c, 60)
        last_c = c[-1]

        # 多头排列
        if last_c > m5 > m10 > m20 > m60:
            patterns.append(PatternResult(
                name="多头排列(完整)", date=dates[-1],
                details=f"C{last_c:.2f}>MA5{m5:.2f}>MA10{m10:.2f}>MA20{m20:.2f}>MA60{m60:.2f}",
            ))
        elif last_c > m5 > m10 > m20:
            patterns.append(PatternResult(
                name="多头排列(偏短)", date=dates[-1],
                details=f"C{last_c:.2f}>MA5{m5:.2f}>MA10{m10:.2f}>MA20{m20:.2f} MA60={m60:.2f}",
            ))

        # 站稳MA60
        if m60 and last_c > m60 * 1.03:
            dist = (last_c / m60 - 1) * 100
            patterns.append(PatternResult(
                name="站稳MA60", date=dates[-1],
                details=f"超出{dist:.1f}%",
            ))

    # ── 连阳/连阴 ──
    yang = yin = 0
    for i in range(n - 1, max(n - 11, -1), -1):
        if _is_yang(opens[i], closes[i]):
            yang += 1
            yin = 0
        else:
            yin += 1
            yang = 0
    if yang >= 3:
        tag = f"连阳{yang}日" if yang < 5 else "连阳5日+"
        patterns.append(PatternResult(name=tag, date=dates[-1],
                        details=f"连续{yang}日收阳"))
    if yin >= 3:
        tag = f"连阴{yin}日" if yin < 5 else "连阴5日+"
        patterns.append(PatternResult(name=tag, date=dates[-1],
                        details=f"连续{yin}日收阴"))

    # ── 计算base_score（形态已降级为辅助标签，不参与技术评分）──
    # 回测结论: 16种形态胜率全在 45-52%，不具独立择时能力
    # base_score 固定为 50 (中性)，形态名仅用于 K线图标注和 AI 报告上下文
    base_score = 50

    return patterns, base_score


def detect_all_from_df(df) -> Tuple[List[str], float]:
    """
    从通达信DataFrame检测全部形态（用于直接替换原系统）。

    参数:
        df: 通达信日线DataFrame

    返回:
        (hit_patterns, base_score)
        hit_patterns: 形态名称列表
        base_score: 形态评分 (0-100)
    """
    # 统一列名
    col_c = '收盘' if '收盘' in df.columns else ('close' if 'close' in df.columns else None)
    col_h = '最高' if '最高' in df.columns else ('high' if 'high' in df.columns else None)
    col_l = '最低' if '最低' in df.columns else ('low' if 'low' in df.columns else None)
    col_o = '开盘' if '开盘' in df.columns else ('open' if 'open' in df.columns else None)
    col_v = '成交量' if '成交量' in df.columns else ('vol' if 'vol' in df.columns else None)

    if any(c is None for c in [col_c, col_h, col_l, col_o, col_v]):
        return [], 50.0

    patterns, base_score = detect_all_patterns(
        dates=[str(i) for i in range(len(df))],
        opens=[float(v) for v in df[col_o].values],
        highs=[float(v) for v in df[col_h].values],
        lows=[float(v) for v in df[col_l].values],
        closes=[float(v) for v in df[col_c].values],
        volumes=[float(v) for v in df[col_v].values],
    )

    hit_patterns = [p.name for p in patterns]
    return hit_patterns, base_score
