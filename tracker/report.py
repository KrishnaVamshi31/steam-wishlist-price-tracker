"""Markdown report of current prices, deals and gathered sale intel."""
from datetime import datetime, timedelta, timezone

from . import config, db


def money(paise, symbol="\u20b9") -> str:
    if paise is None:
        return "—"
    return f"{symbol}{paise / 100:,.0f}"


def _rows(conn):
    """Every tracked game with its latest price and all-time recorded low."""
    return conn.execute(
        """
        SELECT g.appid, g.name, g.publishers, g.release_date, g.coming_soon, g.on_wishlist,
               p.final, p.initial, p.discount_percent, p.ts AS price_ts,
               (SELECT MIN(final) FROM price_history WHERE appid = g.appid) AS low
        FROM games g
        LEFT JOIN price_history p ON p.id = (
            SELECT id FROM price_history WHERE appid = g.appid ORDER BY ts DESC LIMIT 1
        )
        WHERE g.on_wishlist = 1
        ORDER BY p.discount_percent DESC, g.name
        """
    ).fetchall()


def build(cfg: dict | None = None) -> str:
    cfg = cfg or config.load()
    sym = cfg.get("currency_symbol", "\u20b9")
    out = []
    with db.connect() as conn:
        rows = _rows(conn)
        last_run = db.get_meta(conn, "last_run", "never")
        since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        alerts = conn.execute(
            "SELECT a.*, g.name AS game FROM alerts a LEFT JOIN games g ON g.appid=a.appid"
            " WHERE a.ts > ? ORDER BY a.ts DESC",
            (since,),
        ).fetchall()
        notes = conn.execute(
            "SELECT n.*, g.name AS game FROM notes n LEFT JOIN games g ON g.appid=n.appid"
            " WHERE n.ts > ? ORDER BY n.ts DESC",
            (since,),
        ).fetchall()

        on_sale = [r for r in rows if (r["discount_percent"] or 0) > 0]
        total_now = sum(r["final"] or 0 for r in rows)
        total_full = sum(r["initial"] or r["final"] or 0 for r in rows)

        out.append("# Steam Wishlist Price Report")
        out.append("")
        out.append(f"_Generated {datetime.now():%d %b %Y, %H:%M} · last price check: {last_run}_")
        out.append("")
        out.append(f"- **{len(rows)}** games tracked")
        out.append(f"- **{len(on_sale)}** currently discounted")
        out.append(f"- Buying everything today: **{money(total_now, sym)}** "
                   f"(full price {money(total_full, sym)}, saving {money(total_full - total_now, sym)})")
        out.append("")

        if on_sale:
            out.append("## On sale right now")
            out.append("")
            out.append(f"| Game | Now | Was | Off | Lowest seen |")
            out.append("|---|---|---|---|---|")
            for r in on_sale:
                flag = " ⭐" if r["final"] is not None and r["final"] <= (r["low"] or 1 << 60) else ""
                out.append(
                    f"| [{r['name']}](https://store.steampowered.com/app/{r['appid']}/){flag} "
                    f"| **{money(r['final'], sym)}** | {money(r['initial'], sym)} "
                    f"| {r['discount_percent']}% | {money(r['low'], sym)} |"
                )
            out.append("")
            out.append("⭐ = at or below the lowest price this tracker has recorded.")
            out.append("")

        if alerts:
            out.append("## Alerts this week")
            out.append("")
            for a in alerts:
                when = a["ts"][:16].replace("T", " ")
                out.append(f"- `{when}` **{a['title']}** — {a['body']}")
            out.append("")

        out.append("## Upcoming sales & discount intel")
        out.append("")
        if notes:
            for n in notes:
                scope = f"**{n['game']}**" if n["game"] else "**All wishlist**"
                window = ""
                if n["starts"]:
                    window = f" _({n['starts']}" + (f" → {n['ends']}" if n["ends"] else "") + ")_"
                link = f" [[source]]({n['url']})" if n["url"] else ""
                out.append(f"- {scope}: {n['headline']}{window}{link}")
                if n["body"]:
                    out.append(f"  - {n['body']}")
            out.append("")
        else:
            out.append("_No sale intel gathered yet. Run the daily research task to populate this._")
            out.append("")

        out.append("## Full wishlist")
        out.append("")
        out.append("| Game | Price | Off | Lowest seen | Released |")
        out.append("|---|---|---|---|---|")
        for r in sorted(rows, key=lambda x: (x["name"] or "").lower()):
            released = "Unreleased" if r["coming_soon"] else (r["release_date"] or "—")
            disc = f"{r['discount_percent']}%" if (r["discount_percent"] or 0) > 0 else "—"
            out.append(
                f"| [{r['name']}](https://store.steampowered.com/app/{r['appid']}/) "
                f"| {money(r['final'], sym)} | {disc} | {money(r['low'], sym)} | {released} |"
            )
        out.append("")

    return "\n".join(out)


def write(cfg: dict | None = None) -> str:
    cfg = cfg or config.load()
    text = build(cfg)
    config.REPORTS_DIR.mkdir(exist_ok=True)
    latest = config.REPORTS_DIR / "latest.md"
    latest.write_text(text, encoding="utf-8")
    dated = config.REPORTS_DIR / f"{datetime.now():%Y-%m-%d}.md"
    dated.write_text(text, encoding="utf-8")
    return str(latest)
