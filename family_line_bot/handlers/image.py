import logging
from linebot.v3.webhooks import MessageEvent

from . import get_chat_id
from ..store import ChatStore
from ..services.claude import ClaudeService
from ..services.line_client import LineService

logger = logging.getLogger(__name__)


def process(
    event: MessageEvent,
    store: ChatStore,
    claude: ClaudeService,
    line: LineService,
    auto_describe: bool = False,
) -> None:
    chat_id = get_chat_id(event)
    user_id = event.source.user_id or "unknown"
    user_name = line.get_display_name(chat_id, user_id) if user_id != "unknown" else user_id

    # Always fetch and cache bytes immediately — LINE's content API expires,
    # and the user may @mention the bot later quoting this image.
    try:
        image_bytes = line.fetch_image(event.message.id)
    except Exception as e:
        logger.error("Failed to fetch image on arrival: %s", e)
        image_bytes = None

    store.log_message(
        chat_id, event.message.id, user_name, "[分享了照片]",
        msg_type="image", image_bytes=image_bytes,
    )

    if not auto_describe or not image_bytes:
        return

    # Only describe images in 1:1 chat — groups quote the image and ask instead.
    if event.source.type != "user":
        return

    try:
        reply_text = claude.ask_image(store.get_context(chat_id), image_bytes)
    except Exception as e:
        logger.error("Claude vision error: %s", e)
        return

    line.send_reply(event.reply_token, reply_text)
    store.log_bot_reply(chat_id, "[described an image]")
