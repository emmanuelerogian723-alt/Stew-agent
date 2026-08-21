"""
S.T.E.W Video Tools — Video clipping, creation, and editing.
Uses ffmpeg (system) + yt-dlp + edge-tts + Pollinations for free video processing.
No paid API keys required.
"""
import os
import re
import json
import time
import asyncio
import logging
import tempfile
import subprocess
from typing import Optional
from datetime import datetime

logger = logging.getLogger(__name__)

# ─── Helpers ────────────────────────────────────────────────────────────────

def _run_ffmpeg(args: list[str], timeout: int = 120) -> tuple[bool, str]:
    """Run an ffmpeg command. Returns (success, error_msg)."""
    try:
        cmd = ["ffmpeg", "-y", "-loglevel", "error"] + args
        result = subprocess.run(cmd, capture_output=True, timeout=timeout)
        if result.returncode == 0:
            return True, ""
        return False, result.stderr.decode("utf-8", errors="replace")[:500]
    except subprocess.TimeoutExpired:
        return False, "ffmpeg timed out"
    except FileNotFoundError:
        return False, "ffmpeg not installed"
    except Exception as e:
        return False, str(e)


# ── yt-dlp self-update (once per process, not per-request — avoids adding
#    ~1-2s pip overhead to every single /clip call) ─────────────────────────
_YTDLP_UPDATED = False


def _update_ytdlp_once():
    global _YTDLP_UPDATED
    if _YTDLP_UPDATED:
        return
    _YTDLP_UPDATED = True
    try:
        subprocess.run(
            ["pip", "install", "--upgrade", "-q", "yt-dlp"],
            capture_output=True, timeout=30,
        )
    except Exception:
        pass  # non-fatal — try with whatever version we have


_YOUTUBE_HOSTS = ("youtube.com", "youtu.be", "m.youtube.com", "music.youtube.com")


def _is_youtube_url(url: str) -> bool:
    return any(h in url.lower() for h in _YOUTUBE_HOSTS)


def _friendly_ytdlp_error(raw_err: str) -> str:
    """Map common yt-dlp/YouTube failure signatures to a clear user-facing message."""
    low = raw_err.lower()
    if "sign in" in low or "confirm you" in low or "not a bot" in low:
        return "YouTube is blocking automated downloads for this video right now. Try a different video or try again in a bit."
    if "403" in low or "forbidden" in low:
        return "YouTube blocked the download (403). This can happen with certain videos — try a different link."
    if "private" in low:
        return "That video is private and can't be downloaded."
    if "unavailable" in low or "removed" in low:
        return "That video is unavailable or has been removed."
    if "requested format is not available" in low or "no video formats" in low:
        return "Couldn't find a downloadable format for that video."
    if "requestsdependencywarning" in low or "warnings.warn" in low:
        return "Video download hit a temporary error. Please try again."
    if "timed out" in low:
        return "Download timed out — the video may be too long or the connection too slow."
    if "geo" in low and "restrict" in low:
        return "That video is geo-restricted and can't be downloaded from here."
    # Fall back to a trimmed, cleaned version of the real error
    cleaned = raw_err.strip().split("\n")[-1][:200] if raw_err.strip() else "Unknown error"
    return cleaned


def _run_ytdlp(url: str, output_path: str, timeout: int = 120) -> tuple[bool, str]:
    """Download a video using yt-dlp. Returns (success, error_msg).
    YouTube aggressively blocks datacenter/cloud IPs from its default 'web' client
    with HTTP 403 — the 'android' player client reliably bypasses this and is
    tried first for YouTube URLs. Other platforms (TikTok, Twitter/X, Instagram,
    direct mp4 links, Vimeo, etc.) use yt-dlp's generic/native extractors, which
    work fine without any special client flag."""
    _update_ytdlp_once()

    is_yt = _is_youtube_url(url)

    # Build an ordered list of attempts: (extra_args, format_string)
    attempts = []
    if is_yt:
        # 'android' client bypasses YouTube's cloud-IP bot detection (HTTP 403).
        # 'android_music' and a plain retry are fallbacks if the first fails.
        attempts = [
            (["--extractor-args", "youtube:player_client=android"], "best[ext=mp4]/best"),
            (["--extractor-args", "youtube:player_client=android_music"], "best[ext=mp4]/best"),
            (["--extractor-args", "youtube:player_client=ios,web"], "best[ext=mp4]/best"),
            ([], "best[ext=mp4][filesize<50M]/best[filesize<50M]/best"),
        ]
    else:
        attempts = [
            ([], "best[ext=mp4][filesize<50M]/best[filesize<50M]/best"),
            ([], "mp4/best"),
        ]

    last_err = ""
    for extra_args, fmt in attempts:
        try:
            cmd = [
                "yt-dlp",
                "-f", fmt,
                "--max-filesize", "50M",
                "--no-playlist",
                "--no-warnings",
                *extra_args,
                "-o", output_path,
                url,
            ]
            result = subprocess.run(cmd, capture_output=True, timeout=timeout)
            if result.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 1000:
                return True, ""
            last_err = result.stderr.decode("utf-8", errors="replace")
            # Clean up any partial/empty file before the next attempt
            if os.path.exists(output_path):
                try:
                    os.remove(output_path)
                except Exception:
                    pass
        except subprocess.TimeoutExpired:
            last_err = "Download timed out"
            continue
        except FileNotFoundError:
            return False, "yt-dlp not installed"
        except Exception as e:
            last_err = str(e)
            continue

    return False, _friendly_ytdlp_error(last_err)


def _get_video_duration(video_path: str) -> float:
    """Get video duration in seconds using ffprobe."""
    try:
        cmd = [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", video_path,
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=15)
        return float(result.stdout.decode().strip())
    except Exception:
        return 0.0


# Telegram Bot API limit for sending files via sendVideo
_TELEGRAM_MAX_VIDEO_SIZE = 48 * 1024 * 1024  # 48MB (safe margin under 50MB hard limit)


def _ensure_telegram_safe_size(video_path: str, tmp_dir: str) -> str:
    """If the video exceeds Telegram's 50MB bot API limit, re-encode it smaller.
    Returns the path to use (original if already small enough, or a compressed copy)."""
    try:
        size = os.path.getsize(video_path)
        if size <= _TELEGRAM_MAX_VIDEO_SIZE:
            return video_path
        logger.info(f"Video is {size / 1024 / 1024:.1f}MB — compressing for Telegram...")
        compressed_path = os.path.join(tmp_dir, "compressed.mp4")
        ok, err = _run_ffmpeg([
            "-i", video_path,
            "-c:v", "libx264", "-preset", "fast", "-crf", "30",
            "-b:v", "800k", "-maxrate", "1000k", "-bufsize", "2000k",
            "-c:a", "aac", "-b:a", "96k",
            "-movflags", "+faststart",
            compressed_path,
        ], timeout=90)
        if ok and os.path.exists(compressed_path) and os.path.getsize(compressed_path) < size:
            return compressed_path
        # If compression failed or didn't help, try harder
        ok, err = _run_ffmpeg([
            "-i", video_path,
            "-c:v", "libx264", "-preset", "fast", "-crf", "35",
            "-b:v", "500k", "-maxrate", "600k", "-bufsize", "1200k",
            "-vf", "scale='min(720,iw)':-4",
            "-c:a", "aac", "-b:a", "64k",
            "-movflags", "+faststart",
            compressed_path,
        ], timeout=90)
        if ok and os.path.exists(compressed_path) and os.path.getsize(compressed_path) < _TELEGRAM_MAX_VIDEO_SIZE:
            return compressed_path
        logger.warning(f"Video compression failed: {err}")
        return video_path  # best effort — let Telegram reject it if still too large
    except Exception as e:
        logger.warning(f"Size check failed: {e}")
        return video_path


