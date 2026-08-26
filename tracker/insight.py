"""Glue between stored price data and the cadence model.

Merges two history sources — this tracker's own observations and any imported
IsThereAnyDeal history — then runs the advisor over every tracked game.
"""
from datetime import date, datetime, timedelta, timezone

from . import advisor, db, itad, salecalendar
from .advisor import PricePoint

# How long an ITAD import stays fresh before it is worth re-fetching.
ITAD_TTL_DAYS = 7


def _as_date(stamp: str) -> date | None:
    try:
        return datetime.fromisoformat(stamp).date()
    except (TypeError, ValueError):
        return None


def local_history(conn, appid: int) -> list[PricePoint]:
    rows = conn.execute(
        "SELECT ts, final, initial, discount_percent FROM price_history"
        " WHERE appid=? ORDER BY ts",
        (appid,),
    ).fetchall()
    points = []
    for row in rows:
        when = _as_date(row["ts"])
        if when is None or row["final"] is None:
            continue
        points.append(
            PricePoint(
                ts=when,
                price=row["final"],
                regular=row["initial"] or row["final"],
                cut=row["discount_percent"] or 0,
            )
        )
    return points


def cached_itad(conn, appid: int) -> list[PricePoint]:
    rows = conn.execute(
        "SELECT ts, price, regular, cut FROM itad_history WHERE appid=? ORDER BY ts",
        (appid,),
    ).fetchall()
    points = []
    for row in rows:
        when = _as_date(row["ts"])
        if when is None:
            continue
        points.append(
            PricePoint(
                ts=when,
                price=row["price"],
                regular=row["regular"] or row["price"],
                cut=row["cut"] or 0,
            )
        )
    return points


def itad_is_stale(conn, appid: int) -> bool:
    row = conn.execute(
        "SELECT fetched_at FROM itad_meta WHERE appid=?", (appid,)
    ).fetchone()
    if not row or not row["fetched_at"]:
        return True
    fetched = _as_date(row["fetched_at"])
    return fetched is None or (date.today() - fetched).days >= ITAD_TTL_DAYS


def refresh_itad(conn, appid: int, country: str = "IN") -> int:
    """Import ITAD history for one game. Returns the number of points stored."""
    points = itad.history_for_appid(appid, country=country)
    if points:
        conn.execute("DELETE FROM itad_history WHERE appid=?", (appid,))
        conn.executemany(
            "INSERT OR IGNORE INTO itad_history (appid, ts, price, regular, cut)"
            " VALUES (?,?,?,?,?)",
            [(appid, p.ts.isoformat(), p.price, p.regular, p.cut) for p in points],
        )
    conn.execute(
        "INSERT INTO itad_meta (appid, fetched_at, points) VALUES (?,?,?)"
        " ON CONFLICT(appid) DO UPDATE SET fetched_at=excluded.fetched_at,"
        " points=excluded.points",
        (appid, datetime.now(timezone.utc).isoformat(timespec="seconds"), len(points)),
    )
    return len(points)


def merged_history(conn, appid: int) -> list[PricePoint]:
    """ITAD history plus our own, de-duplicated by date (ours wins on a clash)."""
    by_date: dict[date, PricePoint] = {}
    for point in cached_itad(conn, appid):
        by_date[point.ts] = point
    for point in local_history(conn, appid):
        by_date[point.ts] = point
    return [by_date[k] for k in sorted(by_date)]


def _release_date(row) -> date | None:
    raw = row["release_date"]
    if not raw:
        return None
    for fmt in ("%d %b, %Y", "%b %d, %Y", "%d %B, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw.strip(), fmt).date()
        except ValueError:
            continue
    return None


def advise_all(conn, cfg: dict, use_itad: bool = True, verbose: bool = False):
    """Run the cadence model over every tracked game."""
    symbol = cfg.get("currency_symbol", "₹")
    country = (cfg.get("country_code") or "in").upper()
    today = date.today()
    windows = salecalendar.load(conn)

    rows = conn.execute(
        """
        SELECT g.appid, g.name, g.release_date, g.coming_soon,
               p.final, p.initial, p.discount_percent
        FROM games g
        LEFT JOIN price_history p ON p.id = (
            SELECT id FROM price_history WHERE appid = g.appid ORDER BY ts DESC LIMIT 1
        )
        WHERE g.on_wishlist = 1
        ORDER BY g.name
        """
    ).fetchall()

    if use_itad and itad.configured():
        stale = [r["appid"] for r in rows if itad_is_stale(conn, r["appid"])]
        if stale and verbose:
            print(f"  Importing IsThereAnyDeal history for {len(stale)} game(s)...")
        for appid in stale:
            count = refresh_itad(conn, appid, country=country)
            if verbose:
                name = next((r["name"] for r in rows if r["appid"] == appid), appid)
                print(f"    {name}: {count} historical price points")

    verdicts = []
    for row in rows:
        if row["final"] is None:
            continue
        current = PricePoint(
            ts=today,
            price=row["final"],
            regular=row["initial"] or row["final"],
            cut=row["discount_percent"] or 0,
        )
        verdicts.append(
            advisor.analyse(
                merged_history(conn, row["appid"]),
                current,
                today=today,
                windows=windows,
                release=_release_date(row),
                name=row["name"] or f"App {row['appid']}",
                unreleased=bool(row["coming_soon"]),
                appid=row["appid"],
                symbol=symbol,
            )
        )

    order = {"BUY_NOW": 0, "WAIT": 1, "NEUTRAL": 2, "UNKNOWN": 3}
    return sorted(verdicts, key=lambda v: (order[v.action], -v.expected_savings, v.name))
