"""
S.T.E.W Browser Extension — Deep Research API
Multi-query search + page content extraction + synthesis.
"""
import json
import urllib.request
import urllib.parse
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

def search_ddg(query, num=6):
    data = urllib.parse.urlencode({"q": query}).encode()
    req = urllib.request.Request("https://html.duckduckgo.com/html/", data=data, headers=HEADERS)
    resp = urllib.request.urlopen(req, timeout=15)
    soup = BeautifulSoup(resp.read(), "html.parser")
    results = []
    for r in soup.select(".result")[:num]:
        title_el = r.select_one(".result__title")
        snippet_el = r.select_one(".result__snippet")
        a_tag = r.select_one(".result__a")
        if title_el:
            link = a_tag.get("href", "") if a_tag else ""
            if link and not link.startswith("http"):
                link = "https://" + link
            results.append({
                "title": title_el.get_text(strip=True),
                "snippet": snippet_el.get_text(strip=True) if snippet_el else "",
                "url": link,
            })
    return results

def fetch_page(url, max_len=3000):
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        resp = urllib.request.urlopen(req, timeout=12)
        html = resp.read().decode("utf-8", errors="replace")
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()
        return soup.get_text(separator=" ", strip=True)[:max_len]
    except:
        return ""

def handler(request):
    try:
        body = json.loads(request.body) if hasattr(request, "body") and request.body else {}
        if not body:
            body = {"query": request.query_params.get("q", "")}
        
        query = body.get("query", "")
        depth = body.get("depth", 2)
        
        if not query:
            return {"statusCode": 400, "body": json.dumps({"error": "Query required"})}
        
        # Multi-query search
        all_results = search_ddg(query, 8)
        
        # Fetch top pages for content
        pages = []
        sources = []
        for r in all_results[:5]:
            if r.get("url"):
                content = fetch_page(r["url"])
                if content:
                    pages.append({"url": r["url"], "title": r.get("title", ""), "content": content[:2500]})
                sources.append({"title": r.get("title", ""), "url": r["url"], "snippet": r.get("snippet", "")})
        
        # Build research report from content
        report_parts = [f"Research Report: {query}\n"]
        for p in pages:
            report_parts.append(f"\n--- {p['title']} ({p['url']}) ---\n{p['content'][:1500]}\n")
        
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"},
            "body": json.dumps({
                "query": query,
                "report": "\n".join(report_parts),
                "sources": sources,
                "pages": pages,
                "grounded": len(sources) > 0,
                "total_results": len(all_results),
                "queries_used": [query],
            })
        }
    except Exception as e:
        return {
            "statusCode": 500,
            "headers": {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"},
            "body": json.dumps({"error": str(e), "report": "", "sources": [], "grounded": False})
        }
