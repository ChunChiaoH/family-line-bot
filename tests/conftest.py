"""Shared fixtures.

Everything here is offline: no LINE API, no Anthropic API, no Firestore/GCS.
`Settings` is always built with `_env_file=None` and the relevant OS env vars
are stripped, so a developer's real `.env` can never leak into a test.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
from datetime import timedelta
from types import SimpleNamespace

import pytest

from family_line_bot.config import Settings
from family_line_bot.store import ChatStore

CHANNEL_SECRET = "test-channel-secret"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Strip any real configuration from the process environment."""
    for key in (
        "LINE_CHANNEL_SECRET",
        "LINE_CHANNEL_ACCESS_TOKEN",
        "ANTHROPIC_API_KEY",
        "ALLOWED_CHAT_IDS",
        "USE_FIRESTORE",
        "WEBHOOK_PATH_TOKEN",
        "GCP_PROJECT",
        "MEDIA_BUCKET",
        "TDX_CLIENT_ID",
        "TDX_CLIENT_SECRET",
        "BOT_PERSONA",
        "SESSION_WINDOW_MINUTES",
        "CONTEXT_WINDOW_HOURS",
        "MAX_HISTORY",
    ):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture(autouse=True)
def _reset_handler_globals():
    """`handlers.text` keeps per-chat locks / supersede state at module level."""
    from family_line_bot.handlers import text as text_handler

    text_handler._chat_locks.clear()
    text_handler._latest_message.clear()
    yield
    text_handler._chat_locks.clear()
    text_handler._latest_message.clear()


def make_settings(**overrides) -> Settings:
    base = dict(
        line_channel_secret=CHANNEL_SECRET,
        line_channel_access_token="test-access-token",
        anthropic_api_key="sk-ant-test",
        use_firestore=False,
    )
    base.update(overrides)
    return Settings(_env_file=None, **base)


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
def store() -> ChatStore:
    return ChatStore(context_window=timedelta(hours=12), max_history=50)


# --- fake LINE / Claude collaborators ------------------------------------


class FakeLineService:
    """Records replies instead of calling the LINE Messaging API."""

    def __init__(self, display_name: str = "Alice"):
        self.display_name = display_name
        self.replies: list[tuple[str, str]] = []
        self._counter = 0

    def get_display_name(self, chat_id: str, user_id: str) -> str:
        return self.display_name

    def send_reply(self, reply_token: str, text: str) -> str:
        self._counter += 1
        self.replies.append((reply_token, text))
        return f"sent-{self._counter}"

    def fetch_image(self, message_id: str) -> bytes:
        return b"jpeg-bytes"

    def fetch_video_preview(self, message_id: str) -> bytes:
        return b"preview-bytes"


class FakeClaudeService:
    """Scripted `ask_text` / `ask_image`; records every call's kwargs."""

    def __init__(self, reply: str | None = "hi", error: Exception | None = None):
        self.reply = reply
        self.error = error
        self.calls: list[dict] = []

    def ask_text(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.reply

    def ask_image(self, context, image_bytes):
        self.calls.append({"context": context, "image_bytes": image_bytes})
        if self.error is not None:
            raise self.error
        return self.reply


@pytest.fixture
def line() -> FakeLineService:
    return FakeLineService()


@pytest.fixture
def claude() -> FakeClaudeService:
    return FakeClaudeService()


# --- fake LINE webhook events --------------------------------------------


def make_event(
    text: str = "hello",
    *,
    chat_id: str = "Cgroup123",
    source_type: str = "group",
    user_id: str = "Uuser1",
    message_id: str = "msg-1",
    mentionees: list | None = None,
    quoted_message_id: str | None = None,
    is_redelivery: bool = False,
    reply_token: str = "reply-token",
    message_type: str = "text",
):
    """A duck-typed stand-in for `linebot.v3.webhooks.MessageEvent`.

    The handlers only ever use attribute access, so a namespace is both
    sufficient and far more readable than assembling SDK models.
    """
    source = SimpleNamespace(type=source_type, user_id=user_id)
    if source_type == "group":
        source.group_id = chat_id
    elif source_type == "room":
        source.room_id = chat_id
    else:  # 1:1 chat — the chat id *is* the user id
        source.user_id = chat_id

    mention = None
    if mentionees is not None:
        mention = SimpleNamespace(mentionees=mentionees)

    message = SimpleNamespace(
        id=message_id,
        text=text,
        type=message_type,
        mention=mention,
        quoted_message_id=quoted_message_id,
    )
    return SimpleNamespace(
        source=source,
        message=message,
        reply_token=reply_token,
        delivery_context=SimpleNamespace(is_redelivery=is_redelivery),
    )


def mentionee(index: int, length: int, is_self: bool = False, user_id: str = "Uother"):
    return SimpleNamespace(index=index, length=length, is_self=is_self, user_id=user_id)


# --- webhook signing ------------------------------------------------------


def sign(body: str, secret: str = CHANNEL_SECRET) -> str:
    digest = hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()
    return base64.b64encode(digest).decode()
