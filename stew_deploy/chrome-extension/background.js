// S.T.E.W Browser Agent — Background Service Worker (Manifest V3)
// Uses: chrome.tabs, chrome.scripting, chrome.webNavigation, chrome.storage, chrome.runtime, chrome.contextMenus
// SSE: connects to S.T.E.W server for real-time updates
// Fetch API: all HTTP communication

const STEW_SERVER = 'https://stew-agent.onrender.com';
const STEW_API_KEY = 'stew_extension_default';

// ============ STATE MANAGEMENT ============
let agentState = {
  active: false,
  currentTask: null,
  taskQueue: [],
  memory: {},
  tabSessions: new Map(),
  sseConnection: null,
};

// ============ INITIALIZATION ============
chrome.runtime.onInstalled.addListener(async () => {
  console.log('[S.T.E.W] Extension installed');
  
  // Initialize default settings
  const settings = await chrome.storage.local.get(['stew_settings']);
  if (!settings.stew_settings) {
    await chrome.storage.local.set({
      stew_settings: {
        serverUrl: STEW_SERVER,
        apiKey: STEW_API_KEY,
        autoSummarize: true,
        memoryEnabled: true,
        maxMemoryItems: 500,
        planningDepth: 3,
        scrollSpeed: 'natural',
        humanLikeDelay: true,
        contextMenuEnabled: true,
      }
    });
  }
  
  // Create context menus
  createContextMenus();
  
  // Start SSE connection
  startSSEConnection();
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
      id: 'stew-action',
      title: 'S.T.E.W: Automate a task on this page',
      contexts: ['page']
    });
  });
}

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  const settings = await getSettings();
  
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
    case 'stew-action':
      showActionDialog(tab.id);
      break;
  }
});

// ============ SSE CONNECTION (Server-Sent Events) ============
async function startSSEConnection() {
  const settings = await getSettings();
  
  try {
    // Use fetch streaming for SSE
    const resp = await fetch(`${settings.serverUrl}/events`, {
      headers: { 'Accept': 'text/event-stream' }
    });
    
    if (resp.ok && resp.body) {
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';
        
        for (const line of lines) {
          if (line.startsWith('data: ')) {
            try {
              const data = JSON.parse(line.slice(6));
              handleSSEEvent(data);
            } catch (e) {
              // Not JSON, skip
            }
          }
        }
      }
    }
  } catch (e) {
    console.log('[S.T.E.W] SSE connection failed, will retry in 30s');
    setTimeout(startSSEConnection, 30000);
  }
}

function handleSSEEvent(data) {
  console.log('[S.T.E.W] SSE event:', data.type);
  // Handle server push events (task updates, notifications, etc.)
  if (data.type === 'task_complete') {
    showNotification('S.T.E.W Task Complete', data.message);
  }
}

// ============ MEMORY MANAGEMENT ============
async function saveToMemory(key, value, type = 'general') {
  const settings = await getSettings();
  if (!settings.memoryEnabled) return;
  
  const stored = await chrome.storage.local.get(['stew_memory']);
  const memory = stored.stew_memory || { items: [], byType: {} };
  
  const item = {
    id: crypto.randomUUID(),
    key,
    value,
    type,
    timestamp: Date.now(),
    url: agentState.currentTabUrl || '',
  };
  
  memory.items.unshift(item);
  
  // Enforce max memory items
  if (memory.items.length > settings.maxMemoryItems) {
    memory.items = memory.items.slice(0, settings.maxMemoryItems);
  }
  
  // Index by type
  if (!memory.byType[type]) memory.byType[type] = [];
  memory.byType[type].unshift(item.id);
  
  await chrome.storage.local.set({ stew_memory: memory });
}

async function getMemory(type = null, limit = 10) {
  const stored = await chrome.storage.local.get(['stew_memory']);
  const memory = stored.stew_memory || { items: [] };
  
  if (type) {
    return memory.items.filter(i => i.type === type).slice(0, limit);
  }
  return memory.items.slice(0, limit);
}

async function clearMemory() {
  await chrome.storage.local.set({ stew_memory: { items: [], byType: {} } });
}

// ============ AGENT ACTIONS ============
async function executeAgentAction(tabId, action, params) {
  const settings = await getSettings();
  agentState.active = true;
  
  // Send start notification
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
        return await actionGoogleSearch(tabId, params, settings);
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
  // Inject content script to extract page content
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
      user_id: 'stew_extension',
      context: { action: 'summarize', url: pageContent.url }
    })
  });
  
  const data = await resp.json();
  const summary = data.response || data.reply || 'No summary generated';
  
  // Save to memory
  await saveToMemory(pageContent.url, { title: pageContent.title, summary }, 'summary');
  
  // Send result to popup/sidebar
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
  
  // Extract key information using S.T.E.W
  const resp = await fetch(`${settings.serverUrl}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      message: `Extract the key information from this page. List important facts, data points, contacts, prices, dates, and actionable items:\n\nTitle: ${pageContent.title}\nContent:\n${pageContent.text.slice(0, 5000)}`,
      user_id: 'stew_extension',
      context: { action: 'extract', url: pageContent.url }
    })
  });
  
  const data = await resp.json();
  const extraction = data.response || data.reply || 'No data extracted';
  
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

// ============ RESEARCH ACTION ============
async function actionResearch(tabId, params, settings) {
  const query = params.query;
  
  // Use S.T.E.W search (Serper API = real Google results)
  const searchResp = await fetch(`${settings.serverUrl}/search`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query })
  });
  
  const searchData = await searchResp.json();
  // Handle both 'organic' and 'results' field names
  const searchResults = searchData.organic || searchData.results || [];
  
  // Fetch top results' content
  const pages = [];
  for (const result of searchResults.slice(0, 4)) {
    try {
      const pageUrl = result.link || result.url || '';
      if (!pageUrl) continue;
      const browseResp = await fetch(`${settings.serverUrl}/browse`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: pageUrl, api_key: settings.apiKey })
      });
      if (browseResp.ok) {
        const browseData = await browseResp.json();
        pages.push({ 
          url: pageUrl, 
          title: result.title || browseData.title || '', 
          content: browseData.content || browseData.text || '',
          snippet: result.snippet || ''
        });
      }
    } catch (e) { /* skip */ }
  }
  
  // Synthesize research report
  const context = pages.map(p => `--- ${p.title} (${p.url}) ---\n${p.content.slice(0, 1500)}`).join('\n\n');
  
  const chatResp = await fetch(`${settings.serverUrl}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      message: `Research report for: "${query}"\n\nBased on these sources:\n${context}\n\nProvide a comprehensive, well-structured research summary with key findings and citations.`,
      user_id: 'stew_extension',
      context: { action: 'research', query, sources: pages.map(p => p.url) }
    })
  });
  
  const chatData = await chatResp.json();
  const report = chatData.response || chatData.reply || 'No report generated';
  
  await saveToMemory(query, { report, sources: pages.map(p => p.url) }, 'research');
  
  chrome.runtime.sendMessage({
    type: 'agent_result',
    action: 'research',
    result: report,
    sources: pages.map(p => ({ title: p.title, url: p.url })),
    query
  }).catch(() => {});
  
  showNotification('S.T.E.W Research Complete', `Found ${pages.length} sources`);
  return report;
}

// ============ GOOGLE SEARCH ACTION ============
async function actionGoogleSearch(tabId, params, settings) {
  const query = params.query || params.text || '';
  if (!query) return 'No query provided';
  
  showNotification('S.T.E.W Search', `Searching Google for: ${query}`);
  
  // Use S.T.E.W server's Serper API for real Google results
  const searchResp = await fetch(`${settings.serverUrl}/search`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, num: 10 })
  });
  
  const searchData = await searchResp.json();
  const results = searchData.organic || searchData.results || [];
  const answerBox = searchData.answer_box || {};
  const knowledgeGraph = searchData.knowledge_graph || {};
  
  // Format results for display
  let summary = '';
  if (answerBox && (answerBox.answer || answerBox.snippet)) {
    summary += `💡 Answer: ${answerBox.answer || answerBox.snippet}\n\n`;
  }
  if (knowledgeGraph && knowledgeGraph.title) {
    summary += `📊 ${knowledgeGraph.title}: ${knowledgeGraph.description || ''}\n\n`;
  }
  summary += `Found ${results.length} Google results:\n\n`;
  results.forEach((r, i) => {
    summary += `${i+1}. ${r.title || 'Untitled'}\n   ${r.link || r.url || ''}\n   ${r.snippet || ''}\n\n`;
  });
  
  // Save to memory
  await saveToMemory(`search:${query}`, { query, results: results.slice(0, 5) }, 'search');
  
  // Send result to popup
  chrome.runtime.sendMessage({
    type: 'agent_result',
    action: 'google_search',
    result: summary,
    results: results,
    answerBox: answerBox,
    knowledgeGraph: knowledgeGraph,
    query
  }).catch(() => {});
  
  showNotification('S.T.E.W Search Complete', `Found ${results.length} results`);
  return summary;
}

// ============ BROWSE PAGE ACTION ============
async function actionBrowsePage(tabId, params, settings) {
  const url = params.url;
  if (!url) return 'No URL provided';
  
  showNotification('S.T.E.W Browser', `Browsing: ${url}`);
  
  // First try to get content from the current tab (if it's the same URL)
  let pageContent = null;
  if (tabId) {
    try {
      const [result] = await chrome.scripting.executeScript({
        target: { tabId },
        func: extractPageContent
      });
      pageContent = result.result;
    } catch (e) { /* fall back to server */ }
  }
  
  // Also get server-side content (for pages that need JS rendering)
  const resp = await fetch(`${settings.serverUrl}/browse`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url, api_key: settings.apiKey })
  });
  
  const data = await resp.json();
  
  // Combine local and server content
  const title = pageContent?.title || data.title || '';
  const content = pageContent?.text || data.content || '';
  const links = data.links || [];
  
  await saveToMemory(url, { title, content: content.slice(0, 5000), links: links.slice(0, 10) }, 'browse');
  
  chrome.runtime.sendMessage({
    type: 'agent_result',
    action: 'browse',
    result: `Title: ${title}\nURL: ${url}\n\nContent:\n${content.slice(0, 2000)}`,
    title,
    url,
    links
  }).catch(() => {});
  
  showNotification('S.T.E.W Browser', `Loaded: ${title}`);
  return content;
}

// ============ AUTOMATE ACTION (Multi-step task execution) ============
async function actionAutomate(tabId, params, settings) {
  const task = params.task;
  
  // Step 1: Plan the task using S.T.E.W
  const planResp = await fetch(`${settings.serverUrl}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      message: `I need to automate this task on a webpage: "${task}"\n\nBreak this down into specific browser actions (click, type, scroll, wait, extract). Format as JSON array:\n[{"action": "click", "selector": "...", "description": "..."}, ...]\n\nOnly include actionable steps. Be specific with CSS selectors.`,
      user_id: 'stew_extension',
      context: { action: 'plan', task }
    })
  });
  
  const planData = await planResp.json();
  let steps = [];
  
  try {
    const planText = planData.response || planData.reply || '[]';
    const jsonMatch = planText.match(/\[[\s\S]*\]/);
    steps = JSON.parse(jsonMatch ? jsonMatch[0] : planText);
  } catch (e) {
    steps = [{ action: 'extract', selector: 'body', description: 'Extract page content' }];
  }
  
  // Step 2: Execute each step
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
    
    // Human-like delay between steps
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
  
  showNotification('S.T.E.W Automation Complete', `Executed ${steps.length} steps`);
  return results;
}

// ============ NAVIGATE ACTION ============
async function actionNavigate(tabId, params, settings) {
  await chrome.tabs.update(tabId, { url: params.url });
  
  // Wait for page to load
  return new Promise((resolve) => {
    chrome.webNavigation.onCompleted.addListener(function listener(details) {
      if (details.tabId === tabId) {
        chrome.webNavigation.onCompleted.removeListener(listener);
        resolve({ status: 'navigated', url: params.url });
      }
    });
  });
}

// ============ FILL FORM ACTION ============
async function actionFillForm(tabId, params, settings) {
  const formData = params.formData;
  
  const [result] = await chrome.scripting.executeScript({
    target: { tabId },
    func: fillFormOnPage,
    args: [formData]
  });
  
  return result.result;
}

// ============ MULTI-STEP TASK PLANNING ============
async function actionMultiStep(tabId, params, settings) {
  const taskDescription = params.task;
  
  // Phase 1: Understand current page
  const [pageResult] = await chrome.scripting.executeScript({
    target: { tabId },
    func: extractPageContent
  });
  const pageContent = pageResult.result;
  
  // Phase 2: Plan with S.T.E.W
  const planResp = await fetch(`${settings.serverUrl}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      message: `Task: "${taskDescription}"\n\nCurrent page: ${pageContent.title}\nURL: ${pageContent.url}\n\nPage elements I can see:\n${pageContent.elements?.slice(0, 2000) || pageContent.text.slice(0, 2000)}\n\nCreate a detailed multi-step plan to accomplish this task. Consider what pages to visit, what to click, what to type. Format as JSON:\n{"steps": [{"action": "...", "selector": "...", "value": "...", "description": "...", "navigate_to": "..."}]}`,
      user_id: 'stew_extension',
      context: { action: 'multi_step_plan', task: taskDescription, url: pageContent.url }
    })
  });
  
  const planData = await planResp.json();
  let plan;
  try {
    const planText = planData.response || planData.reply || '{}';
    const jsonMatch = planText.match(/\{[\s\S]*\}/);
    plan = JSON.parse(jsonMatch ? jsonMatch[0] : planText);
  } catch (e) {
    plan = { steps: [{ action: 'extract', description: 'Extract page content' }] };
  }
  
  // Phase 3: Execute steps
  const executionLog = [];
  for (let i = 0; i < (plan.steps || []).length; i++) {
    const step = plan.steps[i];
    showNotification('S.T.E.W Multi-Step', `Step ${i+1}/${plan.steps.length}: ${step.description || step.action}`);
    
    // Navigate if needed
    if (step.navigate_to) {
      await chrome.tabs.update(tabId, { url: step.navigate_to });
      await new Promise(r => setTimeout(r, 2000));
    }
    
    // Execute the action
    if (step.action) {
      const [execResult] = await chrome.scripting.executeScript({
        target: { tabId },
        func: executeBrowserAction,
        args: [step]
      });
      executionLog.push({
        step: i + 1,
        result: execResult.result
      });
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
            user_id: message.userId || 'stew_extension',
            context: message.context || {}
          })
        });
        const chatData = await chatResp.json();
        sendResponse({ success: true, response: chatData.response || chatData.reply });
        break;
        
      case 'search':
        const searchResp2 = await fetch(`${settings.serverUrl}/search`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ query: message.query })
        });
        const searchData2 = await searchResp2.json();
        sendResponse({ 
          success: true, 
          results: searchData2.organic || searchData2.results || [],
          answerBox: searchData2.answer_box || {},
          knowledgeGraph: searchData2.knowledge_graph || {},
          grounded: searchData2.grounded || false,
          source: searchData2.source || 'serper'
        });
        break;
      
      case 'google_search':
        // Use S.T.E.W server to search Google via Serper
        const googleSearchResp = await fetch(`${settings.serverUrl}/search`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ query: message.query, num: message.num || 10 })
        });
        const googleSearchData = await googleSearchResp.json();
        const googleResults = googleSearchData.organic || googleSearchData.results || [];
        // Also browse the top result for full content
        let topPage = null;
        if (googleResults.length > 0) {
          const topUrl = googleResults[0].link || googleResults[0].url || '';
          if (topUrl) {
            try {
              const topResp = await fetch(`${settings.serverUrl}/browse`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ url: topUrl, api_key: settings.apiKey })
              });
              if (topResp.ok) {
                topPage = await topResp.json();
              }
            } catch (e) { /* skip */ }
          }
        }
        sendResponse({
          success: true,
          results: googleResults,
          answerBox: googleSearchData.answer_box || {},
          knowledgeGraph: googleSearchData.knowledge_graph || {},
          topPage: topPage,
          grounded: googleSearchData.grounded || false
        });
        break;
      
      case 'browse_page':
        // Browse a specific URL and extract content
        const browsePageResp = await fetch(`${settings.serverUrl}/browse`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ 
            url: message.url, 
            api_key: settings.apiKey,
            question: message.question || ''
          })
        });
        const browsePageData = await browsePageResp.json();
        sendResponse({ success: true, ...browsePageData });
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
    
    // Auto-summarize if enabled
    const settings = await getSettings();
    if (settings.autoSummarize && agentState.active) {
      // Don't auto-trigger, just track
      console.log('[S.T.E.W] Navigation completed:', details.url);
    }
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
    serverUrl: STEW_SERVER,
    apiKey: STEW_API_KEY,
    autoSummarize: false,
    memoryEnabled: true,
    maxMemoryItems: 500,
  };
}

function showNotification(title, message) {
  chrome.notifications.create({
    type: 'basic',
    iconUrl: 'icons/icon48.png',
    title,
    message,
    priority: 1
  });
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
      const task = prompt('Describe the task you want S.T.E.W to automate on this page:');
      if (task) {
        chrome.runtime.sendMessage({ type: 'agent_action', action: 'automate', params: { task } });
      }
    }
  });
}

// ============ INJECTED FUNCTIONS (run in page context) ============
function extractPageContent() {
  const title = document.title;
  const url = location.href;
  
  // Get visible text
  const walker = document.createTreeWalker(
    document.body,
    NodeFilter.SHOW_TEXT,
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
  
  // Get interactive elements
  const elements = [];
  const interactiveSelectors = 'a, button, input, select, textarea, [role="button"], [role="link"], [role="textbox"], [onclick]';
  document.querySelectorAll(interactiveSelectors).forEach(el => {
    elements.push({
      tag: el.tagName,
      type: el.type || el.role || '',
      text: el.textContent?.trim().slice(0, 50) || el.value?.slice(0, 50) || '',
      href: el.href || '',
      selector: getUniqueSelector(el),
      position: el.getBoundingClientRect(),
    });
  });
  
  // Get meta info
  const metaDescription = document.querySelector('meta[name="description"]')?.content || '';
  const metaKeywords = document.querySelector('meta[name="keywords"]')?.content || '';
  
  return { title, url, text: text.trim(), elements: elements.slice(0, 50), meta: { description: metaDescription, keywords: metaKeywords } };
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

function getUniqueSelector(el) {
  if (el.id) return '#' + el.id;
  if (el.className && typeof el.className === 'string') {
    const classes = el.className.split(' ').filter(c => c.length > 0);
    if (classes.length > 0) return el.tagName.toLowerCase() + '.' + classes.join('.');
  }
  let path = '';
  let current = el;
  while (current && current !== document.body) {
    let selector = current.tagName.toLowerCase();
    if (current.id) {
      path = '#' + current.id + (path ? ' > ' + path : '');
      break;
    }
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

console.log('[S.T.E.W] Background service worker loaded');
