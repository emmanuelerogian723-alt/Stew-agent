"""Tests for the S.T.E.W professional video editor (server/video_editor.py)."""
import asyncio
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from server.video_editor import (  # noqa: E402
    parse_edit_request, is_edit_intent, is_read_intent, edit_video_file,
    store_pending, get_pending, pop_pending, PRESETS,
)

TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


def make_source(tmp, w=1280, h=720, dur=10.0):
    """Synthetic landscape test video with tone audio."""
    p = os.path.join(tmp, "src.mp4")
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", f"testsrc2=size={w}x{h}:rate=25:duration={dur}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={dur}",
         "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-pix_fmt", "yuv420p", p],
        capture_output=True, timeout=120, check=True)
    return p


def dims(path):
    out = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", path],
        capture_output=True, timeout=30).stdout.decode()
    import json
    for s in json.loads(out)["streams"]:
        if s.get("codec_type") == "video":
            return s["width"], s["height"]
    return 0, 0


# ── intent parsing ──────────────────────────────────────────────────────────
@test
def parsing():
    cases = {
        "make this a reel": [({"op": "preset", "name": "reel"})],
        "convert to tiktok format": [({"op": "preset", "name": "tiktok"})],
        "turn it into a youtube video": [({"op": "preset", "name": "youtube"})],
        "cut 0:05 to 0:30": [({"op": "trim", "start": 5.0, "end": 30.0})],
        "first 30 seconds": [({"op": "trim", "start": 0.0, "end": 30.0})],
        "trim from 15 for 20 seconds": [({"op": "trim", "start": 15.0, "end": 35.0})],
        "speed 2x": [({"op": "speed", "factor": 2.0})],
        "slow motion": [({"op": "speed", "factor": 0.5})],
        "reverse it": [({"op": "reverse"})],
        "black and white": [({"op": "filter", "name": "bw"})],
        "cinematic look": [({"op": "filter", "name": "cinematic"})],
        "add captions": [({"op": "captions"})],
        "mute it": [({"op": "mute"})],
        "extract audio": [({"op": "extract_audio"})],
        "make it a gif": [({"op": "gif"})],
        "compress for whatsapp": [({"op": "compress"})],
        "rotate left": [({"op": "rotate", "dir": "ccw"})],
        "flip horizontal": [({"op": "flip", "axis": "h"})],
        "volume 50": [({"op": "volume", "pct": 50})],
        "put text 'NEW DROP' on top": [({"op": "text", "content": "NEW DROP", "pos": "top"})],
    }
    for text, expected_ops in cases.items():
        got = parse_edit_request(text)
        assert got, f"no ops parsed for: {text}"
        for exp in expected_ops:
            matches = [g for g in got if all(g.get(k) == v for k, v in exp.items())]
            assert matches, f"'{text}': expected {exp} in {got}"
    assert not is_edit_intent("what is the weather today")
    assert not is_edit_intent("hello how are you")
    assert is_read_intent("summarize this video")
    assert is_read_intent("read it")
    assert not is_read_intent("make it a reel")


# ── renders ──────────────────────────────────────────────────────────────────
@test
def render_reel_blurpad():
    tmp = tempfile.mkdtemp(prefix="tst_ve_")
    try:
        src = make_source(tmp)
        r = asyncio.run(edit_video_file(src, "make this a reel, cut 0:02 to 0:06", tmp, target_mb=50))
        assert r["ok"], r
        assert r["kind"] == "video"
        w, h = dims(r["output"])
        assert (w, h) == (1080, 1920), f"expected 1080x1920 reel, got {w}x{h}"
        assert abs(r["duration"] - 4.0) < 0.6, f"trim duration wrong: {r['duration']}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@test
def render_youtube_from_vertical():
    tmp = tempfile.mkdtemp(prefix="tst_ve_")
    try:
        src = make_source(tmp, w=720, h=1280)  # vertical source
        r = asyncio.run(edit_video_file(src, "convert to youtube format", tmp, target_mb=50))
        assert r["ok"], r
        w, h = dims(r["output"])
        assert (w, h) == (1920, 1080), f"expected 1920x1080, got {w}x{h}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@test
