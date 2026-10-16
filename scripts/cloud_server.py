#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
云端盘面分析系统 - HTTP 服务
端口 8080
  GET  /health              -> 健康检查
  POST /api/v15-cache       -> 接收 V15Plus 缓存
  POST /feishu/callback     -> 飞书回调
  GET  /api/query?q=小金属    -> 按需查询板块
  POST /wechat              -> 企业微信 API 插件（Token 认证）
"""
import os, sys, json, time, warnings, re
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import requests
import pandas as pd

warnings.filterwarnings("ignore")

DEEPSEEK_KEY = os.getenv("DEEPSEEK_KEY", "")
WECHAT_WEBHOOK = os.getenv("WECHAT_WEBHOOK_URL", "")
DATA_DIR = "/home/ubuntu/market-bot/data"
os.makedirs(DATA_DIR, exist_ok=True)

_news_cache = {"data": None, "time": 0}
_NEWS_CACHE_TTL = 300


def get_news_distill():
    global _news_cache
    now = time.time()
    if _news_cache["data"] is not None and now - _news_cache["time"] < _NEWS_CACHE_TTL:
        return _news_cache["data"]
    try:
        from news_distiller import quick_distill
        from v14_news_provider import get_market_news
        items, source = get_market_news(online=True)
        if items:
            distill = quick_distill(items)
            _news_cache["data"] = {
                "sentiment_label": distill.sentiment_label or "中性",
                "sentiment_score": distill.sentiment_score,
                "sentiment_evidence": distill.sentiment_evidence[:3],
                "hot_themes": distill.hot_themes[:5],
                "good_news": distill.good_news[:5],
                "bad_news": distill.bad_news[:5],
                "news_count": distill.news_count,
            }
            _news_cache["time"] = now
            print("[news] 蒸馏完成:", _news_cache["data"]["sentiment_label"])
    except Exception as e:
        print("[news] 蒸馏失败:", e)
        if _news_cache["data"] is not None:
            return _news_cache["data"]
    return _news_cache["data"] or {"sentiment_label": "中性", "sentiment_score": 0,
        "sentiment_evidence": [], "hot_themes": [], "good_news": [], "bad_news": [], "news_count": 0}


def load_latest_cache():
    v15 = None
    for d in [datetime.now().strftime("%Y%m%d"), (datetime.now()-timedelta(days=1)).strftime("%Y%m%d")]:
        p = os.path.join(DATA_DIR, f"v15_{d}.json")
        if os.path.exists(p):
            try: v15 = json.load(open(p)); break
            except: pass

    # V15 板块名映射：通达信名 → 东方财富名
    if v15 and v15.get("sectors"):
        _ensure_sector_map()
        if _tdx_to_ec:
            for _s in v15["sectors"]:
                _tdx_name = _s.get("name", "")
                _ec_name = _tdx_to_ec.get(_tdx_name, "")
                if _ec_name:
                    _s["name"] = _ec_name

    import akshare as ak
    sectors = []
    concepts = []
    try:
        df = ak.stock_fund_flow_industry()
        if df is not None and not df.empty:
            for _, r in df.iterrows():
                sectors.append({
                    "name": str(r.iloc[1]), "index_price": round(float(r.iloc[2]),2) if pd.notna(r.iloc[2]) else 0,
                    "change_pct": round(float(r.iloc[3]),2) if pd.notna(r.iloc[3]) else 0,
                    "main_inflow": round(float(r.iloc[4]),2) if pd.notna(r.iloc[4]) else 0,
                    "main_outflow": round(float(r.iloc[5]),2) if pd.notna(r.iloc[5]) else 0,
                    "net_amount": round(float(r.iloc[6]),2) if pd.notna(r.iloc[6]) else 0,
                    "company_count": int(r.iloc[7]) if pd.notna(r.iloc[7]) else 0,
                    "top_stock": str(r.iloc[8]) if pd.notna(r.iloc[8]) else "",
                    "top_stock_chg": round(float(r.iloc[9]),2) if pd.notna(r.iloc[9]) else 0,
                })
    except: pass
    try:
        df2 = ak.stock_fund_flow_concept()
        if df2 is not None and not df2.empty:
            for _, r in df2.iterrows():
                name = str(r.iloc[1])
                if name:
                    concepts.append({
                        "name": name, "change_pct": round(float(r.iloc[3]),2) if pd.notna(r.iloc[3]) else 0,
                        "main_inflow": round(float(r.iloc[4]),2) if pd.notna(r.iloc[4]) else 0,
                        "main_outflow": round(float(r.iloc[5]),2) if pd.notna(r.iloc[5]) else 0,
                    })
    except Exception as e:
        print(f"[server] 概念采集失败: {e}")
    return {"v15": v15, "sectors": sectors, "concepts": concepts}


_sector_map_data = None
_tdx_to_ec = None
_industry_names = None
_MANUAL_MAP = {
    "IT服务II": "软件服务", "其他电源设备II": "电气设备", "农化制品": "化工",
    "调味发酵品II": "调味品", "互联网电商": "互联网", "照明设备II": "电气设备",
    "个护用品": "个护小家电", "软件开发": "软件服务",
    "石油加工贸易": "石油加工贸易II", "综合": "综合II", "林业": "种植业II",
    "摩托车及其他": "摩托车及其他II", "调味发酵品": "调味品",
    "有色金属": "工业金属", "有色": "工业金属", "快递": "物流", "基建": "建筑装饰",
}


def _ensure_sector_map():
    global _sector_map_data, _tdx_to_ec, _industry_names
    import json, os
    if _sector_map_data is not None:
        return
    # sector_map.json 在 _internal/ 目录下
    base = os.path.dirname(os.path.abspath(__file__))
    map_dir = os.path.join(os.path.dirname(base), "_internal")
    _sector_map_data = {}
    _tdx_to_ec = {}
    _industry_names = set()
    for fn, key in [("sector_map.json", "name2code"), ("industry_map.json", None)]:
        try:
            with open(os.path.join(map_dir, fn), "r", encoding="utf-8") as f:
                d = json.load(f)
            if key:
                _sector_map_data = d.get(key, {})
            else:
                _industry_names = set(v for v in d.values() if v != "-")
        except: pass
    if not _industry_names and _sector_map_data:
        _industry_names = set(_sector_map_data.keys())
    # 构建 通达信名→东方财富名 映射
    try:
        with open(os.path.join(map_dir, "sector_map.json"), "r", encoding="utf-8") as f:
            full = json.load(f)
        n2c = full.get("name2code", {})
        c2n = full.get("code2name", {})
        _tdx_to_ec = {}
        for ec_name, tdx_code in n2c.items():
            tdx_info = c2n.get(tdx_code)
            if tdx_info and isinstance(tdx_info, dict):
                tdx_name = tdx_info.get("name", "")
            elif tdx_info and isinstance(tdx_info, str):
                tdx_name = tdx_info
            else:
                continue
            if tdx_name:
                _tdx_to_ec[tdx_name] = ec_name
    except:
        _tdx_to_ec = None


def _v15_match(query, extra_names=None):
    import re
    _ensure_sector_map()
    all_known = set(_sector_map_data.keys()) | _industry_names
    if extra_names:
        all_known |= extra_names
    q = query.strip()
    if not q:
        return None
    if q in _MANUAL_MAP:
        return _MANUAL_MAP[q]
    if q in all_known:
        return q
    clean = re.sub(r"[Ⅰ-ⅹ]", "", q)
    clean = re.sub(r"[0-9]+$", "", clean)
    if clean in all_known:
        return clean
    if clean + "行业" in all_known:
        return clean + "行业"
    candidates = []
    for kn in all_known:
        if q in kn: candidates.append((len(kn), kn))
        elif kn in q: candidates.append((len(kn), kn))
        elif clean in kn: candidates.append((len(kn)+100, kn))
    if candidates:
        candidates.sort(key=lambda x: (-x[0], x[1]))
        return candidates[0][1]
    for k, v in _MANUAL_MAP.items():
        if q in k or k in q:
            return v
    return None


def find_sector(query, cache):
    q = query.strip()
    if not q:
        return None
    all_sectors = cache.get("sectors", []) + cache.get("concepts", [])
    for s in all_sectors:
        if s["name"] == q:
            return s
    matched = _v15_match(q, set(s["name"] for s in all_sectors))
    if matched:
        for s in all_sectors:
            if s["name"] == matched:
                return s
            if matched in s["name"] or s["name"] in matched:
                return s
    v15 = cache.get("v15", {})
    for s in v15.get("sectors", []):
        if q in s.get("name", ""):
            return {"name": s["name"], "change_pct": 0, "main_inflow": 0,
                    "main_outflow": 0, "net_amount": 0, "company_count": 0,
                    "top_stock": "", "top_stock_chg": 0, "index_price": 0}
    for s in all_sectors:
        if len(q) >= 2 and (q in s["name"] or s["name"] in q or q[:2] in s["name"]):
            return s
    return None


SYSTEM_PROMPT_SECTOR = """你是一位 A 股板块分析师。你收到一个板块的全维度实时数据，请输出一份结构化的解读报告。

