# -*- coding: utf-8 -*-
"""扫描性能诊断：测量各环节耗时。"""
import time, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '_internal'))

def diag():
    # Pick 20 stocks across different sectors
    from scripts.stock_gui_V14 import load_stock_tdx
    import concurrent.futures

    codes = [
        ('000858', '五粮液'), ('600519', '贵州茅台'), ('002371', '北方华创'),
        ('601991', '大唐发电'), ('000791', '甘肃能源'), ('600111', '北方稀土'),
        ('000657', '中钨高新'), ('601985', '中国核电'), ('601088', '中国神华'),
        ('600030', '中信证券'), ('000001', '平安银行'), ('600276', '恒瑞医药'),
        ('002415', '海康威视'), ('300750', '宁德时代'), ('000725', '京东方A'),
        ('603019', '中科曙光'), ('300059', '东方财富'), ('600809', '山西汾酒'),
        ('000338', '潍柴动力'), ('002230', '科大讯飞'),
    ]

    timers = {
        'sector_power': [],
        'plus_pipeline': [],
        'v15_filter': [],
        'load_tdx': [],
        'total': [],
    }

    def scan_one(code, name, nm):
        t0 = time.time()

        t1 = time.time()
        df = load_stock_tdx(code)
        t2 = time.time()
        timers['load_tdx'].append(t2 - t1)

        if df is None or len(df) < 120:
            return

        # V15 filter only (baseline)
        t3 = time.time()
        try:
            from v15_filter import compute_filtered_signal_from_df
            compute_filtered_signal_from_df(df)
        except: pass
        t4 = time.time()
        timers['v15_filter'].append(t4 - t3)

        # Plus pipeline
        t5 = time.time()
        try:
            from _plus_.plus_pipeline import compute_plus_pipeline
            compute_plus_pipeline(df, code=code, name=name, sector_map=None, news_items=None)
        except: pass
        t6 = time.time()
        timers['plus_pipeline'].append(t6 - t5)

        # Sector power
        t7 = time.time()
        try:
            from _plus_.sector_power import compute_sector_power_cached
            compute_sector_power_cached(code, name, None)
        except: pass
        t8 = time.time()
        timers['sector_power'].append(t8 - t7)

        timers['total'].append(time.time() - t0)

    print(f'Testing {len(codes)} stocks (sequential, 4 threads)...')

    t_start = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futs = {pool.submit(scan_one, c, n, i): c for i, (c, n) in enumerate(codes)}
        for f in concurrent.futures.as_completed(futs):
            f.result()
    total = time.time() - t_start

    print(f'\n┌─────────────────────────────────────────────┐')
    print(f'│ {"阶段":12s} {"均值":>7s} {"中位":>7s} {"最大":>7s} {"占比":>7s} │')
    print(f'├{"─"*45}┤')
    for label in ['load_tdx', 'v15_filter', 'plus_pipeline', 'sector_power', 'total']:
        vals = timers[label]
        if not vals: continue
        avg = sum(vals) / len(vals)
        med = sorted(vals)[len(vals)//2]
        mx = max(vals)
        pct = sum(vals) / total * 100 if total > 0 else 0
        print(f'│ {label:12s} {avg:6.2f}s {med:6.2f}s {mx:6.2f}s {pct:6.1f}% │')
    print(f'└{"─"*45}┘')
    print(f'\n总时长: {total:.1f}s | 20只 4线程 | 410只预估: {total/20*410/4:.0f}s')
    print(f'\n各环节占总时长比重:')
    for label in ['load_tdx', 'v15_filter', 'plus_pipeline', 'sector_power']:
        vals = timers[label]
        if vals:
            print(f'  {label}: {sum(vals)/total*100:.1f}%')

    # Now test sector_power alone with cold vs warm cache
    print(f'\n── 板块动力冷热缓存对比 ──')
    from _plus_.sector_power import compute_sector_power, _power_cache
    _power_cache.clear()

    for code, name in [('000858', '五粮液'), ('002371', '北方华创'), ('000657', '中钨高新')]:
        t1 = time.time()
        r1 = compute_sector_power(code, name, None)
        t_cold = time.time() - t1

        t2 = time.time()
        r2 = compute_sector_power(code, name, None)
        t_warm = time.time() - t2

        print(f'  {code} {name}: 冷={t_cold:.2f}s 热={t_warm:.3f}s | power={r1.get("sector_power",0):.0f} sector={r1.get("sector_name","?")}')

if __name__ == '__main__':
    diag()
