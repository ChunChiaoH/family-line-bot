"""Unified timeline for reviewing a chat: Firestore messages + Cloud Run logs.

Usage:
    python scripts/review_chat.py <chat_id> [--hours N]   (default 24)

Prints one chronological view (Asia/Taipei time) of:
- family/bot messages (Firestore, including media markers)
- trigger decisions, tool calls, token usage, SKIPs, redeliveries,
  cold starts, errors (Cloud Run logs)
- per-reply processing latency (trigger line -> usage line)
plus a summary block. Read-only; needs gcloud auth (logs + Firestore REST).
"""
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def _resolve_project() -> str:
    """GCP project id: env ``GCP_PROJECT`` (see .env.example), else the active
    ``gcloud config get-value project``. Exits with a clear message if neither."""
    project = os.environ.get("GCP_PROJECT", "").strip()
    if project:
        return project
    try:
        out = subprocess.run(
            ["gcloud", "config", "get-value", "project"],
            capture_output=True, text=True, encoding="utf-8", shell=True,
        ).stdout.strip()
    except OSError:
        out = ""
    if out and out != "(unset)":
        return out
    sys.exit(
        "No GCP project configured. Set GCP_PROJECT in .env "
        "or run: gcloud config set project <your-project-id>"
    )

PROJECT = _resolve_project()
SERVICE = "family-line-bot"
TPE = ZoneInfo("Asia/Taipei")


def _gcloud(args: list[str]) -> str:
    result = subprocess.run(
        ["gcloud", *args], capture_output=True, text=True, encoding="utf-8", shell=True
    )
    if result.returncode != 0:
        sys.exit(f"gcloud failed: {result.stderr.strip()[:500]}")
    return result.stdout


def fetch_messages(chat_id: str, since: datetime) -> list[dict]:
    token = _gcloud(["auth", "print-access-token"]).strip()
    import urllib.request

    url = (
        f"https://firestore.googleapis.com/v1/projects/{PROJECT}/databases/(default)/"
        f"documents/chats/{chat_id}/messages?pageSize=300"
    )
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req) as resp:
        data = json.load(resp)
    out = []
    for doc in data.get("documents", []):
        f = doc["fields"]
        ts = datetime.fromisoformat(f["ts"]["timestampValue"].replace("Z", "+00:00"))
        if ts < since:
            continue
        out.append({
            "ts": ts,
            "kind": "msg",
            "user": f["user"]["stringValue"],
            "text": f.get("text", {}).get("stringValue", ""),
            "type": f.get("type", {}).get("stringValue", "text"),
            "media": f.get("media_path", {}).get("stringValue", ""),
        })
    return out


_LOG_PATTERNS = [
    ("trigger", re.compile(r"Trigger: (\S+) \(chat=(\S+?)[,)]")),
    ("tool", re.compile(r"Tool call: (\S+) (.*)")),
    ("usage", re.compile(r"Usage: in=(\d+) out=(\d+) cache_write=(\S+) cache_read=(\S+)")),
    ("skip", re.compile(r"Claude chose to skip \(chat=(\S+?),")),
    ("redelivery", re.compile(r"Skip redelivered message \(chat=(\S+?),")),
    ("coldstart", re.compile(r"Starting new instance")),
    ("aborted", re.compile(r"request was aborted")),
    ("error", re.compile(r"ERROR|Claude API error|Tool \S+ failed")),
]


def fetch_logs(hours: int) -> list[dict]:
    raw = _gcloud([
        "logging", "read",
        f'resource.type=cloud_run_revision AND resource.labels.service_name={SERVICE}',
        f"--project={PROJECT}", "--limit=800",
        "--format=value(timestamp,textPayload)", f"--freshness={hours}h",
    ])
    out = []
    for line in raw.splitlines():
        parts = line.split("\t", 1)
        if len(parts) != 2 or not parts[1].strip():
            continue
        try:
            ts = datetime.fromisoformat(parts[0].replace("Z", "+00:00"))
        except ValueError:
            continue
        payload = parts[1].strip()
        for kind, pat in _LOG_PATTERNS:
            m = pat.search(payload)
            if m:
                out.append({"ts": ts, "kind": kind, "m": m, "payload": payload})
                break
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("chat_id")
    ap.add_argument("--hours", type=int, default=24)
    args = ap.parse_args()

    since = datetime.now(timezone.utc) - timedelta(hours=args.hours)
    msgs = fetch_messages(args.chat_id, since)
    logs = [
        e for e in fetch_logs(args.hours)
        if e["kind"] in ("coldstart", "aborted", "error")
        or args.chat_id in e["payload"]
        or e["kind"] in ("tool", "usage")  # not chat-tagged; shown for context
    ]

    events = sorted(msgs + logs, key=lambda e: e["ts"])
    print(f"=== Timeline chat={args.chat_id} last {args.hours}h (Asia/Taipei) ===")
    last_trigger_ts = None
    latencies = []
    stats = {"msgs": 0, "bot": 0, "skips": 0, "redeliveries": 0, "coldstarts": 0,
             "errors": 0, "cache_read": 0, "in": 0, "out": 0}
    for e in events:
        t = e["ts"].astimezone(TPE).strftime("%m-%d %H:%M:%S")
        if e["kind"] == "msg":
            stats["msgs"] += 1
            if e["user"] == "bot":
                stats["bot"] += 1
            marker = "" if e["type"] == "text" else f" <{e['type']}{'→GCS' if e['media'] else ''}>"
            text = e["text"].replace("\n", " / ")
            print(f"{t}  {'🤖' if e['user'] == 'bot' else '💬'} {e['user']}: {text}{marker}")
        elif e["kind"] == "trigger":
            last_trigger_ts = e["ts"]
            print(f"{t}     ⚡ trigger={e['m'].group(1)}")
        elif e["kind"] == "tool":
            print(f"{t}     🔧 {e['m'].group(1)} {e['m'].group(2)[:100]}")
        elif e["kind"] == "usage":
            i, o, cw, cr = e["m"].groups()
            stats["in"] += int(i); stats["out"] += int(o)
            stats["cache_read"] += int(cr) if cr.isdigit() else 0
            lat = ""
            if last_trigger_ts:
                secs = (e["ts"] - last_trigger_ts).total_seconds()
                if 0 <= secs < 300:
                    latencies.append(secs)
                    lat = f"  ⏱ {secs:.0f}s"
                last_trigger_ts = None
            print(f"{t}     📊 in={i} out={o} cache_r={cr}{lat}")
        elif e["kind"] == "skip":
            stats["skips"] += 1
            print(f"{t}     🤫 judged SKIP")
        elif e["kind"] == "redelivery":
            stats["redeliveries"] += 1
            print(f"{t}     🔁 redelivery skipped")
        elif e["kind"] == "coldstart":
            stats["coldstarts"] += 1
            print(f"{t}     ❄️ cold start")
        elif e["kind"] in ("aborted", "error"):
            stats["errors"] += 1
            print(f"{t}     🔥 {e['payload'][:160]}")

    print()
    print("=== Summary ===")
    print(f"messages: {stats['msgs']} (bot: {stats['bot']})  judged-SKIP: {stats['skips']}  "
          f"redeliveries: {stats['redeliveries']}  cold starts: {stats['coldstarts']}  errors: {stats['errors']}")
    if latencies:
        print(f"reply latency: median {sorted(latencies)[len(latencies)//2]:.0f}s, "
              f"max {max(latencies):.0f}s, n={len(latencies)}")
    print(f"tokens billed in={stats['in']} out={stats['out']}, cache_read={stats['cache_read']}")


if __name__ == "__main__":
    main()
