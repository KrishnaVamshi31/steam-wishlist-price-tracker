"""Setup and settings — everything that used to require editing JSON by hand."""
import subprocess
import sys

import pandas as pd
import streamlit as st

from tracker import chat, config, db, itad, notify, steam

CFG = config.load()

REGIONS = {
    "India (₹ INR)": ("in", "₹"),
    "United States ($ USD)": ("us", "$"),
    "United Kingdom (£ GBP)": ("uk", "£"),
    "Europe (€ EUR)": ("de", "€"),
    "Brazil (R$ BRL)": ("br", "R$"),
    "Russia (₽ RUB)": ("ru", "₽"),
}


def _run(*args: str) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, "track.py", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(config.ROOT),
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


st.title("Settings")
st.caption("Everything here writes to `config.json` or `.env` in the project folder.")

# ------------------------------------------------------------------ account
with st.container(border=True):
    st.subheader(":material/account_circle: Steam account")

    profile = st.text_input(
        "Your Steam profile",
        value=CFG.get("steam_profile", ""),
        placeholder="https://steamcommunity.com/id/yourname",
        help="Paste your profile URL, your custom URL name, or a bare SteamID64.",
    )

    left, right = st.columns([1, 1])
    with left:
        verify = st.button(
            "Verify and save", icon=":material/check_circle:", type="primary", width="stretch"
        )
    with right:
        recheck = st.button("Fetch prices now", icon=":material/download:", width="stretch")

    if verify:
        if not profile.strip():
            st.error("Enter your profile URL first.")
        else:
            with st.spinner("Asking Steam..."):
                try:
                    steamid = steam.resolve_steamid(profile)
                    items = steam.fetch_wishlist(steamid)
                except steam.SteamError as exc:
                    st.error(f"Could not read that profile: {exc}")
                    steamid, items = None, []

            if steamid:
                CFG["steam_profile"] = profile.strip()
                config.save(CFG)
                st.success(f"Resolved to SteamID64 `{steamid}` and saved.")
                if items:
                    st.success(f"Found {len(items)} games on your wishlist.")
                else:
                    st.warning(
                        "Profile resolved, but the wishlist came back empty. If that's "
                        "not right, the **Game details** privacy setting is the cause — "
                        "it's separate from overall profile privacy.",
                        icon=":material/lock:",
                    )
                    st.link_button(
                        "Open Steam privacy settings",
                        f"https://steamcommunity.com/profiles/{steamid}/edit/settings",
                        icon=":material/open_in_new:",
                    )

    if recheck:
        with st.status("Fetching prices from Steam...", expanded=True) as status:
            code, output = _run("check")
            st.code(output, language="text")
            status.update(
                label="Done" if code == 0 else "Failed",
                state="complete" if code == 0 else "error",
            )
        st.cache_data.clear()

# ------------------------------------------------------------------ region
with st.container(border=True):
    st.subheader(":material/public: Store region")
    current = CFG.get("country_code", "in")
    names = list(REGIONS)
    index = next(
        (i for i, n in enumerate(names) if REGIONS[n][0] == current), 0
    )
    picked = st.selectbox("Prices are tracked in this region's store", names, index=index)
    code, symbol = REGIONS[picked]
    if code != current:
        st.warning(
            "Changing region makes previously recorded prices incomparable — they were "
            "stored in the old currency.",
            icon=":material/warning:",
        )
    if st.button("Save region", icon=":material/save:"):
        CFG["country_code"], CFG["currency_symbol"] = code, symbol
        config.save(CFG)
        st.success(f"Region set to {picked}.")

# ------------------------------------------------------------------ alerts
with st.container(border=True):
    st.subheader(":material/notifications: When to alert me")

    min_disc = st.slider(
        "Alert at this discount or deeper",
        min_value=5,
        max_value=90,
        value=int(CFG.get("min_discount_percent", 20)),
        step=5,
        format="%d%%",
    )
    record_low = st.toggle(
        "Also alert on a new all-time low",
        value=bool(CFG.get("alert_on_record_low", True)),
        help="Fires when a game beats the lowest price recorded here.",
    )
    low_floor = st.slider(
        "…but only if the discount is at least",
        min_value=0,
        max_value=50,
        value=int(CFG.get("record_low_min_discount", 10)),
        step=5,
        format="%d%%",
        disabled=not record_low,
        help="Stops a trivial 2% dip being announced as a record low.",
    )

    st.markdown("**Channels**")
    with st.container(horizontal=True):
        tg = st.toggle("Telegram", value=CFG.get("notify", {}).get("telegram", True))
        toast = st.toggle("Desktop notification", value=CFG.get("notify", {}).get("toast", True))

    if st.button("Save alert settings", icon=":material/save:"):
        CFG["min_discount_percent"] = min_disc
        CFG["alert_on_record_low"] = record_low
        CFG["record_low_min_discount"] = low_floor
        CFG["notify"] = {"telegram": tg, "toast": toast}
        config.save(CFG)
        st.success("Alert settings saved.")

