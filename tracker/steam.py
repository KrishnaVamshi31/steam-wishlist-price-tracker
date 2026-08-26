"""Steam Store / Web API client. No API key required for anything here."""
import re
import time
import xml.etree.ElementTree as ET

import requests

UA = {"User-Agent": "GamePriceTracker/1.0 (personal wishlist tracker)"}
STORE = "https://store.steampowered.com"
WEBAPI = "https://api.steampowered.com"
TIMEOUT = 30


class SteamError(RuntimeError):
    pass


def _get(url: str, params: dict | None = None, tries: int = 4):
    """GET with backoff. Steam rate-limits the store API fairly aggressively."""
    delay = 2.0
    last = None
    for attempt in range(tries):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=TIMEOUT)
            if r.status_code == 429:
                time.sleep(delay)
                delay *= 2
                continue
            r.raise_for_status()
            return r
        except requests.RequestException as exc:
            last = exc
            time.sleep(delay)
            delay *= 2
    raise SteamError(f"GET {url} failed after {tries} tries: {last}")


def resolve_steamid(profile: str) -> str:
    """Accept a SteamID64, a /profiles/<id> URL, or an /id/<vanity> URL."""
    profile = (profile or "").strip().rstrip("/")
    if not profile:
        raise SteamError("No Steam profile configured. Set 'steam_profile' in config.json.")
    if re.fullmatch(r"7656\d{13}", profile):
        return profile
    m = re.search(r"/profiles/(\d{17})", profile)
    if m:
        return m.group(1)
    m = re.search(r"/id/([^/?#]+)", profile)
    vanity = m.group(1) if m else profile
    r = _get(f"https://steamcommunity.com/id/{vanity}/", params={"xml": 1})
    try:
        root = ET.fromstring(r.text)
    except ET.ParseError as exc:
        raise SteamError(f"Could not read profile page for '{vanity}': {exc}") from exc
    node = root.find("steamID64")
    if node is None or not node.text:
        raise SteamError(
            f"'{vanity}' is not a valid Steam vanity URL. Use your full profile URL."
        )
    return node.text.strip()


def fetch_wishlist(steamid: str) -> list[dict]:
    """Return [{'appid': int, 'priority': int, 'date_added': int}, ...].

    Uses the current IWishlistService endpoint, falling back to the older
    store wishlistdata pages if that returns nothing.
    """
    r = _get(f"{WEBAPI}/IWishlistService/GetWishlist/v1/", params={"steamid": steamid})
    items = (r.json().get("response") or {}).get("items") or []
    if items:
        return [
            {
                "appid": int(i["appid"]),
                "priority": int(i.get("priority", 0)),
                "date_added": int(i.get("date_added", 0)),
            }
            for i in items
            if i.get("appid")
        ]

    out, page = [], 0
    while page < 20:
        try:
            r = _get(f"{STORE}/wishlist/profiles/{steamid}/wishlistdata/", params={"p": page}, tries=2)
            data = r.json()
        except (SteamError, ValueError):
            break
        if not isinstance(data, dict) or not data:
            break
        for appid, entry in data.items():
            out.append(
                {
                    "appid": int(appid),
                    "priority": int(entry.get("priority", 0)),
                    "date_added": int(entry.get("added", 0)),
                }
            )
        page += 1
        time.sleep(1)
    return out


def fetch_prices(appids, cc: str = "in", batch: int = 20) -> dict[int, dict]:
    """Batched price lookup. Returns {appid: {...}} — missing/free games are omitted."""
    prices: dict[int, dict] = {}
    appids = list(appids)
    for i in range(0, len(appids), batch):
        chunk = appids[i : i + batch]
        r = _get(
            f"{STORE}/api/appdetails",
            params={
                "appids": ",".join(str(a) for a in chunk),
                "cc": cc,
                "l": "en",
                "filters": "price_overview",
            },
        )
        try:
            data = r.json() or {}
        except ValueError:
            continue
        for appid_str, entry in data.items():
            if not isinstance(entry, dict) or not entry.get("success"):
                continue
            po = (entry.get("data") or {}).get("price_overview")
            if not po:
                continue  # free, unreleased, or region-locked
            prices[int(appid_str)] = {
                "currency": po.get("currency"),
                "initial": po.get("initial"),
                "final": po.get("final"),
                "discount_percent": po.get("discount_percent", 0),
                "final_formatted": po.get("final_formatted", ""),
            }
        time.sleep(1.5)
    return prices


def fetch_meta(appid: int, cc: str = "in") -> dict:
    """Full details for one app. Called once per new game, then cached in the DB."""
    r = _get(
        f"{STORE}/api/appdetails",
        params={"appids": str(appid), "cc": cc, "l": "en"},
    )
    entry = (r.json() or {}).get(str(appid)) or {}
    if not entry.get("success"):
        return {"name": f"App {appid}"}
    d = entry.get("data") or {}
    release = d.get("release_date") or {}
    return {
        "name": d.get("name") or f"App {appid}",
        "type": d.get("type"),
        "is_free": d.get("is_free", False),
        "publishers": d.get("publishers") or [],
        "developers": d.get("developers") or [],
        "release_date": release.get("date"),
        "coming_soon": release.get("coming_soon", False),
    }


def store_url(appid: int) -> str:
    return f"{STORE}/app/{appid}/"
