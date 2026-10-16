# -*- coding: utf-8 -*-
"""
V15 Alpha — 仓位与风控管理模块（阶段四）
========================================
根据信号等级和最新行情，给出建仓建议和仓位比例。
"""
from __future__ import annotations


def compute_position_advice(
    score: float,
    level: str,
    atr_norm: float = 0,
    filter_adjustment: float = 0,
    is_dual_confirm: bool = False,
    last_close: float = 0,
) -> dict:
    """
    计算仓位建议。

    参数:
        score: 综合评分 (0-100)
        level: 信号等级
        atr_norm: ATR百分比（波动率）
        filter_adjustment: 过滤调整值
        is_dual_confirm: 是否双确认（过滤+场景都强）
        last_close: 最新收盘价（用于计算止损止盈绝对价格）

    返回:
        {
            "action": str,              # 操作建议（GUI主字段）
            "reason": str,              # 建议理由
            "position_pct": float,      # 建议仓位 0-100%
            "position_suggestion": str, # 仓位文字建议
            "stop_loss_pct": float,     # 止损百分比
            "stop_loss_price": float,   # 止损绝对价格
            "take_profit_pct": float,   # 止盈百分比（第一目标）
            "take_profit_1": float,     # 止盈一 绝对价格
            "take_profit_2": float,     # 止盈二 绝对价格（更高的第二目标）
            "take_profit_1_pct": float, # 止盈一 百分比
            "take_profit_2_pct": float, # 止盈二 百分比
            "time_stop_recommended": bool, # 是否建议时间止损
            "time_stop_reason": str,       # 时间止损原因
        }
    """
    # 基础仓位（按等级）
    base_pos = {
        "强势": 40,
        "偏强": 25,
        "中性": 10,
        "弱势": 0,
    }.get(level, 0)

    # 双确认加成
    if is_dual_confirm and level == "强势":
        base_pos += 20

    # 波动率调整
    vol_reason = ""
    if atr_norm > 5:
        base_pos *= 0.5
        vol_reason = f"高波动(ATR{atr_norm:.1f}%)减半仓位"
    elif atr_norm > 3:
        base_pos *= 0.7
        vol_reason = f"中高波动(ATR{atr_norm:.1f}%)缩减仓位"

    # 过滤调整（负=过滤扣分多，谨慎）
    filter_reason = ""
    if filter_adjustment < -10:
        base_pos *= 0.6
        filter_reason = f"过滤扣分较多({filter_adjustment:+.0f})，降低仓位"

    # 最终仓位
    position = max(0, min(100, round(base_pos)))

    # 操作建议
    if position >= 50:
        action = "积极建仓"
    elif position >= 25:
        action = "适度建仓"
    elif position >= 10:
        action = "轻仓试探"
    elif position >= 5:
        action = "观望"
    else:
        action = "不参与"

    # 止损/止盈（按波动率调整）
    base_stop_pct = 5.0
    base_profit_pct = 15.0
    if atr_norm > 0:
        base_stop_pct = max(3, atr_norm * 1.5)
        base_profit_pct = max(8, atr_norm * 4)

    stop_loss_pct = round(base_stop_pct, 1)
    take_profit_1_pct = round(base_profit_pct, 1)
    take_profit_2_pct = round(base_profit_pct * 1.5, 1)

    # 绝对价格
    stop_loss_price = round(last_close * (1 - stop_loss_pct / 100), 2) if last_close > 0 else 0
    take_profit_1 = round(last_close * (1 + take_profit_1_pct / 100), 2) if last_close > 0 else 0
    take_profit_2 = round(last_close * (1 + take_profit_2_pct / 100), 2) if last_close > 0 else 0

    # 理由拼接
    reason_parts = []
    if level == "强势":
        reason_parts.append("信号等级为强势" + ("且双确认" if is_dual_confirm else ""))
    elif level == "偏强":
        reason_parts.append("信号等级为偏强")
    elif level == "中性":
        reason_parts.append("信号等级为中性，仅适合观察")
    elif level == "弱势":
        reason_parts.append("信号等级为弱势，不建议参与")
    if vol_reason:
        reason_parts.append(vol_reason)
    if filter_reason:
        reason_parts.append(filter_reason)
    if action == "不参与":
        reason_parts.append("综合评分不足以支持入场")
    reason = "；".join(reason_parts) if reason_parts else "无特别理由"

    # 仓位文字
    if position == 0:
        position_suggestion = "不操作"
    elif position < 10:
        position_suggestion = f"极小仓位({position}%)"
    elif position < 25:
        position_suggestion = f"轻仓({position}%)"
    elif position < 50:
        position_suggestion = f"中等仓位({position}%)"
    else:
        position_suggestion = f"积极仓位({position}%)"

    # 时间止损：高波动或持仓时间较长时建议
    time_stop_recommended = position > 0 and atr_norm > 4
    if time_stop_recommended:
        time_stop_reason = f"高波动(ATR{atr_norm:.1f}%)，建议观察3-5个交易日，若不达预期及时离场"
    else:
        time_stop_reason = ""

    return {
        "action": action,
        "reason": reason,
        "position_pct": position,
        "position_suggestion": position_suggestion,
        "suggested_action": action,
        "stop_loss_pct": stop_loss_pct,
        "stop_loss_price": stop_loss_price,
        "take_profit_pct": take_profit_1_pct,
        "take_profit_1": take_profit_1,
        "take_profit_2": take_profit_2,
        "take_profit_1_pct": take_profit_1_pct,
        "take_profit_2_pct": take_profit_2_pct,
        "time_stop_recommended": time_stop_recommended,
        "time_stop_reason": time_stop_reason,
    }


def compute_position_advice_from_signal(
    signal: dict,
    last_close: float = 0,
) -> dict:
    """从compute_filtered_signal结果直接计算仓位

    参数:
        signal: compute_filtered_signal 返回的字典
        last_close: 最新收盘价，为0时跳过绝对价格计算
    """
    score = signal.get("score", 50)
    level = signal.get("level", "中性")
    atr_norm = signal.get("volatility", {}).get("atr_norm", 0)
    filter_adj = signal.get("filter_adjustment", 0)

    # 是否双确认
    scenario_score = signal.get("scenario_score", 0)
    is_dual = level == "强势" and scenario_score >= 55

    return compute_position_advice(score, level, atr_norm, filter_adj, is_dual, last_close)
