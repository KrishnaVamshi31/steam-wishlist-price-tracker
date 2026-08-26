"""Steam wishlist price tracker — command line entry point.

  python track.py check      fetch prices, alert on drops   (the main one)
  python track.py report     rebuild reports/latest.md
  python track.py digest     send the current deal list to Telegram
  python track.py list       print the wishlist and prices to the terminal
  python track.py test       verify Telegram + toast notifications work
  python track.py note       record a piece of sale intel (used by the research task)
"""
import argparse
import sys

from tracker import check, config, console, db, insight, itad, notify, report, steam

console.init()


def cmd_check(args):
    cfg = config.load()
    check.run(cfg)
    path = report.write(cfg)
    print(f"  Report: {path}")


def cmd_report(args):
    print(f"  Report: {report.write()}")


def cmd_list(args):
    cfg = config.load()
    sym = cfg.get("currency_symbol", "\u20b9")
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT g.name, g.appid, p.final, p.initial, p.discount_percent,
                      (SELECT MIN(final) FROM price_history WHERE appid=g.appid) low
               FROM games g LEFT JOIN price_history p ON p.id =
                 (SELECT id FROM price_history WHERE appid=g.appid ORDER BY ts DESC LIMIT 1)
               WHERE g.on_wishlist=1 ORDER BY p.discount_percent DESC, g.name"""
        ).fetchall()
    if not rows:
        print("Nothing tracked yet — run: python track.py check")
        return
    print(f"{'Game':<45} {'Now':>10} {'Was':>10} {'Off':>5} {'Low':>10}")
    print("-" * 84)
    for r in rows:
        disc = f"{r['discount_percent']}%" if (r["discount_percent"] or 0) > 0 else "-"
        print(
            f"{(r['name'] or '')[:44]:<45} {check.money(r['final'], sym):>10} "
            f"{check.money(r['initial'], sym):>10} {disc:>5} {check.money(r['low'], sym):>10}"
        )


def cmd_digest(args):
    cfg = config.load()
    sym = cfg.get("currency_symbol", "\u20b9")
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT g.name, g.appid, p.final, p.initial, p.discount_percent
               FROM games g JOIN price_history p ON p.id =
                 (SELECT id FROM price_history WHERE appid=g.appid ORDER BY ts DESC LIMIT 1)
               WHERE g.on_wishlist=1 AND p.discount_percent > 0
               ORDER BY p.discount_percent DESC"""
        ).fetchall()
    if not notify.telegram_configured():
        sys.exit(
            "Telegram is not configured — add TELEGRAM_BOT_TOKEN and "
            "TELEGRAM_CHAT_ID to .env (see .env.example)."
        )
    if not rows:
        notify.send_telegram(
            "<b>\U0001f3ae Wishlist digest</b>\n\nNothing on your wishlist is discounted right now."
        )
        print("Sent: nothing on sale.")
        return
    lines = ["<b>\U0001f3ae Wishlist deals right now</b>", ""]
    for r in rows:
        lines.append(
            f'\U0001f4b8 <a href="https://store.steampowered.com/app/{r["appid"]}/">'
            f'{notify.esc(r["name"])}</a>\n'
            f'   <b>{check.money(r["final"], sym)}</b> '
            f'<s>{check.money(r["initial"], sym)}</s> — {r["discount_percent"]}% off'
        )
    ok = notify.send_telegram("\n".join(lines))
    print("Digest sent." if ok else "Digest failed — is Telegram configured in .env?")


def cmd_test(args):
    print("Telegram configured:", notify.telegram_configured())
    if notify.telegram_configured():
        ok = notify.send_telegram(
            "<b>\U0001f3ae Game Price Tracker</b>\n\nTelegram is wired up correctly. "
            "You'll get wishlist price drops here."
        )
        print("  Telegram test:", "sent" if ok else "FAILED")
    else:
        print("  Skipped — add TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to .env")
    print("Sending a test toast...")
    ok = notify.send_toast("Game Price Tracker", "Desktop notifications are working.")
    print("  Toast:", "sent" if ok else "FAILED")


ACTION_LABEL = {
    "BUY_NOW": "BUY NOW",
    "WAIT": "WAIT",
    "NEUTRAL": "no action",
    "UNKNOWN": "unknown",
}
ACTION_TAG = {
    "BUY_NOW": "\U0001f7e2",
    "WAIT": "\U0001f7e1",
    "NEUTRAL": "⚪",
    "UNKNOWN": "❔",
}


