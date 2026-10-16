# -*- coding: utf-8 -*-
"""
V15 Plus+ 板块动力评分模块
==========================
独立插件，fail-safe，不接入 V15 评分管道。
板块动力分作为独立指标展示，数据来自通达信本地板块日线。

核心公式:
  板块动力分 = T1_score x 0.6 + T2_score x 0.4 + distill_bonus

  T1_score = 标准化 CDF(z-score) → [0,100]    (板块资金占全市场比重变化)
  T2_score = 成交量变化率 → [0,100]           (板块自身量能变化)
  distill_bonus = 蒸馏命中 +3~5 分          (利好/热点板块加成)
  赔率系数 = 只涨不罚 (低位放大，高位 1.0 不扣)

4 步链路:
  个股 -> 东方财富行业 -> 通达信板块代码 -> .day 日线 -> 聚合
"""
from __future__ import annotations
import os
import struct
import json
import time
from datetime import datetime, timedelta

# ══════════════════════════════════════════════════════
# 模块级缓存
# ══════════════════════════════════════════════════════

# 板块动力结果缓存（扫描级，同一板块只算一次）
_power_cache: dict[str, dict] = {}
_cache_date: str = ""  # 缓存日期，跨日自动清空

# 扫描级预计算：17个板块各算一次
_all_sector_powers: dict[str, dict] = {}

TDX_BASE = os.path.join(os.environ.get("TDX_ROOT_PATH", r"D:\通达信"), "vipdoc", "sh", "lday")

# ══════════════════════════════════════════════════════
# 0. T1 评分：板块资金占全市场比重 z-score
# ══════════════════════════════════════════════════════

_t1_cache: dict | None = None  # {tdx_code: T1_score}
_t1_cache_date: str = ""  # 缓存日期，跨日自动重算
_t1_detail: dict[str, dict] = {}  # {tdx_code: {z_60, z_240, z, r5, mean_60, mean_240, judgment}}


def _get_today_str() -> str:
    """返回 YYYYMMDD 格式的今日日期字符串。"""
    from datetime import datetime as _dt
    return _dt.now().strftime("%Y%m%d")


def _compute_all_t1_scores() -> None:
    """一次性算所有 EM 映射板块的 T1 分数（成交额占比 z-score → CDF → 0-100）。

    分母固定为沪深两市总成交额（上证 999999 + 深证 399001），
    分子为各板块自身成交额（.day 文件 bytes 20-23 float32）。
    _em_to_tdx 映射到的全部板块（行业+概念）一视同仁。
    每日首次调用，结果缓存到 _t1_cache。跨日自动重算。
    """
    global _t1_cache, _t1_cache_date
    today = _get_today_str()
    if _t1_cache is not None and _t1_cache_date == today:
        return  # 已缓存且未跨日
    _t1_cache = None  # 跨日或首次，强制重算

    _t1_cache = {}

    LOOKBACK = 240

    # ── 1. 读沪深两市总成交额（分母：上证 + 深证）──
    market_ts: dict[int, float] = {}
    for idx_code, idx_market in [("999999", "sh"), ("399001", "sz")]:
        path = os.path.join(r"D:\通达信\vipdoc", idx_market, "lday",
                           f"{idx_market}{idx_code}.day")
        if not os.path.exists(path):
            continue
        try:
            with open(path, "rb") as f:
                d = f.read()
            n = len(d) // 32
            start = max(0, n - LOOKBACK)
            for i in range(start, n):
                b = d[i * 32:(i + 1) * 32]
                dt = struct.unpack("<I", b[0:4])[0]
                amt = struct.unpack("<f", b[20:24])[0]
                market_ts[dt] = market_ts.get(dt, 0.0) + amt
        except Exception:
            pass

    all_dates = sorted(market_ts.keys())
    if len(all_dates) < 180:
        return  # 数据不足

    # ── 2. 获取需要计算 T1 的全部板块代码 ──
    # 层次1: _em_to_tdx（127个东方财富行业 → 121个通达信板块）
    _build_em_tdx_map()
    tdx_set: set[str] = set()
    if _em_to_tdx:
        tdx_set = set(_em_to_tdx.values())

    # 层次2: NEWS_TO_TDX + KEYWORD_TDX_ROUTE（17个新闻板块+二级路由）
    # 这些是概念板块（如 AI算力 sh880669），_em_to_tdx 不会直接映射，
    # 但个股通过 news 兜底路由时可能匹配，也需要真实的 T1 分数
    for code in NEWS_TO_TDX.values():
        if code:
            tdx_set.add(code)
    for routes in KEYWORD_TDX_ROUTE.values():
        for code in routes.values():
            if code:
                tdx_set.add(code)

    TDX_SYNTHETIC = {
        "sh880981", "sh880982", "sh880983", "sh880984", "sh880985",
        "sh880986", "sh880987", "sh880988", "sh880989", "sh880990",
        "sh880991", "sh880992", "sh880993",
    }

    # ── 3. 逐板块计算成交额占比 z-score ──
    TDX_BASE_SH = r"D:\通达信\vipdoc\sh\lday"
    for code in sorted(tdx_set):
        if not code or code in TDX_SYNTHETIC:
            continue
        path = os.path.join(TDX_BASE_SH, f"{code}.day")
        if not os.path.exists(path):
            continue
        try:
            with open(path, "rb") as f:
                sd = f.read()
        except Exception:
            continue

        sn = len(sd) // 32
        s_start = max(0, sn - LOOKBACK)
        sector_ts: dict[int, float] = {}
        for i in range(s_start, sn):
            b = sd[i * 32:(i + 1) * 32]
            dt = struct.unpack("<I", b[0:4])[0]
            amt = struct.unpack("<f", b[20:24])[0]
            sector_ts[dt] = amt

        shares = []
        for d in all_dates:
            if d in sector_ts and d in market_ts and market_ts[d] > 0:
                shares.append(sector_ts[d] / market_ts[d] * 100)

        if len(shares) < 180:
            continue

        recent5 = sum(shares[-5:]) / 5 if len(shares) >= 5 else shares[-1]

        # ── 双周期 z-score 混合：60天短窗 + 240天长窗，各50% ──
        #
        # 为什么不用纯240天？
        #   板块半年前火爆、之后衰退，均值会被历史高点永久拉高。
        #   即便资金近期持续回流，recent5 也永远够不到 240 天均值，z-score 判"弱"。
        #
        # 为什么用 60 天而不是 30 天？
        #   30 天太短——半导体这种高位横盘的板块，30 天均值≈recent5，
        #   z_30 接近 0，反而把 z_240 的+2.0 拖成+1.0，劣化信号。
        #   60 天够长，横盘不会拖累；又够短，反转能被捕获。
        #
        # 用法：看 z_60 和 z_240 的方向是否一致——
        #   一致看弱 → 真弱板块，分数如实低
        #   一致看强 → 真强板块，分数如实高
        #   z_240 弱、z_60 强 → 资金在回流，60 天窗救起分数（如证券）
        def _z_scores(vals, window):
            w = vals[-window:] if len(vals) >= window else vals
            w_mean = sum(w) / len(w)
            sq = [(s - w_mean) ** 2 for s in w]
            w_std = (sum(sq) / len(w)) ** 0.5 if sum(sq) > 1e-12 else 1.0
            return (recent5 - w_mean) / w_std

        # 240天窗均值与标准差
        mean_240 = sum(shares) / len(shares)
        sq_diffs = [(s - mean_240) ** 2 for s in shares]
        var_240 = sum(sq_diffs) / len(shares)
        std_240 = var_240 ** 0.5 if var_240 > 1e-12 else 1.0

        z_60 = _z_scores(shares, 60) if len(shares) >= 60 else 0
        z_240 = (recent5 - mean_240) / std_240
        z = z_60 * 0.5 + z_240 * 0.5

        # 判断信号
        if z_240 < -0.5 and z_60 > 0.3:
            judgment = "资金回流"
        elif z_240 > 0.5 and z_60 < -0.3:
            judgment = "资金流出"
        elif z_240 < -0.5 and z_60 < -0.5:
            judgment = "一致看弱"
        elif z_240 > 1 and z_60 > 0.5:
            judgment = "一致看强"
        else:
            judgment = "中性"

        _t1_cache[code] = _normal_cdf_score(z)

        mean_60 = sum(shares[-60:]) / 60 if len(shares) >= 60 else recent5
        _t1_detail[code] = {
            "z_60": round(z_60, 2), "z_240": round(z_240, 2), "z": round(z, 2),
            "r5": round(recent5, 2), "mean_60": round(mean_60, 2),
            "mean_240": round(mean_240, 2), "judgment": judgment,
        }

    _t1_cache_date = today  # 标记缓存日期

