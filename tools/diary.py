"""A daily diary of what he did on the PC: "what did I work on yesterday?", "how long was I on YouTube today?".

Every 30 seconds, while he's actually at the PC (touched the mouse or keyboard in the last 2 minutes), the app in
front and its window title are noted in data/diary.db. Nothing leaves the PC, no screenshots, no typing: just app
names and window titles, kept for DIARY_DAYS (60) days. Private browser windows are noted as "private browsing"
without their title. DIARY=0 in .env turns it off.
"""
import ctypes
import ctypes.wintypes as wt
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "diary.db")
ON = os.getenv("DIARY", "1") != "0"
KEEP_DAYS = int(os.getenv("DIARY_DAYS", "60"))
EVERY = 30        # seconds between looks
AWAY_AFTER = 120  # seconds without mouse or keyboard = he's away, nothing is noted
SKIP = {"lockapp.exe", "searchhost.exe", "shellexperiencehost.exe", "startmenuexperiencehost.exe",
        "applicationframehost.exe", "pythonw.exe", "python.exe", "screenclippinghost.exe", ""}
NAMES = {"msedge.exe": "Edge", "chrome.exe": "Chrome", "firefox.exe": "Firefox", "opera.exe": "Opera GX",
         "code.exe": "VS Code", "unity.exe": "Unity", "unity hub.exe": "Unity Hub", "explorer.exe": "File Explorer",
         "windowsterminal.exe": "Terminal", "powershell.exe": "PowerShell", "cmd.exe": "Command Prompt",
         "winword.exe": "Word", "excel.exe": "Excel", "powerpnt.exe": "PowerPoint", "outlook.exe": "Outlook",
         "whatsapp.exe": "WhatsApp", "whatsapp.root.exe": "WhatsApp", "discord.exe": "Discord",
         "spotify.exe": "Spotify", "steam.exe": "Steam", "steamwebhelper.exe": "Steam", "claude.exe": "Claude",
         "notepad.exe": "Notepad", "devenv.exe": "Visual Studio", "blender.exe": "Blender", "vlc.exe": "VLC",
         "epicgameslauncher.exe": "Epic Games", "riotclientux.exe": "Riot Client", "photoshop.exe": "Photoshop"}
BROWSERS = {"Edge", "Chrome", "Firefox", "Opera GX"}
APP_SUFFIX = re.compile(r"\s+[-–—|]\s+(?:Microsoft​?\s*Edge|Google Chrome|Mozilla Firefox|Opera|Visual Studio Code|"
                        r"Notepad|Word|Excel|PowerPoint|File Explorer|Discord|Personal|Work|Profile \d+)\s*$", re.I)
PRIVATE = re.compile(r"InPrivate|Incognito|Private Browsing", re.I)
SCHEMA = """CREATE TABLE IF NOT EXISTS activity (
    id    INTEGER PRIMARY KEY,
    start TEXT NOT NULL,      -- local time, ISO
    secs  INTEGER NOT NULL,   -- how long (consecutive looks at the same window are one row)
    app   TEXT NOT NULL,
    title TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS activity_start ON activity(start);"""

_user32 = ctypes.WinDLL("user32", use_last_error=True)


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.UINT), ("dwTime", wt.DWORD)]


def _idle_seconds():
    info = _LASTINPUTINFO(ctypes.sizeof(_LASTINPUTINFO))
    if not _user32.GetLastInputInfo(ctypes.byref(info)):
        return 0
    return (ctypes.windll.kernel32.GetTickCount() - info.dwTime) / 1000


def _front():
    """(app name, window title) of the window in front, or None."""
    hwnd = _user32.GetForegroundWindow()
    if not hwnd:
        return None
    buf = ctypes.create_unicode_buffer(512)
    _user32.GetWindowTextW(hwnd, buf, 512)
    pid = wt.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    try:
        import psutil
        exe = psutil.Process(pid.value).name().lower()
    except Exception:
        exe = ""
    if exe in SKIP:
        return None
    app = NAMES.get(exe) or os.path.splitext(exe)[0].replace("_", " ").title()
    title = " ".join(buf.value.split())
    if PRIVATE.search(title):
        title = "private browsing"
    while (shorter := APP_SUFFIX.sub("", title)) != title:   # 'New tab - Personal - Microsoft Edge' -> 'New tab'
        title = shorter
    return app, title[:200]


def _db():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    db = sqlite3.connect(DB, check_same_thread=False)
    db.executescript(SCHEMA)
    return db


