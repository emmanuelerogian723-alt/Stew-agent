"""
S.T.E.W Voice Cloner — zero-shot voice cloning for content creators.

Creators send one 10-20s voice sample; Stew stores their voice profile and
generates unlimited voiceovers that mimic their own voice.

Engine: F5-TTS (open source, MIT — SWivid/F5-TTS) served via free public
Hugging Face Spaces (gradio_client). Zero paid APIs, zero local GPU.

Flow (Telegram):
  1. /voiceclone → Stew asks for a 10-20s voice sample
  2. Voice note received while session pending → profile saved
     (Whisper transcribes the sample — F5-TTS needs the transcript)
  3. "voiceover: <text>" → cloned-voice voice note returned

Profiles persist to Supabase Storage when configured (survives redeploys),
with a local /tmp copy as fallback.
"""
import asyncio
import hashlib
import logging
import os
import time
import urllib.request
from typing import Optional

logger = logging.getLogger("stew.voice_clone")

# ── Public open-source cloning Spaces (anonymous, no token needed).
# First that responds wins. Add mirrors here as they appear.
F5_SPACES = [
    "mrfakename/E2-F5-TTS",  # verified working anonymously (2026-09)
]

PROFILE_DIR = "/tmp/stew_voice_profiles"
MAX_GEN_CHARS = 1200        # keep Space generation under ~60s
MAX_PROFILE_BYTES = 8 * 1024 * 1024

# key -> {"awaiting_sample": True, "ts": float}
_SESSIONS: dict = {}


# ── session state ────────────────────────────────────────────────────────────
def start_session(key: str) -> None:
    """Mark a chat as awaiting the creator's voice sample."""
    _SESSIONS[key] = {"awaiting_sample": True, "ts": time.time()}


def is_awaiting_sample(key: str) -> bool:
    s = _SESSIONS.get(key)
    if not s:
        return False
    if time.time() - s.get("ts", 0) > 600:  # session expires after 10 min
        _SESSIONS.pop(key, None)
        return False
    return bool(s.get("awaiting_sample"))


def end_session(key: str) -> None:
    _SESSIONS.pop(key, None)


# ── profile storage ──────────────────────────────────────────────────────────
def _profile_path(key: str) -> str:
    safe = hashlib.sha256(key.encode()).hexdigest()[:24]
    os.makedirs(PROFILE_DIR, exist_ok=True)
    return os.path.join(PROFILE_DIR, f"{safe}.wav")


def _profile_meta(key: str) -> dict:
    """Transcript + storage URL for a profile, wherever they survive."""
    meta = {"path": _profile_path(key), "ref_text": "", "supabase_url": None}
    txt_path = meta["path"] + ".txt"
    url_path = meta["path"] + ".url"
    if os.path.exists(txt_path):
        try:
            meta["ref_text"] = open(txt_path, encoding="utf-8").read().strip()
        except Exception:
            pass
    if os.path.exists(url_path):
        try:
            meta["supabase_url"] = open(url_path, encoding="utf-8").read().strip()
        except Exception:
            pass
    return meta


async def save_profile(key: str, wav_bytes: bytes, transcript: str) -> bool:
    """Store a creator's voice sample + its transcript. Durable via Supabase
    when configured; local /tmp copy always kept for this instance."""
    if not wav_bytes or len(wav_bytes) > MAX_PROFILE_BYTES:
        return False
    try:
        path = _profile_path(key)
        await asyncio.to_thread(_write, path, wav_bytes)
        await asyncio.to_thread(_write, path + ".txt", (transcript or "").encode("utf-8"))
    except Exception as e:
        logger.error(f"voice profile save failed: {e}")
        return False

    # Durable copy → Supabase Storage (survives Render redeploys)
    try:
        from server.persistent_memory import upload_file
        url = await upload_file(
            wav_bytes, f"voice_{hashlib.sha256(key.encode()).hexdigest()[:24]}.wav",
            "audio/wav", folder="voice_profiles",
        )
        if url:
            await asyncio.to_thread(_write, path + ".url", url.encode())
            logger.info("voice profile persisted to supabase")
    except Exception as e:
        logger.warning(f"supabase profile upload skipped: {e}")
    return True


def _write(path: str, data) -> None:
    with open(path, "wb") as f:
        f.write(data)


