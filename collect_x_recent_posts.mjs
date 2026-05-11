import { createBrowser, createPage } from "xactions/scrapers";

const DEFAULT_LIMIT = 30;
const DEFAULT_MAX_SCROLLS = 18;
const DEFAULT_INCLUDE_REPLIES = false;
const DEFAULT_REPLY_SCAN_LIMIT = 8;

const SIGNAL_KEYWORDS = [
  "stake",
  "staking",
  "staked",
  "apr",
  "apy",
  "yield",
  "delegate",
  "delegation",
];

const SUPPORTING_KEYWORDS = [
  "reward",
  "rewards",
];

function parseArgs(argv) {
  const args = {
    username: null,
    limit: DEFAULT_LIMIT,
    includeReplies: DEFAULT_INCLUDE_REPLIES,
    maxScrolls: DEFAULT_MAX_SCROLLS,
    replyScanLimit: DEFAULT_REPLY_SCAN_LIMIT,
  };

  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (!args.username && !arg.startsWith("--")) {
      args.username = arg.replace(/^@/, "");
      continue;
    }
    if (arg === "--limit" && argv[i + 1]) {
      args.limit = Number.parseInt(argv[i + 1], 10) || DEFAULT_LIMIT;
      i += 1;
      continue;
    }
    if (arg === "--max-scrolls" && argv[i + 1]) {
      args.maxScrolls = Number.parseInt(argv[i + 1], 10) || DEFAULT_MAX_SCROLLS;
      i += 1;
      continue;
    }
    if (arg === "--reply-scan-limit" && argv[i + 1]) {
      args.replyScanLimit = Number.parseInt(argv[i + 1], 10) || DEFAULT_REPLY_SCAN_LIMIT;
      i += 1;
      continue;
    }
    if (arg === "--replies") {
      args.includeReplies = true;
      continue;
    }
  }

  if (!args.username) {
    throw new Error("Usage: node collect_x_recent_posts.mjs <username> [--limit 30] [--max-scrolls 18] [--reply-scan-limit 8] [--replies]");
  }

  return args;
}

function keywordHits(text) {
  const lower = (text || "").toLowerCase();
  return [...SIGNAL_KEYWORDS, ...SUPPORTING_KEYWORDS].filter((keyword) => lower.includes(keyword));
}

