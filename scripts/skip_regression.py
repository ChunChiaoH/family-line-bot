"""Regression test for _SKIP_INSTRUCTION — run before deploying any change to it.

Replays a real family-group conversation (2026-07-16). A/B must reply (they were
production misses once); C/D must SKIP (guards against the prompt change
making the bot chatty). Each case runs 3x because judgments are sampled at
temperature — a case passes only if all runs agree. Live API calls (~$0.05).
"""
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv

load_dotenv(_ROOT / ".env")

from family_line_bot.config import _DEFAULT_PERSONA
from family_line_bot.services.claude import ClaudeService

svc = ClaudeService(
    api_key=os.environ["ANTHROPIC_API_KEY"],
    model="claude-sonnet-4-6",
    system_prompt=_DEFAULT_PERSONA,
)

BASE = (
    "Recent conversation (last 2 hours):\n"
    "Joe: @Bot 台灣天氣怎麼樣\n"
    "bot: 最近都很熱啦，夏天嘛 😅 今天有沒有下雨看你在哪？\n\n"
    "台北的話這幾天都高溫悶熱，有時午後雷陣雨，出門記得帶傘。你現在在台灣嗎？\n\n"
)

CASES = [
    ("A 吐槽bot(要回)", BASE, "阿華: 這個AI講話太台了吧", True),
    ("B 回答bot問題(要回)", BASE + "阿華: 這個AI講話太台了吧\n", "Joe: 我在澳洲雪梨 但是 @阿華 應該是在台北", True),
    ("C 家人互聊(該SKIP)", BASE + "Joe: 我在澳洲雪梨 但是 @阿華 應該是在台北\n", "阿華: 晚餐要吃什麼", False),
    ("D 無關新話題(該SKIP)", BASE, "Joe: 我下週開始要去健身房報到", False),
]

RUNS = 3
fails = 0
for name, ctx, query, should_reply in CASES:
    votes = []
    sample = None
    for _ in range(RUNS):
        reply = svc.ask_text(context=ctx, query=query, quoted="", quoted_image=None, may_skip=True)
        votes.append(reply is not None)
        if reply:
            sample = reply
    hits = sum(1 for v in votes if v == should_reply)
    ok = hits == RUNS
    fails += 0 if ok else 1
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  ({hits}/{RUNS} 符合預期)")
    print(f"       -> {sample if sample else '(SKIP)'}")
print()
print("RESULT:", "ALL PASS (3/3 each)" if fails == 0 else f"{fails} case(s) not unanimous")
