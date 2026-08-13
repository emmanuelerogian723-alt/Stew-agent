// S.T.E.W Browser Agent — Background Service Worker (Manifest V3)
// Free, open-source browsing engine — like Comet by Perplexity
// Uses: chrome.tabs, chrome.scripting, chrome.webNavigation, chrome.storage
// Built-in free search: DuckDuckGo, Bing, SearXNG, Wikipedia
// No API key required for basic functionality

// ============ STATE MANAGEMENT ============
let agentState = {
  active: false,
  currentTask: null,
  currentTabUrl: null,
  currentTabId: null,
  sseConnection: null,
};

// ============ FREE SEARCH ENGINE (Built-in) ============
// Multiple free search engines — no API key, no Google dependency
// Runs in the USER'S BROWSER so no datacenter IP blocking
const FREE_SEARCH_ENGINES = {
  duckduckgo: {
    name: 'DuckDuckGo',
    search: async (query, numResults = 8) => {
      try {
        const resp = await fetch('https://html.duckduckgo.com/html/', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/x-www-form-urlencoded',
          },
          body: `q=${encodeURIComponent(query)}`
        });
        const html = await resp.text();
        const parser = new DOMParser();
        const doc = parser.parseFromString(html, 'text/html');
        const results = [];
        const resultElements = doc.querySelectorAll('.result__body, .result');
        let count = 0;
        for (const el of resultElements) {
          if (count >= numResults) break;
          const titleEl = el.querySelector('.result__title a, .result__a');
          const snippetEl = el.querySelector('.result__snippet');
          if (titleEl) {
            let link = titleEl.getAttribute('href') || '';
            if (link.includes('uddg=')) {
              link = decodeURIComponent(link.split('uddg=')[1].split('&')[0]);
            } else if (link.startsWith('//')) {
              link = 'https:' + link;
            }
            results.push({
              title: titleEl.textContent.trim(),
              link: link,
              snippet: snippetEl ? snippetEl.textContent.trim() : '',
              source: 'duckduckgo',
            });
            count++;
          }
        }
        return results;
      } catch (e) {
        console.error('[S.T.E.W] DuckDuckGo search error:', e);
        return [];
      }
    }
  },

  duckduckgo_lite: {
    name: 'DuckDuckGo Lite',
    search: async (query, numResults = 8) => {
      try {
        const resp = await fetch(
          `https://lite.duckduckgo.com/lite/?q=${encodeURIComponent(query)}&kl=us-en`,
          { headers: { 'Accept': 'text/html' } }
        );
        const html = await resp.text();
        const parser = new DOMParser();
        const doc = parser.parseFromString(html, 'text/html');
        const results = [];
        // Lite uses table-based layout
        const links = doc.querySelectorAll('a.result-link, a[target="_self"]');
        let count = 0;
        for (const a of links) {
          if (count >= numResults) break;
          let href = a.getAttribute('href') || '';
          const title = a.textContent.trim();
          if (!title || !href || href.includes('duckduckgo.com')) continue;
          if (href.startsWith('//')) href = 'https:' + href;
          // Find snippet in adjacent table row
          let snippet = '';
          const tr = a.closest('tr');
          if (tr) {
            const nextTr = tr.nextElementSibling;
            if (nextTr) snippet = nextTr.textContent.trim().slice(0, 200);
          }
          results.push({ title, link: href, snippet, source: 'duckduckgo_lite' });
          count++;
        }
        return results;
      } catch (e) {
        console.error('[S.T.E.W] DDG Lite error:', e);
        return [];
      }
    }
  },

  google: {
    name: 'Google',
    search: async (query, numResults = 8) => {
      // From a Chrome extension, Google scraping works (user's browser IP, not datacenter)
      try {
        const resp = await fetch(
          `https://www.google.com/search?q=${encodeURIComponent(query)}&num=${numResults}&hl=en`,
          {
            headers: {
              'Accept': 'text/html,application/xhtml+xml',
              'Accept-Language': 'en-US,en;q=0.9',
            }
          }
        );
        const html = await resp.text();
        const parser = new DOMParser();
        const doc = parser.parseFromString(html, 'text/html');
        const results = [];
        // Google results are in div.g or div[data-ved] containers
        const resultDivs = doc.querySelectorAll('div.g, div[data-ved]');
        let count = 0;
        for (const div of resultDivs) {
          if (count >= numResults) break;
          // Find the title link (usually h3 > a)
          const h3 = div.querySelector('h3');
          const a = h3 ? h3.closest('a') : div.querySelector('a[href]');
          if (a && h3) {
            let href = a.getAttribute('href') || '';
            if (href.startsWith('/url?q=')) {
              href = decodeURIComponent(href.split('/url?q=')[1].split('&')[0]);
            } else if (href.startsWith('/search?')) {
              continue; // Skip Google internal links
            }
            if (!href.startsWith('http')) continue;
            // Find snippet
            const snippetEl = div.querySelector('.VwiC3b, [data-sokoban-container], span.aCOpRe');
            const snippet = snippetEl ? snippetEl.textContent.trim() : '';
            results.push({
              title: h3.textContent.trim(),
              link: href,
              snippet: snippet,
              source: 'google',
            });
            count++;
          }
        }
        return results;
      } catch (e) {
        console.error('[S.T.E.W] Google search error:', e);
        return [];
      }
    }
  },

  bing: {
    name: 'Bing',
    search: async (query, numResults = 8) => {
      try {
        const resp = await fetch(`https://www.bing.com/search?q=${encodeURIComponent(query)}&count=${numResults}`, {
          headers: {
            'Accept': 'text/html,application/xhtml+xml',
            'Accept-Language': 'en-US,en;q=0.9',
          },
        });
        const html = await resp.text();
        const parser = new DOMParser();
        const doc = parser.parseFromString(html, 'text/html');
        const results = [];
        // Bing results — try multiple selectors for different Bing layouts
        const items = doc.querySelectorAll('li.b_algo, li.b_algo, .b_result');
        let count = 0;
        for (const li of items) {
          if (count >= numResults) break;
          const h2 = li.querySelector('h2');
          const a = h2 ? h2.querySelector('a') : li.querySelector('a[href]');
          if (a) {
            const href = a.getAttribute('href') || '';
            if (!href.startsWith('http')) continue;
            // Try multiple snippet selectors
            const snippetEl = li.querySelector('p, .b_caption p, .b_lineclamp');
            results.push({
              title: a.textContent.trim(),
              link: href,
              snippet: snippetEl ? snippetEl.textContent.trim() : '',
              source: 'bing',
            });
            count++;
          }
        }
        // Fallback: if no results from structured parsing, try finding any links
        if (results.length === 0) {
          const allLinks = doc.querySelectorAll('a[href]');
          for (const a of allLinks) {
            if (count >= numResults) break;
            const href = a.getAttribute('href') || '';
            if (href.startsWith('http') && !href.includes('bing.com') && !href.includes('microsoft.com')) {
              const h3 = a.querySelector('h3') || a.closest('h2');
              const title = h3 ? h3.textContent.trim() : a.textContent.trim();
              if (title && title.length > 10) {
                results.push({ title, link: href, snippet: '', source: 'bing' });
                count++;
              }
            }
          }
        }
        return results;
      } catch (e) {
        console.error('[S.T.E.W] Bing search error:', e);
        return [];
      }
    }
  },

  wikipedia: {
    name: 'Wikipedia',
    search: async (query, numResults = 5) => {
      try {
        const resp = await fetch(
          `https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch=${encodeURIComponent(query)}&format=json&srlimit=${numResults}&origin=*`,
          { headers: { 'Accept': 'application/json' } }
        );
        const data = await resp.json();
        const searchResults = data.query?.search || [];
        const results = [];
        for (const r of searchResults) {
          try {
            const summaryResp = await fetch(
              `https://en.wikipedia.org/api/rest_v1/page/summary/${encodeURIComponent(r.title.replace(/ /g, '_'))}`,
              { headers: { 'Accept': 'application/json' } }
            );
            if (summaryResp.ok) {
              const summary = await summaryResp.json();
              results.push({
                title: summary.title || r.title,
                link: summary.content_urls?.desktop?.page || `https://en.wikipedia.org/wiki/${r.title.replace(/ /g, '_')}`,
                snippet: summary.extract || r.snippet || '',
                source: 'wikipedia',
              });
            }
          } catch (e) { /* skip */ }
        }
        return results;
      } catch (e) {
        console.error('[S.T.E.W] Wikipedia search error:', e);
        return [];
      }
    }
  },

  allorigins_proxy: {
    name: 'AllOrigins Proxy',
    search: async (query, numResults = 8) => {
      // Fallback: use allorigins proxy to fetch DuckDuckGo if direct fails
      try {
        const ddgUrl = `https://html.duckduckgo.com/html/?q=${encodeURIComponent(query)}`;
        const proxyUrl = `https://api.allorigins.win/raw?url=${encodeURIComponent(ddgUrl)}`;
        const resp = await fetch(proxyUrl);
        const html = await resp.text();
        const parser = new DOMParser();
        const doc = parser.parseFromString(html, 'text/html');
        const results = [];
        const resultElements = doc.querySelectorAll('.result__body, .result');
        let count = 0;
        for (const el of resultElements) {
          if (count >= numResults) break;
          const titleEl = el.querySelector('.result__title a, .result__a');
          const snippetEl = el.querySelector('.result__snippet');
          if (titleEl) {
            let link = titleEl.getAttribute('href') || '';
            if (link.includes('uddg=')) {
              link = decodeURIComponent(link.split('uddg=')[1].split('&')[0]);
            } else if (link.startsWith('//')) {
              link = 'https:' + link;
            }
            results.push({
              title: titleEl.textContent.trim(),
              link: link,
              snippet: snippetEl ? snippetEl.textContent.trim() : '',
              source: 'allorigins_proxy',
            });
            count++;
          }
        }
        return results;
      } catch (e) {
        console.error('[S.T.E.W] AllOrigins proxy search error:', e);
        return [];
      }
    }
  }
};

