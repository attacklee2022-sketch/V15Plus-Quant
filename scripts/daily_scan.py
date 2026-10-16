# -*- coding: utf-8 -*-
"""
V15Alpha 通达信每日自动扫描脚本
=================================
遍历自选股全量，跑V15评分+场景引擎+过滤引擎，
输出当天最新信号排名报告。

用法：
    python daily_scan.py              # 全量扫描输出报告
    python daily_scan.py --quick      # 只输出强势/弱势(更快)
    python daily_scan.py --code 600000 # 只扫单只股票

输出：
    V15Alpha/daily_scan_report.txt    # 今日报告（覆盖）
    V15Alpha/daily_scan_history.txt   # 历史追加（可选）
"""
from __future__ import annotations
import os
import sys
import struct
import time
from datetime import datetime

import pandas as pd
import numpy as np

# ── 导入 V15 引擎 ──
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_INTERNAL = os.path.join(_SCRIPT_DIR, "..", "_internal")
if _INTERNAL not in sys.path:
    sys.path.insert(0, _INTERNAL)
from v15_scorer import compute_v15_signal
from v15_scenario_engine import compute_scenario_signal
from v15_filter import compute_filtered_signal

# ── 配置 ──
APP_DIR = os.path.dirname(_SCRIPT_DIR)
TDX_ROOT = r"D:\通达信"
SELF_STOCK_FILE = os.path.join(APP_DIR, "self_stock.txt")
REPORT_FILE = os.path.join(APP_DIR, "daily_scan_report.txt")
HISTORY_FILE = os.path.join(APP_DIR, "daily_scan_history.txt")

# 分数段颜色/分类标签
LEVEL_LABELS = {
    "强势": "[强势]",
    "偏强":   "[偏强]",
    "中性":   "[中性]",
    "弱势":   "[弱势]",
}


# ══════════════════════════════════════════════════
# 1. 通达信日线解析（复用 backtest_all.py 逻辑）
# ══════════════════════════════════════════════════

def _read_tdx_day_file(file_path: str) -> bytes | None:
    for attempt in range(3):
        try:
            with open(file_path, "rb") as f:
                return f.read()
        except PermissionError:
            time.sleep(0.3 * (attempt + 1))
        except Exception:
            raise
    return None


def parse_tdx_day(file_path):
    data = _read_tdx_day_file(file_path)
    if data is None:
        return None
    try:
        row_cnt = len(data) // 32
        rows = []
        for i in range(row_cnt):
            b = data[i * 32: (i + 1) * 32]
            date_code = struct.unpack("<I", b[0:4])[0]
            open_p = struct.unpack("<I", b[4:8])[0] / 100
            high_p = struct.unpack("<I", b[8:12])[0] / 100
            low_p = struct.unpack("<I", b[12:16])[0] / 100
            close_p = struct.unpack("<I", b[16:20])[0] / 100
            vol = struct.unpack("<I", b[24:28])[0]
            rows.append([str(date_code), open_p, high_p, low_p, close_p, vol])

        df = pd.DataFrame(
            rows, columns=["date", "开盘", "最高", "最低", "收盘", "成交量"]
        )
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)
        return df
    except Exception as e:
        print(f"  [ERROR] 解析失败 {file_path}: {e}")
        return None


def get_tdx_file(code):
    if code.startswith(("60", "68")):
        return os.path.join(TDX_ROOT, "vipdoc", "sh", "lday", f"sh{code}.day")
    else:
        return os.path.join(TDX_ROOT, "vipdoc", "sz", "lday", f"sz{code}.day")


def load_stock_tdx(code):
    fp = get_tdx_file(code)
    if not os.path.exists(fp):
        return None
    return parse_tdx_day(fp)


# ══════════════════════════════════════════════════
# 2. 单只股票扫描
# ══════════════════════════════════════════════════

