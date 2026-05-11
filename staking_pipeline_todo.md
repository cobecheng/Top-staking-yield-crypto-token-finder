# Staking Pipeline TODO

1. Prefill staking info from `Staking Rewards` for the canonical top-1000 list.
2. Add additional third-party staking sources after `Staking Rewards`.
   Current candidates:
   - Kraken staking directory
     Status: viable, public page exposes staking data in page payload.
   - Coinbase Earn / staking pages
     Status: lower priority, direct fetches are Cloudflare-gated in this environment.
3. Reorder the workflow:
   - third-party baseline first
   - official website crawl second
   - official X profile crawl third
   - broader X search fallback last
4. Remove `validator` / `validators` from the recent-post staking detector.
5. Add broader X search fallback for unresolved projects.
   Query shape:
   - `<ticker> staking`
   - `<project name> staking`
   - top or popular posts first
6. Skip website and X scans for projects that already have staking info from a third-party baseline source.
7. Expand shortlinks like `t.co` when social posts contain candidate staking links.
8. Add more reliable yield extraction for JavaScript-rendered staking pages.
