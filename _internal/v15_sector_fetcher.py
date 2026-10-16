# -*- coding: utf-8 -*-
"""
V15 Alpha — 板块数据获取模块
=============================
从同花顺/东方财富获取行业板块实时数据，
为板块共振过滤提供热度和扩散度信息。
"""
from __future__ import annotations
import json
import os
import time
import ssl
import socket
import http.client
import threading
from datetime import datetime
from typing import Optional
from _http_utils import get_headers
from urllib.parse import urlparse

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False


def _raw_http_get(url: str, headers: dict = None, timeout: int = 15) -> tuple[int, str]:
    """HTTP GET 请求（绕过 requests/urllib 的 Windows TLS 兼容问题）。
    用原始 socket + 自定义 SSL context，直接对话。

    返回 (status_code, response_body_text)
    """
    parsed = urlparse(url)
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    path = parsed.path + ("?" + parsed.query if parsed.query else "")

    if not headers:
        headers = {}
    headers = dict(headers)  # copy
    headers.setdefault("Host", host)
    if "User-Agent" not in headers:
        headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

    sock = socket.create_connection((host, port), timeout=timeout)

    if parsed.scheme == "https":
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        sock = ctx.wrap_socket(sock, server_hostname=host)

    header_lines = [f"GET {path} HTTP/1.1"]
    for k, v in headers.items():
        header_lines.append(f"{k}: {v}")
    header_lines.append("Connection: close")
    header_lines.append("")
    header_lines.append("")
    raw_req = "\r\n".join(header_lines).encode("utf-8")

    sock.sendall(raw_req)
    response = b""
    while True:
        data = sock.recv(65536)
        if not data:
            break
        response += data
    sock.close()

    # 解析 HTTP 响应
    text = response.decode("utf-8", errors="ignore")
    parts = text.split("\r\n\r\n", 1)
    header_part = parts[0]
    body_raw = parts[1] if len(parts) > 1 else ""

    # 提取状态码
    status_line = header_part.split("\r\n", 1)[0]
    status_parts = status_line.split(" ")
    status_code = int(status_parts[1]) if len(status_parts) > 1 else 0

    # 处理分块编码
    if "chunked" in header_part.lower():
        body = ""
        pos = 0
        while pos < len(body_raw):
            crlf = body_raw.find("\r\n", pos)
            if crlf == -1:
                break
            try:
                chunk_size = int(body_raw[pos:crlf], 16)
            except ValueError:
                break
            if chunk_size == 0:
                break
            pos = crlf + 2
            body += body_raw[pos:pos + chunk_size]
            pos += chunk_size + 2
    else:
        body = body_raw

    return status_code, body

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.path.join(APP_DIR, "..", "sector_cache.json")

_cache = {"data": None, "time": 0, "lock": threading.Lock()}
_CACHE_TTL = 600  # 10分钟