// ============ MULTI-ENGINE SEARCH ============
// Searches all free engines in parallel, merges and deduplicates results
// Runs in the user's browser so no datacenter IP blocking
async function freeSearch(query, numResults = 10) {
  console.log('[S.T.E.W] Free search for:', query);
  
  // Primary engines — run in parallel (user's browser, no IP blocking)
  const engines = [
    FREE_SEARCH_ENGINES.duckduckgo.search(query, numResults),
    FREE_SEARCH_ENGINES.google.search(query, numResults),
    FREE_SEARCH_ENGINES.bing.search(query, numResults),
  ];

  const allResults = await Promise.allSettled(engines);
  
  // Merge and deduplicate
  const merged = [];
  const seenUrls = new Set();
  for (const result of allResults) {
    if (result.status === 'fulfilled' && result.value) {
      for (const r of result.value) {
        if (r.link && !seenUrls.has(r.link)) {
          seenUrls.add(r.link);
          merged.push(r);
        }
      }
    }
  }

  // If primary engines returned few results, try fallbacks
  if (merged.length < 3) {
    console.log('[S.T.E.W] Primary engines returned few results, trying fallbacks...');
    const fallbacks = [
      FREE_SEARCH_ENGINES.duckduckgo_lite.search(query, numResults),
      FREE_SEARCH_ENGINES.allorigins_proxy.search(query, numResults),
    ];
    const fallbackResults = await Promise.allSettled(fallbacks);
    for (const result of fallbackResults) {
      if (result.status === 'fulfilled' && result.value) {
        for (const r of result.value) {
          if (r.link && !seenUrls.has(r.link)) {
            seenUrls.add(r.link);
            merged.push(r);
          }
        }
      }
    }
  }

  // If still no results, try Wikipedia
  if (merged.length === 0) {
    console.log('[S.T.E.W] No results from search engines, trying Wikipedia...');
    const wikiResults = await FREE_SEARCH_ENGINES.wikipedia.search(query, 5);
    merged.push(...wikiResults);
  }

  console.log(`[S.T.E.W] Free search found ${merged.length} results`);
  return merged.slice(0, numResults);
}

