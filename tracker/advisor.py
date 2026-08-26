"""Discount-cadence model: is the current price worth taking, or will it be beaten?

The question every price tracker dodges is not "did the price drop" but "should I
buy now". That is answerable from three things:

  1. how deeply and how often this specific game has been discounted before,
  2. how the current discount compares to those,
  3. whether a store-wide sale is close enough to be worth waiting for.

Everything here works off a list of PricePoints, so it does not care whether the
history came from this tracker's own database or from IsThereAnyDeal.
"""
from dataclasses import dataclass, field
from datetime import date, timedelta
from statistics import median

from . import salecalendar

# How far ahead a seasonal sale still counts as "worth waiting for".
WAIT_HORIZON_DAYS = 45
# Waiting must save at least this many percentage points to be worth recommending.
MEANINGFUL_GAP_PP = 5
# A current discount within this fraction of the game's typical sale is "as good as usual".
NEAR_TYPICAL = 0.90
# Steam locks discounts for 30 days after a publisher raises the base price.
HIKE_COOLDOWN_DAYS = 30
# Below this many days of history, cadence estimates are not trustworthy.
MIN_SPAN_FOR_CADENCE = 150
# A game with this much history and no discounts is treated as one that never discounts.
NEVER_DISCOUNTS_SPAN = 400


@dataclass
class PricePoint:
    ts: date
    price: int      # paise actually charged
    regular: int    # paise at full price
    cut: int        # discount percent


@dataclass
class Episode:
    start: date
    end: date
    peak_cut: int
    low_price: int

    @property
    def days(self) -> int:
        return (self.end - self.start).days


@dataclass
class Verdict:
    appid: int | None = None
    name: str = ""
    action: str = "UNKNOWN"          # BUY_NOW | WAIT | NEUTRAL | UNKNOWN
    confidence: str = "low"          # high | medium | low
    headline: str = ""
    current_price: int | None = None
    current_cut: int = 0
    best_cut: int = 0
    typical_cut: int = 0
    expected_price: int | None = None
    expected_savings: int = 0
    wait_days: int | None = None
    next_event: str = ""
    episodes: int = 0
    span_days: int = 0
    price_hike: bool = False
    reasons: list[str] = field(default_factory=list)


def find_episodes(history: list[PricePoint]) -> list[Episode]:
    """Group the history into distinct discount episodes.

    History is stored as change events, so a run of consecutive discounted points
    is one sale; a zero-discount point closes it.
    """
    episodes: list[Episode] = []
    current: Episode | None = None
    for point in sorted(history, key=lambda p: p.ts):
        if point.cut > 0:
            if current is None:
                current = Episode(point.ts, point.ts, point.cut, point.price)
            else:
                current.end = point.ts
                current.peak_cut = max(current.peak_cut, point.cut)
                current.low_price = min(current.low_price, point.price)
        elif current is not None:
            current.end = point.ts
            episodes.append(current)
            current = None
    if current is not None:
        episodes.append(current)
    return episodes


def cadence_days(episodes: list[Episode]) -> int | None:
    """Median gap between the starts of consecutive discount episodes."""
    if len(episodes) < 2:
        return None
    starts = sorted(e.start for e in episodes)
    gaps = [(b - a).days for a, b in zip(starts, starts[1:])]
    return int(median(gaps)) if gaps else None


def detect_hike(history: list[PricePoint]) -> tuple[bool, date | None]:
    """Did the base (non-discounted) price go up, and when most recently?

    Steam blocks discounts for 30 days after a price increase, so a recent hike
    means the price is not just higher — it is also locked there for a month.
    """
    hike_date = None
    prev_regular = None
    for point in sorted(history, key=lambda p: p.ts):
        if prev_regular is not None and point.regular > prev_regular:
            hike_date = point.ts
        prev_regular = point.regular
    return hike_date is not None, hike_date


def _confidence(episode_count: int, span: int) -> str:
    if episode_count >= 4 and span >= MIN_SPAN_FOR_CADENCE:
        return "high"
    if episode_count >= 2:
        return "medium"
    return "low"


