#!/usr/bin/env python3

import csv
import html
import json
import sys
import re
import subprocess
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse


INPUT_PATH = Path("top_1000_official_links_validated.csv")
OUTPUT_PATH = Path("top_1000_staking_website_discovery_v2.csv")
CACHE_PATH = Path("staking_website_discovery_cache_v2.json")
SKIP_BASELINE_PATH = Path("top_1000_staking_baseline.csv")
USER_AGENT = "Mozilla/5.0"
REQUEST_GAP_SECONDS = 0.6
MAX_CANDIDATE_PAGES = 3
MAX_GATEWAY_PAGES = 2

STAKE_KEYWORDS = [
    "stake",
    "staking",
    "staked",
    "delegate",
    "delegation",
    "validator",
    "validators",
    "earn",
    "rewards",
    "reward",
    "apy",
    "apr",
    "yield",
]

APP_GATEWAY_KEYWORDS = [
    "launch app",
    "app",
    "webapp",
    "open app",
    "launch",
    "trade",
    "predict",
    "portfolio",
    "bridge",
]

DIRECT_ROUTE_GUESSES = [
    "/staking",
    "/stake",
    "/earn",
    "/rewards",
    "/validators",
    "/validator",
]

YIELD_RE = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*%\s*(APY|APR)", re.I)
RATE_RE = re.compile(r"(APY|APR)\s*[: ]\s*([0-9]+(?:\.[0-9]+)?)\s*%", re.I)
WORD_RE = re.compile(r"\b(" + "|".join(re.escape(k) for k in STAKE_KEYWORDS) + r")\b", re.I)


class AnchorParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.anchors = []
        self._current_href = None
        self._current_text = []
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if tag == "a":
            self._current_href = attrs_dict.get("href")
            self._current_text = []
        elif tag == "title":
            self._in_title = True

    def handle_data(self, data):
        if self._current_href is not None:
            self._current_text.append(data)
        if self._in_title:
            self.title += data

    def handle_endtag(self, tag):
        if tag == "a" and self._current_href is not None:
            self.anchors.append(
                {
                    "href": self._current_href,
                    "text": " ".join("".join(self._current_text).split()),
                }
            )
            self._current_href = None
            self._current_text = []
        elif tag == "title":
            self._in_title = False


@dataclass
class FetchResult:
    body: str
    final_url: str
    http_code: str
    content_type: str
    fetch_error: str


def load_cache():
    if not CACHE_PATH.exists():
        return {}
    return json.loads(CACHE_PATH.read_text(encoding="utf-8"))


