"""Image and video handlers: eager byte caching and the auto-describe gate.

The design point is that bytes are fetched on arrival (LINE's content API
expires) so a later quote-reply can still show the bot the picture.
"""
from datetime import timedelta

from family_line_bot.handlers import image as image_handler
from family_line_bot.handlers import video as video_handler
from tests.conftest import FakeClaudeService, FakeLineService, make_event

CHAT = "Cgroup123"


def test_image_bytes_are_fetched_and_cached_on_arrival(store, claude, line):
    event = make_event(chat_id=CHAT, message_id="img-1", message_type="image")
    image_handler.process(event, store, claude, line, auto_describe=False)
    entry = store.get_message(CHAT, "img-1")
    assert entry["type"] == "image"
    assert entry["image_bytes"] == b"jpeg-bytes"
    assert entry["text"] == "[分享了照片]"


def test_a_failed_image_fetch_is_swallowed_and_still_logged(store, claude, line):
    line.fetch_image = lambda message_id: (_ for _ in ()).throw(RuntimeError("410 gone"))
    event = make_event(chat_id=CHAT, message_id="img-1", message_type="image")
    image_handler.process(event, store, claude, line)
    assert store.get_message(CHAT, "img-1")["image_bytes"] is None


def test_images_are_not_described_by_default(store, claude, line):
    event = make_event(chat_id=CHAT, message_id="img-1", message_type="image")
    image_handler.process(event, store, claude, line, auto_describe=False)
    assert claude.calls == [] and line.replies == []


def test_auto_describe_stays_off_in_groups_even_when_enabled(store, claude, line):
    # Groups are expected to quote the photo and ask; unprompted commentary
    # on every shared photo would be noise.
    event = make_event(chat_id=CHAT, message_id="img-1", message_type="image")
    image_handler.process(event, store, claude, line, auto_describe=True)
    assert line.replies == []


def test_auto_describe_replies_in_one_to_one_chat(store, claude, line):
    event = make_event(chat_id="Uuser1", source_type="user", message_id="img-1", message_type="image")
    image_handler.process(event, store, claude, line, auto_describe=True)
    assert [t for _, t in line.replies] == ["hi"]
    assert store.in_session("Uuser1", timedelta(minutes=10)) is True


def test_a_vision_failure_does_not_reply(store, line):
    claude = FakeClaudeService(error=RuntimeError("boom"))
    event = make_event(chat_id="Uuser1", source_type="user", message_id="img-1", message_type="image")
    image_handler.process(event, store, claude, line, auto_describe=True)
    assert line.replies == []


def test_video_preview_is_fetched_and_logged(store, line):
    event = make_event(chat_id=CHAT, message_id="vid-1", message_type="video")
    video_handler.process(event, store, line)
    entry = store.get_message(CHAT, "vid-1")
    assert entry["type"] == "video"
    assert entry["image_bytes"] == b"preview-bytes"
    assert entry["text"] == "[分享了影片]"


def test_a_failed_preview_fetch_is_swallowed(store, line):
    line.fetch_video_preview = lambda message_id: (_ for _ in ()).throw(RuntimeError("410"))
    event = make_event(chat_id=CHAT, message_id="vid-1", message_type="video")
    video_handler.process(event, store, line)
    assert store.get_message(CHAT, "vid-1")["image_bytes"] is None


def test_videos_never_trigger_a_reply(store, line):
    event = make_event(chat_id=CHAT, message_id="vid-1", message_type="video")
    video_handler.process(event, store, line)
    assert line.replies == []


def test_unknown_sender_falls_back_to_the_raw_user_id(store, claude):
    line = FakeLineService()
    event = make_event(chat_id=CHAT, message_id="img-1", message_type="image", user_id=None)
    image_handler.process(event, store, claude, line)
    assert store.get_message(CHAT, "img-1")["user"] == "unknown"
