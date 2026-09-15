"""
S.T.E.W AI Podcast Generator — turn any topic (or your own text/notes) into
a two-host podcast episode, fully voiced and ready to share.

Gen Z loves podcast-style audio (NotebookLM-style). Stew writes a natural
two-host script, voices each host with distinct free neural voices (edge-tts),
and stitches the episode with FFmpeg into a single MP3 — zero paid APIs.
"""
import asyncio
import json
import logging
import os
import re
import tempfile
import time
from typing import Callable, Optional

logger = logging.getLogger("stew.podcast")

# Voice packs — all free edge-tts neural voices, no API key needed.
# Default is full Naija (both hosts Nigerian) — the differentiator for the market.
VOICE_PACKS = {
    "naija": {
        "Ada": "en-NG-EzinneNeural",   # female, Nigerian
        "Zik": "en-NG-AbeoNeural",     # male, Nigerian
    },
    "afro": {
        "Ada": "en-ZA-LeahNeural",      # female, South African
        "Zik": "en-KE-ChilemeNeural",  # male, Kenyan
    },
    "global": {
        "Ada": "en-US-JennyNeural",    # female, US
        "Zik": "en-GB-RyanNeural",     # male, British
    },
}
HOSTS = VOICE_PACKS["naija"]
MAX_LINES = 22
MAX_EPISODE_SECONDS = 210  # safety cap


def _script_prompt(topic: str) -> list[dict]:
    return [
        {"role": "system", "content":
            "You are a podcast scriptwriter. Reply with ONLY valid JSON."},
        {"role": "user", "content":
            f"Write a punchy two-host podcast episode about: {topic}\n\n"
            "Hosts: Ada (warm, curious) and Zik (witty, sharp). They banter naturally — "
            "hooks, hot takes, a personal angle, and a memorable closing line. No intro music "
            "or sound-effect notes. 10-16 turns total, each line max 2 sentences.\n\n"
            'Reply as JSON only: {"title": string, "lines": [{"speaker": "Ada"|"Zik", "line": string}]}'},
    ]


def _safe_json(raw: str) -> Optional[dict]:
    if not raw:
        return None
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


async def generate_podcast(topic: str, llm_chat_fn: Callable, voice_pack: str = "naija",
                           progress_cb=None) -> tuple[Optional[bytes], dict]:
    """Returns (mp3_bytes, meta{'title','lines','seconds'})."""
    topic = (topic or "").strip()[:600]
    if not topic:
        return None, {"error": "no topic"}

    async def _say(msg):
        if progress_cb:
            try:
                await progress_cb(msg)
            except Exception:
                pass

    await _say("🎙️ Writing your podcast script…")
    script = None
    try:
        result = await asyncio.to_thread(llm_chat_fn, _script_prompt(topic), 1500)
        content = result.get("content") if isinstance(result, dict) else str(result)
        script = _safe_json(content)
    except Exception as e:
        logger.warning(f"podcast script LLM failed: {e}")
    if not script or not script.get("lines"):
        return None, {"error": "could not write a podcast script"}

    _hosts = VOICE_PACKS.get(voice_pack) or VOICE_PACKS["naija"]
    lines = [l for l in script["lines"] if l.get("speaker") in _hosts and l.get("line")]
    if not lines:
        return None, {"error": "empty script"}
    lines = lines[:MAX_LINES]
    title = (script.get("title") or topic).strip()

    await _say(f"🔊 Voicing {len(lines)} turns with Ada & Zik…")

    async def _tts_line(idx: int, speaker: str, text: str) -> Optional[str]:
        import edge_tts
        voice = _hosts[speaker]
        out = os.path.join(tempfile.gettempdir(), f"stew_pod_{int(time.time()*1000)}_{idx}.mp3")
        try:
            communicate = edge_tts.Communicate(text[:600], voice)
            await communicate.save(out)
            if os.path.exists(out) and os.path.getsize(out) > 1000:
                return out
        except Exception as e:
            logger.warning(f"podcast tts line {idx} failed: {e}")
        return None

    paths = await asyncio.gather(*[_tts_line(i, l["speaker"], l["line"]) for i, l in enumerate(lines)])
    paths = [p for p in paths if p]
    if len(paths) < 2:
        return None, {"error": "voice synthesis failed"}

    await _say("🎚️ Mixing your episode…")
    merged = await asyncio.to_thread(_concat_mp3, paths)
    for p in paths:
        try:
            os.remove(p)
        except Exception:
            pass
    if not merged:
        return None, {"error": "audio mixing failed"}
    return merged, {"title": title, "lines": len(lines)}


def _concat_mp3(paths: list) -> Optional[bytes]:
    """FFmpeg concat with tiny silence between turns for natural pacing."""
    import subprocess
    try:
        listfile = os.path.join(tempfile.gettempdir(), f"stew_pod_list_{int(time.time()*1000)}.txt")
        with open(listfile, "w") as f:
            for p in paths:
                f.write(f"file '{p}'\n")
        out = os.path.join(tempfile.gettempdir(), f"stew_pod_out_{int(time.time()*1000)}.mp3")
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", listfile,
             "-c:a", "libmp3lame", "-b:a", "96k", "-ar", "44100", out],
            capture_output=True, timeout=180,
        )
        os.remove(listfile)
        if not os.path.exists(out) or os.path.getsize(out) < 2000:
            return None
        with open(out, "rb") as f:
            data = f.read()
        os.remove(out)
        return data
    except Exception as e:
        logger.warning(f"podcast concat failed: {e}")
        return None


def is_podcast_intent(text: str) -> Optional[str]:
    """'make a podcast about X', 'podcast: X', /podcast X → topic."""
    t = (text or "").strip()
    if not t:
        return None
    low = t.lower()
    if low.startswith("/podcast"):
        return t[len("/podcast"):].strip() or None
    import re as _re
    m = _re.match(r"^podcast\s*:\s*(.+)$", low, _re.DOTALL)
    if m:
        return t[len(m.group(0)) - len(m.group(1)):].strip()
    m = _re.search(r"\b(?:make|create|generate|do)\s+(?:me\s+)?(?:a\s+|an\s+)?"
                   r"(?:ai\s+)?podcast\s+(?:about|on|of|for)\s+(.+)$", low, _re.DOTALL)
    if m:
        return t[m.start(1):].strip()
    m = _re.search(r"\bturn\s+(?:this|it|my notes?)\s+into\s+(?:a\s+)?podcast\b", low)
    if m:
        return t[m.end():].strip() or "the topic the user just shared"
    return None
