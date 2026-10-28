import io

import pytest

from grocery_agent import webpage


@pytest.mark.parametrize("url,store", [
    ("https://www.dirk.nl/aanbiedingen", "dirk"),
    ("https://dekamarkt.nl/aanbiedingen", "dekamarkt"),
    ("https://www.ah.nl/bonus", "ah"),
])
def test_store_for_supermarket_domains(url, store):
    assert webpage.store_for(url) == store


@pytest.mark.parametrize("url", [
    "http://www.dirk.nl/aanbiedingen",          # not https
    "https://www.dirk.nl:8443/aanbiedingen",    # odd port
    "https://evil-dirk.nl/aanbiedingen",        # look-alike domain
    "https://www.dirk.nl.evil.com/",
    "https://169.254.169.254/latest/meta-data",
    "https://localhost/",
    "file:///etc/passwd",
])
def test_store_for_rejects_other_addresses(url):
    with pytest.raises(webpage.PageError):
        webpage.store_for(url)


def test_page_text_drops_scripts_and_tags():
    raw = "<html><head><style>p{}</style><script>var x='€9';</script></head><body><p>Kipfilet&nbsp;<b>2+1</b></p><!-- x --></body></html>"
    assert webpage.page_text(raw) == "Kipfilet 2+1"


class _Response(io.BytesIO):
    def __init__(self, body, headers=None):
        super().__init__(body)
        import email.message
        self.headers = email.message.Message()
        self.headers["Content-Type"] = "text/html; charset=utf-8"
        for k, v in (headers or {}).items():
            self.headers[k] = v

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _serve(monkeypatch, result):
    def open_(req, timeout):
        if isinstance(result, Exception):
            raise result
        return result
    monkeypatch.setattr(webpage._opener, "open", open_)


def test_fetch_returns_text(monkeypatch):
    import gzip
    body = gzip.compress(("<body>" + "<p>Halfvolle melk 1+1 gratis</p>" * 30 + "</body>").encode())
    _serve(monkeypatch, _Response(body, {"Content-Encoding": "gzip"}))
    store, text = webpage.fetch("https://www.dirk.nl/aanbiedingen")
    assert store == "dirk" and text.startswith("Halfvolle melk 1+1 gratis")


def test_fetch_explains_blocked_and_javascript_pages(monkeypatch):
    import urllib.error
    _serve(monkeypatch, urllib.error.HTTPError("https://www.ah.nl/bonus", 403, "Forbidden", {}, None))
    with pytest.raises(webpage.PageError, match="blocks automatic reading"):
        webpage.fetch("https://www.ah.nl/bonus")
    _serve(monkeypatch, ConnectionResetError())
    with pytest.raises(webpage.PageError, match="blocks automatic reading"):
        webpage.fetch("https://www.jumbo.com/aanbiedingen/nu")
    _serve(monkeypatch, _Response(b"<body><div id=root></div>Loading</body>"))
    with pytest.raises(webpage.PageError, match="JavaScript"):
        webpage.fetch("https://www.plus.nl/aanbiedingen")


def test_redirect_to_another_site_is_refused():
    import urllib.request
    handler = webpage._SameSiteRedirects()
    req = urllib.request.Request("https://www.dirk.nl/aanbiedingen")
    with pytest.raises(webpage.PageError, match="another website"):
        handler.redirect_request(req, None, 302, "Found", {}, "https://example.com/")


FOLDER = "https://www.allefolders.nl/albert-heijn-aanbiedingen/012d0fb4-c013-4d08-b558-be65ef165e4a/0"


def _pages(n):
    return "".join(f'<img src="https://cdn.jafolders.com/pages/{i:08d}-0000-0000-0000-000000000000/large.webp">' * 2
                   for i in range(n))


def test_folder_pages_downloads_each_page_once_in_order(monkeypatch):
    fetched = []

    def open_(req, timeout):
        fetched.append(req.full_url)
        if req.full_url.endswith("/folder-aanbiedingen"):
            return _Response(f'<a href="{FOLDER[len("https://www.allefolders.nl"):]}">week 44</a>'.encode())
        if "allefolders" in req.full_url:
            return _Response(_pages(3).encode())
        return _Response(b"webp-bytes")
    monkeypatch.setattr(webpage._opener, "open", open_)

    page = webpage.read("https://www.allefolders.nl/albert-heijn/folder-aanbiedingen")
    assert page.store == "ah" and page.text == ""
    assert [n for n, _ in page.files] == ["ah-folder-p01.webp", "ah-folder-p02.webp", "ah-folder-p03.webp"]
    assert fetched[1] == FOLDER and fetched[2].startswith("https://cdn.jafolders.com/pages/00000000-")


def test_folder_pages_are_capped(monkeypatch):
    monkeypatch.setattr(webpage._opener, "open",
                        lambda req, timeout: _Response(_pages(60).encode() if "allefolders" in req.full_url else b"x"))
    assert len(webpage.read(FOLDER).files) == webpage.MAX_FOLDER_PAGES


@pytest.mark.parametrize("url,match", [
    ("https://www.allefolders.nl/kruidvat-aanbiedingen/012d0fb4-c013-4d08-b558-be65ef165e4a/0", "supported supermarket"),
    ("https://www.allefolders.nl/", "supported supermarket"),
    ("https://www.folderz.nl/bekijk/aanbiedingen/albert-heijn-folder-3865403", "allefolders"),
    ("https://cdn.jafolders.com/pages/x/large.webp", "supermarket's own"),
])
def test_read_explains_unsupported_addresses(url, match):
    with pytest.raises(webpage.PageError, match=match):
        webpage.read(url)
