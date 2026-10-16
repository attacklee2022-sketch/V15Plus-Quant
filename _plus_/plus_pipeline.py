# -*- coding: utf-8 -*-
"""
V15 Plus+ — 增强版管线
==================
包装 V15 原始管线，追加：
  - 资金流过滤 (fund_flow_filter)
  - 多周期共振 (multi_tf_resonance)
  - 新闻情绪量化 (sentiment_quant)
  - 信号追踪记录 (signal_tracker)
"""
from __future__ import annotations


def _recalc_level(score: float) -> str:
    from v15_scenario_engine import LEVEL_THRESHOLDS
    for threshold, lvl in LEVEL_THRESHOLDS:
        if score >= threshold:
            return lvl
    return "回避"


def _apply_adj(v15: dict, adj: float) -> None:
    """将调整值并入V15评分，触发等级重判。同步更新 filter_adjustment。"""
    if adj == 0:
        return

    fs = v15.get("filtered_signal", {})

    # 同步更新 filter_adjustment（仓位计算依赖此字段）
    old_filter_adj = v15.get("filter_adjustment", 0)
    v15["filter_adjustment"] = round(old_filter_adj + adj, 1)
    fs["filter_adjustment"] = v15["filter_adjustment"]

    extras = fs.get("extras", {})
    old_extras_adj = extras.get("adjustment", 0)
    extras["adjustment"] = round(old_extras_adj + adj, 1)

    old_score = v15.get("score", 0)
    new_score = max(0, min(100, old_score + adj))
    v15["score"] = round(new_score, 1)
    v15["level"] = _recalc_level(new_score)

    fs["score"] = round(fs.get("score", 0) + adj, 1)
    fs["level"] = _recalc_level(new_score)


def compute_plus_pipeline(df, code=None, name=None, sector_map=None,
                          news_items=None, track: bool = False):
    """V15 Plus+ 增强版管线。

    V15原版 + 资金流 + 多周期共振 + 新闻情绪。

    参数:
        df: 通达信日线 DataFrame
        code, name, sector_map: 同 V15
        news_items: 预拉取的新闻列表（可选，传给情绪量化模块）
        track: 是否记录到 SignalTracker（默认 False）

    返回:
        V15 原始 dict + {"plus": {"fund_flow":..., "multi_tf_resonance":..., "sentiment":...}}
    """
    # 1. 跑原始 V15 管线
    from scripts.stock_gui_V14 import compute_v15_pipeline
    v15 = compute_v15_pipeline(df, code=code, name=name, sector_map=sector_map)

    if not v15.get("available"):
        return v15

    extras = v15.get("filtered_signal", {}).get("extras", {})
    extra_detail = extras.get("detail", {})
    plus_facts = []

    # ── 2. 资金流 ──
    fund_result = {"adjustment": 0.0, "facts": []}
    if code:
        try:
            from _plus_.fund_flow_filter import _filter_fund_flow
            fund_result = _filter_fund_flow(code)
        except Exception:
            pass
    _apply_adj(v15, fund_result.get("adjustment", 0.0))
    extra_detail["fund_flow"] = fund_result
    plus_facts.extend(fund_result.get("facts", []))

    # ── 2.5. ATR T1 调节 ──
    # 板块资金涌入 → 高ATR打折, 板块资金流出 → 高ATR加重
    atr_result = {"adjustment": 0.0, "facts": []}
    if code:
        try:
            fs = v15.get("filtered_signal", {})
            vol = fs.get("volatility", {})
            atr_norm = vol.get("atr_norm", 0)
            vol_adj = vol.get("vol_adjustment", 0)

            if atr_norm > 3.0:
                from _plus_.sector_power import _get_t1_score, _em_industry_to_tdx
                from v15_sector_fetcher import match_sector, get_stock_industry

                ind = match_sector(code, name) or ""
                if not ind or ind == "通用":
                    ind = get_stock_industry(code) or ""
                tdx = _em_industry_to_tdx(ind)

                if tdx:
                    t1 = _get_t1_score(tdx)

                    if atr_norm > 4.0:
                        orig_atr = -18
                    else:
                        orig_atr = -8

                    if t1 >= 70:
                        discount = 0.3
                    elif t1 >= 50:
                        discount = 0.6
                    elif t1 >= 30:
                        discount = 1.0
                    else:
                        discount = 1.2

                    new_atr = orig_atr * discount
                    delta = new_atr - orig_atr

                    if abs(delta) >= 2:
                        atr_result["adjustment"] = round(delta, 1)
                        if delta > 0:
                            atr_result["facts"].append(
                                f"[ATR] T1={t1:.0f} 板块资金涌入 "
                                f"高波动{abs(orig_atr)}→{abs(new_atr):.0f}分 +{delta:.0f}")
                        else:
                            atr_result["facts"].append(
                                f"[ATR] T1={t1:.0f} 板块资金流出 "
                                f"高波动{abs(orig_atr)}→{abs(new_atr):.0f}分 {delta:.0f}")
        except Exception:
            pass

    _apply_adj(v15, atr_result.get("adjustment", 0.0))
    extra_detail["atr_t1"] = atr_result
    plus_facts.extend(atr_result.get("facts", []))

    # ── 3. 多周期共振 ──
    # 回避级别的股票日线已判弱，分钟共振最多+8分救不回来，跳过以提速
    resonance_result = {"adjustment": 0.0, "facts": []}
    if code and v15.get("level") != "回避":
        try:
            from _plus_.multi_tf_resonance import compute_resonance
            resonance_result = compute_resonance(code)
        except Exception:
            pass
    _apply_adj(v15, resonance_result.get("adjustment", 0.0))
    extra_detail["multi_tf_resonance"] = resonance_result
    plus_facts.extend(resonance_result.get("facts", []))

    # ── 4. 新闻情绪 ──
    sentiment_result = {"adjustment": 0.0, "facts": []}
    if code and name:
        try:
            from _plus_.sentiment_quant import compute_sentiment
            sentiment_result = compute_sentiment(code, name,
                                                  news_items=news_items,
                                                  sector_map=sector_map)
        except Exception:
            pass
    _apply_adj(v15, sentiment_result.get("adjustment", 0.0))
    extra_detail["sentiment_quant"] = sentiment_result
    plus_facts.extend(sentiment_result.get("facts", []))

    # ── 5. 仓位重新计算（Plus+ 调整后，需用最新分数重算）──
    # compute_v15_pipeline 里算的 position_advice 用的是旧 filter_adjustment，
    # _apply_adj 已经把分数和 filter_adjustment 更新了，这里基于最新值重算。
    try:
        from v15_position_manager import compute_position_advice_from_signal
        col_close = '收盘' if '收盘' in df.columns else 'close'
        last_close = float(df[col_close].iloc[-1]) if len(df) > 0 else 0
        v15["position_advice"] = compute_position_advice_from_signal(v15["filtered_signal"], last_close)
    except Exception:
        pass

    # ── 6. 附加字段 ──
    v15["plus"] = {
        "fund_flow": fund_result,
        "atr_t1": atr_result,
        "multi_tf_resonance": resonance_result,
        "sentiment_quant": sentiment_result,
        "plus_facts": plus_facts,
        "tracked": False,
    }

    return v15
