"""
S.T.E.W Web Search — REAL Serper API calls only.
Anti-hallucination: NEVER return fabricated results.
"""
import logging
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
            logger.warning("SERPER_API_KEY not set — web search disabled")
            return {
                "organic": [],
                "answer_box": {},
                "knowledge_graph": {},
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "query": query,
                "grounded": False,
                "error": "Web search not available (SERPER_API_KEY not configured)",
            }

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
            logger.error("Serper API timeout")
            raise HTTPException(status_code=504, detail="Web search timed out")
        except requests.HTTPError as e:
            logger.error(f"Serper API HTTP error: {e}")
            raise HTTPException(status_code=502, detail=f"Web search API error: {e}")
        except Exception as e:
            logger.error(f"Serper API unexpected error: {e}")
            raise HTTPException(status_code=502, detail="Web search failed")

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
