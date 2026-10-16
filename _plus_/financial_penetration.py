"""
财务穿透模块 v2 — 五维评分 + 粉饰预警信号

评分体系（基于deep-research + CSRC/CEIBS/SASAC 三方框架）：
  ① 现金流质量 25%  — 净现比(OCF/NP)为核心，CSRC预警线<1.0，CEIBS安全区间0.7~3.0
  ② 营收真实性 20%  — 营收增速与净利增速匹配度、营收现金含量
  ③ 盈利能力质量 20% — ROE水平、毛利率水平、EPS
  ④ 偿债安全   15%  — 基于真实资产负债表（Phase 2）
  ⑤ 粉饰预警   20%  — 扣分制，检出信号扣分，零信号满分

数据源：
  Phase 1: akshare.stock_yjbb_em() 批量业绩报表
  Phase 2: akshare.stock_balance_sheet_by_report_em() + cash_flow_sheet 按需拉取
"""

import os
import json
import warnings
from datetime import datetime
from typing import Optional

warnings.filterwarnings("ignore")

try:
    import akshare as ak
except ImportError:
    ak = None

# 模块级缓存：同一个进程内只加载一次全量数据
_FIN_DATA_CACHE: dict[str, dict] = {}
_FIN_DATA_CACHE_DATE: str = ""

_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SELF_STOCK_FILE = os.path.join(_APP_DIR, "scripts", "self_stock.txt")

# ── 五维权重 ──
_DIM_WEIGHTS = {
    "cash_flow_quality": 0.25,
    "revenue_authenticity": 0.20,
    "profitability": 0.20,
    "debt_safety": 0.15,
    "fraud_warning": 0.20,
}

_DIM_NAMES = {
    "cash_flow_quality": "现金流质量",
    "revenue_authenticity": "营收真实性",
    "profitability": "盈利能力",
    "debt_safety": "偿债安全",
    "fraud_warning": "粉饰预警",
}


def _read_self_stock_codes() -> list[str]:
    codes = []
    for fp in [_SELF_STOCK_FILE,
               os.path.join(_APP_DIR, "self_stock.txt")]:
        if os.path.exists(fp):
            with open(fp, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        code = line.split()[0]
                        if code.isdigit() and len(code) == 6:
                            codes.append(code)
            break
    return sorted(set(codes))


def _safe_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def fetch_financial_data(date: str = "20260331", use_cache: bool = True) -> Optional[dict]:
    """获取全A股财务数据（一期：业绩报表），返回 {code: item_dict}"""
    if ak is None:
        return None

    # 模块级内存缓存（同一进程内，无需重复读文件）
    global _FIN_DATA_CACHE, _FIN_DATA_CACHE_DATE
    if use_cache and _FIN_DATA_CACHE_DATE == date and _FIN_DATA_CACHE:
        return _FIN_DATA_CACHE

    cache_dir = os.path.join(_APP_DIR, "data")
    os.makedirs(cache_dir, exist_ok=True)
    cache_key = "fin_v2_" + date
    cache_path = os.path.join(cache_dir, cache_key + ".json")

    if use_cache and os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cached = json.load(f)
            if isinstance(cached, dict) and len(cached) > 100:
                _FIN_DATA_CACHE = cached
                _FIN_DATA_CACHE_DATE = date
                return cached
        except Exception:
            pass

    try:
        df = ak.stock_yjbb_em(date=date)
    except Exception:
        return None

    if df is None or df.empty:
        return None

    result = {}
    for _, row in df.iterrows():
        code = str(row.get("股票代码", ""))
        if not code or len(code) != 6:
            continue
        try:
            item = {
                "name": str(row.get("股票简称", "")),
                "eps": _safe_float(row.get("每股收益")),
                "revenue": _safe_float(row.get("营业总收入-营业总收入")),
                "revenue_yoy": _safe_float(row.get("营业总收入-同比增长")),
                "net_profit": _safe_float(row.get("净利润-净利润")),
                "net_profit_yoy": _safe_float(row.get("净利润-同比增长")),
                "bps": _safe_float(row.get("每股净资产")),
                "roe": _safe_float(row.get("净资产收益率")),
                "ocfps": _safe_float(row.get("每股经营现金流量")),
                "gross_margin": _safe_float(row.get("销售毛利率")),
                "industry": str(row.get("所处行业", "")),
                "report_date": date,
            }
            result[code] = item
        except Exception:
            continue

    if result:
        try:
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False)
        except Exception:
            pass
        # 写入内存缓存
        _FIN_DATA_CACHE = result
        _FIN_DATA_CACHE_DATE = date

    return result


# ═══════════════════════════════════════════════
# 维度① 现金流质量 (25%)
# ═══════════════════════════════════════════════

