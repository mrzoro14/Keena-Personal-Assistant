"""WhatsApp Desktop by voice: send a message (only after his "yes") and read new messages.

Works through Windows UI Automation on his own WhatsApp app (his account, his contacts). Before sending she checks
the open chat is really that person: WhatsApp labels the message box "Type a message to <name>", and she asks
"Send '...' to <that name>?" with the name WhatsApp shows, so a wrong match is caught before anything goes.
Reading uses the chat list (who wrote, how many, the latest message), so chats aren't marked as read.
"""
import difflib
import re
import subprocess
import time

import psutil
import pyautogui

from tools.edge import _in_thread

APP = r"shell:AppsFolder\5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App"
UNREAD = re.compile(r"^(\d+) unread messages?$", re.I)
TIME = re.compile(r"^(\d{1,2}:\d{2}( ?[ap]m)?|yesterday|today|monday|tuesday|wednesday|thursday|friday|saturday|"
                  r"sunday|\d{1,2}/\d{1,2}/\d{2,4})$", re.I)


SELF = {"me", "myself", "you", "my own chat", "my chat", "message yourself", "yourself", "self"}


class WhatsAppError(Exception):
    pass


def _auto():
    import uiautomation as auto
    return auto


def _window(open_it=True):
    auto = _auto()
    for _ in range(20):
        pids = {p.pid for p in psutil.process_iter(["name"]) if (p.info["name"] or "").lower().startswith("whatsapp")}
        for w in auto.GetRootControl().GetChildren():
            if w.ProcessId in pids and w.Name == "WhatsApp" and w.ClassName.startswith("WinUI"):
                return w
        if not open_it:
            return None
        if _ == 0:
            subprocess.Popen(["explorer.exe", APP])
        time.sleep(0.5)
    raise WhatsAppError("WhatsApp didn't open.")


def _front(win):
    import pygetwindow as gw
    from tools.pc_tools import _bring_forward   # here: pc_tools imports this module
    _bring_forward(gw.Win32Window(win.NativeWindowHandle))


def _grid(win, wait=6):
    """The chat list. Its contents appear a moment after the first look (the app turns accessibility on then)."""
    auto = _auto()
    t0 = time.time()
    while time.time() - t0 < wait:
        g = win.DataGridControl(searchDepth=25)
        if g.Exists(0.3) and g.GetChildren():
            return g
        for _c, _d in auto.WalkControl(win, maxDepth=6):   # a look around wakes it up
            pass
        time.sleep(0.4)
    raise WhatsAppError("I can't see the chat list in WhatsApp.")


def _parse(row):
    """{'name', 'unread', 'time', 'last', 'muted', 'row'} from one chat-list row."""
    texts = [c.Name.strip() for c, _ in _auto().WalkControl(row, maxDepth=8)
             if c.ControlTypeName == "TextControl" and c.Name.strip()]
    unread = 0
    if texts and (m := UNREAD.match(texts[0])):
        unread, texts = int(m[1]), texts[1:]
    name = texts[0] if texts else ""
    rest = texts[1:]
    when = rest.pop(0) if rest and TIME.match(rest[0]) else ""
    last = " ".join(t for t in rest if t != ":")
    return {"name": name, "unread": unread, "time": when, "last": last, "muted": "muted" in row.Name.lower(),
            "row": row}


def _rows(win):
    """Chat rows. In search results only the Chats and Contacts sections count: Messages are matching lines."""
    out, section = [], "chats"
    for x in _grid(win).GetChildren():
        if x.ControlTypeName != "DataItemControl":
            continue
        if x.Name.strip().lower() in ("chats", "contacts", "messages", "groups in common"):
            section = x.Name.strip().lower()
            continue
        r = _parse(x)
        if r["name"] and section in ("chats", "contacts"):
            r["section"] = section
            out.append(r)
    return out


def _plain(s):
    s = re.sub(r"\s*\(you\)\s*$", "", s, flags=re.I)   # his own chat: "Zoro (You)"
    return " ".join(re.sub(r"[^\w\s]", " ", s.lower()).split())


def _score(want, name):
    w, n = _plain(want), _plain(name)
    if not w or not n:
        return 0
    if w == n:
        return 1.0
    if n.startswith(w + " ") or n.split()[0] == w:
        return 0.95
    if all(x in n.split() for x in w.split()):
        return 0.9
    return max(difflib.SequenceMatcher(None, w, n).ratio(), difflib.SequenceMatcher(None, w, n.split()[0]).ratio())


def _search_box(win):
    """The chat-list search box: a plain html input (the message box is a rich editor). Its label changes once
    something is typed in it, so it's found by kind, not by name."""
    for c, _ in _auto().WalkControl(win, maxDepth=25):
        if c.ControlTypeName == "EditControl" and (c.Name.lower().startswith("search") or "html-input" in c.ClassName):
            return c
    raise WhatsAppError("I can't find WhatsApp's search box.")


def _composer(win):
    for c, _ in _auto().WalkControl(win, maxDepth=30):
        if c.ControlTypeName == "EditControl" and c.Name.lower().startswith("type a message"):
            return c
    return None


def _paste(text):
    """Type any text (emoji, Tamil) by pasting it, then put his clipboard back."""
    from tools.pc_tools import _clipboard
    saved = _clipboard()
    _clipboard(text)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.3)
    if saved is not None:
        _clipboard(saved)


