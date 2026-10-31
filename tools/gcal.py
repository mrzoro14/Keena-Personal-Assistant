"""His calendar by voice: "what's on today?", "anything tomorrow?", "what's my next meeting?", "what's on Friday?".

Read from Google Calendar's private address (Calendar settings -> his calendar -> "Secret address in iCal format"),
pasted once into .env as GCAL_ICS_URL (several calendars: separated by commas). Read-only, no sign-in, and the
address stays in .env (anyone with it can read the calendar). Repeating events are expanded properly.
(Named gcal, not calendar: that's a Python module of its own.)
"""
import os
import re
import time
import urllib.request
from datetime import date, datetime, timedelta

URLS = [u.strip() for u in os.getenv("GCAL_ICS_URL", "").split(",") if u.strip()]
CACHE_FOR = 300   # seconds: "what's on today" then "and tomorrow?" doesn't download it twice
NOT_SET_UP = ("Your calendar isn't connected yet. In Google Calendar's settings, copy your calendar's secret "
              "address in iCal format into the settings file as GCAL_ICS_URL.")
DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_cache = {"at": 0, "cals": []}


def ready():
    return bool(URLS)


def _calendars():
    if time.time() - _cache["at"] < CACHE_FOR and _cache["cals"]:
        return _cache["cals"]
    from icalendar import Calendar
    cals = []
    for url in URLS:
        req = urllib.request.Request(url.replace("webcal://", "https://"), headers={"User-Agent": "Edith"})
        with urllib.request.urlopen(req, timeout=10) as r:
            cals.append(Calendar.from_ical(r.read()))
    _cache.update(at=time.time(), cals=cals)
    return cals


def _local(dt):
    """Event start as local time (an aware time converted; a floating one taken as is)."""
    return dt.astimezone().replace(tzinfo=None) if dt.tzinfo else dt


def events(start, end):
    """[(start, end, title, location, all_day)] between two local datetimes, earliest first."""
    import recurring_ical_events
    found = []
    for cal in _calendars():
        for ev in recurring_ical_events.of(cal).between(start, end):
            s, e = ev.get("DTSTART").dt, ev.get("DTEND").dt if ev.get("DTEND") else None
            all_day = not isinstance(s, datetime)
            s = datetime.combine(s, datetime.min.time()) if all_day else _local(s)
            e = (datetime.combine(e, datetime.min.time()) if e and not isinstance(e, datetime) else _local(e)) if e else s
            title = re.sub(r"\s+", " ", str(ev.get("SUMMARY") or "Busy")).strip()
            where = re.sub(r"\s+", " ", str(ev.get("LOCATION") or "")).strip().split(",")[0]
            if str(ev.get("STATUS") or "").upper() != "CANCELLED":
                found.append((s, e, title, where, all_day))
    return sorted(found, key=lambda x: (not x[4], x[0]))


def _clock(t):
    """14:30 -> '2:30 PM', 9:00 -> '9 AM'."""
    h = t.hour % 12 or 12
    return f"{h}{':%02d' % t.minute if t.minute else ''} {'AM' if t.hour < 12 else 'PM'}"


def _which_day(day):
    """'today' / 'tomorrow' / 'friday' / '2026-11-03' -> (date, how to say it)."""
    d = (day or "today").strip().lower()
    today = date.today()
    if d in ("", "today", "tonight", "this morning", "this afternoon", "this evening"):
        return today, "today"
    if d in ("tomorrow", "tmrw", "tomorrow morning"):
        return today + timedelta(days=1), "tomorrow"
    if d.removeprefix("on ").removeprefix("this ").removeprefix("next ") in DAYS:
        name = d.split()[-1]
        ahead = (DAYS.index(name) - today.weekday()) % 7 or (7 if d.startswith("next") else 0)
        when = today + timedelta(days=ahead)
        return when, "today" if ahead == 0 else f"on {name.title()}"
    try:
        when = date.fromisoformat(d)
        return when, f"on {when.strftime('%A %d %B').replace(' 0', ' ')}"
    except ValueError:
        return today, "today"


