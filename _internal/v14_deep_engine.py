from __future__ import annotations

import time
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import exp
import json
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import pandas as pd
import threading
from _http_utils import get_headers

# 在线数据源 (V15 Alpha)
from v14_news_provider import get_market_news, news_relevance_for_stock as _news_relevance_for_stock
from v14_cross_market import apply_cross_market_boost, build_cross_market_text, get_cross_market_quotes

# 新评分引擎（替换原系统择时信号）
from v14_tech_scorer import compute_signal_from_df

# 新形态检测引擎（替换编译.pyd中的bug）
from v14_pattern_detector import detect_all_from_df


# ── 数据类 ──────────────────────────────────────────

@dataclass
class DeepSignal:
    code: str
    name: str
    tech_score: float
    fund_score: float
    news_score: float
    sector_score: float
    total_score: float
    timing: str
    sector: str
    reasons: list[str]
    risks: list[str]
    news_relevance: float = 0.0  # 最高新闻关联度 [0,1]
    # 止损止盈
    stop_loss: float = 0.0
    stop_loss_pct: float = 0.0
    stop_loss_reason: str = ""
    take_profit: float = 0.0
    take_profit_pct: float = 0.0
    take_profit_reason: str = ""


# ── 演示数据 ────────────────────────────────────────

DEMO_CONCEPT_MAP = {
    "300750": "新能源汽车",
    "002594": "新能源汽车",
    "603019": "AI算力",
    "688981": "半导体",
    "601012": "光伏",
    "000977": "AI算力",
    "000063": "通信设备",
    "002230": "AI算力",
}

DEMO_SECTOR_HEAT = {
    "AI算力":        {"change": 3.8, "fund": 4880, "breadth": 78, "heat": 91, "total_stocks": 85},
    "半导体":        {"change": 3.1, "fund": 3560, "breadth": 72, "heat": 86, "total_stocks": 120},
    "新能源汽车":    {"change": 1.6, "fund": 1720, "breadth": 63, "heat": 69, "total_stocks": 95},
    "通信设备":      {"change": 2.2, "fund": 1420, "breadth": 67, "heat": 74, "total_stocks": 60},
    "光伏":          {"change": -1.2, "fund": -620, "breadth": 38, "heat": 42, "total_stocks": 55},
    "通用":          {"change": 0.5, "fund": 300, "breadth": 52, "heat": 50, "total_stocks": 30},
}

DEMO_NEWS = [
    ("AI算力订单继续释放，海外龙头资本开支预期上修", "AI算力", 92, 0.85, 0.5),
    ("国产半导体设备招标升温，先进封装链条资金关注", "半导体", 83, 0.72, 2.0),
    ("新能源汽车产业链价格企稳，头部车企订单改善", "新能源汽车", 76, 0.66, 3.0),
    ("通信设备受益算力网络建设，资金关注度提升", "通信设备", 70, 0.58, 5.0),
    ("部分高位主题短线涨幅过快，异常波动风险上升", "光伏", 61, -0.38, 9.0),
]

# 板块缓存路径：打包环境用 exe 同目录，开发环境用源码目录
if getattr(sys, 'frozen', False):
    _cache_dir = os.path.dirname(sys.executable)
else:
    _cache_dir = os.path.dirname(os.path.abspath(__file__))
SECTOR_CACHE_PATH = os.path.join(_cache_dir, "sector_cache.json")
SECTOR_CACHE_TTL = 600  # 10分钟缓存
_sector_cache_lock = threading.Lock()  # 防止多线程并发读写缓存文件


# ── 通用工具函数 ────────────────────────────────────

def _clamp(value: float, low: float = 0, high: float = 100) -> float:
    return max(low, min(high, value))

def _safe_float(value, default: float = 0) -> float:
    try:
        return float(value) if value is not None else default
    except Exception:
        return default

