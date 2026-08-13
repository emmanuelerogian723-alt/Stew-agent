"""
S.T.E.W Market Data — live cryptocurrency and stock price lookups.
No paid API keys required, ever. Uses a layered, all-free fallback chain so a
single provider being rate-limited or blocked (common for cloud-hosted IPs
like Render's) never breaks price lookups:

Crypto: CoinGecko -> Coinbase spot API -> Stew's own browser (DuckDuckGo scrape)
Stocks: Yahoo Finance chart API -> Stew's own browser (stockanalysis.com scrape)
        -> Stew's own browser (DuckDuckGo scrape)

The browser fallback reuses server/browser.py (StewBrowser) — the same
Jina-reader/httpx/BeautifulSoup browser already built into Stew, so this
costs nothing and needs no API key.
"""
import logging
import re
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; StewAgent/1.0)"}

# Common crypto name/symbol -> CoinGecko id
CRYPTO_ALIASES = {
    "btc": "bitcoin", "bitcoin": "bitcoin",
    "eth": "ethereum", "ethereum": "ethereum", "ether": "ethereum",
    "bnb": "binancecoin", "binance coin": "binancecoin",
    "xrp": "ripple", "ripple": "ripple",
    "ada": "cardano", "cardano": "cardano",
    "doge": "dogecoin", "dogecoin": "dogecoin",
    "sol": "solana", "solana": "solana",
    "dot": "polkadot", "polkadot": "polkadot",
    "matic": "matic-network", "polygon": "matic-network",
    "ltc": "litecoin", "litecoin": "litecoin",
    "trx": "tron", "tron": "tron",
    "shib": "shiba-inu", "shiba inu": "shiba-inu",
    "usdt": "tether", "tether": "tether",
    "usdc": "usd-coin",
    "avax": "avalanche-2", "avalanche": "avalanche-2",
    "link": "chainlink", "chainlink": "chainlink",
    "ton": "the-open-network", "toncoin": "the-open-network",
    "pepe": "pepe",
}

# CoinGecko id -> ticker symbol, used for the Coinbase spot-price fallback
# and for building good browser-search queries.
COINGECKO_TO_TICKER = {
    "bitcoin": "BTC", "ethereum": "ETH", "binancecoin": "BNB",
    "ripple": "XRP", "cardano": "ADA", "dogecoin": "DOGE",
    "solana": "SOL", "polkadot": "DOT", "matic-network": "MATIC",
    "litecoin": "LTC", "tron": "TRX", "shiba-inu": "SHIB",
    "tether": "USDT", "usd-coin": "USDC", "avalanche-2": "AVAX",
    "chainlink": "LINK", "the-open-network": "TON", "pepe": "PEPE",
}

# Common company name -> ticker (for the stock tool)
STOCK_ALIASES = {
    "apple": "AAPL", "tesla": "TSLA", "google": "GOOGL", "alphabet": "GOOGL",
    "amazon": "AMZN", "microsoft": "MSFT", "meta": "META", "facebook": "META",
    "netflix": "NFLX", "nvidia": "NVDA", "amd": "AMD", "intel": "INTC",
    "coca cola": "KO", "coca-cola": "KO", "disney": "DIS", "boeing": "BA",
    "visa": "V", "mastercard": "MA", "paypal": "PYPL", "uber": "UBER",
    "airbnb": "ABNB", "spotify": "SPOT", "wix": "WIX", "shopify": "SHOP",
}


def _extract_price_from_text(text: str) -> Optional[float]:
    """Pull the first plausible price (e.g. $63,483.12 or 301.32 USD) out of
    free-text — used to read prices out of search-engine snippets and scraped
    pages when no structured API is available."""
    if not text:
        return None
    # $63,483.12  or  $301.32
    m = re.search(r"\$\s?([\d,]{1,12}\.\d{2,8})", text)
    if not m:
        # 63,483.12 USD
        m = re.search(r"([\d,]{1,12}\.\d{2,8})\s*USD", text, re.IGNORECASE)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


async def _usd_to_ngn(usd_amount: float) -> Optional[float]:
    """Best-effort USD->NGN conversion using the free, no-key exchange-rate API
    already used elsewhere in Stew (server/skills_engine.py currency_rates)."""
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get("https://open.er-api.com/v6/latest/USD")
            rate = resp.json().get("rates", {}).get("NGN")
            if rate:
                return round(usd_amount * rate, 2)
    except Exception as e:
        logger.warning(f"USD->NGN conversion failed: {e}")
    return None


async def _resolve_crypto_id(query: str) -> Optional[str]:
    q = query.strip().lower()
    if q in CRYPTO_ALIASES:
        return CRYPTO_ALIASES[q]
    # Fall back to CoinGecko's own search endpoint for anything not aliased
    try:
        async with httpx.AsyncClient(timeout=10, headers=_HEADERS) as client:
            resp = await client.get("https://api.coingecko.com/api/v3/search", params={"query": query})
            data = resp.json()
            coins = data.get("coins", [])
            if coins:
                return coins[0]["id"]
    except Exception as e:
        logger.warning(f"CoinGecko search failed for '{query}': {e}")
    # No network match — if it's a known ticker/name we still recognize it
    # locally (CoinGecko's search endpoint itself can be rate-limited), so
    # fall through to the alias table one more time via ticker matching.
    for alias, coin_id in CRYPTO_ALIASES.items():
        if alias == q or COINGECKO_TO_TICKER.get(coin_id, "").lower() == q:
            return coin_id
    return None


