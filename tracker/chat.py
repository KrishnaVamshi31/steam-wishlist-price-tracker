"""Claude-powered chat over the price database.

The model gets three tools: read-only SQL against the tracker's own SQLite file,
the buy-or-wait cadence model, and the sale calendar. SQL is the flexible one —
the other two expose reasoning that cannot be expressed as a query.

Streaming uses the manual agentic loop rather than the SDK tool runner, because the
UI needs per-token text as it arrives *and* control between tool rounds.
"""
import json
import sqlite3
from collections.abc import Callable, Iterator
from datetime import date

import anthropic

from . import advisor, config, db, insight, salecalendar

MODEL = "claude-opus-5"
MAX_TOKENS = 8000
# These are lookups over a small database, not deep reasoning — the cadence model
# already does the hard thinking deterministically. Low effort keeps chat snappy.
EFFORT = "low"
MAX_TOOL_ROUNDS = 6
MAX_ROWS = 60

SCHEMA_NOTE = """
Tables available (SQLite):

  games(appid, name, type, publishers, developers, release_date,
        coming_soon, is_free, on_wishlist, first_seen, last_seen)
      on_wishlist = 1 means actively tracked.

  price_history(id, appid, ts, initial, final, discount_percent, currency)
      One row per observed price CHANGE, not per day. ts is an ISO-8601 UTC string.
      `initial` is the full price, `final` is what you actually pay.
      PRICES ARE IN PAISE — divide by 100 for rupees. 110000 means Rs 1,100.

  itad_history(appid, ts, price, regular, cut)
      Imported IsThereAnyDeal history, same paise convention. May be empty.

  notes(id, appid, ts, source, headline, url, body, starts, ends)
      Sale intel gathered from the web. appid NULL = applies to the whole wishlist.

  alerts(id, appid, ts, kind, title, body, notified)
      kind is one of: discount, record_low, target_hit, free.
"""

SYSTEM = f"""You are the assistant built into a personal Steam wishlist price tracker.
You answer questions about the user's tracked games, their prices, price history,
and whether now is a good time to buy.

{SCHEMA_NOTE}

How to work:
- Use `query_database` for anything factual about prices, games, or history.
- Use `get_buy_advice` when asked whether to buy, wait, or what is worth getting.
  It runs a discount-cadence model that reasons about how deeply and often each
  game discounts versus the sale calendar. Do not try to reproduce it in SQL.
- Use `get_sale_calendar` for upcoming Steam sale dates.

Rules:
- Always convert paise to the user's currency before showing a number. Never show
  a raw paise value.
- The tracker records prices only from when it was set up, so its own history is
  short unless IsThereAnyDeal data has been imported. If someone asks about
  long-term trends and there is little history, say so plainly instead of
  extrapolating from two data points.
- Be concise and concrete. Lead with the answer, then the reasoning.
- If a query returns nothing, say so — never invent games, prices or dates.
"""

