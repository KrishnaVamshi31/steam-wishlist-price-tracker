# Steam wishlist price tracker

Tracks the price of every game on your Steam wishlist in **INR**, records the full
price history, and alerts you — via Telegram, Windows notification, and a local
dashboard — when something drops. A daily Claude task additionally searches the web
for upcoming Steam sales and publisher discounts so you know whether to buy now or wait.

## Setup

**1. Install dependencies**

This project uses the `.venv` in the project root:

```bash
.venv\Scripts\activate
```

Then, if anything is missing:

```bash
pip install -r requirements.txt
```

If you see `ModuleNotFoundError`, the venv isn't active. You can skip activation
entirely by calling its Python directly: `.venv\Scripts\python.exe track.py check`

**2. Open the app and use the Settings page**

```bash
streamlit run streamlit_app.py
```

Go to **Settings** and paste your Steam profile URL (or custom URL name, or bare
SteamID64), then press **Verify and save**. It resolves the ID, checks how many
wishlist games are visible, and writes `config.json` for you. Everything else —
region, alert thresholds, per-game price targets, Telegram, IsThereAnyDeal — is on
that page too. No hand-editing required.

**If Verify reports an empty wishlist**, the cause is almost always Steam's
**Game details** privacy setting, which is separate from overall profile privacy.
The page links you straight to it. Set *Game details* to **Public**.

**3. Optional connections**

- **Telegram** — message [@BotFather](https://t.me/BotFather), send `/newbot`, paste the
  token into Settings, then use *Find my chat ID*. Test it with *Send test*.
- **IsThereAnyDeal** — a free key from [here](https://isthereanydeal.com/apps/new/) gives the
  buy-or-wait model years of real price history instead of only what this tracker has
  recorded. Biggest single upgrade available.

Both are stored in `.env`, which is gitignored and never leaves this machine.

## Daily use

```bash
python track.py check
```

Fetches prices, records changes, and notifies you about drops. This is the one command
that matters — everything else reads what it wrote.

```bash
streamlit run streamlit_app.py
```

Opens the dashboard at http://localhost:8501 — buy-or-wait verdicts, cover art, a
sale countdown, discount bars, price-history charts, and gathered sale intel.
Two pages: **Overview** and **Settings**.

### Other commands

| Command | Does |
|---|---|
| `python track.py advise` | **Buy-now-or-wait verdicts** from the cadence model |
| `python tests/test_advisor.py` | Archetype tests for the cadence model |
| `python tests/test_chat_loop.py` | Chat tool-loop tests (offline, no API cost) |
| `python track.py list` | Print tracked games and prices in the terminal |
| `python track.py report` | Rebuild `reports/latest.md` |
| `python track.py digest` | Telegram the current list of discounted games |
| `python track.py test` | Verify Telegram and desktop notifications |
| `python track.py whoami` | Check profile resolution and wishlist visibility |
| `python track.py note "..."` | Record sale intel (used by the daily research task) |

## Ask (chat)

The **Ask** page answers questions about your wishlist in plain English — "what's
worth buying right now?", "which of my games are most expensive?", "should I wait
on Elden Ring?".

It runs on Claude with three tools:

- **`query_database`** — read-only SQL against the tracker's SQLite file
- **`get_buy_advice`** — the discount-cadence model below
- **`get_sale_calendar`** — upcoming Steam sale windows

Add an Anthropic API key in **Settings → Connections → Claude**. Queries are small,
typically a fraction of a cent each.

The SQL tool is read-only at two levels: statements are validated to be `SELECT`
only, *and* the connection is opened with SQLite's `mode=ro`, so even a validation
bypass cannot modify the database. Results are capped at 60 rows per query.

## Buy now or wait?

The part Steam and the deal aggregators do not do. `track.py advise` weighs each
game's own discount history against the Steam sale calendar and tells you whether
the current price is worth taking:

```bash
python track.py advise
```

It reasons about:

- **Depth vs. history** — is this discount as deep as this game normally goes?
- **Cadence** — how often does it discount, and is one overdue?
- **Calendar proximity** — is a store-wide sale close enough to be worth waiting for?
- **Never-discounters** — some games simply do not go on sale; waiting is wasted.
- **Unreleased and new titles** — no history is not the same as no discount.
- **Price hikes** — Steam blocks discounts for 30 days after a publisher raises the
  base price. Relevant in India, where INR repricing has been volatile.

Verdicts are `BUY NOW`, `WAIT`, or no action, each with a confidence level and its
reasoning. Add `--all` to include quiet games, `--telegram` to push the verdicts,
`--only WAIT` to filter.

**Confidence depends on history.** With only this tracker's own data the model is
honest but thin for the first months. A free [IsThereAnyDeal](https://isthereanydeal.com/apps/new/)
key in `.env` gives it years of real price history immediately — this is the single
highest-value thing you can add.

## Tuning what you get alerted about

Everything lives in `config.json`:

| Setting | Meaning |
|---|---|
| `min_discount_percent` | Alert at this discount or deeper. Default 20. |
| `target_prices` | Per-game price targets in rupees, keyed by appid: `{"1145360": 500}` alerts when Hades hits ₹500. Fires once per crossing, not daily. |
| `alert_on_record_low` | Alert when a game beats the lowest price ever recorded here. |
| `record_low_min_discount` | A record low must be at least this deep to alert. Stops trivial 2% dips being announced. |
| `record_low_min_history` | Needs this many price observations before "record low" means anything. Prevents a burst of fake all-time-lows in week one. |
| `extra_appids` | Track games that aren't on your wishlist. Find the appid in the store URL. |
| `notify` | Turn `telegram` or `toast` off individually. |

Alerts fire **once per crossing**. A game staying on sale for a week won't message you
every day; a deeper cut will.

## The daily automated check

`DAILY_TASK.md` is the prompt for a scheduled Claude task that runs the price check,
searches the web for upcoming sale news, records what it finds, and messages you only
when something is worth acting on. Price tracking is deterministic Python; the sale
research and the buy-now-vs-wait judgment are the parts that need a model.

## How it works

- **Prices** come from Steam's public store API (`appdetails`), batched ~20 games per
  request with backoff, in the region set by `country_code`. No API key needed.
- **The wishlist** comes from `IWishlistService/GetWishlist`, falling back to the older
  store endpoint. No API key, but the profile must be public.
- **History** is in SQLite at `data/prices.db`. A row is written only when the price
  actually changes, so the DB stays small and the charts stay meaningful.
- **"Lowest seen"** is the lowest price *this tracker has recorded* — not a true all-time
  low. It gets more accurate the longer it runs. For a real cross-store all-time low, add
  a free [IsThereAnyDeal](https://isthereanydeal.com/apps/new/) key to `.env`.

## Notes and limits

- Prices are stored in paise (₹1,100 → `110000`) to avoid float rounding.
- Steam rate-limits the store API. A wishlist of a few hundred games takes a couple of
  minutes; don't run `check` in a tight loop.
- If a wishlist fetch comes back empty but games are already on record, the tracker keeps
  them rather than wiping the database — a privacy toggle or a blip won't lose your history.
- Free, unreleased, and region-unavailable games return no price and are skipped silently.