def _format_timestamp(seconds: float) -> str:
    """Format seconds as HH:MM:SS."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


# ─── Video Clipping (Opus Clips style) ──────────────────────────────────────

async def clip_video(
    video_url: str,
    start_time: str = "00:00:00",
    duration: int = 30,
    add_captions: bool = True,
    aspect_ratio: str = "9:16",  # 9:16 for vertical (TikTok/Shorts), 16:9 for landscape
) -> dict:
    """
    Clip a segment from a video URL (YouTube, direct link, etc.).
    
    Args:
        video_url: URL of the video (YouTube, direct mp4, etc.)
        start_time: Start time in HH:MM:SS format
        duration: Clip duration in seconds (max 180)
        add_captions: If True, transcribe the clip and burn in captions
        aspect_ratio: "9:16" for vertical (TikTok/Reels/Shorts) or "16:9" for landscape
    
    Returns:
        {"success": bool, "file": base64, "filename": str, "error": str, ...}
    """
    import base64
    import io

    duration = min(max(duration, 5), 180)  # clamp 5-180 seconds
    
    tmp_dir = tempfile.mkdtemp(prefix="stew_clip_")
    try:
        # Step 1: Download the video
        raw_video = os.path.join(tmp_dir, "source.mp4")
        logger.info(f"Downloading video from {video_url}...")
        
        # If it's a direct URL to an mp4, use ffmpeg directly
        if video_url.endswith((".mp4", ".webm", ".mov", ".mkv")):
            ok, err = _run_ffmpeg([
                "-i", video_url,
                "-t", str(duration + 10),  # download a bit more than needed
                "-c", "copy",
                raw_video,
            ], timeout=60)
            if not ok:
                # Try with re-encoding
                ok, err = _run_ffmpeg([
                    "-i", video_url,
                    "-t", str(duration + 10),
                    "-c:v", "libx264", "-preset", "fast",
                    "-c:a", "aac",
                    raw_video,
                ], timeout=90)
        else:
            ok, err = _run_ytdlp(video_url, raw_video, timeout=90)
        
        if not ok:
            return {"success": False, "error": f"Download failed: {err}"}
        
        if not os.path.exists(raw_video) or os.path.getsize(raw_video) < 1000:
            return {"success": False, "error": "Downloaded file too small or missing"}
        
        # Step 2: Extract the clip
        clip_path = os.path.join(tmp_dir, "clip.mp4")
        
        # Parse start time
        start_seconds = 0
        for part in start_time.split(":"):
            start_seconds = start_seconds * 60 + int(float(part))
        
        # Crop based on aspect ratio
        if aspect_ratio == "9:16":
            # Vertical: crop center to 9:16
            vf = "crop=ih*9/16:ih,scale=720:1280"
        elif aspect_ratio == "1:1":
            # Square
            vf = "crop=ih:ih,scale=720:720"
        else:
            # Landscape 16:9
            vf = "scale=1280:720"
        
        ok, err = _run_ffmpeg([
            "-ss", str(start_seconds),
            "-i", raw_video,
            "-t", str(duration),
            "-vf", vf,
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            clip_path,
        ], timeout=90)
        
        if not ok:
            return {"success": False, "error": f"Clip extraction failed: {err}"}
        
        if not os.path.exists(clip_path) or os.path.getsize(clip_path) < 1000:
            return {"success": False, "error": "Clip file too small or missing"}
        
        # Step 3: Optionally add captions
        final_path = clip_path
        if add_captions:
            try:
                # Extract audio for transcription
                audio_path = os.path.join(tmp_dir, "clip_audio.ogg")
                ok, err = _run_ffmpeg([
                    "-i", clip_path,
                    "-vn", "-ac", "1", "-ar", "16000",
                    "-c:a", "libopus",
                    audio_path,
                ], timeout=30)
                
                if ok and os.path.exists(audio_path):
                    # Transcribe using Groq Whisper
                    import requests
                    groq_key = os.getenv("GROQ_API_KEY", "")
                    transcript = ""
                    if groq_key:
                        with open(audio_path, "rb") as af:
                            resp = requests.post(
                                "https://api.groq.com/openai/v1/audio/transcriptions",
                                headers={"Authorization": f"Bearer {groq_key}"},
                                files={"file": ("audio.ogg", af, "audio/ogg")},
                                data={
                                    "model": "whisper-large-v3-turbo",
                                    "response_format": "verbose_json",
                                    "timestamp_granularities[]": "segment",
                                },
                                timeout=60,
                            )
                        if resp.status_code == 200:
                            tdata = resp.json()
                            segments = tdata.get("segments", [])
                            transcript = " ".join(s.get("text", "") for s in segments)
                    
                    if transcript:
                        # Create SRT subtitles and burn them in
                        srt_path = os.path.join(tmp_dir, "captions.srt")
                        _create_srt_from_transcript(transcript, duration, srt_path)
                        
                        if os.path.exists(srt_path):
                            captioned_path = os.path.join(tmp_dir, "captioned.mp4")
                            # Style: white text, black outline, bottom center
                            ok, err = _run_ffmpeg([
                                "-i", clip_path,
                                "-vf", f"subtitles={srt_path}:force_style='FontSize=18,FontName=DejaVu Sans,PrimaryColour=&HFFFFFF&,OutlineColour=&H000000&,BorderStyle=3,Outline=2,Alignment=2,MarginV=40'",
                                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                                "-c:a", "aac", "-b:a", "128k",
                                "-movflags", "+faststart",
                                captioned_path,
                            ], timeout=90)
                            
                            if ok and os.path.exists(captioned_path):
                                final_path = captioned_path
                                logger.info("Captions burned in successfully")
            except Exception as e:
                logger.warning(f"Caption generation failed (non-fatal): {e}")
        
        # Step 4: Ensure the video fits Telegram's 50MB limit
        final_path = _ensure_telegram_safe_size(final_path, tmp_dir)

        # Step 5: Read and encode the final video
        with open(final_path, "rb") as f:
            video_bytes = f.read()
        
        b64 = base64.b64encode(video_bytes).decode()
        filename = f"stew_clip_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}.mp4"
        
        return {
            "success": True,
            "file": b64,
            "filename": filename,
            "mime_type": "video/mp4",
            "size_bytes": len(video_bytes),
            "duration": duration,
            "start_time": start_time,
            "aspect_ratio": aspect_ratio,
            "captions_added": final_path != clip_path,
        }
        
    except Exception as e:
        logger.error(f"Video clip error: {e}")
        return {"success": False, "error": str(e)}
    finally:
        # Cleanup
        import shutil
        try:
            shutil.rmtree(tmp_dir)
        except Exception:
            pass


def _create_srt_from_transcript(transcript: str, duration: float, srt_path: str):
    """Create a simple SRT file from transcript text, splitting into segments."""
    # Split transcript into ~3-second segments
    words = transcript.split()
    if not words:
        return
    
    words_per_segment = max(3, len(words) // max(1, int(duration / 3)))
    segments = []
    for i in range(0, len(words), words_per_segment):
        seg_text = " ".join(words[i:i + words_per_segment])
        seg_start = (i / len(words)) * duration
        seg_end = ((i + words_per_segment) / len(words)) * duration
        segments.append((seg_start, seg_end, seg_text))
    
    with open(srt_path, "w", encoding="utf-8") as f:
        for idx, (start, end, text) in enumerate(segments, 1):
            start_ts = _format_srt_timestamp(start)
            end_ts = _format_srt_timestamp(end)
            f.write(f"{idx}\n{start_ts} --> {end_ts}\n{text}\n\n")


def _format_srt_timestamp(seconds: float) -> str:
    """Format seconds as SRT timestamp: HH:MM:SS,mmm"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


