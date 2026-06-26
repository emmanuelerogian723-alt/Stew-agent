"""
S.T.E.W Browser Agent — Real web browsing, form filling, automation.
Uses httpx + BeautifulSoup as primary. Falls back to requests.
No Playwright/Selenium needed — works on any hosting platform.
"""
import asyncio
import re
import logging
from typing import Optional
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}


class StewBrowser:
    """Autonomous web browser for S.T.E.W."""

    def __init__(self):
        self.session_cookies: dict = {}
        self.history: list[str] = []
        self.current_url: Optional[str] = None
        self.current_html: Optional[str] = None

    async def fetch(self, url: str, timeout: int = 20) -> dict:
        """Fetch any URL and return structured content."""
        try:
            async with httpx.AsyncClient(
                headers=HEADERS,
                follow_redirects=True,
                timeout=timeout,
                cookies=self.session_cookies,
            ) as client:
                resp = await client.get(url)
                self.current_url = str(resp.url)
                self.current_html = resp.text
                self.history.append(self.current_url)
                # Persist cookies
                self.session_cookies.update(dict(resp.cookies))
                return self._parse_page(resp.text, str(resp.url), resp.status_code)
        except httpx.TimeoutException:
            return {"error": f"Timeout fetching {url}", "url": url}
        except Exception as e:
            logger.error(f"Browser fetch error: {e}")
            return {"error": str(e), "url": url}

    def _parse_page(self, html: str, url: str, status: int) -> dict:
        """Extract structured content from HTML."""
        soup = BeautifulSoup(html, "html.parser")

        # Remove noise
        for tag in soup(["script", "style", "nav", "footer", "aside", "iframe"]):
            tag.decompose()

        title = soup.title.string.strip() if soup.title else "No title"

        # Main content
        main = soup.find("main") or soup.find("article") or soup.find("body")
        text = main.get_text(separator="\n", strip=True) if main else soup.get_text(separator="\n", strip=True)

        # Clean up excessive whitespace
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        clean_text = "\n".join(lines)
        # Truncate to 8000 chars to stay within LLM context
        if len(clean_text) > 8000:
            clean_text = clean_text[:8000] + "\n\n[...content truncated for brevity...]"

        # Extract links
        links = []
        for a in soup.find_all("a", href=True)[:20]:
            href = urljoin(url, a["href"])
            text_label = a.get_text(strip=True)
            if href.startswith("http") and text_label:
                links.append({"text": text_label[:80], "url": href})

        # Extract forms
        forms = self._extract_forms(soup, url)

        # Extract meta description
        meta_desc = ""
        meta = soup.find("meta", attrs={"name": "description"})
        if meta:
            meta_desc = meta.get("content", "")

        return {
            "url": url,
            "status": status,
            "title": title,
            "description": meta_desc,
            "content": clean_text,
            "links": links,
            "forms": forms,
            "word_count": len(clean_text.split()),
        }

    def _extract_forms(self, soup: BeautifulSoup, base_url: str) -> list[dict]:
        """Extract all forms and their fields."""
        forms = []
        for form in soup.find_all("form"):
            fields = []
            for inp in form.find_all(["input", "textarea", "select"]):
                field_type = inp.get("type", "text")
                if field_type in ("hidden", "submit", "button", "reset"):
                    continue
                fields.append({
                    "name": inp.get("name", inp.get("id", "unknown")),
                    "type": field_type,
                    "placeholder": inp.get("placeholder", ""),
                    "required": inp.has_attr("required"),
                })
            action = urljoin(base_url, form.get("action", base_url))
            method = form.get("method", "GET").upper()
            forms.append({
                "action": action,
                "method": method,
                "fields": fields,
            })
        return forms

    async def submit_form(self, url: str, form_data: dict, method: str = "POST") -> dict:
        """Submit a form with given data."""
        try:
            async with httpx.AsyncClient(
                headers=HEADERS,
                follow_redirects=True,
                timeout=20,
                cookies=self.session_cookies,
            ) as client:
                if method.upper() == "POST":
                    resp = await client.post(url, data=form_data)
                else:
                    resp = await client.get(url, params=form_data)
                self.session_cookies.update(dict(resp.cookies))
                return self._parse_page(resp.text, str(resp.url), resp.status_code)
        except Exception as e:
            return {"error": str(e), "url": url}

    async def search_web_fallback(self, query: str) -> dict:
        """
        Fallback browser search when Serper API is unavailable.
        Uses DuckDuckGo HTML search — no API key required.
        """
        ddg_url = f"https://html.duckduckgo.com/html/?q={query.replace(' ', '+')}"
        try:
            async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True, timeout=15) as client:
                resp = await client.post(ddg_url, data={"q": query})
                soup = BeautifulSoup(resp.text, "html.parser")
                results = []
                for r in soup.select(".result")[:8]:
                    title_el = r.select_one(".result__title")
                    snippet_el = r.select_one(".result__snippet")
                    link_el = r.select_one(".result__url")
                    if title_el:
                        results.append({
                            "title": title_el.get_text(strip=True),
                            "snippet": snippet_el.get_text(strip=True) if snippet_el else "",
                            "url": "https://" + link_el.get_text(strip=True) if link_el else "",
                        })
                return {
                    "source": "duckduckgo_fallback",
                    "query": query,
                    "results": results,
                    "count": len(results),
                }
        except Exception as e:
            # Ultimate fallback — Bing HTML
            return await self._bing_fallback(query)

    async def _bing_fallback(self, query: str) -> dict:
        """Third-layer fallback using Bing HTML."""
        bing_url = f"https://www.bing.com/search?q={query.replace(' ', '+')}"
        try:
            async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True, timeout=15) as client:
                resp = await client.get(bing_url)
                soup = BeautifulSoup(resp.text, "html.parser")
                results = []
                for li in soup.select("li.b_algo")[:8]:
                    h2 = li.find("h2")
                    p = li.find("p")
                    a = h2.find("a") if h2 else None
                    if a:
                        results.append({
                            "title": a.get_text(strip=True),
                            "snippet": p.get_text(strip=True) if p else "",
                            "url": a.get("href", ""),
                        })
                return {
                    "source": "bing_fallback",
                    "query": query,
                    "results": results,
                    "count": len(results),
                }
        except Exception as e:
            return {"source": "none", "query": query, "results": [], "error": str(e)}

    async def navigate_and_answer(self, url: str, question: str) -> str:
        """Browse a URL and answer a specific question about it."""
        page = await self.fetch(url)
        if "error" in page:
            return f"Could not access {url}: {page['error']}"
        content = page.get("content", "")
        title = page.get("title", "")
        return f"Page: {title}\nURL: {url}\n\nContent:\n{content[:4000]}"


# Singleton browser instance per request lifecycle
_browser_instance = None


def get_browser() -> StewBrowser:
    global _browser_instance
    if _browser_instance is None:
        _browser_instance = StewBrowser()
    return _browser_instance
