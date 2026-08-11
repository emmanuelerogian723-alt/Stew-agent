"""
S.T.E.W Web Search — Multi-fallback search engine.

Architecture (inspired by Serper/Firecrawl):
1. Serper API (if key configured) — Google results, fastest
2. DuckDuckGo HTML — primary FREE fallback, no key needed, works from cloud IPs
3. DuckDuckGo Lite — secondary FREE fallback (different endpoint, less likely to be rate-limited)
4. Brave Search API (if key configured) — independent index, free $5/mo tier
5. Jina AI s.jina.ai (if key configured) — search + page content, 10M free tokens

Anti-hallucination: NEVER return fabricated results. If all backends fail, return empty.
"""
import logging
import os
import re
import urllib.parse
from datetime import datetime, timezone
from typing import Optional

import requests
from bs4 import BeautifulSoup
from fastapi import HTTPException

from server.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# Common browser headers to avoid bot detection
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

DDG_REFERER = {"Referer": "https://duckduckgo.com/"}


class WebSearch:
    def __init__(self):
        self.api_key = settings.SERPER_API_KEY
        self.base_url = "https://google.serper.dev/search"
        self.news_url = "https://google.serper.dev/news"
        self.brave_key = os.environ.get("BRAVE_SEARCH_API_KEY", "")
        self.jina_key = os.environ.get("JINA_API_KEY", os.environ.get("JINA_SEARCH_KEY", ""))

    def _is_available(self) -> bool:
        return bool(self.api_key or True)  # DuckDuckGo fallback always available

    def search(self, query: str, num_results: int = 8) -> dict:
        """
        Perform a real web search via multiple fallback providers.
        Returns structured results with source URLs.
        NEVER fabricates results.
        """
        # Try 1: Serper API (if configured)
        if self.api_key:
            result = self._serper_search(query, num_results)
            if result.get("organic"):
                return result

        # Try 2: DuckDuckGo HTML (free, no key — primary free backend)
        result = self._duckduckgo_html_search(query, num_results)
        if result.get("organic"):
            return result

        # Try 2b: DuckDuckGo via allorigins proxy (for blocked IPs like Render free tier)
        result = self._duckduckgo_proxy_search(query, num_results)
        if result.get("organic"):
            return result

        # Try 3: DuckDuckGo Lite (different endpoint, free)
        result = self._duckduckgo_lite_search(query, num_results)
        if result.get("organic"):
            return result

        # Try 4: Brave Search API (if configured)
        if self.brave_key:
            result = self._brave_search(query, num_results)
            if result.get("organic"):
                return result

        # Try 5: Jina AI search (if configured)
        if self.jina_key:
            result = self._jina_search(query, num_results)
            if result.get("organic"):
                return result

        # All failed
        logger.error(f"All search backends failed for: {query}")
        return {
            "organic": [],
            "answer_box": {},
            "knowledge_graph": {},
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "query": query,
            "grounded": False,
            "error": "All search backends failed. Try again later.",
        }

    def news_search(self, query: str, num_results: int = 5) -> dict:
        """Search for recent news articles."""
        # Try Serper news
        if self.api_key:
            headers = {"X-API-KEY": self.api_key, "Content-Type": "application/json"}
            payload = {"q": query, "num": num_results}
            try:
                resp = requests.post(self.news_url, json=payload, headers=headers, timeout=15)
                resp.raise_for_status()
                data = resp.json()
                articles = [
                    {
                        "title": a.get("title", ""),
                        "link": a.get("link", ""),
                        "snippet": a.get("snippet", ""),
                        "date": a.get("date", ""),
                        "source": a.get("source", ""),
                    }
                    for a in data.get("news", [])
                ]
                if articles:
                    return {
                        "articles": articles,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "query": query,
                        "grounded": True,
                    }
            except Exception as e:
                logger.warning(f"Serper news error: {e}")

        # Fallback: DuckDuckGo HTML with news bias (add "news" to query)
        result = self._duckduckgo_html_search(f"{query} news", num_results)
        if result.get("organic"):
            articles = [
                {
                    "title": r.get("title", ""),
                    "link": r.get("link", ""),
                    "snippet": r.get("snippet", ""),
                    "date": "",
                    "source": "",
                }
                for r in result["organic"]
            ]
            return {
                "articles": articles,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "query": query,
                "grounded": True,
            }

        return {
            "articles": [],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "query": query,
            "grounded": False,
            "error": "News search not available",
        }

    # ── Serper (Google) ──────────────────────────────────────────────

    def _serper_search(self, query: str, num_results: int) -> dict:
        headers = {"X-API-KEY": self.api_key, "Content-Type": "application/json"}
        payload = {"q": query, "num": num_results}
        try:
            resp = requests.post(self.base_url, json=payload, headers=headers, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            organic = [
                {
                    "title": r.get("title", ""),
                    "link": r.get("link", ""),
                    "snippet": r.get("snippet", ""),
                    "position": r.get("position", idx + 1),
                }
                for idx, r in enumerate(data.get("organic", []))
            ]
            return {
                "organic": organic,
                "answer_box": data.get("answerBox", {}),
                "knowledge_graph": data.get("knowledgeGraph", {}),
                "related_searches": [r.get("query", "") for r in data.get("relatedSearches", [])],
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "query": query,
                "grounded": True,
                "source": "serper",
            }
        except Exception as e:
            logger.warning(f"Serper API error: {e}")
            return {"organic": []}

    # ── DuckDuckGo HTML ──────────────────────────────────────────────

    def _duckduckgo_html_search(self, query: str, num_results: int) -> dict:
        """Search via DuckDuckGo HTML endpoint — free, no API key, reliable from cloud IPs."""
        try:
            resp = requests.post(
                "https://html.duckduckgo.com/html/",
                data={"q": query},
                headers={**BROWSER_HEADERS, **DDG_REFERER},
                timeout=12,
                allow_redirects=True,
            )
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            results = []
            for r in soup.select(".result__body")[:num_results]:
                title_tag = r.select_one(".result__title a")
                snippet_tag = r.select_one(".result__snippet")
                if not title_tag:
                    continue
                link = title_tag.get("href", "")
                # DuckDuckGo wraps links in a redirect
                if "uddg=" in link:
                    link = urllib.parse.unquote(link.split("uddg=")[-1].split("&")[0])
                elif link.startswith("//"):
                    link = "https:" + link
                title = title_tag.get_text(strip=True)
                snippet = snippet_tag.get_text(strip=True) if snippet_tag else ""
                if title and link:
                    results.append({
                        "title": title,
                        "link": link,
                        "snippet": snippet,
                        "position": len(results) + 1,
                    })
            logger.info(f"DuckDuckGo HTML returned {len(results)} results for: {query}")
            return {
                "organic": results,
                "answer_box": {},
                "knowledge_graph": {},
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "query": query,
                "grounded": len(results) > 0,
                "source": "duckduckgo_html",
            }
        except Exception as e:
            logger.warning(f"DuckDuckGo HTML error: {e}")
            return {"organic": []}

    # ── DuckDuckGo via Allorigins Proxy (for blocked IPs like Render) ──

    def _duckduckgo_proxy_search(self, query: str, num_results: int) -> dict:
        """Search via DuckDuckGo HTML through allorigins.win proxy.
        Works from datacenter IPs that DuckDuckGo blocks directly (e.g. Render free tier)."""
        import urllib.parse as _up
        try:
            ddg_url = f"https://html.duckduckgo.com/html/?q={_up.quote(query)}"
            proxy_url = f"https://api.allorigins.win/raw?url={_up.quote(ddg_url)}"
            resp = requests.get(proxy_url, timeout=15, headers={**BROWSER_HEADERS, **DDG_REFERER})
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            results = []
            for r in soup.select(".result__body")[:num_results]:
                title_tag = r.select_one(".result__title a")
                snippet_tag = r.select_one(".result__snippet")
                if not title_tag:
                    continue
                link = title_tag.get("href", "")
                if "uddg=" in link:
                    link = _up.unquote(link.split("uddg=")[-1].split("&")[0])
                elif link.startswith("//"):
                    link = "https:" + link
                title = title_tag.get_text(strip=True)
                snippet = snippet_tag.get_text(strip=True) if snippet_tag else ""
                if title and link:
                    results.append({
                        "title": title,
                        "link": link,
                        "snippet": snippet,
                        "position": len(results) + 1,
                    })
            logger.info(f"DuckDuckGo proxy returned {len(results)} results for: {query}")
            return {
                "organic": results,
                "answer_box": {},
                "knowledge_graph": {},
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "query": query,
                "grounded": len(results) > 0,
                "source": "duckduckgo_proxy",
            }
        except Exception as e:
            logger.warning(f"DuckDuckGo proxy error: {e}")
            return {"organic": []}

    # ── DuckDuckGo Lite ─────────────────────────────────────────────

    def _duckduckgo_lite_search(self, query: str, num_results: int) -> dict:
        """Search via DuckDuckGo Lite — simpler HTML, different endpoint, less rate-limited."""
        try:
            resp = requests.get(
                "https://lite.duckduckgo.com/lite/",
                params={"q": query, "kl": "us-en"},
                headers={**BROWSER_HEADERS, **DDG_REFERER},
                timeout=12,
            )
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            results = []
            # Lite uses table-based layout — find result links
            for a in soup.select("a.result-link, a[target='_self']"):
                href = a.get("href", "")
                title = a.get_text(strip=True)
                if not title or not href or "duckduckgo.com" in href:
                    continue
                if href.startswith("//"):
                    href = "https:" + href
                # Find snippet in adjacent cell
                snippet = ""
                parent_tr = a.find_parent("tr")
                if parent_tr:
                    next_tr = parent_tr.find_next_sibling("tr")
                    if next_tr:
                        snippet = next_tr.get_text(strip=True)[:200]
                results.append({
                    "title": title,
                    "link": href,
                    "snippet": snippet,
                    "position": len(results) + 1,
                })
                if len(results) >= num_results:
                    break
            logger.info(f"DuckDuckGo Lite returned {len(results)} results for: {query}")
            return {
                "organic": results,
                "answer_box": {},
                "knowledge_graph": {},
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "query": query,
                "grounded": len(results) > 0,
                "source": "duckduckgo_lite",
            }
        except Exception as e:
            logger.warning(f"DuckDuckGo Lite error: {e}")
            return {"organic": []}

    # ── Brave Search API ─────────────────────────────────────────────

    def _brave_search(self, query: str, num_results: int) -> dict:
        """Search via Brave Search API — independent index, free $5/mo tier."""
        try:
            resp = requests.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": min(num_results, 20)},
                headers={
                    "Accept": "application/json",
                    "Accept-Encoding": "gzip",
                    "X-Subscription-Token": self.brave_key,
                },
                timeout=12,
            )
            resp.raise_for_status()
            data = resp.json()
            results = []
            for idx, r in enumerate(data.get("results", [])[:num_results]):
                results.append({
                    "title": r.get("title", ""),
                    "link": r.get("url", ""),
                    "snippet": r.get("description", ""),
                    "position": idx + 1,
                })
            logger.info(f"Brave Search returned {len(results)} results for: {query}")
            return {
                "organic": results,
                "answer_box": {},
                "knowledge_graph": {},
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "query": query,
                "grounded": len(results) > 0,
                "source": "brave",
            }
        except Exception as e:
            logger.warning(f"Brave Search error: {e}")
            return {"organic": []}

    # ── Jina AI Search ──────────────────────────────────────────────

    def _jina_search(self, query: str, num_results: int) -> dict:
        """Search via Jina AI s.jina.ai — returns SERP with page content, 10M free tokens."""
        try:
            encoded_q = urllib.parse.quote(query)
            resp = requests.get(
                f"https://s.jina.ai/{encoded_q}",
                headers={
                    "Authorization": f"Bearer {self.jina_key}",
                    "Accept": "application/json",
                    "X-Retain-Images": "none",
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            results = []
            for idx, item in enumerate(data.get("data", [])[:num_results]):
                results.append({
                    "title": item.get("title", ""),
                    "link": item.get("url", ""),
                    "snippet": item.get("content", "")[:300],
                    "position": idx + 1,
                })
            logger.info(f"Jina AI search returned {len(results)} results for: {query}")
            return {
                "organic": results,
                "answer_box": {},
                "knowledge_graph": {},
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "query": query,
                "grounded": len(results) > 0,
                "source": "jina",
            }
        except Exception as e:
            logger.warning(f"Jina AI search error: {e}")
            return {"organic": []}

    # ── Extension search (kept for backwards compat) ─────────────────

    def stew_extension_search(self, query: str, num_results: int = 8) -> dict:
        """
        Search via DuckDuckGo + optional Serper (extension proxy removed).
        """
        return self.search(query, num_results)


# Singleton
web_search = WebSearch()
