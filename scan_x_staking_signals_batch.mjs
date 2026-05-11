import fs from "node:fs";
import { createBrowser, createPage } from "xactions/scrapers";
import { scrapeRecentPostsWithPage } from "./collect_x_recent_posts.mjs";

const DEFAULT_TOP_N = 100;
const DEFAULT_LIMIT = 30;
const DEFAULT_MAX_SCROLLS = 18;
const DEFAULT_REPLY_SCAN_LIMIT = 8;
const DEFAULT_DELAY_MS = 2500;

function parseArgs(argv) {
  const args = {
    input: "top_1000_official_links_validated.csv",
    output: "top_100_x_staking_signals.csv",
    skipBaselineFile: "top_1000_staking_baseline.csv",
    topN: DEFAULT_TOP_N,
    limit: DEFAULT_LIMIT,
    maxScrolls: DEFAULT_MAX_SCROLLS,
    replyScanLimit: DEFAULT_REPLY_SCAN_LIMIT,
    delayMs: DEFAULT_DELAY_MS,
    includePostText: false,
  };

  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--input" && argv[i + 1]) {
      args.input = argv[++i];
      continue;
    }
    if (arg === "--output" && argv[i + 1]) {
      args.output = argv[++i];
      continue;
    }
    if (arg === "--skip-baseline-file" && argv[i + 1]) {
      args.skipBaselineFile = argv[++i];
      continue;
    }
    if (arg === "--top" && argv[i + 1]) {
      args.topN = Number.parseInt(argv[++i], 10) || DEFAULT_TOP_N;
      continue;
    }
    if (arg === "--limit" && argv[i + 1]) {
      args.limit = Number.parseInt(argv[++i], 10) || DEFAULT_LIMIT;
      continue;
    }
    if (arg === "--max-scrolls" && argv[i + 1]) {
      args.maxScrolls = Number.parseInt(argv[++i], 10) || DEFAULT_MAX_SCROLLS;
      continue;
    }
    if (arg === "--reply-scan-limit" && argv[i + 1]) {
      args.replyScanLimit = Number.parseInt(argv[++i], 10) || DEFAULT_REPLY_SCAN_LIMIT;
      continue;
    }
    if (arg === "--delay-ms" && argv[i + 1]) {
      args.delayMs = Number.parseInt(argv[++i], 10) || DEFAULT_DELAY_MS;
      continue;
    }
    if (arg === "--include-post-text") {
      args.includePostText = true;
      continue;
    }
  }

  return args;
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function parseCsvLine(line) {
  const values = [];
  let current = "";
  let inQuotes = false;
  for (let i = 0; i < line.length; i += 1) {
    const char = line[i];
    const next = line[i + 1];
    if (char === '"') {
      if (inQuotes && next === '"') {
        current += '"';
        i += 1;
      } else {
        inQuotes = !inQuotes;
      }
      continue;
    }
    if (char === "," && !inQuotes) {
      values.push(current);
      current = "";
      continue;
    }
    current += char;
  }
  values.push(current);
  return values;
}

function readCsv(path) {
  const text = fs.readFileSync(path, "utf8").replace(/^\uFEFF/, "");
  const lines = text.split(/\r?\n/).filter(Boolean);
  const headers = parseCsvLine(lines[0]);
  return lines.slice(1).map((line) => {
    const values = parseCsvLine(line);
    const row = {};
    headers.forEach((header, index) => {
      row[header] = values[index] ?? "";
    });
    return row;
  });
}

