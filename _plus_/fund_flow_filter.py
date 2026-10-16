# -*- coding: utf-8 -*-
"""
V15 Plus+ — 资金流过滤模块
=====================
从东方财富获取个股主力资金流，作为第六维度加入评分。
"""
from __future__ import annotations
import json
import socket
import ssl
from urllib.parse import urlparse


def _raw_get(url: str, timeout: int = 8) -> str | None:
    """裸 socket HTTP GET（绕过 urllib Windows TLS 问题）。"""
    parsed = urlparse(url)
    host = parsed.hostname or ""
    port = parsed.port or 443
    path = parsed.path + ("?" + parsed.query if parsed.query else "")

    sock = socket.create_connection((host, port), timeout=timeout)
    try:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        sock = ctx.wrap_socket(sock, server_hostname=host)

        req = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            f"User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36\r\n"
            f"Accept: */*\r\n"
            f"Referer: https://data.eastmoney.com/\r\n"
            f"Connection: close\r\n\r\n"
        )
        sock.sendall(req.encode())
        resp = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            resp += chunk
        parts = resp.split(b"\r\n\r\n", 1)
        return parts[1].decode("utf-8", errors="ignore") if len(parts) > 1 else None
    finally:
        try:
            sock.close()
        except Exception:
            pass


def _fetch_fund_flow(code: str) -> dict | None:
    """获取个股近5日主力资金流（东方财富 push2 API）。"""
    market = "1" if code.startswith(("60", "68")) else "0"
    url = (
        f"https://push2.eastmoney.com/api/qt/stock/fflow/daykline/get"
        f"?secid={market}.{code}"
        f"&fields1=f1,f2,f3,f7"
        f"&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64"
    )
    try:
        body = _raw_get(url, timeout=5)
        if not body:
            return None
        data = json.loads(body)
        klines = (data.get("data") or {}).get("klines") or []
        if not klines:
            return None
        last = klines[-1].split(",")
        if len(last) < 7:
            return None

        def _f(val):
            try:
                return float(val)
            except (ValueError, TypeError):
                return 0.0

        return {
            "main_inflow_1d": _f(last[1]),
            "main_net_pct_1d": _f(last[6]) * 100,
            "main_inflow_3d": _f(last[9]) if len(last) > 9 else 0,
            "main_inflow_5d": _f(last[10]) if len(last) > 10 else 0,
            "main_net_pct_3d": _f(last[13]) * 100 if len(last) > 13 else 0,
            "main_net_pct_5d": _f(last[14]) * 100 if len(last) > 14 else 0,
        }
    except Exception:
        return None


def _filter_fund_flow(code: str) -> dict:
    """资金流过滤 — 主力连续买卖信号。±6分。"""
    result = {"adjustment": 0.0, "facts": [], "detail": {}}

    if not code:
        return {"adjustment": 0.0, "facts": ["[资金] 无股票代码"]}

    try:
        flow = _fetch_fund_flow(code)
        if not flow:
            return {"adjustment": 0.0, "facts": ["[资金] 数据获取失败"]}

        adj = 0.0
        facts = []
        net_1d = flow.get("main_net_pct_1d", 0) or 0
        net_3d = flow.get("main_net_pct_3d", 0) or 0
        net_5d = flow.get("main_net_pct_5d", 0) or 0
        inflow_1d = flow.get("main_inflow_1d", 0) or 0

        result["detail"] = {
            "net_pct_1d": round(net_1d, 2),
            "net_pct_3d": round(net_3d, 2),
            "net_pct_5d": round(net_5d, 2),
            "inflow_1d": round(inflow_1d, 1),
        }

        # 主力连续3日大幅流入 → +5
        if net_1d > 2 and net_3d > 5:
            adj += 5
            facts.append(f"[资金] 主力连续流入 1日{net_1d:+.1f}% 3日{net_3d:+.1f}% +5")
        elif net_1d > 0 and net_3d > 2:
            adj += 3
            facts.append(f"[资金] 主力温和流入 3日{net_3d:+.1f}% +3")

        # 主力连续3日流出 → -5
        if net_1d < -2 and net_3d < -5:
            adj -= 5
            facts.append(f"[资金] 主力连续流出 1日{net_1d:+.1f}% 3日{net_3d:+.1f}% -5")
        elif net_1d < 0 and net_3d < -2:
            adj -= 3
            facts.append(f"[资金] 主力温和流出 3日{net_3d:+.1f}% -3")

        # 5日 vs 1日背离
        if net_1d > 2 and net_5d < -2:
            adj += 2
            facts.append(f"[资金] 短期主力回流 1日{net_1d:+.1f}% vs 5日{net_5d:+.1f}% +2")
        elif net_1d < -2 and net_5d > 2:
            adj -= 2
            facts.append(f"[资金] 短期主力撤离 1日{net_1d:+.1f}% vs 5日{net_5d:+.1f}% -2")

        # 大单
        if inflow_1d > 100 and net_1d > 1:
            adj += 2
            facts.append(f"[资金] 大单净流入 {inflow_1d:.0f}万 +2")
        elif inflow_1d < -100 and net_1d < -1:
            adj -= 2
            facts.append(f"[资金] 大单净流出 {inflow_1d:.0f}万 -2")

        adj = max(-6, min(6, adj))
        result["adjustment"] = round(adj, 1)
        return result

    except Exception as e:
        return {"adjustment": 0.0, "facts": [f"[资金] 异常: {e}"]}
