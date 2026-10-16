# -*- coding: utf-8 -*-
"""
股票名称缓存模块
================
从腾讯行情 API 批量拉取股票名称，缓存到本地 JSON。
首次拉取约 80 秒（5342 x 107 批），后续秒读。
"""
from __future__ import annotations
import os, json, time, threading
from urllib.request import Request, urlopen

CACHE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "data", "stock_names.json"
)
BATCH_SIZE = 50
RETRIES = 2
_build_lock = threading.Lock()


def _fetch_batch(codes):
    """从腾讯行情 API 拉一批股票名称。返回 {code: name}。"""
    ids = ",".join([
        f"sh{c}" if c.startswith(("60", "68")) else f"sz{c}" for c in codes
    ])
    url = f"https://qt.gtimg.cn/q={ids}"
    req = Request(url, headers={
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://qt.gtimg.cn/",
    })

    for attempt in range(RETRIES):
        try:
            with urlopen(req, timeout=15) as resp:
                raw = resp.read().decode("gbk", errors="ignore")
            break
        except Exception:
            if attempt == RETRIES - 1:
                return {}
            time.sleep(1.0)

    result = {}
    for line in raw.strip().split(";"):
        line = line.strip()
        if "=" not in line or "~" not in line:
            continue
        fields = line.split("~")
        if len(fields) < 3:
            continue
        code = fields[2].strip()
        name = fields[1].strip()
        if code.isdigit() and len(code) == 6 and name:
            result[code] = name
    return result


def build_name_cache(force=False):
    """构建或加载股票名称缓存。线程安全。"""
    with _build_lock:
        # 已有有效缓存则直接返回
        if not force and os.path.exists(CACHE_PATH):
            try:
                with open(CACHE_PATH, "r", encoding="utf-8") as f:
                    cache = json.load(f)
                if len(cache) >= 4000:
                    return cache
            except Exception:
                pass

        # ── 枚举全 A 股代码 ──
        TDX_BASE = r"D:\通达信\vipdoc"
        all_codes = []
        for market, prefix in [("sh", "60"), ("sh", "68"), ("sz", "00"), ("sz", "30")]:
            lday_dir = os.path.join(TDX_BASE, market, "lday")
            if not os.path.isdir(lday_dir):
                continue
            for fname in os.listdir(lday_dir):
                if fname.startswith(f"{market}{prefix}") and fname.endswith(".day"):
                    code = fname[2:8]
                    if code.isdigit() and len(code) == 6:
                        all_codes.append(code)

        all_codes = sorted(set(all_codes))

        # ── 批量拉取 ──
        name_map = {}
        total = len(all_codes)
        for i in range(0, total, BATCH_SIZE):
            batch = all_codes[i:i + BATCH_SIZE]
            result = _fetch_batch(batch)
            name_map.update(result)
            if (i // BATCH_SIZE + 1) % 20 == 0:
                pct = min(i + BATCH_SIZE, total)
                print(f"  [stock_names] {pct}/{total} ({pct*100//total}%)")

        # ── 验证 ──
        hit = len(name_map)
        miss = total - hit
        print(f"  [stock_names] 完成: {hit}只有效, {miss}只未获取")

        # ── 保存 ──
        try:
            os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
            with open(CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump(name_map, f, ensure_ascii=False)
            print(f"  [stock_names] 已保存到 {CACHE_PATH}")
        except Exception as e:
            print(f"  [stock_names] 保存失败: {e}")

        return name_map


def get_name(code):
    """获取股票名称，缓存未命中返回代码本身。"""
    try:
        names = build_name_cache()
        return names.get(code, code)
    except Exception:
        return code
