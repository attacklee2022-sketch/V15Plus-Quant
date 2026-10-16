# -*- coding: utf-8 -*-
"""
V15 Alpha — 扩展过滤器模块
============================
四维度可插拔过滤器：
  1. 相对强度 — 个股 vs 板块 vs 大盘
  2. 支撑/阻力盈亏比 — 风险收益空间
  3. 分时确认 — 日内尾盘强度验证
  4. 跨市场锚点 — 港美股主题映射

每个过滤器返回 {"adjustment": float, "facts": [str]}。
异常时返回 neutral (adjustment=0)，不阻塞主评分管道。
"""
from __future__ import annotations
from typing import Optional
from datetime import datetime


# ══════════════════════════════════════════════════════
# 工具函数
# ══════════════════════════════════════════════════════

def _ma(vals: list[float], n: int) -> Optional[float]:
    if len(vals) < n:
        return None
    return sum(vals[-n:]) / n


def _clamp(v: float, lo: float = -100, hi: float = 100) -> float:
    return max(lo, min(hi, v))


# ══════════════════════════════════════════════════════
# 1. 相对强度过滤器
# ══════════════════════════════════════════════════════

_index_closes_cache = None
_index_closes_cache_time = 0.0
_INDEX_CACHE_TTL = 300  # 5分钟过期，盘中通达信数据更新后能自动重读


def _get_index_closes():
    """获取指数收盘价列表（带缓存，5分钟过期）。"""
    global _index_closes_cache, _index_closes_cache_time
    import time as _time
    now = _time.time()
    if _index_closes_cache is not None and (now - _index_closes_cache_time) < _INDEX_CACHE_TTL:
        return _index_closes_cache
    _index_closes_cache = _fetch_index_closes()
    _index_closes_cache_time = now
    return _index_closes_cache


def _fetch_index_closes(index_code: str = "999999") -> list[float]:
    """从通达信读取指数日线收盘价。失败返回空列表。"""
    import os, struct
    tdx_roots = [r"D:\通达信", r"D:\tdx", r"C:\通达信", r"C:\tdx"]
    for root in tdx_roots:
        if index_code.startswith(("60", "68")) or index_code == "999999":
            p = os.path.join(root, "vipdoc", "sh", "lday", f"sh{index_code}.day")
        else:
            p = os.path.join(root, "vipdoc", "sz", "lday", f"sz{index_code}.day")
        if not os.path.exists(p):
            continue
        try:
            with open(p, "rb") as f:
                data = f.read()
            row_cnt = len(data) // 32
            closes = []
            for i in range(row_cnt):
                b = data[i * 32:(i + 1) * 32]
                close_p = struct.unpack("<I", b[16:20])[0] / 100
                closes.append(close_p)
            return closes
        except Exception:
            continue
    return []


def _filter_relative_strength(
    closes: list[float],
    sector_map: Optional[dict] = None,
    code: Optional[str] = None,
    name: Optional[str] = None,
) -> dict:
    """个股相对强度：跑赢/跑输板块和大盘的 alpha 度量。"""
    facts = []
    adj = 0.0
    n = len(closes)

    if n < 15:
        return {"adjustment": 0.0, "facts": ["[强度] 数据不足"]}

    stock_ret_10d = (closes[-1] / closes[-11] - 1) * 100 if n >= 11 else 0
    stock_ret_5d = (closes[-1] / closes[-6] - 1) * 100 if n >= 6 else 0

    # ── vs 板块 ──
    sector_change = 0
    if sector_map and code and name:
        from v14_deep_engine import _best_sector_match
        try:
            sector = _best_sector_match(sector_map, code, name)
            sector_change = sector_map.get(sector, {}).get("change", 0)
        except Exception:
            pass

    stock_vs_sector = stock_ret_5d - sector_change
    if stock_vs_sector > 5:
        adj += 5
        facts.append(f"[强度] 近5日跑赢板块 {stock_vs_sector:+.1f}% (个股{stock_ret_5d:+.1f}% vs 板块{sector_change:+.2f}%) +5")
    elif stock_vs_sector > 1:
        adj += 2
        facts.append(f"[强度] 近5日微跑赢板块 {stock_vs_sector:+.1f}% +2")
    elif stock_vs_sector < -4:
        adj -= 5
        facts.append(f"[强度] 近5日跑输板块 {stock_vs_sector:+.1f}% (可能受利空影响) -5")

    # ── vs 大盘 ──
    market_closes = _get_index_closes()  # 上证指数
    if market_closes and len(market_closes) >= 11:
        market_ret_10d = (market_closes[-1] / market_closes[-11] - 1) * 100
        stock_vs_market = stock_ret_10d - market_ret_10d
        if stock_vs_market > 4:
            adj += 3
            facts.append(f"[强度] 近10日跑赢上证 {stock_vs_market:+.1f}% (个股{stock_ret_10d:+.1f}% vs 大盘{market_ret_10d:+.1f}%) +3")
        elif stock_vs_market < -5:
            adj -= 3
            facts.append(f"[强度] 近10日跑输上证 {stock_vs_market:+.1f}% -3")
    else:
        facts.append(f"[强度] 大盘数据不可用，跳过 vs 大盘比较")

    adj = _clamp(adj, -8, 8)
    return {"adjustment": round(adj, 1), "facts": facts}