def _normal_cdf_score(z: float) -> float:
    """标准正态 CDF(z) 映射到 [0, 100]。
    z=-3 → 0, z=0 → 50, z=+3 → 100。
    """
    import math
    # ϕ(z) = 0.5 * (1 + erf(z / sqrt(2)))
    cdf = 0.5 * (1 + math.erf(z / (2 ** 0.5)))
    return round(cdf * 100, 1)


def _get_t1_score(tdx_code: str) -> float:
    """获取某板块的 T1 分（双周期 z-score 混合）。"""
    if _t1_cache is None:
        _compute_all_t1_scores()
    if _t1_cache is None:
        return 50.0
    return _t1_cache.get(tdx_code, 50.0)


def _get_t1_detail(tdx_code: str) -> dict | None:
    """获取某板块 T1 的详细分解（z_60/z_240/r5/均值/判断）。"""
    if _t1_cache is None:
        _compute_all_t1_scores()
    return _t1_detail.get(tdx_code)



# ── 板块资金流向图表数据缓存 ──
_flow_chart_cache: dict | None = None
_flow_chart_cache_date: str = ""


def get_flow_chart_data() -> dict:
    """返回板块资金流向图表数据。首次调用时计算并缓存。
    跨日自动清空，避免使用过期板块数据。
    """
    global _flow_chart_cache, _flow_chart_cache_date
    today = _get_today_str()
    if _flow_chart_cache is not None and _flow_chart_cache_date == today:
        return _flow_chart_cache
    _flow_chart_cache = None  # 跨日或首次，强制重算
    _flow_chart_cache_date = today

    import struct as _struct
    from collections import defaultdict

    # 只取东方财富映射到的板块（去重）
    _build_em_tdx_map()
    mapped_set = set(_em_to_tdx.values()) if _em_to_tdx else set()

    # 加载板块名
    import json as _json
    try:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        spath = os.path.join(base, "_internal", "sector_map.json")
        with open(spath, "r", encoding="utf-8") as f:
            nc = _json.load(f).get("code2name", {})
    except Exception:
        nc = {}

    TDX_SYNTHETIC = {
        "sh880981","sh880982","sh880983","sh880984","sh880985",
        "sh880986","sh880987","sh880988","sh880989","sh880990",
        "sh880991","sh880992","sh880993",
    }

    hy_codes = []
    hy_names = {}
    for code in mapped_set:
        if code and code not in TDX_SYNTHETIC and code in nc:
            hy_codes.append(code)
            hy_names[code] = nc[code] if isinstance(nc[code], str) else nc[code].get('name', code)

    if not hy_codes:
        _flow_chart_cache = {"daily": [], "weekly": [], "daily_dz": [], "weekly_dz": []}
        return _flow_chart_cache

    LOOKBACK = 240

    # 分母：沪深两市总成交额
    market_ts: dict[int, float] = {}
    for idx_code, idx_market in [("999999", "sh"), ("399001", "sz")]:
        idx_path = os.path.join(r"D:\通达信\vipdoc", idx_market, "lday",
                               f"{idx_market}{idx_code}.day")
        if not os.path.exists(idx_path):
            continue
        try:
            with open(idx_path, "rb") as f:
                d = f.read()
            n_idx = len(d) // 32
            start_idx = max(0, n_idx - LOOKBACK)
            for i in range(start_idx, n_idx):
                b = d[i * 32:(i + 1) * 32]
                dt = _struct.unpack("<I", b[0:4])[0]
                amt = _struct.unpack("<f", b[20:24])[0]
                market_ts[dt] = market_ts.get(dt, 0.0) + amt
        except Exception:
            pass

    all_dates = sorted(market_ts.keys())
    if len(all_dates) < 60:
        _flow_chart_cache = {"daily": [], "weekly": [], "daily_dz": [], "weekly_dz": []}
        return _flow_chart_cache

    # 读各板块成交额
    sector_daily = defaultdict(dict)
    for code in hy_codes:
        path = os.path.join(TDX_BASE, f"{code}.day")
        if not os.path.exists(path):
            continue
        try:
            with open(path, "rb") as f:
                sd = f.read()
            sn = len(sd) // 32
            s_start = max(0, sn - LOOKBACK)
            for i in range(s_start, sn):
                b = sd[i * 32:(i + 1) * 32]
                dt = _struct.unpack("<I", b[0:4])[0]
                amt = _struct.unpack("<f", b[20:24])[0]
                sector_daily[code][dt] = amt
        except Exception:
            continue

    # ── 日维度：3日加权均值 vs 前10日均值 ──
    # recent3 = 当日×0.5 + 昨×0.3 + 前×0.2（EMA风格，当日权重最大）
    # prior10 = 前11-20天简单均值（对比基线）
    daily_results = []
    daily_dz_list = []
    for code in hy_codes:
        if code not in sector_daily:
            continue
        shares = []
        for d in all_dates:
            if d in sector_daily[code] and d in market_ts and market_ts[d] > 0:
                shares.append(sector_daily[code][d] / market_ts[d] * 100)
        if len(shares) < 30:
            continue
        mean_s = sum(shares) / len(shares)
        sq_diffs = [(s - mean_s) ** 2 for s in shares]
        var_s = sum(sq_diffs) / len(shares)
        std_s = var_s ** 0.5 if var_s > 1e-12 else 0
        if std_s < 1e-6:
            continue
        recent3 = shares[-1] * 0.5 + shares[-2] * 0.3 + shares[-3] * 0.2
        prior10 = sum(shares[-13:-3]) / 10 if len(shares) >= 13 else mean_s
        z_curr = (recent3 - mean_s) / std_s
        z_prev = (prior10 - mean_s) / std_s
        dz = z_curr - z_prev
        daily_results.append((round(recent3, 2), round(z_curr, 2), code,
                              hy_names.get(code, code), round(dz, 2), round(prior10, 2)))
        if abs(dz) >= 0.25:
            daily_dz_list.append((hy_names.get(code, code), round(z_curr, 2),
                                  round(dz, 2), round(recent3, 2), round(prior10, 2)))

    # ── 周维度：自然周 vs 前4周均值 ──
    # 用 isocalendar 切自然周（周一~周日），最新一周 vs 前4周平均
    weekly_totals = defaultdict(float)
    weekly_sector = defaultdict(lambda: defaultdict(float))
    for d in all_dates:
        date_str = str(d)
        try:
            yr = int(date_str[:4]); m = int(date_str[4:6]); dy = int(date_str[6:8])
            from datetime import date as _date
            iso = _date(yr, m, dy).isocalendar()
            week_key = iso[0] * 100 + iso[1]  # 2026 * 100 + 25 = 202625
        except Exception:
            week_key = d // 100 * 100 + (d % 100) // 7 * 7  # fallback
        for code in hy_codes:
            if d in sector_daily[code]:
                v = sector_daily[code][d]
                weekly_totals[week_key] += v
                weekly_sector[code][week_key] += v

    all_weeks = sorted(weekly_totals.keys())
    weekly_results = []
    weekly_dz_list = []
    for code in hy_codes:
        if code not in weekly_sector:
            continue
        shares = []
        for w in all_weeks:
            if w in weekly_sector[code] and weekly_totals[w] > 0:
                shares.append(weekly_sector[code][w] / weekly_totals[w] * 100)
        if len(shares) < 20:
            continue
        mean_s = sum(shares) / len(shares)
        sq_diffs = [(s - mean_s) ** 2 for s in shares]
        var_s = sum(sq_diffs) / len(shares)
        std_s = var_s ** 0.5 if var_s > 1e-12 else 0
        if std_s < 1e-6:
            continue
        # 最新自然周 vs 前4周平均
        recent1 = shares[-1]
        prior4 = sum(shares[-5:-1]) / 4 if len(shares) >= 5 else mean_s
        z_curr = (recent1 - mean_s) / std_s
        z_prev = (prior4 - mean_s) / std_s
        dz = z_curr - z_prev
        weekly_results.append((round(recent1, 2), round(z_curr, 2), code,
                               hy_names.get(code, code), round(dz, 2), round(prior4, 2)))
        if abs(dz) >= 0.25:
            weekly_dz_list.append((hy_names.get(code, code), round(z_curr, 2),
                                   round(dz, 2), round(recent1, 2), round(prior4, 2)))

    _flow_chart_cache = {
        "daily": daily_results,
        "weekly": weekly_results,
        "daily_dz": daily_dz_list,
        "weekly_dz": weekly_dz_list,
    }
    return _flow_chart_cache
