"""
S.T.E.W Browser Extension — Page Fetch API
Fetch and extract clean content from any URL.
"""
import json
import urllib.request
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

def handler(request):
    try:
        url = request.query_params.get("url", "")
        if not url:
            return {"statusCode": 400, "body": json.dumps({"error": "URL required"})}
        
        req = urllib.request.Request(url, headers=HEADERS)
        resp = urllib.request.urlopen(req, timeout=15)
        html = resp.read().decode("utf-8", errors="replace")
        soup = BeautifulSoup(html, "html.parser")
        
        title = soup.title.string.strip() if soup.title else "No title"
        
        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "iframe"]):
            tag.decompose()
        
        text = soup.get_text(separator="\n", strip=True)
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        clean_text = "\n".join(lines)[:8000]
        
        links = []
        for a in soup.find_all("a", href=True)[:15]:
            href = a["href"]
            if href.startswith("http"):
                links.append({"text": a.get_text(strip=True)[:80], "url": href})
        
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"},
            "body": json.dumps({
                "url": url,
                "title": title,
                "content": clean_text,
                "links": links,
                "word_count": len(clean_text.split()),
            })
        }
    except Exception as e:
        return {
            "statusCode": 500,
            "headers": {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"},
            "body": json.dumps({"error": str(e), "url": url})
        }
