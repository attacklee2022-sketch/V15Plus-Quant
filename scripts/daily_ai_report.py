# -*- coding: utf-8 -*-
"""
V15Plus AI 增强报告
====================
全A股批量扫描 → 排除自选股 → Top N 生成 AI 投资分析报告。

用法:
    python scripts/daily_ai_report.py --top 5         # 全A股排除自选股后，Top 5 生成AI报告（默认）
    python scripts/daily_ai_report.py --top 10 --no-ai # 只看排名，不调AI
    python scripts/daily_ai_report.py --code 600519   # 只看单只
    python scripts/daily_ai_report.py --include-self  # 包含自选股（不过滤）

输出:
    data/ai_report_{date}.md
"""
from __future__ import annotations
import os, sys, json, time
from datetime import datetime
from typing import Optional

import pandas as pd
import numpy as np

# ── V15Plus 路径 ──
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.dirname(_SCRIPT_DIR)
_INTERNAL = os.path.join(_APP_DIR, "_internal")
_PLUS = os.path.join(_APP_DIR, "_plus_")
for p in [_INTERNAL, _PLUS, _APP_DIR]:
    if p not in sys.path:
        sys.path.insert(0, p)

from v15_scorer import compute_v15_signal
from v15_scenario_engine import compute_scenario_signal
from v15_filter import compute_filtered_signal

# ── 配置 ──
TDX_ROOT = os.environ.get("TDX_ROOT_PATH", r"D:\通达信")
SELF_STOCK_FILE = os.path.join(_APP_DIR, "self_stock.txt")
REPORT_DIR = os.path.join(_APP_DIR, "data")
os.makedirs(REPORT_DIR, exist_ok=True)

# DeepSeek API
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_KEY", "")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-chat"

# ── 分数段标签 ──
LEVEL_LABELS = {
    "强势": "强势",
    "偏强":   "偏强",
    "中性":   "中性",
    "弱势":   "弱势",
}


# ══════════════════════════════════════════════════
# 1. 数据加载 — 全A股 + 排除自选股
# ══════════════════════════════════════════════════

def read_tdx_day(file_path: str) -> Optional[pd.DataFrame]:
    """读取通达信 .day 文件"""
    import struct
    for attempt in range(3):
        try:
            with open(file_path, "rb") as f:
                data = f.read()
            break
        except PermissionError:
            time.sleep(0.3 * (attempt + 1))
        except Exception:
            return None
    else:
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

        df = pd.DataFrame(rows, columns=["date", "开盘", "最高", "最低", "收盘", "成交量"])
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)
        return df
    except Exception:
        return None


def get_tdx_file(code: str) -> str:
    if code.startswith(("60", "68")):
        return os.path.join(TDX_ROOT, "vipdoc", "sh", "lday", f"sh{code}.day")
    else:
        return os.path.join(TDX_ROOT, "vipdoc", "sz", "lday", f"sz{code}.day")


def load_stock_data(code: str) -> Optional[pd.DataFrame]:
    fp = get_tdx_file(code)
    if not os.path.exists(fp):
        return None
    return read_tdx_day(fp)


def enumerate_all_a_shares() -> list[tuple[str, str]]:
    """枚举全A股代码（从通达信日线目录），返回 [(code, name), ...]"""
    from _plus_.stock_names import build_name_cache
    print("  正在枚举全A股...")
    all_codes = set()
    tdx_prefixes = [("sh", "60"), ("sh", "68"), ("sz", "00"), ("sz", "30")]
    for market, prefix in tdx_prefixes:
        lday_dir = os.path.join(TDX_ROOT, "vipdoc", market, "lday")
        if not os.path.isdir(lday_dir):
            continue
        for fname in os.listdir(lday_dir):
            if fname.startswith(f"{market}{prefix}") and fname.endswith(".day"):
                code = fname[2:8]
                if code.isdigit() and len(code) == 6:
                    all_codes.add(code)

    print(f"  全A股池: {len(all_codes)} 只，正在加载名称缓存...")
    names = build_name_cache()
    result = [(code, names.get(code, code)) for code in sorted(all_codes)]
    print(f"  名称加载完成")
    return result