def _score_cash_flow_quality(item: dict) -> dict:
    """
    核心指标：净现比 = OCFPS / EPS
    CSRC预警：营业利润经营现金比率 < 1.0
    安全区间(CEIBS)：0.7 ~ 3.0
    """
    eps = item.get("eps")
    ocfps = item.get("ocfps")

    if eps is None or ocfps is None or eps == 0:
        return {"score": 50, "detail": "数据不足"}

    ratio = ocfps / eps if eps != 0 else 0
    detail = f"净现比={ratio:.2f}"

    if ratio < 0:
        score = 10
        detail += " 经营现金流为负，严重预警"
    elif ratio < 0.5:
        score = 20
        detail += " 危险区(<0.5)，利润无现金支撑"
    elif ratio < 0.7:
        score = 50
        detail += " 警戒区(0.5~0.7)，需关注"
    elif ratio <= 3.0:
        score = 90
        detail += f" 健康区(0.7~3.0) ✓"
    else:
        score = 60
        detail += " 偏高(>3.0)，可能一次性收益或折旧过大"

    return {"score": score, "detail": detail, "ratio": round(ratio, 2)}


# ═══════════════════════════════════════════════
# 维度② 营收真实性 (20%)
# ═══════════════════════════════════════════════

def _score_revenue_authenticity(item: dict) -> dict:
    """
    基于现有数据评估：
    - 营收增速与净利增速的匹配度（若严重背离说明可能有问题）
    - 营收现金含量 (OCFPS/EPS 的补充视角)
    """
    ry = item.get("revenue_yoy")
    ny = item.get("net_profit_yoy")

    if ry is None or ny is None:
        return {"score": 50, "detail": "数据不足"}

    gap = abs(ny - ry)  # 增速缺口
    detail = f"营收{ry:+.1f}% 净利{ny:+.1f}% 缺口{gap:.0f}pp"

    if gap < 15:
        score = 90
        detail += " 匹配良好 ✓"
    elif gap < 30:
        score = 65
        detail += " 缺口偏大，需关注"
    elif gap < 50:
        score = 40
        detail += " 明显背离，排查营收确认方式"
    else:
        score = 20
        detail += " 严重背离，营收质量存疑"

    return {"score": score, "detail": detail, "gap": round(gap, 1)}


# ═══════════════════════════════════════════════
# 维度③ 盈利能力质量 (20%)
# ═══════════════════════════════════════════════

def _score_profitability(item: dict) -> dict:
    """
    ROE水平 + 毛利率水平 + EPS水平
    A股2024 ROE中位数4.3%，以此为锚
    """
    roe = item.get("roe")
    gm = item.get("gross_margin")
    eps = item.get("eps")

    sub_scores = []
    details = []

    # ROE评分（以A股中位数4.3%为基准）
    if roe is not None:
        if roe >= 15:
            rs = 95
        elif roe >= 10:
            rs = 80
        elif roe >= 5:
            rs = 60
        elif roe >= 3:
            rs = 40
        elif roe >= 0:
            rs = 25
        else:
            rs = 10
        sub_scores.append(rs)
        details.append(f"ROE={roe:.1f}%")

    # 毛利率评分
    if gm is not None:
        if gm >= 50:
            gs = 90
        elif gm >= 30:
            gs = 75
        elif gm >= 15:
            gs = 55
        elif gm >= 5:
            gs = 35
        else:
            gs = 15
        sub_scores.append(gs)
        details.append(f"毛利率={gm:.1f}%")

    # EPS评分
    if eps is not None:
        if eps >= 2:
            es = 90
        elif eps >= 1:
            es = 75
        elif eps >= 0.5:
            es = 55
        elif eps >= 0:
            es = 35
        else:
            es = 10
        sub_scores.append(es)
        details.append(f"EPS={eps:.3f}")

    if not sub_scores:
        return {"score": 50, "detail": "数据不足"}

    avg = sum(sub_scores) / len(sub_scores)
    return {"score": round(avg, 1), "detail": " | ".join(details)}


# ═══════════════════════════════════════════════
# 维度④ 偿债安全 (15%)
# ═══════════════════════════════════════════════

def _score_debt_safety(item: dict) -> dict:
    """
    一期：以每股净资产(BPS)为近似锚点
    BPS越高，安全垫越厚（但不同行业差异大，暂不精细调整）
    待Phase 2接入资产负债表后使用真实有息负债率
    """
    bps = item.get("bps")
    eps = item.get("eps")

    if bps is None:
        return {"score": 50, "detail": "数据不足"}

    detail = f"每股净资产={bps:.2f}"

    # 用扣非净利润 vs BPS 近似ROE作为补充
    if bps >= 20:
        score = 85
        detail += " 充足 ✓"
    elif bps >= 10:
        score = 70
        detail += " 良好"
    elif bps >= 5:
        score = 55
        detail += " 中等"
    elif bps >= 1:
        score = 35
        detail += " 偏薄"
    else:
        score = 10
        detail += " 极低，净资产为负风险"

    # 用EPS/BPS近似ROE的补充判断
    if eps is not None and bps > 0:
        implied_roe = eps / bps * 100
        if implied_roe < 0:
            detail += " | ROE为负，盈利能力侵蚀净资产"

    return {"score": score, "detail": detail, "bps": round(bps, 2)}