# ══════════════════════════════════════════════════════

# 79 个东方财富细行业 -> 17 个新闻板块，按关键词归类
_INDUSTRY_KEYWORDS = [
    ("半导体", ["半导体", "芯片", "集成电路", "光刻"]),
    ("AI算力", ["IT服务", "软件", "人工智能", "AI", "算力", "云计算", "大数据",
               "通信服务", "通信设备", "通信", "计算机", "信息", "互联网",
               "电子", "元件", "PCB", "LED", "光学", "光电子",
               "消费电子", "电子制造", "自动化", "机器人"]),
    ("新能源汽车", ["汽车", "新能源车", "电池", "锂电", "储能", "充电",
                   "电机", "电控", "燃料电池", "固态电池"]),
    ("光伏", ["光伏", "太阳能", "逆变器", "硅", "组件"]),
    ("通信设备", ["通信", "5G", "光模块", "光纤", "CPO"]),
    ("证券金融", ["证券", "银行", "保险", "金融", "多元金融", "期货", "信托"]),
    ("能源", ["煤炭", "石油", "石化", "油气", "天然气", "煤", "油"]),
    ("军工", ["军工", "航天", "航空", "国防", "卫星", "无人机", "船舶", "兵器"]),
    ("钢铁", ["钢铁", "钢材", "螺纹", "铁矿石"]),
    ("房地产", ["房地产", "地产", "物业", "园区", "商业地产"]),
    ("数字金融", ["数字货币", "区块链", "Web3", "金融科技"]),
    ("医药", ["医药", "医疗", "药", "生物", "化学制药", "中药", "医",
             "创新药", "CXO", "器械", "诊断", "检测"]),
    ("消费", ["白酒", "食品", "饮料", "旅游", "免税", "零售", "家电",
             "纺织", "服装", "日化", "乳业", "啤酒", "调味品", "养殖",
             "农业", "农产品", "渔业", "餐饮", "酒店", "超市",
             "消费", "酒", "茅台"]),
    ("电力", ["电力", "电网", "发电", "水电", "火电", "核电", "风电",
             "光伏", "绿电", "特高压", "储能"]),
    ("有色", ["有色", "金属", "铜", "铝", "黄金", "稀土", "钨", "锡", "镍", "锂", "钴"]),
    ("机械", ["机械", "工程", "机床", "工业", "制造", "设备", "重工", "自动化"]),
]


