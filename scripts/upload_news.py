# -*- coding: utf-8 -*-
"""
实盘新闻上传脚本。
调 V15Plus 现有的 get_market_news() 获取新闻，不做 DeepSeek 蒸馏，
直接打包原始新闻数据上传到云端。

用法：
    python scripts/upload_news.py                          # 采集并上传
    python scripts/upload_news.py --no-upload               # 只保存本地
    python scripts/upload_news.py --cloud-url http://xxx
"""
from __future__ import annotations
import json
import os
import sys
import time
from datetime import datetime

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.dirname(_SCRIPT_DIR)
_INTERNAL = os.path.join(_APP_DIR, "_internal")
_PLUS = os.path.join(_APP_DIR, "_plus_")
for p in [_INTERNAL, _PLUS, _APP_DIR]:
    if p not in sys.path:
        sys.path.insert(0, p)

DEFAULT_CLOUD_URL = os.environ.get("CLOUD_URL", "http://your-server.example.com/research")
HMAC_SECRET = os.environ.get("HYBRID_HMAC_SECRET", "")


# ═══════════════════════════════════════════════════════════
# 采集新闻（直接用 V15 现有的 get_market_news）
# ═══════════════════════════════════════════════════════════

def fetch_news() -> dict:
    """调用 V15Plus 新闻源，不做蒸馏，只做基本去重归类。"""
    from v14_news_provider import get_market_news, MarketNews

    items, source = get_market_news(online=True)
    print(f"  [采集] {len(items)} 条 (来源: {source})")

    # 按板块归类
    sectors: dict[str, list[dict]] = {}
    all_news: list[dict] = []
    total_sent = 0

    for item in items:
        entry = {
            "title": item.title,
            "sector": item.sector,
            "heat": round(item.heat, 1),
            "sentiment": round(item.sentiment, 1),
            "source": item.source,
            "priority": item.priority,
        }
        all_news.append(entry)
        total_sent += item.sentiment
        sec = item.sector or "通用"
        if sec not in sectors:
            sectors[sec] = []
        sectors[sec].append(entry)

    avg_sent = total_sent / max(len(items), 1)
    if avg_sent >= 60:
        label = "偏多"
    elif avg_sent >= 45:
        label = "中性"
    elif avg_sent >= 30:
        label = "偏空"
    else:
        label = "恐慌"

    return {
        "collected_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S+08:00"),
        "total": len(all_news),
        "market_sentiment": round(avg_sent, 1),
        "market_sentiment_label": label,
        "source": source,
        "sectors": sectors,
        "all_news": all_news,
    }


# ═══════════════════════════════════════════════════════════
# 上传到云端
# ═══════════════════════════════════════════════════════════

def upload(data: dict, cloud_url: str) -> bool:
    """上传新闻数据到云端。"""
    import hashlib
    import hmac
    import requests

    url = cloud_url.rstrip("/") + "/api/v1/news-radar"
    payload = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
    ts = int(time.time())
    message = f"{ts}.".encode("utf-8") + payload
    sig = hmac.new(HMAC_SECRET.encode("utf-8"), message, hashlib.sha256).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "X-Client-Id": "v15plus-news",
        "X-Timestamp": str(ts),
        "X-Signature": sig,
    }
    try:
        resp = requests.post(url, data=payload, headers=headers, timeout=30)
        if resp.status_code in (200, 201):
            print(f"  [上传] 成功 HTTP {resp.status_code}")
            return True
        print(f"  [上传] HTTP {resp.status_code}: {resp.text[:100]}")
        return False
    except Exception as e:
        print(f"  [上传] 失败: {e}")
        return False


def save_local(data: dict) -> str:
    """本地备份。"""
    out_dir = os.path.join(_APP_DIR, "data", "cloud_outbox")
    os.makedirs(out_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(out_dir, f"news_{ts}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, default=str, indent=2)
    print(f"  [保存] {path}")
    return path


# ═══════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-upload", action="store_true")
    parser.add_argument("--cloud-url", default=DEFAULT_CLOUD_URL)
    args = parser.parse_args()

    print(f"[新闻上传] {datetime.now():%Y-%m-%d %H:%M:%S}")
    data = fetch_news()
    print(f"  新闻 {data['total']} 条 | 情绪 {data['market_sentiment_label']} ({data['market_sentiment']}分)")
    for sec, lst in sorted(data['sectors'].items(), key=lambda x: -len(x[1]))[:5]:
        print(f"    {sec}: {len(lst)} 条")

    save_local(data)
    if not args.no_upload:
        print()
        upload(data, args.cloud_url)
    print("[完成]")
