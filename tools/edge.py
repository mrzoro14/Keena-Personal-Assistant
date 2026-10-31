"""Microsoft Edge: every tab in every Edge window (his own Edge, his logins), through Windows UI Automation.

List, switch to, close (even background tabs: each tab has its own Close button) and read a tab.
Edge only shows page text to UI Automation when a screen reader runs, so reading switches to the tab and copies
its text (Ctrl+A, Ctrl+C), then puts his clipboard back.
"""
import difflib
import re
import time

import psutil
import pyautogui

TAB_STATE = re.compile(r"\s*-\s*(?:Discarded|Audio playing|Muted|Sleeping|Memory usage.*|Network error|"
                       r"Camera or microphone recording|Pinned)\s*$", re.I)
SITE_WORDS = re.compile(r"\s*[-|–—·]\s*(?:YouTube|Gmail|Google Search|Microsoft​? Edge)\s*$", re.I)


class EdgeError(Exception):
    pass


def _auto():
    import uiautomation as auto
    return auto


def _windows():
    auto = _auto()
    pids = {p.pid for p in psutil.process_iter(["name"]) if (p.info["name"] or "").lower() == "msedge.exe"}
    return [w for w in auto.GetRootControl().GetChildren() if w.ClassName == "Chrome_WidgetWin_1" and w.ProcessId in pids]


def _tab_items(win):
    out = []

    def walk(c, depth=0):
        if depth > 16:
            return
        for ch in c.GetChildren():
            if ch.ControlTypeName == "TabItemControl":
                out.append(ch)
            elif ch.ControlTypeName != "DocumentControl":   # page content: never holds tabs, can be huge
                walk(ch, depth + 1)
    walk(win)
    return out


def _title(tab):
    return TAB_STATE.sub("", tab.Name or "").strip()


def _short(title):
    t = SITE_WORDS.sub("", re.sub(r"^\(\d+\)\s*", "", title)).strip()
    return t if len(t) <= 50 else t[:47].rsplit(" ", 1)[0] + "..."


def tabs():
    """[(window, tab, title, selected)] for every Edge window, in tab-strip order."""
    out = []
    for w in _windows():
        for t in _tab_items(w):
            try:
                sel = t.GetSelectionItemPattern().IsSelected
            except Exception:
                sel = False
            out.append((w, t, _title(t), sel))
    return out


def _norm(s):
    return re.sub(r"[^a-z0-9 ]+", " ", s.lower()).strip()


def match(name, all_tabs=None, many=False):
    """Tabs whose title fits `name` ('youtube', 'the expense manager', 'tab 3'), best first."""
    all_tabs = tabs() if all_tabs is None else all_tabs
    want = _norm(name or "")
    m = re.fullmatch(r"(?:tab\s*)?(?:number\s*)?(\d+)", want)
    if m:
        i = int(m[1]) - 1
        return [all_tabs[i]] if 0 <= i < len(all_tabs) else []
    if not want:
        return [x for x in all_tabs if x[3]][:1]
    scored = []
    for x in all_tabs:
        title = _norm(x[2])
        words = want.split()
        if want in title or all(w in title for w in words):
            score = 1.0
        else:
            score = max([difflib.SequenceMatcher(None, want, title).ratio()] +
                        [difflib.SequenceMatcher(None, want, title[i:i + len(want)]).ratio()
                         for i in range(0, max(1, len(title) - len(want) + 1), 3)])
        if score >= 0.75:
            scored.append((score, x))
    scored.sort(key=lambda s: -s[0])
    best = [x for s, x in scored if s >= (scored[0][0] if scored and not many else 0.75)]
    return best if many else best[:1]


def _front(win):
    """Bring an Edge window to the front (Windows only lets us after a key press)."""
    import pygetwindow as gw
    from tools.pc_tools import _bring_forward
    return _bring_forward(gw.Win32Window(win.NativeWindowHandle))


def _select(win, tab):
    try:
        tab.GetSelectionItemPattern().Select()
    except Exception:
        tab.Click(simulateMove=False)
    time.sleep(0.3)


# ------------------------------------------------------------------ tools
def list_tabs(**_):
    all_tabs = tabs()
    if not all_tabs:
        return "Edge isn't open."
    now = next((x[2] for x in all_tabs if x[3]), None)
    names = [f"{i + 1}. {_short(x[2])}" for i, x in enumerate(all_tabs[:8])]
    more = f", and {len(all_tabs) - 8} more" if len(all_tabs) > 8 else ""
    lead = f"You have {len(all_tabs)} tabs open in Edge" + (f"; you're on {_short(now)}" if now else "") + ". "
    return lead + "; ".join(names) + more + "."