# ═══════════════════════════════════════════════
# 维度⑤ 粉饰预警信号 (20%，扣分制)
# ═══════════════════════════════════════════════

def _detect_fraud_signals(item: dict) -> tuple:
    """
    一期可检测的预警信号（基于现有数据）：
    ① 毛利率突变  — 单期时无法检测，标记为'需多期数据'
       用营收增速与净利增速严重背离作为替代
    ② 净现危险    — OCFPS<0 且 EPS>0（纸面盈利但经营失血）
    ③ 成长质量差  — 营收负增长但毛利率异常高
    ④ ROE为负且EPS为正（矛盾信号）
    ⑤ 营收与净利增速相反方向

    返回 (score_0_to_100, warning_list)
    """
    warnings = []
    deductions = 0

    eps = item.get("eps")
    ocfps = item.get("ocfps")
    roe = item.get("roe")
    gm = item.get("gross_margin")
    ry = item.get("revenue_yoy")
    ny = item.get("net_profit_yoy")

    # ① 纸面盈利但经营失血：EPS>0 但 OCFPS<0
    if eps is not None and ocfps is not None:
        if eps > 0 and ocfps < 0:
            warnings.append({
                "id": "cash_leak",
                "severity": "high",
                "signal": "纸面盈利但经营失血",
                "detail": f"EPS={eps:.3f}>0 但 OCFPS={ocfps:.3f}<0，利润无现金支撑",
            })
            deductions += 25

    # ② 净现比<0.5
    if eps is not None and ocfps is not None and eps > 0:
        ratio = ocfps / eps
        if 0 <= ratio < 0.5:
            warnings.append({
                "id": "low_cash_ratio",
                "severity": "medium",
                "signal": "净现比<0.5",
                "detail": f"经营现金流仅为净利润的{ratio:.0%}，盈利含金量低",
            })
            deductions += 15

    # ③ 营收正增长但净利负增长（增收不增利）
    if ry is not None and ny is not None:
        if ry > 10 and ny < -10:
            warnings.append({
                "id": "revenue_no_profit",
                "severity": "medium",
                "signal": "增收不增利",
                "detail": f"营收+{ry:.0f}%但净利{ny:.0f}%，成本费用增速失控",
            })
            deductions += 15
        elif ry < -10 and ny > 10:
            warnings.append({
                "id": "profit_no_revenue",
                "severity": "medium",
                "signal": "减收反增利",
                "detail": f"营收{ry:.0f}%但净利+{ny:.0f}%，可能一次性收益或粉饰",
            })
            deductions += 15

    # ④ ROE<0但毛利率正常（核心业务亏损）
    if roe is not None and gm is not None:
        if roe < 0 and gm > 20:
            warnings.append({
                "id": "high_gm_low_roe",
                "severity": "medium",
                "signal": "高毛利率低ROE",
                "detail": f"毛利率{gm:.1f}%但ROE{roe:.1f}%，费用端或资产端有拖累",
            })
            deductions += 10

    # ⑤ 营收负增长（成长性缺失）
    if ry is not None and ry < -20:
        warnings.append({
            "id": "severe_decline",
            "severity": "low",
            "signal": "营收严重下滑",
            "detail": f"营收同比{ry:.0f}%，业务萎缩明显",
        })
        deductions += 10

    # Phase 2 预留信号（标记为"待完善"）
    # - 毛利率突变（需多期数据）
    # - 存贷双高（需资产负债表）
    # - 预付账款剧增（需资产负债表）
    # - 应收转票据（需资产负债表+附注）
    # - 客商重叠（需年报文本）
    # - 总额法虚增收（需分部报告）

    score = max(0, 100 - deductions)
    return score, warnings


# ═══════════════════════════════════════════════
# 综合评分
# ═══════════════════════════════════════════════