def _which_span(day):
    """'weekend' / 'this week' / 'next week' -> (first date, day after the last, how to say it); else None."""
    d = (day or "").strip().lower()
    today = date.today()
    if "weekend" in d:
        sat = today + timedelta(days=(5 - today.weekday()) % 7 if today.weekday() != 6 else -1)
        if "next" in d:
            sat += timedelta(days=7)
        return max(sat, today), sat + timedelta(days=2), "this weekend" if "next" not in d else "next weekend"
    if re.search(r"\bnext week\b", d):
        mon = today + timedelta(days=7 - today.weekday())
        return mon, mon + timedelta(days=7), "next week"
    if re.search(r"\b(?:this|the) week\b|\bweek\b", d):
        return today, today + timedelta(days=7 - today.weekday()), "this week"
    return None


def _say(ev, with_day=False):
    s, _, title, where, all_day = ev
    at = "all day" if all_day else f"at {_clock(s)}"
    day = f"{s.strftime('%A')} " if with_day else ""
    return f"{title} {day}{at}" + (f" at {where}" if where and len(where) < 40 else "")


def calendar_day(day="today", **_):
    """What's on his calendar for one day."""
    if not URLS:
        return NOT_SET_UP
    span = _which_span(day)
    when, said = (span[0], span[2]) if span else _which_day(day)
    last = span[1] if span else when + timedelta(days=1)
    start = datetime.combine(when, datetime.min.time())
    if when == date.today():
        start = max(start, datetime.now() - timedelta(hours=1))   # "what's on today" at 6 PM: not this morning's
    try:
        evs = events(start, datetime.combine(last, datetime.min.time()))
        if span:   # several days: name the day with each one, in order
            evs.sort(key=lambda e: e[0])
            items = [_say(e, with_day=True) for e in evs[:8]]
            if not evs:
                return f"Nothing on your calendar {said}."
            more = f", and {len(evs) - 8} more" if len(evs) > 8 else ""
            return f"{said[0].upper() + said[1:]}: " + ("; ".join(items[:-1]) + " and " + items[-1]
                                                        if len(items) > 1 else items[0]) + more + "."
    except Exception as e:
        print(f"   [calendar: {e!r}]")
        return "I couldn't reach your calendar just now."
    if not evs:
        return "Nothing else on your calendar today." if start.date() == date.today() and start.hour >= 6 else \
            f"Nothing on your calendar {said}."
    items = [_say(e) for e in evs[:6]]
    more = f", and {len(evs) - 6} more" if len(evs) > 6 else ""
    listed = "; ".join(items[:-1]) + " and " + items[-1] if len(items) > 1 else items[0]
    return f"{said[0].upper() + said[1:]}: {listed}{more}."


def next_event(**_):
    """His next thing on the calendar, in the next two weeks."""
    if not URLS:
        return NOT_SET_UP
    now = datetime.now()
    try:
        evs = [e for e in events(now, now + timedelta(days=14)) if not e[4] and e[0] >= now - timedelta(minutes=5)]
    except Exception as e:
        print(f"   [calendar: {e!r}]")
        return "I couldn't reach your calendar just now."
    if not evs:
        return "Nothing coming up on your calendar in the next two weeks."
    s = evs[0][0]
    mins = int((s - now).total_seconds() // 60)
    soon = f"in {mins} minutes" if 0 < mins < 90 else "now" if mins <= 0 else None
    if soon:
        return f"Next is {evs[0][2]}, {soon}" + (f", at {evs[0][3]}." if evs[0][3] else ".")
    return f"Next is {_say(evs[0], with_day=s.date() != date.today())}."


def today_line():
    """For the morning briefing: '' when nothing's on or it isn't set up."""
    if not URLS:
        return ""
    said = calendar_day("today")
    return "" if said.startswith(("Nothing", "I couldn't")) else said.replace("Today:", "On your calendar today:", 1)


TOOLS = [
    {"name": "calendar_day",
     "description": "What's on his Google Calendar ('what's on today', 'am I free tomorrow', 'what do I have on "
                    "Friday', 'anything planned this weekend'). day: today, tomorrow, a weekday, YYYY-MM-DD, "
                    "weekend, this week or next week.",
     "input_schema": {"type": "object", "properties": {"day": {"type": "string"}}, "required": []}},
    {"name": "next_event", "description": "His next meeting or event on his calendar ('what's my next meeting').",
     "input_schema": {"type": "object", "properties": {}, "required": []}},
]
FUNCTIONS = {"calendar_day": calendar_day, "next_event": next_event}