async def _coingecko_price(coin_id: str, vs: str) -> Optional[dict]:
    try:
        vs_list = {vs, "usd", "ngn"}
        async with httpx.AsyncClient(timeout=8, headers=_HEADERS) as client:
            resp = await client.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={
                    "ids": coin_id,
                    "vs_currencies": ",".join(vs_list),
                    "include_24hr_change": "true",
                    "include_market_cap": "true",
                },
            )
            data = resp.json().get(coin_id, {})
            if data.get("usd"):
                return {
                    "price_usd": data.get("usd"),
                    "price_ngn": data.get("ngn"),
                    "price_requested_currency": data.get(vs),
                    "change_24h_pct": round(data.get(f"{vs}_24h_change", data.get("usd_24h_change", 0)) or 0, 2),
                    "market_cap_usd": data.get("usd_market_cap"),
                    "source": "CoinGecko",
                }
    except Exception as e:
        logger.warning(f"CoinGecko price lookup failed for '{coin_id}': {e}")
    return None


async def _coinbase_price(coin_id: str) -> Optional[dict]:
    """Free, no-key Coinbase spot price — used when CoinGecko is unavailable
    (e.g. rate-limited on a shared cloud IP)."""
    ticker = COINGECKO_TO_TICKER.get(coin_id)
    if not ticker:
        return None
    try:
        async with httpx.AsyncClient(timeout=8, headers=_HEADERS) as client:
            resp = await client.get(f"https://api.coinbase.com/v2/prices/{ticker}-USD/spot")
            amount = resp.json().get("data", {}).get("amount")
            if amount:
                price_usd = float(amount)
                price_ngn = await _usd_to_ngn(price_usd)
                return {
                    "price_usd": price_usd,
                    "price_ngn": price_ngn,
                    "price_requested_currency": price_usd,
                    "change_24h_pct": 0,
                    "market_cap_usd": None,
                    "source": "Coinbase",
                }
    except Exception as e:
        logger.warning(f"Coinbase price lookup failed for '{coin_id}': {e}")
    return None


_COINBASE_PRICE_PAGE_RE = re.compile(
    r"\$([\d,]+\.\d+)\s*\n+\s*[↘↗]?\$?([\d,]+\.\d+)\s*\(([+-]?[\d.]+)%\)"
)


async def _browser_crypto_price(coin_id: str, display_symbol: str) -> Optional[dict]:
    """Last-resort, always-free fallback using Stew's own built-in browser
    (server/browser.py). This never needs an API key and never costs money.

    Layer A: fetch coinbase.com's public price page through the browser's
    Jina-reader proxy — this reads from Jina's servers, not Render's IP, so
    it sidesteps any cloud-IP rate-limit/geo-block entirely, and the page
    renders the live price server-side (e.g. "$63,402.50 / -$129.26 (-0.20%)").

    Layer B: if that page's layout ever changes, fall back to a plain
    DuckDuckGo web search and read the price out of a results snippet —
    exactly what a human would do if every API was down.
    """
    try:
        from server.browser import get_browser
        browser = get_browser()

        # Layer A — structured scrape via Jina reader proxy.
        try:
            page = await browser.fetch(f"https://www.coinbase.com/price/{coin_id}")
            content = page.get("content", "") or ""
            m = _COINBASE_PRICE_PAGE_RE.search(content)
            if m:
                price = float(m.group(1).replace(",", ""))
                change_pct = float(m.group(3))
                price_ngn = await _usd_to_ngn(price)
                return {
                    "price_usd": price,
                    "price_ngn": price_ngn,
                    "price_requested_currency": price,
                    "change_24h_pct": change_pct,
                    "market_cap_usd": None,
                    "source": "coinbase.com (via Stew browser)",
                }
        except Exception as e:
            logger.warning(f"Browser coinbase.com scrape failed for '{coin_id}': {e}")

        # Layer B — plain web search snippet reading.
        ticker = COINGECKO_TO_TICKER.get(coin_id, display_symbol)
        result = await browser.search_web_fallback(f"{coin_id} {ticker} price usd")
        for item in result.get("results", [])[:5]:
            price = _extract_price_from_text(item.get("snippet", ""))
            if price:
                price_ngn = await _usd_to_ngn(price)
                return {
                    "price_usd": price,
                    "price_ngn": price_ngn,
                    "price_requested_currency": price,
                    "change_24h_pct": 0,
                    "market_cap_usd": None,
                    "source": f"web search ({item.get('url', 'search')})",
                }
    except Exception as e:
        logger.warning(f"Browser crypto fallback failed for '{coin_id}': {e}")
    return None


