#!/usr/bin/env python3

import csv
import json
import subprocess
import time
import urllib.parse
from pathlib import Path


BASE_URL = "https://api.coingecko.com/api/v3"
USER_AGENT = "Mozilla/5.0"
INPUT_PATH = Path("top_1000_marketcap_coingecko_canonical.csv")
OUTPUT_PATH = Path("top_1000_official_links_from_coingecko.csv")
CACHE_PATH = Path("coingecko_official_links_cache.json")
REQUEST_GAP_SECONDS = 3.0


def load_cache():
    if not CACHE_PATH.exists():
        return {}
    return json.loads(CACHE_PATH.read_text(encoding="utf-8"))


def save_cache(cache):
    CACHE_PATH.write_text(
        json.dumps(cache, ensure_ascii=True, indent=2, sort_keys=True),
        encoding="utf-8",
    )


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
    delay = REQUEST_GAP_SECONDS
    last_error = None
    for _ in range(retries):
        try:
            return fetch_json(url)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(delay)
            delay = min(delay * 2, 120)
    raise last_error


def detail_url(coin_id: str):
    params = urllib.parse.urlencode(
        {
            "localization": "false",
            "tickers": "false",
            "market_data": "false",
            "community_data": "false",
            "developer_data": "false",
            "sparkline": "false",
        }
    )
    return f"{BASE_URL}/coins/{coin_id}?{params}"


def first_nonempty(items):
    for item in items or []:
        if item:
            return item
    return ""


def normalize_x_url(handle: str):
    handle = (handle or "").strip().lstrip("@")
    if not handle:
        return ""
    return f"https://x.com/{handle}"


def extract_row(base_row, detail):
    links = detail.get("links", {})
    repos = links.get("repos_url", {}) if isinstance(links, dict) else {}
    homepage = first_nonempty(links.get("homepage", []))
    twitter_handle = (links.get("twitter_screen_name") or "").strip()
    github_url = first_nonempty(repos.get("github", [])) if isinstance(repos, dict) else ""
    telegram_url = first_nonempty(links.get("telegram_channel_identifier", []))
    if telegram_url and not telegram_url.startswith("http"):
        telegram_url = f"https://t.me/{telegram_url.lstrip('@')}"
    return {
        "rank": base_row["rank"],
        "name": base_row["name"],
        "symbol": base_row["symbol"],
        "coingecko_id": base_row["coingecko_id"],
        "market_cap_usd": base_row["market_cap_usd"],
        "official_project_website": homepage or "N/A",
        "official_x": normalize_x_url(twitter_handle) or "N/A",
        "twitter_handle": twitter_handle or "N/A",
        "official_github": github_url or "N/A",
        "official_telegram": telegram_url or "N/A",
        "coingecko_web_slug": detail.get("web_slug", "") or "N/A",
        "coingecko_asset_platform_id": detail.get("asset_platform_id", "") or "N/A",
        "link_source": "coingecko_coin_detail",
    }


def main():
    with INPUT_PATH.open(encoding="utf-8") as f:
        base_rows = list(csv.DictReader(f))

    cache = load_cache()
    output_rows = []

    for index, base_row in enumerate(base_rows, start=1):
        coin_id = base_row["coingecko_id"]
        detail = cache.get(coin_id)
        if detail is None:
            detail = fetch_with_retry(detail_url(coin_id))
            cache[coin_id] = detail
            save_cache(cache)
            time.sleep(REQUEST_GAP_SECONDS)

        output_rows.append(extract_row(base_row, detail))

        if index % 50 == 0:
            with OUTPUT_PATH.open("w", newline="", encoding="utf-8") as csvfile:
                writer = csv.DictWriter(
                    csvfile,
                    fieldnames=[
                        "rank",
                        "name",
                        "symbol",
                        "coingecko_id",
                        "market_cap_usd",
                        "official_project_website",
                        "official_x",
                        "twitter_handle",
                        "official_github",
                        "official_telegram",
                        "coingecko_web_slug",
                        "coingecko_asset_platform_id",
                        "link_source",
                    ],
                )
                writer.writeheader()
                writer.writerows(output_rows)

    with OUTPUT_PATH.open("w", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(
            csvfile,
            fieldnames=[
                "rank",
                "name",
                "symbol",
                "coingecko_id",
                "market_cap_usd",
                "official_project_website",
                "official_x",
                "twitter_handle",
                "official_github",
                "official_telegram",
                "coingecko_web_slug",
                "coingecko_asset_platform_id",
                "link_source",
            ],
        )
        writer.writeheader()
        writer.writerows(output_rows)

    print(f"wrote {len(output_rows)} rows to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
