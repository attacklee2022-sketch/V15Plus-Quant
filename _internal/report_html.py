#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HTML 报告渲染模块。run.py 和 server.py 共用。"""

import os, json
from datetime import datetime

HTML_HEAD = '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0,maximum-scale=1.0,user-scalable=no">
<title>盘面分析 · {date}</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;background:#f5f5f5;color:#222;font-size:16px;line-height:1.9}}
.header{{background:#1a1a2e;color:#fff;padding:14px;text-align:center;position:sticky;top:0;z-index:10}}
.header h1{{font-size:17px;font-weight:600}}
.header .sub{{font-size:12px;color:#999;margin-top:3px}}
.container{{padding:10px}}
.section{{background:#fff;border-radius:8px;padding:12px 14px;margin-bottom:8px}}
.section-title{{font-size:16px;font-weight:600;color:#1a1a2e;margin-bottom:8px;padding-bottom:5px;border-bottom:1px solid #eee}}
.section-text{{font-size:15px;color:#333;line-height:1.9;white-space:pre-wrap;word-break:break-word}}
.badge{{display:inline-block;padding:2px 10px;border-radius:4px;font-size:14px;font-weight:600}}
.bull{{background:#e8f5e9;color:#2e7d32}}
.bear{{background:#fbe9e7;color:#c62828}}
.neutral{{background:#fff3e0;color:#e65100}}
.tbl-wrap{{overflow-x:auto;-webkit-overflow-scrolling:touch;margin-top:6px}}
.tbl{{width:100%;border-collapse:collapse;font-size:14px;min-width:600px}}
.tbl th{{background:#f8f9fa;padding:8px 6px;text-align:center;font-weight:600;color:#555;font-size:13px;border-bottom:2px solid #e0e0e0;white-space:nowrap}}
.tbl td{{padding:8px 6px;text-align:center;border-bottom:1px solid #f0f0f0;font-size:13px;white-space:nowrap}}
.tbl .l{{text-align:left;font-weight:500;white-space:normal}}
.tbl .up{{color:#c62828}} .tbl .down{{color:#2e7d32}}
.tbl .in{{color:#c62828;font-weight:500}} .tbl .out{{color:#2e7d32;font-weight:500}}
.v15{{display:inline-block;background:#f0f0f5;color:#555;padding:1px 6px;border-radius:3px;font-size:12px;font-weight:500}}
.subtitle{{font-size:15px;font-weight:500;margin:12px 0 6px;color:#444}}
.sbox{{display:flex;gap:8px;margin-top:8px}}
.sitem{{flex:1;background:#f8f9fc;border-radius:6px;padding:10px;text-align:center}}
.sitem .l{{font-size:12px;color:#888}} .sitem .v{{font-size:16px;font-weight:600;margin-top:3px}} .sitem .d{{font-size:12px;color:#888;margin-top:3px;line-height:1.5}}
.news-t{{display:inline-block;padding:2px 8px;border-radius:3px;font-size:13px;margin-right:4px}}
.news-p{{background:#e8f5e9;color:#2e7d32}} .news-n{{background:#fbe9e7;color:#c62828}}
.risk{{background:#fff8e1;border-left:3px solid #f9a825;padding:10px 12px;border-radius:6px;font-size:15px;color:#555;line-height:1.8}}
.footer{{text-align:center;padding:16px;font-size:11px;color:#999}}
@media(max-width:480px){{body{{font-size:15px}}.container{{padding:8px}}.section{{padding:10px 12px}}.section-text{{font-size:14px}}.tbl{{font-size:13px;min-width:520px}}}}
</style>
</head>
<body>
<div class="header"><h1>盘面分析报告</h1><div class="sub">{date} {time} · {status}</div></div>
<div class="container">{body}</div>
<div class="footer">V15Plus 量化选股 · {date}</div>
</body>
</html>'''

SECTOR_TABLE_ROW = '<tr><td class="l">{name}</td><td class="{chg_cls}">{chg}</td><td class="{flow_cls}">{main}</td><td>{sl}</td><td>{lg}</td><td>{md}</td><td>{sm}</td><td><span class="v15">{v15}</span></td></tr>'


def _sector_rows(sectors, top_n=5):
    """渲染板块表格行，取前 top_n 流入 + 前 top_n 流出。"""
    sorted_s = sorted(sectors, key=lambda x: -x.get("main_inflow", 0))
    in_rows = []
    for s in sorted_s[:top_n]:
        chg = s.get("change_pct", 0)
        main = s.get("main_inflow", 0) / 1e8
        name = s.get("name", "")
        in_rows.append(SECTOR_TABLE_ROW.format(
            name=name, chg_cls="up" if chg > 0 else "down",
            chg=f"{chg:+.2f}%", flow_cls="in" if main > 0 else "out",
            main=f"{main:+.1f}亿",
            sl=f"{s.get('super_large_inflow',0)/1e8:+.1f}亿" if s.get("super_large_inflow") else "0",
            lg=f"{s.get('large_inflow',0)/1e8:+.1f}亿" if s.get("large_inflow") else "0",
            md=f"{s.get('mid_inflow',0)/1e8:+.1f}亿" if s.get("mid_inflow") else "0",
            sm=f"{s.get('small_inflow',0)/1e8:+.1f}亿" if s.get("small_inflow") else "0",
            v15="-"))
    out_rows = []
    for s in sorted_s[-top_n:]:
        chg = s.get("change_pct", 0)
        main = s.get("main_inflow", 0) / 1e8
        name = s.get("name", "")
        out_rows.append(SECTOR_TABLE_ROW.format(
            name=name, chg_cls="up" if chg > 0 else "down",
            chg=f"{chg:+.2f}%", flow_cls="in" if main > 0 else "out",
            main=f"{main:+.1f}亿",
            sl=f"{s.get('super_large_inflow',0)/1e8:+.1f}亿" if s.get("super_large_inflow") else "0",
            lg=f"{s.get('large_inflow',0)/1e8:+.1f}亿" if s.get("large_inflow") else "0",
            md=f"{s.get('mid_inflow',0)/1e8:+.1f}亿" if s.get("mid_inflow") else "0",
            sm=f"{s.get('small_inflow',0)/1e8:+.1f}亿" if s.get("small_inflow") else "0",
            v15="-"))
    return in_rows, out_rows


def _judge_tag(text):
    """从报告第一行提取看多/看空/中性标签。"""
    if "看多" in text[:30]: return "bull", "看多"
    if "看空" in text[:30]: return "bear", "看空"
    return "neutral", "中性"


def render_market_report(report_text, indices=None, sectors=None, v15=None, status="", out_dir=None):
    """渲染全市场盘面报告 HTML。

    参数:
        report_text: DeepSeek 返回的完整报告 markdown
        indices: 指数列表
        sectors: 板块列表（含资金分层）
        status: 市场状态
        out_dir: 输出目录（None 则不写文件，仅返回 HTML 字符串）
    返回:
        (html_str, filepath) 或 html_str
    """
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    ts_str = now.strftime("%H:%M")

    # 解析报告各段落
    lines = report_text.split("\n")
    sections = {"盘面定位": "", "指数": "", "板块资金": "", "趋势": "", "消息": "", "后续": "", "风险": ""}

    cur_key = None
    for l in lines:
        if "### 一、" in l or "当前盘面" in l or "一、当前" in l:
            cur_key = "盘面定位"
            continue
        elif "### 二、" in l or "指数与结构" in l or "二、指数" in l:
            cur_key = "指数"
            continue
        elif "### 三、" in l or "板块资金" in l or "三、板块" in l:
            cur_key = "板块资金"
            continue
        elif "### 四、" in l or "趋势与回顾" in l or "四、趋势" in l:
            cur_key = "趋势"
            continue
        elif "### 五、" in l or "消息催化" in l or "五、消息" in l:
            cur_key = "消息"
            continue
        elif "### 六、" in l or "后续方向" in l or "六、后续" in l:
            cur_key = "后续"
            continue
        elif "### 七、" in l or "风险提示" in l or "七、风险" in l:
            cur_key = "风险"
            continue
        if cur_key and l.strip():
            sections[cur_key] += "\n" + l.strip()

    # 构建 body
    body_parts = []

    # 一、盘面定位
    first_line = sections["盘面定位"].split("\n")[0] if sections["盘面定位"] else ""
    tag_cls, tag_text = _judge_tag(first_line or report_text)
    body_parts.append(f'''<div class="section"><div class="section-title">一、当前盘面定位</div>
<div style="margin-bottom:6px"><span class="badge {tag_cls}">{tag_text}</span></div>
<div class="section-text">{_escape(sections["盘面定位"])}</div></div>''')

    # 二、指数
    idx_html = ""
    if indices:
        for ix in indices:
            chg = ix.get("change_pct", 0)
            cls = "idx-up" if chg >= 0 else "idx-down"
            idx_html += f'<div class="idx-row"><span class="idx-name">{_escape(ix["name"])}</span><span class="idx-right {cls}">{ix["price"]:.2f}  {chg:+.2f}%</span></div>\n'
    body_parts.append(f'''<div class="section"><div class="section-title">二、指数与结构</div>
{idx_html}
<div class="section-text" style="margin-top:6px">{_escape(sections["指数"])}</div></div>''')

    # 三、板块资金
    sector_html = ""
    if sectors:
        in_rows, out_rows = _sector_rows(sectors, top_n=5)
        tbl_head = '''<tr><th>板块</th><th>涨跌</th><th>主力净额</th><th>超大单</th><th>大单</th><th>中单</th><th>小单</th><th>V15</th></tr>'''
        sector_html = f'''<div class="subtitle">流入前5</div><div class="tbl-wrap"><table class="tbl">{tbl_head}{"".join(in_rows)}</table></div>
<div class="subtitle">流出前5</div><div class="tbl-wrap"><table class="tbl">{tbl_head}{"".join(out_rows)}</table></div>'''
    body_parts.append(f'''<div class="section"><div class="section-title">三、板块资金解读</div>
{sector_html}
<div class="section-text">{_escape(sections["板块资金"])}</div></div>''')

    # 四、趋势
    body_parts.append(f'''<div class="section"><div class="section-title">四、趋势与回顾</div>
<div class="section-text">{_escape(sections["趋势"])}</div></div>''')

    # 五、消息
    body_parts.append(f'''<div class="section"><div class="section-title">五、消息催化</div>
<div class="section-text">{_escape(sections["消息"])}</div></div>''')

    # 六、后续
    body_parts.append(f'''<div class="section"><div class="section-title">六、后续方向判断</div>
<div class="section-text">{_escape(sections["后续"])}</div></div>''')

    # 七、风险
    body_parts.append(f'''<div class="section"><div class="section-title">七、风险提示</div>
<div class="risk">{_escape(sections["风险"])}</div></div>''')

    body = "\n".join(body_parts)
    html = HTML_HEAD.format(date=date_str, time=ts_str, status=status, body=body)

    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        fname = f"market_{datetime.now().strftime('%Y%m%d_%H%M')}.html"
        fpath = os.path.join(out_dir, fname)
        with open(fpath, "w", encoding="utf-8") as f:
            f.write(html)
        return html, fpath
    return html


def render_sector_report(sector_name, report_text, sector_data=None, v15_data=None):
    """渲染单个板块分析 HTML。"""
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    ts_str = now.strftime("%H:%M")

    first_line = report_text.split("\n")[0] if report_text else ""
    tag_cls,_ = _judge_tag(first_line or "")

    body = f'''
<div class="section"><div class="section-title">板块分析 · {_escape(sector_name)}</div>
<div style="margin-bottom:6px"><span class="badge {tag_cls}">{_escape(first_line[:20])}</span></div>
<div class="section-text">{_escape(report_text)}</div></div>'''

    html = HTML_HEAD.format(date=date_str, time=ts_str, status="板块查询", body=body)
    return html


def _escape(text):
    """HTML 转义"""
    if not text: return ""
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return text


def save_html(html_str, prefix, out_dir):
    """保存 HTML 到文件，返回文件名。"""
    os.makedirs(out_dir, exist_ok=True)
    fname = f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M')}.html"
    fpath = os.path.join(out_dir, fname)
    with open(fpath, "w", encoding="utf-8") as f:
        f.write(html_str)
    return fpath
