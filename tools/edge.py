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


# ------------------------------------------------------------------ the page itself: click and type
CLICKABLE = {"ButtonControl", "HyperlinkControl", "CheckBoxControl", "RadioButtonControl", "MenuItemControl",
             "TabItemControl", "ListItemControl", "SplitButtonControl", "ImageControl"}
TYPEABLE = {"EditControl", "ComboBoxControl"}
# same name twice (Bing's "Images" is a link AND a list entry): the real link or button wins
KIND_ORDER = {"HyperlinkControl": 0, "ButtonControl": 0, "SplitButtonControl": 0, "CheckBoxControl": 1,
              "RadioButtonControl": 1, "TabItemControl": 1, "MenuItemControl": 2, "ListItemControl": 3, "ImageControl": 4}
# the same safety list as Jarvis's own Chrome: these change something real, so she asks first
from tools.browser import RISKY  # noqa: E402


def front_window():
    """The Edge window in front, or None."""
    import ctypes
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    return next((w for w in _windows() if w.NativeWindowHandle == hwnd), None)


def _page_doc(win):
    """The page of the tab on show. Edge fills in the page's buttons a moment after it's first looked at."""
    auto = _auto()
    docs = []
    for c, _d in auto.WalkControl(win, maxDepth=16):
        if c.ControlTypeName == "DocumentControl" and c.Name != "" and not _off(c):
            docs.append(c)
    if not docs:
        doc = win.DocumentControl(searchDepth=16)
        if not doc.Exists(1, 0):
            raise EdgeError("I can't see a page in Edge.")
        docs = [doc]
    return docs[0]


def _off(c):
    """Off screen? An element the page just removed counts as off screen (asking it raises)."""
    try:
        return bool(c.IsOffscreen)
    except Exception:
        return True


def _elements(doc, kinds, wait=3.0):
    """[(control, name)] of the given kinds on the page, on-screen first. Waits for Edge to fill the page in."""
    auto = _auto()
    t0, out = time.time(), []
    while True:
        out = [(c, " ".join((c.Name or "").split())) for c, _d in auto.WalkControl(doc, maxDepth=60)
               if c.ControlTypeName in kinds]
        if len(out) > 2 or time.time() - t0 > wait:
            break
        time.sleep(0.5)
    return sorted(out, key=lambda x: _off(x[0]))


def _page_name(doc):
    """'Bing', 'Tailscale': said with every click and typing, so he always knows which page she touched."""
    return _short(SITE_WORDS.sub("", " ".join((doc.Name or "this page").split()))) or "this page"


def _score_name(want, name):
    w, n = _norm(want), _norm(name)
    if not w or not n:
        return 0
    if w == n:
        return 1.0
    if n.startswith(w + " ") or n.endswith(" " + w):
        return 0.93
    if re.search(rf"\b{re.escape(w)}\b", n):
        return 0.9 - min(0.2, len(n) / 400)   # "sign in" inside a long link text counts, but less
    return difflib.SequenceMatcher(None, w, n).ratio() * (0.95 if len(n) < 40 else 0.8)


def _target_win():
    """Edge in front; or else the Edge window used last, brought to the front."""
    win = front_window()
    if win:
        return win
    wins = _windows()
    if not wins:
        raise EdgeError("Edge isn't open.")
    _front(wins[0])
    return wins[0]


def _press_control(c):
    for get in ("GetInvokePattern", "GetTogglePattern", "GetSelectionItemPattern"):
        try:
            p = getattr(c, get)()
            if p:
                {"GetInvokePattern": lambda: p.Invoke(), "GetTogglePattern": lambda: p.Toggle(),
                 "GetSelectionItemPattern": lambda: p.Select()}[get]()
                return
        except Exception:
            continue
    try:
        c.GetScrollItemPattern().ScrollIntoView()
    except Exception:
        pass
    c.Click(simulateMove=False)


