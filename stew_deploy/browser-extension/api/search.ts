import type { VercelRequest, VercelResponse } from '@vercel/node';

const HEADERS = {
  'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
  'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
  'Accept-Language': 'en-US,en;q=0.9',
};

interface SearchResult {
  title: string;
  url: string;
  snippet: string;
}

async function searchDuckDuckGo(query: string, num: number = 8): Promise<SearchResult[]> {
  const body = new URLSearchParams({ q: query }).toString();
  const resp = await fetch('https://html.duckduckgo.com/html/', {
    method: 'POST',
    headers: { ...HEADERS, 'Content-Type': 'application/x-www-form-urlencoded' },
  });
  const html = await resp.text();
  const results: SearchResult[] = [];

  // Parse DDG HTML results
  const linkRegex = /<a[^>]*class="result__a"[^>]*href="([^"]*)"[^>]*>([\s\S]*?)<\/a>/g;
  const snippetRegex = /<a[^>]*class="result__snippet"[^>*>([\s\S]*?)<\/a>/g;

  const titles: string[] = [];
  const links: string[] = [];
  const snippets: string[] = [];

  let match;
  while ((match = linkRegex.exec(html)) !== null && links.length < num) {
    let url = match[1];
    // DDG wraps URLs in a redirect
    const uddgMatch = url.match(/uddg=([^&]+)/);
    if (uddgMatch) url = decodeURIComponent(uddgMatch[1]);
    links.push(url);
    titles.push(match[2].replace(/<[^>]*>/g, '').trim());
  }

  while ((match = snippetRegex.exec(html)) !== null && snippets.length < num) {
    snippets.push(match[1].replace(/<[^>]*>/g, '').trim());
  }

  for (let i = 0; i < Math.min(titles.length, num); i++) {
    results.push({
      title: titles[i] || '',
      url: links[i] || '',
      snippet: snippets[i] || '',
    });
  }

  return results;
}

async function searchBing(query: string, num: number = 8): Promise<SearchResult[]> {
  const url = `https://www.bing.com/search?q=${encodeURIComponent(query)}`;
  const resp = await fetch(url, { headers: HEADERS });
  const html = await resp.text();
  const results: SearchResult[] = [];

  const algoRegex = /<li class="b_algo"[\s\S]*?<h2><a[^>]*href="([^"]*)"[^>]*>(.*?)<\/a><\/h2>[\s\S]*?<p[^>]*>(.*?)<\/p>/g;
  let match;
  while ((match = algoRegex.exec(html)) !== null && results.length < num) {
    results.push({
      title: match[2].replace(/<[^>]*>/g, '').trim(),
      url: match[1],
      snippet: match[3].replace(/<[^>]*>/g, '').trim(),
    });
  }

  return results;
}

async function fetchPageContent(url: string, maxLen: number = 3000): Promise<string> {
  try {
    const resp = await fetch(url, { headers: HEADERS, signal: AbortSignal.timeout(10000) });
    const html = await resp.text();
    return html
      .replace(/<script[\s\S]*?<\/script>/g, '')
      .replace(/<style[\s\S]*?<\/style>/g, '')
      .replace(/<nav[\s\S]*?<\/nav>/g, '')
      .replace(/<footer[\s\S]*?<\/footer>/g, '')
      .replace(/<[^>]*>/g, ' ')
      .replace(/\s+/g, ' ')
      .trim()
      .slice(0, maxLen);
  } catch {
    return '';
  }
}

export default async function handler(req: VercelRequest, res: VercelResponse) {
  // CORS
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');

  if (req.method === 'OPTIONS') return res.status(200).end();

  const query = (req.query.q as string) || (req.body?.query as string) || '';
  const doFetch = ((req.query.fetch as string) || 'false').toLowerCase() === 'true';
  const num = parseInt((req.query.num as string) || '8');

  if (!query) {
    return res.status(400).json({ error: 'Query required', results: [] });
  }

  try {
    let results = await searchDuckDuckGo(query, num);
    if (results.length === 0) {
      results = await searchBing(query, num);
    }

    const pages: any[] = [];
    if (doFetch && results.length > 0) {
      for (const r of results.slice(0, 3)) {
        const content = await fetchPageContent(r.url, 2000);
        if (content.length > 100) {
          pages.push({ url: r.url, title: r.title, content });
        }
      }
    }

    return res.status(200).json({
      query,
      results,
      pages,
      count: results.length,
      grounded: results.length > 0,
      source: 'stew_browser_extension',
    });
  } catch (error: any) {
    return res.status(500).json({
      error: error.message,
      results: [],
      pages: [],
      grounded: false,
    });
  }
}
