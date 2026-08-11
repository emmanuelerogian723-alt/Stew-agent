"""
S.T.E.W Browser Agent — Full Playwright automation + httpx fallback.
Playwright (Chromium) is used when available for true JS rendering.
Falls back to httpx+BeautifulSoup on platforms without Playwright.
"""
import asyncio
import logging
import os
import re
from typing import Optional
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# Detect Playwright availability
try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
    logger.info("Playwright available — full browser automation enabled")
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    logger.info("Playwright not available — using httpx fallback")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
}


class StewBrowser:
    """Autonomous web browser — Playwright preferred, httpx fallback."""

    def __init__(self):
        self.session_cookies: dict = {}
        self.history: list[str] = []
        self.current_url: Optional[str] = None
        self.current_html: Optional[str] = None

    async def fetch(self, url: str, timeout: int = 20) -> dict:
        """Fetch URL — tries Jina AI reader (free, bypasses blocks), then Playwright, then httpx."""
        # Special case: Wikipedia blocks datacenter IPs, use REST API instead
        if "wikipedia.org" in url:
            wiki_result = await self._wikipedia_fetch(url, timeout)
            if wiki_result.get("content"):
                return wiki_result

        # Method 1: Jina AI free reader — reads any URL, bypasses blocks
        try:
            jina_result = await self._jina_fetch(url, timeout)
            if jina_result.get("content"):
                return jina_result
        except Exception as e:
            logger.warning(f"Jina reader failed for {url}: {e}")

        # Method 2: Playwright (full JS rendering)
        if PLAYWRIGHT_AVAILABLE:
            try:
                return await self._playwright_fetch(url, timeout)
            except Exception as e:
                logger.warning(f"Playwright fetch failed, falling back to httpx: {e}")
        
        # Method 3: httpx direct fetch
        return await self._httpx_fetch(url, timeout)

    async def _jina_fetch(self, url: str, timeout: int = 20) -> dict:
        """Fetch URL via Jina AI reader API — bypasses blocks and renders JS.
        Works without key for most URLs. API key (from env) enables higher rate limits."""
        import httpx as _httpx
        jina_url = f"https://r.jina.ai/{url}"
        jina_key = os.environ.get("JINA_API_KEY", os.environ.get("JINA_SEARCH_KEY", ""))
        headers = {
            "Accept": "text/plain",
            "User-Agent": "S.T.E.W-Agent/6.0 (https://stew-agent.onrender.com; contact@erogian.com)",
        }
        if jina_key:
            headers["Authorization"] = f"Bearer {jina_key}"
        async with _httpx.AsyncClient(timeout=timeout + 10, follow_redirects=True) as client:
            resp = await client.get(jina_url, headers=headers)
            if resp.status_code == 200 and len(resp.text) > 100:
                text = resp.text
                # Extract title from Jina's format
                title_match = re.search(r'^Title:\s*(.+)', text, re.MULTILINE)
                title = title_match.group(1).strip() if title_match else url
                
                # Clean up Jina metadata
                lines = text.split('\n')
                content_lines = []
                skip_metadata = True
                for line in lines:
                    if skip_metadata:
                        if line.strip() == '' and len(content_lines) == 0:
                            continue
                        if line.startswith('Title:') or line.startswith('URL:') or line.startswith('Markdown Content:'):
                            continue
                        skip_metadata = False
                    content_lines.append(line)
                
                content = '\n'.join(content_lines).strip()
                if not content:
                    content = text[:8000]
                
                return {
                    "url": url,
                    "status": 200,
                    "title": title,
                    "description": "",
                    "content": content[:8000],
                    "links": [],
                    "forms": [],
                    "word_count": len(content.split()),
                    "rendered": True,
                    "source": "jina_ai_reader",
                }
        return {}

    async def _wikipedia_fetch(self, url: str, timeout: int = 20) -> dict:
        """Fetch Wikipedia content via REST API (bypasses IP blocks)."""
        import re
        # Extract article title from URL
        match = re.search(r'/wiki/(.+?)(?:\?|#|$)', url)
        if not match:
            return {"error": "Could not parse Wikipedia URL", "url": url}
        article = match.group(1)
        title = article.replace("_", " ")
        
        # Wikipedia requires a proper User-Agent for API access
        wiki_headers = {
            "User-Agent": "S.T.E.W-Agent/6.0 (https://stew-agent.onrender.com; contact@erogian.com)",
            "Accept": "application/json",
        }
        
        # Try REST API first (summary), then action API (full extract)
        try:
            api_url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{article}"
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                resp = await client.get(api_url, headers=wiki_headers)
                if resp.status_code == 200:
                    data = resp.json()
                    extract = data.get("extract", "")
                    if extract and len(extract) > 50:
                        return {
                            "url": url,
                            "status": 200,
                            "title": data.get("title", title),
                            "description": data.get("description", ""),
                            "content": extract[:8000],
                            "links": [],
                            "forms": [],
                            "word_count": len(extract.split()),
                            "rendered": False,
                            "source": "wikipedia_api",
                        }
        except Exception as e:
            logger.warning(f"Wikipedia REST API failed: {e}")
        
        # Fallback: Action API with full text extraction
        try:
            action_url = "https://en.wikipedia.org/w/api.php"
            params = {
                "action": "query",
                "titles": title,
                "prop": "extracts",
                "explaintext": "true",
                "format": "json",
                "exintro": "false",
            }
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                resp = await client.get(action_url, params=params, headers=wiki_headers)
                if resp.status_code == 200:
                    data = resp.json()
                    pages = data.get("query", {}).get("pages", {})
                    for page_id, page in pages.items():
                        extract = page.get("extract", "")
                        if extract and len(extract) > 50:
                            return {
                                "url": url,
                                "status": 200,
                                "title": page.get("title", title),
                                "description": "",
                                "content": extract[:8000],
                                "links": [],
                                "forms": [],
                                "word_count": len(extract.split()),
                                "rendered": False,
                                "source": "wikipedia_action_api",
                            }
        except Exception as e:
            logger.warning(f"Wikipedia action API failed: {e}")
        
        # Fall through to normal fetch
        return {}

    async def _playwright_fetch(self, url: str, timeout: int = 20) -> dict:
        """Full browser fetch with JS rendering via Playwright."""
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--no-first-run",
                    "--no-zygote",
                    "--single-process",
                    "--disable-extensions",
                ]
            )
            try:
                page = await browser.new_page(
                    user_agent=HEADERS["User-Agent"]
                )
                await page.goto(url, timeout=timeout * 1000, wait_until="domcontentloaded")
                await page.wait_for_timeout(1500)  # wait for JS to render
                html = await page.content()
                final_url = page.url
                self.current_url = final_url
                self.current_html = html
                self.history.append(final_url)
                result = self._parse_page(html, final_url, 200)
                result["rendered"] = True
                return result
            finally:
                await browser.close()

    async def _httpx_fetch(self, url: str, timeout: int = 20) -> dict:
        """HTTP fetch with BeautifulSoup parsing."""
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
                self.session_cookies.update(dict(resp.cookies))
                result = self._parse_page(resp.text, str(resp.url), resp.status_code)
                result["rendered"] = False
                return result
        except httpx.TimeoutException:
            return {"error": f"Timeout fetching {url}", "url": url}
        except Exception as e:
            logger.error(f"Browser fetch error: {e}")
            return {"error": str(e), "url": url}

    async def screenshot(self, url: str, save_path: Optional[str] = None) -> dict:
        """Take a screenshot of a webpage (Playwright only)."""
        if not PLAYWRIGHT_AVAILABLE:
            return {"error": "Screenshot requires Playwright — not available on this platform"}
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
                      "--no-first-run", "--no-zygote", "--single-process"]
            )
            try:
                page = await browser.new_page(viewport={"width": 1280, "height": 800})
                await page.goto(url, timeout=30000, wait_until="networkidle")
                path = save_path or f"screenshots/stew_{int(asyncio.get_event_loop().time())}.png"
                os.makedirs(os.path.dirname(path), exist_ok=True)
                await page.screenshot(path=path, full_page=True)
                return {"success": True, "path": path, "url": url}
            finally:
                await browser.close()

    async def fill_and_submit(self, url: str, selectors: dict, submit_selector: str = None) -> dict:
        """Fill a form using CSS selectors and submit it (Playwright only)."""
        if not PLAYWRIGHT_AVAILABLE:
            return await self.submit_form(url, selectors)
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
                      "--no-first-run", "--no-zygote", "--single-process"]
            )
            try:
                page = await browser.new_page()
                await page.goto(url, timeout=30000)
                for selector, value in selectors.items():
                    await page.fill(selector, str(value))
                if submit_selector:
                    await page.click(submit_selector)
                    await page.wait_for_load_state("networkidle")
                html = await page.content()
                return self._parse_page(html, page.url, 200)
            finally:
                await browser.close()

    def _parse_page(self, html: str, url: str, status: int) -> dict:
        """Extract structured content from HTML."""
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "aside", "iframe"]):
            tag.decompose()

        title = soup.title.string.strip() if soup.title else "No title"
        main = soup.find("main") or soup.find("article") or soup.find("body")
        text = main.get_text(separator="\n", strip=True) if main else soup.get_text(separator="\n", strip=True)
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        clean_text = "\n".join(lines)
        if len(clean_text) > 8000:
            clean_text = clean_text[:8000] + "\n\n[...content truncated...]"

        links = []
        for a in soup.find_all("a", href=True)[:20]:
            href = urljoin(url, a["href"])
            label = a.get_text(strip=True)
            if href.startswith("http") and label:
                links.append({"text": label[:80], "url": href})

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
            "forms": self._extract_forms(soup, url),
            "word_count": len(clean_text.split()),
        }

    def _extract_forms(self, soup: BeautifulSoup, base_url: str) -> list[dict]:
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
            forms.append({"action": action, "method": method, "fields": fields})
        return forms

    async def submit_form(self, url: str, form_data: dict, method: str = "POST") -> dict:
        """Submit a form — httpx based."""
        try:
            async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True,
                                          timeout=20, cookies=self.session_cookies) as client:
                if method.upper() == "POST":
                    resp = await client.post(url, data=form_data)
                else:
                    resp = await client.get(url, params=form_data)
                self.session_cookies.update(dict(resp.cookies))
                return self._parse_page(resp.text, str(resp.url), resp.status_code)
        except Exception as e:
            return {"error": str(e), "url": url}

    async def search_web_fallback(self, query: str) -> dict:
        """DuckDuckGo HTML search — no API key required, reliable from cloud IPs."""
        import urllib.parse as _up
        ddg_url = "https://html.duckduckgo.com/html/"
        ddg_headers = {
            **HEADERS,
            "Referer": "https://duckduckgo.com/",
            "Accept": "text/html,application/xhtml+xml",
        }
        try:
            async with httpx.AsyncClient(headers=ddg_headers, follow_redirects=True, timeout=15) as client:
                resp = await client.post(ddg_url, data={"q": query})
                soup = BeautifulSoup(resp.text, "html.parser")
                results = []
                for r in soup.select(".result__body")[:8]:
                    title_tag = r.select_one(".result__title a")
                    snippet_tag = r.select_one(".result__snippet")
                    if not title_tag:
                        continue
                    link = title_tag.get("href", "")
                    if "uddg=" in link:
                        link = _up.unquote(link.split("uddg=")[-1].split("&")[0])
                    elif link.startswith("//"):
                        link = "https:" + link
                    results.append({
                        "title": title_tag.get_text(strip=True),
                        "snippet": snippet_tag.get_text(strip=True) if snippet_tag else "",
                        "url": link,
                    })
                return {"source": "duckduckgo", "query": query,
                        "results": results, "count": len(results)}
        except Exception as e:
            logger.warning(f"DuckDuckGo fallback error: {e}")
            return {"source": "none", "query": query, "results": [], "error": str(e)}

    async def _bing_fallback(self, query: str) -> dict:
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
                return {"source": "bing_fallback", "query": query,
                        "results": results, "count": len(results)}
        except Exception as e:
            return {"source": "none", "query": query, "results": [], "error": str(e)}


def get_browser() -> StewBrowser:
    return StewBrowser()
