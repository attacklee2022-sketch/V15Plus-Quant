# -*- coding: utf-8 -*-
"""
V15Plus — 本地快照导出脚本
==========================
从 V15Plus 读取个股评分、板块动力、新闻蒸馏、自选股、组合等数据，
组装为 HybridSnapshot JSON，交给 cloud_sync 发送到云端。

使用方式（在 V15Plus 目录下）：
    python scripts/export_hybrid_snapshot.py

或通过 GUI 板块 Tab 的"同步到云端"按钮调用。

旁路设计：不修改 V15Plus GUI 主流程，以脚本+模块方式集成。
"""
from __future__ import annotations
import os
import sys
import json
import time
from datetime import datetime

# ── 路径 ──
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.dirname(_SCRIPT_DIR)
_INTERNAL = os.path.join(_APP_DIR, "_internal")
_PLUS = os.path.join(_APP_DIR, "_plus_")
for p in [_INTERNAL, _PLUS, _APP_DIR]:
    if p not in sys.path:
        sys.path.insert(0, p)


# ══════════════════════════════════════════════════════
# 导入 V15Plus 模块（try/except fail-safe）
# ══════════════════════════════════════════════════════

def _import_v15_modules():
    """返回各模块引用（失败=None）。"""
    mods = {}
    try:
        from _plus_ import sector_power
        mods["sector_power"] = sector_power
    except Exception:
        mods["sector_power"] = None
    try:
        from _plus_ import news_distiller
        mods["news_distiller"] = news_distiller
    except Exception:
        mods["news_distiller"] = None
    try:
        from _plus_ import signal_tracker
        mods["signal_tracker"] = signal_tracker
    except Exception:
        mods["signal_tracker"] = None
    return mods


# ══════════════════════════════════════════════════════
# 数据采集函数（各模块独立 try/except）
# ══════════════════════════════════════════════════════

def _collect_sectors(mods: dict) -> dict:
    """采集板块动力数据。"""
    result = {
        "sectors": [], "t1_updated": False,
        "updated_at": "", "status": "ok", "message": "",
    }
    sp = mods.get("sector_power")
    if sp is None:
        result["status"] = "missing"
        result["message"] = "sector_power 模块不可用"
        return result

    try:
        sp._compute_all_t1_scores()
        result["t1_updated"] = True

        # T1 数据（资金占比）
        try:
            chart = sp.get_flow_chart_data()
            daily = chart.get("daily", [])
            for item in daily:
                share, z_val, tdx_code, name, dz, prev = item
                result["sectors"].append({
                    "name": name,
                    "tdx_code": tdx_code,
                    "share_pct": round(share, 2),
                    "z_score": round(z_val, 2),
                    "dz": round(dz, 4) if dz else 0,
                    "prev_dz": round(prev, 4) if prev else 0,
                })
        except Exception as e:
            result["status"] = "partial"
            result.setdefault("warnings", []).append(f"flow_chart: {e}")

        # T2 + 综合动力分
        try:
            for sec in result["sectors"]:
                tdx = sec["tdx_code"]
                score = sp.get_sector_power(tdx) if hasattr(sp, "get_sector_power") else {}
                if score:
                    sec["power_score"] = round(score.get("power_score", 0), 1)
                    sec["t1_score"] = round(score.get("t1_score", 0), 1)
                    sec["t2_score"] = round(score.get("t2_score", 0), 1)
                    sec["volume_change_pct"] = round(score.get("volume_change_pct", 0), 1)
                    sec["distill_bonus"] = round(score.get("distill_bonus", 0), 1)
                    sec["power_rank"] = score.get("power_rank", 0)
                    sec["decay_days"] = score.get("decay_days", 999)
        except Exception as e:
            result.setdefault("warnings", []).append(f"power_score: {e}")

        # 排序
        result["sectors"].sort(key=lambda x: -x.get("share_pct", 0))
        result["updated_at"] = datetime.now().strftime("%H:%M:%S")

    except Exception as e:
        result["status"] = "error"
        result["message"] = str(e)

    # 补充排名
    total = len(result["sectors"])
    for i, sec in enumerate(result["sectors"]):
        sec["power_rank"] = sec.get("power_rank") or (i + 1)
        sec["power_total"] = total

    return result


def _collect_news(mods: dict) -> dict:
    """采集新闻蒸馏简报。"""
    result = {
        "sentiment_label": "中性", "sentiment_score": 50.0,
        "sentiment_evidence": [], "hot_themes": [],
        "good_news": [], "bad_news": [], "news_count": 0,
        "updated_at": "", "status": "ok", "message": "",
    }
    nd = mods.get("news_distiller")
    if nd is None:
        result["status"] = "missing"
        result["message"] = "news_distiller 模块不可用"
        return result

    try:
        from v14_news_provider import get_market_news
        items, _source = get_market_news(online=True)
        if items:
            distill = nd.quick_distill(items)
            result["sentiment_label"] = distill.sentiment_label or "中性"
            result["sentiment_score"] = distill.sentiment_score or 50.0
            result["sentiment_evidence"] = distill.sentiment_evidence[:3]
            result["hot_themes"] = distill.hot_themes[:5]
            result["good_news"] = distill.good_news[:5]
            result["bad_news"] = distill.bad_news[:5]
            result["news_count"] = distill.news_count or len(items)
        result["updated_at"] = datetime.now().strftime("%H:%M:%S")
    except Exception as e:
        result["status"] = "error"
        result["message"] = str(e)

    return result


