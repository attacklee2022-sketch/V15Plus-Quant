from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from urllib.request import Request, urlopen
from _http_utils import get_headers
SINA_QUOTE_URL = "https://hq.sinajs.cn/list"


@dataclass
class CrossMarketQuote:
    symbol: str
    name: str
    market: str       # "US" 或 "H"
    theme: str        # "AI算力" / "半导体" / "新能源汽车"
    price: float
    change_pct: float
    source: str


# ── 监控的港美股锚点 ──────────────────────────────────
# 每个条目: (symbol, 显示名, 市场, 主题, sina代码)
WATCH_SYMBOLS: dict[str, tuple[str, str, str, str]] = {
    # 美股科技七巨头 + AI
    "NVDA":    ("英伟达",       "US", "AI算力",       "gb_nvda"),
    "MSFT":    ("微软",         "US", "AI算力",       "gb_msft"),
    "GOOGL":   ("谷歌",         "US", "AI算力",       "gb_goog"),
    "META":    ("Meta",         "US", "AI算力",       "gb_meta"),
    "AMZN":    ("亚马逊",       "US", "AI算力",       "gb_amzn"),
    # 美股半导体
    "AVGO":    ("博通",         "US", "半导体",       "gb_avgo"),
    "AMD":     ("AMD",          "US", "半导体",       "gb_amd"),
    "QCOM":    ("高通",         "US", "半导体",       "gb_qcom"),
    "INTC":    ("英特尔",       "US", "半导体",       "gb_intc"),
    # 美股消费 + 新能源
    "AAPL":    ("苹果",         "US", "消费电子",     "gb_aapl"),
    "TSLA":    ("特斯拉",       "US", "新能源汽车",   "gb_tsla"),
    # 美股中概
    "BABA":    ("阿里巴巴ADR",  "US", "互联网",       "gb_baba"),
    "BIDU":    ("百度",         "US", "AI算力",       "gb_bidu"),
    "NIO":     ("蔚来",         "US", "新能源汽车",   "gb_nio"),
    "XPEV":    ("小鹏汽车",     "US", "新能源汽车",   "gb_xpev"),
    "LI":      ("理想汽车",     "US", "新能源汽车",   "gb_li"),
    # 美股三大指数
    ".IXIC":   ("纳斯达克",     "US", "指数",         "gb_ixic"),
    ".DJI":    ("道琼斯",       "US", "指数",         "gb_dji"),
    ".SPX":    ("标普500",      "US", "指数",         "gb_inx"),
    # 港股互联网
    "0700.HK": ("腾讯控股",     "H",  "互联网",       "hk00700"),
    "9988.HK": ("阿里巴巴",     "H",  "互联网",       "hk09988"),
    "3690.HK": ("美团",         "H",  "互联网",       "hk03690"),
    "9618.HK": ("京东",         "H",  "互联网",       "hk09618"),
    "9999.HK": ("网易",         "H",  "互联网",       "hk09999"),
    "9888.HK": ("百度集团",     "H",  "AI算力",       "hk09888"),
    # 港股新能源
    "1211.HK": ("比亚迪股份",   "H",  "新能源汽车",   "hk01211"),
    "1810.HK": ("小米集团",     "H",  "新能源汽车",   "hk01810"),
    "2015.HK": ("理想汽车",     "H",  "新能源汽车",   "hk02015"),
    "9868.HK": ("小鹏汽车",     "H",  "新能源汽车",   "hk09868"),
    # 港股半导体 + 医药
    "0981.HK": ("中芯国际",     "H",  "半导体",       "hk00981"),
    "2269.HK": ("药明生物",     "H",  "医药",         "hk02269"),
}