def scan_stock(code: str, name: str) -> dict | None:
    """
    扫描单只股票，返回最新信号字典。
    返回 None 表示数据不足或解析失败。
    """
    df = load_stock_tdx(code)
    if df is None or df.empty or len(df) < 80:
        return None

    closes = df["收盘"].values.tolist()
    highs = df["最高"].values.tolist()
    lows = df["最低"].values.tolist()
    volumes = df["成交量"].values.tolist()
    last_close = closes[-1]
    last_vol = volumes[-1]
    last_date = str(df["date"].iloc[-1].date())

    # ── V15评分引擎 ──
    try:
        v15 = compute_v15_signal(closes, highs, lows, volumes, last_close, last_vol)
    except Exception:
        return None

    # ── 场景引擎 ──
    try:
        sce = compute_scenario_signal(closes, highs, lows, volumes, last_close, last_vol)
    except Exception:
        sce = {"score": 50, "level": "中性", "scenario": "未知"}

    # ── 过滤引擎 ──
    try:
        fil = compute_filtered_signal(closes, highs, lows, volumes, last_close, last_vol, code=code, name=name)
    except Exception:
        fil = {"level": "中性", "score": 50, "facts": ["过滤引擎异常"]}

    return {
        "code": code,
        "name": name[:12],  # 截断长名
        "date": last_date,
        "close": last_close,
        "volume": last_vol,
        "v15_score": v15.get("score", 50),
        "v15_level": v15.get("level", "中性"),
        "scenario_score": sce.get("score", 50),
        "scenario_name": sce.get("scenario", "未知"),
        "scenario_level": sce.get("level", "中性"),
        "filtered_score": fil.get("score", 50),
        "filtered_level": fil.get("level", "中性"),
        "resonance_consistency": fil.get("resonance", {}).get("consistency", "—"),
        "resonance_direction": fil.get("resonance", {}).get("direction", "—"),
        "divergence_type": fil.get("momentum_divergence", {}).get("type", "无"),
        "divergence_adj": fil.get("momentum_divergence", {}).get("adjustment", 0),
        "atr_norm": fil.get("volatility", {}).get("atr_norm", 0),
        "filter_adj": fil.get("filter_adjustment", 0),
        # 子因子
        "rev_score": v15.get("rev_score", 0),
        "macd_score": v15.get("macd_score", 0),
        "vol_score": v15.get("vol_score", 0),
        "safe_score": v15.get("safe_score", 0),
        # 过滤说明
        "facts": fil.get("facts", []),
    }


# ══════════════════════════════════════════════════
# 3. 报告格式化
# ══════════════════════════════════════════════════

def fmt_pct(v):
    """格式化百分比，带+号"""
    if v > 0:
        return f"+{v:.2f}%"
    elif v < 0:
        return f"{v:.2f}%"
    return "0.00%"


def fmt_score(r):
    """格式化一行信号"""
    div_type = r["divergence_type"]
    div_mark = ""
    if "顶背离" in div_type:
        div_mark = " [顶背离]"
    elif "底背离" in div_type:
        div_mark = " [底背离]"
    elif "隐藏顶" in div_type:
        div_mark = " [隐顶]"
    elif "隐藏底" in div_type:
        div_mark = " [隐底]"

    res = r["resonance_consistency"]
    res_mark = ""
    if res == "一致":
        res_mark = " [一致]"
    elif res == "分歧偏空":
        res_mark = " [分歧偏空]"

    return (
        f"  {r['code']} {r['name']:<12s}"
        f" 分{r['filtered_score']:5.0f}"
        f" {r['scenario_name']:<6s}"
        f" 评分({r['rev_score']:.0f}/{r['macd_score']:.0f}/{r['vol_score']:.0f}/{r['safe_score']:.0f})"
        f"{res_mark}{div_mark}"
    )


