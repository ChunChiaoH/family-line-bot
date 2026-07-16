from datetime import datetime, timedelta, timezone


class FirestoreChatStore:
    """Firestore-backed ChatStore so context survives Cloud Run scale-to-zero.

    Text and timestamps live in Firestore. Media bytes go to GCS via `media`
    (the doc keeps only the blob path — Firestore's 1MB doc limit rules out
    inline bytes) with a process-local cache as the hot path; without a
    MediaStore they are cache-only and lost on restart.
    """

    def __init__(self, context_window: timedelta, max_history: int, project: str = "", media=None):
        # Lazy import so google-cloud-firestore isn't required for local dev.
        from google.cloud import firestore

        self._firestore = firestore
        self._db = firestore.Client(project=project) if project else firestore.Client()
        self._context_window = context_window
        self._max_history = max_history
        self._image_cache: dict[str, bytes] = {}
        self._media = media

    def _chat_ref(self, chat_id: str):
        return self._db.collection("chats").document(chat_id)

    def _messages_ref(self, chat_id: str):
        return self._chat_ref(chat_id).collection("messages")

    def _memory_files_ref(self, chat_id: str):
        return self._chat_ref(chat_id).collection("memory_files")

    def log_message(
        self,
        chat_id: str,
        message_id: str,
        user_id: str,
        text: str,
        msg_type: str = "text",
        image_bytes: bytes | None = None,
    ) -> None:
        now = datetime.now(timezone.utc)
        entry = {"ts": now, "user": user_id, "text": text, "type": msg_type}
        if image_bytes is not None:
            self._image_cache[message_id] = image_bytes
            if self._media is not None:
                path = self._media.save(chat_id, message_id, image_bytes)
                if path:
                    entry["media_path"] = path
        self._messages_ref(chat_id).document(message_id).set(entry)

    def log_bot_reply(self, chat_id: str, text: str, message_id: str | None = None) -> None:
        now = datetime.now(timezone.utc)
        entry = {"ts": now, "user": "bot", "text": text, "type": "text"}
        if message_id:
            self._messages_ref(chat_id).document(message_id).set(entry)
        else:
            self._messages_ref(chat_id).add(entry)
        self._chat_ref(chat_id).set({"last_bot_ts": now}, merge=True)

    def in_session(self, chat_id: str, window: timedelta) -> bool:
        # snap.get(field) raises KeyError on a missing field; go via to_dict.
        snap = self._chat_ref(chat_id).get()
        last = (snap.to_dict() or {}).get("last_bot_ts")
        return last is not None and datetime.now(timezone.utc) - last < window

    def _recent_messages(self, chat_id: str) -> list[dict]:
        cutoff = datetime.now(timezone.utc) - self._context_window
        # Newest-first with a limit to bound reads, then flip to chronological.
        query = (
            self._messages_ref(chat_id)
            .where(filter=self._firestore.FieldFilter("ts", ">", cutoff))
            .order_by("ts", direction=self._firestore.Query.DESCENDING)
            .limit(self._max_history)
        )
        docs = [d.to_dict() for d in query.stream()]
        docs.reverse()
        return docs

    def get_context(self, chat_id: str) -> str:
        messages = self._recent_messages(chat_id)
        if len(messages) <= 1:
            return ""
        hours = int(self._context_window.total_seconds() / 3600)
        lines = [f'{m["user"]}: {m["text"]}' for m in messages[:-1]]
        return f"Recent conversation (last {hours} hours):\n" + "\n".join(lines) + "\n\n"

    def get_message(self, chat_id: str, message_id: str) -> dict | None:
        snap = self._messages_ref(chat_id).document(message_id).get()
        if not snap.exists:
            return None
        entry = snap.to_dict()
        image_bytes = self._image_cache.get(message_id)
        # Cache miss (restart since arrival): restore from GCS.
        if image_bytes is None and self._media is not None and entry.get("media_path"):
            image_bytes = self._media.load(entry["media_path"])
            if image_bytes is not None:
                self._image_cache[message_id] = image_bytes
        entry["image_bytes"] = image_bytes
        return entry

    def get_memory(self, chat_id: str) -> str:
        snap = self._chat_ref(chat_id).get()
        return (snap.to_dict() or {}).get("memory") or ""

    def append_memory(self, chat_id: str, fact: str) -> None:
        # Read-modify-write, no transaction: fine at family scale where concurrent
        # appends to the same chat's memory are not expected.
        text = self.get_memory(chat_id)
        text = f"{text}\n- {fact}" if text else f"- {fact}"
        self._chat_ref(chat_id).set({"memory": _trim_memory(text)}, merge=True)

    def list_memory_files(self, chat_id: str) -> dict[str, str]:
        docs = self._memory_files_ref(chat_id).stream()
        return {d.id: (d.to_dict() or {}).get("content", "") for d in docs}

    def write_memory_file(self, chat_id: str, name: str, content: str) -> None:
        now = datetime.now(timezone.utc)
        self._memory_files_ref(chat_id).document(name).set({"content": content, "updated": now})

    def delete_memory_file(self, chat_id: str, name: str) -> None:
        self._memory_files_ref(chat_id).document(name).delete()


_MEMORY_LIMIT = 4000


def _trim_memory(text: str) -> str:
    lines = text.split("\n")
    while len("\n".join(lines)) > _MEMORY_LIMIT and lines:
        lines.pop(0)
    return "\n".join(lines)