// ============ FREE PAGE READER ============
// Reads any URL and extracts content — uses Jina AI free reader API
async function freeReadPage(url) {
  console.log('[S.T.E.W] Reading page:', url);
  
  // Method 1: Jina AI free reader (reads any URL, bypasses blocks)
  try {
    const jinaResp = await fetch(`https://r.jina.ai/${url}`, {
      headers: {
        'Accept': 'text/plain',
        'User-Agent': 'S.T.E.W-Agent/1.0',
      },
    });
    if (jinaResp.ok) {
      const text = await jinaResp.text();
      if (text && text.length > 100) {
        return {
          url: url,
          title: extractTitleFromJina(text, url),
          content: text.slice(0, 10000),
          word_count: text.split(/\s+/).length,
          source: 'jina_reader',
          success: true,
        };
      }
    }
  } catch (e) {
    console.log('[S.T.E.W] Jina reader failed, trying direct fetch:', e);
  }

  // Method 2: Direct fetch + parse HTML
  try {
    const resp = await fetch(url, {
      headers: {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
      },
    });
    const html = await resp.text();
    const parser = new DOMParser();
    const doc = parser.parseFromString(html, 'text/html');
    
    // Remove scripts, styles, ads
    doc.querySelectorAll('script, style, nav, footer, aside, iframe, noscript').forEach(el => el.remove());
    
    const title = doc.querySelector('title')?.textContent || url;
    const metaDesc = doc.querySelector('meta[name="description"]')?.getAttribute('content') || '';
    
    // Get main content
    const main = doc.querySelector('main') || doc.querySelector('article') || doc.querySelector('body');
    const text = main ? main.textContent.replace(/\s+/g, ' ').trim() : doc.body.textContent.replace(/\s+/g, ' ').trim();
    
    // Extract links
    const links = [];
    doc.querySelectorAll('a[href]').forEach(a => {
      const href = a.href;
      const label = a.textContent.trim();
      if (href.startsWith('http') && label && links.length < 20) {
        links.push({ text: label.slice(0, 80), url: href });
      }
    });

    // Extract forms
    const forms = [];
    doc.querySelectorAll('form').forEach(f => {
      const fields = [];
      f.querySelectorAll('input, textarea, select').forEach(i => {
        const type = i.getAttribute('type') || i.tagName.toLowerCase();
        if (!['hidden', 'submit', 'button', 'reset'].includes(type)) {
          fields.push({
            name: i.getAttribute('name') || i.id || 'unknown',
            type: type,
            placeholder: i.getAttribute('placeholder') || '',
            selector: getUniqueSelectorSync(i),
          });
        }
      });
      forms.push({
        action: f.action || url,
        method: (f.method || 'GET').toUpperCase(),
        fields: fields,
      });
    });

    return {
      url: url,
      title: title.trim(),
      description: metaDesc,
      content: text.slice(0, 10000),
      links: links,
      forms: forms,
      word_count: text.split(/\s+/).length,
      source: 'direct_fetch',
      success: true,
    };
  } catch (e) {
    console.error('[S.T.E.W] Direct fetch failed:', e);
  }

  // Method 3: AllOrigins CORS proxy (free, open source)
  try {
    const proxyResp = await fetch(`https://api.allorigins.win/raw?url=${encodeURIComponent(url)}`);
    if (proxyResp.ok) {
      const html = await proxyResp.text();
      const parser = new DOMParser();
      const doc = parser.parseFromString(html, 'text/html');
      doc.querySelectorAll('script, style, nav, footer, aside').forEach(el => el.remove());
      const title = doc.querySelector('title')?.textContent || url;
      const text = doc.body ? doc.body.textContent.replace(/\s+/g, ' ').trim() : '';
      return {
        url: url,
        title: title.trim(),
        content: text.slice(0, 10000),
        word_count: text.split(/\s+/).length,
        source: 'allorigins_proxy',
        success: true,
      };
    }
  } catch (e) {
    console.error('[S.T.E.W] AllOrigins proxy failed:', e);
  }

  return { url: url, success: false, error: 'Could not read this page. The site may be blocking automated access.' };
}

function extractTitleFromJina(text, url) {
  // Jina reader returns content with "Title:" prefix usually
  const titleMatch = text.match(/^Title:\s*(.+)/m);
  if (titleMatch) return titleMatch[1].trim();
  // Fallback to URL
  try {
    const u = new URL(url);
    return u.hostname;
  } catch { return url; }
}

