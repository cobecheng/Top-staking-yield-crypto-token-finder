# Top Staking Yield Crypto Token Finder

Research pipeline for building a top-1000 crypto token list, validating official project links, and discovering staking information from Staking Rewards, project websites, and official X accounts.

This is a tooling repository, not financial advice. Staking yields, market caps, and project links change quickly, so generate a fresh local dataset before using the results.

## What This Repo Contains

- Python scripts for CoinGecko market-cap collection, official-link validation, Staking Rewards baseline matching, unresolved queue building, and website discovery.
- Optional Node.js scripts for X account and X search discovery.
- Documentation for the generated result files.

Generated CSV datasets are intentionally not committed. This keeps the repository easy to browse and avoids turning it into a stale data dump.

## Setup

Python scripts use the standard library plus `requests`:

```bash
python3 -m pip install requests
```

X scanning is optional and uses `xactions`. Install it only when you want to run the X scanners locally:

```bash
npm run setup:x
```

## Workflow

Run the steps in order:

```bash
python3 fetch_top1000_marketcap_coingecko_canonical.py
python3 enrich_official_links_from_canonical_coingecko.py
python3 validate_official_links.py
python3 prefill_staking_from_stakingrewards.py
python3 build_unresolved_staking_queue.py
```

Then run website discovery against unresolved rows:

```bash
python3 discover_staking_from_websites.py \
  --input top_1000_staking_unresolved_queue.csv \
  --output top_1000_staking_website_unresolved.csv \
  --cache staking_website_discovery_cache_v2.json \
  --skip-baseline-file top_1000_staking_baseline.csv
```

Optional official-X discovery:

```bash
node scan_x_staking_signals_batch.mjs \
  --input top_1000_staking_unresolved_queue.csv \
  --output top_1000_x_staking_signals_unresolved_resumed.csv \
  --top 1000 \
  --limit 30 \
  --reply-scan-limit 8 \
  --delay-ms 2500 \
  --skip-baseline-file top_1000_staking_baseline.csv
```

Optional broader X search fallback:

```bash
node scan_x_search_staking_fallback.mjs \
  --input top_1000_staking_baseline.csv \
  --output top_1000_x_search_fallback.csv \
  --top 100
```

## Where To Find Results

See [`RESULT_FILES.md`](RESULT_FILES.md) for the generated filenames, what each file answers, and the expected columns.

The shortest version:

- Want the top-1000 token universe: `top_1000_marketcap_coingecko_canonical.csv`
- Want official websites and X accounts: `top_1000_official_links_validated.csv`
- Want first-pass staking matches and APY/APR: `top_1000_staking_baseline.csv`
- Want tokens still needing deeper discovery: `top_1000_staking_unresolved_queue.csv`
- Want website-discovered staking pages/yields: `top_1000_staking_website_unresolved.csv`
- Want official-X staking signals: `top_1000_x_staking_signals_unresolved_resumed.csv`
- Want broader X search fallback: `top_1000_x_search_fallback.csv`

## Public Data Notes

- The repository does not require API keys or credentials.
- Do not commit browser profiles, X session cookies, `.env` files, private keys, local caches, or generated CSV output.
- X output redacts post text by default while preserving source URLs, dates, keyword hits, and selected links. Pass `--include-post-text` only for private local analysis.
- Treat discovered yields as leads for manual verification. Do not rely on this repository for investment, tax, legal, or custody decisions.

## License

MIT License. See [`LICENSE`](LICENSE).
