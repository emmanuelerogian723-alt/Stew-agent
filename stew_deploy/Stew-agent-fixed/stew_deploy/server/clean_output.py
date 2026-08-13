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
    
    # Remove bold markers: **text** → text
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    
    # Remove italic markers: *text* → text (but not list bullets)
    # Be careful not to touch bullet lists or multiplication signs
    text = re.sub(r'(?<!\w)\*([^*\n]+?)\*(?!\w)', r'\1', text)
    
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
    cleaned = clean_markdown(text)
    # Final safety: strip any remaining ## at the start of lines
    cleaned = re.sub(r'^\s*#+\s*', '', cleaned, flags=re.MULTILINE)
    return cleaned.strip()