# ─── Video Creation (images + voiceover → video) ────────────────────────────

# Resolution presets keyed by aspect ratio, used by create_video()
_ASPECT_SIZES = {
    "16:9": (1280, 720),
    "9:16": (720, 1280),
    "1:1": (960, 960),
}


def _create_scene_srt(narration: str, scene_duration: float, srt_path: str, words_per_chunk: int = 6):
    """Build an SRT file for one scene from its KNOWN narration text (no transcription needed —
    we already have the exact text we asked edge-tts to speak, so this is 100% accurate and free)."""
    words = narration.split()
    if not words:
        return False
    chunks = [" ".join(words[i:i + words_per_chunk]) for i in range(0, len(words), words_per_chunk)]
    n = len(chunks)

    def fmt(s):
        h = int(s // 3600); m = int((s % 3600) // 60); sec = int(s % 60); ms = int((s % 1) * 1000)
        return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"

    with open(srt_path, "w", encoding="utf-8") as f:
        for i, c in enumerate(chunks):
            start = (i / n) * scene_duration
            end = ((i + 1) / n) * scene_duration
            f.write(f"{i+1}\n{fmt(start)} --> {fmt(end)}\n{c}\n\n")
    return True


async def create_video(
    topic: str,
    scenes: list[dict],
    voice: str = "en-US-AriaNeural",
    duration_per_scene: int = 5,
    aspect_ratio: str = "16:9",  # "16:9" landscape, "9:16" vertical (Reels/Shorts/TikTok), "1:1" square
    add_captions: bool = True,
) -> dict:
    """
    Create a video from scenes with AI-generated images and voiceover.
    Uses free/open tools only: Pollinations (flux model) for images, edge-tts for voice,
    ffmpeg zoompan for Ken Burns motion (so static images feel like real video), and
    burned-in captions generated directly from the known narration text (accurate, no
    re-transcription needed).

    Args:
        topic: Overall topic/title for the video
        scenes: List of {"image_prompt": str, "narration": str} dicts
        voice: edge-tts voice name
        duration_per_scene: fallback seconds per scene when there's no narration audio
        aspect_ratio: "16:9", "9:16", or "1:1"
        add_captions: burn narration text as captions onto each scene

    Returns:
        {"success": bool, "file": base64, "filename": str, ...}
    """
    import base64
    import requests as req

    width, height = _ASPECT_SIZES.get(aspect_ratio, _ASPECT_SIZES["16:9"])
    # Request the source image ~1.3x larger so zoompan has room to zoom/pan without upscaling artifacts
    src_w, src_h = int(width * 1.3), int(height * 1.3)
    fps = 24

    tmp_dir = tempfile.mkdtemp(prefix="stew_video_")
    try:
        scene_files = []
        total_duration = 0

        for idx, scene in enumerate(scenes):
            image_prompt = scene.get("image_prompt", f"Image for {topic}, scene {idx+1}")
            narration = scene.get("narration", "")

            # Step 1: Generate image with Pollinations (free), explicit flux model for quality.
            # Retry with backoff since Pollinations frequently rate-limits (429) or times out
            # under shared cloud IPs (common on Render) — a single attempt isn't reliable enough.
            image_path = os.path.join(tmp_dir, f"scene_{idx}.jpg")
            image_url = (
                f"https://image.pollinations.ai/prompt/{req.utils.quote(image_prompt[:500])}"
                f"?width={src_w}&height={src_h}&nologo=true&seed={idx+1}&model=flux"
            )
            got_image = False
            for attempt in range(3):
                try:
                    if attempt > 0:
                        await asyncio.sleep(3 * attempt)  # 3s, 6s backoff between retries
                    resp = req.get(image_url, timeout=45)
                    if resp.status_code == 200 and len(resp.content) > 1000:
                        with open(image_path, "wb") as f:
                            f.write(resp.content)
                        got_image = True
                        break
                    else:
                        logger.warning(f"Pollinations returned {resp.status_code} for scene {idx} (attempt {attempt+1}/3)")
                except Exception as e:
                    logger.warning(f"Image generation failed for scene {idx} (attempt {attempt+1}/3): {e}")

            if not got_image:
                # Final fallback: plain color background so the scene can still be produced
                _run_ffmpeg([
                    "-f", "lavfi", "-i", f"color=c=0x1a1a2e:s={src_w}x{src_h}:d=1",
                    "-frames:v", "1", image_path,
                ], timeout=10)

            if not os.path.exists(image_path):
                continue

            # Step 2: Generate voiceover with edge-tts
            audio_path = os.path.join(tmp_dir, f"scene_{idx}.mp3")
            scene_duration = duration_per_scene

            if narration:
                try:
                    import edge_tts
                    communicate = edge_tts.Communicate(narration, voice)
                    audio_chunks = []
                    async for chunk in communicate.stream():
                        if chunk["type"] == "audio":
                            audio_chunks.append(chunk["data"])
                    if audio_chunks:
                        with open(audio_path, "wb") as f:
                            f.write(b"".join(audio_chunks))
                        dur = _get_video_duration(audio_path)
                        if dur > 0:
                            scene_duration = dur + 0.4  # small buffer so audio isn't cut off
                except Exception as e:
                    logger.warning(f"TTS failed for scene {idx}: {e}")

            total_duration += scene_duration
            has_audio = os.path.exists(audio_path)

            # Step 3: Build the scene video with a Ken Burns zoom/pan effect (feels like real video,
            # not a static slideshow). Falls back to a plain static-image clip if zoompan fails.
            segment_path = os.path.join(tmp_dir, f"segment_{idx}.mp4")
            total_frames = max(int(scene_duration * fps), fps)
            zoompan_vf = (
                f"scale={src_w}:{src_h},"
                f"zoompan=z='min(zoom+0.0015,1.2)':d={total_frames}:"
                f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={width}x{height}:fps={fps}"
            )

            base_args = ["-loop", "1", "-i", image_path]
            if has_audio:
                base_args += ["-i", audio_path]
            base_args += [
                "-vf", zoompan_vf,
                "-c:v", "libx264", "-preset", "fast", "-tune", "stillimage",
                "-pix_fmt", "yuv420p",
                "-t", str(scene_duration),
            ]
            if has_audio:
                base_args += ["-c:a", "aac", "-b:a", "128k", "-shortest"]
            base_args += ["-movflags", "+faststart", segment_path]

            ok, err = _run_ffmpeg(base_args, timeout=60)

            if not ok:
                # Fallback: plain static image (no zoom) if the zoompan filter fails for any reason
                logger.warning(f"Ken Burns zoompan failed for scene {idx}, falling back to static image: {err}")
                fallback_args = ["-loop", "1", "-i", image_path]
                if has_audio:
                    fallback_args += ["-i", audio_path]
                fallback_args += [
                    "-vf", f"scale={width}:{height}",
                    "-c:v", "libx264", "-preset", "fast", "-tune", "stillimage",
                    "-pix_fmt", "yuv420p",
                    "-t", str(scene_duration),
                ]
                if has_audio:
                    fallback_args += ["-c:a", "aac", "-b:a", "128k", "-shortest"]
                fallback_args += ["-movflags", "+faststart", segment_path]
                ok, err = _run_ffmpeg(fallback_args, timeout=60)

            if not (ok and os.path.exists(segment_path)):
                continue

            # Step 4: Optionally burn in captions from the KNOWN narration text (accurate, free —
            # no re-transcription needed since we already know exactly what was spoken)
            if add_captions and narration:
                try:
                    srt_path = os.path.join(tmp_dir, f"captions_{idx}.srt")
                    if _create_scene_srt(narration, scene_duration, srt_path):
                        captioned_path = os.path.join(tmp_dir, f"captioned_{idx}.mp4")
                        cap_ok, cap_err = _run_ffmpeg([
                            "-i", segment_path,
                            "-vf", f"subtitles={srt_path}:force_style='FontSize=16,FontName=DejaVu Sans,PrimaryColour=&HFFFFFF&,OutlineColour=&H000000&,BorderStyle=3,Outline=2,Alignment=2,MarginV=60'",
                            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                            "-c:a", "copy" if has_audio else "aac",
                            "-movflags", "+faststart",
                            captioned_path,
                        ], timeout=60)
                        if cap_ok and os.path.exists(captioned_path):
                            segment_path = captioned_path
                except Exception as e:
                    logger.warning(f"Caption burn failed for scene {idx} (non-fatal): {e}")

            scene_files.append(segment_path)

        if not scene_files:
            return {"success": False, "error": "No video segments could be created"}

        # Step 5: Concatenate all segments
        if len(scene_files) == 1:
            final_path = scene_files[0]
        else:
            concat_path = os.path.join(tmp_dir, "concat.txt")
            with open(concat_path, "w") as f:
                for sf in scene_files:
                    f.write(f"file '{sf}'\n")

            final_path = os.path.join(tmp_dir, "final.mp4")
            ok, err = _run_ffmpeg([
                "-f", "concat", "-safe", "0", "-i", concat_path,
                "-c", "copy",
                final_path,
            ], timeout=60)

            if not ok:
                # Fallback: re-encode (needed if segments have mismatched codec params)
                ok, err = _run_ffmpeg([
                    "-f", "concat", "-safe", "0", "-i", concat_path,
                    "-c:v", "libx264", "-preset", "fast",
                    "-c:a", "aac",
                    "-movflags", "+faststart",
                    final_path,
                ], timeout=90)

            if not ok:
                return {"success": False, "error": f"Concatenation failed: {err}"}

        # Step 6: Ensure the video fits Telegram's 50MB limit
        final_path = _ensure_telegram_safe_size(final_path, tmp_dir)

        # Step 7: Read and encode
        with open(final_path, "rb") as f:
            video_bytes = f.read()

        if len(video_bytes) < 1000:
            return {"success": False, "error": "Output video too small"}

        b64 = base64.b64encode(video_bytes).decode()
        clean_title = re.sub(r'[^a-zA-Z0-9_]', '_', topic)[:40]
        filename = f"{clean_title}_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}.mp4"

        return {
            "success": True,
            "file": b64,
            "filename": filename,
            "mime_type": "video/mp4",
            "size_bytes": len(video_bytes),
            "scenes": len(scene_files),
            "total_duration": round(total_duration, 1),
            "voice": voice,
            "aspect_ratio": aspect_ratio,
        }

    except Exception as e:
        logger.error(f"Video creation error: {e}")
        return {"success": False, "error": str(e)}
    finally:
        import shutil
        try:
            shutil.rmtree(tmp_dir)
        except Exception:
            pass


# ─── AI-Powered Smart Clips (Opus Clips style) ──────────────────────────────

async def smart_clips(
    video_url: str,
    num_clips: int = 3,
    clip_duration: int = 30,
    aspect_ratio: str = "9:16",
) -> dict:
    """
    AI-powered smart clipping: downloads a video, transcribes it,
    identifies the most interesting segments, and creates short clips
    with burned-in captions. Like Opus Clips.
    
    Args:
        video_url: URL of the source video
        num_clips: Number of clips to generate (1-5)
        clip_duration: Duration of each clip in seconds (10-60)
        aspect_ratio: "9:16" or "16:9"
    
    Returns:
        {"success": bool, "clips": list[dict], ...}
    """
    import base64
    import requests as req
    
    num_clips = min(max(num_clips, 1), 5)
    clip_duration = min(max(clip_duration, 10), 60)
    
    tmp_dir = tempfile.mkdtemp(prefix="stew_smart_")
    try:
        # Step 1: Download video
        raw_video = os.path.join(tmp_dir, "source.mp4")
        logger.info(f"Downloading {video_url} for smart clipping...")
        
        if video_url.endswith((".mp4", ".webm", ".mov")):
            ok, err = _run_ffmpeg(["-i", video_url, "-c", "copy", raw_video], timeout=60)
            if not ok:
                ok, err = _run_ffmpeg([
                    "-i", video_url,
                    "-c:v", "libx264", "-preset", "fast",
                    "-c:a", "aac",
                    raw_video,
                ], timeout=90)
        else:
            ok, err = _run_ytdlp(video_url, raw_video, timeout=120)
        
        if not ok:
            return {"success": False, "error": f"Download failed: {err}"}
        
        video_duration = _get_video_duration(raw_video)
        if video_duration < clip_duration:
            return {"success": False, "error": f"Video too short ({video_duration:.0f}s) for {clip_duration}s clips"}
        
        # Step 2: Extract full audio for transcription
        full_audio = os.path.join(tmp_dir, "full_audio.ogg")
        ok, err = _run_ffmpeg([
            "-i", raw_video,
            "-vn", "-ac", "1", "-ar", "16000",
            "-c:a", "libopus",
            full_audio,
        ], timeout=60)
        
        if not ok or not os.path.exists(full_audio):
            return {"success": False, "error": f"Audio extraction failed: {err}"}
        
        # Step 3: Transcribe with timestamps using Groq Whisper
        groq_key = os.getenv("GROQ_API_KEY", "")
        if not groq_key:
            return {"success": False, "error": "GROQ_API_KEY required for smart clips"}
        
        with open(full_audio, "rb") as af:
            resp = req.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {groq_key}"},
                files={"file": ("audio.ogg", af, "audio/ogg")},
                data={
                    "model": "whisper-large-v3-turbo",
                    "response_format": "verbose_json",
                    "timestamp_granularities[]": "segment",
                },
                timeout=120,
            )
        
        if resp.status_code != 200:
            return {"success": False, "error": f"Transcription failed: {resp.text[:200]}"}
        
        tdata = resp.json()
        segments = tdata.get("segments", [])
        full_text = tdata.get("text", "")
        
        if not segments:
            return {"success": False, "error": "No speech detected in video"}
        
        # Step 4: Use AI to identify the best segments
        # Build segment summaries for AI ranking
        seg_summaries = []
        for i, seg in enumerate(segments):
            seg_summaries.append({
                "index": i,
                "start": seg.get("start", 0),
                "end": seg.get("end", 0),
                "text": seg.get("text", "").strip(),
            })
        
        # Simple heuristic: find segments with the most content density
        # (longer text, more keywords, etc.) — no need for another LLM call
        # Score each potential clip window
        clip_candidates = []
        
        for seg in seg_summaries:
            # Find a window of clip_duration starting from this segment
            window_start = seg["start"]
            window_end = min(window_start + clip_duration, video_duration)
            if window_end - window_start < clip_duration * 0.8:
                continue  # skip if not enough content
            
            # Collect text in this window
            window_text = " ".join(
                s["text"] for s in seg_summaries
                if s["start"] >= window_start - 1 and s["end"] <= window_end + 1
            )
            
            # Score: longer text + presence of impactful words
            impactful_words = ["important", "key", "secret", "amazing", "incredible",
                              "never", "always", "best", "worst", "love", "hate",
                              "money", "success", "fail", "learn", "discover",
                              "truth", "mistake", "warning", "tip", "hack"]
            word_count = len(window_text.split())
            impactful_count = sum(1 for w in impactful_words if w in window_text.lower())
            score = word_count + impactful_count * 5
            
            # Deduplicate: skip if too close to an existing candidate
            too_close = any(
                abs(window_start - c["start"]) < clip_duration * 0.5
                for c in clip_candidates
            )
            if not too_close:
                clip_candidates.append({
                    "start": window_start,
                    "end": window_end,
                    "text": window_text[:200],
                    "score": score,
                })
        
        # Sort by score and take top N
        clip_candidates.sort(key=lambda x: x["score"], reverse=True)
        selected_clips = clip_candidates[:num_clips]
        selected_clips.sort(key=lambda x: x["start"])  # chronological order
        
        if not selected_clips:
            # Fallback: evenly spaced clips
            gap = video_duration / (num_clips + 1)
            for i in range(num_clips):
                start = gap * (i + 1)
                selected_clips.append({
                    "start": start,
                    "end": min(start + clip_duration, video_duration),
                    "text": "",
                    "score": 0,
                })
        
        # Step 5: Generate each clip
        results = []
        for i, clip_info in enumerate(selected_clips):
            clip_path = os.path.join(tmp_dir, f"clip_{i}.mp4")
            start_s = clip_info["start"]
            dur = clip_info["end"] - clip_info["start"]
            
            # Crop for aspect ratio
            if aspect_ratio == "9:16":
                vf = "crop=ih*9/16:ih,scale=720:1280"
            elif aspect_ratio == "1:1":
                vf = "crop=ih:ih,scale=720:720"
            else:
                vf = "scale=1280:720"
            
            ok, err = _run_ffmpeg([
                "-ss", str(start_s),
                "-i", raw_video,
                "-t", str(dur),
                "-vf", vf,
                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                "-c:a", "aac", "-b:a", "128k",
                "-movflags", "+faststart",
                clip_path,
            ], timeout=60)
            
            if not ok or not os.path.exists(clip_path):
                continue
            
            # Add captions from the transcription segments
            captioned_path = os.path.join(tmp_dir, f"captioned_{i}.mp4")
            srt_path = os.path.join(tmp_dir, f"clip_{i}.srt")
            
            # Create SRT from the segments in this clip's time range
            clip_segs = [
                s for s in seg_summaries
                if s["start"] >= start_s - 0.5 and s["end"] <= clip_info["end"] + 0.5
            ]
            
            if clip_segs:
                with open(srt_path, "w", encoding="utf-8") as f:
                    for idx, seg in enumerate(clip_segs, 1):
                        rel_start = seg["start"] - start_s
                        rel_end = seg["end"] - start_s
                        f.write(f"{idx}\n")
                        f.write(f"{_format_srt_timestamp(max(0, rel_start))} --> {_format_srt_timestamp(max(0, rel_end))}\n")
                        f.write(f"{seg['text'].strip()}\n\n")
                
                if os.path.exists(srt_path) and os.path.getsize(srt_path) > 10:
                    ok, err = _run_ffmpeg([
                        "-i", clip_path,
                        "-vf", f"subtitles={srt_path}:force_style='FontSize=18,FontName=DejaVu Sans,PrimaryColour=&HFFFFFF&,OutlineColour=&H000000&,BorderStyle=3,Outline=2,Alignment=2,MarginV=40'",
                        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                        "-c:a", "aac", "-b:a", "128k",
                        "-movflags", "+faststart",
                        captioned_path,
                    ], timeout=60)
                    
                    if ok and os.path.exists(captioned_path):
                        clip_path = captioned_path
            
            # Ensure the clip fits Telegram's 50MB limit
            clip_path = _ensure_telegram_safe_size(clip_path, tmp_dir)

            # Read and encode
            with open(clip_path, "rb") as f:
                video_bytes = f.read()
            
            b64 = base64.b64encode(video_bytes).decode()
            results.append({
                "file": b64,
                "filename": f"stew_smartclip_{i+1}_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}.mp4",
                "mime_type": "video/mp4",
                "size_bytes": len(video_bytes),
                "start_time": _format_timestamp(start_s),
                "duration": round(dur, 1),
                "preview_text": clip_info["text"][:100],
            })
        
        return {
            "success": True,
            "clips": results,
            "total_clips": len(results),
            "video_duration": round(video_duration, 1),
            "transcript": full_text[:500],
        }
        
    except Exception as e:
        logger.error(f"Smart clips error: {e}")
        return {"success": False, "error": str(e)}
    finally:
        import shutil
        try:
            shutil.rmtree(tmp_dir)
        except Exception:
            pass


