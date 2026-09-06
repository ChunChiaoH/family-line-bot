"""Hybrid triggering rules in handlers/text.py.

Design intent (wiki/triggering.md):
  1. hard trigger - @mention or quote-reply to the bot; 1:1 chat counts as a
     mention. Always answers, never SKIPs.
  2. soft trigger - inside the session window after a real bot reply; Claude
     judges whether the message is addressed to it and may return SKIP.
  3. otherwise - log only, stay silent.
Plus: redelivery guard, supersede (soft triggers only), asymmetric error
handling, and the bare-@ greeting placeholder.
"""
import threading
import time
from datetime import timedelta

from family_line_bot.handlers import get_chat_id
from family_line_bot.handlers import text as text_handler
from family_line_bot.handlers.text import _extract_query, _is_bot_mentioned
from tests.conftest import FakeClaudeService, make_event, mentionee

SESSION = timedelta(minutes=10)
CHAT = "Cgroup123"


def run(event, store, claude, line, session_window=SESSION, thsr=None):
    text_handler.process(event, store, claude, line, session_window, thsr)


# --- chat id resolution ---------------------------------------------------

def test_chat_id_is_the_group_id_in_a_group():
    assert get_chat_id(make_event(chat_id="Cabc")) == "Cabc"


def test_chat_id_is_the_room_id_in_a_room():
    assert get_chat_id(make_event(chat_id="Rabc", source_type="room")) == "Rabc"


def test_chat_id_falls_back_to_the_user_id_in_one_to_one():
    assert get_chat_id(make_event(chat_id="Uabc", source_type="user")) == "Uabc"


# --- mention detection ----------------------------------------------------

def test_one_to_one_chat_always_counts_as_a_mention():
    assert _is_bot_mentioned(make_event(source_type="user")) is True


def test_group_message_without_a_mention_payload_is_not_a_mention():
    assert _is_bot_mentioned(make_event("hello")) is False


def test_mention_of_another_member_is_not_a_mention_of_the_bot():
    event = make_event("@Bob hi", mentionees=[mentionee(0, 4, is_self=False)])
    assert _is_bot_mentioned(event) is False


def test_mention_with_is_self_is_a_mention_of_the_bot():
    event = make_event("@bot hi", mentionees=[mentionee(0, 4, is_self=True)])
    assert _is_bot_mentioned(event) is True


def test_mention_of_bot_alongside_another_member_still_counts():
    event = make_event(
        "@Bob @bot hi",
        mentionees=[mentionee(0, 4, is_self=False), mentionee(5, 4, is_self=True)],
    )
    assert _is_bot_mentioned(event) is True


# --- query extraction -----------------------------------------------------

def test_extract_query_strips_the_mention_span():
    event = make_event("@bot 今天天氣如何", mentionees=[mentionee(0, 4, is_self=True)])
    assert _extract_query(event) == "今天天氣如何"


def test_extract_query_strips_multiple_mentions_back_to_front():
    event = make_event(
        "@bot 你好 @Bob",
        mentionees=[mentionee(0, 4, is_self=True), mentionee(8, 4, is_self=False)],
    )
    assert _extract_query(event) == "你好"


def test_extract_query_on_a_plain_message_just_trims():
    assert _extract_query(make_event("  hello  ")) == "hello"


# --- rule 1: hard triggers ------------------------------------------------

def test_mention_gets_a_reply(store, claude, line):
    run(make_event("@bot 在嗎", mentionees=[mentionee(0, 4, is_self=True)]), store, claude, line)
    assert [t for _, t in line.replies] == ["hi"]


def test_hard_trigger_never_allows_skip(store, claude, line):
    run(make_event("@bot hi", mentionees=[mentionee(0, 4, is_self=True)]), store, claude, line)
    assert claude.calls[0]["may_skip"] is False


def test_one_to_one_message_replies_without_any_mention(store, claude, line):
    run(make_event("hello", chat_id="Uuser1", source_type="user"), store, claude, line)
    assert len(line.replies) == 1


def test_quote_reply_to_the_bot_is_a_hard_trigger(store, claude, line):
    store.log_bot_reply(CHAT, "你想吃什麼？", message_id="bot-msg-1")
    # Push the bot reply outside the session window so only the quote can trigger.
    store._last_bot_ts[CHAT] = store.get_message(CHAT, "bot-msg-1")["ts"] - timedelta(hours=1)
    run(make_event("火鍋", quoted_message_id="bot-msg-1"), store, claude, line)
    assert len(line.replies) == 1
    assert claude.calls[0]["may_skip"] is False


