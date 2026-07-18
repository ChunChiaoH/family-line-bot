import base64
import logging
from datetime import datetime
from typing import Callable
from zoneinfo import ZoneInfo

import anthropic

_WEEKDAYS = "一二三四五六日"

logger = logging.getLogger(__name__)

_SKIP_INSTRUCTION = (
    "\n\n你剛剛才在這個對話中發言過，以下這則新訊息「沒有」@你，"
    "請判斷它是說給你聽的、還是說給家人聽的。\n"
    "說給你聽的（正常回覆）：延續或追問你剛才說的內容；對你提出新的問題或請求；"
    "語氣明顯是在對你說話（即使沒有稱呼你）；"
    "你上次發言以問題結尾且還沒有人回答過，而這則訊息的內容回應了那個問題——"
    "這是在回答你，一定要回覆，即使中間隔了別人的訊息。"
    "注意：訊息裡 @ 某個家人不一定是在跟那個人說話，更常只是「提到」他——"
    "「我在雪梨 但 @小華 應該在台北」是在回答你的問題順便向你補充小華的狀況"
    "（跟小華說她自己在哪是說不通的），這種要回覆；"
    "評論或吐槽「你」這個助理（輕鬆接一兩句就好）。\n"
    "說給家人聽的（只回覆「SKIP」）：家人彼此之間的交談；"
    "與你先前發言無關的新話題，例如宣告自己的行程、分享生活近況——"
    "這種訊息即使出現在你發言後不久，也安靜記著就好。"
)

_WEB_SEARCH_TOOL = {
    "type": "web_search_20260209",
    "name": "web_search",
    "max_uses": 3,
}

_MEMORY_TOOL = {"type": "memory_20250818", "name": "memory"}

# Counters the API-injected "ALWAYS VIEW YOUR MEMORY DIRECTORY" protocol:
# memory is already fully rendered into the prompt, so a view round-trip per
# message would only add cost and latency.
_MEMORY_INSTRUCTION = (
    "\n\n你的長期記憶（{root} 目錄的全部內容）已經完整附在下方，"
    "「不需要」也「不應該」用 view 指令查看——直接根據下方內容回答即可。"
    "只有在需要新增、修改、刪除或整理記憶時才使用 memory 工具的寫入指令"
    "（create / str_replace / insert / delete / rename）。"
    "值得記住的：家人的偏好、過敏、重要日期、約定好的事情；"
    "家人明確說「記住…」時一定要記。不要記臨時或瑣碎的資訊。"
    "只要你這一輪用了記憶寫入指令，就代表這則訊息是說給你聽的——"
    "「不要」回覆 SKIP，至少簡短讓對方知道你記下了。"
    "建議的檔案：members.md（成員與稱謂）、preferences.md（偏好與禁忌）、"
    "dates.md（重要日期）、agreements.md（約定事項）。"
    "記憶空間有限，定期把過期的條目刪掉、重複的合併。\n\n"
    "=== 長期記憶開始 ===\n{memory}\n=== 長期記憶結束 ==="
)

_THSR_TOOL = {
    "name": "search_thsr",
    "description": (
        "查詢台灣高鐵的班次時刻、票價與對號座剩餘座位狀態。當家人詢問高鐵班次、"
        "發車時間、票價、還有沒有位子、或想訂高鐵票時使用。座位狀態（有位/位少/售完）"
        "只涵蓋約一天內的班次，更遠的日期只會回時刻與票價。"
        "回傳結果已含官方訂票連結，直接轉述給家人即可。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "origin": {"type": "string", "description": "出發站，例如 台北"},
            "destination": {"type": "string", "description": "到達站，例如 左營"},
            "date": {"type": "string", "description": "乘車日期 YYYY-MM-DD"},
            "time_after": {
                "type": "string",
                "description": "只列出此時間之後出發的班次 HH:MM，未指定時用 00:00",
            },
        },
        "required": ["origin", "destination", "date"],
    },
}

_MAX_TOOL_ITERATIONS = 5