def click_on_page(target, confirm=None, **_):
    """Click a button, link, checkbox or tab on the Edge page by what it says."""
    target = re.sub(r"^(?:on |the |a )+|\s+(?:button|link|tab|option|icon|checkbox|box)$", "", target.strip(" .!?").lower())
    win = _target_win()
    doc = _page_doc(win)
    els = [(c, n) for c, n in _elements(doc, CLICKABLE) if n]
    scored = sorted(((round(_score_name(target, n), 2), KIND_ORDER.get(c.ControlTypeName, 5), i, c, n)
                     for i, (c, n) in enumerate(els)), key=lambda x: (-x[0], x[1], x[2]))
    if not scored or scored[0][0] < 0.62:
        return f"I can't find {target} on this page."
    _s, _k, _i, c, name = scored[0]
    if RISKY.search(name) or RISKY.search(target):
        if not confirm or not confirm(f"That will {name.lower()[:60]}. Shall I go ahead?"):
            return f"Okay, I didn't click {name[:60]}."
    _press_control(c)
    return f"Clicked {name[:60]} on {_page_name(doc)}."


def type_on_page(text, field=None, submit=False, **_):
    """Type into a box on the Edge page: the named one ('search', 'email'), or the one in focus, or the first box."""
    from tools.pc_tools import _clipboard
    win = _target_win()
    doc = _page_doc(win)
    boxes = [(c, n) for c, n in _elements(doc, TYPEABLE) if not _off(c) or field]
    if not boxes:
        return "I don't see a box to type in on this page."
    box = None
    if field:
        scored = sorted(((max(_score_name(field, n), _score_name(field, c.AutomationId or "")), i, c)
                         for i, (c, n) in enumerate(boxes)), key=lambda x: (-x[0], x[1]))
        if scored[0][0] >= 0.6:
            box = scored[0][2]
        elif field.lower().strip() not in ("search", "box", "field", "text box"):
            return f"I can't find a {field} box on this page."
    if box is None:
        focused = _auto().GetFocusedControl()
        box = next((c for c, _n in boxes if focused and c.NativeWindowHandle == focused.NativeWindowHandle
                    and c.BoundingRectangle == focused.BoundingRectangle), None) or boxes[0][0]
    try:
        if box.GetLegacyIAccessiblePattern().State & 0x20000000:   # STATE_SYSTEM_PROTECTED: a password box
            return "That's a password box. Type passwords yourself, so they're never said out loud."
    except Exception:
        pass
    box.SetFocus()
    time.sleep(0.15)
    pyautogui.hotkey("ctrl", "a")
    saved = _clipboard()
    _clipboard(text)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.25)
    if saved is not None:
        _clipboard(saved)
    if submit:
        pyautogui.press("enter")
    label = re.split(r"\s+-\s+", " ".join((box.Name or field or "the box").split()))[0][:40]
    return f"Typed it into {label} on {_page_name(doc)}" + (" and pressed Enter." if submit else ".")


def page_controls(**_):
    """What he can click or fill in on the Edge page (on screen), so he knows what to say."""
    win = _target_win()
    doc = _page_doc(win)
    els = [n for c, n in _elements(doc, CLICKABLE | TYPEABLE) if n and not _off(c)]
    seen, names = set(), []
    for n in els:
        if n.lower() not in seen and len(n) <= 50:
            seen.add(n.lower())
            names.append(n)
    if not names:
        return "I don't see anything to click on this page."
    return f"On screen you can click: {', '.join(names[:12])}" + (", and more." if len(names) > 12 else ".")


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
click_on_page = _in_thread(click_on_page)
type_on_page = _in_thread(type_on_page)
page_controls = _in_thread(page_controls)


def _page_tool(fn):
    """Spoken reason instead of a crash when Edge isn't there."""
    def wrapped(*a, **kw):
        try:
            return fn(*a, **kw)
        except EdgeError as e:
            return str(e)
    wrapped.__name__ = fn.__name__
    return wrapped


click_on_page = _page_tool(click_on_page)
type_on_page = _page_tool(type_on_page)
page_controls = _page_tool(page_controls)


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
    _tool("type_on_page", "Type text into a box on the Edge page he's on: a search box, a form field. 'field' is the "
          "box's label ('search', 'email', 'first name'); empty = the box in focus. submit=true presses Enter after.",
          {"text": {"type": "string"}, "field": {"type": "string"}, "submit": {"type": "boolean"}}, ["text"]),
    _tool("page_controls", "Say what can be clicked or filled in on the Edge page he's on."),
]
FUNCTIONS = {"list_tabs": list_tabs, "switch_tab": switch_tab, "close_tabs": close_tabs, "type_on_page": type_on_page,
             "page_controls": page_controls}
