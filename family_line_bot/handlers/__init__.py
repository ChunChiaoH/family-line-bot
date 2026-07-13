from linebot.v3.webhooks import MessageEvent


def get_chat_id(event: MessageEvent) -> str:
    src = event.source
    return getattr(src, "group_id", None) or getattr(src, "room_id", None) or src.user_id