# ══════════════════════════════════════════════════════
# 2. 支撑/阻力盈亏比过滤器
# ══════════════════════════════════════════════════════

def _filter_risk_reward(
    closes: list[float],
    highs: list[float],
    lows: list[float],
    last_close: float,
    atr_norm: float = 1.5,
) -> dict:
    """计算当前价格离最近支撑/阻力位的盈亏比。"""
    facts = []
    adj = 0.0
    n = len(closes)

    if n < 20:
        return {"adjustment": 0.0, "facts": ["[盈亏比] 数据不足"]}

    resistance = max(highs[-20:])
    support = min(lows[-20:])
    ma60 = _ma(closes, 60) or support

    # 上空间 / 下空间
    upside = (resistance - last_close) / last_close * 100
    downside_to_support = (last_close - max(support, ma60)) / last_close * 100
    stop_size = max(atr_norm * 1.5, 2.0)  # 至少2%

    if upside < 1:
        facts.append(f"[盈亏比] 距20日高阻力仅 {upside:.1f}%，上升空间极小")
    else:
        rr_ratio = upside / stop_size

        if rr_ratio > 3.5:
            adj += 8
            facts.append(f"[盈亏比] 盈亏比 {rr_ratio:.1f}:1 (上空间{upside:.1f}%/止损{stop_size:.1f}%) 风险收益优良 +8")
        elif rr_ratio > 2.0:
            adj += 4
            facts.append(f"[盈亏比] 盈亏比 {rr_ratio:.1f}:1 可接受 +4")
        elif rr_ratio < 1.0:
            adj -= 5
            facts.append(f"[盈亏比] 盈亏比 {rr_ratio:.1f}:1 (空间不足) -5")
        elif rr_ratio < 1.5:
            adj -= 2
            facts.append(f"[盈亏比] 盈亏比 {rr_ratio:.1f}:1 空间偏紧 -2")
        else:
            facts.append(f"[盈亏比] 盈亏比 {rr_ratio:.1f}:1 (上空间{upside:.1f}%/止损{stop_size:.1f}%) 中性")

    if last_close < support * 1.02:
        adj += 2
        facts.append(f"[盈亏比] 现价接近20日支撑位{support:.2f}，下行空间有限 +2")

    if last_close > resistance * 0.97:
        adj -= 2
        facts.append(f"[盈亏比] 现价接近20日阻力位{resistance:.2f}，突破前谨慎 -2")

    adj = _clamp(adj, -6, 8)
    return {"adjustment": round(adj, 1), "facts": facts}


# ══════════════════════════════════════════════════════
# 3. 分时确认过滤器
# ══════════════════════════════════════════════════════

def _is_trading_session() -> bool:
    """判断当前是否交易时段 (9:30-11:30 或 13:00-15:00 周一至周五)。"""
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    t = now.hour * 60 + now.minute
    return (9 * 60 + 30 <= t <= 11 * 60 + 30) or (13 * 60 <= t <= 15 * 60 + 5)