def _watch():
    db = _db()
    db.execute("DELETE FROM activity WHERE start < ?", ((datetime.now() - timedelta(days=KEEP_DAYS)).isoformat(),))
    db.commit()
    last, row_id = None, None
    while True:
        time.sleep(EVERY)
        try:
            now = _front() if _idle_seconds() < AWAY_AFTER else None
            if now and now == last and row_id:
                db.execute("UPDATE activity SET secs = secs + ? WHERE id = ?", (EVERY, row_id))
            elif now:
                row_id = db.execute("INSERT INTO activity (start, secs, app, title) VALUES (?,?,?,?)",
                                    (datetime.now().isoformat(timespec="seconds"), EVERY, *now)).lastrowid
            else:
                row_id = None
            last = now
            db.commit()
        except Exception as e:
            print(f"   [diary: {e!r}]")


def start():
    if ON:
        threading.Thread(target=_watch, daemon=True, name="diary").start()


# ------------------------------------------------------------------ answering
def _topic(app, title):
    """What the window was about: the site in a browser ('YouTube'), the project in an editor ('keena')."""
    if not title or title == "private browsing":
        return title
    parts = [p.strip() for p in re.split(r"\s+[-–—|•]\s+", title) if p.strip()]
    if app in BROWSERS:
        site = parts[-1] if len(parts) > 1 else parts[0]
        return re.sub(r"\s*\(\d+\)$", "", site)[:40]   # 'Inbox (3)' -> 'Inbox'
    if app in ("VS Code", "Visual Studio"):
        return parts[-1][:40]
    return parts[0][:40]


def _span(secs):
    h, m = divmod(int(secs) // 60, 60)
    if h and m >= 5:
        return f"{h} hour{'s' if h > 1 else ''} {m}"
    if h:
        return f"{h} hour{'s' if h > 1 else ''}"
    return f"{max(m, 1)} minute{'s' if m != 1 else ''}"


def _when(text):
    from brain.summaries import _window
    start, end = _window(text or "today", datetime.now())
    if start is None:
        start, end = _window("today", datetime.now())
    t = (text or "today").lower()
    said = ("yesterday" if "yesterday" in t else "this week" if "week" in t else "this month" if "month" in t
            else f"on {start.strftime('%A')}" if any(d in t for d in ("monday", "tuesday", "wednesday", "thursday",
                                                                    "friday", "saturday", "sunday")) else "today")
    return start, end, said


def diary(when="today", about="", **_):
    """What he did on the PC in a time span; with 'about', how long on one app or site ('YouTube', 'Unity')."""
    if not ON:
        return "The diary is turned off."
    start, end, said = _when(when)
    rows = _db().execute("SELECT start, secs, app, title FROM activity WHERE start >= ? AND start < ? ORDER BY start",
                         (start.isoformat(), end.isoformat())).fetchall()
    if not rows:
        return f"I don't have anything in your diary for {said}." if said != "today" else \
            "Nothing in your diary yet today."
    about = re.sub(r"^(?:the |my )?(?:pc|laptop|computer|desktop)$", "", (about or "").strip(), flags=re.I)
    if about:   # "what was I up to on the laptop" is the whole diary, not a search for "laptop"
        a = about.lower().strip()
        secs = sum(s for _, s, app, title in rows if a in app.lower() or a in title.lower())
        return f"You spent about {_span(secs)} on {about} {said}." if secs else f"I don't see {about} in your diary {said}."
    total = sum(s for _, s, _, _ in rows)
    apps, topics = {}, {}
    for _, s, app, title in rows:
        apps[app] = apps.get(app, 0) + s
        t = _topic(app, title)
        if t:
            topics.setdefault(app, {})[t] = topics.get(app, {}).get(t, 0) + s
    top = sorted(apps.items(), key=lambda x: -x[1])[:4]
    parts = []
    for app, secs in top:
        if secs < 300:
            continue
        what = [t for t, s in sorted(topics.get(app, {}).items(), key=lambda x: -x[1])[:2] if s >= 300 and t != app]
        parts.append(f"{app}, {_span(secs)}" + (f", on {' and '.join(what)}" if what else ""))
    first = datetime.fromisoformat(rows[0][0])
    last = datetime.fromisoformat(rows[-1][0]) + timedelta(seconds=rows[-1][1])
    clock = lambda d: d.strftime("%I:%M %p").lstrip("0")
    head = f"{said[0].upper() + said[1:]} you were on the PC for about {_span(total)}"
    hours = f", from {clock(first)} to {clock(last)}" if said in ("today", "yesterday") or said.startswith("on ") else ""
    return f"{head}{hours}." + (f" Mostly {'; '.join(parts)}." if parts else "")


TOOLS = [
    {"name": "diary",
     "description": "What he did on the PC: 'what did I work on yesterday', 'what did I do today', 'how long was I on "
                    "YouTube today' (about='YouTube'). when: today, yesterday, a weekday, this week.",
     "input_schema": {"type": "object", "properties": {"when": {"type": "string"}, "about": {"type": "string"}},
                      "required": []}},
]
FUNCTIONS = {"diary": diary}