function escapeCsv(value) {
  const text = value == null ? "" : String(value);
  if (/[",\n]/.test(text)) {
    return `"${text.replace(/"/g, '""')}"`;
  }
  return text;
}

function writeCsv(path, rows) {
  const headers = [
    "rank",
    "name",
    "symbol",
    "coingecko_id",
    "x_username",
    "profile_url",
    "scan_status",
    "posts_returned",
    "staking_signal_count",
    "latest_signal_post_url",
    "latest_signal_post_date",
    "latest_signal_post_text",
    "latest_signal_keywords",
    "selected_link",
    "link_source",
    "reply_link_count",
  ];
  const lines = [headers.join(",")];
  for (const row of rows) {
    lines.push(headers.map((header) => escapeCsv(row[header] ?? "")).join(","));
  }
  fs.writeFileSync(path, `${lines.join("\n")}\n`, "utf8");
}

function loadBaselineMap(path) {
  if (!path || !fs.existsSync(path)) {
    return new Map();
  }
  const rows = readCsv(path);
  return new Map(rows.map((row) => [row.coingecko_id, row]));
}

function loadExistingResults(path) {
  if (!path || !fs.existsSync(path)) {
    return [];
  }
  return readCsv(path);
}

function extractUsername(xUrl) {
  if (!xUrl || xUrl === "N/A") {
    return null;
  }
  try {
    const parsed = new URL(xUrl);
    const parts = parsed.pathname.split("/").filter(Boolean);
    return parts[0] || null;
  } catch {
    return null;
  }
}

function publicPostText(tweet, includePostText) {
  if (!tweet) {
    return "";
  }
  return includePostText ? tweet.text || "" : "[redacted: see post URL]";
}

function buildOutputRow(row, username, result, includePostText = false) {
  const latestSignal = (result?.tweets || []).find((tweet) => tweet.isStakingSignal);
  return {
    rank: row.rank,
    name: row.name,
    symbol: row.symbol,
    coingecko_id: row.coingecko_id,
    x_username: username || "",
    profile_url: username ? `https://x.com/${username}` : "",
    scan_status: result ? "ok" : "no_x_profile",
    posts_returned: result?.returned ?? 0,
    staking_signal_count: result?.stakingSignalCount ?? 0,
    latest_signal_post_url: latestSignal?.url || "",
    latest_signal_post_date: latestSignal?.timestamp || "",
    latest_signal_post_text: publicPostText(latestSignal, includePostText),
    latest_signal_keywords: (latestSignal?.keywordHits || []).join(";"),
    selected_link: latestSignal?.selectedLink || latestSignal?.directLink || "",
    link_source: latestSignal?.linkSource || (latestSignal?.directLink ? "post" : ""),
    reply_link_count: latestSignal?.replyLinks?.length || 0,
  };
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const baselineById = loadBaselineMap(args.skipBaselineFile);
  const rows = readCsv(args.input)
    .sort((a, b) => Number(a.rank) - Number(b.rank))
    .slice(0, args.topN);

  let browser = await createBrowser({ headless: true });
  let page = await createPage(browser);
  const results = loadExistingResults(args.output);
  const processedIds = new Set(results.map((row) => row.coingecko_id));
  let processedSinceRefresh = 0;

  try {
    for (const row of rows) {
      if (processedIds.has(row.coingecko_id)) {
        continue;
      }
      const baseline = baselineById.get(row.coingecko_id);
      if (baseline?.baseline_source && baseline?.baseline_yield_pct) {
        const skipped = {
          rank: row.rank,
          name: row.name,
          symbol: row.symbol,
          coingecko_id: row.coingecko_id,
          x_username: "",
          profile_url: "",
          scan_status: "skipped_has_baseline",
          posts_returned: 0,
          staking_signal_count: 0,
          latest_signal_post_url: "",
          latest_signal_post_date: "",
          latest_signal_post_text: "",
          latest_signal_keywords: "",
          selected_link: baseline.baseline_staking_link || baseline.baseline_asset_url || "",
          link_source: "baseline",
          reply_link_count: 0,
        };
        results.push(skipped);
        processedIds.add(row.coingecko_id);
        writeCsv(args.output, results);
        continue;
      }

      const username = extractUsername(row.selected_official_x);
      if (!username) {
        results.push(buildOutputRow(row, null, null, args.includePostText));
        continue;
      }

      try {
        let result;
        try {
          result = await scrapeRecentPostsWithPage(page, {
            username,
            limit: args.limit,
            includeReplies: false,
            maxScrolls: args.maxScrolls,
            replyScanLimit: args.replyScanLimit,
          });
        } catch (error) {
          const message = error?.message || "unknown";
          if (message.includes("detached Frame") || message.includes("Target closed") || message.includes("Execution context was destroyed")) {
            try {
              await page.close();
            } catch {}
            page = await createPage(browser);
            result = await scrapeRecentPostsWithPage(page, {
              username,
              limit: args.limit,
              includeReplies: false,
              maxScrolls: args.maxScrolls,
              replyScanLimit: args.replyScanLimit,
            });
          } else {
            throw error;
          }
        }
        results.push(buildOutputRow(row, username, result, args.includePostText));
      } catch (error) {
        results.push({
          rank: row.rank,
          name: row.name,
          symbol: row.symbol,
          coingecko_id: row.coingecko_id,
          x_username: username,
          profile_url: `https://x.com/${username}`,
          scan_status: `error:${error?.message || "unknown"}`,
          posts_returned: 0,
          staking_signal_count: 0,
          latest_signal_post_url: "",
          latest_signal_post_date: "",
          latest_signal_post_text: "",
          latest_signal_keywords: "",
          selected_link: "",
          link_source: "",
          reply_link_count: 0,
        });
      }

      processedIds.add(row.coingecko_id);
      writeCsv(args.output, results);
      processedSinceRefresh += 1;
      if (processedSinceRefresh >= 25) {
        try {
          await page.close();
        } catch {}
        try {
          await browser.close();
        } catch {}
        browser = await createBrowser({ headless: true });
        page = await createPage(browser);
        processedSinceRefresh = 0;
      }
      await sleep(args.delayMs + Math.floor(Math.random() * 1500));
    }
  } finally {
    await browser.close();
  }

  writeCsv(args.output, results);
}

await main();
