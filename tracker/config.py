"""Configuration: config.json for settings, .env for secrets."""
import json
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
REPORTS_DIR = ROOT / "reports"
CONFIG_PATH = ROOT / "config.json"
DB_PATH = DATA_DIR / "prices.db"

load_dotenv(ROOT / ".env")

DEFAULTS = {
    # Steam profile URL or bare SteamID64. Wishlist must be set to Public.
    "steam_profile": "",
    # Store region. "in" = India / INR.
    "country_code": "in",
    "currency_symbol": "\u20b9",
    # Alert when a game hits at least this discount.
    "min_discount_percent": 20,
    # Always alert when a game is at its lowest price we have ever recorded.
    "alert_on_record_low": True,
    # A "record low" only counts if the discount is at least this deep, and only
    # once we have this many price observations to compare against.
    "record_low_min_discount": 10,
    "record_low_min_history": 3,
    # Per-game target prices, keyed by appid as a string, in rupees.
    #   "1145360": 500   -> alert when Hades drops to Rs 500 or below
    "target_prices": {},
    # Games to watch that are not on the wishlist. List of appids.
    "extra_appids": [],
    "notify": {"telegram": True, "toast": True},
}


def load() -> dict:
    cfg = json.loads(json.dumps(DEFAULTS))
    if CONFIG_PATH.exists():
        user = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        for key, value in user.items():
            if isinstance(value, dict) and isinstance(cfg.get(key), dict):
                cfg[key].update(value)
            else:
                cfg[key] = value
    DATA_DIR.mkdir(exist_ok=True)
    REPORTS_DIR.mkdir(exist_ok=True)
    return cfg


def save(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def secret(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


ENV_PATH = ROOT / ".env"


def write_env(updates: dict) -> None:
    """Merge keys into .env, preserving comments and unrelated lines.

    Values stay on this machine — .env is gitignored and nothing here transmits it
    anywhere except to the service the key belongs to. An empty value clears a key.
    """
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    remaining = dict(updates)
    out = []

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            out.append(line)
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in remaining:
            out.append(f"{key}={remaining.pop(key)}")
        else:
            out.append(line)

    for key, value in remaining.items():
        out.append(f"{key}={value}")

    ENV_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")

    # Reflect the change in this process so callers see it without a restart.
    for key, value in updates.items():
        if value:
            os.environ[key] = value
        else:
            os.environ.pop(key, None)
