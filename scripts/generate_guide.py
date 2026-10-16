# -*- coding: utf-8 -*-
"""生成 V15 Plus+ 使用指南 PDF"""
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.colors import HexColor, black, white, grey
from reportlab.lib.units import mm, cm
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                 TableStyle, PageBreak, Frame, NextPageTemplate,
                                 PageTemplate, BaseDocTemplate, KeepTogether)
from reportlab.platypus.flowables import HRFlowable
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
import os

# ── 字体注册 ──
try:
    pdfmetrics.registerFont(TTFont('YaHei', 'C:/Windows/Fonts/msyh.ttc'))
    pdfmetrics.registerFont(TTFont('YaHeiBold', 'C:/Windows/Fonts/msyhbd.ttc'))
    CN = 'YaHei'
    CNB = 'YaHeiBold'
except Exception:
    pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
    CN = 'STSong-Light'
    CNB = 'STSong-Light'

# ── 配色 ──
C_PRIMARY  = HexColor('#1a56db')  # 主蓝
C_DARK     = HexColor('#1f2937')  # 深灰
C_RED      = HexColor('#c62828')  # 红
C_GREEN    = HexColor('#2e7d32')  # 绿
C_ORANGE   = HexColor('#e65100')  # 橙
C_BG_BLUE  = HexColor('#eff6ff')  # 浅蓝底
C_BG_GREY  = HexColor('#f3f4f6')  # 浅灰底
C_BG_YELLOW= HexColor('#fffbeb')  # 浅黄底
C_BORDER   = HexColor('#d1d5db')

W, H = A4  # 210 x 297 mm

# ── 样式 ──
styles = getSampleStyleSheet()

def s(name, **kw):
    """中文段落样式工厂"""
    defaults = dict(fontName=CN, fontSize=10, leading=16, textColor=C_DARK)
    defaults.update(kw)
    return ParagraphStyle(name, **defaults)

h1_style = s('h1_cn', fontName=CNB, fontSize=22, leading=30, textColor=C_PRIMARY, spaceAfter=12)
h2_style = s('h2_cn', fontName=CNB, fontSize=15, leading=22, textColor=C_PRIMARY, spaceBefore=20, spaceAfter=8)
h3_style = s('h3_cn', fontName=CNB, fontSize=13, leading=19, textColor=C_DARK, spaceBefore=14, spaceAfter=6)
h4_style = s('h4_cn', fontName=CNB, fontSize=11, leading=17, textColor=HexColor('#374151'), spaceBefore=10, spaceAfter=4)
body = s('body_cn', fontSize=10, leading=18, spaceAfter=6)
body_indent = s('body_indent', fontSize=10, leading=18, leftIndent=16, spaceAfter=4)
small_style = s('small_cn', fontSize=9, leading=14, textColor=HexColor('#6b7280'))
code_style = s('code_cn', fontName='Courier', fontSize=8.5, leading=12, textColor=HexColor('#1e40af'),
               backColor=HexColor('#f0f4ff'), borderPadding=8, leftIndent=12, spaceBefore=4, spaceAfter=8)
tip_style = s('tip_cn', fontSize=9.5, leading=15, leftIndent=8, textColor=HexColor('#92400e'))
note_style = s('note_cn', fontSize=9.5, leading=15, leftIndent=8, textColor=HexColor('#1e40af'))
cover_title = s('cover_title', fontName=CNB, fontSize=32, leading=42, textColor=white, alignment=TA_CENTER)

# ── 工具函数 ──
def hr():
    return HRFlowable(width="100%", thickness=0.5, color=C_BORDER, spaceBefore=6, spaceAfter=6)

def h2(text):
    return [hr(), Paragraph(text, h2_style)]

def h3(text):
    return [Paragraph(text, h3_style)]

def h4(text):
    return [Paragraph(text, h4_style)]

def p(text):
    return [Paragraph(text, body)]

def pi(text):
    return [Paragraph(text, body_indent)]

def tip(text):
    """黄色提示框"""
    t = Table([[Paragraph(f'💡 {text}', tip_style)]], colWidths=[160*mm])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), C_BG_YELLOW),
        ('LEFTPADDING', (0,0), (-1,-1), 10), ('RIGHTPADDING', (0,0), (-1,-1), 10),
        ('TOPPADDING', (0,0), (-1,-1), 8), ('BOTTOMPADDING', (0,0), (-1,-1), 8),
        ('ROUNDEDCORNERS', [4,4,4,4]),
    ]))
    return [t, Spacer(1, 4)]

def note(text):
    t = Table([[Paragraph(f'📌 {text}', note_style)]], colWidths=[160*mm])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), C_BG_BLUE),
        ('LEFTPADDING', (0,0), (-1,-1), 10), ('RIGHTPADDING', (0,0), (-1,-1), 10),
        ('TOPPADDING', (0,0), (-1,-1), 8), ('BOTTOMPADDING', (0,0), (-1,-1), 8),
        ('ROUNDEDCORNERS', [4,4,4,4]),
    ]))
    return [t, Spacer(1, 4)]