async def get_profile(key: str) -> Optional[dict]:
    """Return {'wav_bytes', 'ref_text'} for a stored profile, or None.
    Restores from Supabase if the local copy was wiped by a redeploy."""
    meta = _profile_meta(key)
    if os.path.exists(meta["path"]):
        try:
            wav = await asyncio.to_thread(_read, meta["path"])
            return {"wav_bytes": wav, "ref_text": meta["ref_text"]}
        except Exception:
            pass
    if meta["supabase_url"]:
        try:
            wav = await asyncio.to_thread(_download, meta["supabase_url"])
            if wav:
                await asyncio.to_thread(_write, meta["path"], wav)
                return {"wav_bytes": wav, "ref_text": meta["ref_text"]}
        except Exception as e:
            logger.warning(f"profile restore from supabase failed: {e}")
    return None


def _read(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def _download(url: str) -> Optional[bytes]:
    with urllib.request.urlopen(url, timeout=30) as r:
        if r.status == 200:
            return r.read()
    return None


async def has_profile(key: str) -> bool:
    return await get_profile(key) is not None


# ── cloning synthesis ─────────────────────────────────────────────────────────
def _clone_sync(profile_path: str, ref_text: str, gen_text: str) -> tuple[Optional[bytes], str]:
    """Call the F5-TTS Space. Returns (wav_bytes, error)."""
    from gradio_client import Client, handle_file

    last_err = "no engine reachable"
    for space in F5_SPACES:
        try:
            client = Client(space, verbose=False)
            result = client.predict(
                ref_audio=handle_file(profile_path),
                ref_text=ref_text,
                gen_text=gen_text[:MAX_GEN_CHARS],
                remove_silence=False,
                api_name="/predict",
            )
            # result is a path (or tuple containing one) to the generated wav
            out_path = None
            if isinstance(result, str):
                out_path = result
            elif isinstance(result, (tuple, list)):
                for item in result:
                    if isinstance(item, str) and os.path.exists(item):
                        out_path = item
                        break
            if not out_path or not os.path.exists(out_path):
                last_err = f"{space}: no audio in response"
                continue
            with open(out_path, "rb") as f:
                return f.read(), ""
        except Exception as e:
            last_err = f"{space}: {str(e)[:160]}"
            logger.warning(f"clone attempt failed — {last_err}")
            continue
    return None, last_err


async def synthesize_cloned_voice(key: str, gen_text: str) -> tuple[Optional[bytes], str]:
    """Generate a voiceover in the creator's cloned voice. Returns (wav_bytes, err).
    send_voice() handles the final OGG/OPUS transcode for Telegram."""
    profile = await get_profile(key)
    if not profile:
        return None, "no voice profile — send /voiceclone and a 10-20s voice sample first"
    if not (gen_text or "").strip():
        return None, "no voiceover text provided"
    ref_text = profile["ref_text"]
    if not ref_text:
        # F5-TTS needs the sample transcript; without it cloning is unreliable
        return None, "voice sample transcript missing — re-register with /voiceclone"

    tmp_path = _profile_path(key) + ".gen_ref.wav"
    await asyncio.to_thread(_write, tmp_path, profile["wav_bytes"])
    try:
        wav, err = await asyncio.to_thread(_clone_sync, tmp_path, ref_text, gen_text)
        return wav, err
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass


def is_voiceover_intent(text: str) -> Optional[str]:
    """Detect 'voiceover: X' / 'clone my voice and say X' style requests.
    Returns the voiceover text, or None."""
    import re
    t = (text or "").strip()
    if not t:
        return None
    low = t.lower()
    m = re.match(r'^(voiceover|voice over|clone voice|cloned voice|my voice)\s*:\s*(.+)$',
                 low, re.DOTALL)
    if m:
        return t[len(m.group(1)) + 1:].strip()
    m = re.search(r'\b(?:clone|mimic|use)\s+(?:my|the)\s+voice\b[^.!?]*?(?:and\s+)?(?:say|speak|read|narrate)\s*[:\-]?\s*(.+)$',
                  low, re.DOTALL)
    if m:
        return t[m.start(1):].strip()
    m = re.search(r'\b(?:make|create|do|generate)\s+(?:a\s+)?(?:voiceover|voice over|voice-over)\b[^:]*?:\s*(.+)$',
                  low, re.DOTALL)
    if m:
        return t[m.start(1):].strip()
    return None
