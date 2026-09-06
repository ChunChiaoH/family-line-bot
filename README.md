# Family LINE Bot

A Claude-powered member of a family LINE group. I built it for my own family — a Taiwanese
family spread across time zones — so the bot's replies, its persona, and the `wiki/` design
log are all in Traditional Chinese by design. That is not an oversight; it is what the
artefact actually is. The `wiki/` directory is also an experiment in LLM-maintained
documentation (a Karpathy-style "LLM wiki"): an agent writes and lints it under the rules in
[CLAUDE.md](CLAUDE.md), recording the reasoning that the code itself cannot show.

The bot lives in the group chat like a person does: it stays quiet most of the time, answers
when spoken to, remembers what matters, and can look things up. It runs on Cloud Run for
roughly the price of the Anthropic tokens it burns.

## Highlights

**Hybrid triggering — knowing when *not* to speak.** In a family group, interrupting is
ruder than silence. Three layers ([wiki/triggering.md](wiki/triggering.md)):

- **Hard triggers, always reply**: an `@`-mention, or a quote-reply to one of the bot's own
  messages (LINE has no threads, so quote-reply is the clearest "still talking to you" signal).
  1:1 chat counts as a mention.
- **Soft trigger, model judgement**: messages arriving within `SESSION_WINDOW_MINUTES` of the
  bot's last reply are sent to Claude with a `SKIP` instruction — "is this being said *to* me?"
  If not, the model answers `SKIP` and nothing is posted. Only real replies extend the window;
  SKIPs do not.
- **Everything else**: logged to context, never answered.

The SKIP prompt is a see-saw — pushing it to be more responsive makes the bot butt in, pushing
it back makes it miss questions aimed at it. So changes to it are gated by
`scripts/skip_regression.py`: five real production scenarios (three must reply, two must SKIP),
three votes each, unanimous or it fails. Two findings worth stealing: when rules lose to the
model, a **concrete example** wins where three rewordings failed; and judgement and generation
share one API call, so `temperature` was dropped to 0.3 — at 1.0 a borderline case flipped from
3/3 to 1/3 on a few words of persona change.

