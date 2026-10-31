"""Files anywhere on the PC: find, read, list, move, copy, rename, delete, new folder.

Search uses her own map of every file on C: and D: (rebuilt in the background), then the Windows Search index, then
a quick scan of any other drive. Changes don't ask first (he chose that), with two safety nets: delete goes to the Recycle Bin (never
permanent), and Windows / Program Files / Jarvis's own keys are never touched. File text goes to the local brain only.
"""
import os
import re
import shutil
import threading
import time
from array import array
from bisect import bisect_right

from tools.extras import _find_folder, _spoken_filename, _squash, FOLDERS, ROOT

HOME = os.path.expanduser("~")
DRIVES = [f"{d}:\\" for d in "CDEFGH" if os.path.isdir(f"{d}:\\")]
SKIP = {"windows", "$recycle.bin", "system volume information", "programdata", "appdata", "node_modules", ".git",
        "__pycache__", "recovery", "perflogs", "$windows.~ws", "$winreagent", ".venv", "venv", "site-packages",
        "program files", "program files (x86)", "msocache", "$sysreset"}
# never changed (move/rename/delete): Windows itself, installed programs, and Jarvis's keys and memories
PROTECTED = [os.environ.get("SystemRoot", r"C:\Windows"), r"C:\Program Files", r"C:\Program Files (x86)",
             r"C:\ProgramData", os.path.join(ROOT, ".env"), os.path.join(ROOT, "data"), os.path.join(ROOT, ".venv")]
# never read out loud: secrets (her voice goes through Microsoft's servers)
SECRET = re.compile(r"(^\.env$|\.pem$|\.key$|\.kdbx$|\.pfx$|^id_rsa|^id_ed25519|remote_key\.txt$|login data$)", re.I)
TEXT_EXT = {".txt", ".md", ".csv", ".json", ".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".java", ".c", ".cpp",
            ".cs", ".go", ".rs", ".sql", ".yaml", ".yml", ".xml", ".ini", ".log", ".bat", ".ps1", ".sh", ".dart", ".kt"}
KINDS = {"pdf": (".pdf",), "word": (".docx", ".doc"), "excel": (".xlsx", ".xls", ".csv"), "powerpoint": (".pptx", ".ppt"),
         "image": (".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic"), "video": (".mp4", ".mkv", ".avi", ".mov", ".webm"),
         "music": (".mp3", ".wav", ".flac", ".m4a"), "code": (".py", ".js", ".ts", ".java", ".cpp", ".cs", ".html")}
CODE_EXT = (TEXT_EXT - {".txt", ".md", ".csv", ".html"}) | {".map", ".br", ".gz", ".dll", ".pyc", ".pyd", ".h", ".hpp",
                                                            ".lib", ".obj", ".class", ".jar", ".mjs", ".cjs", ".scss",
                                                            ".php", ".pm", ".pod", ".pl", ".rb", ".lua", ".mo", ".po"}
# his own places rank first in search; apps, games and tools installed there come after
MINE = tuple(os.path.normcase(os.path.join(HOME, f)) for f in
             ("Desktop", "Documents", "Downloads", "Pictures", "Videos", "Music", "OneDrive")) + ("d:\\",)
NOT_MINE = ("\\steamlibrary\\", "\\games\\", "\\xampp\\", "\\pinokio\\", "\\anaconda", "\\miniconda", "\\android\\sdk")
SEARCH_SECONDS = 6  # the fallback scan gives up after this: a slow answer is worse than "couldn't find it"
# every file and folder name on C: and D:, walked in the background (about 4s) so search sees all of them instantly
MAP_DRIVES = [d for d in DRIVES if d[0] in "CD"]
MAP_EVERY = 600   # seconds between rebuilds; a change she makes herself rebuilds it sooner

last = []   # paths from the last search: "open the second one", "delete the first one"
_index_lock = threading.Lock()
_map = None   # (folder paths, file names, folder index of each file) once the first walk is done
_map_dirty = threading.Event()