【数据来源标签】
每条数据引用必须标注来源标签，标注在数据旁边：
- 【实时】同花顺实时采集的板块资金流数据
- 【V15】V15Plus 本地量化评分
- 【新闻】新闻蒸馏产出的结构化结果
- 【推演】基于上述数据的逻辑推导

【纪律要求】
1. 结论先行：第一行就给出评级（看多/中性/看空）和一句核心判断
2. 每条数据引用必须标注对应的【来源标签】
3. 必须区分"事实"与"推断"：数据本身是事实，基于数据的推演是推断
4. 推断必须标注置信度（高/中/低），并注明推断依据
5. 缺失数据如实写"暂无数据"，不得编造或过度解读
6. 禁止使用"据分析""数据显示"等模糊表述替代具体来源标签

【输出结构】
### 一、板块定位
（一句话总结板块当前状态：强势/中性/弱势 + 核心特征）

### 二、资金面解读
（主力净流入/净流出分析 + 资金结构判断 + V15中线信号）

### 三、趋势判断
（涨跌幅、板块指数位置、与大盘对比 + 均线/量能趋势分析）

### 四、消息催化
（与板块相关的近期热点题材、利好/利空催化，引用新闻数据；若无则写"暂无相关催化"）

### 五、后续预期
（短中期方向判断 + 置信度 + 关键观察点）

