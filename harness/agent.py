#!/usr/bin/env python3
"""One benchmark run. Usage: agent.py TASK ARM N   (e.g. C1 K 1); ARM is K (KaliCart) or S (Store API).
A minimal tool-using agent: DeepSeek chat model + ONE tool (http_request), identical for both arms.
No cookie jar: the agent must carry any token itself from response headers. Calls are counted by
the harness, not self-reported. Writes runs/TASK-ARM-N.txt and .json, then scores it."""
import json, os, sys, time, urllib.request, urllib.error, subprocess
from urllib.parse import urlparse
HERE = os.path.dirname(os.path.abspath(__file__))
if os.environ.get('BENCH_I_OWN_THESE_STORES') != 'yes':
    sys.exit('Refusing to run: set BENCH_I_OWN_THESE_STORES=yes to confirm you own these stores or have permission to test them.')
TASK, ARM, N = sys.argv[1], sys.argv[2], sys.argv[3]
T = json.load(open(os.path.join(HERE, os.environ.get('BENCH_TASKS', 'tasks.json'))))[TASK]
TEMPLATE = open(os.path.join(HERE, 'prompts', f"{T.get('template', 'single-store')}-{ARM}.txt")).read()
PROMPT = (TEMPLATE.replace('{NEGOZIO}', T['store']).replace('{COMPITO}', T['task'])
          .replace('{BUDGET}', T['budget_text']).replace('{DESTINAZIONE}', T['destination_text']))
for _i, _s in enumerate(T.get('stores', [])):  # two-store tasks: {NEGOZIO_A}, {NEGOZIO_B}
    PROMPT = PROMPT.replace('{NEGOZIO_' + 'AB'[_i] + '}', _s)
KEY = os.environ['BENCH_API_KEY']; MODEL = os.environ.get('BENCH_MODEL', 'deepseek-flash')
API_BASE = os.environ.get('BENCH_API_BASE', 'https://api.deepseek.com').rstrip('/')  # any OpenAI-compatible chat API
MAX_TURNS, BODY_LIMIT, MAX_COST_USD = 40, 30000, 0.60  # estimate overstates real cost about 3x
PRICE_IN, PRICE_OUT = float(os.environ.get('BENCH_PRICE_IN', '0.3')), float(os.environ.get('BENCH_PRICE_OUT', '1.2'))  # USD per 1M tokens, conservative
os.makedirs(os.path.join(HERE, 'runs'), exist_ok=True)
OUT = os.path.join(HERE, 'runs', f'{TASK}-{ARM}-{N}')
if os.path.exists(OUT + '.txt'): sys.exit(f'exists: {OUT}.txt')
UA = 'kalicart-agent-bench (+https://github.com/giuseppesocci-bot/kalicart-agent-bench)'
TOOLS = [{"type": "function", "function": {"name": "http_request",
  "description": "Make one HTTP request to the store. Returns status, response headers and body (body truncated to 30000 chars).",
  "parameters": {"type": "object", "properties": {
    "method": {"type": "string", "enum": ["GET", "POST", "PUT", "DELETE"]},
    "url": {"type": "string"},
    "headers": {"type": "object", "additionalProperties": {"type": "string"}},
    "body": {"type": "string", "description": "raw request body, e.g. JSON text"}},
    "required": ["method", "url"]}}}]
# System and task prompts are in Italian, exactly as used in the published runs (the stores are Italian).
SYSTEM = "Sei un agente che esegue compiti di acquisto via API HTTP. Hai un solo strumento: http_request. Non ci sono cookie automatici. Quando hai finito rispondi con il report richiesto."
calls = []

# Politeness toward the store: at least MIN_INTERVAL seconds between two requests to the same host, and after a 429
# no request to that host until its Retry-After has passed. The agent still sees every response as the store sent it.
MIN_INTERVAL = float(os.environ.get('BENCH_MIN_INTERVAL', '1.0'))
_next_ok = {}

def _polite_wait(host):
    wait = _next_ok.get(host, 0) - time.time()
    if wait > 0:
        time.sleep(wait)

