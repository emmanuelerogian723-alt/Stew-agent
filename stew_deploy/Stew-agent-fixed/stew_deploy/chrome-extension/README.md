# S.T.E.W Browser Agent — Chrome Extension (Manifest V3)

AI-powered browser agent that browses, understands, and automates web tasks like a human.
Built with 100% free and open-source technologies. No Playwright required.

## Features

- **Page Understanding** — Analyzes DOM structure, extracts text, identifies interactive elements
- **Summarization** — AI-generated summaries of any webpage
- **Information Extraction** — Pulls key facts, contacts, prices, dates from pages
- **Research** — Multi-source research with content synthesis
- **Task Automation** — Plans and executes multi-step browser tasks (click, type, scroll)
- **Memory** — Remembers pages visited, summaries generated, tasks performed
- **Multi-Tab Support** — Works across tabs with webNavigation tracking
- **Sidebar Mode** — Inline agent panel on any page
- **Context Menus** — Right-click to summarize, extract, research, or automate
- **SSE Connection** — Real-time updates from S.T.E.W server
- **Human-Like Delays** — Natural interaction timing

## Technologies Used

- Chrome Extension Manifest V3
- chrome.tabs API
- chrome.scripting API
- chrome.webNavigation API
- chrome.storage API
- chrome.runtime API
- chrome.contextMenus API
- MutationObserver (DOM change detection)
- DOM APIs (TreeWalker, querySelector, events)
- Fetch API (HTTP communication)
- Server-Sent Events (SSE) (real-time server updates)

## Installation

1. Download the extension files
2. Open Chrome and navigate to `chrome://extensions`
3. Enable "Developer mode" (top right)
4. Click "Load unpacked" and select the extension folder
5. The S.T.E.W icon will appear in your toolbar

## Configuration

1. Click the extension icon to open the popup
2. Go to Settings tab
3. Set the server URL (default: https://stew-agent.onrender.com)
4. Set your API key if needed

## Usage

### Popup
- **Chat tab**: Ask S.T.E.W anything or describe a task
- **Actions tab**: Quick actions (Summarize, Extract, Research, Automate)
- **Memory tab**: View and manage stored memories
- **Settings tab**: Configure the agent

### Context Menu (Right-Click)
- Summarize this page
- Extract key information
- Ask about this page
- Research this topic (with selected text)
- Automate a task on this page

### Sidebar
- Click "Open Sidebar" in Actions tab
- Inline agent panel appears on the right side of any page

## Architecture

```
background.js    → Service worker (core agent logic, SSE, memory, context menus)
content.js       → Content script (DOM observation, page understanding, sidebar injection)
popup.js         → Extension popup UI (chat, actions, memory, settings)
options.js       → Settings page
manifest.json    → Extension manifest (V3)
```

## Backend

Connects to S.T.E.W server at https://stew-agent.onrender.com
- POST /chat — AI chat with context awareness
- POST /search — Web search via DuckDuckGo
- POST /browse — Page content extraction
- GET /heartbeat — Server health check

## License

Free and open-source. Part of the S.T.E.W Agent platform.