# ─── AI Text-to-Video (Real video generation via Hugging Face Spaces) ─────────

async def generate_ai_video(
    prompt: str,
    duration: float = 2.0,
    height: int = 512,
    width: int = 704,
    negative_prompt: str = "worst quality, inconsistent motion, blurry, jittery, distorted",
    add_narration: bool = False,
    narration_text: str = "",
    voice: str = "en-US-AriaNeural",
) -> dict:
    """
    Generate a REAL AI video from a text prompt using LTX-Video on Hugging Face Spaces.
    
    This uses the free Gradio API to call the LTX-Video model hosted on HF Spaces.
    The video is generated on HF's GPU servers — no local GPU needed.
    
    Args:
        prompt: Text description of the video to generate
        duration: Video duration in seconds (1-5)
        height: Video height in pixels (256-1280)
        width: Video width in pixels (256-1280)
        negative_prompt: What to avoid in the video
        add_narration: If True, add TTS voiceover to the video
        narration_text: Text to speak for narration (uses prompt if empty)
        voice: edge-tts voice name for narration
    
    Returns:
        {"success": bool, "file": base64, "filename": str, ...}
    """
    import base64
    from gradio_client import Client as GradioClient
    
    duration = min(max(duration, 1.0), 5.0)
    height = min(max(height, 256), 768)
    width = min(max(width, 256), 1280)
    
    tmp_dir = tempfile.mkdtemp(prefix="stew_aivideo_")
    try:
        # Step 1: Generate AI video using LTX-Video on Hugging Face Spaces
        logger.info(f"Generating AI video: {prompt[:100]}...")
        
        def _generate():
            client = GradioClient("Lightricks/ltx-video-distilled", verbose=False)
            result = client.predict(
                prompt=prompt,
                negative_prompt=negative_prompt,
                input_image_filepath=None,
                input_video_filepath=None,
                height_ui=height,
                width_ui=width,
                mode="text-to-video",
                duration_ui=duration,
                ui_frames_to_use=9,
                seed_ui=42,
                randomize_seed=True,
                ui_guidance_scale=1,
                improve_texture_flag=True,
                api_name="/text_to_video",
            )
            return result
        
        result = await asyncio.to_thread(_generate)
        
        if not result or not isinstance(result, tuple):
            return {"success": False, "error": "Video generation returned no result"}
        
        video_data = result[0]
        if isinstance(video_data, dict) and "video" in video_data:
            video_path = video_data["video"]
        else:
            return {"success": False, "error": "Unexpected result format from LTX-Video"}
        
        if not os.path.exists(video_path) or os.path.getsize(video_path) < 1000:
            return {"success": False, "error": "Generated video file is missing or too small"}
        
        # Step 2: Copy to our temp dir for processing
        raw_video = os.path.join(tmp_dir, "ai_raw.mp4")
        import shutil
        shutil.copy(video_path, raw_video)
        
        video_duration = _get_video_duration(raw_video)
        logger.info(f"AI video generated: {os.path.getsize(raw_video)/1024:.0f}KB, {video_duration:.1f}s")
        
        # Step 3: Optionally add narration (TTS voiceover)
        final_path = raw_video
        if add_narration and narration_text:
            try:
                import edge_tts
                audio_path = os.path.join(tmp_dir, "narration.mp3")
                communicate = edge_tts.Communicate(narration_text or prompt, voice)
                audio_chunks = []
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        audio_chunks.append(chunk["data"])
                if audio_chunks:
                    with open(audio_path, "wb") as f:
                        f.write(b"".join(audio_chunks))
                    
                    # Merge video + audio
                    narrated_path = os.path.join(tmp_dir, "narrated.mp4")
                    ok, err = _run_ffmpeg([
                        "-i", raw_video,
                        "-i", audio_path,
                        "-c:v", "copy",
                        "-c:a", "aac", "-b:a", "128k",
                        "-shortest",
                        "-movflags", "+faststart",
                        narrated_path,
                    ], timeout=30)
                    if ok and os.path.exists(narrated_path):
                        final_path = narrated_path
            except Exception as e:
                logger.warning(f"Narration failed (non-fatal): {e}")
        
        # Step 4: Ensure it fits Telegram's size limit
        final_path = _ensure_telegram_safe_size(final_path, tmp_dir)
        
        # Step 5: Read and encode
        with open(final_path, "rb") as f:
            video_bytes = f.read()
        
        if len(video_bytes) < 500:
            return {"success": False, "error": "Output video too small"}
        
        b64 = base64.b64encode(video_bytes).decode()
        filename = f"stew_aivideo_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}.mp4"
        
        return {
            "success": True,
            "file": b64,
            "filename": filename,
            "mime_type": "video/mp4",
            "size_bytes": len(video_bytes),
            "duration": round(video_duration, 1),
            "prompt": prompt[:200],
            "model": "LTX-Video",
            "narration_added": add_narration and narration_text,
        }
        
    except Exception as e:
        logger.error(f"AI video generation error: {e}")
        return {"success": False, "error": str(e)[:300]}
    finally:
        import shutil
        try:
            shutil.rmtree(tmp_dir)
        except Exception:
            pass


