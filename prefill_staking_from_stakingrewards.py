#!/usr/bin/env python3

import csv
import html
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


INPUT_PATH = Path("top_1000_marketcap_coingecko_canonical.csv")
OUTPUT_PATH = Path("top_1000_staking_baseline.csv")
SUMMARY_PATH = Path("top_1000_staking_baseline_stakingrewards_only.csv")
STAKING_REWARDS_SITEMAP_URL = "https://www.stakingrewards.com/sitemap.xml"
USER_AGENT = "Mozilla/5.0"
REQUEST_GAP_SECONDS = 0.15

LOC_RE = re.compile(r"<loc>(https://www\.stakingrewards\.com/asset/[^<]+)</loc>")
TITLE_RE = re.compile(r"<title>(.*?)</title>", re.S | re.I)
DESC_RE = re.compile(r'<meta[^>]+name="description"[^>]+content="([^"]+)"', re.I)
TITLE_NAME_SYMBOL_RE = re.compile(r"^(.*?) \(([A-Z0-9.\-]+)\) Staking\b", re.I)
DESC_RATE_RE = re.compile(r"earn(?: up to)? ([0-9]+(?:\.[0-9]+)?)% (APY|APR)", re.I)


@dataclass
class SrAsset:
    slug: str
    name: str
    symbol: str
    asset_url: str
    best_link: str
    best_rate: float | None
    best_rate_type: str
    match_key_slug: str
    match_key_name: str


def fetch_text(url: str) -> str:
    return subprocess.check_output(
        [
            "curl",
            "-L",
            "-sS",
            "-A",
            USER_AGENT,
            url,
        ],
        text=True,
        timeout=120,
    )


def normalize_key(text: str) -> str:
    text = (text or "").strip().lower()
    text = text.replace("&", "and")
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text.strip("-")


def asset_urls_from_sitemap(xml_text: str) -> dict[str, str]:
    slug_to_url: dict[str, str] = {}
    for url in LOC_RE.findall(xml_text):
        path = url.replace("https://www.stakingrewards.com", "")
        parts = [part for part in path.split("/") if part]
        if len(parts) != 2:
            continue
        if parts[0] != "asset":
            continue
        slug_to_url[parts[1]] = url
    return slug_to_url


def parse_asset_page(url: str, page_html: str) -> SrAsset | None:
    title_match = TITLE_RE.search(page_html)
    desc_match = DESC_RE.search(page_html)
    if not title_match:
        return None

    title = html.unescape(title_match.group(1)).strip()
    description = html.unescape(desc_match.group(1)).strip() if desc_match else ""
    title_info = TITLE_NAME_SYMBOL_RE.search(title)
    if not title_info:
        return None

    name = title_info.group(1).strip()
    symbol = title_info.group(2).strip().upper()
    rate = None
    rate_type = ""
    rate_match = DESC_RATE_RE.search(description)
    if rate_match:
        rate = float(rate_match.group(1))
        rate_type = rate_match.group(2).upper()

    slug = url.rstrip("/").split("/")[-1]
    return SrAsset(
        slug=slug,
        name=name,
        symbol=symbol,
        asset_url=url,
        best_link=url,
        best_rate=rate,
        best_rate_type=rate_type,
        match_key_slug=normalize_key(slug),
        match_key_name=normalize_key(name),
    )


def find_candidate_slugs(rows: list[dict[str, str]], sitemap_slug_to_url: dict[str, str]) -> set[str]:
    all_slugs = list(sitemap_slug_to_url.keys())
    candidates: set[str] = set()
    alias_map = {
        "ethereum": ["ethereum-2-0"],
        "binancecoin": ["bnb"],
        "matic-network": ["matic-network"],
        "bitcoin-cash": ["bitcoin-cash"],
        "the-open-network": ["toncoin"],
    }

    for row in rows:
        keys = {
            normalize_key(row.get("coingecko_id", "")),
            normalize_key(row.get("name", "")),
        }
        keys.discard("")
        for key in list(keys):
            for alias in alias_map.get(key, []):
                if alias in sitemap_slug_to_url:
                    candidates.add(alias)
        for key in keys:
            if key in sitemap_slug_to_url:
                candidates.add(key)
                continue
            prefixed = [slug for slug in all_slugs if slug.startswith(f"{key}-")]
            if len(prefixed) == 1:
                candidates.add(prefixed[0])
    return candidates