def _filter_intraday_confirm(code: str, prev_close: float | None = None) -> dict:
    """分时尾盘强度确认。非交易时段跳过。
    prev_close: 昨日收盘价，用于 calc_intraday_strength 计算真实涨跌幅。"""
    if not _is_trading_session():
        return {"adjustment": 0.0, "facts": ["[分时] 非交易时段，跳过"]}

    if not code:
        return {"adjustment": 0.0, "facts": ["[分时] 无股票代码"]}

    try:
        from v14_intraday import fetch_intraday_minutes, calc_intraday_strength
        df = fetch_intraday_minutes(code)
        if df is None or len(df) < 10:
            return {"adjustment": 0.0, "facts": ["[分时] 数据获取失败"]}

        result = calc_intraday_strength(df, prev_close=prev_close)
        if not result.get("valid"):
            return {"adjustment": 0.0, "facts": ["[分时] 日内数据不足"]}

        adj = 0.0
        facts = []

        trend = result.get("trend", "震荡")
        pos_pct = result.get("position_pct", 50)
        vwap_dev = result.get("vwap_dev_pct", 0)
        vol_ratio_30 = result.get("vol_ratio_30min", 1.0)
        strength = result.get("strength_score", 50)

        # 尾盘判断（下午14:30后）
        now = datetime.now()
        is_late = now.hour >= 14 and now.minute >= 30

        if is_late:
            if trend == "上升" and pos_pct > 60:
                adj += 5
                facts.append(f"[分时] 尾盘确认↑ 位置{pos_pct:.0f}% 量比{vol_ratio_30:.1f} +5")
            elif trend == "下降" and pos_pct < 30:
                adj -= 5
                facts.append(f"[分时] 尾盘走弱↓ 位置{pos_pct:.0f}% -5")
            elif vwap_dev > 2:
                adj -= 3
                facts.append(f"[分时] 溢价VWAP {vwap_dev:+.1f}% -3")
            elif vwap_dev < -2:
                adj += 2
                facts.append(f"[分时] 折价VWAP {vwap_dev:+.1f}% +2")
        else:
            # 盘中：强度分映射
            if strength >= 70:
                adj += 3
                facts.append(f"[分时] 盘中强度{strength:.0f}(偏强) +3")
            elif strength < 35:
                adj -= 3
                facts.append(f"[分时] 盘中强度{strength:.0f}(偏弱) -3")
            facts.append(f"[分时] 趋势:{trend} 位置:{pos_pct:.0f}% VWAP偏离:{vwap_dev:+.1f}%")

        adj = _clamp(adj, -6, 6)
        return {"adjustment": round(adj, 1), "facts": facts}

    except ImportError:
        return {"adjustment": 0.0, "facts": ["[分时] mootdx不可用"]}
    except Exception as e:
        return {"adjustment": 0.0, "facts": [f"[分时] 异常: {e}"]}


# ══════════════════════════════════════════════════════
# 4. 跨市场锚点过滤器
# ══════════════════════════════════════════════════════

# 个股→港美股主题映射 (关键词按优先级排列，先用具体公司名精确匹配，再用行业词)
CROSS_THEME_MAP = {
    "AI算力": [
        # 精确公司名
        "中科曙光", "浪潮信息", "科大讯飞", "工业富联", "海光信息", "寒武纪",
        "紫光股份", "拓维信息", "神州数码", "数据港", "光环新网", "奥飞数据",
        # 行业关键词（≥2字避免子串误匹配）
        "算力", "人工智能", "AI", "大模型", "服务器", "光模块", "数据中心", "英伟达",
    ],
    "半导体": [
        "中芯国际", "华虹", "兆易创新", "长鑫", "韦尔股份", "卓胜微", "北方华创",
        "中微公司", "沪硅产业", "士兰微", "斯达半导", "圣邦股份", "纳芯微",
        "半导体", "芯片", "光刻", "封测", "晶圆", "先进封装",
    ],
    "新能源汽车": [
        "比亚迪", "宁德时代", "特斯拉", "蔚来", "小鹏", "理想汽车",
        "长安汽车", "长城汽车", "江淮汽车", "赛力斯", "北汽蓝谷",
        "锂电池", "固态电池", "锂矿", "锂电",
        # "新能源"开头但不属于汽车的公司名排除
    ],
}

