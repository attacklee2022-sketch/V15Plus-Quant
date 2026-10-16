# -*- coding: utf-8 -*-
r"""
V15Plus 全自动管线脚本
======================
模拟真人操作一次完整的 V15Plus 工作流程:

1. 读取自选股 (self_stock.txt)
2. 逐只跑 V15 评分 + 场景识别 + 过滤引擎 (← 这是原来 snapshot 缺少的!)
3. 跑板块动力评分 (sector_power)
4. 跑新闻蒸馏 (news_distiller)
5. 合并成完整 HybridSnapshot (含 stock_scan)
6. 保存到本地文件
7. 自动上传到 Hybrid Research System (如果服务在运行)
8. 调用 Hybrid 系统的采集 + 评分 API 填充数据库

用法:
    cd <项目根目录>
    python scripts/v15plus_auto_pipeline.py                    # 全流程
    python scripts/v15plus_auto_pipeline.py --quick             # 快速模式（只扫描前50只验证）
    python scripts/v15plus_auto_pipeline.py --no-upload         # 只导出不上传
    python scripts/v15plus_auto_pipeline.py --hybrid-url http://localhost:8000
"""
from __future__ import annotations
import os
import sys
import json
import time
import argparse
from datetime import datetime

# ── 路径 ──
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.dirname(_SCRIPT_DIR)
_INTERNAL = os.path.join(_APP_DIR, "_internal")
_PLUS_DIR = os.path.join(_APP_DIR, "_plus_")
for p in [_INTERNAL, _PLUS_DIR, _APP_DIR]:
    if p not in sys.path:
        sys.path.insert(0, p)

TDX_ROOT = os.environ.get("TDX_ROOT_PATH", r"D:\通达信")


# ═══════════════════════════════════════════════════════════
# 1. 读取自选股
# ═══════════════════════════════════════════════════════════

def load_self_stocks() -> list[tuple[str, str]]:
    """读取 self_stock.txt，返回 [(code, name), ...]"""
    path = os.path.join(_APP_DIR, "self_stock.txt")
    stocks = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split(None, 1)
            code = parts[0].strip()
            name = parts[1].strip() if len(parts) > 1 else ""
            stocks.append((code, name))
    print(f"  [自选股] 共 {len(stocks)} 只")
    return stocks


# ═══════════════════════════════════════════════════════════
# 2. 通达信日线数据读取
# ═══════════════════════════════════════════════════════════

import struct
import pandas as pd
import numpy as np


def _read_tdx_day_file(file_path: str) -> bytes | None:
    for attempt in range(3):
        try:
            with open(file_path, "rb") as f:
                return f.read()
        except PermissionError:
            time.sleep(0.3 * (attempt + 1))
        except Exception:
            raise
    return None


def parse_tdx_day(file_path: str) -> pd.DataFrame | None:
    """解析通达信 .day 文件为 DataFrame"""
    data = _read_tdx_day_file(file_path)
    if data is None:
        return None
    try:
        row_cnt = len(data) // 32
        rows = []
        for i in range(row_cnt):
            b = data[i * 32: (i + 1) * 32]
            date_code = struct.unpack("<I", b[0:4])[0]
            open_p = struct.unpack("<I", b[4:8])[0] / 100
            high_p = struct.unpack("<I", b[8:12])[0] / 100
            low_p = struct.unpack("<I", b[12:16])[0] / 100
            close_p = struct.unpack("<I", b[16:20])[0] / 100
            vol = struct.unpack("<I", b[24:28])[0]
            rows.append([str(date_code), open_p, high_p, low_p, close_p, vol])

        df = pd.DataFrame(rows, columns=["date", "开盘", "最高", "最低", "收盘", "成交量"])
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)
        return df
    except Exception:
        return None


def get_tdx_file_path(code: str) -> str:
    """获取通达信 day 文件路径"""
    if code.startswith(("60", "68", "688")):
        return os.path.join(TDX_ROOT, "vipdoc", "sh", "lday", f"sh{code}.day")
    else:
        return os.path.join(TDX_ROOT, "vipdoc", "sz", "lday", f"sz{code}.day")


def load_tdx_data(code: str) -> pd.DataFrame | None:
    """加载股票通达信日线数据"""
    fp = get_tdx_file_path(code)
    if not os.path.exists(fp):
        return None
    return parse_tdx_day(fp)


# ═══════════════════════════════════════════════════════════
# 3. V15 评分管线（三阶段：评分 → 场景 → 过滤）
# ═══════════════════════════════════════════════════════════

