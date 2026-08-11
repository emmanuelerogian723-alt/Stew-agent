"""
S.T.E.W Web Search — REAL Serper API calls only.
Anti-hallucination: NEVER return fabricated results.
"""
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import requests
from fastapi import HTTPException

from server.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class WebSearch:
    def __init__(self):
        self.api_key = settings.SERPER_API_KEY
        self.base_url = "https://google.serper.dev/search"
        self.news_url = "https://google.serper.dev/news"

    def _is_available(self) -> bool:
        return bool(self.api_key)

    def search(self, query: str, num_results: int = 5) -> dict:
        """
        Perform a real web search via Serper API.
        Returns structured results with source URLs.
        NEVER fabricates results.
        """
        if not self._is_available():
            logger.warning("SERPER_API_KEY not set — trying SearXNG")
            return self._searxng_fallback(query, num_results)

        headers = {
            "X-API-KEY": self.api_key,
            "Content-Type": "application/json",
        }
        payload = {"q": query, "num": num_results}

        try:
            resp = requests.post(
                self.base_url, json=payload, headers=headers, timeout=15
            )
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
                "related_searches": [
                    r.get("query", "") for r in data.get("relatedSearches", [])
                ],
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "query": query,
                "grounded": True,
            }

        except requests.Timeout:
            logger.error("Serper API timeout, falling back to SearXNG")
            return self._searxng_fallback(query, num_results)
        except requests.HTTPError as e:
            logger.warning(f"Serper API HTTP error: {e}, falling back to SearXNG")
            return self._searxng_fallback(query, num_results)
        except Exception as e:
            logger.warning(f"Serper API error: {e}, falling back to SearXNG")
            return self._searxng_fallback(query, num_results)

    def news_search(self, query: str, num_results: int = 5) -> dict:
        """Search for recent news articles."""
        if not self._is_available():
            return {
                "articles": [],
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "query": query,
                "grounded": False,
                "error": "Web search not available",
            }

        headers = {
            "X-API-KEY": self.api_key,
            "Content-Type": "application/json",
        }
        payload = {"q": query, "num": num_results}

        try:
            resp = requests.post(
                self.news_url, json=payload, headers=headers, timeout=15
            )
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

            return {
                "articles": articles,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "query": query,
                "grounded": True,
            }

        except Exception as e:
            logger.error(f"News search error: {e}")
            return {
                "articles": [],
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "query": query,
                "grounded": False,
                "error": str(e),
            }



    def _searxng_fallback(self, query: str, num_results: int = 5) -> dict:
        """Fallback search via SearXNG public instances (no API key needed)."""
        searxng_instances = [
            "https://searx.be/search",
            "https://search.mdosch.de/search",
            "https://searx.tiekoetter.com/search",
        ]
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "Accept": "application/json",
        }
        for base_url in searxng_instances:
            try:
                resp = requests.get(
                    base_url,
                    params={"q": query, "format": "json", "categories": "general", "pageno": 1},
                    headers=headers,
                    timeout=12,
                )
                resp.raise_for_status()
                data = resp.json()
                organic = []
                for idx, r in enumerate(data.get("results", [])[:num_results]):
                    organic.append({
                        "title": r.get("title", ""),
                        "link": r.get("url", r.get("link", "")),
                        "snippet": r.get("content", r.get("snippet", "")),
                        "position": idx + 1,
                    })
                if organic:
                    logger.info(f"SearXNG ({base_url}) returned {len(organic)} results for: {query}")
                    return {
                        "organic": organic,
                        "answer_box": {},
                        "knowledge_graph": {},
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "query": query,
                        "grounded": True,
                        "source": f"searxng:{base_url.split('//')[1].split('/')[0]}",
                    }
            except Exception as e:
                logger.warning(f"SearXNG instance {base_url} failed: {e}")
                continue

        # All SearXNG instances failed, try DuckDuckGo
        logger.warning("All SearXNG instances failed, falling back to DuckDuckGo")
        return self._duckduckgo_fallback(query, num_results)

    def _duckduckgo_fallback(self, query: str, num_results: int = 5) -> dict:
        """Fallback search using DuckDuckGo HTML (no API key needed)."""
        try:
            import urllib.parse
            resp = requests.post(
                "https://html.duckduckgo.com/html/",
                data={"q": query},
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"},
                timeout=15,
            )
            resp.raise_for_status()
            
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(resp.text, "html.parser")
            results = []
            for r in soup.select(".result__body")[:num_results]:
                title_tag = r.select_one(".result__title a")
                snippet_tag = r.select_one(".result__snippet")
                if title_tag:
                    link = title_tag.get("href", "")
                    # DuckDuckGo wraps links in a redirect
                    if "uddg=" in link:
                        link = urllib.parse.unquote(link.split("uddg=")[-1].split("&")[0])
                    results.append({
                        "title": title_tag.get_text(strip=True),
                        "link": link,
                        "snippet": snippet_tag.get_text(strip=True) if snippet_tag else "",
                        "position": len(results) + 1,
                    })
            
            logger.info(f"DuckDuckGo fallback returned {len(results)} results for: {query}")
            return {
                "organic": results,
                "answer_box": {},
                "knowledge_graph": {},
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "query": query,
                "grounded": len(results) > 0,
                "source": "duckduckgo_fallback",
            }
        except Exception as e:
            logger.error(f"DuckDuckGo fallback also failed: {e}")
            return {
                "organic": [],
                "answer_box": {},
                "knowledge_graph": {},
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "query": query,
                "grounded": False,
                "error": f"Both Serper and DuckDuckGo failed: {e}",
            }


    def stew_extension_search(self, query: str, num_results: int = 8) -> dict:
        """
        Search via the S.T.E.W Browser Extension (Vercel, free, no API key).
        Uses DuckDuckGo + Wikipedia + SearXNG + page content extraction.
        """
        ext_url = os.getenv("STEW_BROWSER_EXTENSION_URL", "https://stew-browser-extension.vercel.app")
        try:
            resp = requests.get(
                f"{ext_url}/api/search",
                params={"q": query, "depth": 2, "fetch": "false"},
                headers={"Accept": "application/json"},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()

            organic = [
                {
                    "title": r.get("title", ""),
                    "link": r.get("url", ""),
                    "snippet": r.get("snippet", ""),
                    "position": idx + 1,
                }
                for idx, r in enumerate(data.get("results", [])[:num_results])
            ]

            return {
                "organic": organic,
                "answer_box": {},
                "knowledge_graph": {},
                "pages": data.get("pages", []),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "query": query,
                "grounded": len(organic) > 0,
                "source": "stew_browser_extension",
            }
        except Exception as e:
            logger.warning(f"Stew Browser Extension search failed: {e}")
            return {
                "organic": [],
                "answer_box": {},
                "knowledge_graph": {},
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "query": query,
                "grounded": False,
                "error": str(e),
            }

    def stew_extension_research(self, query: str, depth: int = 3) -> dict:
        """
        Deep research via the S.T.E.W Browser Extension.
        Fetches pages, extracts content, and returns comprehensive results.
        """
        ext_url = os.getenv("STEW_BROWSER_EXTENSION_URL", "https://stew-browser-extension.vercel.app")
        try:
            resp = requests.post(
                f"{ext_url}/api/research",
                json={"query": query, "depth": depth},
                headers={"Content-Type": "application/json"},
                timeout=25,
            )
            resp.raise_for_status()
            data = resp.json()

            return {
                "report": data.get("report", ""),
                "organic": [
                    {"title": s.get("title", ""), "link": s.get("url", ""), "snippet": ""}
                    for s in data.get("sources", [])
                ],
                "pages": data.get("pages", []),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "query": query,
                "grounded": data.get("grounded", False),
                "source": "stew_browser_extension_research",
                "queries_used": data.get("queries_used", []),
                "total_results": data.get("total_results", 0),
            }
        except Exception as e:
            logger.warning(f"Stew Browser Extension research failed: {e}")
            return {
                "report": "",
                "organic": [],
                "pages": [],
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "query": query,
                "grounded": False,
                "error": str(e),
            }


    def format_results_for_llm(self, results: dict) -> str:
        """Format search results as context for the LLM prompt."""
        if not results.get("grounded"):
            return ""

        lines = [f"[Web Search Results for: '{results['query']}']",
                 f"Timestamp: {results['timestamp']}", ""]

        ab = results.get("answer_box", {})
        if ab:
            lines.append(f"Answer Box: {ab.get('answer') or ab.get('snippet', '')}")
            lines.append("")

        for i, r in enumerate(results.get("organic", []), 1):
            lines.append(f"{i}. {r['title']}")
            lines.append(f"   URL: {r['link']}")
            lines.append(f"   {r['snippet']}")
            lines.append("")

        return "\n".join(lines)


# Singleton
_searcher: Optional[WebSearch] = None


def get_searcher() -> WebSearch:
    global _searcher
    if _searcher is None:
        _searcher = WebSearch()
    return _searcher