def _press(win, row):
    """Click a chat row; one scrolled out of sight is scrolled into view first (a click there would hit something
    else). WhatsApp ignores the 'default action' it advertises, so it has to be a real click."""
    view = win.BoundingRectangle
    for _ in range(12):
        r = row.BoundingRectangle
        if view.top + 120 < r.ycenter() < view.bottom - 60:
            row.Click(simulateMove=False)
            return True
        _auto().WheelDown(4) if r.ycenter() >= view.bottom - 60 else _auto().WheelUp(4)
        time.sleep(0.25)
    return False


def _open_chat(win, contact):
    """Search for the contact and open the best match. Returns the name WhatsApp shows for that chat."""
    me = contact.strip().lower() in SELF
    box = _search_box(win)
    box.Click(simulateMove=False)
    pyautogui.hotkey("ctrl", "a")
    _paste("You" if me else contact)
    time.sleep(1.5)
    rows = _rows(win)
    if me:   # his own "message yourself" chat is listed under his name, with "(You)" after it
        best = next((r for r in rows if "(you)" in r["row"].Name.lower()), None)
    else:
        best = max(rows, key=lambda r: (_score(contact, r["name"]), r.get("section") == "chats"), default=None)
    if not best or (not me and _score(contact, best["name"]) < 0.75):
        pyautogui.press("esc")
        raise WhatsAppError(f"I couldn't find {contact} in WhatsApp.")
    if not _press(win, best["row"]):
        raise WhatsAppError(f"I found {best['name']} but couldn't open the chat.")
    for _ in range(10):
        time.sleep(0.3)
        box = _composer(win)
        if box:
            return box, re.sub(r"^type a message to\s*", "", box.Name, flags=re.I).strip() or best["name"]
    raise WhatsAppError(f"I opened {best['name']} but can't find the message box.")


def send_whatsapp(contact, message, confirm=None, **_):
    if not message.strip():
        return "What should the message say?"
    try:
        win = _window()
        _front(win)
        box, name = _open_chat(win, contact)
        if contact.strip().lower() not in SELF and _score(contact, name) < 0.75:
            return f"The chat that opened is {name}, not {contact}, so I didn't type anything."
        box.Click(simulateMove=False)
        _paste(message)
        if not confirm or not confirm(f"Send \"{message}\" to {name} on WhatsApp?"):
            box.Click(simulateMove=False)
            pyautogui.hotkey("ctrl", "a")
            pyautogui.press("delete")
            return f"Okay, I didn't send it. {name}'s chat is open."
        _front(win)
        box.Click(simulateMove=False)
        pyautogui.press("enter")
        return f"Sent to {name}."
    except WhatsAppError as e:
        return str(e)


def read_whatsapp(contact=None, **_):
    """New messages from the chat list (not opened, so they stay unread), or the latest from one person."""
    try:
        win = _window()
        rows = _rows(win)
        if contact:
            best = max(rows, key=lambda r: _score(contact, r["name"]), default=None)
            if not best or _score(contact, best["name"]) < 0.75:
                _front(win)
                box = _search_box(win)
                box.Click(simulateMove=False)
                pyautogui.hotkey("ctrl", "a")
                _paste(contact)
                time.sleep(1.5)
                found = _rows(win)
                pyautogui.press("esc")
                best = max(found, key=lambda r: _score(contact, r["name"]), default=None)
                if not best or _score(contact, best["name"]) < 0.75:
                    return f"I couldn't find {contact} in WhatsApp."
            new = f"{best['unread']} new. " if best["unread"] else ""
            return f"{best['name']}: {new}The latest, at {best['time'] or 'some point'}: {best['last'] or 'a photo or file'}."
        unread = [r for r in rows if r["unread"] and not r["muted"]]
        muted = [r for r in rows if r["unread"] and r["muted"]]
        if not unread:
            extra = f" {len(muted)} muted chats have new messages." if muted else ""
            return "No new WhatsApp messages." + extra
        parts = [f"{r['name']}, {r['unread']}: {r['last'][:120] or 'a photo or file'}" for r in unread[:5]]
        more = f" And {len(unread) - 5} more chats." if len(unread) > 5 else ""
        extra = f" Plus {len(muted)} muted chats." if muted else ""
        return f"New messages in {len(unread)} chats. " + "; ".join(parts) + "." + more + extra
    except WhatsAppError as e:
        return str(e)


send_whatsapp = _in_thread(send_whatsapp)
read_whatsapp = _in_thread(read_whatsapp)


def _tool(name, description, props=None, required=None):
    return {"name": name, "description": description,
            "input_schema": {"type": "object", "properties": props or {}, "required": required or []}}


TOOLS = [
    _tool("send_whatsapp", "Send a WhatsApp message to a contact through the WhatsApp app. She asks him to confirm "
          "first. 'message' is exactly what to send, written as he'd write it.",
          {"contact": {"type": "string", "description": "the contact or group name, e.g. 'Arjun'"},
           "message": {"type": "string"}}, ["contact", "message"]),
    _tool("read_whatsapp", "Read his new WhatsApp messages, or the latest message from one contact "
          "('what did Arjun say on WhatsApp').",
          {"contact": {"type": "string", "description": "optional: one contact"}}),
]
FUNCTIONS = {"send_whatsapp": send_whatsapp, "read_whatsapp": read_whatsapp}