**Long-term memory without RAG.** Memory is a set of per-group Markdown files that Claude edits
itself through Anthropic's first-party memory tool (`memory_20250818`) — create, str_replace,
insert, delete, rename. Reads and writes are split: the *whole* knowledge base is rendered into
the system prompt (zero extra calls, zero latency, and passive facts like allergies always
apply — on-demand retrieval misses the cases where the model doesn't know to look), while
writes go through the tool and are rare. At a few KB, injecting everything is both cheaper and
more accurate than retrieving; a hard 4,000-character cap plus the model's own housekeeping
keeps it that way, and the upgrade path if it grows is index-in-prompt + detail-on-demand, not
a vector DB. Rationale and the trigger conditions for revisiting it:
[wiki/memory-design.md](wiki/memory-design.md), decision D3.

One deliberate counter-measure: attaching the memory tool makes the API inject a "view your
memory directory first" protocol, costing 1–2 extra round trips per message. The system prompt
explicitly counter-instructs it ("the content is already attached, don't view"), verified live.

**Hand-written tool-use loop.** Not the SDK's Tool Runner — the Python Tool Runner does not
auto-continue a `pause_turn`, so a long `web_search` gets silently truncated into a partial
answer. The loop in `services/claude.py` handles `pause_turn`, `tool_use`, per-tool exceptions
(returned as `is_error` tool results so the model can adapt) and caps at 5 iterations. Tools
today: Anthropic server-side `web_search`, the memory tool, and `search_thsr` — Taiwan High
Speed Rail timetable, fares and seat availability via the official TDX API.

**Media at zero token cost.** Photos and videos are never sent to Claude on arrival — the bot
just caches the bytes/thumbnail, persists them to GCS and logs the message. Media only enters a
request when a family member quote-replies it with a question. The persona explains this
mechanism to the family, because in production someone asked "can you see this photo?" without
quoting, got "I can't see images", and concluded the bot was blind.

**Security and cost posture.** The only inbound surface is `POST /webhook/<random-token>`,
protected by three layers: LINE HMAC signature verification, the random path token
(defense-in-depth against scanners, not a credential), and a group whitelist — chat IDs live in
an env var and never in the repo. Credentials sit in Secret Manager; Cloud Run runs as a
dedicated least-privilege service account (`datastore.user`, per-secret `secretAccessor`,
`objectAdmin` on one bucket) rather than the default compute SA; `--max-instances=1` both caps
cost and is a correctness precondition for the in-process per-chat locks. Firestore has PITR
and delete protection; the media bucket has versioning. Running cost for a family-sized group
is a few dollars a month of Anthropic tokens (prompt caching on, `web_search` capped at 3 uses
per turn); the GCP bill rounds to zero inside free tiers. Details:
[wiki/operations.md](wiki/operations.md).

## Architecture

```
Family's LINE app  →  LINE Platform
                          │  HTTPS POST /webhook/<random-token> + X-Line-Signature
                          ▼
Cloud Run: <service>  (asia-east1, max-instances=1)
  app.py ── HMAC signature verification → group whitelist
     │
     ├─ handlers/text.py    trigger decision → Claude reply
     ├─ handlers/image.py   fetch + cache bytes, log (optional auto-describe in 1:1)
     ├─ handlers/video.py   fetch + cache thumbnail, log (no Claude call)
     │
     ├─ store.py / firestore_store.py   messages + session state + memory files
     ├─ services/claude.py              tool-use loop → Anthropic API
     ├─ services/memory.py              memory-tool backend (per-group markdown)
     ├─ services/media.py               GCS persistence
     └─ services/line_client.py         reply / fetch content / display names → LINE API
```

Firestore layout:

```
chats/{chat_id}                        → last_bot_ts
chats/{chat_id}/messages/{msg_id}      → ts, user, text, type, media_path
chats/{chat_id}/memory_files/{file}    → content, updated
```

`ChatStore` (in-memory, local dev) and `FirestoreChatStore` (production) implement the same
interface, so swapping the backend means implementing one set of methods. Image and video
bytes never go into Firestore (1 MB document limit) — they go to GCS, with a process-local
cache in front. See [wiki/architecture.md](wiki/architecture.md).

## Quick start

**The intended path: open this repo in [Claude Code](https://claude.com/claude-code) and run
`/setup`.** Claude checks your prerequisites, asks for the handful of credentials it needs
(LINE channel secret + access token, Anthropic API key, GCP project ID, region; TDX keys
optional), writes them to a gitignored `.env`, runs the deploy, registers the LINE webhook,
reads the Cloud Run logs to find your group's chat ID, whitelists it, and verifies end to end.
It never echoes secrets back into the chat.

Doing it by hand, roughly:

1. `cp .env.example .env` and fill it in — see [.env.example](.env.example) for every key;
   generate `WEBHOOK_PATH_TOKEN` with `python -c "import secrets; print(secrets.token_urlsafe(24))"`.
2. Create a GCP project with billing on, enable Run / Cloud Build / Artifact Registry /
   Secret Manager / Firestore / Storage, and create a Firestore database (Native mode) in
   your region.
3. Follow [.claude/commands/deploy.md](.claude/commands/deploy.md) — idempotent; first run
   creates the runtime service account, secrets, media bucket and Cloud Run service.
4. `python scripts/set_webhook.py <cloud-run-url>` to point LINE at the deployment and verify
   it; in the LINE channel's Messaging API settings, turn **off** auto-reply messages and
   **on** "Use webhook".
5. Add the bot to your group, send a message, find `Trigger: none (chat=Cxxx)` in the Cloud Run
   logs, then
   `gcloud run services update <service> --region=<region> --update-env-vars='^;^ALLOWED_CHAT_IDS=Cxxx'`
   (the `^;^` delimiter is required — the value contains commas).

Local development needs no GCP at all: `pip install -e .`, leave `USE_FIRESTORE=false`, run
`python main.py`, and `GET /health` should return `{"status": "ok"}`.

## Project layout

```
family_line_bot/
  app.py              FastAPI app, webhook route, signature check, whitelist
  config.py           Settings (all env vars) + the default Traditional-Chinese persona
  store.py            in-memory ChatStore (local dev)
  firestore_store.py  production store, same interface
  handlers/           text.py (triggering) · image.py · video.py
  services/           claude.py (tool loop) · memory.py · media.py · thsr.py · line_client.py
scripts/
  set_webhook.py      point LINE at a URL and verify it
  review_chat.py      unified timeline: Firestore messages × Cloud Run logs
  skip_regression.py  regression gate for the SKIP instruction
  migrate_memory.py   one-off memory migration
.claude/commands/     setup · deploy · add-tool · review-bot
wiki/                 LLM-maintained design log (Chinese)
Dockerfile · main.py · pyproject.toml
```

## Built with Claude Code

This repo was pair-programmed with Claude Code, and the collaboration is part of the design —
not just how the code got typed.

- **`.claude/commands/` holds the operational skills.** `/setup` onboards a stranger's own
  copy, `/deploy` is the idempotent Cloud Run procedure, `/add-tool` scaffolds a new tool
  along the existing seams, `/review-bot` runs the improvement loop. They are written for
  Claude to execute, which is why they are terse and command-first.
- **`wiki/` is an LLM-maintained knowledge base.** Per CLAUDE.md, the agent ingests a page
  update plus an append-only log entry after every meaningful change, cross-links pages with
  `[[wiki-links]]`, and occasionally lints for claims that the code has outgrown. The rule for
  what belongs there: only things the code cannot tell you — reasons, trade-offs, rejected
  alternatives, where the tuning knobs are, and lessons learned the hard way.
- **The improvement loop is evidence-first with a regression gate.** `/review-bot` pulls
  `scripts/review_chat.py <chat_id> --hours 24`, a single chronological view of family
  messages against trigger decisions, tool calls, token usage, latency, cold starts and
  errors. Findings become prompt changes; prompt changes must pass
  `scripts/skip_regression.py`; the change is then written into the wiki; and deployment is
  the human's call. That loop is how the SKIP instruction and the persona reached their
  current state — every tuning decision in `wiki/triggering.md` came from a real miss in a
  real conversation, not from a benchmark.

## Tech stack

Python 3.12 · FastAPI · line-bot-sdk v3 · Anthropic Python SDK (Claude Sonnet, server-side
`web_search` + first-party memory tool) · Firestore · Cloud Storage · Secret Manager ·
Cloud Run · Cloud Build.

## License

MIT — see [LICENSE](LICENSE).
