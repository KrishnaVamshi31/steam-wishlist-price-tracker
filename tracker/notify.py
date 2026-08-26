"""Delivery channels: Telegram and Windows toast notifications."""
import html
import threading
import time

import requests

from . import config

TG_API = "https://api.telegram.org"


def telegram_configured() -> bool:
    return bool(config.secret("TELEGRAM_BOT_TOKEN") and config.secret("TELEGRAM_CHAT_ID"))


def send_telegram(text: str, disable_preview: bool = True) -> bool:
    """Send one HTML-formatted Telegram message. Long messages are split."""
    token = config.secret("TELEGRAM_BOT_TOKEN")
    chat_id = config.secret("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False

    chunks, current = [], ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > 3800:
            chunks.append(current)
            current = ""
        current += line + "\n"
    if current.strip():
        chunks.append(current)

    ok = True
    for chunk in chunks:
        try:
            r = requests.post(
                f"{TG_API}/bot{token}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": chunk,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": disable_preview,
                },
                timeout=30,
            )
            if r.status_code != 200:
                print(f"  Telegram error {r.status_code}: {r.text[:200]}")
                ok = False
        except requests.RequestException as exc:
            print(f"  Telegram send failed: {exc}")
            ok = False
        time.sleep(0.4)
    return ok


def send_toast(title: str, body: str, url: str | None = None, wait: float = 6.0) -> bool:
    """Windows notification. Clicking it opens the Steam store page.

    win11toast blocks for the full lifetime of the notification and prints its
    click result to stdout, so run it on a daemon thread with a cap: a scheduled
    run must never wedge because nobody dismissed a popup.
    """
    try:
        from win11toast import toast
    except ImportError:
        return False

    result = {"ok": False}

    def _show():
        try:
            kwargs = {"app_id": "Game Price Tracker", "duration": "short"}
            if url:
                # A string on_click makes the toast open that URL when clicked.
                # win11toast also swaps its own handler to the builtin print, so an
                # activation may echo a dict to the console — cosmetic, and worth it
                # to keep click-to-open-store working. Do NOT wrap this in
                # contextlib.redirect_stdout: that patches sys.stdout process-wide,
                # not per-thread, and would swallow the caller's output too.
                kwargs["on_click"] = url
            toast(title, body, **kwargs)
            result["ok"] = True
        except Exception as exc:  # toast backends vary wildly by Windows build
            result["error"] = str(exc)

    thread = threading.Thread(target=_show, daemon=True)
    thread.start()
    thread.join(timeout=wait)
    if "error" in result:
        print(f"  Toast failed: {result['error']}")
        return False
    return True


def esc(text: str) -> str:
    return html.escape(str(text), quote=False)