def _protected(path):
    p = os.path.normcase(os.path.abspath(path))
    if p.rstrip("\\") in [os.path.normcase(d).rstrip("\\") for d in DRIVES]:
        return True   # a whole drive
    return any(p == os.path.normcase(x) or p.startswith(os.path.normcase(x) + os.sep) for x in PROTECTED)


def _index_search(words, ext=(), limit=40):
    """Windows Search index: file names containing every word. [] if the index isn't available."""
    try:
        import comtypes.client as cc
        with _index_lock:
            conn = cc.CreateObject("ADODB.Connection")
            conn.Open("Provider=Search.CollatorDSO;Extended Properties='Application=Windows';")
            where = " AND ".join(f"System.FileName LIKE '%{w.replace(chr(39), '')}%'" for w in words) or "1=1"
            if ext:
                where += " AND (" + " OR ".join(f"System.FileExtension = '{e}'" for e in ext) + ")"
            rs = cc.CreateObject("ADODB.Recordset")
            rs.Open(f"SELECT TOP {limit} System.ItemPathDisplay FROM SystemIndex WHERE {where} "
                    "ORDER BY System.DateModified DESC", conn)
            out = []
            while not rs.EOF:
                out.append(rs.Fields.Item(0).Value)
                rs.MoveNext()
            rs.Close()
            conn.Close()
        return [p for p in out if p and os.path.exists(p)]
    except Exception as e:
        print(f"   [search index: {e!r}]")
        return []


def _packed(keys):
    """One newline-joined string of squashed names + where each starts: str.find over it is ~100x faster than a loop."""
    starts, at = array("I"), 0
    for k in keys:
        starts.append(at)
        at += len(k) + 1
    return "\n".join(keys), starts


def _build_map():
    dirs, names, where = [], [], array("I")
    for root in MAP_DRIVES:
        for dirpath, dirnames, files in os.walk(root):
            dirnames[:] = [d for d in dirnames if d.lower() not in SKIP and not d.startswith(".")]
            where.extend([len(dirs)] * len(files))
            dirs.append(dirpath)
            names.extend(files)
    file_keys = _packed([_squash(os.path.splitext(n)[0]) for n in names])
    dir_keys = _packed([_squash(os.path.basename(d.rstrip("\\"))) for d in dirs])
    return dirs, names, where, file_keys, dir_keys


def _hits(packed, key):
    """Indexes of every packed name containing `key`."""
    text, starts = packed
    out, pos = [], text.find(key)
    while pos != -1:
        i = bisect_right(starts, pos) - 1
        out.append(i)
        nxt = text.find("\n", pos)
        if nxt == -1:
            break
        pos = text.find(key, nxt + 1)
    return out


def _rank(path):
    """0 for his own files in his own folders, 1 for other files (inside apps and games), 2 for code and program parts."""
    if os.path.splitext(path)[1].lower() in CODE_EXT:
        return 2
    p = os.path.normcase(path)
    return 0 if p.startswith(MINE) and not any(x in p for x in NOT_MINE) else 1


def _map_loop():
    global _map
    while True:
        try:
            t = time.perf_counter()
            _map = _build_map()
            print(f"   [file map: {len(_map[1])} files, {len(_map[0])} folders in {time.perf_counter() - t:.1f}s]")
        except Exception as e:
            print(f"   [file map: {e!r}]")
        _map_dirty.wait(MAP_EVERY)
        _map_dirty.clear()
        time.sleep(2)   # let a burst of moves finish first


def _map_search(key, ext=(), folders=False):
    """Every mapped name that squashes to contain `key`, newest first. None until the first walk is done."""
    m = _map
    if m is None:
        return None
    dirs, names, where, file_keys, dir_keys = m
    if folders:
        found = [dirs[i] for i in _hits(dir_keys, key) if i]   # 0 is a drive itself
    else:
        found = [os.path.join(dirs[where[i]], names[i]) for i in _hits(file_keys, key)
                 if not ext or names[i].lower().endswith(ext)]
    found = [p for p in sorted(found, key=_rank)[:300] if os.path.exists(p)]
    return sorted(found, key=lambda p: (_rank(p), -os.path.getmtime(p)))