function isStakingSignal(text) {
  const lower = (text || "").toLowerCase();
  return SIGNAL_KEYWORDS.some((keyword) => lower.includes(keyword));
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function isExternalUrl(url, username) {
  if (!url) {
    return false;
  }
  try {
    const parsed = new URL(url);
    const host = parsed.hostname.toLowerCase();
    if (host === "x.com" || host === "twitter.com") {
      const path = parsed.pathname.toLowerCase();
      if (path.startsWith(`/${username.toLowerCase()}/status/`)) {
        return false;
      }
      if (path === `/${username.toLowerCase()}` || path.startsWith(`/${username.toLowerCase()}/`)) {
        return false;
      }
      return false;
    }
    return true;
  } catch {
    return false;
  }
}

function pickBestLink(urls) {
  if (!urls || !urls.length) {
    return null;
  }
  const preferred = urls.find((url) => {
    const lower = url.toLowerCase();
    return lower.includes("staking") || lower.includes("stake") || lower.includes("earn") || lower.includes("reward");
  });
  return preferred || urls[0];
}

async function extractTweets(page) {
  return page.evaluate(() => {
    const normalizeUrl = (href) => {
      if (!href) {
        return null;
      }
      try {
        return new URL(href, window.location.origin).toString();
      } catch {
        return href;
      }
    };

    const articles = document.querySelectorAll('article[data-testid="tweet"]');
    return Array.from(articles).map((article) => {
      const authorLink = article.querySelector('[data-testid="User-Name"] a[href^="/"]');
      const statusLink = article.querySelector('a[href*="/status/"]');
      const textEl = article.querySelector('[data-testid="tweetText"]');
      const timeEl = article.querySelector("time");
      const socialContext = article.querySelector('[data-testid="socialContext"]');
      const quoteTweet = article.querySelector('[data-testid="quoteTweet"]');
      const anchors = Array.from(article.querySelectorAll('a[href]'))
        .map((anchor) => normalizeUrl(anchor.getAttribute("href")))
        .filter(Boolean);

      const authorHref = authorLink?.getAttribute("href") || "";
      const author = authorHref.split("/").filter(Boolean)[0] || null;
      const statusHref = normalizeUrl(statusLink?.getAttribute("href")) || null;
      const statusMatch = statusHref?.match(/status\/(\d+)/);
      const statusAuthorMatch = statusHref?.match(/x\.com\/([^/]+)\/status\/(\d+)/);

      return {
        id: statusMatch?.[1] || null,
        author,
        statusAuthor: statusAuthorMatch?.[1] || null,
        text: textEl?.textContent || "",
        timestamp: timeEl?.getAttribute("datetime") || null,
        url: statusHref,
        links: anchors,
        isRetweet: Boolean(socialContext),
        isQuote: Boolean(quoteTweet),
      };
    }).filter((tweet) => tweet.id && tweet.url);
  });
}

function normalizeTweet(tweet, targetUsername) {
  const author = (tweet.author || "").replace(/^@/, "");
  const statusAuthor = (tweet.statusAuthor || "").replace(/^@/, "");
  const canonicalAuthor = author || statusAuthor;
  const matchesTarget = canonicalAuthor.toLowerCase() === targetUsername.toLowerCase();
  const text = tweet.text || "";
  const externalLinks = Array.from(new Set((tweet.links || []).filter((url) => isExternalUrl(url, canonicalAuthor || targetUsername))));

  return {
    id: tweet.id,
    author: canonicalAuthor || null,
    timestamp: tweet.timestamp,
    url: tweet.url,
    text,
    isRetweet: Boolean(tweet.isRetweet),
    isQuote: Boolean(tweet.isQuote),
    matchesTarget,
    keywordHits: keywordHits(text),
    isStakingSignal: isStakingSignal(text),
    externalLinks,
    directLink: pickBestLink(externalLinks),
  };
}

async function scrapeRepliesForLinks(page, tweetUrl, targetUsername, replyScanLimit) {
  await page.goto(tweetUrl, { waitUntil: "networkidle2" });
  await sleep(1800);

  let stablePasses = 0;
  const replies = new Map();

  for (let scroll = 0; scroll < replyScanLimit; scroll += 1) {
    const rows = await page.evaluate((tweetUrlArg) => {
      const normalizeUrl = (href) => {
        if (!href) {
          return null;
        }
        try {
          return new URL(href, window.location.origin).toString();
        } catch {
          return href;
        }
      };

      const articles = document.querySelectorAll('article[data-testid="tweet"]');
      return Array.from(articles).map((article) => {
        const statusLink = article.querySelector('a[href*="/status/"]');
        const normalizedStatus = normalizeUrl(statusLink?.getAttribute("href"));
        const isMainTweet = normalizedStatus === tweetUrlArg;
        const authorLink = article.querySelector('[data-testid="User-Name"] a[href^="/"]');
        const textEl = article.querySelector('[data-testid="tweetText"]');
        const anchors = Array.from(article.querySelectorAll('a[href]'))
          .map((anchor) => normalizeUrl(anchor.getAttribute("href")))
          .filter(Boolean);

        return {
          url: normalizedStatus,
          isMainTweet,
          author: authorLink?.getAttribute("href")?.split("/").filter(Boolean)[0] || null,
          text: textEl?.textContent || "",
          links: anchors,
        };
      }).filter((row) => row.url && !row.isMainTweet);
    }, tweetUrl);

    const before = replies.size;
    for (const row of rows) {
      const externalLinks = Array.from(new Set((row.links || []).filter((url) => isExternalUrl(url, row.author || targetUsername))));
      if (!externalLinks.length) {
        continue;
      }
      const id = row.url.match(/status\/(\d+)/)?.[1] || row.url;
      replies.set(id, {
        replyUrl: row.url,
        replyAuthor: row.author,
        replyText: row.text,
        externalLinks,
        bestLink: pickBestLink(externalLinks),
        keywordHits: keywordHits(row.text),
        isStakingSignal: isStakingSignal(row.text),
      });
    }

    if (replies.size === before) {
      stablePasses += 1;
    } else {
      stablePasses = 0;
    }

    if (stablePasses >= 3 || replies.size >= 5) {
      break;
    }

    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
    await sleep(1800 + Math.floor(Math.random() * 1200));
  }

  return Array.from(replies.values()).sort((a, b) => {
    const aScore = (a.isStakingSignal ? 100 : 0) + a.keywordHits.length * 10 + (a.bestLink ? 1 : 0);
    const bScore = (b.isStakingSignal ? 100 : 0) + b.keywordHits.length * 10 + (b.bestLink ? 1 : 0);
    return bScore - aScore;
  });
}

export async function scrapeRecentPostsWithPage(page, { username, limit, includeReplies, maxScrolls, replyScanLimit = DEFAULT_REPLY_SCAN_LIMIT }) {
  const route = includeReplies ? `${username}/with_replies` : username;
  const url = `https://x.com/${route}`;

  await page.goto(url, { waitUntil: "networkidle2" });
  await sleep(1800);

  const collected = new Map();
  let stablePasses = 0;

  for (let scroll = 0; scroll < maxScrolls; scroll += 1) {
    const rawTweets = await extractTweets(page);
    const normalized = rawTweets.map((tweet) => normalizeTweet(tweet, username));
    const authored = normalized.filter((tweet) => tweet.matchesTarget && !tweet.isRetweet);

    const before = collected.size;
    for (const tweet of authored) {
      if (!collected.has(tweet.id)) {
        collected.set(tweet.id, tweet);
      }
    }

    if (collected.size >= limit) {
      break;
    }

    if (collected.size === before) {
      stablePasses += 1;
    } else {
      stablePasses = 0;
    }

    if (stablePasses >= 4) {
      break;
    }

    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
    await sleep(1800 + Math.floor(Math.random() * 1200));
  }

  const tweets = Array.from(collected.values())
    .sort((a, b) => {
      const aTime = Date.parse(a.timestamp || 0);
      const bTime = Date.parse(b.timestamp || 0);
      return bTime - aTime;
    })
    .slice(0, limit);

  const stakingTweets = tweets.filter((tweet) => tweet.isStakingSignal);
  for (const tweet of stakingTweets) {
    if (tweet.directLink) {
      tweet.replyLinks = [];
      tweet.selectedLink = tweet.directLink;
      tweet.linkSource = "post";
      continue;
    }
    const replyLinks = await scrapeRepliesForLinks(page, tweet.url, username, replyScanLimit);
    tweet.replyLinks = replyLinks;
    tweet.selectedLink = replyLinks[0]?.bestLink || null;
    tweet.linkSource = tweet.selectedLink ? "reply" : null;
  }

  return {
    username,
    requested: limit,
    returned: tweets.length,
    scannedUrl: url,
      stakingSignalCount: stakingTweets.length,
    tweets,
  };
}

export async function scrapeRecentPosts(args) {
  const browser = await createBrowser({ headless: true });
  const page = await createPage(browser);
  try {
    return await scrapeRecentPostsWithPage(page, args);
  } finally {
    await browser.close();
  }
}

const isMainModule = process.argv[1] && new URL(`file://${process.argv[1]}`).href === import.meta.url;

if (isMainModule) {
  const args = parseArgs(process.argv.slice(2));
  const result = await scrapeRecentPosts(args);
  console.log(JSON.stringify(result, null, 2));
}
