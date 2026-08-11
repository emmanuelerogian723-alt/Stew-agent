"""
S.T.E.W Browser Extension — Search API
Free search via DuckDuckGo HTML (no API key needed).
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

def search_duckduckgo(query, num=8):
    ddg_url = "https://html.duckduckgo.com/html/"
    data = urllib.parse.urlencode({"q": query}).encode()
    req = urllib.request.Request(ddg_url, data=data, headers=HEADERS)
    resp = urllib.request.urlopen(req, timeout=15)
    soup = BeautifulSoup(resp.read(), "html.parser")
    results = []
    for r in soup.select(".result")[:num]:
        title_el = r.select_one(".result__title")
        snippet_el = r.select_one(".result__snippet")
        link_el = r.select_one(".result__url")
        if title_el:
            title = title_el.get_text(strip=True)
            snippet = snippet_el.get_text(strip=True) if snippet_el else ""
            link = ""
            if link_el:
                link_text = link_el.get_text(strip=True)
                link = "https://" + link_text if not link_text.startswith("http") else link_text
            # Also try to get actual href
            a_tag = r.select_one(".result__a")
            if a_tag and a_tag.get("href"):
                href = a_tag["href"]
                if href.startswith("http"):
                    link = href
            results.append({"title": title, "snippet": snippet, "url": link})
    return results

def search_bing(query, num=8):
    bing_url = f"https://www.bing.com/search?q={urllib.parse.quote(query)}"
    req = urllib.request.Request(bing_url, headers=HEADERS)
    resp = urllib.request.urlopen(req, timeout=15)
    soup = BeautifulSoup(resp.read(), "html.parser")
    results = []
    for li in soup.select("li.b_algo")[:num]:
        h2 = li.find("h2")
        p = li.find("p")
        a = h2.find("a") if h2 else None
        if a:
            results.append({
                "title": a.get_text(strip=True),
                "snippet": p.get_text(strip=True) if p else "",
                "url": a.get("href", ""),
            })
    return results

def fetch_page_content(url, max_length=3000):
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        resp = urllib.request.urlopen(req, timeout=10)
        html = resp.read().decode("utf-8", errors="replace")
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "iframe"]):
            tag.decompose()
        text = soup.get_text(separator=" ", strip=True)
        return text[:max_length] if text else ""
    except:
        return ""

def handler(request):
    try:
        params = request.query_params
        query = params.get("q", "")
        depth = int(params.get("depth", "1"))
        do_fetch = params.get("fetch", "false").lower() == "true"
        num = int(params.get("num", "8"))
        
        if not query:
            return {"statusCode": 400, "body": json.dumps({"error": "Query required"})}
        
        # Try DuckDuckGo first, then Bing
        results = search_duckduckgo(query, num)
        if not results:
            results = search_bing(query, num)
        
        pages = []
        if do_fetch and results:
            for r in results[:3]:
                content = fetch_page_content(r.get("url", ""))
                if content:
                    pages.append({"url": r.get("url"), "title": r.get("title"), "content": content[:2000]})
        
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"},
            "body": json.dumps({
                "query": query,
                "results": results,
                "pages": pages,
                "count": len(results),
                "grounded": len(results) > 0,
            })
        }
    except Exception as e:
        return {
            "statusCode": 500,
            "headers": {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"},
            "body": json.dumps({"error": str(e), "results": [], "grounded": False})
        }