def code_block(text):
    return [
        Paragraph(f'<pre>{text}</pre>', code_style),
        Spacer(1, 2)
    ]

def build_table(headers, rows, col_widths=None):
    """建表"""
    hdr = [Paragraph(h, s('th', fontName=CNB, fontSize=9, textColor=white)) for h in headers]
    data = [hdr]
    for row in rows:
        data.append([Paragraph(str(c), s('td', fontSize=9, leading=14)) for c in row])
    if not col_widths:
        col_widths = [160*mm // len(headers)] * len(headers)
    t = Table(data, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), C_PRIMARY),
        ('TEXTCOLOR', (0,0), (-1,0), white),
        ('FONTNAME', (0,0), (-1,0), CNB),
        ('FONTSIZE', (0,0), (-1,0), 9),
        ('BACKGROUND', (0,1), (-1,-1), white),
        ('GRID', (0,0), (-1,-1), 0.4, C_BORDER),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [white, C_BG_GREY]),
        ('LEFTPADDING', (0,0), (-1,-1), 6),
        ('RIGHTPADDING', (0,0), (-1,-1), 6),
        ('TOPPADDING', (0,0), (-1,-1), 5),
        ('BOTTOMPADDING', (0,0), (-1,-1), 5),
    ]))
    return [t, Spacer(1, 8)]

# ═══════════════════════════════════════════════
# 构建文档
# ═══════════════════════════════════════════════

output_path = os.path.join(os.path.expanduser('~'), 'Desktop', 'V15Plus_使用指南.pdf')
doc = SimpleDocTemplate(output_path, pagesize=A4,
                        leftMargin=20*mm, rightMargin=20*mm,
                        topMargin=18*mm, bottomMargin=20*mm,
                        title='V15 Plus+ 使用指南')

story = []

# ── 封面 ──
story.append(Spacer(1, 40*mm))
story.append(Paragraph('V15 Plus+', s('cover_sub', fontName=CNB, fontSize=38, leading=50, textColor=C_PRIMARY, alignment=TA_CENTER)))
story.append(Paragraph('大赢家 量化选股系统', s('cover_sub', fontName=CN, fontSize=20, leading=30, textColor=C_DARK, alignment=TA_CENTER)))
story.append(Spacer(1, 10*mm))
story.append(Paragraph('使用指南', s('cover_sub', fontName=CNB, fontSize=26, leading=35, textColor=C_PRIMARY, alignment=TA_CENTER)))
story.append(Spacer(1, 8*mm))
story.append(Paragraph('版本 2.0  |  板块动力模块上线  |  2026年6月', s('cover_sub', fontSize=12, textColor=HexColor('#6b7280'), alignment=TA_CENTER)))
story.append(Spacer(1, 20*mm))

# 快速信息卡片
info_data = [
    [Paragraph('<b>启动方式</b>', s('td', fontName=CNB, fontSize=10)),
     Paragraph('<b>数据源</b>', s('td', fontName=CNB, fontSize=10)),
     Paragraph('<b>系统要求</b>', s('td', fontName=CNB, fontSize=10))],
    [Paragraph('桌面双击 V15Plus启动.bat', s('td', fontSize=10)),
     Paragraph('通达信本地日线 + 东方财富API + akshare新闻', s('td', fontSize=10)),
     Paragraph('Python 3.11 / Windows 10+ / 通达信数据完整', s('td', fontSize=10))],
]
info_t = Table(info_data, colWidths=[50*mm, 64*mm, 48*mm])
info_t.setStyle(TableStyle([
    ('BACKGROUND', (0,0), (-1,0), C_BG_BLUE),
    ('BACKGROUND', (0,1), (-1,1), white),
    ('GRID', (0,0), (-1,-1), 0.4, C_BORDER),
    ('LEFTPADDING', (0,0), (-1,-1), 10),
    ('TOPPADDING', (0,0), (-1,-1), 8),
    ('BOTTOMPADDING', (0,0), (-1,-1), 8),
]))
story.append(info_t)

story.append(PageBreak())

# ── 目录 ──
story.append(Paragraph('目  录', h1_style))
story.append(Spacer(1, 6))
toc_items = [
    ('一、系统概述', 'V15 Plus+ 是什么，与 V15Alpha 的关系'),
    ('二、快速上手', '启动、界面布局、首次使用步骤'),
    ('三、界面详解（13 个 Tab）', '每个 Tab 的功能、使用场景、示例'),
    ('四、板块动力模块', '核心公式、赔率系数、标签含义、展示位置'),
    ('五、外部环境调节', 'FOMC 窗口期、资金集中度'),
    ('六、实战示例', '典型工作流：从扫描到决策'),
    ('七、验证清单', '首次启动后确认每项功能正常'),
    ('八、常见问题', 'FAQ'),
]
for title, desc in toc_items:
    story.append(Paragraph(f'<b>{title}</b>', s('toc', fontName=CNB, fontSize=12, leading=22, textColor=C_PRIMARY)))
    story.append(Paragraph(f'　　{desc}', s('toc', fontSize=10, leading=18, textColor=HexColor('#6b7280'))))
    story.append(Spacer(1, 4))