DEMO_QUOTES = [
    CrossMarketQuote("NVDA",    "英伟达",       "US", "AI算力",       129.8,  3.6, "DemoCross"),
    CrossMarketQuote("MSFT",    "微软",         "US", "AI算力",       482.4,  1.1, "DemoCross"),
    CrossMarketQuote("GOOGL",   "谷歌",         "US", "AI算力",       365.8,  0.9, "DemoCross"),
    CrossMarketQuote("META",    "Meta",         "US", "AI算力",       593.0,  2.3, "DemoCross"),
    CrossMarketQuote("AMZN",    "亚马逊",       "US", "AI算力",       246.0,  1.8, "DemoCross"),
    CrossMarketQuote("AVGO",    "博通",         "US", "半导体",       385.7,  3.2, "DemoCross"),
    CrossMarketQuote("AMD",     "AMD",          "US", "半导体",       174.3,  2.8, "DemoCross"),
    CrossMarketQuote("QCOM",    "高通",         "US", "半导体",       215.9,  1.5, "DemoCross"),
    CrossMarketQuote("INTC",    "英特尔",       "US", "半导体",        99.2, -0.8, "DemoCross"),
    CrossMarketQuote("AAPL",    "苹果",         "US", "消费电子",     307.3,  0.5, "DemoCross"),
    CrossMarketQuote("TSLA",    "特斯拉",       "US", "新能源汽车",   198.5, -0.7, "DemoCross"),
    CrossMarketQuote("BABA",    "阿里巴巴ADR",  "US", "互联网",       121.1,  2.1, "DemoCross"),
    CrossMarketQuote("BIDU",    "百度",         "US", "AI算力",       121.7, -1.2, "DemoCross"),
    CrossMarketQuote("NIO",     "蔚来",         "US", "新能源汽车",     5.4,  1.8, "DemoCross"),
    CrossMarketQuote("XPEV",    "小鹏汽车",     "US", "新能源汽车",    16.0,  2.5, "DemoCross"),
    CrossMarketQuote("LI",      "理想汽车",     "US", "新能源汽车",    14.2,  1.0, "DemoCross"),
    CrossMarketQuote(".IXIC",   "纳斯达克",     "US", "指数",       25709.4,  0.8, "DemoCross"),
    CrossMarketQuote(".DJI",    "道琼斯",       "US", "指数",       50866.8,  0.3, "DemoCross"),
    CrossMarketQuote(".SPX",    "标普500",      "US", "指数",        7383.7,  0.5, "DemoCross"),
    CrossMarketQuote("0700.HK", "腾讯控股",     "H",  "互联网",       418.6,  2.2, "DemoCross"),
    CrossMarketQuote("9988.HK", "阿里巴巴",     "H",  "互联网",       248.0,  1.5, "DemoCross"),
    CrossMarketQuote("3690.HK", "美团",         "H",  "互联网",        79.0,  1.1, "DemoCross"),
    CrossMarketQuote("9618.HK", "京东",         "H",  "互联网",       115.0,  0.3, "DemoCross"),
    CrossMarketQuote("9999.HK", "网易",         "H",  "互联网",       191.7, -2.4, "DemoCross"),
    CrossMarketQuote("9888.HK", "百度集团",     "H",  "AI算力",       131.0,  1.8, "DemoCross"),
    CrossMarketQuote("1211.HK", "比亚迪股份",   "H",  "新能源汽车",   255.2,  1.0, "DemoCross"),
    CrossMarketQuote("1810.HK", "小米集团",     "H",  "新能源汽车",    28.6,  2.8, "DemoCross"),
    CrossMarketQuote("2015.HK", "理想汽车",     "H",  "新能源汽车",    57.1,  0.5, "DemoCross"),
    CrossMarketQuote("9868.HK", "小鹏汽车",     "H",  "新能源汽车",    66.3, -0.6, "DemoCross"),
    CrossMarketQuote("0981.HK", "中芯国际",     "H",  "半导体",        25.7,  2.7, "DemoCross"),
    CrossMarketQuote("2269.HK", "药明生物",     "H",  "医药",          33.0,  1.0, "DemoCross"),
]


def _safe_float(value, default: float = 0) -> float:
    try:
        return float(value) if value is not None else default
    except (ValueError, TypeError):
        return default


def _fetch_sina(symbols: list[str], timeout: int = 10) -> dict[str, list[str]]:
    """批量请求新浪财经行情，返回 {sina_code: [字段列表]}。"""
    if not symbols:
        return {}
    ids = ",".join(symbols)
    # 注意: 新浪 API 要求逗号字面值，不能用 quote 编码
    # 新浪 API 特殊格式: /list=id1,id2,... (没有 ?，逗号不编码)
    url = f"{SINA_QUOTE_URL}={ids}"
    req = Request(url, headers=get_headers("https://finance.sina.com.cn"))
    with urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("gbk", errors="ignore")

    result: dict[str, list[str]] = {}
    for line in raw.strip().split("\n"):
        line = line.strip()
        if not line or "=" not in line:
            continue
        var_part, data_part = line.split("=", 1)
        sina_code = var_part.strip().replace("var hq_str_", "").strip('"')
        fields = data_part.strip().strip('";').split(",")
        result[sina_code] = fields
    return result


def _parse_sina_a(stock_code: str, fields: list[str]) -> tuple[float, float, str] | None:
    """解析 A 股新浪行情，返回 (price, change_pct, name)。"""
    if len(fields) < 6:
        return None
    price = _safe_float(fields[3])   # 当前价
    prev = _safe_float(fields[2])    # 昨收
    if prev == 0:
        return None
    change_pct = (price - prev) / prev * 100 if price else 0
    name = fields[0]
    return round(price, 2), round(change_pct, 2), name


def _parse_sina_hk(fields: list[str]) -> tuple[float, float, str] | None:
    """解析港股新浪行情，返回 (price, change_pct, name)。"""
    if len(fields) < 9:
        return None
    price = _safe_float(fields[2])   # 当前价
    change_pct = _safe_float(fields[8])  # 涨跌幅 %
    name = str(fields[1]) if fields[1] else fields[0]
    if price == 0:
        return None
    return round(price, 2), round(change_pct, 2), name