def _clamp(v, lo=0, hi=100):
    return max(lo, min(hi, v))


# ── 蒸馏信号缓存 ──
_distill_adjust_cache: dict = {}
_distill_adjust_time: float = 0.0
_DISTILL_CACHE_TTL = 300  # 5分钟内不重复读 DB


def _get_distill_adjustment(news_sector: str) -> float:
    """从今日蒸馏简报中读取该板块的利好/利空信号，返回情绪分调整系数。

    逻辑：
    - 蒸馏 good_news 提到该板块 → ×1.25（利多加分）
    - 蒸馏 bad_news 提到该板块  → ×0.75（利空减分）
    - 同时有利好和利空         → ×1.00（互抵）
    - 蒸馏 hot_themes 里是该板块 → ×1.15（热点关注）
    - 蒸馏未提及                → ×1.00（不改）

    只读取今日最新一版蒸馏结果。有缓存 5 分钟。
    """
    global _distill_adjust_cache, _distill_adjust_time
    import time as _time

    now = _time.time()
    if news_sector in _distill_adjust_cache and (now - _distill_adjust_time) < _DISTILL_CACHE_TTL:
        return _distill_adjust_cache[news_sector]

    multiplier = 1.0
    try:
        import json, sqlite3, os
        from datetime import datetime as _dt

        db = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "data", "news_distill.db")
        if not os.path.exists(db):
            _distill_adjust_cache[news_sector] = multiplier
            _distill_adjust_time = now
            return multiplier

        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        today = _dt.now().strftime("%Y-%m-%d")
        row = conn.execute(
            "SELECT brief_json, updated_at FROM daily_briefs "
            "WHERE date = ? ORDER BY seq DESC LIMIT 1",
            (today,)
        ).fetchone()
        conn.close()

        if not row:
            _distill_adjust_cache[news_sector] = multiplier
            _distill_adjust_time = now
            return multiplier

        brief = json.loads(row["brief_json"])
        sector_lower = news_sector.lower()

        # ── 检查 good_news ──
        good_mention = False
        for g in brief.get("good_news", []) or []:
            impact = (g.get("impact", "") or "").lower()
            event = (g.get("event", "") or "").lower()
            if sector_lower in impact or sector_lower in event:
                good_mention = True
                break

        # ── 检查 bad_news ──
        bad_mention = False
        for b in brief.get("bad_news", []) or []:
            impact = (b.get("impact", "") or "").lower()
            event = (b.get("event", "") or "").lower()
            if sector_lower in impact or sector_lower in event:
                bad_mention = True
                break

        # ── 检查 hot_themes ──
        theme_mention = False
        for t in brief.get("hot_themes", []) or []:
            theme = (t.get("theme", "") or "").lower()
            sector_name = (t.get("sector", "") or "").lower()
            if sector_lower in theme or sector_lower in sector_name:
                theme_mention = True
                break

        # ── 综合判断 ──
        if good_mention and not bad_mention:
            multiplier = 1.25  # 明确利好
        elif bad_mention and not good_mention:
            multiplier = 0.75  # 明确利空
        elif not good_mention and not bad_mention and theme_mention:
            multiplier = 1.15  # 热点关注（情绪中性但值得注意）
        # else: 同时有利好利空或无提及 → 1.0

    except Exception:
        pass

    _distill_adjust_cache[news_sector] = multiplier
    _distill_adjust_time = now
    return multiplier


def industry_to_news_sector(industry: str) -> str:
    """东方财富细行业(79个) -> 新闻板块(17个)。"""
    if not industry:
        return "通用"
    ind_lower = industry.lower()
    for news_sector, keywords in _INDUSTRY_KEYWORDS:
        for kw in keywords:
            if kw.lower() in ind_lower:
                return news_sector
    return "通用"


# ══════════════════════════════════════════════════════
# 1.5. 东方财富行业 -> 通达信板块代码（直接匹配，不走新闻板块）NEW
# ══════════════════════════════════════════════════════

# ── 东方财富行业 → 通达信板块代码（全量映射，一次性生成）──
# 92.4% 自动匹配 + 6条手动补漏

_em_to_tdx: dict | None = None  # {EM行业名: TDX代码}


