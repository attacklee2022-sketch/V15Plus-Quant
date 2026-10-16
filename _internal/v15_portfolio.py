# -*- coding: utf-8 -*-
"""
V15 Alpha — 持仓跟踪与盈亏管理
==============================
记录买入成本、监控止损止盈、统计累计盈亏。
数据持久化到 V15Alpha/portfolio.json。
"""
from __future__ import annotations
import os, json, threading
from datetime import datetime, timedelta
from typing import Optional

# 数据文件路径
_PORTFOLIO_DIR = os.path.dirname(os.path.abspath(__file__))
_PORTFOLIO_PATH = os.path.join(_PORTFOLIO_DIR, "..", "portfolio.json")
_LOCK = threading.Lock()
_RW_LOCK = threading.Lock()


def _load() -> dict:
    """加载持仓数据。"""
    try:
        if os.path.exists(_PORTFOLIO_PATH):
            with open(_PORTFOLIO_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {"positions": [], "history": [], "stats": {}}


def _save(data: dict):
    """持久化持仓数据。"""
    with _LOCK:
        try:
            with open(_PORTFOLIO_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass


def get_positions() -> list[dict]:
    """获取当前所有持仓。"""
    return _load().get("positions", [])


def add_position(code: str, name: str, buy_price: float, shares: int,
                 buy_date: str = "", stop_loss_pct: float = 7.0,
                 take_profit_pct: float = 15.0, notes: str = ""):
    """新增一笔持仓。"""
    with _RW_LOCK:
        data = _load()
        if not buy_date:
            buy_date = datetime.now().strftime("%Y-%m-%d")

        # 生成唯一 ID
        pos_id = f"{code}_{buy_date}_{len(data['positions'])}"

        pos = {
            "id": pos_id,
            "code": code,
            "name": name,
            "buy_price": buy_price,
            "shares": shares,
            "buy_date": buy_date,
            "stop_loss_pct": stop_loss_pct,
            "take_profit_pct": take_profit_pct,
            "notes": notes,
            "status": "持有",
            "close_date": "",
            "close_price": 0.0,
            "pnl": 0.0,
            "pnl_pct": 0.0,
            "added_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }

        data["positions"].append(pos)
        _save(data)
        return pos_id


def close_position(pos_id: str, close_price: float, close_date: str = ""):
    """平仓一笔持仓。"""
    with _RW_LOCK:
        data = _load()
        if not close_date:
            close_date = datetime.now().strftime("%Y-%m-%d")

        for pos in data["positions"]:
            if pos["id"] == pos_id and pos["status"] == "持有":
                pos["status"] = "已平仓"
                pos["close_date"] = close_date
                pos["close_price"] = close_price
                pos["pnl"] = round((close_price - pos["buy_price"]) * pos["shares"], 2)
                pos["pnl_pct"] = round((close_price / pos["buy_price"] - 1) * 100, 2)

                # 加入历史
                data.setdefault("history", []).append({
                    "id": pos_id,
                    "code": pos["code"],
                    "name": pos["name"],
                    "buy_date": pos["buy_date"],
                    "close_date": close_date,
                    "buy_price": pos["buy_price"],
                    "close_price": close_price,
                    "shares": pos["shares"],
                    "pnl": pos["pnl"],
                    "pnl_pct": pos["pnl_pct"],
                    "hold_days": _hold_days(pos["buy_date"], close_date),
                })
                break

        _recalc_stats(data)
        _save(data)
        return True


def delete_position(pos_id: str):
    """删除一笔持仓（历史记录）。"""
    with _RW_LOCK:
        data = _load()
        data["positions"] = [
            p for p in data["positions"] if p["id"] != pos_id]
        _save(data)


def update_market_price(pos_id: str, current_price: float) -> dict:
    """更新持仓的市价，返回是否触发止损/止盈。"""
    with _RW_LOCK:
        data = _load()
    for pos in data["positions"]:
        if pos["id"] == pos_id and pos["status"] == "持有":
            pos["current_price"] = current_price
            pos["current_pnl_pct"] = round((current_price / pos["buy_price"] - 1) * 100, 2)
            alerts = []

            # 止损检查
            loss_pct = -pos["current_pnl_pct"]
            if loss_pct >= pos["stop_loss_pct"]:
                stop_price = round(pos["buy_price"] * (1 - pos["stop_loss_pct"] / 100), 2)
                alerts.append({
                    "type": "止损",
                    "level": "危险",
                    "msg": f"{pos['name']}({pos['code']}) 触发止损: 现价{current_price:.2f} 亏损{loss_pct:.1f}% (止损线{pos['stop_loss_pct']}% / 止损价{stop_price:.2f})"
                })

            # 止盈检查
            elif pos["current_pnl_pct"] >= pos["take_profit_pct"]:
                alerts.append({
                    "type": "止盈",
                    "level": "提醒",
                    "msg": f"{pos['name']}({pos['code']}) 达到止盈目标: 现价{current_price:.2f} 盈利{pos['current_pnl_pct']:.1f}% (止盈线{pos['take_profit_pct']}%)"
                })

            # 时间止损（持仓超过30天仍亏损）
            if pos["buy_date"]:
                try:
                    bd = datetime.strptime(pos["buy_date"], "%Y-%m-%d")
                    if (datetime.now() - bd).days > 30 and pos["current_pnl_pct"] < -3:
                        alerts.append({
                            "type": "时间止损",
                            "level": "提醒",
                            "msg": f"{pos['name']}({pos['code']}) 持仓超30天仍亏损{pos['current_pnl_pct']:.1f}%，建议考虑减仓"
                        })
                except Exception:
                    pass

            _save(data)
            return {"pos": pos, "alerts": alerts}

    return {"pos": None, "alerts": [{"type": "错误", "level": "错误", "msg": f"未找到持仓 {pos_id}"}]}


def get_portfolio_summary() -> dict:
    """获取持仓汇总：总成本、总市值、总盈亏、胜率等。"""
    data = _load()
    positions = [p for p in data.get("positions", []) if p.get("status") == "持有"]
    history = data.get("history", [])

    total_cost = 0.0
    total_market = 0.0
    total_pnl = 0.0
    active_count = len(positions)

    for p in positions:
        cost = p["buy_price"] * p["shares"]
        total_cost += cost
        cur_price = p.get("current_price", p["buy_price"])
        total_market += cur_price * p["shares"]

    total_pnl = total_market - total_cost
    total_pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0

    # 历史统计
    closed = history
    wins = [h for h in closed if h.get("pnl", 0) > 0]
    win_rate = (len(wins) / len(closed) * 100) if closed else 0
    total_hist_pnl = sum(h.get("pnl", 0) for h in closed)
    avg_win = (sum(h["pnl"] for h in wins) / len(wins)) if wins else 0
    losses = [h for h in closed if h.get("pnl", 0) <= 0]
    avg_loss = (sum(abs(h["pnl"]) for h in losses) / len(losses)) if losses else 0

    return {
        "active_count": active_count,
        "total_cost": round(total_cost, 2),
        "total_market": round(total_market, 2),
        "total_pnl": round(total_pnl, 2),
        "total_pnl_pct": round(total_pnl_pct, 2),
        "closed_count": len(closed),
        "win_count": len(wins),
        "win_rate": round(win_rate, 1),
        "total_hist_pnl": round(total_hist_pnl, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "profit_factor": round(abs(avg_win / avg_loss) if avg_loss else 0, 2),
    }


def _hold_days(buy_date: str, close_date: str) -> int:
    try:
        bd = datetime.strptime(buy_date, "%Y-%m-%d")
        cd = datetime.strptime(close_date, "%Y-%m-%d")
        return (cd - bd).days
    except Exception:
        return 0


def _recalc_stats(data: dict):
    """重新计算统计数据。"""
    summary = get_portfolio_summary()
    data["stats"] = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        **summary,
    }
