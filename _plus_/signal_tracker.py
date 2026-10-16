# -*- coding: utf-8 -*-
"""
V15 Plus+ — 信号质量追踪模块
========================
每次跑 V15 扫描时自动记录信号到 SQLite，
次日自动回填实际涨跌，计算命中率。
"""
from __future__ import annotations
import sqlite3
import os
import threading
from datetime import datetime, date, timedelta

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "data", "signal_history.db")


class SignalTracker:
    """V15 信号质量追踪。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self):
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._lock:
            conn = self._connect()
            conn.execute("""
                CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    code TEXT NOT NULL,
                    name TEXT,
                    level TEXT,
                    score REAL,
                    scenario TEXT,
                    sector TEXT,
                    action TEXT,
                    next_date TEXT,
                    next_chg REAL,
                    is_hit INTEGER,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sig_date ON signals(date)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sig_code ON signals(code)")
            conn.commit()
            conn.close()

    # ── 写入 ──

    def record_batch(self, results: list[dict], signal_date: str = None):
        """批量写入 V15 扫描结果。
        results: [{"code","name","level","score","scenario","sector","action"}, ...]
        signal_date: "2026-06-09"，默认今天
        """
        if not results:
            return
        if signal_date is None:
            signal_date = datetime.now().strftime("%Y-%m-%d")

        with self._lock:
            conn = self._connect()
            rows = []
            for r in results:
                sec = r.get("sector", {})
                sector_name = sec.get("sector", "") if isinstance(sec, dict) else str(sec)
                rows.append((
                    signal_date,
                    r.get("code", ""),
                    r.get("name", ""),
                    r.get("level", ""),
                    r.get("score", 0),
                    r.get("scenario", ""),
                    sector_name,
                    r.get("action", ""),
                ))
            conn.executemany(
                "INSERT INTO signals (date,code,name,level,score,scenario,sector,action) "
                "VALUES (?,?,?,?,?,?,?,?)",
                rows
            )
            conn.commit()
            conn.close()

    def update_next_day(self, signal_date: str):
        """对 signal_date 那天的信号，用真实次日涨跌回填。
        次日日期 = signal_date + 1 个交易日（简单跳过周末）。
        """
        date_obj = datetime.strptime(signal_date, "%Y-%m-%d").date()
        # 推算次日（跳过周末）
        next_obj = date_obj + timedelta(days=1)
        while next_obj.weekday() >= 5:  # 周五→周一
            next_obj += timedelta(days=1)
        next_date_str = next_obj.strftime("%Y-%m-%d")

        with self._lock:
            conn = self._connect()
            # 取所有当日无 next_chg 的信号
            cur = conn.execute(
                "SELECT id, code, date FROM signals WHERE date=? AND next_chg IS NULL",
                (signal_date,)
            )
            pending = cur.fetchall()
            if not pending:
                conn.close()
                return

            updated = 0
            for row in pending:
                code = row["code"]
                try:
                    # 加载次日日线数据
                    from scripts.stock_gui_V14 import load_stock_tdx
                    df = load_stock_tdx(code)
                    if df is not None and len(df) >= 2:
                        # 找到 signal_date 那天的收盘价和后一天的收盘价
                        df_sorted = df.sort_values("date").reset_index(drop=True)
                        for i in range(len(df_sorted) - 1):
                            d = df_sorted.iloc[i]["date"]
                            if hasattr(d, "strftime"):
                                d_str = d.strftime("%Y-%m-%d")
                            else:
                                d_str = str(d)[:10]
                            if d_str == signal_date:
                                today_close = df_sorted.iloc[i]["收盘"]
                                next_close = df_sorted.iloc[i + 1]["收盘"]
                                if today_close and today_close > 0:
                                    chg = (next_close - today_close) / today_close * 100
                                    is_hit = 1 if chg > 0 else 0
                                    conn.execute(
                                        "UPDATE signals SET next_date=?, next_chg=?, is_hit=? WHERE id=?",
                                        (next_date_str, round(chg, 2), is_hit, row["id"])
                                    )
                                    updated += 1
                                break
                except Exception:
                    pass

            conn.commit()
            conn.close()
        return updated

    # ── 统计 ──

    def get_stats(self, days: int = 30) -> dict:
        """近 N 天信号命中率统计。"""
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        conn = self._connect()
        row = conn.execute(
            "SELECT COUNT(*) AS total, SUM(is_hit) AS hits "
            "FROM signals WHERE date >= ? AND is_hit IS NOT NULL",
            (cutoff,)
        ).fetchone()
        total = row["total"] or 0
        hits = row["hits"] or 0
        rate = round(hits / total * 100, 1) if total > 0 else 0
        conn.close()
        return {"total": total, "hits": hits, "hit_rate": rate, "days": days}

    def get_stats_by_level(self, days: int = 30) -> dict:
        """按等级统计命中率。"""
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        conn = self._connect()
        rows = conn.execute(
            "SELECT level, COUNT(*) AS total, SUM(is_hit) AS hits "
            "FROM signals WHERE date >= ? AND is_hit IS NOT NULL "
            "GROUP BY level",
            (cutoff,)
        ).fetchall()
        conn.close()
        result = {}
        for r in rows:
            total = r["total"] or 0
            hits = r["hits"] or 0
            result[r["level"]] = {
                "total": total,
                "hits": hits,
                "hit_rate": round(hits / total * 100, 1) if total > 0 else 0,
            }
        return result

    def get_stats_by_scenario(self, days: int = 30) -> dict:
        """按场景统计命中率。"""
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        conn = self._connect()
        rows = conn.execute(
            "SELECT scenario, COUNT(*) AS total, SUM(is_hit) AS hits "
            "FROM signals WHERE date >= ? AND is_hit IS NOT NULL "
            "GROUP BY scenario",
            (cutoff,)
        ).fetchall()
        conn.close()
        result = {}
        for r in rows:
            total = r["total"] or 0
            hits = r["hits"] or 0
            result[r["scenario"]] = {
                "total": total,
                "hits": hits,
                "hit_rate": round(hits / total * 100, 1) if total > 0 else 0,
            }
        return result

    def get_latest_stats_text(self) -> str:
        """获取最新统计文本（用于信号看板展示）。"""
        stats = self.get_stats(30)
        by_level = self.get_stats_by_level(30)
        if stats["total"] == 0:
            return "V15 暂无历史信号数据"

        parts = [f"V15 近30日命中率 {stats['hit_rate']:.0f}% ({stats['hits']}/{stats['total']})"]
        for lv in ["强势", "偏强", "中性", "弱势"]:
            if lv in by_level:
                lv_stats = by_level[lv]
                if lv_stats["total"] > 0:
                    parts.append(f"{lv} {lv_stats['hit_rate']:.0f}%")
        return " | ".join(parts)


# 全局单例
_tracker: SignalTracker | None = None


def get_tracker() -> SignalTracker:
    global _tracker
    if _tracker is None:
        _tracker = SignalTracker()
    return _tracker