def test_quote_reply_to_the_bot_passes_the_quoted_text_as_context(store, claude, line):
    store.log_bot_reply(CHAT, "你想吃什麼？", message_id="bot-msg-1")
    run(make_event("火鍋", quoted_message_id="bot-msg-1"), store, claude, line)
    assert "你想吃什麼？" in claude.calls[0]["quoted"]


def test_quote_reply_to_another_member_is_not_a_trigger(store, claude, line):
    store.log_message(CHAT, "m-prev", "Bob", "明天出門")
    run(make_event("好喔", quoted_message_id="m-prev"), store, claude, line)
    assert line.replies == []


def test_quoting_an_unknown_message_id_yields_no_quoted_context(store, claude, line):
    run(
        make_event("@bot 這個", mentionees=[mentionee(0, 4, is_self=True)],
                   quoted_message_id="never-seen"),
        store, claude, line,
    )
    assert claude.calls[0]["quoted"] == ""


def test_quoted_photo_attaches_its_bytes(store, claude, line):
    store.log_message(CHAT, "img-1", "Bob", "[分享了照片]", msg_type="image", image_bytes=b"jpg")
    run(
        make_event("@bot 這是什麼", mentionees=[mentionee(0, 4, is_self=True)],
                   quoted_message_id="img-1"),
        store, claude, line,
    )
    assert claude.calls[0]["quoted_image"] == b"jpg"
    assert "photo" in claude.calls[0]["quoted"]


def test_quoted_video_is_labelled_as_a_preview_thumbnail(store, claude, line):
    store.log_message(CHAT, "vid-1", "Bob", "[分享了影片]", msg_type="video", image_bytes=b"thumb")
    run(
        make_event("@bot 這是什麼", mentionees=[mentionee(0, 4, is_self=True)],
                   quoted_message_id="vid-1"),
        store, claude, line,
    )
    assert claude.calls[0]["quoted_image"] == b"thumb"
    assert "thumbnail" in claude.calls[0]["quoted"]


# --- rule 3: no trigger ---------------------------------------------------

def test_plain_group_chatter_gets_no_reply_but_is_logged(store, claude, line):
    run(make_event("今天好熱", message_id="m1"), store, claude, line)
    assert line.replies == []
    assert claude.calls == []
    assert store.get_message(CHAT, "m1")["text"] == "今天好熱"


def test_session_window_expiry_stops_the_soft_trigger(store, claude, line):
    store.log_bot_reply(CHAT, "hi")
    store._last_bot_ts[CHAT] -= timedelta(minutes=11)
    run(make_event("那我先去洗澡"), store, claude, line)
    assert line.replies == []


# --- rule 2: soft (session) triggers --------------------------------------

def test_message_inside_the_session_window_is_judged_by_claude(store, claude, line):
    store.log_bot_reply(CHAT, "你想吃什麼？")
    run(make_event("火鍋吧"), store, claude, line)
    assert claude.calls[0]["may_skip"] is True
    assert [t for _, t in line.replies] == ["hi"]


def test_skip_sentinel_produces_no_reply(store, line):
    claude = FakeClaudeService(reply=None)  # ClaudeService maps "SKIP" -> None
    store.log_bot_reply(CHAT, "你想吃什麼？")
    run(make_event("阿明你到了嗎"), store, claude, line)
    assert line.replies == []


def test_a_skipped_message_does_not_extend_the_session_window(store, line):
    claude = FakeClaudeService(reply=None)
    store.log_bot_reply(CHAT, "hi")
    before = store._last_bot_ts[CHAT]
    run(make_event("家人閒聊"), store, claude, line)
    assert store._last_bot_ts[CHAT] == before


def test_a_real_reply_extends_the_session_window(store, claude, line):
    run(make_event("@bot hi", mentionees=[mentionee(0, 4, is_self=True)]), store, claude, line)
    assert store.in_session(CHAT, SESSION) is True


# --- bare @ handling ------------------------------------------------------

def test_bare_mention_becomes_a_greeting_placeholder(store, claude, line):
    run(make_event("@bot", mentionees=[mentionee(0, 4, is_self=True)]), store, claude, line)
    # A summoned bot that stays silent reads as broken (wiki/triggering.md).
    assert len(line.replies) == 1
    assert "只 @ 了你" in claude.calls[0]["query"]


def test_bare_mention_of_someone_else_in_session_stays_silent(store, claude, line):
    store.log_bot_reply(CHAT, "hi")
    run(make_event("@Bob", mentionees=[mentionee(0, 4, is_self=False)]), store, claude, line)
    assert line.replies == []
    assert claude.calls == []


# --- redelivery guard -----------------------------------------------------

