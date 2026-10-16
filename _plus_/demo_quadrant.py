# -*- coding: utf-8 -*-
"""板块资金流向 Tab Demo — v2：精简板块、Δz 文字名单"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '_internal'))

import struct, json
from collections import defaultdict
import matplotlib
matplotlib.use('TkAgg')
import matplotlib.pyplot as plt
import numpy as np

plt.rcParams['font.sans-serif'] = ['Microsoft YaHei']
plt.rcParams['axes.unicode_minus'] = False

# ── 加载 东方财富→TDX 映射 ──
from _plus_.sector_power import _em_industry_to_tdx, _build_em_tdx_map
_build_em_tdx_map()

base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ipath = os.path.join(base_dir, '_internal', 'industry_map.json')
with open(ipath, 'r', encoding='utf-8') as f:
    imap = json.load(f)

# 收集所有映射到 TDX 的板块（去重）
mapped_tdx = set()
for em_ind in set(imap.values()):
    tdx = _em_industry_to_tdx(em_ind)
    if tdx:
        mapped_tdx.add(tdx)

# 加载板块名称
spath = os.path.join(base_dir, '_internal', 'sector_map.json')
with open(spath, 'r', encoding='utf-8') as f:
    nc = json.load(f).get('code2name', {})

TDX_SYNTHETIC = {
    "sh880981","sh880982","sh880983","sh880984","sh880985",
    "sh880986","sh880987","sh880988","sh880989","sh880990",
    "sh880991","sh880992","sh880993",
}

hy_codes = []
hy_names = {}
for code in mapped_tdx:
    if code not in TDX_SYNTHETIC and code in nc:
        hy_codes.append(code)
        hy_names[code] = nc[code].get('name', code)

print(f'精简后板块数: {len(hy_codes)}（仅东方财富映射到的板块）')

# ── 读日线 ──
TDX_BASE = r'D:\通达信\vipdoc\sh\lday'
daily_totals = defaultdict(float)
sector_daily = defaultdict(dict)
LOOKBACK = 240

for code in hy_codes:
    path = os.path.join(TDX_BASE, f'{code}.day')
    if not os.path.exists(path): continue
    with open(path,'rb') as f: data = f.read()
    n = len(data)//32
    for i in range(max(0, n-LOOKBACK), n):
        b = data[i*32:(i+1)*32]
        date_raw = struct.unpack('<I', b[0:4])[0]
        vol = struct.unpack('<I', b[24:28])[0]
        daily_totals[date_raw] += vol
        sector_daily[code][date_raw] = vol

all_dates = sorted(daily_totals.keys())

# ── 日维度计算 ──
def calc_results(recent_n, prev_offset, lookback_n, date_list, totals_dict, sector_dict):
    """返回 (results, dz_list)"""
    results = []
    dz_list = []
    for code in hy_codes:
        if code not in sector_dict: continue
        shares = []
        for d in date_list:
            if d in sector_dict[code] and d in totals_dict and totals_dict[d] > 0:
                shares.append(sector_dict[code][d] / totals_dict[d] * 100)
        if len(shares) < lookback_n: continue

        # std_detection: drop zero-variance sectors
        mean_s = sum(shares) / len(shares)
        sq_diffs = [(s - mean_s)**2 for s in shares]
        var_s = sum(sq_diffs) / len(shares)
        std_s = var_s**0.5 if var_s > 1e-12 else 0
        if std_s < 1e-6: continue  # skip zero-variance

        if len(shares) >= recent_n + prev_offset:
            recent = sum(shares[-recent_n:]) / recent_n
            prev = sum(shares[-(recent_n+prev_offset):-prev_offset or None]) / recent_n
        elif len(shares) >= recent_n:
            recent = sum(shares[-recent_n:]) / recent_n
            prev = recent
        else:
            continue

        z_curr = (recent - mean_s) / std_s
        z_prev = (prev - mean_s) / std_s
        dz = z_curr - z_prev
        results.append((round(recent, 2), round(z_curr, 2), code, hy_names.get(code, code), round(dz, 2), round(prev, 2)))

        if abs(dz) >= 0.4:
            dz_list.append((hy_names.get(code, code), round(z_curr, 2), round(dz, 2), round(recent, 2), round(prev, 2)))
    return results, dz_list

daily_results, daily_dz = calc_results(5, 5, 180, all_dates, daily_totals, sector_daily)

# ── 周维度计算 ──
weekly_totals = defaultdict(float)
weekly_sector = defaultdict(lambda: defaultdict(float))
for d in all_dates:
    week_key = d // 100 * 100 + (d % 100) // 7 * 7
    for code in hy_codes:
        if d in sector_daily[code]:
            v = sector_daily[code][d]
            weekly_totals[week_key] += v
            weekly_sector[code][week_key] += v

all_weeks = sorted(weekly_totals.keys())

weekly_results, weekly_dz = [], []
for code in hy_codes:
    if code not in weekly_sector: continue
    shares = []
    for w in all_weeks:
        if w in weekly_sector[code] and weekly_totals[w] > 0:
            shares.append(weekly_sector[code][w] / weekly_totals[w] * 100)
    if len(shares) < 30: continue

    mean_s = sum(shares)/len(shares)
    sq_diffs = [(s-mean_s)**2 for s in shares]
    var_s = sum(sq_diffs)/len(shares)
    std_s = var_s**0.5 if var_s > 1e-12 else 0
    if std_s < 1e-6: continue

    if len(shares) >= 4:
        recent2 = sum(shares[-2:])/2
        prev2 = sum(shares[-4:-2])/2
    else:
        continue
    z_curr = (recent2 - mean_s) / std_s
    z_prev = (prev2 - mean_s) / std_s
    dz = z_curr - z_prev
    weekly_results.append((round(recent2, 2), round(z_curr, 2), code, hy_names.get(code, code), round(dz, 2), round(prev2, 2)))

    if abs(dz) >= 0.4:
        weekly_dz.append((hy_names.get(code, code), round(z_curr, 2), round(dz, 2), round(recent2, 2), round(prev2, 2)))

# ── 画图 ──
fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(18, 7.5))

def draw_scatter(ax, data, title):
    xs = [r[0] for r in data]
    ys = [r[1] for r in data]
    dzs = [r[4] for r in data]
    names = [r[3] for r in data]

    # 点大小：按 z 绝对值缩放
    sizes = [max(25, min(180, (abs(y)+1)*45)) for y in ys]
    # 颜色：按 z 方向
    colors = ['#c62828' if y > 0 else '#1565c0' for y in ys]

    x_mid = np.median(xs)
    x_max = max(xs) * 1.05
    y_max = max(ys) * 1.15
    y_min = min(ys) * 1.15

    # 象限背景
    ax.axvspan(x_mid, x_max, ymin=0.5, ymax=1.0, alpha=0.06, color='green')
    ax.axvspan(0, x_mid, ymin=0.5, ymax=1.0, alpha=0.04, color='orange')
    ax.axvspan(0, x_mid, ymin=0.0, ymax=0.5, alpha=0.02, color='gray')
    ax.axvspan(x_mid, x_max, ymin=0.0, ymax=0.5, alpha=0.04, color='red')

    ax.axhline(0, color='#666', ls='--', lw=0.6, alpha=0.4)
    ax.axvline(x_mid, color='#666', ls='--', lw=0.6, alpha=0.4)

    ax.scatter(xs, ys, c=colors, s=sizes, alpha=0.75, edgecolors='white', linewidth=0.5, zorder=3)

    # 标注极端板块（|z|>2）
    for x, y, name, dz in zip(xs, ys, names, dzs):
        if abs(y) > 1.8:
            arrow = '↑' if dz > 0.3 else ('↓' if dz < -0.3 else '→')
            ax.annotate(f'{name}{arrow}', (x, y), fontsize=7, alpha=0.9,
                       textcoords='offset points', xytext=(4, 4), zorder=4)

    ax.set_xlabel('绝对占比 (%)', fontsize=10)
    ax.set_ylabel('Z-score', fontsize=10)
    ax.set_title(title, fontsize=12, fontweight='bold')
    ax.grid(True, alpha=0.15, zorder=0)

    # 象限标注
    ax.text(x_max, y_max, '主力进攻', fontsize=9, ha='right', va='top', color='green', fontweight='bold')
    ax.text(0.02, y_max, '边缘升温', fontsize=9, ha='left', va='top', color='orange', fontweight='bold')
    ax.text(0.02, y_min, '无人区', fontsize=9, ha='left', va='bottom', color='#999')
    ax.text(x_max, y_min, '主力撤退', fontsize=9, ha='right', va='bottom', color='red', fontweight='bold')

draw_scatter(ax1, daily_results, '日维度（近5日 vs 240日历史）')
draw_scatter(ax2, weekly_results, '周维度（近2周 vs 48周历史）')

plt.suptitle('板块资金流向 · 双维度监测\n（仅显示东方财富映射的板块）', fontsize=14, fontweight='bold', y=1.02)
plt.tight_layout(rect=[0, 0.05, 1, 0.95])

# 保存
out_path = os.path.join(os.path.expanduser('~'), 'Desktop', '板块资金流向_demo_v2.png')
fig.savefig(out_path, dpi=150, bbox_inches='tight')
print(f'图表已保存: {out_path}')
plt.close()

# ── Δz 文字名单 ──
print()
print('='*70)
print('  Δz 异常板块（近5日 z 变化，绝对值 >= 0.4）')
print('='*70)
daily_dz.sort(key=lambda x: -x[2])
print(f'\n【日维度】 流入加快/流出加快 ({len(daily_dz)} 个板块)')
print(f'{"板块":10s} {"当前z":>6s} {"Δz":>6s} {"当前占比":>8s} {"前占比":>8s} {"方向":5s}')
print('-'*50)
for name, z, dz, curr, prev in daily_dz[:6]:
    direction = '↑流入加快' if dz > 0 else '↓流出加快'
    print(f'{name:8s} {z:>+6.2f} {dz:>+6.2f} {curr:>7.2f}% {prev:>7.2f}% {direction:8s}')
if len(daily_dz) > 6:
    print(f'... 还有 {len(daily_dz)-6} 个板块')
print()
print(f'【周维度】 流入加快/流出加快 ({len(weekly_dz)} 个板块)')
print(f'{"板块":10s} {"当前z":>6s} {"Δz":>6s} {"当前占比":>8s} {"前占比":>8s} {"方向":5s}')
print('-'*50)
weekly_dz.sort(key=lambda x: -x[2])
for name, z, dz, curr, prev in weekly_dz[:6]:
    direction = '↑流入加快' if dz > 0 else '↓流出加快'
    print(f'{name:8s} {z:>+6.2f} {dz:>+6.2f} {curr:>7.2f}% {prev:>7.2f}% {direction:8s}')
if len(weekly_dz) > 6:
    print(f'... 还有 {len(weekly_dz)-6} 个板块')
