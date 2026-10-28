"""Read the text of a supermarket's promotions page, for the importer.

Only pages on the supermarkets' own domains are fetched, with an honest user agent. Sites that
block automated requests (AH, Jumbo) or only render with JavaScript (PLUS, Lidl, Vomar) give an
error that tells the owner to use screenshots instead; nothing tries to get around that.
"""
from __future__ import annotations

import html
import re
import urllib.error
import urllib.request
import zlib
from urllib.parse import urlsplit

USER_AGENT = "BoodschappenAgent/1.0 (+https://github.com/MLeona020839/boodschappen-agent)"
MAX_BYTES = 5_000_000
MAX_CHARS = 40_000
MIN_CHARS = 400   # less than this is a cookie wall, an error page or a JavaScript-only shell

STORE_DOMAINS = {
    "ah": "ah.nl", "dekamarkt": "dekamarkt.nl", "dirk": "dirk.nl", "hoogvliet": "hoogvliet.com",
    "jumbo": "jumbo.com", "lidl": "lidl.nl", "plus": "plus.nl", "poiesz": "poiesz-supermarkten.nl",
    "spar": "spar.nl", "vomar": "vomar.nl",
}


class PageError(ValueError):
    """The page can't be read; the message is shown to the owner."""


def store_for(url: str) -> str:
    """The store whose domain the URL is on, or PageError for anything else."""
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or parts.port not in (None, 443):
        raise PageError("Use the https:// address of the supermarket's promotions page.")
    for store, domain in STORE_DOMAINS.items():
        if host == domain or host.endswith("." + domain):
            return store
    raise PageError("Only the supermarkets' own websites can be read (" + ", ".join(sorted(STORE_DOMAINS.values())) + ").")


class _SameStoreRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        try:
            same = store_for(newurl) == store_for(req.full_url)
        except PageError:
            same = False
        if not same:
            raise PageError("The page redirects to another website.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_SameStoreRedirects)


def page_text(raw: str) -> str:
    """Visible text of an HTML page, whitespace collapsed."""
    raw = re.sub(r"(?is)<(script|style|noscript|svg|template)\b[^>]*>.*?</\1\s*>", " ", raw)
    raw = re.sub(r"(?s)<!--.*?-->", " ", raw)
    text = html.unescape(re.sub(r"<[^>]+>", " ", raw))
    return re.sub(r"\s+", " ", text).strip()


def fetch(url: str, timeout: float = 20) -> tuple[str, str]:
    """(store code, page text) for a promotions page, or PageError."""
    store = store_for(url)
    req = urllib.request.Request(url.strip(), headers={
        "User-Agent": USER_AGENT, "Accept": "text/html", "Accept-Language": "nl-NL,nl;q=0.9",
        "Accept-Encoding": "gzip"})
    blocked = PageError("This website blocks automatic reading. Use screenshots of the promotions instead.")
    try:
        with _opener.open(req, timeout=timeout) as r:
            body = r.read(MAX_BYTES + 1)
            if r.headers.get("Content-Encoding") == "gzip":
                body = zlib.decompressobj(wbits=31).decompress(body, MAX_BYTES + 1)  # bounded
            charset = r.headers.get_content_charset() or "utf-8"
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 429):
            raise blocked from None
        raise PageError(f"The website answered with error {e.code}.") from None
    except OSError:   # URLError, refused or dropped connections, timeouts
        raise blocked from None
    if len(body) > MAX_BYTES:
        raise PageError("The page is too large to read.")
    text = page_text(body.decode(charset, errors="replace"))
    if len(text) < MIN_CHARS or "browser wordt niet ondersteund" in text.lower():
        raise PageError("This page only shows its promotions with JavaScript. Use screenshots instead.")
    return store, text[:MAX_CHARS]
