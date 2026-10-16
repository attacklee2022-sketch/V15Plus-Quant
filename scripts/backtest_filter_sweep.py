# -*- coding: utf-8 -*-
"""
V15Alpha 过滤引擎参数敏感性扫描
================================
每个股票只加载一次，在同一次遍历中对所有参数组合求值。
对每个交易日：先用基线算 → 如果≥80分就对所有候选配置跑一遍。
这样大幅减少重复IO和计算。

使用方式:
    python backtest_filter_sweep.py

输出: V15Alpha\filter_sweep_report.txt
"""

from __future__ import annotations
import os
import sys
import time
from datetime import datetime
from collections import defaultdict

import numpy as np
import pandas as pd

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_SCRIPT_DIR, "..", "_internal"))
import v15_filter
from v15_filter import compute_filtered_signal, MULTI_PERIOD_CONFIG, DIVERGENCE_CONFIG, VOLATILITY_CONFIG
from backtest_all import load_stock_tdx

# ── 配置 ──
APP_DIR = os.path.dirname(_SCRIPT_DIR)
SELF_STOCK_FILE = os.path.join(APP_DIR, "self_stock.txt")
FORWARD_DAYS = [5, 10, 20, 60]
PROFIT_THRESHOLD = 0.03
USE_STOCKS = 80

# ============================================================
# 扫描定义: (组名, 参数名, [候选值])
# ============================================================
SWEEPS = [
    ("共振", "pulse_threshold", [1.10, 1.15, 1.20, 1.25]),
    ("共振", "downgrade_gap", [8, 10, 12, 15]),
    ("共振", "upgrade_bonus", [6, 8, 10, 12]),
    ("背离", "top_div_penalty", [10, 12, 15, 18]),
    ("背离", "bottom_div_bonus", [6, 8, 10, 12]),
    ("波动", "high_vol_threshold", [3.5, 4.0, 4.5, 5.0]),
    ("波动", "high_vol_penalty", [15, 18, 20, 25]),
    ("波动", "low_vol_threshold", [0.5, 0.6, 0.8, 1.0]),
    ("波动", "low_vol_penalty", [6, 8, 10, 12]),
    ("波动", "volume_surge_penalty", [8, 10, 12, 15]),
]

# 基线值（用于对比）
BASELINE_VALS = {
    "pulse_threshold": 1.15,
    "downgrade_gap": 10,
    "upgrade_bonus": 8,
    "top_div_penalty": 12,
    "bottom_div_bonus": 8,
    "high_vol_threshold": 4.0,
    "high_vol_penalty": 18,
    "low_vol_threshold": 0.6,
    "low_vol_penalty": 8,
    "volume_surge_penalty": 12,
}

# 参数→组名映射
PARAM_GROUP = {p: g for g, p, _ in SWEEPS}


def make_cfg(group: str, param: str, val) -> dict:
    """在当前模块全局配置基础上，只改一个参数。"""
    cfg = {
        "resonance": dict(MULTI_PERIOD_CONFIG),
        "divergence": dict(DIVERGENCE_CONFIG),
        "volatility": dict(VOLATILITY_CONFIG),
    }
    cfg[group][param] = val
    return cfg


def agg_metrics(rets: list) -> dict:
    n = len(rets)
    if n < 5:
        return {}
    mean_r = np.mean(rets) * 100
    med_r = np.median(rets) * 100
    wr = sum(1 for x in rets if x >= PROFIT_THRESHOLD) / n * 100
    std_r = np.std(rets)
    sharpe = (np.mean(rets) / std_r * np.sqrt(252)) if std_r > 1e-8 else 0
    return {
        "cnt": n,
        "mean": round(mean_r, 2),
        "median": round(med_r, 2),
        "wr": round(wr, 2),
        "sharpe": round(sharpe, 2),
    }


# ============================================================
# 主流程
# ============================================================