def _fetch_sector_data() -> Optional[dict]:
    """从东方财富获取行业板块数据（raw socket，绕过 TLS 兼容问题）。"""
    try:
        qs = "&".join([
            "pn=1", "pz=200", "po=1", "np=1",
            "fltt=2", "invt=2", "fid=f3",
            "fs=m:90+t2",
            "fields=f2,f3,f4,f12,f14,f104,f105,f128,f140,f136",
            "ut=bd1d9ddb04089700cf9c27f6f7426281",
        ])
        url = f"https://push2.eastmoney.com/api/qt/clist/get?{qs}"
        status, body = _raw_http_get(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://quote.eastmoney.com/",
        })
        if status != 200 or not body:
            return None
        data = json.loads(body)
        diffs = (data.get("data") or {}).get("diff") or []
        if diffs:
            sector_map = {}
            for d in diffs:
                name = d.get("f14", "").strip()
                if not name:
                    continue
                change = float(d.get("f3", 0) or 0)
                up_count = int(d.get("f104", 0) or 0)
                down_count = int(d.get("f105", 0) or 0)
                total = max(up_count + down_count, 1)
                breadth = round(up_count / total * 100, 1)
                heat = max(0, min(100, 50 + change * 7 + (breadth - 50) * 0.3))
                sector_map[name] = {
                    "change": change,
                    "rise_count": up_count,
                    "total_count": total,
                    "amount": 0,
                    "breadth": breadth,
                    "heat": round(heat, 1),
                }
            return sector_map
    except Exception:
        pass

    # 第二源：同花顺 (HTML table，需 pd.read_html)
    try:
        import requests as _req
        url = "http://q.10jqka.com.cn/thshy/"
        r = _req.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        if r.status_code == 200:
            r.encoding = "gbk"
            import pandas as pd
            tables = pd.read_html(r.text)
            if tables:
                df = tables[0]
                sector_map = {}
                for _, row in df.iterrows():
                    name = str(row.get("行业", row.iloc[1] if len(row) > 1 else ""))
                    if not name:
                        continue
                    sector_map[name] = {
                        "change": float(row.get("涨跌幅", row.iloc[2] if len(row) > 2 else 0)),
                        "rise_count": int(row.get("上涨家数", row.iloc[4] if len(row) > 4 else 0)),
                        "total_count": int(row.get("总家数", 1)),
                        "amount": float(row.get("总成交额", row.iloc[5] if len(row) > 5 else 0)),
                    }
                    total = sector_map[name]["total_count"]
                    rise = sector_map[name]["rise_count"]
                    sector_map[name]["breadth"] = round(rise / total * 100, 1) if total > 0 else 50
                    sector_map[name]["heat"] = round(
                        max(0, min(100, sector_map[name]["change"] * 2 + sector_map[name]["breadth"] * 0.5 + 50))
                    )
                if sector_map:
                    return sector_map
    except Exception:
        pass

    return None


def get_sector_map(force_refresh: bool = False) -> dict:
    """
    获取板块热度图。
    返回 {板块名: {heat, breadth, change, amount, ...}}
    """
    now = time.time()

    # 先检查缓存（加锁）
    with _cache["lock"]:
        if not force_refresh and _cache["data"] and (now - _cache["time"]) < _CACHE_TTL:
            return _cache["data"].copy()

    # 在锁外发 HTTP 请求
    data = _fetch_sector_data()

    if data:
        # 更新缓存（加锁）
        with _cache["lock"]:
            _cache["data"] = data
            _cache["time"] = now
        try:
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump({"time": now, "data": data}, f, ensure_ascii=False)
        except Exception:
            pass
        return data

    # 降级到缓存文件（加锁）
    with _cache["lock"]:
        try:
            if os.path.exists(CACHE_FILE):
                with open(CACHE_FILE, "r", encoding="utf-8") as f:
                    cached = json.load(f)
                _cache["data"] = cached.get("data", {})
                _cache["time"] = cached.get("time", 0)
                return _cache["data"].copy()
        except Exception:
            pass
        return {}


_INDUSTRY_MAP_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "industry_map.json")
_INDUSTRY_MAP: dict[str, str] | None = None


def _load_industry_map() -> dict[str, str]:
    global _INDUSTRY_MAP
    if _INDUSTRY_MAP is None:
        try:
            with open(_INDUSTRY_MAP_FILE, "r", encoding="utf-8") as f:
                _INDUSTRY_MAP = json.load(f)
        except Exception:
            _INDUSTRY_MAP = {}
    return _INDUSTRY_MAP


