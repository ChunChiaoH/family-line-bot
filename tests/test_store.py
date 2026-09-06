"""In-memory ChatStore: context rendering, pruning, caps, session window."""
from datetime import datetime, timedelta, timezone

from family_line_bot.store import ChatStore, _trim_memory

CHAT = "Cgroup"


def test_get_context_empty_for_single_message(store):
    store.log_message(CHAT, "m1", "Alice", "hello")
    # The newest message is the query itself — it must not be echoed as context.
    assert store.get_context(CHAT) == ""


def test_get_context_excludes_the_latest_message(store):
    store.log_message(CHAT, "m1", "Alice", "hello")
    store.log_message(CHAT, "m2", "Bob", "hi")
    store.log_message(CHAT, "m3", "Alice", "what's up")
    ctx = store.get_context(CHAT)
    assert "Alice: hello" in ctx
    assert "Bob: hi" in ctx
    assert "what's up" not in ctx


def test_get_context_announces_the_window_in_hours(store):
    store.log_message(CHAT, "m1", "Alice", "a")
    store.log_message(CHAT, "m2", "Bob", "b")
    assert "last 12 hours" in store.get_context(CHAT)


def test_context_is_per_chat(store):
    store.log_message(CHAT, "m1", "Alice", "in group")
    store.log_message("Cother", "m2", "Bob", "elsewhere")
    store.log_message("Cother", "m3", "Bob", "again")
    assert "in group" not in store.get_context("Cother")


def test_messages_older_than_the_context_window_are_pruned():
    store = ChatStore(context_window=timedelta(hours=1), max_history=50)
    store.log_message(CHAT, "old", "Alice", "ancient")
    # Backdate the entry, then let the next write trigger the cutoff sweep.
    store.get_message(CHAT, "old")["ts"] = datetime.now(timezone.utc) - timedelta(hours=2)
    store.log_message(CHAT, "m2", "Bob", "recent")
    store.log_message(CHAT, "m3", "Bob", "newest")
    assert "ancient" not in store.get_context(CHAT)
    assert "recent" in store.get_context(CHAT)


def test_max_history_caps_the_log():
    store = ChatStore(context_window=timedelta(hours=12), max_history=3)
    for i in range(10):
        store.log_message(CHAT, f"m{i}", "Alice", f"line{i}")
    ctx = store.get_context(CHAT)
    assert "line0" not in ctx and "line6" not in ctx
    # 3 kept, minus the latest which get_context withholds.
    assert ctx.count("Alice:") == 2


def test_in_session_false_before_any_bot_reply(store):
    assert store.in_session(CHAT, timedelta(minutes=10)) is False


def test_in_session_true_right_after_a_bot_reply(store):
    store.log_bot_reply(CHAT, "hi there")
    assert store.in_session(CHAT, timedelta(minutes=10)) is True


def test_in_session_false_once_the_window_has_passed(store):
    store.log_bot_reply(CHAT, "hi there")
    store._last_bot_ts[CHAT] = datetime.now(timezone.utc) - timedelta(minutes=11)
    assert store.in_session(CHAT, timedelta(minutes=10)) is False


def test_session_window_is_per_chat(store):
    store.log_bot_reply(CHAT, "hi")
    assert store.in_session("Cother", timedelta(minutes=10)) is False


def test_bot_reply_is_retrievable_by_sent_message_id(store):
    store.log_bot_reply(CHAT, "hi there", message_id="sent-1")
    entry = store.get_message(CHAT, "sent-1")
    assert entry["user"] == "bot" and entry["text"] == "hi there"


def test_logged_image_keeps_type_and_bytes(store):
    store.log_message(CHAT, "img-1", "Alice", "[分享了照片]", msg_type="image", image_bytes=b"jpg")
    entry = store.get_message(CHAT, "img-1")
    assert entry["type"] == "image" and entry["image_bytes"] == b"jpg"


def test_get_message_returns_none_for_unknown_id(store):
    assert store.get_message(CHAT, "nope") is None


def test_memory_files_round_trip_and_delete(store):
    store.write_memory_file(CHAT, "members.md", "- Alice")
    assert store.list_memory_files(CHAT) == {"members.md": "- Alice"}
    store.delete_memory_file(CHAT, "members.md")
    assert store.list_memory_files(CHAT) == {}


def test_list_memory_files_returns_a_copy(store):
    store.write_memory_file(CHAT, "a.md", "x")
    snapshot = store.list_memory_files(CHAT)
    snapshot["a.md"] = "tampered"
    assert store.list_memory_files(CHAT)["a.md"] == "x"


def test_trim_memory_drops_oldest_lines_past_the_limit():
    text = "\n".join(f"line{i}" * 100 for i in range(20))
    trimmed = _trim_memory(text)
    assert len(trimmed) <= 4000
    assert trimmed.endswith("line19" * 100)