def _name_to_industry(name: str) -> str:
    """股票名称 -> 通达信行业板块名（最后的兜底匹配，不依赖 API）。

    用常见行业关键词反推，如"芯"→"半导体"、"药"→"医药"、"银行"→"银行"。
    """
    if not name or len(name) < 2:
        return ""
    # 关键词 → 行业板块名（按匹配优先级排序，长的优先）
    _NAME_KEYWORDS = [
        ("芯", "半导体"), ("芯片", "半导体"), ("半导体", "半导体"),
        ("银行", "银行"), ("药", "医药"), ("医", "医药"),
        ("证券", "证券"), ("保险", "保险"), ("电力", "电力"),
        ("钢", "钢铁"), ("煤", "煤炭"), ("石油", "石油"),
        ("地产", "房地产"), ("房", "房地产"),
        ("白酒", "酿酒"), ("酒", "酿酒"), ("啤酒", "酿酒"),
        ("食品", "食品饮料"), ("饮料", "食品饮料"), ("乳", "食品饮料"),
        ("通信", "通信设备"), ("5G", "通信设备"), ("光模块", "通信设备"),
        ("光伏", "电气设备"), ("锂电", "电气设备"), ("电池", "电气设备"),
        ("新材", "有色"), ("稀土", "有色"), ("铜", "有色"), ("铝", "有色"),
        ("航空", "航空"), ("航天", "航空"), ("军工", "航空"),
        ("汽车", "汽车类"), ("新能源车", "汽车类"),
        ("软件", "软件服务"), ("信息", "IT设备"), ("科技", "IT设备"),
        ("环保", "环境保护"), ("水务", "水务"),
        ("港口", "港口"), ("路桥", "路桥"), ("高速", "路桥"),
        ("旅游", "旅游"), ("酒店", "酒店餐饮"), ("餐饮", "酒店餐饮"),
        ("黄金", "有色"), ("珠宝", "有色"),
        ("玻璃", "建材"), ("水泥", "建材"), ("建材", "建材"),
        ("农牧", "农林牧渔"), ("农业", "农林牧渔"), ("养殖", "农林牧渔"),
        ("纺织", "纺织服饰"), ("服装", "纺织服饰"),
        ("电气", "电气设备"), ("设备", "通用机械"), ("机械", "通用机械"),
        ("化工", "化工"), ("化纤", "化纤"), ("造纸", "造纸"),
        ("港口", "港口"), ("港", "港口"),
        ("交通", "交通运输"), ("运输", "交通运输"), ("物流", "仓储物流"),
        ("出版", "传媒娱乐"), ("传媒", "传媒娱乐"), ("影视", "传媒娱乐"),
        ("建筑", "建筑"), ("工程", "建筑"), ("建工", "建筑"),
        ("燃气", "供气供热"), ("智能", "通用机械"), ("智联", "IT设备"),
        ("光学", "元器件"), ("光电", "元器件"),
        ("化工", "化工"), ("化学", "化工"), ("化纤", "化纤"),
        ("造纸", "造纸"), ("纸", "造纸"),
        ("公用", "水务"), ("路桥", "路桥"),
        ("复材", "航空"), ("新材", "建材"),
        ("金融", "多元金融"), ("期货", "多元金融"), ("信托", "多元金融"),
        ("保险", "保险"),
    ]
    for kw, sector in _NAME_KEYWORDS:
        if kw in name:
            return sector
    return ""


def _load_name2code() -> dict:
    """加载 sector_map.json 的 name2code 映射表。"""
    try:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        spath = os.path.join(base, "_internal", "sector_map.json")
        with open(spath, "r", encoding="utf-8") as f:
            smap = json.load(f)
        return smap.get("name2code", {})
    except Exception:
        return {}


def _em_industry_to_tdx(industry: str) -> str:
    """东方财富行业名 -> 通达信板块代码。自动匹配+手动补漏，覆盖79个行业。
    自带自愈：_em_to_tdx 为空或过小时自动重建。"""
    global _em_to_tdx
    if _em_to_tdx is None or not isinstance(_em_to_tdx, dict) or len(_em_to_tdx) < 10:
        _build_em_tdx_map()
    if _em_to_tdx is None or len(_em_to_tdx) < 10:
        return ""  # 两次尝试仍失败，放弃
    return _em_to_tdx.get(industry, "")


def _build_em_tdx_map():
    """一次性构建完整映射表。失败时重置 None 以允许重试。"""
    global _em_to_tdx
    try:
        _em_to_tdx = {}

        import json, re
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        spath = os.path.join(base, "_internal", "sector_map.json")
        ipath = os.path.join(base, "_internal", "industry_map.json")

        with open(spath, "r", encoding="utf-8") as f:
            nc = json.load(f).get("name2code", {})

        with open(ipath, "r", encoding="utf-8") as f:
            imap = json.load(f)

        em_set = set(imap.values())
        if '-' in em_set:
            em_set.remove('-')

        tdx_names = set(nc.keys())

        manual = {
            "IT服务Ⅱ":          "软件服务",      # 原"信息服务"在TDX中不存在→改为软件服务
            "其他电源设备Ⅱ":    "电气设备",      # 原"电源设备"在TDX中不存在→改为电气设备
            "农化制品":          "化工",          # 原"农化"在TDX中不存在→改为化工
            "调味发酵品Ⅱ":      "调味品",
            "互联网电商":        "互联网",        # 原"电商"在TDX中不存在→改为互联网
            "照明设备Ⅱ":        "电气设备",      # 原空字符串→改为电气设备
            "个护用品":          "个护小家电",    # 新增映射
            "软件开发":          "软件服务",      # 新增映射
        }

        for em in em_set:
            tdx_code = ""

            if em in tdx_names:
                tdx_code = nc[em]
            elif em not in tdx_names:
                clean = re.sub(r'[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]', '', em)
                clean = re.sub(r'[0-9]+$', '', clean)
                if clean in tdx_names:
                    tdx_code = nc[clean]
                elif clean + "行业" in tdx_names:
                    tdx_code = nc[clean + "行业"]
                elif not tdx_code:
                    for td in tdx_names:
                        if clean in td or td in clean:
                            tdx_code = nc[td]
                            break

            if not tdx_code and em in manual and manual[em]:
                lookup = manual[em]
                if lookup in nc:
                    tdx_code = nc[lookup]

            if tdx_code:
                _em_to_tdx[em] = tdx_code
    except Exception:
        import traceback
        traceback.print_exc()
        _em_to_tdx = None  # 失败时重置，允许下次重试


# ══════════════════════════════════════════════════════
# 2. 新闻板块 -> 通达信板块代码映射
# ══════════════════════════════════════════════════════

