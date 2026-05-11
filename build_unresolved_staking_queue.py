#!/usr/bin/env python3

import csv
from pathlib import Path


BASELINE_PATH = Path("top_1000_staking_baseline.csv")
OFFICIAL_LINKS_PATH = Path("top_1000_official_links_validated.csv")
OUTPUT_PATH = Path("top_1000_staking_unresolved_queue.csv")


def read_rows(path: Path):
    with path.open(newline="", encoding="utf-8") as csvfile:
        return list(csv.DictReader(csvfile))


def main():
    baseline_rows = read_rows(BASELINE_PATH)
    official_rows = read_rows(OFFICIAL_LINKS_PATH)
    official_by_id = {row["coingecko_id"]: row for row in official_rows}

    unresolved = []
    for row in baseline_rows:
        if row.get("baseline_yield_pct", "") not in ("", None):
            continue
        official = official_by_id.get(row["coingecko_id"], {})
        unresolved.append(
            {
                **row,
                "website_final_url": official.get("website_final_url", ""),
                "website_http_code": official.get("website_http_code", ""),
                "selected_official_x": official.get("selected_official_x", ""),
                "validation_status": official.get("validation_status", ""),
            }
        )

    fieldnames = [
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
        "website_final_url",
        "website_http_code",
        "selected_official_x",
        "validation_status",
    ]

    with OUTPUT_PATH.open("w", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(unresolved)

    print(f"wrote {len(unresolved)} rows to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
