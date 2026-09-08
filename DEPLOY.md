# Deploying the dashboard

The app is prepared for hosting. What remains needs your Streamlit and GitHub
sign-in, so it has to be done by you — four clicks and a paste.

## 1. Merge the prep branch

https://github.com/KrishnaVamshi31/steam-wishlist-price-tracker/pull/1

Or deploy straight from the `deploy-ready` branch and merge later.

## 2. Deploy

1. Go to https://share.streamlit.io and sign in with GitHub.
2. **Create app** → **Deploy a public app from GitHub**.
3. Fill in:
   - Repository: `KrishnaVamshi31/steam-wishlist-price-tracker`
   - Branch: `main` (or `deploy-ready`)
   - Main file path: `streamlit_app.py`
4. Under **Advanced settings**, set Python to **3.12** — that's what the daily
   workflow already uses and is known good. Paste any secrets you want (below).
5. **Deploy.**

First build takes a couple of minutes while it installs requirements.

## 3. Secrets (all optional)

In **Advanced settings → Secrets**, in TOML format:

```toml
ITAD_API_KEY = "your-key"
```

- `ITAD_API_KEY` — only one worth adding for the hosted copy. It gives the
  buy-or-wait model years of real price history instead of the few weeks this
  tracker has recorded itself. Free key: https://isthereanydeal.com/apps/new/
- `ANTHROPIC_API_KEY` + `ENABLE_PUBLIC_CHAT = "true"` — only if you want the Ask
  page live publicly. **This bills your key for anyone who opens the page.**
  Leave both out unless you mean it.
- Telegram keys are pointless here — alerts are sent by the daily GitHub Actions
  run, not by the dashboard.

Secrets for the **daily workflow** are separate: repository
Settings → Secrets and variables → Actions.

## What the hosted copy does and doesn't do

| | Hosted | Local |
|---|---|---|
| View prices, verdicts, history | yes | yes |
| Data freshness | daily, via GitHub Actions | on demand |
| "Check prices now" | hidden | yes |
| Settings | read-only summary | full editor |
| Ask (chat) | off unless enabled | yes |

The host rebuilds the filesystem from git on every restart, so nothing written at
runtime survives. That is why the write paths are disabled rather than left to
fail quietly. The data you see comes from `data/prices.db`, which the daily
workflow commits back to the repo.

## Note on privacy

The repo is public and `data/prices.db` is committed to it, so your wishlist and
its price history are already public. Deploying does not expose anything new — but
if you would rather it were private, make the repo private first (the daily
workflow keeps working; Streamlit Community Cloud can still deploy from a private
repo once you grant it access).
