"""E2E tests for the 5 new agentic media capabilities (commit: agentic media powers).

1. User sends video + "post this to my YouTube with title X" → routes to tool agent
2. User sends document + "email this to x@y.com" → routes to tool agent
3. "Edit this with Capcut" style captions → video editor engine
4. "Get me images of X" → real internet image search + download
5. "Audit my videos / why no views" → connected social account audit tool
"""
import asyncio, os, re, sys

os.environ.setdefault("STEW_TEST", "1")
sys.path.insert(0, os.path.abspath("stew_deploy"))
os.chdir("stew_deploy")

PASS, FAIL = [], []
def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"{'PASS' if cond else 'FAIL'} {name}" + (f" — {detail}" if detail and not cond else ""))

async def main():
    # ── 1) media intent classifier ──────────────────────────────────────
    from server.user_media import is_media_task_intent, store_media, get_media, pop_media
    check("upload intent detected",
          is_media_task_intent("post this video to my YouTube with title My Vlog"))
    check("tiktok upload intent", is_media_task_intent("upload this to tiktok with #fyp"))
    check("email document intent", is_media_task_intent("email this document to john@gmail.com"))
    check("email via send-to", is_media_task_intent("send it to jane@company.com now"))
    check("plain analysis NOT routed",
          not is_media_task_intent("what do you see in this video"))
    check("random caption NOT routed",
          not is_media_task_intent("my birthday party 2026"))

    # ── 2) media registry ───────────────────────────────────────────────
    import tempfile
    tmp = tempfile.mkdtemp()
    p1 = os.path.join(tmp, "test.mp4")
    open(p1, "wb").write(b"fakevideobytes")
    store_media("tg:111", p1, "test.mp4", "video/mp4", "video")
    m = get_media("tg:111")
    check("media registry stores+returns", m and m["filename"] == "test.mp4")
    pop_media("tg:111")
    check("pop clears registry", get_media("tg:111") is None)

    # ── 3) capcut/canva edit intents → editor engine ─────────────────────
    from server.video_editor import is_edit_intent
    check("capcut edit intent", is_edit_intent("edit this with capcut"))
    check("canva edit intent", is_edit_intent("use canva to make it vertical"))
    check("captions request", is_edit_intent("add captions to this video"))

    # ── 4) real internet image search + download ────────────────────────
    from server import web_images
    hits = web_images.search_images("solar panels on rooftops", 3)
    check("web image search returns hits", len(hits) >= 1, f"got {len(hits)}")
    if hits:
        raw = web_images.download_first(hits)
        check("image downloads to bytes", bool(raw) and len(raw) > 1000,
              "all candidate images failed to download")
        check("ext guess", web_images.guess_ext(hits[0]["url"], raw or b"") in
              ("jpg", "jpeg", "png", "webp", "gif"))

    # ── 5) tool_agent: search_web_images executor (network) ─────────────
    from server.tool_agent import execute_tool
    res = await execute_tool({"tool": "search_web_images",
                              "args": {"query": "wind turbines", "count": 2}},
                             tg_user_id="999", chat_id=999, bot=None)
    check("search_web_images tool succeeds", bool(res.get("success")), str(res.get("error"))[:150])
    check("search_web_images returns image files",
          bool(res.get("files")) and all("base64" in f for f in res.get("files", [])))

    # empty query → graceful error
    res2 = await execute_tool({"tool": "search_web_images", "args": {"query": ""}},
                              tg_user_id="999", chat_id=999, bot=None)
    check("empty image query rejected gracefully", not res2.get("success"))

    # ── 6) get_user_media: no media → graceful; with media → URL or storage error ──
    res3 = await execute_tool({"tool": "get_user_media", "args": {}},
                              tg_user_id="999", chat_id=777, bot=None)
    check("get_user_media empty is graceful", not res3.get("success") and "send" in str(res3.get("output", "")).lower())
    store_media("tg:777", p1, "test.mp4", "video/mp4", "video")
    res4 = await execute_tool({"tool": "get_user_media", "args": {}},
                              tg_user_id="999", chat_id=777, bot=None)
    ok_or_storage = res4.get("success") or "storage" in str(res4.get("error", "")).lower()
    check("get_user_media hosts or reports storage state", ok_or_storage)
    if res4.get("success"):
        check("hosted URL returned", str(res4["data"].get("url", "")).startswith("http"))
    pop_media("tg:777")

    # ── 7) audit_my_videos: unconnected platform → graceful guidance ────
    res5 = await execute_tool({"tool": "audit_my_videos", "args": {"platform": "youtube"}},
                              tg_user_id="testaudit1", chat_id=888, bot=None)
    graceful = (not res5.get("success")) and ("connect" in str(res5.get("output", "")).lower()
                                              or "error" in res5)
    check("audit on unconnected account is graceful", graceful)

    # ── 8) classifier keywords present in main.py ──────────────────────
    src = open("server/main.py").read()
    for kw in ("get me images of", "download images of", "audit my videos",
               "not getting views", "improve my videos"):
        check(f"classifier keyword present: '{kw}'", kw in src)

    # ── 9) routing present: video/doc handlers call _handle_media_task ──
    check("video handler routes media tasks",
          "is_media_task_intent" in src and src.count("_handle_media_task") >= 3)
    check("document handler stores user media", "_um_store_d" in src)

    # ── 10) prompt rules + tool list entries ───────────────────────────
    tsrc = open("server/tool_agent.py").read()
    for entry in ("24. get_user_media", "25. search_web_images", "26. audit_my_videos",
                  "18l. USER-SENT MEDIA", "18m. REAL INTERNET IMAGES", "18n. SOCIAL VIDEO AUDIT"):
        check(f"tool_agent has: {entry}", entry in tsrc)

    print()
    if FAIL:
        print(f"FAILURES: {len(FAIL)} — {FAIL}")
        sys.exit(1)
    print(f"ALL AGENTIC MEDIA E2E TESTS: PASS ({len(PASS)} checks)")

asyncio.run(main())
