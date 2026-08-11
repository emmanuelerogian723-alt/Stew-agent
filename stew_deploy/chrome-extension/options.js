document.addEventListener('DOMContentLoaded', () => {
  chrome.storage.local.get(['stew_settings'], (result) => {
    const s = result.stew_settings || {};
    document.getElementById('server-url').value = s.serverUrl || 'https://stew-agent.onrender.com';
    document.getElementById('api-key').value = s.apiKey || 'stew_extension_default';
    document.getElementById('auto-summarize').checked = s.autoSummarize || false;
    document.getElementById('human-delay').checked = s.humanLikeDelay !== false;
    document.getElementById('planning-depth').value = s.planningDepth || 3;
    document.getElementById('memory-enabled').checked = s.memoryEnabled !== false;
    document.getElementById('max-memory').value = s.maxMemoryItems || 500;
  });
  
  document.getElementById('save').addEventListener('click', () => {
    const settings = {
      serverUrl: document.getElementById('server-url').value,
      apiKey: document.getElementById('api-key').value,
      autoSummarize: document.getElementById('auto-summarize').checked,
      humanLikeDelay: document.getElementById('human-delay').checked,
      planningDepth: parseInt(document.getElementById('planning-depth').value),
      memoryEnabled: document.getElementById('memory-enabled').checked,
      maxMemoryItems: parseInt(document.getElementById('max-memory').value),
      scrollSpeed: 'natural',
      contextMenuEnabled: true,
    };
    
    chrome.storage.local.set({ stew_settings: settings }, () => {
      document.getElementById('status').textContent = 'Settings saved successfully!';
      setTimeout(() => {
        document.getElementById('status').textContent = 'Ready';
      }, 2000);
    });
  });
});
