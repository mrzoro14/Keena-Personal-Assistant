"""Search INSIDE his documents: "which file has my P number", "find the PDF about the visa".

A helper process (python -m tools.docsearch, started by Edith at low priority) reads every PDF, Word, PowerPoint,
Excel and text file in his own folders once, and keeps the words in data/docs.db (SQLite full-text search). After
that it only re-reads files that changed. Nothing leaves the laptop; she says file names, never the text.
"""
import os
import re
import sqlite3
import subprocess
import sys
import time

from tools import files

DB = os.path.join(files.ROOT, "data", "docs.db")
OFFICE_EXT = {".pdf", ".docx", ".pptx", ".xlsx"}   # always his, even inside a project folder
DOC_EXT = OFFICE_EXT | {".txt", ".md", ".csv"}
PROJECT_MARKERS = (".git", "package.json", "pyproject.toml", "setup.py", "Cargo.toml", "pubspec.yaml",
                   "build.gradle", "pom.xml", "go.mod", "CMakeLists.txt")
MAX_BYTES = 30_000_000
MAX_CHARS = 100_000
# project boilerplate, not his writing
BOILERPLATE = re.compile(r"^(readme|license|licence|changelog|changes|contributing|security|code_of_conduct|"
                         r"authors|notice|history|third.party.notices?|copying)\b", re.I)
EVERY = 900   # seconds between passes over the drives
STOP = {"a", "an", "the", "my", "of", "in", "on", "about", "for", "with", "that", "which", "file", "files", "document",
        "documents", "doc", "pdf", "says", "say", "has", "have", "mentions", "mention", "contains", "talks", "is", "it",
        "and", "or", "to", "me", "find", "where", "what", "word", "words"}


def _db():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    c = sqlite3.connect(DB, timeout=10)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("CREATE TABLE IF NOT EXISTS seen (path TEXT PRIMARY KEY, mtime REAL)")
    c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS docs USING fts5(path UNINDEXED, name, body, "
              "tokenize='porter unicode61 remove_diacritics 2')")
    return c


_project = {}


def _in_project(folder):
    """Inside a code project (a .git or package.json up the tree)? Its .md/.txt files are docs for code, not his."""
    if folder in _project:
        return _project[folder]
    parent = os.path.dirname(folder)
    here = any(os.path.exists(os.path.join(folder, m)) for m in PROJECT_MARKERS)
    _project[folder] = here or (parent != folder and _in_project(parent))
    return _project[folder]


def _wanted(path):
    base = os.path.basename(path)
    ext = os.path.splitext(base)[1].lower()
    if ext not in DOC_EXT or files._rank(path) != 0 or files.SECRET.search(base) or BOILERPLATE.match(base):
        return False
    return ext in OFFICE_EXT or not _in_project(os.path.dirname(path))


def index_once(c, say=print):
    t0, done, failed = time.perf_counter(), 0, 0
    while files._map is None:   # importing tools.files walks the drives in the background
        time.sleep(1)
    dirs, names, where = files._map[:3]
    paths = [p for p in (os.path.join(dirs[i], n) for n, i in zip(names, where)) if _wanted(p)]
    seen = dict(c.execute("SELECT path, mtime FROM seen"))
    for p in paths:
        try:
            st = os.stat(p)
        except OSError:
            continue
        if seen.pop(p, None) == st.st_mtime:
            continue
        text = ""
        if st.st_size <= MAX_BYTES:
            try:
                text = (files.extract(p, pages=60) or "")[:MAX_CHARS]
            except Exception:
                failed += 1
        c.execute("DELETE FROM docs WHERE path = ?", (p,))
        c.execute("INSERT INTO docs (path, name, body) VALUES (?, ?, ?)",
                  (p, os.path.splitext(os.path.basename(p))[0], text))
        c.execute("INSERT OR REPLACE INTO seen VALUES (?, ?)", (p, st.st_mtime))
        done += 1
        if done % 50 == 0:
            c.commit()
    _project.clear()
    for gone in seen:   # deleted, moved, or no longer wanted since the last pass
        c.execute("DELETE FROM docs WHERE path = ?", (gone,))
        c.execute("DELETE FROM seen WHERE path = ?", (gone,))
    c.commit()
    say(f"   [doc search: {len(paths)} documents, {done} read, {len(seen)} removed, {failed} unreadable, "
        f"{time.perf_counter() - t0:.0f}s]")