async def generate_ai_video_with_narration(
    topic: str,
    scenes: list[dict],
    voice: str = "en-US-AriaNeural",
    clip_duration: float = 2.0,
) -> dict:
    """
    Generate a multi-scene AI video with narration.
    Each scene generates a real AI video clip + TTS narration, then stitches them together.
    
    Args:
        topic: Overall topic/title
        scenes: List of {"video_prompt": str, "narration": str} dicts
        voice: edge-tts voice name
        clip_duration: Duration of each AI video clip in seconds (1-5)
    
    Returns:
        {"success": bool, "file": base64, ...}
    """
    import base64
    from gradio_client import Client as GradioClient
    
    clip_duration = min(max(clip_duration, 1.0), 5.0)
    tmp_dir = tempfile.mkdtemp(prefix="stew_aivideo_multi_")
    
    try:
        scene_files = []
        total_duration = 0
        
        for idx, scene in enumerate(scenes):
            video_prompt = scene.get("video_prompt", scene.get("image_prompt", f"Scene {idx+1} for {topic}"))
            narration = scene.get("narration", "")
            
            logger.info(f"Generating AI video scene {idx+1}/{len(scenes)}: {video_prompt[:80]}...")
            
            # Generate AI video clip — multi-provider with fallback
            try:
                # Try LTX-Video first
                clip_path = None
                try:
                    ltx_r = await _generate_ltx_video(
                        prompt=video_prompt,
                        duration=clip_duration,
                        height=512,
                        width=704,
                        negative_prompt="worst quality, inconsistent motion, blurry, jittery, distorted",
                        seed=42 + idx,
                    )
                    if ltx_r.get("success"):
                        clip_path = ltx_r["video_path"]
                        logger.info(f"Scene {idx}: LTX-Video succeeded")
                except Exception as ltx_err:
                    logger.warning(f"Scene {idx}: LTX-Video failed: {str(ltx_err)[:100]}")

                # Fallback: Wan2.1
                if not clip_path:
                    try:
                        wan_r = await _generate_wan21_video(video_prompt, seed=42 + idx)
                        if wan_r.get("success"):
                            clip_path = wan_r["video_path"]
                            logger.info(f"Scene {idx}: Wan2.1 succeeded")
                    except Exception as wan_err:
                        logger.warning(f"Scene {idx}: Wan2.1 failed: {str(wan_err)[:100]}")

                if not clip_path:
                    logger.warning(f"Scene {idx}: all GPU providers failed, skipping")
                    continue

                raw_clip = os.path.join(tmp_dir, f"clip_{idx}.mp4")
                import shutil
                shutil.copy(clip_path, raw_clip)
                
                # Add narration audio if provided
                if narration:
                    try:
                        import edge_tts
                        audio_path = os.path.join(tmp_dir, f"audio_{idx}.mp3")
                        communicate = edge_tts.Communicate(narration, voice)
                        audio_chunks = []
                        async for chunk in communicate.stream():
                            if chunk["type"] == "audio":
                                audio_chunks.append(chunk["data"])
                        if audio_chunks:
                            with open(audio_path, "wb") as f:
                                f.write(b"".join(audio_chunks))
                            
                            narrated_path = os.path.join(tmp_dir, f"narrated_{idx}.mp4")
                            ok, err = _run_ffmpeg([
                                "-i", raw_clip,
                                "-i", audio_path,
                                "-c:v", "copy",
                                "-c:a", "aac", "-b:a", "128k",
                                "-shortest",
                                "-movflags", "+faststart",
                                narrated_path,
                            ], timeout=30)
                            if ok and os.path.exists(narrated_path):
                                raw_clip = narrated_path
                    except Exception as e:
                        logger.warning(f"Scene {idx} narration failed: {e}")
                
                clip_dur = _get_video_duration(raw_clip)
                total_duration += clip_dur
                scene_files.append(raw_clip)
                
            except Exception as e:
                logger.warning(f"Scene {idx} generation failed: {e}")
                continue
        
        if not scene_files:
            return {"success": False, "error": "No AI video clips could be generated"}
        
        # Concatenate all clips
        if len(scene_files) == 1:
            final_path = scene_files[0]
        else:
            concat_path = os.path.join(tmp_dir, "concat.txt")
            with open(concat_path, "w") as f:
                for sf in scene_files:
                    f.write(f"file '{sf}'\n")
            
            final_path = os.path.join(tmp_dir, "final.mp4")
            ok, err = _run_ffmpeg([
                "-f", "concat", "-safe", "0", "-i", concat_path,
                "-c:v", "libx264", "-preset", "fast",
                "-c:a", "aac",
                "-movflags", "+faststart",
                final_path,
            ], timeout=60)
            if not ok:
                return {"success": False, "error": f"Concatenation failed: {err}"}
        
        # Ensure Telegram-safe size
        final_path = _ensure_telegram_safe_size(final_path, tmp_dir)
        
        with open(final_path, "rb") as f:
            video_bytes = f.read()
        
        if len(video_bytes) < 1000:
            return {"success": False, "error": "Output video too small"}
        
        b64 = base64.b64encode(video_bytes).decode()
        clean_title = re.sub(r'[^a-zA-Z0-9_]', '_', topic)[:40]
        filename = f"{clean_title}_aivideo_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}.mp4"
        
        return {
            "success": True,
            "file": b64,
            "filename": filename,
            "mime_type": "video/mp4",
            "size_bytes": len(video_bytes),
            "scenes": len(scene_files),
            "total_duration": round(total_duration, 1),
            "voice": voice,
            "model": "LTX-Video",
        }
        
    except Exception as e:
        logger.error(f"Multi-scene AI video error: {e}")
        return {"success": False, "error": str(e)[:300]}
    finally:
        import shutil
        try:
            shutil.rmtree(tmp_dir)
        except Exception:
            pass


