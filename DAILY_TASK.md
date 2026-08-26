# Daily wishlist check — task prompt

This is the prompt the scheduled daily task runs. It does two things the Python
tracker cannot do on its own: search the web for sale news, and judge whether a
given deal is actually worth buying now or worth waiting out.

---

## Steps

**1. Run the price check.**

```bash
cd C:/Users/vamshi/Documents/GamePriceTracker && python track.py check
```

This fetches every wishlist price, records changes, and fires Telegram + desktop
alerts for anything crossing a threshold. Note what it reports.

**2. Read the current state** so you know what to research:

```bash
cd C:/Users/vamshi/Documents/GamePriceTracker && python track.py list
```

**3. Search the web for discount intel.** Cover these angles, skipping any you
already recorded recently (check `reports/latest.md` — don't re-record the same
note twice):

- **Steam-wide sales**: confirmed dates for the next seasonal sale and any themed
  fest in the next ~6 weeks that a wishlist game would be included in.
- **Per-game news**: for the 5–10 most expensive or most-wanted wishlist games,
  search for publisher sales, bundle appearances, franchise sales, or an
  announced discount. Also worth catching: a game going free-to-play, an imminent
  price *rise*, or a delisting.
- **Non-Steam sales for the same games**: Epic, GOG, Fanatical, Humble, GreenManGaming.
  A key that activates on Steam is still a Steam game. Note the price in INR and
  whether the key is region-locked for India.
- **Historical pattern**: for a game that has never been discounted, or discounts
  rarely, say so — that is useful ("Nintendo-style, never goes on sale, buy whenever").

Prefer primary sources (Steam news posts, publisher announcements, SteamDB) over
aggregator blogs. Several sale-calendar sites publish guesses as fact — if sources
disagree on a date, say it is unconfirmed rather than picking one.

**4. Record what you found** so it shows in the dashboard and report:

```bash
python track.py note "HEADLINE" --game "Hollow Knight" --body "detail" --source "SteamDB" --url "https://..." --starts 2026-10-01 --ends 2026-10-08
```

Omit `--game` for anything that applies to the whole wishlist. Omit `--starts`
and `--ends` when no date is known.

**4b. Run the buy-or-wait model** and let it steer what you tell the user:

```bash
python track.py advise
```

Treat its verdicts as the baseline, then adjust with what you found on the web. If
the model says WAIT because a seasonal sale is near but you learned the publisher
is pulling the game from sale, say so — you have context it does not.

**5. Rebuild the report and send the digest.**

```bash
python track.py report
python track.py digest
```

**6. Send a short Telegram summary** of anything genuinely worth acting on — a
notable drop, a sale starting within a week that covers a wishlist game, or a
"buy now vs wait" call. Use `python track.py note` output plus your own judgment.
If nothing is worth acting on, send nothing; a daily "nothing happened" message
trains the user to ignore the channel.

## Judgment guidance

- A game at 20% off two weeks before a seasonal sale is usually worth waiting on —
  seasonal sales typically match or beat interim discounts.
- A game at or near its all-time low is worth flagging as buy-now.
- Never claim a sale is confirmed when only aggregator sites report it.
- Prices in this project are in INR paise internally (₹1,100 is stored as 110000).