def build_report(results: list[dict], elapsed: float) -> str:
    """构建完整报告文本"""
    lines = []
    now = datetime.now()
    lines.append("=" * 92)
    lines.append(f"  V15Alpha 通达信每日自动扫描报告")
    lines.append(f"  扫描时间：{now:%Y-%m-%d %H:%M:%S}")
    lines.append(f"  有效股票：{len(results)} 只")
    lines.append(f"  耗时：{elapsed:.1f}s")
    lines.append("=" * 92)
    lines.append("")

    # 按分数降序排列
    sorted_r = sorted(results, key=lambda r: -r["filtered_score"])

    # ── 分类统计 ──
    strong = [r for r in sorted_r if r["filtered_level"] == "强势"]
    buy = [r for r in sorted_r if r["filtered_level"] == "偏强"]
    watch = [r for r in sorted_r if r["filtered_level"] == "中性"]
    avoid = [r for r in sorted_r if r["filtered_level"] == "弱势"]

    # 双确认（强势 + 场景评分≥68）
    dual_confirm = [r for r in strong if r["scenario_score"] >= 68]
    filter_lift = [r for r in strong if r["scenario_score"] < 68]

    lines.append(f"[汇总] 强势 {len(strong)} | 偏强 {len(buy)} | 中性 {len(watch)} | 弱势 {len(avoid)}")
    lines.append(f"       其中双确认(过滤+场景均强) {len(dual_confirm)} | 过滤提升 {len(filter_lift)}")
    lines.append("")

    # ── 🟢 双确认（最优先） ──
    if dual_confirm:
        lines.append("━" * 92)
        lines.append(f"  [双确认] 过滤强势 + 场景评分>=68 优先关注")
        lines.append("━" * 92)
        lines.append("  {:8s} {:12s} {:>6s} {:8s} {:28s} {:4s} {:s}".format('代码','名称','过滤分','场景','子因子(反/MACD/量/安)','共振','背离'))
        lines.append("  " + "-" * 84)
        for r in dual_confirm:
            lines.append(fmt_score(r))
        lines.append("")

    # ── 过滤提升强势 ──
    if filter_lift:
        lines.append("━" * 92)
        lines.append(f"  [过滤提升] 场景<68但过滤后强势")
        lines.append("━" * 92)
        lines.append("  {:8s} {:12s} {:>6s} {:8s} {:28s} {:4s} {:s}".format('代码','名称','过滤分','场景','子因子(反/MACD/量/安)','共振','背离'))
        lines.append("  " + "-" * 84)
        for r in filter_lift:
            lines.append(fmt_score(r))
        lines.append("")

    # ── 🔵 偏强 ──
    if buy:
        lines.append("━" * 92)
        lines.append(f"  [买入信号]")
        lines.append("━" * 92)
        lines.append("  {:8s} {:12s} {:>6s} {:8s} {:28s} {:4s} {:s}".format('代码','名称','过滤分','场景','子因子(反/MACD/量/安)','共振','背离'))
        lines.append("  " + "-" * 84)
        for r in buy:
            lines.append(fmt_score(r))
        lines.append("")

    # ── 🔴 弱势（含背离注释） ──
    if avoid:
        lines.append("━" * 92)
        lines.append(f"  [弱势信号]（含顶背离标记）")
        lines.append("━" * 92)
        lines.append("  {:8s} {:12s} {:>6s} {:8s} {:28s} {:4s} {:s}".format('代码','名称','过滤分','场景','子因子(反/MACD/量/安)','共振','背离'))
        lines.append("  " + "-" * 84)
        for r in avoid[:20]:  # 弱势最多显示20条
            lines.append(fmt_score(r))
        if len(avoid) > 20:
            lines.append(f"  ... 还有 {len(avoid) - 20} 只弱势信号，详见历史")
        lines.append("")

    # ── 顶背离一览（独立模块） ──
    top_divs = [r for r in sorted_r if "顶背离" in r["divergence_type"]]
    if top_divs:
        lines.append("━" * 92)
        lines.append(f"  [顶背离] 动量顶背离股票（{len(top_divs)}只）— 历史中位-4.32%，需警惕")
        lines.append("━" * 92)
        for r in top_divs:
            lines.append(f"  {r['code']} {r['name']:<12s} 过滤分{r['filtered_score']:5.0f} {r['divergence_type']}")
        lines.append("")

    # ── 底背离一览 ──
    bottom_divs = [r for r in sorted_r if "底背离" in r["divergence_type"] and "隐藏" not in r["divergence_type"]]
    if bottom_divs:
        lines.append("━" * 92)
        lines.append(f"  [底背离] 动量底背离股票（{len(bottom_divs)}只）— 历史中位+2.41%，可关注反转机会")
        lines.append("━" * 92)
        for r in bottom_divs:
            lines.append(f"  {r['code']} {r['name']:<12s} 过滤分{r['filtered_score']:5.0f} {r['divergence_type']}")
        lines.append("")

    # ── 极速排名（Top 15 全量排名） ──
    lines.append("━" * 92)
    lines.append(f"  [排名] 全量排名 Top 20")
    lines.append("━" * 92)
    lines.append("  {:>3s} {:8s} {:12s} {:>6s} {:6s} {:8s} {:6s} {:12s}".format('#','代码','名称','过滤分','等级','场景','共振','背离'))
    lines.append("  " + "-" * 84)
    for i, r in enumerate(sorted_r[:20]):
        lvl = r["filtered_level"]
        lvl_mark = LEVEL_LABELS.get(lvl, lvl)
        lines.append(
            f"  {i+1:3d} {r['code']:<8s} {r['name']:<12s}"
            f" {r['filtered_score']:5.0f} {lvl_mark:<6s}"
            f" {r['scenario_name']:<8s}"
            f" {r['resonance_consistency'][:4]:<6s}"
            f" {r['divergence_type']:<12s}"
        )
    lines.append("")

    # ── 统计报表 ──
    lines.append("━" * 92)
    lines.append(f"  [统计] 分数段统计")
    lines.append("━" * 92)
    segments = [
        (">=80(强势)", [r for r in sorted_r if r["filtered_score"] >= 80]),
        ("68-80(较优)",  [r for r in sorted_r if 68 <= r["filtered_score"] < 80]),
        ("58-68(偏强)",  [r for r in sorted_r if 58 <= r["filtered_score"] < 68]),
        ("48-58(中性)",  [r for r in sorted_r if 48 <= r["filtered_score"] < 58]),
        ("40-48(边缘)",  [r for r in sorted_r if 40 <= r["filtered_score"] < 48]),
        ("<40(弱势)",    [r for r in sorted_r if r["filtered_score"] < 40]),
    ]
    for seg_name, seg_list in segments:
        lines.append(f"  {seg_name:<16s} {len(seg_list):3d} 只")

    lines.append("")
    lines.append("=" * 92)
    lines.append(f"  — 报告完毕 {now:%Y-%m-%d %H:%M:%S} —")
    lines.append("=" * 92)
    return "\n".join(lines)