def save_cache(cache):
    CACHE_PATH.write_text(
        json.dumps(cache, ensure_ascii=True, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def parse_args():
    args = {
        "input_path": INPUT_PATH,
        "output_path": OUTPUT_PATH,
        "cache_path": CACHE_PATH,
        "skip_baseline_path": SKIP_BASELINE_PATH,
    }
    argv = sys.argv[1:]
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--input" and i + 1 < len(argv):
            args["input_path"] = Path(argv[i + 1])
            i += 2
            continue
        if arg == "--output" and i + 1 < len(argv):
            args["output_path"] = Path(argv[i + 1])
            i += 2
            continue
        if arg == "--cache" and i + 1 < len(argv):
            args["cache_path"] = Path(argv[i + 1])
            i += 2
            continue
        if arg == "--skip-baseline-file" and i + 1 < len(argv):
            args["skip_baseline_path"] = Path(argv[i + 1])
            i += 2
            continue
        i += 1
    return args


def load_csv_rows(path: Path):
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_baseline_map(path: Path):
    if not path.exists():
        return {}
    rows = load_csv_rows(path)
    return {row["coingecko_id"]: row for row in rows}


def skipped_baseline_result(row, baseline_row):
    return {
        "rank": row["rank"],
        "name": row["name"],
        "symbol": row["symbol"],
        "coingecko_id": row["coingecko_id"],
        "website_final_url": row["website_final_url"],
        "selected_official_x": row["selected_official_x"],
        "staking_status": "skipped_has_baseline",
        "home_page_keywords": "N/A",
        "candidate_staking_pages": baseline_row.get("baseline_asset_url", "N/A") or "N/A",
        "selected_staking_page": baseline_row.get("baseline_staking_link", "") or baseline_row.get("baseline_asset_url", "N/A") or "N/A",
        "selected_page_title": "N/A",
        "selected_page_http_code": "N/A",
        "yield_pct": baseline_row.get("baseline_yield_pct", ""),
        "yield_type": baseline_row.get("baseline_yield_type", "N/A") or "N/A",
        "yield_snippet": "N/A",
        "discovery_source": "baseline",
        "discovery_notes": "Skipped website crawl because baseline yield was already available.",
    }


def fetch_url(url: str) -> FetchResult:
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
        return FetchResult(text, url, "", "", f"curl_exit_{exc.returncode}")
    except Exception as exc:  # noqa: BLE001
        return FetchResult("", url, "", "", str(exc))

    final_url_match = re.search(r"\n__CURL_FINAL_URL__:(.*)\n", text)
    code_match = re.search(r"__CURL_HTTP_CODE__:(.*)\n", text)
    type_match = re.search(r"__CURL_CONTENT_TYPE__:(.*)\n", text)
    body = text.split("\n__CURL_FINAL_URL__:", 1)[0]
    return FetchResult(
        body=body,
        final_url=(final_url_match.group(1).strip() if final_url_match else url),
        http_code=(code_match.group(1).strip() if code_match else ""),
        content_type=(type_match.group(1).strip() if type_match else ""),
        fetch_error="",
    )


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
    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    clean = parsed._replace(
        scheme=parsed.scheme.lower(),
        netloc=parsed.netloc.lower(),
        path=path,
        params="",
        query="",
        fragment="",
    )
    return clean.geturl()


def root_domain(url: str):
    if not url:
        return ""
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def same_project_domain(url_a: str, url_b: str):
    a = root_domain(url_a)
    b = root_domain(url_b)
    if not a or not b:
        return False
    return a == b or a.endswith("." + b) or b.endswith("." + a)


def parse_page(fetch_result: FetchResult):
    parser = AnchorParser()
    parser.feed(fetch_result.body)
    return parser


def score_anchor(anchor_text: str, href: str):
    hay = f"{anchor_text} {href}".lower()
    score = 0
    for keyword in STAKE_KEYWORDS:
        if keyword in hay:
            score += 2
    if any(k in href.lower() for k in ["stake", "staking", "validator", "delegate", "earn"]):
        score += 4
    if "/blog" in href.lower() or "/news" in href.lower():
        score -= 2
    return score


def score_gateway_anchor(anchor_text: str, href: str):
    hay = f"{anchor_text} {href}".lower()
    score = 0
    for keyword in APP_GATEWAY_KEYWORDS:
        if keyword in hay:
            score += 3
    if "app." in href.lower():
        score += 6
    if any(k in href.lower() for k in ["/app", "/trade", "/predict", "/portfolio", "/bridge"]):
        score += 3
    return score


def candidate_urls(base_url: str, parser: AnchorParser):
    ranked = []
    seen = set()
    for anchor in parser.anchors:
        href = html.unescape((anchor["href"] or "").strip())
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        absolute = normalize_url(urljoin(base_url, href))
        if not absolute:
            continue
        if not same_project_domain(base_url, absolute):
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        score = score_anchor(anchor["text"], absolute)
        if score <= 0:
            continue
        ranked.append((score, absolute, anchor["text"]))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return ranked[:MAX_CANDIDATE_PAGES]


def gateway_urls(base_url: str, parser: AnchorParser):
    ranked = []
    seen = set()
    for anchor in parser.anchors:
        href = html.unescape((anchor["href"] or "").strip())
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        absolute = normalize_url(urljoin(base_url, href))
        if not absolute:
            continue
        if not same_project_domain(base_url, absolute):
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        score = score_gateway_anchor(anchor["text"], absolute)
        if score <= 0:
            continue
        ranked.append((score, absolute, anchor["text"]))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return ranked[:MAX_GATEWAY_PAGES]


def extract_yields(text: str):
    found = []
    for match in YIELD_RE.finditer(text):
        found.append((float(match.group(1)), match.group(2).upper(), match.group(0)))
    for match in RATE_RE.finditer(text):
        found.append((float(match.group(2)), match.group(1).upper(), match.group(0)))
    dedup = []
    seen = set()
    for item in found:
        key = (item[0], item[1])
        if key not in seen:
            seen.add(key)
            dedup.append(item)
    dedup.sort(key=lambda item: (-item[0], item[1]))
    return dedup


def summarize_keywords(text: str):
    words = []
    seen = set()
    for match in WORD_RE.finditer(text):
        word = match.group(1).lower()
        if word not in seen:
            seen.add(word)
            words.append(word)
    return words


def analyze_page(fetch_result: FetchResult):
    text = re.sub(r"\s+", " ", fetch_result.body)
    parser = parse_page(fetch_result)
    yields = extract_yields(text)
    keywords = summarize_keywords(text)
    return {
        "title": " ".join(parser.title.split()) or "N/A",
        "yields": yields,
        "keywords": keywords,
        "parser": parser,
    }


def score_page_choice(candidate_url: str, page_analysis):
    score = 0
    url_l = candidate_url.lower()
    title_l = page_analysis["title"].lower()
    if url_l.endswith("/staking"):
        score += 18
    elif url_l.endswith("/stake"):
        score += 16
    elif url_l.endswith("/validators") or url_l.endswith("/validator"):
        score += 10
    elif url_l.endswith("/rewards"):
        score += 6
    elif url_l.endswith("/earn"):
        score += 4
    for keyword in STAKE_KEYWORDS:
        if keyword in url_l:
            score += 4
        if keyword in title_l:
            score += 2
    score += len(page_analysis["keywords"])
    if page_analysis["yields"]:
        score += 25
    if any(noisy in url_l for noisy in ["breakpoint", "blog", "news", "event", "conference"]):
        score -= 6
    if any(bad in title_l for bad in ["not found", "page not found", "category not found", "404"]):
        score -= 20
    return score


def guessed_routes(seed_url: str):
    guesses = []
    for index, route in enumerate(DIRECT_ROUTE_GUESSES):
        guesses.append((20 - index, normalize_url(urljoin(seed_url.rstrip("/") + "/", route.lstrip("/")))))
    parsed = urlparse(seed_url)
    host_root = f"{parsed.scheme}://{parsed.netloc}/" if parsed.scheme and parsed.netloc else ""
    if host_root:
        for index, route in enumerate(DIRECT_ROUTE_GUESSES):
            guesses.append((20 - index, normalize_url(urljoin(host_root, route.lstrip("/")))))
    dedup = []
    seen = set()
    for score, guess in guesses:
        if guess and guess not in seen:
            seen.add(guess)
            dedup.append((score, guess))
    return dedup


def discover_for_row(row, cache):
    cache_key = row["coingecko_id"]
    if cache_key in cache:
        return cache[cache_key]

    result = {
        "rank": row["rank"],
        "name": row["name"],
        "symbol": row["symbol"],
        "coingecko_id": row["coingecko_id"],
        "website_final_url": row["website_final_url"],
        "selected_official_x": row["selected_official_x"],
        "staking_status": "not_checked",
        "home_page_keywords": "N/A",
        "candidate_staking_pages": "N/A",
        "selected_staking_page": "N/A",
        "selected_page_title": "N/A",
        "selected_page_http_code": "N/A",
        "yield_pct": "",
        "yield_type": "N/A",
        "yield_snippet": "N/A",
        "discovery_source": "website",
        "discovery_notes": "",
    }

    base_url = normalize_url(row.get("website_final_url", ""))
    if not base_url or row.get("website_http_code", "") == "N/A":
        result["staking_status"] = "website_unavailable"
        result["discovery_notes"] = "No validated website available."
        cache[cache_key] = result
        return result

    home_fetch = fetch_url(base_url)
    home_analysis = analyze_page(home_fetch)
    result["home_page_keywords"] = " | ".join(home_analysis["keywords"]) if home_analysis["keywords"] else "N/A"

    if home_fetch.http_code.startswith("2") and home_analysis["yields"]:
        best = home_analysis["yields"][0]
        result["staking_status"] = "yield_found_on_homepage"
        result["selected_staking_page"] = normalize_url(home_fetch.final_url) or base_url
        result["selected_page_title"] = home_analysis["title"]
        result["selected_page_http_code"] = home_fetch.http_code or "N/A"
        result["yield_pct"] = best[0]
        result["yield_type"] = best[1]
        result["yield_snippet"] = best[2]
        result["candidate_staking_pages"] = result["selected_staking_page"]
        cache[cache_key] = result
        return result

    candidates = candidate_urls(home_fetch.final_url or base_url, home_analysis["parser"])
    gateways = gateway_urls(home_fetch.final_url or base_url, home_analysis["parser"])

    expanded_candidates = list(candidates)
    seen_candidate_urls = {item[1] for item in expanded_candidates}

    for _, gateway_url, _ in gateways:
        gateway_fetch = fetch_url(gateway_url)
        gateway_analysis = analyze_page(gateway_fetch)
        gateway_final = normalize_url(gateway_fetch.final_url) or gateway_url

        if gateway_fetch.http_code.startswith("2") and gateway_analysis["yields"]:
            best = gateway_analysis["yields"][0]
            result["staking_status"] = "yield_found_on_staking_page"
            result["selected_staking_page"] = gateway_final
            result["selected_page_title"] = gateway_analysis["title"]
            result["selected_page_http_code"] = gateway_fetch.http_code or "N/A"
            result["yield_pct"] = best[0]
            result["yield_type"] = best[1]
            result["yield_snippet"] = best[2]
            result["candidate_staking_pages"] = gateway_final
            result["discovery_notes"] = "Yield found on app gateway page."
            cache[cache_key] = result
            return result

        for item in candidate_urls(gateway_final, gateway_analysis["parser"]):
            if item[1] not in seen_candidate_urls:
                expanded_candidates.append(item)
                seen_candidate_urls.add(item[1])
        for guess_score, guess in guessed_routes(gateway_final):
            if guess not in seen_candidate_urls:
                expanded_candidates.append((guess_score, guess, "direct_route_guess"))
                seen_candidate_urls.add(guess)
        time.sleep(REQUEST_GAP_SECONDS)

    for guess_score, guess in guessed_routes(home_fetch.final_url or base_url):
        if guess not in seen_candidate_urls:
            expanded_candidates.append((guess_score, guess, "direct_route_guess"))
            seen_candidate_urls.add(guess)

    expanded_candidates.sort(key=lambda item: (-item[0], item[1]))
    candidates = expanded_candidates[: max(MAX_CANDIDATE_PAGES, 8)]
    result["candidate_staking_pages"] = " | ".join(item[1] for item in candidates) if candidates else "N/A"

    best_page = None
    best_score = -10**9
    for candidate_anchor_score, candidate_url, _ in candidates:
        page_fetch = fetch_url(candidate_url)
        page_analysis = analyze_page(page_fetch)
        if page_fetch.http_code.startswith("2") and (page_analysis["yields"] or page_analysis["keywords"]):
            page_score = candidate_anchor_score + score_page_choice(candidate_url, page_analysis)
            if page_score > best_score:
                best_score = page_score
                best_page = (candidate_url, page_fetch, page_analysis)
            if page_analysis["yields"]:
                break
        time.sleep(REQUEST_GAP_SECONDS)

    if best_page:
        candidate_url, page_fetch, page_analysis = best_page
        result["selected_staking_page"] = normalize_url(page_fetch.final_url) or candidate_url
        result["selected_page_title"] = page_analysis["title"]
        result["selected_page_http_code"] = page_fetch.http_code or "N/A"
        if page_analysis["yields"]:
            best = page_analysis["yields"][0]
            result["staking_status"] = "yield_found_on_staking_page"
            result["yield_pct"] = best[0]
            result["yield_type"] = best[1]
            result["yield_snippet"] = best[2]
            result["discovery_notes"] = "Keyword-ranked page contained a yield pattern."
        else:
            result["staking_status"] = "staking_page_found_no_yield"
            result["discovery_notes"] = "Candidate page had staking keywords but no explicit APR/APY pattern."
    else:
        if home_analysis["keywords"]:
            result["staking_status"] = "keywords_on_homepage_no_page"
            result["discovery_notes"] = "Homepage mentions staking-related terms but no stronger candidate page was found."
        else:
            result["staking_status"] = "no_staking_signal"
            result["discovery_notes"] = "No staking-related signal found on homepage or linked candidate pages."

    cache[cache_key] = result
    return result


def write_rows(rows, output_path: Path):
    with output_path.open("w", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(
            csvfile,
            fieldnames=[
                "rank",
                "name",
                "symbol",
                "coingecko_id",
                "website_final_url",
                "selected_official_x",
                "staking_status",
                "home_page_keywords",
                "candidate_staking_pages",
                "selected_staking_page",
                "selected_page_title",
                "selected_page_http_code",
                "yield_pct",
                "yield_type",
                "yield_snippet",
                "discovery_source",
                "discovery_notes",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)


def main():
    args = parse_args()
    global CACHE_PATH
    CACHE_PATH = args["cache_path"]
    rows = load_csv_rows(args["input_path"])
    baseline_by_id = load_baseline_map(args["skip_baseline_path"])
    cache = load_cache()
    output_rows = []

    for idx, row in enumerate(rows, start=1):
        baseline_row = baseline_by_id.get(row["coingecko_id"])
        if baseline_row and baseline_row.get("baseline_yield_pct", "") not in ("", None):
            output_rows.append(skipped_baseline_result(row, baseline_row))
            if idx % 25 == 0:
                write_rows(output_rows, args["output_path"])
            continue
        was_cached = row["coingecko_id"] in cache
        output_rows.append(discover_for_row(row, cache))
        if idx % 25 == 0:
            save_cache(cache)
            write_rows(output_rows, args["output_path"])
        if not was_cached:
            time.sleep(REQUEST_GAP_SECONDS)

    save_cache(cache)
    write_rows(output_rows, args["output_path"])
    print(f"wrote {len(output_rows)} rows to {args['output_path']}")


if __name__ == "__main__":
    main()
