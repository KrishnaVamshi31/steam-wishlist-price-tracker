"""Main dashboard view."""
import subprocess
import sys
from datetime import date

import altair as alt
import pandas as pd
import streamlit as st

from tracker import config, db, insight, live, salecalendar

CFG = config.load()
SYM = CFG.get("currency_symbol", "₹")
HEADER_IMG = "https://cdn.cloudflare.steamstatic.com/steam/apps/{}/header.jpg"
STORE_URL = "https://store.steampowered.com/app/{}/"


def money(paise, dash="—"):
    if paise is None or pd.isna(paise):
        return dash
    return f"{SYM}{paise / 100:,.0f}"


@st.cache_data(ttl=60)
def load_games() -> pd.DataFrame:
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT g.appid, g.name, g.publishers, g.release_date, g.coming_soon,
                   p.final, p.initial, p.discount_percent,
                   (SELECT MIN(final) FROM price_history WHERE appid = g.appid) AS low
            FROM games g
            LEFT JOIN price_history p ON p.id = (
                SELECT id FROM price_history WHERE appid = g.appid ORDER BY ts DESC LIMIT 1
            )
            WHERE g.on_wishlist = 1
            ORDER BY g.name
            """
        ).fetchall()
    return pd.DataFrame([dict(r) for r in rows])


@st.cache_data(ttl=60)
def load_history(appid: int) -> pd.DataFrame:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT ts, final, discount_percent FROM price_history"
            " WHERE appid=? ORDER BY ts",
            (appid,),
        ).fetchall()
    df = pd.DataFrame([dict(r) for r in rows])
    if not df.empty:
        df["ts"] = pd.to_datetime(df["ts"], format="ISO8601")
        df["price"] = df["final"] / 100
    return df


@st.cache_data(ttl=60)
def load_notes() -> pd.DataFrame:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT n.*, g.name AS game FROM notes n LEFT JOIN games g ON g.appid=n.appid"
            " ORDER BY n.ts DESC LIMIT 40"
        ).fetchall()
    return pd.DataFrame([dict(r) for r in rows])


@st.cache_data(ttl=300)
def load_verdicts() -> list[dict]:
    with db.connect() as conn:
        verdicts = insight.advise_all(conn, CFG, use_itad=False)
    return [
        {
            "appid": v.appid, "name": v.name, "action": v.action,
            "confidence": v.confidence, "headline": v.headline,
            "price": v.current_price, "cut": v.current_cut,
            "savings": v.expected_savings, "reasons": list(v.reasons),
        }
        for v in verdicts
    ]


@st.cache_data(ttl=60)
def meta() -> dict:
    with db.connect() as conn:
        return {"last_run": db.get_meta(conn, "last_run", "never")}


@st.cache_data(ttl=300)
def sale_windows():
    with db.connect() as conn:
        return [
            {"name": w.name, "start": w.start, "end": w.end, "major": w.major}
            for w in salecalendar.load(conn)
            if w.end >= date.today()
        ][:4]


# ------------------------------------------------------------------ live lookup
@st.cache_data(ttl=600, show_spinner=False)
def load_live_games(profile: str, country: str):
    """A visitor's wishlist, fetched from Steam. Cached so a reload is not a refetch."""
    steamid, items = live.load_wishlist(profile, country)
    rows = []
    with db.connect() as conn:
        for game in items:
            # "Lowest seen" is per-game, not per-user, so any history this instance
            # recorded applies to a visitor looking at the same game.
            low = conn.execute(
                "SELECT MIN(final) AS low FROM price_history WHERE appid=?", (game.appid,)
            ).fetchone()["low"]
            rows.append(
                {
                    "appid": game.appid, "name": game.name, "publishers": game.publishers,
                    "release_date": game.release_date, "coming_soon": int(game.coming_soon),
                    "final": game.final, "initial": game.initial,
                    "discount_percent": game.discount_percent, "low": low,
                }
            )
    return steamid, rows


@st.cache_data(ttl=600, show_spinner=False)
def load_live_verdicts(profile: str, country: str):
    _, rows = load_live_games(profile, country)
    games = [
        live.LiveGame(
            appid=r["appid"], name=r["name"], final=r["final"], initial=r["initial"],
            discount_percent=r["discount_percent"], release_date=r["release_date"],
            coming_soon=bool(r["coming_soon"]),
        )
        for r in rows
    ]
    with db.connect() as conn:
        verdicts = insight.advise_live(conn, CFG, games)
    return [
        {
            "appid": v.appid, "name": v.name, "action": v.action,
            "confidence": v.confidence, "headline": v.headline, "price": v.current_price,
            "cut": v.current_cut, "savings": v.expected_savings, "reasons": list(v.reasons),
        }
        for v in verdicts
    ]


# ------------------------------------------------------------------ sidebar
READ_ONLY = config.is_read_only()

