import logging
from linebot.v3.webhooks import MessageEvent

from . import get_chat_id
from ..store import ChatStore
from ..services.line_client import LineService

logger = logging.getLogger(__name__)


def process(
    event: MessageEvent,
    store: ChatStore,
    line: LineService,
) -> None:
    chat_id = get_chat_id(event)
    user_id = event.source.user_id or "unknown"
    user_name = line.get_display_name(chat_id, user_id) if user_id != "unknown" else user_id

    # Always fetch and cache the preview thumbnail immediately — LINE's content
    # API expires, and the user may @mention the bot later quoting this video.
    try:
        preview_bytes = line.fetch_video_preview(event.message.id)
    except Exception as e:
        logger.error("Failed to fetch video preview on arrival: %s", e)
        preview_bytes = None

    store.log_message(
        chat_id, event.message.id, user_name, "[分享了影片]",
        msg_type="video", image_bytes=preview_bytes,
    )
