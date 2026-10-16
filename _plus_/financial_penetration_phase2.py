"""
Phase 2 — 直连东方财富 Ajax 接口（无akshare依赖）
资产负债表/现金流量表 按需拉取 + 杜邦拆解 + 多期趋势 + 存贷双高检测
companyType硬编码为4（已验证SH/SZ统一）
"""
import json, os, warnings
from typing import Optional

warnings.filterwarnings("ignore")

_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BS_CACHE: dict[str, list] = {}
_CF_CACHE: dict[str, list] = {}


def _rating_label(score: float) -> str:
    if score >= 85: return "优秀"
    elif score >= 70: return "良好"
    elif score >= 55: return "中等"
    elif score >= 40: return "偏弱"
    else: return "差"


def _sf(v) -> float:
    if v is None: return 0.0
    try: return float(v)
    except: return 0.0


def _em_ajax(endpoint: str, sym: str, dates: str) -> Optional[list]:
    """调东方财富Ajax接口获取财务报表数据"""
    try:
        import requests
    except ImportError:
        return None
    url = f"https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/{endpoint}"
    params = {"companyType": "4", "reportDateType": "0", "reportType": "1",
              "dates": dates, "code": sym}
    try:
        r = requests.get(url, params=params,
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        data = r.json()
        if "data" in data and data["data"]:
            return data["data"]
    except Exception:
        pass
    return None


def _get_dates(sym: str) -> list[str]:
    """获取最近6个报告期"""
    try:
        import requests
    except ImportError:
        return []
    url = "https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/zcfzbDateAjaxNew"
    params = {"companyType": "4", "reportDateType": "0", "code": sym}
    try:
        r = requests.get(url, params=params,
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        data = r.json()
        return [d["REPORT_DATE"][:10] for d in data.get("data", [])[:6]]
    except Exception:
        return []


# ── Ajax字段名 → 统一字段名映射 ──
_BS_FIELD_MAP = {
    "MONETARYFUNDS": "MONETARYFUNDS",
    "TOTAL_ASSETS": "TOTAL_ASSETS",
    "TOTAL_EQUITY": "TOTAL_EQUITY",
    "TOTAL_LIABILITIES": "TOTAL_LIABILITIES",
    "TOTAL_CURRENT_ASSETS": "TOTAL_CURRENT_ASSETS",
    "TOTAL_CURRENT_LIAB": "TOTAL_CURRENT_LIAB",
    "TOTAL_NONCURRENT_ASSETS": "TOTAL_NONCURRENT_ASSETS",
    "TOTAL_NONCURRENT_LIAB": "TOTAL_NONCURRENT_LIAB",
    "SHORT_LOAN": "SHORT_LOAN",
    "LONG_LOAN": "LONG_LOAN",
    "ACCOUNTS_RECE": "ACCOUNTS_RECE",
    "INVENTORY": "INVENTORY",
    "ADVANCE_RECEIVABLES": "ADVANCE_RECEIVABLES",
    "FIXED_ASSET": "FIXED_ASSET",
    "INTANGIBLE_ASSET": "INTANGIBLE_ASSET",
    "GOODWILL": "GOODWILL",
    "LEASE_LIAB": "LEASE_LIAB",
    "CONTRACT_LIAB": "CONTRACT_LIAB",
    "ACCOUNTS_PAYABLE": "ACCOUNTS_PAYABLE",
}

_CF_FIELD_MAP = {
    "NETCASH_OPERATE": "OPERATE_NETCASH_BALANCE",
    "NETCASH_INVEST": "INVEST_NETCASH_BALANCE",
    "NETCASH_FINANCE": "FINANCE_NETCASH_BALANCE",
    "SALES_SERVICES": "SALES_SERVICES",
    "TOTAL_OPERATE_INFLOW": "TOTAL_OPERATE_INFLOW",
    "TOTAL_OPERATE_OUTFLOW": "TOTAL_OPERATE_OUTFLOW",
    "TOTAL_INVEST_INFLOW": "TOTAL_INVEST_INFLOW",
    "TOTAL_INVEST_OUTFLOW": "TOTAL_INVEST_OUTFLOW",
    "CONSTRUCT_LONG_ASSET": "CONSTRUCT_LONG_ASSET",
    "TOTAL_FINANCE_INFLOW": "TOTAL_FINANCE_INFLOW",
    "TOTAL_FINANCE_OUTFLOW": "TOTAL_FINANCE_OUTFLOW",
}


def _normalize_bs(item: dict) -> dict:
    """将Ajax响应字段映射到统一命名，所有值转float"""
    result = {}
    for ajax_key, std_key in _BS_FIELD_MAP.items():
        if ajax_key in item:
            try:
                result[std_key] = float(item[ajax_key]) if item[ajax_key] else 0.0
            except (ValueError, TypeError):
                result[std_key] = 0.0
        else:
            result[std_key] = 0.0
    return result


def _normalize_cf(item: dict) -> dict:
    """将Ajax响应字段映射到统一命名"""
    result = {}
    for ajax_key, std_key in _CF_FIELD_MAP.items():
        if ajax_key in item:
            try:
                result[std_key] = float(item[ajax_key]) if item[ajax_key] else 0.0
            except (ValueError, TypeError):
                result[std_key] = 0.0
        else:
            result[std_key] = 0.0
    return result


def _bs_date_match(target_date: str, available_dates: list[str]) -> str:
    """从可用报告期列表中匹配最接近target_date的日期"""
    if not available_dates:
        return ""
    # target_date如 "20260331"，available_dates如 "2026-03-31"
    target = target_date[:4] + "-" + target_date[4:6] + "-" + target_date[6:8]
    # 精确匹配
    for d in available_dates:
        if d == target:
            return d
    # 模糊匹配：取同一年同一季度的最早一期
    target_ym = target[:7]
    for d in available_dates:
        if d.startswith(target_ym):
            return d
    # 取最近的一期
    return available_dates[0]


def fetch_bs_cf(stock_code: str, max_periods: int = 4, target_date: str = "") -> tuple[list, list, bool]:
    """
    直连东方财富 Ajax 接口获取BS/CF，1-2秒/只
    target_date: 匹配到指定报告期（如 "20260331"），为空则取最新
    """
    code = stock_code[:6]
    sym = ("SZ" + code) if code[0] in ("0", "3", "2") else ("SH" + code)
    dates = _get_dates(sym)
    if not dates:
        return [], [], False

    if target_date:
        cache_key = code + "_" + target_date
    else:
        cache_key = code

    if cache_key in _BS_CACHE and cache_key in _CF_CACHE:
        return _BS_CACHE[cache_key], _CF_CACHE[cache_key], False

    if target_date:
        need = [_bs_date_match(target_date, dates)]
        need = [d for d in need if d]  # 去掉空值
        if not need:
            return [], [], False
    else:
        need = [d for d in dates[:max_periods]]

    bs_list, cf_list = [], []
    for i in range(0, len(need), 5):
        batch = ",".join(need[i:i+5])
        bd = _em_ajax("zcfzbAjaxNew", sym, batch)
        if bd:
            for item in bd:
                if len(bs_list) < max_periods:
                    bs_list.append(_normalize_bs(item))
        cd = _em_ajax("xjllbAjaxNew", sym, batch)
        if cd:
            for item in cd:
                if len(cf_list) < max_periods:
                    cf_list.append(_normalize_cf(item))

    if not bs_list:
        return [], [], False

    _BS_CACHE[cache_key] = bs_list
    _CF_CACHE[cache_key] = cf_list
    return bs_list, cf_list, True


def _dupont_decomposition(item: dict, bs_list: list) -> dict:
    """杜邦拆解 ROE = 净利率 × 资产周转率 × 权益乘数"""
    result = {
        "net_profit_margin": None, "asset_turnover": None,
        "equity_multiplier": None, "mode": "", "mode_risk": "",
    }
    revenue = item.get("revenue")
    net_profit = item.get("net_profit")
    roe = item.get("roe")
    gross_margin = item.get("gross_margin")

    if not bs_list or not revenue or revenue <= 0:
        result["mode_risk"] = "资产负债表数据未加载"
        return result

    b0 = bs_list[0]
    total_assets = b0.get("TOTAL_ASSETS", 0) or 0
    total_equity = b0.get("TOTAL_EQUITY", 0) or 0

    if total_equity <= 0 or total_assets <= 0:
        result["mode_risk"] = "资产负债表数据异常"
        return result

    equity_mult = total_assets / total_equity
    result["equity_multiplier"] = round(equity_mult, 2)

    if revenue > 0 and total_assets > 0:
        result["asset_turnover"] = round(revenue / total_assets, 4)

    if net_profit and net_profit > 0:
        net_margin = net_profit / revenue * 100
        result["net_profit_margin"] = round(net_margin, 2)
        if equity_mult >= 3:
            result["mode"] = "高杠杆驱动"
            result["mode_risk"] = "负债率偏高，利率上升周期偿债压力大"
        elif net_margin >= 20 and (result.get("asset_turnover") or 0) < 1:
            result["mode"] = "高利润驱动"
            result["mode_risk"] = "靠高利润率赚钱，需关注毛利率变化和竞争格局"
        elif net_margin < 8 and (result.get("asset_turnover") or 0) >= 1.0:
            result["mode"] = "高周转驱动"
            result["mode_risk"] = "薄利多销，需关注存货和应收周转效率"
        else:
            result["mode"] = "均衡型"
            result["mode_risk"] = "三因子均衡，稳定性较好"
    elif net_profit is not None and net_profit <= 0:
        result["mode"] = "亏损状态"
        parts = []
        if gross_margin is not None:
            parts.append(f"毛利率{gross_margin:.1f}%")
        parts.append("净利润为负")
        result["mode_risk"] = " | ".join(parts)
    return result


def _calc_free_cash_flow(cf_list: list) -> dict:
    """自由现金流 = 经营净现金流 - 资本开支"""
    if not cf_list:
        return {"fcf": None, "trend": "", "latest_ocf": None, "latest_capex": None}
    results, ocf_list, capex_list = [], [], []
    for row in cf_list:
        ocf = row.get("OPERATE_NETCASH_BALANCE", 0) or 0
        if ocf == 0:
            total_in = row.get("TOTAL_OPERATE_INFLOW", 0) or 0
            total_out = row.get("TOTAL_OPERATE_OUTFLOW", 0) or 0
            ocf = total_in - total_out if total_in > 0 else 0
        capex = row.get("CONSTRUCT_LONG_ASSET", 0) or 0
        if capex == 0:
            total_inv = row.get("TOTAL_INVEST_OUTFLOW", 0) or 0
            capex = total_inv * 0.5 if total_inv > 0 else 0
        fcf = ocf - capex
        results.append(fcf); ocf_list.append(ocf); capex_list.append(capex)
    if not results:
        return {"fcf": None, "trend": "", "latest_ocf": None, "latest_capex": None}
    l = results[0]
    trend = "(单期)"
    if len(results) >= 2 and results[1] != 0:
        trend = "↑ 改善" if l > results[1] * 1.2 else ("↓ 恶化" if l < results[1] * 0.8 else "→ 稳定")
    return {"fcf": round(l, 2), "trend": trend,
            "latest_ocf": round(ocf_list[0], 2) if ocf_list else None,
            "latest_capex": round(capex_list[0], 2) if capex_list else None}


def _analyze_bs_ratios(bs_list: list) -> dict:
    """资产负债表核心比率 + 存贷双高 + 多期趋势"""
    if not bs_list: return {}
    b0 = bs_list[0]
    monetary = b0.get("MONETARYFUNDS", 0) or 0
    ta = b0.get("TOTAL_ASSETS", 0) or 0
    tl = b0.get("TOTAL_LIABILITIES", 0) or 0
    te = b0.get("TOTAL_EQUITY", 0) or 0
    sl = b0.get("SHORT_LOAN", 0) or 0
    ll = b0.get("LONG_LOAN", 0) or 0
    inv = b0.get("INVENTORY", 0) or 0
    ar = b0.get("ACCOUNTS_RECE", 0) or 0
    ca = b0.get("TOTAL_CURRENT_ASSETS", 0) or 0
    cl = b0.get("TOTAL_CURRENT_LIAB", 0) or 0
    r = {}
    if tl > 0 and ta > 0: r["debt_ratio"] = round(tl / ta * 100, 1)
    if te > 0: r["interest_bearing_debt_ratio"] = round((sl + ll) / te * 100, 1)
    if cl > 0:
        r["current_ratio"] = round(ca / cl, 2)
        r["quick_ratio"] = round((ca - inv) / cl, 2)
    if ta > 0:
        r["cash_pct"] = round(monetary / ta * 100, 1)
        r["receivable_pct"] = round(ar / ta * 100, 1)
    if ta > 0:
        cash_pct = monetary / ta * 100
        debt_pct = (sl + ll) / ta * 100
        r["deposit_loan_high"] = cash_pct > 25 and debt_pct > 25
        if r["deposit_loan_high"]:
            r["deposit_loan_detail"] = f"货币资金占资产{cash_pct:.0f}%，有息负债占资产{debt_pct:.0f}%，均偏高"
    adv = b0.get("ADVANCE_RECEIVABLES", 0) or 0
    if adv > 0 and ta > 0: r["advance_pct"] = round(adv / ta * 100, 3)
    if len(bs_list) >= 2:
        b1 = bs_list[1]
        r["receivable_trend"] = round((ar - (b1.get("ACCOUNTS_RECE",0) or 0)) / max((b1.get("ACCOUNTS_RECE",0) or 0), 1) * 100, 1)
        r["inventory_trend"] = round((inv - (b1.get("INVENTORY",0) or 0)) / max((b1.get("INVENTORY",0) or 0), 1) * 100, 1)
        if adv > 0 and (b1.get("ADVANCE_RECEIVABLES",0) or 0) > 0:
            r["advance_trend"] = round((adv - (b1.get("ADVANCE_RECEIVABLES",0) or 0)) / max((b1.get("ADVANCE_RECEIVABLES",0) or 0), 1) * 100, 1)
    return r


def _phase2_warnings(bs_ratios: dict, fcf: dict, base_warnings: list) -> list:
    """Phase 2 扩展粉饰检测"""
    all_w = list(base_warnings)
    seen = {w["id"] for w in all_w}
    if bs_ratios.get("deposit_loan_high") and "deposit_loan_high" not in seen:
        all_w.append({"id": "deposit_loan_high", "severity": "high",
                       "signal": "存贷双高", "detail": bs_ratios.get("deposit_loan_detail", "")})
    if fcf.get("fcf") is not None and fcf["fcf"] < 0 and "negative_fcf" not in seen:
        all_w.append({"id": "negative_fcf", "severity": "medium",
                       "signal": "自由现金流为负",
                       "detail": f"经营现金流({fcf.get('latest_ocf',0):.0f})不足以覆盖资本开支({fcf.get('latest_capex',0):.0f})"})
    if bs_ratios.get("receivable_pct", 0) > 30 and bs_ratios.get("receivable_trend", 0) > 20 and "high_receivable" not in seen:
        all_w.append({"id": "high_receivable", "severity": "medium",
                       "signal": "应收账款占比高且快速增长",
                       "detail": f"应收占总资产{bs_ratios['receivable_pct']:.0f}%，同比+{bs_ratios['receivable_trend']:.0f}%"})
    if bs_ratios.get("advance_trend", 0) > 50 and "surge_advance" not in seen:
        all_w.append({"id": "surge_advance", "severity": "medium",
                       "signal": "预付账款剧增", "detail": f"预付同比+{bs_ratios['advance_trend']:.0f}%"})
    if bs_ratios.get("inventory_trend", 0) > 50 and "surge_inventory" not in seen:
        all_w.append({"id": "surge_inventory", "severity": "low",
                       "signal": "存货激增", "detail": f"存货同比+{bs_ratios['inventory_trend']:.0f}%，可能滞销"})
    return all_w


def get_stock_detail_phase2(code: str, date: str = "20260331") -> Optional[dict]:
    """Phase 2 增强版 — Phase 1 + 直连BS/CF"""
    from _plus_.financial_penetration import get_stock_detail
    base = get_stock_detail(code, date=date)
    if base is None:
        return None
    bs_list, cf_list, is_new = fetch_bs_cf(code, target_date=date)
    dupont = _dupont_decomposition(base, bs_list)
    fcf = _calc_free_cash_flow(cf_list)
    bs_ratios = _analyze_bs_ratios(bs_list)
    all_warnings = _phase2_warnings(bs_ratios, fcf, base.get("fraud_warnings", []))

    ds = dict(base.get("dimension_scores", {}))
    if bs_ratios:
        dr = bs_ratios.get("debt_ratio", 60)
        ds["debt_safety"] = 85 if dr <= 40 else 70 if dr <= 55 else 50 if dr <= 70 else 30 if dr <= 85 else 10
        if bs_ratios.get("current_ratio", 2) < 1:
            ds["debt_safety"] = max(10, ds["debt_safety"] - 15)
    if fcf.get("fcf") is not None and fcf["fcf"] < 0:
        ds["cash_flow_quality"] = min(ds.get("cash_flow_quality", 50), 40)

    _W = {"cash_flow_quality": 0.25, "revenue_authenticity": 0.20, "profitability": 0.20, "debt_safety": 0.15, "fraud_warning": 0.20}
    new_total = sum(ds.get(k, 50) * w for k, w in _W.items())

    return {
        "code": code, "name": base.get("name",""), "industry": base.get("industry",""),
        "total_score": round(new_total, 1), "rating": _rating_label(new_total),
        "dimension_scores": ds, "dimension_details": base.get("dimension_details",{}),
        "fraud_warnings": all_warnings, "fraud_warning_count": len(all_warnings),
        "phase": "v2+BS/CF(direct)", "is_new_fetch": is_new,
        "dupont": dupont, "free_cash_flow": fcf, "bs_ratios": bs_ratios,
        "bs_loaded": len(bs_list) > 0,
        "revenue": base.get("revenue"), "revenue_yoy": base.get("revenue_yoy"),
        "net_profit": base.get("net_profit"), "net_profit_yoy": base.get("net_profit_yoy"),
        "gross_margin": base.get("gross_margin"), "roe": base.get("roe"),
        "eps": base.get("eps"), "ocfps": base.get("ocfps"), "bps": base.get("bps"),
    }
