import logging
import threading
from collections import defaultdict
from datetime import timedelta

from linebot.v3.webhooks import MessageEvent

from . import get_chat_id
from ..store import ChatStore
from ..services.claude import ClaudeService
from ..services.line_client import LineService
from ..services.memory import MemoryToolBackend

logger = logging.getLogger(__name__)

# Per-chat serialization + supersede tracking. In-process state is safe as a
# global lock only because the service runs with max-instances=1.
_chat_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
_latest_message: dict[str, str] = {}


def _is_bot_mentioned(event: MessageEvent) -> bool:
    if event.source.type == "user":
        return True
    mention = getattr(event.message, "mention", None)
    if not mention or not mention.mentionees:
        return False
    return any(getattr(m, "is_self", False) for m in mention.mentionees)


def _extract_query(event: MessageEvent) -> str:
    text = event.message.text
    mention = getattr(event.message, "mention", None)
    if not mention or not mention.mentionees:
        return text.strip()
    for m in sorted(mention.mentionees, key=lambda x: x.index, reverse=True):
        text = text[: m.index] + text[m.index + m.length :]
    return text.strip()


def _get_quoted_context(
    event: MessageEvent, store: ChatStore, chat_id: str
) -> tuple[str, bytes | None, bool]:
    quoted_id = getattr(event.message, "quoted_message_id", None)
    if not quoted_id:
        return "", None, False
    q = store.get_message(chat_id, quoted_id)
    if not q:
        return "", None, False
    is_bot = q["user"] == "bot"
    if q.get("type") == "image":
        return "[Replying to a photo]\n", q.get("image_bytes"), is_bot
    if q.get("type") == "video":
        return "[Replying to a video — its preview thumbnail is attached]\n", q.get("image_bytes"), is_bot
    return f'[Replying to {q["user"]}: "{q["text"]}"]\n', None, is_bot


def process(
    event: MessageEvent,
    store: ChatStore,
    claude: ClaudeService,
    line: LineService,
    session_window: timedelta,
    thsr=None,
) -> None:
    chat_id = get_chat_id(event)
    user_id = event.source.user_id or "unknown"
    user_name = line.get_display_name(chat_id, user_id) if user_id != "unknown" else user_id

    store.log_message(chat_id, event.message.id, user_name, event.message.text)
    _latest_message[chat_id] = event.message.id

    quoted, quoted_image, replying_to_bot = _get_quoted_context(event, store, chat_id)

    # Hard triggers always respond; a recent bot reply opens a session window
    # where Claude judges whether the message is addressed to it (may SKIP).
    if _is_bot_mentioned(event) or replying_to_bot:
        may_skip = False
        trigger = "reply_to_bot" if replying_to_bot else "mention"
    elif store.in_session(chat_id, session_window):
        may_skip = True
        trigger = "session"
    else:
        logger.info("Trigger: none (chat=%s)", chat_id)
        return

    query = _extract_query(event)
    if not query:
        return

    logger.info("Trigger: %s (chat=%s, msg=%s)", trigger, chat_id, event.message.id)
    if quoted:
        logger.info("Quoted context: %s | has_image=%s", quoted.strip(), quoted_image is not None)

    with _chat_locks[chat_id]:
        # While waiting for the lock a newer message may have arrived; for
        # soft (session) triggers, let its handler judge with full context.
        if may_skip and _latest_message.get(chat_id) != event.message.id:
            logger.info("Trigger: superseded by newer message (chat=%s, msg=%s)", chat_id, event.message.id)
            return

        memory_backend = MemoryToolBackend(store, chat_id)
        tool_handlers = {"memory": memory_backend.handle}
        if thsr is not None:
            tool_handlers["search_thsr"] = thsr.search

        try:
            reply_text = claude.ask_text(
                context=store.get_context(chat_id),
                query=f"{user_name}: {query}",
                quoted=quoted,
                quoted_image=quoted_image,
                may_skip=may_skip,
                memory=memory_backend.render(),
                tool_handlers=tool_handlers,
            )
        except Exception as e:
            logger.error("Claude API error: %s", e)
            if may_skip:
                return
            reply_text = "Sorry, I ran into an issue. Please try again!"

        if reply_text is None:
            logger.info("Claude chose to skip (chat=%s, msg=%s)", chat_id, event.message.id)
            return

        sent_id = line.send_reply(event.reply_token, reply_text)
        store.log_bot_reply(chat_id, reply_text, message_id=sent_id)
