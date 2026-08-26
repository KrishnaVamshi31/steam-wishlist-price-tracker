"""Automated daily sale research: web search + note recording, via Claude.

This is DAILY_TASK.md's research step turned into code, for the scheduled GitHub
Actions workflow where there's no live agent session to run the prompt by hand.
Unlike chat.py there's no user to stream text to or approve tool calls, so this
runs non-streaming and just returns a short plain-text summary for Telegram.

Web search is Anthropic's server-side tool: the API resolves it internally (possibly
several times) before ever handing control back to us, so the only tool we execute
ourselves is `record_note`, exactly like chat.py's query_database round trip.
"""
import json
from datetime import date

import anthropic

from . import config, db, notify

MODEL = "claude-opus-5"
MAX_TOKENS = 4000
EFFORT = "medium"
MAX_ROUNDS = 8
MAX_NOTES = 8

SYSTEM = f"""You are the daily research assistant for a personal Steam wishlist price
tracker. Search the web for upcoming Steam sales and publisher discounts relevant to
the tracked games below, then record anything genuinely useful with `record_note`.

Cover:
- Confirmed dates for the next Steam-wide seasonal sale or themed fest in the next
  ~6 weeks that would include a wishlist game.
- Publisher sales, bundle appearances, or announced discounts for the most
  expensive/most-wanted tracked games (the list you're given).
- Non-Steam sales (Epic, GOG, Fanatical, Humble, GreenManGaming) for the same games,
  if a key from there activates on Steam.
- A game that has never discounted, or rarely does — that's worth noting plainly.

Rules:
- Prefer primary sources (Steam news, publisher posts, SteamDB) over aggregator
  blogs. If sources disagree on a date, say it's unconfirmed rather than picking one.
- Only call record_note for something new and useful — skip anything already in
  "Recent notes" below, don't restate it.
- Call record_note at most {MAX_NOTES} times.
- When done researching, reply with a short plain-text summary (2-5 sentences) of
  what's worth telling the user about. If nothing is worth flagging, reply with
  exactly: NOTHING TO REPORT
"""

TOOLS = [
    {"type": "web_search_20250305", "name": "web_search", "max_uses": 12},
    {
        "name": "record_note",
        "description": "Record one piece of sale intel so it shows up in the dashboard and report.",
        "input_schema": {
            "type": "object",
            "properties": {
                "headline": {"type": "string"},
                "body": {"type": "string", "description": "1-2 sentence detail."},
                "game": {
                    "type": "string",
                    "description": "Tracked game name this applies to (partial match ok). "
                    "Omit if it applies to the whole wishlist.",
                },
                "source": {"type": "string"},
                "url": {"type": "string"},
                "starts": {"type": "string", "description": "ISO date the sale starts, if known."},
                "ends": {"type": "string", "description": "ISO date the sale ends, if known."},
            },
            "required": ["headline"],
            "additionalProperties": False,
        },
    },
]


def configured() -> bool:
    return bool(config.secret("ANTHROPIC_API_KEY"))


def _record_note(conn, payload: dict) -> str:
    appid = None
    game = (payload.get("game") or "").strip()
    if game:
        row = conn.execute(
            "SELECT appid FROM games WHERE name LIKE ? ORDER BY LENGTH(name) LIMIT 1",
            (f"%{game}%",),
        ).fetchone()
        appid = row["appid"] if row else None
    db.add_note(
        conn,
        headline=(payload.get("headline") or "")[:300],
        body=payload.get("body") or "",
        source=payload.get("source") or "",
        url=payload.get("url") or "",
        appid=appid,
        starts=payload.get("starts"),
        ends=payload.get("ends"),
    )
    return json.dumps({"ok": True})


def run() -> str:
    """Research today's sale news and record it. Returns a short summary, or "" if
    nothing was worth reporting (or no API key is configured)."""
    if not configured():
        return ""

    client = anthropic.Anthropic(api_key=config.secret("ANTHROPIC_API_KEY"))

    with db.connect() as conn:
        games = conn.execute(
            """SELECT g.name, p.final FROM games g LEFT JOIN price_history p ON p.id = (
                   SELECT id FROM price_history WHERE appid=g.appid ORDER BY ts DESC LIMIT 1)
               WHERE g.on_wishlist=1 ORDER BY p.final DESC LIMIT 10"""
        ).fetchall()
        recent = conn.execute(
            "SELECT headline FROM notes WHERE ts > datetime('now', '-14 days')"
            " ORDER BY ts DESC LIMIT 20"
        ).fetchall()

    game_list = "\n".join(f"- {g['name']}" for g in games) or "(none tracked yet)"
    recent_list = "\n".join(f"- {n['headline']}" for n in recent) or "(none)"

    messages = [
        {
            "role": "user",
            "content": (
                f"Today is {date.today():%d %B %Y}.\n\n"
                f"Most expensive/most-wanted tracked games:\n{game_list}\n\n"
                f"Recent notes (don't repeat these):\n{recent_list}"
            ),
        }
    ]

    for _ in range(MAX_ROUNDS):
        resp = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM,
            tools=TOOLS,
            output_config={"effort": EFFORT},
            messages=messages,
        )
        messages.append({"role": "assistant", "content": resp.content})

        client_calls = [b for b in resp.content if b.type == "tool_use" and b.name == "record_note"]

        if not client_calls:
            text = "".join(b.text for b in resp.content if b.type == "text").strip()
            return "" if text.upper().startswith("NOTHING TO REPORT") else text

        results = []
        with db.connect() as conn:
            for block in client_calls:
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": _record_note(conn, dict(block.input)),
                    }
                )
        messages.append({"role": "user", "content": results})

    return "Research stopped after several rounds — check reports/latest.md for what was recorded."


def run_and_notify() -> str:
    """Run research and, if anything was found, push it to Telegram too."""
    summary = run()
    if summary and notify.telegram_configured():
        notify.send_telegram(f"<b>\U0001f50d Sale research</b>\n\n{notify.esc(summary)}")
    return summary
