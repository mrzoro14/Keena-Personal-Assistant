"""'What's on my screen?': Edith takes a screenshot and asks a vision model about it.

Through his FreeLLMAPI app (an OpenAI-style server on this PC, http://127.0.0.1:31415/v1) with its API key in
.env as FREELLM_API_KEY. FREELLM_VISION_MODEL picks the model; empty = the first one the server lists that can
see images. FreeLLMAPI passes the picture on to whichever free online model it uses, so the screenshot does
leave the PC: she never looks unless he asks, and she won't read passwords or card numbers out loud.
The key is read from .env on every look, so pasting it in works without restarting her.
"""
import base64
import io
import json
import os
import re
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV = os.path.join(ROOT, ".env")
DEFAULT_URL = "http://127.0.0.1:31415/v1"
MAX_WIDTH = 1600   # wide enough to read screen text, small enough to send quickly
SEES_IMAGES = re.compile(r"gemini|gpt-4o|gpt-4\.1|gpt-5|vision|vl\b|-vl|llava|pixtral|llama-4|llama-3\.2-(?:11|90)b|"
                         r"claude|grok-(?:2-vision|4)|gemma-3|qwen.*vl|mistral-(?:small|medium)|phi-4-multimodal", re.I)
PROMPT = ("You are {name}, {user}'s assistant, looking at a screenshot of his PC screen. Answer his question about it "
          "in one to three short sentences for speaking aloud: plain words, no markdown, no lists. If he just asks what's "
          "on screen, say what app or page is in front and the main thing on it. Read text exactly when he asks you to "
          "read it. Never read out passwords, card numbers, codes or keys: say there's private information instead. "
          "The window in front is titled: {title!r}.\n\nHis question: {question}")
_picked = {}
# a window that may show secrets: she asks before the screenshot leaves the PC (his .env was on screen once)
PRIVATE = re.compile(r"\.env\b|password|passwort|1password|bitwarden|keepass|lastpass|dashlane|credential|secret|"
                     r"api[ _-]?key|private key|\bbank|paypal|revolut|monzo|barclays|hsbc|lloyds|natwest|santander|"
                     r"sign[ -]?in|log[ -]?in|inprivate|incognito", re.I)


def _settings():
    vals = {}
    try:
        from dotenv import dotenv_values
        vals = dotenv_values(ENV)
    except Exception:
        pass
    get = lambda k, d="": (vals.get(k) or os.getenv(k) or d).strip()
    return get("FREELLM_URL", DEFAULT_URL).rstrip("/"), get("FREELLM_API_KEY"), get("FREELLM_VISION_MODEL")


def _request(url, key, body=None, timeout=60):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


# Tried in this order (2026-11-03 on his FreeLLMAPI: gemini-flash-lite answered in 1.5 s, "auto" in 7 s; the big
# Gemini and Claude models timed out after 60 s, qwen3-vl-plus failed upstream)
PREFERRED = ["gemini-flash-lite", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-2.5-flash-lite", "auto"]


def _models(base, key, wanted):
    """Models to try, best first: his choice, else the fast ones this server has, then 'auto'."""
    if wanted:
        return [wanted]
    if base not in _picked:
        ids = [m.get("id", "") for m in _request(f"{base}/models", key, timeout=10).get("data", [])]
        found = [m for m in PREFERRED if m in ids] or [i for i in ids if SEES_IMAGES.search(i)][:2] or ids[:1]
        _picked[base] = found
    return list(_picked[base])


def _screenshot():
    """The main screen as JPEG bytes, and the title of the window in front."""
    import ctypes
    import mss
    from PIL import Image
    with mss.mss() as s:
        shot = s.grab(s.monitors[1])
        img = Image.frombytes("RGB", shot.size, shot.rgb)
    if img.width > MAX_WIDTH:
        img = img.resize((MAX_WIDTH, int(img.height * MAX_WIDTH / img.width)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=82)
    title = ctypes.create_unicode_buffer(300)
    user32 = ctypes.windll.user32
    user32.GetWindowTextW(user32.GetForegroundWindow(), title, 300)
    return buf.getvalue(), " ".join(title.value.split())


def look_at_screen(question="", confirm=None, **_):
    from config import ASSISTANT_NAME, USER_NAME
    base, key, wanted = _settings()
    if not key:
        return ("I can't see the screen yet: FreeLLMAPI's API key isn't in my settings. Copy it from the FreeLLMAPI "
                "app into the settings file as FREELLM_API_KEY.")
    question = (question or "").strip() or "What's on my screen?"
    t0 = time.time()
    jpeg, title = _screenshot()
    if PRIVATE.search(title):
        short = re.split(r"\s+[-–—|]\s+", title)[0][:50]
        if not confirm or not confirm(f"Your screen shows {short}, which may have private details, and the picture goes "
                                      "online to be read. Send it anyway?"):
            return "Okay, I didn't look. Switch to the window you want me to see and ask again."
    image = {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()}}
    text = {"type": "text", "text": PROMPT.format(name=ASSISTANT_NAME, user=USER_NAME, title=title, question=question)}
    try:
        models = _models(base, key, wanted)
    except urllib.error.HTTPError as e:
        return ("FreeLLMAPI didn't accept the API key in my settings. Check FREELLM_API_KEY." if e.code in (401, 403)
                else "FreeLLMAPI couldn't look at the screen just now. Try again in a moment.")
    except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
        print(f"   [vision: {e!r}]")
        return "I can't reach FreeLLMAPI. Is the FreeLLMAPI app open?"
    out = None
    for i, model in enumerate(models):   # a model that's down or slow: the next one
        body = {"model": model, "max_tokens": 350, "temperature": 0.2,
                "messages": [{"role": "user", "content": [text, image]}]}
        try:
            out = _request(f"{base}/chat/completions", key, body, timeout=20 if i < len(models) - 1 else 40)
            break
        except urllib.error.HTTPError as e:
            print(f"   [vision: {model}: HTTP {e.code} {e.read().decode('utf-8', 'replace')[:120]}]")
            if e.code in (401, 403):
                return "FreeLLMAPI didn't accept the API key in my settings. Check FREELLM_API_KEY."
        except urllib.error.URLError as e:
            if isinstance(e.reason, ConnectionRefusedError):
                return "I can't reach FreeLLMAPI. Is the FreeLLMAPI app open?"
            print(f"   [vision: {model}: {e!r}]")
        except (TimeoutError, ConnectionError) as e:
            print(f"   [vision: {model}: {e!r}]")
    if out is None:
        return "FreeLLMAPI couldn't look at the screen just now. Try again in a moment."
    try:
        said = out["choices"][0]["message"]["content"]
        said = said if isinstance(said, str) else " ".join(p.get("text", "") for p in said if isinstance(p, dict))
    except (KeyError, IndexError, TypeError):
        return "I couldn't make sense of the screen just now."
    said = re.sub(r"<think>.*?</think>", "", said, flags=re.S)
    said = re.sub(r"[*_#`]+", "", said).strip()
    print(f"   [vision: {model} in {time.time() - t0:.1f}s]")
    return said or "I couldn't make sense of the screen just now."


TOOLS = [
    {"name": "look_at_screen",
     "description": "Look at his PC screen and answer about it: 'what's on my screen', 'what am I looking at', "
                    "'read this error', 'what does this say', 'explain this chart'. question = his words.",
     "input_schema": {"type": "object", "properties": {"question": {"type": "string"}}, "required": []}},
]
FUNCTIONS = {"look_at_screen": look_at_screen}
