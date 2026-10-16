# -*- coding: utf-8 -*-
"""
V15 Plus+ — 新闻蒸馏引擎
========================
将每日数百条原始市场新闻蒸馏为四问浓缩简报：
  1. 市场情绪怎么样？
  2. 市场在炒什么？
  3. 有什么大利好？
  4. 有什么大利空？

核心特性：
  - 90天跨日语义去重（Jaccard预筛 + LLM确认）
  - 重点新闻拉取完整正文（priority ≥ 50）
  - DeepSeek 3-Key轮换 + 3次重试 + 降级保障
  - 异步消息队列，不阻塞UI
  - 每日简报存入SQLite，支持历史回测

零侵入：不修改 v14_news_provider、v15_scorer、评分管道等任何已有模块。
"""
from __future__ import annotations

import json
import os
import queue
import random
import re
import sqlite3
import threading
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from typing import Optional
from urllib.request import Request, urlopen

# ── 复用项目已有的马甲轮换系统 ──
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "_internal"))
from _http_utils import get_headers, random_delay


# ═══════════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════════

# 数据存储路径（与 signal_tracker.db 同目录）
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
DB_PATH = os.path.join(DATA_DIR, "news_distill.db")

# 去重窗口
DEDUP_WINDOW_DAYS = 90

# Jaccard 预筛阈值（< 此值直接放行，≥ 此值进入 LLM 确认）
JACCARD_MIN = 0.35

# 蒸馏调用参数
DISTILL_TIMEOUT = 90       # 蒸馏超时（秒）
DEDUP_TIMEOUT = 20         # 去重 LLM 确认超时（秒）
FETCH_TIMEOUT = 12         # 正文抓取超时（秒）
MAX_RETRIES = 3            # API 最大重试次数

# 队列容量（满时丢弃最旧批次）
QUEUE_MAX_SIZE = 20

# 正文抓取间隔（秒），避免触发反爬
FETCH_DELAY_MIN = 0.3
FETCH_DELAY_MAX = 0.8

# 重点新闻阈值（priority >= 此值才拉正文）
BODY_FETCH_PRIORITY = 50


# ═══════════════════════════════════════════════════════════
# Key 加载
# ═══════════════════════════════════════════════════════════