def run_v15_pipeline(code: str, name: str) -> dict | None:
    """
    对单只股票执行完整 V15 管线。
    返回评分结果 dict，或 None（数据不足）。
    """
    df = load_tdx_data(code)
    if df is None or df.empty or len(df) < 80:
        return None

    closes = df["收盘"].values.tolist()
    highs = df["最高"].values.tolist()
    lows = df["最低"].values.tolist()
    volumes = df["成交量"].values.tolist()
    last_close = closes[-1]
    last_vol = volumes[-1]
    last_date = str(df["date"].iloc[-1].date())

    # 阶段1: V15 评分引擎
    try:
        from v15_scorer import compute_v15_signal
        v15 = compute_v15_signal(closes, highs, lows, volumes, last_close, last_vol)
    except Exception as e:
        print(f"    [跳过] V15评分异常: {e}")
        return None

    # 阶段2: 场景引擎
    try:
        from v15_scenario_engine import compute_scenario_signal
        sce = compute_scenario_signal(closes, highs, lows, volumes, last_close, last_vol)
    except Exception:
        sce = {"score": 50, "level": "中性", "scenario": "未知"}

    # 阶段3: 过滤引擎
    try:
        from v15_filter import compute_filtered_signal
        fil = compute_filtered_signal(closes, highs, lows, volumes,
                                      last_close, last_vol, code=code, name=name)
    except Exception:
        fil = {"level": "中性", "score": 50, "facts": ["过滤引擎异常"]}

    return {
        "code": code,
        "name": name[:12],
        "date": last_date,
        "close": last_close,
        "volume": last_vol,
        "score": v15.get("score", 50),
        "level": fil.get("level", "中性"),
        "scenario": sce.get("scenario", "未知"),
        "scenario_confidence": sce.get("score", 50) / 100.0,
        "filter_level": fil.get("level", "中性"),
        "filter_score": fil.get("score", 50),
        "resonance_level": str(fil.get("resonance", {}).get("consistency", "—")),
        "divergence": fil.get("momentum_divergence", {}).get("type", "无"),
        "position_action": "",
        "position_pct": 0,
        "stop_loss": 0,
        "take_profit": 0,
        "fund_flow_adj": 0,
        "sentiment_adj": 0,
        "resonance_adj": 0,
        "atr_adj": 0,
        "sector": "",
        "industry": "",
        # 子因子
        "rev_score": v15.get("rev_score", 0),
        "macd_score": v15.get("macd_score", 0),
        "vol_score": v15.get("vol_score", 0),
        "safe_score": v15.get("safe_score", 0),
        "facts": fil.get("facts", []),
    }


# ═══════════════════════════════════════════════════════════
# 4. 生成完整 HybridSnapshot（含 stock_scan）
# ═══════════════════════════════════════════════════════════

