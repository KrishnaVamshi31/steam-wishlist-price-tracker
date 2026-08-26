"""Main dashboard view."""
import subprocess
import sys
from datetime import date

import altair as alt
import pandas as pd
import streamlit as st

from tracker import config, db, insight, salecalendar

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
def wishlist_value_trend() -> list[float]:
    """Total cost of the wishlist over time — the sparkline under the headline metric."""
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT appid, ts, final FROM price_history ORDER BY ts"
        ).fetchall()
    if not rows:
        return []
    frame = pd.DataFrame([dict(r) for r in rows])
    frame["day"] = pd.to_datetime(frame["ts"], format="ISO8601").dt.date
    latest = frame.sort_values("ts").groupby(["day", "appid"], as_index=False).last()
    totals = latest.groupby("day")["final"].sum() / 100
    return [float(v) for v in totals.tail(30)]


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


# ------------------------------------------------------------------ sidebar
with st.sidebar:
    if st.button("Check prices now", icon=":material/refresh:", type="primary", width="stretch"):
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

    st.caption(f"Last check: {meta()['last_run'][:16].replace('T', ' ')}")
    search = st.text_input("Search", placeholder="Filter games", icon=":material/search:")
    only_sale = st.toggle("Only discounted", value=False)


games = load_games()

if games.empty:
    st.title("Wishlist price tracker")
    st.info(
        "No games tracked yet. Head to **Settings**, paste your Steam profile, "
        "and hit Verify.",
        icon=":material/rocket_launch:",
    )
    st.page_link("app_pages/settings.py", label="Open settings", icon=":material/settings:")
    st.stop()

games["discount_percent"] = games["discount_percent"].fillna(0).astype(int)

# ------------------------------------------------------------------ header
on_sale = games[games["discount_percent"] > 0]
total_now = games["final"].fillna(0).sum()
total_full = games["initial"].fillna(games["final"]).fillna(0).sum()
saving = total_full - total_now
trend = wishlist_value_trend()

st.title("Wishlist price tracker")

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
        "Cost to buy all",
        money(total_now),
        border=True,
        chart_data=trend if len(trend) > 1 else None,
        chart_type="line",
    )
    st.metric(
        "Saved vs full price",
        money(saving),
        f"{saving / total_full * 100:.0f}% off" if total_full else None,
        border=True,
    )

# ------------------------------------------------------------------ verdicts
verdicts = load_verdicts()
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
