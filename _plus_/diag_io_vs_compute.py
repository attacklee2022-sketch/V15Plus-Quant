# -*- coding: utf-8 -*-
"""诊断 load_5min 内部的 I/O vs 计算耗时。"""
import time, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '_internal'))

codes = ['000858','002371','600111','000657','601088','600030','000001',
         '600276','002415','300750','000725','603019','300059','600809']

def diag3():
    from _plus_.minline_reader import load_5min, aggregate
    t_read = []
    t_agg = []
    t_score = []
    t_total = []

    for code in codes:
        t0 = time.time()
        df5 = load_5min(code, recent_days=10)
        t1 = time.time()
        t_read.append(t1 - t0)

        if df5 is None:
            continue

        t2 = time.time()
        tf15 = aggregate(df5, 3)
        tf30 = aggregate(df5, 6)
        tf60 = aggregate(df5, 12)
        t3 = time.time()
        t_agg.append(t3 - t2)

        t4 = time.time()
        from _plus_.multi_tf_resonance import _tf_trend_score
        s15 = _tf_trend_score(tf15, "15")
        s30 = _tf_trend_score(tf30, "30")
        s60 = _tf_trend_score(tf60, "60")
        t5 = time.time()
        t_score.append(t5 - t4)

        t_total.append(time.time() - t0)

    print(f'load_5min:  mean={sum(t_read)/len(t_read):.3f}s  max={max(t_read):.3f}s')
    print(f'aggregate:  mean={sum(t_agg)/len(t_agg):.3f}s  max={max(t_agg):.3f}s')
    print(f'tf_score:   mean={sum(t_score)/len(t_score):.3f}s  max={max(t_score):.3f}s')
    print(f'total:      mean={sum(t_total)/len(t_total):.3f}s  max={max(t_total):.3f}s')
    print()
    print(f'I/O占比: {sum(t_read)/sum(t_total)*100:.0f}%  |  计算占比: {(sum(t_agg)+sum(t_score))/sum(t_total)*100:.0f}%')

if __name__ == '__main__':
    diag3()
