from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from urllib.request import Request, urlopen
from _http_utils import get_headers


@dataclass
class RealtimeQuote:
    code: str
    name: str
    price: float
    change_pct: float
    change_amount: float
    high: float
    low: float
    open_p: float
    prev_close: float
    volume: int
    amount: float
    source: str


@dataclass
class StrongStock:
    code: str
    name: str
    change_pct: float
    price: float
    turnover: float
    reason: str
    board: str  # 所属板块/概念


@dataclass
class LimitUpStock:
    code: str
    name: str
    price: float
    change_pct: float
    first_time: str  # 首次封板时间
    reason: str
    board: str


DEMO_QUOTES: dict[str, RealtimeQuote] = {}


def _parse_tencent_line(line: str) -> RealtimeQuote | None:
    """解析腾讯财经个股行情。

    字段: 0=市场, 1=名称, 2=代码, 3=当前价, 4=昨收, 5=开盘, 6=成交量(手),
          7=外盘, 8=内盘, ... 31=最高, 32=最低, ... 33=时间,
          38=涨跌额, 39=涨跌幅%, ...
    """
    try:
        if "=" not in line:
            return None
        fields = line.split("~")
        if len(fields) < 40:
            return None
        code = fields[2].strip()
        name = fields[1].strip()
        price = _safe_float(fields[3])
        prev_close = _safe_float(fields[4])
        high = _safe_float(fields[33])
        low = _safe_float(fields[34])
        open_p = _safe_float(fields[5])
        change_pct = _safe_float(fields[32])
        change_amount = _safe_float(fields[31])
        volume = int(_safe_float(fields[6]) * 100)  # 手 → 股
        amount = _safe_float(fields[37]) if len(fields) > 37 else 0
        return RealtimeQuote(
            code=code, name=name, price=price, change_pct=change_pct,
            change_amount=change_amount, high=high, low=low, open_p=open_p,
            prev_close=prev_close, volume=volume, amount=amount,
            source="腾讯财经",
        )
    except Exception:
        return None


def _safe_float(value, default: float = 0) -> float:
    try:
        return float(value) if value else default
    except (ValueError, TypeError):
        return default


def fetch_tencent_quotes(codes: list[str], timeout: int = 8) -> dict[str, RealtimeQuote]:
    """批量获取腾讯财经个股行情。

    代码格式: 'sh600000' 或 'sz000977'
    """
    if not codes:
        return {}
    ids = ",".join(codes)
    url = f"https://qt.gtimg.cn/q={ids}"
    req = Request(url, headers=get_headers("https://qt.gtimg.cn/"))
    u = urlopen(req, timeout=timeout)
    raw = u.read().decode("gbk", errors="ignore")

    result: dict[str, RealtimeQuote] = {}
    for line in raw.strip().split(";"):
        line = line.strip()
        if not line:
            continue
        q = _parse_tencent_line(line)
        if q is not None:
            # 统一为纯数字代码
            result[q.code] = q
    return result


def get_realtime_quotes(codes: list[str], online: bool = True) -> dict[str, RealtimeQuote]:
    """获取多只股票实时行情。"""
    if online and codes:
        tencent_codes = []
        for c in codes:
            c = c.strip()
            if c.startswith(("60", "68")):
                tencent_codes.append(f"sh{c}")
            else:
                tencent_codes.append(f"sz{c}")
        try:
            return fetch_tencent_quotes(tencent_codes)
        except Exception:
            pass
    return {}


def get_strong_stocks(online: bool = True) -> tuple[list[StrongStock], str]:
    """获取当日强势股池。"""
    if online:
        try:
            import akshare as ak
            df = ak.stock_zt_pool_strong_em(date=datetime.now().strftime("%Y%m%d"))
            if df is not None and not df.empty:
                stocks = []
                for _, row in df.iterrows():
                    stocks.append(StrongStock(
                        code=str(row.get("代码", "")),
                        name=str(row.get("名称", "")),
                        change_pct=_safe_float(row.get("涨跌幅")),
                        price=_safe_float(row.get("最新价")),
                        turnover=_safe_float(row.get("成交额")),
                        reason=str(row.get("入选原因", "") or row.get("精选原因", "")),
                        board=str(row.get("所属行业", "") or ""),
                    ))
                return stocks, f"东方财富强势股池 {datetime.now():%Y-%m-%d}"
        except Exception as exc:
            return [], f"演示空数据（akshare失败：{exc}）"
    return [], "演示空数据"


def get_limit_up_stocks(online: bool = True) -> tuple[list[LimitUpStock], str]:
    """获取当日涨停股池。"""
    if online:
        try:
            import akshare as ak
            df = ak.stock_zt_pool_em(date=datetime.now().strftime("%Y%m%d"))
            if df is not None and not df.empty:
                stocks = []
                for _, row in df.iterrows():
                    stocks.append(LimitUpStock(
                        code=str(row.get("代码", "")),
                        name=str(row.get("名称", "")),
                        price=_safe_float(row.get("最新价")),
                        change_pct=_safe_float(row.get("涨跌幅")),
                        first_time=str(row.get("首次封板时间", "")),
                        reason=str(row.get("涨停原因", "") or row.get("入选原因", "")),
                        board=str(row.get("所属行业", "") or ""),
                    ))
                return stocks, f"东方财富涨停股池 {datetime.now():%Y-%m-%d}"
        except Exception as exc:
            return [], f"演示空数据（akshare失败：{exc}）"
    return [], "演示空数据"