def load_self_stock_codes() -> set[str]:
    """加载自选股代码集合（用于排除）"""
    codes = set()
    if not os.path.exists(SELF_STOCK_FILE):
        return codes
    with open(SELF_STOCK_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                code = line.split(" ", 1)[0].strip()
                if code:
                    codes.add(code)
    return codes


# ══════════════════════════════════════════════════
# 2. 技术指标计算（补充 V15 未直接输出的）
# ══════════════════════════════════════════════════

def _ma(vals: list[float], n: int) -> Optional[float]:
    if len(vals) < n:
        return None
    return sum(vals[-n:]) / n


def compute_technicals(df: pd.DataFrame) -> dict:
    """计算补充技术指标"""
    closes = df["收盘"].values.tolist()
    highs = df["最高"].values.tolist()
    lows = df["最低"].values.tolist()
    volumes = df["成交量"].values.tolist()
    n = len(closes)

    result = {}

    result["ma5"] = _ma(closes, 5)
    result["ma10"] = _ma(closes, 10)
    result["ma20"] = _ma(closes, 20)
    result["ma60"] = _ma(closes, 60)

    last_close = closes[-1]

    # 均线形态
    ma5, ma10, ma20 = result["ma5"], result["ma10"], result["ma20"]
    if ma5 and ma10 and ma20:
        if ma5 > ma10 > ma20:
            result["ma_pattern"] = "多头排列"
            result["ma_pattern_detail"] = f"MA5({ma5:.2f}) > MA10({ma10:.2f}) > MA20({ma20:.2f})"
        elif ma5 < ma10 < ma20:
            result["ma_pattern"] = "空头排列"
            result["ma_pattern_detail"] = f"MA5({ma5:.2f}) < MA10({ma10:.2f}) < MA20({ma20:.2f})"
        elif ma5 < ma10 and ma10 > ma20:
            result["ma_pattern"] = "短期死叉"
            result["ma_pattern_detail"] = f"MA5({ma5:.2f}) < MA10({ma10:.2f}) > MA20({ma20:.2f})，短期走弱中期仍强"
        elif ma5 > ma10 and ma10 < ma20:
            result["ma_pattern"] = "短期金叉"
            result["ma_pattern_detail"] = f"MA5({ma5:.2f}) > MA10({ma10:.2f}) < MA20({ma20:.2f})，短期反弹但中期承压"
        else:
            result["ma_pattern"] = "震荡交织"
            result["ma_pattern_detail"] = f"MA5({ma5:.2f}) / MA10({ma10:.2f}) / MA20({ma20:.2f})，均线交织无明确方向"
    else:
        result["ma_pattern"] = "数据不足"
        result["ma_pattern_detail"] = ""

    # 乖离率
    if ma5 and ma5 != 0:
        result["bias_ma5"] = (last_close - ma5) / ma5 * 100
    else:
        result["bias_ma5"] = None

    # 量比
    if n >= 21:
        avg_vol_20 = sum(volumes[-21:-1]) / 20
        result["volume_ratio"] = round(volumes[-1] / avg_vol_20, 2) if avg_vol_20 > 0 else 1.0
    else:
        result["volume_ratio"] = 1.0

    result["turnover_rate"] = None

    # 振幅
    if n >= 1:
        result["amplitude"] = (highs[-1] - lows[-1]) / closes[-1] * 100
    else:
        result["amplitude"] = 0

    # 支撑/压力
    if ma5 and ma10 and ma20:
        result["support"] = min(ma5, ma10, ma20) if result["ma_pattern"] != "空头排列" else last_close * 0.95
        result["resistance"] = max(ma5, ma10, ma20)
    else:
        result["support"] = last_close * 0.95
        result["resistance"] = last_close * 1.05

    # 量能趋势
    if n >= 11:
        vol_5 = sum(volumes[-5:]) / 5
        vol_10 = sum(volumes[-10:]) / 10
        if vol_5 > vol_10 * 1.2:
            result["vol_trend"] = "放量"
        elif vol_5 < vol_10 * 0.8:
            result["vol_trend"] = "缩量"
        else:
            result["vol_trend"] = "平量"
    else:
        result["vol_trend"] = "数据不足"

    return result


# ══════════════════════════════════════════════════
# 3. 数据聚合
# ══════════════════════════════════════════════════

_SCORE_THRESHOLDS = [
    ("极低", 0), ("很低", 15), ("偏低", 30), ("中性", 45),
    ("偏高", 60), ("很高", 75), ("极高", 90),
]


def _score_desc(s: float) -> str:
    for label, threshold in reversed(_SCORE_THRESHOLDS):
        if s >= threshold:
            return label
    return "未知"


def build_stock_data(code: str, name: str) -> Optional[dict]:
    """采集一只股票的完整数据"""
    df = load_stock_data(code)
    if df is None or df.empty or len(df) < 60:
        return None

    closes = df["收盘"].values.tolist()
    highs = df["最高"].values.tolist()
    lows = df["最低"].values.tolist()
    volumes = df["成交量"].values.tolist()
    last_close = closes[-1]
    last_vol = volumes[-1]
    last_date = str(df["date"].iloc[-1].date())

    try:
        v15 = compute_v15_signal(closes, highs, lows, volumes, last_close, last_vol)
    except Exception as e:
        return None

    try:
        sce = compute_scenario_signal(closes, highs, lows, volumes, last_close, last_vol)
    except Exception:
        sce = {"score": 50, "level": "中性", "scenario": "未知", "scenario_data": {}}

    try:
        fil = compute_filtered_signal(closes, highs, lows, volumes, last_close, last_vol, code=code, name=name)
    except Exception:
        fil = {"level": "中性", "score": 50, "facts": ["过滤引擎异常"],
               "resonance": {}, "momentum_divergence": {}, "volatility": {}}

    tech = compute_technicals(df)

    position_text = ""
    try:
        from v15_position_manager import compute_position_advice_from_signal
        pos = compute_position_advice_from_signal(fil, last_close)
        if pos:
            pos_pct = pos.get("建议仓位", pos.get("position_pct", ""))
            stop_loss = pos.get("止损价", pos.get("stop_loss", ""))
            take_profit = pos.get("止盈价", pos.get("take_profit", ""))
            position_text = f"建议仓位: {pos_pct}"
            if stop_loss:
                position_text += f" | 止损: {stop_loss}"
            if take_profit:
                position_text += f" | 止盈: {take_profit}"
    except Exception:
        position_text = "仓位建议计算失败"

    return {
        "code": code,
        "name": name,
        "date": last_date,
        "close": last_close,
        "volume": last_vol,
        "v15_score": v15.get("score", 50),
        "v15_level": v15.get("level", "中性"),
        "rev_score": v15.get("rev_score", 0),
        "macd_score": v15.get("macd_score", 0),
        "vol_score": v15.get("vol_score", 0),
        "safe_score": v15.get("safe_score", 0),
        "scenario": sce.get("scenario", "未知"),
        "scenario_score": sce.get("score", 50),
        "scenario_level": sce.get("level", "中性"),
        "filtered_score": fil.get("score", 50),
        "filtered_level": fil.get("level", "中性"),
        "resonance": fil.get("resonance", {}),
        "divergence": fil.get("momentum_divergence", {}),
        "atr_norm": fil.get("volatility", {}).get("atr_norm", 0),
        "vol_adjustment": fil.get("volatility", {}).get("vol_adjustment", 0),
        "filter_adj": fil.get("filter_adjustment", 0),
        "facts": fil.get("facts", []),
        "ma5": tech.get("ma5"),
        "ma10": tech.get("ma10"),
        "ma20": tech.get("ma20"),
        "ma60": tech.get("ma60"),
        "ma_pattern": tech.get("ma_pattern", "未知"),
        "ma_pattern_detail": tech.get("ma_pattern_detail", ""),
        "bias_ma5": tech.get("bias_ma5"),
        "volume_ratio": tech.get("volume_ratio", 1.0),
        "amplitude": tech.get("amplitude", 0),
        "vol_trend": tech.get("vol_trend", "未知"),
        "support": tech.get("support"),
        "resistance": tech.get("resistance"),
        "position_text": position_text,
    }


# ══════════════════════════════════════════════════
# 4. Prompt + DeepSeek
# ══════════════════════════════════════════════════

SYSTEM_PROMPT = """你是一位专业的 A 股投资分析师。你将收到一只股票的完整量化评分数据和技术指标，请生成一份结构化的投资分析报告。

## 输出要求
1. **结论先行** — 报告开头第一行就给出评级（买入/关注/观望/回避）和一句话核心结论
2. **推理透明** — 每个判断都要引用数据，让读者看到"为什么"
3. **操作区分** — 分别给空仓者和持仓者具体的操作建议
4. **诚实严谨** — 数据不足时如实标注，不编造
5. **格式** — 纯 Markdown，不要 JSON，不要代码块

## 报告结构（依次输出）
### 一、量化评分总览
### 二、技术面解析
### 三、风险与催化
### 四、操作建议
### 五、检查清单"""


def build_prompt(s: dict) -> str:
    resonance = s.get("resonance", {})
    divergence = s.get("divergence", {})
    resonance_text = f"一致性: {resonance.get('consistency', '—')} | 方向: {resonance.get('direction', '—')}"
    div_type = divergence.get("type", "无")
    div_adj = divergence.get("adjustment", 0)
    divergence_text = f"类型: {div_type} | 调整值: {div_adj:+.1f}"
    facts_text = "\n".join(f"- {f}" for f in s.get("facts", []))

    return f"""请分析以下股票数据并生成投资分析报告：

## 基本信息
股票: {s['name']} ({s['code']})
分析日期: {s['date']}
当前价: {s['close']:.2f}

## V15 量化评分（经回测验证的四因子模型）
综合评分: {s['v15_score']:.1f}/100 — {s['v15_level']}

四因子评分:
| 因子 | 评分 | 含义 |
|------|------|------|
| 反转(均值回归) | {s['rev_score']:.1f}/100 | {_score_desc(s['rev_score'])} |
| MACD(趋势动量) | {s['macd_score']:.1f}/100 | {_score_desc(s['macd_score'])} |
| 量能(量价配合) | {s['vol_score']:.1f}/100 | {_score_desc(s['vol_score'])} |
| 安全结构(支撑) | {s['safe_score']:.1f}/100 | {_score_desc(s['safe_score'])} |

场景识别: {s['scenario']}（场景调整后评分: {s['scenario_score']:.1f}）

## 过滤引擎输出
过滤后评分: {s['filtered_score']:.1f}/100 — {s['filtered_level']}
多周期共振: {resonance_text}
动量背离: {divergence_text}
波动率(ATR): {s['atr_norm']:.2f} | 波动调整: {s['vol_adjustment']:+.1f}
综合调整: {s['filter_adj']:+.1f}

## 技术指标
| 指标 | 数值 |
|------|------|
| MA5 | {s['ma5']:.2f} |
| MA10 | {s['ma10']:.2f} |
| MA20 | {s['ma20']:.2f} |
| MA60 | {s['ma60']:.2f} |
| 均线形态 | {s['ma_pattern']} |
| 乖离率(MA5) | {s['bias_ma5']:+.2f}% |
| 量比 | {s['volume_ratio']:.2f} |
| 振幅 | {s['amplitude']:.2f}% |
| 量能趋势 | {s['vol_trend']} |
| 支撑位 | {s['support']:.2f} |
| 压力位 | {s['resistance']:.2f} |

## 过滤说明
{facts_text}

## 仓位建议（凯利分配）
{s['position_text']}"""


def call_deepseek(prompt: str) -> Optional[str]:
    from openai import OpenAI
    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
    try:
        resp = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.7,
            max_tokens=2000,
            timeout=60,
        )
        return resp.choices[0].message.content
    except Exception as e:
        print(f"    [API ERROR] {e}")
        return None


