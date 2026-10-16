# -*- coding: utf-8 -*-
"""
择时雷达 AI 解读模块
======================
把 V15Plus 全链路量化数据喂给 DeepSeek，生成 readable 的解读报告。
"""
import requests
import os
import struct
from datetime import datetime

DEEPSEEK_KEY = os.getenv("DEEPSEEK_KEY", "")
TDX_ROOT = os.environ.get("TDX_ROOT_PATH", r"D:\通达信")

# ── 辅助：读取通达信数据算技术指标 ──

def _load_tdx_df(code):
    """加载通达信日线，返回 DataFrame 或 None"""
    if code.startswith(("60", "68")):
        fp = os.path.join(TDX_ROOT, "vipdoc", "sh", "lday", f"sh{code}.day")
    else:
        fp = os.path.join(TDX_ROOT, "vipdoc", "sz", "lday", f"sz{code}.day")
    if not os.path.exists(fp):
        return None
    for _ in range(3):
        try:
            with open(fp, "rb") as f:
                data = f.read()
            break
        except PermissionError:
            import time
            time.sleep(0.3)
        except Exception:
            return None
    else:
        return None
    try:
        row_cnt = len(data) // 32
        rows = []
        for i in range(row_cnt):
            b = data[i * 32: (i + 1) * 32]
            import struct
            date_code = struct.unpack("<I", b[0:4])[0]
            open_p = struct.unpack("<I", b[4:8])[0] / 100
            high_p = struct.unpack("<I", b[8:12])[0] / 100
            low_p = struct.unpack("<I", b[12:16])[0] / 100
            close_p = struct.unpack("<I", b[16:20])[0] / 100
            vol = struct.unpack("<I", b[24:28])[0]
            rows.append([str(date_code), open_p, high_p, low_p, close_p, vol])
        import pandas as pd
        df = pd.DataFrame(rows, columns=["date", "开盘", "最高", "最低", "收盘", "成交量"])
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)
        return df
    except Exception:
        return None


def _calc_tech_indicators(code):
    """计算技术指标，返回 dict"""
    df = _load_tdx_df(code)
    if df is None or df.empty or len(df) < 60:
        return {}
    cc = list(df["收盘"])
    hh = list(df["最高"])
    ll = list(df["最低"])
    vv = list(df["成交量"])
    lc = cc[-1]
    n = len(cc)

    m5 = sum(cc[-5:]) / 5 if n >= 5 else 0
    m10 = sum(cc[-10:]) / 10 if n >= 10 else 0
    m20 = sum(cc[-20:]) / 20 if n >= 20 else 0
    m60 = sum(cc[-60:]) / 60 if n >= 60 else 0

    # 均线形态
    if m5 and m10 and m20:
        if m5 > m10 > m20:
            ma_pat = "多头排列"
        elif m5 < m10 < m20:
            ma_pat = "空头排列"
        elif m5 < m10 and m10 > m20:
            ma_pat = "短期死叉"
        elif m5 > m10 and m10 < m20:
            ma_pat = "短期金叉"
        else:
            ma_pat = "震荡交织"
    else:
        ma_pat = "数据不足"

    bias = (lc - m5) / m5 * 100 if m5 else 0
    avgv = sum(vv[-21:-1]) / 20 if n >= 21 else 1
    vr = round(vv[-1] / avgv, 2) if avgv > 0 else 1.0
    ampl = (hh[-1] - ll[-1]) / lc * 100 if lc else 0

    # 量能趋势
    if n >= 11:
        v5 = sum(vv[-5:]) / 5
        v10 = sum(vv[-10:]) / 10
        if v5 > v10 * 1.2:
            vol_trend = "放量"
        elif v5 < v10 * 0.8:
            vol_trend = "缩量"
        else:
            vol_trend = "平量"
    else:
        vol_trend = "数据不足"

    return {
        "ma5": round(m5, 2), "ma10": round(m10, 2), "ma20": round(m20, 2), "ma60": round(m60, 2),
        "ma_pattern": ma_pat,
        "last_close": lc,
        "bias": round(bias, 2),
        "volume_ratio": vr,
        "amplitude": round(ampl, 2),
        "vol_trend": vol_trend,
        "support": round(min(m5, m10, m20), 2) if m5 and m10 and m20 else round(lc * 0.95, 2),
        "resistance": round(max(m5, m10, m20), 2) if m5 and m10 and m20 else round(lc * 1.05, 2),
    }