def switch_tab(name, **_):
    found = match(name)
    if not found:
        return f"I don't see an Edge tab for {name}."
    win, tab, title, _ = found[0]
    _select(win, tab)
    _front(win)
    return f"Switched to {_short(title)}."


def close_tabs(name, every=False, **_):
    """Close the matching tab, or every matching tab ('close all YouTube tabs'), even in the background."""
    found = match(name, many=every)
    if not found:
        return f"I don't see an Edge tab for {name}."
    closed = []
    for win, tab, title, _ in reversed(found):   # right to left: closing doesn't shift the ones still to close
        btn = tab.ButtonControl(searchDepth=1, Name="Close tab")
        if btn.Exists(0.3, 0):
            try:
                btn.GetInvokePattern().Invoke()
            except Exception:
                btn.Click(simulateMove=False)
            closed.append(title)
            time.sleep(0.15)
    if not closed:
        return "I couldn't close that tab."
    return f"Closed {_short(closed[0])}." if len(closed) == 1 else f"Closed {len(closed)} tabs."


def read_tab(name=None):
    """{'title', 'url', 'text'} of a tab (the front one if no name). Raises EdgeError with a spoken reason."""
    from tools.pc_tools import _clipboard, _focus_is_terminal
    found = match(name or "")
    if not found:
        raise EdgeError(f"I don't see an Edge tab for {name}." if name else "Edge isn't open.")
    win, tab, title, _ = found[0]
    _select(win, tab)
    if not _front(win):
        raise EdgeError("I couldn't bring Edge to the front to read it.")
    if _focus_is_terminal():
        raise EdgeError("The focused window is a terminal, so I won't press keys there.")
    url = ""
    try:
        url = win.EditControl(searchDepth=14, Name="Address and search bar").GetValuePattern().Value or ""
    except Exception:
        pass
    saved = _clipboard()
    try:
        _clipboard("")
        # selecting the tab leaves focus on the tab strip (Ctrl+A there copied nothing); Esc hands it to the page
        pyautogui.press("esc")
        time.sleep(0.25)
        if _auto().GetFocusedControl().ControlTypeName != "DocumentControl":
            doc = win.DocumentControl(searchDepth=16)
            if doc.Exists(0.5, 0):
                doc.SetFocus()
        pyautogui.hotkey("ctrl", "a")
        time.sleep(0.1)
        pyautogui.hotkey("ctrl", "c")
        time.sleep(0.3)
        text = _clipboard() or ""
        pyautogui.press("right")   # drop the blue selection (doesn't scroll the page)
    finally:
        if saved is not None:
            _clipboard(saved)
    text = re.sub(r"\n\s*\n+", "\n", text).strip()
    if text == url.strip():
        text = ""
    return {"title": title, "url": url, "site": re.sub(r"^https?://(www\.)?|/.*$", "", url) or "Edge", "text": text}


class _uia:
    """UI Automation needs COM set up in whichever thread calls it (tools can run in worker threads)."""
    def __enter__(self):
        self.init = _auto().UIAutomationInitializerInThread()
        self.init.__enter__()

    def __exit__(self, *exc):
        self.init.__exit__(*exc)


def _in_thread(fn):
    def wrapped(*a, **kw):
        with _uia():
            return fn(*a, **kw)
    wrapped.__name__ = fn.__name__
    return wrapped


list_tabs = _in_thread(list_tabs)
close_tabs = _in_thread(close_tabs)
read_tab = _in_thread(read_tab)
switch_tab = _in_thread(switch_tab)


def _tool(name, description, props=None, required=None):
    return {"name": name, "description": description,
            "input_schema": {"type": "object", "properties": props or {}, "required": required or []}}


TOOLS = [
    _tool("list_tabs", "Say which tabs are open in Microsoft Edge (all windows)."),
    _tool("switch_tab", "Go to an open Edge tab by its title or site ('switch to YouTube', 'go to tab 3').",
          {"name": {"type": "string"}}, ["name"]),
    _tool("close_tabs", "Close an Edge tab by title or site, even in the background ('close the Instagram tab'). "
          "every=true closes ALL matching tabs ('close all YouTube tabs').",
          {"name": {"type": "string"}, "every": {"type": "boolean"}}, ["name"]),
    _tool("read_tab", "Read an Edge tab and answer his question about it or summarise it ('what does the expense "
          "manager tab say', 'summarise this tab'). 'name' empty = the tab he's on.",
          {"name": {"type": "string"}, "question": {"type": "string"}}),
]
FUNCTIONS = {"list_tabs": list_tabs, "switch_tab": switch_tab, "close_tabs": close_tabs}
