#!/usr/bin/env python3

import csv
import json
import subprocess
import time
import urllib.parse
from pathlib import Path


BASE_URL = "https://api.coingecko.com/api/v3"
USER_AGENT = "Mozilla/5.0"
OUTPUT_PATH = Path("top_1000_marketcap_coingecko_canonical.csv")
TOP_N = 1000
PER_PAGE = 250
PAGES = 4


def fetch_json(url: str):
    output = subprocess.check_output(
        [
            "curl",
            "-sS",
            "-A",
            USER_AGENT,
            "-H",
            "Accept: application/json",
            url,
        ],
        text=True,
        timeout=60,
    )
    data = json.loads(output)
    if isinstance(data, dict) and data.get("status", {}).get("error_code") == 429:
        raise RuntimeError("CoinGecko rate limited the request")
    return data


def fetch_with_retry(url: str, retries: int = 8):
    delay = 3
    last_error = None
    for _ in range(retries):
        try:
            return fetch_json(url)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(delay)
            delay = min(delay * 2, 90)
    raise last_error


def markets_url(page: int):
    params = urllib.parse.urlencode(
        {
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": PER_PAGE,
            "page": page,
            "sparkline": "false",
            "price_change_percentage": "24h",
        }
    )
    return f"{BASE_URL}/coins/markets?{params}"


def main():
    rows = []
    for page in range(1, PAGES + 1):
        batch = fetch_with_retry(markets_url(page))
        if not isinstance(batch, list):
            raise RuntimeError(f"unexpected CoinGecko response type for page {page}")
        rows.extend(batch)
        time.sleep(4)

    rows = rows[:TOP_N]

    with OUTPUT_PATH.open("w", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(
            csvfile,
            fieldnames=[
                "rank",
                "name",
                "symbol",
                "coingecko_id",
                "market_cap_usd",
                "current_price_usd",
                "fully_diluted_valuation_usd",
                "circulating_supply",
                "total_supply",
                "max_supply",
                "last_updated",
            ],
        )
        writer.writeheader()
        for coin in rows:
            writer.writerow(
                {
                    "rank": coin.get("market_cap_rank", ""),
                    "name": coin.get("name", ""),
                    "symbol": (coin.get("symbol") or "").upper(),
                    "coingecko_id": coin.get("id", ""),
                    "market_cap_usd": coin.get("market_cap", ""),
                    "current_price_usd": coin.get("current_price", ""),
                    "fully_diluted_valuation_usd": coin.get("fully_diluted_valuation", ""),
                    "circulating_supply": coin.get("circulating_supply", ""),
                    "total_supply": coin.get("total_supply", ""),
                    "max_supply": coin.get("max_supply", ""),
                    "last_updated": coin.get("last_updated", ""),
                }
            )

    print(f"wrote {len(rows)} rows to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
