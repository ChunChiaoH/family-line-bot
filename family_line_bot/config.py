from datetime import timedelta
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_PERSONA = (
    "你是住在家庭 LINE 群組裡的一員，講話像個熟識的人，不是客服。"
    "請務必用繁體中文回覆。\n\n"
    "說話方式：訊息要短，貼近家人傳 LINE 的長度——多數回覆一兩句就夠，"
    "只有查詢結果、被追問細節時才值得多行。"
    "語氣自然溫暖，可以帶一點口語（欸、喔、啦），但別過量；"
    "emoji 偶爾一個就好，不要每句都加。"
    "被問看法時可以有自己的偏好，直說，不必四平八穩。\n\n"
    "避免 AI 腔：不要用「好的！」「當然可以！」開頭；"
    "不要用「如果還有其他問題，歡迎隨時問我」「很高興能幫上忙」這類客服結尾；"
    "不要說「好問題」；不要先複述對方的問題才回答；"
    "簡單的事就直接講，不要列成一二三點；驚嘆號少用。\n\n"
    "LINE 不支援 markdown，回覆一律用純文字："
    "不要用 **粗體**、# 標題、`程式碼` 等符號；"
    "真的需要列點時用「・」或數字加句號，段落之間空一行即可。\n\n"
    "談到日期時，把你解析後的具體日期講出來（例如「明天（7/14 週二）」），"
    "讓家人能發現理解偏差。凌晨時段（約 00:00–05:00）家人說的「明天」"
    "通常是指當天白天，不確定時以當天優先。"
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    line_channel_secret: str
    line_channel_access_token: str
    anthropic_api_key: str

    bot_persona: str = _DEFAULT_PERSONA
    claude_model: str = "claude-sonnet-4-6"
    context_window_hours: int = 2
    max_history: int = 50
    auto_describe_images: bool = False
    webhook_path_token: str = ""
    allowed_chat_ids: str = ""
    session_window_minutes: int = 10
    use_firestore: bool = False
    gcp_project: str = ""  # empty = let the client auto-detect via ADC
    media_bucket: str = ""  # empty = media bytes stay cache-only (no GCS persistence)
    tdx_client_id: str = ""
    tdx_client_secret: str = ""

    @property
    def context_window(self) -> timedelta:
        return timedelta(hours=self.context_window_hours)

    @property
    def session_window(self) -> timedelta:
        return timedelta(minutes=self.session_window_minutes)

    @property
    def allowed_chat_id_set(self) -> set[str]:
        return {c.strip() for c in self.allowed_chat_ids.split(",") if c.strip()}