def build_full_snapshot(
    stock_results: list[dict],
    market_phase: str = "close",
) -> dict:
    """
    生成完整的 HybridSnapshot，包含 stock_scan + sectors + news + portfolio + watchlist + signals。

    参数:
        stock_results: V15 管线扫描结果列表
        market_phase: 盘面阶段

    返回:
        HybridSnapshot dict
    """
    now = datetime.now()
    time_str = now.strftime("%Y-%m-%dT%H:%M:%S+08:00")

    snapshot = {
        "snapshot_time": time_str,
        "date": now.strftime("%Y%m%d"),
        "market_phase": market_phase,
        "v15_version": "3.5.0",
        "v15plus_version": "0.2.0",
        "export_tool": "v15plus_auto_pipeline.py",
    }

    # ── 4a: stock_scan（个股 V15 评分）──
    snapshot["stock_scan"] = {
        "stocks": stock_results,
        "total_scanned": len(stock_results),
        "updated_at": time_str,
        "provenance": {
            "source": "v15",
            "as_of": time_str,
            "freshness_seconds": 0,
            "confidence": "high",
            "missing_reason": "",
        }
    }
    print(f"  [个股扫描] {len(stock_results)} 只完成")

    # ── 4b: sectors（板块动力）──
    try:
        from _plus_ import sector_power as sp
        sp._compute_all_t1_scores()
        # 获取板块列表
        try:
            chart = sp.get_flow_chart_data()
            daily = chart.get("daily", [])
            sectors = []
            for item in daily:
                share, z_val, tdx_code, name, dz, prev = item
                sec = {
                    "name": name,
                    "tdx_code": tdx_code,
                    "share_pct": round(share, 2),
                    "z_score": round(z_val, 2),
                    "dz": round(dz, 4) if dz else 0,
                    "prev_dz": round(prev, 4) if prev else 0,
                    "power_score": 50.0,
                    "t1_score": 50.0,
                    "t2_score": 50.0,
                    "volume_change_pct": 0,
                    "distill_bonus": 0,
                    "power_rank": 0,
                    "decay_days": 999,
                }
                # 补充综合动力分
                try:
                    score = sp.get_sector_power(tdx_code) if hasattr(sp, "get_sector_power") else {}
                    if score:
                        sec["power_score"] = round(score.get("power_score", 50), 1)
                        sec["t1_score"] = round(score.get("t1_score", 50), 1)
                        sec["t2_score"] = round(score.get("t2_score", 50), 1)
                        sec["volume_change_pct"] = round(score.get("volume_change_pct", 0), 1)
                        sec["distill_bonus"] = round(score.get("distill_bonus", 0), 1)
                        sec["decay_days"] = score.get("decay_days", 999)
                except Exception:
                    pass
                sectors.append(sec)

            sectors.sort(key=lambda x: -x.get("share_pct", 0))
            total = len(sectors)
            for i, sec in enumerate(sectors):
                sec["power_rank"] = sec.get("power_rank") or (i + 1)
                sec["power_total"] = total

            snapshot["sectors"] = {
                "sectors": sectors,
                "t1_updated": True,
                "updated_at": now.strftime("%H:%M:%S"),
                "provenance": {
                    "source": "v15",
                    "as_of": time_str,
                    "confidence": "high",
                    "missing_reason": "",
                }
            }
            print(f"  [板块动力] {len(sectors)} 个板块")
        except Exception as e:
            raise e
    except Exception as e:
        print(f"  [警告] 板块动力采集失败: {e}")
        snapshot["sectors"] = {
            "sectors": [],
            "provenance": {"source": "v15", "as_of": time_str, "confidence": "low", "missing_reason": str(e)}
        }

    # ── 4c: news_distill（新闻蒸馏）──
    try:
        from _plus_ import news_distiller as nd
        from v14_news_provider import get_market_news
        items, _source = get_market_news(online=True)
        if items:
            distill = nd.quick_distill(items)
            snapshot["news_distill"] = {
                "sentiment_label": distill.sentiment_label or "中性",
                "sentiment_score": distill.sentiment_score or 50.0,
                "sentiment_evidence": distill.sentiment_evidence[:3] if hasattr(distill, "sentiment_evidence") else [],
                "hot_themes": distill.hot_themes[:5] if hasattr(distill, "hot_themes") else [],
                "good_news": distill.good_news[:5] if hasattr(distill, "good_news") else [],
                "bad_news": distill.bad_news[:5] if hasattr(distill, "bad_news") else [],
                "news_count": distill.news_count or len(items),
                "updated_at": now.strftime("%H:%M:%S"),
                "provenance": {
                    "source": "v15", "as_of": time_str,
                    "confidence": "high", "missing_reason": "",
                }
            }
            print(f"  [新闻蒸馏] {distill.news_count} 条新闻, 情绪: {distill.sentiment_label}")
        else:
            raise ValueError("get_market_news 返回空")
    except Exception as e:
        print(f"  [新闻蒸馏] 失败或不可用: {e}")
        snapshot["news_distill"] = {
            "sentiment_label": "暂无数据", "sentiment_score": 50,
            "hot_themes": [], "good_news": [], "bad_news": [], "news_count": 0,
            "provenance": {"source": "v15", "as_of": time_str, "confidence": "low", "missing_reason": str(e)}
        }

    # ── 4d: portfolio（投资组合）──
    try:
        pf_path = os.path.join(_APP_DIR, "portfolio.json")
        if os.path.exists(pf_path):
            with open(pf_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            positions = []
            for pos in data.get("positions", []):
                if pos.get("status") == "持有":
                    positions.append({
                        "code": pos["code"],
                        "name": pos["name"],
                        "shares": pos.get("shares", 0),
                        "buy_price": pos.get("buy_price", 0),
                        "current_price": pos.get("current_price", 0),
                        "status": "持有",
                        "pnl_pct": round(pos.get("current_pnl_pct", 0), 2),
                        "stop_loss_pct": pos.get("stop_loss_pct", 0),
                        "take_profit_pct": pos.get("take_profit_pct", 0),
                    })
            snapshot["portfolio"] = {
                "positions": positions,
                "active_count": data.get("stats", {}).get("active_count", 0),
                "total_cost": data.get("stats", {}).get("total_cost", 0),
                "total_market": data.get("stats", {}).get("total_market", 0),
                "total_pnl_pct": data.get("stats", {}).get("total_pnl_pct", 0),
                "provenance": {"source": "v15", "as_of": time_str, "confidence": "high"},
            }
            print(f"  [投资组合] {len(positions)} 持仓")
        else:
            snapshot["portfolio"] = {"positions": [], "active_count": 0,
                                      "provenance": {"source": "v15", "as_of": time_str, "confidence": "low", "missing_reason": "portfolio.json 不存在"}}
    except Exception as e:
        snapshot["portfolio"] = {"positions": [], "active_count": 0,
                                  "provenance": {"source": "v15", "as_of": time_str, "confidence": "low", "missing_reason": str(e)}}

    # ── 4e: watchlist（自选股）──
    stocks_list = [{"code": c, "name": n} for c, n in load_self_stocks()]
    snapshot["watchlist"] = {
        "stocks": stocks_list,
        "total": len(stocks_list),
        "provenance": {"source": "v15", "as_of": time_str, "confidence": "high"},
    }
    print(f"  [自选股] {len(stocks_list)} 只")

    # ── 4f: signals（信号追踪）──
    try:
        from _plus_ import signal_tracker as st
        tracker = st.SignalTracker()
        signals = tracker.get_recent(limit=20)
        snapshot["signals"] = {
            "recent_signals": [{
                "code": s.get("code", ""),
                "name": s.get("name", ""),
                "signal_date": s.get("signal_date", ""),
                "signal_type": s.get("signal_type", ""),
                "score": s.get("score", 0),
                "level": s.get("level", ""),
                "hit_status": s.get("hit_status", "待验证"),
                "hit_pnl_pct": s.get("hit_pnl_pct", 0),
            } for s in (signals or [])],
            "total_tracked": tracker.count(),
            "hit_rate": round(tracker.hit_rate(), 2) if tracker.hit_rate() else 0,
            "provenance": {"source": "v15", "as_of": time_str, "confidence": "medium"},
        }
        print(f"  [信号追踪] {len(signals)} 条")
    except Exception as e:
        snapshot["signals"] = {"recent_signals": [], "total_tracked": 0, "hit_rate": 0,
                                "provenance": {"source": "v15", "as_of": time_str, "confidence": "low", "missing_reason": str(e)}}

    return snapshot


# ═══════════════════════════════════════════════════════════
# 5. 上传到 Hybrid Research System
# ═══════════════════════════════════════════════════════════

def upload_to_hybrid(snapshot: dict, hybrid_url: str) -> bool:
    """
    将快照上传到 Hybrid Research System。

    返回 True=成功, False=失败
    """
    secret = os.environ.get("HYBRID_HMAC_SECRET", "dev-secret-change-in-prod")
    client_id = "v15plus-local"

    try:
        import hashlib
        import hmac
        import requests
    except ImportError:
        print("  [上传] 需要 requests 库: pip install requests")
        return False

    url = hybrid_url.rstrip("/") + "/api/v1/local-snapshots"

    # HMAC 签名
    payload = json.dumps(snapshot, ensure_ascii=False, default=str).encode("utf-8")
    ts = int(time.time())
    message = f"{ts}.".encode("utf-8") + payload
    sig = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()

    headers = {
        "Content-Type": "application/json",
        "X-Client-Id": client_id,
        "X-Timestamp": str(ts),
        "X-Signature": sig,
    }

    for attempt in range(3):
        try:
            resp = requests.post(url, data=payload, headers=headers, timeout=30)
            if resp.status_code in (200, 201):
                data = resp.json()
                dup = "(重复)" if data.get("duplicate") else ""
                print(f"  [上传] 成功 HTTP {resp.status_code} id={data.get('id')} {dup}")
                return True
            else:
                print(f"  [上传] HTTP {resp.status_code}: {resp.text[:100]}")
        except requests.exceptions.ConnectionError:
            print(f"  [上传] 第{attempt+1}次 连接失败 (服务未启动?)")
        except Exception as e:
            print(f"  [上传] 第{attempt+1}次 {e}")
        if attempt < 2:
            time.sleep(2 * (attempt + 1))

    # 全部失败 → 写入 outbox
    outbox_dir = os.path.join(_APP_DIR, "data", "cloud_outbox")
    os.makedirs(outbox_dir, exist_ok=True)
    ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(outbox_dir, f"outbox_{ts_str}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, default=str)
    print(f"  [上传] 已保存到 outbox: {out_path}")
    return False


# ═══════════════════════════════════════════════════════════
# 6. 通知 Hybrid 系统：触发采集 + 评分
# ═══════════════════════════════════════════════════════════

def trigger_hybrid_scoring(hybrid_url: str) -> None:
    """
    通知 Hybrid Research System 执行采集和评分。
    这样 Web 面板和问答就能马上看到数据。
    """
    base = hybrid_url.rstrip("/")

    try:
        import requests
    except ImportError:
        return

    # 采集东方财富实时行情
    try:
        print("  [混合系统] 触发实时行情采集...")
        # 直接导入 collector 模块运行（在 hybrid 项目目录下）
        # 如果无法导入，调用 API
    except Exception:
        pass

    # 触发评分（需 HMAC 签名）
    try:
        import hashlib, hmac
        print("  [混合系统] 触发融合评分...")
        secret = os.environ.get("HYBRID_HMAC_SECRET", "dev-secret-change-in-prod")
        ts = int(time.time())
        signature = hmac.new(
            secret.encode("utf-8"),
            f"{ts}.".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        headers = {
            "X-Client-Id": "v15plus-local",
            "X-Timestamp": str(ts),
            "X-Signature": signature,
        }
        resp = requests.post(f"{base}/api/v1/scores/run", headers=headers, timeout=30)
        if resp.status_code in (200, 201):
            data = resp.json()
            print(f"  [混合系统] 评分完成: 情绪={data.get('sentiment',{}).get('level','?')} 风险={data.get('risk',{}).get('level','?')}")
        else:
            print(f"  [混合系统] 评分触发返回: HTTP {resp.status_code} {resp.text[:100]}")
    except requests.exceptions.ConnectionError:
        print("  [混合系统] 连接失败 (服务未启动)")
    except Exception as e:
        print(f"  [混合系统] 评分触发异常: {e}")


# ═══════════════════════════════════════════════════════════
# 7. 主入口
# ═══════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="V15Plus 全自动管线")
    parser.add_argument("--quick", action="store_true", help="快速模式（只扫前50只）")
    parser.add_argument("--no-upload", action="store_true", help="只导出不上传")
    parser.add_argument("--hybrid-url", default="http://localhost:8000", help="Hybrid 系统地址")
    parser.add_argument("--phase", default="close", choices=["open", "midday", "afternoon", "close"], help="盘面阶段")
    args = parser.parse_args()

    print("=" * 60)
    print("  V15Plus 全自动管线 v1.0")
    print(f"  开始时间: {datetime.now():%Y-%m-%d %H:%M:%S}")
    print(f"  阶段: {args.phase}")
    if args.quick:
        print(f"  模式: 快速 (前50只)")
    print("=" * 60)
    t_start = time.time()

    # ── 第1步: 读取自选股 ──
    print("\n[第1步] 读取自选股...")
    stocks = load_self_stocks()
    if args.quick:
        stocks = stocks[:50]
        print(f"  [快速模式] 截取前 {len(stocks)} 只")

    # ── 第2步: V15 评分管线 ──
    print(f"\n[第2步] 执行 V15 评分管线 (共 {len(stocks)} 只)...")
    stock_results = []
    ok_count = 0
    fail_count = 0
    t0 = time.time()

    for idx, (code, name) in enumerate(stocks):
        if (idx + 1) % 50 == 0 or idx == 0:
            elapsed = time.time() - t_start
            print(f"  进度 {idx+1}/{len(stocks)} | 成功{ok_count} 失败{fail_count} | 耗时{elapsed:.0f}s")

        result = run_v15_pipeline(code, name)
        if result is None:
            fail_count += 1
            continue
        stock_results.append(result)
        ok_count += 1

    scan_elapsed = time.time() - t0
    print(f"  [完成] 成功{ok_count}/{len(stocks)} 失败{fail_count} 耗时{scan_elapsed:.0f}s")

    # ── 第3步: 构建完整快照 ──
    print(f"\n[第3步] 构建完整 HybridSnapshot...")
    snapshot = build_full_snapshot(stock_results, market_phase=args.phase)

    # ── 第4步: 保存本地 ──
    print(f"\n[第4步] 保存到本地...")
    out_dir = os.path.join(_APP_DIR, "data", "cloud_outbox")
    os.makedirs(out_dir, exist_ok=True)
    date_str = snapshot["date"]
    time_str = datetime.now().strftime("%H%M%S")
    save_path = os.path.join(out_dir, f"snapshot_{date_str}_{time_str}.json")
    with open(save_path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, default=str, indent=2)

    # JSON 体积统计
    payload_size = len(json.dumps(snapshot, ensure_ascii=False, default=str).encode("utf-8"))
    print(f"  已保存: {save_path}")
    print(f"  快照大小: {payload_size/1024:.0f} KB")
    print(f"  内容: 个股{len(stock_results)}只 / 板块{len(snapshot.get('sectors',{}).get('sectors',[]))}个 / 自选{snapshot.get('watchlist',{}).get('total',0)}只")

    # ── 第5步: 上传到 Hybrid 系统 ──
    if not args.no_upload:
        print(f"\n[第5步] 上传到 Hybrid Research System ({args.hybrid_url})...")
        success = upload_to_hybrid(snapshot, args.hybrid_url)
        if success:
            print(f"\n[第6步] 调用混合系统采集+评分...")
            trigger_hybrid_scoring(args.hybrid_url)
    else:
        print(f"\n[第5步] 跳过上传 (--no-upload)")

    # ── V15 数据上传到 market-bot ──
    if not args.no_upload:
        print(f"\n[V15上传] 上传板块评分到 market-bot...")
        try:
            from _plus_.sector_power import get_flow_chart_data, get_sector_fund_score
            chart = get_flow_chart_data()
            daily = chart.get("daily", [])
            v15_sectors = []
            for item in daily:
                share, z_val, tdx_code, name, dz, prev = item
                t2 = {"t2_score": 50, "t2_label": "", "vol_ratio": 1.0, "trend_label": ""}
                try:
                    fs = get_sector_fund_score(tdx_code)
                    t2 = {"t2_score": round(fs.get("score", 50), 1), "t2_label": fs.get("label", ""),
                          "vol_ratio": round(fs.get("vol_ratio", 1.0), 2), "trend_label": fs.get("trend_label", "")}
                except: pass
                v15_sectors.append({
                    "name": name, "tdx_code": tdx_code, "share_pct": round(share, 2),
                    "z_score": round(z_val, 2), "dz": round(dz, 4), "prev_dz": round(prev, 4) if prev else 0,
                    "t2_score": t2["t2_score"], "t2_label": t2["t2_label"],
                    "vol_ratio": t2["vol_ratio"], "trend_label": t2["trend_label"],
                })
            v15_sectors.sort(key=lambda x: -x["share_pct"])
            payload = {"date": datetime.now().strftime("%Y%m%d"), "time": datetime.now().strftime("%H:%M:%S"),
                       "sectors": v15_sectors, "total_count": len(v15_sectors)}
            import paramiko
            ssh_key = os.path.expanduser(os.environ.get("CLOUD_SSH_KEY", "~/.ssh/id_ed25519"))
            cloud_host = os.environ.get("CLOUD_HOST", "")
            cloud_user = os.environ.get("CLOUD_USER", "ubuntu")
            remote_dir = os.environ.get("CLOUD_MARKETBOT_DIR", "/home/ubuntu/market-bot")
            client = paramiko.SSHClient(); client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(cloud_host, 22, cloud_user, timeout=10,
                           allow_agent=False, look_for_keys=False, key_filename=ssh_key)
            sftp = client.open_sftp()
            remote_path = f"{remote_dir}/data/v15_{payload['date']}.json"
            import tempfile
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".json", delete=False) as f:
                json.dump(payload, f, ensure_ascii=False)
                tmp_path = f.name
            sftp.put(tmp_path, remote_path)
            sftp.close(); client.close()
            os.unlink(tmp_path)
            print(f"  [V15上传] OK: {len(v15_sectors)}个板块 -> {remote_path}")
        except Exception as e:
            print(f"  [V15上传] 失败: {e}")

    # ── 汇总 ──
    total_elapsed = time.time() - t_start
    print()
    print("=" * 60)
    print(f"  [完成] 全流程完成!")
    print(f"  总耗时: {total_elapsed:.0f}s")
    print(f"  自选股: {len(stocks)} 只")
    print(f"  个股评分: {len(stock_results)} 只")
    print(f"  板块: {len(snapshot.get('sectors',{}).get('sectors',[]))} 个")
    print(f"  快照: {save_path}")
    if not args.no_upload:
        print(f"  Hybrid: {args.hybrid_url}")
    print("=" * 60)


if __name__ == "__main__":
    main()
