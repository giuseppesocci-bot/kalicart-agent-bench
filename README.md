# kalicart-agent-bench

What does it cost a shopping agent to reach a delivered total that the store itself confirms?

This repository holds the harness, the pre-registered protocols and the anonymized run data behind
[the write-up](https://bridge.kalicart.com/blog/same-total-fewer-tokens-agent-benchmark/). It compares two paths on the same WooCommerce stores, with the same agent and the same task:

- **K**: [KaliCart Bridge](https://wordpress.org/plugins/kalicart-bridge/), a free WooCommerce plugin with an agent-oriented catalog and a read-only delivered-total quote.
- **S**: the public WooCommerce Store API (`/wp-json/wc/store/v1/`), no credentials.

## Results in one table

| Series | Tasks | Verified K / S | Calls, median K / S | Tokens, median K / S |
| --- | --- | --- | --- | --- |
| M5 | simple products, 3 stores | 14/15 vs 12/15 | 8 vs 10 (20% fewer) | 107k vs 373k (71% fewer, descriptive) |
| M6 | variants and shipping, 2 stores | 20/20 vs 20/20 | 10.5 vs 14 (25% fewer) | 281k vs 533k (47% fewer, descriptive) |
| M7 | quantity limits, missing size, shipping threshold, two stores | 20/20 vs 17/20 | 10 vs 14 (29% fewer) | 215k vs 698k (69% fewer, registered) |

Thresholds, deviations and verdicts are in [`protocols/`](protocols). Some thresholds were not met, and the protocols say so.

## The harness

`harness/agent.py` is a deliberately minimal agent: one chat model (any OpenAI-compatible API) and one tool, `http_request`.
There is no cookie jar; the harness counts calls and tokens itself. `harness/score.py` rebuilds the agent's basket, asks the
store for its delivered total (through the KaliCart quote, read-only: no order, no writes), checks the task's constraints
against the live catalog and classifies the run as verified, deduced or failed.

```bash
cd harness
cp tasks.example.json tasks.json          # describe your own tasks and stores
export BENCH_API_KEY=...                  # model API key
export BENCH_API_BASE=https://api.deepseek.com BENCH_MODEL=deepseek-flash   # or any OpenAI-compatible endpoint
export BENCH_I_OWN_THESE_STORES=yes       # see below
./run_series.sh "C1:K:1 C1:S:1"
python3 score.py --summary
```

K needs KaliCart Bridge active on the store, with the cart quote enabled. The prompts in `harness/prompts/` are in Italian,
exactly as used in the published runs.

## Be kind to stores

Run it only against stores you own or have the owner's permission to test. The harness refuses to start without
`BENCH_I_OWN_THESE_STORES=yes`, and it slows itself down:

- at least 1 s between two requests to the same store, and no request to a store until its `Retry-After` has passed after a 429;
- at least 60 s between runs (120 s by default) and at most 10 runs per store per day;
- a User-Agent that names this repository, so the store owner can recognise the traffic.

## Data

`data/M5`, `data/M6`, `data/M7`: one record per run with outcome, calls (method, generalized path, status, turn), tokens,
turns and time. Stores are labelled store-a to store-d; product IDs, categories, search words, prices and response bodies are removed.

## License

MIT.
