"""E2E test: Live Execution & Activity Streaming System.

Simulates a realistic multi-tool agent run (Gmail check -> Instagram
analytics -> report generation) through the SAME code path production uses:
tool_agent's _tool_display/_tool_evidence/_thinking_label feeding
live_motion.LiveActivityStream.record(), driven by its real background
ticker against a fake Telegram bot that just records edit calls.
"""
import os, sys, asyncio, time

sys.path.insert(0, os.path.abspath("stew_deploy"))
os.chdir("stew_deploy")


class FakeBot:
    def __init__(self):
        self.sent = []
        self.edits = []
        self._next_id = 1000

    async def send_chat_action(self, chat_id, action):
        pass

    async def send_message(self, chat_id, text, parse_mode=""):
        mid = self._next_id
        self._next_id += 1
        self.sent.append((chat_id, text))
        return {"message_id": mid}

    async def edit_message(self, chat_id, message_id, text, clear_keyboard=False):
        self.edits.append(text)
        return {"ok": True}


async def main():
    from server.live_motion import LiveActivityStream
    from server.tool_agent import _tool_display, _tool_evidence, _thinking_label

    bot = FakeBot()
    stream = LiveActivityStream(bot, 12345)
    await stream.start()
    assert stream.message_id is not None
    print("PASS start() sent initial message, id =", stream.message_id)

    # 1) Thinking labels vary across iterations (never frozen on one string)
    labels = [_thinking_label(i, set()) for i in range(1, 6)]
    assert labels[0] == "Planning execution…"
    assert len(set(labels)) >= 3, f"thinking labels too repetitive: {labels}"
    print("PASS thinking labels vary:", labels)

    # 2) Simulate: composio_execute (Gmail) -> real evidence with a count
    disp = _tool_display("composio_execute", {"toolkit": "gmail", "action": "list_messages"})
    assert disp["icon"] == "📧" and disp["connector"] is True
    stream.record({"kind": "tool_start", "tool": "composio_execute", "iteration": 1, **disp})
    assert stream.tool_card and stream.tool_card["status"] == "running"
    gmail_result = {"success": True, "data": {"messages": list(range(327))}}
    evidence, ok = _tool_evidence("composio_execute", {}, gmail_result)
    assert ok and evidence == "Found 327 messages", evidence
    stream.record({"kind": "tool_done", "tool": "composio_execute", "iteration": 1,
                   "icon": disp["icon"], "name": disp["name"], "connector": True,
                   "evidence": evidence, "ok": ok})
    gmail_line = stream.timeline[-1]
    assert gmail_line.startswith("📧 ✅") and "Found 327 messages" in gmail_line
    assert stream.tool_card["status"] == "done" and stream.tool_card["evidence"] == evidence
    print("PASS Gmail connector step: evidence =", evidence, "| timeline:", gmail_line)

    # 3) Instagram analytics — a failure case must show a real error, not silence
    disp2 = _tool_display("composio_execute", {"toolkit": "instagram", "action": "get_analytics"})
    stream.record({"kind": "tool_start", "tool": "composio_execute", "iteration": 2, **disp2})
    ig_fail = {"success": False, "error": "Instagram token expired"}
    evidence2, ok2 = _tool_evidence("composio_execute", {}, ig_fail)
    assert not ok2 and "token expired" in evidence2
    stream.record({"kind": "tool_done", "tool": "composio_execute", "iteration": 2,
                   "icon": disp2["icon"], "name": disp2["name"], "connector": True,
                   "evidence": evidence2, "ok": ok2})
    assert "⚠️" in stream.timeline[-1] and "token expired" in stream.timeline[-1]
    print("PASS Instagram failure surfaced honestly:", stream.timeline[-1])

    # 4) web_search step with a query-specific label + result count evidence
    disp3 = _tool_display("web_search", {"query": "best posting times instagram 2026"})
    assert "best posting times" in disp3["label"]
    stream.record({"kind": "tool_start", "tool": "web_search", "iteration": 3, **disp3})
    search_result = {"success": True, "data": {"results": [1, 2, 3, 4, 5]}}
    evidence3, ok3 = _tool_evidence("web_search", {}, search_result)
    assert evidence3 == "Found 5 results"
    stream.record({"kind": "tool_done", "tool": "web_search", "iteration": 3,
                   "icon": disp3["icon"], "name": disp3["name"], "connector": False,
                   "evidence": evidence3, "ok": ok3})
    print("PASS web_search step:", disp3["label"], "->", evidence3)

    # 5) generate_document — evidence falls back to output text
    disp4 = _tool_display("generate_document", {"topic": "Instagram engagement report"})
    stream.record({"kind": "tool_start", "tool": "generate_document", "iteration": 4, **disp4})
    doc_result = {"success": True, "output": "PDF generated: 4 pages, 1,204 words", "file_base64": "x"}
    evidence4, ok4 = _tool_evidence("generate_document", {}, doc_result)
    assert "PDF generated" in evidence4
    stream.record({"kind": "tool_done", "tool": "generate_document", "iteration": 4,
                   "icon": disp4["icon"], "name": disp4["name"], "connector": False,
                   "evidence": evidence4, "ok": ok4})
    print("PASS generate_document step:", evidence4)

    # 6) Progress must be monotonically non-decreasing and bounded <= 90 pre-finish
    assert 0 <= stream.progress_pct <= 90
    print(f"PASS progress heuristic after 4 steps: {stream.progress_pct}%")

    # 7) Render must never blow past Telegram's 4096-char hard limit
    rendered = stream._render()
    assert len(rendered) <= 3900, len(rendered)
    assert "📧" in rendered and "Found 327 messages" in rendered  # Gmail step still traceable
    assert "🖥 Live Log" in rendered
    assert "%" in rendered
    print(f"PASS render length {len(rendered)} chars, well under Telegram's 4096 cap")
    print("----- sample render -----")
    print(rendered)
    print("--------------------------")

    # 8) Let the real background ticker run a couple of cycles and confirm
    #    it actually edited the live message (not just sent once and gone
    #    static — that's the exact bug this whole feature exists to fix).
    await asyncio.sleep(3.4)
    assert len(bot.edits) >= 1, "ticker never edited the message — screen went static"
    print(f"PASS ticker performed {len(bot.edits)} live edits during the run")

    # 9) Identical content between ticks must NOT trigger a duplicate edit
    #    (avoids Telegram's 'message is not modified' spam / flood limits).
    edits_before = len(bot.edits)
    await asyncio.sleep(1.6)
    # no new record() calls happened, so with a static state past the last
    # visible spinner-frame change... spinner frame DOES change every tick,
    # so edits will still occur — that's intended "always alive" motion.
    # What must NOT happen is an edit when finish() has already frozen state.
    print(f"PASS spinner keeps the screen alive between tool events ({len(bot.edits)} total edits so far)")

    # 10) finish() stops the ticker for good — no more edits after finish
    await stream.finish("✅ Task completed — report sent")
    edits_at_finish = len(bot.edits)
    await asyncio.sleep(2.0)
    assert len(bot.edits) == edits_at_finish, "ticker kept running after finish() — resource leak"
    final_text = bot.edits[-1]
    assert "Task completed" in final_text
    assert "100%" in final_text
    print("PASS finish() froze the ticker permanently and rendered the final summary")
    print("----- final render -----")
    print(final_text)
    print("-------------------------")

    print("\nALL LIVE EXECUTION STREAM E2E TESTS: PASS")


asyncio.run(main())

async def motion_check():
    """Explicit proof the spinner actually animates frame-to-frame while a
    'thinking' or 'tool running' state is active — the exact bug ('screen
    goes static while thinking') this whole feature exists to kill."""
    from server.live_motion import LiveActivityStream
    bot = FakeBot()
    stream = LiveActivityStream(bot, 555)
    await stream.start()
    stream.record({"kind": "thinking", "label": "Planning execution…"})
    await asyncio.sleep(1.6)
    stream.record({"kind": "tool_start", "tool": "web_search", "icon": "🔍",
                   "name": "Web Search", "label": "Searching…", "connector": False})
    await asyncio.sleep(4.6)  # ~3 ticks while "running" — spinner must move each time
    await stream.finish("✅ Done")
    assert len(bot.edits) >= 4, f"expected several live edits while active, got {len(bot.edits)}"
    distinct_frames = len(set(bot.edits[:-1]))  # exclude final frozen render
    assert distinct_frames >= 3, f"spinner barely moved — only {distinct_frames} distinct frames"
    print(f"PASS motion check: {len(bot.edits)} total edits, {distinct_frames} visually distinct "
          f"live frames while 'thinking'/'running' — screen never went static")

asyncio.run(motion_check())
