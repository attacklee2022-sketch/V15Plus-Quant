# -*- coding: utf-8 -*-
"""
板块历史资金采集模块 — 独立，不依赖 V15Plus 其他模块。

职责：
  每天收盘后用 bb-browser 抓东方财富板块资金流页面，
  存入本地 SQLite，长期累积板块历史数据。

用法：
  python -c "from _plus_.sector_history_collector import SectorHistoryCollector; SectorHistoryCollector().collect()"

Windows 定时任务（每天 15:35）：
  schtasks /create /tn "V15Plus-SectorHistory" /tr "python <项目根目录>\\_plus_\\sector_history_collector.py" /sc daily /st 15:35
"""
from __future__ import annotations
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, date

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.dirname(_SCRIPT_DIR)
_DB_PATH = os.path.join(_APP_DIR, "data", "sector_history.db")

# ── bb-browser 辅助 ──────────────────────────────────

def _bb(cmd: list[str], timeout: int = 30) -> str:
    npx = os.environ.get("NPX_CMD", "C:\\Program Files\\nodejs\\npx.cmd")
    full = [npx, "bb-browser"] + cmd
    try:
        r = subprocess.run(full, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
        return r.stdout.strip() or ""
    except subprocess.TimeoutExpired:
        return ""
    except Exception:
        return ""


def _bb_open(url: str) -> str:
    r = _bb(["open", url], timeout=15)
    m = re.search(r'tab:\s*(\S+)', r)
    return m.group(1) if m else ""


def _bb_eval(js: str, tab: str, timeout: int = 30) -> str:
    return _bb(["eval", js, "--tab", tab], timeout)


def _parse_yi(val: str) -> float:
    """解析东方财富单位：'20.65亿' → 2065000000，'-5.74亿' → -574000000"""
    if not val:
        return 0.0
    val = val.replace(",", "").strip()
    if "万亿" in val:
        return float(val.replace("万亿", "").strip()) * 1e12
    if "亿" in val:
        return float(val.replace("亿", "").strip()) * 1e8
    if "万" in val:
        return float(val.replace("万", "").strip()) * 1e4
    try:
        return float(val)
    except ValueError:
        return 0.0


# ── SQLite ──────────────────────────────────────────

def _get_conn():
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_sector (
            date TEXT NOT NULL,
            name TEXT NOT NULL,
            change_pct REAL DEFAULT 0,
            main_net_inflow REAL DEFAULT 0,
            super_large_inflow REAL DEFAULT 0,
            large_inflow REAL DEFAULT 0,
            mid_inflow REAL DEFAULT 0,
            small_inflow REAL DEFAULT 0,
            main_inflow_pct REAL DEFAULT 0,
            top_stock TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            UNIQUE(date, name)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ds_date ON daily_sector(date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ds_name ON daily_sector(name)")
    return conn


# ── 采集 ────────────────────────────────────────────

class SectorHistoryCollector:
    """板块历史资金采集器。"""

    @staticmethod
    def collect() -> dict:
        """
        用 bb-browser 采集东方财富板块资金流，写入 SQLite。

        返回:
            {"ok": True/False, "count": N, "date": "20260708", "source": "bb-browser", "error": "..."}
        """
        today = date.today().strftime("%Y%m%d")
        print(f"[SectorHistory] 采集 {today} 板块资金流...")

        # ── 打开页面 ──
        tab = _bb_open("https://data.eastmoney.com/bkzj/hy.html")
        if not tab:
            return {"ok": False, "count": 0, "date": today, "error": "bb-browser 打开页面失败"}
        print(f"  [OK] 页面已打开 tab={tab}")
        time.sleep(3)

        # ── 获取总页数 ──
        js_pages = (
            "JSON.stringify(Math.max(...[...document.querySelectorAll('a[data-page]')]"
            ".map(a => parseInt(a.getAttribute('data-page')))))"
        )
        raw_pages = _bb_eval(js_pages, tab, timeout=10)
        try:
            total_pages = max(int(json.loads(raw_pages)), 1)
        except (json.JSONDecodeError, ValueError, TypeError):
            total_pages = 1
        print(f"  [OK] 总页数: {total_pages}")

        # ── 逐页采集 ──
        all_rows = []
        for page in range(1, total_pages + 1):
            if page > 1:
                js_click = (
                    f"(()=>{{const t={page},"
                    f"e=document.querySelector('a[data-page=\"'+t+'\"]');"
                    f"if(!e)return 'nf';"
                    f"e.dispatchEvent(new MouseEvent('click',{{bubbles:!0,cancelable:!0,view:window}}));"
                    f"let i=0;return new Promise(r=>{{"
                    f"let c=()=>{{let a=document.querySelector('.pagerbox a.active');"
                    f"if(a&&a.textContent.trim()==t)return r('ok');i++;"
                    f"if(i>=15)return r('retry');setTimeout(c,1000)}};setTimeout(c,300)}})}})()"
                )
                r = _bb_eval(js_click, tab, timeout=20)
                if "ok" not in r:
                    print(f"  [WARN] 第{page}页翻页失败: {r}")
                    continue
                time.sleep(1)

            js_extract = (
                "JSON.stringify({"
                "count:document.querySelectorAll('table tbody tr').length,"
                "data:[...document.querySelectorAll('table tbody tr')]"
                ".map(tr=>[...tr.querySelectorAll('td')].map(td=>td.textContent.trim()))"
                "})"
            )
            raw_data = _bb_eval(js_extract, tab, timeout=15)
            try:
                parsed = json.loads(raw_data)
                rows = parsed.get("data", [])
                print(f"  [OK] 第{page}页: {len(rows)} 行")
                all_rows.extend(rows)
            except json.JSONDecodeError:
                print(f"  [WARN] 第{page}页解析失败")

        _bb(["close", "--tab", tab])
        print(f"  [OK] 共采集 {len(all_rows)} 个板块")

        if not all_rows:
            return {"ok": False, "count": 0, "date": today, "error": "未采集到数据"}

        # ── 解析写入 ──
        conn = _get_conn()
        inserted = 0
        for row in all_rows:
            if len(row) < 10:
                continue
            try:
                name = row[1]
                change_pct = float(row[3].replace("%", "").strip()) if row[3] else 0.0
                main_net = _parse_yi(row[4])
                main_pct = float(row[5].replace("%", "").strip()) if len(row) > 5 and row[5] else 0.0
                super_large = _parse_yi(row[6]) if len(row) > 6 else 0.0
                large = _parse_yi(row[8]) if len(row) > 8 else 0.0
                mid = _parse_yi(row[10]) if len(row) > 10 else 0.0
                small = _parse_yi(row[12]) if len(row) > 12 else 0.0
                top_stock = row[14] if len(row) > 14 else ""
            except (ValueError, IndexError):
                continue

            try:
                conn.execute(
                    """INSERT OR IGNORE INTO daily_sector
                       (date, name, change_pct, main_net_inflow, super_large_inflow,
                        large_inflow, mid_inflow, small_inflow, main_inflow_pct, top_stock)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (today, name, change_pct, main_net, super_large,
                     large, mid, small, main_pct, top_stock)
                )
                if conn.total_changes:
                    inserted += 1
            except Exception:
                pass
        conn.commit()
        conn.close()

        print(f"  [OK] 写入 {inserted} 条记录")
        return {"ok": True, "count": inserted, "date": today, "source": "bb-browser"}

    # ── 查询 ──

    @staticmethod
    def list_dates() -> list[str]:
        conn = _get_conn()
        rows = conn.execute("SELECT DISTINCT date FROM daily_sector ORDER BY date DESC").fetchall()
        conn.close()
        return [r["date"] for r in rows]

    @staticmethod
    def summary() -> dict:
        conn = _get_conn()
        info = conn.execute("""
            SELECT
                COUNT(DISTINCT date) AS total_days,
                IFNULL(MIN(date), '') AS first_date,
                IFNULL(MAX(date), '') AS last_date,
                COUNT(DISTINCT name) AS total_sectors,
                COUNT(*) AS total_records
            FROM daily_sector
        """).fetchone()
        conn.close()
        return dict(info) if info else {"total_days": 0, "first_date": "", "last_date": "", "total_sectors": 0, "total_records": 0}

    @staticmethod
    def query(date_from: str = "", date_to: str = "", sector: str = "", limit: int = 500) -> list[dict]:
        conn = _get_conn()
        parts = ["SELECT * FROM daily_sector WHERE 1=1"]
        params = []
        if date_from:
            parts.append("AND date >= ?")
            params.append(date_from)
        if date_to:
            parts.append("AND date <= ?")
            params.append(date_to)
        if sector:
            parts.append("AND name LIKE ?")
            params.append(f"%{sector}%")
        parts.append("ORDER BY date DESC, main_net_inflow DESC")
        parts.append("LIMIT ?")
        params.append(limit)
        rows = conn.execute(" ".join(parts), params).fetchall()
        conn.close()
        return [dict(r) for r in rows]


# ── CLI ─────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="板块历史资金采集")
    parser.add_argument("action", nargs="?", default="collect",
                        choices=["collect", "list", "summary"])
    args = parser.parse_args()

    if args.action == "collect":
        result = SectorHistoryCollector.collect()
        print(f"\n结果: {json.dumps(result, ensure_ascii=False)}")
    elif args.action == "list":
        dates = SectorHistoryCollector.list_dates()
        print(f"已收集 {len(dates)} 天:")
        for d in dates:
            print(f"  {d}")
    elif args.action == "summary":
        s = SectorHistoryCollector.summary()
        print(f"总天数: {s.get('total_days', 0)}")
        print(f"最早: {s.get('first_date', '-')}")
        print(f"最新: {s.get('last_date', '-')}")
        print(f"板块数: {s.get('total_sectors', 0)}")
        print(f"总记录: {s.get('total_records', 0)}")