# ─── Multi-Provider AI Video (Fallback Chain) ─────────────────────────────
# Providers tried in order: LTX-Video → Wan2.1 → Ken Burns (guaranteed).
# Each provider has different GPU quotas — rotating between them means even
# if one quota is exhausted, the other may still work.

async def _generate_ltx_video(
    prompt: str, duration: float, height: int, width: int,
    negative_prompt: str, seed: int = 42,
) -> dict:
    """Provider 1: LTX-Video on HuggingFace Spaces (free ZeroGPU)."""
    from gradio_client import Client as GradioClient
    import shutil

    def _gen():
        client = GradioClient("Lightricks/ltx-video-distilled", verbose=False)
        result = client.predict(
            prompt=prompt,
            negative_prompt=negative_prompt,
            input_image_filepath=None,
            input_video_filepath=None,
            height_ui=height,
            width_ui=width,
            mode="text-to-video",
            duration_ui=min(duration, 5.0),
            ui_frames_to_use=9,
            seed_ui=seed,
            randomize_seed=True,
            ui_guidance_scale=1,
            improve_texture_flag=True,
            api_name="/text_to_video",
        )
        return result

    result = await asyncio.to_thread(_gen)
    if not result or not isinstance(result, tuple):
        return {"success": False, "error": "LTX returned no result"}

    video_data = result[0]
    if isinstance(video_data, dict) and "video" in video_data:
        video_path = video_data["video"]
    elif isinstance(video_data, (str,)) and os.path.exists(video_data):
        video_path = video_data
    else:
        return {"success": False, "error": "Unexpected LTX result format"}

    if not os.path.exists(video_path) or os.path.getsize(video_path) < 1000:
        return {"success": False, "error": "LTX video file missing/too small"}

    return {"success": True, "video_path": video_path, "provider": "LTX-Video"}


