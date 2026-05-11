import fs from "node:fs";
import { createBrowser, createPage, searchTweets } from "xactions/scrapers";

const KEYWORDS = ["stake", "staking", "staked", "apr", "apy", "yield", "delegate", "delegation"];

function parseArgs(argv) {
  const args = {
    input: "top_1000_staking_baseline.csv",
    output: "top_100_x_search_fallback.csv",
    topN: 100,
    limit: 20,
    delayMs: 2500,
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
    if (arg === "--top" && argv[i + 1]) {
      args.topN = Number.parseInt(argv[++i], 10) || 100;
      continue;
    }
    if (arg === "--limit" && argv[i + 1]) {
      args.limit = Number.parseInt(argv[++i], 10) || 20;
      continue;
    }
    if (arg === "--delay-ms" && argv[i + 1]) {
      args.delayMs = Number.parseInt(argv[++i], 10) || 2500;
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

function keywordHits(text) {
  const lower = (text || "").toLowerCase();
  return KEYWORDS.filter((keyword) => lower.includes(keyword));
}

function publicPostText(tweet, includePostText) {
  if (!tweet) {
    return "";
  }
  return includePostText ? tweet.text || "" : "[redacted: see post URL]";
}

function writeCsv(path, rows) {
  const headers = [
    "rank",
    "name",
    "symbol",
    "coingecko_id",
    "query",
    "search_status",
    "match_count",
    "top_post_url",
    "top_post_author",
    "top_post_date",
    "top_post_text",
    "top_post_keywords",
  ];
  const lines = [headers.join(",")];
  for (const row of rows) {
    lines.push(headers.map((header) => escapeCsv(row[header] ?? "")).join(","));
  }
  fs.writeFileSync(path, `${lines.join("\n")}\n`, "utf8");
}

function buildQueries(row) {
  const symbol = (row.symbol || "").trim();
  const name = (row.name || "").trim();
  const queries = [];
  if (symbol) {
    queries.push(`$${symbol} staking`);
    queries.push(`${symbol} staking`);
  }
  if (name) {
    queries.push(`${name} staking`);
  }
  return [...new Set(queries)];
}

function unresolvedRows(rows) {
  return rows
    .filter((row) => !row.baseline_source || !row.baseline_yield_pct)
    .sort((a, b) => Number(a.rank) - Number(b.rank));
}

async function searchOne(page, query, limit) {
  const tweets = await searchTweets(page, query, { limit, filter: "top" });
  return tweets
    .map((tweet) => ({
      ...tweet,
      keywordHits: keywordHits(tweet.text || ""),
    }))
    .filter((tweet) => tweet.keywordHits.length > 0);
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const sourceRows = unresolvedRows(readCsv(args.input)).slice(0, args.topN);
  const browser = await createBrowser({ headless: true });
  const page = await createPage(browser);
  const results = [];

  try {
    for (const row of sourceRows) {
      const queries = buildQueries(row);
      let bestQuery = "";
      let bestMatches = [];

      for (const query of queries) {
        try {
          const matches = await searchOne(page, query, args.limit);
          if (matches.length > bestMatches.length) {
            bestMatches = matches;
            bestQuery = query;
          }
        } catch {
          // keep trying other query variants
        }
        await sleep(1200);
      }

      const topPost = bestMatches[0];
      results.push({
        rank: row.rank,
        name: row.name,
        symbol: row.symbol,
        coingecko_id: row.coingecko_id,
        query: bestQuery,
        search_status: bestMatches.length ? "ok" : "no_match",
        match_count: bestMatches.length,
        top_post_url: topPost?.url || "",
        top_post_author: topPost?.author || "",
        top_post_date: topPost?.timestamp || "",
        top_post_text: publicPostText(topPost, args.includePostText),
        top_post_keywords: (topPost?.keywordHits || []).join(";"),
      });
      writeCsv(args.output, results);
      await sleep(args.delayMs);
    }
  } finally {
    await browser.close();
  }

  writeCsv(args.output, results);
}

await main();