def analyse(
    history: list[PricePoint],
    current: PricePoint,
    today: date | None = None,
    windows: list[salecalendar.SaleWindow] | None = None,
    release: date | None = None,
    name: str = "",
    appid: int | None = None,
    symbol: str = "₹",
    unreleased: bool = False,
) -> Verdict:
    """Decide whether to buy the current price or wait for a better one."""
    today = today or date.today()
    full = [p for p in sorted(history, key=lambda p: p.ts) if p.ts <= today]
    verdict = Verdict(
        appid=appid,
        name=name,
        current_price=current.price,
        current_cut=current.cut,
    )

    span = (today - full[0].ts).days if full else 0
    verdict.span_days = span

    eps = find_episodes(full)
    verdict.episodes = len(eps)
    # Historical best only — including the current cut would make the comparison
    # below circular ("55% beats the record of 55%").
    verdict.best_cut = max([e.peak_cut for e in eps], default=0)
    peaks = [e.peak_cut for e in eps]
    verdict.typical_cut = int(median(peaks)) if peaks else 0
    verdict.confidence = _confidence(len(eps), span)

    hiked, hike_date = detect_hike(full + [current])
    verdict.price_hike = hiked
    if hiked and hike_date and (today - hike_date).days <= HIKE_COOLDOWN_DAYS:
        unlock = hike_date + timedelta(days=HIKE_COOLDOWN_DAYS)
        verdict.reasons.append(
            f"Base price was raised on {hike_date:%d %b}. Steam blocks discounts for "
            f"{HIKE_COOLDOWN_DAYS} days after a hike, so nothing before ~{unlock:%d %b}."
        )

    upcoming = salecalendar.next_major(today, windows=windows)
    if upcoming:
        verdict.next_event = salecalendar.describe(upcoming, today)
        verdict.wait_days = max(0, upcoming.days_until(today))

    # --- not out yet: nothing to decide --------------------------------------
    if unreleased or (release and release > today):
        verdict.action = "NEUTRAL"
        verdict.confidence = "low"
        verdict.headline = "Not released yet"
        verdict.reasons.append(
            f"Releases {release:%d %b %Y}." if release and release > today
            else "Still listed as coming soon — pre-order pricing rarely improves."
        )
        return verdict

    # --- not enough to say anything ------------------------------------------
    if not full:
        verdict.action = "UNKNOWN"
        verdict.headline = "No price history yet"
        verdict.reasons.append(
            "This tracker has not recorded a price change yet. Confidence improves "
            "after a few weeks, or immediately with an IsThereAnyDeal key."
        )
        return verdict

    # --- at or beyond the best price ever seen -------------------------------
    if current.cut > 0 and eps and current.cut >= verdict.best_cut:
        verdict.action = "BUY_NOW"
        verdict.confidence = _confidence(len(eps), span)
        beats = current.cut > verdict.best_cut
        verdict.headline = (
            f"Record low — {current.cut}% off" if beats
            else f"Matches its best ever — {current.cut}% off"
        )
        verdict.reasons.append(
            f"{current.cut}% {'beats' if beats else 'matches'} the deepest discount "
            f"on record ({verdict.best_cut}%) across {len(eps)} past sale(s)."
        )
        return verdict

    # --- a game that simply does not discount ---------------------------------
    if not eps and span >= NEVER_DISCOUNTS_SPAN:
        verdict.action = "BUY_NOW"
        verdict.confidence = "high" if span >= NEVER_DISCOUNTS_SPAN * 1.5 else "medium"
        verdict.headline = "Never discounted — waiting gains nothing"
        verdict.reasons.append(
            f"No discount in {span} days of history. Waiting for a sale on this one "
            "is unlikely to pay off."
        )
        return verdict

    # --- new release, no discount yet ----------------------------------------
    if not eps and release and 0 <= (today - release).days < 365:
        verdict.action = "WAIT"
        verdict.confidence = "low"
        verdict.headline = "New release — first discount usually comes within a year"
        verdict.reasons.append(
            f"Released {(today - release).days} days ago and not discounted yet. "
            "First cuts typically land at the first seasonal sale after launch."
        )
        return verdict

    expected_cut = max(verdict.typical_cut, 0)
    if expected_cut:
        verdict.expected_price = int(current.regular * (100 - expected_cut) / 100)
        verdict.expected_savings = max(0, current.price - verdict.expected_price)

    sale_soon = upcoming is not None and upcoming.days_until(today) <= WAIT_HORIZON_DAYS
    gap = expected_cut - current.cut

    # --- a store-wide sale is close enough to matter --------------------------
    if sale_soon and expected_cut and gap >= MEANINGFUL_GAP_PP:
        verdict.action = "WAIT"
        verdict.headline = f"Wait — usually hits {expected_cut}% off"
        verdict.reasons.append(
            f"Current {current.cut}% is below this game's typical {expected_cut}% sale."
        )
        if upcoming:
            verdict.reasons.append(salecalendar.describe(upcoming, today) + ".")
        if verdict.expected_savings:
            verdict.reasons.append(
                f"Waiting would likely save about {symbol}{verdict.expected_savings / 100:,.0f} more."
            )
        return verdict

    # --- as good as this game's sales normally get ----------------------------
    if current.cut > 0 and expected_cut and current.cut >= expected_cut * NEAR_TYPICAL:
        verdict.action = "BUY_NOW"
        verdict.headline = f"Good deal — {current.cut}% is its usual best"
        verdict.reasons.append(
            f"{current.cut}% is in line with the typical {expected_cut}% discount "
            f"across {len(eps)} past sale(s)."
        )
        return verdict

    # --- no sale near, and the game is overdue one ----------------------------
    cadence = cadence_days(eps)
    if cadence and eps:
        since = (today - max(e.start for e in eps)).days
        if since >= cadence and current.cut < expected_cut:
            verdict.action = "WAIT"
            verdict.headline = "A discount is overdue"
            verdict.reasons.append(
                f"Discounts land roughly every {cadence} days and it has been {since}. "
                f"Typical depth is {expected_cut}%."
            )
            return verdict

    if current.cut > 0:
        verdict.action = "NEUTRAL"
        verdict.headline = f"{current.cut}% off — modest for this game"
        verdict.reasons.append(
            f"Typical sale is {expected_cut}%. No store-wide sale within "
            f"{WAIT_HORIZON_DAYS} days."
            if expected_cut
            else "Not enough past sales to judge the depth."
        )
    else:
        verdict.action = "NEUTRAL"
        verdict.headline = "Not on sale"
        if upcoming:
            verdict.reasons.append(salecalendar.describe(upcoming, today) + ".")
        if expected_cut:
            verdict.reasons.append(f"Typically discounts to {expected_cut}% off.")
    return verdict
