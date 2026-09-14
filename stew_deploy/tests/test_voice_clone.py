"""Tests for the S.T.E.W Voice Cloner (server/voice_clone.py).
Pure-logic tests: no network, no gradio, no Supabase."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server import voice_clone as vc

KEY = "tg:999999"


def test_voiceover_intent_patterns():
    assert vc.is_voiceover_intent("voiceover: Welcome to my channel") == "Welcome to my channel"
    assert vc.is_voiceover_intent("clone my voice and say: buy my course now") == "buy my course now"
    assert vc.is_voiceover_intent("make a voiceover of this: hello world") is not None
    assert vc.is_voiceover_intent("hello there") is None
    assert vc.is_voiceover_intent("") is None
    # must not hijack ordinary chat
    assert vc.is_voiceover_intent("what is a voiceover?") is None


def test_session_state():
    vc.start_session(KEY)
    assert vc.is_awaiting_sample(KEY) is True
    vc.end_session(KEY)
    assert vc.is_awaiting_sample(KEY) is False


def test_profile_roundtrip():
    asyncio.run(vc.save_profile(KEY, b"RIFFtestbytes", "hello this is a test transcript"))
    p = asyncio.run(vc.get_profile(KEY))
    assert p is not None
    assert p["wav_bytes"] == b"RIFFtestbytes"
    assert "test transcript" in p["ref_text"]
    assert asyncio.run(vc.has_profile(KEY)) is True
    vc.end_session(KEY)


def test_oversize_sample_rejected():
    ok = asyncio.run(vc.save_profile("tg:huge", b"x" * (vc.MAX_PROFILE_BYTES + 1), "t"))
    assert ok is False


if __name__ == "__main__":
    test_voiceover_intent_patterns()
    test_session_state()
    test_profile_roundtrip()
    test_oversize_sample_rejected()
    print("ALL VOICE CLONE TESTS PASS")
