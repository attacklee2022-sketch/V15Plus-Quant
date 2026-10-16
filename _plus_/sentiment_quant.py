# -*- coding: utf-8 -*-
"""
V15 Plus+ — 新闻情绪量化模块
============================
从市场新闻中提取情绪信号，接入个股评分管道。
独立插件，fail-safe，不修改核心引擎。

设计原则：
  - 新闻情绪只做"修正"，不做"主导"（±8分上限）
  - 个股关联新闻权重最高，板块情绪次之，风险预警做减法
  - 时间衰减：半衰期12小时，旧闻不压新闻
"""
from __future__ import annotations
import time
from datetime import datetime
from math import exp


# ── 新闻缓存（模块级，跨股票共享）──
_news_cache: list | None = None
_news_cache_time: float = 0
_NEWS_CACHE_TTL: float = 120  # 2分钟，避免扫描每只股票都拉一次新闻


def warm_news_cache(news_items: list | None = None) -> list:
    """预热新闻缓存。外部已拉取新闻时直接注入，避免重复拉取。"""
    global _news_cache, _news_cache_time
    if news_items is not None:
        _news_cache = news_items
        _news_cache_time = time.time()
        return news_items
    return _get_news()


def _get_news() -> list:
    """获取市场新闻（带缓存）。"""
    global _news_cache, _news_cache_time
    now = time.time()
    if _news_cache is not None and (now - _news_cache_time) < _NEWS_CACHE_TTL:
        return _news_cache
    try:
        from v14_news_provider import get_market_news
        items, _source = get_market_news(online=True)
        _news_cache = items
        _news_cache_time = now
        return items
    except Exception:
        return []


# ── 核心评分 ──

def compute_sentiment(code: str, name: str,
                      news_items: list | None = None,
                      sector_map: dict | None = None) -> dict:
    """
    个股新闻情绪修正。

    参数:
        code: 股票代码
        name: 股票名称
        news_items: 预拉取的新闻列表（可选，为None则自动拉取）
        sector_map: 板块数据（用于确定股票所属板块）

    返回:
        {"adjustment": float,       # ±8
         "facts": [str],
         "detail": {...}}
    """
    result = {"adjustment": 0.0, "facts": [], "detail": {}}

    try:
        # 1. 获取新闻
        items = news_items if news_items else _get_news()
        if not items:
            result["facts"] = ["[情绪] 无新闻数据"]
            return result

        # 2. 确定股票所属板块
        stock_sector = _get_stock_sector(code, name, sector_map)

        now = datetime.now()
        adj = 0.0
        facts = []

        # ── 3. 个股级新闻 ──
        try:
            from v14_news_provider import news_relevance_for_stock
            stock_score, stock_news = news_relevance_for_stock(code, name, items)
            result["detail"]["stock_news_score"] = round(stock_score, 1)
            result["detail"]["stock_news_count"] = len(stock_news)

            if stock_news:
                # stock_score 是 [0,100]，映射到 [-6, +6]
                stock_adj = (stock_score - 50) * 0.12
                stock_adj = max(-6.0, min(6.0, stock_adj))
                adj += stock_adj
                if abs(stock_adj) >= 2:
                    direction = "偏多" if stock_adj > 0 else "偏空"
                    facts.append(
                        f"[情绪] 个股新闻{direction} "
                        f"关联度{stock_score:.0f}分 {len(stock_news)}条 {stock_adj:+.1f}"
                    )
                result["detail"]["stock_adj"] = round(stock_adj, 1)
        except Exception:
            result["detail"]["stock_adj"] = 0

        # ── 4. 板块级情绪（从蒸馏简报读取利好/利空）──
        try:
            from _plus_.sector_power import _get_distill_adjustment
            distill_adj = _get_distill_adjustment(stock_sector)
            if distill_adj > 1.01:
                sector_adj = +4.0
                result["detail"]["sector_adj"] = +4.0
                facts.append(f"[情绪] 蒸馏利好: 板块{stock_sector}出现在今日利好报表 +4")
            elif distill_adj < 0.99:
                sector_adj = -4.0
                result["detail"]["sector_adj"] = -4.0
                facts.append(f"[情绪] 蒸馏利空: 板块{stock_sector}出现在今日利空报表 -4")
            else:
                sector_adj = 0.0
                result["detail"]["sector_adj"] = 0.0
            adj += sector_adj
        except Exception:
            result["detail"]["sector_adj"] = 0

        # ── 5. 风险预警 ──
        try:
            from v14_news_provider import scan_stock_risks
            risks = scan_stock_risks(code, name, items)
            result["detail"]["risk_count"] = len(risks)

            risk_adj = 0.0
            for risk in risks:
                level = risk.get("risk_level", "medium")
                if level == "critical":
                    risk_adj -= 4
                elif level == "high":
                    risk_adj -= 2
                else:
                    risk_adj -= 1
            risk_adj = max(-6.0, risk_adj)
            adj += risk_adj
            if risks:
                critical_count = sum(1 for r in risks if r.get("risk_level") == "critical")
                high_count = sum(1 for r in risks if r.get("risk_level") == "high")
                if critical_count:
                    facts.append(f"[情绪] 风险预警 critical×{critical_count} high×{high_count} {risk_adj:+.1f}")
                elif high_count:
                    facts.append(f"[情绪] 风险预警 high×{high_count} {risk_adj:+.1f}")
            result["detail"]["risk_adj"] = round(risk_adj, 1)
        except Exception:
            result["detail"]["risk_adj"] = 0

        # ── 6. 汇总 ──
        adj = max(-8.0, min(8.0, adj))
        result["adjustment"] = round(adj, 1)
        result["detail"]["total_adj"] = round(adj, 1)
        result["facts"] = facts

        if not facts:
            result["facts"] = [f"[情绪] {stock_sector}板块无明显情绪信号 中性"]

        return result

    except ImportError:
        result["facts"] = ["[情绪] 新闻模块不可用"]
        return result
    except Exception as e:
        result["facts"] = [f"[情绪] 异常: {e}"]
        return result


