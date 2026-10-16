# -*- coding: utf-8 -*-
"""深入诊断 plus_pipeline 内部三个模块耗时。"""
import time, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '_internal'))

def diag2():
    from scripts.stock_gui_V14 import load_stock_tdx
    import concurrent.futures

    codes = [
        '000858', '600519', '002371', '601991', '000791', '600111',
        '000657', '601985', '601088', '600030', '000001', '600276',
        '002415', '300750', '000725', '603019', '300059', '600809',
        '000338', '002230',
    ]

    timers = {
        'fund_flow': [],
        'multi_tf': [],
        'sentiment': [],
        'v15_baseline': [],
    }

    def scan_one(code, i):
        df = load_stock_tdx(code)
        if df is None or len(df) < 120:
            return

        # Baseline: raw V15
        t0 = time.time()
        try:
            from scripts.stock_gui_V14 import compute_v15_pipeline
            compute_v15_pipeline(df, code=code, name=code, sector_map=None)
        except: pass
        t1 = time.time()
        timers['v15_baseline'].append(t1 - t0)

        # Fund flow
        t2 = time.time()
        try:
            from _plus_.fund_flow_filter import _filter_fund_flow
            _filter_fund_flow(code)
        except: pass
        t3 = time.time()
        timers['fund_flow'].append(t3 - t2)

        # Multi-tf resonance
        t4 = time.time()
        try:
            from _plus_.multi_tf_resonance import compute_resonance
            compute_resonance(code)
        except: pass
        t5 = time.time()
        timers['multi_tf'].append(t5 - t4)

        # Sentiment
        t6 = time.time()
        try:
            from _plus_.sentiment_quant import compute_sentiment
            compute_sentiment(code, code, news_items=None, sector_map=None)
        except: pass
        t7 = time.time()
        timers['sentiment'].append(t7 - t6)

    print('Testing 20 stocks (sequential, 4 threads)...')

    t_start = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futs = {pool.submit(scan_one, c, i): c for i, c in enumerate(codes)}
        for f in concurrent.futures.as_completed(futs):
            f.result()
    total = time.time() - t_start

    print(f'\n┌─────────────────────────────────────────────┐')
    print(f'| {"Module":14s} {"Mean":>7s} {"Med":>7s} {"Max":>7s} {"Sum":>7s} |')
    print(f'|{"─"*45}|')
    for label in ['v15_baseline', 'fund_flow', 'multi_tf', 'sentiment']:
        vals = timers[label]
        if not vals: continue
        avg = sum(vals)/len(vals)
        med = sorted(vals)[len(vals)//2]
        mx = max(vals)
        sm = sum(vals)
        print(f'| {label:14s} {avg:6.2f}s {med:6.2f}s {mx:6.2f}s {sm:6.1f}s |')
    print(f'└{"─"*45}┘')
    print(f'\nTotal: {total:.1f}s | 20 stocks 4 threads | 410 estimate: {total/20*410/4:.0f}s')

    # Breakdown
    all_sum = sum(sum(timers[k]) for k in timers)
    print(f'\nWeight breakdown (of total module time):')
    for label in ['v15_baseline', 'fund_flow', 'multi_tf', 'sentiment']:
        vals = timers[label]
        if vals:
            print(f'  {label}: {sum(vals)/all_sum*100:.1f}% ({sum(vals):.1f}s)')

if __name__ == '__main__':
    diag2()