# ══════════════════════════════════════════════════
# 4. 主流程
# ══════════════════════════════════════════════════

def main():
    quick_mode = "--quick" in sys.argv
    single_code = None
    for arg in sys.argv:
        if arg.startswith("--code="):
            single_code = arg.split("=", 1)[1]

    # 读取自选股
    if not os.path.exists(SELF_STOCK_FILE):
        print(f"[ERROR] 自选股文件不存在: {SELF_STOCK_FILE}")
        sys.exit(1)

    stocks = []
    with open(SELF_STOCK_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split(" ", 1)
            code = parts[0]
            name = parts[1] if len(parts) > 1 else code
            if single_code and code != single_code:
                continue
            stocks.append((code, name))

    if single_code:
        print(f"[SCAN] 单只扫描: {single_code}")
    else:
        print(f"[SCAN] 自选股总数：{len(stocks)}")
        if quick_mode:
            print("[SCAN] 快速模式")

    results = []
    t0 = time.time()
    ok = 0
    fail = 0

    for idx, (code, name_full) in enumerate(stocks):
        if (idx + 1) % 50 == 0 or idx == 0:
            elapsed = time.time() - t0
            print(f"  进度 {idx+1}/{len(stocks)} | 成功{ok} 失败{fail} | 耗时{elapsed:.0f}s")
            sys.stdout.flush()

        r = scan_stock(code, name_full)
        if r is None:
            fail += 1
            continue
        results.append(r)
        ok += 1

    elapsed = time.time() - t0
    print(f"\n[SCAN] 完成！有效{ok}/{len(stocks)}，耗时{elapsed:.0f}s")

    if not results:
        print("[SCAN] 无有效数据")
        return

    # 生成报告
    report = build_report(results, elapsed)
    print("\n" + report)

    # 写入报告
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"\n[OK] 报告已写入: {REPORT_FILE}")

    # 追加历史（精简行）
    now = datetime.now()
    summary = (
        f"{now:%Y-%m-%d %H:%M} "
        f"强势{len([r for r in results if r['filtered_level']=='强势'])} "
        f"偏强{len([r for r in results if r['filtered_level']=='偏强'])} "
        f"弱势{len([r for r in results if r['filtered_level']=='弱势'])} "
        f"有效{ok}/{len(stocks)} "
        f"{elapsed:.0f}s\n"
    )
    with open(HISTORY_FILE, "a", encoding="utf-8") as f:
        f.write(summary)

    # 快速模式只输出关键列表
    if quick_mode:
        top_buy = [r for r in results if r["filtered_level"] == "强势"]
        top_buy.sort(key=lambda r: -r["filtered_score"])
        print(f"\n{'='*60}")
        print(f"  强势列表（{len(top_buy)} 只）")
        print(f"{'='*60}")
        for r in top_buy:
            div_info = f" [{r['divergence_type']}]" if r['divergence_type'] != "无" else ""
            print(f"  {r['code']} {r['name']:<12s} 分{r['filtered_score']:.0f} 场景{r['scenario_name']}{div_info}")


if __name__ == "__main__":
    main()
