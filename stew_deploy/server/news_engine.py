"""
S.T.E.W News Engine — real, dated headlines. No API key, no scraping fragility.

Primary source: Google News RSS (any topic query + recency filter, returns
real headlines from real publications with publish dates).
Secondary: curated tech/AI RSS feeds.

Why this exists: generic web search on a news question returns junk
(category pages, YouTube videos) and the LLM then hallucinates a
"how to build a news pipeline" essay. This engine guarantees the LLM
only ever sees REAL, DATED stories — and the summarizer prompt forbids
invention outright.
"""
import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import List, Optional
from xml.etree import ElementTree

import httpx

logger = logging.getLogger("stew.news")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

CURATED_FEEDS = {
    "techcrunch_ai": "https://techcrunch.com/category/artificial-intelligence/feed/",
    "verge_ai": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
    "venturebeat_ai": "https://venturebeat.com/category/ai/feed/",
    "mit_ai": "https://www.technologyreview.com/topic/artificial-intelligence/feed/",
    "arstechnica_ai": "https://arstechnica.com/ai/feed/",
    "hn": "https://hnrss.org/frontpage?points=150",
}

# ── Intent detection ──────────────────────────────────────────────────────────

_NEWS_NEGATIVE_RE = re.compile(
    r"\b(good news|bad news|no news|any news about you|news for me is|"
    r"the bad news|your news|my news)\b", re.I)

_NEWS_INTENT_RE = re.compile(
    r"\b(news|headlines|what'?s happening|whats happening|what is happening|"
    r"latest developments|recent developments|this week in|"
    r"current events|breaking)\b", re.I)


def is_news_intent(text: str) -> bool:
    t = (text or "").strip()
    if not t or len(t) > 400:
        return False
    if t.startswith("/news"):
        return True
    if _NEWS_NEGATIVE_RE.search(t):
        return False
    return bool(_NEWS_INTENT_RE.search(t))


def extract_news_topic(text: str) -> str:
    """Pull a clean search topic out of a natural-language news request."""
    t = (text or "").strip()
    t = re.sub(r"^/news\s*", "", t, flags=re.I)
    filler = (
        r"\b(?:find|give|get|show|tell|send|share|fetch|look\s?up|search|"
        r"please|can\s?you|could\s?you|me|us|the|a|an|for|about|on|in|of|"
        r"latest|recent|current|today'?s?|todays|this|week|month|now|"
        r"news|headline|headlines|update|updates|up?to-?date|"
        r"whats|what'?s|happening|happenings|events|stories|story)\b"
    )
    cleaned = re.sub(filler, " ", t, flags=re.I)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.!?")
    return cleaned[:80]


# ── RSS fetching ─────────────────────────────────────────────────────────────

def _parse_feed(xml_text: str, source: str) -> List[dict]:
    items: List[dict] = []
    try:
        root = ElementTree.fromstring(xml_text.encode("utf-8")
                                      if isinstance(xml_text, str) else xml_text)
    except Exception:
        return items

    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        pub = (it.findtext("pubDate") or "").strip()
        items.append({"title": title, "link": link,
                      "published": pub, "source": source})
    if not items:
        ns = {"a": "http://www.w3.org/2005/Atom"}
        for it in root.iter("{http://www.w3.org/2005/Atom}entry"):
            title = (it.findtext("a:title", default="", namespaces=ns) or "").strip()
            link_el = it.find("a:link", ns)
            link = (link_el.get("href", "") if link_el is not None else "").strip()
            pub = (it.findtext("a:updated", default="", namespaces=ns)
                   or it.findtext("a:published", default="", namespaces=ns) or "").strip()
            items.append({"title": title, "link": link,
                          "published": pub, "source": source})
    return items


def _parse_date(s: str) -> Optional[datetime]:
    if not s:
        return None
    try:
        return parsedate_to_datetime(s).astimezone(timezone.utc)
    except Exception:
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
        except Exception:
            return None