def _collect_portfolio() -> dict:
    """采集投资组合数据。"""
    result = {
        "positions": [], "active_count": 0,
        "total_cost": 0.0, "total_market": 0.0,
        "total_pnl_pct": 0.0,
        "updated_at": "", "status": "ok", "message": "",
    }
    pf_path = os.path.join(_APP_DIR, "portfolio.json")
    if not os.path.exists(pf_path):
        result["status"] = "missing"
        result["message"] = "portfolio.json 不存在"
        return result

    try:
        with open(pf_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        result["active_count"] = data.get("stats", {}).get("active_count", 0)
        result["total_cost"] = data.get("stats", {}).get("total_cost", 0)
        result["total_market"] = data.get("stats", {}).get("total_market", 0)
        result["total_pnl_pct"] = data.get("stats", {}).get("total_pnl_pct", 0)

        for pos in data.get("positions", []):
            if pos.get("status") == "持有":
                result["positions"].append({
                    "code": pos["code"],
                    "name": pos["name"],
                    "shares": pos.get("shares", 0),
                    "buy_price": pos.get("buy_price", 0),
                    "current_price": pos.get("current_price", 0),
                    "buy_date": pos.get("buy_date", ""),
                    "status": "持有",
                    "pnl_pct": round(pos.get("current_pnl_pct", 0), 2),
                    "pnl": round(pos.get("pnl", 0), 2),
                    "stop_loss_pct": pos.get("stop_loss_pct", 0),
                    "take_profit_pct": pos.get("take_profit_pct", 0),
                })

        result["updated_at"] = data.get("stats", {}).get("updated_at", "")
    except Exception as e:
        result["status"] = "error"
        result["message"] = str(e)

    return result


def _collect_watchlist() -> dict:
    """采集自选股列表。"""
    result = {
        "stocks": [], "total": 0,
        "updated_at": "", "status": "ok", "message": "",
    }
    wl_path = os.path.join(_APP_DIR, "self_stock.txt")
    if not os.path.exists(wl_path):
        result["status"] = "missing"
        result["message"] = "self_stock.txt 不存在"
        return result

    try:
        stocks = []
        with open(wl_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                parts = line.split(None, 1)
                if len(parts) == 2:
                    stocks.append({"code": parts[0], "name": parts[1]})
                elif len(parts) == 1:
                    stocks.append({"code": parts[0], "name": ""})
        result["stocks"] = stocks
        result["total"] = len(stocks)
        result["updated_at"] = datetime.now().strftime("%H:%M:%S")
    except Exception as e:
        result["status"] = "error"
        result["message"] = str(e)

    return result


# ══════════════════════════════════════════════════════
# 入口
# ══════════════════════════════════════════════════════

def export_snapshot(market_phase: str = "close") -> dict:
    """
    导出完整 HybridSnapshot（不包含个股扫描，扫描另行触发）。

    参数:
        market_phase: 盘面阶段标签
            open / midday / afternoon / close

    返回:
        HybridSnapshot dict（同时提供 to_json 方法）
    """
    mods = _import_v15_modules()
    now = datetime.now()

    snapshot = {
        "snapshot_time": now.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
        "date": now.strftime("%Y%m%d"),
        "market_phase": market_phase,
        "v15_version": "",
        "v15plus_version": "0.2.0",
        "export_tool": "export_hybrid_snapshot.py",
    }

    # ── 板块动力 ──
    try:
        sec = _collect_sectors(mods)
        snapshot["sectors"] = {
            "sectors": sec["sectors"],
            "t1_updated": sec["t1_updated"],
            "updated_at": sec["updated_at"],
            "provenance": {
                "source": "v15",
                "as_of": snapshot["snapshot_time"],
                "freshness_seconds": 0,
                "confidence": "high" if sec["status"] == "ok" else "low",
                "missing_reason": sec.get("message", "") if sec["status"] != "ok" else "",
            }
        }
    except Exception as e:
        snapshot["sectors"] = {
            "sectors": [], "t1_updated": False,
            "provenance": {"source": "v15", "as_of": snapshot["snapshot_time"],
                           "confidence": "low", "missing_reason": str(e)}
        }

    # ── 新闻蒸馏 ──
    try:
        news = _collect_news(mods)
        snapshot["news_distill"] = {
            "sentiment_label": news["sentiment_label"],
            "sentiment_score": news["sentiment_score"],
            "sentiment_evidence": news["sentiment_evidence"],
            "hot_themes": news["hot_themes"],
            "good_news": news["good_news"],
            "bad_news": news["bad_news"],
            "news_count": news["news_count"],
            "updated_at": news["updated_at"],
            "provenance": {
                "source": "v15", "as_of": snapshot["snapshot_time"],
                "confidence": "high" if news["status"] == "ok" else "low",
                "missing_reason": news.get("message", "") if news["status"] != "ok" else "",
            }
        }
    except Exception as e:
        snapshot["news_distill"] = {
            "sentiment_label": "暂无数据", "sentiment_score": 50,
            "provenance": {"source": "v15", "as_of": snapshot["snapshot_time"],
                           "confidence": "low", "missing_reason": str(e)}
        }

    # ── 投资组合 ──
    try:
        pf = _collect_portfolio()
        snapshot["portfolio"] = {
            "positions": pf["positions"],
            "active_count": pf["active_count"],
            "total_cost": pf["total_cost"],
            "total_market": pf["total_market"],
            "total_pnl_pct": pf["total_pnl_pct"],
            "updated_at": pf["updated_at"],
            "provenance": {
                "source": "v15", "as_of": snapshot["snapshot_time"],
                "confidence": "high" if pf["status"] == "ok" else "low",
                "missing_reason": pf.get("message", "") if pf["status"] != "ok" else "",
            }
        }
    except Exception as e:
        snapshot["portfolio"] = {
            "positions": [], "active_count": 0,
            "provenance": {"source": "v15", "as_of": snapshot["snapshot_time"],
                           "confidence": "low", "missing_reason": str(e)}
        }

    # ── 自选股 ──
    try:
        wl = _collect_watchlist()
        snapshot["watchlist"] = {
            "stocks": wl["stocks"],
            "total": wl["total"],
            "updated_at": wl["updated_at"],
            "provenance": {
                "source": "v15", "as_of": snapshot["snapshot_time"],
                "confidence": "high" if wl["status"] == "ok" else "low",
                "missing_reason": wl.get("message", "") if wl["status"] != "ok" else "",
            }
        }
    except Exception as e:
        snapshot["watchlist"] = {
            "stocks": [], "total": 0,
            "provenance": {"source": "v15", "as_of": snapshot["snapshot_time"],
                           "confidence": "low", "missing_reason": str(e)}
        }

    # ── 信号追踪 ──
    try:
        st = mods.get("signal_tracker")
        if st:
            tracker = st.SignalTracker()
            signals = tracker.get_recent(limit=20)
            total = tracker.count()
            hit = tracker.hit_rate()
            snapshot["signals"] = {
                "recent_signals": [
                    {
                        "code": s.get("code", ""),
                        "name": s.get("name", ""),
                        "signal_date": s.get("signal_date", ""),
                        "score": s.get("score", 0),
                        "level": s.get("level", ""),
                        "hit_status": s.get("hit_status", "待验证"),
                        "hit_pnl_pct": s.get("hit_pnl_pct", 0),
                    }
                    for s in (signals or [])
                ],
                "total_tracked": total,
                "hit_rate": round(hit, 2) if hit else 0,
                "updated_at": snapshot["snapshot_time"],
                "provenance": {
                    "source": "v15", "as_of": snapshot["snapshot_time"],
                    "confidence": "medium",
                }
            }
    except Exception as e:
        snapshot["signals"] = {
            "recent_signals": [], "total_tracked": 0, "hit_rate": 0,
            "provenance": {"source": "v15", "as_of": snapshot["snapshot_time"],
                           "confidence": "low", "missing_reason": str(e)}
        }

    return snapshot


def export_and_save(market_phase: str = "close",
                    out_dir: str | None = None) -> str:
    """
    导出快照并保存到本地 JSON 文件。

    返回:
        保存路径
    """
    if out_dir is None:
        out_dir = os.path.join(_APP_DIR, "data", "cloud_outbox")

    os.makedirs(out_dir, exist_ok=True)
    snapshot = export_snapshot(market_phase)

    date_str = snapshot["date"]
    time_str = datetime.now().strftime("%H%M%S")
    path = os.path.join(out_dir, f"snapshot_{date_str}_{time_str}.json")

    with open(path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, default=str, indent=2)

    return path


# ══════════════════════════════════════════════════════
# CLI 入口
# ══════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="V15Plus 本地快照导出")
    parser.add_argument("--phase", default="close",
                        choices=["open", "midday", "afternoon", "close"],
                        help="盘面阶段")
    parser.add_argument("--save", action="store_true", default=True,
                        help="保存到本地文件")
    parser.add_argument("--out-dir", default=None,
                        help="输出目录（默认 data/cloud_outbox）")
    args = parser.parse_args()

    print(f"[快照导出] 阶段: {args.phase}")
    path = export_and_save(args.phase, args.out_dir)
    print(f"[快照导出] 已保存: {path}")

    # 统计摘要
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    sec_count = len(data.get("sectors", {}).get("sectors", []))
    wl_count = data.get("watchlist", {}).get("total", 0)
    pf_count = data.get("portfolio", {}).get("active_count", 0)
    print(f"[快照导出] 板块:{sec_count} 自选:{wl_count} 持仓:{pf_count}")
    print("[快照导出] 完成")
