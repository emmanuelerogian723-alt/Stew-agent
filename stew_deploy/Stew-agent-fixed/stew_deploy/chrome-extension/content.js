// S.T.E.W Browser Agent — Content Script
// Uses: DOM APIs, MutationObserver, Fetch API
// Runs on every page to enable agent interactions

(function() {
  'use strict';
  
  // Prevent double-injection
  if (window.__stewAgentInjected) return;
  window.__stewAgentInjected = true;
  
  // ============ MUTATION OBSERVER ============
  // Monitors DOM changes for dynamic content (SPAs, infinite scroll, etc.)
  const observer = new MutationObserver((mutations) => {
    let hasSignificantChange = false;
    for (const mutation of mutations) {
      if (mutation.type === 'childList' && mutation.addedNodes.length > 0) {
        for (const node of mutation.addedNodes) {
          if (node.nodeType === Node.ELEMENT_NODE && node.tagName !== 'SCRIPT' && node.tagName !== 'STYLE') {
            hasSignificantChange = true;
            break;
          }
        }
      }
    }
    if (hasSignificantChange) {
      // Notify background of page changes
      chrome.runtime.sendMessage({
        type: 'dom_changed',
        url: location.href,
        title: document.title,
      }).catch(() => {});
    }
  });
  
  observer.observe(document.body, {
    childList: true,
    subtree: true,
    attributes: false,
    characterData: false,
  });
  
  // ============ PAGE UNDERSTANDING ============
  // Continuously analyzes page structure for the agent
  window.__stewGetPageStructure = function() {
    const headings = Array.from(document.querySelectorAll('h1, h2, h3')).map(h => ({
      level: parseInt(h.tagName[1]),
      text: h.textContent.trim().slice(0, 100),
    }));
    
    const links = Array.from(document.querySelectorAll('a[href]')).slice(0, 30).map(a => ({
      text: a.textContent.trim().slice(0, 50),
      href: a.href,
    }));
    
    const forms = Array.from(document.querySelectorAll('form')).map(f => ({
      action: f.action,
      method: f.method,
      inputs: Array.from(f.querySelectorAll('input, textarea, select')).map(i => ({
        type: i.type,
        name: i.name,
        placeholder: i.placeholder,
        selector: getUniqueSelector(i),
      })),
    }));
    
    const buttons = Array.from(document.querySelectorAll('button, [role="button"], input[type="submit"]')).slice(0, 20).map(b => ({
      text: b.textContent.trim().slice(0, 50),
      selector: getUniqueSelector(b),
    }));
    
    return { headings, links, forms, buttons, url: location.href, title: document.title };
  };
  
  // ============ SCROLL TRACKING ============
  let scrollTimeout;
  window.addEventListener('scroll', () => {
    clearTimeout(scrollTimeout);
    scrollTimeout = setTimeout(() => {
      const scrollPercent = Math.round(
        (window.scrollY / (document.documentElement.scrollHeight - window.innerHeight)) * 100
      );
      chrome.runtime.sendMessage({
        type: 'scroll_update',
        percent: scrollPercent,
        url: location.href,
      }).catch(() => {});
    }, 200);
  });
  
  // ============ MESSAGE HANDLER ============
  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    switch(message.type) {
      case 'get_page_structure':
        sendResponse(window.__stewGetPageStructure());
        break;
        
      case 'highlight_element':
        const el = document.querySelector(message.selector);
        if (el) {
          el.style.outline = '3px solid #10b981';
          el.scrollIntoView({ behavior: 'smooth', block: 'center' });
          setTimeout(() => { el.style.outline = ''; }, 2000);
          sendResponse({ success: true });
        } else {
          sendResponse({ success: false, error: 'Element not found' });
        }
        break;
        
      case 'inject_sidebar':
        injectSidebar();
        sendResponse({ success: true });
        break;
        
      case 'remove_sidebar':
        removeSidebar();
        sendResponse({ success: true });
        break;
        
      default:
        // Let other messages pass through
        break;
    }
    return true;
  });
  
  // ============ SIDEBAR INJECTION ============
  function injectSidebar() {
    if (document.getElementById('stew-sidebar')) return;
    
    const sidebar = document.createElement('div');
    sidebar.id = 'stew-sidebar';
    sidebar.style.cssText = `
      position: fixed; top: 0; right: 0; width: 400px; height: 100vh;
      background: #0a0a0a; color: #e5e5e5; z-index: 999999;
      box-shadow: -4px 0 20px rgba(0,0,0,0.5); font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      display: flex; flex-direction: column; overflow: hidden;
      border-left: 1px solid #333;
    `;
    
    sidebar.innerHTML = `
      <div style="padding: 16px; background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); border-bottom: 1px solid #333;">
        <div style="display: flex; justify-content: space-between; align-items: center;">
          <div>
            <div style="font-size: 18px; font-weight: 700; color: #10b981;">S.T.E.W Agent</div>
            <div style="font-size: 12px; color: #888;">Browser Intelligence</div>
          </div>
          <button id="stew-sidebar-close" style="background: none; border: none; color: #888; font-size: 24px; cursor: pointer;">&times;</button>
        </div>
      </div>
      <div id="stew-sidebar-content" style="flex: 1; overflow-y: auto; padding: 16px;">
        <div style="margin-bottom: 16px;">
          <textarea id="stew-input" placeholder="Ask S.T.E.W to do anything on this page..." style="width: 100%; min-height: 80px; background: #1a1a1a; border: 1px solid #333; border-radius: 8px; color: #e5e5e5; padding: 12px; font-size: 14px; resize: vertical; box-sizing: border-box;"></textarea>
          <div style="display: flex; gap: 8px; margin-top: 8px;">
            <button id="stew-btn-summarize" style="flex: 1; background: #10b981; color: #000; border: none; padding: 8px; border-radius: 6px; font-weight: 600; cursor: pointer; font-size: 13px;">Summarize</button>
            <button id="stew-btn-extract" style="flex: 1; background: #3b82f6; color: #fff; border: none; padding: 8px; border-radius: 6px; font-weight: 600; cursor: pointer; font-size: 13px;">Extract</button>
            <button id="stew-btn-automate" style="flex: 1; background: #f59e0b; color: #000; border: none; padding: 8px; border-radius: 6px; font-weight: 600; cursor: pointer; font-size: 13px;">Automate</button>
          </div>
        </div>
        <div id="stew-output" style="background: #1a1a1a; border: 1px solid #333; border-radius: 8px; padding: 12px; min-height: 200px; font-size: 14px; line-height: 1.6;">
          <div style="color: #666; text-align: center; padding: 40px 0;">S.T.E.W is ready. Ask me anything or use the buttons above.</div>
        </div>
      </div>
    `;
    
    document.body.appendChild(sidebar);
    
    // Adjust body width
    document.body.style.marginRight = '400px';
    
    // Close handler
    document.getElementById('stew-sidebar-close').addEventListener('click', removeSidebar);
    
    // Button handlers
    document.getElementById('stew-btn-summarize').addEventListener('click', () => {
      sendToStew('summarize', null);
    });
    document.getElementById('stew-btn-extract').addEventListener('click', () => {
      sendToStew('extract', null);
    });
    document.getElementById('stew-btn-automate').addEventListener('click', () => {
      const task = document.getElementById('stew-input').value.trim();
      if (task) sendToStew('automate', task);
      else document.getElementById('stew-input').placeholder = 'Describe the task to automate...';
    });
    
    // Enter key on textarea
    document.getElementById('stew-input').addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        const text = document.getElementById('stew-input').value.trim();
        if (text) sendToStew('chat', text);
      }
    });
  }
  
  function sendToStew(action, value) {
    const output = document.getElementById('stew-output');
    if (!output) return;
    
    output.innerHTML = '<div style="color: #10b981; text-align: center; padding: 20px;">⠋ Processing...</div>';
    
    chrome.runtime.sendMessage({
      type: 'agent_action',
      action: action,
      params: value ? { task: value, query: value, message: value } : {}
    }, (response) => {
      if (response && response.success) {
        output.innerHTML = `<div style="color: #e5e5e5; white-space: pre-wrap;">${response.result || 'Done'}</div>`;
      } else {
        output.innerHTML = '<div style="color: #ef4444;">Error processing request</div>';
      }
    });
  }
  
  function removeSidebar() {
    const sidebar = document.getElementById('stew-sidebar');
    if (sidebar) sidebar.remove();
    document.body.style.marginRight = '';
  }
  
  // ============ HELPER ============
  function getUniqueSelector(el) {
    if (el.id) return '#' + el.id;
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
  
  console.log('[S.T.E.W] Content script loaded on', location.href);
})();
