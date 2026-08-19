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


def _run_ytdlp(url: str, output_path: str, timeout: int = 120) -> tuple[bool, str]:
    """Download a video using yt-dlp. Returns (success, error_msg).
    Updates yt-dlp first to handle YouTube's frequent signature changes."""
    try:
        # Update yt-dlp to the latest version (handles YouTube breaking changes)
        subprocess.run(
            ["pip", "install", "--upgrade", "-q", "yt-dlp"],
            capture_output=True, timeout=30,
        )
    except Exception:
        pass  # non-fatal — try with whatever version we have
    try:
        cmd = [
            "yt-dlp",
            "-f", "best[ext=mp4][filesize<50M]/best[filesize<50M]/best",
            "--max-filesize", "50M",
            "--no-playlist",
            "--no-warnings",
            "-o", output_path,
            url,
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=timeout)
        if result.returncode == 0:
            return True, ""
        err = result.stderr.decode("utf-8", errors="replace")[:500]
        # If format selection failed, try with a simpler format string
        if "format" in err.lower() or "requested format" in err.lower():
            cmd2 = [
                "yt-dlp",
                "-f", "mp4/best",
                "--max-filesize", "50M",
                "--no-playlist",
                "--no-warnings",
                "-o", output_path,
                url,
            ]
            result2 = subprocess.run(cmd2, capture_output=True, timeout=timeout)
            if result2.returncode == 0:
                return True, ""
            err = result2.stderr.decode("utf-8", errors="replace")[:500]
        return False, err
    except subprocess.TimeoutExpired:
        return False, "Download timed out"
    except FileNotFoundError:
        return False, "yt-dlp not installed"
    except Exception as e:
        return False, str(e)


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

            # Step 1: Generate image with Pollinations (free), explicit flux model for quality
            image_path = os.path.join(tmp_dir, f"scene_{idx}.jpg")
            try:
                image_url = (
                    f"https://image.pollinations.ai/prompt/{req.utils.quote(image_prompt[:500])}"
                    f"?width={src_w}&height={src_h}&nologo=true&seed={idx+1}&model=flux"
                )
                resp = req.get(image_url, timeout=40)
                if resp.status_code == 200 and len(resp.content) > 1000:
                    with open(image_path, "wb") as f:
                        f.write(resp.content)
                else:
                    raise RuntimeError(f"Pollinations returned {resp.status_code}")
            except Exception as e:
                logger.warning(f"Image generation failed for scene {idx}: {e}")
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
