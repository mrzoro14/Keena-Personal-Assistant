"""Reading Gmail by voice: "any important emails?", "read the email from Arjun", "read my latest email".

Through Edith's own Chrome (tools/browser.py), signed in to his Google account once. Each check opens Gmail in a
BACKGROUND tab, reads it and closes it again, so nothing jumps in front of what he's doing. The list never marks
anything read; reading one email opens it, so Gmail marks that one read (as if he'd opened it).
Writing and sending mail is browser.gmail_compose / gmail_send (draft_email), which always asks before sending.
"""
import re
import urllib.parse

from tools import browser
from tools.browser import BrowserError, _call, _connect, _not_signed_in

INBOX = "https://mail.google.com/mail/u/0/#inbox"
SIGN_IN = "https://accounts.google.com/ServiceLogin?service=mail&continue=https://mail.google.com/mail/"
NOT_SIGNED_IN = ("Gmail isn't signed in, in my Chrome window. I've opened the sign-in page there: sign in once, "
                 "then ask me again.")
ROWS_JS = """() => [...document.querySelectorAll('div[role=main] tr.zA')].filter(r => r.offsetParent).slice(0, 40)
  .map(r => {
    const who = r.querySelector('.yW [email]');
    const date = r.querySelector('td.xW span');
    return {unread: r.classList.contains('zE'),
            from: who ? (who.getAttribute('name') || who.textContent) : (r.querySelector('.yW') || {}).innerText || '',
            subject: (r.querySelector('.bog') || {}).innerText || '',
            snippet: ((r.querySelector('.y2') || {}).innerText || '').replace(/^\\s*-\\s*/, ''),
            date: date ? date.innerText : ''};
  })"""
READY_JS = """() => location.host.startsWith('accounts.') || location.host.startsWith('workspace.') ||
  [...document.querySelectorAll('div[role=main] tr.zA, div[role=main] td.TC')].some(e => e.offsetParent)"""
EMAIL_JS = """() => {
  const bodies = [...document.querySelectorAll('div[role=main] .a3s')].filter(e => e.offsetParent);
  const senders = [...document.querySelectorAll('div[role=main] .gD')].filter(e => e.offsetParent);
  const last = senders[senders.length - 1];
  return {subject: (document.querySelector('div[role=main] h2.hP') || {}).innerText || '',
          from: last ? (last.getAttribute('name') || last.innerText) : '',
          body: bodies.length ? bodies[bodies.length - 1].innerText : ''};
}"""


def _background_tab(url):
    """A tab that opens behind the one he's looking at."""
    b = _connect(True)
    ctx = b.contexts[0]
    if not ctx.pages:   # Chrome just started: a tab can only open behind another one once a window exists
        try:
            ctx.wait_for_event("page", timeout=3000)
        except Exception:
            ctx.new_page()
    with ctx.expect_page(timeout=10000) as info:
        b.new_browser_cdp_session().send("Target.createTarget", {"url": url, "background": True, "newWindow": False})
    return info.value


def _open(url):
    page = _background_tab(url)
    try:
        page.wait_for_function(READY_JS, timeout=25000)
    except Exception:
        pass
    if _not_signed_in(page):
        page.goto(SIGN_IN, wait_until="domcontentloaded", timeout=25000)
        page.bring_to_front()
        browser._window_front(page)
        raise BrowserError(NOT_SIGNED_IN)
    page.wait_for_timeout(600)   # rows keep filling in for a moment
    return page


def _clean(s, n=90):
    s = re.sub(r"\s+", " ", s or "").strip()
    return s if len(s) <= n else s[:n].rsplit(" ", 1)[0] + "..."


def _who(name):
    """'Amazon.in' / 'Arjun Kumar' / 'me, Arjun 3' -> something short to say."""
    name = re.sub(r"\s+\d+$", "", _clean(name, 40))
    return name or "someone"


def _unread(limit):
    page = _open(INBOX)
    try:
        rows = page.evaluate(ROWS_JS)
        count = page.evaluate("""() => { const a = document.querySelector('a[href$="#inbox"]');
            const n = a && a.closest('div').querySelector('.bsU'); return n ? n.innerText : ''; }""")
    finally:
        page.close()
    unread = [r for r in rows if r["unread"]]
    return unread[:limit], count


def check_email(limit=5, **_):
    """Unread mail in his inbox (Gmail's Primary tab): who from and what about."""
    try:
        unread, count = _call(_unread, int(limit or 5), timeout=60)
    except BrowserError as e:
        return str(e)
    if not unread:
        return "No new emails in your inbox."
    total = int(re.sub(r"\D", "", count) or len(unread))
    head = "You have one new email." if total == 1 else f"You have {total} new emails" + (
        f". The latest {len(unread)}:" if total > len(unread) else ":")
    lines = [f"From {_who(r['from'])}: {_clean(r['subject'] or r['snippet'], 80)}." for r in unread]
    return " ".join([head] + lines)


def _read_one(query):
    url = INBOX if not query else "https://mail.google.com/mail/u/0/#search/" + urllib.parse.quote(query)
    page = _open(url)
    try:
        row = page.locator("div[role=main] tr.zA:visible").first
        if not row.count():
            return None
        row.click(timeout=8000)
        page.wait_for_selector("div[role=main] .a3s", timeout=15000)
        page.wait_for_timeout(400)
        return page.evaluate(EMAIL_JS)
    finally:
        page.close()


def read_email(sender="", about="", **_):
    """Read one email out: the latest from someone, or about something, or simply the newest."""
    sender, about = (sender or "").strip(" .?!"), (about or "").strip(" .?!")
    query = " ".join(([f"from:({sender})"] if sender else []) + ([about] if about else []))
    try:
        mail = _call(_read_one, query, timeout=60)
    except BrowserError as e:
        return str(e)
    if not mail:
        what = f"from {sender}" if sender else f"about {about}" if about else "in your inbox"
        return f"I couldn't find an email {what}."
    body = re.sub(r"\n{2,}", "\n", (mail["body"] or "").strip())
    body = body if len(body) <= 1200 else body[:1200].rsplit(" ", 1)[0] + "... (it goes on)"
    return f"Email from {_who(mail['from'])}, subject {_clean(mail['subject'], 100) or 'none'}. {body}"


TOOLS = [
    {"name": "check_email",
     "description": "His new (unread) emails in Gmail: who they're from and what about ('any new emails?', "
                    "'check my mail', 'anything important in my inbox?').",
     "input_schema": {"type": "object", "properties": {"limit": {"type": "integer"}}, "required": []}},
    {"name": "read_email",
     "description": "Read one email out loud: the latest from a person ('read the email from Arjun'), about a topic "
                    "('read the email about the invoice'), or just the newest one (both empty).",
     "input_schema": {"type": "object", "properties": {"sender": {"type": "string"}, "about": {"type": "string"}},
                      "required": []}},
]
FUNCTIONS = {"check_email": check_email, "read_email": read_email}