// ============ INITIALIZATION ============
chrome.runtime.onInstalled.addListener(async () => {
  console.log('[S.T.E.W] Extension installed');
  
  const settings = await chrome.storage.local.get(['stew_settings']);
  if (!settings.stew_settings) {
    await chrome.storage.local.set({
      stew_settings: {
        serverUrl: 'https://stew-agent.onrender.com',
        apiKey: '',  // No hardcoded key — user enters their own
        autoSummarize: false,
        memoryEnabled: true,
        maxMemoryItems: 500,
        planningDepth: 3,
        scrollSpeed: 'natural',
        humanLikeDelay: true,
        contextMenuEnabled: true,
        useFreeSearch: true,  // Use built-in free search engines
      }
    });
  }
  
  createContextMenus();
});

// ============ CONTEXT MENUS ============
function createContextMenus() {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({
      id: 'stew-summarize',
      title: 'S.T.E.W: Summarize this page',
      contexts: ['page']
    });
    chrome.contextMenus.create({
      id: 'stew-extract',
      title: 'S.T.E.W: Extract key information',
      contexts: ['page']
    });
    chrome.contextMenus.create({
      id: 'stew-ask',
      title: 'S.T.E.W: Ask about this page',
      contexts: ['page']
    });
    chrome.contextMenus.create({
      id: 'stew-research',
      title: 'S.T.E.W: Research this topic',
      contexts: ['selection']
    });
    chrome.contextMenus.create({
      id: 'stew-search',
      title: 'S.T.E.W: Search the web',
      contexts: ['selection']
    });
    chrome.contextMenus.create({
      id: 'stew-action',
      title: 'S.T.E.W: Automate a task on this page',
      contexts: ['page']
    });
  });
}

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  switch(info.menuItemId) {
    case 'stew-summarize':
      executeAgentAction(tab.id, 'summarize', { url: tab.url, title: tab.title });
      break;
    case 'stew-extract':
      executeAgentAction(tab.id, 'extract', { url: tab.url, title: tab.title });
      break;
    case 'stew-ask':
      showAskDialog(tab.id);
      break;
    case 'stew-research':
      executeAgentAction(tab.id, 'research', { query: info.selectionText });
      break;
    case 'stew-search':
      executeAgentAction(tab.id, 'google_search', { query: info.selectionText });
      break;
    case 'stew-action':
      showActionDialog(tab.id);
      break;
  }
});

// ============ MEMORY MANAGEMENT ============
async function saveToMemory(key, value, type = 'general') {
  const settings = await getSettings();
  if (settings.memoryEnabled === false) return;
  
  const stored = await chrome.storage.local.get(['stew_memory']);
  const memory = stored.stew_memory || { items: [] };
  
  const item = {
    id: crypto.randomUUID(),
    key,
    value,
    type,
    timestamp: Date.now(),
    url: agentState.currentTabUrl || '',
  };
  
  memory.items.unshift(item);
  if (memory.items.length > (settings.maxMemoryItems || 500)) {
    memory.items = memory.items.slice(0, settings.maxMemoryItems || 500);
  }
  
  await chrome.storage.local.set({ stew_memory: memory });
}

async function getMemory(type = null, limit = 10) {
  const stored = await chrome.storage.local.get(['stew_memory']);
  const memory = stored.stew_memory || { items: [] };
  if (type) return memory.items.filter(i => i.type === type).slice(0, limit);
  return memory.items.slice(0, limit);
}

async function clearMemory() {
  await chrome.storage.local.set({ stew_memory: { items: [] } });
}

// ============ AGENT ACTIONS ============
async function executeAgentAction(tabId, action, params) {
  const settings = await getSettings();
  agentState.active = true;
  showNotification('S.T.E.W Agent', `Starting: ${action}`);
  
  try {
    switch(action) {
      case 'summarize':
        return await actionSummarize(tabId, params, settings);
      case 'extract':
        return await actionExtract(tabId, params, settings);
      case 'research':
        return await actionResearch(tabId, params, settings);
      case 'google_search':
      case 'search':
        return await actionSearch(tabId, params, settings);
      case 'browse':
      case 'browse_page':
        return await actionBrowsePage(tabId, params, settings);
      case 'automate':
        return await actionAutomate(tabId, params, settings);
      case 'navigate':
        return await actionNavigate(tabId, params, settings);
      case 'fill_form':
        return await actionFillForm(tabId, params, settings);
      case 'multi_step':
        return await actionMultiStep(tabId, params, settings);
      default:
        console.error('[S.T.E.W] Unknown action:', action);
    }
  } catch (e) {
    console.error('[S.T.E.W] Action failed:', e);
    showNotification('S.T.E.W Error', e.message);
  } finally {
    agentState.active = false;
  }
}

