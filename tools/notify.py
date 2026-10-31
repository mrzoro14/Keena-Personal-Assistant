"""Phone notifications through the free ntfy app (no account).

Setup (once): install "ntfy" on the phone and subscribe to the topic in .env (NTFY_TOPIC: a long random name, so
nobody else can guess it and read his alerts). Sent: reminders when they go off, low battery, a finished download
when he asks, and anything he says "send ... to my phone". Alerts pass through the ntfy server (NTFY_SERVER).
"""
import os
import threading
import time
import urllib.request

SERVER = os.getenv("NTFY_SERVER", "https://ntfy.sh").strip().rstrip("/")
TOPIC = os.getenv("NTFY_TOPIC", "").strip()
DOWNLOADS = os.path.join(os.path.expanduser("~"), "Downloads")
IN_PROGRESS = (".crdownload", ".partial", ".part", ".download", ".opdownload")
LOW_BATTERY = 20
NOT_SET_UP = "Your phone isn't connected yet: the ntfy topic isn't in the settings."


def ready():
    return bool(TOPIC)


def send(text, title=None, tags=None, priority=None):
    """True if ntfy took it. Never raises: a missing phone alert must not break her."""
    if not TOPIC:
        return False
    headers = {"Title": (title or "Edith").encode("utf-8")}   # ntfy reads UTF-8 header values
    if tags:
        headers["Tags"] = tags
    if priority:
        headers["Priority"] = str(priority)
    try:
        req = urllib.request.Request(f"{SERVER}/{TOPIC}", data=text[:4000].encode("utf-8"), headers=headers)
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status == 200
    except Exception as e:
        print(f"   [phone: {e!r}]")
        return False


def send_later(text, **kw):
    threading.Thread(target=send, args=(text,), kwargs=kw, daemon=True).start()


# ------------------------------------------------------------------ tools
def notify_phone(message, **_):
    if not TOPIC:
        return NOT_SET_UP
    return "Sent to your phone." if send(message) else "I couldn't reach your phone just now."


def _in_progress():
    try:
        return [e.name for e in os.scandir(DOWNLOADS) if e.name.lower().endswith(IN_PROGRESS)]
    except OSError:
        return []


def watch_download(**_):
    """'Tell me on my phone when the download finishes': waits for every download in progress to finish."""
    if not TOPIC:
        return NOT_SET_UP
    busy = _in_progress()
    if not busy:
        return "I don't see a download in progress in your Downloads folder."

    def wait():
        started = time.time()
        while _in_progress() and time.time() - started < 6 * 3600:
            time.sleep(5)
        names = [os.path.splitext(n)[0] for n in busy]
        done = [n for n in names if os.path.exists(os.path.join(DOWNLOADS, n))]
        if done:
            send(", ".join(done), title="Download finished", tags="white_check_mark")
        else:
            send("It may have failed or been cancelled.", title="Download stopped", tags="warning")
    threading.Thread(target=wait, daemon=True).start()
    return f"Okay, I'll tell you on your phone when {'it finishes' if len(busy) == 1 else f'all {len(busy)} finish'}."


def watch_battery():
    """Low battery: one phone alert per discharge, below 20% and not plugged in."""
    import psutil
    warned = False
    while True:
        try:
            b = psutil.sensors_battery()
            if b and not b.power_plugged and b.percent <= LOW_BATTERY and not warned:
                warned = send(f"{int(b.percent)}% left. Plug the laptop in.", title="Battery low", tags="battery",
                              priority=4)
            elif b and b.power_plugged:
                warned = False
        except Exception:
            pass
        time.sleep(120)


def start():
    if TOPIC:
        threading.Thread(target=watch_battery, daemon=True).start()


def _tool(name, description, props=None, required=None):
    return {"name": name, "description": description,
            "input_schema": {"type": "object", "properties": props or {}, "required": required or []}}


TOOLS = [
    _tool("notify_phone", "Send a message to his phone as a notification ('send the address to my phone').",
          {"message": {"type": "string"}}, ["message"]),
    _tool("watch_download", "Notify his phone when the download in progress finishes."),
]
FUNCTIONS = {"notify_phone": notify_phone, "watch_download": watch_download}
