# -*- coding: utf-8 -*-
"""
V15Plus 自动虚拟交易引擎
========================
完全无界面，只依赖通达信本地日线数据 + DeepSeek API。
每个交易日收盘后运行一次，自动扫描→决策→执行→记录。

启动方式:
    python _plus_/auto_trader.py           # 手动运行一次
    python _plus_/auto_trader.py --now     # 强制运行（忽略日内去重）

数据依赖:
    - 通达信本地 .day 文件（D:\通达信\vipdoc\）  → 100% 本地
    - sector_cache.json（板块映射缓存）          → 100% 本地
    - DeepSeek API                             → 唯一外部依赖

不对 V15Plus 任何现有文件做修改，账户和日志写入 data/ 目录。
"""
from __future__ import annotations

import os
import sys
import json
import re
import time
import threading
import concurrent.futures
from datetime import datetime, date
from typing import Optional

# ── 路径设置 ──────────────────────────────────────────
_THIS_FILE = os.path.abspath(__file__)
_PLUS_DIR = os.path.dirname(_THIS_FILE)
_PROJECT_DIR = os.path.dirname(_PLUS_DIR)
_INTERNAL_DIR = os.path.join(_PROJECT_DIR, "_internal")
_DATA_DIR = os.path.join(_PROJECT_DIR, "data")

