from datetime import timedelta
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_PERSONA = (
    "你是家庭 LINE 群組裡的友善助理。"
    "請務必用繁體中文回覆。"
    "語氣要溫暖親切、簡潔有幫助。"
    "除非被問到需要詳細說明的問題，否則請保持簡短。"
    "LINE 不支援 markdown，回覆一律用純文字："
    "不要用 **粗體**、# 標題、`程式碼` 等符號；"
    "需要列點時用「・」或數字加句號，段落之間空一行即可。"
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