async def _generate_wan21_video(
    prompt: str, seed: int = 42,
) -> dict:
    """Provider 2: Wan2.1 on HuggingFace Spaces (free ZeroGPU, async polling)."""
    from gradio_client import Client as GradioClient
    import shutil

    def _gen():
        client = GradioClient("Wan-AI/Wan2.1", verbose=False)
        # Wan2.1 uses async generation — submit, then poll for status
        result = client.predict(
            prompt=prompt,
            size="832*1088",  # vertical (good for Reels/Shorts)
            watermark_wan=False,
            seed=seed,
            api_name="/t2v_generation_async",
        )
        return result

    result = await asyncio.to_thread(_gen)
    if not result:
        return {"success": False, "error": "Wan2.1 returned no result"}

    # Wan2.1 async returns (cost_time, estimated_wait) — need to poll for the video
    # The actual video file appears after generation completes
    est_wait = 0
    if isinstance(result, tuple) and len(result) >= 2:
        est_wait = float(result[1]) if result[1] else 60

    # Poll the status endpoint
    def _poll():
        client = GradioClient("Wan-AI/Wan2.1", verbose=False)
        for _ in range(30):  # max ~150 seconds
            status = client.predict(api_name="/status_refresh")
            # Status returns gallery/video data when ready
            if isinstance(status, tuple) and len(status) >= 1:
                gallery = status[0]
                if gallery and isinstance(gallery, list) and len(gallery) > 0:
                    item = gallery[0]
                    if isinstance(item, (list, tuple)) and len(item) >= 1:
                        video_info = item[0]
                        if isinstance(video_info, dict) and "video" in video_info:
                            return video_info["video"]
                        elif isinstance(video_info, str) and os.path.exists(video_info):
                            return video_info
            import time as _t
            _t.sleep(5)
        return None

    max_wait = min(est_wait + 30, 150)
    video_path = await asyncio.to_thread(_poll)

    if not video_path or not os.path.exists(str(video_path)):
        return {"success": False, "error": "Wan2.1 polling timed out"}

    if os.path.getsize(str(video_path)) < 1000:
        return {"success": False, "error": "Wan2.1 video file too small"}

    return {"success": True, "video_path": str(video_path), "provider": "Wan2.1"}