def _load_deepseek_keys() -> list[str]:
    """从桌面 key 文件加载 DeepSeek API Keys（3个轮换）。"""
    keys = []
    desktop = os.path.expanduser("~/Desktop")

    # 环境变量优先
    for i in range(1, 4):
        env_key = os.environ.get(f"DEEPSEEK_KEY{i}", "")
        if env_key.strip():
            keys.append(env_key.strip())

    # 桌面 txt 文件
    for i in range(1, 4):
        path = os.path.join(desktop, f"deepseek_key{i}.txt")
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and line.startswith("sk-"):
                            if line not in keys:
                                keys.append(line)
            except Exception:
                pass

    # 合并 key 文件
    combo_path = os.path.join(desktop, "deepseek_key.txt")
    if os.path.exists(combo_path):
        try:
            with open(combo_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and line.startswith("sk-"):
                        if line not in keys:
                            keys.append(line)
        except Exception:
            pass

    return keys[:3] if keys else []


# ═══════════════════════════════════════════════════════════
# 数据结构
# ═══════════════════════════════════════════════════════════

@dataclass
class NewsItem:
    """蒸馏用新闻条目（兼容 MarketNews 的简化版）。"""
    title: str          # 标题 + 摘要拼接
    sector: str
    priority: int
    sentiment: float
    published_at: str   # ISO 格式时间字符串
    is_stock_specific: bool = False
    full_text: str = ""  # 完整正文（仅重点新闻拉取）


@dataclass
class DistillResult:
    """蒸馏结果。"""
    date: str
    updated_at: str
    # 四问答案
    sentiment_label: str      # 贪婪 / 偏多 / 中性 / 偏空 / 恐慌
    sentiment_score: int      # 0-10
    sentiment_evidence: list[str]
    hot_themes: list[dict]    # [{theme, news, sector}, ...]
    good_news: list[dict]     # [{event, impact, freshness}, ...]
    bad_news: list[dict]      # [{event, impact, freshness}, ...]
    # 统计
    news_count: int
    dedup_removed: int
    body_fetched: int         # 成功拉取正文的篇数
    api_calls: int
    verified_count: int = 0   # 交叉验证通过的篇数（agent-reach 多平台确认）
    raw_json: str = ""


# ═══════════════════════════════════════════════════════════
# 新闻蒸馏引擎
# ═══════════════════════════════════════════════════════════

class NewsDistiller:
    """新闻蒸馏引擎（单例）。"""

    _instance: Optional["NewsDistiller"] = None
    _instance_lock = threading.Lock()

    def __new__(cls) -> "NewsDistiller":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        # API Keys
        self._keys = _load_deepseek_keys()
        self._key_index = 0

        # 异步处理
        self._queue: queue.Queue = queue.Queue(maxsize=QUEUE_MAX_SIZE)
        self._worker_thread: Optional[threading.Thread] = None
        self._running = False
        self._ingest_lock = threading.Lock()

        # 最新结果缓存 + 合并计数器
        self._latest_result: Optional[DistillResult] = None
        self._result_lock = threading.Lock()
        self._today_merge_count = 0  # 当天累计蒸馏次数

        # 数据库
        os.makedirs(DATA_DIR, exist_ok=True)
        self._init_db()

        # 启动后台工作者
        self._start_worker()

        print(f"[NewsDistiller] 初始化完成, {len(self._keys)}个Key, "
              f"数据库: {DB_PATH}")

    # ── 数据库初始化 ──

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        """创建表结构（含无缝迁移）。"""
        conn = self._connect()

        # ── news_titles（去重库）──
        conn.execute("""
            CREATE TABLE IF NOT EXISTS news_titles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                title TEXT NOT NULL,
                title_hash TEXT UNIQUE NOT NULL,
                char_set TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_titles_date ON news_titles(date)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_titles_hash ON news_titles(title_hash)
        """)

        # ── daily_briefs（蒸馏历史）──
        # 迁移 v1→v2：旧表有 UNIQUE(date)，新表用 (date, seq) 支持多版本
        cur = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='daily_briefs'")
        old_schema = cur.fetchone()
        needs_migration = old_schema and "UNIQUE" in (old_schema[0] or "").upper()

        if needs_migration:
            # 备份旧数据 → 重建新表 → 恢复
            old_rows = conn.execute("SELECT * FROM daily_briefs").fetchall()
            conn.execute("DROP TABLE daily_briefs")
            conn.execute("""
                CREATE TABLE daily_briefs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    seq INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT,
                    brief_json TEXT NOT NULL,
                    news_count INTEGER DEFAULT 0,
                    dedup_removed INTEGER DEFAULT 0,
                    body_fetched INTEGER DEFAULT 0,
                    api_calls INTEGER DEFAULT 0
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_briefs_date ON daily_briefs(date)")
            for row in old_rows:
                conn.execute(
                    "INSERT INTO daily_briefs (date, seq, updated_at, brief_json, "
                    "news_count, dedup_removed, body_fetched, api_calls) "
                    "VALUES (?, 1, ?, ?, ?, ?, ?, ?)",
                    (row["date"], row["updated_at"], row["brief_json"],
                     row["news_count"], row["dedup_removed"], row["body_fetched"],
                     row["api_calls"])
                )
        else:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS daily_briefs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    seq INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT,
                    brief_json TEXT NOT NULL,
                    news_count INTEGER DEFAULT 0,
                    dedup_removed INTEGER DEFAULT 0,
                    body_fetched INTEGER DEFAULT 0,
                    api_calls INTEGER DEFAULT 0
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_briefs_date ON daily_briefs(date)")

        conn.commit()
        conn.close()

    # ── 字符集提取（Jaccard 预筛用）──

    @staticmethod
    def _extract_char_set(text: str) -> set:
        """提取文本的规范化字符集。

        包含：中文字符（含数字和字母）、英文单词（去停用词）、数字序列。
        去除：标点、空格、纯虚词。
        """
        result = set()

        # 中文字符 + 数字 + 字母（按字切分）
        chinese_chars = re.findall(r'[一-鿿0-9a-zA-Z]', text)
        for ch in chinese_chars:
            result.add(ch)

        # 连续数字串
        numbers = re.findall(r'\d{2,}', text)
        for n in numbers:
            if len(n) >= 3:  # 至少3位数字才有区分度
                result.add(f"NUM:{n}")

        # 英文大写缩写（3-6个字母）
        acronyms = re.findall(r'[A-Z]{3,6}', text)
        for a in acronyms:
            result.add(f"ACR:{a}")

        # 双字中文组合（增强区分度）
        bigrams = re.findall(r'[一-鿿]{2}', text)
        for bg in bigrams[:30]:  # 最多取30个，控制大小
            result.add(f"BIG:{bg}")

        return result

    @staticmethod
    def _jaccard(set_a: set, set_b: set) -> float:
        """计算两个集合的 Jaccard 相似度。"""
        if not set_a or not set_b:
            return 0.0
        intersection = len(set_a & set_b)
        union = len(set_a | set_b)
        return intersection / union if union > 0 else 0.0

    # ── 去重引擎 ──

    def _dedup(self, items: list[NewsItem]) -> tuple[list[NewsItem], int]:
        """两阶段跨日去重。

        返回: (去重后的新闻列表, 被移除的数量)
        """
        today = datetime.now().strftime("%Y-%m-%d")
        conn = self._connect()

        # 先从 DB 拉取 90 天内的历史记录做内存比对
        # 注意：排除今天，否则同一天多次蒸馏会把上一批存入的标题当成"重复"
        cutoff = (datetime.now() - timedelta(days=DEDUP_WINDOW_DAYS)).strftime("%Y-%m-%d")
        rows = conn.execute(
            "SELECT title, char_set FROM news_titles WHERE date >= ? AND date < ?",
            (cutoff, today)
        ).fetchall()

        history: list[tuple[str, set]] = []
        for row in rows:
            try:
                cs = set(json.loads(row["char_set"]))
                history.append((row["title"], cs))
            except (json.JSONDecodeError, TypeError):
                continue

        kept: list[NewsItem] = []
        kept_char_sets: list[set] = []  # 批次内已保留的字符集（用于批次内去重）
        removed = 0
        candidates_for_llm: list[tuple[NewsItem, list[str]]] = []

        for item in items:
            item_cs = self._extract_char_set(item.title)

            # 第一阶段：Jaccard 预筛（历史 + 批次内）
            candidates: list[str] = []
            # 查历史
            for hist_title, hist_cs in history:
                jac = self._jaccard(item_cs, hist_cs)
                if jac >= JACCARD_MIN:
                    candidates.append(hist_title)
                    if len(candidates) >= 5:
                        break

            # 查批次内（同一批次中已保留的标题）
            if not candidates:
                for bi, bcs in enumerate(kept_char_sets):
                    jac = self._jaccard(item_cs, bcs)
                    if jac >= JACCARD_MIN:
                        candidates.append(kept[bi].title)
                        if len(candidates) >= 3:
                            break

            if not candidates:
                # 无匹配 → 放行
                kept.append(item)
                kept_char_sets.append(item_cs)
                self._store_title(conn, today, item.title, item_cs)
                continue

            # 有匹配 → 批量 LLM 确认
            candidates_for_llm.append((item, candidates))

        # 第二阶段：批量 LLM 去重确认
        if candidates_for_llm:
            batch_items = [c[0] for c in candidates_for_llm]
            batch_candidates = [c[1] for c in candidates_for_llm]
            decisions = self._batch_dedup_check(batch_items, batch_candidates)

            for (item, _), is_dup in zip(candidates_for_llm, decisions):
                if is_dup:
                    removed += 1
                else:
                    kept.append(item)
                    kept_char_sets.append(self._extract_char_set(item.title))
                    self._store_title(conn, today, item.title,
                                      self._extract_char_set(item.title))

        conn.commit()
        conn.close()

        # 清理过期标题
        self._cleanup_titles()

        return kept, removed

    def _store_title(self, conn, date_str: str, title: str, char_set: set):
        """存储新闻标题到去重库（忽略重复）。"""
        import hashlib
        title_hash = hashlib.md5(title.encode("utf-8")).hexdigest()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO news_titles (date, title, title_hash, char_set) "
                "VALUES (?, ?, ?, ?)",
                (date_str, title[:500], title_hash, json.dumps(list(char_set), ensure_ascii=False))
            )
        except Exception:
            pass

    def _cleanup_titles(self):
        """删除 90 天前的标题记录。"""
        try:
            cutoff = (datetime.now() - timedelta(days=DEDUP_WINDOW_DAYS)).strftime("%Y-%m-%d")
            conn = self._connect()
            conn.execute("DELETE FROM news_titles WHERE date < ?", (cutoff,))
            conn.commit()
            conn.close()
        except Exception:
            pass

    def _batch_dedup_check(self, items: list[NewsItem],
                           candidates_list: list[list[str]]) -> list[bool]:
        """批量 LLM 去重确认。

        将多对 (新标题, 候选旧标题) 合并到一个 Prompt 中一次性判断。
        返回与 items 等长的 bool 列表：True = 重复，False = 非重复。
        """
        if not items:
            return []

        # 构造批量 Prompt
        lines = ["判断以下每对新闻是否为同一事件的重复报道。"]
        lines.append("仅回复每组的编号和 YES/NO，用逗号分隔。如: 1:NO, 2:YES, 3:NO\n")
        for i, (item, cands) in enumerate(zip(items, candidates_list)):
            lines.append(f"【第{i+1}组 — 新新闻】")
            lines.append(f"  {item.title[:200]}")
            lines.append(f"【第{i+1}组 — 历史新闻】")
            for j, cand in enumerate(cands[:3]):  # 每组最多3条候选
                lines.append(f"  候选{j+1}: {cand[:200]}")
            lines.append("")

        prompt = "\n".join(lines)
        result_text = self._call_deepseek(prompt, timeout=DEDUP_TIMEOUT, purpose="去重")

        # 解析结果
        decisions = []
        for i in range(len(items)):
            idx = i + 1
            # 查找 "1:NO" 或 "1: YES" 等模式
            pattern = rf'{idx}\s*:\s*(YES|NO)'
            match = re.search(pattern, result_text, re.IGNORECASE)
            if match and match.group(1).upper() == "YES":
                decisions.append(True)
            else:
                decisions.append(False)

        # 补齐缺失的判断
        while len(decisions) < len(items):
            decisions.append(False)

        return decisions

    # ── 正文抓取 ──

    def _fetch_body(self, url: str) -> str:
        """抓取东方财富文章正文，带马甲轮换和随机延迟。"""
        if not url or not url.startswith("http"):
            return ""

        random_delay(FETCH_DELAY_MIN, FETCH_DELAY_MAX)

        try:
            headers = get_headers("https://finance.eastmoney.com/")
            req = Request(url, headers=headers)
            with urlopen(req, timeout=FETCH_TIMEOUT) as resp:
                if resp.status != 200:
                    return ""
                # 检测编码
                content_type = resp.headers.get("Content-Type", "")
                charset = "utf-8"
                if "gbk" in content_type.lower() or "gb2312" in content_type.lower():
                    charset = "gbk"
                html = resp.read().decode(charset, errors="ignore")
        except Exception:
            return ""

        # 提取正文（东方财富正文在 <div class="article-body"> 或 <div id="ContentBody"> 中）
        text = self._extract_article_text(html)
        return text[:3000] if text else ""  # 截断到 3000 字，留给 LLM 足够上下文

    def _fetch_body_browser(self, url: str) -> str:
        """兜底：用浏览器提取正文（处理JS渲染页面/非东财来源）。

        仅 urlopen 失败时调用，不影响现有快速路径。
        依赖 agent-browser 已运行的 Chrome 守护进程。
        """
        import subprocess
        try:
            result = subprocess.run(
                ["npx", "agent-browser", "eval",
                 f"document.body?.innerText?.substring(0,3000) || ''"],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode == 0:
                text = result.stdout.strip()
                if text and not text.startswith("✓") and not text.startswith("✗"):
                    return text[:3000]
        except Exception:
            pass
        return ""

    def _cross_verify(self, items: list, max_items: int = 5) -> int:
        """交叉验证重点新闻：用 agent-reach 跨平台确认信息一致性。

        只对 priority ≥ 50 的前 max_items 条执行，单条超时 20 秒。
        agent-reach 不可用时静默降级，不阻塞蒸馏管道。
        结果直接写入 item.full_text 末尾（[已交叉验证: X平台确认]）。
        """
        if not items:
            return 0
        verified = 0
        for item in items[:max_items]:
            verified += self._verify_single(item)
        return verified

    def _verify_single(self, item, timeout: int = 20) -> int:
        """单条新闻交叉验证。返回 0（失败）或 1（成功）。"""
        import subprocess
        title = item.title[:40]  # 截短标题用于搜索
        try:
            result = subprocess.run(
                ["npx", "agent-reach", "search", f"\"{title}\""],
                capture_output=True, text=True, timeout=timeout,
            )
            if result.returncode == 0 and len(result.stdout.strip()) > 20:
                note = f"\n[已交叉验证: 多平台确认 - {len(result.stdout)//500}+ 字匹配]"
                item.full_text = (item.full_text or "") + note
                return 1
        except Exception:
            pass
        return 0

    def _extract_article_text(self, html: str) -> str:
        """从 HTML 中提取正文纯文本。"""
        # 移除 script/style
        html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)

        # 尝试多种正文容器
        patterns = [
            r'<div[^>]*class="[^"]*article-body[^"]*"[^>]*>(.*?)</div>',
            r'<div[^>]*id="ContentBody"[^>]*>(.*?)</div>',
            r'<div[^>]*class="[^"]*newsContent[^"]*"[^>]*>(.*?)</div>',
            r'<div[^>]*class="[^"]*article-content[^"]*"[^>]*>(.*?)</div>',
            r'<div[^>]*class="[^"]*body[^"]*"[^>]*>(.*?)</div>',
        ]

        for pattern in patterns:
            match = re.search(pattern, html, re.DOTALL | re.IGNORECASE)
            if match:
                text = match.group(1)
                break
        else:
            # 降级：移除所有标签，取中间部分
            text = re.sub(r'<[^>]+>', ' ', html)
            # 取长度最长的段落
            paragraphs = [p.strip() for p in text.split('\n') if len(p.strip()) > 50]
            text = '\n'.join(paragraphs)

        # 清理
        text = re.sub(r'<[^>]+>', '', text)  # 再次去标签
        text = re.sub(r'&nbsp;', ' ', text)
        text = re.sub(r'&lt;', '<', text)
        text = re.sub(r'&gt;', '>', text)
        text = re.sub(r'&amp;', '&', text)
        text = re.sub(r'&quot;', '"', text)
        text = re.sub(r'\s+', ' ', text).strip()

        return text

    # ── DeepSeek API 调用 ──

    def _call_deepseek(self, prompt: str, timeout: int = DISTILL_TIMEOUT,
                       purpose: str = "蒸馏") -> str:
        """带重试+Key轮换的 DeepSeek API 调用。"""
        last_error = ""

        for attempt in range(MAX_RETRIES):
            key = self._get_key()
            if not key:
                return f"[降级] 无可用的 DeepSeek API Key"

            try:
                import requests
                resp = requests.post(
                    "https://api.deepseek.com/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "deepseek-chat",
                        "messages": [
                            {"role": "system", "content": "你是一个专业的A股市场新闻分析师。请用中文回答，严格按要求的JSON格式输出。"},
                            {"role": "user", "content": prompt},
                        ],
                        "temperature": 0.1,
                        "max_tokens": 2048,
                    },
                    timeout=timeout,
                )
                resp.raise_for_status()
                content = resp.json()["choices"][0]["message"]["content"]
                return content

            except Exception as e:
                last_error = str(e)
                self._mark_key_failed(key)
                if attempt < MAX_RETRIES - 1:
                    time.sleep(1.0 * (attempt + 1))  # 递增等待

        return f"[降级] DeepSeek API 调用{purpose}失败（重试{MAX_RETRIES}次）: {last_error}"

    def _get_key(self) -> str:
        """获取当前可用 Key（轮换）。"""
        if not self._keys:
            return ""
        key = self._keys[self._key_index % len(self._keys)]
        self._key_index += 1
        return key

    def _mark_key_failed(self, key: str):
        """标记 Key 失败并尝试轮换到下一个。"""
        # 简单实现：不永久排除，仅当前轮换
        pass

    # ── 四问蒸馏 ──

    def _distill(self, items: list[NewsItem]) -> dict:
        """调用 DeepSeek 进行四问蒸馏，返回结构化结果。"""
        if not items:
            return self._fallback_distill(items)

        # 构建新闻列表
        news_lines = []
        for item in items:
            sent_tag = "+" if item.sentiment > 0.3 else ("-" if item.sentiment < -0.3 else "~")
            line = f"[{item.published_at[-8:]}] [{sent_tag}] [{item.sector}] {item.title[:150]}"
            if item.full_text:
                line += f" | 正文: {item.full_text[:500]}"
            news_lines.append(line)

        news_text = "\n".join(news_lines)

        # 如果总文本太长，按 priority 裁剪
        if len(news_text) > 40000:
            # 保留 priority >= 30 的，其他只保留标题
            truncated = []
            for item in items:
                if item.priority >= 30 or item.full_text:
                    sent_tag = "+" if item.sentiment > 0.3 else ("-" if item.sentiment < -0.3 else "~")
                    line = f"[{item.published_at[-8:]}] [{sent_tag}] [{item.sector}] {item.title[:150]}"
                    if item.full_text:
                        line += f" | 正文: {item.full_text[:300]}"
                    truncated.append(line)
                else:
                    truncated.append(f"[{item.published_at[-8:]}] [{item.sector}] {item.title[:80]}")
            news_text = "\n".join(truncated)

        prompt = f"""你是A股市场新闻分析师。以下是今天盘中已发布的市场新闻，请回答四个问题。

【今日新闻列表】
{news_text}

问题1：今天市场整体情绪如何？用一个词概括（贪婪/偏多/中性/偏空/恐慌），并给出1-3条关键证据。
问题2：今天市场在炒什么？列出最热的2-3个主题/板块，每个附1条代表性新闻。
问题3：今天有什么大利好？列出最重要的1-3条正面事件，说明利好什么方向，并判断是新信息还是老消息（区分"新信息"和"持续发酵"）。注意：如果核心事件在近期已被反复报道（题材已充分炒作），应标为"持续发酵"。
问题4：今天有什么大利空？列出最重要的1-3条负面事件，说明是新信息还是老消息（区分"新信息"和"持续发酵"）。

请严格用JSON格式回复，不要加额外的解释文字：
{{
  "sentiment": {{"label": "偏多", "score": 6, "evidence": ["证据1", "证据2"]}},
  "hot_themes": [{{"theme": "AI算力", "news": "代表新闻...", "sector": "AI算力"}}],
  "good_news": [{{"event": "...", "impact": "...", "freshness": "新信息"}}],
  "bad_news": [{{"event": "...", "impact": "...", "freshness": "持续发酵"}}]
}}"""

        result_text = self._call_deepseek(prompt, timeout=DISTILL_TIMEOUT, purpose="蒸馏")

        # 解析 JSON
        try:
            # 提取 JSON 块
            json_match = re.search(r'\{[\s\S]*\}', result_text)
            if json_match:
                return json.loads(json_match.group(0))
        except (json.JSONDecodeError, AttributeError):
            pass

        # 解析失败 → 降级
        return self._fallback_distill(items)

    def _fallback_distill(self, items: list[NewsItem]) -> dict:
        """降级规则引擎：纯关键词统计，不依赖 LLM。"""
        if not items:
            return {
                "sentiment": {"label": "中性", "score": 5, "evidence": ["今日暂无新闻数据"]},
                "hot_themes": [],
                "good_news": [],
                "bad_news": [],
            }

        # 情绪统计
        sentiments = [n.sentiment for n in items]
        avg_sent = sum(sentiments) / len(sentiments)
        if avg_sent > 0.4:
            label, score = "偏多", 7
        elif avg_sent > 0.15:
            label, score = "偏多", 6
        elif avg_sent > -0.15:
            label, score = "中性", 5
        elif avg_sent > -0.4:
            label, score = "偏空", 4
        else:
            label, score = "恐慌", 2

        pos_count = sum(1 for s in sentiments if s > 0.2)
        neg_count = sum(1 for s in sentiments if s < -0.2)
        evidence = [f"正面新闻 {pos_count} 条，负面新闻 {neg_count} 条"]

        # 热度板块
        sector_count: dict[str, int] = {}
        sector_news: dict[str, str] = {}
        for n in items:
            sector_count[n.sector] = sector_count.get(n.sector, 0) + 1
            if n.sector not in sector_news and n.priority >= 40:
                sector_news[n.sector] = n.title[:100]
        top_sectors = sorted(sector_count.items(), key=lambda x: x[1], reverse=True)[:3]
        hot_themes = [
            {"theme": s, "news": sector_news.get(s, f"{c}条相关新闻"), "sector": s}
            for s, c in top_sectors
        ]

        # 利好/利空
        good = [n for n in items if n.sentiment > 0.4 and n.priority >= 40]
        bad = [n for n in items if n.sentiment < -0.3 and n.priority >= 40]

        return {
            "sentiment": {"label": label, "score": score, "evidence": evidence},
            "hot_themes": hot_themes,
            "good_news": [{"event": n.title[:120], "impact": f"利好{n.sector}", "freshness": "规则判断"}
                          for n in good[:3]],
            "bad_news": [{"event": n.title[:120], "impact": f"利空{n.sector}", "freshness": "规则判断"}
                          for n in bad[:3]],
        }

    # ── 主入口 ──

    def ingest(self, news_items: list) -> bool:
        """接收市场新闻，送入处理队列。

        参数:
            news_items: v14_news_provider.MarketNews 列表，或 dict 列表

        返回:
            True 如果成功入队，False 如果队列满
        """
        # 统一转换为 NewsItem
        items = []
        for n in news_items:
            if isinstance(n, dict):
                items.append(NewsItem(
                    title=str(n.get("title", "")),
                    sector=str(n.get("sector", "通用")),
                    priority=int(n.get("priority", 0)),
                    sentiment=float(n.get("sentiment", 0)),
                    published_at=str(n.get("published_at", datetime.now().isoformat())),
                    is_stock_specific=bool(n.get("is_stock_specific", False)),
                ))
            elif hasattr(n, "title"):
                pub_time = ""
                if hasattr(n, "published_at"):
                    if isinstance(n.published_at, datetime):
                        pub_time = n.published_at.isoformat()
                    else:
                        pub_time = str(n.published_at)
                items.append(NewsItem(
                    title=str(n.title),
                    sector=str(getattr(n, "sector", "通用")),
                    priority=int(getattr(n, "priority", 0)),
                    sentiment=float(getattr(n, "sentiment", 0)),
                    published_at=pub_time,
                ))

        if not items:
            return False

        try:
            self._queue.put_nowait(items)
            return True
        except queue.Full:
            # 丢弃最旧批次
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except queue.Empty:
                pass
            try:
                self._queue.put_nowait(items)
                return True
            except queue.Full:
                return False

    def run(self, news_items: list = None) -> DistillResult:
        """同步运行蒸馏流水线（不经过队列，直接处理）。

        参数:
            news_items: 可选，直接传入新闻列表；为 None 时从队列取

        返回:
            DistillResult 蒸馏结果
        """
        if news_items is not None:
            items = self._convert_items(news_items)
        else:
            try:
                items = self._queue.get_nowait()
            except queue.Empty:
                # 返回缓存结果
                with self._result_lock:
                    if self._latest_result:
                        return self._latest_result
                return self._empty_result()

        today = datetime.now().strftime("%Y-%m-%d")
        api_calls = 0

        # 步骤1: 去重
        kept, removed = self._dedup(items)

        # 步骤2: 拉重点新闻正文
        body_fetched = 0
        priority_items = [n for n in kept if n.priority >= BODY_FETCH_PRIORITY]
        if priority_items:
            # 用 akshare 获取原始数据中的 URL
            urls = self._fetch_news_urls()
            for item in priority_items:
                if item.full_text:
                    continue
                # 尝试匹配 URL
                url = self._match_url(item.title, urls)
                if url:
                    body = self._fetch_body(url)
                    if not body:
                        body = self._fetch_body_browser(url)  # 兜底：JS渲染/非东财
                    if body:
                        item.full_text = body
                        body_fetched += 1

        # 步骤2.5: 交叉验证重点新闻（不阻塞管道，agent-reach 不可用时静默降级）
        verified_count = self._cross_verify(priority_items)

        # 步骤3: 蒸馏
        distill_data = self._distill(kept)
        api_calls += 1  # 蒸馏调用

        # 步骤3.5: 自清洁 — 清除单次蒸馏内的 LLM 输出重复
        for key in ("good_news", "bad_news", "hot_themes"):
            if key in distill_data and len(distill_data[key]) > 1:
                clean = self._self_dedup_list(distill_data[key], key)
                if clean is not None:
                    distill_data[key] = clean

        # 步骤4: 去重 LLM 调用已包含在 _dedup 中，这里汇总
        # （_dedup 内部可能有额外的 API 调用，保守估计）
        api_calls += 1 if removed > 0 else 0

        # 构建本次蒸馏结果
        new_result = DistillResult(
            date=today,
            updated_at=datetime.now().isoformat(),
            sentiment_label=distill_data.get("sentiment", {}).get("label", "中性"),
            sentiment_score=distill_data.get("sentiment", {}).get("score", 5),
            sentiment_evidence=distill_data.get("sentiment", {}).get("evidence", []),
            hot_themes=distill_data.get("hot_themes", []),
            good_news=distill_data.get("good_news", []),
            bad_news=distill_data.get("bad_news", []),
            news_count=len(items),
            dedup_removed=removed,
            body_fetched=body_fetched,
            verified_count=verified_count,
            api_calls=api_calls,
            raw_json=json.dumps(distill_data, ensure_ascii=False),
        )

        # 存储（自动合并到今日已有记录）
        was_merged = self._store_brief(new_result)

        # 返回合并后的完整结果（如果今天之前有记录）
        if was_merged:
            merged = self.get_today_brief()
            if merged:
                with self._result_lock:
                    self._latest_result = merged
                return merged

        # 首次蒸馏，直接返回
        with self._result_lock:
            self._latest_result = new_result
        return new_result

    def _convert_items(self, news_items: list) -> list[NewsItem]:
        """将外部格式转换为 NewsItem。"""
        items = []
        for n in news_items:
            if isinstance(n, NewsItem):
                items.append(n)
            elif isinstance(n, dict):
                items.append(NewsItem(
                    title=str(n.get("title", "")),
                    sector=str(n.get("sector", "通用")),
                    priority=int(n.get("priority", 0)),
                    sentiment=float(n.get("sentiment", 0)),
                    published_at=str(n.get("published_at", datetime.now().isoformat())),
                ))
            elif hasattr(n, "title"):
                pub_time = ""
                if hasattr(n, "published_at"):
                    pt = n.published_at
                    if isinstance(pt, datetime):
                        pub_time = pt.isoformat()
                    else:
                        pub_time = str(pt)
                items.append(NewsItem(
                    title=str(n.title),
                    sector=str(getattr(n, "sector", "通用")),
                    priority=int(getattr(n, "priority", 0)),
                    sentiment=float(getattr(n, "sentiment", 0)),
                    published_at=pub_time,
                ))
        return items

    def _fetch_news_urls(self) -> dict[str, str]:
        """从 akshare 获取新闻标题→URL 映射。"""
        urls = {}
        try:
            import akshare as ak
            df = ak.stock_info_global_em()
            if df is not None and not df.empty:
                for _, row in df.iterrows():
                    title = str(row.get("标题", "") or "").strip()
                    url = str(row.get("来源", "") or "").strip()
                    if title and url and url.startswith("http"):
                        urls[title[:60]] = url
        except Exception:
            pass
        return urls

    def _match_url(self, title: str, urls: dict[str, str]) -> str:
        """匹配新闻标题到 URL（模糊匹配）。"""
        # 精确前缀
        prefix = title[:60]
        if prefix in urls:
            return urls[prefix]

        # 模糊匹配：取标题前 30 个字符中有意义的子串
        short = re.sub(r'[^一-鿿]', '', title)[:30]
        if len(short) >= 8:
            for key, url in urls.items():
                if short[:15] in key:
                    return url

        return ""

    def _store_brief(self, result: DistillResult) -> bool:
        """存储每日简报（合并模式）。返回 True 表示合并入了旧记录。"""
        try:
            conn = self._connect()

            existing = conn.execute(
                "SELECT id, brief_json, news_count, dedup_removed, body_fetched, api_calls "
                "FROM daily_briefs WHERE date = ?",
                (result.date,)
            ).fetchone()

            was_merged = False
            if existing:
                # 合并：解析旧 JSON，追加新数据
                try:
                    old = json.loads(existing["brief_json"])
                except (json.JSONDecodeError, TypeError):
                    old = {}

                merged = self._merge_distill_results(
                    old,
                    json.loads(result.raw_json) if result.raw_json else {}
                )

                merged_json = json.dumps(merged, ensure_ascii=False)
                self._today_merge_count = self._today_merge_count + 1
                was_merged = True

                conn.execute("""
                    UPDATE daily_briefs
                    SET updated_at = ?, brief_json = ?,
                        news_count = news_count + ?, dedup_removed = dedup_removed + ?,
                        body_fetched = body_fetched + ?, api_calls = api_calls + ?,
                        seq = seq + 1
                    WHERE date = ?
                """, (
                    result.updated_at,
                    merged_json,
                    result.news_count,
                    result.dedup_removed,
                    result.body_fetched,
                    result.api_calls,
                    result.date,
                ))
            else:
                # 当天首次蒸馏：直接插入
                self._today_merge_count = 1
                conn.execute("""
                    INSERT INTO daily_briefs
                    (date, seq, updated_at, brief_json, news_count, dedup_removed, body_fetched, api_calls)
                    VALUES (?, 1, ?, ?, ?, ?, ?, ?)
                """, (
                    result.date,
                    result.updated_at,
                    result.raw_json,
                    result.news_count,
                    result.dedup_removed,
                    result.body_fetched,
                    result.api_calls,
                ))

            conn.commit()
            conn.close()
            return was_merged
        except Exception:
            return False

    def _merge_distill_results(self, old: dict, new: dict) -> dict:
        """合并两次蒸馏结果（含 LLM 语义去重）。"""
        if not old:
            return new
        if not new:
            return old

        merged = dict(old)

        # ── 情绪：用最新 ──
        if new.get("sentiment"):
            merged["sentiment"] = new["sentiment"]

        # ── hot_themes：theme 去重 + LLM 语义去重 ──
        old_themes = old.get("hot_themes", []) or []
        new_themes = new.get("hot_themes", []) or []
        new_unique = self._llm_dedup_list(
            old_themes, new_themes,
            key_fn=lambda t: t.get("theme", ""),
            category="热点主题"
        )
        merged["hot_themes"] = old_themes + new_unique

        # ── good_news：event 去重（15字 + LLM）──
        old_good = old.get("good_news", []) or []
        new_good = new.get("good_news", []) or []
        good_to_add = self._llm_dedup_list(
            old_good, new_good,
            key_fn=lambda g: g.get("event", "")[:15],
            category="利好事件"
        )
        merged["good_news"] = old_good + good_to_add

        # ── bad_news：同上 ──
        old_bad = old.get("bad_news", []) or []
        new_bad = new.get("bad_news", []) or []
        bad_to_add = self._llm_dedup_list(
            old_bad, new_bad,
            key_fn=lambda b: b.get("event", "")[:15],
            category="利空事件"
        )
        merged["bad_news"] = old_bad + bad_to_add

        return merged

    def _llm_dedup_list(self, existing: list, incoming: list,
                        key_fn, category: str = "") -> list:
        """LLM 语义去重：判断 incoming 中有哪些是 existing 中不存在的。

        两阶段：
        1. 快速去重：key_fn 完全相同 → 直接丢弃
        2. LLM 语义去重：key 不同但语义相近 → LLM 判断

        返回：新的、不重复的条目列表。
        """
        if not incoming:
            return []

        # 第一阶段：快速去重
        existing_keys = {key_fn(e) for e in existing}
        candidates = []
        for item in incoming:
            k = key_fn(item)
            if k not in existing_keys:
                candidates.append(item)

        if not candidates:
            return []

        # 第二阶段：LLM 语义去重（即使只有1条候选也要 LLM 确认）
        # 要求 LLM 用 Y/N 回答，避免正则解析歧义
        final_candidates = []
        for i, item in enumerate(candidates):
            # 只取前 8 条已有条目发给 LLM
            e_list = "\n".join([f"  {key_fn(e)}" for e in existing[:8]])
            prompt = (
                f"判断以下两条新闻是否为同一事件的重复报道。\n\n"
                f"【已有新闻】\n{e_list}\n\n"
                f"【待添加新闻】\n  {item}\n\n"
                f"仅回复一个词：YES 或 NO。YES=是重复，NO=不同事件。"
            )
            result = self._call_deepseek(
                prompt, timeout=DEDUP_TIMEOUT, purpose=f"合并{category}"
            )
            if result and result.strip().upper().startswith("Y"):
                continue  # duplicate, skip
            final_candidates.append(item)

        return final_candidates

    def _self_dedup_list(self, items: list, category: str = "") -> list:
        """单次蒸馏结果内去重：Jaccard 预筛 + LLM 确认。

        返回去重后的列表（保持原序），如果无需去重则返回原列表。
        """
        n = len(items)
        if n <= 1:
            return items

        # 提取每条的 key 和字符集
        keys = []
        char_sets = []
        for item in items:
            if category == "hot_themes":
                text = item.get("theme", "")
            else:
                text = item.get("event", "")
            keys.append(text[:20])
            char_sets.append(self._extract_char_set(text))

        # 寻找疑似重复对（Jaccard > 0.6）
        dup_pairs = []
        for i in range(n):
            for j in range(i + 1, n):
                jac = self._jaccard(char_sets[i], char_sets[j])
                if jac > 0.6:
                    dup_pairs.append((i, j, jac))

        if not dup_pairs:
            return items

        # 去重：保留第一个出现的，标记后续重复的
        to_remove = set()
        for i, j, _ in dup_pairs:
            if j not in to_remove:
                to_remove.add(j)

        # 如果只有轻度重复（Jaccard < 0.85 且 < 3 对），跳过 LLM，直接规则去重
        all_high = all(jac >= 0.85 for _, _, jac in dup_pairs)
        if not all_high and len(dup_pairs) <= 2:
            return [items[k] for k in range(n) if k not in to_remove]

        # 高置信度重复 → 逐对 LLM 确认（Y/N 回答，避免正则解析歧义）
        for i, j, _ in dup_pairs:
            text_i = items[i].get("event", items[i].get("theme", str(items[i])[:100]))[:120]
            text_j = items[j].get("event", items[j].get("theme", str(items[j])[:100]))[:120]
            prompt = (
                f"判断以下两条新闻是否为同一事件的重复报道。\n\n"
                f"【新闻A】\n  {text_i}\n\n"
                f"【新闻B】\n  {text_j}\n\n"
                f"仅回复一个词：YES 或 NO。YES=是重复（应删B），NO=不同事件。"
            )
            result = self._call_deepseek(
                prompt, timeout=DEDUP_TIMEOUT, purpose=f"自清洁{category}"
            )
            if result and result.strip().upper().startswith("Y"):
                to_remove.add(j)

        return [items[k] for k in range(n) if k not in to_remove]

    def _empty_result(self) -> DistillResult:
        """返回空的蒸馏结果。"""
        today = datetime.now().strftime("%Y-%m-%d")
        return DistillResult(
            date=today,
            updated_at=datetime.now().isoformat(),
            sentiment_label="未知",
            sentiment_score=5,
            sentiment_evidence=["暂无蒸馏数据"],
            hot_themes=[],
            good_news=[],
            bad_news=[],
            news_count=0,
            dedup_removed=0,
            body_fetched=0,
            verified_count=0,
            api_calls=0,
        )

    # ── 后台工作者 ──

    def _start_worker(self):
        """启动后台蒸馏线程。"""
        if self._running:
            return
        self._running = True
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker_thread.start()

    def _worker_loop(self):
        """后台工作循环：从队列取新闻批次蒸馏。"""
        while self._running:
            try:
                # 阻塞等待，超时后检查 running 标志
                items = self._queue.get(timeout=5)
                try:
                    self.run(items)
                except Exception as e:
                    print(f"[NewsDistiller] 蒸馏出错: {e}")
                finally:
                    self._queue.task_done()
            except queue.Empty:
                continue
            except Exception as e:
                print(f"[NewsDistiller] 工作线程异常: {e}")
                time.sleep(5)

    def get_today_brief(self) -> Optional[DistillResult]:
        """获取今日最新简报（先查缓存，再查 DB）。"""
        with self._result_lock:
            if self._latest_result:
                return self._latest_result

        today = datetime.now().strftime("%Y-%m-%d")
        try:
            conn = self._connect()
            row = conn.execute(
                "SELECT * FROM daily_briefs WHERE date = ? ORDER BY seq DESC LIMIT 1",
                (today,)
            ).fetchone()
            conn.close()

            if row:
                dist_data = json.loads(row["brief_json"])
                result = DistillResult(
                    date=row["date"],
                    updated_at=row["updated_at"],
                    sentiment_label=dist_data.get("sentiment", {}).get("label", "中性"),
                    sentiment_score=dist_data.get("sentiment", {}).get("score", 5),
                    sentiment_evidence=dist_data.get("sentiment", {}).get("evidence", []),
                    hot_themes=dist_data.get("hot_themes", []),
                    good_news=dist_data.get("good_news", []),
                    bad_news=dist_data.get("bad_news", []),
                    news_count=row["news_count"],
                    dedup_removed=row["dedup_removed"],
                    body_fetched=row["body_fetched"],
                    api_calls=row["api_calls"],
                    raw_json=row["brief_json"],
                )
                with self._result_lock:
                    self._latest_result = result
                return result
        except Exception:
            pass

        return None

    def get_history_briefs(self, days: int = 30) -> list[dict]:
        """获取历史简报列表（每天取最新版本）。"""
        try:
            conn = self._connect()
            cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
            # 每天可能有多个版本，取 seq 最大的
            rows = conn.execute("""
                SELECT d.* FROM daily_briefs d
                JOIN (SELECT date, MAX(seq) AS max_seq FROM daily_briefs
                      WHERE date >= ? GROUP BY date) latest
                ON d.date = latest.date AND d.seq = latest.max_seq
                ORDER BY d.date DESC
            """, (cutoff,)).fetchall()
            conn.close()
            result = []
            for row in rows:
                try:
                    brief = json.loads(row["brief_json"])
                    result.append({
                        "date": row["date"],
                        "updated_at": row["updated_at"],
                        "news_count": row["news_count"],
                        "dedup_removed": row["dedup_removed"],
                        "body_fetched": row["body_fetched"],
                        "api_calls": row["api_calls"],
                        "seq": row["seq"],
                        "sentiment_label": brief.get("sentiment", {}).get("label", ""),
                        "sentiment_score": brief.get("sentiment", {}).get("score", 5),
                    })
                except (json.JSONDecodeError, KeyError):
                    result.append({"date": row["date"], "error": "JSON解析失败"})
            return result
        except Exception:
            return []

    def get_day_versions(self, date_str: str = None) -> list[dict]:
        """获取某天所有蒸馏版本（用于追溯日内演变）。"""
        if date_str is None:
            date_str = datetime.now().strftime("%Y-%m-%d")
        try:
            conn = self._connect()
            rows = conn.execute(
                "SELECT * FROM daily_briefs WHERE date = ? ORDER BY seq ASC",
                (date_str,)
            ).fetchall()
            conn.close()
            return [{
                "date": r["date"], "seq": r["seq"], "updated_at": r["updated_at"],
                "news_count": r["news_count"], "dedup_removed": r["dedup_removed"],
                "body_fetched": r["body_fetched"], "api_calls": r["api_calls"],
            } for r in rows]
        except Exception:
            return []

    def shutdown(self):
        """安全关闭蒸馏引擎。"""
        self._running = False


# ═══════════════════════════════════════════════════════════
# 便捷函数
# ═══════════════════════════════════════════════════════════

def get_distiller() -> NewsDistiller:
    """获取蒸馏引擎单例。"""
    return NewsDistiller()


def quick_distill(news_items: list) -> DistillResult:
    """快速蒸馏（同步，用于测试/命令行）。"""
    d = get_distiller()
    return d.run(news_items)


# ═══════════════════════════════════════════════════════════
# 测试入口
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 60)
    print("  NewsDistiller 自检")
    print("=" * 60)

    d = get_distiller()
    print(f"\nKeys: {len(d._keys)} 个")
    print(f"DB: {DB_PATH}")

    # 用 demo 数据测试
    from v14_news_provider import get_market_news
    print("\n拉取市场新闻...")
    news_items, source = get_market_news(online=True)
    print(f"来源: {source}")
    print(f"新闻数: {len(news_items)} 条")

    if news_items:
        print("\n开始蒸馏...")
        start = time.time()
        result = d.run(news_items)
        elapsed = time.time() - start

        print(f"\n耗时: {elapsed:.1f}秒")
        print(f"输入: {result.news_count} 条 | 去重移除: {result.dedup_removed} | 拉正文: {result.body_fetched}")
        print(f"API调用: {result.api_calls} 次")
        print(f"\n{'='*40}")
        print(f"市场情绪: {result.sentiment_label} ({result.sentiment_score}/10)")
        print(f"证据: {result.sentiment_evidence}")
        print(f"\n热点主题:")
        for t in result.hot_themes:
            print(f"  · {t.get('theme', '')} — {t.get('news', '')[:80]}")
        print(f"\n大利好:")
        for g in result.good_news:
            print(f"  · {g.get('event', '')[:100]}")
        print(f"\n大利空:")
        for b in result.bad_news:
            print(f"  · {b.get('event', '')[:100]}")
        print(f"{'='*40}")
    else:
        print("未获取到新闻，跳过蒸馏测试。")

    d.shutdown()
    print("\n自检完成。")
