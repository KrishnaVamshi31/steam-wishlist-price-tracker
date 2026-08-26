"""SQLite storage for games, price history, alerts and sale research notes."""
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from . import config

BACKUP_DIR = config.DATA_DIR / "backups"

SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    appid           INTEGER PRIMARY KEY,
    name            TEXT,
    type            TEXT,
    publishers      TEXT,
    developers      TEXT,
    release_date    TEXT,
    coming_soon     INTEGER DEFAULT 0,
    is_free         INTEGER DEFAULT 0,
    on_wishlist     INTEGER DEFAULT 1,
    first_seen      TEXT,
    last_seen       TEXT
);

CREATE TABLE IF NOT EXISTS price_history (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    appid            INTEGER NOT NULL,
    ts               TEXT NOT NULL,
    initial          INTEGER,          -- paise
    final            INTEGER,          -- paise
    discount_percent INTEGER,
    currency         TEXT,
    FOREIGN KEY (appid) REFERENCES games(appid)
);
CREATE INDEX IF NOT EXISTS idx_price_appid_ts ON price_history(appid, ts);

CREATE TABLE IF NOT EXISTS alerts (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    appid    INTEGER,
    ts       TEXT NOT NULL,
    kind     TEXT,                     -- discount | record_low | target_hit | free
    title    TEXT,
    body     TEXT,
    notified INTEGER DEFAULT 0
);

-- Sale intel gathered from the web (upcoming Steam sales, publisher sales, etc.)
CREATE TABLE IF NOT EXISTS notes (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    appid    INTEGER,                  -- NULL = applies to everything
    ts       TEXT NOT NULL,
    source   TEXT,
    headline TEXT,
    url      TEXT,
    body     TEXT,
    starts   TEXT,                     -- ISO date if a sale date is known
    ends     TEXT
);

-- Price history imported from IsThereAnyDeal, kept apart from our own
-- observations so a refresh can replace it wholesale without touching them.
CREATE TABLE IF NOT EXISTS itad_history (
    appid    INTEGER NOT NULL,
    ts       TEXT NOT NULL,
    price    INTEGER,
    regular  INTEGER,
    cut      INTEGER,
    PRIMARY KEY (appid, ts, price, cut)
);
CREATE INDEX IF NOT EXISTS idx_itad_appid ON itad_history(appid, ts);

CREATE TABLE IF NOT EXISTS itad_meta (
    appid      INTEGER PRIMARY KEY,
    uuid       TEXT,
    fetched_at TEXT,
    points     INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect():
    config.DATA_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    # WAL lets the dashboard (reader) and `track.py check` (writer) run at the same
    # time without "database is locked" — readers no longer block behind a writer.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        # Fold the WAL back into prices.db and truncate it, so the .db file on disk
        # is always self-contained. Without this, a raw file copy or `git add` of
        # prices.db alone (as the daily workflow does) could miss whatever's still
        # sitting only in the WAL — SQLite's own auto-checkpoint isn't guaranteed to
        # have run yet, and TRUNCATE also keeps the -wal file from growing unbounded.
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error:
            pass
        conn.close()


def upsert_game(conn, appid: int, meta: dict, on_wishlist: bool = True) -> None:
    ts = now()
    conn.execute(
        """
        INSERT INTO games (appid, name, type, publishers, developers, release_date,
                           coming_soon, is_free, on_wishlist, first_seen, last_seen)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(appid) DO UPDATE SET
            name         = COALESCE(excluded.name, games.name),
            type         = COALESCE(excluded.type, games.type),
            publishers   = COALESCE(excluded.publishers, games.publishers),
            developers   = COALESCE(excluded.developers, games.developers),
            release_date = COALESCE(excluded.release_date, games.release_date),
            coming_soon  = excluded.coming_soon,
            is_free      = excluded.is_free,
            on_wishlist  = excluded.on_wishlist,
            last_seen    = excluded.last_seen
        """,
        (
            appid,
            meta.get("name"),
            meta.get("type"),
            ", ".join(meta.get("publishers") or []),
            ", ".join(meta.get("developers") or []),
            meta.get("release_date"),
            int(bool(meta.get("coming_soon"))),
            int(bool(meta.get("is_free"))),
            int(on_wishlist),
            ts,
            ts,
        ),
    )


def last_price(conn, appid: int):
    return conn.execute(
        "SELECT * FROM price_history WHERE appid=? ORDER BY ts DESC LIMIT 1", (appid,)
    ).fetchone()


def record_low(conn, appid: int):
    """Lowest final price we have ever recorded for this app."""
    return conn.execute(
        "SELECT MIN(final) AS low FROM price_history WHERE appid=? AND final IS NOT NULL",
        (appid,),
    ).fetchone()["low"]


def add_price(conn, appid: int, price: dict) -> bool:
    """Insert a price row only when it differs from the last one. Returns True if changed."""
    prev = last_price(conn, appid)
    changed = (
        prev is None
        or prev["final"] != price["final"]
        or prev["discount_percent"] != price["discount_percent"]
    )
    if changed:
        conn.execute(
            "INSERT INTO price_history (appid, ts, initial, final, discount_percent, currency)"
            " VALUES (?,?,?,?,?,?)",
            (
                appid,
                now(),
                price.get("initial"),
                price.get("final"),
                price.get("discount_percent"),
                price.get("currency"),
            ),
        )
    return changed


def add_alert(conn, appid, kind: str, title: str, body: str) -> int:
    cur = conn.execute(
        "INSERT INTO alerts (appid, ts, kind, title, body) VALUES (?,?,?,?,?)",
        (appid, now(), kind, title, body),
    )
    return cur.lastrowid


def mark_notified(conn, alert_ids) -> None:
    conn.executemany(
        "UPDATE alerts SET notified=1 WHERE id=?", [(i,) for i in alert_ids]
    )


def add_note(conn, headline, body, source="", url="", appid=None, starts=None, ends=None) -> None:
    """Store a piece of sale intel. Skips exact duplicate headlines for the same app."""
    dupe = conn.execute(
        "SELECT 1 FROM notes WHERE headline=? AND IFNULL(appid,-1)=IFNULL(?,-1)",
        (headline, appid),
    ).fetchone()
    if dupe:
        return
    conn.execute(
        "INSERT INTO notes (appid, ts, source, headline, url, body, starts, ends)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (appid, now(), source, headline, url, body, starts, ends),
    )


def set_meta(conn, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta (key,value) VALUES (?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )


def get_meta(conn, key: str, default=None):
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def backup(keep: int = 30):
    """Copy prices.db to data/backups/, at most once per day, pruning old copies.

    Price history isn't reconstructable if the DB is lost — it's this tracker's
    own observations, gitignored by design. A WAL-mode DB can't just be copied as
    a plain file while it might be mid-write, so this uses SQLite's own backup API
    rather than shutil, which would risk grabbing a torn/inconsistent snapshot.
    """
    if not config.DB_PATH.exists():
        return None
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    dest = BACKUP_DIR / f"prices-{datetime.now():%Y-%m-%d}.db"
    created = False
    if not dest.exists():
        src = sqlite3.connect(config.DB_PATH)
        try:
            out = sqlite3.connect(dest)
            try:
                src.backup(out)
            finally:
                out.close()
        finally:
            src.close()
        created = True

    backups = sorted(BACKUP_DIR.glob("prices-*.db"))
    for old in backups[:-keep]:
        old.unlink(missing_ok=True)

    return dest if created else None
