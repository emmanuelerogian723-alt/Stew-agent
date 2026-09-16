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

  const url = (req.query.url as string) || '';
  if (!url) {
    return res.status(400).json({ error: 'URL required' });
  }

  try {
    // Wikipedia special case
    if (url.includes('wikipedia.org')) {
      const wikiMatch = url.match(/\/wiki\/(.+?)(?:\?|#|$)/);
      if (wikiMatch) {
        const article = wikiMatch[1];
        const wikiResp = await fetch(
          `https://en.wikipedia.org/api/rest_v1/page/summary/${article}`,
          { headers: { 'User-Agent': 'S.T.E.W-Agent/6.0 (https://stew-agent.onrender.com)', 'Accept': 'application/json' } }
        );
        if (wikiResp.ok) {
          const wikiData = await wikiResp.json() as any;
          return res.status(200).json({
            url,
            title: wikiData.title,
            content: wikiData.extract || '',
            links: [],
            word_count: (wikiData.extract || '').split(/\s+/).length,
            source: 'wikipedia_api',
          });
        }
      }
    }

    const resp = await fetch(url, { headers: HEADERS, signal: AbortSignal.timeout(15000) });
    const html = await resp.text();

    // Extract title
    const titleMatch = html.match(/<title[^>]*>(.*?)<\/title>/);
    const title = titleMatch ? titleMatch[1].trim() : 'No title';

    // Strip tags and extract text
    const text = html
      .replace(/<script[\s\S]*?<\/script>/g, '')
      .replace(/<style[\s\S]*?<\/style>/g, '')
      .replace(/<nav[\s\S]*?<\/nav>/g, '')
      .replace(/<footer[\s\S]*?<\/footer>/g, '')
      .replace(/<[^>]*>/g, '\n')
      .split('\n')
      .map(l => l.trim())
      .filter(l => l.length > 0)
      .join('\n')
      .slice(0, 8000);

    // Extract links
    const links: any[] = [];
    const linkRegex = /<a[^>]*href="(https?:\/\/[^"]*)"[^>]*>(.*?)<\/a>/g;
    let match;
    while ((match = linkRegex.exec(html)) !== null && links.length < 15) {
      links.push({ text: match[2].replace(/<[^>]*>/g, '').trim().slice(0, 80), url: match[1] });
    }

    return res.status(200).json({
      url,
      title,
      content: text,
      links,
      word_count: text.split(/\s+/).length,
    });
  } catch (error: any) {
    return res.status(500).json({ error: error.message, url });
  }
}
