"""IsThereAnyDeal provider — optional source of real multi-year price history.

Without a key the advisor still works, but it can only reason about history this
tracker recorded itself, which is thin for the first few months. A free key from
https://isthereanydeal.com/apps/new/ gives the model years of real discount data
immediately, which is the difference between a usable verdict and a shrug.

API v2 docs: https://docs.isthereanydeal.com/
"""
from datetime import date, datetime, timedelta

import requests

from . import config
from .advisor import PricePoint

BASE = "https://api.isthereanydeal.com"
TIMEOUT = 30
STEAM_SHOP_NAME = "steam"


class ITADError(RuntimeError):
    pass


def configured() -> bool:
    return bool(config.secret("ITAD_API_KEY"))


def _key() -> str:
    key = config.secret("ITAD_API_KEY")
    if not key:
        raise ITADError("No ITAD_API_KEY in .env")
    return key


def lookup_uuid(appid: int) -> str | None:
    """Map a Steam appid to ITAD's internal game uuid."""
    r = requests.get(
        f"{BASE}/games/lookup/v1",
        params={"key": _key(), "appid": appid},
        timeout=TIMEOUT,
    )
    if r.status_code != 200:
        raise ITADError(f"lookup failed for appid {appid}: HTTP {r.status_code}")
    data = r.json() or {}
    if not data.get("found"):
        return None
    return ((data.get("game") or {}).get("id")) or None


def history(
    uuid: str,
    country: str = "IN",
    since: date | None = None,
    years: int = 4,
    steam_only: bool = True,
) -> list[PricePoint]:
    """Price-change history for one game, newest-safe and Steam-filtered.

    ITAD defaults to the last 3 months, so a start date must be passed explicitly to
    get anything the cadence model can actually learn from. Pass the game's release
    date as `since` to pull its entire discount history back to launch; `years` is
    only the fallback for when the release date isn't known.
    """
    since_dt = datetime.combine(since, datetime.min.time()) if since else (
        datetime.now() - timedelta(days=365 * years)
    )
    since_str = since_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    r = requests.get(
        f"{BASE}/games/history/v2",
        params={"key": _key(), "id": uuid, "country": country, "since": since_str},
        timeout=TIMEOUT,
    )
    if r.status_code != 200:
        raise ITADError(f"history failed for {uuid}: HTTP {r.status_code}")

    points: list[PricePoint] = []
    for entry in r.json() or []:
        shop = (entry.get("shop") or {}).get("name", "")
        if steam_only and shop.strip().lower() != STEAM_SHOP_NAME:
            continue
        deal = entry.get("deal") or {}
        price = (deal.get("price") or {}).get("amountInt")
        regular = (deal.get("regular") or {}).get("amountInt")
        if price is None or regular is None:
            continue
        stamp = entry.get("timestamp") or ""
        try:
            when = datetime.fromisoformat(stamp.replace("Z", "+00:00")).date()
        except ValueError:
            continue
        points.append(
            PricePoint(
                ts=when,
                price=int(price),
                regular=int(regular),
                cut=int(deal.get("cut") or 0),
            )
        )
    return sorted(points, key=lambda p: p.ts)


def history_for_appid(
    appid: int, country: str = "IN", since: date | None = None, years: int = 4
) -> list[PricePoint]:
    """Convenience: appid straight to price history. Returns [] when unavailable."""
    try:
        uuid = lookup_uuid(appid)
        if not uuid:
            return []
        return history(uuid, country=country, since=since, years=years)
    except (ITADError, requests.RequestException, ValueError):
        return []