def test_redelivery_of_an_already_seen_message_is_ignored(store, claude, line):
    event = make_event("@bot hi", mentionees=[mentionee(0, 4, is_self=True)], message_id="dup-1")
    run(event, store, claude, line)
    redelivered = make_event(
        "@bot hi", mentionees=[mentionee(0, 4, is_self=True)],
        message_id="dup-1", is_redelivery=True,
    )
    run(redelivered, store, claude, line)
    assert len(line.replies) == 1


def test_a_redelivered_message_we_never_saw_is_still_processed(store, claude, line):
    # LINE also redelivers events genuinely lost in transit; those were never
    # logged, so they must go through.
    event = make_event(
        "@bot hi", mentionees=[mentionee(0, 4, is_self=True)],
        message_id="lost-1", is_redelivery=True,
    )
    run(event, store, claude, line)
    assert len(line.replies) == 1


def test_a_non_redelivered_duplicate_id_is_not_suppressed(store, claude, line):
    for _ in range(2):
        run(
            make_event("@bot hi", mentionees=[mentionee(0, 4, is_self=True)], message_id="m1"),
            store, claude, line,
        )
    assert len(line.replies) == 2


# --- supersede ------------------------------------------------------------

def test_soft_trigger_superseded_by_a_newer_message_stays_silent(store, claude, line):
    """Two quick messages: the older one yields so the newest judges with full context."""
    store.log_bot_reply(CHAT, "hi")

    # Hold the per-chat lock so the older message's handler parks on it, exactly
    # as it would while a sibling thread-pool worker is mid-flight.
    lock = text_handler._chat_locks[CHAT]
    lock.acquire()
    worker = threading.Thread(
        target=run, args=(make_event("第一句", message_id="m1"), store, claude, line)
    )
    worker.start()
    while text_handler._latest_message.get(CHAT) != "m1":
        time.sleep(0.005)  # wait until the older handler has registered itself

    # A newer message arrives and claims the chat.
    store.log_message(CHAT, "m2", "Alice", "第二句")
    text_handler._latest_message[CHAT] = "m2"
    lock.release()
    worker.join(timeout=5)

    assert claude.calls == []
    assert line.replies == []


def test_hard_trigger_is_never_superseded(store, claude, line):
    text_handler._latest_message[CHAT] = "someone-else"
    run(
        make_event("@bot hi", mentionees=[mentionee(0, 4, is_self=True)], message_id="m1"),
        store, claude, line,
    )
    assert len(line.replies) == 1


# --- error handling asymmetry --------------------------------------------

def test_hard_trigger_apologises_when_claude_fails(store, line):
    claude = FakeClaudeService(error=RuntimeError("boom"))
    run(make_event("@bot hi", mentionees=[mentionee(0, 4, is_self=True)]), store, claude, line)
    assert len(line.replies) == 1
    assert "Sorry" in line.replies[0][1]


def test_soft_trigger_fails_silently(store, line):
    claude = FakeClaudeService(error=RuntimeError("boom"))
    store.log_bot_reply(CHAT, "hi")
    run(make_event("隨口一句"), store, claude, line)
    assert line.replies == []


# --- wiring ---------------------------------------------------------------

def test_memory_tool_is_always_offered(store, claude, line):
    run(make_event("@bot hi", mentionees=[mentionee(0, 4, is_self=True)]), store, claude, line)
    assert "memory" in claude.calls[0]["tool_handlers"]


def test_thsr_tool_is_offered_only_when_configured(store, claude, line):
    run(make_event("@bot hi", mentionees=[mentionee(0, 4, is_self=True)]), store, claude, line)
    assert "search_thsr" not in claude.calls[0]["tool_handlers"]

    thsr = type("T", (), {"search": staticmethod(lambda **kw: "")})()
    run(
        make_event("@bot hi", mentionees=[mentionee(0, 4, is_self=True)], message_id="m2"),
        store, claude, line, thsr=thsr,
    )
    assert "search_thsr" in claude.calls[1]["tool_handlers"]


def test_query_is_prefixed_with_the_resolved_display_name(store, claude, line):
    line.display_name = "媽媽"
    run(make_event("@bot 吃飯了", mentionees=[mentionee(0, 4, is_self=True)]), store, claude, line)
    assert claude.calls[0]["query"].startswith("媽媽: ")


def test_memory_is_rendered_into_the_call(store, claude, line):
    store.write_memory_file(CHAT, "preferences.md", "- Alice 對花生過敏")
    run(make_event("@bot hi", mentionees=[mentionee(0, 4, is_self=True)]), store, claude, line)
    assert "對花生過敏" in claude.calls[0]["memory"]


def test_the_bot_reply_is_logged_under_the_sent_message_id(store, claude, line):
    run(make_event("@bot hi", mentionees=[mentionee(0, 4, is_self=True)]), store, claude, line)
    assert store.get_message(CHAT, "sent-1")["user"] == "bot"