story.append(PageBreak())

# ═══════════════════════════════════════════════
# 一、系统概述
# ═══════════════════════════════════════════════
story.append(Paragraph('一、系统概述', h1_style))
story += p('V15 Plus+ 是 A 股量化选股与择时系统，基于 Python + Tkinter GUI，从通达信本地日线数据出发，通过四阶段评分流水线对自选股进行技术面评分、场景识别、多层过滤，输出买卖信号和仓位建议。')
story += p('V15 Plus+ 在 V15Alpha 的基础上，以插件形式追加了三个增强因子和板块动力模块。<b>所有增强功能不修改 V15Alpha 核心引擎</b>，保持原始评分管道的稳定性。')

story += h3('评分管道')
story += p('评分管道分四层，每层各司其职：')
story += code_block(
    '通达信日线 (.day)\n'
    '    │\n'
    '    ▼\n'
    '① V15 四因子评分       四维度: 反转(35%) + MACD(25%) + 量能(25%) + 安全结构(15%)\n'
    '    │\n'
    '    ▼\n'
    '② 场景感知引擎         超跌反弹 / 趋势接力 / 突破确认 / 无场景 → 调整权重\n'
    '    │\n'
    '    ▼\n'
    '③ V15 过滤层           多周期共振 / 波动率 / MACD背离 / 动量背离 / 板块共振 / 扩展5项\n'
    '    │\n'
    '    ▼\n'
    '④ 仓位建议             强势40% / 偏强25% / 中性10% / 弱势0% + 止盈止损\n'
    '    │\n'
    '    ▼\n'
    '⑤ Plus+ 增强层         资金流(±6) + 多周期共振(±8) + 新闻情绪(±8)\n'
    '    │\n'
    '    ▼\n'
    '⑥ 组合优化             行业集中度 + 仓位上限 + 浮亏收紧\n'
    '    │\n'
    '    ▼\n'
    '🆕 板块动力(独立展示)   新闻 + 资金 + 位置 → 顺风/逆风标签，不参与评分'
)

story += h3('本节要点')
story += p('• V15 Plus+ = V15Alpha + Plus+ 增强层 + 板块动力（独立）')
story += p('• 板块动力分不在上述评分管道任何一级中，仅作为独立数据列展示')
story += p('• 所有 Plus+ 模块位于 <font face="Courier" color="#1e40af">_plus_/</font> 目录，以插件形式接入，fail-safe 设计')

story.append(PageBreak())

# ═══════════════════════════════════════════════
# 二、快速上手
# ═══════════════════════════════════════════════
story.append(Paragraph('二、快速上手', h1_style))

story += h3('启动系统')
story += p('双击桌面 <b>V15Plus启动.bat</b> 即可启动。首次启动需要 10-30 秒加载数据。')
story += p('启动后界面分为三部分：')
story += pi('• <b>左侧面板</b>：自选股列表（可搜索/双击选中）')
story += pi('• <b>顶部状态栏</b>：5 个数据源指示灯（通达信/腾讯/板块/港美股/新闻）')
story += pi('• <b>中央区域</b>：13 个 Tab 页签，切换不同功能')

story += h3('首次使用验证步骤')
story += p('<b>第1步</b> — 打开"新闻雷达" Tab，点击刷新，确认新闻正常拉取（显示条数 > 0）')
story += p('<b>第2步</b> — 打开"板块热度" Tab，确认热力地图正常显示（彩色方块分布）')
story += p('<b>第3步</b> — 打开"信号看板" Tab，点击"刷新信号"，等待扫描完成（约 1-2 分钟）')
story += p('<b>第4步</b> — 在信号列表中观察"板块动力"列，确认每只股票都有分值')
story += p('<b>第5步</b> — 双击一只股票，打开"择时雷达" Tab，拉到最下方确认板块动力面板显示')

story += h3('数据源指示灯')
build_data = [
    ['指示灯', '数据源', '绿色 = OK', '灰色/红色 = 离线'],
    ['通达信', '本地 .day 文件', '始终绿（本地文件）', '文件路径错误'],
    ['腾讯', 'qt.gtimg.cn', '实时行情可用', '网络不通'],
    ['板块', '东方财富 API', '板块数据已拉取', 'API 超时'],
    ['港美股', '新浪财经', '跨市场数据可用', '网络不通'],
    ['新闻', 'akshare', '市场新闻正常', 'akshare 离线'],
]
story += build_table(['指示项', '数据源', '正常状态', '异常状态'],
                     [[r[0],r[1],r[2],r[3]] for r in build_data[1:]],
                     [24*mm, 38*mm, 48*mm, 48*mm])

