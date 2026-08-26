"""Main price check: sync wishlist, fetch prices, detect drops, notify."""
from datetime import datetime

from . import config, db, notify, steam


def money(paise, symbol="\u20b9") -> str:
    if paise is None:
        return "n/a"
    return f"{symbol}{paise / 100:,.0f}"


def sync_wishlist(conn, cfg: dict) -> list[int]:
    """Pull the wishlist and reconcile it with the local games table."""
    extra = [int(a) for a in cfg.get("extra_appids", [])]
    wishlist_ids: list[int] = []

    if cfg.get("steam_profile"):
        steamid = steam.resolve_steamid(cfg["steam_profile"])
        db.set_meta(conn, "steamid", steamid)
        wishlist_ids = [i["appid"] for i in steam.fetch_wishlist(steamid)]
    elif extra:
        print("  No steam_profile set — tracking only the appids in extra_appids.")
    else:
        raise steam.SteamError(
            "Set 'steam_profile' in config.json to your Steam profile URL, "
            "or add appids to 'extra_appids'."
        )

    appids = list(dict.fromkeys(wishlist_ids + extra))

    if not wishlist_ids and cfg.get("steam_profile"):
        known = conn.execute("SELECT appid FROM games WHERE on_wishlist=1").fetchall()
        if known:
            # Don't wipe a good DB because of one bad fetch or a privacy toggle.
            print(
                "  ! Wishlist came back empty but we have games on record — "
                "keeping them. Check that your wishlist is set to Public."
            )
            appids = list(dict.fromkeys([r["appid"] for r in known] + extra))
        else:
            print(
                "  ! Wishlist is empty or private. In Steam: Profile > Edit Profile > "
                "Privacy Settings > Game details = Public."
            )

    # on_wishlist really means "actively tracked": the live wishlist plus extra_appids.
    if wishlist_ids:
        conn.execute("UPDATE games SET on_wishlist=0")
    if appids:
        conn.executemany(
            "UPDATE games SET on_wishlist=1 WHERE appid=?", [(a,) for a in appids]
        )

    # Fetch metadata only for games we've never seen.
    known = {r["appid"] for r in conn.execute("SELECT appid FROM games").fetchall()}
    new = [a for a in appids if a not in known]
    if new:
        print(f"  Fetching details for {len(new)} new game(s)...")
    for appid in new:
        meta = steam.fetch_meta(appid, cfg["country_code"])
        db.upsert_game(conn, appid, meta, on_wishlist=True)
        print(f"    + {meta.get('name')}")

    return appids


