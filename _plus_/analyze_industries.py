# -*- coding: utf-8 -*-
import json, os

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '_internal')

with open(os.path.join(BASE, 'sector_map.json'), 'r', encoding='utf-8') as f:
    smap = json.load(f)

with open(os.path.join(BASE, 'industry_map.json'), 'r', encoding='utf-8') as f:
    imap = json.load(f)

es = sorted(set(imap.values()))

tdx_all = sorted(set(info['name'] for info in smap['code2name'].values()))

matched = []
unmatched = []
for em in es:
    if em == '-': continue
    clean = em.rstrip('0123456789ⅠⅡⅢⅣⅤⅥ ')
    if clean in tdx_all:
        matched.append((em, clean))
        continue
    query = clean.replace('Ⅱ','').strip()
    for td in tdx_all:
        if query in td or td in query:
            matched.append((em, td))
            break
    else:
        unmatched.append(em)

print(f'EM: {len(es)-1} | TDX: {len(tdx_all)}')
print(f'Matched: {len(matched)} | Unmatched: {len(unmatched)}\n')
print('=== 未匹配 ===')
for u in unmatched: print(f'  {u}')
print('\n=== 匹配明细 ===')
for em, td in matched: print(f'  {em:16s} -> {td}')