story.append(PageBreak())

# ═══════════════════════════════════════════════
# 三、界面详解
# ═══════════════════════════════════════════════
story.append(Paragraph('三、界面详解（13 个 Tab）', h1_style))

tabs = [
    ('批量扫描', '一键全量扫描自选股池，跑 V15 全链路 + Plus+ 增强 + 板块动力', [
        '使用场景：每日开盘后/收盘后，快速了解全市场机会',
        '操作：点击"刷新信号"，等待 1-2 分钟扫描完成',
        '扫描结果分级统计：强势/偏强/中性/弱势 各多少只',
        '示例输出："股票池 410 只 | 强势 12 偏强 38 中性 85 弱势 275"',
    ]),
    ('单股分析', '深度研报 + AI 推理 + 蜡烛图形态标注', [
        '使用场景：对信号看板中的候选股做深度分析',
        '包含：资金流向、板块热度、场景判定、AI 多维度评述',
        'AI 推理调用 DeepSeek API（3 个 Key 轮换），离线时降级为本地规则',
        '注意事项：AI 推理仅基于本地技术事实，不编造公告/资金流/行业消息',
    ]),
    ('择时雷达', 'V15 全链路评分详情 + Plus+ 增强因子 + 板块动力面板', [
        '使用场景：选股决策前，查看完整评分路径和每个维度的贡献',
        '评分路径示例："四因子68 → 场景+5 → 共振-3 → 波动-2 → 板块+5 → 扩展+3 = 最终76"',
        '冲突检测：当技术面与其他维度矛盾时（如"信号偏空但盈亏比好"），自动提示',
        '板块动力面板在详情最底部，展示新闻/资金/位置三维明细',
    ]),
    ('形态备注', '16 种蜡烛图形态检测结果', [
        '使用场景：辅助参考，不作为评分依据',
        '已回测确认：16 种形态胜率均在 45-52%，仅用于 K 线图标注和 AI 报告上下文',
    ]),
    ('K线图表', '单股 K 线图 + 成交量 + 均线 + 布林带', [
        '使用场景：技术面分析，确认信号的有效性',
        '叠加：MA5/MA10/MA20/MA60 + 布林带(20,2) + V15 信号标注',
    ]),
    ('V15趋势', 'V15 评分 + 四因子趋势图', [
        '使用场景：观察评分变化方向，判断趋势是否在改善/恶化',
        '评分从 50 涨到 65 → 趋势改善；从 75 降到 60 → 需警惕',
    ]),
    ('信号看板', '🆕 实盘信号总览表，含板块动力列', [
        '使用场景：日常盯盘的核心页面，一张表纵览全自选',
        '表头：代码 | 名称 | V15分 | 等级 | 场景 | 板块 | 板块动力 | 操作 | 分时强度 | 进场建议',
        '板块动力列示例：78 🔷 (顺风) / 38 🔶 (逆风) / 52 (中性)',
        '颜色编码：偏强=红色底、中性=橙色底、弱势=灰色',
        '每 60 秒可自动刷新（需打开"自动刷新"开关）',
    ]),
    ('板块热度', '板块热力地图 + 涨跌幅颜色', [
        '使用场景：发现当日热点板块，配合板块动力判断持续性',
        '红色→上涨板块，绿色→下跌板块，颜色深浅=幅度大小',
        '点击刷新可重新拉取东方财富板块数据',
    ]),
    ('新闻雷达', '市场新闻情绪 + 风险预警 + 板块新闻归类', [
        '使用场景：开盘前了解宏观情绪，盘中关注突发事件',
        '顶部：🚨 风险预警（严重/高风险/关注 三级）',
        '中部：⭐ 重点新闻（priority ≥ 50 的按重要度排序）',
        '底部：按板块归类的全部新闻，含时间、热度、情绪标签',
        '新闻数据同时缓存给板块动力和情绪量化模块使用',
    ]),
    ('市场全景', '🆕 三大指数 + 涨跌家数 + 环境状态 + 跨市场锚点', [
        '使用场景：快速把握全市场状态，判断大环境是否适合操作',
        '三大指数卡片：上证/深证/创业板 实时涨跌',
        '涨跌家数统计：上涨 X 只 / 下跌 Y 只 / 主力净流入',
        '环境状态行（新增）：FOMC 窗口 + 资金集中度',
        'FOMC 窗口期：额外显示跨市场锚点（美股/港股）',
        '底部：自选股实时行情排行（涨跌幅排序）',
        '底部：自选股板块动力排行（板块动力分排序）',
    ]),
    ('持仓管理', '投资组合跟踪 + 浮盈浮亏 + 止损止盈提醒', [
        '使用场景：持仓后的跟踪管理',
        '记录买入价/数量/日期，自动计算浮盈浮亏',
        '止损止盈价格自动从仓位建议中带入',
    ]),
    ('盘前雷达', '盘前准备清单：隔夜外盘 + 今日关注 + 提醒', [
        '使用场景：开盘前 30 分钟快速过一遍',
        '包含：隔夜美股涨跌、今日新股/日历事件、重点关注股票',
    ]),
    ('因子IC', '因子信息系数回测工具', [
        '使用场景：评估各因子的预测能力，指导参数调优',
        '计算各因子与未来收益的 Rank IC 和 ICIR',
    ]),
]