def _fetch_url(url: str, timeout: float = 12.0) -> Optional[str]:
    try:
        r = httpx.get(url, headers={"User-Agent": UA}, timeout=timeout, follow_redirects=True)
        if r.status_code == 200:
            return r.text
    except Exception:
        pass
    return None


def fetch_topic_news_sync(topic: str, days: int = 7, max_items: int = 20) -> List[dict]:
    """REAL, DATED headlines. Google News RSS first, curated feeds as backstop."""
    topic = (topic or "").strip() or "artificial intelligence"
    days = max(1, min(days, 30))
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    stories: List[dict] = []

    gq = f"{topic} when:{days}d"
    gnews_url = (f"https://news.google.com/rss/search?q={httpx.QueryParams({'q': gq}).get('q')}"
                 f"&hl=en-US&gl=US&ceid=US:en")
    xml = _fetch_url(gnews_url)
    if xml:
        for raw in _parse_feed(xml, "Google News"):
            title = raw["title"]
            source = raw["source"]
            if " - " in title:
                title, _, tail = title.rpartition(" - ")
                source = tail if len(tail) < 40 else source
            dt = _parse_date(raw["published"])
            if dt and dt >= cutoff:
                stories.append({"title": title, "link": raw["link"],
                                "published_iso": dt.isoformat(), "source": source})
    logger.info(f"news engine: google news returned {len(stories)} items for '{topic}'")

    if len(stories) < 5:
        _kw = re.compile(r"\b(ai|artificial intelligence|llm|gpt|openai|anthropic|"
                         r"google deepmind|gemini|claude|chatgpt|machine learning|"
                         r"robot|model|tech|startup)\b", re.I)
        for name, url in CURATED_FEEDS.items():
            xml = _fetch_url(url, timeout=10)
            if not xml:
                continue
            for raw in _parse_feed(xml, name):
                dt = _parse_date(raw["published"])
                if not (dt and dt >= cutoff):
                    continue
                if not _kw.search(raw["title"]):
                    continue
                stories.append({"title": raw["title"], "link": raw["link"],
                                "published_iso": dt.isoformat(), "source": name})
            if len(stories) >= max_items * 2:
                break

    seen, uniq = set(), []
    for s in stories:
        k = s["title"].lower()[:80]
        if k not in seen:
            seen.add(k)
            uniq.append(s)

    uniq.sort(key=lambda s: s["published_iso"], reverse=True)
    return uniq[:max_items]


async def fetch_topic_news(topic: str, days: int = 7, max_items: int = 20) -> List[dict]:
    return await asyncio.to_thread(fetch_topic_news_sync, topic, days, max_items)


def format_news_for_llm(stories: List[dict]) -> str:
    lines = ["REAL NEWS HEADLINES (fetched just now, with dates and sources):"]
    for i, s in enumerate(stories, 1):
        dt = _parse_date(s["published_iso"])
        when = dt.strftime("%d %b %Y") if dt else "recent"
        lines.append(f"{i}. [{when}] {s['title']} (source: {s['source']}) link: {s['link']}")
    return "\n".join(lines)


NEWS_SYSTEM_PROMPT = (
    "You are S.T.E.W's news editor. The user asked for news. You are given ONLY real, "
    "dated headlines fetched live from Google News and reputable tech RSS feeds.\n"
    "RULES:\n"
    "1. Use ONLY the provided headlines. NEVER invent stories, dates, quotes, or sources.\n"
    "2. Pick the top 5 most important/interesting stories and summarize each in 1-2 plain sentences.\n"
    "3. Attribute every story: mention the publication and the date, e.g. (TechCrunch, 14 Sept).\n"
    "4. NEVER describe how to build a news pipeline, script, or system. NEVER mention agents, "
    "workflows, RSS feeds setup, PDF generation steps, or 'what you'll get'. The user wants "
    "the NEWS ITSELF, not a lesson about how to gather it.\n"
    "5. Format for Telegram: a short bold header, then a numbered list, then one line: "
    "'Ask me to expand on any story.'\n"
    "6. If fewer than 5 stories are available, use what is there and say so honestly."
)
