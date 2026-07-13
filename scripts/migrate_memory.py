#!/usr/bin/env python
"""One-time migration of per-chat bot memory to per-file documents.

Old schema: Firestore doc ``chats/{chat_id}`` has an optional string field
``memory`` holding flat "- fact" lines.

New schema: subcollection ``chats/{chat_id}/memory_files/{filename}`` where each
doc has ``content`` (str) and ``updated`` (utc datetime).

For each chat with a non-empty ``memory`` field this script classifies the fact
lines into skeleton files using Claude (structured outputs), writes them to the
subcollection, then backs up the original text to ``memory_legacy`` and deletes
the ``memory`` field. Idempotent: chats that already have memory_files docs are
skipped.

Auth: reads an OAuth access token from GCLOUD_TOKEN (no ADC). Run with:

    $env:PYTHONIOENCODING='utf-8'
    $env:GCLOUD_TOKEN = (& gcloud auth print-access-token)
    python scripts/migrate_memory.py
"""
import json
import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

PROJECT = "<your-project-id>"
MODEL = "claude-sonnet-4-6"

# Skeleton files: filename -> Chinese heading used in the file body.
SKELETON = {
    "members.md": "成員與稱謂",
    "preferences.md": "偏好與禁忌",
    "dates.md": "重要日期",
    "agreements.md": "約定事項",
    "misc.md": "其他",
}
FILENAMES = list(SKELETON.keys())

# Structured-outputs schema: a list of {name, content} where name is one of the
# five skeleton filenames.
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "files": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "enum": FILENAMES},
                    "content": {"type": "string"},
                },
                "required": ["name", "content"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["files"],
    "additionalProperties": False,
}

CLASSIFY_PROMPT = """你正在整理一個家庭聊天機器人的長期記憶。下面是一份以「- 事實」形式列出的記憶內容，請把每一條事實分類到最合適的檔案中：

- members.md（成員與稱謂）：家庭成員、名字、稱呼、關係
- preferences.md（偏好與禁忌）：喜好、討厭、飲食或其他偏好、禁忌
- dates.md（重要日期）：生日、紀念日、預定事件等日期
- agreements.md（約定事項）：約定、規則、承諾、待辦
- misc.md（其他）：無法歸入以上任何一類的事實

規則：
- 逐條分類每一行事實，選擇最適合的檔案。
- 完整保留原本用字，不要改寫或翻譯事實內容。
- 每個檔案的格式為：第一行是 "# <中文標題>"，接著每條事實一行，以 "- " 開頭。
- 沒有任何事實的檔案請省略，不要輸出。

以下是要分類的記憶內容：

{memory}
"""


def _fact_lines(memory: str) -> list[str]:
    """Return the non-empty fact lines from a flat memory string."""
    lines = []
    for raw in memory.splitlines():
        line = raw.strip()
        if line:
            lines.append(line)
    return lines


def _heading(name: str) -> str:
    return f"# {SKELETON[name]}"


def _trivial_misc(memory: str) -> str:
    """Build a misc.md body directly from trivial memory (no LLM call)."""
    facts = _fact_lines(memory)
    body = [_heading("misc.md")]
    for f in facts:
        body.append(f if f.startswith("- ") else f"- {f}")
    return "\n".join(body)


def classify_with_claude(client, memory: str) -> dict[str, str]:
    """Classify fact lines into skeleton files. Returns {name: content}."""
    resp = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        messages=[{"role": "user", "content": CLASSIFY_PROMPT.format(memory=memory)}],
        output_config={"format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
    )
    text = next(b.text for b in resp.content if b.type == "text")
    data = json.loads(text)
    files: dict[str, str] = {}
    for entry in data.get("files", []):
        name = entry.get("name")
        content = (entry.get("content") or "").strip()
        if name in SKELETON and content:
            files[name] = content
    return files


def migrate_chat(client, db, firestore, doc) -> None:
    """Migrate a single chat doc; prints a per-chat report."""
    chat_id = doc.id
    data = doc.to_dict() or {}
    memory = (data.get("memory") or "").strip()

    if not memory:
        print(f"[{chat_id}] no memory field / empty — nothing to migrate")
        return

    chat_ref = db.collection("chats").document(chat_id)
    memory_files_ref = chat_ref.collection("memory_files")

    # Idempotency: skip chats that already have memory_files docs.
    existing = list(memory_files_ref.limit(1).stream())
    if existing:
        print(f"[{chat_id}] SKIPPED (already migrated)")
        return

    facts = _fact_lines(memory)
    n_facts = len(facts)

    if n_facts < 1:
        # Trivial: write misc.md directly without an LLM call.
        files = {"misc.md": _trivial_misc(memory)}
    else:
        files = classify_with_claude(client, memory)
        if not files:
            # Defensive fallback: never lose the facts.
            files = {"misc.md": _trivial_misc(memory)}

    now = datetime.now(timezone.utc)
    for name, content in files.items():
        memory_files_ref.document(name).set({"content": content, "updated": now})

    # Back up the original text and remove the old field in one update. The
    # backup must never be lost.
    chat_ref.update({"memory_legacy": memory, "memory": firestore.DELETE_FIELD})

    written = ", ".join(f"{name} ({len(c)} chars)" for name, c in sorted(files.items()))
    print(f"[{chat_id}] {n_facts} facts -> {written}")


def main() -> int:
    load_dotenv()

    token = os.environ.get("GCLOUD_TOKEN", "")
    if not token:
        print("ERROR: GCLOUD_TOKEN missing. Set it to a gcloud access token.")
        return 1

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY missing from .env")
        return 1

    from google.cloud import firestore
    from google.oauth2.credentials import Credentials
    import anthropic

    creds = Credentials(token=token)
    db = firestore.Client(project=PROJECT, credentials=creds)
    client = anthropic.Anthropic(api_key=api_key)

    print(f"Scanning chats collection in project {PROJECT} ...")
    docs = list(db.collection("chats").stream())
    print(f"Found {len(docs)} chat doc(s).\n")

    migrated = 0
    for doc in docs:
        try:
            before = "memory" in (doc.to_dict() or {})
            migrate_chat(client, db, firestore, doc)
            if before:
                migrated += 1
        except Exception as e:  # noqa: BLE001 — report and continue
            print(f"[{doc.id}] ERROR: {e}")

    print(f"\nDone. Processed {len(docs)} chat(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
