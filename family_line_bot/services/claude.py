import base64
import logging
from typing import Callable

import anthropic

logger = logging.getLogger(__name__)

_SKIP_INSTRUCTION = (
    "\n\n你剛剛才在這個對話中發言過，以下這則新訊息「沒有」@你，"
    "請先判斷它是不是說給你聽的。"
    "屬於以下情況就正常回覆：延續或追問你剛才說的內容、對你提出新的問題或請求、"
    "語氣明顯是在對你說話（即使沒有稱呼你）。"
    "只有當訊息明顯是家人彼此之間的交談、或是與你先前發言無關的新話題時，才只回覆「SKIP」。"
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
    "建議的檔案：members.md（成員與稱謂）、preferences.md（偏好與禁忌）、"
    "dates.md（重要日期）、agreements.md（約定事項）。"
    "記憶空間有限，定期把過期的條目刪掉、重複的合併。\n\n"
    "=== 長期記憶開始 ===\n{memory}\n=== 長期記憶結束 ==="
)

_MAX_TOOL_ITERATIONS = 5


class ClaudeService:
    def __init__(self, api_key: str, model: str, system_prompt: str):
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model
        self._system_prompt = system_prompt

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

        prompt = context + quoted + query
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

        messages = [{"role": "user", "content": content}]
        response = None
        for _ in range(_MAX_TOOL_ITERATIONS):
            response = self._client.messages.create(
                model=self._model,
                max_tokens=1024,
                system=system,
                tools=tools,
                messages=messages,
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