with st.sidebar:
    if READ_ONLY:
        # Hosted: a fetch here would write to a database that is thrown away on the
        # next restart, and the page is public — anyone could trigger Steam calls.
        # The daily GitHub Actions run is what actually updates the data.
        st.caption("Updated daily by GitHub Actions.")
        st.caption(f"Last check: {meta()['last_run'][:16].replace('T', ' ')}")
    elif st.button(
        "Check prices now", icon=":material/refresh:", type="primary", width="stretch"
    ):
        with st.status("Fetching from Steam...", expanded=True) as status:
            proc = subprocess.run(
                [sys.executable, "track.py", "check"],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", cwd=str(config.ROOT),
            )
            st.code((proc.stdout or "") + (proc.stderr or ""), language="text")
            status.update(
                label="Done" if proc.returncode == 0 else "Failed",
                state="complete" if proc.returncode == 0 else "error",
            )
        st.cache_data.clear()

    st.divider()
    st.markdown("**Your own wishlist**")
    st.caption("Paste a Steam profile to see its prices and verdicts instead.")
    typed = st.text_input(
        "Steam profile",
        value=st.session_state.get("viewer_profile", ""),
        placeholder="steamcommunity.com/id/yourname",
        label_visibility="collapsed",
    )
    look_up, clear = st.columns([2, 1])
    with look_up:
        if st.button("Look up", icon=":material/search:", width="stretch", type="primary"):
            st.session_state.viewer_profile = typed.strip()
            st.rerun()
    with clear:
        if st.button("Reset", width="stretch"):
            st.session_state.pop("viewer_profile", None)
            st.rerun()

    st.divider()
    search = st.text_input("Search", placeholder="Filter games", icon=":material/search:")
    only_sale = st.toggle("Only discounted", value=False)


# ------------------------------------------------------------------ data source
VIEWER = (st.session_state.get("viewer_profile") or "").strip()
steamid = None

if VIEWER:
    try:
        with st.spinner("Reading that wishlist from Steam..."):
            steamid, rows = load_live_games(VIEWER, CFG.get("country_code", "in"))
            verdicts = load_live_verdicts(VIEWER, CFG.get("country_code", "in"))
        games = pd.DataFrame(rows)
    except live.LiveError as exc:
        st.title("Wishlist price tracker")
        st.error(str(exc), icon=":material/error:")
        st.caption("Use the Reset button in the sidebar to go back.")
        st.stop()
else:
    games = load_games()
    verdicts = load_verdicts()

if games.empty:
    st.title("Wishlist price tracker")
    st.info(
        "No games tracked yet. Paste a Steam profile in the sidebar to look one up.",
        icon=":material/rocket_launch:",
    )
    st.stop()

games["discount_percent"] = games["discount_percent"].fillna(0).astype(int)

# ------------------------------------------------------------------ header
on_sale = games[games["discount_percent"] > 0]
total_now = games["final"].fillna(0).sum()
total_full = games["initial"].fillna(games["final"]).fillna(0).sum()
saving = total_full - total_now
buy_now = [v for v in verdicts if v["action"] == "BUY_NOW"]
buy_now_cost = sum(v["price"] or 0 for v in buy_now)

st.title("Wishlist price tracker")

if VIEWER:
    st.caption(
        f"Live prices for [{steamid}](https://steamcommunity.com/profiles/{steamid}/) "
        f"— {len(games)} games, fetched just now. Nothing is stored; close the tab and "
        "it is gone. Use **Reset** in the sidebar to go back to the tracked wishlist."
    )

upcoming = sale_windows()
if upcoming:
    nxt = upcoming[0]
    days = (nxt["start"] - date.today()).days
    if days <= 0 <= (nxt["end"] - date.today()).days:
        st.success(f"**{nxt['name']}** is live now — ends {nxt['end']:%d %b}.",
                   icon=":material/local_fire_department:")
    else:
        st.info(f"**{nxt['name']}** starts in **{days} days** ({nxt['start']:%d %b}).",
                icon=":material/event:")

with st.container(horizontal=True):
    st.metric("Games tracked", len(games), border=True)
    st.metric(
        "On sale now",
        len(on_sale),
        f"{len(on_sale)} of {len(games)}" if len(on_sale) else None,
        border=True,
    )
    st.metric(
        "Worth buying now",
        money(buy_now_cost) if buy_now else "—",
        f"{len(buy_now)} game{'s' if len(buy_now) != 1 else ''}" if buy_now else "nothing yet",
        border=True,
        help="What today's BUY_NOW picks would cost — not the whole wishlist at once.",
    )
    st.metric(
        "Saved vs full price",
        money(saving),
        f"{saving / total_full * 100:.0f}% off" if total_full else None,
        border=True,
    )

# ------------------------------------------------------------------ verdicts
actionable = [v for v in verdicts if v["action"] in ("BUY_NOW", "WAIT")]

st.subheader("Buy now or wait?")
if not actionable:
    st.caption(
        "Nothing decisive yet. Verdicts sharpen as history builds — or immediately "
        "with an IsThereAnyDeal key in Settings."
    )
