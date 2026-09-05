"""
S.T.E.W Autonomous Web Crawler — No-API-Key Search & Page Extraction

This module implements real web crawling that doesn't depend on any paid
search API (Serper, Brave, etc.). It scrapes search engines directly and
fetches page content through multiple strategies, exactly like how
Kimi/Perplexity agents work — they hit the real web, not an API proxy.

Search engines supported (all free, no keys):
  1. Google HTML scrape (google.com/search?q=...) — the real thing
  2. Bing HTML scrape (bing.com/search?q=...) — Microsoft's index
  3. DuckDuckGo HTML (html.duckduckgo.com/html/) — privacy-focused
  4. Jina Reader proxy (r.jina.ai) — free proxy for cloud IPs

Page fetch strategies:
  1. Direct httpx fetch + BeautifulSoup (fast, works for most sites)
  2. Jina AI Reader (r.jina.ai) — renders JS, bypasses blocks
  3. Allorigins proxy — CORS bypass for blocked datacenter IPs
  4. Wikipedia REST API — special handling for Wikipedia

Content extraction:
  - Readability-style article extraction (find main content block)
  - Table extraction (parse <table> to structured data)
  - Meta tag extraction (title, description, og:image, etc.)
  - Link extraction (find all outbound links)
  - Smart text cleaning (remove nav/footer/ads, keep content)
"""

import asyncio
import logging
import os
import re
import time
import random
import urllib.parse
from datetime import datetime, timezone
from typing import Optional

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# Rotating user agents — real browsers, so search engines don't block us
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.5; rv:128.0) Gecko/20100101 Firefox/128.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36",
]


def _random_ua():
    return random.choice(USER_AGENTS)


def _search_headers():
    # Keep headers simple - Sec-Fetch-* and "br" encoding cause search engines
    # to return different (smaller/empty) pages that break parsing.
    return {
        "User-Agent": _random_ua(),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
    }


