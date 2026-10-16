# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import date
import concurrent.futures
import pandas as pd
import numpy as np


def fetch_intraday_minutes(code: str) -> pd.DataFrame | None:
    """获取当日1分钟K线数据。非交易日/非交易时段返回None。"""
    from datetime import datetime as _dt, date as _date
    now = _dt.now()
    if now.weekday() >= 5:
        return None  # 周末不获取
    t = now.hour * 60 + now.minute
    if t < 9 * 60 + 28 or t > 15 * 60 + 10:
        return None  # 非交易时段

    try:
        from mootdx.quotes import Quotes
        if not hasattr(fetch_intraday_minutes, "_client"):
            fetch_intraday_minutes._client = Quotes.factory(market='std')
        client = fetch_intraday_minutes._client

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(client.bars, symbol=code, frequency=8, count=800)
            try:
                raw = future.result(timeout=15)
            except concurrent.futures.TimeoutError:
                return None
            except Exception:
                return None
        if raw is None or raw.empty:
            return None

        today = _date.today()
        sub = raw[raw.index.date == today].copy()
        if sub.empty:
            return None

        # 数据新鲜度检查：最新K线不能超过1个交易日
        latest_ts = sub.index.max()
        latest_date = latest_ts.date() if hasattr(latest_ts, 'date') else latest_ts
        if isinstance(latest_date, _date) and (today - latest_date).days > 0:
            return None  # 旧数据

        df = pd.DataFrame({
            "时间": sub.index,
            "开盘": sub["open"].values,
            "最高": sub["high"].values,
            "最低": sub["low"].values,
            "收盘": sub["close"].values,
            "成交量": sub["vol"].values,
            "成交额": sub["amount"].values,
        }, index=range(len(sub)))

        return df.sort_values("时间").reset_index(drop=True)

    except Exception:
        return None


