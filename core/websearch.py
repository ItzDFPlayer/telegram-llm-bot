"""
Web search backend for the model's web_search tool.

Free providers (no paid tier required):
  - DuckDuckGo – no API key; uses the optional `ddgs` package, falling back to
                 direct scraping of DuckDuckGo's HTML endpoint
  - Brave      – free tier, requires a BRAVE_API_KEY
  - Bing       – no API key; scrapes Bing search results directly
  - SearXNG    – no API key; queries a SearXNG instance (set SEARXNG_URL, or it
                 tries well-known public instances)

Every search is reduced to a short plain-text digest that gets injected into
 the model's context as a tool result.
"""
import logging
import time
from typing import Optional
from urllib.parse import parse_qs, urlparse

import httpx

import config

logger = logging.getLogger("bot.websearch")

BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"

# A normal browser User-Agent, which makes the HTML-scraping providers behave
# like a regular visitor instead of a bot.
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# Used only when SEARXNG_URL is not configured. Public instances are hit-or-miss
# (many disable the JSON API or block bots), so we probe them with a short,
# bounded timeout and never block the reply for long.
SEARXNG_FALLBACK_INSTANCES = (
    "https://searx.tiekoetter.com",
    "https://searx.be",
)
# Per-instance timeout and total budget for the fallback probing loop, so an
# unreachable public instance can never make the bot look stuck.
SEARXNG_PROBE_TIMEOUT = 5.0
SEARXNG_MAX_TOTAL = 15.0

PROVIDERS = ("duckduckgo", "brave", "bing", "searxng")


def search_enabled() -> bool:
    """True if a search provider is configured."""
    return config.SEARCH_PROVIDER in PROVIDERS


def _format_results(results: list[dict]) -> Optional[str]:
    """Render a list of result dicts as a compact text digest for the model."""
    lines = []
    for i, r in enumerate(results, 1):
        title = (r.get("title") or "").strip()
        url = (r.get("url") or "").strip()
        snippet = (r.get("snippet") or "").strip()
        lines.append(f"{i}. {title}\n   {url}\n   {snippet}")
    if not lines:
        return None
    text = "\n\n".join(lines)
    limit = config.SEARCH_RESULT_LIMIT_CHARS
    if len(text) > limit:
        text = text[:limit].rstrip() + "\n…(truncated)"
    return text


def _search_brave(query: str, max_results: int) -> list[dict]:
    """Brave Search API (free tier). Requires BRAVE_API_KEY."""
    api_key = config.BRAVE_API_KEY
    if not api_key:
        logger.error("🔎 Brave provider selected but BRAVE_API_KEY is not set.")
        return []
    params = {"q": query, "count": max_results, "safesearch": "moderate"}
    headers = {"X-Subscription-Token": api_key, "Accept": "application/json"}
    try:
        resp = httpx.get(
            BRAVE_SEARCH_URL, params=params, headers=headers, timeout=config.SEARCH_TIMEOUT
        )
        resp.raise_for_status()
    except Exception as e:
        logger.error(f"🔎 Brave search failed: {e}")
        return []
    results = []
    for item in (resp.json().get("web") or {}).get("results", [])[:max_results]:
        results.append({
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "snippet": item.get("description", ""),
        })
    return results


def _search_duckduckgo(query: str, max_results: int) -> list[dict]:
    """DuckDuckGo via the optional `ddgs` package (no API key needed)."""
    try:
        from ddgs import DDGS
    except ImportError:
        try:
            from duckduckgo_search import DDGS
        except ImportError:
            logger.error(
                "🔎 DuckDuckGo provider selected but `ddgs` is not installed. "
                "Run: pip install ddgs"
            )
            return []
    results = []
    try:
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                results.append({
                    "title": r.get("title", ""),
                    "url": r.get("href", ""),
                    "snippet": r.get("body", ""),
                })
    except Exception as e:
        logger.error(f"🔎 DuckDuckGo search failed: {e}")
        return []
    return results


def _soup_from_html(html: str):
    """Parse HTML with BeautifulSoup (optional dependency)."""
    from bs4 import BeautifulSoup
    return BeautifulSoup(html, "html.parser")


def _fetch_html(url: str, *, params=None, data=None, headers=None, method="GET"):
    """Fetch a page with a browser-like UA. Returns the HTML text or None."""
    hdrs = {"User-Agent": DEFAULT_UA}
    if headers:
        hdrs.update(headers)
    try:
        if method.upper() == "POST":
            resp = httpx.post(
                url, params=params, data=data, headers=hdrs,
                timeout=config.SEARCH_TIMEOUT, follow_redirects=True,
            )
        else:
            resp = httpx.get(
                url, params=params, headers=hdrs,
                timeout=config.SEARCH_TIMEOUT, follow_redirects=True,
            )
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        logger.warning(f"🔎 Failed to fetch {url}: {e}")
        return None


