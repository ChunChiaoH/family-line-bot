"""HTTP surface: signature verification, whitelist, webhook path token, health.

Runs through FastAPI's TestClient against a real `create_app`. Firestore is off
and the LINE/Anthropic clients are never actually called: handler dispatch is
either short-circuited by the whitelist or monkeypatched.
"""
import json

import pytest
from fastapi.testclient import TestClient

from family_line_bot.app import create_app
from family_line_bot.handlers import text as text_handler
from tests.conftest import make_settings, sign

ALLOWED = "Callowed"
BLOCKED = "Cblocked"


def webhook_body(chat_id=ALLOWED, text="hello", message_id="msg-1", message_type="text"):
    event = {
        "type": "message",
        "mode": "active",
        "timestamp": 1757000000000,
        "source": {"type": "group", "groupId": chat_id, "userId": "Uuser1"},
        "webhookEventId": "01ABCDEF",
        "deliveryContext": {"isRedelivery": False},
        "replyToken": "reply-token",
        # quoteToken is required by the SDK's message models.
        "message": {"type": message_type, "id": message_id, "quoteToken": "qt-1"},
    }
    if message_type == "text":
        event["message"]["text"] = text
    else:
        event["message"]["contentProvider"] = {"type": "line"}
    return json.dumps({"destination": "Ubot", "events": [event]}, ensure_ascii=False)


@pytest.fixture
def app_settings():
    return make_settings(allowed_chat_ids=ALLOWED)


@pytest.fixture
def client(app_settings):
    return TestClient(create_app(app_settings))


@pytest.fixture
def dispatched(monkeypatch):
    """Capture handler dispatch instead of talking to LINE/Anthropic."""
    seen = []
    monkeypatch.setattr(
        text_handler, "process", lambda event, *a, **kw: seen.append(event.message.text)
    )
    return seen


# --- health ---------------------------------------------------------------

def test_health_endpoint(client):
    assert client.get("/health").json() == {"status": "ok"}


# --- signature verification ----------------------------------------------

def test_valid_signature_is_accepted(client, dispatched):
    body = webhook_body()
    resp = client.post("/webhook", content=body, headers={"X-Line-Signature": sign(body)})
    assert resp.status_code == 200
    assert dispatched == ["hello"]


def test_tampered_body_is_rejected(client, dispatched):
    body = webhook_body()
    signature = sign(body)
    tampered = webhook_body(text="transfer all the money")
    resp = client.post("/webhook", content=tampered, headers={"X-Line-Signature": signature})
    assert resp.status_code == 400
    assert dispatched == []


def test_wrong_secret_is_rejected(client, dispatched):
    body = webhook_body()
    resp = client.post(
        "/webhook", content=body, headers={"X-Line-Signature": sign(body, "attacker-secret")}
    )
    assert resp.status_code == 400
    assert dispatched == []


def test_missing_signature_header_is_rejected(client, dispatched):
    resp = client.post("/webhook", content=webhook_body())
    assert resp.status_code == 400
    assert dispatched == []


def test_garbage_signature_is_rejected(client, dispatched):
    body = webhook_body()
    resp = client.post("/webhook", content=body, headers={"X-Line-Signature": "not-base64"})
    assert resp.status_code == 400
    assert dispatched == []


def test_signature_is_computed_over_the_raw_utf8_body(client, dispatched):
    # Non-ASCII bodies are the normal case for this bot; the HMAC must be taken
    # over the exact bytes LINE sent, not a re-encoded form.
    body = webhook_body(text="今天晚餐吃什麼？")
    resp = client.post(
        "/webhook",
        content=body.encode("utf-8"),
        headers={"X-Line-Signature": sign(body), "Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    assert dispatched == ["今天晚餐吃什麼？"]


# --- whitelist ------------------------------------------------------------

def test_non_whitelisted_chat_is_ignored(client, dispatched):
    body = webhook_body(chat_id=BLOCKED)
    resp = client.post("/webhook", content=body, headers={"X-Line-Signature": sign(body)})
    # Signature is valid, so LINE gets a 200 — the message just goes nowhere.
    assert resp.status_code == 200
    assert dispatched == []


def test_empty_whitelist_allows_every_chat(dispatched):
    client = TestClient(create_app(make_settings(allowed_chat_ids="")))
    body = webhook_body(chat_id="Canything")
    resp = client.post("/webhook", content=body, headers={"X-Line-Signature": sign(body)})
    assert resp.status_code == 200
    assert dispatched == ["hello"]


def test_whitelist_applies_to_image_events(monkeypatch):
    from family_line_bot.handlers import image as image_handler

    seen = []
    monkeypatch.setattr(image_handler, "process", lambda *a, **kw: seen.append(1))
    client = TestClient(create_app(make_settings(allowed_chat_ids=ALLOWED)))
    for chat_id, expected in ((BLOCKED, 0), (ALLOWED, 1)):
        body = webhook_body(chat_id=chat_id, message_type="image")
        client.post("/webhook", content=body, headers={"X-Line-Signature": sign(body)})
        assert len(seen) == expected


def test_whitelist_applies_to_video_events(monkeypatch):
    from family_line_bot.handlers import video as video_handler

    seen = []
    monkeypatch.setattr(video_handler, "process", lambda *a, **kw: seen.append(1))
    client = TestClient(create_app(make_settings(allowed_chat_ids=ALLOWED)))
    for chat_id, expected in ((BLOCKED, 0), (ALLOWED, 1)):
        body = webhook_body(chat_id=chat_id, message_type="video")
        client.post("/webhook", content=body, headers={"X-Line-Signature": sign(body)})
        assert len(seen) == expected


# --- webhook path token ---------------------------------------------------

def test_default_webhook_path_when_no_token_is_set(client):
    body = webhook_body(chat_id=BLOCKED)
    assert client.post(
        "/webhook", content=body, headers={"X-Line-Signature": sign(body)}
    ).status_code == 200


def test_path_token_moves_the_endpoint_and_the_bare_path_404s():
    client = TestClient(create_app(make_settings(allowed_chat_ids=ALLOWED, webhook_path_token="s3cr3t")))
    body = webhook_body(chat_id=BLOCKED)
    headers = {"X-Line-Signature": sign(body)}
    assert client.post("/webhook/s3cr3t", content=body, headers=headers).status_code == 200
    assert client.post("/webhook", content=body, headers=headers).status_code == 404
