"""Spotify (the desktop app from the Microsoft Store) by voice: "play my liked songs", "play Yuvan melodies",
"play Believer on Spotify".

No Spotify account link or developer key: the app opens a spotify: link (search, liked songs) and Edith presses
its Play button through Windows UI Automation, like the WhatsApp and Edge tools. His own playlists come first
(the "Your Library" side panel), then a search. Pause / next / previous are the normal media keys (media_control).
Spotify plays on the device that's active in Spotify Connect: if his phone is playing, the music may start there.
"""
import os
import re
import time
import urllib.parse

from tools.edge import _press_control, _score_name, _uia

IDLE_TITLES = {"spotify", "spotify free", "spotify premium"}   # the window title when nothing plays on this PC
LIKED = re.compile(r"^(?:my |the )?(?:liked|favou?rite|saved|loved)(?: songs?| tracks?| music)?$|^my (?:songs|music)$", re.I)


class SpotifyError(Exception):
    pass


def _auto():
    import uiautomation as auto
    return auto


def _window(open_it=True):
    """Spotify's main window (started if needed)."""
    auto = _auto()

    def find():
        for w in auto.GetRootControl().GetChildren():
            if w.ClassName == "Chrome_WidgetWin_1" and _proc(w.ProcessId) == "spotify.exe" and w.Name:
                return w
        return None

    win = find()
    if win or not open_it:
        return win
    os.startfile("spotify:")
    for _ in range(40):
        time.sleep(0.5)
        if win := find():
            time.sleep(1.5)   # its page fills in a moment after the window appears
            return win
    raise SpotifyError("Spotify didn't open. Is it installed?")


def _proc(pid):
    try:
        import psutil
        return psutil.Process(pid).name().lower()
    except Exception:
        return ""


def _walk(win):
    """Every named control, in screen-reader order. Chromium fills its tree in on the first walk, so it's done twice
    when the first one comes back thin."""
    auto = _auto()
    for _ in range(2):
        items = [(c, c.ControlTypeName, " ".join((c.Name or "").split())) for c, _ in auto.WalkControl(win, maxDepth=60)]
        if sum(1 for _, k, _ in items if k == "ButtonControl") > 15:
            return items
        time.sleep(0.8)
    return items


def _library(items):
    """{'Liked Songs': play button, 'Yuvan melodies💯': ...}: his side panel, up to the player bar."""
    found = {}
    for c, kind, name in items:
        if kind == "GroupControl" and name == "Now playing bar":
            break
        if kind == "ButtonControl" and name.startswith("Play ") and len(name) > 5:
            found.setdefault(name[5:], c)
    return found


def _top_result(items):
    """(play button, title, 'Song • Imagine Dragons') of the search page's top result, or None."""
    in_main, title, kind_by = False, None, ""
    for c, kind, name in items:
        if kind == "GroupControl" and name.startswith("Spotify") and "Search" in name:
            in_main = True
            continue
        if not in_main or not name:
            continue
        if kind == "GroupControl" and title is None:
            title = name
        elif kind == "DataItemControl" and "•" in name and not kind_by:
            kind_by = name
        elif kind == "ButtonControl" and (name == "Play" or name.startswith("Play ")):
            return c, title or name[5:], kind_by
    return None


def _spoken(name):
    """Emoji and decoration off a playlist name, so it's said cleanly ('Yuvan melodies💯' -> 'Yuvan melodies')."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s'&,.()-]", " ", name)).strip() or name


def _result(win, what):
    """What to say after pressing Play: where it's playing, if Windows can tell (the title shows the song)."""
    for _ in range(8):
        time.sleep(0.5)
        if (win.Name or "").strip().lower() not in IDLE_TITLES:
            return f"Playing {what} on Spotify."
    return (f"I pressed play on {what} in Spotify, but it isn't playing on this PC. "
            "It may be playing on your phone or another device.")


def spotify_play(what="", **_):
    what = re.sub(r"^(?:some |the |my )?(?:music|songs)$", "", (what or "").strip(" .!?"), flags=re.I)
    try:
        win = _window()
    except SpotifyError as e:
        return str(e)
    try:
        win.SetActive()
    except Exception:
        pass
    if not what or LIKED.match(what):
        lib = _library(_walk(win))
        button = lib.get("Liked Songs")
        if not button:
            return "I couldn't find your Liked Songs in Spotify."
        _press_control(button)
        return _result(win, "your liked songs")

    # one of his own playlists / artists first: "play Yuvan melodies", "play my club bangers playlist"
    want = re.sub(r"^(?:my |the )|\s+(?:playlist|album|mix)$", "", what, flags=re.I).strip()
    lib = _library(_walk(win))
    best = max(lib, key=lambda n: _score_name(want, _spoken(n)), default=None)
    if best and _score_name(want, _spoken(best)) >= 0.8:
        _press_control(lib[best])
        return _result(win, _spoken(best))

    # otherwise a search: the top result (a song, an artist's mix, an album). The page may still show the LAST
    # search for a moment: wait for the top result to change, so the old song is never the one played
    before = _top_result(_walk(win))
    os.startfile("spotify:search:" + urllib.parse.quote(want))
    time.sleep(1.2)
    top = None
    for i in range(10):
        top = _top_result(_walk(win))
        if top and (not before or top[1:] != before[1:] or i >= 4):
            break
        time.sleep(0.7)
    if not top:
        return f"I couldn't find {want} on Spotify."
    button, title, kind_by = top
    _press_control(button)
    by = kind_by.split("•", 1)[1].strip() if "•" in kind_by and not kind_by.lower().startswith("playlist") else ""
    return _result(win, _spoken(title) + (f" by {by}" if by and by.lower() not in title.lower() else ""))


def _with_uia(fn):
    def wrapped(*a, **kw):
        with _uia():
            return fn(*a, **kw)
    wrapped.__name__ = fn.__name__
    return wrapped


spotify_play = _with_uia(spotify_play)


TOOLS = [
    {"name": "spotify_play",
     "description": "Play music in the Spotify app: his liked songs, one of his playlists, or a song / artist / album "
                    "by name. 'what' empty or 'liked songs' = his liked songs. Pausing and skipping use media_control.",
     "input_schema": {"type": "object", "properties": {"what": {"type": "string"}}, "required": []}},
]
FUNCTIONS = {"spotify_play": spotify_play}