# ══════════════════════════════════════════════════
# 5. 格式化输出（不调AI时用）
# ══════════════════════════════════════════════════

def format_stock_data_only(s: dict) -> str:
    lvl_mark = LEVEL_LABELS.get(s.get("filtered_level", ""), s.get("filtered_level", ""))
    lines = []
    lines.append(f"## {lvl_mark} {s['name']}({s['code']})")
    lines.append(f"")
    lines.append(f"| 维度 | 数据 |")
    lines.append(f"|------|------|")
    lines.append(f"| **V15评分** | {s['v15_score']:.1f}/100 -> {s['v15_level']} |")
    lines.append(f"| **四因子** | 反转{s['rev_score']:.0f} / MACD{s['macd_score']:.0f} / 量能{s['vol_score']:.0f} / 安全{s['safe_score']:.0f} |")
    lines.append(f"| **场景** | {s['scenario']} ({s['scenario_score']:.1f}) |")
    lines.append(f"| **均线** | {s['ma_pattern']} |")
    lines.append(f"| **量比/量能** | {s['volume_ratio']:.2f} / {s['vol_trend']} |")
    lines.append(f"| **共振/背离** | {s.get('resonance',{}).get('consistency','—')} / {s.get('divergence',{}).get('type','无')} |")
    lines.append(f"| **当前价** | {s['close']:.2f}（支撑{s['support']:.2f} / 压力{s['resistance']:.2f}）|")
    facts = s.get("facts", [])
    if facts:
        lines.append(f"")
        lines.append(f"**过滤说明:**")
        for f in facts:
            lines.append(f"- {f}")
    lines.append(f"")
    lines.append(f"**仓位建议:** {s.get('position_text', '无')}")
    lines.append(f"")
    return "\n".join(lines)