def load_sr_assets(rows: list[dict[str, str]]) -> list[SrAsset]:
    sitemap = fetch_text(STAKING_REWARDS_SITEMAP_URL)
    sitemap_slug_to_url = asset_urls_from_sitemap(sitemap)
    candidate_slugs = sorted(find_candidate_slugs(rows, sitemap_slug_to_url))
    assets = []
    for slug in candidate_slugs:
        url = sitemap_slug_to_url[slug]
        try:
            page_html = fetch_text(url)
            asset = parse_asset_page(url, page_html)
            if asset:
                assets.append(asset)
        except Exception:  # noqa: BLE001
            pass
        time.sleep(REQUEST_GAP_SECONDS)
    return assets


def build_indices(assets: list[SrAsset]):
    by_symbol: dict[str, list[SrAsset]] = {}
    by_name: dict[str, list[SrAsset]] = {}
    by_slug: dict[str, list[SrAsset]] = {}
    for asset in assets:
        by_symbol.setdefault(asset.symbol, []).append(asset)
        by_name.setdefault(asset.match_key_name, []).append(asset)
        by_slug.setdefault(asset.match_key_slug, []).append(asset)
    return by_symbol, by_name, by_slug


def pick_asset(row: dict[str, str], by_symbol, by_name, by_slug):
    symbol = (row.get("symbol") or "").upper()
    name_key = normalize_key(row.get("name", ""))
    id_key = normalize_key(row.get("coingecko_id", ""))

    symbol_matches = by_symbol.get(symbol, [])
    if len(symbol_matches) == 1:
        return symbol_matches[0], "symbol_exact"

    name_matches = by_name.get(name_key, [])
    if len(name_matches) == 1:
        return name_matches[0], "name_exact"

    slug_matches = by_slug.get(id_key, [])
    if len(slug_matches) == 1:
        return slug_matches[0], "coingecko_id_slug"

    narrowed = [asset for asset in symbol_matches if asset.match_key_name == name_key or asset.match_key_slug == id_key]
    if len(narrowed) == 1:
        return narrowed[0], "symbol_plus_name"

    return None, ""


def main():
    with INPUT_PATH.open(newline="", encoding="utf-8") as csvfile:
        rows = list(csv.DictReader(csvfile))

    assets = load_sr_assets(rows)
    by_symbol, by_name, by_slug = build_indices(assets)

    merged_rows = []
    sr_only_rows = []
    for row in rows:
        asset, method = pick_asset(row, by_symbol, by_name, by_slug)
        merged = {
            **row,
            "baseline_source": "Staking Rewards" if asset else "",
            "baseline_asset_url": asset.asset_url if asset else "",
            "baseline_staking_link": asset.best_link if asset else "",
            "baseline_yield_pct": asset.best_rate if asset and asset.best_rate is not None else "",
            "baseline_yield_type": asset.best_rate_type if asset else "",
            "baseline_match_method": method,
            "needs_website_scan": "0" if asset and asset.best_rate is not None else "1",
            "needs_x_scan": "0" if asset and asset.best_rate is not None else "1",
        }
        merged_rows.append(merged)
        if asset:
            sr_only_rows.append(
                {
                    "rank": row["rank"],
                    "name": row["name"],
                    "symbol": row["symbol"],
                    "coingecko_id": row["coingecko_id"],
                    "baseline_asset_url": asset.asset_url,
                    "baseline_staking_link": asset.best_link,
                    "baseline_yield_pct": asset.best_rate if asset.best_rate is not None else "",
                    "baseline_yield_type": asset.best_rate_type if asset else "",
                    "baseline_match_method": method,
                }
            )

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
                "baseline_source",
                "baseline_asset_url",
                "baseline_staking_link",
                "baseline_yield_pct",
                "baseline_yield_type",
                "baseline_match_method",
                "needs_website_scan",
                "needs_x_scan",
            ],
        )
        writer.writeheader()
        writer.writerows(merged_rows)

    with SUMMARY_PATH.open("w", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(
            csvfile,
            fieldnames=[
                "rank",
                "name",
                "symbol",
                "coingecko_id",
                "baseline_asset_url",
                "baseline_staking_link",
                "baseline_yield_pct",
                "baseline_yield_type",
                "baseline_match_method",
            ],
        )
        writer.writeheader()
        writer.writerows(sr_only_rows)

    covered = sum(1 for row in merged_rows if row["baseline_source"])
    with_rate = sum(1 for row in merged_rows if row["baseline_yield_pct"] not in ("", None))
    print(f"parsed {len(assets)} staking rewards assets")
    print(f"matched {covered} canonical tokens")
    print(f"matched {with_rate} canonical tokens with yield")
    print(f"wrote {OUTPUT_PATH}")
    print(f"wrote {SUMMARY_PATH}")


if __name__ == "__main__":
    main()
