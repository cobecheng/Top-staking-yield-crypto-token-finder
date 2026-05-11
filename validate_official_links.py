#!/usr/bin/env python3

import csv
import html
import json
import re
import subprocess
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urljoin, urlparse


INPUT_PATH = Path("top_1000_official_links_from_coingecko.csv")
OUTPUT_PATH = Path("top_1000_official_links_validated.csv")
CACHE_PATH = Path("official_link_validation_cache.json")
USER_AGENT = "Mozilla/5.0"
REQUEST_GAP_SECONDS = 1.2

X_DOMAINS = {"x.com", "www.x.com", "twitter.com", "www.twitter.com", "mobile.twitter.com"}


class LinkExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.meta = {}

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if tag == "a" and attrs_dict.get("href"):
            self.links.append(attrs_dict["href"])
        if tag == "meta":
            key = attrs_dict.get("property") or attrs_dict.get("name")
            content = attrs_dict.get("content")
            if key and content:
                self.meta[key.lower()] = content


def load_cache():
    if not CACHE_PATH.exists():
        return {}
    return json.loads(CACHE_PATH.read_text(encoding="utf-8"))


def save_cache(cache):
    CACHE_PATH.write_text(
        json.dumps(cache, ensure_ascii=True, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def fetch_url(url: str):
    try:
        result = subprocess.run(
            [
                "curl",
                "-L",
                "-sS",
                "-A",
                USER_AGENT,
                "-H",
                "Accept: text/html,application/xhtml+xml,application/json",
                "-w",
                "\n__CURL_FINAL_URL__:%{url_effective}\n__CURL_HTTP_CODE__:%{http_code}\n__CURL_CONTENT_TYPE__:%{content_type}\n",
                url,
            ],
            check=True,
            capture_output=True,
            timeout=60,
        )
        text = result.stdout.decode("utf-8", errors="replace")
    except subprocess.CalledProcessError as exc:
        text = exc.stdout.decode("utf-8", errors="replace") if exc.stdout else ""
        return {
            "body": text,
            "final_url": url,
            "http_code": "",
            "content_type": "",
            "fetch_error": f"curl_exit_{exc.returncode}",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "body": "",
            "final_url": url,
            "http_code": "",
            "content_type": "",
            "fetch_error": str(exc),
        }
    final_url_match = re.search(r"\n__CURL_FINAL_URL__:(.*)\n", text)
    code_match = re.search(r"__CURL_HTTP_CODE__:(.*)\n", text)
    type_match = re.search(r"__CURL_CONTENT_TYPE__:(.*)\n", text)
    body = text.split("\n__CURL_FINAL_URL__:", 1)[0]
    return {
        "body": body,
        "final_url": final_url_match.group(1).strip() if final_url_match else url,
        "http_code": code_match.group(1).strip() if code_match else "",
        "content_type": type_match.group(1).strip() if type_match else "",
        "fetch_error": "",
    }


def normalize_url(url: str):
    url = (url or "").strip()
    if not url or url == "N/A":
        return ""
    if url.startswith("//"):
        url = "https:" + url
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url.lstrip("/")
    try:
        parsed = urlparse(url)
    except ValueError:
        return ""
    netloc = parsed.netloc.lower()
    path = parsed.path or ""
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    clean = parsed._replace(
        scheme=parsed.scheme.lower(),
        netloc=netloc,
        path=path or "/",
        query="",
        fragment="",
    )
    return clean.geturl()


def root_domain(url: str):
    if not url:
        return ""
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def normalize_x_url(url: str):
    url = normalize_url(url)
    if not url:
        return ""
    parsed = urlparse(url)
    if parsed.netloc.lower() not in X_DOMAINS:
        return ""
    path = parsed.path.strip("/")
    if not path:
        return ""
    first = path.split("/", 1)[0]
    if first.lower() in {
        "home",
        "share",
        "intent",
        "search",
        "hashtag",
        "i",
        "explore",
        "messages",
        "settings",
        "privacy",
        "tos",
    }:
        return ""
    return f"https://x.com/{first}"


def clean_social_href(href: str, base_url: str):
    href = html.unescape((href or "").strip())
    if not href:
        return ""
    if href.startswith(("mailto:", "tel:", "javascript:", "#")):
        return ""
    if href.startswith("/"):
        return normalize_url(urljoin(base_url, href))
    if href.startswith("?"):
        return normalize_url(urljoin(base_url, href))
    if href.startswith("//"):
        return normalize_url("https:" + href)
    return normalize_url(href)


def extract_website_and_x_candidates(fetch_result):
    parser = LinkExtractor()
    parser.feed(fetch_result["body"])
    base_url = fetch_result["final_url"]
    x_candidates = []
    website_candidates = []

    for raw_href in parser.links:
        href = clean_social_href(raw_href, base_url)
        if not href:
            continue
        parsed = urlparse(href)
        host = parsed.netloc.lower()
        if host in X_DOMAINS:
            x_url = normalize_x_url(href)
            if x_url:
                x_candidates.append(x_url)
        else:
            website_candidates.append(href)

    for meta_key in ["og:url", "twitter:url"]:
        meta_url = normalize_url(parser.meta.get(meta_key, ""))
        if meta_url:
            website_candidates.append(meta_url)

    dedup_x = []
    seen_x = set()
    for item in x_candidates:
        if item not in seen_x:
            dedup_x.append(item)
            seen_x.add(item)

    dedup_sites = []
    seen_sites = set()
    for item in website_candidates:
        if item not in seen_sites:
            dedup_sites.append(item)
            seen_sites.add(item)

    return dedup_sites, dedup_x


def score_x_candidate(candidate: str, token_name: str, symbol: str, website_domain: str):
    score = 0
    handle = candidate.rstrip("/").rsplit("/", 1)[-1].lower()
    symbol_l = symbol.lower()
    name_l = re.sub(r"[^a-z0-9]+", "", token_name.lower())
    if symbol_l and symbol_l in handle:
        score += 2
    if name_l and name_l in re.sub(r"[^a-z0-9]+", "", handle):
        score += 3
    if website_domain:
        domain_stub = website_domain.split(".")[0]
        if domain_stub and domain_stub in handle:
            score += 4
    return score


def choose_best_x(row, website_final_url, x_from_website):
    website_domain = root_domain(website_final_url)
    website_best = ("none", "N/A", -1)
    seen_website = set()
    for candidate in x_from_website:
        if candidate in seen_website:
            continue
        seen_website.add(candidate)
        score = score_x_candidate(candidate, row["name"], row["symbol"], website_domain)
        if score > website_best[2]:
            website_best = ("website", candidate, score)

    if website_best[1] != "N/A":
        return {
            "selected_official_x": website_best[1],
            "selected_official_x_source": "website",
            "selected_official_x_score": website_best[2],
        }

    coingecko_x = normalize_x_url(row.get("official_x", ""))
    if coingecko_x:
        score = score_x_candidate(coingecko_x, row["name"], row["symbol"], website_domain)
        return {
            "selected_official_x": coingecko_x,
            "selected_official_x_source": "coingecko",
            "selected_official_x_score": score,
        }

    return {
        "selected_official_x": "N/A",
        "selected_official_x_source": "none",
        "selected_official_x_score": "",
    }


def validate_row(row, cache):
    website_seed = normalize_url(row.get("official_project_website", ""))
    cache_key = row["coingecko_id"]
    if cache_key in cache:
        return cache[cache_key]

    result = {
        "rank": row["rank"],
        "name": row["name"],
        "symbol": row["symbol"],
        "coingecko_id": row["coingecko_id"],
        "market_cap_usd": row["market_cap_usd"],
        "coingecko_website": row.get("official_project_website", "N/A"),
        "coingecko_x": row.get("official_x", "N/A"),
        "website_final_url": "N/A",
        "website_final_domain": "N/A",
        "website_http_code": "N/A",
        "website_content_type": "N/A",
        "x_found_on_website": "N/A",
        "selected_official_x": "N/A",
        "selected_official_x_source": "none",
        "selected_official_x_score": "",
        "validation_status": "unresolved",
        "validation_notes": "",
    }

    if not website_seed:
        result["validation_notes"] = "No CoinGecko website seed."
        cache[cache_key] = result
        return result

    fetch_result = fetch_url(website_seed)
    website_final_url = normalize_url(fetch_result["final_url"])
    result["website_final_url"] = website_final_url or "N/A"
    result["website_final_domain"] = root_domain(website_final_url) or "N/A"
    result["website_http_code"] = fetch_result["http_code"] or "N/A"
    result["website_content_type"] = fetch_result["content_type"] or "N/A"

    _, x_from_website = extract_website_and_x_candidates(fetch_result)
    result["x_found_on_website"] = " | ".join(x_from_website[:5]) if x_from_website else "N/A"

    x_choice = choose_best_x(row, website_final_url, x_from_website)
    result.update(x_choice)

    if result["website_http_code"].startswith("2") and result["selected_official_x"] != "N/A":
        result["validation_status"] = "website_and_x_confirmed"
        result["validation_notes"] = "Website reachable and X candidate confirmed from website/CoinGecko."
    elif result["website_http_code"].startswith("2"):
        result["validation_status"] = "website_confirmed_x_missing"
        result["validation_notes"] = "Website reachable but no strong X candidate."
    else:
        result["validation_status"] = "website_unreachable"
        if fetch_result.get("fetch_error"):
            result["validation_notes"] = f"Website fetch failed: {fetch_result['fetch_error']}"
        else:
            result["validation_notes"] = "Website seed did not resolve to a 2xx page."

    cache[cache_key] = result
    return result


def main():
    with INPUT_PATH.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    cache = load_cache()
    output_rows = []

    for idx, row in enumerate(rows, start=1):
        was_cached = row["coingecko_id"] in cache
        output_rows.append(validate_row(row, cache))
        if idx % 25 == 0:
            save_cache(cache)
            with OUTPUT_PATH.open("w", newline="", encoding="utf-8") as csvfile:
                writer = csv.DictWriter(
                    csvfile,
                    fieldnames=[
                        "rank",
                        "name",
                        "symbol",
                        "coingecko_id",
                        "market_cap_usd",
                        "coingecko_website",
                        "coingecko_x",
                        "website_final_url",
                        "website_final_domain",
                        "website_http_code",
                        "website_content_type",
                        "x_found_on_website",
                        "selected_official_x",
                        "selected_official_x_source",
                        "selected_official_x_score",
                        "validation_status",
                        "validation_notes",
                    ],
                )
                writer.writeheader()
                writer.writerows(output_rows)
        if not was_cached:
            time.sleep(REQUEST_GAP_SECONDS)

    save_cache(cache)
    with OUTPUT_PATH.open("w", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(
            csvfile,
            fieldnames=[
                "rank",
                "name",
                "symbol",
                "coingecko_id",
                "market_cap_usd",
                "coingecko_website",
                "coingecko_x",
                "website_final_url",
                "website_final_domain",
                "website_http_code",
                "website_content_type",
                "x_found_on_website",
                "selected_official_x",
                "selected_official_x_source",
                "selected_official_x_score",
                "validation_status",
                "validation_notes",
            ],
        )
        writer.writeheader()
        writer.writerows(output_rows)

    print(f"wrote {len(output_rows)} rows to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