def evaluate(conn, cfg: dict, appids: list[int]) -> list[dict]:
    """Fetch current prices and return the list of events worth telling the user about."""
    symbol = cfg.get("currency_symbol", "\u20b9")
    targets = {int(k): float(v) for k, v in (cfg.get("target_prices") or {}).items()}
    min_disc = int(cfg.get("min_discount_percent", 20))
    low_floor = int(cfg.get("record_low_min_discount", 10))
    min_history = int(cfg.get("record_low_min_history", 3))

    prices = steam.fetch_prices(appids, cfg["country_code"])
    print(f"  Got prices for {len(prices)}/{len(appids)} games.")

    events = []
    for appid, price in prices.items():
        row = conn.execute("SELECT * FROM games WHERE appid=?", (appid,)).fetchone()
        name = row["name"] if row else f"App {appid}"

        prev = db.last_price(conn, appid)
        prev_low = db.record_low(conn, appid)          # before inserting today's price
        prev_final = prev["final"] if prev else None
        prev_disc = prev["discount_percent"] if prev else 0

        changed = db.add_price(conn, appid, price)
        final = price["final"]
        disc = price.get("discount_percent") or 0

        kinds = []
        target = targets.get(appid)
        if target is not None and final <= target * 100:
            # Only fire once per crossing, not every day it stays cheap.
            if prev_final is None or prev_final > target * 100:
                kinds.append(("target_hit", f"hit your target of {money(target * 100, symbol)}"))

        if cfg.get("alert_on_record_low", True) and disc >= low_floor:
            # "Record low" is meaningless until we have some history to compare against,
            # otherwise every first-ever discount looks like an all-time low.
            seen = conn.execute(
                "SELECT COUNT(*) c FROM price_history WHERE appid=?", (appid,)
            ).fetchone()["c"]
            if seen >= min_history and prev_low is not None and final < prev_low:
                kinds.append(("record_low", f"lowest price we've ever seen (was {money(prev_low, symbol)})"))

        if disc >= min_disc and disc > prev_disc:
            kinds.append(("discount", f"{disc}% off"))

        if final == 0 and prev_final not in (0, None):
            kinds.append(("free", "is free right now"))

        for kind, reason in kinds:
            events.append(
                {
                    "appid": appid,
                    "name": name,
                    "kind": kind,
                    "reason": reason,
                    "final": final,
                    "initial": price.get("initial"),
                    "discount": disc,
                    "url": steam.store_url(appid),
                }
            )

        if changed and not kinds and prev_final is not None:
            direction = "down" if final < prev_final else "up"
            print(f"    ~ {name}: {money(prev_final, symbol)} -> {money(final, symbol)} ({direction})")

    # One alert per game: strongest signal wins.
    rank = {"target_hit": 0, "record_low": 1, "free": 2, "discount": 3}
    best: dict[int, dict] = {}
    for e in events:
        cur = best.get(e["appid"])
        if cur is None or rank[e["kind"]] < rank[cur["kind"]]:
            best[e["appid"]] = e
    return sorted(best.values(), key=lambda e: (rank[e["kind"]], -e["discount"]))


def announce(conn, cfg: dict, events: list[dict]) -> None:
    """Persist alerts and push them to Telegram / Windows toast."""
    if not events:
        return
    symbol = cfg.get("currency_symbol", "\u20b9")
    channels = cfg.get("notify", {})
    alert_ids = []

    lines = ["<b>\U0001f3ae Steam wishlist price drops</b>", ""]
    for e in events:
        was = money(e["initial"], symbol)
        now_ = money(e["final"], symbol)
        title = f"{e['name']} — {e['reason']}"
        body = f"{now_} (was {was})"
        alert_ids.append(db.add_alert(conn, e["appid"], e["kind"], title, body))

        tag = {"target_hit": "\U0001f3af", "record_low": "\U0001f4c9",
               "free": "\U0001f381", "discount": "\U0001f4b8"}[e["kind"]]
        lines.append(
            f'{tag} <a href="{e["url"]}">{notify.esc(e["name"])}</a>\n'
            f'   <b>{now_}</b> <s>{was}</s> — {notify.esc(e["reason"])}'
        )

    if channels.get("telegram", True) and notify.telegram_configured():
        if notify.send_telegram("\n".join(lines)):
            print(f"  Telegram: sent {len(events)} alert(s).")

    if channels.get("toast", True):
        head = events[0]
        extra = f" (+{len(events) - 1} more)" if len(events) > 1 else ""
        notify.send_toast(
            f"{head['name']} — {head['reason']}{extra}",
            f"{money(head['final'], symbol)} (was {money(head['initial'], symbol)})",
            url=head["url"],
        )

    db.mark_notified(conn, alert_ids)


def run(cfg: dict | None = None) -> list[dict]:
    cfg = cfg or config.load()
    print(f"[{datetime.now():%Y-%m-%d %H:%M}] Checking wishlist prices...")
    with db.connect() as conn:
        appids = sync_wishlist(conn, cfg)
        if not appids:
            print("  Nothing to track.")
            return []
        print(f"  Tracking {len(appids)} games.")
        events = evaluate(conn, cfg, appids)
        announce(conn, cfg, events)
        db.set_meta(conn, "last_run", db.now())
        db.set_meta(conn, "last_run_count", len(appids))
    if events:
        print(f"  {len(events)} alert(s) raised.")
    else:
        print("  No new drops.")
    return events
