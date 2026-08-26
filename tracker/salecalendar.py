"""Known Steam sale windows.

Seasonal sales ("major") are the ones worth waiting for — they apply store-wide and
carry the deepest discounts. Themed fests are narrow: a game only participates if it
fits the theme, so they are weak signals and never drive a "wait" on their own.

Dates confirmed against multiple outlets in Aug 2026. Valve moved the Autumn Sale
from its historical late-November slot to early October this year.
"""
from dataclasses import dataclass
from datetime import date

from . import db

BUILTIN: list[tuple[str, str, str, bool]] = [
    # (name, start, end, is_major)
    ("Steam Autumn Sale", "2026-10-01", "2026-10-08", True),
    ("Steam Next Fest", "2026-10-19", "2026-10-26", False),
    ("Steam Scream V (Halloween)", "2026-10-26", "2026-11-02", False),
    ("Steam Winter Sale", "2026-12-17", "2027-01-04", True),
    # Valve has run these in roughly the same window every year; treat as expected,
    # not confirmed, until announced.
    ("Steam Spring Sale (expected)", "2027-03-11", "2027-03-18", True),
    ("Steam Summer Sale (expected)", "2027-06-24", "2027-07-08", True),
]


@dataclass
class SaleWindow:
    name: str
    start: date
    end: date
    major: bool

    def days_until(self, today: date) -> int:
        return (self.start - today).days

    def active_on(self, today: date) -> bool:
        return self.start <= today <= self.end


def _parse(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def load(conn=None) -> list[SaleWindow]:
    """Built-in windows merged with any dated notes recorded by the research task."""
    windows: dict[tuple[str, date], SaleWindow] = {}

    for name, start, end, major in BUILTIN:
        s, e = _parse(start), _parse(end)
        if s and e:
            windows[(name, s)] = SaleWindow(name, s, e, major)

    rows = []
    if conn is not None:
        rows = conn.execute(
            "SELECT headline, starts, ends FROM notes WHERE starts IS NOT NULL AND appid IS NULL"
        ).fetchall()
    for row in rows:
        s = _parse(row["starts"])
        e = _parse(row["ends"]) or s
        if not s:
            continue
        headline = row["headline"]
        # A note only counts as a major sale if it reads like a seasonal one.
        major = any(
            word in headline.lower()
            for word in ("autumn", "winter", "summer", "spring", "fall")
        )
        windows.setdefault((headline, s), SaleWindow(headline, s, e, major))

    # A researched note usually restates a built-in window in wordier form. Collapse
    # anything covering the same dates and keep the tersest name.
    by_range: dict[tuple[date, date], SaleWindow] = {}
    for window in windows.values():
        key = (window.start, window.end)
        existing = by_range.get(key)
        if existing is None or len(window.name) < len(existing.name):
            by_range[key] = SaleWindow(
                window.name,
                window.start,
                window.end,
                window.major or (existing.major if existing else False),
            )

    return sorted(by_range.values(), key=lambda w: w.start)


def next_major(today: date, conn=None, windows=None) -> SaleWindow | None:
    """The next store-wide sale that has not ended yet."""
    windows = windows if windows is not None else load(conn)
    upcoming = [w for w in windows if w.major and w.end >= today]
    return upcoming[0] if upcoming else None


def describe(window: SaleWindow, today: date) -> str:
    if window.active_on(today):
        return f"{window.name} is running now (ends {window.end:%d %b})"
    days = window.days_until(today)
    return f"{window.name} starts in {days} day{'s' if days != 1 else ''} ({window.start:%d %b})"
