"""The morning briefing: the first time she starts each day (his login), a short spoken rundown.

Greeting, weather where he is, today's reminders and calendar, battery if it needs him, and new WhatsApp messages if WhatsApp is
open. Later starts the same day (a restart, a crash) just say hello, so it's never repeated. Each part gets a few
seconds at most: a slow website never holds up "Hey Edith".
"""
import os
import threading
from datetime import date

from brain.persona import startup_greeting

DONE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "briefed_on.txt")
WAIT = 6   # seconds for the slow parts (weather, WhatsApp), together


def _first_start_today():
    today = date.today().isoformat()
    try:
        with open(DONE_FILE, encoding="utf-8") as f:
            if f.read().strip() == today:
                return False
    except OSError:
        pass
    try:
        os.makedirs(os.path.dirname(DONE_FILE), exist_ok=True)
        with open(DONE_FILE, "w", encoding="utf-8") as f:
            f.write(today)
    except OSError:
        pass
    return True


def _weather():
    from tools.extras import get_weather
    return get_weather(quiet=True)


def _calendar():
    from tools.gcal import today_line
    return today_line()


def _whatsapp():
    """'New WhatsApp messages from Arjun and Mom.' Only when WhatsApp's window is open: never opens it for him."""
    from tools import whatsapp as W
    with W._auto().UIAutomationInitializerInThread():
        win = W._window(open_it=False)
        if not win:
            return ""
        new = [r["name"] for r in W._rows(win) if r["unread"] and not r["muted"]]
    if not new:
        return ""
    names = new[0] if len(new) == 1 else ", ".join(new[:2]) + (f" and {len(new) - 2} more" if len(new) > 2 else "")
    if len(new) == 2:
        names = f"{new[0]} and {new[1]}"
    return f"New WhatsApp messages from {names}."


def _battery():
    import psutil
    b = psutil.sensors_battery()
    if not b or b.power_plugged or b.percent > 30:
        return ""
    return f"Battery is at {int(b.percent)} percent, so plug in soon."


def briefing(reminders=()):
    """The words to say at startup."""
    if not _first_start_today():
        return startup_greeting(reminders)
    got = {}

    def run(key, fn):
        try:
            got[key] = fn() or ""
        except Exception as e:
            print(f"   [briefing {key}: {e!r}]")

    threads = [threading.Thread(target=run, args=(k, f), daemon=True)
               for k, f in (("weather", _weather), ("calendar", _calendar), ("whatsapp", _whatsapp))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(WAIT)
    parts = [startup_greeting(reminders), got.get("weather", ""), got.get("calendar", ""), _battery(),
             got.get("whatsapp", "")]
    return " ".join(p for p in parts if p)