def _save_industry_map(data: dict[str, str]):
    try:
        with open(_INDUSTRY_MAP_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _sync_missing_thread(codes: list[str]):
    import http.client as _hc
    imap = _load_industry_map()
    fetched = 0
    for c in codes:
        if c in imap:
            continue
        try:
            sid = ("1." if c.startswith(("60", "68")) else "0.") + c
            path = f"/api/qt/ulist.np/get?fltt=2&invt=2&fields=f12,f100&secids={sid}&ut=bd1d9ddb04089700cf9c27f6f7426281"
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            conn = _hc.HTTPSConnection("push2.eastmoney.com", timeout=10, context=ctx)
            conn.request("GET", path, headers={
                "User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/",
            })
            resp = conn.getresponse()
            if resp.status == 200:
                body = resp.read().decode("utf-8")
                diffs = (json.loads(body).get("data") or {}).get("diff") or []
                if diffs:
                    ind = (diffs[0].get("f100") or "").strip()
                    if ind:
                        imap[c] = ind
                        fetched += 1
        except Exception:
            pass
    if fetched > 0:
        _save_industry_map(imap)


def match_sector(code: str, name: str, sector_map: dict = None) -> str:
    """匹配股票所属板块。优先本地 industry_map.json（O(1)），
    其次 EM API 运行时缓存。本地文件缺失的代码后台线程自动补全。"""
    if not code:
        return "通用"

    # 1. 本地文件（最快，374只命中）
    imap = _load_industry_map()
    if code in imap:
        return imap[code]

    # 2. 运行时缓存（prefetch_tags 写入的 EM 数据）
    em = get_stock_industry(code)
    if em:
        return em

    # 3. 本地文件没有 → 后台补全（本次刷新显示"通用"，下次就有）
    import threading
    threading.Thread(target=_sync_missing_thread, args=([code],), daemon=True).start()

    return "通用"


def get_sector_detail(code: str, name: str, sector_map: dict = None) -> dict:
    """获取单只股票的板块详细信息。优先用 EM 接口 f100（精准），回退到文本匹配。"""
    if sector_map is None:
        sector_map = get_sector_map()

    # 优先东方财富 f100 缓存
    em_industry = get_stock_industry(code)
    if em_industry:
        info = sector_map.get(em_industry, {})
        return {
            "sector": em_industry,
            "heat": info.get("heat", 50),
            "breadth": info.get("breadth", 50),
            "change": info.get("change", 0),
            "available": bool(info),
        }

    # 回退到文本匹配
    sector = match_sector(code, name, sector_map)
    info = sector_map.get(sector, {})
    return {
        "sector": sector,
        "heat": info.get("heat", 50),
        "breadth": info.get("breadth", 50),
        "change": info.get("change", 0),
        "available": bool(info),
    }


# ── 个股行业/地区标签（东方财富批量 API，f100=行业, f102=地区板块）──
_industry_cache: dict[str, str] = {}      # code -> 行业名
_region_cache: dict[str, str] = {}        # code -> 地区板块名
_cache_time = 0.0
_CACHE_TTL = 600


def _fetch_tag_batch(codes: list[str]) -> tuple[dict[str, str], dict[str, str]]:
    """批量获取行业(f100)和地区(f102)标签。使用 http.client 绕过 TLS 兼容问题。"""
    ind_map, reg_map = {}, {}
    for i in range(0, len(codes), 50):
        batch = codes[i:i+50]
        ids = [("1." if c.startswith(("60", "68")) else "0.") + c for c in batch]
        path = "/api/qt/ulist.np/get?fltt=2&invt=2&fields=f12,f14,f100,f102&secids=" + ",".join(ids) + "&ut=bd1d9ddb04089700cf9c27f6f7426281"
        try:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            conn = http.client.HTTPSConnection("push2.eastmoney.com", timeout=15, context=ctx)
            conn.request("GET", path, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://quote.eastmoney.com/",
            })
            resp = conn.getresponse()
            if resp.status == 200:
                body = resp.read().decode("utf-8")
                diffs = (json.loads(body).get("data") or {}).get("diff") or []
                for d in diffs:
                    code = d.get("f12", "")
                    if code:
                        ind_map[code] = (d.get("f100") or "").strip()
                        reg_map[code] = (d.get("f102") or "").strip()
        except Exception:
            pass
    return ind_map, reg_map


def prefetch_tags(codes: list[str]):
    """预热行业+地区缓存。"""
    global _cache_time, _industry_cache, _region_cache
    now = time.time()
    if (now - _cache_time) < _CACHE_TTL and _industry_cache:
        return
    ind, reg = _fetch_tag_batch(codes)
    if ind:
        _industry_cache.update(ind)
        _region_cache.update(reg)
        _cache_time = now


def get_stock_industry(code: str) -> str:
    """获取个股行业标签（f100）。仅从缓存读取，缓存未命中返回空字符串。
    使用前请先调用 prefetch_tags() 预热缓存。"""
    return _industry_cache.get(code, "")


def get_stock_region(code: str) -> str:
    """获取个股地区标签（f102）。缓存未命中时单次查询。"""
    if code in _region_cache:
        return _region_cache[code]
    global _cache_time
    _, reg = _fetch_tag_batch([code])
    if reg:
        _region_cache.update(reg)
        _cache_time = time.time()
    return reg.get(code, "")