// ============ SUMMARIZE ACTION ============
async function actionSummarize(tabId, params, settings) {
  const [result] = await chrome.scripting.executeScript({
    target: { tabId },
    func: extractPageContent
  });
  
  const pageContent = result.result;
  
  // Send to S.T.E.W server for summarization
  const resp = await fetch(`${settings.serverUrl}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      message: `Summarize this webpage:\n\nTitle: ${pageContent.title}\nURL: ${pageContent.url}\n\nContent:\n${pageContent.text.slice(0, 5000)}`,
      api_key: settings.apiKey || undefined,
      context: { action: 'summarize', url: pageContent.url }
    })
  });
  
  const data = await resp.json();
  const summary = data.response || 'No summary generated';
  
  await saveToMemory(pageContent.url, { title: pageContent.title, summary }, 'summary');
  
  chrome.runtime.sendMessage({
    type: 'agent_result',
    action: 'summarize',
    result: summary,
    url: pageContent.url,
    title: pageContent.title
  }).catch(() => {});
  
  showNotification('S.T.E.W Summary Ready', summary.slice(0, 100) + '...');
  return summary;
}

// ============ EXTRACT ACTION ============
async function actionExtract(tabId, params, settings) {
  const [result] = await chrome.scripting.executeScript({
    target: { tabId },
    func: extractPageContent
  });
  
  const pageContent = result.result;
  
  const resp = await fetch(`${settings.serverUrl}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      message: `Extract the key information from this page. List important facts, data points, contacts, prices, dates, and actionable items:\n\nTitle: ${pageContent.title}\nContent:\n${pageContent.text.slice(0, 5000)}`,
      api_key: settings.apiKey || undefined,
      context: { action: 'extract', url: pageContent.url }
    })
  });
  
  const data = await resp.json();
  const extraction = data.response || 'No data extracted';
  
  await saveToMemory(pageContent.url, { title: pageContent.title, extraction }, 'extraction');
  
  chrome.runtime.sendMessage({
    type: 'agent_result',
    action: 'extract',
    result: extraction,
    url: pageContent.url,
    title: pageContent.title
  }).catch(() => {});
  
  showNotification('S.T.E.W Extraction Complete', extraction.slice(0, 100) + '...');
  return extraction;
}

// ============ SEARCH ACTION (Free Multi-Engine) ============
async function actionSearch(tabId, params, settings) {
  const query = params.query || params.text || '';
  if (!query) return 'No query provided';
  
  showNotification('S.T.E.W Search', `Searching: ${query}`);
  
  // Use built-in free search engines (no API key needed)
  const results = await freeSearch(query, 10);
  
  // Build summary
  let summary = `Found ${results.length} results for "${query}":\n\n`;
  results.forEach((r, i) => {
    summary += `${i+1}. ${r.title || 'Untitled'}\n   ${r.link || r.url || ''}\n   ${r.snippet || ''}\n\n`;
  });
  
  // Save to memory
  await saveToMemory(`search:${query}`, { query, results: results.slice(0, 5) }, 'search');
  
  chrome.runtime.sendMessage({
    type: 'agent_result',
    action: 'search',
    result: summary,
    results: results,
    query,
  }).catch(() => {});
  
  showNotification('S.T.E.W Search Complete', `Found ${results.length} results`);
  return summary;
}

