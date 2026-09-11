"""
S.T.E.W Professional Video Editor — file-based editing engine for uploaded videos.

Design constraints (Render 512MB free tier):
  * Output capped at 1080p (1080x1920 vertical / 1920x1080 landscape)
  * Max 2 concurrent ffmpeg jobs (asyncio.Semaphore)
  * One-pass filtergraph wherever possible; temp files cleaned by callers
  * Every edit re-encodes H.264 + AAC with +faststart for instant social playback

Understands natural-language edit requests:
  "make this a reel" "convert to tiktok format" "crop to 9:16"
  "trim 0:10 to 0:45" "first 30 seconds" "cut from 15s for 20s"
  "speed 2x" "slow motion" "reverse it"
  "black and white" "vintage" "cinematic" "brighten" "sharpen" "vignette"
  "rotate left" "flip horizontal" "mute" "volume 50" "extract audio"
  "add captions" "put text HELLO on top" "watermark @voscn247"
  "fade in" "fade out" "make it a gif" "thumbnail" "compress for whatsapp"
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import time
import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# ── resource guard: max 2 concurrent ffmpeg renders (standing rule) ─────────
_RENDER_SEMAPHORE = asyncio.Semaphore(2)

MAX_OUT_W, MAX_OUT_H = 1080, 1920

FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

# ── presets: name → (width, height, label) ───────────────────────────────────
PRESETS = {
    "reel": (1080, 1920, "Instagram Reels"),
    "reels": (1080, 1920, "Instagram Reels"),
    "tiktok": (1080, 1920, "TikTok"),
    "shorts": (1080, 1920, "YouTube Shorts"),
    "short": (1080, 1920, "YouTube Shorts"),
    "story": (1080, 1920, "IG/WhatsApp Story"),
    "status": (1080, 1920, "WhatsApp Status"),
    "vertical": (1080, 1920, "Vertical 9:16"),
    "portrait": (1080, 1920, "Vertical 9:16"),
    "youtube": (1920, 1080, "YouTube 16:9"),
    "landscape": (1920, 1080, "Landscape 16:9"),
    "square": (1080, 1080, "Square 1:1"),
    "twitter": (1280, 720, "X/Twitter"),
}

EDIT_MENU = (
    "🎬 *S.T.E.W Video Editor — got it!*\n\n"
    "Reply with any edit (or combine them):\n"
    "1. *Reels/Shorts/TikTok* — 'make it a reel'\n"
    "2. *Trim* — 'cut 0:10 to 0:30'\n"
    "3. *Captions* — 'add captions' (auto-subtitles)\n"
    "4. *Text/Watermark* — 'put text NEW DROP on top'\n"
    "5. *Filters* — 'cinematic', 'black and white', 'vintage', 'brighten'\n"
    "6. *Speed* — 'speed 2x', 'slow motion'\n"
    "7. *Audio* — 'mute', 'extract audio', 'volume 50%'\n"
    "8. *Rotate/Flip* — 'rotate left', 'flip horizontal'\n"
    "9. *Reverse* — 'reverse it'\n"
    "10. *Extras* — 'fade in', 'gif', 'thumbnail', 'compress'\n"
    "11. *Loom-style cuts* — 'remove the silence' (auto-cut dead air)\n"
    "12. *Higgsfield-style motion* — 'zoom in', 'ken burns', 'pan right'\n"
    "13. *Read it* — 'summarize this video'\n\n"
    "_Example:_ 'make it a reel, cut 0:05 to 0:35, add captions, cinematic'"
)


# ── intent parsing ──────────────────────────────────────────────────────────
def _parse_time(s: str) -> float:
    """'90', '1:30', '01:02:03' → seconds."""
    s = s.strip()
    if ":" in s:
        sec = 0.0
        for p in s.split(":"):
            sec = sec * 60 + float(p)
        return sec
    return float(s)


def is_read_intent(text: str) -> bool:
    lower = text.lower().strip()
    return bool(re.search(
        r"\b(summari[sz]e|analy[sz]e|watch|transcribe|describe|what('| i)?s (in|this))\b.{0,24}\bvideo\b"
        r"|^(read|summari[sz]e|analy[sz]e|watch|transcribe)( this| it)?$"
        r"|^what('| i)?s (in|this) video", lower))


def parse_edit_request(text: str) -> list[dict]:
    """Turn natural language into ops. Empty list = not an edit request."""
    lower = text.lower().strip()
    ops: list[dict] = []

    # ── format / preset ──
    preset_hit = re.search(r"\b9:16\b", lower) or re.search(r"\b16:9\b", lower) or re.search(r"\b1:1\b", lower)
    if preset_hit:
        ops.append({"op": "preset", "name": preset_hit.group(0)})
    else:
        for key in PRESETS:
            if re.search(rf"\b{re.escape(key)}\b", lower) and re.search(
                    r"\b(make|turn|convert|change|edit|crop|resize|format|switch|as|to)\b|"
                    rf"{re.escape(key)} (format|version|style|size)|^{re.escape(key)}$", lower):
                ops.append({"op": "preset", "name": key})
                break

    # ── trim ──
    m = re.search(r"\b(?:trim|cut|clip)\w* (?:from )?(?:the )?(?:first )?(\d+(?::\d{1,2}){0,2})\s*(?:s|sec|secs|seconds)?\s*(?:to|until|till|-|–|—)\s*(\d+(?::\d{1,2}){0,2})", lower)
    if m:
        ops.append({"op": "trim", "start": _parse_time(m.group(1)), "end": _parse_time(m.group(2))})
    else:
        m = re.search(r"\b(?:from|starting(?: at)?) (\d+(?::\d{1,2}){0,2})\s*(?:s|secs?|seconds?)? (?:for|lasting) (\d+(?::\d{1,2}){0,2})", lower)
        if m:
            st = _parse_time(m.group(1))
            ops.append({"op": "trim", "start": st, "end": st + _parse_time(m.group(2))})
        else:
            m = re.search(r"\bfirst (\d+(?::\d{1,2}){0,2})\s*(?:s|secs?|seconds?)\b", lower)
            if m:
                ops.append({"op": "trim", "start": 0, "end": _parse_time(m.group(1))})
            else:
                m = re.search(r"\blast (\d+(?::\d{1,2}){0,2})\s*(?:s|secs?|seconds?)\b", lower)
                if m:
                    ops.append({"op": "trim_from_end", "dur": _parse_time(m.group(1))})
                else:
                    m = re.search(r"\b(?:cut|remove|delete|take out) (?:the )?first (\d+(?::\d{1,2}){0,2})\s*(?:s|secs?|seconds?)", lower)
                    if m:
                        ops.append({"op": "trim_after", "skip": _parse_time(m.group(1))})

    # ── speed ──
    m = re.search(r"\b(\d+(?:\.\d+)?)\s*x\b", lower)
    if m and re.search(r"\b(speed|faster|slow|slower|motion|times)\b", lower):
        ops.append({"op": "speed", "factor": max(0.25, min(4.0, float(m.group(1))))})
    elif re.search(r"\bslow ?mo(tion)?\b", lower):
        ops.append({"op": "speed", "factor": 0.5})
    elif re.search(r"\bspeed (?:it )?up\b|\bmake it faster\b|\bfaster\b", lower):
        ops.append({"op": "speed", "factor": 1.5})
    elif re.search(r"\bslow(er)? (it|this|down)?\b", lower) and "slow" in lower and not re.search(r"\bslow ?mo(tion)?\b", lower):
        ops.append({"op": "speed", "factor": 0.75})

    # ── reverse ──
    if re.search(r"\breverse\b", lower):
        ops.append({"op": "reverse"})

    # ── Loom-style auto-cuts ──
    if re.search(r"\b(remove|cut|delete|strip|trim) (the |all |any )?(silence|silents|dead air|pauses|gaps)\b|\bno silence\b|\btighten (it|the video|up)\b|\bauto ?cut\b", lower):
        ops.append({"op": "remove_silence"})

    # ── Higgsfield-style camera motion ──
    if re.search(r"\bken burns\b|\bcinematic (move|motion|pan)\b", lower):
        ops.append({"op": "motion", "name": "ken_burns"})
    elif re.search(r"\bzoom (in|into)\b|\bslow zoom\b", lower):
        ops.append({"op": "motion", "name": "zoom_in"})
    elif re.search(r"\bzoom out\b", lower):
        ops.append({"op": "motion", "name": "zoom_out"})
    if re.search(r"\bpan (to the )?(left|right)\b|\bmove (the )?camera (to the )?(left|right)\b", lower):
        _dir = "left" if re.search(r"\bleft\b", lower) else "right"
        ops.append({"op": "motion", "name": f"pan_{_dir}"})

    # ── rotate / flip ──
    if re.search(r"\brotate\b", lower):
        ops.append({"op": "rotate", "dir": "ccw" if re.search(r"\b(left|anti ?clockwise|ccw)\b", lower) else "cw"})
    if re.search(r"\bflip\b.*\b(horizontal(ly)?|h)\b|\bmirror\b", lower):
        ops.append({"op": "flip", "axis": "h"})
    elif re.search(r"\bflip\b.*\b(vertical(ly)?|v|upside ?down)\b", lower):
        ops.append({"op": "flip", "axis": "v"})

    # ── audio ──
    if re.search(r"\b(remove|delete|drop|strip|kill) (the )?(audio|sound|music)\b|\bmute\b|\bno (audio|sound)\b", lower):
        ops.append({"op": "mute"})
    m = re.search(r"\bvolume (\d{1,3})\s*%?\b", lower)
    if m:
        ops.append({"op": "volume", "pct": max(0, min(200, int(m.group(1))))})
    if re.search(r"\bextract (the )?audio\b|\b(?:save|give) (me )?(the )?audio\b|\baudio only\b|\bto mp3\b|^mp3$", lower):
        ops.append({"op": "extract_audio"})

    # ── looks / filters ──
    looks = {
        "bw": r"\bblack ?(and|&)? ?white\b|\bb&w\b|\bgrayscale\b|\bmono(chrome)?\b",
        "sepia": r"\bsepia\b",
        "vintage": r"\bvintage\b|\bfilm ?grain\b|\b8mm\b|\bold (school|film)",
        "cinematic": r"\bcinematic\b|\bfilmic\b|\bmovie (look|style)\b",
        "bright": r"\bbrighten\b|\bbrighter\b|\blighten\b",
        "moody": r"\bdarken\b|\bdarker\b|\bmoody\b",
        "vivid": r"\bvivid\b|\bmore colou?r\b|\bsaturate\b|\bboost (the )?colou?rs?\b",
        "sharpen": r"\bsharpen\b|\bcrisper\b|\bhd quality\b",
        "vignette": r"\bvignette\b",
        "fade_in": r"\bfade ?in\b",
        "fade_out": r"\bfade ?out\b",
        "blur_bg": r"\bblur(red|ry)? background\b|\bblur ?pad\b",
    }
    for name, pat in looks.items():
        if re.search(pat, lower):
            ops.append({"op": "filter", "name": name})

    # ── captions ──
    if re.search(r"\bcaptions?\b|\bsubtitles?\b|\bsubs\b", lower) and re.search(
            r"\b(add|burn|put|generate|give|auto|need|with|make|do|apply)\b|\bcaptions?\b", lower):
        ops.append({"op": "captions"})

    # ── text overlay / watermark ──
    m = re.search(r"[\"'“”]([^\"'“”]{1,60})[\"'“”]", text)
    if m and re.search(r"\b(text|title|write|put|overlay|watermark|stamp)\b", lower):
        ops.append({"op": "text", "content": m.group(1).strip(),
                    "pos": "top" if re.search(r"\bon top|top\b", lower) else "bottom"})
    else:
        m = re.search(r"\b(?:put|add|write) (?:the )?(?:text|title|words?) ([A-Za-z0-9@#][^\n,.;]{0,50})", text)
        if m:
            ops.append({"op": "text", "content": m.group(1).strip(),
                        "pos": "top" if re.search(r"\bon top\b", lower) else "bottom"})
    if re.search(r"\bwatermark\b", lower):
        ops.append({"op": "text", "content": "@S.T.E.W", "pos": "corner"})

    # ── gif / thumbnail / compress ──
    if re.search(r"\bgif\b", lower):
        ops.append({"op": "gif"})
    if re.search(r"\bthumbnail\b|\bposter\b|\bcover (photo|frame)\b", lower):
        ops.append({"op": "thumbnail"})
    if re.search(r"\bcompress\b|\breduce (size|quality)\b|\bsmaller (file|size)\b|\bwhatsapp (size|format)\b|\bmake it (smaller|lighter)\b", lower):
        ops.append({"op": "compress"})

    return ops


def is_edit_intent(text: str) -> bool:
    return len(parse_edit_request(text)) > 0


# ── ffmpeg plumbing ─────────────────────────────────────────────────────────
def _run_ffmpeg(args: list[str], timeout: int = 900) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"] + args,
            capture_output=True, timeout=timeout,
        )
        if proc.returncode == 0:
            return True, ""
        return False, proc.stderr.decode(errors="ignore")[-400:]
    except subprocess.TimeoutExpired:
        return False, "Render timed out"
    except Exception as e:
        return False, str(e)


def _probe(path: str) -> dict:
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", path],
            capture_output=True, timeout=30,
        )
        meta = json.loads(proc.stdout.decode())
        dur = float(meta.get("format", {}).get("duration", 0) or 0)
        w = h = 0
        has_audio = False
        for s in meta.get("streams", []):
            if s.get("codec_type") == "video" and not w:
                w, h = s.get("width", 0), s.get("height", 0)
            if s.get("codec_type") == "audio":
                has_audio = True
        return {"duration": dur, "width": w, "height": h, "has_audio": has_audio,
                "size": os.path.getsize(path) if os.path.exists(path) else 0}
    except Exception:
        return {"duration": 0, "width": 0, "height": 0, "has_audio": False,
                "size": os.path.getsize(path) if os.path.exists(path) else 0}


def _esc_text(s: str) -> str:
    return (s.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\u2019")
             .replace("%", "\\%").replace(",", "\\,"))


def _build_look(name: str) -> Optional[str]:
    """Named looks → ffmpeg filter chain."""
    if name == "bw":
        return "hue=s=0,eq=contrast=1.05"
    if name == "sepia":
        return ("colorchannelmixer=rr=0.393:rg=0.769:rb=0.189:gr=0.349:gg=0.689:gb=0.168:"
                "br=0.272:bg=0.534:bb=0.131,eq=contrast=1.05:brightness=0.02")
    if name == "vintage":
        return ("colorchannelmixer=rr=0.393:rg=0.769:rb=0.189:gr=0.349:gg=0.689:gb=0.168:"
                "br=0.272:bg=0.534:bb=0.131,eq=contrast=1.1:brightness=0.03:saturation=0.85,"
                "noise=alls=6:allf=t,vignette=PI/4")
    if name == "cinematic":
        return "eq=contrast=1.15:saturation=1.1:brightness=-0.015:gamma=0.95,vignette=PI/5"
    if name == "bright":
        return "eq=brightness=0.09:gamma=1.06:saturation=1.05"
    if name == "moody":
        return "eq=brightness=-0.05:contrast=1.12:saturation=0.85:gamma=0.95"
    if name == "vivid":
        return "eq=saturation=1.45:contrast=1.08"
    if name == "sharpen":
        return "unsharp=5:5:1.4:5:5:0.6"
    if name == "vignette":
        return "vignette=PI/4"
    return None


def _detect_speech_intervals(input_path: str, in_args: list[str], min_gap: float = 0.45, noise_db: float = -32) -> list[tuple[float, float]]:
    """Run silencedetect on the (possibly trimmed) input; return speech (non-silent)
    intervals [start, end]. Returns [] when detection fails (caller keeps whole video)."""
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-i", input_path] + in_args +
            ["-af", f"silencedetect=noise={noise_db}dB:d={min_gap}", "-f", "null", "-"],
            capture_output=True, timeout=180,
        )
        log = proc.stderr.decode(errors="ignore")
        sils: list[tuple[float, float]] = []
        starts = [float(m) for m in re.findall(r"silence_start: ([0-9.]+)", log)]
        ends = [float(m) for m in re.findall(r"silence_end: ([0-9.]+)", log)]
        for i, st in enumerate(starts):
            en = ends[i] if i < len(ends) else None
            sils.append((st, en))
        if not sils:
            return [(0.0, 1e9)]  # no silence detected -> keep everything
        # build speech intervals between silences
        speech, cursor = [], 0.0
        for st, en in sils:
            if st - cursor >= 0.15:
                speech.append((cursor, st))
            cursor = en if en else st + min_gap
        speech.append((cursor, 1e9))
        # merge tiny gaps back (< 0.25s of speech is a blip)
        merged = []
        for a, b in speech:
            if merged and a - merged[-1][1] < 0.25:
                merged[-1] = (merged[-1][0], b)
            else:
                merged.append((a, b))
        return merged or [(0.0, 1e9)]
    except Exception as e:
        logger.warning(f"silencedetect failed: {e}")
        return []


def _motion_filter(name: str, w: int, h: int, dur: float) -> Optional[str]:
    """Higgsfield-style camera motion via zoompan (applied at source resolution)."""
    if not w or not h:
        return None
    total_frames = max(25, int((dur or 10) * 25))
    if name == "zoom_in":
        return (f"zoompan=z='min(1+0.0018*in,1.45)':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                f"s={w}x{h}:fps=25")
    if name == "zoom_out":
        return (f"zoompan=z='max(1.45-0.0018*in,1.0)':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                f"s={w}x{h}:fps=25")
    if name == "pan_left":
        return (f"zoompan=z=1.25:d=1:x='(iw-iw/zoom)*(1-min(in/{total_frames},1))':y='ih/2-(ih/zoom/2)':"
                f"s={w}x{h}:fps=25")
    if name == "pan_right":
        return (f"zoompan=z=1.25:d=1:x='(iw-iw/zoom)*min(in/{total_frames},1)':y='ih/2-(ih/zoom/2)':"
                f"s={w}x{h}:fps=25")
    if name == "ken_burns":
        return (f"zoompan=z='min(1+0.0012*in,1.3)':d=1:x='iw/2-(iw/zoom/2)':"
                f"y='ih/2-(ih/zoom/2)+ih*0.05*min(in/{total_frames},1)':s={w}x{h}:fps=25")
    return None


def _atempo_chain(factor: float) -> str:
    """atempo accepts 0.5–2.0 per stage — chain for extremes."""
    stages, remaining = [], factor
    while remaining > 2.0:
        stages.append("atempo=2.0")
        remaining /= 2.0
    while remaining < 0.5:
        stages.append("atempo=0.5")
        remaining *= 2.0
    stages.append(f"atempo={max(0.5, min(2.0, remaining)):.4g}")
    return ",".join(stages)


async def _transcribe_for_captions(input_path: str, tmp_dir: str, transcriber) -> Optional[str]:
    """Extract + transcribe the audio track for caption burning."""
    audio_path = os.path.join(tmp_dir, f"cap_audio_{os.getpid()}.ogg")
    ok, _ = _run_ffmpeg(["-i", input_path, "-vn", "-ac", "1", "-ar", "16000",
                         "-c:a", "libopus", audio_path], timeout=180)
    if not ok or not os.path.exists(audio_path):
        return None
    with open(audio_path, "rb") as f:
        audio_bytes = f.read()
    if not audio_bytes:
        return None
    try:
        if transcriber is None:
            return None
        result = transcriber(audio_bytes)
        if asyncio.iscoroutine(result):
            result = await result
        if isinstance(result, tuple):        # (transcript, err) style
            text = str(result[0] or "")
        elif isinstance(result, dict):
            text = str(result.get("text") or "")
        else:
            text = str(result or "")
        return text.strip() or None
    except Exception as e:
        logger.warning(f"caption transcribe failed: {e}")
        return None


def _build_srt(transcript: str, duration: float, srt_path: str) -> bool:
    """Spread transcript words across duration in 4-word chunks (reel style)."""
    words = transcript.split()
    if not words:
        return False
    chunks = [" ".join(words[i:i + 4]) for i in range(0, len(words), 4)]
    per = max(1.2, duration / len(chunks))

    def ts(t: float) -> str:
        h = int(t // 3600); m = int((t % 3600) // 60); s = int(t % 60); ms = int((t % 1) * 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    lines, t = [], 0.0
    for i, c in enumerate(chunks):
        end = min(duration or (t + per), t + per)
        lines.append(f"{i+1}\n{ts(t)} --> {ts(end)}\n{c}\n")
        t = end
    with open(srt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return True


# ── main entry ──────────────────────────────────────────────────────────────
async def edit_video_file(
    input_path: str,
    text: str,
    tmp_dir: str,
    transcriber: Optional[Callable] = None,
    target_mb: float = 15.0,
) -> dict:
    """Apply the requested edits to a local video file.
    Returns {"ok", "output", "kind": video|gif|image|audio, "applied", ...}"""
    ops = parse_edit_request(text)
    if not ops:
        return {"ok": False, "error": "no_edit_intent"}
    if not os.path.exists(input_path):
        return {"ok": False, "error": "Source video missing — send it again."}

    os.makedirs(tmp_dir, exist_ok=True)
    src = _probe(input_path)

    async with _RENDER_SEMAPHORE:
        try:
            return await _apply_ops(input_path, ops, src, tmp_dir, transcriber, target_mb)
        except Exception as e:
            logger.error(f"video editor failed: {e}", exc_info=True)
            return {"ok": False, "error": f"Editor error: {str(e)[:160]}"}


async def _apply_ops(input_path, ops, src, tmp_dir, transcriber, target_mb) -> dict:
    dur = src["duration"]
    filters: list[str] = []
    in_args: list[str] = []
    applied: list[str] = []
    kind = "video"

    # ---------------- single-output ops first ----------------
    if any(o["op"] == "gif" for o in ops):
        out_path = os.path.join(tmp_dir, "edited.gif")
        if _make_gif(input_path, tmp_dir, out_path):
            return {"ok": True, "output": out_path, "kind": "gif",
                    "applied": ["Converted to GIF (≤12s, 480px)"], "size_mb": round(os.path.getsize(out_path) / 1048576, 1)}
        return {"ok": False, "error": "GIF conversion failed — try trimming to a shorter piece first."}

    if any(o["op"] == "extract_audio" for o in ops):
        mp3_path = os.path.join(tmp_dir, "audio.mp3")
        ok, err = _run_ffmpeg(["-i", input_path, "-vn", "-c:a", "libmp3lame", "-q:a", "4", mp3_path])
        if not ok:
            return {"ok": False, "error": f"Audio extraction failed: {err[:140]}"}
        return {"ok": True, "output": mp3_path, "kind": "audio", "applied": ["Audio extracted as MP3"]}

    if any(o["op"] == "thumbnail" for o in ops):
        thumb_path = os.path.join(tmp_dir, "thumbnail.jpg")
        at = max(0.0, (dur / 2 if dur else 1.0) - 0.5)
        ok, err = _run_ffmpeg(["-ss", f"{at:.2f}", "-i", input_path, "-frames:v", "1", "-q:v", "2", thumb_path], timeout=120)
        if not ok:
            return {"ok": False, "error": f"Thumbnail failed: {err[:140]}"}
        return {"ok": True, "output": thumb_path, "kind": "image", "applied": [f"Thumbnail from {int(at)}s"]}

    # ---------------- combined pipeline ----------------
    # trim (input-side seek = fast)
    trim_applied = False
    out_dur = dur
    for o in ops:
        if o["op"] == "trim":
            start = max(0.0, o["start"])
            end = o["end"] if not dur else min(dur, o["end"])
            if end <= start:
                end = start + 10
            in_args = ["-ss", f"{start:.3f}", "-to", f"{end:.3f}"]
            out_dur = end - start
            applied.append(f"Trimmed {int(start)}s → {int(end)}s")
            trim_applied = True
            break
    if not trim_applied:
        for o in ops:
            if o["op"] == "trim_after":
                in_args = ["-ss", f"{o['skip']:.3f}"]
                if dur:
                    out_dur = dur - o["skip"]
                applied.append(f"Cut first {int(o['skip'])}s off")
                break
            if o["op"] == "trim_from_end" and dur:
                in_args = ["-ss", f"{max(0.0, dur - o['dur']):.3f}"]
                out_dur = o["dur"]
                applied.append(f"Last {int(o['dur'])}s kept")
                break

    # speed / reverse / rotate / flip
    speed = 1.0
    for o in ops:
        if o["op"] == "speed":
            speed = o["factor"]
        elif o["op"] == "reverse":
            filters.append("reverse")
            applied.append("Reversed")
        elif o["op"] == "rotate":
            filters.append("transpose=2" if o["dir"] == "ccw" else "transpose=1")
            applied.append(f"Rotated {'left' if o['dir'] == 'ccw' else 'right'}")
        elif o["op"] == "flip":
            filters.append("hflip" if o["axis"] == "h" else "vflip")
            applied.append(f"Flipped {'horizontally' if o['axis'] == 'h' else 'vertically'}")
    if speed != 1.0:
        applied.append(f"{speed}x speed")
        out_dur = (out_dur / speed) if out_dur else out_dur

    # Higgsfield-style camera motion — applied FIRST (at source resolution)
    motion_op = next((o for o in ops if o["op"] == "motion"), None)
    if motion_op and src["width"]:
        mf = _motion_filter(motion_op["name"], src["width"], src["height"], out_dur or dur)
        if mf:
            filters.insert(0, mf)
            applied.append(f"Camera move: {motion_op['name'].replace('_', ' ').title()}")

    # audio chain init (needed by silence removal below)
    mute = any(o["op"] == "mute" for o in ops)
    vol = next((o for o in ops if o["op"] == "volume"), None)
    has_audio = src["has_audio"]
    af_chain: list[str] = []

    # Loom-style silence removal — speech intervals -> select/aselect cuts
    if any(o["op"] == "remove_silence" for o in ops):
        intervals = _detect_speech_intervals(input_path, in_args)
        if intervals and intervals != [(0.0, 1e9)]:
            keep = "+".join(
                f"between(t,{max(0.0, a):.2f},{b if b < 1e8 else 36000:.2f})" for a, b in intervals)
            filters.insert(0, f"select='{keep}',setpts=N/FRAME_RATE/TB")
            if not mute and has_audio:
                af_chain.insert(0, f"aselect='{keep}',asetpts=N/SR/TB")
            applied.append("Silence auto-removed (Loom-style cut)")
        else:
            applied.append("No dead air found — nothing to cut")

    # looks
    for o in ops:
        if o["op"] == "filter" and o["name"] not in ("blur_bg", "fade_in", "fade_out"):
            chain = _build_look(o["name"])
            if chain:
                filters.append(chain)
                applied.append(o["name"].replace("_", " ").title())

    # text overlays
    for o in ops:
        if o["op"] == "text":
            pos = o.get("pos", "bottom")
            y = {"top": "h*0.08", "bottom": "h*0.82", "corner": "h*0.05", "center": "(h-text_h)/2"}[pos]
            x = "w*0.03" if pos == "corner" else "(w-text_w)/2"
            box = "" if pos == "corner" else ":box=1:boxcolor=black@0.55:boxborderw=18"
            fsize = "h/16" if pos != "corner" else "h/34"
            filters.append(
                f"drawtext=fontfile={FONT_BOLD}:text='{_esc_text(o['content'])}':"
                f"fontcolor=white@{0.85 if pos == 'corner' else 1.0}:fontsize={fsize}{box}:x={x}:y={y}")
            applied.append(f"Text: {o['content'][:30]}")

    # fades
    for o in ops:
        if o["op"] == "filter" and o["name"] == "fade_in":
            filters.append("fade=t=in:st=0:d=1.2")
            applied.append("Fade in")
        if o["op"] == "filter" and o["name"] == "fade_out":
            if out_dur:
                filters.append(f"fade=t=out:st={max(0.0, out_dur - 1.2):.2f}:d=1.2")
                applied.append("Fade out")

    # preset / aspect
    preset_op = next((o for o in ops if o["op"] == "preset"), None)
    filter_complex = None
    preset_dims = None
    if preset_op:
        w, h, label = PRESETS[preset_op["name"]]
        preset_dims = (w, h)
        sw, sh = src["width"], src["height"]
        applied.append(f"{label} ({w}×{h})")
        want_blur = any(o["op"] == "filter" and o["name"] == "blur_bg" for o in ops)
        if sw and sh:
            src_ar, dst_ar = sw / sh, w / h
            if abs(src_ar - dst_ar) <= 0.02:
                filters.append(f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1")
            elif dst_ar < src_ar:
                # going taller (landscape → 9:16): blur-pad unless user said crop
                if re.search(r"\bcrop\b", (ops and str(ops)) or "") or "crop" in label.lower():
                    filters.append(f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1")
                    applied.append("Center-cropped")
                else:
                    filter_complex = (
                        f"[0:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
                        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1[fg];"
                        f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,"
                        f"crop={w}:{h},gblur=sigma=28,eq=brightness=-0.06[bg];"
                        f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[vout]")
                    applied.append("Blurred cinema background")
            else:
                filters.append(f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1")
                applied.append("Center-cropped")
        else:
            filters.append(f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1")

    if speed != 1.0:
        filters.append(f"setpts=PTS/{speed:.4g}")

    # captions (transcribe the trimmed region's audio from the ORIGINAL file so
    # timing is right even though we seek with -ss on the same input)
    srt_path = None
    if any(o["op"] == "captions" for o in ops):
        transcript = await _transcribe_for_captions(input_path, tmp_dir, transcriber)
        if transcript:
            srt_path = os.path.join(tmp_dir, "caps.srt")
            if _build_srt(transcript, out_dur or dur or 10, srt_path):
                applied.append(f"Auto-captions ({len(transcript.split())} words)")
        else:
            applied.append("Captions skipped — no speech detected")

    # audio handling (speed/volume ride on top of any silence-cut chain)
    audio_out_args: list[str] = []
    if mute or not has_audio:
        audio_out_args = ["-an"]
        if mute:
            applied.append("Audio muted")
    else:
        if speed != 1.0:
            af_chain.append(_atempo_chain(speed))
        if vol:
            af_chain.append(f"volume={vol['pct'] / 100:.2g}")
            applied.append(f"Volume {vol['pct']}%")

    if not applied:
        if any(o["op"] == "compress" for o in ops):
            applied.append("Optimized for size")
        else:
            return {"ok": False, "error": "no_edit_intent"}

    out_path = os.path.join(tmp_dir, f"edited_{os.getpid()}.mp4")

    # ---------------- render ----------------
    if filter_complex:
        # blur-pad path: complex graph handles video; audio via -af
        fc = filter_complex
        cmd = ["-i", input_path] + in_args + ["-filter_complex", fc, "-map", "[vout]"]
        if audio_out_args:
            cmd += audio_out_args
        else:
            cmd += ["-map", "0:a:0"]
            if af_chain:
                cmd += ["-af", ",".join(af_chain), "-c:a", "aac", "-b:a", "128k"]
            else:
                cmd += ["-c:a", "aac", "-b:a", "128k"]
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                "-pix_fmt", "yuv420p", "-movflags", "+faststart", out_path]
        ok, err = _run_ffmpeg(cmd)
        if not ok:
            return {"ok": False, "error": f"Render failed: {err[:180]}"}
    else:
        vf = list(filters)
        if srt_path and os.path.exists(srt_path):
            style = ("FontName=DejaVu Sans,FontSize=13,Bold=1,PrimaryColour=&H00FFFFFF,"
                     "OutlineColour=&HAA000000,BorderStyle=1,Outline=2,Shadow=1,MarginV=36,Alignment=2")
            vf.append(f"subtitles={srt_path}:force_style='{style}'")
        # resolution cap (protects 512MB RAM: never upscale, cap at 1080p)
        # — skipped when a preset already fixed the exact output dimensions
        if not preset_dims:
            vf.append(f"scale='min({MAX_OUT_W},iw)':'min({MAX_OUT_H},ih)':force_original_aspect_ratio=decrease")
        vf.append("setsar=1")
        cmd = ["-i", input_path] + in_args + ["-vf", ",".join(vf)]
        if audio_out_args:
            cmd += audio_out_args
        elif af_chain:
            cmd += ["-af", ",".join(af_chain), "-c:a", "aac", "-b:a", "128k"]
        else:
            cmd += ["-c:a", "aac", "-b:a", "128k"]
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                "-pix_fmt", "yuv420p", "-movflags", "+faststart", out_path]
        ok, err = _run_ffmpeg(cmd)
        if not ok:
            return {"ok": False, "error": f"Render failed: {err[:180]}"}

    # ---------------- size guard: WhatsApp 16MB / Telegram 50MB ----------------
    final = out_path

    def _compress(path: str) -> str:
        try:
            if os.path.getsize(path) <= target_mb * 1024 * 1024:
                return path
            pd = _probe(path)
            d = pd.get("duration") or 10
            total_kbps = max(100, int(target_mb * 8192 * 0.88 / d))
            v_kbps = max(100, int(total_kbps * 0.92))
            cpath = path.replace(".mp4", "_small.mp4")
            ok2, _ = _run_ffmpeg(
                ["-i", path, "-c:v", "libx264", "-preset", "veryfast", "-b:v", f"{v_kbps}k",
                 "-maxrate", f"{int(v_kbps * 1.5)}k", "-bufsize", f"{v_kbps * 2}k",
                 "-vf", "scale=-2:'min(720,ih)'", "-c:a", "aac", "-b:a", "48k",
                 "-movflags", "+faststart", cpath])
            if ok2 and os.path.exists(cpath) and os.path.getsize(cpath) < os.path.getsize(path):
                os.remove(path)
                return cpath
            return path
        except Exception as e:
            logger.warning(f"compress failed: {e}")
            return path

    final = _compress(out_path)
    if any(o["op"] == "compress" for o in ops):
        applied.append(f"Compressed ({os.path.getsize(final) // 1048576}MB)")

    pd = _probe(final)
    return {"ok": True, "output": final, "kind": "video", "applied": applied,
            "duration": pd.get("duration"), "size_mb": round(pd.get("size", 0) / 1048576, 1)}


def _make_gif(input_path: str, tmp_dir: str, out_path: str, max_dur: float = 10.0) -> bool:
    """Two-pass palette GIF, capped at 10s / 480px for size sanity."""
    dur = _probe(input_path).get("duration") or 0
    trim = ["-t", f"{min(max_dur, dur):.2f}"] if dur > max_dur else []
    pal = os.path.join(tmp_dir, "palette.png")
    ok, _ = _run_ffmpeg(["-i", input_path] + trim +
                        ["-vf", "fps=12,scale=480:-1:flags=lanczos,palettegen=stats_mode=diff", pal], timeout=300)
    if not ok:
        return False
    ok, _ = _run_ffmpeg(["-i", input_path, "-i", pal] + trim +
                        ["-filter_complex", "fps=12,scale=480:-1:flags=lanczos[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=3", out_path], timeout=300)
    if not ok:
        return False
    if os.path.getsize(out_path) > 14 * 1024 * 1024:
        ok, _ = _run_ffmpeg(["-i", input_path, "-i", pal, "-t", "5",
                             "-filter_complex", "fps=10,scale=360:-1:flags=lanczos[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=3", out_path], timeout=300)
    return os.path.exists(out_path) and os.path.getsize(out_path) <= 14 * 1024 * 1024


# ── pending-video registry (per-chat, TTL 30 min) ────────────────────────────
_PENDING: dict[str, dict] = {}
PENDING_TTL = 30 * 60


def store_pending(key: str, path: str, label: str = "video"):
    """Remember the last video a user sent so follow-up edits can use it."""
    _cleanup()
    _PENDING[key] = {"path": path, "ts": time.time(), "label": label}


def get_pending(key: str) -> Optional[dict]:
    _cleanup()
    p = _PENDING.get(key)
    if not p:
        return None
    if not os.path.exists(p["path"]):
        _PENDING.pop(key, None)
        return None
    return p


def pop_pending(key: str):
    p = _PENDING.pop(key, None)
    if p and os.path.exists(p["path"]):
        try:
            os.remove(p["path"])
        except OSError:
            pass


def _cleanup():
    now = time.time()
    for k in [k for k, v in _PENDING.items() if now - v["ts"] > PENDING_TTL]:
        p = _PENDING.pop(k)
        try:
            if os.path.exists(p["path"]):
                os.remove(p["path"])
        except OSError:
            pass