for tab_name, desc, details in tabs:
    story += h3(tab_name)
    story += p(f'<b>{desc}</b>')
    for d in details:
        story += pi(d)
    story.append(Spacer(1, 2))

story.append(PageBreak())

# ═══════════════════════════════════════════════
# 四、板块动力模块
# ═══════════════════════════════════════════════
story.append(Paragraph('四、板块动力模块（重点）', h1_style))

story += p('板块动力是本次更新的核心功能。它不参与 V15 评分计算，作为独立指标展示，用户自行对照参考。')

story += h3('设计理念')
story += p('板块动力分不是第四个评分维度，而是一个独立的"环境温度计"。它回答的问题是：<b>"这只股票所在的板块，当前是顺风还是逆风？"</b>')
story += p('设计原则：<b>板块动力不接入手动评分</b>。等积攒 60 个交易日以上的数据后，通过 2×2 混淆矩阵回测，用统计显著性决定是否接入评分、以什么倍率接入。')

story += h3('核心公式')
story += code_block(
    '板块动力分 = (新闻情绪 × w + 资金活跃 × (1-w)) × 赔率系数\n'
    '\n'
    '其中:\n'
    '  news_score  = 时间衰减聚合的板块新闻情绪 [0, 100]\n'
    '  fund_score  = 通达信板块日线成交量变化率映射 [0, 100]\n'
    '  w           = 分时段权重:\n'
    '                盘中(9:30-15:00) → 0.7  (更信新闻)\n'
    '                盘后/周末        → 0.4  (更信量价)\n'
    '                无新闻数据      → 0    (纯量价)\n'
    '  赔率系数    = 板块指数 60日分位数 → 五档调节'
)

story += h3('赔率系数五档')
story += p('位置不是打分项，而是乘法调节器。高位利好打折，低位利好放大。')
story += build_table(
    ['60日分位数', '系数', '含义', '示例场景'],
    [
        ['< 20%', '1.30', '低位，好赔率', '医药板块在60日最低区域 → 新闻利好效果放大30%'],
        ['20-40%', '1.15', '偏低', '白酒从底部反弹初期 → 略放大'],
        ['40-60%', '1.00', '中位，不调节', '半导体在中间位置 → 保持原样'],
        ['60-80%', '0.85', '偏高', 'AI板块涨了两个月 → 利好打85折'],
        ['> 80%', '0.70', '高位，赔率差', '煤炭在历史高点 → 利好只值7折'],
    ],
    [26*mm, 20*mm, 42*mm, 78*mm]
)

story += h3('标签解读')
story += build_table(
    ['板块动力分', '标签', '含义', '建议'],
    [
        ['≥ 70', '🔷🔷 强顺风', '板块环境非常有利', '个股信号可适当积极'],
        ['58-69', '🔷 顺风', '板块环境偏正面', '正常参考个股信号'],
        ['43-57', '中性', '板块无明确信号', '以个股技术面为准'],
        ['31-42', '🔶 逆风', '板块环境偏负面', '个股买入信号需更谨慎'],
        ['≤ 30', '🔶🔶 强逆风', '板块环境明显不利', '即使个股信号好，也建议减仓'],
    ],
    [24*mm, 28*mm, 44*mm, 60*mm]
)

story += h3('在界面中的展示位置')
story += p('<b>位置1 — 信号看板</b>：信号列表中有独立的"板块动力"列，每只股票显示分值和标签。')
story += p('<b>位置2 — 择时雷达</b>：选中一只股票后，详情底部有板块动力面板，展开三维明细：')
story += code_block(
    '【板块动力】\n'
    '  半导体板块: 39分 🔶 逆风\n'
    '  ├─ 新闻情绪: 45分  (板块无明显利好)\n'
    '  ├─ 资金活跃: 40分  (量萎缩)\n'
    '  └─ 指数位置: ×0.85  (中高 63%分位，偏高区域)\n'
    '  ⚡ FOMC窗口期: 新闻信号已降权'
)
story += p('<b>位置3 — 市场全景</b>：顶部环境状态栏显示 FOMC 窗口和资金集中度。')

story += h3('覆盖的板块')
story += p('板块动力覆盖 17 个板块，从东方财富 79 个细行业自动归类：')
story += build_table(
    ['板块', '通达信代码', '主要覆盖行业'],
    [
        ['AI算力', 'sh880669', 'IT服务、软件、电子、通信、自动化'],
        ['半导体', 'sh880491', '半导体、芯片、集成电路'],
        ['新能源汽车', 'sh880391', '汽车零部件、电池、锂电'],
        ['光伏', 'sh880544', '光伏设备、逆变器'],
        ['通信设备', 'sh880490', '通信、5G、光模块'],
        ['证券金融', 'sh880472', '证券、银行、保险'],
        ['能源', '石油/煤炭分流', '煤炭开采、石油石化'],
        ['军工', 'sh880507', '航空装备、船舶、兵器'],
        ['钢铁', 'sh880318', '钢铁、钢材'],
        ['房地产', 'sh880482', '房地产开发、园区'],
        ['数字金融', 'sh880946', '数字货币、区块链'],
        ['医药', 'sh880400', '医药、医疗器械、生物制品'],
        ['消费', '食品/白酒分流', '白酒、食品饮料、家电、旅游'],
        ['电力', 'sh880305', '电力、电网、发电'],
        ['有色', 'sh880324', '工业金属、黄金、稀土'],
        ['机械', '三子类分流', '通用机械、工业机械、机械基件'],
    ],
    [24*mm, 26*mm, 112*mm]
)
story += p('注：能源/消费/机械三个板块根据新闻标题的关键词自动分流到更细的通达信子板块。宏观不映射到单一板块。')

story += h3('数据来源')
story += build_table(
    ['数据', '来源', '说明'],
    [
        ['板块日线', '通达信本地 vipdoc/shl/lday/', 'shl880*.day 文件，2005年至今，2611条'],
        ['行业标签', '东方财富 API f100', '个股→行业精准匹配'],
        ['新闻情绪', 'akshare 东方财富全球', '每2分钟缓存刷新'],
        ['板块热度', '东方财富行业板块 API', '实时涨跌+涨跌家数'],
    ],
    [34*mm, 52*mm, 70*mm]
)

story.append(PageBreak())

# ═══════════════════════════════════════════════
# 五、外部环境调节
# ═══════════════════════════════════════════════
story.append(Paragraph('五、外部环境调节', h1_style))

story += h3('FOMC 窗口期')
story += p('美联储议息会议对 A 股有显著影响。经过 28 次历史 FOMC 会议回测（2022-2025），发现：')
story += pi('• "暂停加息"反而对 A 股最危险（T日胜率仅 21%，均值 -0.31%）')
story += pi('• "加息"对 A 股影响不明显（T日胜率 55%，市场提前消化）')
story += pi('• "降息"对 A 股利好（T+10均值 +6.43%，但样本仅3次）')

story += h3('FOMC 窗口期系统行为')
story += p('<b>触发条件</b>：距下次 FOMC 公告日 ≤ 10 个交易日（纯日历判断，100% 可预测）')
story += p('<b>系统自动</b>：')
story += pi('• 板块动力的新闻权重减半（×0.5），因为宏观事件主导情绪，行业新闻被稀释')
story += pi('• 市场全景中 FOMC 状态栏变橙色，显示"⚡ FOMC窗口: 距06-18还有7天 (新闻信号降权)"')
story += pi('• 额外展示跨市场锚点："跨市场锚点(FOMC窗口): 纳指 -0.8% | 恒指 +0.3%"')
story += p('<b>系统不做的</b>：不改变 V15 评分、不改变扩展过滤器、不改变跨市场锚点在 V15 管线中的原有逻辑。')

story += h3('资金集中度')
story += p('<b>触发条件</b>：Top3 板块成交额占全市场超过 35%（双窗口 20+60 日检测）')
story += p('<b>系统行为</b>：在市场全景状态栏显示"⚠ 资金集中度偏高 (42% Top3: AI算力, 半导体)"')
story += p('<b>系统不做的</b>：不介入评分、不调节权重。仅提示用户：当下市场资金高度集中，非主线板块的信号需结合此背景判断。')

story += h3('2025-2026 FOMC 日程（系统内置）')
story += build_table(
    ['日期', '日期', '日期'],
    [
        ['2025-01-30', '2025-06-19', '2025-11-07'],
        ['2025-03-20', '2025-07-31', '2025-12-11'],
        ['2025-05-08', '2025-09-18', '2026-01-29'],
    ],
    [48*mm, 48*mm, 48*mm]
)

story.append(PageBreak())

# ═══════════════════════════════════════════════
# 六、实战示例
# ═══════════════════════════════════════════════
story.append(Paragraph('六、实战示例', h1_style))

story += h3('场景1：收盘后选股')
story += p('<b>下午 3:30，收盘后</b>')
story += pi('1. 打开 V15 Plus+，进入"信号看板"Tab，点击"刷新信号"')
story += pi('2. 等待扫描完成，看到"股票池 410只 | 强势 8 偏强 32 中性 78 弱势 292"')
story += pi('3. 关注"板块动力"列，找到 V15 分为"偏强"且板块动力为"顺风"或"强顺风"的票')
story += pi('4. 双击其中一只（如五粮液：V15 72 偏强 + 板块动力 78 🔷顺风）')
story += pi('5. 切换到"择时雷达"，查看评分路径、冲突检测、板块动力面板明细')
story += pi('6. 确认信号无冲突后，将股票加入观察清单')

story += h3('场景2：FOMC 窗口期的特殊处理')
story += p('<b>6月10日，距 FOMC（6月18日）还有 7 个交易日</b>')
story += pi('1. 打开"市场全景"Tab，顶部看到"⚡ FOMC窗口: 距06-18还有7天 (新闻信号降权)"')
story += pi('2. 跨市场锚点显示"纳指 -0.8% | 恒指 +0.3%"，注意到纳指偏弱')
story += pi('3. 回到"信号看板"，此时所有股票的板块动力分中，新闻权重已被自动减半')
story += pi('4. 选股时更依赖 V15 技术分本身，对板块动力标签持保留态度')
story += pi('5. 等 FOMC 结果出来后，重新扫描，新闻权重恢复正常')

story += h3('场景3：资金高度集中时的应对')
story += p('<b>AI 基建行情如火如荼，Top3 板块占全市场 42% 成交额</b>')
story += pi('1. 市场全景显示"⚠ 资金集中度偏高 (42% Top3: AI算力, 半导体, 通信)"')
story += pi('2. AI算力、半导体等主线板块内的股票：板块动力参考价值高')
story += pi('3. 消费、医药等非主线板块：即使板块动力显示顺风，也可能被主线抽血')
story += pi('4. 策略：主线内精选，非主线谨慎')

story += h3('场景4：择时雷达深度分析')
story += p('<b>以北方华创（半导体，V15 65分 偏强级）为例</b>')
story += code_block(
    '============================================================\n'
    '  择时雷达 — 北方华创(002371)\n'
    '  2026-06-10 15:35\n'
    '============================================================\n'
    '\n'
    '  🔵 结论: 偏强 | 评分路径: 四因子62 → 场景+4 → 共振-1 → 扩展+5 = 最终65\n'
    '\n'
    '【1/4 场景检测】\n'
    '  当前场景: 趋势接力\n'
    '  MACD>0 + 多头排列 → MACD权重从0.25→0.30\n'
    '\n'
    '【2/4 因子评分】\n'
    '  反转评分 58.2 | MACD 65.8 | 量能 62.1 | 结构 60.5\n'
    '\n'
    '【3/4 过滤引擎】\n'
    '  多周期共振: 一致(看好) +6分\n'
    '  波动率过滤: ATR=2.1% 正常\n'
    '  板块共振: 半导体热度76 扩散度65 +10分\n'
    '  扩展过滤器: 相对强度+3 盈亏比+2 大盘+2\n'
    '\n'
    '【4/4 仓位与风控】\n'
    '  操作: 建议买入\n'
    '  仓位: 25% | 止损: 298.50(-6.0%) | 止盈一: 340.00(+7.1%)\n'
    '\n'
    '【板块动力】\n'
    '  半导体板块: 39分 🔶 逆风\n'
    '  ├─ 新闻情绪: 45分 (板块无明显利好)\n'
    '  ├─ 资金活跃: 40分 (量萎缩)\n'
    '  └─ 指数位置: ×0.85 (中高 63%分位)\n'
    '  ⚡ FOMC窗口期: 新闻信号已降权\n'
    '============================================================'
)
story += p('<b>分析</b>：V15 技术面给 65 分（偏强），但板块动力显示逆风。半导体处于中高位置且资金萎缩。FOMC 窗口期内新闻降权，所以 news_score=45 不完全可信。用户可据此适当降低仓位或等 FOMC 结束后再决策。')

story.append(PageBreak())

# ═══════════════════════════════════════════════
# 七、验证清单
# ═══════════════════════════════════════════════
story.append(Paragraph('七、验证清单', h1_style))
story += p('首次启动后，按以下清单逐项确认系统正常：')

story += build_table(
    ['序号', '验证项', '预期结果', '异常排查'],
    [
        ['1', '新闻雷达拉取新闻', '显示新闻条数 > 0', '检查网络/akshare 是否正常'],
        ['2', '板块热力地图显示', '彩色方块覆盖满窗口', '检查东方财富 API 连通'],
        ['3', '信号看板板块动力列', '每只股票都有分值', '检查通达信 sh880*.day 是否存在'],
        ['4', '择时雷达板块动力面板', '展开三维明细', '确认选中股票的行业标签有效'],
        ['5', '市场全景 FOMC 状态', '显示距下次天数', '日期硬编码，应始终正常'],
        ['6', '市场全景集中度', '显示百分比', 'sector_map 有数据即可'],
        ['7', '跨市场锚点(FOMC窗口期)', '显示美股/港股涨跌', '检查新浪财经网络连通'],
        ['8', '全量扫描不崩溃', '410 只全部跑完', '查看 error_log 文件'],
        ['9', 'sector_power_history.jsonl', '文件存在且有当日记录', '检查 scans 文件夹'],
    ],
    [12*mm, 42*mm, 50*mm, 56*mm]
)

story += h3('板块动力分合理性快速检查')
story += p('扫描完成后在信号看板中观察：')
story += pi('• 不同股票的板块动力分应该有差异（不应全 50）')
story += pi('• 近期强势板块（涨得多）的股票，板块动力分应该偏高')
story += pi('• 近期弱势板块（跌得多）的股票，板块动力分应该偏低')
story += pi('• 不应出现全 0 或全 100 的极端值')

story.append(PageBreak())

# ═══════════════════════════════════════════════
# 八、常见问题
# ═══════════════════════════════════════════════
story.append(Paragraph('八、常见问题', h1_style))

faq = [
    ('板块动力分会影响 V15 的买卖信号吗？',
     '不会。板块动力分完全独立于 V15 评分管道，只在信号列表和择时雷达中作为参考指标展示。目前系统在积攒数据阶段，等 60 个交易日后会通过回测决定是否接入评分。'),
    ('为什么有的股票板块动力分是 50 且显示"—"？',
     '可能原因：① 东方财富行业标签未命中（行业不属于 17 个已覆盖板块）② 通达信 .day 文件缺失 ③ 数据链路中某步异常（已 fail-safe 兜底）。'),
    ('FOMC 窗口期对我的选股有什么影响？',
     '板块动力的新闻权重会自动减半，所以 FOMC 窗口期内板块动力分对新闻的依赖降低。此时建议更关注 V15 技术分和跨市场锚点（美股/港股方向）。FOMC 公告出来后新闻权重自动恢复。'),
    ('资金集中度显示"偏高"该怎么办？',
     '这是提示性信息，不会介入评分。当 Top3 板块吸金严重时，主线板块（集中度名单中的板块）内的股票更有参考价值，非主线板块即使板块动力显示顺风也可能被抽血，建议更谨慎。'),
    ('板块动力分多久更新一次？',
     '每次点击"信号看板"的"刷新信号"时，板块动力缓存会清空重算。新闻数据每 2 分钟刷新一次缓存；通达信 .day 文件是本地数据，实时读取。'),
    ('为什么同一板块的股票板块动力分都一样？',
     '这是设计意图——板块动力是板块级别的指标，同一个板块的所有股票共享同一个分值。系统加了缓存，17 个板块各算一次，避免对 410 只股票重复计算。'),
    ('可以单独刷新某个板块的板块动力吗？',
     '目前不支持单独刷新。点击"信号看板→刷新信号"会清空整个缓存重算。等后续版本考虑加入板块动力排行 Tab。'),
    ('数据积攒有什么用？',
     '每次扫描会自动把每只股票的快照写入 sector_power_history.jsonl。攒够 60 个交易日后，可以通过 2×2 混淆矩阵回测（技术分高低 × 板块动力高低）判断板块动力是否提供了超越技术面的增量信息，从而决定是否接入评分及倍率。'),
    ('板块动力的新闻数据和新闻雷达的数据是同一份吗？',
     '是的。系统拉取一次市场新闻，同时缓存给新闻雷达、板块动力模块和情绪量化模块使用，不会重复拉取。'),
    ('通达信数据不完整怎么办？',
     '板块动力依赖 D:\\通达信\\vipdoc\\sh\\lday\\sh880*.day 文件。如果文件缺失，对应板块的 fund_score 和 position_mult 会自动降级为中性值（50 分/×1.0）。板块动力分仍然会输出，只是缺乏量价维度。'),
]

for q, a in faq:
    story += h4(f'Q: {q}')
    story += p(f'A: {a}')
    story.append(Spacer(1, 4))

# ── 尾页 ──
story.append(PageBreak())
story.append(Spacer(1, 60*mm))
story.append(Paragraph('V15 Plus+ 量化选股系统', s('cover_sub', fontName=CNB, fontSize=22, leading=30, textColor=C_PRIMARY, alignment=TA_CENTER)))
story.append(Spacer(1, 8*mm))
story.append(Paragraph('数据驱动 · 全量回测 · 独立展示 · 不入主评分', s('cover_sub', fontSize=14, leading=22, textColor=C_DARK, alignment=TA_CENTER)))
story.append(Spacer(1, 15*mm))
story.append(Paragraph('如有问题，在 Claude Code 中说"继续聊板块四维"即可恢复讨论上下文。', s('cover_sub', fontSize=10, leading=16, textColor=HexColor('#9ca3af'), alignment=TA_CENTER)))

# ── 生成 ──
doc.build(story)
print(f'PDF 已生成: {output_path}')