class ClaudeService:
    def __init__(self, api_key: str, model: str, system_prompt: str, timezone: str = "Asia/Taipei"):
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model
        self._system_prompt = system_prompt
        self._tz = ZoneInfo(timezone)

    def _now_line(self) -> str:
        # Goes in the user prompt, not the system prompt: a per-request
        # timestamp at the front of the prefix would defeat prompt caching.
        now = datetime.now(self._tz)
        return f"[現在時間：{now:%Y-%m-%d} 週{_WEEKDAYS[now.weekday()]} {now:%H:%M}（台灣時間）]\n"

    def ask_text(
        self,
        context: str,
        query: str,
        quoted: str,
        quoted_image: bytes | None,
        may_skip: bool = False,
        memory: str = "",
        tool_handlers: dict[str, Callable[..., str]] | None = None,
    ) -> str | None:
        system = self._system_prompt
        if tool_handlers and "memory" in tool_handlers:
            system += _MEMORY_INSTRUCTION.format(root="/memories", memory=memory or "（目前是空的）")
        if may_skip:
            system += _SKIP_INSTRUCTION

        prompt = self._now_line() + context + quoted + query
        if quoted_image:
            content = [
                {"type": "text", "text": prompt},
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": base64.standard_b64encode(quoted_image).decode(),
                    },
                },
            ]
        else:
            content = prompt

        tools = [_WEB_SEARCH_TOOL]
        if tool_handlers and "memory" in tool_handlers:
            tools.append(_MEMORY_TOOL)
        if tool_handlers and "search_thsr" in tool_handlers:
            tools.append(_THSR_TOOL)

        messages = [{"role": "user", "content": content}]
        response = None
        for _ in range(_MAX_TOOL_ITERATIONS):
            response = self._client.messages.create(
                model=self._model,
                max_tokens=1024,
                # SKIP judgments ride the same call as generation; default
                # temperature (1.0) makes borderline judgments coin-flips.
                temperature=0.3,
                # Breakpoint at the system tail caches the tools+system prefix
                # across tool-loop iterations (and 5-min-adjacent requests).
                # Below the model's minimum cacheable length it's silently a no-op.
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                tools=tools,
                messages=messages,
            )
            usage = response.usage
            logger.info(
                "Usage: in=%s out=%s cache_write=%s cache_read=%s",
                usage.input_tokens,
                usage.output_tokens,
                getattr(usage, "cache_creation_input_tokens", None),
                getattr(usage, "cache_read_input_tokens", None),
            )

            # Server tools (web_search) hit their iteration limit: resend to resume.
            if response.stop_reason == "pause_turn":
                messages.append({"role": "assistant", "content": response.content})
                continue

            if response.stop_reason != "tool_use":
                break

            messages.append({"role": "assistant", "content": response.content})
            results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                handler = (tool_handlers or {}).get(block.name)
                logger.info("Tool call: %s %s", block.name, block.input)
                try:
                    result = handler(**block.input) if handler else f"Unknown tool: {block.name}"
                    # Handlers may return (content, is_error) — the memory backend does.
                    content, is_error = result if isinstance(result, tuple) else (result, False)
                    entry = {"type": "tool_result", "tool_use_id": block.id, "content": content}
                    if is_error:
                        entry["is_error"] = True
                    results.append(entry)
                except Exception as e:
                    logger.error("Tool %s failed: %s", block.name, e)
                    results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": f"Error: {e}",
                        "is_error": True,
                    })
            messages.append({"role": "user", "content": results})

        reply = "".join(b.text for b in response.content if b.type == "text").strip()
        if not reply:
            return None
        return None if reply == "SKIP" else reply

    def ask_image(self, context: str, image_bytes: bytes) -> str:
        response = self._client.messages.create(
            model=self._model,
            max_tokens=512,
            system=self._system_prompt,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": context + "A family member shared this image. Briefly describe what you see and react warmly.",
                    },
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": base64.standard_b64encode(image_bytes).decode(),
                        },
                    },
                ],
            }],
        )
        return response.content[0].text.strip()
