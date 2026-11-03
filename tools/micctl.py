"""Mic mute: the orb's mic button, "mute your mic" by voice, and "turn your mic on" from the phone.

main.py registers the function that really stops / starts the microphone. The orb's button sends
{"mute": "toggle"} to MIC_PORT on this PC only. Muted stays muted after a restart or a crash
(data/mic_muted), so the mic never turns itself back on behind his back.
"""
import json
import os
import socket
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FLAG = os.path.join(ROOT, "data", "mic_muted")
MIC_PORT = 47322
_set = {"fn": None}


def saved_muted():
    return os.path.exists(FLAG)


def _save(on):
    try:
        if on:
            os.makedirs(os.path.dirname(FLAG), exist_ok=True)
            open(FLAG, "w").close()
        elif os.path.exists(FLAG):
            os.remove(FLAG)
    except OSError:
        pass


def register(fn, muted_now):
    """fn(on) stops or starts the mic and updates the orb. Starts listening for the orb's button."""
    _set["fn"], _set["muted"] = fn, muted_now

    def listen():
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.bind(("127.0.0.1", MIC_PORT))
        except OSError:
            return
        while True:
            try:
                msg = json.loads(s.recv(1024).decode("utf-8"))
            except (OSError, ValueError):
                continue
            want = msg.get("mute")
            if want is not None:
                set_muted(not _set.get("muted") if want == "toggle" else bool(want))
    threading.Thread(target=listen, daemon=True, name="mic-button").start()


def set_muted(on):
    if not _set["fn"]:
        return False
    _set["muted"] = bool(on)
    _save(on)
    _set["fn"](bool(on))
    return True


def is_muted():
    return bool(_set.get("muted"))


def mic(on, **_):
    """Tool: on=False mutes ("mute your mic"), on=True turns it back on (from the phone or the keyboard)."""
    if not _set["fn"]:
        return "The microphone isn't in use in this mode."
    if on:
        set_muted(False)
        return "My mic is back on. Say Hey Edith when you need me."
    set_muted(True)
    return "Mic off. I can't hear anything now. Tap the mic button next to the orb, or tell me from your phone, to turn it back on."


TOOLS = [
    {"name": "mic", "description": "Turn her own microphone off ('mute your mic', 'stop listening') or back on "
                                   "('turn your mic on', 'unmute yourself'). Not the PC's sound: that's media_control.",
     "input_schema": {"type": "object", "properties": {"on": {"type": "boolean"}}, "required": ["on"]}},
]
FUNCTIONS = {"mic": mic}