// ============ RESEARCH ACTION (Deep Dive) ============
async function actionResearch(tabId, params, settings) {
  const query = params.query;
  if (!query) return 'No query provided';
  
  showNotification('S.T.E.W Research', `Deep diving: ${query}`);
  
  // Step 1: Search with free engines
  const searchResults = await freeSearch(query, 8);
  if (searchResults.length === 0) {
    return 'No results found. Try different keywords.';
  }
  
  let summary = `Research: ${query}\nFound ${searchResults.length} sources.\n\n`;
  
  // Step 2: Read top 3 pages for full content
  const pages = [];
  for (let i = 0; i < Math.min(3, searchResults.length); i++) {
    const result = searchResults[i];
    showNotification('S.T.E.W Research', `Reading source ${i+1}/${Math.min(3, searchResults.length)}: ${result.title?.slice(0, 40) || ''}`);
    
    const pageData = await freeReadPage(result.link || result.url || '');
    if (pageData.success) {
      pages.push({
        url: result.link || result.url,
        title: pageData.title || result.title,
        content: pageData.content?.slice(0, 3000) || '',
      });
      summary += `Source ${i+1}: ${pageData.title || result.title}\n${pageData.content?.slice(0, 500) || result.snippet}\n\n`;
    } else {
      summary += `Source ${i+1}: ${result.title}\n${result.snippet}\n\n`;
    }
  }
  
  // Step 3: Send to S.T.E.W server for synthesis
  try {
    const resp = await fetch(`${settings.serverUrl}/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message: `Research synthesis for: ${query}\n\nHere are the sources I found:\n\n${summary}\n\nProvide a comprehensive, well-structured analysis based on these sources.`,
        api_key: settings.apiKey || undefined,
        context: { action: 'research', query },
      })
    });
    const data = await resp.json();
    const synthesis = data.response || summary;
    
    await saveToMemory(`research:${query}`, { query, pages: pages.length, synthesis: synthesis.slice(0, 500) }, 'research');
    
    chrome.runtime.sendMessage({
      type: 'agent_result',
      action: 'research',
      result: synthesis,
      sources: searchResults,
      pages: pages,
      query,
    }).catch(() => {});
    
    showNotification('S.T.E.W Research Complete', `${pages.length} pages analyzed`);
    return synthesis;
  } catch (e) {
    // Even without server, return raw research
    chrome.runtime.sendMessage({
      type: 'agent_result',
      action: 'research',
      result: summary,
      sources: searchResults,
      pages: pages,
      query,
    }).catch(() => {});
    return summary;
  }
}

// ============ BROWSE PAGE ACTION ============
async function actionBrowsePage(tabId, params, settings) {
  const url = params.url || params.text || '';
  if (!url) return 'No URL provided';
  
  // Normalize URL
  let targetUrl = url.trim();
  if (!targetUrl.startsWith('http')) {
    targetUrl = 'https://' + targetUrl;
  }
  
  showNotification('S.T.E.W Browser', `Reading: ${targetUrl}`);
  
  // Use free page reader
  const pageData = await freeReadPage(targetUrl);
  
  if (pageData.success) {
    chrome.runtime.sendMessage({
      type: 'agent_result',
      action: 'browse',
      result: `${pageData.title}\n\n${pageData.content?.slice(0, 1000) || ''}`,
      ...pageData,
    }).catch(() => {});
    
    showNotification('S.T.E.W Browser', `Read ${pageData.word_count || 0} words`);
    return `${pageData.title}\n\n${pageData.content?.slice(0, 2000) || ''}`;
  } else {
    // Fallback: try reading from the active tab if it's the same URL
    if (tabId) {
      try {
        const [result] = await chrome.scripting.executeScript({
          target: { tabId },
          func: extractPageContent
        });
        if (result.result) {
          return `${result.result.title}\n\n${result.result.text?.slice(0, 2000) || ''}`;
        }
      } catch (e) { /* tab might not be accessible */ }
    }
    return `Could not read ${targetUrl}. ${pageData.error || ''}`;
  }
}

// ============ NAVIGATE ACTION ============
async function actionNavigate(tabId, params, settings) {
  await chrome.tabs.update(tabId, { url: params.url });
  return new Promise((resolve) => {
    chrome.webNavigation.onCompleted.addListener(function listener(details) {
      if (details.tabId === tabId) {
        chrome.webNavigation.onCompleted.removeListener(listener);
        resolve({ status: 'navigated', url: params.url });
      }
    });
  });
}

// ============ AUTOMATE ACTION ============
async function actionAutomate(tabId, params, settings) {
  const task = params.task || params.text || '';
  if (!task) return 'No task provided';
  
  showNotification('S.T.E.W Agent', `Automating: ${task}`);
  
  // Get current page structure
  const [pageResult] = await chrome.scripting.executeScript({
    target: { tabId },
    func: extractPageContent
  });
  const pageContent = pageResult.result;
  
  // Plan the task with S.T.E.W server
  let steps;
  try {
    const planResp = await fetch(`${settings.serverUrl}/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message: `Task: "${task}"\n\nCurrent page: ${pageContent.title}\nURL: ${pageContent.url}\n\nPage elements:\n${pageContent.elements?.slice(0, 2000) || pageContent.text.slice(0, 2000)}\n\nCreate a step-by-step plan as JSON: {"steps": [{"action": "click|type|scroll|wait|extract", "selector": "...", "value": "...", "description": "..."}]}`,
        api_key: settings.apiKey || undefined,
        context: { action: 'automate', task, url: pageContent.url }
      })
    });
    const planData = await planResp.json();
    const planText = planData.response || '{}';
    const jsonMatch = planText.match(/\{[\s\S]*\}/);
    steps = JSON.parse(jsonMatch ? jsonMatch[0] : planText).steps || [{ action: 'extract', description: 'Extract page content' }];
  } catch (e) {
    steps = [{ action: 'extract', selector: 'body', description: 'Extract page content' }];
  }
  
  // Execute each step
  const results = [];
  for (let i = 0; i < steps.length; i++) {
    const step = steps[i];
    showNotification('S.T.E.W Agent', `Step ${i+1}/${steps.length}: ${step.description || step.action}`);
    
    const [execResult] = await chrome.scripting.executeScript({
      target: { tabId },
      func: executeBrowserAction,
      args: [step]
    });
    
    results.push({
      step: i + 1,
      action: step.action,
      description: step.description,
      result: execResult.result
    });
    
    if (settings.humanLikeDelay) {
      await new Promise(r => setTimeout(r, 500 + Math.random() * 1000));
    }
  }
  
  await saveToMemory(task, { steps, results }, 'automation');
  
  chrome.runtime.sendMessage({
    type: 'agent_result',
    action: 'automate',
    result: JSON.stringify(results, null, 2),
    task
  }).catch(() => {});
  
  showNotification('S.T.E.W Automation Complete', `${steps.length} steps executed`);
  return results;
}

// ============ FILL FORM ACTION ============
async function actionFillForm(tabId, params, settings) {
  const formData = params.formData || params.data || {};
  
  const [result] = await chrome.scripting.executeScript({
    target: { tabId },
    func: fillFormOnPage,
    args: [formData]
  });
  
  return result.result;
}

// ============ MULTI-STEP TASK PLANNING ============
async function actionMultiStep(tabId, params, settings) {
  const taskDescription = params.task || '';
  
  const [pageResult] = await chrome.scripting.executeScript({
    target: { tabId },
    func: extractPageContent
  });
  const pageContent = pageResult.result;
  
  // Plan with S.T.E.W server
  let plan;
  try {
    const planResp = await fetch(`${settings.serverUrl}/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message: `Task: "${taskDescription}"\n\nCurrent page: ${pageContent.title}\nURL: ${pageContent.url}\n\nPage elements:\n${pageContent.elements?.slice(0, 2000) || pageContent.text.slice(0, 2000)}\n\nCreate a detailed multi-step plan as JSON: {"steps": [{"action": "...", "selector": "...", "value": "...", "description": "...", "navigate_to": "..."}]}`,
        api_key: settings.apiKey || undefined,
        context: { action: 'multi_step', task: taskDescription, url: pageContent.url }
      })
    });
    const planData = await planResp.json();
    const planText = planData.response || '{}';
    const jsonMatch = planText.match(/\{[\s\S]*\}/);
    plan = JSON.parse(jsonMatch ? jsonMatch[0] : planText);
  } catch (e) {
    plan = { steps: [{ action: 'extract', description: 'Extract page content' }] };
  }
  
  const executionLog = [];
  for (let i = 0; i < (plan.steps || []).length; i++) {
    const step = plan.steps[i];
    showNotification('S.T.E.W Multi-Step', `Step ${i+1}/${plan.steps.length}: ${step.description || step.action}`);
    
    if (step.navigate_to) {
      await chrome.tabs.update(tabId, { url: step.navigate_to });
      await new Promise(r => setTimeout(r, 2000));
    }
    
    if (step.action) {
      const [execResult] = await chrome.scripting.executeScript({
        target: { tabId },
        func: executeBrowserAction,
        args: [step]
      });
      executionLog.push({ step: i + 1, result: execResult.result });
    }
    
    if (settings.humanLikeDelay) {
      await new Promise(r => setTimeout(r, 800 + Math.random() * 1200));
    }
  }
  
  await saveToMemory(taskDescription, { plan, executionLog }, 'multi_step');
  
  chrome.runtime.sendMessage({
    type: 'agent_result',
    action: 'multi_step',
    result: JSON.stringify(executionLog, null, 2),
    task: taskDescription
  }).catch(() => {});
  
  showNotification('S.T.E.W Multi-Step Complete', `${(plan.steps || []).length} steps executed`);
  return executionLog;
}

// ============ MESSAGE HANDLING ============
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  (async () => {
    const settings = await getSettings();
    
    switch(message.type) {
      case 'agent_action':
        const result = await executeAgentAction(sender.tab?.id || message.tabId, message.action, message.params || {});
        sendResponse({ success: true, result });
        break;
        
      case 'get_status':
        sendResponse({
          active: agentState.active,
          currentTask: agentState.currentTask,
          serverUrl: settings.serverUrl,
          memoryEnabled: settings.memoryEnabled,
          useFreeSearch: settings.useFreeSearch !== false,
        });
        break;
        
      case 'get_memory':
        const memory = await getMemory(message.memoryType, message.limit || 10);
        sendResponse({ success: true, memory });
        break;
        
      case 'clear_memory':
        await clearMemory();
        sendResponse({ success: true });
        break;
        
      case 'chat':
        const chatResp = await fetch(`${settings.serverUrl}/chat`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            message: message.message,
            api_key: settings.apiKey || undefined,
            context: message.context || {}
          })
        });
        const chatData = await chatResp.json();
        sendResponse({ success: true, response: chatData.response });
        break;
        
      case 'search':
      case 'google_search':
        // Use built-in free search — no API key needed, never blocked
        const searchResults = await freeSearch(message.query, message.num || 10);
        sendResponse({
          success: true,
          results: searchResults,
          grounded: searchResults.length > 0,
          source: 'free_multi_engine',
        });
        break;
      
      case 'browse_page':
        // Use built-in free page reader
        const pageData = await freeReadPage(message.url);
        sendResponse({ success: pageData.success, ...pageData });
        break;
        
      case 'settings_update':
        await chrome.storage.local.set({ stew_settings: { ...settings, ...message.settings } });
        sendResponse({ success: true });
        break;
        
      default:
        sendResponse({ error: 'Unknown message type' });
    }
  })();
  
  return true; // Keep channel open for async response
});

// ============ WEB NAVIGATION TRACKING ============
chrome.webNavigation.onCompleted.addListener(async (details) => {
  if (details.frameId === 0) {
    agentState.currentTabUrl = details.url;
    console.log('[S.T.E.W] Navigation completed:', details.url);
  }
});

// ============ TAB MANAGEMENT ============
chrome.tabs.onActivated.addListener((activeInfo) => {
  agentState.currentTabId = activeInfo.tabId;
});

// ============ HELPER FUNCTIONS ============
async function getSettings() {
  const stored = await chrome.storage.local.get(['stew_settings']);
  return stored.stew_settings || {
    serverUrl: 'https://stew-agent.onrender.com',
    apiKey: '',
    autoSummarize: false,
    memoryEnabled: true,
    maxMemoryItems: 500,
    useFreeSearch: true,
  };
}

function showNotification(title, message) {
  try {
    chrome.notifications.create({
      type: 'basic',
      iconUrl: 'icons/icon48.png',
      title,
      message: message || '',
      priority: 1
    });
  } catch (e) { /* notifications might not be available */ }
}

function showAskDialog(tabId) {
  chrome.scripting.executeScript({
    target: { tabId },
    func: () => {
      const question = prompt('Ask S.T.E.W about this page:');
      if (question) {
        chrome.runtime.sendMessage({ type: 'agent_action', action: 'ask', params: { question, url: location.href, title: document.title } });
      }
    }
  });
}

function showActionDialog(tabId) {
  chrome.scripting.executeScript({
    target: { tabId },
    func: () => {
      const task = prompt('Describe the task you want S.T.E.W to automate:');
      if (task) {
        chrome.runtime.sendMessage({ type: 'agent_action', action: 'automate', params: { task } });
      }
    }
  });
}

function getUniqueSelectorSync(el) {
  if (el.id) return '#' + el.id;
  if (el.className && typeof el.className === 'string') {
    const classes = el.className.split(' ').filter(c => c.length > 0);
    if (classes.length > 0) return el.tagName.toLowerCase() + '.' + classes.join('.');
  }
  let path = '';
  let current = el;
  while (current && current !== document.body) {
    let selector = current.tagName.toLowerCase();
    if (current.id) { path = '#' + current.id + (path ? ' > ' + path : ''); break; }
    const parent = current.parentElement;
    if (parent) {
      const siblings = Array.from(parent.children).filter(c => c.tagName === current.tagName);
      const index = siblings.indexOf(current);
      if (siblings.length > 1) selector += `:nth-of-type(${index + 1})`;
    }
    path = selector + (path ? ' > ' + path : '');
    current = current.parentElement;
  }
  return path;
}

// ============ INJECTED FUNCTIONS (run in page context) ============
function extractPageContent() {
  const title = document.title;
  const url = location.href;
  
  const walker = document.createTreeWalker(
    document.body, NodeFilter.SHOW_TEXT,
    {
      acceptNode: (node) => {
        const parent = node.parentElement;
        if (!parent) return NodeFilter.FILTER_REJECT;
        const style = window.getComputedStyle(parent);
        if (style.display === 'none' || style.visibility === 'hidden') return NodeFilter.FILTER_REJECT;
        if (parent.tagName === 'SCRIPT' || parent.tagName === 'STYLE') return NodeFilter.FILTER_REJECT;
        return node.textContent.trim() ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
      }
    }
  );
  
  let text = '';
  let node;
  while (node = walker.nextNode()) {
    text += node.textContent.trim() + ' ';
    if (text.length > 8000) break;
  }
  
  const elements = [];
  const interactiveSelectors = 'a, button, input, select, textarea, [role="button"], [role="link"], [role="textbox"], [onclick]';
  document.querySelectorAll(interactiveSelectors).forEach(el => {
    elements.push({
      tag: el.tagName,
      type: el.type || el.role || '',
      text: el.textContent?.trim().slice(0, 50) || el.value?.slice(0, 50) || '',
      href: el.href || '',
      selector: (function(e) {
        if (e.id) return '#' + e.id;
        if (e.className && typeof e.className === 'string') {
          const classes = e.className.split(' ').filter(c => c.length > 0);
          if (classes.length > 0) return e.tagName.toLowerCase() + '.' + classes.join('.');
        }
        let path = ''; let current = e;
        while (current && current !== document.body) {
          let sel = current.tagName.toLowerCase();
          if (current.id) { path = '#' + current.id + (path ? ' > ' + path : ''); break; }
          const parent = current.parentElement;
          if (parent) {
            const siblings = Array.from(parent.children).filter(c => c.tagName === current.tagName);
            const idx = siblings.indexOf(current);
            if (siblings.length > 1) sel += `:nth-of-type(${idx + 1})`;
          }
          path = sel + (path ? ' > ' + path : ''); current = current.parentElement;
        }
        return path;
      })(el),
      position: el.getBoundingClientRect(),
    });
  });
  
  const metaDescription = document.querySelector('meta[name="description"]')?.content || '';
  
  return { title, url, text: text.trim(), elements: elements.slice(0, 50), meta: { description: metaDescription } };
}

function executeBrowserAction(step) {
  return new Promise((resolve) => {
    switch(step.action) {
      case 'click': {
        const el = document.querySelector(step.selector);
        if (el) {
          el.scrollIntoView({ behavior: 'smooth', block: 'center' });
          setTimeout(() => {
            el.click();
            resolve({ success: true, action: 'click', selector: step.selector });
          }, 300);
        } else {
          resolve({ success: false, error: 'Element not found', selector: step.selector });
        }
        break;
      }
      case 'type':
      case 'fill': {
        const el = document.querySelector(step.selector);
        if (el) {
          el.focus();
          el.value = step.value || '';
          el.dispatchEvent(new Event('input', { bubbles: true }));
          el.dispatchEvent(new Event('change', { bubbles: true }));
          resolve({ success: true, action: 'type', selector: step.selector, value: step.value });
        } else {
          resolve({ success: false, error: 'Element not found' });
        }
        break;
      }
      case 'scroll': {
        const direction = step.direction || 'down';
        const amount = step.amount || window.innerHeight * 0.8;
        if (direction === 'down') window.scrollBy(0, amount);
        else window.scrollBy(0, -amount);
        resolve({ success: true, action: 'scroll', direction, amount });
        break;
      }
      case 'wait': {
        const ms = step.duration || 1000;
        setTimeout(() => resolve({ success: true, action: 'wait', duration: ms }), ms);
        break;
      }
      case 'extract': {
        const content = document.body.innerText.slice(0, 5000);
        resolve({ success: true, action: 'extract', content });
        break;
      }
      case 'screenshot': {
        resolve({ success: true, action: 'screenshot', note: 'Screenshot requires additional permissions' });
        break;
      }
      case 'press_enter': {
        const el = document.querySelector(step.selector) || document.activeElement;
        if (el) {
          el.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', keyCode: 13, bubbles: true }));
          el.dispatchEvent(new KeyboardEvent('keyup', { key: 'Enter', code: 'Enter', keyCode: 13, bubbles: true }));
        }
        resolve({ success: true, action: 'press_enter' });
        break;
      }
      default:
        resolve({ success: false, error: 'Unknown action: ' + step.action });
    }
  });
}

function fillFormOnPage(formData) {
  const results = [];
  for (const [selector, value] of Object.entries(formData)) {
    const el = document.querySelector(selector);
    if (el) {
      el.focus();
      el.value = value;
      el.dispatchEvent(new Event('input', { bubbles: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
      results.push({ selector, success: true });
    } else {
      results.push({ selector, success: false, error: 'Not found' });
    }
  }
  return results;
}

console.log('[S.T.E.W] Background service worker loaded — Free Search Engine mode');