def _scan(key, ext=(), folders=False):
    """Walk the drives (user folders first) for names that squash to contain `key`. Time-limited."""
    t0, found = time.perf_counter(), []
    roots = [HOME] + DRIVES
    if _map is not None:
        roots = [d for d in DRIVES if d not in MAP_DRIVES]   # C: and D: are already in the map
    seen = set()
    for root in roots:
        for dirpath, dirnames, files in os.walk(root):
            if time.perf_counter() - t0 > SEARCH_SECONDS or len(found) >= 40:
                return found
            if dirpath in seen:
                dirnames[:] = []
                continue
            seen.add(dirpath)
            dirnames[:] = [d for d in dirnames if d.lower() not in SKIP and not d.startswith(".")]
            names = dirnames if folders else files
            for f in names:
                if key in _squash(f if folders else os.path.splitext(f)[0]) and (not ext or f.lower().endswith(ext)):
                    found.append(os.path.join(dirpath, f))
    return found


def find(name, kind=None, folders=False):
    """Best matches for a spoken name, best first (exact name, then name containing it, newest first)."""
    want = _spoken_filename(name)
    stem, typed = os.path.splitext(want)
    ext = KINDS.get((kind or "").lower().rstrip("s"), ()) or ((typed,) if typed else ())
    if typed and typed not in sum(KINDS.values(), ()) and typed not in TEXT_EXT:
        stem, ext = want, ()   # "version 2.1" isn't an extension
    words = [w for w in re.split(r"[\s_\-]+", stem) if w]
    key = _squash(stem)
    if not key:
        return []
    hits = _map_search(key, ext, folders)
    if not hits:
        hits = [] if folders else _index_search(words, ext)
        hits = [p for p in hits if key in _squash(os.path.splitext(os.path.basename(p))[0])] or hits
    if not hits:
        hits = _scan(key, ext, folders)
    exact = [p for p in hits if _squash(os.path.splitext(os.path.basename(p))[0]) == key]
    rest = [p for p in hits if p not in exact]
    return list(dict.fromkeys(exact + rest))


def _where(path):
    """Spoken location: just the folder it's in ('Downloads', 'D drive'), not the whole path."""
    folder = os.path.dirname(path)
    if os.path.normcase(folder) == os.path.normcase(HOME):
        return "your home folder"
    name = os.path.basename(folder.rstrip("\\"))
    return name or f"the {folder[0]} drive"


def _resolve(name, kind=None):
    """A path from: a full path, 'first'/'2'/'the last one' (last search), or a search. None if nothing."""
    n = (name or "").strip().strip('"')
    if os.path.exists(n):
        return os.path.abspath(n)   # one kind of slash: the Recycle Bin call failed on "C:/x\\y.txt"
    order = {"first": 0, "one": 0, "1": 0, "second": 1, "two": 1, "2": 1, "third": 2, "three": 2, "3": 2,
             "fourth": 3, "4": 3, "fifth": 4, "5": 4, "last": -1}
    m = re.fullmatch(r"(?:the\s+)?(\w+)(?:\s+(?:one|file|result))?", n.lower())
    if last and m and m[1] in order:
        i = order[m[1]]
        return last[i] if -len(last) <= i < len(last) else None
    found = find(n, kind)
    return found[0] if found else None


def _target_folder(to):
    t = (to or "").strip().strip('"')
    if os.path.isdir(t):
        return t
    key = t.lower().removesuffix(" folder").removeprefix("my ").removeprefix("the ").strip()
    if key in FOLDERS:
        path = os.path.join(HOME, FOLDERS[key])
        alt = os.path.join(HOME, "OneDrive", FOLDERS[key])
        return path if os.path.isdir(path) else alt if os.path.isdir(alt) else None
    m = re.fullmatch(r"([a-z])\s*(?:drive|:)?", key)
    if m and os.path.isdir(f"{m[1].upper()}:\\"):
        return f"{m[1].upper()}:\\"
    return _find_folder(key) or next(iter(find(key, folders=True)), None)