NEWS_TO_TDX = {
    "AI算力":       "sh880669",
    "半导体":       "sh880491",
    "新能源汽车":   "sh880391",
    "光伏":         "sh880544",
    "通信设备":     "sh880490",
    "证券金融":     "sh880472",
    "军工":         "sh880507",
    "钢铁":         "sh880318",
    "宏观":         "",
    "房地产":       "sh880482",
    "数字金融":     "sh880946",
    "医药":         "sh880400",
    "电力":         "sh880305",
    "有色":         "sh880324",
}

# 二级关键词分流: 能源/消费/机械 根据新闻标题路由到不同通达信代码
KEYWORD_TDX_ROUTE = {
    "能源": {
        "_default": "sh880310",
        "煤炭": "sh880301", "煤矿": "sh880301", "煤价": "sh880301",
        "油价": "sh880310", "原油": "sh880310", "天然气": "sh880310",
        "OPEC": "sh880310", "石化": "sh880310",
    },
    "消费": {
        "_default": "sh880372",
        "酒": "sh880381", "茅台": "sh880381", "五粮液": "sh880381",
        "白酒": "sh880381", "社零": "sh880372", "旅游": "sh880372",
        "免税": "sh880372",
    },
    "机械": {
        "_default": "sh880439",
        "通用": "sh880437", "工业": "sh880440", "基件": "sh880439",
        "机床": "sh880437", "数控": "sh880437", "工程": "sh880440",
        "三一": "sh880440",
    },
}


def _route_tdx_code(news_sector: str, news_title: str = "") -> str:
    """根据新闻板块名 + 可选标题路由到通达信代码。"""
    tdx = NEWS_TO_TDX.get(news_sector, "")
    if tdx:
        return tdx

    # 二级分流
    routes = KEYWORD_TDX_ROUTE.get(news_sector)
    if routes:
        title_lower = news_title.lower() if news_title else ""
        for kw, code in routes.items():
            if kw != "_default" and kw.lower() in title_lower:
                return code
        return routes.get("_default", "")

    return ""


# ══════════════════════════════════════════════════════
# 3. FOMC 窗口检测
# ══════════════════════════════════════════════════════

# 公布日 = 会议第 2 天（北京时间周四凌晨）
FOMC_ANNOUNCE_DATES = [
    datetime(2025, 1, 30), datetime(2025, 3, 20), datetime(2025, 5, 8),
    datetime(2025, 6, 19), datetime(2025, 7, 31), datetime(2025, 9, 18),
    datetime(2025, 11, 7), datetime(2025, 12, 11),
    datetime(2026, 1, 29), datetime(2026, 3, 19), datetime(2026, 5, 7),
    datetime(2026, 6, 18), datetime(2026, 7, 30), datetime(2026, 9, 17),
    datetime(2026, 11, 5), datetime(2026, 12, 10),
]

FOMC_WINDOW_DAYS = 10


def is_fomc_window() -> tuple[bool, int, str]:
    """检测当前是否 FOMC 窗口期。
    返回: (in_window, days_to_next, next_date_str)
    """
    now = datetime.now()
    for fomc_dt in FOMC_ANNOUNCE_DATES:
        days = (fomc_dt - now).days
        if 0 <= days <= FOMC_WINDOW_DAYS:
            return True, days, fomc_dt.strftime("%m-%d")
    # 找下一次
    future = [d for d in FOMC_ANNOUNCE_DATES if d > now]
    if future:
        days = (future[0] - now).days
        return False, days, future[0].strftime("%m-%d")
    return False, 999, "未知"


# ══════════════════════════════════════════════════════
# 4. 资金集中度检测
# ══════════════════════════════════════════════════════

CONCENTRATION_LONG   = 60  # 长期窗口
CONCENTRATION_SHORT  = 20  # 短期窗口
CONCENTRATION_HIGH   = 0.35  # 集中度阈值


def calc_concentration(sector_map: dict) -> dict:
    """双窗口资金集中度检测。
    返回: {short_pct, long_pct, is_high, top3_short, top3_long}
    """
    try:
        if not sector_map:
            return {"short_pct": 0, "long_pct": 0, "is_high": False,
                    "top3_short": [], "top3_long": []}

        # 按 amount(成交额) 排序
        ranked = sorted(sector_map.items(),
                       key=lambda x: x[1].get("amount", 0) or 0,
                       reverse=True)
        total_amt = sum(v.get("amount", 0) or 0 for _, v in ranked)
        if total_amt <= 0:
            return {"short_pct": 0, "long_pct": 0, "is_high": False,
                    "top3_short": [], "top3_long": []}

        top3 = ranked[:3]
        top3_pct = sum(v.get("amount", 0) or 0 for _, v in top3) / total_amt
        top3_names = [name for name, _ in top3]

        # 短期集中度从 sector_map 的热度推算
        short_pct = top3_pct  # 简化：当日集中度
        long_pct = top3_pct   # 暂无历史，用当日近似

        is_high = short_pct > CONCENTRATION_HIGH

        return {
            "short_pct": round(short_pct * 100, 1),
            "long_pct": round(long_pct * 100, 1),
            "is_high": is_high,
            "top3_short": top3_names,
            "top3_long": top3_names,
        }
    except Exception:
        return {"short_pct": 0, "long_pct": 0, "is_high": False,
                "top3_short": [], "top3_long": []}


# ══════════════════════════════════════════════════════
# 5. 通达信板块日线读取
# ══════════════════════════════════════════════════════

# 读取缓存: {tdx_code: (file_mtime, data)}
_read_cache: dict[str, tuple[float, dict]] = {}


def read_tdx_sector(tdx_code: str) -> dict | None:
    """读通达信板块 .day 文件，返回 closes/volumes 序列。带 mtime 缓存。"""
    if not tdx_code:
        return None
    path = os.path.join(TDX_BASE, f"{tdx_code}.day")
    if not os.path.exists(path):
        return None
    try:
        mtime = os.path.getmtime(path)
        cached = _read_cache.get(tdx_code)
        if cached and cached[0] == mtime:
            return cached[1]  # 缓存命中，文件未变

        with open(path, "rb") as f:
            data = f.read()
        rows = len(data) // 32
        if rows < 60:
            return None
        closes = []
        volumes = []
        amounts = []
        for i in range(rows):
            b = data[i * 32:(i + 1) * 32]
            closes.append(struct.unpack("<I", b[16:20])[0] / 100)
            amounts.append(struct.unpack("<f", b[20:24])[0])
            volumes.append(struct.unpack("<I", b[24:28])[0])
        result = {"closes": closes, "volumes": volumes, "amounts": amounts, "rows": rows}
        _read_cache[tdx_code] = (mtime, result)
        return result
    except Exception:
        return None


