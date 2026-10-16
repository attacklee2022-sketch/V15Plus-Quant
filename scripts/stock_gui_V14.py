import os

# Suppress noisy libpng warnings caused by PNG files with old/incorrect sRGB profiles.
# The warning is harmless but can flood the console when Tk/Matplotlib loads images.
os.environ["PNG_SKIP_sRGB_CHECK_PROFILE"] = "1"

import tkinter as tk
from tkinter import scrolledtext, ttk, messagebox
import pandas as pd
import numpy as np
import struct
import sys
try:
    import requests
except ImportError:
    requests = None
from datetime import datetime
try:
    import matplotlib
    matplotlib.use("TkAgg")
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    from mplfinance.original_flavor import candlestick_ohlc
    import matplotlib.dates as mdates
    CHART_AVAILABLE = True
except ImportError:
    matplotlib = None
    plt = None
    FigureCanvasTkAgg = None
    candlestick_ohlc = None
    mdates = None
    CHART_AVAILABLE = False
import threading
import concurrent.futures
import re
import json
import traceback

# ---------------- V15 择时雷达模块导入 ----------------
if getattr(sys, 'frozen', False):
    # PyInstaller --onedir 模式：sys._MEIPASS 指向 _internal 目录
    _INTERNAL_DIR = sys._MEIPASS
else:
    _SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
    _INTERNAL_DIR = os.path.join(_SCRIPT_DIR, "..", "_internal")
    # scripts 目录自身也要在 path 里（backtest_factor_ic.py 等位于同目录）
    if _SCRIPT_DIR not in sys.path:
        sys.path.insert(0, _SCRIPT_DIR)
if os.path.isdir(_INTERNAL_DIR):
    sys.path.insert(0, _INTERNAL_DIR)

try:
    from v15_scorer import compute_v15_signal, compute_v15_signal_from_df
    from v15_scenario_engine import compute_scenario_signal, detect_scenario
    from v15_filter import compute_filtered_signal, compute_filtered_signal_from_df
    from v15_position_manager import compute_position_advice, compute_position_advice_from_signal
    from v15_sector_fetcher import get_sector_map, match_sector
    V15_AVAILABLE = True
except ImportError as e:
    V15_AVAILABLE = False
    print(f"[WARN] V15模块导入失败（择时雷达功能不可用）: {e}")
    # 桩函数，防止GUI调用时崩溃
    def compute_v15_signal(*a, **kw):
        return {"level": "中性", "score": 50.0, "facts": ["V15模块未加载"]}
    def compute_v15_signal_from_df(*a, **kw):
        return {"level": "中性", "score": 50.0, "facts": ["V15模块未加载"]}
    def compute_scenario_signal(*a, **kw):
        return {"level": "中性", "score": 50.0, "scenario": "无场景", "facts": ["V15模块未加载"]}
    def compute_filtered_signal(*a, **kw):
        return {"level": "中性", "score": 50.0, "scenario": "无场景", "facts": ["V15模块未加载"]}
    def compute_filtered_signal_from_df(*a, **kw):
        return {"level": "中性", "score": 50.0, "scenario": "无场景", "facts": ["V15模块未加载"]}
    def compute_position_advice(*a, **kw):
        return {"action": "观望", "reason": "V15模块未加载", "position_suggestion": "不操作"}
    def compute_position_advice_from_signal(*a, **kw):
        return {"action": "观望", "reason": "V15模块未加载", "position_suggestion": "不操作"}
    def get_sector_map(*a, **kw):
        return {}, "V15模块未加载"
    def match_sector(*a, **kw):
        return "通用"

# 场景级别映射（用于颜色标签）
V15_LEVEL_ORDER = ["强势", "偏强", "中性", "弱势"]
V15_LEVEL_WEIGHT = {lvl: i for i, lvl in enumerate(V15_LEVEL_ORDER)}

# ---------------- 全局异常捕获 ----------------
def global_except_hook(exc_type, exc_value, exc_traceback):
    with open("error_log.txt", "a", encoding="utf-8") as f:
        f.write("".join(traceback.format_exception(exc_type, exc_value, exc_traceback)))
sys.excepthook = global_except_hook

# ---------------- 修复：matplotlib 中文 ----------------
if CHART_AVAILABLE:
    plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'sans-serif']
    plt.rcParams['axes.unicode_minus'] = False
    # 确保中文渲染（首次删除缓存，后续使用缓存加速启动）
    import matplotlib.font_manager as _fm
    try:
        # 仅首次重建缓存（中文经常丢失），之后使用 ~/.matplotlib/fontlist-v330.json
        _fm._load_fontmanager(try_read_cache=False)
    except Exception:
        pass
    except Exception:
        pass
import warnings
warnings.filterwarnings('ignore')

# ==================== 【配置区】 ====================
APP_DIR = os.path.dirname(os.path.abspath(__file__))
TDX_ROOT = os.environ.get("TDX_ROOT_PATH", r"D:\通达信")
SELF_STOCK_TXT = os.path.join(APP_DIR, "self_stock.txt")
REPORT_TXT = "技术形态综合选股报告.txt"
BATCH_SCAN_REPORT = "批量扫描结果报告.txt"
BATCH_AI_REPORT = "批量全技术维度AI报告.txt"
AI_DEBUG_TXT = "AI输入事实快照.txt"
UNSUPPORTED_AI_TERMS = [
    "主力", "庄家", "游资", "机构资金", "资金流", "北向", "龙虎榜",
    "公告", "财报", "业绩预告", "行业消息", "板块消息", "新闻",
    "利好", "利空", "抢筹", "出货", "洗盘", "控盘"
]
UNCERTAIN_MARKERS = [
    "暂无数据", "无法判断", "不能判断", "未提供", "缺失", "不可判断",
    "没有提供", "不具备", "无数据支撑"
]
TECHNICAL_SHAPE_PATTERNS = {
    "5日线金叉10日线", "5日线死叉10日线", "5日线金叉20日线", "5日线死叉20日线",
    "金针探底", "阳包阴", "放量长阳", "缩量回调", "放量突破压力", "缩量回踩支撑",
    "量价顶背离", "量价底背离", "布林上轨压力", "布林下轨支撑", "老鸭头形态",
    # ── 失效形态 ──
    "假突破", "破位",
    "金针探底失效", "阳包阴失效", "放量长阳失效",
    "均线金叉失效", "老鸭头失效",
}

def is_valid_deepseek_key(value):
    value = (value or "").strip()
    return value.startswith("sk-") and len(value) >= 20

def read_text_key(file_name):
    # 搜索路径：scripts/ → V15Alpha/ → Desktop/
    search_dirs = [
        APP_DIR,
        os.path.dirname(APP_DIR),
        os.path.expanduser("~/Desktop"),
    ]
    for search_dir in search_dirs:
        try:
            file_path = os.path.join(search_dir, file_name)
            if not os.path.exists(file_path):
                continue
            with open(file_path, "r", encoding="utf-8") as f:
                for line in f:
                    key = line.strip()
                    if not key or key.startswith("#"):
                        continue
                    if is_valid_deepseek_key(key):
                        return key, file_path
                    return "", f"{file_path}（格式不像DeepSeek Key）"
        except:
            continue
    return "", ""

def load_deepseek_key(number):
    env_names = [f"DEEPSEEK_KEY{number}", "DEEPSEEK_KEY"]
    for env_name in env_names:
        key = os.getenv(env_name, "").strip()
        if is_valid_deepseek_key(key):
            return key, f"环境变量 {env_name}"

    file_names = [f"deepseek_key{number}.txt", "deepseek_key.txt"]
    invalid_sources = []
    for file_name in file_names:
        key, source = read_text_key(file_name)
        if key:
            return key, source
        if source:
            invalid_sources.append(source)

    if invalid_sources:
        return "", "；".join(invalid_sources)
    return "", f"未找到 DEEPSEEK_KEY{number}/DEEPSEEK_KEY 或 {', '.join(file_names)}"

def mask_key(key):
    key = (key or "").strip()
    if len(key) <= 12:
        return "未配置"
    return f"{key[:6]}...{key[-4:]}"

DEEPSEEK_KEY1, DEEPSEEK_KEY1_SOURCE = load_deepseek_key(1)
DEEPSEEK_KEY2, DEEPSEEK_KEY2_SOURCE = load_deepseek_key(2)
DEEPSEEK_KEY3, DEEPSEEK_KEY3_SOURCE = load_deepseek_key(3)
AI_TEMPERATURE = 0.1

BACKTEST_YEARS = 3
FORWARD_1 = 10
FORWARD_2 = 20
# ====================================================

def _read_tdx_day_file(file_path: str) -> bytes | None:
    """读取通达信 .day 文件，自动处理文件共享冲突，最多重试3次。"""
    import time as _time
    last_err = None
    for attempt in range(3):
        try:
            with open(file_path, "rb") as f:
                return f.read()
        except PermissionError as e:
            last_err = e
            _time.sleep(0.3 * (attempt + 1))
        except Exception:
            raise  # 非权限错误直接抛出不重试
    # 三次重试都权限失败，记录日志并返回 None
    print(f"[WARN] 通达信文件读写冲突（3次重试均失败）: {file_path} — {last_err}")
    return None


def parse_tdx_day(file_path):
    data = _read_tdx_day_file(file_path)
    if data is None:
        return None
    try:
        row_cnt = len(data) // 32
        rows = []
        for i in range(row_cnt):
            b = data[i*32 : (i+1)*32]
            date_code = struct.unpack("<I", b[0:4])[0]
            open_p  = struct.unpack("<I", b[4:8])[0] / 100
            high_p  = struct.unpack("<I", b[8:12])[0] / 100
            low_p   = struct.unpack("<I", b[12:16])[0] / 100
            close_p = struct.unpack("<I", b[16:20])[0] / 100
            vol     = struct.unpack("<I", b[24:28])[0]
            rows.append([str(date_code), open_p, high_p, low_p, close_p, vol])

        df = pd.DataFrame(rows, columns=["date","开盘","最高","最低","收盘","成交量"])
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)
        df["ma5"] = df["收盘"].rolling(5).mean()
        df["ma10"] = df["收盘"].rolling(10).mean()
        df["ma20"] = df["收盘"].rolling(20).mean()
        df["ma60"] = df["收盘"].rolling(60).mean()
        df["vol_ma10"] = df["成交量"].rolling(10).mean()
        df["vol_ma20"] = df["成交量"].rolling(20).mean()
        df["macd"], df["macd_signal"], df["macd_hist"] = calc_macd(df["收盘"])
        df["kdj_k"], df["kdj_d"] = calc_kdj(df["最高"], df["最低"], df["收盘"])
        df["rsi"] = calc_rsi(df["收盘"], 14)
        df["boll_mid"] = df["收盘"].rolling(20).mean()
        df["boll_std"] = df["收盘"].rolling(20).std()
        df["boll_up"] = df["boll_mid"] + 2 * df["boll_std"]
        df["boll_down"] = df["boll_mid"] - 2 * df["boll_std"]
        return df
    except Exception as e:
        print(f"[ERROR] 解析通达信日线文件失败 {file_path}: {e}")
        import traceback
        traceback.print_exc()
        return None

def calc_macd(close, fast=12, slow=26, signal=9):
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd = ema_fast - ema_slow
    signal_line = macd.ewm(span=signal, adjust=False).mean()
    hist = macd - signal_line
    return macd, signal_line, hist

def calc_kdj(high, low, close, n=9, m=3):
    lowest_low = low.rolling(n).min()
    highest_high = high.rolling(n).max()
    rsv = (close - lowest_low) / (highest_high - lowest_low) * 100
    rsv = rsv.fillna(50)
    k = rsv.ewm(com=m-1, adjust=False).mean()
    d = k.ewm(com=m-1, adjust=False).mean()
    return k, d

def calc_rsi(close, period=14):
    delta = close.diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)

def get_tdx_file(code):
    if code.startswith(("60","68")) or code == "999999":
        return os.path.join(TDX_ROOT, "vipdoc","sh","lday",f"sh{code}.day")
    else:
        return os.path.join(TDX_ROOT, "vipdoc","sz","lday",f"sz{code}.day")

def load_stock_tdx(code):
    fp = get_tdx_file(code)
    if not os.path.exists(fp):
        return None
    df = parse_tdx_day(fp)
    if df is not None:
        cut_date = datetime.now() - pd.Timedelta(days=365*BACKTEST_YEARS)
        df = df[df["date"] >= cut_date].reset_index(drop=True)
    return df

# ---------------- 多形态识别 ----------------
def detect_all_patterns(df):
    pat = {}
    if len(df) < 60:
        return [], pat
    last = df.iloc[-1]
    pre1 = df.iloc[-2]
    pre5 = df.iloc[-5]

    pat["多头排列"] = last["ma5"] > last["ma10"] > last["ma20"] > last["ma60"]
    pat["空头排列"] = last["ma5"] < last["ma10"] < last["ma20"] < last["ma60"]
    pat["站稳60日线"] = last["收盘"] > last["ma60"]
    pat["跌破60日线"] = last["收盘"] < last["ma60"]

    pat["5日线金叉10日线"] = pre1["ma5"] < pre1["ma10"] and last["ma5"] > last["ma10"]
    pat["5日线死叉10日线"] = pre1["ma5"] > pre1["ma10"] and last["ma5"] < last["ma10"]
    pat["5日线金叉20日线"] = pre1["ma5"] < pre1["ma20"] and last["ma5"] > last["ma20"]
    pat["5日线死叉20日线"] = pre1["ma5"] > pre1["ma20"] and last["ma5"] < last["ma20"]

    pat["金针探底"] = (last["最低"] < last["开盘"]) and (last["收盘"] > last["最低"]) and ((last["开盘"] - last["最低"]) > abs(last["收盘"] - last["开盘"])*2)
    pat["阳包阴"] = (last["收盘"] > last["开盘"]) and (pre1["收盘"] < pre1["开盘"]) and (last["开盘"] < pre1["收盘"]) and (last["收盘"] > pre1["开盘"])
    pat["放量长阳"] = (last["收盘"] > last["开盘"]) and (last["成交量"] > last["vol_ma20"] * 1.5)
    pat["缩量回调"] = (last["收盘"] < pre1["收盘"]) and (last["成交量"] < last["vol_ma10"] * 0.7)

    recent_high = df["最高"].iloc[-20:].max()
    recent_low = df["最低"].iloc[-20:].min()
    pat["放量突破压力"] = (last["收盘"] >= recent_high) and (last["成交量"] > last["vol_ma20"] * 1.6)
    pat["缩量回踩支撑"] = (last["收盘"] <= recent_low*1.02) and (last["成交量"] < last["vol_ma10"] * 0.65)

    pat["量价顶背离"] = (last["收盘"] > pre5["收盘"]) and (last["成交量"] < pre5["vol_ma20"])
    pat["量价底背离"] = (last["收盘"] < pre5["收盘"]) and (last["成交量"] > pre5["vol_ma20"])

    pat["布林上轨压力"] = last["最高"] >= last["boll_up"] and last["收盘"] < last["boll_up"]
    pat["布林下轨支撑"] = last["最低"] <= last["boll_down"] and last["收盘"] > last["boll_down"]

    # ── 通用失效检测（检查过去N天内触发的形态是否已被破坏） ──
    _check_failure_patterns(df, pat)

    # ── 标准老鸭头形态检测（含失效判断） ──
    duck = _detect_duck_head(df)
    if duck["valid"]:
        pat["老鸭头形态"] = True
    elif duck["status"] == "成形失效":
        pat["老鸭头失效"] = True
    elif duck["status"] == "成形未突破":
        pat["老鸭头形态"] = True

    hit_patterns = [k for k, v in pat.items() if v]
    return hit_patterns, pat


def _check_failure_patterns(df, pat):
    """检测各种技术形态是否失效（过去几天触发的形态，现在条件已破坏）。"""
    if len(df) < 25:
        return

    last = df.iloc[-1]
    recent_20 = df.tail(20)
    recent_high = recent_20["最高"].max()
    recent_low = recent_20["最低"].min()

    # 1. 假突破: 过去6天内曾接近20日高(98%以内)，现在大幅回落(>5%)
    for i in range(1, min(7, len(df))):
        row = df.iloc[-(i+1)]
        if row["收盘"] >= recent_high * 0.98:
            drop = (recent_high - last["收盘"]) / recent_high * 100
            if drop > 5:
                pat["假突破"] = True
                break

    # 2. 破位: 放量跌破20日最低
    if last["收盘"] < recent_low and last["成交量"] > last["vol_ma20"] * 1.2:
        pat["破位"] = True

    # 3. 金针探底失效: 过去7天内有金针探底，现在收盘跌破针底
    for i in range(1, min(7, len(df) - 1)):
        row = df.iloc[-(i+1)]
        if (row["最低"] < row["开盘"] and row["收盘"] > row["最低"] and
            (row["开盘"] - row["最低"]) > abs(row["收盘"] - row["开盘"]) * 2):
            if last["收盘"] < row["最低"]:
                pat["金针探底失效"] = True
                break

    # 4. 阳包阴失效: 过去5天内有阳包阴，现在收盘跌破该K线最低
    for i in range(1, min(5, len(df) - 2)):
        row = df.iloc[-(i+1)]
        row_prev = df.iloc[-(i+2)]
        if row["收盘"] > row["开盘"] and row["开盘"] < row_prev["收盘"] and row["收盘"] > row_prev["开盘"]:
            if last["收盘"] < row["最低"]:
                pat["阳包阴失效"] = True
                break

    # 5. 放量长阳失效: 过去5天内有放量长阳，现在收盘跌回中点以下
    for i in range(1, min(5, len(df) - 1)):
        row = df.iloc[-(i+1)]
        if row["收盘"] > row["开盘"] and row["成交量"] > row["vol_ma20"] * 1.5:
            mid = (row["收盘"] + row["开盘"]) / 2
            if last["收盘"] < mid:
                pat["放量长阳失效"] = True
                break

    # 6. 均线金叉失效: 过去7天内有金叉，现在已死叉或即将死叉
    for i in range(1, min(7, len(df) - 2)):
        row_pre = df.iloc[-(i+2)]
        row_now = df.iloc[-(i+1)]
        if row_pre["ma5"] < row_pre["ma10"] and row_now["ma5"] > row_now["ma10"]:
            if last["ma5"] < last["ma10"]:
                pat["均线金叉失效"] = True
                break
            gap = (last["ma5"] - last["ma10"]) / last["ma10"]
            prev_gap = (df.iloc[-2]["ma5"] - df.iloc[-2]["ma10"]) / df.iloc[-2]["ma10"]
            if gap < 0.003 and prev_gap > gap:
                pat["均线金叉失效"] = True
                break


def _detect_duck_head(df):
    """标准老鸭头形态检测 + 失效判断。

    正宗老鸭头四阶段：
      鸭头：放量急涨形成高点
      鸭嘴：缩量回踩但不破60日线（核心条件！）
      鸭鼻孔：窄幅横盘整理（振幅<3%），成交量极度萎缩
      张嘴：放量突破鸭头高点

    失效条件（满足任一即失效）：
      - 鸭嘴回踩跌破60日线
      - MA60 走平或拐头向下
      - 横盘超过30天仍未突破
      - 突破后回落到鸭头高点下方
    """
    recent = df.tail(60).copy()
    result = {
        "valid": False,
        "head_idx": None, "head_price": None,
        "mouth_idx": None, "mouth_price": None,
        "nose_idx": None, "nose_price": None,
        "open_idx": None, "open_price": None,
        "status": "未成形",
        "fail_reasons": [],
    }

    if len(recent) < 40:
        return result

    head_idx = recent["最高"].idxmax()
    head_price = recent.loc[head_idx, "最高"]
    if recent.index.get_loc(head_idx) > len(recent) // 2 + 5:
        return result

    post_head = recent.loc[head_idx:]
    if len(post_head) < 5:
        return result
    mouth_idx = post_head["最低"].idxmin()
    mouth_price = post_head.loc[mouth_idx, "最低"]
    mouth_ma60 = post_head.loc[mouth_idx, "ma60"]
    result["head_idx"] = head_idx
    result["head_price"] = head_price
    result["mouth_idx"] = mouth_idx
    result["mouth_price"] = mouth_price

    if pd.notna(mouth_ma60) and mouth_price < mouth_ma60:
        result["status"] = "成形失效"
        result["fail_reasons"].append(f"鸭嘴回踩 {mouth_price:.2f} 跌破60日线 {mouth_ma60:.2f}")
        return result

    ma60_now = recent["ma60"].iloc[-1]
    ma60_10ago = recent["ma60"].iloc[-10] if len(recent) >= 10 else recent["ma60"].iloc[0]
    if pd.isna(ma60_now) or pd.isna(ma60_10ago):
        return result
    if ma60_now <= ma60_10ago:
        result["status"] = "成形失效"
        result["fail_reasons"].append(f"MA60 走平或向下（当前 {ma60_now:.2f} ≤ 10日前 {ma60_10ago:.2f}）")
        return result

    post_mouth = recent.loc[mouth_idx:]
    narrow_mask = (post_mouth["最高"] - post_mouth["最低"]) / post_mouth["收盘"] < 0.03
    if narrow_mask.any():
        nose_idx = post_mouth[narrow_mask].index[0]
        nose_price = recent.loc[nose_idx, "收盘"]
    else:
        nose_idx = mouth_idx
        nose_price = mouth_price
    result["nose_idx"] = nose_idx
    result["nose_price"] = nose_price

    post_nose = recent.loc[nose_idx:]
    open_idx = None
    for idx, row in post_nose.iterrows():
        if row["收盘"] > head_price * 0.98:
            open_idx = idx
            break
    if open_idx is None:
        # 成形但久未突破 → 过期失效，不报告
        if recent.index.get_loc(mouth_idx) > len(recent) - 13:
            result["status"] = "成形未突破"
        return result

    open_price = recent.loc[open_idx, "收盘"]
    result["open_idx"] = open_idx
    result["open_price"] = open_price

    post_open = recent.loc[open_idx:]
    # 仅检查突破后5天内的回落（超过5天说明突破有效，不必追认失效）
    post_5d = post_open.head(5)
    if len(post_5d) >= 3:
        min_close_5d = post_5d["收盘"].min()
        if min_close_5d < head_price * 0.95:
            result["status"] = "成形失效"
            result["fail_reasons"].append(f"突破鸭头 {head_price:.2f} 后5日内回落至 {min_close_5d:.2f}，假突破")
            return result

    result["valid"] = True
    result["status"] = "成形有效"
    return result


def _precompute_signals(df):
    """预计算所有技术形态信号为布尔列（一次计算，供回测循环O(1)查询）。"""
    s = pd.DataFrame(index=df.index)

    # 均线排列
    s["多头排列"] = (df["ma5"] > df["ma10"]) & (df["ma10"] > df["ma20"]) & (df["ma20"] > df["ma60"])
    s["空头排列"] = (df["ma5"] < df["ma10"]) & (df["ma10"] < df["ma20"]) & (df["ma20"] < df["ma60"])
    s["站稳60日线"] = df["收盘"] > df["ma60"]
    s["跌破60日线"] = df["收盘"] < df["ma60"]

    # 均线交叉
    s["5日线金叉10日线"] = (df["ma5"].shift(1) < df["ma10"].shift(1)) & (df["ma5"] > df["ma10"])
    s["5日线死叉10日线"] = (df["ma5"].shift(1) > df["ma10"].shift(1)) & (df["ma5"] < df["ma10"])
    s["5日线金叉20日线"] = (df["ma5"].shift(1) < df["ma20"].shift(1)) & (df["ma5"] > df["ma20"])
    s["5日线死叉20日线"] = (df["ma5"].shift(1) > df["ma20"].shift(1)) & (df["ma5"] < df["ma20"])

    # K线形态
    s["金针探底"] = (df["最低"] < df["开盘"]) & (df["收盘"] > df["最低"]) & ((df["开盘"] - df["最低"]) > (df["收盘"] - df["开盘"]).abs() * 2)
    s["阳包阴"] = (df["收盘"] > df["开盘"]) & (df["开盘"] < df["收盘"].shift(1)) & (df["收盘"] > df["开盘"].shift(1))
    s["放量长阳"] = (df["收盘"] > df["开盘"]) & (df["成交量"] > df["vol_ma20"] * 1.5)
    s["缩量回调"] = (df["收盘"] < df["收盘"].shift(1)) & (df["成交量"] < df["vol_ma10"] * 0.7)

    # 放量突破/缩量回踩（20日高/低，rolling）
    rh = df["最高"].rolling(20, min_periods=20).max()
    rl = df["最低"].rolling(20, min_periods=20).min()
    s["放量突破压力"] = (df["收盘"] >= rh) & (df["成交量"] > df["vol_ma20"] * 1.6)
    s["缩量回踩支撑"] = (df["收盘"] <= rl * 1.02) & (df["成交量"] < df["vol_ma10"] * 0.65)

    # 量价背离（对比5日前）
    s["量价顶背离"] = (df["收盘"] > df["收盘"].shift(4)) & (df["成交量"] < df["vol_ma20"].shift(4))
    s["量价底背离"] = (df["收盘"] < df["收盘"].shift(4)) & (df["成交量"] > df["vol_ma20"].shift(4))

    # 布林带
    s["布林上轨压力"] = (df["最高"] >= df["boll_up"]) & (df["收盘"] < df["boll_up"])
    s["布林下轨支撑"] = (df["最低"] <= df["boll_down"]) & (df["收盘"] > df["boll_down"])

    # 老鸭头形态（简化版MA5判定，供回测使用，与 detect_all_patterns 原始算法一致）
    ma5_peak = df["ma5"].rolling(20, min_periods=20).max().shift(10)  # max of ma5[-30:-10)
    ma5_trough = df["ma5"].rolling(10, min_periods=10).min()           # min of ma5[-10:]
    s["老鸭头形态"] = (ma5_peak > df["ma5"].shift(19)) & (ma5_trough < df["ma5"].shift(9)) & \
                     (df["ma5"] > df["ma10"]) & (df["ma60"] > df["ma60"].shift(9))
    return s


# ==================== V15 择时雷达全链路计算 ====================

def compute_v15_pipeline(df, code=None, name=None, sector_map=None):
    """
    对单只股票运行V15全链路（因子→场景→过滤→仓位建议），返回结构化结果。

    参数:
        df: 通达信日线DataFrame
        code: 股票代码（用于板块共振过滤）
        name: 股票名称（用于板块共振过滤）
        sector_map: 板块热度图（由 get_sector_map() 获取）

    返回:
        {
            "available": bool,           # V15是否可用
            "v15_signal": dict,           # V15基础评分
            "scenario_signal": dict,      # 场景引擎评分
            "filtered_signal": dict,      # 过滤引擎评分
            "position_advice": dict,      # 仓位建议
            "scenario": str,              # 当前场景
            "score": float,               # 过滤后评分（主评分）
            "level": str,                 # 等级（强势/偏强/中性/弱势）
            "sector": dict,               # 板块信息
        }
    """
    if not V15_AVAILABLE:
        return {"available": False}

    try:
        # 统一列名
        col_close = '收盘' if '收盘' in df.columns else 'close'
        col_high = '最高' if '最高' in df.columns else 'high'
        col_low = '最低' if '最低' in df.columns else 'low'
        col_vol = '成交量' if '成交量' in df.columns else 'vol'

        closes = [float(v) for v in df[col_close].values]
        highs = [float(v) for v in df[col_high].values]
        lows = [float(v) for v in df[col_low].values]
        volumes = [float(v) for v in df[col_vol].values]
        last_close = closes[-1]
        last_volume = volumes[-1]

        # 1. V15基础评分
        v15 = compute_v15_signal(closes, highs, lows, volumes, last_close, last_volume)

        # 2. 场景引擎评分
        scenario = compute_scenario_signal(closes, highs, lows, volumes, last_close, last_volume)

        # 3. 过滤引擎评分（V15全链路最终输出，含板块过滤）
        filtered = compute_filtered_signal(closes, highs, lows, volumes, last_close, last_volume,
                                           code=code, name=name, sector_map=sector_map)

        # 4. 仓位建议（未入场视角）
        position = compute_position_advice_from_signal(filtered, last_close)

        # 5. 板块详情（用于展示）
        sector_info = {}
        if code and name and sector_map:
            try:
                from v15_sector_fetcher import get_sector_detail
                sector_info = get_sector_detail(code, name, sector_map)
            except Exception:
                pass

        return {
            "available": True,
            "v15_signal": v15,
            "scenario_signal": scenario,
            "filtered_signal": filtered,
            "position_advice": position,
            "scenario": filtered.get("scenario", "无场景"),
            "score": filtered.get("score", 50.0),
            "level": filtered.get("level", "中性"),
            "sector": sector_info,
        }
    except Exception as e:
        print(f"[V15] 计算异常: {e}")
        import traceback
        traceback.print_exc()
        return {"available": False}


def get_all_pattern_win_rate(df, hit_patterns):
    """预计算+一次遍历：回测扫描计算所有命中形态的胜率。

    先用 _precompute_signals 把所有需要的形态信号算好（一次 pandas 向量化操作），
    然后在历史遍历循环中直接查布尔列（O(1)），不再调用 detect_all_patterns。

    原版 2000次×N形态: N×2000次 detect_all_patterns 调用
    新版 2000次: 预计算向量化列 + 2000次布尔列查询
    """
    if not hit_patterns:
        return {}
    skip_patterns = {"假突破", "破位", "金针探底失效", "阳包阴失效",
                     "放量长阳失效", "均线金叉失效", "老鸭头失效"}
    active = [p for p in hit_patterns if p not in skip_patterns]
    if not active:
        return {p: {"胜率(10日)": 0.0, "历史触发次数": 0} for p in skip_patterns if p in hit_patterns}

    forward = FORWARD_1
    stats = {p: {"触发": 0, "胜": 0} for p in active}

    # 一次性预计算所有需要的信号列
    all_signals = _precompute_signals(df)

    for i in range(60, len(df) - forward):
        future_ret = (df.iloc[i + forward]["收盘"] - df.iloc[i]["收盘"]) / df.iloc[i]["收盘"]
        win = future_ret > 0.03
        for p in active:
            if all_signals[p].iloc[i]:
                stats[p]["触发"] += 1
                if win:
                    stats[p]["胜"] += 1

    res = {}
    for p in active:
        t = stats[p]["触发"]
        w = stats[p]["胜"]
        res[p] = {"胜率(10日)": round(w / t * 100, 2) if t else 0.0, "历史触发次数": t}
    for p in skip_patterns:
        if p in hit_patterns:
            res[p] = {"胜率(10日)": 0.0, "历史触发次数": 0}
    return res

def safe_num(value, digits=2):
    try:
        if pd.isna(value):
            return None
        if isinstance(value, (np.integer, int)):
            return int(value)
        return round(float(value), digits)
    except:
        return None

def safe_pct(current, base, digits=2):
    try:
        if pd.isna(current) or pd.isna(base) or float(base) == 0:
            return None
        return round((float(current) - float(base)) / float(base) * 100, digits)
    except:
        return None

def classify_volume(vol_5_avg, vol_20_avg):
    if not vol_5_avg or not vol_20_avg:
        return "数据不足"
    ratio = vol_5_avg / vol_20_avg
    if ratio >= 1.2:
        return "近5日明显放量"
    if ratio <= 0.8:
        return "近5日明显缩量"
    return "近5日量能接近20日均量"

def classify_ma_status(last):
    if any(pd.isna(last.get(k, np.nan)) for k in ("ma5", "ma10", "ma20", "ma60")):
        return "均线数据不足"
    if last["ma5"] > last["ma10"] > last["ma20"] > last["ma60"]:
        return "多头排列"
    if last["ma5"] < last["ma10"] < last["ma20"] < last["ma60"]:
        return "空头排列"
    if last["ma5"] > last["ma10"] and last["收盘"] > last["ma20"]:
        return "短线偏强但均线未完全多头"
    if last["ma5"] < last["ma10"] and last["收盘"] < last["ma20"]:
        return "短线偏弱"
    return "均线缠绕或震荡"

def build_technical_snapshot(df, hit_patterns, rate_info, score, tag, price_info):
    last = df.iloc[-1]
    recent_20 = df.tail(20)
    recent_60 = df.tail(60)
    vol_5_avg = df["成交量"].tail(5).mean()
    vol_20_avg = df["成交量"].tail(20).mean()
    high_20 = recent_20["最高"].max()
    low_20 = recent_20["最低"].min()
    high_60 = recent_60["最高"].max()
    low_60 = recent_60["最低"].min()

    pattern_backtest = {}
    for p, info in rate_info.items():
        pattern_backtest[p] = {
            "10日胜率_pct": safe_num(info.get("胜率(10日)")),
            "历史触发次数": info.get("历史触发次数", 0)
        }

    recent_klines = []
    for _, row in df.tail(12).iterrows():
        recent_klines.append({
            "date": row["date"].strftime("%Y-%m-%d"),
            "open": safe_num(row["开盘"]),
            "high": safe_num(row["最高"]),
            "low": safe_num(row["最低"]),
            "close": safe_num(row["收盘"]),
            "volume": safe_num(row["成交量"], 0)
        })

    return {
        "latest_date": last["date"].strftime("%Y-%m-%d"),
        "latest_ohlcv": {
            "open": safe_num(last["开盘"]),
            "high": safe_num(last["最高"]),
            "low": safe_num(last["最低"]),
            "close": safe_num(last["收盘"]),
            "volume": safe_num(last["成交量"], 0)
        },
        "moving_average": {
            "ma5": safe_num(last["ma5"]),
            "ma10": safe_num(last["ma10"]),
            "ma20": safe_num(last["ma20"]),
            "ma60": safe_num(last["ma60"]),
            "ma_status": classify_ma_status(last),
            "ma60_slope_20d_pct": safe_pct(last["ma60"], df["ma60"].iloc[-20]) if len(df) >= 80 else None,
            "distance_to_ma60_pct": safe_pct(last["收盘"], last["ma60"])
        },
        "return_and_position": {
            "return_5d_pct": safe_pct(last["收盘"], df["收盘"].iloc[-6]) if len(df) >= 6 else None,
            "return_10d_pct": safe_pct(last["收盘"], df["收盘"].iloc[-11]) if len(df) >= 11 else None,
            "return_20d_pct": safe_pct(last["收盘"], df["收盘"].iloc[-21]) if len(df) >= 21 else None,
            "20d_high": safe_num(high_20),
            "20d_low": safe_num(low_20),
            "60d_high": safe_num(high_60),
            "60d_low": safe_num(low_60),
            "distance_to_20d_high_pct": safe_pct(last["收盘"], high_20),
            "distance_to_20d_low_pct": safe_pct(last["收盘"], low_20)
        },
        "volume": {
            "vol_5d_avg": safe_num(vol_5_avg, 0),
            "vol_20d_avg": safe_num(vol_20_avg, 0),
            "vol_5_to_20_ratio": safe_num(vol_5_avg / vol_20_avg if vol_20_avg else None),
            "volume_status": classify_volume(vol_5_avg, vol_20_avg)
        },
        "indicators": {
            "macd": safe_num(last["macd"]),
            "macd_signal": safe_num(last["macd_signal"]),
            "macd_hist": safe_num(last["macd_hist"]),
            "kdj_k": safe_num(last["kdj_k"]),
            "kdj_d": safe_num(last["kdj_d"]),
            "rsi14": safe_num(last["rsi"]),
            "boll_up": safe_num(last["boll_up"]),
            "boll_mid": safe_num(last["boll_mid"]),
            "boll_down": safe_num(last["boll_down"])
        },
        "patterns": {
            "triggered": hit_patterns,
            "backtest": pattern_backtest,
            "score": score,
            "score_tag": tag
        },
        "support_resistance": price_info,
        "recent_klines": recent_klines,
        "missing_data": ["公告", "财报", "资金流", "北向资金", "龙虎榜", "行业板块强弱", "实时新闻"],
        "analysis_boundary": "以上仅包含通达信本地日线技术数据，不能据此判断公告、资金、行业或基本面。"
    }

def get_shape_matches(hit_patterns):
    return [p for p in hit_patterns if p in TECHNICAL_SHAPE_PATTERNS]

def build_shape_summary(hit_patterns):
    shape_matches = get_shape_matches(hit_patterns)
    if not shape_matches:
        return "未匹配到技术形态库中的明确形态；结论应写“不具备任何技术形态”，仅保留趋势、均线、支撑压力等背景数据。"
    return "已匹配技术形态：" + "、".join(shape_matches)

def add_point(points, tag, row, price_col, desc):
    if row is None:
        return
    points.append((
        tag,
        row["date"].strftime("%Y-%m-%d"),
        safe_num(row[price_col]),
        desc
    ))

def build_old_duck_points(recent):
    """构建老鸭头标注点（使用 _detect_duck_head 的标准结果保持一致）。"""
    if recent.empty:
        return []
    duck = _detect_duck_head(recent)
    points = []
    if duck["head_idx"] is not None:
        head_row = recent.loc[duck["head_idx"]]
        head_desc = "老鸭头形态候选高点"
        st = duck.get("status", "")
        if st and st != "未成形":
            head_desc += f" | {st}"
        add_point(points, "鸭头", head_row, "最高", head_desc)
    if duck["mouth_idx"] is not None:
        mouth_row = recent.loc[duck["mouth_idx"]]
        mouth_desc = "高点后回踩低点"
        if duck.get("fail_reasons"):
            mouth_desc += " | 注意：" + "；".join(duck["fail_reasons"])
        add_point(points, "鸭嘴", mouth_row, "最低", mouth_desc)
    if duck["nose_idx"] is not None:
        nose_row = recent.loc[duck["nose_idx"]]
        add_point(points, "鸭鼻孔", nose_row, "收盘", "回踩后的窄幅整理点")
    if duck["open_idx"] is not None:
        open_row = recent.loc[duck["open_idx"]]
        add_point(points, "张嘴", open_row, "收盘", "突破鸭头高点观察点")
    return points

def build_point_candidates(df, hit_patterns=None):
    recent = df.tail(60).copy()
    if recent.empty:
        return []

    hit_patterns = hit_patterns or []
    shape_matches = get_shape_matches(hit_patterns)
    points = []
    last = recent.iloc[-1]

    if "老鸭头形态" in shape_matches:
        points.extend(build_old_duck_points(recent))
    if "金针探底" in shape_matches:
        add_point(points, "金针探底", last, "最低", "最新K线下影线探底观察点")
    if "阳包阴" in shape_matches:
        add_point(points, "阳包阴", last, "收盘", "最新K线阳包阴确认点")
    if "放量长阳" in shape_matches:
        add_point(points, "放量长阳", last, "收盘", "最新K线放量长阳收盘点")
    if "缩量回调" in shape_matches:
        add_point(points, "缩量回调", last, "收盘", "最新K线缩量回调点")
    if "放量突破压力" in shape_matches:
        add_point(points, "突破压力", last, "收盘", "放量突破近20日压力观察点")
    if "缩量回踩支撑" in shape_matches:
        add_point(points, "回踩支撑", last, "最低", "缩量回踩近20日支撑观察点")
    if "量价顶背离" in shape_matches:
        add_point(points, "顶背离", last, "收盘", "价格上行但量能未同步的观察点")
    if "量价底背离" in shape_matches:
        add_point(points, "底背离", last, "收盘", "价格走低但量能变化异常的观察点")
    if "布林上轨压力" in shape_matches:
        add_point(points, "布林上轨压力", last, "最高", "触及布林上轨后的压力观察点")
    if "布林下轨支撑" in shape_matches:
        add_point(points, "布林下轨支撑", last, "最低", "触及布林下轨后的支撑观察点")
    if "5日线金叉10日线" in shape_matches or "5日线金叉20日线" in shape_matches:
        add_point(points, "均线金叉", last, "收盘", "短均线金叉观察点")
    if "5日线死叉10日线" in shape_matches or "5日线死叉20日线" in shape_matches:
        add_point(points, "均线死叉", last, "收盘", "短均线死叉风险点")

    deduped = []
    seen = set()
    for point in points:
        key = (point[0], point[1], point[2])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(point)
    return deduped

def point_candidates_to_marks(candidates):
    if not CHART_AVAILABLE:
        return {}
    marks = {}
    for tag, date_str, price, desc in candidates:
        try:
            x_pos = mdates.date2num(pd.to_datetime(date_str))
            marks[tag] = (x_pos, float(price), f"{date_str} {price}元 | {desc}")
        except:
            continue
    return marks

# ==================== 评分逻辑（已降级为纯显示，不参与决策）====================
# V15全链路（因子→场景→过滤）为唯一择时决策依据
# 此函数仅用于批量扫描中的形态评分展示，不影响任何决策
def calc_composite_score(hit_patterns, rate_dict):
    bull_patterns = {
        "多头排列", "站稳60日线", "5日线金叉10日线", "5日线金叉20日线",
        "金针探底", "阳包阴", "放量长阳", "放量突破压力", "缩量回踩支撑",
        "量价底背离", "布林下轨支撑", "老鸭头形态"
    }
    bear_patterns = {
        "空头排列", "跌破60日线", "5日线死叉10日线", "5日线死叉20日线",
        "缩量回调", "量价顶背离", "布林上轨压力",
    }

    score = 50
    for p in hit_patterns:
        if p in bull_patterns:
            score += 8
        if p in bear_patterns:
            score -= 8

    for p, info in rate_dict.items():
        wr = info["胜率(10日)"]
        if p in bull_patterns:
            if wr >= 60: score += 3
            elif wr <= 40: score -=3
        if p in bear_patterns:
            if wr >=60: score -=3
            elif wr <=40: score +=3

    score = max(0, min(100, round(score, 1)))

    if score >=70: tag = "强势偏多"
    elif score >=55: tag = "温和偏多"
    elif score >=45: tag = "中性震荡"
    elif score >=30: tag = "温和偏空"
    else: tag = "强势偏空"
    return score, tag

# ==================================================================

def call_deepseek_api(prompt, api_key, timeout=30):
    api_key = (api_key or "").strip()
    if requests is None:
        return "提示：当前Python环境未安装requests，AI请求不可用；基础技术分析仍可使用"
    if not api_key or api_key.startswith("在此填写"):
        return "提示：未填写对应DeepSeek API密钥"
    url = "https://api.deepseek.com/v1/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"model": "deepseek-chat", "messages": [{"role":"user","content":prompt}], "temperature":AI_TEMPERATURE}
    try:
        res = requests.post(url, headers=headers, json=payload, timeout=timeout)
        res.raise_for_status()
        return res.json()["choices"][0]["message"]["content"]
    except requests.exceptions.RequestException as e:
        return f"API请求失败：{e}"
    except (KeyError, IndexError, ValueError) as e:
        return f"API返回格式异常：{e}"

def get_ai_key_status_text():
    rows = [
        ("量价解读Key", DEEPSEEK_KEY1, DEEPSEEK_KEY1_SOURCE),
        ("形态标注Key", DEEPSEEK_KEY2, DEEPSEEK_KEY2_SOURCE),
        ("备用Key", DEEPSEEK_KEY3, DEEPSEEK_KEY3_SOURCE),
    ]
    lines = ["===== AI Key 本地读取状态 ====="]
    for label, key, source in rows:
        status = "已配置" if key else "未配置"
        lines.append(f"{label}：{status} | {mask_key(key)}")
        lines.append(f"来源：{source}")
    lines.append("")
    lines.append("读取顺序：环境变量 DEEPSEEK_KEY1/2/3 或 DEEPSEEK_KEY，然后读取程序同目录 deepseek_key1.txt/deepseek_key2.txt/deepseek_key3.txt/deepseek_key.txt。")
    lines.append("本地txt文件可只放一行新Key；空行和#开头的注释会被忽略。")
    return "\n".join(lines)

def audit_ai_output(text):
    if not text or text.startswith(("提示：", "API请求失败", "API返回格式异常")):
        return text

    suspicious = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        hit_terms = [term for term in UNSUPPORTED_AI_TERMS if term in line]
        if not hit_terms:
            continue
        has_uncertain_marker = any(marker in line for marker in UNCERTAIN_MARKERS)
        if not has_uncertain_marker:
            suspicious.append((line, hit_terms))

    if not suspicious:
        return text + "\n\n【AI边界检查】通过：未发现明显使用缺失数据编造的表述。"

    lines = [
        "",
        "【AI边界检查】警告：以下表述可能超出本地技术数据范围，请人工核对："
    ]
    for line, hit_terms in suspicious[:8]:
        lines.append(f"- 命中 {','.join(hit_terms)}：{line}")
    lines.append("处理建议：以上内容若没有出现在AI输入事实快照中，应视为低可信或删除。")
    return text + "\n" + "\n".join(lines)

def ai_signal_verification(code, name, cache):
    """信号验证引擎：四问合并，一次 DeepSeek 调用。
    参数 cache 来自 self.stock_cache[code]，包含 v15/df 等。"""
    df = cache["df"]
    v15_info = cache.get("v15", {})
    hit, rate, score_v1, tag_v1, price, _name = cache["info"]

    # 1. V15 评分路径
    v15_context = ""
    v15_score_val = "N/A"
    v15_level_val = "N/A"
    if v15_info.get("available"):
        fs = v15_info.get("filtered_signal", {})
        facts_list = fs.get("facts", [])
        v15_context = "\n".join(facts_list[-15:])
        v15_score_val = str(fs.get("score", "N/A"))
        v15_level_val = fs.get("level", "N/A")

    # 2. 板块动力
    sector_context = ""
    try:
        from _plus_.sector_power import compute_sector_power
        sp = compute_sector_power(code, name)
        if sp.get("sector_name") != "通用":
            sector_context = (
                f"板块: {sp['sector_name']} | "
                f"板块动力: {sp['sector_power']:.0f}分({sp['tag']}) | "
                f"T1资金占比: {sp.get('t1_score',50):.0f}分 | "
                f"T2量能: {sp['fund_score']:.0f}分({sp.get('fund_label','')}) | "
                f"位置: {sp['position_signal']}({sp['position_pct']:.0f}%分位)"
            )
    except Exception:
        pass

    # 3. 量价与形态
    atr_note = ""
    try:
        vol_v = v15_info.get("filtered_signal", {}).get("volatility", {})
        atr_val = vol_v.get("atr_norm", 0)
        if atr_val > 4.0:
            atr_note = f"ATR={atr_val:.1f}%(高波动)"
        elif atr_val > 3.0:
            atr_note = f"ATR={atr_val:.1f}%(中高波动)"
    except Exception:
        pass

    shape_summary = build_shape_summary(hit)
    candidates = build_point_candidates(df, hit)
    if candidates:
        candidate_text = "\n".join([f"- {tag}：{date}，价格{pv}元，{desc}" for tag, date, pv, desc in candidates])
    else:
        candidate_text = "无形态点位候选"

    # 最近10日 OHLCV 简表（仅供AI参考，不输出到用户界面）
    klines_text = ""
    try:
        recent = df.tail(10)
        klines_text = "最近10个交易日 K线（日期 O H L C 成交量）：\n"
        for _, row in recent.iterrows():
            klines_text += (
                f"  {row['date'].strftime('%m-%d')} "
                f"O:{row['开盘']:.2f} H:{row['最高']:.2f} "
                f"L:{row['最低']:.2f} C:{row['收盘']:.2f} "
                f"V:{int(row['成交量'])}\n"
            )
    except Exception:
        pass

    prompt = f"""
A股个股：{code} {name}

【最近K线数据】
{klines_text}
【V15评分路径】
{v15_context}

【板块环境】
{sector_context}
{atr_note}

【技术形态匹配】
{shape_summary}

【候选标注点】
{candidate_text}

【核心规则】
0. 价格和涨跌必须基于【最近K线数据】！禁止使用不在表格中的价格。量价关系的涨跌方向以O和C的比较为准。
1. 只能基于以上数据回答，不得编造公告、资金流、北向、龙虎榜、行业新闻
2. 每段以结论开头，再列依据，避免数据罗列
3. 用"如果...则..."表达条件判断，不做确定性预测
4. 不输出买卖建议，不承诺收益
5. 禁止提及技术形态匹配结果中未列出的任何形态名称

【四个必答问题】
### 1. V15信号可信度
- 当前评分{v15_score_val}分/等级{v15_level_val}是否被技术面支撑？
- 有哪些扣分项是合理的？哪些可能被误判？

### 2. 量价与位置
- 最近5天量价是否健康（放量涨/放量跌/缩量）？
- 当前价格处在什么位置？回踩是否有承接？

### 3. 板块判断
- 该股票所在板块的资金流向是否支持当前信号？

### 4. 明日确认条件
- 给出2-3个明天需要观察的条件（价格、量能、均线方向）
- 满足则信号成立，不满足则信号作废
"""

    result = call_deepseek_api(prompt, DEEPSEEK_KEY1)
    return audit_ai_output(result)


def ai_vol_analysis(code, name, hit_patterns, rate_info, score, tag, price_info, df):
    snapshot = build_technical_snapshot(df, hit_patterns, rate_info, score, tag, price_info)
    snapshot_text = json.dumps(snapshot, ensure_ascii=False, indent=2)
    shape_summary = build_shape_summary(hit_patterns)
    prompt = f"""
个股：{code} {name}

【可用事实数据】
{snapshot_text}

【技术形态匹配结果】
{shape_summary}

【硬性规则】
1. 只能基于“可用事实数据”分析，不得补充未提供的公告、财报、资金流、北向、龙虎榜、行业新闻。
2. 不允许使用“主力资金”“庄家”“利好刺激”“资金抢筹”等无数据支撑的表述。
3. 每条判断必须写清楚依据字段，例如 MA60、20日高低点、5日/20日均量、RSI、MACD、触发形态。
4. 缺失数据必须明确写“暂无数据，无法判断”，不能用猜测替代。
5. 先按“技术形态匹配结果”展开；如果没有匹配到明确技术形态，必须写“不具备任何技术形态”，不能强行套老鸭头或其他形态。
6. 不给确定性买卖建议，不承诺收益，不使用“必涨、必跌、主升浪启动”等绝对化表达。

只输出【第一部分：本地技术面解读】
1. 技术结论等级：观察 / 偏强观察 / 中性震荡 / 风险回避，四选一
2. 量价与均线依据：列出3-5条，每条必须引用数据字段
3. 关键支撑与压力：只使用 support_resistance 和 20日/60日高低点
4. 不可判断项：列出缺失的数据维度
5. 触发条件与失效条件：用“如果...则...”表达，避免确定性预测
"""
    return audit_ai_output(call_deepseek_api(prompt, DEEPSEEK_KEY1))

# ==================== AI形态点位说明：只解释本地识别出的候选点 ====================
def ai_shape_note(code, name, hit_patterns, price_info, df):
    snapshot = build_technical_snapshot(df, hit_patterns, {}, None, None, price_info)
    snapshot_text = json.dumps(snapshot, ensure_ascii=False, indent=2)
    shape_summary = build_shape_summary(hit_patterns)
    candidates = build_point_candidates(df, hit_patterns)
    if candidates:
        candidate_text = "\n".join([f"- {tag}：{date}，价格{price}元，{desc}" for tag, date, price, desc in candidates])
    else:
        candidate_text = "无形态点位候选；截图仅标注支撑位和压力位。"
    prompt = f"""
个股：{code} {name}

【可用事实数据】
{snapshot_text}

【技术形态匹配结果】
{shape_summary}

【候选标注点，均由本地K线和已匹配形态计算得出】
{candidate_text}

【硬性规则】
1. 只能使用候选标注点中已经给出的日期、价格、标签和说明，不得编造新日期、新价格或新点位。
2. 先匹配技术库形态：匹配到什么形态就解释什么形态；没有匹配到明确形态时，直接写“不具备任何技术形态”。
3. 如果“老鸭头形态”未出现在技术形态匹配结果中，禁止写鸭头、鸭嘴、鸭鼻孔、张嘴。
4. 候选点有几个就写几个；没有候选点时，不写点位清单，只写支撑位和压力位。
5. 不输出具体仓位，只输出风险分级和观察条件。

只输出【第二部分：形态点位+截图备注】
### 1. 形态是否成立
### 2. 截图关键点位标注
按候选标注点逐条输出；不得补齐ABCDE。
### 3. 支撑与压力位分析
### 4. 风险分级与观察条件
"""
    return audit_ai_output(call_deepseek_api(prompt, DEEPSEEK_KEY2))

def calc_support_resistance(df):
    last = df.iloc[-1]
    return {
        "现价": round(last["收盘"],2),
        "压力位": round(df["最高"].iloc[-20:].max(),2),
        "支撑位": round(df["最低"].iloc[-20:].min(),2),
        "MA60": round(last["ma60"],2),
        "10日均量":int(last["vol_ma10"]),
        "20日均量":int(last["vol_ma20"])
    }

def read_self_stock_with_name():
    if not os.path.exists(SELF_STOCK_TXT):
        with open(SELF_STOCK_TXT,"w",encoding="utf-8") as f:
            f.write("000977 浪潮信息\n603019 中科曙光\n002230 科大讯飞\n601138 工业富联\n")
    with open(SELF_STOCK_TXT,"r",encoding="utf-8") as f:
        lines = [l.strip() for l in f if l.strip()]
    stock_list = []
    seen = set()
    dup = 0
    for line in lines:
        parts = line.split(" ",1)
        code = parts[0]
        if code in seen:
            dup += 1
            continue
        seen.add(code)
        stock_list.append((code, line))
    if dup > 0:
        print(f"[load_stock_list] 已跳过 {dup} 只重复代码（股票更名），实际 {len(stock_list)} 只")
    return stock_list

def export_report(res):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(REPORT_TXT,"w",encoding="utf-8") as f:
        f.write(f"=== 技术形态综合选股报告 {now} ===\n")
        f.write("\n".join(res))

def export_batch_scan_report(rows):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    rank_order = {"择时强势": 1, "择时偏强": 2, "观察": 3, "择时弱势": 4, "数据不足": 5}
    sorted_rows = sorted(
        rows,
        key=lambda x: (rank_order.get(x.get("track_label", ""), 99), -float(x.get("score") or 0))
    )

    with open(BATCH_SCAN_REPORT, "w", encoding="utf-8") as f:
        f.write(f"=== 批量扫描结果报告 {now} ===\n")
        f.write("说明：纯V15全链路评分（四因子→场景→过滤→扩展维度→仓位），≥55偏强、≥45中性、<45弱势。\n")
        f.write("注：等级可能因共振偏空/波动率/背离等定性信号降级，会标注 ⚠中性→弱势 等降级提示。\n\n")
        for item in sorted_rows:
            if item.get("status") != "ok":
                f.write(f"{item['name']} | 数据不足\n")
                continue
            dn = item.get("downgrade_note", "")
            f.write(
                f"{item['track_label']} | {item['name']} | "
                f"V15:{item.get('v15_score', 0):.0f}分/{item.get('v15_level', 'N/A')}{dn} | "
                f"场景:{item.get('v15_scenario', 'N/A')}\n"
            )
    return BATCH_SCAN_REPORT

def export_batch_ai_report(content):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(BATCH_AI_REPORT,"w",encoding="utf-8") as f:
        f.write(f"=== 批量全技术维度AI报告 {now} ===\n")
        f.write(content)

def export_ai_input_snapshot(code, name, hit_patterns, rate_info, score, tag, price_info, df):
    snapshot = build_technical_snapshot(df, hit_patterns, rate_info, score, tag, price_info)
    candidates = build_point_candidates(df, hit_patterns)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    file_path = os.path.join(APP_DIR, AI_DEBUG_TXT)
    content = {
        "export_time": now,
        "stock": {"code": code, "name": name},
        "purpose": "用于核对AI是否只基于本地技术事实输出，避免公告、资金流、行业消息等缺失数据被编造。",
        "technical_snapshot": snapshot,
        "point_candidates": [
            {"tag": tag_name, "date": date, "price": price, "description": desc}
            for tag_name, date, price, desc in candidates
        ],
        "ai_boundaries": [
            "只能使用technical_snapshot和point_candidates里的数据。",
            "missing_data中的项目必须标为暂无数据，无法判断。",
            "不得编造公告、财报、资金流、北向、龙虎榜、行业新闻。",
            "不得无依据使用主力资金、庄家、利好刺激、资金抢筹等表述。",
            "不得给确定性收益判断。"
        ]
    }
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(content, ensure_ascii=False, indent=2))
    return file_path

# ==================== 终极绘图函数：修复点位解析问题 ====================
def _get_screen_bbox(ax, x, y, dx, dy, w_pt, h_pt):
    """计算标注文本在屏幕像素空间的包围盒（用于精确碰撞检测）。

    ax: matplotlib axes
    x, y: 锚点在数据坐标系的坐标
    dx, dy: 偏移量（points，1pt=1/72英寸）
    w_pt, h_pt: 文本宽高（points）
    返回 (left, bottom, width, height) 屏幕像素坐标。
    """
    dpi = ax.figure.dpi
    pt2px = dpi / 72.0
    anchor_px = ax.transData.transform((x, y))
    left = anchor_px[0] + dx * pt2px
    bottom = anchor_px[1] + dy * pt2px
    return (left, bottom, w_pt * pt2px, h_pt * pt2px)


def _pick_anno_offset(ax, x, y, used_screen_boxes, base_offsets,
                       text_width_pt=80, text_height_pt=16):
    """从多个候选偏移位置中选一个在屏幕空间不重叠的。"""
    for dx, dy in base_offsets:
        box = _get_screen_bbox(ax, x, y, dx, dy, text_width_pt, text_height_pt)
        overlap = False
        for ub in used_screen_boxes:
            if (box[0] < ub[0] + ub[2] and box[0] + box[2] > ub[0] and
                box[1] < ub[1] + ub[3] and box[1] + box[3] > ub[1]):
                overlap = True
                break
        if not overlap:
            return dx, dy, box
    # 全部重叠就返回第一个
    first_box = _get_screen_bbox(ax, x, y, base_offsets[0][0], base_offsets[0][1],
                                  text_width_pt, text_height_pt)
    return base_offsets[0][0], base_offsets[0][1], first_box


def _add_annotation(ax, text, x, y, used_screen_boxes, offsets, color='blue',
                    fontsize=11, arrow_color=None, text_width_pt=100):
    """智能标注：自动选择不重叠的位置放置文本。"""
    text_height_pt = fontsize + 4
    dx, dy, bbox = _pick_anno_offset(ax, x, y, used_screen_boxes, offsets,
                                      text_width_pt, text_height_pt)
    used_screen_boxes.append(bbox)
    ac = arrow_color or color
    ax.scatter(x, y, s=100, c=color, zorder=6, edgecolors="white")
    ax.annotate(text, xy=(x, y), xytext=(dx, dy),
                textcoords="offset points", fontsize=fontsize, color=color,
                arrowprops=dict(arrowstyle="->", color=ac, lw=1.5))


def plot_stock_chart(df, hit_patterns, parent, point_marks=None):
    try:
        if not CHART_AVAILABLE:
            return None
        df_plot = df.tail(60).copy()
        df_plot['date_num'] = mdates.date2num(df_plot['date'])
        ohlc = df_plot[['date_num','开盘','最高','最低','收盘']].values
        fig, ax = plt.subplots(figsize=(11,6), dpi=100)
        candlestick_ohlc(ax, ohlc, width=0.6, colorup='red', colordown='green')

        ax.plot(df_plot.date_num, df_plot.ma5, label='MA5', c='#377eb8')
        ax.plot(df_plot.date_num, df_plot.ma10, label='MA10', c='#ff7f0e')
        ax.plot(df_plot.date_num, df_plot.ma20, label='MA20', c='#9400d3')
        ax.plot(df_plot.date_num, df_plot.ma60, label='MA60', c='#b03060')

        rh = df_plot['最高'].iloc[-20:].max()
        rl = df_plot['最低'].iloc[-20:].min()
        ld = df_plot.iloc[-1]['date_num']
        ax.axhline(rh, c='r', ls='--', alpha=0.7)
        ax.axhline(rl, c='g', ls='--', alpha=0.7)

        # 压力/支撑标注，简单放右侧
        ax.annotate(f'压力 {rh:.2f}', (ld, rh), fontsize=10, c='red',
                    xytext=(8, 0), textcoords="offset points", va='bottom')
        ax.annotate(f'支撑 {rl:.2f}', (ld, rl), fontsize=10, c='green',
                    xytext=(8, 0), textcoords="offset points", va='top')

        # 用于跟踪已占用的标注区域
        anno_boxes = []

        # 候选偏移位置（多样化方向，避免扎堆）
        OFFSET_SET = [
            (18, 18),    # 右上
            (-18, 18),   # 左上
            (18, -18),   # 右下
            (-18, -18),  # 左下
            (0, 22),     # 正上
            (0, -22),    # 正下
            (25, 10),    # 右偏上
            (-25, 10),   # 左偏上
            (25, -10),   # 右偏下
            (-25, -10),  # 左偏下
        ]

        # 标注点位全部来自本地形态识别，避免AI文字与截图不一致。
        if point_marks and isinstance(point_marks, dict):
            for tag, (x_pos, y_pos, desc) in point_marks.items():
                text_full = f"{tag} | {desc}"
                tw = min(180, max(90, len(text_full) * 6))
                _add_annotation(ax, text_full, x_pos, y_pos, anno_boxes,
                                OFFSET_SET, color='#1565c0', fontsize=10,
                                text_width_pt=tw)

        df_sub = df_plot.iloc[-60:]
        last_idx = df_sub.index[-1]

        # 如果没有 point_marks，直接用智能布局兼容旧逻辑
        if not point_marks:
            if "金针探底" in hit_patterns:
                x = df_sub.loc[last_idx, 'date_num']
                y = df_sub.loc[last_idx, '最低']
                _add_annotation(ax, "金针探底", x, y, anno_boxes,
                                OFFSET_SET, color='#1565c0', fontsize=11, text_width_pt=80)

            if "阳包阴" in hit_patterns:
                x = df_sub.loc[last_idx, 'date_num']
                y = df_sub.loc[last_idx, '收盘']
                _add_annotation(ax, "阳包阴", x, y, anno_boxes,
                                OFFSET_SET, color='#6a1b9a', fontsize=11, text_width_pt=80)

        # 老鸭头标注
        if "老鸭头形态" in hit_patterns:
            first_half = df_sub.iloc[:len(df_sub)//2]
            dh_idx = first_half['最高'].idxmax()
            dh_x = df_sub.loc[dh_idx, 'date_num']
            dh_y = df_sub.loc[dh_idx, '最高']

            post_head = df_sub.loc[dh_idx:]
            dm_idx = post_head['最低'].idxmin()
            dm_x = df_sub.loc[dm_idx, 'date_num']
            dm_y = df_sub.loc[dm_idx, '最低']

            post_mouth = df_sub.loc[dm_idx:]
            mask = (post_mouth['最高']-post_mouth['最低'])/post_mouth['收盘'] < 0.02
            if mask.any():
                dn_idx = post_mouth[mask].index[0]
                dn_x = df_sub.loc[dn_idx, 'date_num']
                dn_y = df_sub.loc[dn_idx, '收盘']
            else:
                dn_x, dn_y = dm_x, dm_y

            post_nose = df_sub.loc[dn_idx:]
            om_idx = post_nose['收盘'].idxmax()
            om_x = df_sub.loc[om_idx, 'date_num']
            om_y = df_sub.loc[om_idx, '收盘']

            # 鸭头/鸭嘴/鸭鼻孔/张嘴使用专门的不同方向偏移
            DUCK_OFFSETS = [(-40, 25), (-40, -25), (40, 0), (40, 25)]
            DUCK_LABELS = ["鸭头", "鸭嘴", "鸭鼻孔", "张嘴"]
            DUCK_PTS = [(dh_x, dh_y), (dm_x, dm_y), (dn_x, dn_y), (om_x, om_y)]

            for lbl, (px, py), (ddx, ddy) in zip(DUCK_LABELS, DUCK_PTS, DUCK_OFFSETS):
                tw, th = 70, 18
                # 检查此偏移在屏幕空间是否与已有标注重叠
                candidate_box = _get_screen_bbox(ax, px, py, ddx, ddy, tw, th)
                overlap = False
                for ub in anno_boxes:
                    if (candidate_box[0] < ub[0] + ub[2] and candidate_box[0] + candidate_box[2] > ub[0] and
                        candidate_box[1] < ub[1] + ub[3] and candidate_box[1] + candidate_box[3] > ub[1]):
                        overlap = True
                        break
                if overlap:
                    ndx, ndy, new_box = _pick_anno_offset(ax, px, py, anno_boxes, OFFSET_SET, tw, th)
                    ddx, ddy = ndx, ndy
                    anno_boxes.append(new_box)
                else:
                    anno_boxes.append(candidate_box)
                ax.scatter(px, py, s=100, c='red', zorder=6, edgecolors='white')
                ax.annotate(lbl, xy=(px, py), xytext=(ddx, ddy),
                            textcoords="offset points", fontsize=12, color='red',
                            arrowprops=dict(arrowstyle="->", color="red", lw=2))

            ax.plot([dh_x, dm_x, dn_x, om_x], [dh_y, dm_y, dn_y, om_y],
                    c='red', linewidth=2.5, alpha=0.8)

        ax.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d'))
        plt.xticks(rotation=30)
        plt.legend(fontsize=10)
        plt.tight_layout()
        canvas = FigureCanvasTkAgg(fig, master=parent)
        canvas.draw()
        return canvas
    except Exception as e:
        print("绘图异常：", e)
        return None

# ---------------- 主界面 ----------------
class StockProUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("本地技术面AI解读系统")

        # 自适应窗口：不超过屏幕 90%
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        w = min(1480, int(sw * 0.92))
        h = min(860, int(sh * 0.88))
        self.geometry(f"{w}x{h}+{int((sw-w)/2)}+{int((sh-h)/2)}")
        self.minsize(1100, 680)

        self.stock_cache = {}
        self.batch_scan_rows = []
        self.canvas = None
        self.sector_map = {}
        self.news_items = []       # 市场新闻缓存（情绪模块复用）
        self.sector_source = "未加载"
        self.configure(bg="#eef2f6")
        self.setup_ui_style()
        self._build_ui()

    # ═══════════════════════════════════════════
    # UI 构建（拆分方法，保持 __init__ 干净）
    # ═══════════════════════════════════════════

    def _build_ui(self):
        self._build_header()
        self._build_body()
        self._build_statusbar()

        self.load_stock_list()
        self._startup_init()

    def _build_header(self):
        h = tk.Frame(self, bg="#1f2937", height=44)
        h.pack(side=tk.TOP, fill=tk.X)
        h.pack_propagate(False)

        title_col = tk.Frame(h, bg="#1f2937")
        title_col.pack(side=tk.LEFT, padx=14)

        tk.Label(title_col, text="V15Alpha", font=("Consolas", 16, "bold"),
                 fg="#60a5fa", bg="#1f2937").pack(side=tk.LEFT)
        tk.Label(title_col, text=" | 本地技术面AI解读系统",
                 font=("微软雅黑", 11), fg="white", bg="#1f2937").pack(side=tk.LEFT, padx=(6, 0))

        # 信号源状态（紧凑单行）
        src_row = tk.Frame(h, bg="#1f2937")
        src_row.pack(side=tk.RIGHT, padx=12)

        tk.Label(src_row, text="数据:", font=("微软雅黑", 8), fg="#9ca3af",
                 bg="#1f2937").pack(side=tk.LEFT, padx=(0, 2))

        self.src_labels = {}
        srcs = [("通达信", "tdx"), ("行情", "tencent"),
                ("板块", "sector"), ("新闻", "news"), ("港美", "cross")]
        for name, key in srcs:
            lbl = tk.Label(src_row, text=f"●{name}", font=("微软雅黑", 8),
                           fg="#6b7280", bg="#1f2937")
            lbl.pack(side=tk.LEFT, padx=3)
            self.src_labels[key] = lbl

    def _build_body(self):
        """主体：左侧可滚动控制面板 + 右侧 Notebook。"""
        body = tk.Frame(self, bg="#eef2f6")
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=8, pady=6)

        # ---- 左侧可滚动面板 ----
        left_outer = tk.Frame(body, width=270, bg="#eef2f6")
        left_outer.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 6))
        left_outer.pack_propagate(False)

        left_canvas = tk.Canvas(left_outer, bg="#eef2f6", highlightthickness=0, width=260)
        left_scroll = ttk.Scrollbar(left_outer, orient="vertical", command=left_canvas.yview)
        self.left = tk.Frame(left_canvas, bg="#eef2f6")

        self.left.bind("<Configure>", lambda e: left_canvas.configure(
            scrollregion=left_canvas.bbox("all")))

        left_canvas.create_window((0, 0), window=self.left, anchor="nw")
        left_canvas.configure(yscrollcommand=left_scroll.set)

        left_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        left_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # 鼠标滚轮 → 左侧面板滚动（递归绑定到所有子组件，跳过股票列表）
        def _on_left_wheel(event):
            left_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        # ---- 自选股票池 ----
        stock_box = ttk.LabelFrame(self.left, text="自选股票池")
        stock_box.pack(fill=tk.X, padx=4, pady=(0, 4))

        search_row = tk.Frame(stock_box, bg="#eef2f6")
        search_row.pack(fill=tk.X, padx=6, pady=(6, 0))
        tk.Label(search_row, text="搜索", font=("微软雅黑", 8), bg="#eef2f6",
                 fg="#374151").pack(side=tk.LEFT)
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self.apply_stock_filter())
        self.search_entry = ttk.Entry(search_row, textvariable=self.search_var, font=("微软雅黑", 9))
        self.search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 2))
        ttk.Button(search_row, text="x", width=3, command=self.clear_stock_search).pack(side=tk.RIGHT)

        self.lb = tk.Listbox(stock_box, font=("微软雅黑", 9), height=12,
                             activestyle="none", relief=tk.FLAT,
                             selectbackground="#2563eb", selectforeground="white", bg="#ffffff")
        self.lb.pack(fill=tk.BOTH, expand=True, padx=6, pady=4)

        # 自选股列表鼠标滚轮
        def _lb_wheel(e):
            self.lb.yview_scroll(int(-1 * (e.delta / 120)), "units")
        self.lb.bind("<MouseWheel>", _lb_wheel)

        # ---- 功能按钮分组（紧凑） ----
        btn_groups = [
            ("扫描与选股", [
                ("刷新自选", self.load_stock_list),
                ("批量扫描", self.batch_scan),
                ("导出扫描结果", self.export_batch_scan_result),
                ("强势股筛选", self.screen_stock),
                ("添加自选股", self.add_stock_dialog),
                ("导出自选股", self.export_stocks_file),
                ("复盘", self.run_review),
            ]),
            ("单股分析", [
                ("择时雷达信号", self.show_timing_radar),
                ("V15趋势图", self.show_v15_trend_chart),
                ("技术面AI解读", self.run_ai_analysis),
            ]),
            ("市场全景", [
                ("板块热度", self.show_sector_heatmap),
                ("新闻雷达", self.show_news_radar),
                ("新闻蒸馏", self.show_news_distill),
                ("市场仪表盘", self.show_market_dashboard),
                ("刷新板块数据", self.refresh_sector_data),
            ]),
            ("持仓管理", [
                ("持仓总览", self.show_portfolio),
                ("新增持仓", self.add_portfolio_dialog),
                ("盘前雷达", self.show_premarket),
            ]),
            ("其他", [
                ("AI Key状态", self.show_key_status),
                ("批量技术报告", self.batch_make_report),
                ("清空缓存", self.clear_cache),
            ]),
        ]

        for group_title, btns in btn_groups:
            box = ttk.LabelFrame(self.left, text=group_title)
            box.pack(fill=tk.X, padx=4, pady=(0, 4))
            for t, c in btns:
                ttk.Button(box, text=t, command=c).pack(
                    fill=tk.X, padx=6, pady=1)

        # 左侧栏所有内容建完后递归绑定滚轮（跳过股票列表）
        def _bind_left_scroll(w):
            if w is not self.lb:
                w.bind("<MouseWheel>", _on_left_wheel)
                for child in w.winfo_children():
                    _bind_left_scroll(child)
        _bind_left_scroll(self.left)

        # ---- 右侧 Notebook ----
        self.right = tk.Frame(body, bg="#eef2f6")
        self.right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        self.notebook = ttk.Notebook(self.right)
        self.notebook.pack(fill=tk.BOTH, expand=True)

        self._build_tabs()

    def _build_tabs(self):
        """Tab: 批量扫描/单股分析/择时雷达/K线图表/V15趋势/信号看板/板块热度/新闻蒸馏/新闻雷达/市场全景/复盘/持仓/全量回测/单股回测/盘前/因子IC/板块资金流向。"""
        self.scan_tab = tk.Frame(self.notebook, bg="#ffffff")
        self.single_tab = tk.Frame(self.notebook, bg="#ffffff")
        self.radar_tab = tk.Frame(self.notebook, bg="#ffffff")
        self.chart_tab = tk.Frame(self.notebook, bg="#ffffff")
        self.trend_tab = tk.Frame(self.notebook, bg="#ffffff")
        self.signal_tab = tk.Frame(self.notebook, bg="#ffffff")
        self.sector_tab = tk.Frame(self.notebook, bg="#ffffff")
        self.news_tab = tk.Frame(self.notebook, bg="#ffffff")
        self.distill_tab = tk.Frame(self.notebook, bg="#ffffff")
        self.hotspot_tab = tk.Frame(self.notebook, bg="#ffffff")
        self.review_tab = tk.Frame(self.notebook, bg="#ffffff")

        self.notebook.add(self.scan_tab, text="批量扫描")
        self.notebook.add(self.single_tab, text="单股分析")
        self.notebook.add(self.radar_tab, text="择时雷达")
        self.notebook.add(self.chart_tab, text="K线图表")
        self.notebook.add(self.trend_tab, text="V15趋势")
        self.notebook.add(self.signal_tab, text="信号看板")
        self.notebook.add(self.sector_tab, text="板块热度")
        self.notebook.add(self.news_tab, text="新闻雷达")
        self.notebook.add(self.distill_tab, text="新闻蒸馏")
        self.notebook.add(self.hotspot_tab, text="市场全景")
        self.notebook.add(self.review_tab, text="复盘")

        # t_review: 复盘报告

        # t_review: 复盘报告
        self.t_review = scrolledtext.ScrolledText(self.review_tab, font=("微软雅黑", 10),
                                                   bg="#f8fbff", relief=tk.FLAT, wrap=tk.WORD)
        self.t_review.pack(fill=tk.BOTH, expand=True, padx=8, pady=6)

        # t_distill: 新闻蒸馏
        self.t_distill = scrolledtext.ScrolledText(self.distill_tab, font=("微软雅黑", 10),
                                                     bg="#f8fbff", relief=tk.FLAT, wrap=tk.WORD)
        self.t_distill.pack(fill=tk.BOTH, expand=True, padx=8, pady=6)

        # t1: 批量扫描
        self.scan_content = tk.Frame(self.scan_tab, bg="#ffffff")
        self.scan_content.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)
        self.t1 = scrolledtext.ScrolledText(self.scan_content, font=("微软雅黑", 10),
                                            bg="#f8fbff", relief=tk.FLAT, wrap=tk.WORD)
        self.t1.pack(fill=tk.BOTH, expand=True, padx=6, pady=(6, 0))
        self.configure_scan_color_tags()

        # 信号验证区（批量扫描Tab底部）
        self.verify_frm = tk.LabelFrame(self.scan_content, text="信号验证",
                                         font=("微软雅黑", 9, "bold"),
                                         bg="#fefefe", fg="#1f2937")
        self.verify_frm.pack(fill=tk.X, padx=6, pady=(4, 6))
        vf_top = tk.Frame(self.verify_frm, bg="#fefefe")
        vf_top.pack(fill=tk.X, padx=6, pady=(6, 2))
        tk.Label(vf_top, text="粘贴代码+名称（每行一个）：", font=("微软雅黑", 8),
                 bg="#fefefe", fg="#6b7280").pack(side=tk.LEFT)
        ttk.Button(vf_top, text="一键复制", command=self._copy_verify_result).pack(side=tk.RIGHT, padx=2)
        ttk.Button(vf_top, text="开始验证", command=self.verify_signal_list).pack(side=tk.RIGHT, padx=2)
        self.verify_input = scrolledtext.ScrolledText(self.verify_frm, font=("微软雅黑", 9),
                                                       bg="#fafafa", relief=tk.FLAT,
                                                       wrap=tk.WORD, height=4)
        self.verify_input.pack(fill=tk.X, padx=6, pady=(0, 4))
        self.verify_output = scrolledtext.ScrolledText(self.verify_frm, font=("微软雅黑", 9),
                                                        bg="#fefefe", relief=tk.FLAT,
                                                        wrap=tk.WORD, height=6)
        self.verify_output.pack(fill=tk.BOTH, expand=True, padx=6, pady=(0, 6))

        # t8: 单股分析
        self.t8 = scrolledtext.ScrolledText(self.single_tab, font=("微软雅黑", 10),
                                            bg="#f8fbff", relief=tk.FLAT, wrap=tk.WORD)
        self.t8.pack(fill=tk.BOTH, expand=True, padx=8, pady=6)

        # t9: 择时雷达（独立Tab）
        self._radar_data = None
        radar_top_frame = tk.Frame(self.radar_tab, bg="#f0f4f8")
        radar_top_frame.pack(fill=tk.X, padx=8, pady=(6, 0))
        self.btn_ai_radar = tk.Button(
            radar_top_frame, text="🤖 AI解读", font=("微软雅黑", 9, "bold"),
            bg="#1a73e8", fg="white", relief=tk.FLAT, padx=12, pady=3, cursor="hand2",
            state=tk.DISABLED, command=self._on_ai_radar)
        self.btn_ai_radar.pack(side=tk.RIGHT, padx=(4, 0))
        tk.Label(radar_top_frame, text="择时雷达信号看板", font=("微软雅黑", 11, "bold"),
                 bg="#f0f4f8", fg="#1f2937").pack(side=tk.LEFT)
        self.t9 = scrolledtext.ScrolledText(self.radar_tab, font=("微软雅黑", 10),
                                            bg="#f8fbff", relief=tk.FLAT, wrap=tk.WORD)
        self.t9.pack(fill=tk.BOTH, expand=True, padx=8, pady=6)

        # t2: 形态备注
        # t2 为隐式控件：形态备注Tab已删除，保留实例供 show_key_status/择时雷达 清空引用
        self.t2 = scrolledtext.ScrolledText(self.single_tab, font=("微软雅黑", 10),
                                            bg="#fffaf0", relief=tk.FLAT, wrap=tk.WORD)

        # t3: 信号看板
        s_top = tk.Frame(self.signal_tab, bg="#ffffff")
        s_top.pack(fill=tk.X, padx=8, pady=(6, 0))
        tk.Label(s_top, text="实盘信号看板", font=("微软雅黑", 11, "bold"),
                 bg="#ffffff", fg="#1f2937").pack(side=tk.LEFT)
        self.signal_auto_refresh = tk.BooleanVar(value=False)
        tk.Checkbutton(s_top, variable=self.signal_auto_refresh, bg="#ffffff",
                       text="自动刷新", font=("微软雅黑", 8),
                       command=self._toggle_signal_auto_refresh).pack(side=tk.LEFT, padx=12)
        ttk.Button(s_top, text="刷新", command=self.refresh_signal_board).pack(side=tk.RIGHT, padx=2)
        ttk.Button(s_top, text="导出Excel", command=self.export_signal_excel).pack(side=tk.RIGHT, padx=2)

        self.t3 = scrolledtext.ScrolledText(self.signal_tab, font=("微软雅黑", 10),
                                            bg="#f8fbff", relief=tk.FLAT, wrap=tk.WORD)
        self.t3.pack(fill=tk.BOTH, expand=True, padx=8, pady=(6, 0))
        self._signal_refresh_id = None

        # ── 底部：单股分时详情（选中左侧股票后点刷新）──
        self.intraday_frm = tk.Frame(self.signal_tab, bg="#f0f4ff", height=52)
        self.intraday_frm.pack(fill=tk.X, padx=8, pady=(4, 6))
        self.intraday_frm.pack_propagate(False)
        tk.Label(self.intraday_frm, text="单股分时", font=("微软雅黑", 9, "bold"),
                 bg="#f0f4ff", fg="#1f2937").pack(side=tk.LEFT, padx=8)
        self.intraday_status = tk.Label(self.intraday_frm, text="选择左侧股票 → 点击刷新分时",
                                        font=("微软雅黑", 9), bg="#f0f4ff", fg="#6b7280")
        self.intraday_status.pack(side=tk.LEFT, padx=8)
        ttk.Button(self.intraday_frm, text="刷新分时",
                   command=self.refresh_intraday_panel).pack(side=tk.RIGHT, padx=4)

        # K线图表（K线形态标注 + 支撑压力）
        self.chart_frm = tk.Frame(self.chart_tab, bg="white")
        self.chart_frm.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

        # V15趋势图（评分曲线）
        self.trend_frm = tk.Frame(self.trend_tab, bg="white")
        self.trend_frm.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

        # 帮助按钮栏
        trend_bar = tk.Frame(self.trend_frm, bg="white")
        trend_bar.pack(fill=tk.X, pady=(0, 2))
        help_btn = tk.Label(trend_bar, text="? 因子说明", font=("微软雅黑", 9, "bold"),
                           bg="#e8eaf6", fg="#3949ab", padx=8, pady=1, cursor="hand2")
        help_btn.pack(side=tk.RIGHT, padx=4)
        self._trend_help_tooltip = None

        def _toggle_trend_help(event):
            # 点击切换：如果已显示则关闭，否则打开
            if self._trend_help_tooltip:
                try:
                    self._trend_help_tooltip.destroy()
                except Exception:
                    pass
                self._trend_help_tooltip = None
                help_btn.config(text="? 因子说明")
                return

            tw = tk.Toplevel(self)
            tw.overrideredirect(True)
            tw.configure(bg="#1a1a2e")
            x = event.widget.winfo_rootx() - 480
            y = event.widget.winfo_rooty() + 28
            tw.geometry(f"500x560+{x}+{y}")

            # Canvas + 滚动条
            canvas = tk.Canvas(tw, bg="#1a1a2e", highlightthickness=0, width=480, height=540)
            scrollbar = tk.Scrollbar(tw, orient="vertical", command=canvas.yview)
            canvas.configure(yscrollcommand=scrollbar.set)
            scrollbar.pack(side="right", fill="y")
            canvas.pack(side="left", fill="both", expand=True)

            frame = tk.Frame(canvas, bg="#1a1a2e", padx=12, pady=10)
            canvas.create_window((0, 0), window=frame, anchor="nw", tags="inner")

            lines = ["下子图 · 因子匹配说明", ""]

            # ── 如果有当前股票的结论，优先展示 ──
            if hasattr(self, '_trend_conclusion') and self._trend_conclusion:
                lines += self._trend_conclusion.split("\n")
                lines += ["", "━━ 四种因子 · 逐一解读 ━━", ""]
            else:
                lines += [
                    "价格走势下方叠加了V15总分 + 与价格最相关的子因子（归一化到价格尺度）。",
                    "r值越大，说明该因子的评分变化能解释价格的波动。",
                    "",
                    "先选股票点「V15趋势图」生成图表，再回来看结论。",
                    "",
                    "━━ 四种因子 · 逐一解读 ━━",
                    "",
                ]

            lines += [
                "反转因子（红）— 均值回归信号",
                "  构成: RSI超卖 + 布林带下轨偏离 + MA20乖离",
                "  高分 = 跌过头了，该弹了；低分 = 涨过头了，该歇了",
                "  如果 r 高 → 这是震荡票，价格被均值回归力量主导",
                "  用法: 等反转分飙升后进场（价格低位+反转高分=反弹窗口），",
                "        不要追涨，赚钱就跑（10-15%），因为震荡区间总会回来",
                "",
                "MACD因子（蓝）— 趋势动量信号",
                "  构成: 快慢线离差 + 金叉死叉 + 柱状图方向",
                "  高分 = 多头趋势强劲；低分 = 空头主导",
                "  如果 r 高 → 这是趋势票，价格由动量驱动而非均值回归",
                "  用法: 顺势而为，金叉确认后追进去，设跟踪止盈，",
                "        不要抄底（趋势反转前会一直跌），等转弱信号再走",
                "",
                "量能因子（绿）— 量价配合信号",
                "  构成: 量比 + 量价趋势一致性 + 缩量/放量判断",
                "  高分 = 放量上涨/缩量企稳；低分 = 缩量阴跌/放量滞涨",
                "  如果 r 高 → 这是资金驱动票，量的变化走在价的前面",
                "  用法: 跟量走，放量突破=真突破可以追，缩量上涨=虚涨要跑",
                "",
                "安全因子（橙）— 均线结构信号",
                "  构成: 均线多头排列 + MA60支撑距离 + 乖离率",
                "  高分 = 均线多头排列且价格在支撑位上方",
                "  如果 r 高 → 这是均线票，价格围绕关键均线（MA20/60）运行",
                "  用法: 均线多头排列时逢MA20/60回踩买，均线走平时观望",
            ]
            for i, txt in enumerate(lines):
                # 高亮重要行：标题、━━线、性格、状态、买入/风险/持仓
                bright = (i == 0 or txt.startswith("━") or
                          txt.startswith("性格") or txt.startswith("状态") or
                          txt.startswith("买入要点") or txt.startswith("风险提示") or
                          txt.startswith("持仓策略"))
                fg = "#ffffff" if bright else "#b0bec5"
                fs = 11 if (i == 0 or bright) else 9
                tk.Label(frame, text=txt, font=("微软雅黑", fs), bg="#1a1a2e", fg=fg,
                        anchor="w", justify=tk.LEFT, wraplength=440).pack(fill=tk.X, pady=(0, 2) if txt else 0)

            # 更新滚动区域
            frame.update_idletasks()
            canvas.configure(scrollregion=canvas.bbox("all"))

            # 鼠标滚轮支持 — 绑定到窗口及所有子控件（包括 Label）
            def _on_mousewheel(event):
                canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

            def _bind_scroll_recursive(widget):
                widget.bind("<MouseWheel>", _on_mousewheel)
                for child in widget.winfo_children():
                    _bind_scroll_recursive(child)

            _bind_scroll_recursive(tw)

            self._trend_help_tooltip = tw
            help_btn.config(text="? 关闭说明")

        help_btn.bind("<Button-1>", _toggle_trend_help)

        self.trend_chart_frm = tk.Frame(self.trend_frm, bg="white")
        self.trend_chart_frm.pack(fill=tk.BOTH, expand=True)

        # 板块热度
        sector_top = tk.Frame(self.sector_tab, bg="#ffffff")
        sector_top.pack(fill=tk.X, padx=8, pady=(6, 0))
        tk.Label(sector_top, text="板块热力地图", font=("微软雅黑", 11, "bold"),
                 bg="#ffffff", fg="#1f2937").pack(side=tk.LEFT)
        ttk.Button(sector_top, text="刷新", command=self.show_sector_heatmap).pack(side=tk.RIGHT)
        self.sector_frm = tk.Frame(self.sector_tab, bg="white")
        self.sector_frm.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

        # 新闻雷达
        self.t6 = scrolledtext.ScrolledText(self.news_tab, font=("微软雅黑", 10),
                                            bg="#f8fbff", relief=tk.FLAT, wrap=tk.WORD)
        self.t6.pack(fill=tk.BOTH, expand=True, padx=8, pady=6)

        # 市场全景（仪表盘）
        hs_top = tk.Frame(self.hotspot_tab, bg="#ffffff")
        hs_top.pack(fill=tk.X, padx=8, pady=(6, 0))
        tk.Label(hs_top, text="全市场实时仪表盘", font=("微软雅黑", 11, "bold"),
                 bg="#ffffff", fg="#1f2937").pack(side=tk.LEFT)
        ttk.Button(hs_top, text="刷新", command=self.show_market_dashboard).pack(side=tk.RIGHT)
        self.dashboard_frm = tk.Frame(self.hotspot_tab, bg="white")
        self.dashboard_frm.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

        # 财务穿透 Tab
        self.finance_tab = tk.Frame(self.notebook, bg="#ffffff")
        self.notebook.add(self.finance_tab, text="财务穿透")
        fi_top = tk.Frame(self.finance_tab, bg="#ffffff")
        fi_top.pack(fill=tk.X, padx=8, pady=(6, 0))
        tk.Label(fi_top, text="财务穿透 · 单股财报解读", font=("微软雅黑", 11, "bold"),
                 bg="#ffffff", fg="#1f2937").pack(side=tk.LEFT)
        self.fi_period_var = tk.StringVar(value="2026Q1")
        tk.Label(fi_top, text="报告期:", font=("微软雅黑", 8),
                 bg="#ffffff", fg="#6b7280").pack(side=tk.LEFT, padx=(12, 2))
        fi_periods = ttk.Combobox(fi_top, textvariable=self.fi_period_var,
                                   values=["2026Q1", "2025年报", "2025Q3"],
                                   font=("微软雅黑", 8), width=10, state="readonly")
        fi_periods.pack(side=tk.LEFT, padx=2)
        fi_periods.bind("<<ComboboxSelected>>", lambda e: self.refresh_financial_penetration())
        ttk.Button(fi_top, text="刷新", command=self.refresh_financial_penetration).pack(side=tk.RIGHT)
        self._fi_date_map = {"2026Q1": "20260331", "2025年报": "20251231", "2025Q3": "20250930"}
        self.t_finance = scrolledtext.ScrolledText(self.finance_tab, font=("微软雅黑", 9),
                                                    bg="#f8fbff", relief=tk.FLAT, wrap=tk.WORD)
        self.t_finance.pack(fill=tk.BOTH, expand=True, padx=8, pady=6)

        # 持仓管理 Tab
        self.portfolio_tab = tk.Frame(self.notebook, bg="#ffffff")
        self.notebook.add(self.portfolio_tab, text="持仓管理")
        pf_top = tk.Frame(self.portfolio_tab, bg="#ffffff")
        pf_top.pack(fill=tk.X, padx=8, pady=(6, 0))
        tk.Label(pf_top, text="持仓跟踪与盈亏管理", font=("微软雅黑", 11, "bold"),
                 bg="#ffffff", fg="#1f2937").pack(side=tk.LEFT)
        ttk.Button(pf_top, text="平仓", command=self.close_portfolio_dialog).pack(side=tk.RIGHT, padx=2)
        ttk.Button(pf_top, text="删除", command=self.delete_portfolio_dialog).pack(side=tk.RIGHT, padx=2)
        ttk.Button(pf_top, text="+ 新增持仓", command=self.add_portfolio_dialog).pack(side=tk.RIGHT, padx=2)
        ttk.Button(pf_top, text="刷新持仓", command=self.show_portfolio).pack(side=tk.RIGHT, padx=2)
        self.t10 = scrolledtext.ScrolledText(self.portfolio_tab, font=("微软雅黑", 10),
                                             bg="#f8fbff", relief=tk.FLAT, wrap=tk.WORD)
        self.t10.pack(fill=tk.BOTH, expand=True, padx=8, pady=6)

        # 全量回测 Tab
        self.backtest_tab = tk.Frame(self.notebook, bg="#ffffff")
        self.notebook.add(self.backtest_tab, text="全量回测")
        bt_top = tk.Frame(self.backtest_tab, bg="#ffffff")
        bt_top.pack(fill=tk.X, padx=8, pady=(6, 0))
        tk.Label(bt_top, text="全量回测 · 四因子原始信号纯净度测试", font=("微软雅黑", 11, "bold"),
                 bg="#ffffff", fg="#1f2937").pack(side=tk.LEFT)
        ttk.Button(bt_top, text="启动全量回测", command=self.run_backtest_all).pack(side=tk.RIGHT, padx=2)
        self.t_backtest = scrolledtext.ScrolledText(self.backtest_tab, font=("微软雅黑", 10),
                                                     bg="#f8fbff", relief=tk.FLAT, wrap=tk.WORD, height=10)
        self.t_backtest.pack(fill=tk.BOTH, expand=True, padx=8, pady=6)

        # 单股回测 Tab
        self.single_bt_tab = tk.Frame(self.notebook, bg="#ffffff")
        self.notebook.add(self.single_bt_tab, text="单股回测")
        bt1_top = tk.Frame(self.single_bt_tab, bg="#ffffff")
        bt1_top.pack(fill=tk.X, padx=8, pady=(6, 0))
        tk.Label(bt1_top, text="单股回测 · 完整V15全链路实战模拟", font=("微软雅黑", 11, "bold"),
                 bg="#ffffff", fg="#1f2937").pack(side=tk.LEFT)
        self.bt_stock_label = tk.Label(bt1_top, text="", font=("微软雅黑", 9), bg="#ffffff", fg="#6b7280")
        self.bt_stock_label.pack(side=tk.LEFT, padx=15)

        bt_help_btn = tk.Label(bt1_top, text="? 回测说明", font=("微软雅黑", 9, "bold"),
                              bg="#e8eaf6", fg="#3949ab", padx=8, pady=1, cursor="hand2")
        bt_help_btn.pack(side=tk.RIGHT, padx=4)
        ttk.Button(bt1_top, text="启动单股回测", command=self.run_backtest_single).pack(side=tk.RIGHT, padx=2)
        self._bt_help_tooltip = None

        def _toggle_bt_help(event):
            if self._bt_help_tooltip:
                try: self._bt_help_tooltip.destroy()
                except Exception: pass
                self._bt_help_tooltip = None; bt_help_btn.config(text="? 回测说明"); return

            tw = tk.Toplevel(self); tw.overrideredirect(True); tw.configure(bg="#1a1a2e")
            x = event.widget.winfo_rootx() - 420
            y = event.widget.winfo_rooty() + 28
            tw.geometry(f"440x380+{x}+{y}")
            cv = tk.Canvas(tw, bg="#1a1a2e", highlightthickness=0, width=420, height=360)
            sb = tk.Scrollbar(tw, orient="vertical", command=cv.yview)
            cv.configure(yscrollcommand=sb.set); sb.pack(side="right", fill="y"); cv.pack(side="left", fill="both", expand=True)
            frame = tk.Frame(cv, bg="#1a1a2e", padx=12, pady=10)
            cv.create_window((0, 0), window=frame, anchor="nw")
            lines = [
                "单股回测 · 使用说明", "",
                "回测逻辑：在每根日K线上运行完整V15全链路评分",
                "（四因子→场景→共振→波动率→背离→板块→扩展维度），",
                "当信号为「强势」或「偏强」时开仓，持仓后逐日跟踪止盈止损。", "",
                "━━ 回测指标解读 ━━", "",
                "净值（纵轴）= 资金倍数，起始1.0",
                "  1.10 = 赚10%，0.90 = 亏10%，灰线=盈亏平衡",
                "  曲线向右上方走 → 策略对该股有效",
                "  有深坑 → 策略会经历大回撤，注意仓位", "",
                "胜率 = 盈利平仓次数 / 总平仓次数",
                "  50%以上且有正盈亏 → 策略有正向预期",
                "  胜率高但盈亏低 → 赚小钱亏大钱，风控有问题", "",
                "累计盈亏 = 所有平仓交易的盈亏总和(%)",
                "终净值 = 初始资金×(1+逐日收益率累积)", "",
                "━━ 如何判断结论 ━━", "",
                "  终净值>1.1 + 胜率>55% → 策略对该股有效，可实盘参考",
                "  终净值>1.0但胜率不高 → 少数大赚覆盖多数小亏，适合长期持有",
                "  终净值<1.0 → 策略在该股上持续亏损，降低仓位或回避",
                "  交易次数过少(<3次) → 参考意义低，数据噪声大",
            ]
            for i, txt in enumerate(lines):
                fg = "#ffffff" if i == 0 or txt.startswith("━") else "#b0bec5"
                fs = 11 if i == 0 else 9
                tk.Label(frame, text=txt, font=("微软雅黑", fs), bg="#1a1a2e", fg=fg,
                        anchor="w", justify=tk.LEFT, wraplength=380).pack(fill=tk.X)
            frame.update_idletasks(); cv.configure(scrollregion=cv.bbox("all"))
            def _mw(event): cv.yview_scroll(int(-1*(event.delta/120)), "units")
            def _bind_all(w): w.bind("<MouseWheel>", _mw); [ _bind_all(c) for c in w.winfo_children() ]
            _bind_all(tw)
            self._bt_help_tooltip = tw; bt_help_btn.config(text="? 关闭说明")

        bt_help_btn.bind("<Button-1>", _toggle_bt_help)

        self.bt_chart_frm = tk.Frame(self.single_bt_tab, bg="white")
        self.bt_chart_frm.pack(fill=tk.X, padx=8, pady=4)
        self.t_single_backtest = scrolledtext.ScrolledText(self.single_bt_tab, font=("微软雅黑", 10),
                                                     bg="#f8fbff", relief=tk.FLAT, wrap=tk.WORD, height=10)
        self.t_single_backtest.pack(fill=tk.BOTH, expand=True, padx=8, pady=6)

        # 板块资金流向 Tab
        self.flow_tab = tk.Frame(self.notebook, bg="#ffffff")
        self.notebook.add(self.flow_tab, text="板块资金流向")
        flow_top = tk.Frame(self.flow_tab, bg="#ffffff")
        flow_top.pack(fill=tk.X, padx=8, pady=(6, 0))
        tk.Label(flow_top, text="板块资金流向 · 双维度监测", font=("微软雅黑", 11, "bold"),
                 bg="#ffffff", fg="#1f2937").pack(side=tk.LEFT)
        ttk.Button(flow_top, text="结论", command=self.show_sector_flow_conclusion).pack(side=tk.RIGHT, padx=2)
        ttk.Button(flow_top, text="刷新", command=self.show_sector_flow).pack(side=tk.RIGHT, padx=2)
        self.flow_chart_frm = tk.Frame(self.flow_tab, bg="white")
        self.flow_chart_frm.pack(fill=tk.X, padx=8, pady=4)

        # Δz 名单区域（日/周各一列）
        flow_bottom = tk.Frame(self.flow_tab, bg="#ffffff")
        flow_bottom.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 6))
        flow_bottom.columnconfigure(0, weight=1)
        flow_bottom.columnconfigure(1, weight=1)
        flow_bottom.rowconfigure(0, weight=1)

        self.flow_daily_frm = tk.LabelFrame(flow_bottom, text="日维度 Δz 异常", font=("微软雅黑", 9, "bold"),
                                            bg="#fefefe", fg="#c62828")
        self.flow_daily_frm.grid(row=0, column=0, sticky="nsew", padx=(0, 3))
        self.t_flow_daily = scrolledtext.ScrolledText(self.flow_daily_frm, font=("微软雅黑", 9),
                                                       bg="#fff5f5", relief=tk.FLAT, wrap=tk.WORD)
        self.t_flow_daily.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        self.flow_weekly_frm = tk.LabelFrame(flow_bottom, text="周维度 Δz 异常", font=("微软雅黑", 9, "bold"),
                                             bg="#fefefe", fg="#1565c0")
        self.flow_weekly_frm.grid(row=0, column=1, sticky="nsew", padx=(3, 0))
        self.t_flow_weekly = scrolledtext.ScrolledText(self.flow_weekly_frm, font=("微软雅黑", 9),
                                                        bg="#f5f8ff", relief=tk.FLAT, wrap=tk.WORD)
        self.t_flow_weekly.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        # 盘前雷达 Tab
        self.premarket_tab = tk.Frame(self.notebook, bg="#ffffff")
        self.notebook.add(self.premarket_tab, text="盘前雷达")
        pm_top = tk.Frame(self.premarket_tab, bg="#ffffff")
        pm_top.pack(fill=tk.X, padx=8, pady=(6, 0))
        tk.Label(pm_top, text="盘前雷达 · 港美股锚点映射", font=("微软雅黑", 11, "bold"),
                 bg="#ffffff", fg="#1f2937").pack(side=tk.LEFT)
        ttk.Button(pm_top, text="刷新", command=self.show_premarket).pack(side=tk.RIGHT, padx=2)
        self.t_premarket = scrolledtext.ScrolledText(self.premarket_tab, font=("微软雅黑", 10),
                                                      bg="#f8fbff", relief=tk.FLAT, wrap=tk.WORD)
        self.t_premarket.pack(fill=tk.BOTH, expand=True, padx=8, pady=6)

        # 因子IC Tab
        self.factor_ic_tab = tk.Frame(self.notebook, bg="#ffffff")
        self.notebook.add(self.factor_ic_tab, text="因子IC")
        ic_top = tk.Frame(self.factor_ic_tab, bg="#ffffff")
        ic_top.pack(fill=tk.X, padx=8, pady=(6, 0))
        tk.Label(ic_top, text="因子IC · 诊断各因子预测力", font=("微软雅黑", 11, "bold"),
                 bg="#ffffff", fg="#1f2937").pack(side=tk.LEFT)
        ttk.Button(ic_top, text="启动因子IC分析", command=self.run_factor_ic).pack(side=tk.RIGHT, padx=2)
        self.t_factor_ic = scrolledtext.ScrolledText(self.factor_ic_tab, font=("微软雅黑", 10),
                                                      bg="#f8fbff", relief=tk.FLAT, wrap=tk.WORD)
        self.t_factor_ic.pack(fill=tk.BOTH, expand=True, padx=8, pady=6)

        # 板块历史资金 Tab
        self.sector_history_tab = tk.Frame(self.notebook, bg="#ffffff")
        self.notebook.add(self.sector_history_tab, text="板块历史资金")
        sh_top = tk.Frame(self.sector_history_tab, bg="#ffffff")
        sh_top.pack(fill=tk.X, padx=8, pady=(6, 0))
        tk.Label(sh_top, text="板块历史资金 · 每日收盘资金流快照", font=("微软雅黑", 11, "bold"),
                 bg="#ffffff", fg="#1f2937").pack(side=tk.LEFT)
        self.sh_summary_label = tk.Label(sh_top, text="", font=("微软雅黑", 9),
                                          bg="#ffffff", fg="#6b7280")
        self.sh_summary_label.pack(side=tk.LEFT, padx=15)
        ttk.Button(sh_top, text="采集今日数据", command=self.collect_sector_history_today).pack(side=tk.RIGHT, padx=2)
        ttk.Button(sh_top, text="刷新", command=self.show_sector_history).pack(side=tk.RIGHT, padx=2)
        self.t_sector_history = scrolledtext.ScrolledText(self.sector_history_tab, font=("微软雅黑", 10),
                                                           bg="#f8fbff", relief=tk.FLAT, wrap=tk.WORD)
        self.t_sector_history.pack(fill=tk.BOTH, expand=True, padx=8, pady=6)

    def _build_statusbar(self):
        """底部状态栏：一行显示关键信息。"""
        bar = tk.Frame(self, bg="#e5e7eb", height=24)
        bar.pack(side=tk.BOTTOM, fill=tk.X)
        bar.pack_propagate(False)

        self.status_var = tk.StringVar(value="就绪")
        tk.Label(bar, textvariable=self.status_var, anchor="w",
                 font=("微软雅黑", 9), bg="#e5e7eb", fg="#374151").pack(
            side=tk.LEFT, fill=tk.X, padx=10, pady=2)

    def _startup_init(self):
        """启动时自动：API Key检测 + 板块预拉取 + 数据源状态初始化。"""
        n_keys = sum(1 for k in [DEEPSEEK_KEY1, DEEPSEEK_KEY2, DEEPSEEK_KEY3] if k)
        self.set_status(
            f"已载入自选股 {len(self.data)} 只 | "
            f"API Key: {n_keys}/3 | 正在检测数据源..."
        )

        # 通达信本机文件 → 始终绿
        self._update_source_color("tdx", "ok")

        # 腾讯行情快速检测
        threading.Thread(target=self._probe_tencent, daemon=True).start()

        # 港美股快速检测
        threading.Thread(target=self._probe_cross, daemon=True).start()

        # 后台预拉取板块数据
        threading.Thread(target=self._preload_sector, args=(n_keys,), daemon=True).start()

        # 新闻 — 启动时检测一次
        threading.Thread(target=self._probe_news, daemon=True).start()

        # 股票名称缓存（后台预拉取，首次约80s，后续秒开）
        threading.Thread(target=self._preload_names, daemon=True).start()

    def _probe_tencent(self):
        """快速检测腾讯行情连通性。"""
        try:
            from urllib.request import Request, urlopen
            req = Request("https://qt.gtimg.cn/q=sh000001", headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://qt.gtimg.cn/",
            })
            with urlopen(req, timeout=5) as resp:
                if b"~" in resp.read()[:200]:
                    self._tencent_ok = True
                    self.after(0, lambda: self._update_source_color("tencent", "ok"))
                    return
        except Exception:
            pass
        self.after(0, lambda: self._update_source_color("tencent", "off"))

    def _probe_cross(self):
        """快速检测新浪财经港美股连通性。"""
        try:
            from urllib.request import Request, urlopen
            req = Request("https://hq.sinajs.cn/list=gb_nvda", headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://finance.sina.com.cn",
            })
            with urlopen(req, timeout=6) as resp:
                if resp.status == 200 and b"," in resp.read()[:300]:
                    self.after(0, lambda: self._update_source_color("cross", "ok"))
                    return
        except Exception:
            pass
        self.after(0, lambda: self._update_source_color("cross", "off"))

    def _preload_names(self):
        """后台预拉取股票名称缓存（首次约80s，后续秒开）。"""
        try:
            from _plus_.stock_names import build_name_cache
            build_name_cache()
        except Exception:
            pass

    def _probe_news(self):
        """后台拉取市场新闻（板块动力 + 情绪模块共用）。"""
        try:
            from v14_news_provider import get_market_news
            self.news_items, _ = get_market_news(online=True)
            n = len(self.news_items) if self.news_items else 0
            if n > 0:
                self.after(0, lambda: self._update_source_color("news", "ok"))
                self.after(0, lambda: self.set_status(
                    f"已载入自选股 {len(self.data)} 只 | 新闻: {n}条"))
            else:
                self.after(0, lambda: self._update_source_color("news", "off"))
        except Exception:
            self.after(0, lambda: self._update_source_color("news", "off"))

    def _preload_sector(self, n_keys):
        """后台预拉取板块热度数据。"""
        try:
            from v14_deep_engine import get_sector_heat_map
            sector_map, source = get_sector_heat_map(online=True)
            self.sector_map = sector_map
            self.sector_source = source

            if "同花顺" in source or "在线" in source:
                self.after(0, lambda: self._update_source_color("sector", "ok"))
            else:
                self.after(0, lambda: self._update_source_color("sector", "stale"))

            n = len(sector_map)
            self.after(0, lambda: self.set_status(
                f"已载入自选股 {len(self.data)} 只 | "
                f"板块数据: {n}个 | "
                f"API Key: {n_keys}/3"
            ))
        except Exception:
            self.after(0, lambda: self._update_source_color("sector", "off"))
            self.after(0, lambda: self.set_status(
                f"已载入自选股 {len(self.data)} 只 | "
                f"板块离线 | API Key: {n_keys}/3"
            ))

    def setup_ui_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except:
            pass
        style.configure("TFrame", background="#eef2f6")
        style.configure("TLabelframe", background="#eef2f6", bordercolor="#cbd5e1", relief="solid")
        style.configure("TLabelframe.Label", font=("微软雅黑", 10, "bold"), foreground="#1f2937", background="#eef2f6")
        style.configure("TButton", font=("微软雅黑", 10), padding=(8, 5))
        style.configure("TNotebook", background="#eef2f6", borderwidth=0)
        style.configure("TNotebook.Tab", font=("微软雅黑", 10), padding=(14, 7))

    def set_status(self, text):
        if hasattr(self, "status_var"):
            self.status_var.set(text)

    def load_stock_list(self):
        self.data = read_self_stock_with_name()
        self.filtered_data = list(self.data)
        if hasattr(self, "search_var"):
            self.search_var.set("")
        self.refresh_stock_listbox()
        self.set_status(f"已载入自选股 {len(self.data)} 只")
        # 后台补全新增股票的行业标签
        try:
            from v15_sector_fetcher import _sync_missing_thread, _load_industry_map
            imap = _load_industry_map()
            all_codes = [c for c, _ in self.data]
            missing = [c for c in all_codes if c not in imap]
            if missing:
                import threading
                threading.Thread(target=_sync_missing_thread, args=(missing,), daemon=True).start()
        except Exception:
            pass

    def refresh_stock_listbox(self):
        self.lb.delete(0, tk.END)
        for _, n in self.filtered_data:
            self.lb.insert(tk.END, n)

    def apply_stock_filter(self):
        keyword = self.search_var.get().strip()
        if not keyword:
            self.filtered_data = list(self.data)
        else:
            kw_lower = keyword.lower()
            # 尝试拼音首字母匹配
            try:
                from pypinyin import pinyin, Style
                def _initials(name):
                    """提取中文名的拼音首字母，如 陕国投A → sgt"""
                    py = pinyin(name, style=Style.FIRST_LETTER)
                    return "".join(p[0] for p in py).lower()
            except ImportError:
                def _initials(name):
                    return ""
            self.filtered_data = [
                item for item in self.data
                if (kw_lower in item[0].lower()                      # 代码匹配
                    or kw_lower in item[1].lower()                    # 名称匹配
                    or kw_lower in _initials(item[1].split(" ", 1)[-1] if " " in item[1] else item[1]))  # 首字母匹配
            ]
        self.refresh_stock_listbox()
        self.set_status(f"搜索结果：{len(self.filtered_data)} / {len(self.data)}")

    def clear_stock_search(self):
        self.search_var.set("")
        self.search_entry.focus_set()

    def get_selected(self):
        idx = self.lb.curselection()
        return self.filtered_data[idx[0]] if idx else None

    def configure_scan_color_tags(self):
        self.t1.tag_config("track_core", background="#ff8a80", foreground="#111111")
        self.t1.tag_config("track_focus", background="#ffcc80", foreground="#111111")
        self.t1.tag_config("track_watch", background="#fff59d", foreground="#111111")
        self.t1.tag_config("track_light", background="#e8f5c8", foreground="#111111")
        self.t1.tag_config("track_normal", background="#e6f7ff", foreground="#111111")
        self.t1.tag_config("track_risk", background="#eeeeee", foreground="#444444")
        # V15 专有颜色标签（更深色，突出择时信号）
        self.t1.tag_config("v15_strong_buy", background="#d32f2f", foreground="#ffffff")
        self.t1.tag_config("v15_buy", background="#ff6d00", foreground="#ffffff")
        self.t1.tag_config("v15_avoid", background="#bdbdbd", foreground="#222222")

    def get_tracking_style(self, score, hit_patterns, v15_level=None):
        # V15等级优先
        if v15_level == "强势":
            return "v15_strong_buy", "择时强势"
        if v15_level == "偏强":
            return "v15_buy", "择时偏强"
        if v15_level == "弱势":
            if score >= 55:
                return "v15_avoid", "择时弱势"
            return "track_risk", "风险观察"

        # 回退到形态评分系统
        shape_matches = get_shape_matches(hit_patterns)
        has_positive_shape = any(p in {
            "金针探底", "阳包阴", "放量长阳", "放量突破压力", "缩量回踩支撑",
            "量价底背离", "布林下轨支撑", "5日线金叉10日线", "5日线金叉20日线",
            "老鸭头形态"
        } for p in shape_matches)

        if score >= 80:
            return "track_core", "核心跟踪"
        if score >= 70:
            return "track_focus", "重点跟踪"
        if score >= 60 or (score >= 55 and has_positive_shape):
            return "track_watch", "值得跟踪"
        if score >= 55:
            return "track_light", "浅色观察"
        if score < 45:
            return "track_risk", "风险观察"
        return "track_normal", "普通观察"

    def insert_scan_line(self, text, score=None, hit_patterns=None, v15_level=None):
        if score is None or hit_patterns is None:
            self.t1.insert(tk.END, text)
            self.t1.see(tk.END)
            return
        tag, _ = self.get_tracking_style(score, hit_patterns, v15_level)
        self.t1.insert(tk.END, text, tag)
        self.t1.see(tk.END)

    @staticmethod
    def _safe_daily_chg(code, prev_close, today_row):
        """计算日涨跌幅，自动处理除权除息导致的不复权数据跳空缺口。

        不复权 .day 文件中，除权除息会导致相邻交易日收盘价出现超过涨跌停限制的
        虚假涨跌幅（如 -26%）。检测到这种情况时，用 (收盘-开盘)/开盘 作为当日
        实际涨跌幅的近似（开盘价接近交易所除权参考价）。

        Args:
            code: 股票代码（用于判断涨跌停限制）
            prev_close: 前一日收盘价（不复权）
            today_row: 当日行情 Series, 需含 开盘/收盘/date

        Returns:
            (chg_pct, is_exrights) — 涨跌幅百分比，是否除权除息日
        """
        today_close = today_row["收盘"]
        raw_chg = (today_close - prev_close) / prev_close * 100

        # 判断涨跌停限制：创业板/科创板 ±20%，主板 ±10%
        limit = 20 if str(code).startswith(("300", "688")) else 10

        # 如果涨跌幅明显超过涨跌停限制，必为除权除息
        if abs(raw_chg) > limit * 1.05:
            # 用开盘价作为除权参考价的近似
            adjusted = (today_close - today_row["开盘"]) / today_row["开盘"] * 100
            return adjusted, True

        return raw_chg, False

    def run_review(self):
        """复盘：选定历史日期，回溯当天的行情/情绪/信号/板块/次日验证。"""
        from tkinter.simpledialog import askstring
        today_str = datetime.now().strftime("%Y-%m-%d")
        date_str = askstring("复盘日期", f"请输入复盘日期 (YYYY-MM-DD)\n默认昨天", initialvalue="")
        if date_str is None:
            return  # 用户取消
        if not date_str.strip():
            # 默认昨天
            date_str = (datetime.now() - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        date_str = date_str.strip()

        try:
            target_date = pd.Timestamp(date_str)
        except Exception:
            messagebox.showerror("日期格式错误", f"无法解析日期: {date_str}")
            return

        self.notebook.select(self.review_tab)
        self.t_review.delete(1.0, tk.END)
        self.t_review.insert(tk.END, f"⏳ 复盘 {target_date.strftime('%Y-%m-%d')} 中...\n")
        self.set_status(f"复盘 {target_date.strftime('%Y-%m-%d')} ...")

        def worker():
            try:
                lines = []
                W = 66  # 输出宽度

                def _sep(title=""):
                    if title:
                        lines.append(f"\n{'═'*W}")
                        lines.append(f"  {title}")
                        lines.append(f"{'═'*W}")
                    else:
                        lines.append(f"{'─'*W}")

                _sep(f"复盘报告 — {target_date.strftime('%Y-%m-%d')}")

                # ── 模块1: 行情回顾 ──
                lines.append(f"\n  【一、行情回顾】")

                # 拉三大指数当天的涨跌
                avg_idx = 0.0
                idx_codes = [("999999", "上证指数"), ("399001", "深证成指"), ("399006", "创业板指")]
                idx_info = []
                for icode, iname in idx_codes:
                    try:
                        df_i = load_stock_tdx(icode)
                        if df_i is not None and len(df_i) >= 2:
                            # 找到目标日期那行
                            df_i = df_i[df_i["date"] <= target_date]
                            if len(df_i) >= 2:
                                today_row = df_i.iloc[-1]
                                prev_row = df_i.iloc[-2]
                                chg = (today_row["收盘"] - prev_row["收盘"]) / prev_row["收盘"] * 100
                                idx_info.append({
                                    "name": iname, "close": today_row["收盘"],
                                    "chg": chg, "vol": today_row["成交量"],
                                    "prev_close": prev_row["收盘"],
                                    "amplitude": (today_row["最高"] - today_row["最低"]) / prev_row["收盘"] * 100,
                                })
                    except Exception:
                        pass

                for ii in idx_info:
                    icon = "📈" if ii["chg"] > 0.5 else ("📉" if ii["chg"] < -0.5 else "📊")
                    lines.append(f"  {icon} {ii['name']}: {ii['close']:,.2f}  "
                                 f"{ii['chg']:+.2f}%  "
                                 f"振幅{ii['amplitude']:.1f}%  "
                                 f"昨收{ii['prev_close']:,.2f}")

                # 指数近5日简述
                if idx_info:
                    recent_trend = []
                    for ii in idx_info:
                        recent_trend.append(f"{ii['name']}{ii['chg']:+.1f}%")
                    lines.append(f"  指数当日: {'  '.join(recent_trend)}")
                    avg_idx = sum(i["chg"] for i in idx_info) / len(idx_info)
                    if avg_idx > 1:
                        lines.append(f"  市场当日: 🟢 普涨 (均值{avg_idx:+.2f}%)")
                    elif avg_idx < -1:
                        lines.append(f"  市场当日: 🔴 普跌 (均值{avg_idx:+.2f}%)")
                    else:
                        lines.append(f"  市场当日: ⚪ 窄幅震荡 (均值{avg_idx:+.2f}%)")

                # ── 预加载全部股票数据（截断到复盘日期）──
                lines.append(f"\n  ⏳ 加载自选股数据...")
                self.t_review.after(0, lambda: (self.t_review.delete(1.0, tk.END),
                           self.t_review.insert(tk.END, "\n".join(lines))))

                stock_data = []  # [(code, name, df_truncated, today_row, next_row)]
                all_loaded = 0
                for c, n in self.data:
                    try:
                        df = load_stock_tdx(c)
                        if df is None or len(df) < 60:
                            continue
                        # 截断到复盘日期（不含之后）
                        df_trunc = df[df["date"] <= target_date].copy()
                        if len(df_trunc) < 60:
                            continue
                        # 当天行
                        last_date_in_trunc = df_trunc["date"].iloc[-1]
                        if last_date_in_trunc < target_date - pd.Timedelta(days=5):
                            continue  # 最近5天没交易，跳过
                        today_row = df_trunc.iloc[-1]
                        # 次日行（真实日线中取下一根）
                        df_after = df[df["date"] > target_date]
                        next_row = df_after.iloc[0] if len(df_after) > 0 else None
                        stock_data.append((c, n, df_trunc, today_row, next_row))
                    except Exception:
                        pass
                    all_loaded += 1
                    if all_loaded % 80 == 0:
                        self.t_review.after(0, lambda p=all_loaded: self.t_review.insert(tk.END,
                            f"  已加载 {p}/{len(self.data)} 只...\n"))

                if not stock_data:
                    lines.append(f"  ❌ 未找到 {target_date.strftime('%Y-%m-%d')} 的有效数据")
                    self.t_review.after(0, lambda: (self.t_review.delete(1.0, tk.END),
                               self.t_review.insert(tk.END, "\n".join(lines))))
                    self.t_review.after(0, lambda: self.set_status("复盘失败：无有效数据"))
                    return

                lines.append(f"  有效股票: {len(stock_data)}/{len(self.data)} 只")

                # ── 模块2: A 股市场情绪 ──
                _sep("二、A 股市场情绪")

                from statistics import median
                all_chgs = []
                up_c, down_c, flat_c = 0, 0, 0
                for c, _, df_t, today_r, _ in stock_data:
                    if len(df_t) >= 2:
                        prev_c = df_t.iloc[-2]["收盘"]
                        chg, _ = self._safe_daily_chg(c, prev_c, today_r)
                        all_chgs.append(chg)
                        if chg > 0.1:
                            up_c += 1
                        elif chg < -0.1:
                            down_c += 1
                        else:
                            flat_c += 1

                total_n = up_c + down_c + flat_c
                up_pct = up_c / max(total_n, 1) * 100
                lines.append(f"  上涨: {up_c}只 ({up_pct:.0f}%)  |  "
                             f"下跌: {down_c}只 ({down_c/max(total_n,1)*100:.0f}%)  |  "
                             f"平盘: {flat_c}只")

                # 涨幅中位数
                med_chg = median(all_chgs) if all_chgs else 0
                lines.append(f"  涨幅中位数: {med_chg:+.2f}%"
                             f"{' ← 弱于均值，权重股拉盘' if med_chg < -1 and avg_idx > 0.5 else ''}"
                             f"{' ← 强于均值，广泛上涨' if med_chg > 0.5 else ''}")

                # 成交量变化
                avg_vol_ratio = 1.0
                vol_changes = []
                for _, _, df_t, today_r, _ in stock_data:
                    if len(df_t) >= 20:
                        ma20 = df_t["成交量"].iloc[-21:-1].mean()
                        today_vol = today_r["成交量"]
                        if ma20 > 0:
                            vol_changes.append(today_vol / ma20)
                if vol_changes:
                    avg_vol_ratio = sum(vol_changes) / len(vol_changes)
                    lines.append(f"  成交量比(20日均): {avg_vol_ratio:.2f}x"
                                 f"{' ← 明显放量，市场活跃' if avg_vol_ratio > 1.3 else ''}"
                                 f"{' ← 缩量，交投清淡' if avg_vol_ratio < 0.8 else ''}")

                # 波动率（ATR 归一化）
                avg_atr = 2.0
                atr_list = []
                for _, _, df_t, today_r, _ in stock_data:
                    if len(df_t) >= 20:
                        tr_sum = 0
                        for i in range(-20, 0):
                            h = df_t.iloc[i]["最高"]; l = df_t.iloc[i]["最低"]
                            prev_c = df_t.iloc[i-1]["收盘"] if i > -20 else df_t.iloc[i]["开盘"]
                            tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
                            tr_sum += tr
                        atr20 = tr_sum / 20
                        close = today_r["收盘"]
                        if close > 0:
                            atr_list.append(atr20 / close * 100)
                if atr_list:
                    avg_atr = sum(atr_list) / len(atr_list)
                    lines.append(f"  平均波动率(ATR%): {avg_atr:.2f}%"
                                 f"{' ← 高波动，恐慌或亢奋' if avg_atr > 4 else ''}"
                                 f"{' ← 低波动，稳态' if avg_atr < 2 else ''}")

                # 连涨连跌（用指数均值近似）
                if idx_info:
                    lines.append(f"  三大指数均值: {avg_idx:+.2f}%")

                # 综合情绪评分 0-100
                sentiment_base = 50
                if up_pct > 65:
                    sentiment_base += 15
                elif up_pct > 55:
                    sentiment_base += 8
                elif up_pct < 35:
                    sentiment_base -= 15
                elif up_pct < 45:
                    sentiment_base -= 8

                if avg_vol_ratio > 1.3:
                    sentiment_base += 10
                elif avg_vol_ratio < 0.8:
                    sentiment_base -= 5

                if avg_idx > 1:
                    sentiment_base += 10
                elif avg_idx < -1:
                    sentiment_base -= 10

                if med_chg > 1:
                    sentiment_base += 5
                elif med_chg < -1:
                    sentiment_base -= 5

                sentiment = max(0, min(100, sentiment_base))
                if sentiment >= 75:
                    tag, tag_icon = "极热 🔥", "🔥"
                elif sentiment >= 60:
                    tag, tag_icon = "偏热 ☀️", "☀️"
                elif sentiment >= 45:
                    tag, tag_icon = "中性 🌤️", "🌤️"
                elif sentiment >= 30:
                    tag, tag_icon = "偏冷 ❄️", "❄️"
                else:
                    tag, tag_icon = "冰点 🧊", "🧊"

                lines.append(f"\n  综合情绪评分: {sentiment:.0f}/100 → {tag_icon} {tag}")
                lines.append(f"    (涨跌比{up_pct:.0f}% + 量比{avg_vol_ratio:.2f}x + 指数{avg_idx:+.1f}% + 中位数{med_chg:+.1f}%)")

                self.t_review.after(0, lambda: (self.t_review.delete(1.0, tk.END),
                           self.t_review.insert(tk.END, "\n".join(lines))))

                # ── 模块3: 自选股表现 ──
                _sep("三、自选股当日表现")

                # 按涨跌排序
                ranked = []  # (code, name, chg, price, vol, is_exrights)
                for c, n, df_t, today_r, next_r in stock_data:
                    if len(df_t) >= 2:
                        prev_c = df_t.iloc[-2]["收盘"]
                        chg, is_ex = self._safe_daily_chg(c, prev_c, today_r)
                        short_name = n.split(" ")[-1] if " " in n else n
                        ranked.append((c, short_name, chg, today_r["收盘"], today_r["成交量"], is_ex))
                ranked.sort(key=lambda x: x[2], reverse=True)

                lines.append(f"\n  🟢 Top 10 涨幅:")
                for i, (c, name, chg, price, vol, ex) in enumerate(ranked[:10], 1):
                    tag = " (除权)" if ex else ""
                    lines.append(f"    {i:>2}. {c} {name:<8}  {chg:>+6.2f}%{tag}  {price:.2f}")

                lines.append(f"\n  🔴 Bottom 10 跌幅:")
                for i, (c, name, chg, price, vol, ex) in enumerate(ranked[-10:][::-1], 1):
                    tag = " (除权)" if ex else ""
                    lines.append(f"    {i:>2}. {c} {name:<8}  {chg:>+6.2f}%{tag}  {price:.2f}")

                # ── 模块4: V15 信号回溯 ──
                _sep("四、V15 信号回溯")
                lines.append(f"  ⏳ 正在运行 V15 全链路 (使用截止到复盘日期的数据，无未来信息)...")
                self.t_review.after(0, lambda: (self.t_review.delete(1.0, tk.END),
                           self.t_review.insert(tk.END, "\n".join(lines))))

                # 预热行业标签
                try:
                    from v15_sector_fetcher import prefetch_tags
                    prefetch_tags([c for c,_,_,_,_ in stock_data])
                except Exception:
                    pass

                signal_results = []  # [(code, name, level, score, scenario, action, next_chg)]
                sig_done = 0
                import concurrent.futures

                def _review_one(c, n, df_t):
                    try:
                        v15 = compute_v15_pipeline(df_t, code=c, name=n, sector_map=self.sector_map)
                        if not v15.get("available"):
                            return None
                        return (c, n, v15["level"], v15["score"], v15["scenario"],
                                v15["position_advice"].get("action", "观望"))
                    except Exception:
                        return None

                need_run = [(c, n, df_t) for c, n, df_t, _, _ in stock_data]
                with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                    futs = {pool.submit(_review_one, c, n, d): c for c, n, d in need_run}
                    for f in concurrent.futures.as_completed(futs):
                        r = f.result()
                        sig_done += 1
                        if r:
                            signal_results.append(r)
                        if sig_done % 60 == 0:
                            self.t_review.after(0, lambda p=sig_done: self.t_review.insert(tk.END,
                                f"  V15 回溯 {p}/{len(need_run)}...\n"))

                signal_results.sort(key=lambda x: x[3], reverse=True)

                # 信号统计
                lvl_cnt = {}
                scn_cnt = {}
                for _, _, lv, _, sc, _ in signal_results:
                    lvl_cnt[lv] = lvl_cnt.get(lv, 0) + 1
                    scn_cnt[sc] = scn_cnt.get(sc, 0) + 1

                lines.append(f"\n  信号统计 (共{len(signal_results)}只有效):")
                lines.append(f"    等级: 强势={lvl_cnt.get('强势',0)}  "
                             f"偏强={lvl_cnt.get('偏强',0)}  "
                             f"中性={lvl_cnt.get('中性',0)}  "
                             f"弱势={lvl_cnt.get('弱势',0)}")
                lines.append(f"    场景: " + " | ".join(
                    f"{k}={v}" for k,v in sorted(scn_cnt.items(), key=lambda x: x[1], reverse=True)))

                buy_signals = [s for s in signal_results if s[2] in ("强势", "偏强")]
                if buy_signals:
                    lines.append(f"\n  买入信号详情:")
                    for c, n, lv, score, sc, action in buy_signals[:15]:
                        lines.append(f"    {c} {n.split(' ')[-1] if ' ' in n else n}  "
                                     f"V15{score:.0f}/{lv}  {sc}  → {action}")

                self.t_review.after(0, lambda: (self.t_review.delete(1.0, tk.END),
                           self.t_review.insert(tk.END, "\n".join(lines))))

                # ── 模块5: 板块回顾 ──
                _sep("五、板块回顾")

                # 按 EM 行业标签聚合当日涨跌
                try:
                    from v15_sector_fetcher import get_stock_industry
                except Exception:
                    get_stock_industry = lambda c: ""

                ind_chgs = {}  # industry -> [chg%...]
                for c, _, df_t, today_r, _ in stock_data:
                    if len(df_t) >= 2:
                        prev_c = df_t.iloc[-2]["收盘"]
                        chg, _ = self._safe_daily_chg(c, prev_c, today_r)
                        ind = get_stock_industry(c) or "其他"
                        ind_chgs.setdefault(ind, []).append(chg)

                ind_rank = []
                for ind, chgs in ind_chgs.items():
                    if len(chgs) >= 1:
                        avg = sum(chgs) / len(chgs)
                        ind_rank.append((ind, avg, len(chgs)))

                ind_rank.sort(key=lambda x: x[1], reverse=True)
                for i, (ind, avg, cnt) in enumerate(ind_rank[:15]):
                    icon = "🟢" if avg > 1 else ("🔴" if avg < -1 else "⚪")
                    lines.append(f"  {icon} {i+1:>2}. {ind:<12} 平均{avg:>+6.2f}%  ({cnt}只)")
                if len(ind_rank) > 15:
                    lines.append(f"  ... 共 {len(ind_rank)} 个行业")

                # ── 模块6: 次日验证 ──
                _sep("六、次日验证")
                lines.append(f"  对比 V15 买入信号 vs 次日实际走势")

                # 对每个买入信号找次日涨跌
                verified = []
                buy_set = {(s[0], s[1]) for s in buy_signals}
                for c, n, df_t, today_r, next_r in stock_data:
                    if (c, n) in buy_set and next_r is not None:
                        # 次日也可能有除权除息，用 safe 函数计算
                        next_chg, _ = self._safe_daily_chg(c, today_r["收盘"], next_r)
                        s = next((s for s in buy_signals if s[0] == c), None)
                        if s:
                            verified.append((*s, next_chg))

                hit_rate = 0
                if verified:
                    hit = sum(1 for v in verified if v[6] > 0)
                    miss = len(verified) - hit
                    hit_rate = hit / len(verified) * 100
                    avg_next = sum(v[6] for v in verified) / len(verified)

                    lines.append(f"\n  买入信号: {len(buy_signals)} 个")
                    lines.append(f"  有次日数据: {len(verified)} 个")
                    lines.append(f"  次日命中(涨): {hit} | 未命中(跌): {miss}")
                    lines.append(f"  命中率: {hit_rate:.0f}%")
                    lines.append(f"  次日平均涨跌: {avg_next:+.2f}%")

                    # 按次日涨跌排序
                    verified.sort(key=lambda x: x[6], reverse=True)
                    lines.append(f"\n  ✅ 次日涨幅 Top 5:")
                    for c, n, lv, score, sc, action, chg in verified[:5]:
                        lines.append(f"    {c} {n.split(' ')[-1] if ' ' in n else n}  "
                                     f"V15{score:.0f}/{lv}  → 次日{chg:+.2f}% ✅")

                    verified.sort(key=lambda x: x[6])
                    lines.append(f"\n  ❌ 次日跌幅 Top 5:")
                    for c, n, lv, score, sc, action, chg in verified[:5]:
                        lines.append(f"    {c} {n.split(' ')[-1] if ' ' in n else n}  "
                                     f"V15{score:.0f}/{lv}  → 次日{chg:+.2f}% ❌")
                else:
                    lines.append(f"\n  买入信号: {len(buy_signals)} 个")
                    lines.append(f"  ⚠️ 无次日验证数据 (复盘日期可能是最近一个交易日)")

                _sep()
                lines.append(f"  复盘完成 — {target_date.strftime('%Y-%m-%d')}")

                self.t_review.after(0, lambda: (self.t_review.delete(1.0, tk.END),
                           self.t_review.insert(tk.END, "\n".join(lines))))
                self.t_review.after(0, lambda: self.set_status(
                    f"复盘 {target_date.strftime('%m-%d')} 完成 | "
                    f"{len(stock_data)}只 | 买入{len(buy_signals)}个 | "
                    f"命中率{hit_rate:.0f}%" if verified else "无次日数据"))

            except Exception as e:
                import traceback
                traceback.print_exc()
                self.t_review.after(0, lambda: self.t_review.delete(1.0, tk.END))
                self.t_review.after(0, lambda e=e: self.t_review.insert(tk.END, f"❌ 复盘失败: {e}"))
                self.t_review.after(0, lambda: self.set_status("复盘失败"))

        threading.Thread(target=worker, daemon=True).start()

    def show_key_status(self):
        self.t1.delete(1.0, tk.END)
        self.t1.insert(tk.END, get_ai_key_status_text())
        self.t2.delete(1.0, tk.END)
        self.notebook.select(self.scan_tab)
        self.set_status("已显示AI Key读取状态")

    def prepare_stock_analysis(self, code, name_full):
        # 缓存命中时对比文件 mtime，.day 被外部更新则自动失效
        if code in self.stock_cache:
            cached = self.stock_cache[code]
            fp = get_tdx_file(code)
            try:
                cur_mtime = os.path.getmtime(fp)
                if cached.get("file_mtime", 0) == cur_mtime:
                    return True, ""
            except Exception:
                pass
        df = load_stock_tdx(code)
        if df is None or df.empty or len(df) < 120:
            return False, "数据不足"
        hit, _ = detect_all_patterns(df)
        rate = get_all_pattern_win_rate(df, hit)
        score, tag = calc_composite_score(hit, rate)
        sup = calc_support_resistance(df)
        txt = f"【{name_full}】\n综合评分：{score} | 趋势：{tag}\n识别形态：{hit}\n关键数据：{sup}"

        # Plus+ 全链路（含ATR T1、资金流、共振、情绪量化）
        try:
            from _plus_.plus_pipeline import compute_plus_pipeline
            v15_result = compute_plus_pipeline(df, code=code, name=name_full, sector_map=self.sector_map)
        except Exception:
            v15_result = compute_v15_pipeline(df, code=code, name=name_full, sector_map=self.sector_map)
        if v15_result.get("available"):
            v15_txt = (
                f"\n── V15择时雷达 ──\n"
                f"场景：{v15_result['scenario']}\n"
                f"过滤评分：{v15_result['score']:.1f} | 等级：{v15_result['level']}\n"
                f"操作建议：{v15_result['position_advice'].get('action', '观望')}\n"
                f"{v15_result['position_advice'].get('reason', '')}"
            )
            txt += v15_txt

        fp = get_tdx_file(code)
        f_mtime = 0
        try:
            f_mtime = os.path.getmtime(fp)
        except Exception:
            pass
        self.stock_cache[code] = {
            "base": txt,
            "info": (hit, rate, score, tag, sup, name_full.split()[-1]),
            "v15": v15_result,
            "df": df,
            "file_mtime": f_mtime,
        }
        return True, ""

    def show_timing_radar(self):
        """择时雷达信号看板 — 展示V15全链路评分、过滤、风控详情。"""
        self.notebook.select(self.radar_tab)
        self.t9.delete(1.0, tk.END)
        self.t9.insert(tk.END, "⏳ 正在加载择时雷达信号...")

        def worker():
            try:
                s = self.get_selected()
                if not s:
                    self.t9.after(0, lambda: self.t9.delete(1.0, tk.END))
                    self.t9.after(0, lambda: self.t9.insert(tk.END, "请先在左侧选择一只股票\n"))
                    return
                c, n_full = s
                ok, msg = self.prepare_stock_analysis(c, n_full)
                if not ok:
                    self.t9.after(0, lambda: self.t9.delete(1.0, tk.END))
                    self.t9.after(0, lambda m=msg: self.t9.insert(tk.END, f"❌ {m}"))
                    return

                info = self.stock_cache[c]
                v15 = info.get("v15", {})
                if not v15.get("available"):
                    self.t9.after(0, lambda: self.t9.delete(1.0, tk.END))
                    self.t9.after(0, lambda: self.t9.insert(tk.END, f"V15模块不可用，择时雷达无法运行。\n请检查 V15Alpha\\_internal\\ 目录是否存在。"))
                    return

                # ── 结论摘要 ──
                lines = []
                fs = v15["filtered_signal"]
                pa = v15["position_advice"]
                ss = v15["scenario_signal"]
                final_level = fs.get('level', '中性')
                final_score = fs.get('score', 50)
                scenario = v15.get('scenario', '无场景')
                raw_v15 = ss.get('raw_v15_score', 50)
                scenario_adj = ss.get('score', 50) - raw_v15
                filt_adj = fs.get('filter_adjustment', 0)
                extras = fs.get("extras", {})
                extra_adj = extras.get("adjustment", 0)

                # 扣分去重：filter_adjustment 中已包含 extra_adj
                # 拆开：纯过滤层 = filter_adjustment - extra_adj
                pure_filt_adj = filt_adj - extra_adj

                # 拆分多维度调整
                res = fs.get("resonance", {})
                res_score = res.get("resonance_score", raw_v15)
                vol = fs.get("volatility", {})
                vol_adj = vol.get("vol_adjustment", 0)
                div = fs.get("divergence", {})
                div_adj = div.get("adjustment", 0)
                sec = v15.get("sector", {})
                sec_adj = sec.get("adjustment", 0)

                level_icon = {"强势": "🟢", "偏强": "🔵", "中性": "🟡", "弱势": "🔴"}.get(final_level, "⚪")

                # ── 评分路径 ──
                parts = [f"四因子{raw_v15:.0f}"]
                if abs(scenario_adj) > 0.5:
                    parts.append(f"场景{scenario_adj:+.0f}")
                if abs(pure_filt_adj) > 0.5:
                    parts.append(f"共振/波动/背离/板块{pure_filt_adj:+.0f}")
                if abs(extra_adj) > 0.5:
                    parts.append(f"扩展维度{extra_adj:+.0f}")
                parts.append(f"= 最终{final_score:.0f}")
                score_path = " → ".join(parts)

                # ── 冲突检测 ──
                conflicts = []
                risk = extras.get("detail", {}).get("risk_reward", {})
                strength = extras.get("detail", {}).get("relative_strength", {})
                intraday = extras.get("detail", {}).get("intraday", {})
                cross = extras.get("detail", {}).get("cross_market", {})

                if risk.get("adjustment", 0) > 3 and final_level in ("弱势",):
                    conflicts.append("技术面信号偏空，但盈亏比空间充足，可设好止损轻仓观察")
                if strength.get("adjustment", 0) < -3 and risk.get("adjustment", 0) > 3:
                    conflicts.append("相对强度弱(跑输板块/大盘)，但赔率空间好——可能是超跌机会，需等企稳信号")
                if cross.get("adjustment", 0) < -3 and final_level in ("偏强", "强势"):
                    conflicts.append("港美股锚点偏空，与A股信号方向不一致，建议减仓参与")

                lines.append(f"{'='*60}")
                lines.append(f"  择时雷达 — {n_full}")
                lines.append(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
                lines.append(f"{'='*60}")

                lines.append(f"\n  {level_icon} 结论: {final_level} | 评分路径: {score_path}")

                if conflicts:
                    lines.append(f"  ⚠️ 注意: {'; '.join(conflicts)}")

                # ── 1. 场景检测 ──
                lines.append(f"\n【1/4 场景检测】")
                lines.append(f"  当前场景: {scenario}")
                lines.append(f"  V15默认评分(对照基准): {ss.get('raw_v15_score', 0):.1f}")
                lines.append(f"  场景调整评分: {ss.get('score', 0):.1f} → 等级: {ss.get('level', '中性')}")
                for f in ss.get("facts", []):
                    if f.startswith("场景"):
                        lines.append(f"  {f}")

                # ── 2. 子评分详情 ──
                lines.append(f"\n【2/4 因子评分】")
                lines.append(f"  反转评分 {ss.get('rev_score', 0):.1f} | "
                             f"MACD {ss.get('macd_score', 0):.1f} | "
                             f"量能 {ss.get('vol_score', 0):.1f} | "
                             f"结构 {ss.get('safe_score', 0):.1f}")
                for fact_line in ss.get("facts", []):
                    if not fact_line.startswith("场景"):
                        lines.append(f"  {fact_line}")

                # ── 3. 过滤引擎结果 ──
                lines.append(f"\n【3/4 信号过滤】")
                lines.append(f"  过滤评分: {fs.get('score', 0):.1f} | 等级: {fs.get('level', '中性')}")
                lines.append(f"  场景原始分: {fs.get('scenario_score', 0):.1f} → "
                             f"过滤调整: {fs.get('filter_adjustment', 0):+.1f}")

                res = fs.get("resonance", {})
                lines.append(f"  多周期共振: {res.get('consistency', 'N/A')}")
                period_scores = res.get("period_scores", {})
                if period_scores:
                    ps_str = " | ".join(f"{k}日前:{v:.0f}" for k, v in sorted(period_scores.items()))
                    lines.append(f"  各周期评分: {ps_str}")
                for fact_line in res.get("facts", []):
                    lines.append(f"  {fact_line}")

                vol = fs.get("volatility", {})
                lines.append(f"  波动率: ATR={vol.get('atr_norm', 0):.1f}% "
                             f"调整={vol.get('vol_adjustment', 0):+.0f}分")

                sec = v15.get("sector", {})
                if sec:
                    lines.append(f"  板块共振: {sec.get('sector', 'N/A')} "
                                 f"热度{sec.get('heat', 0):.0f} "
                                 f"扩散度{sec.get('breadth', 0):.0f} "
                                 f"调整{sec.get('adjustment', 0):+.0f}分")

                for fact_line in fs.get("facts", []):
                    lines.append(f"  {fact_line}")

                # ── 3.5. 扩展维度 ──
                extras = fs.get("extras", {})
                if extras.get("detail"):
                    lines.append(f"\n【3.5 扩展维度】调整: {extras.get('adjustment', 0):+.0f}分")
                    for fact_line in extras.get("detail", {}).get("relative_strength", {}).get("facts", []):
                        lines.append(f"  {fact_line}")
                    for fact_line in extras.get("detail", {}).get("risk_reward", {}).get("facts", []):
                        lines.append(f"  {fact_line}")
                    for fact_line in extras.get("detail", {}).get("intraday", {}).get("facts", []):
                        lines.append(f"  {fact_line}")
                    for fact_line in extras.get("detail", {}).get("cross_market", {}).get("facts", []):
                        lines.append(f"  {fact_line}")
                    for fact_line in extras.get("detail", {}).get("market_regime", {}).get("facts", []):
                        lines.append(f"  {fact_line}")

                # ── 3.8. Plus+ 增强 ──
                plus_info = v15.get("plus", {})
                plus_facts_list = plus_info.get("plus_facts", [])
                if plus_facts_list:
                    # 合计各模块调整值
                    mod_adjs = []
                    for key in ('fund_flow', 'atr_t1', 'multi_tf_resonance', 'sentiment_quant'):
                        m = plus_info.get(key, {})
                        if isinstance(m, dict) and m.get('adjustment', 0) != 0:
                            mod_adjs.append(m['adjustment'])
                    total_plus_adj = sum(mod_adjs)
                    if abs(total_plus_adj) > 0.1:
                        lines.append(f"\n【3.8 Plus+ 增强】调整: {total_plus_adj:+.0f}分")
                    for fact_line in plus_facts_list:
                        lines.append(f"  {fact_line}")

                # ── 4. 仓位建议 ──
                pa = v15["position_advice"]
                lines.append(f"\n【4/4 仓位与风控】")
                lines.append(f"  操作: {pa.get('action', '观望')}")
                lines.append(f"  理由: {pa.get('reason', '')}")
                lines.append(f"  仓位: {pa.get('position_suggestion', '不操作')}")

                if pa.get("stop_loss_price", 0) > 0:
                    lines.append(f"  止损价: {pa['stop_loss_price']:.2f} "
                                 f"({pa.get('stop_loss_pct', 0):+.1f}%)")
                    lines.append(f"  止盈一: {pa['take_profit_1']:.2f} "
                                 f"({pa.get('take_profit_1_pct', 0):+.1f}%)")
                    lines.append(f"  止盈二: {pa['take_profit_2']:.2f} "
                                 f"({pa.get('take_profit_2_pct', 0):+.1f}%)")

                if pa.get("time_stop_recommended"):
                    lines.append(f"  ⚠️ 时间止损触发: {pa.get('time_stop_reason', '')}")

                # ── 板块动力面板 ──
                try:
                    from _plus_.sector_power import compute_sector_power
                    sp = compute_sector_power(c, n_full, self.news_items)
                    if sp.get("sector_name") != "通用":
                        tag_icon = {"强顺风": "🔷🔷", "顺风": "🔷", "逆风": "🔶", "强逆风": "🔶🔶"}.get(sp.get("tag", ""), "")
                        t1 = sp.get("t1_score", 50)
                        t2 = sp.get("fund_score", 50)
                        mult = sp.get("position_mult", 1.0)
                        pwr = sp.get("sector_power", 50)
                        d = sp.get("t1_detail", {})
                        j = d.get("judgment", "—")
                        fund_label = sp.get("fund_label", "")
                        fund_vol_ratio = sp.get("fund_score", 50)  # we need vol_ratio separately
                        # Use fund label + vol_ratio from sector_power
                        # Actually fund_label already contains the description

                        lines.append(f"\n【板块动力】{sp['sector_name']} — {sp['tag']} {tag_icon} ({pwr:.0f}分)")
                        lines.append(f"  ┌─ 成交额占比(z-score):  T1 = {t1:>3.0f}分")
                        lines.append(f"  │    5日均 {d.get('r5',0):.2f}%  │  60日均 {d.get('mean_60',0):.2f}%  │  240日均 {d.get('mean_240',0):.2f}%")
                        lines.append(f"  │    z_60={d.get('z_60',0):+.2f}  z_240={d.get('z_240',0):+.2f}  →  {j}")
                        lines.append(f"  ├─ 自身量能(T2):          {t2:>3.0f}分  ({fund_label} v5/v20={sp.get('fund_vol_ratio', 1.0):.2f})")
                        lines.append(f"  ├─ 估值分位(赔率):        ×{mult:.2f}  (60日分位 {sp['position_pct']:.0f}%·{sp['position_signal']})")
                        lines.append(f"  └─ 得分公式: ({t1:.0f}×0.6 + {t2:.0f}×0.4) × {mult:.2f} = {pwr:.0f}分")
                        lines.append("")

                        # 解读文案，按判断类型区分
                        if j == "一致看强":
                            lines.append(f"  解读: 板块成交额占比在两个周期均处于高位，资金持续聚集。")
                            lines.append(f"        个股上涨有板块合力支撑，容错率较高。")
                        elif j == "一致看弱":
                            lines.append(f"  解读: 成交额占比处于历史低位，近期未见回暖，两个周期均判弱。")
                            lines.append(f"        板块整体缺钱，个股上涨缺乏合力支撑，需谨慎。")
                        elif j == "资金回流":
                            lines.append(f"  解读: 长期看板块偏弱，但近60天资金在持续流入(z_60={d.get('z_60',0):+.2f})。")
                            lines.append(f"        可能处于底部回暖阶段，可关注但需验证持续性。")
                        elif j == "资金流出":
                            lines.append(f"  解读: 长期看板块有基础，但近60天资金在流出(z_60={d.get('z_60',0):+.2f})。")
                            lines.append(f"        可能处于高位退潮，注意风险。")
                        else:
                            lines.append(f"  解读: 板块资金信号中性，无明显方向性特征。")
                except Exception:
                    pass

                # --- 存储雷达数据给 AI 按钮用 ---
                sp_data = {}
                try:
                    from _plus_.sector_power import compute_sector_power
                    _sp = compute_sector_power(c, n_full, getattr(self, "news_items", None))
                    if _sp.get("sector_name") != "通用":
                        sp_data = {"name": _sp.get("sector_name",""), "tag": _sp.get("tag",""),
                                    "score": _sp.get("sector_power",0), "t1": _sp.get("t1_score",0),
                                    "t2": _sp.get("fund_score",0), "mult": _sp.get("position_mult",1.0)}
                except Exception:
                    pass

                pa = v15["position_advice"]
                self._radar_data = {
                    "name": n_full, "code": c, "price": info.get("last_close", 0),
                    "v15_score": raw_v15, "v15_level": v15.get("v15_level", ""),
                    "rev_score": ss.get("rev_score", 0), "macd_score": ss.get("macd_score", 0),
                    "vol_score": ss.get("vol_score", 0), "safe_score": ss.get("safe_score", 0),
                    "scenario": scenario, "scenario_score": ss.get("score", 50),
                    "filtered_score": final_score, "filtered_level": final_level,
                    "filter_adj": filt_adj, "extras_adj": extra_adj,
                    "extras_detail": extras.get("detail", {}),
                    "plus_facts": plus_info.get("plus_facts", []),
                    "resonance_consistency": res.get("consistency", "-"),
                    "resonance_scores": res.get("period_scores", {}),
                    "divergence_type": div.get("type", "无"), "divergence_adj": div.get("adjustment", 0),
                    "atr_norm": vol.get("atr_norm", 0), "vol_adjustment": vol.get("vol_adjustment", 0),
                    "sector": {"sector_name": sec.get("sector",""), "sector_heat": sec.get("heat",0),
                               "sector_adj": sec.get("adjustment",0)},
                    "sector_power": sp_data,
                    "position": {"action": pa.get("action","观望"), "reason": pa.get("reason",""),
                                 "suggestion": pa.get("position_suggestion",""),
                                 "stop_loss": pa.get("stop_loss_price",0),
                                 "stop_loss_pct": pa.get("stop_loss_pct",0),
                                 "tp1": pa.get("take_profit_1",0), "tp2": pa.get("take_profit_2",0)},
                }
                self.t9.after(0, lambda: self.btn_ai_radar.config(state=tk.NORMAL))

                lines.append(f"\n{'='*60}")
                lines.append(f"  等级说明: 强势≥70 | 偏强≥55 | 中性≥45 | 弱势<45")
                lines.append(f"  数据源: 通达信本地日线 | V15Alpha全链路")
                lines.append(f"{'='*60}")

                self.t9.after(0, lambda: self.t9.delete(1.0, tk.END))
                self.t9.after(0, lambda l=lines: self.t9.insert(tk.END, "\n".join(l)))
                self.t9.after(0, lambda: self.t2.delete(1.0, tk.END))
                self.t9.after(0, lambda: self.notebook.select(self.radar_tab))
                self.t9.after(0, lambda n=n_full: self.set_status(f"择时雷达信号已更新：{n}"))

            except Exception as e:
                self.t9.after(0, lambda e=e: self.t9.insert(tk.END, f"❌ 择时雷达出错：{e}"))
                import traceback
                traceback.print_exc()

        threading.Thread(target=worker, daemon=True).start()

    def refresh_sector_data(self):
        """后台拉取板块数据并刷新热力图。"""
        def worker():
            self.after(0, lambda: self.set_status("正在拉取板块数据..."))
            try:
                from v14_deep_engine import get_sector_heat_map
                smap, source = get_sector_heat_map(online=True)
                self.sector_map = smap
                self.sector_source = source
                n = len(smap)
                self.after(0, lambda: self.set_status(f"板块数据: {n}个板块 ({source})"))
                if "同花顺" in source or "在线" in source:
                    self.after(0, lambda: self._update_source_color("sector", "ok"))
                else:
                    self.after(0, lambda: self._update_source_color("sector", "stale"))
                # 数据更新后自动刷新热力图
                self.after(0, self.show_sector_heatmap)
            except Exception:
                self.after(0, lambda: self.set_status("板块数据拉取失败"))
                self.after(0, lambda: self._update_source_color("sector", "error"))

        threading.Thread(target=worker, daemon=True).start()

    def _auto_upload_v15_cache(self):
        """板块资金流计算完成后自动上传到云端。"""
        import os, json, sys, threading
        try:
            from _plus_.sector_power import get_flow_chart_data, _compute_all_t1_scores
            _compute_all_t1_scores()
            chart = get_flow_chart_data()
            daily = chart.get("daily", [])
            sectors = []
            for item in daily:
                share, z_val, tdx_code, name, dz, prev = item
                # T2 量能数据
                t2 = {"t2_score": 50, "t2_label": "", "vol_ratio": 1.0, "trend_label": ""}
                try:
                    from _plus_.sector_power import get_sector_fund_score
                    fs = get_sector_fund_score(tdx_code)
                    t2 = {
                        "t2_score": round(fs.get("score", 50), 1),
                        "t2_label": fs.get("label", ""),
                        "vol_ratio": round(fs.get("vol_ratio", 1.0), 2),
                        "trend_label": fs.get("trend_label", ""),
                    }
                except:
                    pass
                sectors.append({
                    "name": name, "tdx_code": tdx_code, "share_pct": round(share, 2),
                    "z_score": round(z_val, 2), "dz": round(dz, 4), "prev_dz": round(prev, 4) if prev else 0,
                    "t2_score": t2["t2_score"], "t2_label": t2["t2_label"],
                    "vol_ratio": t2["vol_ratio"], "trend_label": t2["trend_label"],
                })
            sectors.sort(key=lambda x: -x["share_pct"])
            from datetime import datetime
            payload = {
                "date": datetime.now().strftime("%Y%m%d"),
                "time": datetime.now().strftime("%H:%M:%S"),
                "sectors": sectors, "total_count": len(sectors),
            }
            # SSH上传（密钥认证）
            import paramiko
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            cloud_host = os.environ.get("CLOUD_HOST", "")
            cloud_user = os.environ.get("CLOUD_USER", "ubuntu")
            remote_dir = os.environ.get("CLOUD_MARKETBOT_DIR", "/home/ubuntu/market-bot")
            ssh_key = os.path.expanduser(os.environ.get("CLOUD_SSH_KEY", "~/.ssh/id_ed25519"))
            client.connect(cloud_host, 22, cloud_user,
                           timeout=10, allow_agent=False, look_for_keys=False,
                           key_filename=ssh_key)
            sftp = client.open_sftp()
            remote_path = f"{remote_dir}/data/v15_{payload['date']}.json"
            with sftp.open(remote_path, "w") as f:
                f.write(json.dumps(payload, ensure_ascii=False))
            sftp.close()
            client.close()
            print(f"[V15云同步] 上传成功: {len(sectors)}个板块")
        except Exception as e:
            print(f"[V15云同步] 上传失败: {e}")

    def show_sector_flow_conclusion(self):
        """弹出板块资金流向结论窗口。"""
        from _plus_.sector_power import get_flow_chart_data

        tw = tk.Toplevel(self)
        tw.title("板块资金流向 · 结论")
        tw.geometry("520x500")
        tw.configure(bg="#fafafa")

        st = scrolledtext.ScrolledText(tw, font=("微软雅黑", 10),
                                        bg="#fafafa", relief=tk.FLAT, wrap=tk.WORD)
        st.pack(fill=tk.BOTH, expand=True, padx=12, pady=10)
        st.insert(tk.END, "正在生成结论...\n")

        def worker():
            stats = {"daily": "", "weekly": ""}
            for dim, label in [("daily", "日维度（近5日 vs 240日）"), ("weekly", "周维度（近2周 vs 48周）")]:
                data = get_flow_chart_data()
                items = data.get(dim, [])
                dz_list = data.get(f"{dim}_dz", [])

                if not items:
                    stats[dim] = f"{label}\n  暂无数据\n"
                    continue

                items_sorted = sorted(items, key=lambda x: -x[1])
                median_share = sorted([r[0] for r in items])[len(items)//2] if items else 1.0

                offense = []
                retreat = []
                warmup = []
                chill = []

                for share, z_val, code, name, dz, prev in items:
                    if z_val > 1.5:
                        if share > median_share:
                            offense.append((name, z_val, dz, share, prev, code))
                        else:
                            warmup.append((name, z_val, dz, share, prev, code))
                    elif z_val < -1.5:
                        if share > median_share:
                            retreat.append((name, z_val, dz, share, prev, code))
                        else:
                            chill.append((name, z_val, dz, share, prev, code))

                # 快速查 T2 趋势标签
                def _get_t2_trend(tdx_code):
                    try:
                        from _plus_.sector_power import get_sector_fund_score
                        fs = get_sector_fund_score(tdx_code)
                        return fs.get("trend_label", "")
                    except Exception:
                        return ""

                def _fmt_group(lines, items, max_n=5):
                    for name, z_val, dz, share, prev, code in items[:max_n]:
                        arrow = "↑加速" if dz > 0.3 else ("↓减速" if dz < -0.3 else "→平稳")
                        trend = _get_t2_trend(code)
                        lines.append(f"  {name} z={z_val:+.2f} {arrow}  占比 {prev:.2f}%→{share:.2f}% {trend}")

                buf = [f"━━━ {label} ━━━\n"]

                buf.append("\n🟢 主力进攻（大板块 + 钱涌入）：")
                if offense:
                    _fmt_group(buf, offense)
                else:
                    buf.append("  无")

                buf.append("\n\n🔴 主力撤退（大板块 + 钱流出）：")
                if retreat:
                    _fmt_group(buf, retreat)
                else:
                    buf.append("  无")

                buf.append("\n\n🟡 边缘升温（小板块 + 钱涌入）：")
                if warmup:
                    _fmt_group(buf, warmup)
                else:
                    buf.append("  无")

                buf.append("\n\n⚪ 边缘降温（小板块 + 钱流出）：")
                if chill:
                    _fmt_group(buf, chill)
                else:
                    buf.append("  无")

                buf.append("\n")
                stats[dim] = "".join(buf)

            st.after(0, lambda: (
                st.delete(1.0, tk.END),
                st.insert(tk.END, stats["daily"] + "\n" + stats["weekly"]),
                st.insert(tk.END, "\n━━━━━━━━━━━━━━━━\n")
            ))

        threading.Thread(target=worker, daemon=True).start()

    def show_sector_flow(self):
        """板块资金流向 · 双维度散点图 + Δz 异常名单。"""
        self.notebook.select(self.flow_tab)
        for w in self.flow_chart_frm.winfo_children():
            w.destroy()
        self.t_flow_daily.delete(1.0, tk.END)
        self.t_flow_daily.insert(tk.END, "⏳ 计算中...\n")
        self.t_flow_weekly.delete(1.0, tk.END)
        self.t_flow_weekly.insert(tk.END, "⏳ 计算中...\n")
        self.set_status("板块资金流向: 计算中...")

        def worker():
            try:
                import matplotlib
                matplotlib.use('TkAgg')
                import matplotlib.pyplot as plt
                from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
                import numpy as np
                from _plus_ import sector_power
                sector_power._flow_chart_cache = None  # 强制刷新
                from _plus_.sector_power import get_flow_chart_data

                plt.rcParams['font.sans-serif'] = ['Microsoft YaHei']
                plt.rcParams['axes.unicode_minus'] = False

                data = get_flow_chart_data()
                daily = data.get("daily", [])
                weekly = data.get("weekly", [])
                daily_dz = sorted(data.get("daily_dz", []), key=lambda x: -x[2])
                weekly_dz = sorted(data.get("weekly_dz", []), key=lambda x: -x[2])

                fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 6.5), dpi=100)

                def draw_scatter(ax, dataset, title):
                    if not dataset:
                        ax.text(0.5, 0.5, '暂无数据', ha='center', va='center',
                                transform=ax.transAxes, fontsize=16, color='#999')
                        ax.set_title(title, fontsize=12, fontweight='bold')
                        return
                    xs = [r[0] for r in dataset]
                    ys = [r[1] for r in dataset]
                    dzs = [r[4] for r in dataset]
                    names = [r[3] for r in dataset]
                    sizes = [max(25, min(180, (abs(y) + 1) * 45)) for y in ys]
                    colors = ['#c62828' if y > 0 else '#1565c0' for y in ys]

                    x_mid = np.median(xs)
                    x_max_val = max(xs) * 1.05
                    y_max_val = max(ys) * 1.15
                    y_min_val = min(ys) * 1.15

                    ax.axvspan(x_mid, x_max_val, ymin=0.5, ymax=1.0, alpha=0.06, color='green')
                    ax.axvspan(0, x_mid, ymin=0.5, ymax=1.0, alpha=0.04, color='orange')
                    ax.axvspan(0, x_mid, ymin=0.0, ymax=0.5, alpha=0.02, color='gray')
                    ax.axvspan(x_mid, x_max_val, ymin=0.0, ymax=0.5, alpha=0.04, color='red')

                    ax.axhline(0, color='#666', ls='--', lw=0.6, alpha=0.4)
                    ax.axvline(x_mid, color='#666', ls='--', lw=0.6, alpha=0.4)
                    ax.scatter(xs, ys, c=colors, s=sizes, alpha=0.75, edgecolors='white',
                              linewidth=0.5, zorder=3)

                    for x, y, name, dz in zip(xs, ys, names, dzs):
                        if abs(y) > 1.8:
                            arrow = '↑' if dz > 0.3 else ('↓' if dz < -0.3 else '→')
                            ax.annotate(f'{name}{arrow}', (x, y), fontsize=7, alpha=0.9,
                                       textcoords='offset points', xytext=(4, 4), zorder=4)

                    ax.set_xlabel('绝对占比 (%)', fontsize=10)
                    ax.set_ylabel('Z-score', fontsize=10)
                    ax.set_title(title, fontsize=12, fontweight='bold')
                    ax.grid(True, alpha=0.15, zorder=0)
                    ax.text(x_max_val, y_max_val, '主力进攻', fontsize=9, ha='right', va='top',
                           color='green', fontweight='bold')
                    ax.text(0.02, y_max_val, '边缘升温', fontsize=9, ha='left', va='top',
                           color='orange', fontweight='bold')
                    ax.text(0.02, y_min_val, '无人区', fontsize=9, ha='left', va='bottom', color='#999')
                    ax.text(x_max_val, y_min_val, '主力撤退', fontsize=9, ha='right', va='bottom',
                           color='red', fontweight='bold')

                draw_scatter(ax1, daily, '日维度（近5日 vs 240日历史）')
                draw_scatter(ax2, weekly, '周维度（近2周 vs 48周历史）')
                plt.tight_layout()

                def embed():
                    canvas = FigureCanvasTkAgg(fig, master=self.flow_chart_frm)
                    canvas.draw()
                    canvas.get_tk_widget().pack(fill=tk.X)
                    self.set_status("板块资金流向: 已更新")
                    import threading as _th
                    _th.Thread(target=self._auto_upload_v15_cache, daemon=True).start()

                self.flow_chart_frm.after(0, embed)

                def _fmt_list(items):
                    if not items:
                        return "暂无异常板块\n"
                    buf = ["板块\tz\tΔz\t当前\t前均\t方向\n"]
                    buf.append("──\t──\t──\t──\t──\t──\n")
                    for name, z, dz, curr, prev in items:
                        d = '↑流入加快' if dz > 0 else '↓流出加快'
                        buf.append(f"{name}\t{z:+.2f}\t{dz:+.2f}\t{curr:.2f}%\t{prev:.2f}%\t{d}\n")
                    return ''.join(buf)

                self.t_flow_daily.after(0, lambda: (
                    self.t_flow_daily.delete(1.0, tk.END),
                    self.t_flow_daily.insert(tk.END, _fmt_list(daily_dz)),
                    self.t_flow_daily.tag_configure("flow_tab",
                        tabs=("4c", "7c", "10c", "13c", "16c"),
                        font=("微软雅黑", 9)),
                    self.t_flow_daily.tag_add("flow_tab", "1.0", "end")))
                self.t_flow_weekly.after(0, lambda: (
                    self.t_flow_weekly.delete(1.0, tk.END),
                    self.t_flow_weekly.insert(tk.END, _fmt_list(weekly_dz)),
                    self.t_flow_weekly.tag_configure("flow_tab",
                        tabs=("4c", "7c", "10c", "13c", "16c"),
                        font=("微软雅黑", 9)),
                    self.t_flow_weekly.tag_add("flow_tab", "1.0", "end")))

            except Exception as e:
                import traceback
                _err = f"❌ 绘图失败: {e}\n{traceback.format_exc()}"
                self.t_flow_daily.after(0, lambda m=_err: (
                    self.t_flow_daily.delete(1.0, tk.END),
                    self.t_flow_daily.insert(tk.END, m)))

        threading.Thread(target=worker, daemon=True).start()

    def clear_cache(self):
        self.stock_cache = {}
        self.t1.delete(1.0, tk.END)
        self.t2.delete(1.0, tk.END)
        if self.canvas:
            try:
                self.canvas.get_tk_widget().destroy()
            except:
                pass
            self.canvas = None
        self.t1.insert(tk.END, "✅ 缓存已清空")
        self.set_status("缓存已清空")

    def _copy_verify_result(self):
        """复制验证结果到剪贴板（跳过⏳提示行）。"""
        content = self.verify_output.get(1.0, tk.END).strip()
        if not content:
            self.set_status("无验证结果可复制")
            return
        # 跳过第一行"⏳ 验证中..."
        lines = content.split("\n")
        if lines and "⏳" in lines[0]:
            content = "\n".join(lines[1:]).strip()
        if not content:
            self.set_status("无验证结果可复制")
            return
        self.clipboard_clear()
        self.clipboard_append(content)
        self.set_status("验证结果已复制到剪贴板")

    def verify_signal_list(self):
        """信号验证：粘贴代码+名称列表，用Plus+全链路（含板块动力）评分。"""
        raw = self.verify_input.get(1.0, tk.END).strip()
        if not raw:
            self.verify_output.delete(1.0, tk.END)
            self.verify_output.insert(tk.END, "请先粘贴代码和名称，每行一个。\n")
            return
        codes = []
        for line in raw.split("\n"):
            line = line.strip()
            if not line:
                continue
            parts = line.split(None, 1)
            code = parts[0] if parts else ""
            name = parts[1] if len(parts) > 1 else code
            if code.isdigit() and len(code) == 6:
                codes.append((code, name))
        if not codes:
            self.verify_output.delete(1.0, tk.END)
            self.verify_output.insert(tk.END, "未识别到有效股票代码。\n")
            return

        self.verify_output.delete(1.0, tk.END)
        self.verify_output.insert(tk.END, f"⏳ 验证中（{len(codes)}只）...\n")
        self.set_status(f"信号验证: {len(codes)}只")

        def _put(text, tag=None):
            self.verify_output.after(0, lambda t=text, tg=tag: self.verify_output.insert(tk.END, t, tg))

        def _init_tags():
            self.verify_output.tag_configure("vfy_hdr", font=("微软雅黑", 9, "bold"),
                                              foreground="#1a1a2e", background="#e8edf3")
            self.verify_output.tag_configure("vfy_buy", font=("微软雅黑", 9),
                                              foreground="#c62828", background="#fff5f5")
            self.verify_output.tag_configure("vfy_attn", font=("微软雅黑", 9),
                                              foreground="#e65100", background="#fff8f0")
            self.verify_output.tag_configure("vfy_avoid", font=("微软雅黑", 9),
                                              foreground="#757575")
            self.verify_output.tag_configure("vfy_normal", font=("微软雅黑", 9))
        self.verify_output.after(0, _init_tags)

        def _header():
            return "代码\t名称\tV15分\t等级\t场景\t板块\t板块动力\t标签\n" \
                   "──\t──\t──\t──\t──\t───\t───\t───\n"

        def worker():
            try:
                # 强制清 API 缓存 + 预热行业标签（与信号看板完全一致）
                try:
                    import v15_sector_fetcher
                    v15_sector_fetcher._cache_time = 0
                    from v15_sector_fetcher import prefetch_tags
                    prefetch_tags([c for c, _ in codes])
                    from _plus_.sector_power import precompute_all_sector_powers
                    precompute_all_sector_powers(self.news_items)
                except Exception:
                    pass

                results = []
                from _plus_.plus_pipeline import compute_plus_pipeline
                from _plus_.sector_power import compute_sector_power_cached

                for code, name in codes:
                    df = load_stock_tdx(code)
                    if df is None or len(df) < 120:
                        continue
                    try:
                        v15 = compute_plus_pipeline(df, code=code, name=name,
                                                     sector_map=self.sector_map,
                                                     news_items=self.news_items)
                    except Exception:
                        continue
                    if not v15.get("available"):
                        continue
                    sp = {}
                    try:
                        sp = compute_sector_power_cached(code, name, self.news_items)
                    except Exception:
                        pass
                    results.append((code, name, v15, sp))

                results.sort(key=lambda x: -x[2]["score"])

                _put(_header(), "vfy_hdr")
                for code, name, v15, sp in results:
                    score = v15["score"]
                    level = v15["level"]
                    scenario = v15.get("scenario", "")
                    sp_label = sp.get("label", "—") if sp else "—"
                    sp_score = f"{sp['sector_power']:.0f}" if sp and sp.get("sector_name") != "通用" else "—"
                    sp_name = sp.get("sector_name", "—") if sp else "—"
                    sp_tag = sp.get("tag", "中性") if sp else "中性"

                    row = f"{code}\t{name}\t{score:.0f}\t{level}\t{scenario}\t{sp_name}\t{sp_label}\t{sp_tag}\n"
                    if level in ("强势", "偏强"):
                        tag = "vfy_buy"
                    elif level == "中性":
                        tag = "vfy_attn"
                    elif level == "弱势":
                        tag = "vfy_avoid"
                    else:
                        tag = "vfy_normal"
                    _put(row, tag)

                _put(f"\n共 {len(results)} 只有效\n", "vfy_normal")
                self.verify_output.after(0, lambda: self.set_status(
                    f"信号验证完成: {len(results)}/{len(codes)} 只"))

            except Exception as e:
                import traceback
                _put(f"❌ 验证出错: {e}\n{traceback.format_exc()}\n", "vfy_avoid")

        threading.Thread(target=worker, daemon=True).start()

    def batch_scan(self):
        self.notebook.select(self.scan_tab)
        def worker():
            try:
                scan_rows = []
                self.t1.after(0, lambda: self.set_status("批量扫描运行中（全A股，V15全链路，8线程并行）..."))
                self.t1.after(0, lambda: self.t1.delete(1.0, tk.END))
                self.t1.after(0, lambda: self.t1.insert(
                    tk.END,
                    "===== 批量扫描（全A股 · V15全链路，8线程并行）=====\n"
                    "等级: 🟢强势≥70 / 🔵偏强≥55 / 🟡中性≥45 / 🔴弱势<45\n"
                    "注: 等级可能因共振/波动/背离等定性信号降级，会标注降级原因\n\n"
                ))

                # --- 读取自选股代码（self_stock.txt）---
                import os as _os
                self_stock_file = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "self_stock.txt")
                deduped_data = []
                if _os.path.exists(self_stock_file):
                    with open(self_stock_file, "r", encoding="utf-8") as _sf:
                        for _line in _sf:
                            _line = _line.strip()
                            if _line:
                                _parts = _line.split(" ", 1)
                                _code = _parts[0].strip()
                                _name = _parts[1].strip() if len(_parts) > 1 else _code
                                deduped_data.append((_code, _name))
                total_count = len(deduped_data)
                def scan_one(code, name_full):
                    """扫描单只股票 — 纯V15全链路 + 扩展维度。"""
                    try:
                        df = load_stock_tdx(code)
                        if df is None or df.empty or len(df) < 120:
                            return {
                                "code": code, "name": name_full,
                                "status": "no_data", "track_label": "数据不足",
                                "score": 0, "v15_level": "数据不足",
                                "v15_score": 0, "v15_scenario": "无场景",
                                "text": f"{name_full}：数据不足\n"
                            }

                        v15 = compute_v15_pipeline(df, code=code, name=name_full,
                                                   sector_map=self.sector_map)
                        if not v15.get("available"):
                            return {
                                "code": code, "name": name_full,
                                "status": "no_data", "track_label": "不可用",
                                "score": 0, "v15_level": "不可用",
                                "v15_score": 0, "v15_scenario": "无场景",
                                "text": f"{name_full}：V15不可用\n"
                            }

                        v15_level = v15["level"]
                        v15_score = v15["score"]
                        v15_scenario = v15["scenario"]
                        v15_action = v15["position_advice"].get("action", "观望")
                        v15_sector = v15.get("sector", {})

                        # ── ATR T1 折扣 ──
                        try:
                            fs_v = v15.get("filtered_signal", {})
                            vol_v = fs_v.get("volatility", {})
                            atr_v = vol_v.get("atr_norm", 0)
                            if atr_v > 3.0:
                                from _plus_.sector_power import _get_t1_score, _em_industry_to_tdx
                                from v15_sector_fetcher import match_sector, get_stock_industry
                                ind_v = match_sector(code, name_full) or ""
                                if not ind_v or ind_v == "通用":
                                    ind_v = get_stock_industry(code) or ""
                                tdx_v = _em_industry_to_tdx(ind_v)
                                t1v = _get_t1_score(tdx_v) if tdx_v else 50
                                if t1v >= 70:
                                    disc_v = 0.3
                                elif t1v >= 50:
                                    disc_v = 0.6
                                elif t1v >= 30:
                                    disc_v = 1.0
                                else:
                                    disc_v = 1.2
                                orig_v = -18 if atr_v > 4.0 else -8
                                delta_v = orig_v * disc_v - orig_v
                                if abs(delta_v) >= 2:
                                    v15_score += delta_v
                                    v15_score = min(100, max(0, v15_score))
                                    from v15_scenario_engine import LEVEL_THRESHOLDS as _LT
                                    v15_level = "弱势"
                                    for th, lv in _LT:
                                        if v15_score >= th:
                                            v15_level = lv
                                            break
                        except Exception:
                            pass

                        # 扩展维度
                        extras = v15.get("filtered_signal", {}).get("extras", {})
                        extra_adj = extras.get("adjustment", 0)
                        extra_flag = ""
                        if extra_adj >= 3:
                            extra_flag = " +"
                        elif extra_adj <= -3:
                            extra_flag = " -"

                        # 板块标签
                        sector_tag = ""
                        if v15_sector:
                            s_heat = v15_sector.get("heat", 0)
                            s_name = v15_sector.get("sector", "")
                            if s_heat >= 60:
                                sector_tag = f" 🔥{s_name}"
                            elif s_name:
                                sector_tag = f" {s_name}"

                        # 跟踪标签（纯V15）
                        if v15_level == "强势":
                            track_label = "择时强势"
                        elif v15_level == "偏强":
                            track_label = "择时偏强"
                        elif v15_level == "弱势":
                            track_label = "择时弱势"
                        else:
                            track_label = "观察"

                        # 检测等级降级：分数对应的自然等级 vs 实际等级
                        score_level = "弱势"
                        for threshold, lvl in [(70, "强势"), (55, "偏强"), (45, "中性")]:
                            if v15_score >= threshold:
                                score_level = lvl
                                break
                        downgrade_note = ""
                        if score_level != v15_level:
                            downgrade_note = f" ⚠{score_level}→{v15_level}"

                        text = (
                            f"■ {track_label} | {name_full} | "
                            f"V15{v15_score:.0f}分/{v15_level}{downgrade_note} | "
                            f"场景:{v15_scenario}{extra_flag} | "
                            f"操作:{v15_action}{sector_tag}\n"
                        )

                        return {
                            "code": code, "name": name_full, "status": "ok",
                            "score": v15_score, "track_label": track_label,
                            "v15_level": v15_level, "v15_score": v15_score,
                            "v15_scenario": v15_scenario,
                            "downgrade_note": downgrade_note,
                            "hit_patterns": [],
                            "text": text
                        }
                    except Exception as e:
                        return {
                            "code": code, "name": name_full,
                            "status": "no_data", "track_label": "数据不足",
                            "score": 0, "v15_level": "数据不足",
                            "v15_score": 0, "v15_scenario": "无场景",
                            "text": f"{name_full}：出错 {e}\n"
                        }

                with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                    futures = {pool.submit(scan_one, c, n): (c, n) for c, n in deduped_data}
                    for future in concurrent.futures.as_completed(futures):
                        result = future.result()
                        scan_rows.append(result)
                        self.t1.after(0, lambda r=result: self.insert_scan_line(
                            r.get("text", ""), r.get("score"),
                            r.get("hit_patterns"), r.get("v15_level")))

                self.batch_scan_rows = scan_rows
                self.t1.after(0, lambda: self.t1.insert(tk.END, "\n✅ 批量扫描完成！\n"))
                self.t1.after(0, lambda: self.set_status(f"批量扫描完成：{len(scan_rows)} 只"))
                self._after_scan_ai_report()
            except Exception as e:
                self.t1.after(0, lambda e=e: self.t1.insert(tk.END, f"\n❌ 错误：{e}"))
                self.t1.after(0, lambda: self.set_status("批量扫描出错"))

        threading.Thread(target=worker, daemon=True).start()

    def export_batch_scan_result(self):
        """导出批量扫描结果为 Excel。"""
        self.notebook.select(self.scan_tab)
        try:
            if not self.batch_scan_rows:
                self.t1.insert(tk.END, "\n请先点击批量扫描，生成结果后再导出。\n")
                self.t1.see(tk.END)
                return
            import pandas as pd
            rows = []
            for r in self.batch_scan_rows:
                if r.get("status") != "ok":
                    continue
                rows.append({
                    "代码": r["code"],
                    "名称": r["name"],
                    "等级": r.get("v15_level", ""),
                    "V15分": r.get("v15_score", 0),
                    "场景": r.get("v15_scenario", ""),
                    "降级": r.get("downgrade_note", ""),
                    "跟踪标签": r.get("track_label", ""),
                })
            df = pd.DataFrame(rows)
            filename = "批量扫描_" + datetime.now().strftime('%Y%m%d_%H%M%S') + ".xlsx"
            filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", filename)
            df.to_excel(filepath, index=False)
            self.t1.insert(tk.END, "\n批量扫描结果已导出：" + filename + " (" + str(len(rows)) + " 只)\n")
            self.t1.see(tk.END)
            self.set_status("已导出批量扫描：" + filename)
        except ImportError:
            self.t1.insert(tk.END, "\n导出失败：需要安装 pandas 和 openpyxl\n")
        except Exception as e:
            self.t1.insert(tk.END, "\n导出扫描结果失败：" + str(e) + "\n")
    def _after_scan_ai_report(self):
        from scripts.ai_scan_reporter import run_ai_report
        run_ai_report(self.t1, self, getattr(self, "batch_scan_rows", []))

    def refresh_intraday_panel(self):
        """单股分时详情：VWAP + 全日位置 + 趋势 + 进场建议。与信号看板表格逻辑一致。
        可查看任意选中股票的分时，不限于中性以上等级。"""
        s = self.get_selected()
        if not s:
            self.intraday_status.config(text="请先在左侧选择一只股票", fg="#9E9E9E")
            return

        code, name_full = s
        self.intraday_status.config(text="⏳ 获取分时数据...", fg="#f59e0b")

        try:
            from v14_intraday import fetch_intraday_minutes, calc_intraday_strength

            df = fetch_intraday_minutes(code)
            if df is None or len(df) < 5:
                self.intraday_status.config(text="非交易时段或无分时数据", fg="#6b7280")
                return

            # 从日线取昨收（.day文件不含今天盘中数据，closes[-1]=最近完成交易日收盘）
            prev_close = None
            try:
                df_day = load_stock_tdx(code)
                if df_day is not None and len(df_day) >= 1:
                    col_c = '收盘' if '收盘' in df_day.columns else 'close'
                    prev_close = float(df_day[col_c].iloc[-1])
            except Exception:
                pass

            r = calc_intraday_strength(df, prev_close=prev_close)
            if not r.get("valid"):
                self.intraday_status.config(text="分时数据不足", fg="#f59e0b")
                return

            trend = r.get("trend", "震荡")
            pos = r.get("position_pct", 50)
            vwap_dev = r.get("vwap_dev_pct", 0)
            vol_30 = r.get("vol_ratio_30min", 1.0)
            change = r.get("change_pct", 0)
            score = r.get("strength_score", 50)
            tag = r.get("strength_tag", "?")

            # 与信号看板表格逻辑一致
            if vwap_dev < -1.5 and trend == "上升" and pos < 50:
                advice, color = f"低于均价{vwap_dev:+.1f}%且回升→可进", "#22c55e"
            elif vwap_dev < -0.5 and trend != "下降":
                advice, color = f"低于均价{vwap_dev:+.1f}%→等回升", "#3b82f6"
            elif vwap_dev > 2:
                advice, color = f"高于均价{vwap_dev:+.1f}%→不追", "#ef4444"
            elif trend == "下降" and pos > 70:
                advice, color = f"高位{pos:.0f}%下跌→观望", "#f59e0b"
            else:
                advice, color = "均价附近→无信号", "#6b7280"

            # 尾盘量比仅在 14:30 后才叫"尾盘"，否则叫"近端量比"
            now_t = datetime.now()
            is_late = now_t.hour >= 14 and now_t.minute >= 30
            vol_label = "尾盘量比" if is_late else "近端量比"

            text = (f"较昨收:{change:+.2f}% | 强度:{tag}{score:.0f} | "
                    f"趋势:{trend} | 位置:{pos:.0f}% | "
                    f"均价偏离:{vwap_dev:+.2f}% | {vol_label}:{vol_30:.2f} | → {advice}")
            self.intraday_status.config(text=text, fg=color)
            self.set_status(f"分时: {name_full} {advice}")

        except ImportError:
            self.intraday_status.config(text="mootdx 未安装", fg="#ef4444")
        except Exception as e:
            self.intraday_status.config(text=f"获取失败: {e}", fg="#ef4444")

    def refresh_signal_board(self):
        """扫描全自选股池，展示 V15 评分 + 分时强度 + 进场建议。表格式对齐。"""
        def worker():
            try:
                self.t3.after(0, lambda: self.t3.delete(1.0, tk.END))
                self.t3.after(0, lambda: self.set_status("正在扫描实盘信号..."))

                if not self.data:
                    self.t3.after(0, lambda: self.t3.insert(tk.END, "⚠️ 自选股池为空\n"))
                    self.t3.after(0, lambda: self.set_status("自选股池为空"))
                    return

                # 先确保有板块数据
                if not self.sector_map:
                    self.t3.after(0, lambda: self.t3.insert(tk.END, "⏳ 正在拉取板块数据...\n"))
                    smap, src = get_sector_map(online=True)
                    self.sector_map = smap
                    self.sector_source = src

                # 预热 EM f100 行业标签（扫描前，让板块列显示）
                try:
                    from v15_sector_fetcher import prefetch_tags
                    self.t3.after(0, lambda: self.t3.insert(tk.END, "预热行业标签...\n"))
                    prefetch_tags([c for c, _ in self.data])
                except Exception:
                    pass

                # 预热板块动力缓存（17个板块各算一次，news_items 启动时已拉取）
                try:
                    from _plus_.sector_power import precompute_all_sector_powers
                    precompute_all_sector_powers(self.news_items)
                except Exception:
                    pass

                # ── 阶段1: V15 全链路扫描 ──
                results = []
                import concurrent.futures

                def scan_signal(code, name_full):
                    try:
                        df = load_stock_tdx(code)
                        if df is None or df.empty or len(df) < 120:
                            return {"code": code, "name": name_full, "ok": False}
                        # Plus+ 增强管线（资金流 + 原始V15）
                        from _plus_.plus_pipeline import compute_plus_pipeline
                        v15 = compute_plus_pipeline(df, code=code, name=name_full,
                                                    sector_map=self.sector_map,
                                                    news_items=self.news_items)
                        if not v15.get("available"):
                            return {"code": code, "name": name_full, "ok": False}

                        # 板块动力分（独立展示，不接入评分）
                        sector_power = {}
                        try:
                            from _plus_.sector_power import compute_sector_power_cached
                            sector_power = compute_sector_power_cached(
                                code, name_full, self.news_items)
                        except Exception:
                            pass

                        col_c = '收盘' if '收盘' in df.columns else 'close'
                        closes = [float(v) for v in df[col_c].values]
                        prev_close = closes[-1] if len(closes) >= 1 else None
                        return {
                            "code": code, "name": name_full, "ok": True,
                            "level": v15["level"], "score": v15["score"],
                            "scenario": v15["scenario"],
                            "action": v15["position_advice"].get("action", "观望"),
                            "sector": v15.get("sector", {}),
                            "sector_power": sector_power,
                            "prev_close": prev_close,
                            "plus": v15.get("plus", {}),
                        }
                    except Exception as e:
                        return {"code": code, "name": name_full, "ok": False, "error": str(e)}

                total_count = len(self.data)
                scanned_count = 0
                ok_count = 0

                self.t3.after(0, lambda: self.t3.insert(tk.END,
                    f"V15扫描中... 0/{total_count}\n"))

                with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                    fs = {pool.submit(scan_signal, c, n): (c, n) for c, n in self.data}
                    for f in concurrent.futures.as_completed(fs):
                        r = f.result()
                        scanned_count += 1
                        if r.get("ok"):
                            results.append(r)
                            ok_count += 1
                        # 每 30 只追加一行进度
                        if scanned_count % 30 == 0:
                            p = scanned_count; o = ok_count
                            self.t3.after(0, lambda p=p, o=o, t=total_count: self.t3.insert(tk.END,
                                f"  V15 {p}/{t} 完成 | 有效{o}只\n"))

                results.sort(key=lambda x: x.get("score", 0), reverse=True)

                # ── 阶段2: 分时数据（仅 >= 中性 的股票，交易时段才拉）──
                intraday_cache = {}  # code -> {strength_tag, score, advice}
                trade_session = False
                try:
                    from datetime import datetime as _dt
                    now = _dt.now()
                    if now.weekday() < 5:
                        t = now.hour * 60 + now.minute
                        if 9 * 60 + 28 <= t <= 15 * 60 + 10:
                            trade_session = True
                except Exception:
                    pass

                if trade_session:
                    need_intraday = [r for r in results
                                     if r.get("level") in ("强势", "偏强", "中性")]
                    if need_intraday:
                        self.t3.after(0, lambda t=len(need_intraday): self.t3.insert(tk.END,
                            f"⏳ 分时数据 0/{t}\n"))
                        intra_done = 0

                        def _fetch_one(r):
                            try:
                                code = r["code"]
                                from v14_intraday import fetch_intraday_minutes, calc_intraday_strength
                                df = fetch_intraday_minutes(code)
                                if df is None or len(df) < 5:
                                    return code, None
                                result = calc_intraday_strength(df, prev_close=r.get("prev_close"))
                                if not result.get("valid"):
                                    return code, None
                                # 进场建议
                                vd = result.get("vwap_dev_pct", 0)
                                tr = result.get("trend", "震荡")
                                ps = result.get("position_pct", 50)
                                st = result.get("strength_score", 50)
                                if vd < -1.5 and tr == "上升" and ps < 50:
                                    advice, adv_icon = f"低于均价{vd:+.1f}%且回升→可进", "★"
                                elif vd < -0.5 and tr != "下降":
                                    advice, adv_icon = f"低于均价{vd:+.1f}%→等回升", "▲"
                                elif vd > 2:
                                    advice, adv_icon = f"高于均价{vd:+.1f}%→不追", "▼"
                                elif tr == "下降" and ps > 70:
                                    advice, adv_icon = f"高位{ps:.0f}%下跌→观望", "▼"
                                else:
                                    advice, adv_icon = f"均价附近→无信号", "─"
                                return code, {
                                    "score": st,
                                    "tag": result.get("strength_tag", "?"),
                                    "vwap_dev": vd,
                                    "trend": tr,
                                    "advice": advice,
                                    "advice_icon": adv_icon,
                                }
                            except Exception:
                                return code, None

                        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                            futs = {pool.submit(_fetch_one, r): r["code"] for r in need_intraday}
                            for f in concurrent.futures.as_completed(futs):
                                code, data = f.result()
                                intra_done += 1
                                if data:
                                    intraday_cache[code] = data
                                if intra_done % 10 == 0:
                                    d = intra_done; t = len(need_intraday); v = len(intraday_cache)
                                    self.t3.after(0, lambda d=d, t=t, v=v: self.t3.insert(tk.END,
                                        f"  分时 {d}/{t} | 有效{v}\n"))

                # 保存数据供导出使用
                self._signal_results = list(results)

                # 板块动力快照（同日同股不重复）
                try:
                    from _plus_.sector_power import save_daily_snapshot
                    for r in results:
                        if r.get("ok") and r.get("sector_power"):
                            save_daily_snapshot(
                                r["code"], r["name"],
                                r.get("score", 50),
                                r["sector_power"])
                except Exception:
                    pass

                # 清空进度文本，准备输出最终报告
                self.t3.after(0, lambda: self.t3.delete(1.0, tk.END))

                # ── 构建输出 ──
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                def _put(text, tag=None):
                    self.t3.after(0, lambda t=text, tg=tag: self.t3.insert(tk.END, t, tg))

                # 统计
                level_counts = {}
                for r in results:
                    lv = r.get("level", "未知")
                    level_counts[lv] = level_counts.get(lv, 0) + 1

                scene_counts = {}
                action_counts = {}
                for r in results:
                    sc = r.get("scenario", "无场景")
                    scene_counts[sc] = scene_counts.get(sc, 0) + 1
                    ac = r.get("action", "观望")
                    action_counts[ac] = action_counts.get(ac, 0) + 1

                _put(f"  实盘信号看板  |  {now_str}  |  分时:{'实盘' if trade_session else '盘后'}\n")
                _put(f"  股票池 {len(results)}/{len(self.data)}只  |  "
                     f"强势{level_counts.get('强势',0)} 偏强{level_counts.get('偏强',0)} "
                     f"中性{level_counts.get('中性',0)} 弱势{level_counts.get('弱势',0)}\n\n")

                # ── 表格：用 tab 对齐 ──
                # 列: 代码 名称 V15分 等级 场景 板块 板块动力 标签 操作 分时强度 进场建议
                HEADER = ("代码\t名称\tV15分\t等级\t场景\t板块\t板块动力\t标签\t操作\t分时强度\t进场建议\n"
                          "──\t──\t──\t──\t──\t──\t───\t───\t──\t────\t────\n")
                TABS = ("1.8c", "3.8c", "5.8c", "7.2c", "8.8c", "10.5c", "12.2c", "13.8c", "15.8c", "17.5c", "19.5c")

                _apply_tabs = lambda: (
                    self.t3.tag_configure("tbl_header",
                        tabs=TABS, font=("微软雅黑", 9, "bold"),
                        foreground="#1a1a2e", background="#e8edf3"),
                    self.t3.tag_configure("tbl_buy",
                        tabs=TABS, font=("微软雅黑", 9),
                        foreground="#c62828", background="#fff5f5"),
                    self.t3.tag_configure("tbl_attn",
                        tabs=TABS, font=("微软雅黑", 9),
                        foreground="#e65100", background="#fff8f0"),
                    self.t3.tag_configure("tbl_avoid",
                        tabs=TABS, font=("微软雅黑", 9),
                        foreground="#757575"),
                    self.t3.tag_configure("tbl_normal",
                        tabs=TABS, font=("微软雅黑", 9)),
                    # 板块动力标签颜色（独立渲染）
                    self.t3.tag_configure("tbl_顺风",
                        tabs=TABS, font=("微软雅黑", 9, "bold"),
                        foreground="#c62828"),
                    self.t3.tag_configure("tbl_逆风",
                        tabs=TABS, font=("微软雅黑", 9, "bold"),
                        foreground="#1565c0"),
                    self.t3.tag_configure("tbl_中性",
                        tabs=TABS, font=("微软雅黑", 9),
                        foreground="#757575"),
                    # 分时强度颜色
                    self.t3.tag_configure("tbl_intra_up",
                        tabs=TABS, font=("微软雅黑", 9, "bold"),
                        foreground="#c62828"),
                    self.t3.tag_configure("tbl_intra_down",
                        tabs=TABS, font=("微软雅黑", 9, "bold"),
                        foreground="#1565c0"),
                    self.t3.tag_configure("tbl_intra_neutral",
                        tabs=TABS, font=("微软雅黑", 9),
                        foreground="#9e9e9e"),
                )
                self.t3.after(0, _apply_tabs)
                _put(HEADER, "tbl_header")

                for r in results:
                    sec = r.get("sector", {})
                    s_name = sec.get("sector", "") if sec else ""
                    s_tag = f"{s_name}" if s_name and s_name != "通用" else ""

                    # 分时数据
                    idata = intraday_cache.get(r["code"])
                    if idata:
                        itext = f"{idata['tag']}{idata['score']:.0f}"
                        adv_text = f"{idata['advice_icon']}{idata['advice']}"
                    else:
                        itext = "──"
                        adv_text = "──"

                    # 姓名只取核心（去板块后缀）
                    name = r["name"].split(" ")[-1] if " " in r["name"] else r["name"]
                    if len(name) > 6:
                        name = name[:6]

                    # 板块动力分（纯数字） + 独立标签列（红/蓝渲染）
                    sp = r.get("sector_power", {})
                    sp_score = f"{sp['sector_power']:.0f}" if sp and sp.get("sector_name") != "通用" else "—"
                    sp_tag_text = sp.get("tag", "中性") if sp else "中性"

                    # 标签列颜色
                    if "强顺风" == sp_tag_text:
                        sp_tag_color = "tbl_顺风"
                    elif "顺风" == sp_tag_text:
                        sp_tag_color = "tbl_顺风"
                    elif "强逆风" == sp_tag_text:
                        sp_tag_color = "tbl_逆风"
                    elif "逆风" == sp_tag_text:
                        sp_tag_color = "tbl_逆风"
                    else:
                        sp_tag_color = "tbl_中性"

                    # 分时颜色: 强/偏强=红色, 弱/偏弱=蓝色
                    if idata:
                        st_tag = idata.get("tag", "")
                        if st_tag in ("强势", "偏强"):
                            intra_tag = "tbl_intra_up"
                        elif st_tag in ("弱势", "偏弱"):
                            intra_tag = "tbl_intra_down"
                        else:
                            intra_tag = "tbl_intra_neutral"
                    else:
                        intra_tag = "tbl_intra_neutral"

                    prefix = (f"{r['code']}\t{name}\t{r['score']:.0f}\t{r['level']}\t"
                              f"{r['scenario']}\t{s_tag}\t{sp_score}\t")

                    lv = r["level"]
                    if lv in ("强势", "偏强"):
                        tag = "tbl_buy"
                    elif lv == "中性":
                        tag = "tbl_attn"
                    elif lv == "弱势":
                        tag = "tbl_avoid"
                    else:
                        tag = "tbl_normal"

                    _put(prefix, tag)
                    _put(f"{sp_tag_text}\t", sp_tag_color)
                    _put(f"{r['action']}\t", tag)
                    _put(f"{itext}\t", intra_tag)
                    _put(f"{adv_text}\n", intra_tag)

                _put(f"\n  等级: 强势≥70 | 偏强≥55 | 中性≥45 | 弱势<45  |  "
                     f"自动刷新: {'开' if self.signal_auto_refresh.get() else '关'}\n")

                # 优先关注详情
                strong = [r for r in results if r.get("level") in ("强势", "偏强")]
                if strong:
                    _put(f"\n  🔥 优先关注 ({len(strong)} 只):\n")
                    for r in strong[:10]:
                        sid = intraday_cache.get(r["code"])
                        i_str = ""
                        if sid:
                            i_str = f" | 分时:{sid['tag']}{sid['score']:.0f}→{sid['advice']}"
                        _put(f"  ● {r['name']}({r['code']}) V15{r['score']:.0f}/{r['level']} "
                             f"{r['scenario']}{i_str}\n")

                # Plus+: 记录信号到 SQLite + 回填昨日
                try:
                    from _plus_.signal_tracker import get_tracker
                    tracker = get_tracker()
                    today_str = datetime.now().strftime("%Y-%m-%d")
                    track_data = []
                    for r in results:
                        sec = r.get("sector", {})
                        s_name = sec.get("sector", "") if isinstance(sec, dict) else str(sec)
                        track_data.append({
                            "code": r["code"], "name": r["name"].split(" ")[-1] if " " in r["name"] else r["name"],
                            "level": r["level"], "score": r["score"],
                            "scenario": r["scenario"], "sector": s_name, "action": r["action"],
                        })
                    tracker.record_batch(track_data, today_str)
                    yesterday = (datetime.now() - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
                    tracker.update_next_day(yesterday)
                except Exception:
                    pass

                # Plus+: 信号质量统计
                stats_text = ""
                try:
                    from _plus_.signal_tracker import get_tracker
                    stats_text = get_tracker().get_latest_stats_text()
                except Exception:
                    pass
                if stats_text:
                    _put(f"\n  📊 {stats_text}\n")

                # Plus+: 组合层优化
                try:
                    from _plus_.portfolio_optimizer import optimize_portfolio
                    opt = optimize_portfolio(results)
                    if opt["summary"]:
                        _put(f"\n  ┌{'─'*60}\n")
                        _put(f"  │  组合层优化\n")
                        _put(f"  ├{'─'*60}\n")
                        _put(f"  │  {opt['summary']}\n")

                        # 止损/止盈
                        if opt["close_alerts"]:
                            _put(f"  │\n  │  ⛔ 触发预警:\n")
                            for a in opt["close_alerts"]:
                                _put(f"  │    {a['name']}({a['code']}) — {a['reason']}\n")

                        # 持仓诊断
                        alert_holds = [h for h in opt["hold_positions"] if h["alert"]]
                        if alert_holds:
                            _put(f"  │\n  │  📌 持仓关注:\n")
                            for h in alert_holds:
                                _put(f"  │    {h['name']}: {h['alert']}\n")

                        # 买入建议
                        if opt["buy_candidates"]:
                            _put(f"  │\n  │  🎯 买入候选 (按优先级):\n")
                            for idx, c in enumerate(opt["buy_candidates"], 1):
                                _put(f"  │    {idx}. {c['name']}({c['code']}) "
                                     f"{c['reason']}\n")
                        _put(f"  └{'─'*60}\n")
                except Exception:
                    pass

                self.t3.after(0, lambda: self.set_status(
                    f"实盘信号: {len(results)}只 | "
                    f"强势{level_counts.get('强势',0)} | "
                    f"偏强{level_counts.get('偏强',0)} | "
                    f"分时:{len(intraday_cache)}只有效"))

            except Exception as e:
                self.t3.after(0, lambda e=e: self.t3.insert(tk.END, f"\n❌ 实盘扫描失败: {e}"))
                import traceback
                traceback.print_exc()

        threading.Thread(target=worker, daemon=True).start()

    def export_signal_excel(self):
        """导出信号看板结果为 Excel。"""
        if not hasattr(self, '_signal_results') or not self._signal_results:
            self.set_status("无信号数据可导出，请先点击刷新")
            return
        results = self._signal_results
        try:
            import pandas as pd
            rows = []
            for r in results:
                sec = r.get("sector", {})
                s_tag = sec.get("sector", "") if sec else ""
                sp = r.get("sector_power", {})
                sp_score = sp.get("sector_power", "") if sp and sp.get("sector_name") != "通用" else ""
                sp_tag = sp.get("tag", "") if sp else ""
                rows.append({
                    "代码": r["code"],
                    "名称": r["name"].split(" ")[-1] if " " in r["name"] else r["name"],
                    "V15分": r["score"],
                    "等级": r["level"],
                    "场景": r["scenario"],
                    "行业": s_tag,
                    "板块动力": sp_score,
                    "标签": sp_tag,
                    "操作": r["action"],
                })
            df = pd.DataFrame(rows)
            from datetime import datetime
            filename = f"信号看板_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
            filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", filename)
            df.to_excel(filepath, index=False)
            self.set_status(f"已导出: {filename}")
        except ImportError:
            self.set_status("导出失败：需要安装 pandas 和 openpyxl")
        except Exception as e:
            self.set_status(f"导出失败: {e}")

    def _toggle_signal_auto_refresh(self):
        """切换实盘信号自动刷新。"""
        if self.signal_auto_refresh.get():
            self.set_status("实盘信号自动刷新已开启 (30秒间隔)")
            self._signal_auto_refresh_loop()
        else:
            if self._signal_refresh_id:
                try:
                    self.t3.after_cancel(self._signal_refresh_id)
                except Exception:
                    pass
                self._signal_refresh_id = None
            self.set_status("实盘信号自动刷新已关闭")

    def _signal_auto_refresh_loop(self):
        """自动刷新循环：交易时段每30秒，非交易时段每5分钟（仅当 Tab 可见且开关打开时）。"""
        if not self.signal_auto_refresh.get():
            return

        # ── 时段检测 ──
        now = datetime.now()
        is_trading = now.weekday() < 5 and (
            (9 * 60 + 30 <= now.hour * 60 + now.minute <= 11 * 60 + 30) or
            (13 * 60 <= now.hour * 60 + now.minute <= 15 * 60)
        )
        interval_ms = 30000 if is_trading else 300000  # 30秒 / 5分钟

        current_tab = self.notebook.tab(self.notebook.select(), "text")
        if "实盘" in current_tab:
            self.refresh_signal_board()
        self._signal_refresh_id = self.t3.after(interval_ms, self._signal_auto_refresh_loop)

    def screen_stock(self):
        """强势股筛选 — Plus+全链路评分（含ATR T1调节、资金流），后台8线程并行。"""
        self.notebook.select(self.scan_tab)
        self.t1.delete(1.0, tk.END)
        self.t1.insert(tk.END, "⏳ 强势股筛选中（Plus+全链路，8线程并行）...\n\n")
        self.set_status("强势股筛选运行中...")

        def worker():
            import concurrent.futures
            strong = []       # ≥68 强势+偏强
            watch = []        # ≥58 中性
            completed = [0]

            # 按代码去重
            seen = set()
            deduped = []
            for c, n in self.data:
                if c not in seen:
                    seen.add(c)
                    deduped.append((c, n))
            total = len(deduped)

            def scan_one(code, name_full):
                try:
                    df = load_stock_tdx(code)
                    if df is None or df.empty or len(df) < 120:
                        completed[0] += 1
                    try:
                        from _plus_.plus_pipeline import compute_plus_pipeline
                        v15 = compute_plus_pipeline(df, code=code, name=name_full,
                                                     sector_map=self.sector_map)
                    except Exception:
                        v15 = compute_v15_pipeline(df, code=code, name=name_full,
                                                   sector_map=self.sector_map)
                    completed[0] += 1
                    if not v15.get('available'):
                        return None
                    score = v15["score"]
                    level = v15["level"]
                    scenario = v15["scenario"]
                    pa = v15["position_advice"]
                    action = pa.get("action", "观望")
                    sec = v15.get("sector", {})

                    s_name = name_full.split()[-1] if " " in name_full else name_full

                    return {
                        "code": code, "name": s_name,
                        "score": score, "level": level,
                        "scenario": scenario, "action": action,
                        "sector": sec.get("sector", "") if sec else "",
                        "stop_loss": pa.get("stop_loss_price", 0),
                        "take_profit_1": pa.get("take_profit_1", 0),
                    }
                except Exception:
                    completed[0] += 1
                    return None

            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                futures = {pool.submit(scan_one, c, n): (c, n) for c, n in deduped}
                for future in concurrent.futures.as_completed(futures):
                    r = future.result()
                    if r is None:
                        continue
                    if r["score"] >= 70:
                        strong.append(r)
                    elif r["score"] >= 55:
                        watch.append(r)
                    if completed[0] % 20 == 0:
                        self.t1.after(0, lambda p=completed[0]: self.set_status(
                            f"强势股筛选: {p}/{total}"))

            strong.sort(key=lambda x: x["score"], reverse=True)
            watch.sort(key=lambda x: x["score"], reverse=True)

            lines = []
            lines.append(f"{'='*80}")
            lines.append(f"  强势股筛选报告（Plus+全链路）")
            lines.append(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  |  候选池 {total} 只")
            lines.append(f"{'='*80}")

            lines.append(f"\n  🔴 强势+偏强 (V15≥70) — {len(strong)} 只")
            lines.append(f"  {'代码':>6} {'名称':<8} {'V15分':>5} {'等级':<8} {'场景':<10} {'板块':<10} {'操作':<8} {'止损':>8} {'止盈':>8}")
            lines.append(f"  {'─'*75}")
            for r in strong:
                lines.append(
                    f"  {r['code']:>6} {r['name']:<6} {r['score']:>5.0f} {r['level']:<8} "
                    f"{r['scenario']:<10} {r['sector']:<10} {r['action']:<8} "
                    f"{r['stop_loss']:>8.2f} {r['take_profit_1']:>8.2f}"
                )

            lines.append(f"\n  🟡 中性 (V15 55-69) — {len(watch)} 只")
            lines.append(f"  {'代码':>6} {'名称':<8} {'V15分':>5} {'等级':<8} {'场景':<10} {'板块':<10} {'操作':<8}")
            lines.append(f"  {'─'*75}")
            for r in watch:
                lines.append(
                    f"  {r['code']:>6} {r['name']:<6} {r['score']:>5.0f} {r['level']:<8} "
                    f"{r['scenario']:<10} {r['sector']:<10} {r['action']:<8}"
                )

            lines.append(f"\n{'='*80}")
            lines.append(f"  筛选标准: V15过滤评分≥55(偏强线) | 共 {len(strong)+len(watch)} 只")

            export_report([l for l in lines if l.strip()])
            self.t1.after(0, lambda: (self.t1.delete(1.0, tk.END),
                                       self.t1.insert(tk.END, "\n".join(lines))))
            self.t1.after(0, lambda: self.notebook.select(self.scan_tab))
            self.t1.after(0, lambda: self.set_status(
                f"强势股筛选: 强势{len(strong)}只 + 中性{len(watch)}只"))

        threading.Thread(target=worker, daemon=True).start()

    # 兼容旧报告文本解析；新版截图点位直接使用本地候选点，不依赖AI文本。
    def parse_ai_point_text(self, text_content, df_data):
        point_dict = {}
        pattern_rule = r"([A-E])点.*?(\d{4}-\d{2}-\d{2}).*?(\d+\.\d{1,2})元"
        match_list = re.findall(pattern_rule, text_content, re.S | re.I)
        for tag, date_str, price_str in match_list:
            try:
                target_date = pd.to_datetime(date_str)
                target_price = float(price_str)
                match_row = df_data[df_data["date"].dt.date == target_date.date()]
                if not match_row.empty:
                    x = mdates.date2num(target_date)
                    y = target_price
                    point_dict[tag.upper()] = (x, y, f"{date_str} {price_str}元")
            except:
                continue
        return point_dict

    # ==================== V15 趋势图 ====================

    def show_v15_trend_chart(self):
        """绘制V15评分历史趋势图（含子因子分解 + 场景标注）。"""
        self.notebook.select(self.trend_tab)
        if not CHART_AVAILABLE:
            self.t8.insert(tk.END, "❌ 图表库不可用（需要 matplotlib）\n")
            return

        # 加载提示（主线程）
        self.t8.delete(1.0, tk.END)
        self.t8.insert(tk.END, "⏳ 正在计算V15评分历史趋势，请稍候...\n")
        self.t8.see(tk.END)

        def worker():
            try:
                s = self.get_selected()
                if not s:
                    self.t8.after(0, lambda: self.t8.insert(tk.END, "请先在左侧选择一只股票\n"))
                    return
                code, name_full = s
                ok, msg = self.prepare_stock_analysis(code, name_full)
                if not ok:
                    self.t8.after(0, lambda m=msg: self.t8.insert(tk.END, f"❌ {m}\n"))
                    return

                info = self.stock_cache[code]
                df = info.get("df")
                if df is None or len(df) < 120:
                    self.t8.after(0, lambda: self.t8.insert(tk.END, "❌ 数据不足120日，无法绘制趋势图\n"))
                    return

                # 解析列名
                col_close = '收盘' if '收盘' in df.columns else 'close'
                col_high = '最高' if '最高' in df.columns else 'high'
                col_low = '最低' if '最低' in df.columns else 'low'
                col_vol = '成交量' if '成交量' in df.columns else 'vol'

                closes = df[col_close].values.astype(float)
                highs = df[col_high].values.astype(float)
                lows = df[col_low].values.astype(float)
                volumes = df[col_vol].values.astype(float)
                dates = df["date"].values if "date" in df.columns else None

                n = len(closes)

                # ── 在历史各时间点计算评分 ──
                # 采样约40个点（过去600日中每隔~15日）
                step = max(1, min(15, n // 40))
                indices = list(range(90, n, step))
                if indices[-1] != n - 1:
                    indices.append(n - 1)

                hist_scores = []  # V15总分
                hist_rev = []     # 反转分
                hist_macd = []    # MACD分
                hist_vol = []     # 量能分
                hist_safe = []    # 安全分
                hist_scenarios = []
                hist_labels = []

                for idx in indices:
                    seg_c = closes[:idx+1].tolist()
                    seg_h = highs[:idx+1].tolist()
                    seg_l = lows[:idx+1].tolist()
                    seg_v = volumes[:idx+1].tolist()
                    lc = seg_c[-1]
                    lv = seg_v[-1]

                    try:
                        sig = compute_scenario_signal(seg_c, seg_h, seg_l, seg_v, lc, lv)
                        hist_scores.append(sig.get("score", 50))
                        hist_rev.append(sig.get("rev_score", 0))
                        hist_macd.append(sig.get("macd_score", 0))
                        hist_vol.append(sig.get("vol_score", 0))
                        hist_safe.append(sig.get("safe_score", 0))
                        hist_scenarios.append(sig.get("scenario", "无场景"))
                        if dates is not None:
                            try:
                                from pandas import Timestamp
                                hist_labels.append(Timestamp(dates[idx]).strftime("%m-%d"))
                            except Exception:
                                hist_labels.append(str(dates[idx])[:10])
                        else:
                            hist_labels.append(f"T-{n-1-idx}")
                    except Exception:
                        continue

                if len(hist_scores) < 5:
                    self.t8.after(0, lambda: self.t8.insert(tk.END, "❌ 历史评分数据不足\n"))
                    return

                # ── 画图：三行布局 ──
                import matplotlib.pyplot as plt
                fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(12, 10),
                    gridspec_kw={"height_ratios": [4, 2.5, 1.5]})
                fig.suptitle(f"V15择时雷达趋势 — {name_full}", fontsize=14, fontweight="bold")

                x = list(range(len(hist_scores)))
                x_labels = hist_labels
                step_label = max(1, len(x) // 15)

                # ═══ 第一行：V15总分 + 四因子评分 ═══
                ax1.plot(x, hist_scores, "k-", linewidth=2, label="V15总分", zorder=5)
                ax1.plot(x, hist_rev, "r--", alpha=0.6, label="反转因子")
                ax1.plot(x, hist_macd, "b--", alpha=0.6, label="MACD因子")
                ax1.plot(x, hist_vol, "g--", alpha=0.6, label="量能因子")
                ax1.plot(x, hist_safe, "y--", alpha=0.6, label="安全因子")
                ax1.axhline(70, color="darkred", linestyle=":", alpha=0.5, label="强势线(70)")
                ax1.axhline(55, color="orange", linestyle=":", alpha=0.4, label="偏强线(55)")
                ax1.axhline(45, color="gray", linestyle=":", alpha=0.3, label="中性线(45)")
                ax1.set_ylabel("评分")
                ax1.set_xticks(x[::step_label])
                ax1.set_xticklabels([])
                ax1.set_xlim(0, len(x) - 1)
                ax1.set_ylim(0, 105)
                ax1.legend(loc="upper left", fontsize=8)
                ax1.grid(True, alpha=0.3)
                ax1.set_title("V15总分与子因子趋势", fontsize=11)

                # 场景圆点
                scene_colors = {"超跌反弹": "green", "趋势接力": "blue",
                                "突破确认": "orange", "无场景": "gray"}
                for i in range(len(x)):
                    sc = hist_scenarios[i] if i < len(hist_scenarios) else "无场景"
                    ax1.scatter(x[i], hist_scores[i], color=scene_colors.get(sc, "gray"),
                               s=30, zorder=6)

                # ═══ 第二行：价格 + 最佳匹配因子叠加 ═══
                import numpy as np
                price_slice = closes[-len(x):]
                price_x = list(range(len(price_slice)))
                ax2.plot(price_x, price_slice, "k-", linewidth=1.8, label="收盘价", zorder=5)
                ax2.fill_between(price_x, price_slice.min(), price_slice, alpha=0.08, color="gray")

                p_min, p_max = price_slice.min(), price_slice.max()
                p_range = p_max - p_min if p_max > p_min else 1

                factor_series = {"V15总分": hist_scores, "反转因子": hist_rev,
                                "MACD因子": hist_macd, "量能因子": hist_vol,
                                "安全因子": hist_safe}
                factor_colors = {"V15总分": ("#1a1a2e", 1.2), "反转因子": ("#d32f2f", 1.0),
                                "MACD因子": ("#1565c0", 1.0), "量能因子": ("#2e7d32", 1.0),
                                "安全因子": ("#e65100", 1.0)}

                def norm_to_price(series, hist_len):
                    arr = np.array(series[:hist_len])
                    s_min, s_max = arr.min(), arr.max()
                    s_range = s_max - s_min if s_max > s_min else 1
                    return p_min + (arr - s_min) / s_range * p_range

                correlations = {}
                for name, series in factor_series.items():
                    hlen = min(len(series), len(price_slice))
                    if hlen < 3:
                        correlations[name] = 0
                        continue
                    s_arr = np.array(series[:hlen]); p_arr = price_slice[:hlen]
                    s_std, p_std = np.std(s_arr), np.std(p_arr)
                    correlations[name] = 0 if (s_std == 0 or p_std == 0) else \
                        np.corrcoef(s_arr, p_arr)[0, 1]

                sub_factors = {k: v for k, v in correlations.items() if k != "V15总分"}
                best_factor = max(sub_factors, key=lambda k: abs(sub_factors[k]))
                best_corr = correlations[best_factor]
                corr_label = "正相关" if best_corr > 0 else "负相关"

                # 叠加 V15 总分（虚线）
                hlt = min(len(hist_scores), len(price_slice))
                ax2.plot(price_x[:hlt], norm_to_price(hist_scores, hlt),
                         color="#1a1a2e", linewidth=1.2, linestyle="--", alpha=0.7,
                         label="V15总分(归一化)", zorder=4)
                # 叠加最佳匹配子因子（点划线）
                hlb = min(len(factor_series[best_factor]), len(price_slice))
                cbest, lwbest = factor_colors[best_factor]
                ax2.plot(price_x[:hlb], norm_to_price(factor_series[best_factor], hlb),
                         color=cbest, linewidth=lwbest, linestyle="-.", alpha=0.75,
                         label=f"{best_factor}(归一化, r={best_corr:+.2f})", zorder=4)

                ax2.set_ylabel("收盘价")
                ax2.set_xticks(x[::step_label])
                ax2.set_xticklabels([])
                ax2.set_xlim(0, len(price_x) - 1)
                ax2.grid(True, alpha=0.3)
                ax2.set_title(f"价格走势 × 评分叠加  |  {best_factor} r={best_corr:+.2f}", fontsize=10)
                ax2.legend(loc="upper left", fontsize=7.5, ncol=3)

                # ═══ 结论生成 ═══
                v15_now = hist_scores[-1] if hist_scores else 50
                factor_now = factor_series[best_factor][-1] if factor_series[best_factor] else 50
                abs_r = abs(best_corr)
                strength = "强" if abs_r >= 0.7 else ("中等" if abs_r >= 0.5 else "弱")

                advice_map = {
                    "反转因子": {
                        "性格": f"震荡票（{strength}{corr_label}，r={best_corr:+.2f}）。价格由均值回归主导，涨多了会跌、跌多了会弹。",
                        "做多": "等反转分从低位飙升 + 价格在支撑位附近时进场",
                        "禁忌": "不要追涨、不要死扛，赚钱就跑（10-15%止盈）",
                        "持仓": "短线思维，反弹到位就走，不适合长期持有",
                    },
                    "MACD因子": {
                        "性格": f"趋势票（{strength}{corr_label}，r={best_corr:+.2f}）。价格由动量驱动，趋势一旦形成会持续。",
                        "做多": "金叉确认 + 总分≥55时顺势追进，设移动止盈",
                        "禁忌": "不要抄底（趋势反转前会一直跌），不要逆势扛单",
                        "持仓": "趋势不破就持有，等MACD转弱或总分跌破45再走",
                    },
                    "量能因子": {
                        "性格": f"资金票（{strength}{corr_label}，r={best_corr:+.2f}）。量的变化走在价的前面，量价配合决定方向。",
                        "做多": "放量突破 + 量价同步向上时进场",
                        "禁忌": "缩量上涨是虚涨要跑，放量滞涨是出货信号",
                        "持仓": "盯量，量能持续放大则持有，缩量则减仓",
                    },
                    "安全因子": {
                        "性格": f"均线票（{strength}{corr_label}，r={best_corr:+.2f}）。价格围绕MA20/MA60运行，均线是锚。",
                        "做多": "均线多头排列时，逢MA20或MA60回踩不破买入",
                        "禁忌": "均线走平或死叉时不要参与，不要偏离均线太远追涨",
                        "持仓": "以MA60为生命线，跌破就走，线上就持有",
                    },
                }
                adv = advice_map.get(best_factor, advice_map["反转因子"])

                conclusion_lines = [
                    f"━━ {name_full} 结论 ━━",
                    "",
                    f"性格: {adv['性格']}",
                    "",
                    f"当前V15总分: {v15_now:.0f}分  |  {best_factor}: {factor_now:.0f}分",
                ]
                if v15_now >= 55:
                    conclusion_lines.append(f"状态: 信号偏多 ✓ → 可以找{adv['做多']}的机会")
                elif v15_now >= 45:
                    conclusion_lines.append(f"状态: 中性观望 → 等总分≥55或{best_factor}分明显回升再动")
                else:
                    conclusion_lines.append(f"状态: 信号偏空 ✗ → 暂时不碰，等分数回升再关注")
                conclusion_lines += [
                    "",
                    f"买入要点: {adv['做多']}",
                    f"风险提示: {adv['禁忌']}",
                    f"持仓策略: {adv['持仓']}",
                ]
                self._trend_conclusion = "\n".join(conclusion_lines)
                self._trend_best_factor = best_factor

                # ═══ 第三行：因子对照图（自适应）═══
                raw_slice = len(price_x)  # 与价格区对齐的行数
                ax3.set_xticks(x[::step_label])
                ax3.set_xticklabels(x_labels[::step_label], rotation=45, ha="right")
                ax3.set_xlim(0, raw_slice - 1)
                ax3.grid(True, alpha=0.3)

                raw_close = closes[-raw_slice:]
                raw_vol = volumes[-raw_slice:]
                raw_px = list(range(raw_slice))

                # 确认 df 中的列名
                has_ma = all(c in df.columns for c in ['ma5', 'ma10', 'ma20', 'ma60'])

                if best_factor == "反转因子":
                    # RSI(14) 曲线 + 超卖30/超买70线
                    delta = np.diff(raw_close, prepend=raw_close[0])
                    gains = np.where(delta > 0, delta, 0)
                    losses = np.where(delta < 0, -delta, 0)
                    avg_gain = np.array([np.mean(gains[max(0,i-13):i+1]) for i in range(raw_slice)])
                    avg_loss = np.array([np.mean(losses[max(0,i-13):i+1]) for i in range(raw_slice)])
                    rsi_arr = np.where(avg_loss == 0, 100, 100 - 100 / (1 + avg_gain / np.maximum(avg_loss, 1e-9)))
                    ax3.plot(raw_px, rsi_arr, "#d32f2f", linewidth=1.2, label="RSI(14)")
                    ax3.axhline(70, color="#b71c1c", ls=":", alpha=0.5)
                    ax3.axhline(30, color="#2e7d32", ls=":", alpha=0.5)
                    ax3.fill_between(raw_px, 30, 70, alpha=0.05, color="gray")
                    ax3.set_ylabel("RSI")
                    ax3.set_ylim(0, 100)
                    ax3.legend(loc="upper left", fontsize=8)
                    ax3.set_title("反转对照 · RSI(14)  超卖<30 = 反弹窗口 | 超买>70 = 回调风险", fontsize=9)

                elif best_factor == "MACD因子":
                    # MACD 柱 + 信号线
                    e12 = np.array([np.mean(raw_close[max(0,i-11):i+1]) for i in range(raw_slice)])
                    e26 = np.array([np.mean(raw_close[max(0,i-25):i+1]) for i in range(raw_slice)])
                    # 用 EMA 近似
                    e12_ema = raw_close.copy(); e26_ema = raw_close.copy()
                    for i in range(1, raw_slice):
                        e12_ema[i] = e12_ema[i-1] + (2/13)*(raw_close[i] - e12_ema[i-1])
                        e26_ema[i] = e26_ema[i-1] + (2/27)*(raw_close[i] - e26_ema[i-1])
                    macd_line = e12_ema - e26_ema
                    sig_line = np.array([np.mean(macd_line[max(0,i-8):i+1]) for i in range(raw_slice)])
                    hist = macd_line - sig_line
                    colors_macd = ['#d32f2f' if h >= 0 else '#2e7d32' for h in hist]
                    ax3.bar(raw_px, hist, color=colors_macd, width=0.8, alpha=0.7, label="MACD柱")
                    ax3.plot(raw_px, macd_line, "#1565c0", linewidth=1, alpha=0.8, label="DIF")
                    ax3.plot(raw_px, sig_line, "#ff6d00", linewidth=1, alpha=0.8, label="DEA")
                    ax3.axhline(0, color="gray", ls="-", alpha=0.3)
                    ax3.set_ylabel("MACD")
                    ax3.legend(loc="upper left", fontsize=7, ncol=3)
                    ax3.set_title("MACD对照 · 红柱=多头 | 绿柱=空头 | DIF上穿DEA=金叉", fontsize=9)

                elif best_factor == "量能因子":
                    # 成交量柱（红涨绿跌）+ 量能分归一化线
                    vol_colors = ['#d32f2f' if i > 0 and raw_close[i] >= raw_close[i-1]
                                  else '#2e7d32' for i in range(raw_slice)]
                    ax3.bar(raw_px, raw_vol, color=vol_colors, width=0.8, alpha=0.6, label="成交量")
                    # 量能分归一化叠加
                    if len(hist_vol) > 0:
                        v_max = raw_vol.max() if raw_vol.max() > 0 else 1
                        vol_norm = np.array(hist_vol[-raw_slice:]) if len(hist_vol) >= raw_slice else np.array(hist_vol)
                        vlen = min(len(vol_norm), raw_slice)
                        vol_normed = raw_vol[:vlen].max() * vol_norm[:vlen] / 100
                        ax3.plot(raw_px[:vlen], vol_normed, "#2e7d32", linewidth=1.5, linestyle="--",
                                alpha=0.8, label="量能分(归一化)")
                    ax3.set_ylabel("成交量")
                    ax3.legend(loc="upper left", fontsize=8)
                    ax3.set_title("量能对照 · 红柱=涨 | 绿柱=跌 | 虚线=量能分趋势", fontsize=9)

                elif best_factor == "安全因子":
                    # 收盘价 + MA20 + MA60
                    ax3.plot(raw_px, raw_close, "k-", linewidth=1.2, alpha=0.6, label="收盘价")
                    if has_ma:
                        ma20_raw = df['ma20'].values[-raw_slice:].astype(float)
                        ma60_raw = df['ma60'].values[-raw_slice:].astype(float)
                        ax3.plot(raw_px, ma20_raw, "#ff6d00", linewidth=1.2, label="MA20")
                        ax3.plot(raw_px, ma60_raw, "#b03060", linewidth=1.5, label="MA60")
                    else:
                        ma20_raw = np.array([np.mean(raw_close[max(0,i-19):i+1]) for i in range(raw_slice)])
                        ma60_raw = np.array([np.mean(raw_close[max(0,i-59):i+1]) for i in range(raw_slice)])
                        ax3.plot(raw_px, ma20_raw, "#ff6d00", linewidth=1.2, label="MA20")
                        ax3.plot(raw_px, ma60_raw, "#b03060", linewidth=1.5, label="MA60")
                    ax3.set_ylabel("价格")
                    ax3.legend(loc="upper left", fontsize=7, ncol=3)
                    ax3.set_title("安全对照 · MA20/MA60  线上=支撑 | 线下=压力 | 回踩不破=买点", fontsize=9)

                ax3.set_xlabel("时间")
                plt.tight_layout()

                # ── 嵌入GUI ──
                def embed():
                    # 清理旧canvas，保留帮助栏（trend_bar）
                    if self.canvas:
                        try:
                            self.canvas.get_tk_widget().destroy()
                        except Exception:
                            pass
                        self.canvas = None
                    canvas = FigureCanvasTkAgg(fig, master=self.trend_frm)
                    canvas.draw()
                    canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
                    self.canvas = canvas
                    self.t8.delete(1.0, tk.END)
                    self.t8.insert(tk.END, f"✅ V15趋势图已生成，请切换到「V15趋势」Tab 查看\n")
                    self.set_status(f"V15趋势图: {name_full} ({len(hist_scores)}个采样点)")

                self.trend_frm.after(0, embed)

            except Exception as e:
                self.t8.after(0, lambda err=str(e): self.t8.insert(tk.END, f"❌ 生成V15趋势图出错: {err}\n"))
                import traceback
                traceback.print_exc()

        threading.Thread(target=worker, daemon=True).start()

    # ═══════════════════════════════════════════
    # AI解读K线图（真实形态标注）
    # ═══════════════════════════════════════════

    def _draw_ai_chart(self, df, hit_patterns, code, name_full):
        """画K线图：真实形态标注 + 支撑压力位，切换到K线Tab。"""
        if not CHART_AVAILABLE:
            return
        try:
            import matplotlib.pyplot as plt
            from mplfinance.original_flavor import candlestick_ohlc
            import matplotlib.dates as mdates

            # 取最近60~90日K线用于画图
            df_plot = df.tail(80).copy()
            df_plot['date_num'] = mdates.date2num(df_plot['date'])
            ohlc = df_plot[['date_num', '开盘', '最高', '最低', '收盘']].values

            fig, ax = plt.subplots(figsize=(12, 7), dpi=100)
            fig.suptitle(f"{name_full} ({code}) — 技术形态·支撑压力", fontsize=13, fontweight='bold')

            candlestick_ohlc(ax, ohlc, width=0.6, colorup='#d32f2f', colordown='#2e7d32')

            # 均线
            ax.plot(df_plot.date_num, df_plot.ma5, label='MA5', c='#377eb8', lw=0.8)
            ax.plot(df_plot.date_num, df_plot.ma10, label='MA10', c='#ff7f0e', lw=0.8)
            ax.plot(df_plot.date_num, df_plot.ma20, label='MA20', c='#9400d3', lw=1)
            ax.plot(df_plot.date_num, df_plot.ma60, label='MA60', c='#b03060', lw=1.2)

            # 支撑/压力位
            rh = df_plot['最高'].iloc[-20:].max()
            rl = df_plot['最低'].iloc[-20:].min()
            ld = df_plot.iloc[-1]['date_num']
            last_close = df_plot.iloc[-1]['收盘']
            ma60v = df_plot.iloc[-1]['ma60']

            ax.axhline(rh, c='#ef4444', ls='--', lw=1.5, alpha=0.8)
            ax.axhline(rl, c='#22c55e', ls='--', lw=1.5, alpha=0.8)
            ax.annotate(f'压力 {rh:.2f}', (ld, rh), fontsize=10, c='#ef4444',
                       xytext=(10, 6), textcoords='offset points', weight='bold')
            ax.annotate(f'支撑 {rl:.2f}', (ld, rl), fontsize=10, c='#22c55e',
                       xytext=(10, -6), textcoords='offset points', weight='bold')

            # 标注MA60
            if pd.notna(ma60v):
                ax.axhline(ma60v, c='#6b7280', ls=':', lw=1, alpha=0.5)
                ax.annotate(f'MA60 {ma60v:.2f}', (ld, ma60v), fontsize=8, c='#6b7280',
                           xytext=(10, 2), textcoords='offset points')

            # 从 v14_pattern_detector 获取真实形态
            try:
                from v14_pattern_detector import detect_all_patterns as detect_v2
                detected, _ = detect_v2(
                    dates=[str(d) for d in df_plot['date']],
                    opens=df_plot['开盘'].tolist(),
                    highs=df_plot['最高'].tolist(),
                    lows=df_plot['最低'].tolist(),
                    closes=df_plot['收盘'].tolist(),
                    volumes=df_plot['成交量'].tolist(),
                )
                # 只取高价值形态
                real_patterns = [p for p in detected if p not in (
                    '缩量调整',
                ) and not p.name.startswith(('十字星', '连阳', '连阴', '孕线'))]
            except Exception:
                real_patterns = []

            # 标注真实形态点位
            used_boxes = []
            if real_patterns:
                for pat in real_patterns[:6]:
                    if not pat.date:
                        continue
                    try:
                        # pat.date 已改为真实日期字符串，与 df_plot['date'] 匹配
                        pat_date_prefix = str(pat.date)[:10]
                        mask = df_plot['date'].astype(str).str[:10] == pat_date_prefix
                        if not mask.any():
                            continue
                        row = df_plot[mask].iloc[-1]
                        px = mdates.date2num(row['date'])
                        # 根据形态类型选择标注价格
                        if '阳包' in pat.name or '阴包' in pat.name or '刺透' in pat.name or '乌云' in pat.name:
                            py = row['收盘']
                        elif '底背离' in pat.name or '底' in pat.name:
                            py = row['最低']
                        elif '顶' in pat.name:
                            py = row['最高']
                        elif '放量' in pat.name:
                            py = row['收盘']
                        elif '缩量' in pat.name:
                            py = row['最低']
                        elif '多头' in pat.name:
                            py = row['收盘']
                        elif '站稳' in pat.name:
                            py = row['最低']
                        elif '锤子' in pat.name:
                            py = row['最低']
                        else:
                            py = row['收盘']

                        # 避免重叠
                        overlap = any(abs(px - bx) < 1.5 and abs(py - by) / py < 0.03
                                     for bx, by, _ in used_boxes)
                        if not overlap:
                            ax.scatter(px, py, s=80, c='#1565c0', zorder=7, edgecolors='white', lw=1)
                            detail = pat.details or pat.name
                            ax.annotate(pat.name, xy=(px, py),
                                       xytext=(15, 10 + len(used_boxes) * 12),
                                       textcoords='offset points', fontsize=9,
                                       color='#1565c0', weight='bold',
                                       arrowprops=dict(arrowstyle='->', color='#1565c0', lw=1))
                            used_boxes.append((px, py, pat.name))
                    except Exception:
                        continue

            # 图例说明
            legend_lines = ['压力位(20日高)', '支撑位(20日低)', 'MA60']
            if real_patterns:
                pnames = set(p.name.split('(')[0] for p in real_patterns[:4])
                legend_lines.append(' | '.join(pnames))
            ax.text(0.01, 0.99, '\n'.join(legend_lines), transform=ax.transAxes,
                   fontsize=8, va='top', color='#374151',
                   bbox=dict(boxstyle='round,pad=0.3', facecolor='white', alpha=0.8))

            ax.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d'))
            plt.xticks(rotation=30)
            ax.legend(loc='lower left', fontsize=8)
            ax.grid(True, alpha=0.2)

            plt.tight_layout()

            # 嵌入GUI
            for w in self.chart_frm.winfo_children():
                w.destroy()
            canvas = FigureCanvasTkAgg(fig, master=self.chart_frm)
            canvas.draw()
            canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
            self.canvas = canvas

            # 切换Tab
            self.notebook.select(self.chart_tab)
            self.set_status(f"K线图已生成: {name_full} ({len(real_patterns)}个形态标注)")

        except Exception as e:
            print(f"AI图表绘制错误: {e}")
            import traceback
            traceback.print_exc()

    # ==================== 时序修正：先生成文字 → 再画图 ====================
    def run_ai_analysis(self):
        """信号验证引擎：四问合并，一次 DeepSeek 调用。"""
        self.notebook.select(self.single_tab)
        self.t8.delete(1.0, tk.END)
        self.t8.insert(tk.END, "⏳ AI信号验证准备中...")
        self.set_status("AI信号验证准备中...")

        def worker():
            try:
                s = self.get_selected()
                if not s:
                    self.t8.after(0, lambda: (self.t8.delete(1.0, tk.END),
                                               self.t8.insert(tk.END, "请先在左侧选择一只股票")))
                    return
                c, n_full = s
                ok, msg = self.prepare_stock_analysis(c, n_full)
                if not ok:
                    self.t8.after(0, lambda m=msg: (self.t8.delete(1.0, tk.END),
                                                     self.t8.insert(tk.END, f"❌ {m}")))
                    return
                cache = self.stock_cache[c]
                df = cache["df"]
                hit = cache["info"][0]
                name = cache["info"][5]

                self.t8.after(0, lambda: (self.t8.delete(1.0, tk.END),
                    self.t8.insert(tk.END, "AI分析中...\n")))
                self.t8.after(0, lambda: self.t2.delete(1.0, tk.END))
                self.after(0, lambda n=n_full: self.set_status(f"AI信号验证中：{n}"))

                # K线图
                self.after(0, lambda: self._draw_ai_chart(df, hit, c, n_full))

                # 单线程单次调用
                try:
                    result = ai_signal_verification(c, name, cache)
                    self.t8.after(0, lambda r=result: (self.t8.delete(1.0, tk.END),
                                                         self.t8.insert(tk.END, r)))
                    self.t2.after(0, lambda r=result: (self.t2.delete(1.0, tk.END),
                                                         self.t2.insert(tk.END, r)))
                    self.t8.after(0, lambda: self.set_status("AI信号验证完成"))
                except Exception as e:
                    self.t8.after(0, lambda e=e: self.t8.insert(tk.END, f"❌ AI信号验证失败：{e}"))

            except Exception as e:
                self.t8.after(0, lambda e=e: self.t8.insert(tk.END, f"❌ AI研判出错：{e}"))

        threading.Thread(target=worker, daemon=True).start()

    def batch_make_report(self):
        self.notebook.select(self.scan_tab)
        self.t1.insert(tk.END, "⏳ 批量AI报告生成中（后台运行，请稍候）...\n\n")
        self.set_status("批量AI报告生成中...")

        def worker():
            try:
                txt_all = ""
                total = len(self.data)
                for i, (c, n_full) in enumerate(self.data):
                    self.t1.after(0, lambda idx=i: self.set_status(
                        f"批量AI报告: {idx+1}/{total}"))
                    ok, msg = self.prepare_stock_analysis(c, n_full)
                    if not ok:
                        txt_all += f"\n{'='*50}\n{n_full}\n{'='*50}\n跳过：{msg}\n"
                        continue
                    hit, rate, score, tag, price, name = self.stock_cache[c]["info"]
                    df = self.stock_cache[c]["df"]
                    v15_info = self.stock_cache[c].get("v15", {})
                    v15_context = ""
                    if v15_info.get("available"):
                        v15_context = (
                            f"\n【V15择时雷达参考】\n"
                            f"场景：{v15_info['scenario']} | "
                            f"过滤评分：{v15_info['score']:.1f} | "
                            f"等级：{v15_info['level']}\n"
                            f"操作建议：{v15_info['position_advice'].get('action', '观望')} | "
                            f"{v15_info['position_advice'].get('reason', '')}\n"
                        )
                    a = ai_vol_analysis(c, name, hit, rate, score, tag, price, df)
                    b = ai_shape_note(c, name, hit, price, df)
                    txt_all += f"\n{'='*50}\n{n_full}\n{'='*50}\n{v15_context}\n{a}\n\n{b}\n"
                export_batch_ai_report(txt_all)
                self.t1.after(0, lambda: self.t1.insert(tk.END, "✅ 批量报告已生成"))
                self.t1.after(0, lambda: self.set_status("批量AI报告已生成"))
            except Exception as e:
                self.t1.after(0, lambda e=e: self.t1.insert(
                    tk.END, f"❌ 批量报告出错：{e}"))
                self.t1.after(0, lambda: self.set_status("批量AI报告生成失败"))

        threading.Thread(target=worker, daemon=True).start()

    # ═══════════════════════════════════════════
    # 信号源状态更新
    # ═══════════════════════════════════════════

    def _update_source_color(self, key: str, status: str):
        colors = {"ok": "#22c55e", "stale": "#f59e0b", "off": "#6b7280", "error": "#ef4444"}
        if key in self.src_labels:
            self.src_labels[key].config(fg=colors.get(status, "#6b7280"))

    def update_source_status(self, sector_source="", news_source="", cross_source=""):
        """根据数据源字符串更新对应的状态灯（只更新有数据的，不重置其他灯）。"""
        if sector_source:
            if "同花顺" in sector_source or "在线" in sector_source:
                self._update_source_color("sector", "ok")
            elif "缓存" in sector_source or "演示" in sector_source:
                self._update_source_color("sector", "stale")
            else:
                self._update_source_color("sector", "off")

        if news_source:
            if "东方财富" in news_source or "akshare" in news_source:
                self._update_source_color("news", "ok")
            elif "演示" in news_source:
                self._update_source_color("news", "stale")

        if cross_source:
            if "新浪" in cross_source:
                self._update_source_color("cross", "ok")
            elif "演示" in cross_source:
                self._update_source_color("cross", "stale")

    # ═══════════════════════════════════════════
    # 板块热度 Tab
    # ═══════════════════════════════════════════

    # ═══════════════════════════════════════════
    # 板块热度热力图
    # ═══════════════════════════════════════════

    def show_sector_heatmap(self):
        """板块热力地图 — Tkinter Canvas 原生渲染网格，零缩放零模糊。"""
        self.notebook.select(self.sector_tab)

        def worker():
            try:
                from v14_deep_engine import get_sector_heat_map
                sector_map, source = get_sector_heat_map(online=True)
                self.sector_map = sector_map
                self.sector_source = source

                ranked = sorted(sector_map.items(),
                              key=lambda x: (x[1].get("heat", 0)),
                              reverse=True)
                is_demo = "演示" in source
                total = len(ranked)

                names = []
                changes = []
                for name, info in ranked:
                    names.append(name)
                    changes.append(info.get("change", 0))

                # 色卡插值函数
                import numpy as np
                max_abs = max(abs(min(changes)), abs(max(changes)), 0.3)
                def chg_color(val):
                    t = (val + max_abs) / (2 * max_abs)
                    t = max(0, min(1, t))
                    colors_rgb = [
                        (0x1B, 0x5E, 0x20), (0x66, 0xBB, 0x6A), (0xC8, 0xE6, 0xC9),
                        (0xF5, 0xF5, 0xF5),
                        (0xFF, 0xCD, 0xD2), (0xEF, 0x53, 0x50), (0xB7, 0x1C, 0x1C),
                    ]
                    seg = t * (len(colors_rgb) - 1)
                    i = int(seg)
                    f = seg - i
                    if i >= len(colors_rgb) - 1:
                        r, g, b = colors_rgb[-1]
                    else:
                        r1, g1, b1 = colors_rgb[i]
                        r2, g2, b2 = colors_rgb[i+1]
                        r = int(r1 + (r2 - r1) * f)
                        g = int(g1 + (g2 - g1) * f)
                        b = int(b1 + (b2 - b1) * f)
                    return f'#{r:02x}{g:02x}{b:02x}'

                def draw():
                    for w in self.sector_frm.winfo_children():
                        w.destroy()

                    # Canvas 填满整个 frame
                    cv = tk.Canvas(self.sector_frm, bg='white', highlightthickness=0)
                    cv.pack(fill=tk.BOTH, expand=True)

                    title_color = "#d32f2f" if is_demo else "#1a1a2e"
                    cv.create_text(10, 8, anchor='nw',
                                   text=f"行业板块热力地图 — {source}",
                                   font=("微软雅黑", 11), fill=title_color)
                    cv.create_text(10, 28, anchor='nw',
                                   text=f"共 {total} 个板块 · 红涨绿跌",
                                   font=("微软雅黑", 8), fill="#9E9E9E")

                    # 等宽网格：cols 列，自适应 frame 宽度
                    cv.update_idletasks()
                    fw = cv.winfo_width()
                    fh = cv.winfo_height()
                    if fw < 100:
                        fw = 1000
                        fh = 800

                    top_margin = 44
                    cols = int(max(8, fw / 108))

                    import math
                    rows = math.ceil(total / cols)
                    cell_w = (fw - 4) / cols
                    cell_h = min(max(56, (fh - top_margin - 36) / rows), 72)
                    pad = 2

                    for i, (name, chg) in enumerate(zip(names, changes)):
                        row = i // cols
                        col = i % cols
                        x1 = col * cell_w + pad
                        y1 = top_margin + row * cell_h + pad
                        x2 = (col + 1) * cell_w - pad
                        y2 = top_margin + (row + 1) * cell_h - pad

                        color = chg_color(chg)
                        cv.create_rectangle(x1, y1, x2, y2, fill=color,
                                           outline='white', width=1)

                        text = f"{name}\n{chg:+.1f}%"
                        text_color = 'white' if abs(chg) > 2.0 else '#1a1a2e'
                        fs = 9 if cell_w > 100 else (8 if cell_w > 85 else 7)
                        cv.create_text((x1 + x2) / 2, (y1 + y2) / 2,
                                       text=text, font=("微软雅黑", fs),
                                       fill=text_color, justify='center')

                    # 色条
                    bar_h = 18; bar_y = fh - 26
                    bar_x1 = fw * 0.15; bar_x2 = fw * 0.85
                    segs = 12
                    for s in range(segs):
                        bx1 = bar_x1 + (bar_x2 - bar_x1) * s / segs
                        bx2 = bar_x1 + (bar_x2 - bar_x1) * (s + 1) / segs
                        val = -max_abs + 2 * max_abs * s / segs
                        cv.create_rectangle(bx1, bar_y, bx2, bar_y + bar_h,
                                           fill=chg_color(val), outline='')
                    cv.create_text(bar_x1 - 10, bar_y + bar_h / 2,
                                   text=f"-{max_abs:.0f}%", font=("微软雅黑", 7), anchor='e')
                    cv.create_text(bar_x2 + 10, bar_y + bar_h / 2,
                                   text=f"+{max_abs:.0f}%", font=("微软雅黑", 7), anchor='w')
                    cv.create_text((bar_x1 + bar_x2) / 2, bar_y - 8,
                                   text="涨跌幅", font=("微软雅黑", 8), fill="#616161")

                    self.set_status(f"板块热度: {total}个板块")

                self.after(0, draw)
                self.after(0, lambda: self.update_source_status(sector_source=source))

            except Exception as e:
                self.after(0, lambda: self._show_sector_error(str(e)))
                self.after(0, lambda: self._update_source_color("sector", "error"))

        threading.Thread(target=worker, daemon=True).start()

    def _embed_sector_chart(self, fig, source, is_demo, total):
        """将热力图嵌入 sector_frm。"""
        for w in self.sector_frm.winfo_children():
            w.destroy()
        canvas = FigureCanvasTkAgg(fig, master=self.sector_frm)
        canvas.draw()
        canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        self.notebook.select(self.sector_tab)
        tag = "演示" if is_demo else "在线"
        self.set_status(f"板块热度: {total}个板块 ({tag})")

    def _show_sector_error(self, err):
        for w in self.sector_frm.winfo_children():
            w.destroy()
        err_lbl = tk.Label(self.sector_frm, text=f"同花顺板块数据获取失败\n\n{err}\n\n请检查网络连接",
                          font=("微软雅黑", 11), fg="#d32f2f", bg="white", justify=tk.LEFT)
        err_lbl.pack(expand=True)
        self.notebook.select(self.sector_tab)
        self.set_status("板块数据拉取失败")

    # ═══════════════════════════════════════════
    # 新闻雷达 Tab
    # ═══════════════════════════════════════════


    def _on_ai_radar(self):
        if not self._radar_data:
            self.t9.insert("end", "请先点击「择时雷达信号」生成数据\n")
            return
        from scripts.ai_radar_reporter import generate_radar_report
        self.btn_ai_radar.config(state=tk.DISABLED, text="AI解读中...")
        self.t9.insert("end", "\n正在调用 DeepSeek 生成 AI 解读...\n")
        self.t9.see("end")
        generate_radar_report(self.t9, dict(self._radar_data))
        self.t9.after(30000, lambda: self.btn_ai_radar.config(state=tk.NORMAL, text="AI解读"))

    def show_news_radar(self):
        self.notebook.select(self.news_tab)
        self.t6.delete(1.0, tk.END)
        self.t6.insert(tk.END, "⏳ 正在拉取市场新闻...\n")

        def worker():
            try:
                from v14_news_provider import get_market_news, scan_news_risks
                news_items, source = get_market_news(online=True)
                self.news_items = news_items   # 缓存供评分管线复用

                def _put(text, tag=None):
                    """线程安全插入带标签文本。"""
                    self.t6.after(0, lambda t=text, tg=tag: self.t6.insert(tk.END, t, tg))

                self.t6.after(0, lambda: self.t6.delete(1.0, tk.END))
                _put(f"{'='*70}\n")
                _put(f"  市场新闻情绪雷达\n")
                _put(f"  数据源: {source}  |  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                _put(f"{'='*70}\n\n")

                # ── 风险预警 ──
                risk_alerts = scan_news_risks(news_items)
                if risk_alerts:
                    _put(f"  ╔{'═'*66}\n")
                    _put(f"  ║  🚨 风险预警（共{len(risk_alerts)}条）\n")
                    _put(f"  ╠{'═'*66}\n")
                    for a in risk_alerts[:15]:
                        level_tag = {"critical": "🔴 严重", "high": "🟠 高风险", "medium": "🟡 关注"}.get(a["risk_level"], "⚪")
                        _put(f"  ║ [{a['published_at']}] {level_tag} | {a['risk_type']} | {a['title'][:120]}\n")
                    if len(risk_alerts) > 15:
                        _put(f"  ║ ... 还有{len(risk_alerts)-15}条风险预警\n")
                    _put(f"  ╚{'═'*66}\n\n")

                # ── 重点新闻摘要 ──
                high_priority = [n for n in news_items if n.priority >= 50]
                if high_priority:
                    high_priority.sort(key=lambda n: n.priority, reverse=True)
                    _put(f"  ┌{'─'*66}\n")
                    _put(f"  │  ⭐ 重点新闻（共{len(high_priority)}条，按重要度排序）\n")
                    _put(f"  ├{'─'*66}\n")
                    for n in high_priority[:12]:
                        sent_emoji = "📈" if n.sentiment > 0.3 else ("📉" if n.sentiment < -0.3 else "📊")
                        _put(f"  │ [{n.priority}] {sent_emoji} [{n.sector}] {n.title[:100]}\n", "highlight")
                    _put(f"  └{'─'*66}\n\n")

                # 按板块聚合
                sector_news = {}
                for item in sorted(news_items, key=lambda x: x.published_at, reverse=True):
                    sector_news.setdefault(item.sector, []).append(item)

                sector_rank = sorted(
                    sector_news.items(),
                    key=lambda kv: sum(n.heat for n in kv[1]),
                    reverse=True
                )

                for sector, items in sector_rank:
                    total_heat = sum(n.heat for n in items)
                    avg_sent = sum(n.sentiment for n in items) / len(items)
                    _put(f"\n  [{sector}] {len(items)}条新闻 | 热度{total_heat:.0f} | 情绪{avg_sent:+.2f}\n")
                    for item in items:
                        sent_tag = "+" if item.sentiment > 0.3 else ("-" if item.sentiment < -0.3 else "~")
                        tag = "highlight" if item.priority >= 50 else None
                        _put(
                            f"    [{item.published_at:%H:%M}] [{sent_tag}] "
                            f"H{item.heat:.0f} | {item.title[:120]}\n",
                            tag
                        )

                _put(f"\n{'='*70}\n")
                _put(f"  共 {len(news_items)} 条新闻 | {len(sector_news)} 个板块 | ⭐ {len(high_priority)} 条重点\n")
                _put(f"  重点规则: 政策/制裁/降准降息 +35 | 权重股 +20 | 情绪极端 +15 | 热度>85 +10\n")

                # 配置高亮标签
                def _apply_tags():
                    self.t6.tag_configure("highlight", foreground="#d97706", font=("微软雅黑", 10, "bold"))
                self.t6.after(0, _apply_tags)
                self.t6.after(0, lambda: self.notebook.select(self.news_tab))
                self.t6.after(0, lambda: self.set_status(f"新闻雷达: {len(news_items)}条 ⭐{len(high_priority)}"))
                self.after(0, lambda: self.update_source_status(news_source=source))

            except Exception as e:
                self.t6.after(0, lambda e=e: self.t6.insert(tk.END, f"\n❌ 新闻数据获取失败: {e}"))
                self.after(0, lambda: self._update_source_color("news", "error"))

        threading.Thread(target=worker, daemon=True).start()

    # ═══════════════════════════════════════════
    # 新闻蒸馏 Tab（AI四问简报）
    # ═══════════════════════════════════════════

    def show_news_distill(self):
        """新闻蒸馏：四问AI简报（情绪/热点/利好/利空）。

        纯手动触发，不依赖评分管道，独立 Tab 展示。
        同一天多次蒸馏会自动合并到已有记录（知识累积）。
        """
        self.notebook.select(self.distill_tab)
        self.t_distill.delete(1.0, tk.END)
        self.t_distill.insert(tk.END, "⏳ 正在蒸馏新闻（去重→拉正文→AI分析）...\n")
        self.t_distill.insert(tk.END, "   通常需要5-10秒，请耐心等待...\n\n")

        def worker():
            try:
                from _plus_.news_distiller import get_distiller
                from v14_news_provider import get_market_news

                # 手动触发：强制重新拉新闻
                news_items, _ = get_market_news(online=True)
                self.news_items = news_items

                # 运行蒸馏（自动合并到今日已有记录）
                d = get_distiller()
                result = d.run(news_items)

                def _put(text, tag=None):
                    self.t_distill.after(0, lambda t=text, tg=tag:
                                         self.t_distill.insert(tk.END, t, tg))

                self.t_distill.after(0, lambda: self.t_distill.delete(1.0, tk.END))

                # ── 标题区 ──
                now = datetime.now()
                merge_count = getattr(d, '_today_merge_count', 1)
                merge_info = f"合并入已有记录 · 累计{merge_count}次" if merge_count > 1 else ""

                _put(f"{'='*70}\n")
                _put(f"  🧠 AI 新闻蒸馏 · 四问简报\n")
                _put(f"  {now.strftime('%Y-%m-%d %H:%M:%S')}  {merge_info}  |  "
                     f"输入 {result.news_count}条 → 去重移除 {result.dedup_removed}条 → "
                     f"拉正文 {result.body_fetched}篇 → API {result.api_calls}次\n")
                _put(f"{'='*70}\n\n")

                # ── 问题1: 市场情绪 ──
                sent_emoji = {"贪婪": "🤑", "偏多": "😊", "中性": "😐",
                              "偏空": "😟", "恐慌": "😨"}.get(
                    result.sentiment_label, "🤔")
                _put(f"  ┌{'─'*66}\n")
                _put(f"  │  {sent_emoji}  市场情绪: {result.sentiment_label} "
                     f"({result.sentiment_score}/10)\n")
                for ev in result.sentiment_evidence[:3]:
                    _put(f"  │    · {ev}\n")
                _put(f"  └{'─'*66}\n\n")

                # ── 问题2: 市场在炒什么 ──
                if result.hot_themes:
                    _put(f"  ┌{'─'*66}\n")
                    _put(f"  │  🔥 市场在炒什么\n")
                    for t in result.hot_themes[:5]:
                        theme = t.get("theme", "")
                        news = t.get("news", "")[:120]
                        _put(f"  │    · {theme}")
                        if news:
                            _put(f" — {news}")
                        _put(f"\n")
                    _put(f"  └{'─'*66}\n\n")

                # ── 问题3: 大利好 ──
                if result.good_news:
                    _put(f"  ┌{'─'*66}\n")
                    _put(f"  │  🟢 大利好（全天累计, 重复事件已合并）\n")
                    for g in result.good_news[:8]:
                        event = g.get("event", "")
                        impact = g.get("impact", "")
                        freshness = g.get("freshness", "")
                        _put(f"  │    · {event}\n")
                        _put(f"  │      → 影响: {impact} | {freshness}\n")
                    _put(f"  └{'─'*66}\n\n")

                # ── 问题4: 大利空 ──
                if result.bad_news:
                    _put(f"  ┌{'─'*66}\n")
                    _put(f"  │  🔴 大利空（全天累计, 重复事件已合并）\n")
                    for b in result.bad_news[:8]:
                        event = b.get("event", "")
                        impact = b.get("impact", "")
                        freshness = b.get("freshness", "")
                        _put(f"  │    · {event}\n")
                        _put(f"  │      → 影响: {impact} | {freshness}\n")
                    _put(f"  └{'─'*66}\n\n")

                _put(f"{'='*70}\n")
                _put(f"  蒸馏完成 | 全天每次刷新结果都会合并入此记录\n")

                # 标签样式
                def _apply_tags():
                    self.t_distill.tag_configure("highlight",
                        foreground="#d97706", font=("微软雅黑", 10, "bold"))
                self.t_distill.after(0, _apply_tags)

                # 更新状态栏
                self.after(0, lambda: self.set_status(
                    f"新闻蒸馏: {result.sentiment_label}({result.sentiment_score}/10) | "
                    f"热点 {len(result.hot_themes)}个 | 利好 {len(result.good_news)} | "
                    f"利空 {len(result.bad_news)} | 累计{merge_count}次"))

            except Exception as e:
                if not auto:
                    self.t_distill.after(0, lambda e=e:
                        self.t_distill.insert(tk.END, f"\n❌ 蒸馏失败: {e}\n"))
                    self.after(0, lambda: self._update_source_color("news", "error"))

        threading.Thread(target=worker, daemon=True).start()

    # ═══════════════════════════════════════════
    # 财务穿透
    # ═══════════════════════════════════════════

    def refresh_financial_penetration(self):
        """单股财务解读（Phase 2 — 含BS/CF/杜邦）"""
        from _plus_.financial_penetration_phase2 import fetch_bs_cf, _dupont_decomposition, _calc_free_cash_flow, _analyze_bs_ratios, _phase2_warnings

        period_label = self.fi_period_var.get()
        date_str = self._fi_date_map.get(period_label, "20260331")

        sel = self.get_selected()
        if not sel:
            self.notebook.select(self.finance_tab)
            txt = self.t_finance
            txt.delete("1.0", tk.END)
            txt.insert(tk.END, "请先在左侧自选股列表中选中一只股票\n", "h2")
            txt.insert(tk.END, "→ 点击左侧股票名称 → 点「刷新」")
            return

        code = sel[0]
        parts = sel[1].split(None, 1)
        name_from_list = parts[1] if len(parts) > 1 else sel[1]

        txt = self.t_finance
        txt.delete("1.0", tk.END)

        txt.tag_config("h1", font=("微软雅黑", 11, "bold"), foreground="#1f2937")
        txt.tag_config("h2", font=("微软雅黑", 9, "bold"), foreground="#37474f")
        txt.tag_config("dim_label", font=("微软雅黑", 8), foreground="#78909c")
        txt.tag_config("excellent", foreground="#1b5e20", background="#e8f5e9")
        txt.tag_config("good", foreground="#2e7d32")
        txt.tag_config("fair", foreground="#f57f17")
        txt.tag_config("weak", foreground="#e65100")
        txt.tag_config("poor", foreground="#c62828")
        txt.tag_config("score_bar", foreground="#1b5e20", background="#c8e6c9")
        txt.tag_config("warn_high", foreground="#b71c1c", background="#ffcdd2")
        txt.tag_config("warn_mid", foreground="#e65100", background="#ffe0b2")
        txt.tag_config("warn_ok", foreground="#1b5e20", background="#e8f5e9")
        txt.tag_config("data_key", font=("微软雅黑", 8, "bold"), foreground="#37474f")
        txt.tag_config("data_val", font=("微软雅黑", 8), foreground="#546e7a")
        txt.tag_config("data_val_good", font=("微软雅黑", 8), foreground="#1b5e20")
        txt.tag_config("data_val_bad", font=("微软雅黑", 8), foreground="#c62828")
        txt.tag_config("sep", foreground="#eceff1")
        txt.tag_config("caption", font=("微软雅黑", 8), foreground="#90a4ae")

        txt.insert(tk.END, "正在加载财务数据……\n")
        txt.update()

        try:
            from _plus_.financial_penetration import get_stock_detail
            base = get_stock_detail(code, date=date_str)
            if base is None:
                txt.delete("1.0", tk.END)
                txt.insert(tk.END, code + " " + name_from_list + "\n\n", "h1")
                txt.insert(tk.END, "❌ 未获取到该股票的财务数据\n\n可能该股票本期财报数据尚未发布。")
                return

            txt.insert(tk.END, "正在拉取资产负债表和现金流数据（约5-10秒）……\n")
            txt.update()

            bs_list, cf_list, is_new = fetch_bs_cf(code, target_date=date_str)
            bs_loaded = len(bs_list) > 0

            dupont = _dupont_decomposition(base, bs_list)
            fcf = _calc_free_cash_flow(cf_list)
            bs_ratios = _analyze_bs_ratios(bs_list) if bs_loaded else {}
            all_warnings = _phase2_warnings(bs_ratios, fcf, base.get("fraud_warnings", []))

            ds = dict(base.get("dimension_scores", {}))
            if bs_ratios:
                dr = bs_ratios.get("debt_ratio", 60)
                ds["debt_safety"] = 85 if dr <= 40 else 70 if dr <= 55 else 50 if dr <= 70 else 30 if dr <= 85 else 10
                if bs_ratios.get("current_ratio", 2) < 1:
                    ds["debt_safety"] = max(10, ds["debt_safety"] - 15)
            if fcf.get("fcf") is not None and fcf["fcf"] < 0:
                ds["cash_flow_quality"] = min(ds.get("cash_flow_quality", 50), 40)

            _DW = {"cash_flow_quality": 0.25, "revenue_authenticity": 0.20, "profitability": 0.20, "debt_safety": 0.15, "fraud_warning": 0.20}
            new_total = sum(ds.get(k, 50) * w for k, w in _DW.items())

            detail = dict(base)
            detail.update({
                "total_score": round(new_total, 1),
                "dimension_scores": ds,
                "fraud_warnings": all_warnings,
                "fraud_warning_count": len(all_warnings),
                "dupont": dupont,
                "free_cash_flow": fcf,
                "bs_ratios": bs_ratios,
                "bs_loaded": bs_loaded,
            })

        except Exception as e:
            txt.delete("1.0", tk.END)
            txt.insert(tk.END, "❌ 加载失败: " + str(e) + "\n\n请检查网络连接后重试。")
            return

        txt.delete("1.0", tk.END)
        ts = detail["total_score"]
        ds = detail["dimension_scores"]
        rating = detail["rating"]
        ind = detail.get("industry", "")

        self.notebook.select(self.finance_tab)

        # ── 公司头 ──
        txt.insert(tk.END, "财务穿透 · 财报解读\n", "caption")
        txt.insert(tk.END, "─" * 60 + "\n", "sep")
        comp_name = detail.get("name", name_from_list)
        txt.insert(tk.END, code + "  " + comp_name + "\n", "h1")
        if ind:
            txt.insert(tk.END, ind + "  |  " + period_label + "\n", "dim_label")
        txt.insert(tk.END, "─" * 60 + "\n", "sep")
        score_tag = "excellent" if ts >= 85 else ("good" if ts >= 70 else ("fair" if ts >= 55 else ("weak" if ts >= 40 else "poor")))
        txt.insert(tk.END, "\n综合健康评分: " + format(ts, ".1f") + " / 100  ", "h2")
        txt.insert(tk.END, rating + "\n", score_tag)
        txt.insert(tk.END, "─" * 60 + "\n\n", "sep")

        # ── 五维评分 ──
        txt.insert(tk.END, "▌五维评分\n\n", "h2")
        dim_config = [
            ("cash_flow_quality", "① 现金流质量"),
            ("revenue_authenticity", "② 营收真实性"),
            ("profitability", "③ 盈利能力"),
            ("debt_safety", "④ 偿债安全"),
            ("fraud_warning", "⑤ 粉饰预警"),
        ]
        for dk, dlabel in dim_config:
            score = ds.get(dk, 0)
            bar_len = max(1, int(score / 100 * 25))
            bar = "█" * bar_len
            bar_tag = "score_bar"
            score_tag2 = "data_val_good" if score >= 70 else ("data_val" if score >= 40 else "data_val_bad")
            txt.insert(tk.END, dlabel + "  ", "h2")
            txt.insert(tk.END, format(score, "3.0f") + " ", score_tag2)
            txt.insert(tk.END, bar + "\n", bar_tag)
        txt.insert(tk.END, "\n")

        # ── 各维度解读 ──
        txt.insert(tk.END, "▌各维度解读\n\n", "h2")

        # 现金流
        cf_detail = detail["dimension_details"].get("cash_flow_quality", "")
        cf_score = ds.get("cash_flow_quality", 0)
        txt.insert(tk.END, "① 现金流质量: " + format(cf_score, ".0f") + "分\n", "h2")
        txt.insert(tk.END, "   " + cf_detail + "\n", "data_val")
        ocfps = detail.get("ocfps")
        eps_val = detail.get("eps")
        if ocfps is not None and eps_val is not None and eps_val > 0:
            ratio = ocfps / eps_val
            if ratio < 0:
                tag = "warn_mid"; cf_c = "经营现金流为负，账面利润缺乏真金白银支撑"
            elif ratio < 0.5:
                tag = "warn_mid"; cf_c = "利润含金量低，每赚1元只有" + format(ratio, ".2f") + "元现金到账"
            elif ratio < 0.7:
                tag = "warn_mid"; cf_c = "现金流覆盖利润的能力偏弱，需持续跟踪"
            elif ratio <= 3.0:
                tag = "warn_ok"; cf_c = "现金流与利润匹配良好，盈利质量扎实"
            else:
                tag = "warn_mid"; cf_c = "现金含量异常高，可能存在一次性收益或折旧等非经常性因素"
            txt.insert(tk.END, "   → " + cf_c + "\n", tag)
        elif ocfps is not None and ocfps < 0 and eps_val is not None and eps_val > 0:
            txt.insert(tk.END, "   → 纸面盈利但经营失血，EPS>0但经营现金流为负\n", "warn_mid")
        txt.insert(tk.END, "\n")

        # 营收真实性
        ra_detail = detail["dimension_details"].get("revenue_authenticity", "")
        ra_score = ds.get("revenue_authenticity", 0)
        txt.insert(tk.END, "② 营收真实性: " + format(ra_score, ".0f") + "分\n", "h2")
        txt.insert(tk.END, "   " + ra_detail + "\n", "data_val")
        ry = detail.get("revenue_yoy")
        ny = detail.get("net_profit_yoy")
        if ry is not None and ny is not None:
            gap = abs(ry - ny)
            if gap > 50:
                tag = "warn_mid"; ra_c = "营收与净利增速严重背离（差距" + format(gap, ".0f") + "pp），收入确认方式存疑"
            elif gap > 30:
                tag = "warn_mid"; ra_c = "营收与净利增速差距偏大（" + format(gap, ".0f") + "pp），需关注利润来源"
            elif gap > 15:
                tag = "data_val"; ra_c = "营收与净利增速基本匹配，但存在一定偏离"
            else:
                tag = "warn_ok"; ra_c = "营收与净利增速基本一致，成长质量可信"
            txt.insert(tk.END, "   → " + ra_c + "\n", tag)
        txt.insert(tk.END, "\n")

        # 盈利能力
        pf_detail = detail["dimension_details"].get("profitability", "")
        pf_score = ds.get("profitability", 0)
        txt.insert(tk.END, "③ 盈利能力: " + format(pf_score, ".0f") + "分\n", "h2")
        txt.insert(tk.END, "   " + pf_detail + "\n", "data_val")
        roe_val = detail.get("roe")
        if roe_val is not None:
            if roe_val < 0:
                tag = "warn_mid"; pf_c = "ROE为负，股东权益正在被侵蚀，核心业务盈利能力堪忧"
            elif roe_val < 5:
                tag = "warn_mid"; pf_c = "ROE低于A股中位数（4.3%），盈利能力偏弱"
            elif roe_val < 10:
                tag = "data_val"; pf_c = "ROE在行业中游水平，盈利尚可但无突出优势"
            elif roe_val < 15:
                tag = "warn_ok"; pf_c = "ROE良好，盈利能力处于行业中上"
            else:
                tag = "warn_ok"; pf_c = "ROE优秀（" + format(roe_val, ".1f") + "%），盈利能力突出"
            txt.insert(tk.END, "   → " + pf_c + "\n", tag)
        txt.insert(tk.END, "\n")

        # 偿债安全
        ds_detail = detail["dimension_details"].get("debt_safety", "")
        ds_score_val = ds.get("debt_safety", 0)
        txt.insert(tk.END, "④ 偿债安全: " + format(ds_score_val, ".0f") + "分\n", "h2")
        txt.insert(tk.END, "   " + ds_detail + "\n", "data_val")
        bs = detail.get("bs_ratios", {})
        debt_r = bs.get("debt_ratio")
        if debt_r is not None:
            if debt_r > 80:
                tag = "warn_mid"; ds_c = "资产负债率过高（" + format(debt_r, ".0f") + "%），偿债压力大"
            elif debt_r > 65:
                tag = "warn_mid"; ds_c = "资产负债率偏高（" + format(debt_r, ".0f") + "%），需关注流动性"
            elif debt_r < 40:
                tag = "warn_ok"; ds_c = "资产负债率较低（" + format(debt_r, ".0f") + "%），财务结构稳健"
            else:
                tag = "warn_ok"; ds_c = "资产负债率适中（" + format(debt_r, ".0f") + "%），偿债风险可控"
            txt.insert(tk.END, "   → " + ds_c + "\n", tag)
        bps = detail.get("bps")
        if bps is not None and bps < 1:
            txt.insert(tk.END, "   ⚠ 每股净资产低于1元，偿债能力极弱\n", "warn_mid")
        txt.insert(tk.END, "\n")

        # 粉饰预警
        fw_score = ds.get("fraud_warning", 0)
        warnings_list = detail.get("fraud_warnings", [])
        txt.insert(tk.END, "⑤ 粉饰预警: " + format(fw_score, ".0f") + "分\n", "h2")
        if not warnings_list:
            txt.insert(tk.END, "   ✓ 未检测到明显财务粉饰信号\n", "warn_ok")
        else:
            txt.insert(tk.END, "   ⚠ 检测到 " + str(len(warnings_list)) + " 个预警信号\n\n", "warn_mid")
            for w in warnings_list:
                sev = w.get("severity", "low")
                tag = "warn_high" if sev == "high" else ("warn_mid" if sev == "medium" else "data_val")
                severity_label = {"high": "🔴", "medium": "🟠", "low": "🟡"}
                txt.insert(tk.END, "   " + severity_label.get(sev, "  ") + " " + w["signal"] + "\n", tag)
                txt.insert(tk.END, "            " + w["detail"] + "\n", "data_val")
        txt.insert(tk.END, "\n")

        # ── 杜邦拆解 ──
        bs_loaded = detail.get("bs_loaded", False)
        dupont = detail.get("dupont", {})
        if bs_loaded and dupont.get("mode"):
            txt.insert(tk.END, "▌杜邦拆解\n\n", "h2")
            txt.insert(tk.END, "  盈利模式: " + dupont.get("mode", "") + "\n", "h2")
            np_m = dupont.get("net_profit_margin")
            at = dupont.get("asset_turnover")
            em = dupont.get("equity_multiplier")
            if np_m and at and em:
                txt.insert(tk.END, "  净利率 " + format(np_m, ".1f") + "%" + " × 周转率 " + format(at, ".3f") + " × 权益乘数 " + format(em, ".2f") + "\n", "data_val")
            fcf_data = detail.get("free_cash_flow", {})
            if fcf_data.get("fcf") is not None:
                fcf_val = fcf_data["fcf"]
                txt.insert(tk.END, "  自由现金流: " + format(fcf_val / 1e8, ".2f") + "亿  " + fcf_data.get("trend", "") + "\n", "data_val")
            bs = detail.get("bs_ratios", {})
            debt_r = bs.get("debt_ratio")
            curr_r = bs.get("current_ratio")
            bs_items = []
            if debt_r: bs_items.append("资产负债率 " + format(debt_r, ".1f") + "%")
            if curr_r: bs_items.append("流动比率 " + format(curr_r, ".2f"))
            if bs_items: txt.insert(tk.END, "  " + "  ".join(bs_items) + "\n", "data_val")
            txt.insert(tk.END, "\n")

        if not bs_loaded:
            txt.insert(tk.END, "▌资产负债表/现金流\n\n", "h2")
            txt.insert(tk.END, "  资产负债表数据未能加载\n", "warn_mid")
            txt.insert(tk.END, "  杜邦拆解、自由现金流、存贷双高检测需资产负债表数据\n", "data_val")
            txt.insert(tk.END, "  请稍后重试，或检查网络连接\n\n", "data_val")

        # ── 综合分析 ──
        txt.insert(tk.END, "\n" + "─" * 60 + "\n", "sep")
        txt.insert(tk.END, "▌综合分析\n\n", "h2")
        comp_name2 = detail.get("name", "")
        industry2 = detail.get("industry", "")
        total_warnings = detail.get("fraud_warning_count", 0)
        dupont_mode = dupont.get("mode", "")
        roe_val2 = detail.get("roe")
        gm_val2 = detail.get("gross_margin")
        ocfps2 = detail.get("ocfps")
        eps_val2 = detail.get("eps")
        lines = []
        if ts >= 85: quality = "优秀"; outlook = "盈利质量和财务结构均处于健康水平"
        elif ts >= 70: quality = "良好"; outlook = "基本面稳健，部分指标有提升空间"
        elif ts >= 55: quality = "中等"; outlook = "存在明显短板，需重点跟踪弱势维度"
        elif ts >= 40: quality = "偏弱"; outlook = "多项指标不理想，整体风险偏高"
        else: quality = "差"; outlook = "财务状况堪忧，建议规避"
        rating_grade = {"优秀":"A+","良好":"A","中等":"B+","偏弱":"B-","差":"C"}.get(rating,"?")
        lines.append("财务评级: " + rating_grade + " (" + quality + ") | " + outlook + "。")
        if roe_val2 is not None and gm_val2 is not None:
            if dupont_mode == "高利润驱动":
                lines.append("盈利模式为典型的高利润率驱动，净利率" + format(dupont.get("net_profit_margin", 0), ".1f") + "%、毛利率" + format(gm_val2, ".1f") + "%处于行业高位，盈利质量的核心观察点在于利润率能否维持，需关注竞争格局变化和成本端压力。")
            elif dupont_mode == "高杠杆驱动":
                lines.append("盈利模式依赖杠杆（权益乘数" + format(dupont.get("equity_multiplier", 0), ".2f") + "），资产负债率" + format(bs.get("debt_ratio", 0), ".0f") + "%，在利率上行周期中偿债压力将显著放大。")
            elif dupont_mode == "均衡型":
                lines.append("盈利模式为均衡型，净利率" + format(dupont.get("net_profit_margin", 0), ".1f") + "%、周转率" + format(dupont.get("asset_turnover", 0), ".2f") + "、杠杆" + format(dupont.get("equity_multiplier", 0), ".2f") + "三因子均处于合理范围，盈利稳定性较好。")
        if ocfps2 is not None and eps_val2 is not None and eps_val2 > 0:
            ratio2 = ocfps2 / eps_val2
            if ratio2 < 0:
                lines.append("最大的财务问题是经营现金流为负，账面利润缺乏现金支撑，盈利质量存疑。")
            elif ratio2 < 0.5:
                lines.append("盈利含金量不足——经营现金流仅为净利润的" + format(ratio2, ".0%") + "，每赚1元利润实际到账现金不足0.5元，需排查应收账款回款和营运资本占用情况。")
            elif ratio2 < 0.7:
                lines.append("现金流覆盖利润的能力偏弱（净现比" + format(ratio2, ".2f") + "），盈利质量一般。")
            elif ratio2 <= 3.0:
                lines.append("现金流状况健康，净现比" + format(ratio2, ".2f") + "处于CEIBS实证研究的0.7-3.0安全区间内，盈利含金量良好。")
        fcf_val = fcf.get("fcf")
        if fcf_val is not None:
            if fcf_val < 0:
                lines.append("自由现金流为负（" + format(abs(fcf_val) / 1e8, ".1f") + "亿缺口），经营自给能力不足，扩张依赖外部融资。")
            else:
                lines.append("自由现金流为正（" + format(fcf_val / 1e8, ".2f") + "亿），经营自给能力较强。")
        debt_r2 = bs.get("debt_ratio")
        cur_r2 = bs.get("current_ratio")
        if debt_r2 is not None:
            if debt_r2 > 70:
                lines.append("资产负债率偏高（" + format(debt_r2, ".0f") + "%），财务杠杆较大，" + ("短期偿债压力可控" if cur_r2 is not None and cur_r2 > 1 else "流动比率" + format(cur_r2, ".2f") + "低于1，短期偿债压力尤为突出") + "。")
            elif debt_r2 < 30:
                lines.append("资产负债率仅" + format(debt_r2, ".0f") + "%，财务非常保守，杠杆运用不足可能制约ROE提升空间。")
            else:
                lines.append("负债率" + format(debt_r2, ".0f") + "%、流动比率" + format(cur_r2, ".2f") + "，偿债结构合理，财务风险可控。")
        if total_warnings > 0:
            high_w = [w for w in detail.get("fraud_warnings", []) if w.get("severity") == "high"]
            lines.append("存在" + str(total_warnings) + "个财务预警信号，" + ("其中" + str(len(high_w)) + "个为高风险级别，需重点关注。" if high_w else "需定期跟踪排查。"))
        for line in lines:
            txt.insert(tk.END, "  " + line + "\n", "data_val")
        txt.insert(tk.END, "\n" + "─" * 60 + "\n", "sep")

        # ── 关键财务数据 ──
        txt.insert(tk.END, "▌关键财务数据\n\n", "h2")
        data_rows = [
            ("营业总收入", detail.get("revenue")), ("营收同比", detail.get("revenue_yoy"), "%"),
            ("净利润", detail.get("net_profit")), ("净利同比", detail.get("net_profit_yoy"), "%"),
            ("毛利率", detail.get("gross_margin"), "%"), ("ROE", detail.get("roe"), "%"),
            ("每股收益(EPS)", detail.get("eps")), ("每股净资产(BPS)", detail.get("bps")),
            ("每股经营现金流", detail.get("ocfps")),
        ]
        for row in data_rows:
            label = row[0]; val = row[1]; unit = row[2] if len(row) > 2 else ""
            if val is None: continue
            if label in ("营业总收入", "净利润"):
                val_str = format(val / 1e8, ".2f") + "亿"
            elif unit == "%": val_str = format(val, ".2f") + "%"
            else: val_str = format(val, ".4f")
            txt.insert(tk.END, "   " + label + ":  ", "data_key")
            txt.insert(tk.END, val_str + "\n", "data_val")
        txt.insert(tk.END, "\n" + "─" * 60 + "\n", "sep")
        txt.insert(tk.END, period_label + " 数据 | 含杜邦拆解+自由现金流+资产负债表分析\n", "caption")

    # ═══════════════════════════════════════════
    # 市场全景仪表盘
    # ═══════════════════════════════════════════

    def show_market_dashboard(self):
        self.notebook.select(self.hotspot_tab)

        def worker():
            try:
                from urllib.request import Request, urlopen

                # ── 获取三大指数 ──
                quotes = []
                ids = "sh000001,sz399001,sz399006"
                url = f"https://qt.gtimg.cn/q={ids}"
                req = Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://qt.gtimg.cn/"})
                raw = urlopen(req, timeout=8).read().decode("gbk", errors="ignore")
                for line in raw.strip().split(";"):
                    line = line.strip()
                    if "=" not in line or "~" not in line: continue
                    fields = line.split("~")
                    if len(fields) < 40: continue
                    quotes.append({
                        "name": fields[1].strip(), "code": fields[2].strip(),
                        "price": float(fields[3]) if fields[3] else 0,
                        "prev": float(fields[4]) if fields[4] else 1,
                        "change": float(fields[32]) if fields[32] else 0,
                    })

                # ── 全市场涨跌家数 ──
                market_breadth = {}
                try:
                    from v14_deep_engine import get_market_breadth
                    market_breadth = get_market_breadth(online=True)
                except Exception:
                    market_breadth = {"up": 0, "down": 0, "flat": 0, "total": 0,
                                      "source": "离线", "net_inflow": 0}

                # ── 自选股实时行情 ──
                self_stocks_ranked = []
                try:
                    stock_codes = [c for c, _ in self.data]
                    for batch_start in range(0, len(stock_codes), 50):
                        batch = stock_codes[batch_start:batch_start+50]
                        ids = ",".join([f"sh{c}" if c.startswith(("60","68")) else f"sz{c}" for c in batch])
                        url2 = f"https://qt.gtimg.cn/q={ids}"
                        req2 = Request(url2, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://qt.gtimg.cn/"})
                        raw2 = urlopen(req2, timeout=10).read().decode("gbk", errors="ignore")
                        for line in raw2.strip().split(";"):
                            if "=" not in line or "~" not in line: continue
                            flds = line.split("~")
                            if len(flds) < 40: continue
                            chg = float(flds[32]) if flds[32] else 0
                            price = float(flds[3]) if flds[3] else 0
                            # 盘后兜底：当日收盘价可能为空，用昨收填充
                            if price <= 0:
                                price = float(flds[4]) if flds[4] else 0
                            if price > 0:
                                self_stocks_ranked.append({
                                    "code": flds[2].strip(), "name": flds[1].strip(),
                                    "price": price, "change_pct": chg,
                                })
                    self_stocks_ranked.sort(key=lambda x: x["change_pct"], reverse=True)
                except Exception: pass

                # 盘后标记
                is_post_market = not self_stocks_ranked or all(
                    s["change_pct"] == 0 for s in self_stocks_ranked[:10])

                mb = market_breadth
                breadth_ok = mb.get("total", 0) > 100
                up_self = sum(1 for s in self_stocks_ranked if s["change_pct"] > 0) if self_stocks_ranked else 0
                avg_change = (sum(s["change_pct"] for s in self_stocks_ranked)/max(len(self_stocks_ranked),1)) if self_stocks_ranked else 0
                top_self = self_stocks_ranked[:20] if self_stocks_ranked else []

                # ── 全市场涨幅榜（可能盘后不可用）──
                all_market_top = []
                am_note = ""
                try:
                    from v14_market_data import get_strong_stocks as _gs
                    am_stocks, _ = _gs(online=True)
                    all_market_top = [{"code": s.code, "name": s.name, "price": s.price, "change_pct": s.change_pct}
                                      for s in am_stocks[:20]]
                except Exception:
                    pass
                if not all_market_top:
                    am_note = "（盘后不可用）"

                def draw():
                    for w in self.dashboard_frm.winfo_children():
                        w.destroy()

                    acc = lambda chg: "#C62828" if chg > 0 else ("#2E7D32" if chg < 0 else "#757575")

                    # ── 顶部：四大指标横排（均分宽度）──
                    top_bar = tk.Frame(self.dashboard_frm, bg='white')
                    top_bar.pack(fill=tk.X, padx=4, pady=(4, 0))

                    def _make_card(parent, title, value, sub, color, bg='white'):
                        f = tk.Frame(parent, bg=bg, highlightbackground='#e0e0e0',
                                     highlightthickness=1, padx=10, pady=8)
                        f.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=2)
                        tk.Label(f, text=title, font=("微软雅黑", 8), fg='#757575', bg=bg).pack(anchor='w')
                        tk.Label(f, text=value, font=("微软雅黑", 15, "bold"), fg=color, bg=bg).pack(anchor='w')
                        tk.Label(f, text=sub, font=("微软雅黑", 7), fg='#9E9E9E', bg=bg).pack(anchor='w')

                    card_colors = {"上证指数": "#E3F2FD", "深证成指": "#F3E5F5", "创业板指": "#FFEBEE"}
                    for q in quotes[:3]:
                        bg = card_colors.get(q["name"], 'white')
                        sgn = "+" if q["change"] > 0 else ""
                        _make_card(top_bar, q["name"],
                                   f"{q['price']:,.2f}  {sgn}{q['change']:+.2f}%",
                                   f"昨收 {q['prev']:,.2f}", acc(q["change"]), bg)

                    sclr = acc(avg_change)
                    _make_card(top_bar, "自选概览",
                               f"均涨跌 {avg_change:+.2f}%",
                               f"{up_self}涨 / {len(self_stocks_ranked)}只", sclr, "#FFF8E1")

                    # ── 第二行：市场广度（有数据→3卡片，无数据→1提示）──
                    if breadth_ok:
                        stat_bar = tk.Frame(self.dashboard_frm, bg='white')
                        stat_bar.pack(fill=tk.X, padx=4, pady=(4, 0))

                        up_pct = mb["up"] / max(mb["total"], 1) * 100
                        ni = mb.get("net_inflow", 0)
                        stat_items = [
                            ("上涨家数", f"{mb['up']}", f"占{up_pct:.0f}% · 共{mb['total']}只", "#C62828"),
                            ("下跌家数", f"{mb['down']}", f"占{(mb['down']/max(mb['total'],1)*100):.0f}%", "#2E7D32"),
                            ("主力净流入", f"{ni:+.0f}亿" if abs(ni) > 0.1 else "--",
                             "同花顺行业板块汇总", "#C62828" if ni > 0 else "#2E7D32"),
                        ]
                        for label, val, sub, clr in stat_items:
                            sf = tk.Frame(stat_bar, bg='#FAFAFA', highlightbackground='#e0e0e0',
                                          highlightthickness=1)
                            sf.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=1)
                            tk.Label(sf, text=label, font=("微软雅黑", 8), fg='#616161',
                                    bg='#FAFAFA').pack(anchor='w', padx=8, pady=(6, 0))
                            tk.Label(sf, text=val, font=("微软雅黑", 18, "bold"), fg=clr,
                                    bg='#FAFAFA').pack(anchor='w', padx=8)
                            tk.Label(sf, text=sub, font=("微软雅黑", 7), fg='#9E9E9E',
                                    bg='#FAFAFA').pack(anchor='w', padx=8, pady=(0, 5))

                        # ── 环境状态行（FOMC窗口 + 资金集中度）──
                        env_frame = tk.Frame(stat_bar, bg='#F5F5F5')
                        env_frame.pack(fill=tk.X, padx=4, pady=(6, 2))

                        # FOMC
                        try:
                            from _plus_.sector_power import is_fomc_window, calc_concentration
                            in_fomc, fomc_days, fomc_date = is_fomc_window()
                            if in_fomc:
                                fomc_text = f"⚡ FOMC窗口: 距{fomc_date}还有{fomc_days}天 (新闻信号降权)"
                                fomc_color = "#E65100"
                                fomc_bg = "#FFF3E0"
                            else:
                                fomc_text = f"FOMC: 距下次{fomc_date} {fomc_days}交易日 (正常)"
                                fomc_color = "#4CAF50"
                                fomc_bg = "#E8F5E9"

                            # 资金集中度
                            conc = calc_concentration(self.sector_map)
                            if conc["is_high"]:
                                conc_text = f"⚠ 资金集中度偏高 ({conc['short_pct']:.0f}% Top3: {', '.join(conc['top3_short'][:2])})"
                                conc_color = "#E65100"
                                conc_bg = "#FFF3E0"
                            else:
                                conc_text = f"资金集中度: 正常 ({conc['short_pct']:.0f}%)"
                                conc_color = "#757575"
                                conc_bg = "#FAFAFA"

                            tk.Label(env_frame, text=fomc_text, font=("微软雅黑", 8),
                                    fg=fomc_color, bg=fomc_bg,
                                    padx=8, pady=4).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 2))
                            tk.Label(env_frame, text=conc_text, font=("微软雅黑", 8),
                                    fg=conc_color, bg=conc_bg,
                                    padx=8, pady=4).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 0))

                            # FOMC窗口期：展示跨市场锚点
                            if in_fomc:
                                cross_frame = tk.Frame(self.dashboard_frm, bg='#E3F2FD',
                                                      highlightbackground='#90CAF9', highlightthickness=1)
                                cross_frame.pack(fill=tk.X, padx=4, pady=(2, 0), ipady=4)
                                try:
                                    from v14_cross_market import get_cross_market_quotes
                                    cm_quotes, _ = get_cross_market_quotes(online=True)
                                    parts = []
                                    if cm_quotes:
                                        for q in cm_quotes[:3]:
                                            parts.append(f"{q.get('name','')} {q.get('change_pct',0):+.1f}%")
                                    cross_text = "跨市场锚点(FOMC窗口): " + " | ".join(parts) if parts else "跨市场锚点数据暂不可用"
                                except Exception:
                                    cross_text = "跨市场锚点数据暂不可用"
                                tk.Label(cross_frame, text=cross_text,
                                        font=("微软雅黑", 9), fg='#1565C0', bg='#E3F2FD',
                                        padx=10, pady=4).pack(anchor='w')
                        except Exception:
                            pass

                    else:
                        notice = tk.Frame(self.dashboard_frm, bg='#FFF3E0', highlightbackground='#FFB74D',
                                         highlightthickness=1)
                        notice.pack(fill=tk.X, padx=6, pady=(4, 0), ipady=8)
                        tk.Label(notice, text="⏳ 同花顺板块数据暂不可用，自选股行情来自腾讯接口",
                                font=("微软雅黑", 9), fg='#E65100', bg='#FFF3E0').pack(padx=12, pady=6)

                    # ── 底部：两个表格（自适应高度）──
                    table_frame = tk.Frame(self.dashboard_frm, bg='white')
                    table_frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=(4, 0))

                    def _make_table(parent, title, rows, side=tk.LEFT):
                        f = tk.Frame(parent, bg='white', highlightbackground='#e0e0e0',
                                     highlightthickness=1)
                        f.pack(side=side, fill=tk.BOTH, expand=True, padx=2)

                        tk.Label(f, text=title, font=("微软雅黑", 10, "bold"),
                                fg='white', bg='#1a1a2e', padx=10, pady=5).pack(fill=tk.X)

                        lb = tk.Listbox(f, font=("微软雅黑", 9), bg='white', relief=tk.FLAT,
                                       selectbackground='#e0e0e0', activestyle='none',
                                       exportselection=False)
                        lb.pack(fill=tk.BOTH, expand=True, padx=4, pady=(4, 2))

                        if rows:
                            for s in rows[:30]:
                                code = s['code']; name = s['name']
                                price = f"{s['price']:.2f}"; chg = s['change_pct']
                                lb.insert(tk.END, f"  {code:>6}  {name:<8}  {price:>8}  {chg:>+7.1f}%")
                                idx = lb.size() - 1
                                lb.itemconfig(idx, fg='#C62828' if chg > 0 else '#2E7D32')
                        else:
                            lb.insert(tk.END, "  数据加载中...")
                            lb.itemconfig(0, fg='#9E9E9E')

                        def _lb_wheel(e, lb=lb):
                            lb.yview_scroll(int(-1 * (e.delta / 120)), "units")
                            return "break"
                        lb.bind("<MouseWheel>", _lb_wheel)
                        return f

                    _make_table(table_frame, "自选强势股 Top 20", top_self, tk.LEFT)

                    if all_market_top:
                        _make_table(table_frame, "全市场涨幅榜 Top 20", all_market_top, tk.LEFT)
                    else:
                        # 盘后回退面板：紧凑展示自选统计 + 提示
                        f = tk.Frame(table_frame, bg='white', highlightbackground='#e0e0e0',
                                     highlightthickness=1)
                        f.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=2)
                        tk.Label(f, text="全市场涨幅榜 Top 20", font=("微软雅黑", 10, "bold"),
                                fg='white', bg='#1a1a2e', padx=10, pady=5).pack(fill=tk.X)

                        info = tk.Frame(f, bg='white')
                        info.pack(fill=tk.BOTH, expand=True, padx=16, pady=12)

                        # 自选统计摘要
                        summary_lines = [
                            f"自选股共 {len(self_stocks_ranked)} 只",
                            f"上涨 {up_self} 只（{up_self/max(len(self_stocks_ranked),1)*100:.0f}%）",
                            f"平均涨跌 {avg_change:+.2f}%",
                        ]
                        if breadth_ok:
                            summary_lines.append(
                                f"全市场 {mb['up']}涨 / {mb['down']}跌 / {mb.get('flat',0)}平（共{mb['total']}只）")

                        for txt in summary_lines:
                            tk.Label(info, text=txt, font=("微软雅黑", 11), fg='#374151',
                                    bg='white', anchor='w').pack(fill=tk.X, pady=3)

                        tk.Frame(info, bg='#f0f0f0', height=1).pack(fill=tk.X, pady=(10, 8))

                        tk.Label(info, text="📌 盘后状态：涨幅榜仅交易时段可用（9:30-15:00）",
                                font=("微软雅黑", 9), fg='#9E9E9E', bg='white',
                                anchor='w').pack(fill=tk.X)
                        tk.Label(info, text="   自选强势股排名基于腾讯实时行情，已正常展示",
                                font=("微软雅黑", 8), fg='#BDBDBD', bg='white',
                                anchor='w').pack(fill=tk.X)

                    # ── 底部信息栏 ──
                    tk.Frame(self.dashboard_frm, bg='#e0e0e0', height=1).pack(fill=tk.X, padx=6, pady=(6, 0))
                    tk.Label(self.dashboard_frm,
                            text=f"  腾讯行情 qt.gtimg.cn · {datetime.now().strftime('%Y-%m-%d %H:%M')} · {len(self_stocks_ranked)}只自选股",
                            font=("微软雅黑", 7), fg='#BDBDBD', bg='white', padx=8, pady=4,
                            anchor='w').pack(fill=tk.X)

                    self.set_status("市场仪表盘已更新")
                    self._update_source_color("tencent", "ok")

                self.after(0, draw)

            except Exception as e:
                self.after(0, lambda: self._show_dashboard_error(str(e)))

        threading.Thread(target=worker, daemon=True).start()

    def _show_dashboard_error(self, err):
        for w in self.dashboard_frm.winfo_children():
            w.destroy()
        tk.Label(self.dashboard_frm, text=f"市场数据获取失败\n\n{err[:120]}\n\n请检查网络连接，稍后重试",
                 font=("微软雅黑", 11), fg="#d32f2f", bg="white", justify=tk.LEFT).pack(expand=True)
        self.set_status("市场仪表盘数据获取失败")

    # ═══════════════════════════════════════════
    # 持仓管理
    # ═══════════════════════════════════════════

    def show_portfolio(self):
        """持仓总览：当前持仓 + 盈亏统计 + 止损/止盈检查。"""
        self.notebook.select(self.portfolio_tab)
        self.t10.delete(1.0, tk.END)

        def worker():
            try:
                from v15_portfolio import get_positions, get_portfolio_summary, update_market_price
                positions = get_positions()
                summary = get_portfolio_summary()

                lines = []
                lines.append(f"{'='*70}")
                lines.append(f"  持仓管理  |  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
                lines.append(f"{'='*70}")

                # 统计
                lines.append(f"\n  📊 账户概览")
                lines.append(f"  当前持仓: {summary['active_count']}只 | 总成本: {summary['total_cost']:.0f}元 | 总市值: {summary['total_market']:.0f}元")
                pnl_sign = "+" if summary['total_pnl'] >= 0 else ""
                lines.append(f"  浮动盈亏: {pnl_sign}{summary['total_pnl']:.0f}元 ({pnl_sign}{summary['total_pnl_pct']:.1f}%)")
                if summary['closed_count'] > 0:
                    lines.append(f"  已平仓: {summary['closed_count']}笔 | 胜率: {summary['win_rate']:.0f}% | 累计盈亏: {summary['total_hist_pnl']:+.0f}元")
                    lines.append(f"  盈利因子: {summary['profit_factor']} | 均盈: {summary['avg_win']:+.0f} | 均亏: {summary['avg_loss']:+.0f}")

                # 活跃持仓
                active = [p for p in positions if p.get("status") == "持有"]
                all_alerts = []

                if active:
                    # 先更新市价
                    for p in active:
                        try:
                            from urllib.request import Request, urlopen
                            code = p["code"]
                            pfx = "sh" if code.startswith(("60", "68")) else "sz"
                            url = f"https://qt.gtimg.cn/q={pfx}{code}"
                            req = Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://qt.gtimg.cn/"})
                            with urlopen(req, timeout=5) as resp:
                                raw = resp.read().decode("gbk", errors="ignore")
                            fields = raw.split("~")
                            if len(fields) > 3:
                                price = float(fields[3]) if fields[3] else p["buy_price"]
                                result = update_market_price(p["id"], price)
                                all_alerts.extend(result.get("alerts", []))
                        except Exception:
                            result = update_market_price(p["id"], p["buy_price"])

                    lines.append(f"\n  ┌─ 当前持仓 ─{'─'*60}")
                    lines.append(f"  │ {'代码':<8} {'名称':<10} {'成本':>8} {'现价':>8} {'盈亏%':>8} {'持有天':>6} {'止损线%':>8}")
                    lines.append(f"  │ {'─'*60}")

                    for p in active:
                        cp = p.get("current_price", p["buy_price"])
                        pnl_pct = p.get("current_pnl_pct", (cp / p["buy_price"] - 1) * 100)
                        try:
                            bd = datetime.strptime(p["buy_date"], "%Y-%m-%d")
                            days = (datetime.now() - bd).days
                        except Exception:
                            days = 0
                        pnl_str = f"{pnl_pct:+.1f}%"
                        flag = "🔴" if pnl_pct <= -p.get("stop_loss_pct", 7) else ("🟢" if pnl_pct > 0 else "🟡")
                        lines.append(
                            f"  │ {flag}{p['code']:<6} {p['name']:<8} "
                            f"{p['buy_price']:>8.2f} {cp:>8.2f} {pnl_str:>8} {days:>5}d {p['stop_loss_pct']:>7.0f}%"
                        )
                    lines.append(f"  └{'─'*70}")
                else:
                    lines.append(f"\n  (暂无持仓)")

                # 警报
                if all_alerts:
                    lines.append(f"\n  🚨 风控提醒")
                    for a in all_alerts:
                        lines.append(f"    [{a['level']}] {a['msg']}")

                lines.append(f"\n{'='*70}")

                self.t10.after(0, lambda: (self.t10.delete(1.0, tk.END),
                                           self.t10.insert(tk.END, "\n".join(lines))))
                self.t10.after(0, lambda: self.notebook.select(self.portfolio_tab))
                self.t10.after(0, lambda: self.set_status(
                    f"持仓: {summary['active_count']}只 | 浮动盈亏{summary['total_pnl']:+.0f}"))

            except Exception as e:
                self.t10.after(0, lambda e=e: self.t10.insert(tk.END, f"❌ 持仓加载失败: {e}"))

        threading.Thread(target=worker, daemon=True).start()

    def add_portfolio_dialog(self):
        """新增持仓弹窗。"""
        dialog = tk.Toplevel(self)
        dialog.title("新增持仓")
        dialog.geometry("400x360")
        dialog.configure(bg="#ffffff")
        dialog.transient(self)
        dialog.grab_set()

        # 默认填充当前选中的股票
        sel = self.get_selected()
        default_code = ""
        default_name = ""
        if sel:
            default_code = sel[0]
            parts = sel[1].split(None, 1)
            default_name = parts[1] if len(parts) > 1 else sel[1]

        rows = [
            ("股票代码:", "code", default_code),
            ("股票名称:", "name", default_name),
            ("买入价格:", "price", ""),
            ("买入数量(股):", "shares", "100"),
            ("买入日期:", "date", datetime.now().strftime("%Y-%m-%d")),
            ("止损比例(%):", "stop", "7"),
            ("止盈比例(%):", "profit", "15"),
        ]

        entries = {}
        for i, (label, key, default) in enumerate(rows):
            tk.Label(dialog, text=label, font=("微软雅黑", 10), bg="#ffffff").grid(
                row=i, column=0, sticky="e", padx=10, pady=6)
            var = tk.StringVar(value=default)
            ttk.Entry(dialog, textvariable=var, font=("微软雅黑", 10), width=20).grid(
                row=i, column=1, sticky="w", padx=10, pady=6)
            entries[key] = var

        def do_add():
            try:
                from v15_portfolio import add_position
                code = entries["code"].get().strip()
                name = entries["name"].get().strip()
                price = float(entries["price"].get())
                shares = int(entries["shares"].get())
                date = entries["date"].get().strip()
                stop = float(entries["stop"].get())
                profit = float(entries["profit"].get())

                if not code or not name or price <= 0:
                    messagebox.showwarning("输入错误", "请填写完整信息")
                    return

                add_position(code, name, price, shares, date, stop, profit,
                            notes=f"V15择时系统录入")
                dialog.destroy()
                self.set_status(f"已添加持仓: {name}({code})")
                self.show_portfolio()
            except ValueError:
                messagebox.showwarning("输入错误", "价格/数量/比例必须是数字")
            except Exception as e:
                messagebox.showerror("错误", str(e))

        ttk.Button(dialog, text="确认添加", command=do_add).grid(
            row=len(rows), column=0, columnspan=2, pady=15)

    def close_portfolio_dialog(self):
        """平仓弹窗 — 从持仓列表中选择。"""
        from v15_portfolio import get_positions
        active = [p for p in get_positions() if p.get("status") == "持有"]
        if not active:
            messagebox.showinfo("提示", "当前没有持仓")
            return

        dialog = tk.Toplevel(self)
        dialog.title("平仓操作")
        dialog.geometry("400x250")
        dialog.configure(bg="#ffffff")
        dialog.transient(self)
        dialog.grab_set()

        tk.Label(dialog, text="选择要平仓的股票:", font=("微软雅黑", 10, "bold"), bg="#ffffff").pack(pady=10)

        lb = tk.Listbox(dialog, font=("微软雅黑", 10), height=6)
        lb.pack(fill=tk.X, padx=15)
        for p in active:
            lb.insert(tk.END, f"{p['name']}({p['code']}) 成本{p['buy_price']:.2f} {p['shares']}股")
        lb.select_set(0)

        tk.Label(dialog, text="平仓价格:", font=("微软雅黑", 10), bg="#ffffff").pack(pady=(10, 0))
        price_var = tk.StringVar()
        ttk.Entry(dialog, textvariable=price_var, font=("微软雅黑", 10), width=15).pack()

        def do_close():
            try:
                sel_idx = lb.curselection()
                if not sel_idx:
                    return
                pos = active[sel_idx[0]]
                price = float(price_var.get())
                if price <= 0:
                    return
                from v15_portfolio import close_position
                close_position(pos["id"], price)
                dialog.destroy()
                self.set_status(f"已平仓: {pos['name']}")
                self.show_portfolio()
            except Exception as e:
                messagebox.showerror("错误", str(e))

        ttk.Button(dialog, text="确认平仓", command=do_close).pack(pady=10)

    def delete_portfolio_dialog(self):
        """删除持仓弹窗 — 从全部持仓记录中选择（含已平仓）。"""
        from v15_portfolio import get_positions
        all_pos = get_positions()
        if not all_pos:
            messagebox.showinfo("提示", "没有任何持仓记录")
            return

        dialog = tk.Toplevel(self)
        dialog.title("删除持仓")
        dialog.geometry("460x300")
        dialog.configure(bg="#ffffff")
        dialog.transient(self)
        dialog.grab_set()

        tk.Label(dialog, text="选择要删除的持仓记录（含已平仓）:",
                 font=("微软雅黑", 10, "bold"), bg="#ffffff").pack(pady=10)

        lb = tk.Listbox(dialog, font=("微软雅黑", 10), height=8)
        lb.pack(fill=tk.X, padx=15)
        for p in all_pos:
            status = p.get("status", "持有")
            lb.insert(tk.END,
                      f"[{status}] {p['name']}({p['code']}) "
                      f"成本{p['buy_price']:.2f} {p['shares']}股 "
                      f"{p.get('buy_date', '')}")
        lb.select_set(0)

        tk.Label(dialog, text="删除后不可恢复，请确认", font=("微软雅黑", 9),
                 bg="#ffffff", fg="#d32f2f").pack(pady=(8, 0))

        def do_delete():
            try:
                sel_idx = lb.curselection()
                if not sel_idx:
                    return
                pos = all_pos[sel_idx[0]]
                if not messagebox.askyesno("确认删除",
                    f"确定要删除 {pos['name']}({pos['code']}) 的持仓记录吗？\n此操作不可恢复。"):
                    return
                from v15_portfolio import delete_position
                delete_position(pos["id"])
                dialog.destroy()
                self.set_status(f"已删除持仓: {pos['name']}")
                self.show_portfolio()
            except Exception as e:
                messagebox.showerror("错误", str(e))

        ttk.Button(dialog, text="确认删除", command=do_delete).pack(pady=10)

    # ═══════════════════════════════════════════
    # 板块历史资金
    # ═══════════════════════════════════════════

    def collect_sector_history_today(self):
        """采集今日板块资金流快照。"""
        self.notebook.select(self.sector_history_tab)
        self.t_sector_history.delete(1.0, tk.END)
        self.t_sector_history.insert(tk.END, "⏳ 正在采集今日板块资金流（约需20秒）...\n")
        self.set_status("板块历史资金: 采集中...")

        def worker():
            try:
                from _plus_.sector_history_collector import SectorHistoryCollector
                result = SectorHistoryCollector.collect()
                if result.get("ok"):
                    msg = (f"✅ 采集完成: {result['count']} 个板块已保存"
                           f"（来源: {result.get('source','?')}）\n")
                else:
                    msg = f"❌ 采集失败: {result.get('error','未知错误')}\n"
                self.t_sector_history.after(0, lambda: (
                    self.t_sector_history.delete(1.0, tk.END),
                    self.t_sector_history.insert(tk.END, msg)))
                self.t_sector_history.after(0, lambda: self.show_sector_history())
            except Exception as e:
                self.t_sector_history.after(0, lambda: (
                    self.t_sector_history.delete(1.0, tk.END),
                    self.t_sector_history.insert(tk.END, f"❌ 采集异常: {e}\n")))
            self.t_sector_history.after(0, lambda: self.set_status("板块历史资金: 就绪"))

        threading.Thread(target=worker, daemon=True).start()

    def show_sector_history(self):
        """查询并显示板块历史资金数据。"""
        self.notebook.select(self.sector_history_tab)
        self.t_sector_history.delete(1.0, tk.END)
        self.t_sector_history.insert(tk.END, "⏳ 查询中...\n")
        self.set_status("板块历史资金: 查询中...")

        def worker():
            try:
                from _plus_.sector_history_collector import SectorHistoryCollector

                # 汇总信息
                s = SectorHistoryCollector.summary()
                lines = []
                if s and s['total_days'] > 0:
                    lines.append(f"📊 已收集 {s['total_days']} 天数据"
                                 f"（{s['first_date']} ~ {s['last_date']}）"
                                 f" | {s['total_sectors']} 个板块 | {s['total_records']} 条记录")
                    lines.append("")

                # 取最近 6 天各前 10
                dates = SectorHistoryCollector.list_dates()[:6]
                for d in dates:
                    rows = SectorHistoryCollector.query(date_from=d, date_to=d, limit=12)
                    if not rows:
                        continue
                    lines.append(f"── {d} ──")
                    # 表头
                    lines.append(f"  {'板块':<12s} {'涨跌幅':>7s} {'主力净额':>10s}"
                                 f" {'超大单':>9s} {'大单':>9s} {'中单':>9s} {'小单':>9s}")
                    for r in rows:
                        name = r["name"][:10]
                        chg = f"{r['change_pct']:+.2f}%"
                        main = f"{r['main_net_inflow']/1e8:+.1f}亿"
                        sl = f"{r['super_large_inflow']/1e8:+.1f}亿" if r['super_large_inflow'] else "-"
                        ll = f"{r['large_inflow']/1e8:+.1f}亿" if r['large_inflow'] else "-"
                        md = f"{r['mid_inflow']/1e8:+.1f}亿" if r['mid_inflow'] else "-"
                        sm = f"{r['small_inflow']/1e8:+.1f}亿" if r['small_inflow'] else "-"
                        lines.append(f"  {name:<12s} {chg:>7s} {main:>10s}"
                                     f" {sl:>9s} {ll:>9s} {md:>9s} {sm:>9s}")
                    lines.append("")

                if not lines:
                    lines = ["暂无历史数据。点击「采集今日数据」开始积累。"]

                text = "\n".join(lines)
                self.t_sector_history.after(0, lambda: (
                    self.t_sector_history.delete(1.0, tk.END),
                    self.t_sector_history.insert(tk.END, text)))

                # 更新汇总标签
                summary_text = f"{s['total_days']}天 | {s['total_sectors']}板块" if s else "0天 | 0板块"
                self.sh_summary_label.after(0, lambda t=summary_text: self.sh_summary_label.config(text=t))

                self.t_sector_history.after(0, lambda: self.set_status("板块历史资金: 已更新"))
            except Exception as e:
                self.t_sector_history.after(0, lambda: (
                    self.t_sector_history.delete(1.0, tk.END),
                    self.t_sector_history.insert(tk.END, f"❌ 查询异常: {e}\n")))
                self.t_sector_history.after(0, lambda: self.set_status("板块历史资金: 错误"))

        threading.Thread(target=worker, daemon=True).start()

    # ═══════════════════════════════════════════
    # 盘前雷达
    # ═══════════════════════════════════════════

    def show_premarket(self):
        """盘前雷达：港美股锚点 → 自选股今日关注/回避。"""
        self.notebook.select(self.premarket_tab)
        self.t_premarket.delete(1.0, tk.END)
        self.t_premarket.insert(tk.END, "⏳ 正在生成盘前雷达...\n")

        def worker():
            try:
                from v14_cross_market import get_cross_market_quotes, cross_market_theme_boost
                quotes, src = get_cross_market_quotes(online=True)
                boosts = cross_market_theme_boost(quotes)

                # 主题 → 自选股映射（精确公司名 + 排除误匹配）
                theme_map = {
                    "AI算力": ["中科曙光", "浪潮信息", "科大讯飞", "工业富联", "海光信息", "寒武纪",
                              "紫光股份", "拓维信息", "神州数码", "数据港", "光环新网", "奥飞数据",
                              "算力", "人工智能", "AI", "大模型", "服务器", "光模块", "数据中心",
                              "百度", "三六零", "昆仑万维", "云从", "商汤"],
                    "半导体": ["中芯国际", "华虹", "兆易创新", "长鑫", "韦尔股份", "卓胜微", "北方华创",
                             "中微公司", "沪硅产业", "士兰微", "半导体", "芯片", "光刻", "封测", "晶圆",
                             "通富微电", "长电科技", "景嘉微", "澜起", "芯原"],
                    "新能源汽车": ["比亚迪", "宁德时代", "长安汽车", "长城汽车", "江淮汽车", "赛力斯",
                                "北汽蓝谷", "锂电池", "固态电池", "锂矿", "锂电",
                                "亿纬锂能", "天齐锂业", "赣锋锂业", "国轩高科", "欣旺达",
                                "理想", "小鹏", "蔚来", "小米", "华为汽车"],
                    "互联网": ["腾讯", "阿里巴巴", "美团", "京东", "网易", "拼多多", "唯品会",
                             "快手", "哔哩哔哩", "互联网", "电商", "在线"],
                    "消费电子": ["立讯精密", "歌尔股份", "蓝思科技", "京东方", "TCL科技",
                              "苹果", "华为", "小米", "传音", "安克创新", "漫步者",
                              "电子", "声学", "面板", "镜头", "模组", "组装"],
                    "医药": ["药明康德", "药明生物", "恒瑞医药", "迈瑞医疗", "百济神州",
                           "泰格医药", "凯莱英", "康龙化成", "昭衍新药", "医药", "生物",
                           "医疗", "创新药", "CRO", "疫苗", "基因"],
                    "指数": [],  # 指数仅作大盘环境参考，不映射个股
                }
                theme_exclude = {
                    "新能源汽车": ["宝新能源"],
                    "消费电子": ["电子城", "电子烟"],
                }

                # 匹配自选股
                alerts = {}
                for code, line in self.data:
                    for theme, keywords in theme_map.items():
                        # 误匹配排除
                        excluded = any(ex in line for ex in theme_exclude.get(theme, []))
                        if excluded:
                            continue
                        if any(kw in line for kw in keywords):
                            alerts.setdefault(theme, []).append((code, line))

                lines = []
                lines.append(f"{'='*70}")
                lines.append(f"  盘前雷达 — 港美股锚点映射")
                lines.append(f"  数据源: {src}  |  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
                lines.append(f"{'='*70}")

                # 锚点行情 — 分三区：美股指数 → 美股个股 → 港股
                idx_quotes = [q for q in quotes if q.theme == "指数"]
                us_quotes = [q for q in quotes if q.market == "US" and q.theme != "指数"]
                hk_quotes = [q for q in quotes if q.market == "H"]

                def _fmt_quotes(qlist, title, width=70):
                    buf = [f"\n  ┌─ {title} ─{'─'* (width - len(title) - 6)}"]
                    for q in sorted(qlist, key=lambda x: abs(x.change_pct), reverse=True):
                        icon = "🟢" if q.change_pct > 2 else ("🔴" if q.change_pct < -2 else "⚪")
                        buf.append(f"  │ {icon} {q.symbol:<10} {q.name:<14} {q.change_pct:>+7.2f}%  {q.theme}")
                    buf.append(f"  └{'─'*width}")
                    return buf

                if idx_quotes:
                    lines += _fmt_quotes(idx_quotes, "美股三大指数")
                if us_quotes:
                    lines += _fmt_quotes(us_quotes, "美股核心锚点")
                if hk_quotes:
                    lines += _fmt_quotes(hk_quotes, "港股核心锚点")

                # ── 按市场×主题聚合，计算平均涨跌幅 ──
                def _theme_rank(qlist, label, width=70):
                    """聚合某市场的主题涨跌排名，返回展示文本和原始数据。"""
                    groups: dict[str, list[float]] = {}
                    for q in qlist:
                        if q.theme == "指数":
                            continue
                        groups.setdefault(q.theme, []).append(q.change_pct)
                    if not groups:
                        return [], {}
                    ranked = {}
                    for t, vals in groups.items():
                        avg = sum(vals) / len(vals)
                        ranked[t] = {"avg": round(avg, 2), "count": len(vals), "stocks": vals}
                    # 按平均涨跌降序
                    sorted_items = sorted(ranked.items(), key=lambda x: x[1]["avg"], reverse=True)
                    buf = [f"\n  ┌─ {label}板块排名（按主题平均涨跌） ─{'─'*max(1, width - len(label) - 20)}"]
                    for i, (t, info) in enumerate(sorted_items, 1):
                        avg = info["avg"]
                        icon = "🟢" if avg > 2 else ("🔴" if avg < -2 else "⚪")
                        bar = "█" * min(10, max(0, int((avg + 5) / 1.5))) if avg >= 0 else ""
                        buf.append(f"  │ {icon} {i:>2}. {t:<8} 平均{avg:>+6.2f}%  {bar}  ({info['count']}标的)")
                    buf.append(f"  └{'─'*width}")
                    return buf, ranked

                us_theme_buf, us_ranked = _theme_rank(us_quotes, "美股")
                hk_theme_buf, hk_ranked = _theme_rank(hk_quotes, "港股")

                if us_theme_buf:
                    lines += us_theme_buf
                if hk_theme_buf:
                    lines += hk_theme_buf

                # 主题映射
                lines.append(f"\n  ┌─ 主题映射到A股自选股 ─{'─'*48}")
                for theme, boost_info in boosts.items():
                    boost = boost_info["boost"]
                    icon = "🔥" if boost > 3 else ("✅" if boost > 0 else ("⚠️" if boost > -3 else "🔻"))
                    stocks_in_theme = alerts.get(theme, [])
                    stock_names = "、".join([s[1].split()[-1] if " " in s[1] else s[1] for s in stocks_in_theme[:5]])
                    tag = f"  [{icon} {theme}] 修正{boost:+.1f} | 自选股: {stock_names}"
                    if len(stocks_in_theme) > 5:
                        tag += f" ...+{len(stocks_in_theme)-5}只"
                    lines.append(tag)
                lines.append(f"  └{'─'*70}")

                # ── 数据驱动操作建议（不幻觉） ──
                lines.append(f"\n  📌 今日操作建议:")
                lines.append(f"  {'─'*60}")

                # 1. 大盘环境判断（基于三大指数）
                idx_avg = 0.0
                if idx_quotes:
                    idx_avg = sum(q.change_pct for q in idx_quotes) / len(idx_quotes)
                if idx_avg < -3:
                    lines.append(f"  🔴 美股三大指数均重挫（纳指{dict((q.symbol, q.change_pct) for q in idx_quotes).get('.IXIC', 0):+.2f}%），")
                    lines.append(f"     全球风险偏好骤降，A股今日大概率低开，建议减仓观望。")
                elif idx_avg < -1:
                    lines.append(f"  🟡 美股三大指数偏弱（均值{idx_avg:+.2f}%），")
                    lines.append(f"     外围情绪偏冷，A股开盘可能承压，盘中关注北向资金动向。")
                elif idx_avg > 1:
                    lines.append(f"  🟢 美股三大指数偏强（均值{idx_avg:+.2f}%），")
                    lines.append(f"     外围情绪偏暖，A股高开概率较大，可关注强势板块机会。")
                else:
                    lines.append(f"  ⚪ 美股三大指数窄幅波动（均值{idx_avg:+.2f}%），")
                    lines.append(f"     外围无明确方向，A股大概率按自身节奏运行。")

                # 2. 跨市场共振主题（美股和港股同向的主题）
                common_themes = set(us_ranked.keys()) & set(hk_ranked.keys())
                common_themes.discard("指数")
                resonance = []
                for t in common_themes:
                    us_avg = us_ranked[t]["avg"]
                    hk_avg = hk_ranked[t]["avg"]
                    combined = us_avg * 0.5 + hk_avg * 0.5  # 等权
                    direction = 1 if (us_avg > 0 and hk_avg > 0) else (-1 if (us_avg < 0 and hk_avg < 0) else 0)
                    resonance.append((t, us_avg, hk_avg, combined, direction))

                # 同向上涨
                up_themes = [(t, us_a, hk_a, c, d) for t, us_a, hk_a, c, d in resonance if d == 1]
                up_themes.sort(key=lambda x: x[3], reverse=True)
                # 同向下跌
                dn_themes = [(t, us_a, hk_a, c, d) for t, us_a, hk_a, c, d in resonance if d == -1]
                dn_themes.sort(key=lambda x: x[3])

                if up_themes:
                    lines.append(f"")
                    lines.append(f"  ✅ 跨市场共振上行主题：")
                    for t, us_a, hk_a, c, _d in up_themes[:4]:
                        a_stocks = alerts.get(t, [])
                        a_names = "、".join([s[1].split()[-1] if " " in s[1] else s[1] for s in a_stocks[:3]])
                        lines.append(f"     ▸ {t}：美股{us_a:+.2f}% / 港股{hk_a:+.2f}% 同步走强")
                        if a_names:
                            lines.append(f"       关注A股: {a_names}")

                if dn_themes:
                    lines.append(f"")
                    lines.append(f"  ⚠️ 跨市场共振下行主题：")
                    for t, us_a, hk_a, c, _d in dn_themes[:4]:
                        a_stocks = alerts.get(t, [])
                        a_names = "、".join([s[1].split()[-1] if " " in s[1] else s[1] for s in a_stocks[:3]])
                        lines.append(f"     ▸ {t}：美股{us_a:+.2f}% / 港股{hk_a:+.2f}% 同步走弱")
                        if a_names:
                            lines.append(f"       谨慎回避: {a_names}")

                # 3. 分歧主题（一个市场涨一个跌）
                diverge = [(t, us_a, hk_a, c, d) for t, us_a, hk_a, c, d in resonance if d == 0]
                if diverge:
                    diverge.sort(key=lambda x: abs(x[1] - x[2]), reverse=True)
                    lines.append(f"")
                    lines.append(f"  🔀 跨市场分歧（参考价值有限）：")
                    for t, us_a, hk_a, _c, _d in diverge[:3]:
                        lines.append(f"     ▸ {t}：美股{us_a:+.2f}% vs 港股{hk_a:+.2f}%，方向不一致，A股跟随哪边不确定")

                lines.append(f"")
                lines.append(f"  💡 提醒：港美股锚点反映的是外围资金对主题的态度，A股另受国内")
                lines.append(f"     政策、资金面、情绪面影响，以上仅供参考，请结合自身判断。")
                lines.append(f"{'='*70}")

                self.t_premarket.after(0, lambda: (self.t_premarket.delete(1.0, tk.END),
                                          self.t_premarket.insert(tk.END, "\n".join(lines))))
                self.t_premarket.after(0, lambda: self.notebook.select(self.premarket_tab))
                self.t_premarket.after(0, lambda: self.set_status("盘前雷达已生成"))
                self.after(0, lambda: self.update_source_status(cross_source=src))

            except Exception as e:
                self.t_premarket.after(0, lambda e=e: self.t_premarket.insert(tk.END, f"\n❌ 盘前雷达生成失败: {e}"))

        threading.Thread(target=worker, daemon=True).start()

    # ═══════════════════════════════════════════
    # 回测验证
    # ═══════════════════════════════════════════

    def run_backtest_single(self):
        """单股V15全链路回测：资金曲线 + 信号统计。"""
        s = self.get_selected()
        if not s:
            self.t_single_backtest.delete(1.0, tk.END)
            self.t_single_backtest.insert(tk.END, "请先选择一只股票\n")
            return
        code, name_full = s
        self.bt_stock_label.config(text=f"{name_full}")
        self.notebook.select(self.single_bt_tab)
        self.t_single_backtest.delete(1.0, tk.END)
        self.t_single_backtest.insert(tk.END, f"⏳ {name_full} V15全链路回测中（逐日模拟）...\n")
        self.set_status(f"回测: {name_full}")

        def worker():
            try:
                df = load_stock_tdx(code)
                if df is None or len(df) < 250:
                    self.t_single_backtest.after(0, lambda: self.t_single_backtest.insert(tk.END, "❌ 数据不足250日\n"))
                    return

                c = df["收盘"].values.astype(float)
                h = df["最高"].values.astype(float)
                l = df["最低"].values.astype(float)
                v = df["成交量"].values.astype(float)
                dates_arr = df["date"].values
                n = len(c)

                # 按买入信号模拟交易
                equity = [1.0]  # 资金曲线（净值）
                signals = []
                hold_streak = 0  # 当前持仓天数
                dates = [str(dates_arr[249])[:10]]  # 起始日期，对齐 equity[0]
                entry_price = 0
                total_iters = n - 5 - 250
                last_pct = 0

                for idx in range(250, n - 5):
                    # 进度显示（每 10% 更新一次）
                    pct = (idx - 250) * 100 // max(total_iters, 1)
                    if pct >= last_pct + 10:
                        last_pct = pct
                        self.t_single_backtest.after(0, lambda p=pct: self.set_status(f"回测: {name_full} {p}%"))

                    seg_c = c[:idx+1].tolist()
                    seg_h = h[:idx+1].tolist()
                    seg_l = l[:idx+1].tolist()
                    seg_v = v[:idx+1].tolist()

                    date_str = str(dates_arr[idx])[:10]
                    dates.append(date_str)

                    try:
                        from v15_filter import compute_filtered_signal
                        fs = compute_filtered_signal(seg_c, seg_h, seg_l, seg_v,
                            float(c[idx]), float(v[idx]),
                            code=code,
                            name=name_full.split()[-1] if " " in name_full else name_full)
                    except Exception:
                        equity.append(equity[-1])
                        continue

                    level = fs["level"]
                    score = fs["score"]

                    if hold_streak > 0:
                        hold_streak += 1
                        ret = c[idx] / c[idx-1] - 1
                        equity.append(equity[-1] * (1 + ret))

                        # 止盈止损检查
                        pnl = (c[idx] - entry_price) / entry_price * 100
                        if pnl <= -7 or pnl >= 15 or hold_streak >= 20:
                            hold_streak = 0
                            signals.append({
                                "date": date_str,
                                "type": "平仓",
                                "price": round(float(c[idx]), 2),
                                "pnl_pct": round(pnl, 1),
                                "reason": "止损" if pnl <= -7 else ("止盈" if pnl >= 15 else "到期"),
                            })
                        continue

                    # 开仓信号
                    if level in ("强势", "偏强"):
                        hold_streak = 1
                        entry_price = float(c[idx])
                        equity.append(equity[-1])
                        signals.append({
                            "date": date_str,
                            "type": level,
                            "price": round(entry_price, 2),
                            "score": round(score, 0),
                        })
                    else:
                        equity.append(equity[-1])

                # ── 统计 ──
                buy_sigs = [s for s in signals if s["type"] in ("强势", "偏强")]
                closes_sigs = [s for s in signals if s["type"] == "平仓"]
                total_pnl = sum(s.get("pnl_pct", 0) for s in closes_sigs)
                wins = [s for s in closes_sigs if s.get("pnl_pct", 0) > 0]

                # ── 结论摘要 ──
                win_rate = len(wins)/max(len(closes_sigs),1)*100 if closes_sigs else 0
                trade_count = len(closes_sigs)
                final_eq = equity[-1]

                if trade_count >= 3:
                    if final_eq > 1.1 and win_rate > 55:
                        verdict = "策略对该股有效，适合实盘参考"
                        icon = "🟢"
                    elif final_eq > 1.05:
                        verdict = "策略有正向预期但优势不大，建议配合其他信号使用"
                        icon = "🟡"
                    elif final_eq > 0.95:
                        verdict = "策略在该股上接近随机，降低仓位或观望"
                        icon = "🟠"
                    else:
                        verdict = "策略在该股上持续亏损，回避或仅轻仓试探"
                        icon = "🔴"
                else:
                    verdict = "交易次数过少，数据量不足以得出结论"
                    icon = "⚪"

                lines = []
                lines.append(f"  {'='*60}")
                lines.append(f"  {icon} 结论: {verdict}")
                lines.append(f"  {'='*60}")
                lines.append(f"  📌 单股回测：完整V15全链路（含板块共振/波动率/背离过滤），")
                lines.append(f"     逐日模拟真实持仓→止损止盈平仓，适合实战参考。")
                lines.append(f"     ⚠ 与全量回测口径不同（后者测原始信号、无板块过滤），勿直接对比。")
                lines.append(f"  {'='*60}")
                lines.append(f"  回测: {name_full} | {dates[0]} → {dates[-1]} | 共{len(dates)}个交易日")
                lines.append(f"  买入信号: {len(buy_sigs)}次 | 平仓: {len(closes_sigs)}次")
                lines.append(f"  胜率: {win_rate:.0f}%" if closes_sigs else "  胜率: N/A")
                lines.append(f"  累计盈亏: {total_pnl:+.1f}% | 终净值: {final_eq:.3f}")
                lines.append(f"  买入信号频率: {len(buy_sigs)/max(len(dates),1)*100:.1f}%")

                if signals:
                    lines.append(f"\n  信号记录:")
                    for s in signals[-20:]:
                        if s["type"] in ("强势", "偏强"):
                            lines.append(f"    🔵 {s['date']} {s['type']} @{s['price']:.2f} 评分{s['score']:.0f}")
                        else:
                            pnl_str = f"{s['pnl_pct']:+.1f}%"
                            icon = "🟢" if s['pnl_pct'] > 0 else "🔴"
                            lines.append(f"    {icon} {s['date']} {s['type']} @{s['price']:.2f} {pnl_str} ({s['reason']})")

                buy_bench = sum(1 for d_idx in range(250, n-20) if d_idx+20 < n and c[d_idx+20] / c[d_idx] > 1) / max(n-20-250, 1) * 100
                lines.append(f"\n  参考: 随机买入20日胜率≈{buy_bench:.0f}% | 指数20日均收≈{((c[-1]/c[-21]-1)*100):+.1f}%")

                self.t_single_backtest.after(0, lambda: (self.t_single_backtest.delete(1.0, tk.END),
                   self.t_single_backtest.insert(tk.END, "\n".join(lines))))

                # 画资金曲线
                self._plot_equity_curve(equity, dates, name_full)

            except Exception as e:
                self.t_single_backtest.after(0, lambda e=e: self.t_single_backtest.insert(tk.END, f"❌ 回测出错: {e}"))
                import traceback
                traceback.print_exc()

        threading.Thread(target=worker, daemon=True).start()

    def _plot_equity_curve(self, equity, dates, name):
        """画资金曲线到回测Tab。"""
        if not CHART_AVAILABLE or len(equity) < 2:
            return
        try:
            import matplotlib.pyplot as plt
            fig, ax = plt.subplots(figsize=(10, 2.5), dpi=100)
            x = list(range(len(equity)))
            ax.plot(x, equity, 'b-', lw=1.5)
            ax.axhline(1.0, color='gray', ls='--', alpha=0.5)
            ax.fill_between(x, 1.0, equity, where=[e >= 1.0 for e in equity],
                           color='green', alpha=0.1)
            ax.fill_between(x, 1.0, equity, where=[e < 1.0 for e in equity],
                           color='red', alpha=0.1)
            ax.set_title(f"资金曲线 — {name}", fontsize=10)
            ax.set_ylabel("净值")
            ax.grid(True, alpha=0.3)

            # 日期标签：如果 dates 和 equity 对齐，显示真实日期
            if dates and len(dates) == len(equity):
                step = max(1, len(dates) // 10)
                ticks = list(range(0, len(dates), step))
                ax.set_xticks(ticks)
                ax.set_xticklabels([dates[i] for i in ticks], rotation=30, fontsize=7)
            elif dates and len(dates) > 1:
                # fallback: 用 dates 的首尾
                ax.set_xlabel(f"交易日 [{dates[0]} → {dates[-1]}]", fontsize=7, color='#9E9E9E')
            else:
                ax.set_xlabel("交易日序号", fontsize=7, color='#9E9E9E')

            plt.tight_layout()

            def embed():
                for w in self.bt_chart_frm.winfo_children():
                    w.destroy()
                canvas = FigureCanvasTkAgg(fig, master=self.bt_chart_frm)
                canvas.draw()
                canvas.get_tk_widget().pack(fill=tk.X)
                self.set_status(f"回测完成: {name}")

            self.bt_chart_frm.after(0, embed)
        except Exception as e:
            import traceback
            traceback.print_exc()

    def run_factor_ic(self):
        """因子IC分析：诊断40+因子的预测力（Rank IC + 分组收益）。"""
        self.notebook.select(self.factor_ic_tab)
        self.t_factor_ic.delete(1.0, tk.END)
        self.t_factor_ic.insert(tk.END, "⏳ 因子IC分析运行中（约5-10分钟，后台8线程）...\n")

        def _put(text, tag=None):
            self.t_factor_ic.after(0, lambda t=text, tg=tag: self.t_factor_ic.insert(tk.END, t, tg))

        def _init_tags():
            self.t_factor_ic.tag_configure("ic_h", font=("微软雅黑", 11, "bold"), foreground="#1f2937")
            self.t_factor_ic.tag_configure("ic_hdr", font=("微软雅黑", 9, "bold"), foreground="#1a1a2e", background="#e8edf3")
            self.t_factor_ic.tag_configure("ic_grn", font=("微软雅黑", 9), foreground="#2e7d32")
            self.t_factor_ic.tag_configure("ic_red", font=("微软雅黑", 9), foreground="#c62828")
            self.t_factor_ic.tag_configure("ic_nrm", font=("微软雅黑", 9))
        self.t_factor_ic.after(0, _init_tags)

        def worker():
            try:
                import concurrent.futures
                import numpy as np
                from backtest_factor_ic import (compute_factors, compute_rank_ic,
                                                 compute_quantile_spread, load_stock_tdx,
                                                 FORWARD_DAYS, BACKTEST_YEARS)
            except Exception as e:
                _put(f"❌ 导入失败: {e}\n", "ic_red")
                return

            try:
                _do_factor_ic(load_stock_tdx, FORWARD_DAYS, BACKTEST_YEARS,
                              compute_factors, compute_rank_ic, compute_quantile_spread, _put)
            except Exception as e:
                import traceback
                _put(f"❌ 因子IC运行异常: {e}\n{traceback.format_exc()}\n", "ic_red")

        def _do_factor_ic(_ic_load, FORWARD_DAYS, BACKTEST_YEARS,
                          compute_factors, compute_rank_ic, compute_quantile_spread, _put):
            total = len(self.data)
            completed = [0]
            factor_ics = {}
            max_fd = max(FORWARD_DAYS)
            codes = list(self.data)

            def bt_one(code, name):
                try:
                    df = _ic_load(code)
                    if df is None or len(df) < 120:
                        return None
                    n = len(df)
                    pairs = {}  # {factor_name: [(val, fwd_ret), ...]}
                    for idx in range(60, n - max(FORWARD_DAYS), 15):
                        factors = compute_factors(df, idx)
                        for fd in FORWARD_DAYS:
                            if idx + fd < n:
                                fwd_ret = (float(df["收盘"].iloc[idx+fd]) / float(df["收盘"].iloc[idx]) - 1) * 100
                                for fname, fval in factors.items():
                                    if not np.isnan(fval):
                                        pairs.setdefault(f"{fname}|{fd}", []).append((fval, fwd_ret))
                    # 不返回原始pairs，而是立即计算IC摘要并返回
                    stock_result = {}
                    for key, pair_list in pairs.items():
                        if len(pair_list) < 30:
                            continue
                        vals = [p[0] for p in pair_list]
                        rets = [p[1] for p in pair_list]
                        ic, _ = compute_rank_ic(vals, rets)
                        fname, fd_str = key.rsplit("|", 1)
                        fd = int(fd_str)
                        stock_result.setdefault(fname, {})[fd] = (ic, len(pair_list))
                    return stock_result
                except Exception:
                    return None

            # 流式累加: 每只股票返回IC后立刻更新全局聚合
            ic_accum = {}  # {fname: {fd: (sum_ic_w, total_pairs)}}
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                futures = {pool.submit(bt_one, c, n): c for c, n in codes}
                for f in concurrent.futures.as_completed(futures):
                    r = f.result()
                    if r:
                        for fname, fd_dict in r.items():
                            for fd, (ic, n_pairs) in fd_dict.items():
                                prev = ic_accum.setdefault(fname, {}).get(fd, (0.0, 0))
                                ic_accum[fname][fd] = (prev[0] + ic * n_pairs, prev[1] + n_pairs)
                    completed[0] += 1
                    if completed[0] % 20 == 0:
                        self.t_factor_ic.after(0,
                            lambda c=completed[0]: self.set_status(f"因子IC: {c}/{total}"))

            # 加权平均还原最终IC
            factor_ics = {}
            for fname, fd_dict in ic_accum.items():
                for fd, (sum_ic_w, total_n) in fd_dict.items():
                    if total_n >= 100:
                        factor_ics.setdefault(fname, {})[fd] = (sum_ic_w / total_n, 0.0, total_n, 0)

            # ── 输出报告 ──
            _put(f"因子IC报告  |  {datetime.now().strftime('%Y-%m-%d %H:%M')}  |  {completed[0]}/{total}只  {BACKTEST_YEARS}年回测\n", "ic_h")
            _put(f"{'='*90}\n\n", "ic_nrm")

            # 按10日IC排序
            ic_list = []
            for fname, fd_dict in factor_ics.items():
                ic10 = fd_dict.get(10, (0, 1, 0, 0))
                ic_list.append((fname, ic10[0], ic10[3], ic10[2]))

            ic_list.sort(key=lambda x: -abs(x[1]))

            _put("│因子\t10日IC\tQ5-Q1差\tN\t分类\n", "ic_hdr")
            _put("│──\t──\t──\t──\t──\n", "ic_hdr")

            pos_cnt = neg_cnt = noise_cnt = 0
            for fname, ic10, spread, n in ic_list[:40]:
                if ic10 > 0.02:
                    cat, tag = "正向", "ic_grn"
                elif ic10 < -0.02:
                    cat, tag = "反向", "ic_red"
                else:
                    cat, tag = "噪声", "ic_nrm"
                _put(f"│{fname}\t{ic10:+.3f}\t{spread:+.2f}%\t{n}\t{cat}\n", tag)

            pos_cnt = sum(1 for _, ic, _, _ in ic_list if ic > 0.02)
            neg_cnt = sum(1 for _, ic, _, _ in ic_list if ic < -0.02)
            noise_cnt = len(ic_list) - pos_cnt - neg_cnt

            _put(f"\n总计 {len(ic_list)} 因子: 🟢正向{pos_cnt} | 🔄反向{neg_cnt} | ❌噪声{noise_cnt}\n", "ic_nrm")

        threading.Thread(target=worker, daemon=True).start()

    def run_backtest_all(self):
        """全量回测：410只股票汇总统计。"""
        self.t_backtest.delete(1.0, tk.END)
        self.t_backtest.insert(tk.END, "⏳ 全量回测运行中（约5-10分钟，后台8线程）...\n")
        self.bt_stock_label.config(text="全量回测")
        self.set_status("全量回测: 正在扫描...")

        def worker():
            import concurrent.futures
            results = []
            completed = [0]
            total = len(self.data)

            def bt_one(code, name_full):
                try:
                    df = load_stock_tdx(code)
                    if df is None or len(df) < 250:
                        return None
                    c = df["收盘"].values.astype(float)
                    n = len(c)

                    # ── ATR T1 折扣（板块级，仅历史回测场景使用，不调外部API）──
                    t1_score = 50.0
                    try:
                        from _plus_.sector_power import _get_t1_score, _em_industry_to_tdx
                        from v15_sector_fetcher import match_sector, get_stock_industry
                        ind = match_sector(code, name_full) or get_stock_industry(code) or ""
                        tdx = _em_industry_to_tdx(ind)
                        if tdx:
                            t1_score = _get_t1_score(tdx)
                    except Exception:
                        pass

                    wins = 0
                    losses = 0
                    total_pnl = 0.0
                    signals = 0
                    buy_ret_5d = []
                    buy_ret_20d = []
                    avoid_ret_5d = []

                    for idx in range(250, n - 20, 15):  # 每15日采样
                        seg_c = c[:idx+1].tolist()
                        seg_h = df["最高"].values[:idx+1].astype(float).tolist()
                        seg_l = df["最低"].values[:idx+1].astype(float).tolist()
                        seg_v = df["成交量"].values[:idx+1].astype(float).tolist()
                        lc = float(c[idx])
                        lv = float(seg_v[-1])

                        try:
                            from v15_filter import compute_filtered_signal
                            fs = compute_filtered_signal(seg_c, seg_h, seg_l, seg_v, lc, lv)
                        except Exception:
                            continue

                        # ── ATR T1 折扣 ──
                        vol_info = fs.get("volatility", {})
                        atr_norm_val = vol_info.get("atr_norm", 0)
                        level = fs["level"]
                        score_raw = fs["score"]

                        if atr_norm_val > 3.0:
                            orig_atr = -18 if atr_norm_val > 4.0 else -8
                            if t1_score >= 70:
                                discount = 0.3
                            elif t1_score >= 50:
                                discount = 0.6
                            elif t1_score >= 30:
                                discount = 1.0
                            else:
                                discount = 1.2
                            new_atr = orig_atr * discount
                            delta = new_atr - orig_atr
                            score_adj = score_raw + delta

                            # 重新判级
                            from v15_scenario_engine import LEVEL_THRESHOLDS as _LT
                            level = "弱势"
                            for th, lv in _LT:
                                if score_adj >= th:
                                    level = lv
                                    break
                        ret_5 = (c[idx+5] / c[idx] - 1) * 100 if idx+5 < n else 0
                        ret_20 = (c[idx+20] / c[idx] - 1) * 100 if idx+20 < n else 0

                        if level in ("强势", "偏强"):
                            signals += 1
                            buy_ret_5d.append(ret_5)
                            buy_ret_20d.append(ret_20)
                            if ret_5 > 0:
                                wins += 1
                            else:
                                losses += 1
                            total_pnl += ret_20
                        elif level == "弱势":
                            avoid_ret_5d.append(ret_5)

                    completed[0] += 1
                    if signals >= 3:
                        return {
                            "code": code, "name": name_full.split()[-1] if " " in name_full else name_full,
                            "signals": signals, "wins": wins, "losses": losses,
                            "win_rate": round(wins/max(signals,1)*100, 1),
                            "total_pnl": round(total_pnl, 2),
                            "avg_buy_ret_5": round(sum(buy_ret_5d)/max(len(buy_ret_5d),1), 2),
                            "avg_buy_ret_20": round(sum(buy_ret_20d)/max(len(buy_ret_20d),1), 2) if buy_ret_20d else 0,
                            "avg_avoid_ret_5": round(sum(avoid_ret_5d)/max(len(avoid_ret_5d),1), 2) if avoid_ret_5d else 0,
                        }
                    return None
                except Exception:
                    return None

            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                futures = {pool.submit(bt_one, c, nf): (c, nf) for c, nf in self.data}
                for f in concurrent.futures.as_completed(futures):
                    r = f.result()
                    if r:
                        results.append(r)
                    if completed[0] % 50 == 0:
                        self.t_backtest.after(0, lambda p=completed[0]: self.set_status(f"全量回测: {p}/{total}"))

            # ── 报告 ──
            results.sort(key=lambda x: x["win_rate"], reverse=True)
            lines = []
            lines.append(f"  {'='*70}")
            lines.append(f"  📌 全量回测说明：测试四因子原始信号纯净度（无板块共振/波动率过滤），")
            lines.append(f"     每15日采样一次，胜率=买入信号后5日涨跌概率。与单股回测口径不同。")
            lines.append(f"     ⚠ 全量高分+单股低分 → 过滤器在过度降级，建议查看该股择时雷达的过滤路径。")
            lines.append(f"  {'='*70}")
            lines.append(f"  全量回测汇总 | {len(results)}只有效 | {len(self.data)}只总池")
            lines.append(f"  {'='*70}")

            all_signals = sum(r["signals"] for r in results)
            all_wins = sum(r["wins"] for r in results)
            all_pnl = sum(r["total_pnl"] for r in results)
            lines.append(f"  总信号: {all_signals} | 总胜率: {all_wins/max(all_signals,1)*100:.1f}% | 累计盈亏: {all_pnl:+.1f}%")
            lines.append(f"  买入5日均收: {sum(r['avg_buy_ret_5']*r['signals'] for r in results)/max(all_signals,1):+.2f}%")
            lines.append(f"  弱势5日均收: {sum(r.get('avg_avoid_ret_5',0)*max(r.get('signals',1),1) for r in results)/max(all_signals,1):+.2f}%")

            lines.append(f"\n  Top 10 胜率最高:")
            for r in results[:10]:
                lines.append(f"    {r['code']} {r['name']:<8} 信号{r['signals']}次 胜率{r['win_rate']}% 盈亏{r['total_pnl']:+.0f}%")

            self.t_backtest.after(0, lambda: (self.t_backtest.delete(1.0, tk.END),
                                               self.t_backtest.insert(tk.END, "\n".join(lines))))
            self.t_backtest.after(0, lambda: self.notebook.select(self.backtest_tab))
            self.t_backtest.after(0, lambda: self.set_status(f"回测完成: {len(results)}只"))

        threading.Thread(target=worker, daemon=True).start()

    # ═══════════════════════════════════════════
    # 自选股管理
    # ═══════════════════════════════════════════

    def add_stock_dialog(self):
        """弹窗添加自选股。"""
        dialog = tk.Toplevel(self)
        dialog.title("添加自选股")
        dialog.geometry("420x200")
        dialog.configure(bg="#ffffff")
        dialog.transient(self)
        dialog.grab_set()

        tk.Label(dialog, text="添加自选股（每行一个，格式: 代码 名称）",
                 font=("微软雅黑", 10, "bold"), bg="#ffffff").pack(pady=(10, 0))
        tk.Label(dialog, text="示例: 600519 贵州茅台",
                 font=("微软雅黑", 9), bg="#ffffff", fg="#6b7280").pack()

        text = tk.Text(dialog, font=("Consolas", 10), height=5, width=50)
        text.pack(padx=15, pady=10)
        text.insert("1.0", "")

        def do_add():
            lines = text.get("1.0", "end").strip().split("\n")
            added = 0
            existing = set()
            with open(SELF_STOCK_TXT, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        existing.add(line.split()[0] if " " in line else line)

            with open(SELF_STOCK_TXT, "a", encoding="utf-8") as f:
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    parts = line.split(None, 1)
                    code = parts[0]
                    if code not in existing:
                        f.write(f"\n{line}")
                        existing.add(code)
                        added += 1

            dialog.destroy()
            self.load_stock_list()
            self.set_status(f"已添加{added}只自选股")

        ttk.Button(dialog, text="确认添加", command=do_add).pack()
        ttk.Button(dialog, text="从文件导入...",
                   command=lambda: self.import_stocks_file(dialog)).pack(pady=2)

    def import_stocks_file(self, parent=None):
        """从文件导入自选股（文件选择器）。"""
        from tkinter import filedialog
        fp = filedialog.askopenfilename(
            title="选择自选股文件",
            filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")])
        if not fp:
            return
        try:
            with open(fp, "r", encoding="utf-8") as f:
                lines = [l.strip() for l in f if l.strip()]

            existing = set()
            if os.path.exists(SELF_STOCK_TXT):
                with open(SELF_STOCK_TXT, "r", encoding="utf-8") as f2:
                    for line in f2:
                        line = line.strip()
                        if line:
                            existing.add(line.split()[0] if " " in line else line)

            added = 0
            with open(SELF_STOCK_TXT, "a", encoding="utf-8") as f3:
                for line in lines:
                    parts = line.split(None, 1)
                    code = parts[0]
                    if code not in existing:
                        f3.write(f"\n{line}")
                        existing.add(code)
                        added += 1

            if parent:
                parent.destroy()
            self.load_stock_list()
            self.set_status(f"已从文件导入{added}只自选股")
            messagebox.showinfo("导入完成", f"导入{added}只自选股（去重后）")
        except Exception as e:
            messagebox.showerror("导入失败", str(e))

    def export_stocks_file(self):
        """导出当前自选股到文件。"""
        from tkinter import filedialog
        fp = filedialog.asksaveasfilename(
            title="导出自选股",
            defaultextension=".txt",
            filetypes=[("文本文件", "*.txt")])
        if not fp:
            return
        try:
            with open(SELF_STOCK_TXT, "r", encoding="utf-8") as src:
                content = src.read()
            with open(fp, "w", encoding="utf-8") as dst:
                dst.write(content)
            self.set_status(f"自选股已导出到: {fp}")
            messagebox.showinfo("导出完成", f"已导出{len(self.data)}只到:\n{fp}")
        except Exception as e:
            messagebox.showerror("导出失败", str(e))

if __name__ == "__main__":
    app = StockProUI()
    app.mainloop()
