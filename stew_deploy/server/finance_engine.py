"""
S.T.E.W. Finance Engine
Real-time stock prices (Yahoo Finance), forex rates (Frankfurter),
crypto prices (CoinGecko), and trading signals (RSI, MACD, SMA, EMA, Bollinger).
No API keys required for any source.
"""

import logging
import httpx
import math
from typing import Optional
from datetime import datetime

logger = logging.getLogger("stew.finance")

YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
FRANKFURTER_URL = "https://api.frankfurter.dev"
COINGECKO_URL = "https://api.coingecko.com/api/v3"


class FinanceEngine:
    """Free real-time market data for stocks, forex, crypto, and trading signals."""

    def __init__(self):
        self._client = httpx.Client(
            timeout=20,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; STEW-Agent/1.0)"}
        )

    def get_stock(self, symbol: str) -> dict:
        try:
            symbol = symbol.upper().strip()
            resp = self._client.get(
                YAHOO_CHART_URL.format(symbol=symbol),
                params={"range": "1d", "interval": "1m"}
            )
            if resp.status_code != 200:
                return {"success": False, "error": f"Yahoo Finance returned {resp.status_code}", "symbol": symbol}

            data = resp.json()
            result = data.get("chart", {}).get("result", [{}])[0]
            meta = result.get("meta", {})

            price = meta.get("regularMarketPrice", 0)
            prev_close = meta.get("previousClose", 0)
            change = price - prev_close if prev_close else 0
            change_pct = (change / prev_close * 100) if prev_close else 0

            return {
                "success": True,
                "symbol": symbol,
                "name": meta.get("symbol", symbol),
                "currency": meta.get("currency", "USD"),
                "price": round(price, 2),
                "previous_close": round(prev_close, 2),
                "change": round(change, 2),
                "change_percent": round(change_pct, 2),
                "day_high": round(meta.get("regularMarketDayHigh", 0), 2),
                "day_low": round(meta.get("regularMarketDayLow", 0), 2),
                "volume": meta.get("regularMarketVolume", 0),
                "market_state": meta.get("marketState", "UNKNOWN"),
            }
        except Exception as e:
            logger.warning(f"Stock fetch failed for {symbol}: {e}")
            return {"success": False, "error": str(e), "symbol": symbol}

    def get_stock_history(self, symbol: str, range: str = "3mo", interval: str = "1d") -> dict:
        try:
            symbol = symbol.upper().strip()
            resp = self._client.get(
                YAHOO_CHART_URL.format(symbol=symbol),
                params={"range": range, "interval": interval}
            )
            if resp.status_code != 200:
                return {"success": False, "error": f"Yahoo returned {resp.status_code}"}

            data = resp.json()
            result = data.get("chart", {}).get("result", [{}])[0]
            timestamps = result.get("timestamp", [])
            indicators = result.get("indicators", {})
            quote = indicators.get("quote", [{}])[0]

            closes = quote.get("close", [])
            highs = quote.get("high", [])
            lows = quote.get("low", [])
            volumes = quote.get("volume", [])
            opens = quote.get("open", [])

            history = []
            for i in range(len(closes)):
                if closes[i] is not None:
                    history.append({
                        "timestamp": timestamps[i] if i < len(timestamps) else 0,
                        "open": opens[i] if i < len(opens) else None,
                        "high": highs[i] if i < len(highs) else None,
                        "low": lows[i] if i < len(lows) else None,
                        "close": closes[i],
                        "volume": volumes[i] if i < len(volumes) else 0,
                    })

            return {"success": True, "symbol": symbol, "history": history, "count": len(history)}
        except Exception as e:
            logger.warning(f"Stock history failed for {symbol}: {e}")
            return {"success": False, "error": str(e)}

    def get_forex(self, base: str = "USD", target: str = "NGN") -> dict:
        try:
            base = base.upper().strip()
            target = target.upper().strip()
            resp = self._client.get(
                f"{FRANKFURTER_URL}/latest",
                params={"from": base, "to": target}
            )
            if resp.status_code != 200:
                return {"success": False, "error": f"Frankfurter returned {resp.status_code}"}

            data = resp.json()
            rate = data.get("rates", {}).get(target, 0)
            date = data.get("date", "")

            return {"success": True, "base": base, "target": target, "rate": round(rate, 4), "date": date}
        except Exception as e:
            logger.warning(f"Forex fetch failed: {e}")
            return {"success": False, "error": str(e)}

    def get_crypto(self, coin: str = "bitcoin", vs: str = "usd") -> dict:
        try:
            resp = self._client.get(
                f"{COINGECKO_URL}/simple/price",
                params={"ids": coin, "vs_currencies": vs, "include_24hr_change": "true",
                         "include_24hr_vol": "true", "include_market_cap": "true"}
            )
            if resp.status_code != 200:
                return {"success": False, "error": f"CoinGecko returned {resp.status_code}"}

            data = resp.json()
            coin_data = data.get(coin, {})
            price = coin_data.get(vs, 0)
            change_24h = coin_data.get(f"{vs}_24h_change", 0)
            vol_24h = coin_data.get(f"{vs}_24h_vol", 0)
            mcap = coin_data.get(f"{vs}_market_cap", 0)

            return {
                "success": True, "coin": coin, "vs": vs.upper(),
                "price": round(price, 2) if price < 1000 else round(price, 0),
                "change_24h": round(change_24h, 2),
                "volume_24h": round(vol_24h, 0),
                "market_cap": round(mcap, 0),
            }
        except Exception as e:
            logger.warning(f"Crypto fetch failed for {coin}: {e}")
            return {"success": False, "error": str(e)}

    @staticmethod
    def _sma(prices: list, period: int) -> Optional[float]:
        if len(prices) < period: return None
        return sum(prices[-period:]) / period

    @staticmethod
    def _ema(prices: list, period: int) -> Optional[float]:
        if len(prices) < period: return None
        multiplier = 2 / (period + 1)
        ema = sum(prices[:period]) / period
        for price in prices[period:]:
            ema = (price - ema) * multiplier + ema
        return ema

    @staticmethod
    def _rsi(prices: list, period: int = 14) -> Optional[float]:
        if len(prices) < period + 1: return None
        gains, losses = [], []
        for i in range(1, len(prices)):
            change = prices[i] - prices[i-1]
            gains.append(max(0, change))
            losses.append(max(0, -change))
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period
        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0: return 100.0
        rs = avg_gain / avg_loss
        return round(100 - (100 / (1 + rs)), 2)

    @staticmethod
    def _macd(prices: list) -> dict:
        ema12 = FinanceEngine._ema(prices, 12)
        ema26 = FinanceEngine._ema(prices, 26)
        if ema12 is None or ema26 is None:
            return {"macd": None, "signal": None, "histogram": None}
        macd_line = ema12 - ema26
        macd_values = []
        for i in range(max(0, len(prices) - 30), len(prices)):
            e12 = FinanceEngine._ema(prices[:i+1], 12)
            e26 = FinanceEngine._ema(prices[:i+1], 26)
            if e12 and e26: macd_values.append(e12 - e26)
        signal = FinanceEngine._ema(macd_values, 9) if len(macd_values) >= 9 else None
        histogram = macd_line - signal if signal else None
        return {
            "macd": round(macd_line, 4) if macd_line else None,
            "signal": round(signal, 4) if signal else None,
            "histogram": round(histogram, 4) if histogram else None,
        }

    @staticmethod
    def _bollinger(prices: list, period: int = 20) -> dict:
        if len(prices) < period:
            return {"upper": None, "middle": None, "lower": None}
        sma = sum(prices[-period:]) / period
        variance = sum((p - sma) ** 2 for p in prices[-period:]) / period
        std = math.sqrt(variance)
        return {"upper": round(sma + 2 * std, 2), "middle": round(sma, 2), "lower": round(sma - 2 * std, 2)}

    @staticmethod
    def _support_resistance(prices: list, lookback: int = 20) -> dict:
        if len(prices) < lookback:
            return {"support": None, "resistance": None}
        recent = prices[-lookback:]
        return {"support": round(min(recent), 2), "resistance": round(max(recent), 2)}

    def get_trading_signals(self, symbol: str) -> dict:
        try:
            symbol = symbol.upper().strip()
            history = self.get_stock_history(symbol, range="6mo", interval="1d")
            if not history["success"] or len(history["history"]) < 30:
                return {"success": False, "error": f"Insufficient historical data for {symbol}"}

            prices = [h["close"] for h in history["history"] if h["close"] is not None]
            if len(prices) < 30:
                return {"success": False, "error": "Not enough price data for analysis"}

            current_price = prices[-1]
            rsi = self._rsi(prices)
            macd = self._macd(prices)
            sma20 = self._sma(prices, 20)
            sma50 = self._sma(prices, 50)
            sma200 = self._sma(prices, 200)
            ema12 = self._ema(prices, 12)
            ema26 = self._ema(prices, 26)
            bollinger = self._bollinger(prices)
            sr = self._support_resistance(prices)

            score = 0
            signals = []

            if rsi is not None:
                if rsi < 30: score += 2; signals.append(f"RSI {rsi} - Oversold (Bullish)")
                elif rsi < 45: score += 1; signals.append(f"RSI {rsi} - Bearish but approaching oversold")
                elif rsi > 70: score -= 2; signals.append(f"RSI {rsi} - Overbought (Bearish)")
                elif rsi > 55: score -= 1; signals.append(f"RSI {rsi} - Bullish but approaching overbought")
                else: signals.append(f"RSI {rsi} - Neutral")

            if macd["macd"] and macd["signal"]:
                if macd["macd"] > macd["signal"]:
                    if macd["histogram"] and macd["histogram"] > 0:
                        score += 1; signals.append("MACD - Bullish crossover (Buy signal)")
                    else: signals.append("MACD - Above signal line")
                else:
                    if macd["histogram"] and macd["histogram"] < 0:
                        score -= 1; signals.append("MACD - Bearish crossover (Sell signal)")
                    else: signals.append("MACD - Below signal line")

            if sma20 and sma50:
                if current_price > sma20 > sma50: score += 1; signals.append("Price above SMA20 above SMA50 - Uptrend")
                elif current_price < sma20 < sma50: score -= 1; signals.append("Price below SMA20 below SMA50 - Downtrend")
                else: signals.append("SMA20/50 - Mixed signals")

            if bollinger["upper"] and bollinger["lower"]:
                if current_price <= bollinger["lower"]: score += 1; signals.append("Price near lower Bollinger Band - Potential bounce")
                elif current_price >= bollinger["upper"]: score -= 1; signals.append("Price near upper Bollinger Band - Potential reversal")

            if sr["support"] and sr["resistance"]:
                if current_price <= sr["support"] * 1.02: score += 1; signals.append(f"Near support level ({sr['support']})")
                elif current_price >= sr["resistance"] * 0.98: score -= 1; signals.append(f"Near resistance level ({sr['resistance']})")

            if score >= 3: action = "STRONG BUY"; emoji = "🟢🟢"
            elif score >= 1: action = "BUY"; emoji = "🟢"
            elif score <= -3: action = "STRONG SELL"; emoji = "🔴🔴"
            elif score <= -1: action = "SELL"; emoji = "🔴"
            else: action = "NEUTRAL / HOLD"; emoji = "🟡"

            return {
                "success": True, "symbol": symbol, "current_price": round(current_price, 2),
                "signal": action, "emoji": emoji, "score": score,
                "indicators": {
                    "rsi": rsi, "macd": macd,
                    "sma_20": round(sma20, 2) if sma20 else None,
                    "sma_50": round(sma50, 2) if sma50 else None,
                    "sma_200": round(sma200, 2) if sma200 else None,
                    "ema_12": round(ema12, 2) if ema12 else None,
                    "ema_26": round(ema26, 2) if ema26 else None,
                    "bollinger": bollinger,
                    "support": sr["support"], "resistance": sr["resistance"],
                },
                "signals": signals,
            }
        except Exception as e:
            logger.warning(f"Trading signals failed for {symbol}: {e}")
            return {"success": False, "error": str(e)}

    def close(self):
        self._client.close()


_finance_engine: Optional[FinanceEngine] = None

def get_finance_engine() -> FinanceEngine:
    global _finance_engine
    if _finance_engine is None:
        _finance_engine = FinanceEngine()
    return _finance_engine