# ------------------------------------------------------------------ targets
with st.container(border=True):
    st.subheader(":material/target: Price targets")
    st.caption("Get told the moment a specific game drops to your number. Blank = no target.")

    with db.connect() as conn:
        rows = conn.execute(
            "SELECT appid, name FROM games WHERE on_wishlist=1 ORDER BY name"
        ).fetchall()

    if not rows:
        st.caption("Nothing tracked yet — verify your profile and fetch prices first.")
    else:
        targets = CFG.get("target_prices", {}) or {}
        frame = pd.DataFrame(
            [
                {
                    "Game": r["name"],
                    "Target": float(targets[str(r["appid"])])
                    if str(r["appid"]) in targets
                    else None,
                    "appid": r["appid"],
                }
                for r in rows
            ]
        )
        edited = st.data_editor(
            frame,
            hide_index=True,
            column_config={
                "Game": st.column_config.TextColumn("Game", disabled=True),
                "Target": st.column_config.NumberColumn(
                    f"Target ({CFG.get('currency_symbol', '₹')})",
                    min_value=0,
                    step=50,
                    format="%.0f",
                ),
                "appid": None,
            },
            key="targets_editor",
        )
        if st.button("Save targets", icon=":material/save:"):
            saved = {
                str(int(row["appid"])): float(row["Target"])
                for _, row in edited.iterrows()
                if pd.notna(row["Target"]) and row["Target"] > 0
            }
            CFG["target_prices"] = saved
            config.save(CFG)
            st.success(f"Saved {len(saved)} target price(s).")

# ------------------------------------------------------------------ secrets
with st.container(border=True):
    st.subheader(":material/key: Connections")
    st.caption(
        "Stored in `.env` on this machine only. It's gitignored, and each value is "
        "sent only to the service it belongs to."
    )

    tg_tab, itad_tab, claude_tab = st.tabs(["Telegram", "IsThereAnyDeal", "Claude (chat)"])

    with tg_tab:
        st.markdown(
            "Message [@BotFather](https://t.me/BotFather), send `/newbot`, and paste "
            "the token it gives you."
        )
        token = st.text_input(
            "Bot token",
            value=config.secret("TELEGRAM_BOT_TOKEN"),
            type="password",
            placeholder="8123456789:AAH…",
        )
        chat = st.text_input(
            "Chat ID",
            value=config.secret("TELEGRAM_CHAT_ID"),
            placeholder="Leave blank and use Find my chat ID",
        )

        c1, c2, c3 = st.columns(3)
        with c1:
            if st.button("Save", icon=":material/save:", width="stretch"):
                config.write_env(
                    {"TELEGRAM_BOT_TOKEN": token.strip(), "TELEGRAM_CHAT_ID": chat.strip()}
                )
                st.success("Saved to .env")
        with c2:
            if st.button("Find my chat ID", icon=":material/search:", width="stretch"):
                if not token.strip():
                    st.error("Save your bot token first.")
                else:
                    config.write_env({"TELEGRAM_BOT_TOKEN": token.strip()})
                    st.info("Send your bot a message in Telegram, then press this again.")
                    proc = subprocess.run(
                        [sys.executable, "setup_telegram.py"],
                        capture_output=True, text=True, encoding="utf-8",
                        errors="replace", cwd=str(config.ROOT),
                    )
                    st.code((proc.stdout or "") + (proc.stderr or ""), language="text")
        with c3:
            if st.button("Send test", icon=":material/send:", width="stretch"):
                if notify.telegram_configured():
                    ok = notify.send_telegram(
                        "<b>🎮 Game Price Tracker</b>\n\nTelegram is wired up correctly."
                    )
                    st.success("Sent — check Telegram.") if ok else st.error("Failed to send.")
                else:
                    st.error("Save a token and chat ID first.")

    with itad_tab:
        st.markdown(
            "A free key from [IsThereAnyDeal](https://isthereanydeal.com/apps/new/) gives "
            "the buy-or-wait model **years** of real price history instead of only what "
            "this tracker has recorded. It is the single biggest upgrade available here."
        )
        key = st.text_input(
            "API key", value=config.secret("ITAD_API_KEY"), type="password"
        )
        if st.button("Save key", icon=":material/save:"):
            config.write_env({"ITAD_API_KEY": key.strip()})
            st.success("Saved to .env")
        st.caption(
            f"Status: {'connected' if itad.configured() else 'not configured'}"
        )

    with claude_tab:
        st.markdown(
            "Powers the **Ask** page, where you can ask questions about your wishlist "
            "in plain English. Get a key from "
            "[console.anthropic.com](https://console.anthropic.com/settings/keys). "
            "Billed per message — these queries are small, typically a fraction of a "
            "cent each."
        )
        anthropic_key = st.text_input(
            "Anthropic API key", value=config.secret("ANTHROPIC_API_KEY"),
            type="password", placeholder="sk-ant-…",
        )
        if st.button("Save Claude key", icon=":material/save:"):
            config.write_env({"ANTHROPIC_API_KEY": anthropic_key.strip()})
            st.success("Saved to .env")
        st.caption(
            f"Status: {'connected' if chat.configured() else 'not configured'}"
        )

# ------------------------------------------------------------------ danger
with st.expander("Advanced"):
    st.markdown("**Extra games to track** (appids not on your wishlist, comma separated)")
    extra = st.text_input(
        "Extra appids",
        value=", ".join(str(a) for a in CFG.get("extra_appids", [])),
        label_visibility="collapsed",
        placeholder="1145360, 367520",
    )
    if st.button("Save extra appids"):
        try:
            CFG["extra_appids"] = [
                int(x.strip()) for x in extra.split(",") if x.strip()
            ]
            config.save(CFG)
            st.success("Saved.")
        except ValueError:
            st.error("Those need to be plain numbers — find the appid in the store URL.")

    st.markdown("**Notification test**")
    if st.button("Send a test desktop notification"):
        ok = notify.send_toast("Game Price Tracker", "Desktop notifications are working.")
        st.success("Sent.") if ok else st.error("Failed — see console output.")
