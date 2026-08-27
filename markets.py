"""Stock market hours tracker."""

from datetime import datetime, time as dtime
import pytz

_MARKETS = [
    ("NYSE",  "🇺🇸", "America/New_York",  dtime(9, 30),  dtime(16, 0)),
    ("LSE",   "🇬🇧", "Europe/London",     dtime(8, 0),   dtime(16, 30)),
    ("XETRA", "🇩🇪", "Europe/Berlin",     dtime(9, 0),   dtime(17, 30)),
    ("TSE",   "🇯🇵", "Asia/Tokyo",        dtime(9, 0),   dtime(15, 30)),
    ("HKEX",  "🇭🇰", "Asia/Hong_Kong",    dtime(9, 30),  dtime(16, 0)),
]


def get_market_status() -> list[dict]:
    out = []
    for name, flag, tz_name, opens, closes in _MARKETS:
        now = datetime.now(pytz.timezone(tz_name))
        is_weekday = now.weekday() < 5
        is_open = is_weekday and opens <= now.time() < closes
        out.append({
            "name":  name,
            "flag":  flag,
            "open":  is_open,
            "local": now.strftime("%H:%M"),
        })
    return out


def open_market_summary() -> str:
    """One-line context string for the AI prompt."""
    status = get_market_status()
    open_names = [s["name"] for s in status if s["open"]]
    if not open_names:
        return "No major markets currently open (outside trading hours)."
    return f"Currently open markets: {', '.join(open_names)}."
