# -*- coding: utf-8 -*-
"""
V15 Plus+ — 组合层优化模块
=========================
在评分管道之上做组合级决策：
  - 仓位数量上限（最多N只）
  - 凯利比例分配（按信号强度/历史命中率）
  - 行业集中度控制
  - 持仓联动（浮亏→收紧新仓）
  - 止损/止盈监控

不改评分管道，独立于 _internal/。
"""
from __future__ import annotations
import os
import json


def _load_portfolio() -> dict:
    """加载 portfolio.json"""
    fp = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "portfolio.json")
    try:
        if os.path.exists(fp):
            with open(fp, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {"positions": [], "history": [], "stats": {}}


def _get_tracker_hit_rates() -> dict:
    """从 SignalTracker 获取各等级的近期命中率。"""
    try:
        from _plus_.signal_tracker import get_tracker
        t = get_tracker()
        stats = t.get_stats_by_level(30)
        return {lv: d.get("hit_rate", 50) for lv, d in stats.items()}
    except Exception:
        return {}


# ══════════════════════════════════════════════════════
# 核心入口
# ══════════════════════════════════════════════════════

def optimize_portfolio(
    scan_results: list[dict],
    total_capital: float = 100_000,
    max_positions: int = 6,
    sector_concentration_limit: int = 3,
    existing_positions: list[dict] | None = None,
) -> dict:
    """
    输入全量扫描结果，输出组合层建议。

    参数:
        scan_results: 每只股票的扫描结果 [{code, name, level, score, scenario, sector, ...}]
        total_capital: 总资金（元）
        max_positions: 最大持仓数（含现有）
        sector_concentration_limit: 同板块最大持仓数
        existing_positions: 现有持仓列表（可选，不传则自动加载）

    返回:
        {
            "buy_candidates": [{code, name, level, score, proposed_shares, proposed_pct, reason}],
            "hold_positions": [{code, name, status, alert}],
            "close_alerts": [{code, name, reason}],    # 触发止损/止盈
            "total_risk_exposure": float,                # 总风险敞口(%)
            "summary": str,                              # 一句话总结
        }
    """
    # 现有持仓
    positions = existing_positions or _load_portfolio().get("positions", [])
    holding_codes = {p["code"] for p in positions if p.get("status") == "持有"}
    holding_sectors = _get_holding_sectors(positions)

    # 信号追踪命中率
    hit_rates = _get_tracker_hit_rates()

    # ── 1. 止损/止盈检查 ──
    close_alerts = _check_stop_triggers(positions)

    # ── 2. 候选池筛选 ──
    candidates = []
    for r in scan_results:
        if not r.get("ok"):
            continue
        code = r.get("code", "")
        if code in holding_codes:
            continue
        level = r.get("level", "中性")
        if level == "弱势":
            continue
        candidates.append(r)

    # ── 3. 候选排序：综合得分 × 命中率加权 ──
    def _rank_key(c):
        score = c.get("score", 50)
        level = c.get("level", "中性")
        # 命中率加权：如果该等级历史命中率高，加分
        hr = hit_rates.get(level, 50)
        hr_bonus = (hr - 50) * 0.3  # ±15
        # 双确认加分
        scenario = c.get("scenario", "")
        dual_bonus = 5 if level == "强势" and scenario else 0
        return score + hr_bonus + dual_bonus

    candidates.sort(key=_rank_key, reverse=True)

    # ── 4. 行业集中度控制 ──
    available_slots = max(0, max_positions - len(holding_codes))
    buy_list = []
    sector_counts = dict(holding_sectors)

    for c in candidates:
        if len(buy_list) >= available_slots:
            break
        sector = c.get("sector", {}).get("sector", "未知") if isinstance(c.get("sector"), dict) else "未知"
        if sector_counts.get(sector, 0) >= sector_concentration_limit:
            continue  # 同板块已满
        buy_list.append(c)
        sector_counts[sector] = sector_counts.get(sector, 0) + 1

    # ── 5. 仓位分配 ──
    # 凯利简化版：position = 基础仓位 × (signal_score/100) × risk_budget / slots
    risk_budget = _calc_risk_budget(positions)  # 现有浮亏越大，预算越紧
    buy_candidates = []
    if buy_list:
        total_score = sum(c.get("score", 50) for c in buy_list)
        for c in buy_list:
            score = c.get("score", 50)
            level = c.get("level", "中性")
            # 基础仓位（和 v15_position_manager 一致）
            base_pct = {"强势": 40, "偏强": 25, "中性": 10}.get(level, 5)
            # 凯利调整：好信号多配
            kelly_adj = score / 100
            # 风险预算缩放
            pct = base_pct * kelly_adj * risk_budget
            # 等权天花板
            pct = min(pct, 100 / max_positions)
            pct = round(max(2, pct))  # 最少2%

            price = c.get("prev_close", 10) or 10
            amount = total_capital * pct / 100
            lots = int(amount / price / 100)  # A股整手
            shares = lots * 100

            buy_candidates.append({
                "code": c["code"],
                "name": c["name"],
                "level": c["level"],
                "score": c["score"],
                "proposed_pct": pct,
                "proposed_shares": shares,
                "proposed_amount": round(amount),
                "reason": f"{c['level']}({c['score']:.0f}分) → {pct}%仓位 (约¥{amount:.0f})",
            })

    # ── 6. 持仓诊断 ──
    hold_positions = []
    for p in positions:
        if p.get("status") != "持有":
            continue
        pnl = p.get("current_pnl_pct", 0)
        alert = ""
        if pnl < -7:
            alert = f"⚠️ 浮亏{pnl:.1f}%，接近止损线"
        elif pnl < -5:
            alert = f"⚡ 浮亏{pnl:.1f}%，关注"
        elif pnl > 10:
            alert = f"💰 浮盈{pnl:.1f}%，可考虑分批止盈"
        hold_positions.append({
            "code": p["code"],
            "name": p["name"],
            "pnl_pct": round(pnl, 1),
            "alert": alert,
        })

    # ── 7. 汇总 ──
    total_exposure = sum(p.get("proposed_pct", 0) for p in buy_candidates) + sum(
        abs(p.get("current_pnl_pct", 0)) for p in positions if p.get("status") == "持有"
    ) * 0.3

    summary_parts = []
    if buy_candidates:
        summary_parts.append(f"建议新增{len(buy_candidates)}只")
    if close_alerts:
        summary_parts.append(f"触发{len(close_alerts)}条止损/止盈")
    if hold_positions:
        alerts = [h for h in hold_positions if h["alert"]]
        if alerts:
            summary_parts.append(f"{len(alerts)}只持仓需关注")
    if not summary_parts:
        summary_parts.append("当前无需操作")

    return {
        "buy_candidates": buy_candidates,
        "hold_positions": hold_positions,
        "close_alerts": close_alerts,
        "total_risk_exposure": round(total_exposure, 1),
        "summary": "；".join(summary_parts),
    }


# ══════════════════════════════════════════════════════
# 辅助
# ══════════════════════════════════════════════════════

def _get_holding_sectors(positions: list[dict]) -> dict[str, int]:
    """统计现有持仓的板块分布。"""
    counts: dict[str, int] = {}
    for p in positions:
        if p.get("status") != "持有":
            continue
        # 从 notes 或 sector 字段提取板块
        sector = p.get("sector", "")
        if not sector:
            # 尝试从 name 推断
            name = p.get("name", "")
            if "能源" in name or "电力" in name:
                sector = "电力"
            elif "银行" in name:
                sector = "证券金融"
            else:
                sector = "通用"
        counts[sector] = counts.get(sector, 0) + 1
    return counts


def _calc_risk_budget(existing_positions: list[dict]) -> float:
    """根据现有持仓浮亏程度调整风险预算 [0.3, 1.0]。

    浮亏越多，新仓位越保守。
    """
    if not existing_positions:
        return 1.0

    total_pnl = 0.0
    count = 0
    for p in existing_positions:
        if p.get("status") == "持有":
            total_pnl += p.get("current_pnl_pct", 0)
            count += 1

    if count == 0:
        return 1.0

    avg_pnl = total_pnl / count
    if avg_pnl < -8:
        return 0.3   # 大幅浮亏，几乎不开新仓
    elif avg_pnl < -5:
        return 0.5
    elif avg_pnl < -2:
        return 0.7
    elif avg_pnl > 5:
        return 1.0   # 整体浮盈，可以放开
    return 0.85


def _check_stop_triggers(positions: list[dict]) -> list[dict]:
    """检查止损/止盈触发。"""
    alerts = []
    for p in positions:
        if p.get("status") != "持有":
            continue
        pnl = p.get("current_pnl_pct", 0)
        stop = p.get("stop_loss_pct", 7)
        profit = p.get("take_profit_pct", 15)

        if pnl <= -stop:
            alerts.append({
                "code": p["code"],
                "name": p["name"],
                "reason": f"触发止损：浮亏{pnl:.1f}% ≥ {stop}%止损线",
                "action": "卖出",
            })
        elif pnl >= profit * 0.85:
            alerts.append({
                "code": p["code"],
                "name": p["name"],
                "reason": f"接近止盈：浮盈{pnl:.1f}%（止盈线{profit}%），建议分批卖出",
                "action": "分批止盈",
            })

    return alerts