# 误匹配排除表: 名称包含这些词的不应被匹配到该主题
CROSS_EXCLUDE = {
    "新能源汽车": ["宝新能源", "新能源发电", "新能源电力"],
}


def _match_cross_theme(code: str, name: str) -> Optional[str]:
    """匹配个股属于哪个港美股主题。精确公司名 > 行业关键词，含误排除。"""
    for theme, keywords in CROSS_THEME_MAP.items():
        # 先检查排除
        excludes = CROSS_EXCLUDE.get(theme, [])
        if any(ex in name for ex in excludes):
            continue

        # 精确公司名优先匹配
        for kw in keywords:
            if len(kw) >= 3 and kw in name:
                return theme
    return None


def _filter_cross_market(
    code: Optional[str] = None,
    name: Optional[str] = None,
) -> dict:
    """港美股锚点映射到个股评分。"""
    if not code or not name:
        return {"adjustment": 0.0, "facts": []}

    theme = _match_cross_theme(code, name)
    if not theme:
        return {"adjustment": 0.0, "facts": []}

    try:
        from v14_cross_market import get_cross_market_quotes, cross_market_theme_boost
        quotes, src = get_cross_market_quotes(online=True)
        if "演示" in src:
            return {"adjustment": 0.0, "facts": ["[跨市场] 港美股数据离线"]}

        boosts = cross_market_theme_boost(quotes)
        if theme in boosts:
            boost = boosts[theme]["boost"]
            adj = round(boost * 0.5, 1)  # 缩放到 ±5
            facts = [f"[跨市场] {theme}板块港美股锚点修正 {boost:+.1f} → 映射{adj:+.1f}"]
            adj = _clamp(adj, -4, 6)
            return {"adjustment": adj, "facts": facts}
    except Exception as e:
        pass

    return {"adjustment": 0.0, "facts": ["[跨市场] 数据不可用"]}


# ══════════════════════════════════════════════════════
# 5. 大盘环境过滤器
# ══════════════════════════════════════════════════════

def _filter_market_regime(closes: list[float]) -> dict:
    """判断大盘环境（上证指数），输出全局信号降级建议。
    回测验证: 熊市里所有买入信号胜率都低，需要全局降级。"""
    idx = _get_index_closes()
    if not idx or len(idx) < 60:
        return {"adjustment": 0.0, "facts": ["[大盘] 上证数据不可用"]}

    facts = []
    adj = 0.0
    n = len(idx)
    last = idx[-1]
    ma20 = sum(idx[-20:]) / 20 if n >= 20 else last
    ma60 = sum(idx[-60:]) / 60 if n >= 60 else last

    # 20日均线方向
    ma20_20d_ago = sum(idx[-40:-20]) / 20 if n >= 40 else ma20
    ma20_slope = (ma20 - ma20_20d_ago) / ma20_20d_ago * 100 if ma20_20d_ago > 0 else 0

    # 市场状态
    above_ma20 = last > ma20
    above_ma60 = last > ma60
    ret_20d = (last / idx[-21] - 1) * 100 if n >= 21 else 0
    ret_60d = (last / idx[-61] - 1) * 100 if n >= 61 else 0

    if ma20_slope > 0.5 and above_ma20 and above_ma60:
        # 牛市/上升趋势: 信号正常
        adj += 2
        facts.append(f"[大盘] 上证上升趋势(MA20向上{ma20_slope:+.1f}% 站上MA20/MA60) 信号可信 +2")
    elif ma20_slope > 0 and above_ma60:
        adj += 0
        facts.append(f"[大盘] 上证震荡偏强(MA20微{ma20_slope:+.1f}% 站上MA60) 信号正常")
    elif ma20_slope < -0.5 and not above_ma20 and not above_ma60:
        # 熊市/下跌趋势: 全局降级
        adj -= 6
        facts.append(f"[大盘] 上证下跌趋势(MA20向下{ma20_slope:+.1f}% 跌破MA20/MA60) 全局信号降级 -6")
    elif ma20_slope < 0 and not above_ma60:
        adj -= 3
        facts.append(f"[大盘] 上证偏弱(MA20向下{ma20_slope:+.1f}% 跌破MA60) 信号需谨慎 -3")
    elif ma20_slope < 0:
        adj -= 2
        facts.append(f"[大盘] 上证震荡偏弱(MA20向下{ma20_slope:+.1f}%) 信号保守 -2")
    else:
        facts.append(f"[大盘] 上证震荡(MA20{ma20_slope:+.1f}% 现价{last:.0f} MA20={ma20:.0f}) 中性")

    # 极端行情检测
    if ret_20d < -10:
        adj -= 2
        facts.append(f"[大盘] 上证近20日跌幅{ret_20d:.1f}% 恐慌可能延续 -2")
    if ret_60d > 30:
        adj -= 2
        facts.append(f"[大盘] 上证近60日涨幅{ret_60d:.1f}% 追高风险 -2")

    adj = max(-8, min(4, adj))
    return {"adjustment": round(adj, 1), "facts": facts}