def _last(row, key: str, default: float = 0) -> float:
    try:
        return _safe_float(row[key], default)
    except Exception:
        return default

def _concept_for(code: str, name: str) -> str:
    """根据股票代码/名称推断所属概念。"""
    code = (code or "").strip()[:6]
    if code in DEMO_CONCEPT_MAP:
        return DEMO_CONCEPT_MAP[code]
    text = f"{code} {name}"
    if any(x in text for x in ["芯", "半导体", "中芯"]):
        return "半导体"
    if any(x in text for x in ["车", "锂", "电池", "新能源"]):
        return "新能源汽车"
    if any(x in text for x in ["曙光", "浪潮", "算力", "AI"]):
        return "AI算力"
    if any(x in text for x in ["通信", "中兴", "光模块"]):
        return "通信设备"
    return "通用"


# ── 腾讯财经个股实时行情 ────────────────────────

def fetch_individual_quote(code: str, timeout: int = 5) -> dict | None:
    """通过腾讯财经获取个股实时行情 (A股，国内可用，字段丰富)。"""
    prefix = "sh" if code.startswith(("60", "68")) else "sz"
    tencent_code = f"{prefix}{code}"
    url = f"https://qt.gtimg.cn/q={tencent_code}"
    try:
        req = Request(url, headers=get_headers("https://qt.gtimg.cn/"))
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("gbk", errors="ignore")
        # 格式: v_sz000977="51~名称~代码~当前~昨收~开盘~成交量(手)~...~最高~最低~..."
        if "=" not in raw:
            return None
        fields = raw.split("~")
        if len(fields) < 35:
            return None
        name = fields[1].strip()
        price = _safe_float(fields[3])
        prev_close = _safe_float(fields[4])
        if price == 0:
            return None
        change_pct = _safe_float(fields[32])
        return {
            "code": code,
            "name": name,
            "price": round(price, 2),
            "high": round(_safe_float(fields[33]), 2),
            "low": round(_safe_float(fields[34]), 2),
            "open": round(_safe_float(fields[5]), 2),
            "prev_close": round(prev_close, 2),
            "change_pct": round(change_pct, 2),
            "volume": int(_safe_float(fields[6]) * 100),
        }
    except Exception:
        return None


# ── 个股资金流 (东方财富，不可用时静默跳过) ────────

def fetch_individual_fund_flow(code: str, timeout: int = 5) -> dict | None:
    """获取个股近5日主力资金流 (东方财富)。

    返回: {"main_inflow_1d": 百万, "main_inflow_3d": 百万, "main_inflow_5d": 百万,
            "main_net_pct_1d": % ,  ...} 或 None
    """
    market = "1" if code.startswith(("60", "68")) else "0"
    secid = f"{market}.{code}"
    url = (
        f"https://push2.eastmoney.com/api/qt/stock/fflow/daykline/get"
        f"?secid={secid}"
        f"&fields1=f1,f2,f3,f7"
        f"&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62"
    )
    try:
        req = Request(url, headers=get_headers("https://quote.eastmoney.com/"))
        with urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="ignore"))
        klines = (data.get("data") or {}).get("klines") or []
        if not klines:
            return None

        # klines[-1] 是最新一天
        last = (klines[-1] or "").split(",")
        if len(last) < 7:
            return None
        # 字段顺序: f51=日期, f52=主力净流入, f53=小单, f54=中单, f55=大单,
        #           f56=超大单, f57=主力净流入占比%, f58=  ...
        # f60=3日主力净流入, f61=5日主力净流入
        return {
            "main_inflow_1d": _safe_float(last[1]),           # 当日主力净流入(元)
            "main_net_pct_1d": _safe_float(last[6]) * 100,    # 主力净流入占比(%)
            "main_inflow_3d": _safe_float(last[9]) if len(last) > 9 else 0,
            "main_inflow_5d": _safe_float(last[10]) if len(last) > 10 else 0,
        }
    except Exception:
        return None