# ── 辅助函数 ──

def _get_stock_sector(code, name, sector_map) -> str:
    """确定股票所属板块名称。"""
    # 优先用 sector_map + _best_sector_match
    if sector_map and code and name:
        try:
            from v14_deep_engine import _best_sector_match
            return _best_sector_match(sector_map, code, name)
        except Exception:
            pass
    # 降级：从新闻关键词反推
    try:
        from v14_news_provider import sector_from_text
        return sector_from_text(f"{code} {name}")
    except Exception:
        pass
    return "通用"


def _sector_sentiment(items: list, stock_sector: str, now: datetime) -> float:
    """聚合某板块的新闻情绪。

    用优先级×情绪×时间衰减加权，给板块级情绪评分。
    返回调整值 [-4, +4]。
    """
    sector_items = []
    for item in items:
        if item.sector == stock_sector:
            age_hours = max(0.1, (now - item.published_at).total_seconds() / 3600)
            sector_items.append((item, age_hours))

    if not sector_items:
        return 0.0

    # 加权情绪：优先级×情绪×时间衰减
    weighted = 0.0
    total_weight = 0.0
    for item, age_hours in sector_items:
        decay = exp(-age_hours / 12)  # 半衰期12小时
        weight = (item.priority / 100 + 0.2) * decay
        weighted += weight * item.sentiment
        total_weight += weight

    if total_weight == 0:
        return 0.0

    avg_sentiment = weighted / total_weight  # [-1, 1]

    # 映射到 [-4, +4]
    # 强信号需要多条新闻或高优先级新闻
    count_bonus = min(1.0, len(sector_items) / 5)  # 5条以上=满额
    adj = avg_sentiment * 4.0 * count_bonus

    return round(max(-4.0, min(4.0, adj)), 1)