async def get_crypto_price(symbol: str, vs_currency: str = "usd") -> dict:
    """Get the live price of a cryptocurrency, e.g. symbol='bitcoin' or 'btc'.
    Tries CoinGecko, then Coinbase, then Stew's own browser — always free."""
    coin_id = await _resolve_crypto_id(symbol)
    if not coin_id:
        return {"error": f"Could not find a cryptocurrency matching '{symbol}'", "symbol": symbol}

    vs = vs_currency.lower()
    result = await _coingecko_price(coin_id, vs)
    if not result:
        result = await _coinbase_price(coin_id)
    if not result:
        result = await _browser_crypto_price(coin_id, symbol)

    if not result:
        return {"error": f"No price data for '{symbol}' right now — all free sources are temporarily unavailable, please try again shortly", "symbol": symbol}

    return {
        "symbol": symbol,
        "coin_id": coin_id,
        "requested_currency": vs_currency.upper(),
        **result,
    }


def _stockanalysis_extract(content: str) -> Optional[dict]:
    """Parse the price block out of a scraped stockanalysis.com page, e.g.:
    'close: Aug 12, 2026, 4:00 PM EDT\\n\\n301.32\\n\\n-0.93 (-0.31%)'"""
    m = re.search(
        r"close:.*?\n\n([\d,]+\.\d+)\n\n([+-]?[\d,]+\.\d+)\s*\(([+-]?[\d.]+)%\)",
        content, re.IGNORECASE,
    )
    if not m:
        return None
    try:
        return {
            "price": float(m.group(1).replace(",", "")),
            "change": float(m.group(2).replace(",", "")),
            "change_pct": float(m.group(3)),
        }
    except ValueError:
        return None


async def _browser_stock_price(ticker: str) -> Optional[dict]:
    """Free fallback for stock prices using Stew's own browser: scrape
    stockanalysis.com first (clean structured price block), then fall back
    to a plain web search if that page layout changes."""
    try:
        from server.browser import get_browser
        browser = get_browser()

        page = await browser.fetch(f"https://stockanalysis.com/stocks/{ticker.lower()}/")
        content = page.get("content", "") or ""
        parsed = _stockanalysis_extract(content)
        if parsed:
            return {
                "symbol": ticker.upper(),
                "name": ticker.upper(),
                "price": parsed["price"],
                "currency": "USD",
                "day_high": None,
                "day_low": None,
                "previous_close": round(parsed["price"] - parsed["change"], 2),
                "fifty_two_week_high": None,
                "fifty_two_week_low": None,
                "exchange": None,
                "source": "stockanalysis.com (via Stew browser)",
            }

        # Page layout fallback — search the web and read the price out of the
        # snippet, same trick used for crypto.
        result = await browser.search_web_fallback(f"{ticker} stock price today USD")
        for item in result.get("results", [])[:5]:
            price = _extract_price_from_text(item.get("snippet", ""))
            if price:
                return {
                    "symbol": ticker.upper(),
                    "name": ticker.upper(),
                    "price": price,
                    "currency": "USD",
                    "day_high": None,
                    "day_low": None,
                    "previous_close": None,
                    "fifty_two_week_high": None,
                    "fifty_two_week_low": None,
                    "exchange": None,
                    "source": f"web search ({item.get('url', 'search')})",
                }
    except Exception as e:
        logger.warning(f"Browser stock fallback failed for '{ticker}': {e}")
    return None


async def get_stock_price(symbol: str) -> dict:
    """Get the latest live/delayed price of a stock. Tries Yahoo Finance's
    free chart API first, then falls back to Stew's own browser (no API key,
    no cost) if Yahoo is rate-limited or unreachable."""
    q = symbol.strip().lower()
    ticker = STOCK_ALIASES.get(q, symbol.strip().upper())
    try:
        async with httpx.AsyncClient(timeout=8, headers=_HEADERS) as client:
            resp = await client.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
                params={"interval": "1d", "range": "1d"},
            )
            data = resp.json()
            result = (data.get("chart", {}).get("result") or [None])[0]
            if result:
                meta = result.get("meta", {})
                if meta.get("regularMarketPrice"):
                    return {
                        "symbol": meta.get("symbol", ticker),
                        "name": meta.get("longName") or meta.get("shortName") or ticker,
                        "price": meta.get("regularMarketPrice"),
                        "currency": meta.get("currency", "USD"),
                        "day_high": meta.get("regularMarketDayHigh"),
                        "day_low": meta.get("regularMarketDayLow"),
                        "previous_close": meta.get("chartPreviousClose"),
                        "fifty_two_week_high": meta.get("fiftyTwoWeekHigh"),
                        "fifty_two_week_low": meta.get("fiftyTwoWeekLow"),
                        "exchange": meta.get("fullExchangeName"),
                        "source": "Yahoo Finance",
                    }
    except Exception as e:
        logger.warning(f"Yahoo Finance lookup failed for '{ticker}': {e}")

    # Yahoo failed or returned nothing usable — fall back to Stew's own browser.
    fallback = await _browser_stock_price(ticker)
    if fallback:
        return fallback

    return {
        "error": f"Could not find stock '{symbol}' right now. Try the exact ticker (e.g. AAPL, TSLA, WIX) — all free sources are temporarily unavailable.",
        "symbol": symbol,
    }
