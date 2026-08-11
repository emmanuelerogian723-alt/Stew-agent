"""
S.T.E.W Tool-Calling Agent — Agentic loop for Telegram.

The agent receives a user message, decides which tools to use (search,
code execution, browse, document generation), executes them, and
returns a final answer. Like Kimi's agentic mode.

Tools available:
  1. run_python_code(code)      — Execute Python in sandbox (math, data, charts)
  2. web_search(query)           — Search the web (Serper + DuckDuckGo fallback)
  3. browse_url(url)             — Fetch and read any webpage
  4. generate_document(type, topic) — Create PDF/DOCX/XLSX/PPTX
  5. ocr_image(file_id)          — OCR on an uploaded image (called when user sends photo)
"""
import json
import re
import asyncio
import logging
from typing import Optional

from server.config import get_settings
from server.llm_client import get_llm_client
from server.search import get_searcher
from server.code_sandbox import execute_code
from server.clean_output import clean_response
from server.document_generator import (
    generate_pdf, generate_docx, generate_xlsx, generate_pptx, generate_html
)

logger = logging.getLogger(__name__)
settings = get_settings()

TOOL_SYSTEM_PROMPT = """You are S.T.E.W — a powerful AI agent with real tool-calling capabilities.
You help students, professionals, content creators, bankers, churches, and businesses solve ANY problem.

You have access to real tools. To use a tool, output a JSON tool call in this exact format:

TOOL_CALL: {"tool": "run_python_code", "args": {"code": "print(2+2)"}}
TOOL_CALL: {"tool": "web_search", "args": {"query": "latest news Nigeria"}}
TOOL_CALL: {"tool": "browse_url", "args": {"url": "https://example.com"}}
TOOL_CALL: {"tool": "generate_document", "args": {"doc_type": "pdf", "topic": "business plan"}}
TOOL_CALL: {"tool": "generate_document", "args": {"doc_type": "pptx", "topic": "AI trends"}}
TOOL_CALL: {"tool": "generate_document", "args": {"doc_type": "docx", "topic": "marketing strategy"}}
TOOL_CALL: {"tool": "generate_document", "args": {"doc_type": "xlsx", "topic": "monthly expenses"}}

Rules:
1. You can call MULTIPLE tools in sequence — wait for each result before deciding the next step.
2. After getting tool results, analyze them and provide a natural language response.
3. For math, data analysis, charts, or calculations — ALWAYS use run_python_code first.
4. For current information, news, prices — use web_search first.
5. For reading a webpage — use browse_url.
6. For documents (PDF, Word, Excel, PowerPoint) — use generate_document.
7. Never say you can't do something — try the tool first.
8. Be concise in explanations. Show your work when using tools.
9. End with a clear final answer after tool use.

When you don't need a tool, just answer directly.
Always end with a helpful, complete response."""

TOOL_CALL_PATTERN = re.compile(r'TOOL_CALL:\s*(\{.*?\})', re.DOTALL)


def extract_tool_calls(text: str) -> list:
    """Extract all TOOL_CALL JSON blocks from LLM output."""
    calls = []
    for match in TOOL_CALL_PATTERN.finditer(text):
        try:
            call = json.loads(match.group(1))
            calls.append(call)
        except json.JSONDecodeError:
            continue
    return calls