def calc_intraday_strength(df: pd.DataFrame, prev_close: float | None = None) -> dict:
    """计算分时强度指标。

    prev_close: 昨日收盘价（用于计算较昨收涨跌幅）。不传则相对今日开盘价。

    返回字典包含各维度数值和强度评分。
    所有结论由规则驱动，无AI参与。
    """
    result = {"valid": False}

    if df is None or len(df) < 10:
        return result

    # ── 基础价格 ──
    first = df.iloc[0]
    last = df.iloc[-1]
    high = df["最高"].max()
    low = df["最低"].min()
    price = last["收盘"]
    open_p = first["开盘"]
    # 涨跌幅 → 优先相对昨收（与行情软件一致），无昨收则相对今日开盘
    if prev_close and prev_close > 0:
        change_pct = (price - prev_close) / prev_close * 100
    else:
        change_pct = (price - open_p) / open_p * 100
    amplitude = (high - low) / open_p * 100

    # 位置百分比（0%=日内最低, 100%=日内最高）
    range_span = high - low
    position_pct = (price - low) / range_span * 100 if range_span > 0 else 50

    # ── VWAP ──
    total_vol = df["成交量"].sum()
    if total_vol > 0:
        vwap = (df["成交额"].sum()) / total_vol
    else:
        vwap = price
    vwap_dev = (price - vwap) / vwap * 100 if vwap else 0

    # ── 量价配合 ──
    # 上涨分钟: 收盘 > 开盘 视为阳线（买盘主动）
    up_mask = df["收盘"] >= df["开盘"]
    down_mask = ~up_mask
    up_vol = df.loc[up_mask, "成交量"].sum()
    down_vol = df.loc[down_mask, "成交量"].sum()
    total_updown_vol = up_vol + down_vol
    if total_updown_vol > 0:
        up_ratio = up_vol / total_updown_vol  # 上涨分钟成交量占总成交量比例
    else:
        up_ratio = 0.5

    # ── 尾盘量能 ──
    if len(df) >= 30:
        recent = df.tail(30)
        earlier = df.iloc[:-30]
        recent_vol_avg = recent["成交量"].mean()
        earlier_vol_avg = earlier["成交量"].mean()
        vol_ratio_30min = recent_vol_avg / earlier_vol_avg if earlier_vol_avg > 0 else 1.0
    else:
        vol_ratio_30min = 1.0

    # 前30分钟成交量占比
    if len(df) >= 30:
        first_30_vol = df.iloc[:30]["成交量"].sum()
        half_hour_vol_ratio = first_30_vol / total_vol if total_vol > 0 else 0
    else:
        half_hour_vol_ratio = 0

    # ── 趋势方向 ──
    if len(df) >= 20:
        y = df["收盘"].values
        x = np.arange(len(y))
        slope = np.polyfit(x, y, 1)[0]
        if slope > 0.001:
            trend = "上升"
        elif slope < -0.001:
            trend = "下降"
        else:
            trend = "震荡"
    else:
        trend = "震荡"

    # ── 分时强度评分 (0-100) ──
    score = 50

    # ① 涨幅: ±12分
    if change_pct > 1.5:
        score += 12
    elif change_pct > 0.5:
        score += 6
    elif change_pct > 0:
        score += 2
    elif change_pct < -1.5:
        score -= 12
    elif change_pct < -0.5:
        score -= 6
    else:
        score -= 2

    # ② VWAP偏离: ±8分
    if vwap_dev > 0.3:
        score += 8
    elif vwap_dev > 0.1:
        score += 4
    elif vwap_dev < -0.3:
        score -= 8
    elif vwap_dev < -0.1:
        score -= 4

    # ③ 量价配合: ±8分
    if up_ratio > 0.65:
        score += 8
    elif up_ratio > 0.58:
        score += 4
    elif up_ratio < 0.35:
        score -= 8
    elif up_ratio < 0.42:
        score -= 4

    # ④ 价格位置: ±6分
    if position_pct >= 80:
        score += 6
    elif position_pct >= 60:
        score += 3
    elif position_pct <= 20:
        score -= 6
    elif position_pct <= 40:
        score -= 3

    # ⑤ 趋势方向: ±6分
    if trend == "上升":
        score += 6
    elif trend == "下降":
        score -= 6

    # ⑥ 尾盘量能: ±6分
    if vol_ratio_30min > 1.5 and change_pct > 0:
        score += 6
    elif vol_ratio_30min > 1.3 and change_pct < 0:
        score -= 6
    elif vol_ratio_30min < 0.5:
        score -= 4
    elif vol_ratio_30min < 0.7 and change_pct < 0:
        score -= 2

    score = max(0, min(100, score))

    # 强度标签
    if score >= 70:
        tag = "强势"
    elif score >= 55:
        tag = "偏强"
    elif score >= 45:
        tag = "中性"
    elif score >= 30:
        tag = "偏弱"
    else:
        tag = "弱势"

    return {
        "valid": True,
        # 原始数据
        "price": round(price, 2),
        "open": round(open_p, 2),
        "high": round(high, 2),
        "low": round(low, 2),
        "change_pct": round(change_pct, 2),
        "amplitude": round(amplitude, 2),
        "position_pct": round(position_pct, 1),
        "vwap": round(vwap, 2),
        "vwap_dev_pct": round(vwap_dev, 2),
        "up_ratio": round(up_ratio, 2),
        "vol_ratio_30min": round(vol_ratio_30min, 2),
        "half_hour_vol_ratio": round(half_hour_vol_ratio, 2),
        "trend": trend,
        # 综合
        "strength_score": score,
        "strength_tag": tag,
    }


