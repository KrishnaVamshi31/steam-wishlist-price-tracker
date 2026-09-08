"""Fetch any public wishlist live, without touching the database.

The tracked wishlist in `prices.db` belongs to whoever set this instance up. For a
visitor pasting their own Steam profile there is nowhere to persist anything — the
host rebuilds its filesystem from git on restart — so this module reads straight
from Steam and hands back plain objects the dashboard can render.

It uses IStoreBrowseService/GetItems, which returns names, prices, discounts and
release info for a whole wishlist in one request. The older appdetails endpoint
needs a separate call per game just for the name, which would mean a minute of
waiting for a visitor with a large wishlist.
"""
import json
import time
import urllib.parse
from dataclasses import dataclass, field

import requests

from . import steam

STORE_API = "https://api.steampowered.com/IStoreBrowseService/GetItems/v1/"
# 33 in one call was fine in testing; 50 keeps the query string comfortably short.
BATCH = 50
TIMEOUT = 30


@dataclass
class LiveGame:
    appid: int
    name: str
    final: int | None = None        # paise
    initial: int | None = None      # paise, full price
    discount_percent: int = 0
    release_date: str | None = None
    coming_soon: bool = False
    publishers: str = ""


class LiveError(RuntimeError):
    pass


def _get_items(appids: list[int], country: str) -> list[dict]:
    payload = {
        "ids": [{"appid": int(a)} for a in appids],
        "context": {
            "language": "english",
            "country_code": country.upper(),
            "steam_realm": 1,
        },
        "data_request": {"include_release": True, "include_basic_info": True},
    }
    url = STORE_API + "?input_json=" + urllib.parse.quote(json.dumps(payload))
    try:
        response = requests.get(url, headers=steam.UA, timeout=TIMEOUT)
        response.raise_for_status()
        return (response.json().get("response") or {}).get("store_items") or []
    except (requests.RequestException, ValueError) as exc:
        raise LiveError(f"Steam store lookup failed: {exc}") from exc


def fetch_games(appids, country: str = "in") -> dict[int, LiveGame]:
    """Names, prices and discounts for many appids, batched."""
    appids = [int(a) for a in appids]
    out: dict[int, LiveGame] = {}

    for start in range(0, len(appids), BATCH):
        chunk = appids[start : start + BATCH]
        for item in _get_items(chunk, country):
            appid = item.get("appid")
            if not appid:
                continue
            price = item.get("best_purchase_option") or {}
            release = item.get("release") or {}

            final = price.get("final_price_in_cents")
            original = price.get("original_price_in_cents")
            discount = price.get("discount_pct") or 0

            stamp = release.get("steam_release_date")
            when = None
            if stamp:
                try:
                    when = time.strftime("%d %b, %Y", time.gmtime(int(stamp)))
                except (ValueError, OSError):
                    when = None

            out[int(appid)] = LiveGame(
                appid=int(appid),
                name=item.get("name") or f"App {appid}",
                final=int(final) if final is not None else None,
                # Steam omits the original price when nothing is discounted.
                initial=int(original) if original else (int(final) if final is not None else None),
                discount_percent=int(discount),
                release_date=when,
                coming_soon=bool(release.get("is_coming_soon")),
            )
        if start + BATCH < len(appids):
            time.sleep(0.5)

    return out


def load_wishlist(profile: str, country: str = "in") -> tuple[str, list[LiveGame]]:
    """Resolve a profile and return (steamid, games) with live prices.

    Raises LiveError with a message meant for a visitor to read.
    """
    if not (profile or "").strip():
        # steam.resolve_steamid's own message talks about config.json, which means
        # nothing to a visitor pasting a link into a web page.
        raise LiveError("Paste a Steam profile URL first.")

    try:
        steamid = steam.resolve_steamid(profile)
    except steam.SteamError as exc:
        raise LiveError(str(exc)) from exc

    try:
        items = steam.fetch_wishlist(steamid)
    except steam.SteamError as exc:
        raise LiveError(f"Could not read that wishlist: {exc}") from exc

    if not items:
        raise LiveError(
            "That profile resolved, but its wishlist is empty or private. In Steam: "
            "Profile → Edit Profile → Privacy Settings → **Game details = Public**. "
            "It is a separate setting from overall profile privacy."
        )

    appids = [i["appid"] for i in items]
    games = fetch_games(appids, country)

    # Preserve the wishlist's own ordering, and keep games the store did not return
    # (region-locked or delisted) out rather than showing blank rows.
    ordered = [games[a] for a in appids if a in games]
    if not ordered:
        raise LiveError(
            "Found the wishlist, but Steam returned no store data for any of it — "
            "the games may be unavailable in this region."
        )
    return steamid, ordered