# ── 评分定性 ──

_LABELS = [
    ("极高", 90), ("很高", 75), ("偏高", 60), ("中性", 45),
    ("偏低", 30), ("很低", 15), ("极低", 0),
]


def _label(s):
    for l, t in _LABELS:
        if s >= t:
            return l
    return "未知"


# ── Prompt 构建 ──

_SYSTEM_PROMPT = """你是一位专业的 A 股投资分析师，负责解读 V15Plus 量化择时雷达的全链路数据。

## 输出要求
1. **结论先行** — 第一行给出评级（强势/偏强/中性/观望/弱势）和一句话核心判断
2. **推理透明** — 每个判断引用具体数据，让读者看到推理过程
3. **矛盾必须解释** — 当不同模块信号不一致时（如V15看空但过滤看多），必须明确指出矛盾并分析
4. **操作区分** — 分别给空仓者和持仓者具体的、可执行的操作建议，包含具体价位
5. **诚实严谨** — 数据不足如实标注，不编造
6. **格式** — 纯 Markdown，不要 JSON，不要代码块

## 输出结构
```
📡 择时雷达 — {股票名称}({股票代码})

{评级} {评分} | {场景} | {置信度}

## 评分解读
（分析四因子贡献、评分路径中的关键转折点、核心矛盾）

## 趋势研判
（均线形态、多周期共振、背离信号的综合分析）

## 风险与催化
（主要风险点 + 积极因素，分条列出）

## 板块与环境
（板块动力、资金流、大盘背景的综合判断）

## 操作建议
（分空仓/持仓，含具体价位）

## 关键价位
（支撑、压力、止损位一览）
```"""


