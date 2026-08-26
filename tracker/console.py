"""Windows terminals default to cp1252, which cannot encode the rupee sign.
Import this before printing anything with currency in it."""
import sys


def init() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass
