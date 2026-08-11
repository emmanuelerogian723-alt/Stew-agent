// S.T.E.W Browser Agent — Popup Script
document.addEventListener('DOMContentLoaded', async () => {
  checkStatus();
  loadSettings();
  
  // Tab switching
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.tab-content').forEach(c => c.classList.add('hidden'));
      btn.classList.add('active');
      document.getElementById('tab-' + btn.dataset.tab).classList.remove('hidden');
    });
  });
  
  // Chat send
  const chatInput = document.getElementById('chat-input');
  const chatSend = document.getElementById('chat-send');
  
  chatSend.addEventListener('click', () => sendChat());
  chatInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendChat();
    }
  });
  
  // Action buttons
  document.querySelectorAll('.action-card').forEach(card => {
    card.addEventListener('click', () => {
      const action = card.dataset.action;
      if (action === 'sidebar') {
        chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
          chrome.scripting.executeScript({
            target: { tabId: tabs[0].id },
            files: ['content.js']
          }, () => {
            chrome.tabs.sendMessage(tabs[0].id, { type: 'inject_sidebar' });
          });
        });
      } else if (action === 'search') {
        const query = prompt('Enter your search query:');
        if (query) {
          addChatMessage('user', '🔍 Search: ' + query);
          chrome.runtime.sendMessage({
            type: 'search',
            query: query,
            num: 10
          }, (response) => {
            if (response && response.success && response.results) {
              let text = 'Found ' + response.results.length + ' results:\n\n';
              response.results.forEach((r, i) => {
                text += (i+1) + '. ' + (r.title || 'Untitled') + '\n   ' + (r.link || '') + '\n   ' + (r.snippet || '') + '\n\n';
              });
              addChatMessage('agent', text.slice(0, 3000));
            } else {
              addChatMessage('agent', 'Search failed or returned no results.');
            }
          });
        }
      } else if (action === 'browse') {
        const url = prompt('Enter URL to browse:');
        if (url) {
          addChatMessage('user', '🌐 Browsing: ' + url);
          chrome.runtime.sendMessage({
            type: 'browse_page',
            url: url
          }, (response) => {
            if (response && response.success) {
              const title = response.title || 'Untitled';
              const content = response.content || '';
              addChatMessage('agent', '📄 ' + title + '\n\n' + content.slice(0, 1500));
            } else {
              addChatMessage('agent', 'Browse failed: ' + (response?.error || 'Could not read the page. The site may be blocking automated access.'));
            }
          });
        }
      } else {
        executeAction(action);
      }
    });
  });
  
  // Clear memory
  document.getElementById('clear-memory').addEventListener('click', () => {
    chrome.runtime.sendMessage({ type: 'clear_memory' }, (response) => {
      if (response && response.success) {
        loadMemory();
      }
    });
  });
  
  // Save settings
  document.getElementById('save-settings').addEventListener('click', saveSettings);
  
  // Listen for agent results
  chrome.runtime.onMessage.addListener((message) => {
    if (message.type === 'agent_result') {
      displayResult(message);
    }
  });
  
  loadMemory();
});

async function checkStatus() {
  chrome.runtime.sendMessage({ type: 'get_status' }, (response) => {
    if (response) {
      const dot = document.getElementById('status-dot');
      const text = document.getElementById('status-text');
      dot.classList.add('online');
      text.textContent = response.active ? 'Active' : 'Ready';
    }
  });
}

async function sendChat() {
  const input = document.getElementById('chat-input');
  const text = input.value.trim();
  if (!text) return;
  
  addChatMessage('user', text);
  input.value = '';
  
  chrome.runtime.sendMessage({
    type: 'chat',
    message: text
  }, (response) => {
    if (response && response.success) {
      addChatMessage('agent', response.response);
    } else {
      addChatMessage('agent', 'Error: Could not get response. Check that the S.T.E.W server is running and your API key is set in Settings.');
    }
  });
}

function addChatMessage(role, text) {
  const history = document.getElementById('chat-history');
  const empty = history.querySelector('.chat-empty');
  if (empty) empty.remove();
  
  const msg = document.createElement('div');
  msg.className = 'chat-msg ' + role;
  msg.textContent = text;
  history.appendChild(msg);
  history.scrollTop = history.scrollHeight;
}

function executeAction(action) {
  addChatMessage('user', 'Executing: ' + action);
  
  chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
    chrome.runtime.sendMessage({
      type: 'agent_action',
      action: action,
      tabId: tabs[0].id,
      params: {}
    }, (response) => {
      if (response && response.success) {
        addChatMessage('agent', (response.result || 'Done').toString().slice(0, 800));
      } else {
        addChatMessage('agent', 'Action failed. Check that the S.T.E.W server is running.');
      }
    });
  });
}

function displayResult(message) {
  addChatMessage('agent', '[' + message.action + '] ' + (message.result || 'Done').toString().slice(0, 800));
}

async function loadSettings() {
  chrome.storage.local.get(['stew_settings'], (result) => {
    const s = result.stew_settings || {};
    document.getElementById('setting-server').value = s.serverUrl || 'https://stew-agent.onrender.com';
    document.getElementById('setting-apikey').value = s.apiKey || '';
    document.getElementById('setting-freesearch').checked = s.useFreeSearch !== false;
    document.getElementById('setting-autosummarize').checked = s.autoSummarize || false;
    document.getElementById('setting-memory').checked = s.memoryEnabled !== false;
    document.getElementById('setting-delay').checked = s.humanLikeDelay !== false;
    document.getElementById('setting-maxmem').value = s.maxMemoryItems || 500;
  });
}

async function saveSettings() {
  const settings = {
    serverUrl: document.getElementById('setting-server').value || 'https://stew-agent.onrender.com',
    apiKey: document.getElementById('setting-apikey').value || '',
    useFreeSearch: document.getElementById('setting-freesearch').checked,
    autoSummarize: document.getElementById('setting-autosummarize').checked,
    memoryEnabled: document.getElementById('setting-memory').checked,
    humanLikeDelay: document.getElementById('setting-delay').checked,
    maxMemoryItems: parseInt(document.getElementById('setting-maxmem').value) || 500,
    planningDepth: 3,
    scrollSpeed: 'natural',
    contextMenuEnabled: true,
  };
  
  chrome.runtime.sendMessage({ type: 'settings_update', settings }, (response) => {
    const btn = document.getElementById('save-settings');
    btn.textContent = 'Saved!';
    setTimeout(() => { btn.textContent = 'Save Settings'; }, 1500);
  });
}

async function loadMemory() {
  chrome.runtime.sendMessage({ type: 'get_memory', limit: 20 }, (response) => {
    const list = document.getElementById('memory-list');
    if (!response || !response.memory || response.memory.length === 0) return;
    
    list.innerHTML = '';
    for (const item of response.memory) {
      const div = document.createElement('div');
      div.className = 'memory-item';
      const date = new Date(item.timestamp).toLocaleString();
      div.innerHTML = 
        '<div class="memory-item-title">' + (item.type || 'general') + ': ' + (item.key || 'Untitled').toString().slice(0, 60) + '</div>' +
        '<div class="memory-item-desc">' + JSON.stringify(item.value).slice(0, 120) + '...</div>' +
        '<div class="memory-item-time">' + date + '</div>';
      list.appendChild(div);
    }
  });
}
