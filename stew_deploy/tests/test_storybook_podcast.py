"""Tests for storybook, podcast, doccompare and reminder modules (logic only)."""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def test_storybook_intent():
    from server.storybook import is_storybook_intent
    assert "a brave girl in Lagos" in is_storybook_intent("write me a storybook about a brave girl in Lagos")
    assert is_storybook_intent("/storybook the lion who coded") == "the lion who coded"
    assert is_storybook_intent("write me an anime storybook about space") is not None
    assert is_storybook_intent("hello") is None

def test_storybook_pdf_and_generation():
    """Full pipeline with a stub LLM and live image fetch skipped via monkeypatch."""
    from server import storybook as sb

    def stub_llm(messages, max_tokens=1000):
        if "outline" in messages[1]["content"] or "outline" in str(messages):
            return {"content": json.dumps({"title": "Ada and the Solar Kite",
                    "chapters": [{"title": "The Spark", "summary": "Ada finds a broken kite."},
                                 {"title": "The Flight", "summary": "The kite lifts the village."}]})}
        return {"content": json.dumps({"text": "Ada touched the kite and it hummed like a live wire.",
                                       "image_prompt": "anime style, a girl holding a glowing kite"})}

    sb._fetch_image = lambda *a, **k: b"\x89PNG-fakeimagebytes" + b"x" * 5000
    pdf, meta = __import__("asyncio").run(sb.generate_storybook("a girl and her kite", stub_llm))
    assert pdf and pdf[:4] == b"%PDF", "PDF header missing"
    assert meta["chapters"] == 2 and meta["illustrated"] == 3

def test_podcast_intent():
    from server.podcast import is_podcast_intent
    assert "crypto in Nigeria" in is_podcast_intent("make a podcast about crypto in Nigeria")
    assert is_podcast_intent("podcast: how to survive Lagos traffic") is not None
    assert is_podcast_intent("/podcast my startup idea") == "my startup idea"
    assert is_podcast_intent("what is a podcast") is None

def test_reminder_parser():
    from server.reminder import parse_reminder
    r = parse_reminder("remind me to call mum in 20 minutes")
    assert r and "call mum" in r["task"] and r["schedule_config"]
    r2 = parse_reminder("remind me to pray at 6:30am")
    assert r2 and "pray" in r2["task"]
    r3 = parse_reminder("/remind me to stretch in 2 hours")
    assert r3 and "stretch" in r3["task"]
    assert parse_reminder("remind me") is None

def test_doccompare_flow():
    from server import doccompare as dc
    key = "tg:777"
    dc.arm(key)
    assert dc.is_armed(key)
    st = dc.store_doc(key, "a.pdf", "first doc text " * 20)
    assert "second document" in st
    st2 = dc.store_doc(key, "b.pdf", "second doc text " * 20)
    assert st2 == "__READY__"
    pair = dc.pop_pair(key)
    assert pair and pair["doc1"]["name"] == "a.pdf" and pair["doc2"]["name"] == "b.pdf"
    assert not dc.is_armed(key)
    assert "invoice" in dc.extract_bytes(b"hello invoice world", "x.txt")

if __name__ == "__main__":
    test_storybook_intent()
    test_storybook_pdf_and_generation()
    test_podcast_intent()
    test_reminder_parser()
    test_doccompare_flow()
    print("ALL STORYBOOK/PODCAST/COMPARE/REMINDER TESTS PASS")