def http(method, url, headers=None, body=None):
    host = urlparse(url).hostname or ''
    if not any(host == s or host.endswith('.' + s) for s in T.get('stores', [T['store']])):
        return {'error': 'host_not_allowed'}, None
    h = {'User-Agent': UA, 'Accept': 'application/json'}; h.update(headers or {})
    if body and not any(k.lower() == 'content-type' for k in h): h['Content-Type'] = 'application/json'
    req = urllib.request.Request(url, method=method, data=body.encode() if body else None, headers=h)
    _polite_wait(host)
    try:
        r = urllib.request.urlopen(req, timeout=40); code, hdr, txt = r.status, dict(r.headers), r.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as e:
        code, hdr, txt = e.code, dict(e.headers), e.read().decode('utf-8', 'replace')
    except Exception as e:
        return {'error': f'network: {e}'}, 0
    retry = next((v for k, v in hdr.items() if k.lower() == 'retry-after'), None)
    pause = MIN_INTERVAL
    if code == 429:
        try: pause = max(pause, float(retry))
        except (TypeError, ValueError): pause = max(pause, 30.0)
    _next_ok[host] = time.time() + pause
    keep = {k: v for k, v in hdr.items() if k.lower() in ('nonce', 'cart-token', 'x-wp-total', 'x-wp-totalpages', 'link', 'content-type', 'location', 'retry-after')}
    b = txt if len(txt) <= BODY_LIMIT else txt[:BODY_LIMIT] + f'\n[truncated: {len(txt)} chars total]'
    return {'status': code, 'headers': keep, 'body': b}, code

def chat(messages):
    data = json.dumps({'model': MODEL, 'messages': messages, 'tools': TOOLS}).encode()
    for attempt in range(40):
        req = urllib.request.Request(f'{API_BASE}/chat/completions', data=data,
                                     headers={'Authorization': f'Bearer {KEY}', 'Content-Type': 'application/json'})
        try:
            return json.loads(urllib.request.urlopen(req, timeout=180).read())
        except urllib.error.HTTPError as e:
            msg = e.read().decode()[:200]
            if e.code in (429, 500, 502, 503) or 'busy' in msg.lower():
                time.sleep(60); continue
            raise SystemExit(f'model API {e.code}: {msg}')
        except Exception:
            time.sleep(15)
    raise SystemExit('model API unavailable after retries')

messages = [{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': PROMPT}]
tin = tout = 0; final = ''; t0 = time.time(); stop = ''
for turn in range(MAX_TURNS):
    r = chat(messages); u = r.get('usage', {}); tin += u.get('prompt_tokens', 0); tout += u.get('completion_tokens', 0)
    msg = r['choices'][0]['message']; messages.append({k: v for k, v in msg.items() if k in ('role', 'content', 'tool_calls', 'reasoning_content') and v is not None})
    cost = (tin * PRICE_IN + tout * PRICE_OUT) / 1e6
    if cost > MAX_COST_USD: stop = 'cost_limit'; break
    if not msg.get('tool_calls'):
        final = msg.get('content') or ''; break
    for tc in msg['tool_calls']:
        try: a = json.loads(tc['function']['arguments'] or '{}')
        except Exception: a = {}
        res, code = http(a.get('method', 'GET'), a.get('url', ''), a.get('headers'), a.get('body'))
        calls.append({'method': a.get('method'), 'url': a.get('url'), 'status': code, 'batch_turn': turn})
        messages.append({'role': 'tool', 'tool_call_id': tc['id'], 'content': json.dumps(res, ensure_ascii=False)})
else:
    stop = 'max_turns'
failed = sum(1 for c in calls if not c['status'] or c['status'] >= 400 or c['status'] is None)
measured = {'calls_total': len(calls), 'calls_failed': failed, 'turns': turn + 1, 'tokens_in': tin, 'tokens_out': tout,
            'est_cost_usd': round((tin * PRICE_IN + tout * PRICE_OUT) / 1e6, 4), 'secs': round(time.time() - t0), 'stop': stop}
# replace self-reported counts with measured ones inside the closing block
import re
txt = final
m = re.search(r'\{\s*"bench_result"\s*:', txt)
if m:
    depth = 0
    for j in range(m.start(), len(txt)):
        depth += txt[j] == '{'; depth -= txt[j] == '}'
        if depth == 0:
            try:
                blk = json.loads(txt[m.start():j + 1]); inner = blk['bench_result']
                inner['calls_declared'] = inner.get('calls_total'); inner['calls_total'] = len(calls); inner['calls_failed'] = failed
                txt = txt[:m.start()] + json.dumps(blk, ensure_ascii=False) + txt[j + 1:]
            except Exception: pass
            break
open(OUT + '.txt', 'w').write(txt)
json.dump({'measured': measured, 'calls': calls, 'messages': messages}, open(OUT + '.json', 'w'), ensure_ascii=False, indent=1)
print(json.dumps(measured))
env = dict(os.environ, RESULTS_FILE=os.environ.get('RESULTS_FILE', 'results.csv'))
subprocess.run([sys.executable, os.path.join(HERE, 'score.py'), OUT + '.txt', '--note',
                f"agent=minimal-one-tool; model={MODEL}; turns={measured['turns']}; cost~{measured['est_cost_usd']}USD; stop={stop or 'final'}"], env=env)