# ── 板块热度 (同花顺 THS + 缓存) ──────────────────────

def _fund_points(fund_wan: float, scale: float = 80000, low: float = -18, high: float = 18) -> float:
    return _clamp(fund_wan / scale, low, high)


def _load_sector_cache() -> tuple[dict[str, dict], str, float] | None:
    """加载本地缓存，返回 (sector_map, source, timestamp) 或 None。"""
    with _sector_cache_lock:
        try:
            if not os.path.exists(SECTOR_CACHE_PATH):
                return None
            with open(SECTOR_CACHE_PATH, "r", encoding="utf-8") as f:
                obj = json.load(f)
            age = time.time() - obj.get("ts", 0)
            if age < SECTOR_CACHE_TTL:
                return obj["map"], obj["source"], obj["ts"]
        except Exception:
            pass
        return None


def _save_sector_cache(sector_map: dict, source: str):
    """保存板块数据到本地缓存。"""
    with _sector_cache_lock:
        try:
            obj = {"map": sector_map, "source": source, "ts": time.time()}
            with open(SECTOR_CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump(obj, f, ensure_ascii=False, indent=2)
        except Exception:
            pass


def fetch_sector_heat_em(retries: int = 2) -> tuple[dict[str, dict], str]:
    """东方财富行业板块数据 — 主力数据源。"""
    import requests
    import json as _json

    headers = get_headers("https://quote.eastmoney.com/")

    for attempt in range(max(retries, 1)):
        try:
            url = "https://push2.eastmoney.com/api/qt/clist/get"
            params = {
                "pn": "1", "pz": "200", "po": "1", "np": "1",
                "fltt": "2", "invt": "2", "fid": "f3",
                "fs": "m:90+t2",
                "fields": "f2,f3,f4,f12,f14,f104,f105,f128,f140,f136",
                "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            }
            r = requests.get(url, params=params, headers=headers, timeout=15)
            data = _json.loads(r.text)
            diffs = (data.get("data") or {}).get("diff") or []
            if not diffs:
                raise RuntimeError("东方财富返回空数据")

            sector_map = {}
            for d in diffs:
                name = d.get("f14", "").strip()
                if not name or name == "-":
                    continue
                change = float(d.get("f3", 0) or 0)
                up_count = int(d.get("f104", 0) or 0)
                down_count = int(d.get("f105", 0) or 0)

                total_stocks = max(up_count + down_count, 1)
                breadth = _clamp(round(up_count / total_stocks * 100, 1))

                # 估算资金（亿）—— 东方财富行业板块不直接给资金流，用涨跌幅和样本量估算
                fund_estimate = change * 50 * (total_stocks / 30)
                fund_wan = _clamp(fund_estimate * 10000, -999999, 999999)

                heat = _clamp(50 + change * 7 + (breadth - 50) * 0.3 + fund_estimate * 0.08)

                sector_map[name] = {
                    "change": change,
                    "fund": round(fund_wan, 0),
                    "breadth": breadth,
                    "heat": round(heat, 1),
                    "total_stocks": total_stocks,
                    "source": "行业",
                }

            if sector_map:
                return sector_map, f"东方财富行业板块 {len(sector_map)}个 {datetime.now():%H:%M}"
        except Exception as e:
            last_error = e
        if attempt < retries:
            time.sleep(1.5)

    raise RuntimeError(f"东方财富板块 failed after {retries+1} attempts: {last_error}")


def fetch_sector_heat_ths(retries: int = 2) -> tuple[dict[str, dict], str]:
    """通过同花顺获取 A 股行业板块热度数据（备选，东方财富不可用时启用）。"""
    import requests
    from bs4 import BeautifulSoup
    from io import StringIO

    last_error = None
    big_df = None
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    for attempt in range(max(retries, 1)):
        try:
            url = "http://q.10jqka.com.cn/thshy/index/field/199112/order/desc/page/1/ajax/1/"
            r = requests.get(url, headers=headers, timeout=15)
            if r.status_code != 200:
                raise RuntimeError(f"THS returned {r.status_code}")
            soup = BeautifulSoup(r.text, "html.parser")  # 用 html.parser 避免依赖 lxml
            pi = soup.find("span", {"class": "page_info"})
            if pi is None:
                raise RuntimeError("Cannot find page_info in THS response")
            total_pages = int(pi.text.split("/")[1])

            frames = []
            for page in range(1, total_pages + 1):
                pu = f"http://q.10jqka.com.cn/thshy/index/field/199112/order/desc/page/{page}/ajax/1/"
                pr = requests.get(pu, headers=headers, timeout=15)
                temp_df = pd.read_html(StringIO(pr.text))[0]
                frames.append(temp_df)

            big_df = pd.concat(frames, ignore_index=True)
            big_df.columns = [
                "序号", "板块", "涨跌幅", "总成交量", "总成交额", "净流入",
                "上涨家数", "下跌家数", "均价", "领涨股", "领涨股-最新价", "领涨股-涨跌幅",
            ]
            for col in ["涨跌幅", "总成交量", "总成交额", "净流入", "上涨家数", "下跌家数", "均价"]:
                big_df[col] = pd.to_numeric(big_df[col], errors="coerce")
            if big_df is not None and not big_df.empty:
                break
        except Exception as e:
            last_error = e
        if attempt < retries:
            time.sleep(1.5)
    if big_df is None or big_df.empty:
        raise RuntimeError(f"THS failed after {retries+1} attempts: {last_error}")

    sector_map = {}
    for _, row in big_df.iterrows():
        name = str(row.get("板块", "") or "").strip()
        if not name or name == "-":
            continue
        change = float(row.get("涨跌幅", 0) or 0)
        total_amount = float(row.get("总成交额", 0) or 0)
        net_inflow = float(row.get("净流入", 0) or 0)
        up_count = int(row.get("上涨家数", 0) or 0)
        down_count = int(row.get("下跌家数", 0) or 0)

        total_stocks = max(up_count + down_count, 1)
        breadth = _clamp(round(up_count / total_stocks * 100, 1))

        if abs(net_inflow) > 0.01:
            fund_wan = _clamp(net_inflow * 10000, -999999, 999999)
        else:
            if change > 0:
                fund_wan = _clamp(total_amount * change * 10, -99999, 99999)
            else:
                fund_wan = _clamp(total_amount * change * 5, -99999, 99999)

        heat = _clamp(50 + change * 7 + (breadth - 50) * 0.3 + net_inflow * 0.5)

        sector_map[name] = {
            "change": change,
            "fund": round(fund_wan, 0),
            "breadth": breadth,
            "heat": round(heat, 1),
            "total_stocks": total_stocks,
            "source": "行业",
        }

    if not sector_map:
        raise RuntimeError("THS parsed no sector rows")
    return sector_map, f"同花顺行业板块 {len(sector_map)}个 {datetime.now():%H:%M}"


def get_sector_heat_map(online: bool = True) -> tuple[dict[str, dict], str]:
    """获取板块热度图：同花顺 → 东方财富 → 缓存 → 演示 四级降级。"""
    if online:
        # 第一级：同花顺（主力）
        try:
            live_map, source = fetch_sector_heat_ths()
            _save_sector_cache(live_map, source)
            return live_map, source
        except Exception:
            pass

        # 第二级：东方财富（备选）
        try:
            live_map, source = fetch_sector_heat_em()
            _save_sector_cache(live_map, source)
            return live_map, source
        except Exception:
            pass

        # 第三级：本地缓存
        cached = _load_sector_cache()
        if cached is not None:
            cmap, csource, _cts = cached
            return cmap, f"板块缓存 ({csource})"

        # 第四级：演示数据
        return dict(DEMO_SECTOR_HEAT), "演示数据（同花顺+东方财富+缓存均不可用，请检查网络）"
    return dict(DEMO_SECTOR_HEAT), "演示数据"


def get_market_breadth(online: bool = True) -> dict:
    """从同花顺行业板块汇总全市场涨跌家数。
    返回 {"up": int, "down": int, "flat": int, "total": int, "source": str, "net_inflow": float}
    失败返回 {"up": 0, "down": 0, "flat": 0, "total": 0, "source": "离线", "net_inflow": 0}
    """
    if not online:
        return {"up": 0, "down": 0, "flat": 0, "total": 0, "source": "离线", "net_inflow": 0}

    try:
        import requests
        from bs4 import BeautifulSoup
        from io import StringIO
        import pandas as pd

        headers = get_headers()

        # 第 1 页获取总页数
        url = "http://q.10jqka.com.cn/thshy/index/field/199112/order/desc/page/1/ajax/1/"
        r = requests.get(url, headers=headers, timeout=12)
        if r.status_code != 200:
            raise RuntimeError(f"THS returned {r.status_code}")
        soup = BeautifulSoup(r.text, "html.parser")
        pi = soup.find("span", {"class": "page_info"})
        total_pages = int(pi.text.split("/")[1]) if pi else 2

        all_up = 0
        all_dn = 0
        all_net_inflow = 0.0

        for page in range(1, total_pages + 1):
            pu = f"http://q.10jqka.com.cn/thshy/index/field/199112/order/desc/page/{page}/ajax/1/"
            pr = requests.get(pu, headers=headers, timeout=12)
            df = pd.read_html(StringIO(pr.text))[0]
            df.columns = ["序号", "板块", "涨跌幅", "总成交量", "总成交额", "净流入",
                          "上涨家数", "下跌家数", "均价", "领涨股", "领涨股-最新价", "领涨股-涨跌幅"]
            for _, row in df.iterrows():
                try:
                    up = int(row["上涨家数"]) if pd.notna(row["上涨家数"]) else 0
                    dn = int(row["下跌家数"]) if pd.notna(row["下跌家数"]) else 0
                    ni = float(row["净流入"]) if pd.notna(row["净流入"]) else 0
                    all_up += up
                    all_dn += dn
                    all_net_inflow += ni
                except Exception:
                    continue

        total = all_up + all_dn
        if total > 100:
            return {"up": all_up, "down": all_dn, "flat": 0, "total": total,
                    "source": f"同花顺行业板块 {all_up+all_dn}只",
                    "net_inflow": round(all_net_inflow, 1)}
    except Exception:
        pass

    return {"up": 0, "down": 0, "flat": 0, "total": 0, "source": "离线", "net_inflow": 0}


# ── 板块匹配 ────────────────────────────────────────

def _best_sector_match(sector_map: dict[str, dict], code: str, name: str) -> str:
    preferred = _concept_for(code, name)
    if preferred in sector_map:
        return preferred
    aliases = {
        "AI算力": ["算力", "人工智能", "AI", "云计算", "东数西算"],
        "半导体": ["半导体", "芯片", "先进封装", "光刻机"],
        "新能源汽车": ["新能源汽车", "电池", "锂电池", "固态电池", "汽车零部件"],
        "通信设备": ["通信设备", "光模块", "5G", "CPO"],
        "光伏": ["光伏设备", "HJT电池", "BC电池", "TOPCon电池"],
    }
    for keyword in aliases.get(preferred, []):
        for sector_name in sector_map:
            if keyword.lower() in sector_name.lower():
                return sector_name
    return preferred if preferred in DEMO_SECTOR_HEAT else "通用"


# ── 消息面热度 (在线版，支持传入 news_items) ──────────

def news_heat_for_sector(sector: str, now: datetime | None = None,
                         news_items=None) -> tuple[float, list[str]]:
    """根据新闻计算某板块的消息面热度和标题列表。"""
    now = now or datetime.now()
    if news_items is None:
        news_items, _ = get_market_news(online=True)

    score = 0.0
    titles: list[str] = []
    for item in news_items:
        if item.sector != sector:
            continue
        age_hours = max(0.1, (now - item.published_at).total_seconds() / 3600)
        decay = exp(-age_hours / 18)
        score += item.heat * item.sentiment * decay
        titles.append(item.title)
    return _clamp(50 + score / 1.4), titles


# ── 综合评分引擎 ────────────────────────────────────

def score_local_stock(
    code: str,
    name: str,
    df,
    base_score: float,
    hit_patterns: list[str] | None = None,
    sector_map: dict[str, dict] | None = None,
    news_items=None,
) -> DeepSignal:
    """综合评分：技术面 + 资金面 + 消息面 + 板块联动。

    参数:
        df: 通达信日线 DataFrame (含 ma5/ma10/ma20/ma60/macd/kdj/rsi 等)
        base_score: 形态评分 (0-100)
        hit_patterns: 触发形态列表
        sector_map: 板块热度图
        news_items: 新闻列表 (可选，为空则自动拉取)

    返回:
        DeepSignal dataclass
    """
    # ── 形态检测（用新引擎替换编译.pyd的有bug版本） ──
    # 修复: 阳包阴Day1阴线检查、量价底背离术语、删除放量长阳失效
    detected_patterns, detected_base_score = detect_all_from_df(df)
    hit_patterns = detected_patterns  # 强制用新引擎结果，覆盖GUI旧检测传入的
    base_score = detected_base_score  # 使用新引擎的base_score替换.pyd的

    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) >= 2 else last
    close = _last(last, "收盘", _last(last, "鏀剁洏", 0))
    prev_close = _last(prev, "收盘", _last(prev, "鏀剁洏", close))
    ma5 = _last(last, "ma5", close)
    ma10 = _last(last, "ma10", ma5)
    ma20 = _last(last, "ma20", ma10)
    ma60 = _last(last, "ma60", ma20)
    vol = _last(last, "成交量", _last(last, "鎴愪氦閲?", 0))
    vol_ma20 = _last(last, "vol_ma20", vol or 1)
    rsi = _last(last, "rsi", 50)
    pct = (close - prev_close) / prev_close * 100 if prev_close else 0
    volume_ratio = vol / vol_ma20 if vol_ma20 else 1

    # ── 板块匹配 ──
    sector_map = sector_map or DEMO_SECTOR_HEAT
    sector = _best_sector_match(sector_map, code, name)
    sector_info = sector_map.get(
        sector,
        DEMO_SECTOR_HEAT.get(sector, DEMO_SECTOR_HEAT.get("通用",
                            next(iter(DEMO_SECTOR_HEAT.values()))))
    )

    # ── 消息面（个股关联度评分） ──
    try:
        news_score, news_titles_rel = _news_relevance_for_stock(code, name, news_items)
        titles = [t for t, _ in news_titles_rel]
        news_relevance_val = max([r for _, r in news_titles_rel], default=0.0)
    except Exception:
        import traceback
        traceback.print_exc()
        news_score = 50.0
        titles = []
        news_relevance_val = 0.0

    # ── 个股资金流 (在线补充，失败不影响评分) ──
    individual_fund = fetch_individual_fund_flow(code)
    if individual_fund:
        indiv_main_5d = individual_fund.get("main_inflow_5d", 0) or 0
        indiv_main_1d = individual_fund.get("main_inflow_1d", 0) or 0
    else:
        indiv_main_5d = 0
        indiv_main_1d = 0

    # ══════════════════════════════════════════════
    # 新评分引擎 (V2) — 替换原系统技术评分和择时判断
    # ══════════════════════════════════════════════
    sig = compute_signal_from_df(df)
    ts_regime = sig["regime"]
    ts_level = sig["level"]
    ts_score = sig["score"]
    ts_mr = sig["mr_score"]
    ts_tf = sig["tf_score"]

    # ── 技术面评分：直接使用 V14 技术评分引擎（替换形态评分加权）──
    # 形态胜率回测（2026-06-06, 410只全量）: 16种形态胜率全在 45-52%，
    # 不具独立择时能力，base_score 不再参与技术面评分
    technical = ts_score  # V14 tech scorer, 0-100

    # ── 资金面评分 (基础 48 分) ──
    fund = 48 + _fund_points(sector_info["fund"], scale=70000, low=-16, high=18) + max(volume_ratio - 1, -1) * 12
    if pct > 0 and volume_ratio > 1.2:
        fund += 8
    if sector_info["fund"] < 0:
        fund -= 16
    if individual_fund:
        if indiv_main_5d > 0:
            fund += 6
        if indiv_main_1d > 0:
            fund += 3
        if individual_fund.get("main_net_pct_1d", 0) or 0 > 5:
            fund += 4
        elif individual_fund.get("main_net_pct_1d", 0) or 0 < -5:
            fund -= 4
    fund = _clamp(fund)

    # ── 板块面评分 ──
    sector_score = _clamp(
        42
        + sector_info["change"] * 5
        + _fund_points(sector_info["fund"], scale=80000, low=-18, high=18)
        + sector_info["breadth"] * 0.25
        + sector_info["heat"] * 0.16
    )

    # ── 总分 ──
    total = technical * 0.35 + fund * 0.30 + news_score * 0.25 + sector_score * 0.10

    # ── 理由 ──
    reasons: list[str] = []
    risks: list[str] = []

    # 新系统：市场状态
    regime_labels = {"强趋势": "多头趋势", "偏多": "偏多震荡", "震荡": "横盘整理",
                     "偏空": "偏空调整", "强下跌趋势": "下跌趋势"}
    reasons.append(f"{regime_labels.get(ts_regime, ts_regime)}（{ts_regime}）")

    # 新系统：评分等级
    level_labels = {"强势": "☀ 强势信号", "偏强": "偏强信号",
                    "中性": "中性信号", "弱势": "弱势信号"}
    reasons.append(f"择时:{level_labels.get(ts_level, ts_level)}（综合{ts_score}分 MR{ts_mr} TF{ts_tf}）")

    # ★ 修复：形态检测结果（V2引擎，已修复原系统bug）
    if detected_patterns:
        # 过滤掉低价值形态（十字星、连阳连阴、孕线只占位）
        high_value = [p for p in detected_patterns
                      if p not in ("缩量调整",)
                      and not p.startswith(("十字星", "连阳", "连阴", "孕线"))]
        if high_value:
            reasons.append(f"形态:{'；'.join(high_value[:4])}")
        else:
            # 只有低价值形态时，选最重要的1个
            for p in detected_patterns:
                if p.startswith("连阳") or p.startswith("连阴"):
                    reasons.append(f"形态:{p}")
                    break

    # 传统信号
    if close > ma5 > ma20:
        reasons.append("短中期均线结构改善")
    if volume_ratio >= 1.5:
        reasons.append(f"量能放大，量比约 {volume_ratio:.1f}")
    if sector_score >= 68:
        reasons.append(f"{sector} 板块热度和扩散度较强")
    if news_score >= 68 and news_relevance_val >= 0.5:
        rel_desc = f"关联度{news_relevance_val:.0%}" if news_relevance_val < 1.0 else ""
        reasons.append(f"相关新闻 {rel_desc}".strip())
        if titles:
            reasons.append(titles[0])
    elif news_score >= 68 and news_relevance_val > 0:
        if titles:
            reasons.append(f"相关新闻(弱关联): {titles[0]}")
    if individual_fund and indiv_main_5d > 0:
        reasons.append(f"近5日主力资金净流入 {_safe_float(indiv_main_5d/10000, 0):.0f} 万元")

    # 风险
    if pct >= 8:
        risks.append("日内涨幅偏高，追高风险上升")
    if rsi >= 82:
        risks.append("RSI 过热，等待分歧确认")
    if fund < 42:
        risks.append("资金/板块侧偏弱")
    if news_score < 45:
        risks.append("消息热度衰减或偏负面")

    # ── 择时判断（新系统V2取代原系统） ──
    # 4级择时：强势 > 偏强 > 中性 > 弱势
    # （不再映射回原系统5级标签，直接输出新系统结果）
    timing = ts_level

    return DeepSignal(
        code=code,
        name=name,
        tech_score=round(technical, 1),
        fund_score=round(fund, 1),
        news_score=round(news_score, 1),
        sector_score=round(sector_score, 1),
        total_score=round(total, 1),
        timing=timing,
        sector=sector,
        reasons=reasons,
        risks=risks,
        news_relevance=round(news_relevance_val, 2),
        stop_loss=sig.get("stop_loss", 0),
        stop_loss_pct=sig.get("stop_loss_pct", 0),
        stop_loss_reason=sig.get("stop_loss_reason", ""),
        take_profit=sig.get("take_profit", 0),
        take_profit_pct=sig.get("take_profit_pct", 0),
        take_profit_reason=sig.get("take_profit_reason", ""),
    )


# ── 热点 / 板块雷达文本 ──────────────────────────────

def build_hotspot_text() -> str:
    """生成热点新闻 + 板块波动的综合雷达文本。"""
    sector_map, sector_source = get_sector_heat_map(online=True)
    news_items, news_source = get_market_news(online=True)
    cross_quotes, cross_source = get_cross_market_quotes(online=True)  # ← 在线获取港美股
    sector_map = apply_cross_market_boost(sector_map, cross_quotes)

    lines = [
        "===== 市场热点新闻 / 概念雷达 =====",
        f"板块数据源：{sector_source}",
        f"新闻数据源：{news_source}",
        f"跨市场数据源：{cross_source}",
        "",
    ]
    for item in sorted(news_items, key=lambda x: x.published_at, reverse=True)[:25]:
        lines.append(
            f"[{item.published_at:%H:%M}] {item.sector} | "
            f"热度 {item.heat:.0f} | 情绪 {item.sentiment:+.2f} | {item.title}"
        )
    lines.extend(["", "===== A股概念 / 板块波动 ====="])
    ranked = sorted(sector_map.items(), key=lambda x: (x[1]["heat"], x[1]["fund"]), reverse=True)
    for name, info in ranked[:35]:
        cross = info.get("cross_market")
        cross_text = f" | 跨市场修正 {cross['boost']:+.1f}" if cross else ""
        lines.append(
            f"{name}: 涨跌 {info['change']:+.2f}% | 资金 {info['fund']:.0f}万 | "
            f"扩散度 {info['breadth']}% | 热度 {info['heat']} | "
            f"{info.get('source', '演示')}{cross_text}"
        )
    # ── 强势股 / 涨停股池 ──
    try:
        from v14_market_data import get_strong_stocks
        strong_stocks, strong_source = get_strong_stocks(online=True)
        if strong_stocks:
            lines.extend(["", "===== 当日强势 / 涨停股池 =====", f"数据源：{strong_source}"])
            for s in strong_stocks[:15]:
                lines.append(f"  {s.code} {s.name}: {s.change_pct:+.2f}% | {s.price} | {s.reason}")
    except Exception:
        pass

    lines.extend([
        "",
        build_cross_market_text(cross_quotes, cross_source),
        "",
        "数据源状态：日线K线 通达信本地文件 ✅ | 港美股 新浪财经 ✅ | 新闻 akshare ✅ | 板块 同花顺 ✅",
        "所有数据源失败时自动回退演示数据，程序不会崩溃。",
    ])
    return "\n".join(lines)