else:
    for chunk in [actionable[i : i + 2] for i in range(0, len(actionable), 2)]:
        cols = st.columns(2)
        for col, v in zip(cols, chunk):
            with col, st.container(border=True):
                buy = v["action"] == "BUY_NOW"
                st.image(HEADER_IMG.format(v["appid"]))
                st.badge(
                    "Buy now" if buy else "Wait",
                    icon=":material/shopping_cart:" if buy else ":material/schedule:",
                    color="green" if buy else "orange",
                )
                st.markdown(f"**[{v['name']}]({STORE_URL.format(v['appid'])})**")
                st.markdown(f"{v['headline']} — **{money(v['price'])}**")
                for reason in v["reasons"]:
                    st.caption(reason)
                if v["savings"]:
                    st.caption(f"Waiting could save about {money(v['savings'])}.")

# ------------------------------------------------------------------ filters
view = games.copy()
if search:
    view = view[view["name"].str.contains(search, case=False, na=False)]
if only_sale:
    view = view[view["discount_percent"] > 0]

# ------------------------------------------------------------------ deals
if not on_sale.empty:
    st.subheader("On sale right now")
    deals = on_sale.sort_values("discount_percent", ascending=False)
    for chunk in [deals.iloc[i : i + 3] for i in range(0, len(deals), 3)]:
        cols = st.columns(3)
        for col, (_, row) in zip(cols, chunk.iterrows()):
            with col, st.container(border=True):
                st.image(HEADER_IMG.format(row["appid"]))
                st.markdown(f"**[{row['name']}]({STORE_URL.format(row['appid'])})**")
                st.markdown(
                    f":green-badge[-{row['discount_percent']}%] "
                    f"**{money(row['final'])}**  ~~{money(row['initial'])}~~"
                )
                if row["low"] is not None and row["final"] <= row["low"]:
                    st.caption("At its lowest recorded price.")

# ------------------------------------------------------------------ table
st.subheader("All tracked games")
if view.empty:
    st.caption("Nothing matches that filter.")
else:
    table = pd.DataFrame(
        {
            "": view["appid"].map(lambda a: HEADER_IMG.format(a)),
            "Game": view["name"],
            "Open": view["appid"].map(lambda a: STORE_URL.format(a)),
            "Now": view["final"] / 100,
            "Was": view["initial"] / 100,
            "Off": view["discount_percent"],
            "Lowest": view["low"] / 100,
        }
    ).sort_values(["Off", "Game"], ascending=[False, True])
    st.dataframe(
        table,
        hide_index=True,
        column_config={
            "": st.column_config.ImageColumn("", width="small"),
            "Game": st.column_config.TextColumn("Game", width="medium"),
            "Open": st.column_config.LinkColumn("", display_text="Store", width="small"),
            "Now": st.column_config.NumberColumn("Now", format=f"{SYM}%.0f"),
            "Was": st.column_config.NumberColumn("Was", format=f"{SYM}%.0f"),
            "Off": st.column_config.ProgressColumn(
                "Discount", min_value=0, max_value=100, format="%d%%"
            ),
            "Lowest": st.column_config.NumberColumn("Lowest seen", format=f"{SYM}%.0f"),
        },
    )

# ------------------------------------------------------------------ history + intel
left, right = st.columns([3, 2])

with left:
    with st.container(border=True):
        st.subheader("Price history")
        names = view["name"].dropna().tolist() or games["name"].dropna().tolist()
        picked = st.selectbox("Game", names, label_visibility="collapsed")
        row = games[games["name"] == picked].iloc[0]
        hist = load_history(int(row["appid"]))

        if len(hist) < 2:
            st.caption("Only one price point so far — this fills in as the tracker runs.")
            st.metric("Current price", money(row["final"]), border=True)
        else:
            st.altair_chart(
                alt.Chart(hist)
                .mark_line(interpolate="step-after", point=True)
                .encode(
                    x=alt.X("ts:T", title=None),
                    y=alt.Y("price:Q", title=f"Price ({SYM})", scale=alt.Scale(zero=False)),
                    tooltip=[
                        alt.Tooltip("ts:T", title="Date"),
                        alt.Tooltip("price:Q", title="Price", format=",.0f"),
                        alt.Tooltip("discount_percent:Q", title="Discount %"),
                    ],
                )
            )

with right:
    with st.container(border=True):
        st.subheader("Sale calendar")
        for window in upcoming:
            days = (window["start"] - date.today()).days
            label = "Running now" if days <= 0 else f"in {days} days"
            colour = "green" if window["major"] else "blue"
            st.markdown(f"**{window['name']}**")
            st.markdown(
                f":{colour}-badge[{label}] "
                f"{window['start']:%d %b} → {window['end']:%d %b}"
            )

    notes = load_notes()
    if not notes.empty:
        with st.container(border=True):
            st.subheader("Sale intel")
            for _, n in notes.head(6).iterrows():
                scope = n["game"] if isinstance(n["game"], str) and n["game"] else "All wishlist"
                st.markdown(f"**{scope}** — {n['headline']}")
                if isinstance(n["body"], str) and n["body"]:
                    st.caption(n["body"])