def _parse_sina_us(fields: list[str]) -> tuple[float, float, str] | None:
    """解析美股新浪行情，返回 (price, change_pct, name)。"""
    if len(fields) < 3:
        return None
    price = _safe_float(fields[1])   # 当前价
    change_pct = _safe_float(fields[2])  # 涨跌幅(%)
    name = fields[0] if fields[0] else "?"
    if price == 0:
        return None
    return round(price, 2), round(change_pct, 2), name


def fetch_sina_quotes(timeout: int = 10) -> list[CrossMarketQuote]:
    """通过新浪财经获取港美股实时行情 (国内可用，无需 Key)。"""
    us_codes = [v[3] for v in WATCH_SYMBOLS.values() if v[1] == "US"]
    hk_codes = [v[3] for v in WATCH_SYMBOLS.values() if v[1] == "H"]
    all_codes = us_codes + hk_codes

    sina_data = _fetch_sina(all_codes, timeout=timeout)
    if not sina_data:
        raise RuntimeError("Sina returned no data")

    quotes: list[CrossMarketQuote] = []
    for sym_key, (default_name, market, theme, sina_code) in WATCH_SYMBOLS.items():
        fields = sina_data.get(sina_code)
        if not fields:
            continue

        name, price, change_pct = default_name, 0.0, 0.0
        parsed: tuple | None = None

        if market == "US":
            parsed = _parse_sina_us(fields)
        elif market == "H":
            parsed = _parse_sina_hk(fields)

        if parsed:
            price, change_pct, sina_name = parsed
            name = sina_name if sina_name and sina_name != "?" else default_name

        quotes.append(CrossMarketQuote(
            symbol=sym_key,
            name=name,
            market=market,
            theme=theme,
            price=price,
            change_pct=change_pct,
            source="新浪财经",
        ))

    if not quotes:
        raise RuntimeError("Sina returned no parsable quotes")
    return quotes


def get_cross_market_quotes(online: bool = True) -> tuple[list[CrossMarketQuote], str]:
    """获取 A/H/美股联动锚点数据。

    优先使用新浪财经 (国内可用，无需 API Key)，失败则回退演示数据。
    """
    if online:
        try:
            quotes = fetch_sina_quotes()
            return quotes, f"新浪财经港美股行情 {datetime.now():%Y-%m-%d %H:%M:%S}"
        except Exception as exc:
            return list(DEMO_QUOTES), f"演示港美股锚点（新浪财经连接失败：{exc}）"
    return list(DEMO_QUOTES), "演示港美股锚点"


def cross_market_theme_boost(quotes: list[CrossMarketQuote]) -> dict[str, dict]:
    """根据港美股涨跌幅计算各主题的热度修正值。"""
    buckets: dict[str, list[float]] = {}
    for item in quotes:
        buckets.setdefault(item.theme, []).append(item.change_pct)

    boosts: dict[str, dict] = {}
    for theme, changes in buckets.items():
        avg = sum(changes) / len(changes)
        positive_ratio = sum(1 for v in changes if v > 0) / len(changes) if changes else 0
        boost = max(-8, min(10, avg * 1.6 + (positive_ratio - 0.5) * 6))
        boosts[theme] = {
            "avg_change": round(avg, 2),
            "positive_ratio": round(positive_ratio, 2),
            "boost": round(boost, 1),
        }
    return boosts


def apply_cross_market_boost(sector_map: dict[str, dict], quotes: list[CrossMarketQuote]) -> dict[str, dict]:
    """把港美股主题热度修正应用到 A 股板块热度。"""
    boosted = {name: dict(info) for name, info in sector_map.items()}
    for theme, info in cross_market_theme_boost(quotes).items():
        matched = [name for name in boosted if theme in name or name in theme]
        if not matched and theme in boosted:
            matched = [theme]
        for name in matched:
            boosted[name]["heat"] = max(0, min(100, boosted[name].get("heat", 50) + info["boost"]))
            boosted[name]["change"] = boosted[name].get("change", 0) + info["avg_change"] * 0.15
            boosted[name]["cross_market"] = info
    return boosted


def build_cross_market_text(quotes: list[CrossMarketQuote], source: str) -> str:
    """生成港美股联动版块文本。"""
    lines = ["===== A/H/美股联动锚点 =====", f"数据源：{source}"]
    for item in sorted(quotes, key=lambda x: abs(x.change_pct), reverse=True):
        lines.append(
            f"{item.market} {item.symbol} {item.name} | {item.theme} | "
            f"{item.change_pct:+.2f}% | {item.price:.2f} | {item.source}"
        )
    lines.append("")
    lines.append("主题修正：")
    for theme, info in cross_market_theme_boost(quotes).items():
        lines.append(
            f"{theme}: 平均涨跌 {info['avg_change']:+.2f}% | "
            f"上涨占比 {info['positive_ratio']:.0%} | 热度修正 {info['boost']:+.1f}"
        )
    return "\n".join(lines)
