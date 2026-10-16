# -*- coding: utf-8 -*-
"""AI报告模块 - 供 stock_gui_V14.py 调用。独立的文件避免转义问题。"""
import os
import sys
import threading
import requests

DEEPSEEK_KEY = os.getenv("DEEPSEEK_KEY", "")


def run_ai_report(t1_widget, self_obj, scan_rows):
    """在后台线程中运行AI分析，结果插入到 t1 文本框。"""
    # 加载自选股列表（只分析自选股池的票）
    self_codes = set()
    app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sfp = os.path.join(app_dir, "self_stock.txt")
    if os.path.exists(sfp):
        with open(sfp, encoding="utf-8") as f:
            for ln in f:
                if ln.strip():
                    c = ln.split(" ", 1)[0].strip()
                    if c:
                        self_codes.add(c)

    cand = [r for r in scan_rows if r.get("status") == "ok" and r.get("code") in self_codes]
    cand.sort(key=lambda r: -r.get("score", 0))
    ai = cand[:5]
    if not ai:
        t1_widget.after(0, lambda: t1_widget.insert("end", "  [AI] 无符合条件的标的\n"))
        return

    t1_widget.after(0, lambda n=len(ai): t1_widget.insert("end", "\n[AI深度分析] 排除" + str(len(excluded)) + "只, Top" + str(n) + "新发现...\n"))

    def worker():
        for idx, r in enumerate(ai):
            code = r["code"]
            name = r["name"]
            sc = r.get("score", 0)
            t1_widget.after(0, lambda c=code, n=name, s=sc, i=idx:
                t1_widget.insert("end", "\n[" + str(i+1) + "] " + n + "(" + c + ") 评分" + str(s) + "...\n"))
            t1_widget.after(0, lambda: t1_widget.see("end"))
            try:
                _analyze_one(t1_widget, code, name)
            except Exception:
                pass
        t1_widget.after(0, lambda: t1_widget.insert("end", "\n-- AI分析完成 --\n"))

    threading.Thread(target=worker, daemon=True).start()


def _analyze_one(t1_widget, code, name):
    """单只股票调用 DeepSeek 生成 AI 报告"""
    # 延迟导入以避免循环依赖
    from scripts.stock_gui_V14 import load_stock_tdx
    from v15_scorer import compute_v15_signal as cv
    from v15_scenario_engine import compute_scenario_signal as ce
    from v15_filter import compute_filtered_signal as cf

    df = load_stock_tdx(code)
    if df is None or df.empty or len(df) < 60:
        return

    cc = list(df["收盘"])
    hh = list(df["最高"])
    ll = list(df["最低"])
    vv = list(df["成交量"])
    lc = cc[-1]

    v15 = cv(cc, hh, ll, vv, lc, vv[-1])
    sce = ce(cc, hh, ll, vv, lc, vv[-1])
    fil = cf(cc, hh, ll, vv, lc, vv[-1], code=code, name=name)

    n = len(cc)
    m5 = sum(cc[-5:])/5 if n >= 5 else 0
    m10 = sum(cc[-10:])/10 if n >= 10 else 0
    m20 = sum(cc[-20:])/20 if n >= 20 else 0
    bias_str = format((lc-m5)/m5*100, "+.2f") + "%" if m5 else "N/A"
    avgv = sum(vv[-21:-1])/20 if n >= 21 else 1
    vr_str = format(round(vv[-1]/avgv, 2), ".2f") if avgv > 0 else "1.00"

    p = "股票:" + name + "(" + code + ")\n"
    p += "V15评分:" + format(v15["score"], ".0f") + "/100 " + v15["level"] + "\n"
    p += "反转:" + format(v15["rev_score"], ".0f") + " MACD:" + format(v15["macd_score"], ".0f") + " 量能:" + format(v15["vol_score"], ".0f") + " 安全:" + format(v15["safe_score"], ".0f") + "\n"
    p += "场景:" + str(sce.get("scenario", "-")) + " 过滤:" + format(fil["score"], ".0f") + "/100 " + fil["level"] + "\n"
    p += "MA5=" + format(m5, ".2f") + " MA10=" + format(m10, ".2f") + " MA20=" + format(m20, ".2f") + "\n"
    p += "乖离率:" + bias_str + " 量比:" + vr_str + "\n"
    p += "\n按Markdown输出: 结论(评级+一句话) -> 评分总览 -> 技术面 -> 风险催化 -> 操作(分空仓/持仓)"

    resp = requests.post(
        "https://api.deepseek.com/chat/completions",
        headers={"Authorization": "Bearer " + DEEPSEEK_KEY, "Content-Type": "application/json"},
        json={
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": "专业A股分析师,结论先行,推理透明,纯Markdown"},
                {"role": "user", "content": p},
            ],
            "temperature": 0.7,
            "max_tokens": 2000,
        },
        timeout=60,
    )
    if resp.status_code == 200:
        rpt = resp.json()["choices"][0]["message"]["content"]
        t1_widget.after(0, lambda t=rpt: t1_widget.insert("end", t + "\n"))
