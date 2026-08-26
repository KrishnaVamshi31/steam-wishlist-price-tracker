"""Archetype tests for the discount-cadence model.

Run:  python tests/test_advisor.py

Each case is a real buying pattern. The model has to reach the same conclusion a
person who studied the game's price history would.
"""
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tracker import advisor, console
from tracker.advisor import PricePoint

console.init()

TODAY = date(2026, 8, 22)
FAILURES: list[str] = []


def point(day: date, regular: int, cut: int) -> PricePoint:
    return PricePoint(ts=day, price=int(regular * (100 - cut) / 100), regular=regular, cut=cut)


def sales_history(regular: int, sale_dates: list[date], cut: int) -> list[PricePoint]:
    """Full price, dropping to `cut` on each sale date and back up a week later."""
    history = [point(sale_dates[0] - timedelta(days=60), regular, 0)]
    for day in sale_dates:
        history.append(point(day, regular, cut))
        history.append(point(day + timedelta(days=7), regular, 0))
    return history


def check(label: str, verdict: advisor.Verdict, expect_action: str, note: str = "") -> None:
    ok = verdict.action == expect_action
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {label}")
    print(f"       -> {verdict.action} ({verdict.confidence}) — {verdict.headline}")
    for reason in verdict.reasons:
        print(f"          . {reason}")
    if note:
        print(f"          note: {note}")
    if not ok:
        FAILURES.append(f"{label}: expected {expect_action}, got {verdict.action}")
    print()


# --------------------------------------------------------------- 1. never discounts
never = [point(TODAY - timedelta(days=1000 - i * 100), 110000, 0) for i in range(10)]
check(
    "Never-discounted title (3 years, no cuts)",
    advisor.analyse(never, point(TODAY, 110000, 0), today=TODAY, name="Persona-like"),
    "BUY_NOW",
    "waiting gains nothing",
)

# --------------------------------------------------------------- 2. seasonal, shallow now
seasonal_dates = [
    date(2024, 6, 27), date(2024, 12, 19),
    date(2025, 6, 26), date(2025, 12, 18),
    date(2026, 6, 25),
]
seasonal = sales_history(200000, seasonal_dates, cut=50)
check(
    "Seasonal 50%-er currently at only 30% off, Autumn Sale ~40 days out",
    advisor.analyse(seasonal, point(TODAY, 200000, 30), today=TODAY, name="Seasonal"),
    "WAIT",
    "typical sale is deeper and a major sale is close",
)

# --------------------------------------------------------------- 3. at all-time low
check(
    "Currently deeper than any past sale (55% vs best 50%)",
    advisor.analyse(seasonal, point(TODAY, 200000, 55), today=TODAY, name="Seasonal"),
    "BUY_NOW",
    "beats the record",
)

# --------------------------------------------------------------- 4. matches typical
check(
    "Currently at its usual 50% depth",
    advisor.analyse(seasonal, point(TODAY, 200000, 50), today=TODAY, name="Seasonal"),
    "BUY_NOW",
    "as good as it normally gets",
)

# --------------------------------------------------------------- 5. frequent discounter
frequent_dates = [TODAY - timedelta(days=30 * i) for i in range(12, 0, -1)]
frequent = sales_history(50000, frequent_dates, cut=25)
check(
    "Discounts every ~30 days to 25%, currently 25% off",
    advisor.analyse(frequent, point(TODAY, 50000, 25), today=TODAY, name="Frequent"),
    "BUY_NOW",
    "no reason to wait for the same number",
)

# --------------------------------------------------------------- 6. new release
check(
    "Released 60 days ago, never discounted",
    advisor.analyse(
        [point(TODAY - timedelta(days=60), 350000, 0)],
        point(TODAY, 350000, 0),
        today=TODAY,
        release=TODAY - timedelta(days=60),
        name="Fresh",
    ),
    "WAIT",
    "first cut usually arrives within a year",
)

# --------------------------------------------------------------- 7. no history
check(
    "No recorded history at all",
    advisor.analyse([], point(TODAY, 99900, 0), today=TODAY, name="Unknown"),
    "UNKNOWN",
    "should refuse to guess",
)

# --------------------------------------------------------------- 8. overdue
overdue_dates = [date(2024, 8, 1), date(2024, 11, 1), date(2025, 2, 1), date(2025, 5, 1)]
overdue = sales_history(150000, overdue_dates, cut=40)
check(
    "Discounts ~every 90 days, none for 470 days, currently only 10% off",
    advisor.analyse(overdue, point(TODAY, 150000, 10), today=TODAY, name="Overdue"),
    "WAIT",
    "overdue for its usual sale",
)

# --------------------------------------------------------------- 9. price hike
hiked = [
    point(date(2026, 6, 1), 129900, 0),
    point(date(2026, 8, 10), 240000, 0),   # publisher raised INR price
]
hike_verdict = advisor.analyse(hiked, point(TODAY, 240000, 0), today=TODAY, name="Hiked")
check(
    "Base price raised 12 days ago (INR repricing)",
    hike_verdict,
    "NEUTRAL",
    "must warn about the 30-day discount lock",
)
if not hike_verdict.price_hike:
    FAILURES.append("price hike not detected")
if not any("blocks discounts" in r for r in hike_verdict.reasons):
    FAILURES.append("price hike cooldown not explained")

# --------------------------------------------------------------- summary
print("=" * 70)
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S):")
    for failure in FAILURES:
        print(f"  - {failure}")
    sys.exit(1)
print("All archetype cases passed.")
