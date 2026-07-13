import logging

from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    MessagingApi,
    MessagingApiBlob,
    ReplyMessageRequest,
    TextMessage,
)

logger = logging.getLogger(__name__)


class LineService:
    def __init__(self, access_token: str):
        self._config = Configuration(access_token=access_token)
        self._name_cache: dict[tuple[str, str], str] = {}

    def send_reply(self, reply_token: str, text: str) -> str | None:
        with ApiClient(self._config) as api_client:
            response = MessagingApi(api_client).reply_message(
                ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=[TextMessage(text=text)],
                )
            )
        sent = getattr(response, "sent_messages", None)
        return sent[0].id if sent else None

    def fetch_image(self, message_id: str) -> bytes:
        with ApiClient(self._config) as api_client:
            return MessagingApiBlob(api_client).get_message_content(message_id)

    def fetch_video_preview(self, message_id: str) -> bytes:
        with ApiClient(self._config) as api_client:
            return MessagingApiBlob(api_client).get_message_content_preview(message_id)

    def get_display_name(self, chat_id: str, user_id: str) -> str:
        cache_key = (chat_id, user_id)
        if cache_key in self._name_cache:
            return self._name_cache[cache_key]

        try:
            with ApiClient(self._config) as api_client:
                api = MessagingApi(api_client)
                if chat_id.startswith("C"):
                    profile = api.get_group_member_profile(chat_id, user_id)
                elif chat_id.startswith("R"):
                    profile = api.get_room_member_profile(chat_id, user_id)
                else:
                    profile = api.get_profile(user_id)
        except Exception:
            logger.warning(
                "Failed to resolve display name for user_id=%s chat_id=%s",
                user_id,
                chat_id,
                exc_info=True,
            )
            return user_id

        display_name = profile.display_name
        self._name_cache[cache_key] = display_name
        return display_name