# ══════════════════════════════════════════════════════
# 公共入口
# ══════════════════════════════════════════════════════

def compute_extra_filters(
    closes: list[float],
    highs: list[float],
    lows: list[float],
    volumes: list[float],
    last_close: float,
    code: Optional[str] = None,
    name: Optional[str] = None,
    sector_map: Optional[dict] = None,
    atr_norm: float = 1.5,
) -> dict:
    """
    运行所有扩展过滤器，返回聚合调整值。

    返回:
        {
            "adjustment": float,       # 总分调整值 (clamped ±15)
            "facts": [str],           # 所有子过滤器的 fact 文本
            "relative_strength": {...},
            "risk_reward": {...},
            "intraday": {...},
            "cross_market": {...},
        }
    """
    all_facts = []
    total_adj = 0.0
    details = {}

    # 1. 相对强度
    try:
        rs = _filter_relative_strength(closes, sector_map, code, name)
        total_adj += rs["adjustment"]
        all_facts.extend(rs["facts"])
        details["relative_strength"] = rs
    except Exception:
        details["relative_strength"] = {"adjustment": 0, "facts": ["[强度] 异常"]}

    # 2. 盈亏比
    try:
        rr = _filter_risk_reward(closes, highs, lows, last_close, atr_norm)
        total_adj += rr["adjustment"]
        all_facts.extend(rr["facts"])
        details["risk_reward"] = rr
    except Exception:
        details["risk_reward"] = {"adjustment": 0, "facts": ["[盈亏比] 异常"]}

    # 3. 分时确认 (仅交易时段，传入昨收价确保涨跌幅准确)
    try:
        prev_close = closes[-2] if len(closes) >= 2 else None
        intra = _filter_intraday_confirm(code or "", prev_close=prev_close)
        total_adj += intra["adjustment"]
        all_facts.extend(intra["facts"])
        details["intraday"] = intra
    except Exception:
        details["intraday"] = {"adjustment": 0, "facts": ["[分时] 异常"]}

    # 4. 跨市场锚点
    try:
        cross = _filter_cross_market(code, name)
        total_adj += cross["adjustment"]
        all_facts.extend(cross["facts"])
        details["cross_market"] = cross
    except Exception:
        details["cross_market"] = {"adjustment": 0, "facts": ["[跨市场] 异常"]}

    # 5. 大盘环境
    try:
        mr = _filter_market_regime(closes)
        total_adj += mr["adjustment"]
        all_facts.extend(mr["facts"])
        details["market_regime"] = mr
    except Exception:
        details["market_regime"] = {"adjustment": 0, "facts": ["[大盘] 异常"]}

    total_adj = _clamp(round(total_adj, 1), -15, 15)

    return {
        "adjustment": total_adj,
        "facts": all_facts,
        "relative_strength": details.get("relative_strength", {}),
        "risk_reward": details.get("risk_reward", {}),
        "intraday": details.get("intraday", {}),
        "cross_market": details.get("cross_market", {}),
        "market_regime": details.get("market_regime", {}),
    }