def build_intraday_text(code: str, name: str, df_1min: pd.DataFrame | None = None) -> str:
    """生成分时强度分析文本（规则驱动，无AI参与）。"""
    if df_1min is None:
        df_1min = fetch_intraday_minutes(code)
    if df_1min is None or len(df_1min) < 10:
        return (
            f"【{code} {name}】分时数据不足\n"
            "可能原因：今日未交易、未开盘、或非交易时段"
        )

    s = calc_intraday_strength(df_1min)
    if not s.get("valid"):
        return f"【{code} {name}】分时数据不足"

    # ── 数据摘要 ──
    lines = [
        f"===== {code} {name} 分时强度 =====",
        f"数据区间：{df_1min.iloc[0]['时间']:%H:%M} ~ {df_1min.iloc[-1]['时间']:%H:%M} | "
        f"{len(df_1min)} 条1分钟线",
        "",
        f"当前价: {s['price']:.2f} | 开盘: {s['open']:.2f} | 涨幅: {s['change_pct']:+.2f}%",
        f"日内区间: 高 {s['high']:.2f} 低 {s['low']:.2f} | 振幅: {s['amplitude']:.2f}%",
        f"VWAP均价: {s['vwap']:.2f} | 当前偏离: {s['vwap_dev_pct']:+.2f}%",
        f"综合强度评分: {s['strength_score']} | 状态: {s['strength_tag']}",
    ]

    # ── 维度1: VWAP偏离 → 多空结论 ──
    if s["vwap_dev_pct"] > 0.3:
        cond = f"价格 {s['price']:.2f} > VWAP {s['vwap']:.2f}，偏离 {s['vwap_dev_pct']:+.2f}% > +0.3%"
        conclusion = "价格运行在均价线上方，买盘主动推升，日内多头占优"
    elif s["vwap_dev_pct"] > 0.1:
        cond = f"价格 {s['price']:.2f} > VWAP {s['vwap']:.2f}，偏离 {s['vwap_dev_pct']:+.2f}% 在+0.1%~+0.3%之间"
        conclusion = "价格在均价线上方运行，买盘略偏主动"
    elif s["vwap_dev_pct"] < -0.3:
        cond = f"价格 {s['price']:.2f} < VWAP {s['vwap']:.2f}，偏离 {s['vwap_dev_pct']:+.2f}% < -0.3%"
        conclusion = "价格运行在均价线下方，卖盘主动打压，日内空头占优"
    elif s["vwap_dev_pct"] < -0.1:
        cond = f"价格 {s['price']:.2f} < VWAP {s['vwap']:.2f}，偏离 {s['vwap_dev_pct']:+.2f}% 在-0.3%~-0.1%之间"
        conclusion = "价格在均价线下方运行，卖盘偏主动"
    else:
        cond = f"偏离 {s['vwap_dev_pct']:+.2f}%，在±0.1%以内"
        conclusion = "价格围绕均价线窄幅波动，多空力量均衡"

    lines.append("")
    lines.append("--- [VWAP偏离] ---")
    lines.append(f"  触发条件: {cond}")
    lines.append(f"  结论: {conclusion}")

    # ── 维度2: 量价配合 → 资金意愿结论 ──
    up_pct = s["up_ratio"] * 100
    down_pct = (1 - s["up_ratio"]) * 100
    if s["up_ratio"] > 0.65:
        cond = f"上涨分钟成交量占总成交量 {up_pct:.0f}%（下跌分钟 {down_pct:.0f}%），比值 > 65%"
        conclusion = "买盘成交量占比明显高于卖盘，资金主动买入意愿强，量价结构健康"
    elif s["up_ratio"] > 0.58:
        cond = f"上涨分钟成交量占总成交量 {up_pct:.0f}%（下跌分钟 {down_pct:.0f}%），比值在58%~65%之间"
        conclusion = "买盘成交量略高于卖盘，资金买入意愿偏正面"
    elif s["up_ratio"] < 0.35:
        cond = f"上涨分钟成交量占总成交量 {up_pct:.0f}%（下跌分钟 {down_pct:.0f}%），比值 < 35%"
        conclusion = "卖盘成交量占比明显高于买盘，资金主动卖出意愿强，抛压较大"
    elif s["up_ratio"] < 0.42:
        cond = f"上涨分钟成交量占总成交量 {up_pct:.0f}%（下跌分钟 {down_pct:.0f}%），比值在35%~42%之间"
        conclusion = "卖盘成交量略高于买盘，资金卖出意愿偏重"
    else:
        cond = f"上涨分钟成交量占总成交量 {up_pct:.0f}%，比值在42%~58%之间"
        conclusion = "买卖成交量基本均衡，无明显资金倾向"

    lines.append("")
    lines.append("--- [量价配合] ---")
    lines.append(f"  触发条件: {cond}")
    lines.append(f"  结论: {conclusion}")

    # ── 维度3: 价格位置 → 多空控盘结论 ──
    if s["position_pct"] >= 80:
        cond = f"当前价在日内区间 {s['position_pct']:.0f}% 分位（高），接近日内最高 {s['high']:.2f}"
        conclusion = "价格持续运行在日内高位区域，多方控盘，空方无力打压至低位"
    elif s["position_pct"] >= 60:
        cond = f"当前价在日内区间 {s['position_pct']:.0f}% 分位（偏上）"
        conclusion = "价格处于日内偏上位置，多方略占优势"
    elif s["position_pct"] <= 20:
        cond = f"当前价在日内区间 {s['position_pct']:.0f}% 分位（低），接近日内最低 {s['low']:.2f}"
        conclusion = "价格持续在日内低位徘徊，空方主导，多方无力拉回"
    elif s["position_pct"] <= 40:
        cond = f"当前价在日内区间 {s['position_pct']:.0f}% 分位（偏下）"
        conclusion = "价格处于日内偏下位置，空方略占优势"
    else:
        cond = f"当前价在日内区间 {s['position_pct']:.0f}% 分位（中间）"
        conclusion = "价格在日内中间区域，无明显单边控盘"

    lines.append("")
    lines.append("--- [价格位置] ---")
    lines.append(f"  触发条件: {cond}")
    lines.append(f"  结论: {conclusion}")

    # ── 维度4: 趋势方向 → 走势持续性结论 ──
    if s["trend"] == "上升":
        cond = f"1分钟线线性回归斜率 > 0.001，{len(df_1min)} 条K线整体趋势向上"
        conclusion = "分时走势持续上行，高点不断抬高，上升趋势明确"
    elif s["trend"] == "下降":
        cond = f"1分钟线线性回归斜率 < -0.001，{len(df_1min)} 条K线整体趋势向下"
        conclusion = "分时走势持续下行，低点不断降低，下降趋势明确"
    else:
        cond = f"1分钟线线性回归斜率在 ±0.001 之间"
        conclusion = "分时走势无明显方向，横盘整理格局"

    lines.append("")
    lines.append("--- [趋势方向] ---")
    lines.append(f"  触发条件: {cond}")
    lines.append(f"  结论: {conclusion}")

    # ── 维度5: 尾盘量能 → 尾盘资金行为结论 ──
    if s["vol_ratio_30min"] > 1.5 and s["change_pct"] > 0:
        cond = f"最后30分钟量比 {s['vol_ratio_30min']:.2f} > 1.5（全天均值1.0），且涨幅 {s['change_pct']:+.2f}% 为正"
        conclusion = "尾盘放量上涨，短线资金尾盘主动介入，可能有资金博弈次日溢价"
    elif s["vol_ratio_30min"] > 1.3 and s["change_pct"] < 0:
        cond = f"最后30分钟量比 {s['vol_ratio_30min']:.2f} > 1.3，但涨幅 {s['change_pct']:+.2f}% 为负"
        conclusion = "尾盘放量下跌，抛压集中在尾盘释放，次日可能惯性低开"
    elif s["vol_ratio_30min"] < 0.5:
        cond = f"最后30分钟量比 {s['vol_ratio_30min']:.2f} < 0.5（远低于全天均值）"
        conclusion = "尾盘交投极度清淡，资金参与意愿低，可能提前收盘或等待消息"
    elif s["vol_ratio_30min"] < 0.7 and s["change_pct"] < 0:
        cond = f"最后30分钟量比 {s['vol_ratio_30min']:.2f} < 0.7，且涨幅 {s['change_pct']:+.2f}% 为负"
        conclusion = "尾盘缩量阴跌，买盘承接意愿不足，但卖盘也衰减，多空均观望"
    elif s["vol_ratio_30min"] > 1.3:
        cond = f"最后30分钟量比 {s['vol_ratio_30min']:.2f} > 1.3"
        conclusion = "尾盘量能放大，关注方向选择"
    else:
        cond = f"最后30分钟量比 {s['vol_ratio_30min']:.2f}，在0.7~1.5范围内"
        conclusion = "尾盘量能相对平稳，无明显异常波动"

    lines.append("")
    lines.append("--- [尾盘量能] ---")
    lines.append(f"  触发条件: {cond}")
    lines.append(f"  结论: {conclusion}")

    # ── 综合结论 ──
    lines.append("")
    lines.append("=" * 50)
    lines.append("综合结论")

    # 各维度投票
    bullish = 0
    bearish = 0

    if s["vwap_dev_pct"] > 0.1:
        bullish += 1
    elif s["vwap_dev_pct"] < -0.1:
        bearish += 1

    if s["up_ratio"] > 0.55:
        bullish += 1
    elif s["up_ratio"] < 0.45:
        bearish += 1

    if s["position_pct"] > 55:
        bullish += 1
    elif s["position_pct"] < 45:
        bearish += 1

    if s["trend"] == "上升":
        bullish += 1
    elif s["trend"] == "下降":
        bearish += 1

    conclusion_parts = []
    if bullish >= 3:
        conclusion_parts.append(f"多维度指标指向多头（{bullish}/4），分时综合偏强")
    elif bearish >= 3:
        conclusion_parts.append(f"多维度指标指向空头（{bearish}/4），分时综合偏弱")
    elif bullish >= 1 and bearish == 0:
        conclusion_parts.append(f"多数维度偏多（{bullish}/4），分时状态温和偏强")
    elif bearish >= 1 and bullish == 0:
        conclusion_parts.append(f"多数维度偏空（{bearish}/4），分时状态温和偏弱")
    else:
        conclusion_parts.append(f"多空指标不明确（多头{bullish} 空头{bearish}），分时状态震荡待选择方向")

    lines.append(f"  {conclusion_parts[0]}")

    # ── 判断标准参考 ──
    lines.extend([
        "",
        "--- 各维度判断标准 ---",
        "[VWAP偏离] >+0.3%=买盘主动推升 | >+0.1%=略偏多 | ±0.1%以内=均衡 | <-0.1%=略偏空 | <-0.3%=卖盘主动打压",
        "[量价配合] >65%上涨量=买盘意愿强 | 58~65%=略偏多 | 42~58%=均衡 | 35~42%=略偏空 | <35%=抛压大",
        "[价格位置] >80%分位=多方控盘 | 60~80%=多方略优 | 40~60%=均衡 | 20~40%=空方略优 | <20%=空方主导",
        "[趋势方向] 斜率>0.001=上升趋势 | ±0.001=震荡 | <-0.001=下降趋势",
        "[尾盘量能] >1.5倍+涨=尾盘抢筹 | >1.3倍+跌=尾盘抛压 | <0.5=交投清淡 | <0.7+跌=缩量阴跌",
        "",
        "【分时分析边界声明】",
        "1. 以上所有判断均基于当日1分钟K线的数值计算，由条件→结论规则引擎生成",
        "2. 每条结论都附带了触发条件，可与原始数据核对验证",
        '3. "买盘主动""卖盘主动"指成交量在上涨/下跌分钟中的分布比例，不等于主力资金',
        '4. 本系统不区分"主力""庄家""游资"等无法从分时数据验证的主体意图',
        "5. 综合结论为各维度投票结果，仅供参考，不构成买卖建议",
        "6. 尾盘量能异常可能由特定事件驱动，不必然预示次日走势",
    ])

    return "\n".join(lines)
