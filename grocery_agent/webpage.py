"""Read a supermarket's promotions from a web address, for the importer.

Two kinds of address are accepted:

- the supermarket's own promotions page: its visible text is read;
- a folder on allefolders.nl: the folder's page images are downloaded and read like screenshots.
  This also covers AH and Jumbo, whose own sites block automated requests.

Everything is fetched with an honest user agent, from those sites only. Pages that block automated
requests or only render with JavaScript give an error telling the owner what to use instead;
nothing tries to get around that.
"""
from __future__ import annotations

import html
import re
import urllib.error
import urllib.request
import zlib
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

USER_AGENT = "BoodschappenAgent/1.0 (+https://github.com/MLeona020839/boodschappen-agent)"
MAX_BYTES = 5_000_000
MAX_CHARS = 40_000
MIN_CHARS = 400   # less than this is a cookie wall, an error page or a JavaScript-only shell
MAX_FOLDER_PAGES = 40

STORE_DOMAINS = {
    "ah": ("ah.nl",), "dekamarkt": ("dekamarkt.nl",), "dirk": ("dirk.nl",),
    "hoogvliet": ("hoogvliet.com", "hoogvliet.nl"), "jumbo": ("jumbo.com",), "lidl": ("lidl.nl",),
    "plus": ("plus.nl",), "poiesz": ("poiesz-supermarkten.nl",), "spar": ("spar.nl",), "vomar": ("vomar.nl",),
}
FOLDER_SITE = "allefolders.nl"
FOLDER_CDN = "cdn.jafolders.com"
FOLDER_SLUGS = {"albert-heijn": "ah"}   # every other store's slug is its code
# /albert-heijn-aanbiedingen/<folder id>/0 (a folder) or /albert-heijn/folder-aanbiedingen (the store)
_FOLDER_PATH = re.compile(r"^/([a-z0-9-]+?)(?:-aanbiedingen/[0-9a-f-]{36}(?:/\d+)?|/folder-aanbiedingen)/?$")
_FOLDER_LINK = re.compile(r'href="(/[a-z0-9-]+-aanbiedingen/[0-9a-f-]{36}/0)"')
_FOLDER_IMAGE = re.compile(r"https://cdn\.jafolders\.com/pages/[0-9a-f-]{36}/large\.webp")


class PageError(ValueError):
    """The page can't be read; the message is shown to the owner."""


@dataclass
class Page:
    store: str
    text: str = ""
    files: list[tuple[str, bytes]] = field(default_factory=list)


def _host(url: str) -> str:
    parts = urlsplit(url.strip())
    if parts.scheme != "https" or parts.port not in (None, 443):
        raise PageError("Use the https:// address of the promotions page.")
    return (parts.hostname or "").lower()


def _on(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def store_for(url: str) -> str:
    """The supermarket whose own domain the URL is on, or PageError."""
    host = _host(url)
    for store, domains in STORE_DOMAINS.items():
        if any(_on(host, d) for d in domains):
            return store
    raise PageError("Use a supermarket's own promotions page or a folder on allefolders.nl.")


def _site(url: str) -> str:
    """Which allowed site the URL is on (a store code, the folder site or its image CDN)."""
    host = _host(url)
    if _on(host, FOLDER_SITE):
        return FOLDER_SITE
    if host == FOLDER_CDN:
        return FOLDER_CDN
    return store_for(url)


class _SameSiteRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        try:
            same = _site(newurl) == _site(req.full_url)
        except PageError:
            same = False
        if not same:
            raise PageError("The page redirects to another website.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_SameSiteRedirects)


def _get(url: str, accept: str, timeout: float) -> tuple[bytes, str]:
    """(body, charset) of an allowed URL, or PageError."""
    _site(url)
    req = urllib.request.Request(url.strip(), headers={
        "User-Agent": USER_AGENT, "Accept": accept, "Accept-Language": "nl-NL,nl;q=0.9",
        "Accept-Encoding": "gzip"})
    blocked = PageError("This website blocks automatic reading. Use its folder on allefolders.nl "
                        "or screenshots instead.")
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
    return body, charset


def page_text(raw: str) -> str:
    """Visible text of an HTML page, whitespace collapsed."""
    raw = re.sub(r"(?is)<(script|style|noscript|svg|template)\b[^>]*>.*?</\1\s*>", " ", raw)
    raw = re.sub(r"(?s)<!--.*?-->", " ", raw)
    text = html.unescape(re.sub(r"<[^>]+>", " ", raw))
    return re.sub(r"\s+", " ", text).strip()


def fetch(url: str, timeout: float = 20) -> tuple[str, str]:
    """(store code, page text) for a supermarket's own promotions page, or PageError."""
    store = store_for(url)
    body, charset = _get(url, "text/html", timeout)
    text = page_text(body.decode(charset, errors="replace"))
    if len(text) < MIN_CHARS or "browser wordt niet ondersteund" in text.lower():
        raise PageError("This page only shows its promotions with JavaScript. "
                        "Use its folder on allefolders.nl or screenshots instead.")
    return store, text[:MAX_CHARS]


def folder_pages(url: str, timeout: float = 20) -> Page:
    """The page images of a folder on allefolders.nl, or PageError."""
    m = _FOLDER_PATH.match(urlsplit(url.strip()).path)
    slug = m.group(1) if m else ""
    store = FOLDER_SLUGS.get(slug, slug)
    if store not in STORE_DOMAINS:
        raise PageError("Open a supported supermarket's folder on allefolders.nl and copy its address.")
    raw = _get(url, "text/html", timeout)[0].decode("utf-8", errors="replace")
    if "/folder-aanbiedingen" in url:   # the store's page: take its newest folder
        link = _FOLDER_LINK.search(raw)
        if not link:
            raise PageError("No current folder found on that page.")
        raw = _get(urljoin(url, link.group(1)), "text/html", timeout)[0].decode("utf-8", errors="replace")
    images = list(dict.fromkeys(_FOLDER_IMAGE.findall(raw)))   # page order, no repeats
    if not images:
        raise PageError("No folder pages found at that address.")
    files = [(f"{store}-folder-p{i:02d}.webp", _get(src, "image/webp", timeout)[0])
             for i, src in enumerate(images[:MAX_FOLDER_PAGES], 1)]
    return Page(store, files=files)


def read(url: str) -> Page:
    """Promotions from any accepted address, or PageError."""
    host = _host(url)
    if _on(host, FOLDER_SITE):
        return folder_pages(url)
    if _on(host, "folderz.nl"):
        raise PageError("Folderz only shows small previews without JavaScript. "
                        "Open the same folder on allefolders.nl and use that address.")
    store, text = fetch(url)
    return Page(store, text=text)