def main():
    # 读自选股
    stocks = []
    with open(SELF_STOCK_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split(" ", 1)
            stocks.append((parts[0], line))
    stocks = stocks[:USE_STOCKS]

    n_configs = sum(len(v) for _, _, v in SWEEPS)
    print(f"[SWEEP] {len(stocks)}只股票 | {len(SWEEPS)}参数 | {n_configs}候选值")

    # 初始化累加器
    # accum[(group, param, val)] = {fd: [forward_returns]}
    accum = {}
    for group, param, values in SWEEPS:
        for v in values:
            accum[(group, param, v)] = {fd: [] for fd in FORWARD_DAYS}

    # 基线累加器
    base_accum = {fd: [] for fd in FORWARD_DAYS}

    # 预创建所有配置
    all_configs = []
    for group, param, values in SWEEPS:
        cfg_list = [(group, param, v, make_cfg(
            {"共振": "resonance", "背离": "divergence", "波动": "volatility"}[group],
            param, v)) for v in values]
        all_configs.append(cfg_list)

    t0 = time.time()
    stock_ok = 0

    for si, (code, name_full) in enumerate(stocks):
        if (si + 1) % 20 == 0 or si == 0:
            print(f"  进度 {si+1}/{len(stocks)} | 成功{stock_ok} | "
                  f"耗时{time.time()-t0:.0f}s")

        df = load_stock_tdx(code)
        if df is None or df.empty or len(df) < 80:
            continue

        n = len(df)
        max_fwd = max(FORWARD_DAYS)

        closes = df["收盘"].values.tolist()
        highs = df["最高"].values.tolist()
        lows = df["最低"].values.tolist()
        volumes = df["成交量"].values.tolist()

        stock_ok += 1

        # 遍历历史每个交易日（取模3减少计算量）
        for i in range(60, n - max_fwd, 3):
            c = closes[:i+1]
            h = highs[:i+1]
            l_ = lows[:i+1]
            v = volumes[:i+1]
            cc, cv = closes[i], volumes[i]

            # 先算基线
            try:
                sig = compute_filtered_signal(c, h, l_, v, cc, cv)
            except Exception:
                continue

            # 只在≥80分的行继续（节省95%计算量）
            if sig["score"] < 80:
                continue

            # 记录基线
            for fd in FORWARD_DAYS:
                fwd_ret = (closes[i+fd] - cc) / cc
                base_accum[fd].append(fwd_ret)

            # 对每个候选配置计算
            for cfg_list in all_configs:
                for group, param, val, cfg in cfg_list:
                    try:
                        sig2 = compute_filtered_signal(c, h, l_, v, cc, cv, config=cfg)
                    except Exception:
                        continue
                    if sig2["score"] >= 80:
                        for fd in FORWARD_DAYS:
                            fwd_ret = (closes[i+fd] - cc) / cc
                            accum[(group, param, val)][fd].append(fwd_ret)

    # ── 输出报告 ──
    lines = []
    lines.append(f"{'='*100}")
    lines.append(f"  过滤引擎参数敏感性扫描报告")
    lines.append(f"  生成时间: {datetime.now():%Y-%m-%d %H:%M:%S}")
    lines.append(f"  样本股票: {stock_ok}/{len(stocks)} 只")
    lines.append(f"{'='*100}")

    # 基线结果
    base60 = agg_metrics(base_accum[60])
    if base60:
        lines.append(f"\n【基线】当前v15_filter默认参数")
        lines.append(f"  60日≥80分: 信号数{base60['cnt']} 中位{base60['median']}% "
                     f"均值{base60['mean']}% 胜率{base60['wr']}% 夏普{base60['sharpe']}")
    else:
        lines.append(f"\n【基线】信号不足,检查数据")
        lines.append(f"  5日:{len(base_accum[5])} 10日:{len(base_accum[10])} "
                     f"20日:{len(base_accum[20])} 60日:{len(base_accum[60])}")

    # 逐参数扫描结果（只看60日）
    best_improvements = []

    lines.append(f"\n{'='*100}")
    lines.append(f"  单变量扫描结果（60日后收益,≥80分组）")
    lines.append(f"{'='*100}")

    for group, param, values in SWEEPS:
        lines.append(f"\n── {group}.{param} (基线={BASELINE_VALS[param]}) ──")
        hdr = f"  {'值':<8} {'信号数':>7} {'中位%':>9} {'均值%':>9} {'胜率%':>9} {'夏普':>7}"
        lines.append(hdr)
        lines.append("  " + "-" * 55)

        for v in values:
            m = agg_metrics(accum[(group, param, v)][60])
            if m:
                vs_base = (m["median"] - base60["median"]) if base60 else 0
                flag = " ★" if vs_base > 0.5 else ""
                lines.append(
                    f"  {str(v):<8} {m['cnt']:>7} {m['median']:>8.2f}% "
                    f"{m['mean']:>8.2f}% {m['wr']:>8.2f}% {m['sharpe']:>6.2f}"
                    f"{flag}"
                )
                if vs_base > 0.5 and m["sharpe"] >= base60["sharpe"] * 0.95:
                    best_improvements.append({
                        "group": group, "param": param, "val": v,
                        "median": m["median"], "sharpe": m["sharpe"],
                        "wr": m["wr"], "vs_base_mid": vs_base,
                    })
            else:
                lines.append(f"  {str(v):<8} {'-':>7} {'-':>9} {'-':>9} {'-':>9} {'-':>7}")

    # 最优汇总
    best_improvements.sort(key=lambda x: -x["median"])
    lines.append(f"\n{'='*100}")
    lines.append(f"  推荐调优参数")
    lines.append(f"{'='*100}")
    if best_improvements:
        lines.append(f"  {'参数':<25} {'推荐值':<10} {'60日中位%':<10} {'60日夏普':<8} {'vs基线中位':<12}")
        lines.append("  " + "-" * 60)
        for bi in best_improvements[:10]:
            bl = BASELINE_VALS[bi["param"]]
            lines.append(
                f"  {bi['group']}.{bi['param']:<16} {str(bi['val']):<10} "
                f"{bi['median']:<10.2f} {bi['sharpe']:<8.2f} "
                f"{bi['vs_base_mid']:+.2f}%"
            )
    else:
        lines.append("  本次扫描未发现显著优于基线的参数组合。")

    report = "\n".join(lines)
    report_path = os.path.join(APP_DIR, "filter_sweep_report.txt")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"\n[SWEEP] OK 报告已写入: {report_path}")
    print(report[:2000])
    print("...")
    print(report[-1500:])

    return report


if __name__ == "__main__":
    main()
