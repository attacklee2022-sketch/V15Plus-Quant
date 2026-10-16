# 架构说明

## 分层

```
GUI / 脚本层  (scripts/)      ← 用户交互、定时任务、回测
        ↓
Plus+ 增强层  (_plus_/)       ← 资金流/共振/情绪/组合/自动交易（fail-safe 插件）
        ↓
V15 核心层    (_internal/)    ← 四因子评分、场景、过滤、仓位
```

设计原则：

- **不改核心引擎**：新增能力以插件/增强形式接入，不侵入 `v15_scorer.py` 等核心模块
- **fail-safe**：每个增强模块 `try/except` 独立，失败时返回中性值，不拖垮主流程
- **数据驱动**：参数调整以回测验证为准

## 评分管道

```
四因子(rev/macd/vol/safe)
   → 场景权重调整（超跌反弹 / 趋势接力 / 突破确认）
   → 多周期共振过滤
   → MACD / 动量背离检测
   → 波动率过滤
   → 板块共振过滤
   → 扩展五维（相对强度 / 盈亏比 / 分时 / 跨市场 / 大盘环境）
   → [Plus+] 资金流 + ATR-T1 调节 + 多周期共振 + 情绪量化
   → 仓位建议（止盈止损）
```

### 四因子

| 因子 | 含义 |
|---|---|
| `rev` | 反转（超跌反弹机会） |
| `macd` | MACD 动量 |
| `vol` | 量能 |
| `safe` | 安全结构（止损空间、支撑） |

## 核心模块职责（`_internal/`）

| 模块 | 职责 |
|---|---|
| `v15_scorer.py` | 四因子加权评分 |
| `v15_scenario_engine.py` | 场景识别与权重切换 |
| `v15_filter.py` | 多周期共振 / 背离 / 波动率 / 板块共振 |
| `v15_filter_extras.py` | 扩展五维过滤 |
| `v15_position_manager.py` | 仓位建议 + 止盈止损 |
| `v15_portfolio.py` | 持仓跟踪与盈亏 |
| `v15_sector_fetcher.py` / `v15_sector_history.py` | 板块实时 / 历史数据 |
| `v14_market_data.py` | 行情获取 |
| `v14_tech_scorer.py` / `v14_pattern_detector.py` | 技术评分 / 形态检测 |
| `v14_intraday.py` / `v14_cross_market.py` | 分时 / 跨市场 |
| `v14_news_provider.py` | 新闻源 |
| `report_html.py` | HTML 报告 |

## Plus+ 增强模块（`_plus_/`）

| 模块 | 职责 |
|---|---|
| `plus_pipeline.py` | 增强版管线（包装 V15） |
| `fund_flow_filter.py` | 资金流过滤 |
| `multi_tf_resonance.py` | 多周期共振（5 分 → 15/30/60 分） |
| `sentiment_quant.py` | 新闻情绪量化（±8 分上限） |
| `sector_power.py` | 板块动力评分（T1 资金占比 + T2 量能 × 赔率） |
| `signal_tracker.py` | 信号追踪（SQLite + 次日回填命中率） |
| `news_distiller.py` | 新闻蒸馏（AI 四问简报） |
| `portfolio_optimizer.py` | 组合优化（凯利 / 集中度 / 止损） |
| `auto_trader.py` | 自动虚拟交易引擎（纯本地 + AI） |
| `cloud_sync.py` | 云端同步（HMAC 签名） |

## 数据存储

| 位置 | 内容 |
|---|---|
| `self_stock.txt` | 自选股（每行 `代码 名称`） |
| `portfolio.json` | 手工持仓 |
| `data/auto_account.json` | 自动交易账户状态 |
| `data/signal_history.db` | 信号历史（首次运行自动创建） |
| `data/news_distill.db` | 新闻蒸馏库 |
| `data/sector_history.db` | 板块历史库 |

> 数据库为 SQLite，首次运行自动创建；仓库中不含真实数据，仅提供脱敏示例。