# ------------------------------------------------------------------ tools
def find_files(name, kind=None, **_):
    global last
    found = find(name, kind)
    last = found[:5]
    if not found:
        return f"I couldn't find any file called {_spoken_filename(name)}."
    if len(found) == 1:
        return f"Found {os.path.basename(found[0])} in {_where(found[0])}. Say open it, read it, or move it."
    lines = [f"{i + 1}. {os.path.basename(p)} in {_where(p)}" for i, p in enumerate(found[:3])]
    more = f", and {len(found) - 3} more" if len(found) > 3 else ""
    return f"I found {len(found)}. " + "; ".join(lines) + f"{more}. Say open, read or move the first one."


def list_folder(name, **_):
    path = _target_folder(name)
    if not path:
        return f"I couldn't find a folder called {name}."
    try:
        items = sorted(os.scandir(path), key=lambda e: e.stat().st_mtime, reverse=True)
    except OSError as e:
        return f"I can't open {name}: {e.strerror}."
    dirs = [e.name for e in items if e.is_dir()]
    files = [e.name for e in items if e.is_file()]
    global last
    last = [os.path.join(path, f) for f in files[:5]]
    said = os.path.basename(path.rstrip("\\")) or path
    parts = []
    if files:
        parts.append(f"newest files: {', '.join(files[:6])}")
    if dirs:
        parts.append(f"folders: {', '.join(dirs[:5])}")
    return (f"{said} has {len(files)} files and {len(dirs)} folders. " + "; ".join(parts) + ".") if parts \
        else f"{said} is empty."


def read_text(name, kind=None):
    """(path, text) for the brain to answer from; (None, reason) if it can't."""
    path = _resolve(name, kind)
    if not path or not os.path.isfile(path):
        return None, f"I couldn't find a file called {_spoken_filename(name)}."
    base = os.path.basename(path)
    if SECRET.search(base):
        return None, f"{base} holds passwords or keys, so I won't read it out."
    try:
        text = extract(path)
    except Exception as e:
        return None, f"I couldn't read {base}: {e}"
    if text is None:
        return None, f"I can't read {base}: it isn't a text, PDF, Word or Excel file. Say open it to see it."
    if text == "":
        return None, f"{base} isn't a text file, so I can't read it. Say open it to see it."
    if len(text) < 20:
        return None, f"{base} has no text I can read (it may be scanned images)."
    return path, text


