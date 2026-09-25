"""E2E test: Claude-style Execution Live panel with WIND motion.

Simulates a multi-tool run through the real production path
(tool_agent helpers -> LiveActivityStream.record -> real ticker ->
HTML render) and asserts the Claude-panel look: bold step titles,
muted evidence lines, drifting wind strip, wind progress bar,
valid HTML escaping.
"""
import os, sys, asyncio, html, json

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
        self.sent.append((parse_mode, text))
        return {"message_id": mid}

    async def edit_message(self, chat_id, message_id, text, clear_keyboard=False, parse_mode=""):
        self.edits.append((parse_mode, text))
        return {"ok": True}


def assert_valid_tg_html(text):
    """Telegram HTML must be balanced and fully escaped."""
    assert text.count("<b>") == text.count("</b>"), "unbalanced <b> tags"
    stripped = text.replace("<b>", "").replace("</b>", "")
    assert "<" not in stripped and ">" not in stripped, "raw angle bracket leaked into message"


async def main():
    from server.live_motion import LiveActivityStream, _wind_strip
    from server.tool_agent import _tool_display, _tool_evidence, _thinking_label

    # wind strip sanity: drifts phase, constant length
    assert _wind_strip(10, 0) != _wind_strip(10, 1)
    assert len(_wind_strip(24, 7)) == 24
    print("PASS wind strip drifts with phase and keeps length")

    bot = FakeBot()
    stream = LiveActivityStream(bot, 12345)
    await stream.start()
    assert stream.message_id is not None
    assert bot.sent[0][0] == "HTML", "panel must be sent with HTML parse_mode"
    print("PASS start() sends the panel with HTML parse_mode")

    # thinking labels still vary
    labels = [_thinking_label(i, set()) for i in range(1, 6)]
    assert len(set(labels)) >= 3
    print("PASS thinking labels vary:", labels)

    # Gmail step
    d = _tool_display("composio_execute", {"toolkit": "gmail", "action": "list_messages"})
    stream.record({"kind": "tool_start", "tool": "composio_execute", "iteration": 1, **d})
    ev, ok = _tool_evidence("composio_execute", {}, {"success": True, "data": {"messages": list(range(327))}})
    stream.record({"kind": "tool_done", "tool": "composio_execute", "iteration": 1,
                   "icon": d["icon"], "name": d["name"], "connector": True, "evidence": ev, "ok": ok})
    step = stream.timeline[-1]
    assert isinstance(step, dict) and step["title"] == "Running List Messages"
    assert step["evidence"] == "Found 327 messages" and step["ok"]
    assert stream.tool_card["status"] == "done" and stream.tool_card["evidence"] == ev
    print("PASS Gmail step group:", step["title"], "->", step["evidence"])

    # failure step surfaces honestly
    d2 = _tool_display("composio_execute", {"toolkit": "instagram", "action": "get_analytics"})
    stream.record({"kind": "tool_start", "tool": "composio_execute", "iteration": 2, **d2})
    ev2, ok2 = _tool_evidence("composio_execute", {}, {"success": False, "error": "Instagram token expired"})
    stream.record({"kind": "tool_done", "tool": "composio_execute", "iteration": 2,
                   "icon": d2["icon"], "name": d2["name"], "connector": True, "evidence": ev2, "ok": ok2})
    assert not stream.timeline[-1]["ok"] and "token expired" in stream.timeline[-1]["evidence"]
    print("PASS failure step surfaces honestly:", stream.timeline[-1]["evidence"])

    # web search step
    d3 = _tool_display("web_search", {"query": "best time to post on Instagram Nigeria"})
    stream.record({"kind": "tool_start", "tool": "web_search", "iteration": 3, **d3})
    ev3, ok3 = _tool_evidence("web_search", {}, {"success": True, "data": {"results": [1, 2, 3, 4, 5]}})
    stream.record({"kind": "tool_done", "tool": "web_search", "iteration": 3,
                   "icon": d3["icon"], "name": d3["name"], "connector": False, "evidence": ev3, "ok": ok3})
    assert stream.timeline[-1]["evidence"] == "Found 5 results"
    print("PASS web search step group")

    # render: Claude-panel structure + escaping of an adversarial evidence string
    stream.record({"kind": "tool_done", "tool": "web_search", "iteration": 4,
                   "icon": "🔍", "name": "Web Search", "connector": False,
                   "evidence": "result had <script>alert(1)</script> & chars", "ok": True})
    rendered = stream._render()
    assert_valid_tg_html(rendered)
    assert "<b>⚡ Stew is working</b>" in rendered
    assert "<b>✓ Running List Messages</b>" in rendered
    assert "· Found 327 messages" in rendered
    assert "&lt;script&gt;" in rendered  # HTML-escaped, Telegram-safe
    assert "≋" in rendered or "≈" in rendered  # wind present
    assert "%" in rendered
    assert "<b>Activity</b>" in rendered
    assert len(rendered) <= 3900
    print("PASS Claude-style render: bold titles, muted lines, wind, escaped HTML, len", len(rendered))
    print("----- sample render -----")
    print(rendered)
    print("--------------------------")

    # wind drifts between ticks => visually flowing motion
    r1 = stream._render()
    stream._frame = (stream._frame + 1) % 4
    r2 = stream._render()
    assert r1 != r2, "wind did not drift between ticks"
    print("PASS wind drifts between ticks (screen flows, never static)")

    # ticker performs live HTML edits
    stream.record({"kind": "thinking", "label": "Reviewing what's been found…"})
    await asyncio.sleep(3.4)
    assert len(bot.edits) >= 1 and all(pm == "HTML" for pm, _ in bot.edits), "ticker never edited with HTML"
    print(f"PASS ticker performed {len(bot.edits)} live HTML edits")

    # finish: frozen ticker, 100%, valid HTML
    await stream.finish("✅ Task completed — report sent")
    edits_at_finish = len(bot.edits)
    await asyncio.sleep(2.0)
    assert len(bot.edits) == edits_at_finish, "ticker leaked past finish()"
    final_pm, final_text = bot.edits[-1]
    assert final_pm == "HTML"
    assert_valid_tg_html(final_text)
    assert "100%" in final_text and "Task completed" in final_text
    assert "finished" in final_text
    print("PASS finish froze ticker; final panel valid with 100% wind bar")
    print("----- final render -----")
    print(final_text)
    print("-------------------------")

    print("\nALL CLAUDE-STYLE WIND PANEL E2E TESTS: PASS")


asyncio.run(main())
