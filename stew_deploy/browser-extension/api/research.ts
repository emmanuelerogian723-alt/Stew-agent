import type { VercelRequest, VercelResponse } from '@vercel/node';

const HEADERS = {
  'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
  'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
  'Accept-Language': 'en-US,en;q=0.9',
};

export default async function handler(req: VercelRequest, res: VercelResponse) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');

  if (req.method === 'OPTIONS') return res.status(200).end();

  const body = req.body || {};
  const query = body.query || (req.query.q as string) || '';
  const depth = body.depth || 2;

  if (!query) {
    return res.status(400).json({ error: 'Query required', report: '', sources: [] });
  }

  try {
    // Search via DuckDuckGo
    const searchBody = new URLSearchParams({ q: query }).toString();
    const searchResp = await fetch('https://html.duckduckgo.com/html/', {
      method: 'POST',
      headers: { ...HEADERS, 'Content-Type': 'application/x-www-form-urlencoded' },
    });
    const searchHtml = await searchResp.text();

    const linkRegex = /<a[^>]*class="result__a"[^>]*href="([^"]*)"[^>]*>([\s\S]*?)<\/a>/g;
    const snippetRegex = /<a[^>]*class="result__snippet"[^>*>([\s\S]*?)<\/a>/g;

    const sources: any[] = [];
    let match;
    while ((match = linkRegex.exec(searchHtml)) !== null && sources.length < 6) {
      let url = match[1];
      const uddgMatch = url.match(/uddg=([^&]+)/);
      if (uddgMatch) url = decodeURIComponent(uddgMatch[1]);
      sources.push({ title: match[2].replace(/<[^>]*>/g, '').trim(), url, snippet: '' });
    }

    let sm;
    let sIdx = 0;
    while ((sm = snippetRegex.exec(searchHtml)) !== null && sIdx < sources.length) {
      sources[sIdx].snippet = sm[1].replace(/<[^>]*>/g, '').trim();
      sIdx++;
    }

    // Fetch top pages for content
    const pages: any[] = [];
    const reportParts: string[] = [`Research: ${query}\n`];

    for (const s of sources.slice(0, 4)) {
      try {
        const pageResp = await fetch(s.url, { headers: HEADERS, signal: AbortSignal.timeout(10000) });
        const pageHtml = await pageResp.text();
        const content = pageHtml
          .replace(/<script[\s\S]*?<\/script>/g, '')
          .replace(/<style[\s\S]*?<\/style>/g, '')
          .replace(/<[^>]*>/g, ' ')
          .replace(/\s+/g, ' ')
          .trim()
          .slice(0, 2500);

        if (content.length > 100) {
          pages.push({ url: s.url, title: s.title, content });
          reportParts.push(`\n--- ${s.title} (${s.url}) ---\n${content.slice(0, 1500)}\n`);
        }
      } catch { /* skip failed pages */ }
    }

    return res.status(200).json({
      query,
      report: reportParts.join('\n'),
      sources,
      pages,
      grounded: sources.length > 0,
      total_results: sources.length,
      queries_used: [query],
    });
  } catch (error: any) {
    return res.status(500).json({
      error: error.message,
      report: '',
      sources: [],
      pages: [],
      grounded: false,
    });
  }
}
