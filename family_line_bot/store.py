from collections import defaultdict
from datetime import datetime, timedelta, timezone


class ChatStore:
    def __init__(self, context_window: timedelta, max_history: int):
        self._context_window = context_window
        self._max_history = max_history
        self._logs: dict[str, list[dict]] = defaultdict(list)
        self._messages: dict[str, dict] = {}
        self._last_bot_ts: dict[str, datetime] = {}
        self._memory: dict[str, str] = {}
        self._memory_files: dict[str, dict[str, str]] = {}

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
        entry = {"ts": now, "user": user_id, "text": text, "type": msg_type, "image_bytes": image_bytes}
        self._logs[chat_id].append(entry)
        self._messages[message_id] = entry
        cutoff = now - self._context_window
        self._logs[chat_id] = [m for m in self._logs[chat_id] if m["ts"] > cutoff]
        self._logs[chat_id] = self._logs[chat_id][-self._max_history :]

    def log_bot_reply(self, chat_id: str, text: str, message_id: str | None = None) -> None:
        now = datetime.now(timezone.utc)
        entry = {"ts": now, "user": "bot", "text": text, "type": "text", "image_bytes": None}
        self._logs[chat_id].append(entry)
        self._last_bot_ts[chat_id] = now
        if message_id:
            self._messages[message_id] = entry

    def in_session(self, chat_id: str, window: timedelta) -> bool:
        last = self._last_bot_ts.get(chat_id)
        return last is not None and datetime.now(timezone.utc) - last < window

    def get_context(self, chat_id: str) -> str:
        messages = self._logs[chat_id]
        if len(messages) <= 1:
            return ""
        hours = int(self._context_window.total_seconds() / 3600)
        lines = [f'{m["user"]}: {m["text"]}' for m in messages[:-1]]
        return f"Recent conversation (last {hours} hours):\n" + "\n".join(lines) + "\n\n"

    def get_message(self, chat_id: str, message_id: str) -> dict | None:
        return self._messages.get(message_id)

    def get_memory(self, chat_id: str) -> str:
        return self._memory.get(chat_id, "")

    def append_memory(self, chat_id: str, fact: str) -> None:
        text = self._memory.get(chat_id, "")
        text = f"{text}\n- {fact}" if text else f"- {fact}"
        self._memory[chat_id] = _trim_memory(text)

    def list_memory_files(self, chat_id: str) -> dict[str, str]:
        return dict(self._memory_files.get(chat_id, {}))

    def write_memory_file(self, chat_id: str, name: str, content: str) -> None:
        self._memory_files.setdefault(chat_id, {})[name] = content

    def delete_memory_file(self, chat_id: str, name: str) -> None:
        self._memory_files.get(chat_id, {}).pop(name, None)


_MEMORY_LIMIT = 4000


def _trim_memory(text: str) -> str:
    lines = text.split("\n")
    while len("\n".join(lines)) > _MEMORY_LIMIT and lines:
        lines.pop(0)
    return "\n".join(lines)
