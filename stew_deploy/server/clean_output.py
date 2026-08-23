"""
S.T.E.W Output Cleaner — strips raw markdown from LLM responses.
Makes output clean, professional, and readable on any platform (API, Telegram, WhatsApp).

Transforms:
  ## Heading  →  Heading (bold, no ##)
  ### Sub     →  Sub (bold, no ###)
  **bold**    →  bold (plain, no asterisks)
  *italic*    →  italic (plain, no asterisks)
  `code`      →  code (plain, no backticks)
  - bullet    →  • bullet
  ```code```  →  preserved (code blocks stay)
"""
import re
import logging

logger = logging.getLogger(__name__)


def clean_markdown(text: str) -> str:
    """
    Clean markdown formatting from LLM output.
    Preserves code blocks, lists, and structure but removes ## headers, **bold**, etc.
    """
    if not text:
        return text
    
    # Preserve code blocks (don't touch content inside ```)
    code_blocks = []
    def _save_code(m):
        code_blocks.append(m.group(0))
        return f"\x00CODEBLOCK{len(code_blocks)-1}\x00"
    
    text = re.sub(r'```[\s\S]*?```', _save_code, text)
    
    # Remove ## and ### headers (keep the text, make it a clean section title)
    # "## Heading" → "Heading" (just the text, no ## prefix)
    text = re.sub(r'^#{1,6}\s+(.+)$', r'\1', text, flags=re.MULTILINE)
    
    # Remove bold markers: **text** or __text__ → text
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'__(.+?)__', r'\1', text)
    
    # Remove italic markers: *text* or _text_ → text (but not list bullets)
    # Be careful not to touch bullet lists or multiplication signs
    text = re.sub(r'(?<!\w)\*([^*\n]+?)\*(?!\w)', r'\1', text)
    text = re.sub(r'(?<!\w)_([^_\n]+?)_(?!\w)', r'\1', text)
    
    # Remove inline code backticks: `text` → text
    text = re.sub(r'(?<!`)`([^`\n]+?)`(?!`)', r'\1', text)
    
    # Convert markdown bullet lists to clean bullets
    text = re.sub(r'^[\s]*[-•]\s+(.+)$', r'  • \1', text, flags=re.MULTILINE)
    
    # Remove horizontal rules (--- or ___)
    text = re.sub(r'^[\s]*[-_]{3,}[\s]*$', '', text, flags=re.MULTILINE)
    
    # Remove blockquotes: > text → text
    text = re.sub(r'^>\s+(.+)$', r'\1', text, flags=re.MULTILINE)
    
    # Clean up multiple blank lines
    text = re.sub(r'\n{3,}', '\n\n', text)
    
    # Restore code blocks
    for i, block in enumerate(code_blocks):
        text = text.replace(f"\x00CODEBLOCK{i}\x00", block)
    
    return text.strip()


def clean_response(text: str) -> str:
    """Main entry point — clean LLM output for delivery."""
    if not text:
        return text

    # CRITICAL: Detect and BLOCK raw base64 dumps / "decode this yourself" hallucinations.
    # If the LLM ever tries to hand the user raw file bytes or file-saving instructions
    # instead of an actual sent file, replace the whole response with a safe message.
    _leak_signals = [
        "base64-encoded", "base64 encoded", "save the content to a file",
        "decode base64", "decode this base64", "paste this into a file",
        "copy the content below and save", "open it with any pdf viewer",
    ]
    _lower = text.lower()
    if any(sig in _lower for sig in _leak_signals):
        return "Your document is ready! If it didn't appear above, please try again in a moment — I'll send it as a proper downloadable file."
    # Detect a long base64-looking blob (40+ chars, base64 alphabet, no spaces) —
    # a near-certain sign of a raw file dump leaking into chat text.
    if re.search(r'(?:[A-Za-z0-9+/]{60,}={0,2}\s*){3,}', text):
        return "Your document is ready! If it didn't appear above, please try again in a moment — I'll send it as a proper downloadable file."

    # CRITICAL: Strip any TOOL_CALL or TOOL_RESULT artifacts that leaked
    text = re.sub(r'TOOL_CALL:\s*\{.*?\}', '', text, flags=re.DOTALL).strip()
    text = re.sub(r'TOOL_CALL_MARKER.*', '', text, flags=re.DOTALL).strip()
    text = re.sub(r'TOOL_RESULT[\s\S]*', '', text).strip()
    # Strip JSON-like tool call remnants
    text = re.sub(r'\{"tool"\s*:\s*"[^"]+".*?\}', '', text, flags=re.DOTALL).strip()
    # Strip "I should use" or "I need to call" internal reasoning leaks
    text = re.sub(r'^I (should|need to|will|must) (use|call|invoke|emit|generate) (a |the )?tool.*$', '', text, flags=re.MULTILINE|re.IGNORECASE).strip()
    # Strip "Let me" reasoning
    text = re.sub(r'^Let me (search|find|look|check|use|call|generate|create).*$', '', text, flags=re.MULTILINE|re.IGNORECASE).strip()
    
    cleaned = clean_markdown(text)
    # Final safety: strip any remaining ##, ###, **, __ at the start of lines
    cleaned = re.sub(r'^\s*#+\s*', '', cleaned, flags=re.MULTILINE)
    # Strip any remaining ** or __ that survived earlier passes
    cleaned = re.sub(r'\*\*(.+?)\*\*', r'\1', cleaned)
    cleaned = re.sub(r'__(.+?)__', r'\1', cleaned)
    # Strip any remaining inline code backticks
    cleaned = re.sub(r'`([^`\n]+?)`', r'\1', cleaned)
    # Strip empty lines at start/end
    cleaned = cleaned.strip()
    # If after all cleaning the response is empty, return empty (caller should handle)
    return cleaned