class WebCrawler:
    """Autonomous web crawler — no API keys needed.

    Usage:
        crawler = WebCrawler()
        results = await crawler.search("latest news about AI in Nigeria")
        page = await crawler.fetch_page("https://example.com/article")
    """

    def __init__(self):
        self._client = None
        self._sync_client = None
        self._last_request = {}
        self._min_delay = 0.5

    async def _get_async_client(self):
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=30, follow_redirects=True, headers=_search_headers(),
            )
        return self._client

    def _polite_delay(self, domain):
        now = time.time()
        last = self._last_request.get(domain, 0)
        elapsed = now - last
        if elapsed < self._min_delay:
            time.sleep(self._min_delay - elapsed)
        self._last_request[domain] = time.time()

    async def _polite_delay_async(self, domain):
        now = time.time()
        last = self._last_request.get(domain, 0)
        elapsed = now - last
        if elapsed < self._min_delay:
            await asyncio.sleep(self._min_delay - elapsed)
        self._last_request[domain] = time.time()

    # ── GOOGLE SEARCH SCRAPE (no API key!) ───────────────────────────

    async def google_search(self, query, num_results=10):
        """Scrape Google search results directly — no API key, no Serper."""
        try:
            client = await self._get_async_client()
            await self._polite_delay_async("google.com")
            params = {"q": query, "num": min(num_results, 20), "hl": "en", "gl": "us", "safe": "off"}
            resp = await client.get("https://www.google.com/search", params=params, headers=_search_headers())
            if resp.status_code != 200:
                logger.warning(f"Google search returned {resp.status_code}")
                return {"organic": [], "source": "google", "grounded": False}

            soup = BeautifulSoup(resp.text, "html.parser")
            results = []

            # Google results in <div class="g">
            for div in soup.select("div.g"):
                if len(results) >= num_results:
                    break
                link_tag = div.select_one("a[href]")
                if not link_tag:
                    continue
                link = link_tag.get("href", "")
                if not link or not link.startswith("http"):
                    continue
                if "google.com/" in link and "/search?" not in link:
                    continue
                title_tag = div.select_one("h3")
                title = title_tag.get_text(strip=True) if title_tag else ""
                if not title:
                    continue
                snippet = ""
                for st in div.select("span, div.VwiC3b"):
                    text = st.get_text(strip=True)
                    if len(text) > 30 and text != title:
                        snippet = text
                        break
                if not snippet:
                    all_text = div.get_text(separator=" ", strip=True)
                    if title in all_text:
                        snippet = all_text.replace(title, "").strip()[:300]
                results.append({"title": title, "link": link, "snippet": snippet[:300], "position": len(results) + 1})

            # Old-style parsing fallback
            if not results:
                for a in soup.select("a[href]"):
                    href = a.get("href", "")
                    if href.startswith("/url?"):
                        parsed = urllib.parse.parse_qs(href.split("?", 1)[1])
                        href = parsed.get("q", [""])[0]
                    if not href or not href.startswith("http") or "google.com" in href:
                        continue
                    title = a.get_text(strip=True)
                    if not title or len(title) < 10:
                        continue
                    parent = a.find_parent("div")
                    snippet = ""
                    if parent:
                        for s in parent.find_all("span"):
                            text = s.get_text(strip=True)
                            if len(text) > 40 and text != title:
                                snippet = text[:300]
                                break
                    results.append({"title": title, "link": href, "snippet": snippet, "position": len(results) + 1})
                    if len(results) >= num_results:
                        break

            logger.info(f"Google scrape: {len(results)} results for '{query}'")
            return {
                "organic": results[:num_results],
                "answer_box": self._extract_google_answer_box(soup),
                "knowledge_graph": self._extract_google_kg(soup),
                "related_searches": self._extract_google_related(soup),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "query": query, "grounded": len(results) > 0, "source": "google_scrape",
            }
        except Exception as e:
            logger.warning(f"Google search scrape error: {e}")
            return {"organic": [], "source": "google", "grounded": False, "error": str(e)}

    def _extract_google_answer_box(self, soup):
        try:
            for selector in ["div.c2E1Gb", "div.BNeawe", "div[data-ved] span"]:
                el = soup.select_one(selector)
                if el:
                    text = el.get_text(strip=True)
                    if len(text) > 20:
                        return {"snippet": text[:500], "source": "featured_snippet"}
            return {}
        except Exception:
            return {}

    def _extract_google_kg(self, soup):
        try:
            kg = soup.select_one("div.kp-wholepage, div[data-attrid='knowledge_panel']")
            if kg:
                title = kg.select_one("div, span")
                desc = kg.select_one("div[data-attrid='description']")
                return {"title": title.get_text(strip=True) if title else "",
                        "description": desc.get_text(strip=True)[:500] if desc else ""}
            return {}
        except Exception:
            return {}

    def _extract_google_related(self, soup):
        try:
            related = []
            for a in soup.select("a[href*='/search?']"):
                text = a.get_text(strip=True)
                if text and len(text) > 5 and text not in related:
                    related.append(text)
            return related[:8]
        except Exception:
            return []

    # ── BING SEARCH SCRAPE (no API key!) ─────────────────────────────

    async def bing_search(self, query, num_results=10):
        """Scrape Bing search results — Microsoft's index, free."""
        try:
            client = await self._get_async_client()
            await self._polite_delay_async("bing.com")
            params = {"q": query, "count": min(num_results, 30), "setlang": "en-US"}
            resp = await client.get("https://www.bing.com/search", params=params, headers=_search_headers())
            if resp.status_code != 200:
                return {"organic": [], "source": "bing", "grounded": False}

            soup = BeautifulSoup(resp.text, "html.parser")
            results = []
            for li in soup.select("li.b_algo"):
                if len(results) >= num_results:
                    break
                link_tag = li.select_one("h2 a")
                if not link_tag:
                    continue
                link = link_tag.get("href", "")
                title = link_tag.get_text(strip=True)
                if not link or not link.startswith("http"):
                    continue
                snippet = ""
                p_tag = li.select_one("p, div.b_caption p")
                if p_tag:
                    snippet = p_tag.get_text(strip=True)[:300]
                results.append({"title": title, "link": link, "snippet": snippet, "position": len(results) + 1})

            logger.info(f"Bing scrape: {len(results)} results for '{query}'")
            return {
                "organic": results[:num_results], "answer_box": {}, "knowledge_graph": {},
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "query": query, "grounded": len(results) > 0, "source": "bing_scrape",
            }
        except Exception as e:
            logger.warning(f"Bing search scrape error: {e}")
            return {"organic": [], "source": "bing", "grounded": False, "error": str(e)}

    # ── DUCKDUCKGO SEARCH (no API key!) ───────────────────────────────

    async def ddg_search(self, query, num_results=10):
        """Search DuckDuckGo HTML endpoint — free, no key."""
        try:
            client = await self._get_async_client()
            await self._polite_delay_async("duckduckgo.com")
            resp = await client.post(
                "https://html.duckduckgo.com/html/",
                data={"q": query},
                headers={**_search_headers(), "Referer": "https://duckduckgo.com/"},
            )
            if resp.status_code != 200:
                return {"organic": [], "source": "ddg", "grounded": False}

            soup = BeautifulSoup(resp.text, "html.parser")
            results = []
            for r in soup.select(".result__body"):
                if len(results) >= num_results:
                    break
                title_tag = r.select_one(".result__title a")
                snippet_tag = r.select_one(".result__snippet")
                if not title_tag:
                    continue
                link = title_tag.get("href", "")
                if "uddg=" in link:
                    link = urllib.parse.unquote(link.split("uddg=")[-1].split("&")[0])
                elif link.startswith("//"):
                    link = "https:" + link
                title = title_tag.get_text(strip=True)
                snippet = snippet_tag.get_text(strip=True) if snippet_tag else ""
                if title and link:
                    results.append({"title": title, "link": link, "snippet": snippet, "position": len(results) + 1})

            logger.info(f"DDG search: {len(results)} results for '{query}'")
            return {
                "organic": results[:num_results], "answer_box": {}, "knowledge_graph": {},
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "query": query, "grounded": len(results) > 0, "source": "ddg_html",
            }
        except Exception as e:
            logger.warning(f"DDG search error: {e}")
            return {"organic": [], "source": "ddg", "grounded": False, "error": str(e)}

    # ── SEARXNG SEARCH (free, no key, JSON results!) ──────────────────

    async def searxng_search(self, query, num_results=10):
        """Search via SearXNG public instance - returns JSON, no API key.
        This is the most reliable free search source since it returns
        structured JSON instead of HTML that needs parsing."""
        try:
            client = await self._get_async_client()
            await self._polite_delay_async("mectov.my.id")

            instances = [
                "https://search.mectov.my.id/search",
                "https://searx.be/search",
                "https://search.bus-hit.me/search",
            ]

            for instance_url in instances:
                try:
                    resp = await client.get(
                        instance_url,
                        params={"q": query, "format": "json", "pageno": 1},
                        headers={"User-Agent": _random_ua(), "Accept": "application/json"},
                        timeout=15,
                    )
                    if resp.status_code == 200 and resp.text.strip().startswith("{"):
                        import json
                        data = json.loads(resp.text)
                        results = []
                        for r in data.get("results", [])[:num_results]:
                            results.append({
                                "title": r.get("title", ""),
                                "link": r.get("url", ""),
                                "snippet": r.get("content", "")[:300],
                                "position": len(results) + 1,
                            })
                        if results:
                            logger.info(f"SearXNG: {len(results)} results for '{query}' from {instance_url}")
                            return {
                                "organic": results[:num_results],
                                "answer_box": {},
                                "knowledge_graph": {},
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                                "query": query, "grounded": True, "source": "searxng",
                            }
                except Exception as e:
                    logger.warning(f"SearXNG instance {instance_url} failed: {e}")
                    continue

            return {"organic": [], "source": "searxng", "grounded": False}
        except Exception as e:
            logger.warning(f"SearXNG search error: {e}")
            return {"organic": [], "source": "searxng", "grounded": False, "error": str(e)}

    # ── JINA READER PROXY SEARCH ─────────────────────────────────────

    async def jina_search(self, query, num_results=10):
        """Search via Jina AI reader — fetches DDG through r.jina.ai proxy."""
        try:
            client = await self._get_async_client()
            await self._polite_delay_async("jina.ai")
            ddg_url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote_plus(query)}"
            jina_url = f"https://r.jina.ai/{ddg_url}"
            headers = {"Accept": "text/plain", "User-Agent": _random_ua()}
            jina_key = os.environ.get("JINA_API_KEY", "")
            if jina_key:
                headers["Authorization"] = f"Bearer {jina_key}"

            resp = await client.get(jina_url, headers=headers, timeout=20)
            if resp.status_code != 200 or len(resp.text) < 200:
                return {"organic": [], "source": "jina_ddg", "grounded": False}

            text = resp.text
            lines = text.split("\n")
            results = []
            for i, line in enumerate(lines):
                if len(results) >= num_results:
                    break
                line = line.strip()
                if line.startswith("## [") and "](" in line:
                    bracket_end = line.index("](", 3)
                    title = line[4:bracket_end].strip()
                    url_start = bracket_end + 2
                    url_end = line.index(")", url_start)
                    raw_link = line[url_start:url_end].strip()
                    if "uddg=" in raw_link:
                        link = urllib.parse.unquote(raw_link.split("uddg=")[-1].split("&")[0])
                    else:
                        link = raw_link
                    if not link or "duckduckgo.com" in link:
                        continue
                    snippet = ""
                    for j in range(i + 1, min(i + 6, len(lines))):
                        snip = lines[j].strip()
                        if not snip or snip.startswith("[!") or snip.startswith("URL Source:"):
                            continue
                        if snip.startswith("[") and "](" in snip:
                            bracket = snip.index("](")
                            text_inside = snip[1:bracket]
                            if " " not in text_inside and "." in text_inside:
                                continue
                            snippet = text_inside[:300]
                            break
                        snippet = snip[:300]
                        break
                    title = title.replace("**", "").replace("*", "")
                    snippet = snippet.replace("**", "").replace("*", "")
                    results.append({"title": title, "link": link, "snippet": snippet, "position": len(results) + 1})

            logger.info(f"Jina DDG search: {len(results)} results for '{query}'")
            return {
                "organic": results[:num_results], "answer_box": {}, "knowledge_graph": {},
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "query": query, "grounded": len(results) > 0, "source": "jina_ddg",
            }
        except Exception as e:
            logger.warning(f"Jina search error: {e}")
            return {"organic": [], "source": "jina_ddg", "grounded": False, "error": str(e)}

    # ── MASTER SEARCH — tries all engines ────────────────────────────

    async def search(self, query, num_results=10):
        """Full autonomous search - SearXNG -> Bing -> DDG -> Google -> Jina.
        No API keys needed. SearXNG is tried first (returns clean JSON).
        Bing and DDG are next (HTML scraping works well).
        Google is tried later (often blocks datacenter IPs).
        Jina is the last resort proxy."""
        engines = [
            ("searxng", self.searxng_search),
            ("bing", self.bing_search),
            ("ddg", self.ddg_search),
            ("google", self.google_search),
            ("jina", self.jina_search),
        ]
        errors = []
        for name, engine_fn in engines:
            try:
                result = await engine_fn(query, num_results)
                if result.get("organic"):
                    return result
                if result.get("error"):
                    errors.append(f"{name}: {result['error']}")
            except Exception as e:
                errors.append(f"{name}: {e}")
                logger.warning(f"Search engine {name} failed: {e}")
                continue

        logger.error(f"All search engines failed for '{query}': {errors}")
        return {
            "organic": [], "answer_box": {}, "knowledge_graph": {},
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "query": query, "grounded": False,
            "error": f"All engines failed: {'; '.join(errors)}",
        }

    def search_sync(self, query, num_results=10):
        """Synchronous search wrapper."""
        try:
            return asyncio.run(self.search(query, num_results))
        except RuntimeError:
            import threading
            result_box = [None]
            def _run():
                result_box[0] = asyncio.run(self.search(query, num_results))
            t = threading.Thread(target=_run)
            t.start()
            t.join(timeout=30)
            return result_box[0] or {"organic": [], "grounded": False}

    # ── PAGE FETCH & CONTENT EXTRACTION ──────────────────────────────

    async def fetch_page(self, url, timeout=20):
        """Fetch a web page and extract clean content.
        Multi-strategy: direct → Jina → proxy."""
        if "wikipedia.org" in url:
            wiki = await self._fetch_wikipedia(url, timeout)
            if wiki.get("content"):
                return wiki

        try:
            result = await self._direct_fetch(url, timeout)
            if result.get("content") and len(result["content"]) > 200:
                return result
        except Exception as e:
            logger.warning(f"Direct fetch failed for {url}: {e}")

        try:
            result = await self._jina_fetch(url, timeout)
            if result.get("content") and len(result["content"]) > 200:
                return result
        except Exception as e:
            logger.warning(f"Jina fetch failed for {url}: {e}")

        try:
            result = await self._proxy_fetch(url, timeout)
            if result.get("content") and len(result["content"]) > 200:
                return result
        except Exception as e:
            logger.warning(f"Proxy fetch failed for {url}: {e}")

        return {"url": url, "content": "", "error": "All fetch strategies failed"}

    async def _direct_fetch(self, url, timeout=20):
        client = await self._get_async_client()
        domain = urllib.parse.urlparse(url).netloc
        await self._polite_delay_async(domain)
        resp = await client.get(url, headers=_search_headers(), timeout=timeout)
        if resp.status_code != 200:
            return {"url": url, "content": "", "error": f"HTTP {resp.status_code}"}
        return self._extract_from_html(resp.text, url)

    async def _jina_fetch(self, url, timeout=20):
        client = await self._get_async_client()
        jina_url = f"https://r.jina.ai/{url}"
        headers = {"Accept": "text/plain", "User-Agent": _random_ua()}
        jina_key = os.environ.get("JINA_API_KEY", "")
        if jina_key:
            headers["Authorization"] = f"Bearer {jina_key}"
        resp = await client.get(jina_url, headers=headers, timeout=timeout + 10)
        if resp.status_code != 200 or len(resp.text) < 100:
            return {"url": url, "content": "", "error": f"Jina returned {resp.status_code}"}
        text = resp.text
        title_match = re.search(r'^Title:\s*(.+)', text, re.MULTILINE)
        title = title_match.group(1).strip() if title_match else url
        content_lines = []
        skip_metadata = True
        for line in text.split("\n"):
            if skip_metadata:
                if line.strip() == "" and not content_lines:
                    continue
                if line.startswith(("Title:", "URL:", "Markdown Content:")):
                    continue
                skip_metadata = False
            content_lines.append(line)
        content = "\n".join(content_lines).strip() or text[:8000]
        return {
            "url": url, "status": 200, "title": title, "description": "",
            "content": content[:12000], "links": [],
            "word_count": len(content.split()), "rendered": True, "source": "jina_reader",
        }

    async def _proxy_fetch(self, url, timeout=20):
        client = await self._get_async_client()
        proxy_url = f"https://api.allorigins.win/raw?url={urllib.parse.quote_plus(url)}"
        resp = await client.get(proxy_url, headers=_search_headers(), timeout=timeout)
        if resp.status_code != 200 or len(resp.text) < 100:
            return {"url": url, "content": "", "error": f"Proxy returned {resp.status_code}"}
        return self._extract_from_html(resp.text, url)

    async def _fetch_wikipedia(self, url, timeout=20):
        match = re.search(r'/wiki/(.+?)(?:\?|#|$)', url)
        if not match:
            return {"url": url, "content": ""}
        article = match.group(1)
        title = article.replace("_", " ")
        wiki_headers = {"User-Agent": _random_ua(), "Accept": "application/json"}
        client = await self._get_async_client()

        try:
            api_url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{article}"
            resp = await client.get(api_url, headers=wiki_headers, timeout=timeout)
            if resp.status_code == 200:
                data = resp.json()
                extract = data.get("extract", "")
                if extract and len(extract) > 50:
                    return {
                        "url": url, "status": 200, "title": data.get("title", title),
                        "description": data.get("description", ""),
                        "content": extract[:12000], "links": [],
                        "word_count": len(extract.split()), "rendered": False, "source": "wikipedia_api",
                    }
        except Exception as e:
            logger.warning(f"Wikipedia REST failed: {e}")

        try:
            action_url = "https://en.wikipedia.org/w/api.php"
            params = {"action": "query", "titles": title, "prop": "extracts",
                      "explaintext": "true", "format": "json", "exintro": "false"}
            resp = await client.get(action_url, params=params, headers=wiki_headers, timeout=timeout)
            if resp.status_code == 200:
                data = resp.json()
                pages = data.get("query", {}).get("pages", {})
                for page_id, page in pages.items():
                    extract = page.get("extract", "")
                    if extract:
                        return {
                            "url": url, "status": 200, "title": page.get("title", title),
                            "description": "", "content": extract[:12000], "links": [],
                            "word_count": len(extract.split()), "rendered": False, "source": "wikipedia_api",
                        }
        except Exception as e:
            logger.warning(f"Wikipedia Action API failed: {e}")
        return {"url": url, "content": ""}

    # ── HTML CONTENT EXTRACTION ───────────────────────────────────────

    def _extract_from_html(self, html, url):
        soup = BeautifulSoup(html, "html.parser")
        title = ""
        if soup.title and soup.title.string:
            title = soup.title.string.strip()
        if not title:
            h1 = soup.find("h1")
            title = h1.get_text(strip=True) if h1 else url

        description = ""
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc:
            description = meta_desc.get("content", "")
        if not description:
            og_desc = soup.find("meta", attrs={"property": "og:description"})
            if og_desc:
                description = og_desc.get("content", "")

        og_image = ""
        og_img_tag = soup.find("meta", attrs={"property": "og:image"})
        if og_img_tag:
            og_image = og_img_tag.get("content", "")

        for tag in soup.select("script, style, nav, footer, aside, header, "
                               "noscript, iframe, form, button, .ad, .ads, "
                               ".advertisement, .sidebar, .menu, .navigation, "
                               ".cookie-banner, .social-share, .comments, "
                               ".related-posts, .newsletter-signup, .popup"):
            tag.decompose()

        main_content = None
        for selector in ["article", "main", "div.content", "div.post-content",
                         "div.entry-content", "div.article-body", "div.story-body",
                         "div#content", "div.main-content", "div.post",
                         "section.content", "div[role='main']"]:
            main_content = soup.select_one(selector)
            if main_content:
                break

        content_soup = main_content if main_content else soup
        text = content_soup.get_text(separator="\n", strip=True)
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = re.sub(r'[ \t]+', ' ', text)
        text = text.strip()

        links = []
        base_domain = urllib.parse.urlparse(url).netloc
        for a in content_soup.find_all("a", href=True):
            href = a["href"]
            if href.startswith("/"):
                href = urllib.parse.urljoin(url, href)
            elif not href.startswith("http"):
                continue
            link_domain = urllib.parse.urlparse(href).netloc
            if link_domain and link_domain != base_domain:
                link_text = a.get_text(strip=True)
                if link_text and len(link_text) > 3:
                    links.append({"text": link_text, "url": href})

        tables = []
        for table in content_soup.find_all("table"):
            table_data = self._extract_table(table)
            if table_data:
                tables.append(table_data)

        return {
            "url": url, "status": 200, "title": title, "description": description,
            "og_image": og_image, "content": text[:12000], "links": links[:50],
            "tables": tables[:5], "word_count": len(text.split()),
            "rendered": False, "source": "direct_fetch",
        }

    def _extract_table(self, table):
        try:
            rows = []
            for tr in table.find_all("tr"):
                cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
                if cells:
                    rows.append(cells)
            if rows and len(rows) > 1:
                return rows[:20]
            return []
        except Exception:
            return []

    # ── MULTI-PAGE CRAWL (for deep research) ─────────────────────────

    async def crawl(self, query, num_pages=3, num_results=8):
        """Full crawl: search -> fetch top N pages -> compile report."""
        search_result = await self.search(query, num_results)
        organic = search_result.get("organic", [])
        if not organic:
            return {"grounded": False, "report": "No search results found.",
                    "organic": [], "pages": [], "query": query}

        # Fetch MORE urls than needed (some sites block datacenter IPs) and
        # keep only the first num_pages that actually return content.
        fetch_candidates = [r.get("link", "") for r in organic if r.get("link")][:max(num_pages + 3, 6)]
        tasks = [self.fetch_page(link) for link in fetch_candidates]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        pages = []
        for i, result in enumerate(results):
            if len(pages) >= num_pages:
                break
            if isinstance(result, Exception):
                continue
            if result.get("content"):
                idx = min(i, len(organic) - 1)
                pages.append({
                    "title": result.get("title") or organic[idx].get("title", ""),
                    "url": result.get("url", fetch_candidates[i]),
                    "content": result["content"][:5000],
                    "source": result.get("source", "direct"),
                    "word_count": result.get("word_count", 0),
                })

        report_parts = []
        ab = search_result.get("answer_box", {})
        if ab and ab.get("snippet"):
            report_parts.append(f"[Quick Answer]: {ab['snippet']}")
        kg = search_result.get("knowledge_graph", {})
        if kg and kg.get("description"):
            report_parts.append(f"[Knowledge Panel]: {kg['description']}")
        for r in organic[:5]:
            report_parts.append(f"[{r.get('position', '')}] {r.get('title', '')}\n    {r.get('snippet', '')}\n    Source: {r.get('link', '')}")
        for p in pages:
            report_parts.append(f"\n[Full Page: {p['title']}]\nURL: {p['url']}\nWords: {p['word_count']}\n{p['content'][:3000]}")

        return {
            "grounded": True, "report": "\n\n".join(report_parts),
            "organic": organic, "pages": pages, "query": query,
            "search_source": search_result.get("source", "unknown"),
        }

    def crawl_sync(self, query, num_pages=3):
        try:
            return asyncio.run(self.crawl(query, num_pages))
        except RuntimeError:
            import threading
            result_box = [None]
            def _run():
                result_box[0] = asyncio.run(self.crawl(query, num_pages))
            t = threading.Thread(target=_run)
            t.start()
            t.join(timeout=60)
            return result_box[0] or {"grounded": False, "report": "", "organic": []}

    # ── REAL-TIME DATA EXTRACTION ─────────────────────────────────────

    async def extract_realtime_data(self, query):
        """Extract real-time structured data — good for prices, stats, news."""
        search_result = await self.search(query, 5)
        organic = search_result.get("organic", [])
        if not organic:
            return {"data": None, "grounded": False, "query": query}
        top_url = organic[0].get("link", "")
        page = await self.fetch_page(top_url)
        return {
            "data": {"title": page.get("title", ""), "content": page.get("content", "")[:5000],
                     "tables": page.get("tables", []), "url": top_url},
            "organic": organic, "grounded": True, "query": query,
            "search_source": search_result.get("source", ""),
        }

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    def __del__(self):
        try:
            if self._sync_client:
                self._sync_client.close()
        except Exception:
            pass


_crawler = None

def get_crawler():
    global _crawler
    if _crawler is None:
        _crawler = WebCrawler()
    return _crawler