def _decode_duckduckgo_url(href: str) -> str:
    """DuckDuckGo wraps result links as /l/?uddg=<encoded> — unwrap them."""
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        uddg = parse_qs(parsed.query).get("uddg")
        if uddg:
            return uddg[0]
    return href


def _search_duckduckgo_html(query: str, max_results: int) -> list[dict]:
    """Direct scraping of DuckDuckGo's HTML endpoint (no ddgs package needed)."""
    html = _fetch_html(
        "https://html.duckduckgo.com/html/",
        method="POST",
        data={"q": query},
    )
    if not html:
        return []
    try:
        soup = _soup_from_html(html)
    except ImportError:
        logger.error("🔎 DDG HTML scraping needs `beautifulsoup4`. Run: pip install beautifulsoup4")
        return []
    results = []
    for div in soup.select("div.result")[:max_results]:
        a = div.select_one("a.result__a")
        if not a:
            continue
        snippet_el = div.select_one("a.result__snippet") or div.select_one("div.result__snippet")
        results.append({
            "title": a.get_text(" ", strip=True),
            "url": _decode_duckduckgo_url(a.get("href") or ""),
            "snippet": snippet_el.get_text(" ", strip=True) if snippet_el else "",
        })
    return results


def _search_bing(query: str, max_results: int) -> list[dict]:
    """Direct scraping of Bing search results (no API key needed)."""
    html = _fetch_html(
        "https://www.bing.com/search",
        params={"q": query, "count": max_results, "setlang": "en"},
    )
    if not html:
        return []
    try:
        soup = _soup_from_html(html)
    except ImportError:
        logger.error("🔎 Bing scraping needs `beautifulsoup4`. Run: pip install beautifulsoup4")
        return []
    results = []
    for li in soup.select("li.b_algo")[:max_results]:
        a = li.select_one("h2 a")
        if not a:
            continue
        p = li.select_one("p") or li.select_one(".b_caption")
        results.append({
            "title": a.get_text(" ", strip=True),
            "url": a.get("href") or "",
            "snippet": p.get_text(" ", strip=True) if p else "",
        })
    return results


def _query_searxng_instance(base: str, query: str, max_results: int,
                            timeout: Optional[float] = None) -> list[dict]:
    """Query one SearXNG instance; returns results or [] on failure."""
    base = base.rstrip("/")
    try:
        resp = httpx.get(
            f"{base}/search",
            params={"q": query, "format": "json", "safesearch": "1"},
            headers={"User-Agent": DEFAULT_UA, "Accept": "application/json"},
            timeout=timeout if timeout is not None else config.SEARCH_TIMEOUT,
            follow_redirects=True,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.debug(f"🔎 SearXNG instance {base} failed: {e}")
        return []
    results = []
    for item in data.get("results", [])[:max_results]:
        results.append({
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "snippet": item.get("content", ""),
        })
    return results


def _search_searxng(query: str, max_results: int) -> list[dict]:
    """
    SearXNG meta-search. Uses the configured SEARXNG_URL instance if set;
    otherwise probes a few public instances with a short, bounded timeout so
    an unreachable instance can never make the reply hang.
    """
    if config.SEARXNG_URL:
        return _query_searxng_instance(config.SEARXNG_URL, query, max_results,
                                       timeout=config.SEARCH_TIMEOUT)
    deadline = time.monotonic() + SEARXNG_MAX_TOTAL
    for base in SEARXNG_FALLBACK_INSTANCES:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        logger.info(f"🔎 Trying SearXNG instance {base}...")
        results = _query_searxng_instance(
            base, query, max_results,
            timeout=min(SEARXNG_PROBE_TIMEOUT, remaining),
        )
        if results:
            return results
    logger.warning(
        "🔎 No public SearXNG instance responded. Public instances are "
        "unreliable — set SEARXNG_URL to your own instance for dependable results."
    )
    return []


def search_web(query: str, max_results: Optional[int] = None) -> Optional[str]:
    """Search the web with the configured provider; returns a text digest or None."""
    query = (query or "").strip()
    if not query:
        return None
    max_results = max_results or config.SEARCH_MAX_RESULTS

    if config.SEARCH_PROVIDER == "brave":
        results = _search_brave(query, max_results)
    elif config.SEARCH_PROVIDER == "duckduckgo":
        results = _search_duckduckgo(query, max_results) or _search_duckduckgo_html(query, max_results)
    elif config.SEARCH_PROVIDER == "bing":
        results = _search_bing(query, max_results)
    elif config.SEARCH_PROVIDER == "searxng":
        results = _search_searxng(query, max_results)
    else:
        logger.warning(f"🔎 Web search provider {config.SEARCH_PROVIDER!r} is not enabled.")
        return None

    return _format_results(results)