def render_captions():
    tmp = tempfile.mkdtemp(prefix="tst_ve_")
    try:
        src = make_source(tmp, dur=8)
        fake_transcriber = lambda b: {"text": "hello world this is stew editing your video like a pro right now"}
        r = asyncio.run(edit_video_file(src, "add captions", tmp, transcriber=fake_transcriber, target_mb=50))
        assert r["ok"], r
        assert any("captions" in a.lower() for a in r["applied"]), r["applied"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@test
def render_gif_thumb_audio():
    tmp = tempfile.mkdtemp(prefix="tst_ve_")
    try:
        src = make_source(tmp, dur=6)
        r = asyncio.run(edit_video_file(src, "make it a gif", tmp))
        assert r["ok"] and r["kind"] == "gif" and r["output"].endswith(".gif"), r
        r2 = asyncio.run(edit_video_file(src, "thumbnail", tmp))
        assert r2["ok"] and r2["kind"] == "image" and r2["output"].endswith(".jpg"), r2
        r3 = asyncio.run(edit_video_file(src, "extract audio", tmp))
        assert r3["ok"] and r3["kind"] == "audio" and r3["output"].endswith(".mp3"), r3
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@test
def render_speed_and_look():
    tmp = tempfile.mkdtemp(prefix="tst_ve_")
    try:
        src = make_source(tmp, dur=10)
        r = asyncio.run(edit_video_file(src, "2x speed, black and white", tmp, target_mb=50))
        assert r["ok"], r
        assert abs(r["duration"] - 5.0) < 0.8, f"2x speed duration wrong: {r['duration']}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@test
def render_compress_small():
    tmp = tempfile.mkdtemp(prefix="tst_ve_")
    try:
        src = make_source(tmp, dur=20)
        r = asyncio.run(edit_video_file(src, "compress", tmp, target_mb=0.8))
        assert r["ok"], r
        assert os.path.getsize(r["output"]) < 0.8 * 1024 * 1024, \
            f"compress failed: {os.path.getsize(r['output'])/1048576:.2f}MB"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@test
def pending_registry():
    tmp = tempfile.mkdtemp(prefix="tst_ve_")
    try:
        p = os.path.join(tmp, "v.mp4")
        open(p, "wb").write(b"x")
        store_pending("chat1", p)
        assert get_pending("chat1")["path"] == p
        pop_pending("chat1")
        assert get_pending("chat1") is None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def make_speechy_source(tmp, w=1280, h=720, dur=14.0):
    """Video with 3 speech bursts separated by REAL silence (for Loom-cut test)."""
    p = os.path.join(tmp, "speechy.mp4")
    # 0-3s tone, 3-7.5s silence, 7.5-10.5s tone, 10.5-12s silence, 12-14s tone
    expr = (
        "aevalsrc=0.22*sin(440*2*PI*t)*"
        "lt(t\,3)+0.22*sin(440*2*PI*t)*gte(t\,7.5)*lt(t\,10.5)+0.22*sin(440*2*PI*t)*gte(t\,12):"
        f"s=44100:d={dur}"
    )
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", f"testsrc2=size={w}x{h}:rate=25:duration={dur}",
         "-f", "lavfi", "-i", expr,
         "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-pix_fmt", "yuv420p", p],
        capture_output=True, timeout=120, check=True)
    return p


@test
def loom_silence_removal():
    tmp = tempfile.mkdtemp(prefix="tst_ve_")
    try:
        src = make_speechy_source(tmp)
        r = asyncio.run(edit_video_file(src, "remove the silence", tmp, target_mb=50))
        assert r["ok"], r
        assert any("silence" in a.lower() for a in r["applied"]), r["applied"]
        # speech = 3 + 3 + 2 = 8s + merge margins; original 14s -> must shrink by >3.5s
        assert r["duration"] < 10.5, f"silence not cut: {r['duration']}s (orig 14s)"
        assert r["duration"] > 6.0, f"over-cut (kept only {r['duration']}s)"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@test
def higgsfield_camera_motion():
    tmp = tempfile.mkdtemp(prefix="tst_ve_")
    try:
        src = make_source(tmp, dur=8)
        for motion in ["zoom in", "ken burns", "pan right"]:
            r = asyncio.run(edit_video_file(src, motion, tmp, target_mb=50))
            assert r["ok"], f"{motion}: {r}"
            assert any("camera" in a.lower() for a in r["applied"]), f"{motion}: {r['applied']}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@test
def motion_plus_reel():
    tmp = tempfile.mkdtemp(prefix="tst_ve_")
    try:
        src = make_source(tmp, dur=8)
        r = asyncio.run(edit_video_file(src, "make it a reel with slow zoom in, remove silence", tmp, target_mb=50))
        assert r["ok"], r
        w, h = dims(r["output"])
        assert (w, h) == (1080, 1920), f"{w}x{h}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    passed = failed = 0
    for t in TESTS:
        try:
            t()
            print(f"  ✓ {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  ✗ {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ✗ {t.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    return failed == 0


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