# 确保 V15Plus 项目根和 _internal 在 sys.path 最前面
for _p in (_PROJECT_DIR, _INTERNAL_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ── 配置 ──────────────────────────────────────────────
RISK_CONFIG = {
    "initial_capital": 1_000_000,   # 初始资金
    "max_position_pct": 0.20,       # 单票上限
    "max_holdings": 8,              # 最大持仓数
    "max_sector_pct": 0.40,         # 单行业集中度上限
    "commission_rate": 0.0003,      # 手续费 0.03%
    "stamp_tax_rate": 0.001,        # 印花税 0.1%（仅卖出）
    "slippage": 0.001,              # 滑点 0.1%
    "stop_loss_pct": -0.08,         # 强制止损线
    "take_profit_pct": 0.20,        # 强制止盈线
}

# 文件路径
SELF_STOCK_TXT = os.path.join(_PROJECT_DIR, "self_stock.txt")
ACCOUNT_JSON = os.path.join(_DATA_DIR, "auto_account.json")
LOG_DIR = os.path.join(_DATA_DIR, "auto_trader_logs")
LAST_RUN_FILE = os.path.join(_DATA_DIR, ".auto_trader_last_run")

os.makedirs(LOG_DIR, exist_ok=True)


# ══════════════════════════════════════════════════════
# 虚拟账户
# ══════════════════════════════════════════════════════

class VirtualAccount:
    """简易虚拟账户，管理资金、持仓、交易记录。"""

    def __init__(self, name: str = "V15Plus自动交易"):
        self.name = name
        self.initial_capital = RISK_CONFIG["initial_capital"]
        self.cash = self.initial_capital
        self.created_at = datetime.now().strftime("%Y-%m-%d %H:%M")
        self.holdings: dict[str, dict] = {}
        self.transactions: list[dict] = []
        self._lock = threading.Lock()

    # ── 买卖 ──

    def buy(self, code: str, name: str, price: float, shares: int,
            trade_date: str = "", reason: str = "") -> dict:
        with self._lock:
            if shares <= 0 or price <= 0:
                return {"ok": False, "msg": f"参数无效"}
            if code in self.holdings:
                return {"ok": False, "msg": f"已持有{name}"}
            if len(self.holdings) >= RISK_CONFIG["max_holdings"]:
                return {"ok": False, "msg": f"持仓已满({RISK_CONFIG['max_holdings']}只)"}

            raw = price * shares
            cost = raw * (1 + RISK_CONFIG["commission_rate"] + RISK_CONFIG["slippage"])
            if cost > self.cash:
                return {"ok": False, "msg": f"资金不足 需¥{cost:,.0f} 可用¥{self.cash:,.0f}"}
            if raw > self._total_assets() * RISK_CONFIG["max_position_pct"]:
                return {"ok": False, "msg": f"超单票20%上限"}

            if not trade_date:
                trade_date = date.today().strftime("%Y-%m-%d")

            self.cash -= cost
            txn = {
                "id": f"txn_{len(self.transactions)+1:04d}",
                "date": trade_date, "type": "买入",
                "code": code, "name": name, "price": round(price, 2),
                "shares": shares, "amount": round(raw, 2),
                "commission": round(raw * RISK_CONFIG["commission_rate"], 2),
                "stamp_tax": 0,
                "slippage": round(raw * RISK_CONFIG["slippage"], 2),
                "cost": round(cost, 2), "reason": reason,
            }
            self.transactions.append(txn)

            stop_price = round(price * (1 + RISK_CONFIG["stop_loss_pct"]), 2)
            tp_price = round(price * (1 + RISK_CONFIG["take_profit_pct"]), 2)
            self.holdings[code] = {
                "code": code, "name": name, "buy_date": trade_date,
                "buy_price": round(price, 2), "shares": shares,
                "cost": round(cost, 2), "current_price": round(price, 2),
                "market_value": round(raw, 2),
                "pnl": round(-cost + raw, 2),
                "pnl_pct": round((-cost + raw) / cost * 100, 2),
                "stop_loss_price": stop_price,
                "take_profit_price": tp_price,
            }
            return {"ok": True, "msg": f"买入 {name} {shares}股 @{price:.2f}", "txn": txn}

    def sell(self, code: str, price: float, shares: int = 0,
             trade_date: str = "", reason: str = "") -> dict:
        with self._lock:
            if code not in self.holdings:
                return {"ok": False, "msg": f"未持有{code}"}

            h = self.holdings[code]
            if shares <= 0 or shares >= h["shares"]:
                shares = h["shares"]

            if not trade_date:
                trade_date = date.today().strftime("%Y-%m-%d")

            raw = price * shares
            commission = raw * RISK_CONFIG["commission_rate"]
            stamp = raw * RISK_CONFIG["stamp_tax_rate"]
            slip = raw * RISK_CONFIG["slippage"]
            net = raw - commission - stamp - slip
            self.cash += net

            cost_basis = h["cost"] * (shares / h["shares"])
            pnl = net - cost_basis
            pnl_pct = (pnl / cost_basis * 100) if cost_basis > 0 else 0

            txn = {
                "id": f"txn_{len(self.transactions)+1:04d}",
                "date": trade_date, "type": "卖出",
                "code": code, "name": h["name"], "price": round(price, 2),
                "shares": shares, "amount": round(raw, 2),
                "commission": round(commission, 2), "stamp_tax": round(stamp, 2),
                "slippage": round(slip, 2), "net_amount": round(net, 2),
                "pnl": round(pnl, 2), "pnl_pct": round(pnl_pct, 2),
                "reason": reason,
            }
            self.transactions.append(txn)

            if shares >= h["shares"]:
                del self.holdings[code]
            else:
                h["shares"] -= shares
                h["cost"] -= cost_basis
                h["market_value"] = h["shares"] * price
                h["pnl"] = h["market_value"] - h["cost"]
                h["pnl_pct"] = (h["pnl"] / h["cost"] * 100) if h["cost"] > 0 else 0

            return {"ok": True, "msg": f"卖出 {txn['name']} {shares}股 @{price:.2f} 盈亏¥{pnl:+,.0f}", "txn": txn}

    # ── 市价更新与止损止盈检查 ──

    def update_prices(self, prices: dict[str, float]) -> list[dict]:
        """更新持仓市值，返回触发止损/止盈的持仓列表。"""
        alerts = []
        for code, price in prices.items():
            if code not in self.holdings:
                continue
            h = self.holdings[code]
            h["current_price"] = round(price, 2)
            h["market_value"] = round(h["shares"] * price, 2)
            h["pnl"] = round(h["market_value"] - h["cost"], 2)
            h["pnl_pct"] = round(h["pnl"] / h["cost"] * 100, 2) if h["cost"] > 0 else 0

            if price <= h.get("stop_loss_price", 0):
                alerts.append({"type": "止损", "code": code, "name": h["name"],
                               "pnl_pct": h["pnl_pct"]})
            elif price >= h.get("take_profit_price", 0):
                alerts.append({"type": "止盈", "code": code, "name": h["name"],
                               "pnl_pct": h["pnl_pct"]})
        return alerts

    # ── 汇总 ──

    def get_summary(self) -> dict:
        mv = sum(h.get("market_value", h["cost"]) for h in self.holdings.values())
        total = self.cash + mv
        total_pnl = total - self.initial_capital
        pnl_pct = (total_pnl / self.initial_capital * 100) if self.initial_capital > 0 else 0
        sells = [t for t in self.transactions if t["type"] == "卖出"]
        wins = [t for t in sells if t.get("pnl", 0) > 0]
        return {
            "name": self.name, "initial_capital": self.initial_capital,
            "cash": round(self.cash, 2), "market_value": round(mv, 2),
            "total_assets": round(total, 2),
            "total_pnl": round(total_pnl, 2), "total_pnl_pct": round(pnl_pct, 2),
            "holdings_count": len(self.holdings),
            "total_trades": len(sells), "win_count": len(wins),
            "win_rate": round(len(wins) / len(sells) * 100, 1) if sells else 0,
        }

    def get_holdings_list(self) -> list[dict]:
        items = list(self.holdings.values())
        items.sort(key=lambda h: h.get("market_value", 0), reverse=True)
        return items

    # ── 持久化 ──

    def to_dict(self) -> dict:
        return {
            "name": self.name, "initial_capital": self.initial_capital,
            "cash": self.cash, "created_at": self.created_at,
            "holdings": self.holdings, "transactions": self.transactions,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "VirtualAccount":
        a = cls(name=d.get("name", "V15Plus自动交易"))
        a.initial_capital = d.get("initial_capital", RISK_CONFIG["initial_capital"])
        a.cash = d.get("cash", a.initial_capital)
        a.created_at = d.get("created_at", a.created_at)
        a.holdings = d.get("holdings", {})
        a.transactions = d.get("transactions", [])
        return a

    def save(self, path: str = ACCOUNT_JSON):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, path: str = ACCOUNT_JSON) -> "VirtualAccount":
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return cls.from_dict(json.load(f))
        return cls()

    def _total_assets(self) -> float:
        mv = sum(h.get("market_value", h.get("cost", 0)) for h in self.holdings.values())
        return self.cash + mv


# ══════════════════════════════════════════════════════
# 决策引擎（Prompt构建 + DeepSeek调用 + 解析）
# ══════════════════════════════════════════════════════

def build_trade_prompt(signals: list[dict], account: VirtualAccount, empty_days: int = 0) -> str:
    """构建发送给DeepSeek的交易决策prompt。"""
    today_str = date.today().strftime("%Y-%m-%d %A")
    summary = account.get_summary()
    holdings = account.get_holdings_list()

    # 连空提醒
    empty_note = ""
    if empty_days >= 3:
        empty_note = (
            f"⚠ 你已经连续 {empty_days} 个交易日无任何操作。"
            f"空仓无法验证策略有效性，今天你必须从买入信号中至少选 1-2 只小仓位建仓（100-300股试探）。"
            f"宁可小仓位试错，也不能永远空仓。"
        )

    lines = [
        f"# A股虚拟交易决策 — {today_str}（盘后）",
        "",
        "## 你的角色",
        "你是一个严守纪律的A股量化交易员，根据V15技术评分系统管理一个虚拟投资组合。",
        "你只能基于下面提供的V15评分数据做判断，不要编造任何基本面、资金流、消息面信息。",
        "你的目标：在控制回撤的前提下，稳定跑赢沪深300。",
        "",
        "## 评分等级的正确理解",
        "- V15≥70（强势）：罕见高分信号，出现概率极低（<1%），出现时可重仓",
        "- V15 55-69（偏强）：这是你的主要工作区间，可以用小仓位试探性建仓",
        "- V15 45-54（中性）：观察级，不操作但列入观察列表",
        "- V15 <45（弱势）：禁止持有",
        "- 注意：70分是理想状态不是常态，不能作为唯一开仓条件，否则你永远不会交易",
    ]
    if empty_note:
        lines.append(empty_note)
    lines += [
        "",
        "## 风控铁律（违反任一即不合格）",
        f"- 可用资金 ¥{summary['cash']:,.0f} / 总资产 ¥{summary['total_assets']:,.0f}",
        f"- 单只股票仓位 ≤ {RISK_CONFIG['max_position_pct']*100:.0f}% 总资产",
        f"- 最多同时持有 {RISK_CONFIG['max_holdings']} 只",
        f"- 止损线 {RISK_CONFIG['stop_loss_pct']*100:.0f}%，止盈线 {RISK_CONFIG['take_profit_pct']*100:.0f}%",
        f"- V15评分 ≥ 55（偏强级）才可开仓",
        f"- V15评分 < 45 必须清仓该标的",
        f"- 优先卖出已触发止损/止盈的持仓",
        f"- 小仓位试探优于永远空仓：100股起试，错了止损也无妨",
        "",
        "## 看盘指南",
        "- V15评分 = 四因子综合：反转(抄底信号)/MACD(趋势)/量能(资金)/安全结构(均线)",
        "- 场景分类：超跌反弹(左侧抄底) | 趋势接力(右侧追强) | 突破确认(突破跟进) | 震荡(观望为主)",
        "- 55-69分区间有很多可操作标的，不要因为不到70就全部放弃",
        "",
    ]

    # ── 账户状态 ──
    lines += [
        "## 账户状态",
        f"总资产 ¥{summary['total_assets']:,.0f} | "
        f"现金 ¥{summary['cash']:,.0f} | "
        f"持仓市值 ¥{summary['market_value']:,.0f} | "
        f"累计收益 {summary['total_pnl_pct']:+.2f}% | "
        f"已平仓 {summary['total_trades']}笔 | "
        f"胜率 {summary['win_rate']:.0f}% | "
        f"当前持仓 {summary['holdings_count']}只",
        "",
    ]

    # ── 当前持仓 ──
    if holdings:
        lines.append("## 当前持仓")
        lines.append("| 代码 | 名称 | 成本 | 现价 | 盈亏% | 止损 | 止盈 | 状态 |")
        lines.append("|------|------|------|------|--------|------|------|------|")
        for h in holdings:
            pnl = h.get("pnl_pct", 0)
            cur = h.get("current_price", h["buy_price"])
            sl = h.get("stop_loss_price", 0)
            tp = h.get("take_profit_price", 0)
            status = ""
            if cur <= sl:
                status = "⚠已触发止损"
            elif cur >= tp:
                status = "✓已触发止盈"
            lines.append(
                f"| {h['code']} | {h['name']} | {h['buy_price']:.2f} | {cur:.2f} | "
                f"{pnl:+.1f}% | {sl:.2f} | {tp:.2f} | {status} |"
            )
        lines.append("")

    # ── 买入信号看板 ──
    buy_signals = [s for s in signals if s["level"] in ("强势", "偏强")]
    lines.append(f"## 买入信号（共{len(buy_signals)}只，按评分降序）")
    lines.append("")
    for rank, s in enumerate(buy_signals[:30], 1):
        lines.append(
            f"### {rank}. {s['name']}({s['code']}) — V15 {s['score']:.0f}分 [{s['level']}] "
            f"| 场景: {s.get('scenario','')} | 建议: {s.get('action','')}"
        )
        v15s = s.get("v15_signal", {})
        lines.append(
            f"    四因子: 反转{v15s.get('rev_score',0):.0f} | "
            f"MACD{v15s.get('macd_score',0):.0f} | "
            f"量能{v15s.get('vol_score',0):.0f} | "
            f"安全结构{v15s.get('safe_score',0):.0f}"
        )
        sec = s.get("sector", {})
        if isinstance(sec, dict):
            lines.append(
                f"    板块: {sec.get('sector','—')} | "
                f"热度{sec.get('heat',0):.0f} | 扩散度{sec.get('breadth',0):.0f}"
            )
        pos = s.get("position_advice", {})
        if pos:
            sl = pos.get("stop_loss_pct", 0)
            tp = pos.get("take_profit_1", pos.get("take_profit_pct", 0))
            if sl or tp:
                lines.append(f"    风控参考: 止损-{sl:.1f}% | 止盈+{tp:.1f}%")
        lines.append("")

    # ── 输出格式要求 ──
    lines += [
        "---",
        "",
        "## 你的决策",
        "",
        "请按以下步骤输出：",
        "",
        "**1. 盘面概述**（1-3句）",
        "简述当前信号整体质量和你的操作思路。",
        "",
        "**2. 持仓处理**",
        "逐只检查持仓：是否需要卖出（触发止损止盈/评分下滑/换股优化）。",
        "",
        "**3. 交易指令**（严格按格式，每行一条）",
        "```",
        "BUY|代码|名称|股数|理由",
        "SELL|代码|名称|股数|理由",
        "HOLD|代码|名称|理由",
        "```",
        "- 股数必须是100的整数倍（A股1手=100股）",
        "- SELL时股数写0表示全部卖出",
        "- HOLD表示继续持有不需要操作",
        "- 如果没有需要操作的，写「今日无操作」并说明原因",
    ]

    return "\n".join(lines)


def parse_trade_decisions(ai_text: str, available_codes: set[str]) -> list[dict]:
    """解析DeepSeek回复中的交易指令。返回 [{action, code, name, shares, reason}, ...]"""
    decisions = []
    in_block = False

    for line in ai_text.splitlines():
        s = line.strip()
        if s in ("```", "~~~"):
            in_block = not in_block
            continue
        if not s:
            continue

        m = re.match(
            r'^(BUY|SELL)\s*\|\s*(\d{6})\s*\|\s*(.+?)\s*\|\s*(\d+)\s*\|\s*(.+)$',
            s, re.IGNORECASE)
        if m:
            action = m.group(1).lower()
            code = m.group(2)
            name = m.group(3).strip()
            shares = int(m.group(4))
            reason = m.group(5).strip()
            if available_codes and code not in available_codes:
                continue
            # 股数取整到100的倍数
            shares = max(100, (shares // 100) * 100)
            decisions.append({
                "action": action, "code": code, "name": name,
                "shares": shares, "reason": reason,
            })
            continue

        m = re.match(
            r'^(HOLD)\s*\|\s*(\d{6})\s*\|\s*(.+?)\s*\|\s*(.+)$',
            s, re.IGNORECASE)
        if m:
            decisions.append({
                "action": "hold", "code": m.group(2),
                "name": m.group(3).strip(), "shares": 0,
                "reason": m.group(4).strip(),
            })

    return decisions


# ══════════════════════════════════════════════════════
# 自动交易引擎
# ══════════════════════════════════════════════════════

class AutoTrader:
    """自动虚拟交易引擎 —— 无GUI，命令行运行，每天一次。"""

    def __init__(self, force: bool = False):
        self.force = force
        self.account = VirtualAccount.load(ACCOUNT_JSON)
        self.signals: list[dict] = []
        self.ai_response: str = ""
        self.decisions: list[dict] = []
        self.exec_results: list[dict] = []
        self.log_lines: list[str] = []
        self.today = date.today().strftime("%Y-%m-%d")
        self._api_key: Optional[str] = None
        self._sector_map: dict = {}

    # ── 入口 ──

    def _check_data_freshness(self) -> bool:
        """检查通达信日线数据是否已更新到今日。返回 True=数据新鲜。"""
        from scripts.stock_gui_V14 import load_stock_tdx
        from datetime import datetime as dt

        check_codes = ["999999"]
        latest_dates = []
        for code in check_codes:
            df = load_stock_tdx(code)
            if df is not None and len(df) > 0:
                col = "date" if "date" in df.columns else "日期"
                if col in df.columns:
                    val = df[col].iloc[-1]
                    # pandas Timestamp → YYYYMMDD 字符串
                    latest_dates.append(val.strftime("%Y%m%d")
                                        if hasattr(val, "strftime") else str(val)[:8])

        if not latest_dates:
            self._log("⚠ 无法读取通达信数据，跳过新鲜度检查")
            return True

        data_date = max(latest_dates)
        today_str = date.today().strftime("%Y%m%d")

        if data_date == today_str:
            self._log(f"数据新鲜度 OK (最新日线: {data_date})")
            return True

        wd = date.today().weekday()
        if wd >= 5:
            self._log(f"数据最新: {data_date} (今日非交易日，正常)")
            return True

        self._log(f"⚠ 数据不是最新！最新日线: {data_date}，今日: {today_str}")
        self._log(f"⚠ 请先打开通达信下载今日数据后再运行")
        return False

    def run(self) -> bool:
        self._log(f"{'='*60}")
        self._log(f"V15Plus 自动虚拟交易 — {self.today} {datetime.now().strftime('%H:%M:%S')}")
        self._log(f"{'='*60}")

        # 去重检查
        if not self.force and self._already_ran():
            self._log("今日已执行过，跳过（--now 可强制重跑）")
            self._flush_log()
            return False

        # 0. 数据新鲜度检查
        if not self._check_data_freshness():
            self._log("❌ 日线数据未更新，终止运行")
            self._flush_log()
            return False

        # 1. 加载股票列表
        stocks = self._load_stock_list()
        self._log(f"自选股: {len(stocks)}只")

        # 2. 加载板块缓存
        self._load_sector_cache()

        # 3. 加载 DeepSeek Key
        if not self._load_api_key():
            self._log("❌ DeepSeek API Key 不可用，终止")
            self._flush_log()
            return False

        # 4. 更新持仓市价
        self._log("更新持仓市值...")
        self._update_holdings_prices()

        # 5. 扫描全自选股
        self._log("开始扫描...")
        t0 = time.time()
        self._scan_all(stocks)
        elapsed = time.time() - t0
        strong = sum(1 for s in self.signals if s["level"] == "强势")
        buy_list = [s for s in self.signals if s["level"] == "偏强"]
        self._log(f"扫描完成: {len(self.signals)}只有效 | "
                  f"强势{strong} 偏强{len(buy_list)} | 耗时{elapsed:.1f}s")
        for s in self.signals[:5]:  # Top 5 评分
            self._log(f"  Top: {s.get('code','')} {s.get('name','')} V15={s['score']:.0f} [{s['level']}] "
                      f"场景={s.get('scenario','?')}")
        for s in buy_list:
            self._log(f"  买入: {s.get('code','')} {s.get('name','')} V15={s['score']:.0f} "
                      f"场景={s.get('scenario','?')} close={s.get('_last_close',0):.2f}")

        # 6. AI决策
        self._log("调用 DeepSeek 决策...")
        self._ai_decide()

        # 7. 执行交易
        self._log(f"解析到 {len(self.decisions)} 条决策")
        self._execute_all()

        # 8. 保存
        self.account.save(ACCOUNT_JSON)
        self._mark_run()
        self._log(f"账户已保存 → {ACCOUNT_JSON}")

        # 9. 输出摘要
        self._print_summary()
        self._flush_log()
        return True

    # ── 步骤实现 ──

    def _log(self, msg: str):
        print(msg)
        self.log_lines.append(msg)

    @staticmethod
    def _already_ran() -> bool:
        if not os.path.exists(LAST_RUN_FILE):
            return False
        try:
            with open(LAST_RUN_FILE, "r") as f:
                return f.read().strip() == date.today().strftime("%Y-%m-%d")
        except Exception:
            return False

    @staticmethod
    def _mark_run():
        with open(LAST_RUN_FILE, "w") as f:
            f.write(date.today().strftime("%Y-%m-%d"))

    def _calc_empty_days(self) -> int:
        """计算连续无交易日数（从最后一次有交易到今天的自然日数，近似）。"""
        if not self.account.transactions:
            # 从未交易过，从账户创建日算
            from datetime import date as dt_date
            created = self.account.created_at[:10]  # YYYY-MM-DD
            try:
                cd = dt_date.fromisoformat(created)
                return max(0, (dt_date.today() - cd).days)
            except Exception:
                return 999
        # 最后一条交易的日期
        last_date = self.account.transactions[-1].get("date", "")[:10]
        if not last_date:
            return 0
        from datetime import date as dt_date
        try:
            ld = dt_date.fromisoformat(last_date)
            return max(0, (dt_date.today() - ld).days)
        except Exception:
            return 0

    def _load_stock_list(self) -> list[tuple[str, str]]:
        stocks = []
        if os.path.exists(SELF_STOCK_TXT):
            with open(SELF_STOCK_TXT, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    parts = line.split(None, 1)
                    if len(parts) == 2:
                        stocks.append((parts[0], parts[1]))
        return stocks

    def _load_sector_cache(self):
        try:
            cache_path = os.path.join(_INTERNAL_DIR, "sector_cache.json")
            if os.path.exists(cache_path):
                with open(cache_path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                    # sector_cache.json 结构可能是 {sector_name: {heat, breadth, change...}}
                    # 也可能是 {"data": {sector_name: {...}}, "source": "..."}
                    if isinstance(raw, dict):
                        # 支持两种缓存格式: {"map": {...}} 或直接是板块字典
                        if "map" in raw and isinstance(raw["map"], dict):
                            self._sector_map = raw["map"]
                        elif "data" in raw and isinstance(raw["data"], dict):
                            self._sector_map = raw["data"]
                        else:
                            self._sector_map = raw
                self._log(f"板块缓存: {len(self._sector_map)}个板块")
            else:
                self._sector_map = {}
                self._log("无板块缓存（板块过滤将跳过）")
        except Exception as e:
            self._sector_map = {}
            self._log(f"板块缓存加载失败: {e}")

    def _load_api_key(self) -> bool:
        try:
            from scripts.stock_gui_V14 import load_deepseek_key
            key, source = load_deepseek_key(1)
            if key:
                self._api_key = key
                self._log(f"DeepSeek Key: {source}")
                return True
            self._log(f"DeepSeek Key 未找到: {source}")
            return False
        except Exception as e:
            self._log(f"加载Key失败: {e}")
            return False

    def _update_holdings_prices(self):
        """用最新日线收盘价更新持仓市值。"""
        if not self.account.holdings:
            return
        from scripts.stock_gui_V14 import load_stock_tdx
        prices = {}
        for code in self.account.holdings:
            df = load_stock_tdx(code)
            if df is not None and len(df) > 0:
                col = "收盘" if "收盘" in df.columns else "close"
                prices[code] = float(df[col].iloc[-1])
        if prices:
            alerts = self.account.update_prices(prices)
            for a in alerts:
                self._log(f"  ⚠ {a['type']}: {a['name']}({a['code']}) PnL={a['pnl_pct']:.1f}%")

    def _scan_all(self, stocks: list[tuple[str, str]]):
        from scripts.stock_gui_V14 import load_stock_tdx
        from _plus_.plus_pipeline import compute_plus_pipeline

        results = []
        total = len(stocks)
        done = 0

        def _scan_one(code, name):
            try:
                df = load_stock_tdx(code)
                if df is None or df.empty or len(df) < 120:
                    return None
                # 信号看板同款管线：V15 + 资金流 + 多周期共振 + 情绪量化 + ATR-T1调节
                v15 = compute_plus_pipeline(df, code=code, name=name,
                                            sector_map=self._sector_map if self._sector_map else None,
                                            news_items=None, track=False)
                if not v15.get("available"):
                    return None
                col = "收盘" if "收盘" in df.columns else "close"
                v15["_last_close"] = float(df[col].iloc[-1])
                v15["code"] = code
                v15["name"] = name
                return v15
            except Exception:
                return None

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            futs = {pool.submit(_scan_one, c, n): c for c, n in stocks}
            for f in concurrent.futures.as_completed(futs):
                done += 1
                r = f.result()
                if r:
                    results.append(r)
                if done % 100 == 0:
                    print(f"  扫描进度: {done}/{total}")

        results.sort(key=lambda x: x["score"], reverse=True)
        self.signals = results

    def _ai_decide(self):
        # 计算连续无交易天数
        empty_days = self._calc_empty_days()
        prompt = build_trade_prompt(self.signals, self.account, empty_days)
        available = {s["code"] for s in self.signals}
        available.update(self.account.holdings.keys())

        try:
            from scripts.stock_gui_V14 import call_deepseek_api, audit_ai_output
            raw = call_deepseek_api(prompt, self._api_key, timeout=120)
            audited = audit_ai_output(raw)
            self.ai_response = audited
            self.decisions = parse_trade_decisions(audited, available)
        except Exception as e:
            self.ai_response = f"AI调用异常: {e}"
            self.decisions = []
            self._log(f"❌ AI决策失败: {e}")

    def _execute_all(self):
        for d in self.decisions:
            if d["action"] == "buy":
                price = self._get_close_price(d["code"])
                if price <= 0:
                    self.exec_results.append({"ok": False, "decision": d, "msg": f"{d['code']}无价格"})
                    continue
                result = self.account.buy(d["code"], d["name"], price, d["shares"],
                                          self.today, d["reason"])
                self.exec_results.append(result)
            elif d["action"] == "sell":
                price = self._get_close_price(d["code"])
                if price <= 0:
                    self.exec_results.append({"ok": False, "decision": d, "msg": f"{d['code']}无价格"})
                    continue
                result = self.account.sell(d["code"], price, d["shares"],
                                           self.today, d["reason"])
                self.exec_results.append(result)
            elif d["action"] == "hold":
                self.exec_results.append({"ok": True, "decision": d, "msg": f"持有 {d['name']}"})

    def _get_close_price(self, code: str) -> float:
        """获取某只股票的最新收盘价（优先从扫描结果，其次读TDX）。"""
        for s in self.signals:
            if s.get("code") == code:
                close = s.get("_last_close", 0)
                if close > 0:
                    return close
        # 不在信号列表中的持仓股票，直接读TDX
        from scripts.stock_gui_V14 import load_stock_tdx
        df = load_stock_tdx(code)
        if df is not None and len(df) > 0:
            col = "收盘" if "收盘" in df.columns else "close"
            return float(df[col].iloc[-1])
        return 0

    # ── 输出 ──

    def _print_summary(self):
        s = self.account.get_summary()
        self._log("")
        self._log(f"{'─'*40}")
        self._log(f"账户: {s['name']}")
        self._log(f"总资产 ¥{s['total_assets']:,.0f} | "
                  f"现金 ¥{s['cash']:,.0f} | "
                  f"累计 {s['total_pnl_pct']:+.2f}%")
        self._log(f"持仓 {s['holdings_count']}只 | "
                  f"已平仓 {s['total_trades']}笔 | "
                  f"胜率 {s['win_rate']:.0f}%")

        # AI分析摘要
        self._log("")
        self._log("AI分析:")
        if self.decisions:
            for d in self.decisions:
                self._log(f"  [{d['action'].upper()}] {d.get('name','')}({d.get('code','')}) "
                          f"→ {d.get('reason','')[:120]}")
        elif "无操作" in self.ai_response:
            # 提取"今日无操作"的原因
            for line in self.ai_response.splitlines():
                s_line = line.strip()
                if "无操作" in s_line:
                    self._log(f"  {s_line[:200]}")
                    break
            else:
                self._log("  今日无操作（详见AI回复文件）")

        # 执行结果
        self._log("")
        self._log("执行结果:")
        if self.exec_results:
            for r in self.exec_results:
                ok = "✓" if r.get("ok") else "✗"
                self._log(f"  [{ok}] {r.get('msg', '')}")
        else:
            self._log("  无交易执行")

        # 持仓明细
        if self.account.holdings:
            self._log("")
            self._log("当前持仓:")
            for h in self.account.get_holdings_list():
                self._log(f"  {h['code']} {h['name']} | "
                          f"成本{h['buy_price']:.2f} 现价{h.get('current_price', 0):.2f} | "
                          f"{h.get('pnl_pct', 0):+.1f}% | "
                          f"{h['shares']}股")

    def _flush_log(self):
        """将本次日志写入文件。"""
        log_file = os.path.join(LOG_DIR, f"trade_{self.today}.log")
        with open(log_file, "w", encoding="utf-8") as f:
            f.write("\n".join(self.log_lines))
        print(f"\n日志已写入: {log_file}")

        # 同时更新汇总日志
        summary_file = os.path.join(LOG_DIR, "_summary.log")
        s = self.account.get_summary()
        with open(summary_file, "a", encoding="utf-8") as f:
            f.write(f"{self.today}\t{s['total_assets']:,.0f}\t"
                    f"{s['total_pnl_pct']:+.2f}%\t"
                    f"持仓{s['holdings_count']}\t"
                    f"交易{len(self.exec_results)}笔\n")

        # 保存AI完整回复
        ai_file = os.path.join(LOG_DIR, f"ai_response_{self.today}.txt")
        with open(ai_file, "w", encoding="utf-8") as f:
            f.write(self.ai_response)


# ══════════════════════════════════════════════════════
# 入口
# ══════════════════════════════════════════════════════

if __name__ == "__main__":
    force_run = "--now" in sys.argv or "--force" in sys.argv
    trader = AutoTrader(force=force_run)
    success = trader.run()
    sys.exit(0 if success else 1)