def calculate_full_report(item: dict) -> dict:
    """计算完整五维评分报告"""
    cf = _score_cash_flow_quality(item)
    ra = _score_revenue_authenticity(item)
    pf = _score_profitability(item)
    ds = _score_debt_safety(item)
    fw_score, fw_signals = _detect_fraud_signals(item)

    dims = {
        "cash_flow_quality": cf["score"],
        "revenue_authenticity": ra["score"],
        "profitability": pf["score"],
        "debt_safety": ds["score"],
        "fraud_warning": fw_score,
    }

    dim_details = {
        "cash_flow_quality": cf["detail"],
        "revenue_authenticity": ra["detail"],
        "profitability": pf["detail"],
        "debt_safety": ds["detail"],
    }

    total = 0.0
    for key, score in dims.items():
        total += score * _DIM_WEIGHTS[key]

    return {
        "dimension_scores": dims,
        "dimension_details": dim_details,
        "fraud_warnings": fw_signals,
        "fraud_warning_count": len(fw_signals),
        "total_score": round(total, 1),
        "rating": _rating_label(total),
    }


def _rating_label(score: float) -> str:
    if score >= 85:
        return "优秀"
    elif score >= 70:
        return "良好"
    elif score >= 55:
        return "中等"
    elif score >= 40:
        return "偏弱"
    else:
        return "差"


def get_penetration_report(date: str = "20260331") -> list[dict]:
    """
    获取自选股财务穿透报告（五维评分），按综合总分降序排列
    """
    codes = _read_self_stock_codes()
    if not codes:
        return []

    all_data = fetch_financial_data(date=date)
    if not all_data:
        return []

    code_set = set(codes)
    report = []
    for code, item in all_data.items():
        if code not in code_set:
            continue
        fr = calculate_full_report(item)
        report.append({
            "code": code,
            "name": item.get("name", ""),
            "industry": item.get("industry", ""),
            "revenue": item.get("revenue"),
            "revenue_yoy": item.get("revenue_yoy"),
            "net_profit": item.get("net_profit"),
            "net_profit_yoy": item.get("net_profit_yoy"),
            "gross_margin": item.get("gross_margin"),
            "roe": item.get("roe"),
            "eps": item.get("eps"),
            "total_score": fr["total_score"],
            "rating": fr["rating"],
            "dimension_scores": fr["dimension_scores"],
            "dimension_details": fr["dimension_details"],
            "fraud_warnings": fr["fraud_warnings"],
            "fraud_warning_count": fr["fraud_warning_count"],
        })

    report.sort(key=lambda x: x["total_score"], reverse=True)
    return report


def get_stock_detail(code: str, date: str = "20260331") -> Optional[dict]:
    """查询单只股票的完整财务穿透报告"""
    all_data = fetch_financial_data(date=date)
    if not all_data or code not in all_data:
        return None
    item = all_data[code]
    fr = calculate_full_report(item)
    return {
        "code": code,
        "name": item.get("name", ""),
        "industry": item.get("industry", ""),
        "revenue": item.get("revenue"),
        "revenue_yoy": item.get("revenue_yoy"),
        "net_profit": item.get("net_profit"),
        "net_profit_yoy": item.get("net_profit_yoy"),
        "gross_margin": item.get("gross_margin"),
        "roe": item.get("roe"),
        "eps": item.get("eps"),
        "ocfps": item.get("ocfps"),
        "bps": item.get("bps"),
        "total_score": fr["total_score"],
        "rating": fr["rating"],
        "dimension_scores": fr["dimension_scores"],
        "dimension_details": fr["dimension_details"],
        "fraud_warnings": fr["fraud_warnings"],
        "fraud_warning_count": fr["fraud_warning_count"],
        "data": item,
    }


if __name__ == "__main__":
    report = get_penetration_report("20260331")
    print(f"=== V15Plus 财务穿透报告 v2（{len(report)}只自选股）===")
    print()

    # 前20只详情
    hdr = "{:>4s} {:>6s} {:8s} {:>5s} {:>5s} {:>5s} {:>5s} {:>5s} {:>7s} {:>4s} {}".format(
        "排名", "代码", "名称", "总分", "现金流", "营收", "盈利", "偿债", "预警", "红旗", "评级")
    print(hdr)
    print("-" * 75)

    for i, r in enumerate(report[:20]):
        ds = r["dimension_scores"]
        dims_str = "{:>5.0f} {:>5.0f} {:>5.0f} {:>5.0f} {:>5.0f}".format(
            ds.get("cash_flow_quality", 0),
            ds.get("revenue_authenticity", 0),
            ds.get("profitability", 0),
            ds.get("debt_safety", 0),
            ds.get("fraud_warning", 0),
        )
        print("{:>4d} {:>6s} {:8s} {:>5.1f} {} {:>4d} {:4s}".format(
            i + 1, r["code"], r["name"], r["total_score"],
            dims_str, r["fraud_warning_count"], r["rating"]))

    # 统计红旗分布
    total_flags = sum(r["fraud_warning_count"] for r in report)
    flagged_stocks = sum(1 for r in report if r["fraud_warning_count"] > 0)
    print(f"\n红旗统计：共{total_flags}个红旗，覆盖{flagged_stocks}只股票")