### 六、风险提示
（最需要关注的风险点 + 判断失效的条件）"""


def analyze_sector(sector_name, cache):
    sector = find_sector(sector_name, cache)
    if not sector:
        return f"未找到板块「{sector_name}」，请检查名称是否正确"
    v15 = cache.get("v15", {})
    v15_match = None
    for s in v15.get("sectors", []):
        if sector_name in s.get("name", ""):
            v15_match = s
            break
    lines = [f"板块: {sector.get('name', sector_name)}"]
    lines.append(f"涨跌幅: {sector.get('change_pct', 0):+.2f}%【实时】")
    lines.append(f"主力净流入: {sector.get('main_inflow', 0):+.1f}亿【实时】")
    lines.append(f"主力净流出: {sector.get('main_outflow', 0):+.1f}亿【实时】")
    lines.append(f"净额: {sector.get('net_amount', 0):+.1f}亿【实时】")
    lines.append(f"板块指数: {sector.get('index_price', 0):.2f}【实时】")
    lines.append(f"龙头股: {sector.get('top_stock', '')} ({sector.get('top_stock_chg', 0):+.2f}%)【实时】")
    lines.append(f"成分股数: {sector.get('company_count', 0)}【实时】")
    if v15_match:
        sp = v15_match.get('share_pct', 0)
        zs = v15_match.get('z_score', 0)
        dzv = v15_match.get('dz', 0)
        t2s = v15_match.get('t2_score')
        t2l = v15_match.get('t2_label', '')
        tl = v15_match.get('trend_label', '')
        v15_line = f"资金占比{sp:.1f}% z-score={zs:+.2f} dz={dzv:+.4f}"
        if t2s:
            v15_line += f" T2量能:{t2s:.0f}分({t2l}) {tl}"
        v15_line += "【V15】"
    else:
        v15_line = "暂无数据"
    lines.append(f"**V15中线信号**：{v15_line}")
    news = get_news_distill()
    if news:
        lines.append("")
        lines.append(f"【新闻情绪】{news.get('sentiment_label', '中性')}【新闻】")
        for t in news.get("hot_themes", []):
            if t.get("theme"): lines.append(f"热点: {t['theme']}【新闻】")
        for e in news.get("good_news", []):
            if e.get("event"): lines.append(f"利好: {e['event']}【新闻】")
        for e in news.get("bad_news", []):
            if e.get("event"): lines.append(f"利空: {e['event']}【新闻】")
    prompt = "\n".join(lines)
    from openai import OpenAI
    client = OpenAI(api_key=DEEPSEEK_KEY, base_url="https://api.deepseek.com")
    try:
        resp = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "system", "content": SYSTEM_PROMPT_SECTOR}, {"role": "user", "content": prompt}],
            temperature=0.7, max_tokens=1500, timeout=30,
        )
        return resp.choices[0].message.content
    except Exception as e:
        return f"AI分析失败: {e}"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/health" or parsed.path == "/":
            self._json({"status": "ok", "time": datetime.now().strftime("%Y-%m-%d %H:%M")})
        elif parsed.path == "/api/query":
            params = parse_qs(parsed.query)
            q = params.get("q", [""])[0]
            if not q:
                self._json({"error": "缺少参数 q，例: /api/query?q=小金属"})
                return
            cache = load_latest_cache()
            report = analyze_sector(q, cache)
            self._json({"query": q, "report": report})
        elif parsed.path == "/feishu/callback":
            params = parse_qs(parsed.query)
            self._json({"status": "ok"})
        else:
            self.send_response(404); self.end_headers()
            self.wfile.write(b"Not Found")

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        parsed = urlparse(self.path)
        if parsed.path == "/api/v15-cache":
            try:
                data = json.loads(body)
                date = data.get("date", datetime.now().strftime("%Y%m%d"))
                with open(os.path.join(DATA_DIR, f"v15_{date}.json"), "w") as f:
                    json.dump(data, f, ensure_ascii=False)
                self._json({"status": "ok"})
            except Exception as e:
                self._json({"status": "error", "msg": str(e)})
        elif parsed.path == "/feishu/callback":
            try:
                body_str = body.decode("utf-8", errors="replace")
                print("[feishu] 收到:", body_str[:200])
                msg = json.loads(body, strict=False)
                challenge = msg.get("challenge", "")
                if challenge:
                    self._json({"challenge": challenge})
                    print("[feishu] URL验证通过")
                    return
                event = msg.get("event", {})
                header = msg.get("header", {})
                event_type = header.get("event_type", "") or msg.get("type", "")
                print("[feishu] 事件:", event_type)
                if event_type != "im.message.receive_v1":
                    self._json({"code": 0})
                    return
                event_id = header.get("event_id", "")
                if event_id:
                    import os as _os
                    dedup = "/tmp/feishu_events.txt"
                    seen = set()
                    if _os.path.exists(dedup):
                        with open(dedup) as f:
                            seen = set(line.strip() for line in f)
                    if event_id in seen:
                        print("[feishu] 去重跳过")
                        self._json({"code": 0})
                        return
                    with open(dedup, "a") as f:
                        f.write(event_id + chr(10))

                # im.message.receive_v1 格式
                if event_type == "im.message.receive_v1":
                    message = event.get("message", {})
                    msg_type = message.get("msg_type", "")
                    content_str = message.get("content", "{}")
                    chat_id = message.get("chat_id", "") or event.get("chat_id", "")
                    txt = ""
                    if content_str and content_str.startswith("{"):
                        try: txt = json.loads(content_str).get("text", "")
                        except: txt = content_str
                    elif content_str:
                        txt = content_str
                    print("[feishu] msg_type:", msg_type, "chat:", str(chat_id)[:20])
                    if txt and chat_id:
                        cache = load_latest_cache()
                        report = analyze_sector(txt.strip(), cache)
                        # 发回飞书
                        app_id = os.getenv("FEISHU_APP_ID", "")
                        app_secret = os.getenv("FEISHU_APP_SECRET", "")
                        if app_id and app_secret:
                            r = requests.post("https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                                json={"app_id": app_id, "app_secret": app_secret}, timeout=10)
                            j = r.json()
                            if j.get("code") == 0:
                                tk = j["tenant_access_token"]
                                hd = {"Authorization": "Bearer " + tk, "Content-Type": "application/json; charset=utf-8"}
                                bd = {"receive_id": chat_id, "msg_type": "text",
                                      "content": json.dumps({"text": report[:15000]}, ensure_ascii=False)}
                                r2 = requests.post("https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
                                    json=bd, headers=hd, timeout=15)
                                print("[feishu] 回复code:", r2.json().get("code"))
                        print("[feishu] 处理完成")
                self._json({"code": 0})
            except Exception as e:
                print("[feishu] 错误:", e)
                import traceback
                traceback.print_exc()
                self._json({"code": 0})
        elif parsed.path == "/wechat":
            token = self.headers.get("X-WECHAT-TOKEN", "") or self.headers.get("Service-Token", "")
            if token != os.environ.get("WECHAT_TOKEN", ""):
                self._json({"error": "unauthorized"}, 401)
                return
            try:
                msg = json.loads(body)
                query_text = msg.get("query", "") or msg.get("text", {}).get("content", "") or msg.get("content", "")
                if query_text:
                    cache = load_latest_cache()
                    report = analyze_sector(query_text, cache)
                    self._json({"reply": report})
                else:
                    self._json({"reply": "请输入板块名称"})
            except Exception as e:
                self._json({"reply": f"处理失败: {e}"})
        else:
            self._json({"error": "not found"}, 404)

    def _json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

    def log_message(self, format, *args):
        print(f"[server] {args[0]}")


if __name__ == "__main__":
    port = 8080
    server = HTTPServer(("0.0.0.0", port), Handler)
    print(f"[OK] 云端服务启动: http://0.0.0.0:{port}")
    server.serve_forever()
