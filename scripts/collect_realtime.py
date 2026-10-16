# -*- coding: utf-8 -*-
"""
本地实时行情采集 — 使用 bb-browser 浏览器自动化。

模仿 market-judgment 的取数方式，打开东方财富页面提取数据，
打包上传到云端供报告和推送使用。

用法：
    python scripts/collect_realtime.py                    # 采集+上传
    python scripts/collect_realtime.py --no-upload         # 只保存不上传
    python scripts/collect_realtime.py --cloud-url http://xxx
"""
from __future__ import annotations
import json
import os
import sys
import time
import subprocess
import re
from datetime import datetime

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.dirname(_SCRIPT_DIR)
for p in [_APP_DIR]:
    if p not in sys.path:
        sys.path.insert(0, p)

DEFAULT_CLOUD_URL = os.environ.get("CLOUD_URL", "http://your-server.example.com/research")
HMAC_SECRET = os.environ.get("HYBRID_HMAC_SECRET", "")

# ═══════════════════════════════════════════════════════════
# bb-browser 辅助函数
# ═══════════════════════════════════════════════════════════

def _bb(cmd: list[str], timeout: int = 30) -> str:
    """执行 bb-browser 命令并返回 stdout。"""
    npx_path = os.environ.get("NPX_CMD", "C:\\Program Files\\nodejs\\npx.cmd")
    full_cmd = [npx_path, "bb-browser"] + cmd
    try:
        r = subprocess.run(full_cmd, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
        return r.stdout.strip() or ""
    except subprocess.TimeoutExpired:
        print(f"  [bb] 超时: {' '.join(cmd)}")
        return ""
    except Exception as e:
        print(f"  [bb] 异常: {e}")
        return ""


def _bb_eval(js: str, tab: str, timeout: int = 30) -> str:
    """在指定 tab 执行 JS。"""
    return _bb(["eval", js, "--tab", tab], timeout)


def _bb_open(url: str) -> str:
    """打开页面，返回 tab ID。"""
    r = _bb(["open", url], timeout=15)
    m = re.search(r'tab:\s*(\S+)', r)
    return m.group(1) if m else ""


# ═══════════════════════════════════════════════════════════
# 采集大盘指数
# ═══════════════════════════════════════════════════════════

def collect_index_data(tab: str) -> dict:
    """从上证指数页面提取大盘数据。"""
    tab2 = _bb_open("https://quote.eastmoney.com/sh000001.html")
    if not tab2:
        return {}

    time.sleep(2)
    text = _bb_eval("JSON.stringify({text: document.body.innerText.substring(0, 2000)})", tab2)
    _bb(["close", "--tab", tab2])

    try:
        data = json.loads(text)
        t = data.get("text", "")
        result = {}

        # 解析价格和涨跌幅
        for pat, key in [
            (r"今开[：:]\s*([\d.]+)", "open"),
            (r"最高[：:]\s*([\d.]+)", "high"),
            (r"最低[：:]\s*([\d.]+)", "low"),
            (r"昨收[：:]\s*([\d.]+)", "prev_close"),
            (r"成交额[：:]\s*([\d.，,]+)", "amount"),
            (r"成交量[：:]\s*([\d.，,]+)", "volume"),
            (r"上涨[：:]\s*(\d+)", "rise_count"),
            (r"下跌[：:]\s*(\d+)", "fall_count"),
        ]:
            m = re.search(pat, t)
            if m:
                result[key] = m.group(1).replace(",", "").replace("，", "")

        # 当前价
        m = re.search(r"(\d+[\.\d]+)\s*点", t[t.index("今开")-20:]) if "今开" in t else None
        if not m:
            m = re.search(r"(\d+[\.\d]+)\s*点", t[:200])

        # 涨跌幅
        m2 = re.search(r"([+-]?\d+[\.\d]+)%", t[:200])
        if m2:
            result["change_pct"] = m2.group(1)

        return result
    except Exception as e:
        print(f"  [采集] 指数解析失败: {e}")
        return {}


# ═══════════════════════════════════════════════════════════
# 采集板块资金流（全量多页）
# ═══════════════════════════════════════════════════════════

def collect_sector_data() -> list[list[str]]:
    """
    打开东方财富行业板块资金流页面，全量提取板块数据。

    使用 bb-browser 浏览器自动化（模仿 market-judgment 方式）。
    """
    print("  [采集] 打开东方财富行业板块资金流页面...")
    tab = _bb_open("https://data.eastmoney.com/bkzj/hy.html")
    if not tab:
        print("  [采集] 页面打开失败")
        return []
    print(f"  [采集] 页面已打开, tab={tab}")

    time.sleep(3)

    # 获取总页数
    js_pages = """JSON.stringify(Math.max(...[...document.querySelectorAll('a[data-page]')].map(a => parseInt(a.getAttribute('data-page')))))"""
    total_pages_str = _bb_eval(js_pages, tab, timeout=10)
    try:
        total_pages = int(json.loads(total_pages_str))
    except (json.JSONDecodeError, ValueError, TypeError):
        total_pages = 1
    print(f"  [采集] 总页数: {total_pages}")

    all_data = []

    for page in range(1, total_pages + 1):
        if page > 1:
            # 翻页
            js_click = f"""(()=>{{const t={page},e=document.querySelector('a[data-page="'+t+'"]');if(!e)return 'nf';e.dispatchEvent(new MouseEvent('click',{{bubbles:!0,cancelable:!0,view:window}}));let i=0;return new Promise(r=>{{let c=()=>{{let a=document.querySelector('.pagerbox a.active');if(a&&a.textContent.trim()==t)return r('ok');i++;if(i>=15)return r('retry');setTimeout(c,1000)}};setTimeout(c,300)}})}})()"""
            r = _bb_eval(js_click, tab, timeout=20)
            if "ok" not in r:
                print(f"  [采集] 第{page}页翻页失败: {r}")
                continue
            time.sleep(1)

        # 提取当前页数据
        js_extract = """JSON.stringify({count:document.querySelectorAll('table tbody tr').length,data:[...document.querySelectorAll('table tbody tr')].map(tr=>[...tr.querySelectorAll('td')].map(td=>td.textContent.trim()))})"""
        page_data_str = _bb_eval(js_extract, tab, timeout=15)
        try:
            page_data = json.loads(page_data_str)
            rows = page_data.get("data", [])
            count = page_data.get("count", 0)
            print(f"  [采集] 第{page}页: {count}行")
            all_data.extend(rows)
        except json.JSONDecodeError:
            print(f"  [采集] 第{page}页解析失败")

    _bb(["close", "--tab", tab])
    print(f"  [采集] 共采集 {len(all_data)} 个板块")
    return all_data


# ═══════════════════════════════════════════════════════════
# 构建标准数据格式
# ═══════════════════════════════════════════════════════════

def _parse_yi(val: str) -> float:
    """解析东方财富的'亿'单位数值，如'20.65亿' → 2065000000，'-5.74亿' → -574000000。"""
    if not val:
        return 0
    val = val.replace(",", "").strip()
    if "万亿" in val:
        return float(val.replace("万亿", "").strip()) * 1e12
    if "亿" in val:
        return float(val.replace("亿", "").strip()) * 1e8
    if "万" in val:
        return float(val.replace("万", "").strip()) * 1e4
    try:
        return float(val)
    except ValueError:
        return 0


def build_snapshot(sectors_raw: list[list[str]], index_data: dict) -> dict:
    """构建标准行情快照。"""
    now = datetime.now()
    collected_at = now.strftime("%Y-%m-%dT%H:%M:%S+08:00")

    # 板块资金流
    fund_flow = []
    for row in sectors_raw:
        if len(row) < 10:
            continue
        try:
            fund_flow.append({
                "rank": int(row[0]) if row[0].isdigit() else 0,
                "name": row[1],
                "price": 0,
                "change_pct": float(row[3].replace("%", "").strip()) if row[3] else 0,
                "main_net_inflow": _parse_yi(row[4]),
                "main_inflow_pct": float(row[5].replace("%", "").strip()) if len(row) > 5 and row[5] else 0,
                "super_large_inflow": _parse_yi(row[6]) if len(row) > 6 else 0,
                "large_inflow": _parse_yi(row[8]) if len(row) > 8 else 0,
                "mid_inflow": _parse_yi(row[10]) if len(row) > 10 else 0,
                "small_inflow": _parse_yi(row[12]) if len(row) > 12 else 0,
            })
        except (ValueError, IndexError) as e:
            continue

    # 指数
    indices = []
    if index_data:
        indices.append({
            "code": "000001",
            "name": "上证指数",
            "price": float(index_data.get("prev_close", 0) or 0),
            "change_pct": float(index_data.get("change_pct", 0) or 0),
            "open": float(index_data.get("open", 0) or 0),
            "high": float(index_data.get("high", 0) or 0),
            "low": float(index_data.get("low", 0) or 0),
            "amount": float(index_data.get("amount", 0) or 0),
        })

    # 价格涨跌统计
    rise_count = int(index_data.get("rise_count", 0))
    fall_count = int(index_data.get("fall_count", 0))
    market_state = "暂无数据"
    if rise_count + fall_count > 0:
        ratio = rise_count / max(rise_count + fall_count, 1)
        if ratio > 0.6:
            market_state = "普涨行情"
        elif ratio > 0.5:
            market_state = "涨多跌少"
        elif ratio > 0.4:
            market_state = "涨跌互现"
        elif ratio > 0.3:
            market_state = "跌多涨少"
        else:
            market_state = "普跌行情"

    # 融合评分（简化为只用资金流排名）
    fused_sectors = []
    for i, s in enumerate(fund_flow[:50]):
        score = _calc_sector_score(s)
        fused_sectors.append({
            "code": "",
            "name": s["name"],
            "score": score,
            "level": _score_level(score),
            "rank": i + 1,
        })

    return {
        "collected_at": collected_at,
        "date": now.strftime("%Y%m%d"),
        "data_type": "full",
        "indices": indices,
        "market_stats": {
            "total_stocks": rise_count + fall_count,
            "rise_count": rise_count,
            "fall_count": fall_count,
            "market_state": market_state,
        },
        "sector_fund_flow": fund_flow,
        "fused_sectors": fused_sectors,
    }


def _calc_sector_score(s: dict) -> float:
    """简化的板块评分：资金面为主。"""
    inflow = s.get("main_net_inflow", 0)
    change = s.get("change_pct", 0)
    # 主力净流入评分
    if inflow > 5e8:
        inflow_score = 80 + min(20, inflow / 5e8 * 5)
    elif inflow > 0:
        inflow_score = 55 + inflow / 5e8 * 25
    elif inflow > -5e8:
        inflow_score = 40 + inflow / 5e8 * 15
    else:
        inflow_score = max(10, 30 + inflow / 1e9 * 10)

    # 涨跌幅评分
    if change > 2:
        change_score = 80
    elif change > 0:
        change_score = 60
    elif change > -2:
        change_score = 45
    else:
        change_score = 25

    return round(inflow_score * 0.6 + change_score * 0.4)


def _score_level(score: float) -> str:
    if score >= 75:
        return "高关注"
    elif score >= 60:
        return "关注"
    elif score >= 45:
        return "中性"
    else:
        return "弱"


# ═══════════════════════════════════════════════════════════
# 上传到云端
# ═══════════════════════════════════════════════════════════

def upload_to_marketbot(data: dict) -> bool:
    """上传行情数据到 market-bot（云服务器，nginx 代理 80->8080）。

    完整上传采集到的所有数据（指数/板块资金流/涨跌统计/融合评分），
    market-bot 自行取所需字段。
    """
    import hashlib
    import hmac
    import requests

    url = os.environ.get("MARKETBOT_URL", "http://your-server.example.com/api/v1/market-data")

    # 完整快照直接上传，不做字段裁剪
    payload = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
    try:
        resp = requests.post(url, data=payload, headers={"Content-Type": "application/json"}, timeout=30)
        if resp.status_code in (200, 201):
            print(f"  [market-bot] 上传成功 HTTP {resp.status_code}")
            return True
        print(f"  [market-bot] HTTP {resp.status_code}: {resp.text[:100]}")
        return False
    except Exception as e:
        print(f"  [market-bot] 连接失败: {e}")
        return False


def upload(data: dict, cloud_url: str) -> bool:
    """上传行情数据到云端。"""
    import hashlib
    import hmac
    import requests

    url = cloud_url.rstrip("/") + "/api/v1/market-snapshot"
    payload = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
    ts = int(time.time())
    message = f"{ts}.".encode("utf-8") + payload
    sig = hmac.new(HMAC_SECRET.encode("utf-8"), message, hashlib.sha256).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "X-Client-Id": "v15plus-realtime",
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
    path = os.path.join(out_dir, f"market_{ts}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, default=str, indent=2)
    print(f"  [保存] {path}")
    return path


# ═══════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-upload", action="store_true")
    parser.add_argument("--cloud-url", default=DEFAULT_CLOUD_URL)
    args = parser.parse_args()

    print("=" * 50)
    print(f"  本地实时行情采集 (bb-browser)")
    print(f"  {datetime.now():%Y-%m-%d %H:%M:%S}")
    print("=" * 50)

    # 采集板块数据
    print("\n[1/3] 采集板块资金流...")
    sectors_raw = collect_sector_data()

    # 采集指数数据
    print("\n[2/3] 采集大盘指数...")
    index_data = collect_index_data("")

    # 构建快照
    print("\n[3/3] 构建并上传...")
    snapshot = build_snapshot(sectors_raw, index_data)
    print(f"  板块: {len(snapshot['sector_fund_flow'])} 个")
    print(f"  指数: {len(snapshot['indices'])} 条")
    print(f"  涨跌: {snapshot['market_stats']['rise_count']}/{snapshot['market_stats']['fall_count']}")
    print(f"  市场状态: {snapshot['market_stats']['market_state']}")

    # 数据质量闸门：不足128个板块自动重试（东方财富行业板块固定总数）
    if len(snapshot['sector_fund_flow']) < 128:
        print(f"\n  ⚠️  板块数 {len(snapshot['sector_fund_flow'])} < 128，30秒后重试...")
        time.sleep(30)
        for attempt in range(2):
            print(f"\n[重试 {attempt+1}/2] 重新采集...")
            sectors_raw = collect_sector_data()
            index_data = collect_index_data("")
            snapshot = build_snapshot(sectors_raw, index_data)
            n = len(snapshot['sector_fund_flow'])
            print(f"  板块: {n} 个")
            if n >= 128:
                break
            if attempt < 1:
                print(f"\n  ⚠️  仍不足128，再等30秒...")
                time.sleep(30)

    if len(snapshot['sector_fund_flow']) < 128:
        print(f"\n  ❌ 重试2次后板块数仍为 {len(snapshot['sector_fund_flow'])}，跳过保存和上传。")
        print("\n[中止]")
        return

    save_local(snapshot)

    if not args.no_upload:
        print()
        upload_to_marketbot(snapshot)

    print("\n[完成]")


if __name__ == "__main__":
    main()