def cmd_advise(args):
    """Buy-now-or-wait verdicts from the discount-cadence model."""
    cfg = config.load()
    sym = cfg.get("currency_symbol", "₹")
    with db.connect() as conn:
        verdicts = insight.advise_all(conn, cfg, use_itad=not args.no_itad, verbose=True)

    if not verdicts:
        print("Nothing to advise on yet — run: python track.py check")
        return

    if not itad.configured():
        print(
            "\n  Note: no ITAD_API_KEY set, so verdicts use only history this tracker\n"
            "  has recorded. Add a free key from https://isthereanydeal.com/apps/new/\n"
            "  to reason over years of real price data instead.\n"
        )

    if args.only:
        shown = [v for v in verdicts if v.action == args.only]
    elif args.all:
        shown = verdicts
    else:
        # Default to the verdicts worth acting on; the rest are just noise.
        shown = [v for v in verdicts if v.action in ("BUY_NOW", "WAIT")]

    for v in shown:
        price = check.money(v.current_price, sym)
        cut = f" ({v.current_cut}% off)" if v.current_cut else ""
        print(f"{ACTION_TAG[v.action]} {ACTION_LABEL[v.action]:9} {v.name}")
        print(f"      {price}{cut} — {v.headline}  [confidence: {v.confidence}]")
        for reason in v.reasons:
            print(f"      . {reason}")
        if v.expected_savings:
            print(f"      . Potential extra saving: {check.money(v.expected_savings, sym)}")
        print()

    buys = sum(1 for v in verdicts if v.action == "BUY_NOW")
    waits = sum(1 for v in verdicts if v.action == "WAIT")
    quiet = len(verdicts) - buys - waits
    total_wait_saving = sum(v.expected_savings for v in verdicts if v.action == "WAIT")
    print(f"{buys} worth buying now, {waits} worth waiting on"
          + (f" (about {check.money(total_wait_saving, sym)} in potential extra savings)."
             if total_wait_saving else "."))
    if quiet and not args.all and not args.only:
        print(f"{quiet} other game(s) had nothing to act on — use --all to see them.")

    if args.telegram:
        if not notify.telegram_configured():
            sys.exit("Telegram is not configured — see .env.example.")
        lines = ["<b>\U0001f9e0 Buy now or wait?</b>", ""]
        for v in verdicts:
            if v.action not in ("BUY_NOW", "WAIT"):
                continue
            lines.append(
                f'{ACTION_TAG[v.action]} <b>{ACTION_LABEL[v.action]}</b> — '
                f'<a href="https://store.steampowered.com/app/{v.appid}/">'
                f'{notify.esc(v.name)}</a>\n'
                f'   {check.money(v.current_price, sym)} — {notify.esc(v.headline)}'
            )
        if len(lines) > 2:
            notify.send_telegram("\n".join(lines))
            print("Sent verdicts to Telegram.")
        else:
            print("Nothing decisive to send.")


def cmd_note(args):
    """Record sale intel found on the web so it shows up in the report and dashboard."""
    with db.connect() as conn:
        appid = None
        if args.game:
            row = conn.execute(
                "SELECT appid FROM games WHERE name LIKE ? ORDER BY LENGTH(name) LIMIT 1",
                (f"%{args.game}%",),
            ).fetchone()
            if not row:
                sys.exit(f"No tracked game matching '{args.game}'.")
            appid = row["appid"]
        db.add_note(
            conn,
            headline=args.headline,
            body=args.body or "",
            source=args.source or "",
            url=args.url or "",
            appid=appid,
            starts=args.starts,
            ends=args.ends,
        )
    print("Note recorded.")


def cmd_whoami(args):
    cfg = config.load()
    sid = steam.resolve_steamid(cfg["steam_profile"])
    items = steam.fetch_wishlist(sid)
    print(f"SteamID64: {sid}")
    print(f"Wishlist items visible: {len(items)}")
    if not items:
        print("If that's 0, set Profile > Edit Profile > Privacy > Game details = Public.")


def main():
    p = argparse.ArgumentParser(description="Steam wishlist price tracker")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="fetch prices and alert on drops").set_defaults(fn=cmd_check)
    sub.add_parser("report", help="rebuild the markdown report").set_defaults(fn=cmd_report)
    sub.add_parser("list", help="print tracked games and prices").set_defaults(fn=cmd_list)
    sub.add_parser("digest", help="Telegram the current deal list").set_defaults(fn=cmd_digest)
    sub.add_parser("test", help="test notification channels").set_defaults(fn=cmd_test)
    sub.add_parser("whoami", help="check Steam profile + wishlist visibility").set_defaults(fn=cmd_whoami)

    a = sub.add_parser("advise", help="buy-now-or-wait verdicts")
    a.add_argument("--telegram", action="store_true", help="also send verdicts to Telegram")
    a.add_argument("--no-itad", action="store_true", help="skip IsThereAnyDeal import")
    a.add_argument("--all", action="store_true", help="include games with nothing to act on")
    a.add_argument("--only", choices=["BUY_NOW", "WAIT", "NEUTRAL", "UNKNOWN"],
                   help="show only this verdict")
    a.set_defaults(fn=cmd_advise)

    n = sub.add_parser("note", help="record sale intel")
    n.add_argument("headline")
    n.add_argument("--game", help="game name to attach this to (partial match)")
    n.add_argument("--body", default="")
    n.add_argument("--source", default="")
    n.add_argument("--url", default="")
    n.add_argument("--starts", help="ISO date the sale starts")
    n.add_argument("--ends", help="ISO date the sale ends")
    n.set_defaults(fn=cmd_note)

    args = p.parse_args()
    try:
        args.fn(args)
    except steam.SteamError as exc:
        sys.exit(f"Steam error: {exc}")
    except KeyboardInterrupt:
        sys.exit("Interrupted.")


if __name__ == "__main__":
    main()
