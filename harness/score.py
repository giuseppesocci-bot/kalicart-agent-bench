#!/usr/bin/env python3
"""Scorer. Usage: score.py runs/REPORT.txt [--note "..."]  |  score.py --summary
Rebuilds the agent's basket, asks WooCommerce (through the store's KaliCart quote, read-only) for its delivered total,
checks the task's constraints against the live catalog, and the budget
compared with the delivered total (shipping included)."""
import csv, json, os, re, statistics, sys, time, urllib.request, urllib.error, datetime
HERE = os.path.dirname(os.path.abspath(__file__))
TASKS = json.load(open(os.path.join(HERE, os.environ.get('BENCH_TASKS', 'tasks.json'))))
RESULTS = os.path.join(HERE, os.environ.get('RESULTS_FILE', 'results.csv'))
UA = 'kalicart-agent-bench (+https://github.com/giuseppesocci-bot/kalicart-agent-bench)'
FIELDS = ['scored_at', 'report', 'task', 'store', 'arm', 'reported_total', 'truth_total', 'total_source',
          'outcome', 'detail', 'calls_total', 'calls_failed', 'tokens', 'note']

def http(method, url, body=None):
    time.sleep(float(os.environ.get('BENCH_MIN_INTERVAL', '1.0')))  # same politeness as the agent
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={'User-Agent': UA, 'Content-Type': 'application/json', 'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=40) as r: return r.status, json.loads(r.read().decode() or 'null')
    except urllib.error.HTTPError as e:
        try: return e.code, json.loads(e.read().decode() or 'null')
        except Exception: return e.code, None

def block(text):
    for m in re.finditer(r'\{\s*"bench_result"\s*:', text):
        depth = 0
        for j in range(m.start(), len(text)):
            depth += text[j] == '{'; depth -= text[j] == '}'
            if depth == 0:
                try: return json.loads(text[m.start():j + 1])['bench_result']
                except Exception: break
    return None

def lines_with_facts(store, items):
    out = []
    for it in items:
        store = it.get('_store') or store
        pid, vid, qty = int(it.get('product_id') or 0), it.get('variation_id'), int(it.get('quantity') or 1)
        code, p = http('GET', f'https://{store}/wp-json/kalicart/v1/catalog/product/{pid}')
        p = (p or {}).get('data', p) if isinstance(p, dict) else {}
        if not vid and not p.get('variants'):  # a Store API agent may report the variation id as product_id
            c2, sp = http('GET', f'https://{store}/wp-json/wc/store/v1/products/{pid}')
            if c2 == 200 and isinstance(sp, dict) and sp.get('parent'):
                vid, pid = pid, int(sp['parent'])
                code, p = http('GET', f'https://{store}/wp-json/kalicart/v1/catalog/product/{pid}'); p = (p or {}).get('data', p) or {}
        attrs = {}
        for v in p.get('variants') or []:
            if vid and int(v['variation_id']) == int(vid): attrs = v.get('attributes') or {}
        line = {'product_id': pid, 'quantity': qty}
        if vid: line['variation_id'] = int(vid)
        c3, sp = http('GET', f'https://{store}/wp-json/wc/store/v1/products/{pid}')
        cats = ' '.join(c.get('name', '') for c in (sp or {}).get('categories', [])) if c3 == 200 and isinstance(sp, dict) else ''
        line['_store'] = store
        out.append((line, ((p.get('name') or '') + ' ' + cats).lower(), attrs))  # product name + store categories
    return out

def check(t, L):
    c = t.get('checks', {})
    if len(L) < c.get('min_lines', 2): return f"lines {len(L)} < {c.get('min_lines', 2)}"
    has = lambda n, words: any(w in n for w in words)
    if 'qty_line' in c:
        q = c['qty_line']
        if not any(l['quantity'] == q['qty'] and has(n, q['name']) and all(v in str(a.get(k, '')) for k, v in q['attr'].items()) for l, n, a in L):
            return f"no line {q['name']} qty {q['qty']} {q['attr']}"
    if 'distinct_products_named' in c:
        d = c['distinct_products_named']
        if len({l['product_id'] for l, n, _ in L if has(n, d['name'])}) < d['count']: return f"fewer than {d['count']} distinct {d['name']}"
    if 'product_attr_in' in c:
        d = c['product_attr_in']
        if not any(l['product_id'] == d['product_id'] and str(a.get(d['attr'], '')).lower() in d['values'] for l, _, a in L): return f"product {d['product_id']} not in sizes {d['values']}"
    if 'line_named_attr' in c:
        d = c['line_named_attr']
        if not any(has(n, d['name']) and all(v in str(a.get(k, '')) for k, v in d['attr'].items()) for _, n, a in L): return f"no line {d['name']} {d['attr']}"
    for st, words in c.get('per_store_named', {}).items():
        if not any(l['_store'] == st and has(n, words) for l, n, _ in L): return f"no {words} on {st}"
    return ''

def score(path, note=''):
    name = os.path.basename(path); rep = block(open(path, encoding='utf-8', errors='replace').read())
    task, arm = name.split('-')[0], name.split('-')[1]
    t = TASKS[task]; row = {k: '' for k in FIELDS}
    row.update(scored_at=datetime.datetime.now().strftime('%Y-%m-%d %H:%M'), report=name, task=task, store=t['store'], arm=arm, note=note)
    side = os.path.splitext(path)[0] + '.json'  # harness measurements, written by agent.py
    if os.path.exists(side):
        m = json.load(open(side)).get('measured', {}); row['tokens'] = int(m.get('tokens_in', 0)) + int(m.get('tokens_out', 0))
    if not rep: row.update(outcome='no_json_block'); return row
    row.update(reported_total=rep.get('reported_total'), total_source=rep.get('total_source', ''),
               calls_total=rep.get('calls_total'), calls_failed=rep.get('calls_failed'))
    stores = t.get('stores', [t['store']])
    items = rep.get('items') or []
    for it in items:
        st = str(it.get('store') or '').replace('https://', '').strip('/')
        it['_store'] = next((s for s in stores if s in st), stores[0])
    L = lines_with_facts(stores[0], items)
    truth = 0.0
    for st in stores:
        mine = [{k: v for k, v in l.items() if k != '_store'} for l, _, _ in L if l['_store'] == st]
        if not mine: row.update(outcome='fail_constraints', detail=f'no items for {st}'); return row
        dest = (t.get('destinations') or {}).get(st) or t['destination']
        code, q = http('POST', f"https://{st}/wp-json/kalicart/v1/checkout/quote", {'items': mine, 'destination': dest})
        q = (q or {}).get('data', q) if isinstance(q, dict) else {}
        if not isinstance(q, dict) or q.get('status') != 'quoted':
            row.update(outcome='truth_unavailable', detail=f"{st}: {(q or {}).get('reason', code)}"); return row
        truth += float(q['total'])
    row['truth_total'] = f'{truth:.2f}'
    try: reported = float(rep.get('reported_total'))
    except Exception: reported = None
    bad = check(t, L)
    if bad: row.update(outcome='fail_constraints', detail=bad)
    elif truth > t['budget'] + 0.005: row['outcome'] = 'fail_over_budget'
    elif reported is None or abs(reported - truth) > 0.015: row['outcome'] = 'fail_wrong_total'
    elif str(rep.get('total_source', '')).strip().lower().startswith('api'): row['outcome'] = 'verified'
    else: row['outcome'] = 'deduced'
    return row

def summary():
    rows = list(csv.DictReader(open(RESULTS))) if os.path.exists(RESULTS) else []
    calls = lambda rs: [int(r['calls_total']) for r in rs if str(r['calls_total']).isdigit()]
    for arm in 'KS':
        rs = [r for r in rows if r['arm'] == arm]
        print(f"{arm}: runs {len(rs)}, verified {sum(r['outcome']=='verified' for r in rs)}, median calls {statistics.median(calls(rs)) if calls(rs) else '-'}")
        for k in TASKS:
            rk = [r for r in rs if r['task'] == k]
            print(f"   {k}: {sum(r['outcome']=='verified' for r in rk)}/{len(rk)} verified, calls {calls(rk)}, outcomes {[r['outcome'] for r in rk]}")
    K = [r for r in rows if r['arm'] == 'K']; S = [r for r in rows if r['arm'] == 'S']
    if K and S:
        vk, vs = sum(r['outcome'] == 'verified' for r in K), sum(r['outcome'] == 'verified' for r in S)
        mk, ms = statistics.median(calls(K)), statistics.median(calls(S))
        print(f"Accuracy not worse (verified K >= S - 1): {'YES' if vk >= vs - 1 else 'NO'} ({vk} vs {vs})")
        print(f"Calls (median K <= 60% of S): {'YES' if mk <= 0.6 * ms else 'NO'} ({mk} vs {ms}, {100 - 100 * mk / ms:.0f}% fewer)")
        tok = lambda rs: [int(r['tokens']) for r in rs if str(r.get('tokens', '')).isdigit()]
        tk, ts = statistics.median(tok(K)), statistics.median(tok(S))
        print(f"Tokens (median K <= 60% of S): {'YES' if tk <= 0.6 * ts else 'NO'} ({tk:.0f} vs {ts:.0f}, {100 - 100 * tk / ts:.0f}% fewer)")
    else: print('No runs of both arms yet.')

if __name__ == '__main__':
    if sys.argv[1:] == ['--summary']: summary(); sys.exit(0)
    note = sys.argv[sys.argv.index('--note') + 1] if '--note' in sys.argv else ''
    row = score(sys.argv[1], note); new = not os.path.exists(RESULTS)
    with open(RESULTS, 'a', newline='') as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new: w.writeheader()
        w.writerow(row)
    print(json.dumps(row, ensure_ascii=False))