# ══════════════════════════════════════════════════
# 6. 主流程
# ══════════════════════════════════════════════════

def main():
    import argparse
    parser = argparse.ArgumentParser(description="V15Plus AI 增强报告（全A股批量）")
    parser.add_argument("--top", type=int, default=5, help="排除自选股后，取评分最高的 N 只生成 AI 报告（默认 5）")
    parser.add_argument("--code", type=str, default="", help="只看单只股票")
    parser.add_argument("--include-self", action="store_true", help="包含自选股，不过滤（默认排除）")
    parser.add_argument("--no-ai", action="store_true", help="不调用 AI，仅输出排名+数据摘要")
    args = parser.parse_args()

    # ── 获取目标股票列表 ──
    if args.code:
        code = args.code.strip()
        from _plus_.stock_names import build_name_cache
        names = build_name_cache()
        name = names.get(code, code)
        target_stocks = [(code, name)]
        print(f"[INFO] 单只模式: {name}({code})")
    else:
        market_stocks = enumerate_all_a_shares()
        if not args.include_self:
            excluded = load_self_stock_codes()
            before = len(market_stocks)
            market_stocks = [(c, n) for c, n in market_stocks if c not in excluded]
            print(f"  排除自选股 {len(excluded)} 只，剩余 {len(market_stocks)}/{before} 只")
        target_stocks = market_stocks

    if not target_stocks:
        print("[ERROR] 无待分析股票")
        return

    print(f"[INFO] 开始扫描 {len(target_stocks)} 只股票...")

    # ── 扫描 ──
    results = []
    t0 = time.time()
    for idx, (code, name) in enumerate(target_stocks):
        if (idx + 1) % 100 == 0:
            et = time.time() - t0
            print(f"  进度 {idx+1}/{len(target_stocks)} | 已采集 {len(results)} 只 | 耗时 {et:.0f}s")
            sys.stdout.flush()
        s = build_stock_data(code, name)
        if s:
            results.append(s)

    elapsed = time.time() - t0
    print(f"[INFO] 扫描完成: 有效 {len(results)}/{len(target_stocks)}，耗时 {elapsed:.0f}s")

    if not results:
        print("[ERROR] 无有效数据")
        return

    results.sort(key=lambda r: -r["filtered_score"])

    top_count = args.top if args.top > 0 else len(results)
    ai_results = results[:top_count]
    print(f"[INFO] 全量排名已出，AI 报告范围: Top {len(ai_results)}（评分 {ai_results[-1]['filtered_score']:.0f} ~ {ai_results[0]['filtered_score']:.0f}）")

    # ── 生成报告 ──
    now = datetime.now()
    date_str = now.strftime("%Y%m%d")
    lines = []
    lines.append("# V15Plus 量化决策报告")
    lines.append("")
    report_source = "全A股（排除自选股）" if not args.include_self and not args.code else ("全A股（含自选股）" if not args.code else f"单只 {args.code}")
    lines.append(f"> 生成时间: {now:%Y-%m-%d %H:%M} | 扫描范围: {report_source}")
    lines.append(f"> 扫描: {len(results)} 只有效 | AI报告: {len(ai_results)} 只 | 模型: {DEEPSEEK_MODEL}")
    lines.append("")

    strong = sum(1 for r in results if r["filtered_level"] == "强势")
    buy = sum(1 for r in results if r["filtered_level"] == "偏强")
    watch = sum(1 for r in results if r["filtered_level"] == "中性")
    avoid = sum(1 for r in results if r["filtered_level"] == "弱势")
    lines.append(f"**信号汇总:** 强势 {strong} | 偏强 {buy} | 中性 {watch} | 弱势 {avoid}")
    lines.append("")

    # 排名表
    lines.append("---")
    lines.append(f"## 评分排名（全量 {len(results)} 只）")
    lines.append("")
    lines.append("| # | 代码 | 名称 | 评分 | 等级 | 场景 | 均线形态 | 共振 |")
    lines.append("|---|------|------|------|------|------|---------|------|")
    for i, r in enumerate(results[:20]):
        lvl = LEVEL_LABELS.get(r["filtered_level"], r["filtered_level"])
        res_cons = r.get("resonance", {}).get("consistency", "—")
        lines.append(f"| {i+1} | {r['code']} | {r['name']} | {r['filtered_score']:.0f} | {lvl} | {r['scenario']} | {r['ma_pattern']} | {res_cons} |")
    lines.append("")

    # AI 深度报告
    if not args.no_ai and ai_results:
        lines.append("---")
        lines.append(f"## AI 深度分析（Top {len(ai_results)}）")
        lines.append("")
        for idx, s in enumerate(ai_results):
            lines.append("---")
            lines.append("")
            print(f"  [{idx+1}/{len(ai_results)}] {s['name']}({s['code']}) 调用 AI...")
            report_text = call_deepseek(build_prompt(s))
            if report_text:
                lines.append(report_text)
            else:
                lines.append(format_stock_data_only(s))
            lines.append("")
    elif args.no_ai:
        lines.append("---")
        lines.append("## 数据详情（Top 20）")
        lines.append("")
        for s in results[:20]:
            lines.append(format_stock_data_only(s))

    report_path = os.path.join(REPORT_DIR, f"ai_report_{date_str}.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    summary = f"[OK] 报告已生成: {report_path}  |  有效 {len(results)} 只  |  强势 {strong}  偏强 {buy}  中性 {watch}  弱势 {avoid}"
    print(f"\n{summary}")
    print("\n[Top 10 新发现]:")
    print("  #  code     name        score level  scenario")
    for i, r in enumerate(results[:10]):
        print(f"  {i+1}. {r['code']} {r['name']:<10s} {r['filtered_level']}  score={r['filtered_score']:.0f}  scenario={r['scenario']}")


if __name__ == "__main__":
    main()
