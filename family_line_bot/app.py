import logging
from fastapi import FastAPI, HTTPException, Request
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.webhooks import (
    ImageMessageContent,
    MessageEvent,
    TextMessageContent,
    VideoMessageContent,
)

from .config import Settings
from .handlers import get_chat_id
from .handlers import image as image_handler
from .handlers import text as text_handler
from .handlers import video as video_handler
from .services.claude import ClaudeService
from .services.line_client import LineService
from .store import ChatStore

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def create_app(settings: Settings) -> FastAPI:
    app = FastAPI(title="Family LINE Bot")

    if settings.use_firestore:
        from .firestore_store import FirestoreChatStore

        media = None
        if settings.media_bucket:
            from .services.media import MediaStore

            media = MediaStore(settings.media_bucket, project=settings.gcp_project)

        store = FirestoreChatStore(
            context_window=settings.context_window,
            max_history=settings.max_history,
            project=settings.gcp_project,
            media=media,
        )
    else:
        store = ChatStore(
            context_window=settings.context_window,
            max_history=settings.max_history,
        )
    claude = ClaudeService(
        api_key=settings.anthropic_api_key,
        model=settings.claude_model,
        system_prompt=settings.bot_persona,
    )
    line = LineService(access_token=settings.line_channel_access_token)
    webhook_handler = WebhookHandler(settings.line_channel_secret)

    thsr = None
    if settings.tdx_client_id and settings.tdx_client_secret:
        from .services.thsr import ThsrService

        thsr = ThsrService(settings.tdx_client_id, settings.tdx_client_secret)

    @webhook_handler.add(MessageEvent, message=TextMessageContent)
    def handle_text(event: MessageEvent):
        chat_id = get_chat_id(event)
        if settings.allowed_chat_id_set and chat_id not in settings.allowed_chat_id_set:
            logger.warning("Ignoring message from non-whitelisted chat %s", chat_id)
            return
        text_handler.process(event, store, claude, line, settings.session_window, thsr)

    @webhook_handler.add(MessageEvent, message=ImageMessageContent)
    def handle_image(event: MessageEvent):
        chat_id = get_chat_id(event)
        if settings.allowed_chat_id_set and chat_id not in settings.allowed_chat_id_set:
            logger.warning("Ignoring message from non-whitelisted chat %s", chat_id)
            return
        image_handler.process(event, store, claude, line, settings.auto_describe_images)

    @webhook_handler.add(MessageEvent, message=VideoMessageContent)
    def handle_video(event: MessageEvent):
        chat_id = get_chat_id(event)
        if settings.allowed_chat_id_set and chat_id not in settings.allowed_chat_id_set:
            logger.warning("Ignoring message from non-whitelisted chat %s", chat_id)
            return
        video_handler.process(event, store, line)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    webhook_path = f"/webhook/{settings.webhook_path_token}" if settings.webhook_path_token else "/webhook"

    @app.post(webhook_path)
    async def webhook(request: Request):
        signature = request.headers.get("X-Line-Signature", "")
        body = await request.body()
        try:
            webhook_handler.handle(body.decode(), signature)
        except InvalidSignatureError:
            raise HTTPException(status_code=400, detail="Invalid signature")
        return "OK"

    return app