def extract(path, pages=15):
    """The text of a PDF, Word, PowerPoint, Excel or text file. None: not a kind she reads; "": binary."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        from pypdf import PdfReader
        text = "\n".join((p.extract_text() or "") for p in PdfReader(path).pages[:pages])
    elif ext == ".docx":
        import zipfile
        with zipfile.ZipFile(path) as z:
            xml = z.read("word/document.xml").decode("utf-8", "ignore")
        text = re.sub(r"<[^>]+>", "", re.sub(r"</w:p>", "\n", xml))
    elif ext == ".pptx":
        import zipfile
        with zipfile.ZipFile(path) as z:
            slides = sorted(n for n in z.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n))
            text = "\n".join(re.sub(r"<[^>]+>", " ", z.read(s).decode("utf-8", "ignore")) for s in slides[:30])
    elif ext == ".xlsx":
        import zipfile
        with zipfile.ZipFile(path) as z:
            shared = z.read("xl/sharedStrings.xml").decode("utf-8", "ignore") if "xl/sharedStrings.xml" in z.namelist() else ""
        text = re.sub(r"<[^>]+>", " ", re.sub(r"</si>", "\n", shared))
    elif ext in TEXT_EXT or os.path.getsize(path) < 200_000:
        with open(path, encoding="utf-8", errors="ignore") as f:
            text = f.read(60_000)
        if "\x00" in text[:2000]:
            return ""
    else:
        return None
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", html_unescape(text))).strip()


def html_unescape(s):
    import html
    return html.unescape(s)


def file_action(action, name, to=None, **_):
    said = _file_action(action, name, to)
    _map_dirty.set()   # the map shows the change in a few seconds, not ten minutes
    return said


def _file_action(action, name, to=None):
    """move / copy / rename / delete / new_folder. No confirmation (his choice); delete = Recycle Bin."""
    global last
    action = (action or "").lower()
    if action == "new_folder":
        parent = _target_folder(to) if to else os.path.join(HOME, "Desktop")
        if not parent:
            return f"I couldn't find the folder {to}."
        path = os.path.join(parent, re.sub(r'[<>:"/\\|?*]', "", name).strip())
        if _protected(path):
            return "I don't make folders inside Windows or program folders."
        os.makedirs(path, exist_ok=True)
        return f"Made a folder called {os.path.basename(path)} in {folder_name(parent)}."
    src = _resolve(name)
    if not src:
        return f"I couldn't find {_spoken_filename(name)}."
    base = folder_name(src)
    if _protected(src):
        return f"{base} is part of Windows, a program, or my own settings, so I won't change it."
    if action == "delete":
        from send2trash import send2trash
        send2trash(src)
        last = [p for p in last if p != src]
        return f"Moved {base} to the Recycle Bin."
    if action == "rename":
        if not to:
            return "What should I rename it to?"
        new = _spoken_filename(to)
        if not os.path.splitext(new)[1] and os.path.isfile(src):
            new += os.path.splitext(src)[1]   # "rename it to final" keeps .pdf
        dest = os.path.join(os.path.dirname(src), new)
        if os.path.exists(dest):
            return f"There's already a {new} there."
        os.rename(src, dest)
        last = [dest if p == src else p for p in last]
        return f"Renamed {base} to {new}."
    if action in ("move", "copy"):
        folder = _target_folder(to)
        if not folder:
            return f"I couldn't find the folder {to}." if to else f"Where should I {action} it?"
        dest = os.path.join(folder, base)
        if _protected(dest):
            return "I don't put files inside Windows or program folders."
        if os.path.exists(dest):
            return f"{folder_name(folder)} already has a {base}."
        if action == "move":
            shutil.move(src, dest)
            last = [dest if p == src else p for p in last]
        elif os.path.isdir(src):
            shutil.copytree(src, dest)
        else:
            shutil.copy2(src, dest)
        return f"{'Moved' if action == 'move' else 'Copied'} {base} to {folder_name(folder)}."
    return "I can move, copy, rename or delete files, or make a new folder."


def folder_name(path):
    return os.path.basename(path.rstrip("\\")) or path


def _tool(name, description, props=None, required=None):
    return {"name": name, "description": description,
            "input_schema": {"type": "object", "properties": props or {}, "required": required or []}}


TOOLS = [
    _tool("find_files", "Search ALL drives for files by name, e.g. 'find my resume', 'where is the invoice PDF'. "
          "'kind' is optional: pdf, word, excel, powerpoint, image, video, music, code.",
          {"name": {"type": "string"}, "kind": {"type": "string"}}, ["name"]),
    _tool("read_file", "Read a file (text, code, PDF, Word, PowerPoint, Excel/CSV) and answer his question about it or "
          "summarise it. 'name' is the file name, or 'first'/'second' from the last search; 'question' is what he asked.",
          {"name": {"type": "string"}, "question": {"type": "string"}}, ["name"]),
    _tool("list_folder", "Say what's inside a folder (downloads, desktop, a drive, or any folder name).",
          {"name": {"type": "string"}}, ["name"]),
    _tool("file_action", "Change files: move, copy, rename or delete a file/folder, or make a new_folder. 'name' is the "
          "file (or 'first'/'second' from the last search; for new_folder the new folder's name), 'to' is the target "
          "folder (move/copy/new_folder) or the new name (rename). Delete goes to the Recycle Bin.",
          {"action": {"type": "string", "enum": ["move", "copy", "rename", "delete", "new_folder"]},
           "name": {"type": "string"}, "to": {"type": "string"}}, ["action", "name"]),
]
FUNCTIONS = {"find_files": find_files, "list_folder": list_folder, "file_action": file_action}

threading.Thread(target=_map_loop, name="file-map", daemon=True).start()