def get_sector_percentile(tdx_code: str, lookback: int = 60) -> dict:
    """60 日分位数 -> 五档赔率系数。"""
    data = read_tdx_sector(tdx_code)
    if not data:
        return {"percentile": 50.0, "multiplier": 1.0, "signal": "数据缺失"}

    closes = data["closes"]
    n = len(closes)
    lb = min(lookback, n)
    window = closes[-lb:]
    hi, lo = max(window), min(window)
    last = closes[-1]
    pct = (last - lo) / (hi - lo) * 100 if hi != lo else 50.0

    if pct < 20:
        mult, sig = 1.30, "低位"
    elif pct < 40:
        mult, sig = 1.15, "中低"
    else:
        mult, sig = 1.00, "中位及以上"

    return {"percentile": round(pct, 1), "multiplier": mult, "signal": sig}


def get_sector_fund_score(tdx_code: str) -> dict:
    """成交量变化率 -> [0, 100]。含 v20/v60 趋势修正。"""
    data = read_tdx_sector(tdx_code)
    if not data:
        return {"score": 50.0, "label": "数据缺失", "vol_ratio": 1.0, "trend_val": 0, "trend_label": ""}

    volumes = data["volumes"]
    n = len(volumes)
    if n < 60:
        return {"score": 50.0, "label": "数据不足", "vol_ratio": 1.0, "trend_val": 0, "trend_label": ""}

    v5  = sum(volumes[-5:]) / 5
    v20 = sum(volumes[-20:]) / 20
    v60 = sum(volumes[-60:]) / 60
    ratio = v5 / max(v20, 1)
    score = min(100, max(0, 50 + (ratio - 1) * 40))

    # 趋势修正：v20 vs v60 差值 = v5/v20 - v5/v60
    # trend < 0 → v20 > v60 → 板块近期在升温
    # trend > 0 → v20 < v60 → 板块之前更热，近期在降温
    trend = (v5 / max(v20, 1)) - (v5 / max(v60, 1))
    trend_label = ""
    if trend < -0.05:
        trend_label = "🔥升温"
    elif trend > 0.05:
        trend_label = "❄降温"

    # 趋势修正分数（±5）
    if ratio < 1.0 and trend < -0.03:
        # 量萎缩但板块在升温 → 不是真缩，是从冰点反弹
        boost = min(5, abs(trend) * 20)
        score = min(100, score + boost)
    elif ratio > 1.0 and trend > 0.03:
        # 量放大但板块在降温 → 不是真放，是前期热度的余温
        penalty = min(3, trend * 10)
        score = max(0, score - penalty)

    if ratio > 1.5:
        label = "量明显放大"
    elif ratio > 1.15:
        label = "量放大"
    elif ratio > 0.85:
        label = "量平稳"
    elif ratio > 0.6:
        label = "量萎缩"
    else:
        label = "量极度萎缩"

    # 如果修正起了作用，标签追加趋势
    if trend_label and abs(ratio - 1.0) < 0.3:
        # 量能接近平稳但趋势明显，替换标签
        if trend < -0.05:
            label = "量回温"
        elif trend > 0.05:
            label = "量趋冷"

    return {"score": round(score, 1), "label": label, "vol_ratio": round(ratio, 2),
            "trend_val": round(trend, 3), "trend_label": trend_label}


# ══════════════════════════════════════════════════════
# 6. 分时段权重
# ══════════════════════════════════════════════════════

def _get_news_weight() -> float:
    """分时段返回 news 权重 w。"""
    now = datetime.now()
    if now.weekday() >= 5:
        return 0.4  # 周末=盘后
    t = now.hour * 60 + now.minute
    if (570 <= t <= 690) or (780 <= t <= 900):
        return 0.7  # 盘中 9:30-11:30 或 13:00-15:00
    return 0.4  # 盘后


# ══════════════════════════════════════════════════════
# 7. 对外入口
# ══════════════════════════════════════════════════════

