"""
S.T.E.W Market Data — live cryptocurrency and stock price lookups.
No API keys required: CoinGecko (crypto, free) + Yahoo Finance chart API
(stocks, free, no key). Gives reliable, structured real-time prices instead
of relying on generic web search (which was flaky/unreliable for prices).
"""
import logging
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

# Common company name -> ticker (for the stock tool)
STOCK_ALIASES = {
    "apple": "AAPL", "tesla": "TSLA", "google": "GOOGL", "alphabet": "GOOGL",
    "amazon": "AMZN", "microsoft": "MSFT", "meta": "META", "facebook": "META",
    "netflix": "NFLX", "nvidia": "NVDA", "amd": "AMD", "intel": "INTC",
    "coca cola": "KO", "coca-cola": "KO", "disney": "DIS", "boeing": "BA",
    "visa": "V", "mastercard": "MA", "paypal": "PYPL", "uber": "UBER",
    "airbnb": "ABNB", "spotify": "SPOT", "wix": "WIX", "shopify": "SHOP",
}


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
    return None


async def get_crypto_price(symbol: str, vs_currency: str = "usd") -> dict:
    """Get the live price of a cryptocurrency, e.g. symbol='bitcoin' or 'btc'."""
    coin_id = await _resolve_crypto_id(symbol)
    if not coin_id:
        return {"error": f"Could not find a cryptocurrency matching '{symbol}'", "symbol": symbol}
    try:
        vs = vs_currency.lower()
        vs_list = {vs, "usd", "ngn"}
        async with httpx.AsyncClient(timeout=10, headers=_HEADERS) as client:
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
            if not data:
                return {"error": f"No price data for '{symbol}'", "symbol": symbol}
            return {
                "symbol": symbol,
                "coin_id": coin_id,
                "price_usd": data.get("usd"),
                "price_ngn": data.get("ngn"),
                "price_requested_currency": data.get(vs),
                "requested_currency": vs_currency.upper(),
                "change_24h_pct": round(data.get(f"{vs}_24h_change", data.get("usd_24h_change", 0)) or 0, 2),
                "market_cap_usd": data.get("usd_market_cap"),
                "source": "CoinGecko",
            }
    except Exception as e:
        return {"error": str(e), "symbol": symbol}


async def get_stock_price(symbol: str) -> dict:
    """Get the latest live/delayed price of a stock via Yahoo Finance's public
    chart API (no key required), e.g. symbol='AAPL' or 'apple'."""
    q = symbol.strip().lower()
    ticker = STOCK_ALIASES.get(q, symbol.strip().upper())
    try:
        async with httpx.AsyncClient(timeout=10, headers=_HEADERS) as client:
            resp = await client.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
                params={"interval": "1d", "range": "1d"},
            )
            data = resp.json()
            result = (data.get("chart", {}).get("result") or [None])[0]
            if not result:
                # Yahoo returns an "error" object when the symbol is invalid
                err = data.get("chart", {}).get("error", {})
                return {
                    "error": f"Could not find stock '{symbol}'. {err.get('description', 'Try the exact ticker, e.g. AAPL, TSLA, WIX.')}",
                    "symbol": symbol,
                }
            meta = result.get("meta", {})
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
        return {"error": str(e), "symbol": symbol}