async def generate_ai_video_multi_provider(
    prompt: str,
    duration: float = 2.0,
    height: int = 512,
    width: int = 704,
    negative_prompt: str = "worst quality, inconsistent motion, blurry, jittery, distorted",
    add_narration: bool = False,
    narration_text: str = "",
    voice: str = "en-US-AriaNeural",
    aspect_ratio: str = "9:16",
) -> dict:
    """
    Multi-provider AI video generation with automatic fallback.
    
    Tries: LTX-Video → Wan2.1 → Ken Burns (AI images + motion + TTS).
    Guarantees a video is returned unless all providers fail.
    """
    import base64

    duration = min(max(duration, 1.0), 5.0)
    tmp_dir = tempfile.mkdtemp(prefix="stew_aivideo_mp_")

    try:
        video_path = None
        provider_used = None

        # Provider 1: LTX-Video
        try:
            logger.info(f"[multi-provider] Trying LTX-Video...")
            ltx_result = await _generate_ltx_video(
                prompt, duration, height, width, negative_prompt, seed=42
            )
            if ltx_result.get("success"):
                video_path = ltx_result["video_path"]
                provider_used = "LTX-Video"
                logger.info(f"[multi-provider] LTX-Video succeeded")
        except Exception as e:
            logger.warning(f"[multi-provider] LTX-Video failed: {str(e)[:150]}")

        # Provider 2: Wan2.1 (only if LTX failed or quota exceeded)
        if not video_path:
            try:
                logger.info(f"[multi-provider] Trying Wan2.1...")
                wan_result = await _generate_wan21_video(prompt, seed=42)
                if wan_result.get("success"):
                    video_path = wan_result["video_path"]
                    provider_used = "Wan2.1"
                    logger.info(f"[multi-provider] Wan2.1 succeeded")
            except Exception as e:
                logger.warning(f"[multi-provider] Wan2.1 failed: {str(e)[:150]}")

        # Provider 3: Ken Burns fallback (guaranteed — uses AI images + motion)
        if not video_path:
            logger.info(f"[multi-provider] All GPU providers failed — using Ken Burns fallback")
            from video_tools import create_video
            scenes = [{"image_prompt": prompt, "narration": narration_text if add_narration else ""}]
            kb_result = await create_video(
                prompt, scenes, voice, duration_per_scene=int(duration),
                aspect_ratio=aspect_ratio, add_captions=False,
            )
            if kb_result.get("success") and kb_result.get("file"):
                # Ken Burns returns base64 — decode and write to file for uniform processing
                kb_bytes = base64.b64decode(kb_result["file"])
                video_path = os.path.join(tmp_dir, "kenburns.mp4")
                with open(video_path, "wb") as f:
                    f.write(kb_bytes)
                provider_used = "Ken Burns (fallback)"
            else:
                return {"success": False, "error": "All video providers failed"}

        # Copy to our temp dir
        raw_video = os.path.join(tmp_dir, "ai_raw.mp4")
        import shutil
        shutil.copy(video_path, raw_video)

        video_duration = _get_video_duration(raw_video)
        logger.info(f"[multi-provider] Video ready: {os.path.getsize(raw_video)/1024:.0f}KB, {video_duration:.1f}s, via {provider_used}")

        # Add narration if requested
        final_path = raw_video
        if add_narration and narration_text:
            try:
                import edge_tts
                audio_path = os.path.join(tmp_dir, "narration.mp3")
                communicate = edge_tts.Communicate(narration_text or prompt, voice)
                audio_chunks = []
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        audio_chunks.append(chunk["data"])
                if audio_chunks:
                    with open(audio_path, "wb") as f:
                        f.write(b"".join(audio_chunks))

                    narrated_path = os.path.join(tmp_dir, "narrated.mp4")
                    ok, err = _run_ffmpeg([
                        "-i", raw_video, "-i", audio_path,
                        "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
                        "-shortest", "-movflags", "+faststart",
                        narrated_path,
                    ], timeout=30)
                    if ok and os.path.exists(narrated_path):
                        final_path = narrated_path
            except Exception as e:
                logger.warning(f"[multi-provider] Narration failed (non-fatal): {e}")

        # Ensure Telegram-safe size
        final_path = _ensure_telegram_safe_size(final_path, tmp_dir)

        with open(final_path, "rb") as f:
            video_bytes = f.read()

        if len(video_bytes) < 500:
            return {"success": False, "error": "Output video too small"}

        b64 = base64.b64encode(video_bytes).decode()
        filename = f"stew_aivideo_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}.mp4"

        return {
            "success": True,
            "file": b64,
            "filename": filename,
            "mime_type": "video/mp4",
            "size_bytes": len(video_bytes),
            "duration": round(video_duration, 1),
            "prompt": prompt[:200],
            "model": provider_used,
            "narration_added": add_narration and narration_text,
            "provider": provider_used,
        }

    except Exception as e:
        logger.error(f"[multi-provider] AI video error: {e}")
        return {"success": False, "error": str(e)[:300]}
    finally:
        import shutil
        try:
            shutil.rmtree(tmp_dir)
        except Exception:
            pass