def _terms(query):
    words = [w for w in re.findall(r"[\w']+", query.lower()) if w not in STOP and len(w) > 1]
    return ['"' + w.replace('"', "") + '"' for w in words]


def search(query, limit=5):
    """[(path, snippet)] best first: every word in the file, else most of them."""
    if not os.path.exists(DB):
        return None
    terms = _terms(query)
    if not terms:
        return []
    c = sqlite3.connect(DB, timeout=5)
    # every word; with 3+ words, all but one (people misremember one word)
    tries = [terms] + ([terms[:i] + terms[i + 1:] for i in range(len(terms))] if len(terms) >= 3 else [])
    try:
        for words in tries:
            rows = c.execute("SELECT path, snippet(docs, 2, '', '', '...', 12) FROM docs WHERE docs MATCH ? "
                             "ORDER BY bm25(docs, 0, 4, 1) LIMIT ?", (" AND ".join(words), limit * 3)).fetchall()
            rows = [r for r in rows if os.path.exists(r[0])]
            if rows:
                return rows[:limit]
        return []
    finally:
        c.close()


def search_in_files(query, **_):
    found = search(query)
    if found is None:
        return "I'm still reading your documents for the first time. Ask me again in a few minutes."
    if not found:
        return f"None of your documents mention {query}."
    files.last = [p for p, _ in found]
    said = lambda p: f"{' '.join(os.path.splitext(os.path.basename(p))[0].split())} in {files._where(p)}"
    if len(found) == 1:
        return f"{said(found[0][0])} mentions it. Say open it or read it."
    lines = [f"{i + 1}. {said(p)}" for i, (p, _) in enumerate(found[:3])]
    count = "Several documents" if len(found) >= 5 else f"{len(found)} documents"
    return f"{count} mention it. " + "; ".join(lines) + ". Say open or read the first one."


def start_helper():
    """Run the indexer as its own low-priority process: reading PDFs never slows her down."""
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    exe = pythonw if os.path.exists(pythonw) else sys.executable
    try:
        return subprocess.Popen([exe, "-m", "tools.docsearch", str(os.getpid())], cwd=files.ROOT,
                                creationflags=subprocess.BELOW_NORMAL_PRIORITY_CLASS | subprocess.CREATE_NO_WINDOW)
    except OSError as e:
        print(f"   [doc search: couldn't start ({e})]")


TOOLS = [
    {"name": "search_in_files",
     "description": "Search INSIDE his documents (PDF, Word, PowerPoint, Excel, text) for words or a topic: "
                    "'which file has my passport number', 'find the document about the visa'. For a file NAME use "
                    "find_files instead.",
     "input_schema": {"type": "object", "properties": {
         "query": {"type": "string", "description": "the words or topic to look for, e.g. 'P number', 'visa'"}},
         "required": ["query"]}},
]
FUNCTIONS = {"search_in_files": search_in_files}


if __name__ == "__main__":
    import ctypes
    import logging
    logging.getLogger("pypdf").setLevel(logging.ERROR)   # odd fonts in PDFs: harmless, but thousands of lines
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _lock = kernel32.CreateMutexW(None, False, "Local\\EdithDocSearch")
    if ctypes.get_last_error() == 183:
        sys.exit(0)   # one indexer is enough
    import psutil
    parent = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 0
    log = open(os.path.join(files.ROOT, "data", "docsearch.log"), "a", encoding="utf-8")
    conn = _db()
    while True:
        try:
            index_once(conn, say=lambda s: (log.write(time.strftime("%H:%M ") + s.strip() + "\n"), log.flush()))
        except Exception as e:
            log.write(f"{time.strftime('%H:%M')} error: {e!r}\n")
            log.flush()
        for _ in range(EVERY // 15):   # stop with her: no indexer left behind after "goodbye Edith"
            if parent and not psutil.pid_exists(parent):
                sys.exit(0)
            time.sleep(15)