def _build_prompt(data: dict) -> str:
    """构建 prompt"""
    lines = []
    lines.append(f"## 基本信息")
    lines.append(f"股票: {data['name']}({data['code']}) | 当前价: {data['price']}")
    lines.append(f"")

    # V15
    lines.append(f"## V15 量化评分")
    lines.append(f"综合: {data['v15_score']:.0f}/100 - {data['v15_level']}")
    lines.append(f"四因子: 反转{data['rev_score']:.0f}({_label(data['rev_score'])}) | "
                 f"MACD{data['macd_score']:.0f}({_label(data['macd_score'])}) | "
                 f"量能{data['vol_score']:.0f}({_label(data['vol_score'])}) | "
                 f"安全{data['safe_score']:.0f}({_label(data['safe_score'])})")
    lines.append(f"场景: {data.get('scenario','未知')} | 场景调整后: {data.get('scenario_score',0):.0f}/100")
    lines.append(f"过滤后: {data['filtered_score']:.0f}/100 - {data['filtered_level']} | 调整: {data['filter_adj']:+.0f}")
    lines.append(f"")

    # 技术面
    tech = data.get('tech', {})
    if tech:
        lines.append(f"## 技术指标")
        lines.append(f"均线: MA5={tech['ma5']:.2f} MA10={tech['ma10']:.2f} MA20={tech['ma20']:.2f} MA60={tech['ma60']:.2f}")
        lines.append(f"均线形态: {tech['ma_pattern']} | 乖离率(MA5): {tech['bias']:+.2f}%")
        lines.append(f"量比: {tech['volume_ratio']:.2f} | 量能趋势: {tech['vol_trend']} | 振幅: {tech['amplitude']:.2f}%")
        lines.append(f"支撑: {tech['support']:.2f} | 压力: {tech['resistance']:.2f}")
        lines.append(f"")

    # 共振与背离
    res_cons = data.get('resonance_consistency', '-')
    res_scores = data.get('resonance_scores', {})
    res_str = " | ".join(f"{k}日:{v:.0f}" for k, v in sorted(res_scores.items())) if res_scores else "-"
    lines.append(f"多周期共振: {res_cons} ({res_str})")
    div_type = data.get('divergence_type', '无')
    div_adj = data.get('divergence_adj', 0)
    lines.append(f"背离检测: {div_type} (调整: {div_adj:+.0f})")
    lines.append(f"波动率ATR: {data.get('atr_norm', 0):.2f}% | 波动调整: {data.get('vol_adjustment', 0):+.0f}")
    lines.append(f"")

    # 扩展五维
    extras = data.get('extras_detail', {}) or {}
    extra_facts = []
    for k in ['relative_strength', 'risk_reward', 'intraday', 'cross_market', 'market_regime']:
        m = extras.get(k, {})
        for f in m.get('facts', []):
            extra_facts.append(f)
    if extra_facts:
        lines.append(f"## 扩展过滤 (调整: {data.get('extras_adj',0):+.0f})")
        for f in extra_facts:
            lines.append(f"- {f}")
        lines.append(f"")

    # Plus+
    plus_facts = data.get('plus_facts', [])
    if plus_facts:
        lines.append(f"## Plus+ 增强")
        for f in plus_facts:
            lines.append(f"- {f}")
        lines.append(f"")

    # 板块
    sec = data.get('sector', {})
    if sec and sec.get('sector_name'):
        lines.append(f"## 板块")
        lines.append(f"板块: {sec['sector_name']} | 热度: {sec.get('sector_heat',0):.0f} | 调整: {sec.get('sector_adj',0):+.0f}")
        sp = data.get('sector_power', {})
        if sp:
            lines.append(f"板块动力: {sp.get('name', '-')} {sp.get('tag', '-')} ({sp.get('score', 0):.0f}分)")
            lines.append(f"T1资金占比: {sp.get('t1', 0):.0f}分 | T2量能: {sp.get('t2', 0):.0f}分 | 赔率: x{sp.get('mult', 1.0):.2f}")
        lines.append(f"")

    # 仓位
    pos = data.get('position', {})
    if pos:
        lines.append(f"## 仓位建议")
        lines.append(f"操作: {pos.get('action','观望')}")
        lines.append(f"理由: {pos.get('reason','')}")
        lines.append(f"仓位: {pos.get('suggestion','-')}")
        if pos.get('stop_loss'):
            lines.append(f"止损: {pos['stop_loss']:.2f} ({pos.get('stop_loss_pct',0):+.1f}%)")
        if pos.get('tp1'):
            lines.append(f"止盈一: {pos['tp1']:.2f}")
        if pos.get('tp2'):
            lines.append(f"止盈二: {pos['tp2']:.2f}")
        lines.append(f"")

    return "\n".join(lines)


# ── 主入口 ──

def generate_radar_report(t1_widget, data: dict):
    """在后台线程中生成 AI 解读并插入到 t1_widget。"""
    import threading

    def _worker():
        # 添加技术指标
        if 'tech' not in data:
            data['tech'] = _calc_tech_indicators(data.get('code', ''))

        prompt = _build_prompt(data)
        try:
            resp = requests.post(
                "https://api.deepseek.com/chat/completions",
                headers={"Authorization": f"Bearer {DEEPSEEK_KEY}",
                         "Content-Type": "application/json"},
                json={
                    "model": "deepseek-chat",
                    "messages": [
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.7,
                    "max_tokens": 2500,
                },
                timeout=90,
            )
            if resp.status_code == 200:
                report = resp.json()["choices"][0]["message"]["content"]
                content = "\n\n" + "─" * 50 + "\n🤖 AI 综合解读\n" + "─" * 50 + "\n\n" + report + "\n"
                t1_widget.after(0, lambda t=content: t1_widget.insert("end", t))
                t1_widget.after(0, lambda: t1_widget.see("end"))
            else:
                t1_widget.after(0, lambda: t1_widget.insert("end",
                    f"\n[AI解读] API请求失败: {resp.status_code}\n"))
        except Exception as e:
            t1_widget.after(0, lambda: t1_widget.insert("end",
                f"\n[AI解读] 异常: {e}\n"))

        t1_widget.after(0, lambda: t1_widget.see("end"))

    threading.Thread(target=_worker, daemon=True).start()