async def execute_tool(call: dict, bot=None, chat_id=None) -> dict:
    """Execute a single tool call and return the result."""
    tool = call.get("tool", "")
    args = call.get("args", {})

    if tool == "run_python_code":
        code = args.get("code", "")
        if not code:
            return {"error": "No code provided"}
        result = await asyncio.to_thread(execute_code, code, 10)
        output = ""
        if result.get("stdout"):
            output += result["stdout"]
        if result.get("result"):
            output += f"\nResult: {result['result']}"
        if result.get("figures"):
            output += f"\n[Generated {len(result['figures'])} chart(s)]"
        if result.get("error"):
            output += f"\nError: {result['error']}"
            if result.get("traceback"):
                output += f"\n{result['traceback'][-500:]}"
        return {
            "tool": tool,
            "success": result.get("success", False),
            "output": output[:10000],
            "figures": result.get("figures", []),
            "execution_time": result.get("execution_time", 0),
        }

    elif tool == "web_search":
        query = args.get("query", "")
        if not query:
            return {"error": "No query provided"}
        searcher = get_searcher()
        results = await asyncio.to_thread(searcher.search, query, 5)
        organic = results.get("organic", [])
        output_parts = [f"Found {len(organic)} results for '{query}':\n"]
        for i, r in enumerate(organic[:5], 1):
            output_parts.append(f"{i}. {r.get('title', 'No title')}")
            output_parts.append(f"   {r.get('link', '')}")
            output_parts.append(f"   {r.get('snippet', '')[:200]}\n")
        answer_box = results.get("answer_box", {})
        if answer_box:
            output_parts.append(f"Answer: {json.dumps(answer_box, ensure_ascii=False)[:500]}")
        return {
            "tool": tool,
            "success": results.get("grounded", False),
            "output": "\n".join(output_parts)[:8000],
            "source": results.get("source", "unknown"),
        }

    elif tool == "browse_url":
        url = args.get("url", "")
        if not url:
            return {"error": "No URL provided"}
        from server.browser import StewBrowser
        browser = StewBrowser()
        result = await browser.fetch(url)
        content = result.get("content", "")[:8000]
        title = result.get("title", "Unknown")
        return {
            "tool": tool,
            "success": bool(content),
            "output": f"Title: {title}\nURL: {url}\n\n{content}",
        }

    elif tool == "generate_document":
        doc_type = args.get("doc_type", "pdf").lower()
        topic = args.get("topic", "Document")
        llm = get_llm_client()

        try:
            if doc_type == "xlsx":
                system = "You are a data analyst. Generate structured data as JSON array. Return ONLY valid JSON."
                user = f"Create spreadsheet data about: {topic}. 5-15 rows with proper column names. JSON array only."
                messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
                resp = await asyncio.to_thread(llm.chat, messages)
                content = clean_response(resp["content"])
                json_match = re.search(r'\[.*\]', content, re.DOTALL)
                data = json.loads(json_match.group()) if json_match else [{"Topic": topic}]
                result = generate_xlsx(data, "Sheet1", topic)
            elif doc_type == "pptx":
                system = "You are a presentation designer. Return ONLY a JSON array of slides. Each slide has 'title' and 'content'."
                user = f"Create a 6-8 slide presentation about: {topic}. JSON array only."
                messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
                resp = await asyncio.to_thread(llm.chat, messages)
                content = clean_response(resp["content"])
                json_match = re.search(r'\[.*\]', content, re.DOTALL)
                slides = json.loads(json_match.group()) if json_match else [{"title": topic, "content": "Generated"}]
                result = generate_pptx(slides, topic)
            elif doc_type == "docx":
                system = "You are a professional writer. Create a well-structured document. Use markdown."
                user = f"Write a detailed document about: {topic}. Introduction, sections, conclusion."
                messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
                resp = await asyncio.to_thread(llm.chat, messages)
                content = clean_response(resp["content"])
                result = generate_docx(content, topic)
            else:  # pdf
                system = "You are a professional writer. Create a well-structured document. Use markdown."
                user = f"Write a detailed document about: {topic}. Introduction, sections, conclusion."
                messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
                resp = await asyncio.to_thread(llm.chat, messages)
                content = clean_response(resp["content"])
                result = generate_pdf(content, topic)

            return {
                "tool": tool,
                "success": result.get("success", False),
                "output": f"Generated {doc_type.upper()} about '{topic}'. File ready to send.",
                "file_base64": result.get("file", ""),
                "filename": result.get("filename", f"stew_{doc_type}.docx"),
                "doc_type": doc_type,
            }
        except Exception as e:
            return {"tool": tool, "success": False, "error": str(e)}

    elif tool == "ocr_image":
        # This is handled separately in the webhook (needs file_id download)
        return {"error": "OCR is handled at the webhook level when a photo is received"}

    else:
        return {"error": f"Unknown tool: {tool}"}


async def run_agent_loop(
    user_text: str,
    bot=None,
    chat_id: int = None,
    max_iterations: int = 5,
) -> dict:
    """
    Run the agentic tool-calling loop.
    
    Returns:
        {
            "response": str,           # final text response
            "files": list[dict],        # generated files [{base64, filename, doc_type}]
            "figures": list[dict],      # charts [{base64}]
            "tool_calls": list[dict],   # tool call history
        }
    """
    llm = get_llm_client()
    messages = [
        {"role": "system", "content": TOOL_SYSTEM_PROMPT},
        {"role": "user", "content": user_text},
    ]

    files = []
    figures = []
    tool_history = []

    for iteration in range(max_iterations):
        # Get LLM response
        result = await asyncio.to_thread(llm.chat, messages)
        assistant_text = clean_response(result["content"])

        # Check for tool calls
        tool_calls = extract_tool_calls(assistant_text)

        if not tool_calls:
            # No more tool calls — this is the final answer
            return {
                "response": assistant_text,
                "files": files,
                "figures": figures,
                "tool_calls": tool_history,
            }

        # Execute each tool call
        for call in tool_calls:
            tool_name = call.get("tool", "unknown")
            if bot and chat_id:
                short = f"Using tool: {tool_name}..."
                await bot.send_message(chat_id, short)

            tool_result = await execute_tool(call, bot, chat_id)
            tool_history.append({
                "call": call,
                "result": {k: v for k, v in tool_result.items() if k != "file_base64"},
            })

            # Collect files and figures
            if tool_result.get("file_base64"):
                files.append({
                    "base64": tool_result["file_base64"],
                    "filename": tool_result.get("filename", "document"),
                    "doc_type": tool_result.get("doc_type", "pdf"),
                })
            if tool_result.get("figures"):
                figures.extend(tool_result["figures"])

            # Send figures to chat
            if bot and chat_id and tool_result.get("figures"):
                import base64 as _b64
                for fig in tool_result["figures"]:
                    try:
                        fig_bytes = _b64.b64decode(fig["base64"])
                        await bot.send_photo(chat_id, fig_bytes, "Chart generated by S.T.E.W")
                    except:
                        pass

            # Add the tool call + result to conversation
            tool_output = tool_result.get("output", tool_result.get("error", "No output"))
            messages.append({"role": "assistant", "content": assistant_text})
            messages.append({
                "role": "user",
                "content": f"TOOL_RESULT for {tool_name}:\n{tool_output[:5000]}\n\n"
                           f"Analyze this result and continue. If you have enough information, "
                           f"provide your final answer (no more TOOL_CALL)."
            })

    # Max iterations reached — get final response
    messages.append({
        "role": "user",
        "content": "You have used all your tool calls. Please provide your final answer now."
    })
    result = await asyncio.to_thread(llm.chat, messages)
    final_text = clean_response(result["content"])

    return {
        "response": final_text,
        "files": files,
        "figures": figures,
        "tool_calls": tool_history,
    }