TOOLS = [
    {
        "name": "query_database",
        "description": (
            "Run a read-only SQL SELECT against the price tracker database and get "
            "rows back as JSON. Use this for any factual question about games, "
            "prices, discounts or history. Only SELECT is permitted."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "A single SQLite SELECT statement.",
                },
                "purpose": {
                    "type": "string",
                    "description": "Short note on what this query is for.",
                },
            },
            "required": ["sql"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_buy_advice",
        "description": (
            "Run the discount-cadence model over tracked games and return buy-now / "
            "wait verdicts with reasoning, confidence and expected savings. Use this "
            "for any 'should I buy', 'is this a good deal', or 'what's worth getting' "
            "question."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "game": {
                    "type": "string",
                    "description": "Optional game name (partial match) to limit to one game.",
                }
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_sale_calendar",
        "description": "Upcoming Steam seasonal sales and themed fests, with dates.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
]

FORBIDDEN = (
    "insert", "update", "delete", "drop", "alter", "create", "replace",
    "attach", "detach", "pragma", "vacuum", "reindex",
)


def configured() -> bool:
    return bool(config.secret("ANTHROPIC_API_KEY"))


def _readonly_conn() -> sqlite3.Connection:
    """Open the database in true read-only mode.

    Belt and braces: the SQL is validated below, but opening with mode=ro means
    even a validation bypass cannot modify anything.
    """
    conn = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def run_query(sql: str) -> str:
    """Execute a validated read-only SELECT and return JSON rows."""
    cleaned = sql.strip().rstrip(";").strip()
    lowered = cleaned.lower()

    if not lowered.startswith(("select", "with")):
        return json.dumps({"error": "Only SELECT queries are allowed."})
    if ";" in cleaned:
        return json.dumps({"error": "Only one statement per query."})
    if any(f"{word} " in f" {lowered} " for word in FORBIDDEN):
        return json.dumps({"error": "That statement type is not permitted."})

    try:
        with _readonly_conn() as conn:
            rows = conn.execute(cleaned).fetchmany(MAX_ROWS)
            data = [dict(r) for r in rows]
    except sqlite3.Error as exc:
        return json.dumps({"error": f"SQL error: {exc}"})

    return json.dumps(
        {"row_count": len(data), "rows": data, "truncated": len(data) == MAX_ROWS},
        default=str,
    )


def run_advice(game: str | None = None) -> str:
    cfg = config.load()
    with db.connect() as conn:
        verdicts = insight.advise_all(conn, cfg, use_itad=False)
    if game:
        needle = game.lower()
        verdicts = [v for v in verdicts if needle in (v.name or "").lower()]
        if not verdicts:
            return json.dumps({"error": f"No tracked game matching '{game}'."})
    return json.dumps(
        [
            {
                "game": v.name,
                "verdict": v.action,
                "confidence": v.confidence,
                "summary": v.headline,
                "price": (v.current_price or 0) / 100,
                "discount_percent": v.current_cut,
                "best_discount_seen": v.best_cut,
                "typical_discount": v.typical_cut,
                "expected_extra_saving": v.expected_savings / 100,
                "reasons": v.reasons,
                "history_days": v.span_days,
                "past_sales_observed": v.episodes,
            }
            for v in verdicts[:25]
        ],
        default=str,
    )


def run_calendar() -> str:
    today = date.today()
    with db.connect() as conn:
        windows = salecalendar.load(conn)
    return json.dumps(
        [
            {
                "name": w.name,
                "starts": w.start.isoformat(),
                "ends": w.end.isoformat(),
                "days_away": (w.start - today).days,
                "store_wide": w.major,
                "running_now": w.active_on(today),
            }
            for w in windows
            if w.end >= today
        ]
    )


def execute_tool(name: str, payload: dict) -> str:
    if name == "query_database":
        return run_query(payload.get("sql", ""))
    if name == "get_buy_advice":
        return run_advice(payload.get("game"))
    if name == "get_sale_calendar":
        return run_calendar()
    return json.dumps({"error": f"Unknown tool {name}"})


def stream_reply(
    messages: list,
    on_tool: Callable[[str, dict], None] | None = None,
) -> Iterator[str]:
    """Stream Claude's answer, running tools as needed.

    `messages` is the full API history and is appended to in place, so the caller
    keeps the tool_use / tool_result blocks needed for follow-up turns.
    """
    if not configured():
        yield "No Anthropic API key set. Add one in **Settings → Connections** to use chat."
        return

    client = anthropic.Anthropic(api_key=config.secret("ANTHROPIC_API_KEY"))
    cfg = config.load()
    system = SYSTEM + f"\nThe user's currency symbol is {cfg.get('currency_symbol', '₹')}."

    for _ in range(MAX_TOOL_ROUNDS):
        with client.messages.stream(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=system,
            tools=TOOLS,
            output_config={"effort": EFFORT},
            messages=messages,
        ) as stream:
            for text in stream.text_stream:
                yield text
            final = stream.get_final_message()

        messages.append({"role": "assistant", "content": final.content})

        if final.stop_reason == "refusal":
            yield "\n\n_I can't help with that one._"
            return
        if final.stop_reason != "tool_use":
            return

        results = []
        for block in final.content:
            if block.type != "tool_use":
                continue
            if on_tool:
                on_tool(block.name, dict(block.input))
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": execute_tool(block.name, dict(block.input)),
                }
            )
        messages.append({"role": "user", "content": results})

    yield "\n\n_Stopped after several tool rounds — try a narrower question._"