def compute_sector_power(code: str, name: str, news_items: list | None = None) -> dict:
    """板块动力评分 —— 对外唯一入口。

    参数:
        code: 股票代码
        name: 股票名称
        news_items: 市场新闻列表（可选）

    返回:
        {
            "sector_power": float,      # 板块动力分 [0, 100]
            "sector_name": str,         # 板块名（展示用）
            "tdx_code": str,            # 通达信板块代码
            "news_score": float,        # 新闻子分
            "fund_score": float,        # 资金子分
            "fund_label": str,          # 资金标签
            "position_mult": float,     # 赔率系数
            "position_pct": float,      # 分位数
            "position_signal": str,     # 位置信号
            "fomc_window": bool,        # 是否FOMC窗口
            "tag": str,                 # 顺风/逆风/中性
            "label": str,               # 展示用简短标签
        }
    """
    result = {
        "sector_power": 50.0, "sector_name": "通用", "tdx_code": "",
        "news_score": 50.0, "t1_score": 50.0, "fund_score": 50.0, "fund_label": "",
        "position_mult": 1.0, "position_pct": 50.0, "position_signal": "中位",
        "fomc_window": False, "tag": "中性", "label": "—",
    }

    try:
        # ── 步骤①: 个股 -> 东方财富行业 ──
        # 优先用本地文件 industry_map.json（O(1)，离线可用）
        # 降级用 EM API 缓存（需要 prefetch_tags 预热）
        industry = ""
        try:
            from v15_sector_fetcher import match_sector, get_stock_industry
            industry = match_sector(code, name) or ""
            if not industry or industry == "通用":
                industry = get_stock_industry(code) or ""
            # 三次兜底：直接用股票名匹配通达信行业板块名
            if not industry or industry == "通用":
                industry = _name_to_industry(name)
        except Exception:
            pass

        # ── 步骤②: 行业名 -> TDX代码 ──
        # _name_to_industry 返回"化工"(通达信行业名) vs 其他返回"化学制品"(东方财富行业名)
        tdx_code = _em_industry_to_tdx(industry)
        if not tdx_code:
            # 兜底：可能 industry 是通达信行业名，直接用 name2code 查
            nc = _load_name2code()
            tdx_code = nc.get(industry, "")
        news_sector = industry_to_news_sector(industry)  # 旧链路保留，用于新闻聚合
        # sector_name 用东方财富行业原名（展示用），不用新闻板块别名
        result["sector_name"] = industry if industry and industry != "-" else news_sector

        # 新链路失败则回退旧链路（新闻板块->TDX映射表）
        if not tdx_code:
            top_title = ""
            if news_items and news_sector in ("能源", "消费", "机械"):
                for item in sorted(news_items, key=lambda x: x.priority, reverse=True):
                    if item.sector == news_sector:
                        top_title = item.title
                        break
            tdx_code = _route_tdx_code(news_sector, top_title)

        if not tdx_code:
            return result
        result["tdx_code"] = tdx_code

        # ── 并行计算 fund + position ──
        fund = get_sector_fund_score(tdx_code)
        position = get_sector_percentile(tdx_code)

        result["fund_score"] = fund["score"]
        result["fund_label"] = fund.get("label", "")
        result["fund_vol_ratio"] = fund.get("vol_ratio", 1.0)
        # 趋势标签追加到 fund_label
        tl = fund.get("trend_label", "")
        if tl:
            result["fund_label"] = f"{fund.get('label','')} {tl}"
        result["position_mult"] = position["multiplier"]
        result["position_pct"] = position["percentile"]
        result["position_signal"] = position["signal"]

        # ── 步骤④: T1 资金占比分 + T2 自身量能分 ──
        t1 = _get_t1_score(tdx_code)
        t2 = fund["score"]

        result["news_score"] = 50.0  # 固定中性，news 已从板块动力体系移除
        result["t1_score"] = round(t1, 1)

        # T1 详细分解（择时雷达展示用）
        detail = _get_t1_detail(tdx_code)
        if detail:
            result["t1_detail"] = detail
        else:
            result["t1_detail"] = {"z_60": 0, "z_240": 0, "z": 0,
                                   "r5": 0, "mean_60": 0, "mean_240": 0,
                                   "judgment": "数据不足"}

        # 蒸馏加分（利好/热点板块 +3~5 分）
        distill_bonus = 0.0
        try:
            adj = _get_distill_adjustment(news_sector)
            if adj > 1.01:
                distill_bonus = 5.0  # 利好
            elif adj < 0.99:
                distill_bonus = -5.0  # 利空
        except Exception:
            pass

        # 赔率只涨不罚（高位给 1.0，低位放大）
        pos_mult = position["multiplier"]

        power = (t1 * 0.6 + t2 * 0.4) * pos_mult + distill_bonus
        power = min(100, max(0, power))
        result["sector_power"] = round(power, 1)

        # ── 标签 ──
        if power >= 70:
            result["tag"] = "强顺风"
            result["label"] = f"{power:.0f} 🔷"
        elif power >= 58:
            result["tag"] = "顺风"
            result["label"] = f"{power:.0f} 🔷"
        elif power <= 30:
            result["tag"] = "强逆风"
            result["label"] = f"{power:.0f} 🔶"
        elif power <= 42:
            result["tag"] = "逆风"
            result["label"] = f"{power:.0f} 🔶"
        else:
            result["tag"] = "中性"
            result["label"] = f"{power:.0f}"

        return result

    except Exception:
        return result


# ══════════════════════════════════════════════════════
# 8. 扫描级缓存
# ══════════════════════════════════════════════════════

def compute_sector_power_cached(code: str, name: str,
                                 news_items: list | None = None) -> dict:
    """带缓存的板块动力计算。同一板块只算一次。"""
    today = datetime.now().strftime("%Y%m%d")

    # 先确定板块名
    industry = ""
    try:
        from v15_sector_fetcher import match_sector, get_stock_industry
        industry = match_sector(code, name) or ""
        if not industry or industry == "通用":
            industry = get_stock_industry(code) or ""
        if not industry or industry == "通用":
            industry = _name_to_industry(name)
    except Exception:
        pass
    # 缓存 key: 优先用直接匹配的TDX代码，失败则用东方财富行业名
    cache_key = _em_industry_to_tdx(industry) or industry or "通用"

    global _power_cache, _cache_date
    if _cache_date != today:
        _power_cache.clear()
        _cache_date = today

    if cache_key not in _power_cache:
        _power_cache[cache_key] = compute_sector_power(code, name, news_items)

    return _power_cache[cache_key]


def precompute_all_sector_powers(news_items: list | None = None) -> dict[str, dict]:
    """预热：运行 T1 z-score 全量计算 + 清空缓存。一次扫描只跑一次。"""
    global _t1_cache, _power_cache
    _t1_cache = None  # 强制重算
    _compute_all_t1_scores()
    _power_cache.clear()
    return _all_sector_powers or {}


# ══════════════════════════════════════════════════════
# 9. 数据快照（第二阶段积攒）
# ══════════════════════════════════════════════════════

HISTORY_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..",
    "sector_power_history.jsonl"
)

_snapshot_today: set = set()


def save_daily_snapshot(code: str, name: str, v15_score: float,
                         sector_result: dict):
    """每日快照：同日同股覆盖写（不重复）。"""
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        key = f"{today}_{code}"

        if key in _snapshot_today:
            return

        _snapshot_today.add(key)

        record = {
            "date": today,
            "code": code,
            "name": name,
            "v15_score": round(v15_score, 1),
            "sector_power": sector_result.get("sector_power", 50),
            "sector_name": sector_result.get("sector_name", ""),
            "t1_score": sector_result.get("t1_score", 50),
            "fund_score": sector_result.get("fund_score", 50),
            "position_mult": sector_result.get("position_mult", 1.0),
            "tag": sector_result.get("tag", ""),
        }

        with open(HISTORY_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass
