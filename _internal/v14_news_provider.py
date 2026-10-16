from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from math import exp
import os
import random
import re
import time

# ── 个股新闻缓存 (TTL=60秒) ──
_news_cache = {}
_NEWS_CACHE_TTL = 60


def _cached_news_for(code):
    """获取单只股票新闻（带缓存），失败返回 []。"""
    now = time.time()
    if code in _news_cache:
        ts, data = _news_cache[code]
        if now - ts < _NEWS_CACHE_TTL:
            return data
    try:
        import akshare as ak
        df = ak.stock_news_em(symbol=code)
        items = []
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                items.append({"title": str(row.iloc[0]), "time": str(row.iloc[1]) if len(row) > 1 else ""})
        _news_cache[code] = (now, items)
        return items
    except Exception:
        return []


@dataclass
class MarketNews:
    title: str
    sector: str
    heat: float
    sentiment: float
    published_at: datetime
    source: str
    is_stock_specific: bool = False  # True = 关联到具体个股
    priority: int = 0  # 0-100，>=60 为重点新闻


# ── 关键词→板块映射（扩展版）──
# 优先级从高到低匹配，匹配到即返回
NEWS_SECTOR_RULES = [
    # (板块名, 关键词列表, 最小匹配数)
    ("AI算力", ["算力", "人工智能", "AI", "大模型", "数据中心", "服务器", "云计算",
                "英伟达", "GPU", "光模块", "CPO", "东数西算", "智算", "AIGC"]),
    ("半导体", ["半导体", "芯片", "先进封装", "存储芯片", "晶圆", "光刻",
                "封测", "MLCC", "中芯国际", "华虹", "长鑫", "兆易"]),
    ("新能源汽车", ["新能源车", "新能源汽车", "电动车", "锂电池", "固态电池",
                    "智能驾驶", "比亚迪", "特斯拉", "充电桩", "锂矿"]),
    ("光伏", ["光伏", "组件", "硅片", "TOPCon", "HJT", "BC电池", "逆变器",
              "太阳能", "异质结"]),
    ("通信设备", ["通信", "5G", "6G", "运营商", "基站", "华为", "中兴"]),
    ("证券金融", ["券商", "证券", "银行", "保险", "金融", "降准", "降息",
                  "LPR", "贷款利率", "存款利率", "美联储", "央行", "美元",
                  "汇率", "基准利率", "加息", "衰退", "通胀"]),
    ("能源", ["石油", "原油", "天然气", "煤炭", "OPEC", "OPEC+", "煤矿",
              "煤价", "油价", "石化"]),
    ("军工", ["军工", "航天", "卫星", "国产大飞机", "C919", "无人机", "国防",
              "军事", "地缘", "冲突", "战争", "航母"]),
    ("钢铁", ["钢铁", "钢材", "螺纹钢", "铁矿石", "宝钢"]),
    ("宏观", ["GDP", "CPI", "PPI", "PMI", "失业率", "经济数据", "就业",
              "财政", "赤字", "国债", "地方债", "债务", "关税", "贸易",
              "进出口", "外汇", "跨境", "外资", "OECD", "IMF"]),
    ("房地产", ["房地产", "楼市", "房贷", "地产", "碧桂园", "万科",
                "保利", "城中村改造", "公募REITs"]),
    ("数字金融", ["比特币", "加密货币", "数字货币", "区块链", "Web3",
                  "DeFi", "稳定币"]),
    ("医药", ["医药", "创新药", "CXO", "生物医药", "医疗器械", "中药",
              "集采", "恒瑞", "百济"]),
    ("消费", ["消费", "白酒", "食品饮料", "旅游", "免税", "社零",
              "茅台", "五粮液", "格力", "美的"]),
    ("电力", ["电力", "电网", "特高压", "储能", "虚拟电厂", "绿电",
              "火电", "水电", "核电"]),
    ("有色", ["有色金属", "铜", "铝", "黄金", "稀土", "锂", "镍",
              "大宗商品", "期货"]),
    ("机械", ["机器人", "工业母机", "工程机械", "自动化", "智能制造",
              "数控", "三一重工", "汇川"]),
]

# 单个关键词→替代板块映射（快速、精确）
NEWS_KEYWORD_SECTOR = {
    "chatgpt": "AI算力", "openai": "AI算力", "gpt": "AI算力",
    "h100": "AI算力", "a100": "AI算力", "deepseek": "AI算力",
    "字节跳动": "AI算力", "百度": "AI算力",
    "集成电路": "半导体", "国产替代": "半导体", "自主可控": "半导体",
    "华为": "通信设备",
    "特斯拉": "新能源汽车", "造车新势力": "新能源汽车",
    "茅台": "消费", "五粮液": "消费",
    "药明": "医药", "恒瑞": "医药",
    "宁德时代": "新能源汽车", "比亚迪": "新能源汽车",
    "美联储": "证券金融", "央行": "证券金融",
}

POSITIVE_WORDS = [
    "利好", "增长", "上修", "中标", "订单", "回购", "突破",
    "涨价", "活跃", "流入", "扩产", "创新高", "放量",
    "反转", "复苏", "扩张", "超额", "回暖",
]
NEGATIVE_WORDS = [
    "减持", "亏损", "下滑", "处罚", "制裁", "风险", "暴跌",
    "问询", "退市", "流出", "终止", "爆仓", "违约", "利空",
    "下调", "暂停", "警示", "立案",
]


def _clamp(value: float, low: float = 0, high: float = 100) -> float:
    return max(low, min(high, value))


def _compute_sector_heat(items: list[MarketNews]) -> dict[str, float]:
    """从当前批次新闻计算各板块的相对热度系数。

    基于 Σ(情绪绝对值 × 优先级权重) 计算板块的热度排名，
    中位数以上板块 ×1.20，中位数以下 ×0.85，中性 ×1.0。

    返回: {板块名: 系数}
    """
    if not items:
        return {}

    # 按板块聚合加权得分
    sector_scores: dict[str, list[float]] = {}
    for item in items:
        weight = abs(item.sentiment) * (max(item.priority, 10) / 100 + 0.3)
        sector_scores.setdefault(item.sector, []).append(weight)

    # 板块热度 = 平均强度 × 数量加权（板块新闻越多越能确认热度）
    sector_avg: dict[str, float] = {}
    for s, scores in sector_scores.items():
        avg = sum(scores) / len(scores)
        count_bonus = min(1.0, len(scores) / 5)  # 5条=满额
        sector_avg[s] = avg * (1 + count_bonus * 0.5)

    if not sector_avg:
        return {}

    values = sorted(sector_avg.values())
    median = values[len(values) // 2] if values else 0

    coeffs = {}
    for sector, avg in sector_avg.items():
        if median == 0:
            coeffs[sector] = 1.0
        elif avg > median * 1.15:
            coeffs[sector] = 1.20
        elif avg < median * 0.85:
            coeffs[sector] = 0.85
        else:
            coeffs[sector] = 1.0
    return coeffs


def sector_from_text(text: str) -> str:
    """基于文本内容匹配板块，返回最可能的板块名或 '通用'。"""
    lower = text.lower()

    # 第一轮：精确关键词匹配
    for keyword, sector in NEWS_KEYWORD_SECTOR.items():
        if keyword.lower() in lower:
            return sector

    # 第二轮：规则匹配（多个关键词同时出现置信度更高）
    best_sector = "通用"
    best_matches = 0
    for sector, keywords in NEWS_SECTOR_RULES:
        matches = sum(1 for kw in keywords if kw.lower() in lower)
        if matches >= 2 and matches > best_matches:
            best_sector = sector
            best_matches = matches
        elif matches == best_matches and matches >= 2:
            pass  # 持平则保留先匹配到的
    if best_matches >= 2:
        return best_sector

    # 第三轮：单一关键词匹配（只要有一个就归入）
    for sector, keywords in NEWS_SECTOR_RULES:
        if any(kw.lower() in lower for kw in keywords):
            return sector

    return "通用"


def sentiment_from_text(text: str) -> float:
    """判断情绪得分 [-1, 1]。"""
    text_lower = text.lower()
    positive = sum(1 for word in POSITIVE_WORDS if word.lower() in text_lower)
    negative = sum(1 for word in NEGATIVE_WORDS if word.lower() in text_lower)
    if positive == negative == 0:
        return 0.2  # 轻微偏多
    return max(-1, min(1, 0.2 + positive * 0.2 - negative * 0.3))


def _calc_heat(title: str, sentiment: float) -> float:
    return _clamp(55 + len(title) * 0.2 + max(sentiment, 0) * 20)


# ── 新闻类型权重（动态评分用）──
# 每条新闻命中一个类型拿该类型基础分，每多命中一个类型 +8 协同分
NEWS_TYPE_WEIGHTS = {
    "policy":      {"base": 25, "desc": "宏观政策/监管"},
    "fundamental": {"base": 18, "desc": "行业基本面"},
    "risk":        {"base": 20, "desc": "风险事件"},
    "capital":     {"base": 12, "desc": "资金/交易面"},
    "heavyweight": {"base": 10, "desc": "权重股异动"},
}

# 关键词按类型分组（关键词, 分值）
# 同类型多条关键词命中叠加分值，但类型基础分只拿一次
NEWS_KEYWORDS_BY_TYPE = {
    "policy": [
        ("降准", 15), ("降息", 15), ("加息", 10), ("LPR", 10),
        ("存款利率", 10), ("贷款利率", 10), ("基准利率", 10),
        ("美联储", 8), ("央行", 8),
        ("制裁", 15), ("关税", 12), ("贸易战", 10),
        ("熔断", 12), ("崩盘", 12),
        ("地缘", 8), ("冲突", 5),
        ("减产", 8), ("OPEC", 6), ("OPEC+", 6),
        ("GDP", 6), ("PMI", 6), ("CPI", 6), ("非农", 6),
        ("通胀", 6), ("衰退", 8), ("社融", 6), ("M2", 6),
        ("印花税", 8), ("T+0", 6),
        # — 新增：政策类补充 —
        ("国务院", 8), ("政策", 6), ("经济", 5),
        ("印发", 4), ("措施", 3), ("部署", 3),
        ("稳经济", 8), ("稳增长", 8), ("扩内需", 6),
    ],
    "fundamental": [
        ("中标", 10), ("订单", 8), ("涨价", 8), ("扩产", 8),
        ("突破", 8), ("创新高", 10), ("历史新高", 10),
        ("历史新低", 8), ("复苏", 8), ("回暖", 8), ("反转", 6),
        ("暴涨", 8), ("重大合同", 10), ("并购", 8),
        ("重组", 8), ("借壳", 12),
        ("分红", 5), ("高送转", 5),
        # — 新增：基本面补充 —
        ("产能", 6), ("订单量", 6), ("销量", 5),
        ("回升", 6), ("增长", 4), ("扩张", 6),
        ("法说会", 5), ("业绩说明会", 5),
    ],
    "risk": [
        ("暴雷", 15), ("违约", 12), ("强制平仓", 12),
        ("债务违约", 12), ("商誉减值", 10), ("计提减值", 8),
        ("业绩预亏", 10), ("亏损", 6), ("停产", 8),
        ("诉讼", 5), ("仲裁", 5),
        ("破产", 12), ("退市", 15), ("ST", 8),
        ("立案调查", 15), ("财务造假", 15), ("处罚", 8),
        ("警示函", 6), ("暂停上市", 12), ("终止上市", 12),
        ("大股东减持", 8), ("清仓减持", 12), ("股权质押", 5),
        ("限制消费", 6), ("失信", 8), ("被执行人", 6),
        ("解禁", 4), ("配股", 4), ("增发", 4),
        # — 新增 —
        ("风险提示", 4), ("预警", 3),
    ],
    "capital": [
        ("减持", 6), ("回购", 8), ("增持", 8),
        ("流入", 4), ("流出", 4), ("放量", 6),
        ("活跃", 4), ("涨停", 6), ("跌停", 6),
        ("停牌", 8), ("复牌", 8),
        ("创新低", 6),
    ],
    "heavyweight": [
        ("宁德时代", 8), ("比亚迪", 8), ("茅台", 8),
        ("英伟达", 8), ("特斯拉", 8), ("华为", 8),
        ("中芯国际", 8), ("腾讯", 8), ("阿里巴巴", 6),
        ("百度", 6), ("台积电", 8), ("五粮液", 5),
        ("隆基绿能", 6), ("药明康德", 6), ("恒瑞医药", 6),
        ("KOSPI", 3), ("日经", 3),
    ],
}

# LLM 二次判定（可选，默认关闭）
ENABLE_LLM_PRIORITY_JUDGE = True


def _calc_priority(title: str, sentiment: float, heat: float, sector: str = "通用") -> int:
    """动态评分：新闻类型分组 + 多类型协同 + 板块热度 + 情绪强度。

    返回 0-100，>=50 为重点新闻。
    新增 sector 参数用于板块热度加权（后续两趟法调整）。
    """
    score = 0.0
    hit_types = set()
    total_keyword_hits = 0

    # 1. 关键词按类型匹配
    for ntype, keywords in NEWS_KEYWORDS_BY_TYPE.items():
        type_hits = 0
        for kw, pts in keywords:
            if kw in title:
                score += pts
                type_hits += 1
                total_keyword_hits += 1
        if type_hits > 0:
            hit_types.add(ntype)

    # 2. 新闻类型基础分 + 多类型协同
    # 首个命中类型拿全额基础分，后续每多一个类型 +8
    sorted_types = sorted(hit_types, key=lambda t: NEWS_TYPE_WEIGHTS[t]["base"], reverse=True)
    for i, ntype in enumerate(sorted_types):
        if i == 0:
            score += NEWS_TYPE_WEIGHTS[ntype]["base"]
        else:
            score += 8  # 多类型协同

    # 3. 同类型内多关键词协同
    if total_keyword_hits >= 4:
        score += 10
    elif total_keyword_hits >= 2:
        score += 5

    # 4. 情绪强度加成
    if abs(sentiment) > 0.6:
        score += 15
    elif abs(sentiment) > 0.4:
        score += 10

    # 5. 热度加成
    if heat > 85:
        score += 12
    elif heat > 78:
        score += 8
    elif heat > 72:
        score += 5

    # 6. 高热度+强情绪 = 值得关注（即使无关键词命中）
    if heat > 75 and abs(sentiment) > 0.5:
        score += 10

    # 7. 标题长度适中
    if 50 <= len(title) <= 150:
        score += 3

    return _clamp(int(score), 0, 100)


def _llm_judge_priority(candidates: list) -> list[bool]:
    """LLM 二次判定：对规则分在灰色地带的新闻做重要性判定。

    参数: candidates — 30 <= priority <= 54 的 MarketNews 列表
    返回: 与 candidates 等长的 bool 列表，True=应提升为重点(55分)

    默认不启用，由 ENABLE_LLM_PRIORITY_JUDGE 控制。
    """
    if not candidates:
        return []

    try:
        import requests
        key = os.environ.get("DEEPSEEK_KEY1", "")
        if not key:
            # 检查桌面 key 文件（复用 news_distiller 的加载方式）
            desktop = os.path.expanduser("~/Desktop")
            for i in range(1, 4):
                p = os.path.join(desktop, f"deepseek_key{i}.txt")
                if os.path.exists(p):
                    with open(p, "r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if line and not line.startswith("#") and line.startswith("sk-"):
                                key = line
                                break
                if key:
                    break
        if not key:
            return [False] * len(candidates)

        # 统计当前热点板块（从 candidates 的规则分反推）
        hot_sectors = {}
        for n in candidates:
            hot_sectors[n.sector] = hot_sectors.get(n.sector, 0) + 1
        top_sectors = sorted(hot_sectors, key=hot_sectors.get, reverse=True)[:5]
        sector_str = ", ".join(top_sectors) if top_sectors else "未识别"

        # 构造批量判断 Prompt
        lines = [f"你是A股市场新闻编辑。以下是规则评分在灰色地带（30-54分）的新闻。"]
        lines.append(f"今日热点板块参考: {sector_str}")
        lines.append("请判断每条新闻是否值得提升为'重点关注'（55分以上）。")
        lines.append("判断标准：该新闻是否可能引发板块/市场波动？是否与当前热点相关？")
        lines.append("")
        lines.append("仅回复每条新闻的编号和 YES/NO，格式如: 1:YES 2:NO 3:YES")
        lines.append("")

        for i, n in enumerate(candidates):
            lines.append(f"{i+1}. [{n.sector}] [P{n.priority}] {n.title[:120]}")
            lines.append("")

        prompt = "\n".join(lines)

        resp = requests.post(
            "https://api.deepseek.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": "deepseek-chat",
                "messages": [
                    {"role": "system", "content": "你是一个A股新闻编辑，严格按格式回复。"},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.1,
                "max_tokens": 500,
            },
            timeout=15,
        )
        resp.raise_for_status()
        result_text = resp.json()["choices"][0]["message"]["content"]

        # 解析结果
        judgments = []
        for i in range(len(candidates)):
            pattern = rf'{i+1}\s*:\s*(YES|NO)'
            match = re.search(pattern, result_text, re.IGNORECASE)
            if match and match.group(1).upper() == "YES":
                judgments.append(True)
            else:
                judgments.append(False)

        while len(judgments) < len(candidates):
            judgments.append(False)
        return judgments

    except Exception:
        return [False] * len(candidates)


def demo_news() -> list[MarketNews]:
    now = datetime.now()
    samples = [
        ("AI算力订单继续释放，海外龙头资本开支预期上修", "AI算力", 92, 0.85, 0.5),
        ("国产半导体设备招标升温，先进封装链条资金关注", "半导体", 83, 0.72, 2.0),
        ("新能源汽车产业链价格企稳，头部车企订单改善", "新能源汽车", 76, 0.66, 3.0),
        ("通信设备受益算力网络建设，资金关注度提升", "通信设备", 70, 0.58, 5.0),
        ("部分高位主题短线涨幅过快，异常波动风险上升", "光伏", 61, -0.38, 9.0),
    ]
    return [MarketNews(t, s, h, se, now - timedelta(hours=a), "DemoNews")
            for t, s, h, se, a in samples]


def get_market_news(online: bool = True) -> tuple[list[MarketNews], str]:
    """获取市场新闻。

    多层数据源（降级）：
    1. akshare 东方财富全球 (stock_info_global_em) — 完整标题+摘要+真实时间
    2. (已移除 - Caixin summary被截断，无发布时间)
    3. (已移除 - 个股公告无行业分类) — 多只重点股票
    4. akshare 百度经济日历 (news_economic_baidu) — 宏观补充
    5. 降级 → 演示新闻
    """
    if not online:
        return demo_news(), "演示新闻"

    items: list[MarketNews] = []
    errors: list[str] = []
    now = datetime.now()

    # ── 第一源：东方财富全球新闻（完整标题+真实时间）──
    try:
        import akshare as ak
        news_df = ak.stock_info_global_em()
        if news_df is not None and not news_df.empty:
            for _, row in news_df.iterrows():
                title = str(row.get("标题", "") or "").strip()
                summary = str(row.get("摘要", "") or "").strip()
                time_str = str(row.get("发布时间", "") or "").strip()
                if not title or len(title) < 6:
                    continue
                # 标题 + 摘要（截取前60字）合成为展示文本
                full_title = title
                if summary and len(summary) > 10:
                    short_summary = summary[:80] + ("…" if len(summary) > 80 else "")
                    full_title = f"{title} | {short_summary}"
                sector = sector_from_text(title + " " + (summary[:30] if summary else ""))
                sentiment = sentiment_from_text(title + summary)
                heat = _calc_heat(title, sentiment)
                # 解析真实发布时间
                pub_time = _parse_time(time_str)
                items.append(MarketNews(
                    title=full_title, sector=sector,
                    heat=heat, sentiment=sentiment,
                    published_at=pub_time,
                    source="东方财富全球",
                    priority=_calc_priority(full_title, sentiment, heat),
                ))
    except Exception as exc:
        errors.append(f"全球新闻: {exc}")

    # ── 第二源：百度宏观日历（作为补充）──
    if not items or len(items) < 10:
        try:
            import akshare as ak
            eco_df = ak.news_economic_baidu()
            if eco_df is not None and not eco_df.empty:
                count = 0
                for _, row in eco_df.iterrows():
                    event = str(row.get("事件", "") or "").strip()
                    if not event or len(event) < 8:
                        continue
                    if any(item.title == event for item in items):
                        continue
                    date_val = row.get("日期")
                    if date_val:
                        try:
                            d = datetime(date_val.year, date_val.month, date_val.day)
                        except Exception:
                            d = now
                    else:
                        d = now
                    sector = sector_from_text(event)
                    if sector == "通用":
                        sector = "宏观"
                    sentiment = sentiment_from_text(event)
                    heat = _clamp(50 + len(event) * 0.15)
                    items.append(MarketNews(
                        title=event, sector=sector,
                        heat=heat, sentiment=sentiment,
                        published_at=d, source="百度经济日历",
                        priority=_calc_priority(event, sentiment, heat),
                    ))
                    count += 1
                    if count >= 20:
                        break
        except Exception:
            pass

    # ── 两趟评分：先算基础分，再按板块热度系数调整 ──
    if items:
        sector_heat = _compute_sector_heat(items)
        for item in items:
            coeff = sector_heat.get(item.sector, 1.0)
            adjusted = int(item.priority * coeff)
            item.priority = _clamp(adjusted, 0, 100)

    # ── LLM 二次判定（可选，默认关闭）──
    if ENABLE_LLM_PRIORITY_JUDGE:
        try:
            gray_items = [n for n in items if 30 <= n.priority <= 54]
            if gray_items:
                judgments = _llm_judge_priority(gray_items)
                for item, should_promote in zip(gray_items, judgments):
                    if should_promote:
                        item.priority = 55
        except Exception:
            pass

    # ── 去重 + 按真实发布时间排序 ──
    seen: set[str] = set()
    deduped: list[MarketNews] = []
    for item in sorted(items, key=lambda x: x.published_at, reverse=True):
        dedup_key = item.title[:40]
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        deduped.append(item)

    if deduped:
        return deduped[:80], f"东方财富全球新闻 {len(deduped)}条 {now:%Y-%m-%d %H:%M}"

    # ── 终极降级 ──
    return demo_news(), f"演示新闻（在线接口失败：{'; '.join(errors) or '无返回'}）"


# ── 个股新闻关联度 ──────────────────────────────────
# 用于计算某条新闻和某只股票的关联度 [0, 1]
# 关联度规则：
#   1.0 = 新闻标题直接包含股票名称或股票代码
#   0.8 = 新闻标题包含股票核心关键词（如"浪潮信息"→标题含"浪潮"）
#   0.5 = 新闻板块与股票板块一致，且标题含行业关键词
#   0.3 = 新闻板块与股票板块一致
#   0.0 = 无关联

def _name_matches_stock(name: str, code: str, title: str) -> float:
    """检查股票名称/代码是否关联新闻标题，返回关联度得分 [0, 1]。"""
    name = name.strip()
    code = code.strip()
    if not name or len(name) < 2:
        return 0.0

    # 1. 代码匹配（精确）
    if code and (code in title or f"({code}" in title or f"{code})" in title):
        return 1.0

    # 2. 全名匹配
    if name in title:
        return 1.0

    # 3. 核心词匹配（去掉公司类型后缀）
    core = name.replace("股份", "").replace("集团", "").replace("有限", "").replace("公司", "").replace("控股", "").strip()
    if len(core) >= 2 and core in title:
        # 排除通用词语误匹配（如"浪潮"在"AI浪潮"中不是公司名）
        # 核心词 >= 4 个字的直接匹配（如"中科曙光"、"科大讯飞"）
        if len(core) >= 4:
            return 0.9
        # 3个字的核心词（如"工业富联"、"浪潮信息"）如果在标题中被谓语/名词修饰说明是公司名
        if len(core) == 3:
            # 检查标题中 core 前后是否有公司相关上下文
            idx = title.index(core)
            before = title[max(0, idx-2):idx]
            after = title[idx+len(core):idx+len(core)+6]
            # 公司名前后常见：发布、公告、表示、获、拟、将、：、
            company_context = any(kw in after[:4] for kw in ["发布", "公告", "表示", "获得", "拟", "将", "称", ":"])
            company_context = company_context or any(kw in before for kw in ["：", "“", "【"])
            if company_context:
                return 0.9
            return 0.0  # 3字核心词无上下文 → 可能是普通词语
        if len(core) == 2:
            # 2字词太短，必须有明确公司上下文
            idx = title.index(core)
            after = title[idx+len(core):idx+len(core)+6]
            if any(kw in after[:4] for kw in ["发布", "公告", "表示", "获得", "拟", "将", "称", "股份", "集团"]):
                return 0.8
            return 0.0

    # 4. 股票简称的前两个字的匹配（如"贵州茅台"→"贵州"）
    if len(name) >= 4:
        two_char = name[:2]
        if two_char in title and len(core) >= 3:
            idx = title.index(two_char)
            after = title[idx+2:idx+8]
            if any(kw in after[:4] for kw in ["发布", "公告", "表示", "获得", "拟", "将", "称", "股份", "集团", "涨", "跌"]):
                return 0.6

    return 0.0


def news_relevance_for_stock(code: str, name: str, news_items: list[MarketNews]) -> tuple[float, list[tuple[str, float]]]:
    """计算新闻列表中与指定个股相关的新闻及其关联度。

    返回:
        (aggregate_score, [(title, relevance), ...])
        aggregate_score: 聚合关联度评分 [0, 100]，无关联时为 50（中性）
        list: 关联新闻列表，按关联度排序
    """
    if not news_items:
        return 50.0, []

    now = datetime.now()
    relevant: list[tuple[str, float, float, float]] = []

    name_short = name.strip()
    if " " in name_short:
        name_short = name_short.split(" ", 1)[1]

    for item in news_items:
        relevance = 0.0

        # 1. 个股新闻直接匹配来源代码
        if item.is_stock_specific:
            src = item.source
            if f"({code})" in src:
                relevance = 1.0

        # 2. 名称/代码匹配
        if relevance == 0:
            relevance = _name_matches_stock(name_short, code, item.title)

        # 3. 个股新闻未匹配到则跳过（不显示无关的个股新闻）
        if item.is_stock_specific and relevance == 0:
            continue

        # 4. 无关联则跳过
        if relevance == 0:
            continue

        age_hours = max(0.1, (now - item.published_at).total_seconds() / 3600)
        relevant.append((item.title, relevance, item.heat, age_hours))

    if not relevant:
        return 50.0, []

    relevant.sort(key=lambda x: x[1] * x[2] / (x[3] ** 0.3), reverse=True)

    top_relevance, top_heat, top_age = relevant[0][1], relevant[0][2], relevant[0][3]
    decay = exp(-top_age / 18)
    aggregate = _clamp(50 + top_relevance * top_heat * decay * 0.5)

    news_with_rel = [(t, r) for t, r, _, _ in relevant[:3]]
    return aggregate, news_with_rel


def _parse_time(value) -> datetime:
    if not value:
        return datetime.now()
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%m-%d %H:%M", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(text, fmt)
            if fmt.startswith("%m"):
                parsed = parsed.replace(year=datetime.now().year)
            return parsed
        except ValueError:
            pass
    return datetime.now()


# ══════════════════════════════════════════════════════
# 风险预警关键词扫描
# ══════════════════════════════════════════════════════

RISK_KEYWORDS = [
    # 监管风险
    ("问询函", "监管问询", "critical"),
    ("立案调查", "立案调查", "critical"),
    ("ST", "ST风险警示", "critical"),
    ("退市", "退市风险", "critical"),
    ("处罚", "监管处罚", "high"),
    ("警示函", "监管警示", "high"),
    ("暂停上市", "暂停上市", "critical"),
    ("终止上市", "终止上市", "critical"),
    # 减持/质押
    ("大股东减持", "大股东减持", "high"),
    ("股东减持", "股东减持", "high"),
    ("清仓减持", "清仓减持", "critical"),
    ("股权质押", "股权质押风险", "high"),
    ("强制平仓", "强制平仓", "critical"),
    # 财务风险
    ("业绩预亏", "业绩预亏", "high"),
    ("业绩大幅下滑", "业绩大幅下滑", "high"),
    ("亏损", "业绩亏损", "high"),
    ("计提减值", "计提减值", "high"),
    ("商誉减值", "商誉减值", "critical"),
    ("债务违约", "债务违约", "critical"),
    ("财务造假", "财务造假", "critical"),
    ("审计意见", "审计异常", "high"),
    # 经营风险
    ("停产", "停产", "high"),
    ("诉讼", "诉讼风险", "medium"),
    ("仲裁", "仲裁风险", "medium"),
    ("限制消费", "限制消费令", "high"),
    ("失信", "失信被执行人", "high"),
    ("破产", "破产风险", "critical"),
    ("重组失败", "重组失败", "high"),
    # 市场风险
    ("解禁", "解禁压力", "medium"),
    ("大额解禁", "大额解禁", "high"),
    ("配股", "配股稀释", "medium"),
    ("增发", "增发稀释", "medium"),
]


def scan_news_risks(news_items: list) -> list[dict]:
    """扫描新闻列表，返回风险预警列表。
    每条预警: {title, sector, risk_level, risk_type, matched_keyword}
    """
    alerts = []
    seen = set()
    for item in news_items:
        title = item.title
        if title in seen:
            continue
        for keyword, risk_type, level in RISK_KEYWORDS:
            if keyword in title:
                seen.add(title)
                alerts.append({
                    "title": title,
                    "sector": item.sector,
                    "published_at": item.published_at.strftime("%m-%d %H:%M") if item.published_at else "",
                    "risk_level": level,
                    "risk_type": risk_type,
                    "matched_keyword": keyword,
                })
                break  # 一条新闻只标一个最高风险类型
    alerts.sort(key=lambda x: {"critical": 0, "high": 1, "medium": 2}.get(x["risk_level"], 99))
    return alerts


def scan_stock_risks(code: str, name: str, news_items: list) -> list[dict]:
    """扫描指定个股相关的风险预警。
    返回关联到该股票或其行业/板块的风险新闻列表。
    """
    name_short = name.strip()
    if " " in name_short:
        name_short = name_short.split(" ", 1)[1]

    stock_sector = sector_from_text(f"{code} {name_short}")

    alerts = []
    seen = set()
    for item in news_items:
        title = item.title
        if title in seen:
            continue

        # 检查是否关联到该个股
        relevance = 0
        if code in title or name_short in title:
            relevance = 2  # 直接关联
        elif item.sector == stock_sector:
            relevance = 1  # 同板块

        if relevance == 0:
            continue

        for keyword, risk_type, level in RISK_KEYWORDS:
            if keyword in title:
                seen.add(title)
                alerts.append({
                    "title": title,
                    "relevance": relevance,
                    "risk_level": level,
                    "risk_type": risk_type,
                    "matched_keyword": keyword,
                    "source": item.source,
                })
                break

    alerts.sort(key=lambda x: ({"critical": 0, "high": 1, "medium": 2}.get(x["risk_level"], 99),
                               -x["relevance"]))
    return alerts


# ══════════════════════════════════════════════════════
# 板块新闻窗口聚合（板块动力模块使用）
# ══════════════════════════════════════════════════════

def get_sector_news_window(sector: str, news_items: list,
                           window_hours: int = 24) -> dict:
    """聚合某板块在时间窗口内的新闻情绪。

    用 priority × |sentiment| × 时间衰减 加权。

    返回:
        {
            "count": int,              # 窗口内相关新闻条数
            "score": float,            # 综合新闻评分 [0, 100]
            "sentiment": float,        # 加权情绪 [-1, 1]
            "headline": str | None,    # 最高优先级新闻标题
        }
    """
    now = datetime.now()
    sector_items = []

    for item in (news_items or []):
        if item.sector != sector:
            continue
        age_hours = max(0.1, (now - item.published_at).total_seconds() / 3600)
        if age_hours > window_hours:
            continue
        sector_items.append((item, age_hours))

    if not sector_items:
        return {"count": 0, "score": 50.0, "sentiment": 0.0, "headline": None}

    # 加权聚合: Σ(priority × |sentiment| × decay)
    total_weight = 0.0
    weighted_sentiment = 0.0
    weighted_score = 0.0
    top_headline = None
    top_priority = -1

    for item, age in sector_items:
        decay = exp(-age / 12)  # 半衰期 12h
        prio = item.priority / 100
        weight = (prio + 0.2) * decay
        weighted_sentiment += weight * item.sentiment
        weighted_score += weight * abs(item.sentiment) * 100
        total_weight += weight
        if item.priority > top_priority:
            top_priority = item.priority
            top_headline = item.title

    if total_weight > 0:
        sentiment = weighted_sentiment / total_weight
    else:
        sentiment = 0.0

    # 归一化 score: 50 为基线，多条高优新闻可推到 100
    count_bonus = min(1.0, len(sector_items) / 5)  # 5条满额
    raw_score = 50 + (weighted_score / max(total_weight, 0.01) - 50) * count_bonus
    score = _clamp(raw_score, 0, 100)

    return {
        "count": len(sector_items),
        "score": round(score, 1),
        "sentiment": round(sentiment, 2),
        "headline": top_headline,
    }
