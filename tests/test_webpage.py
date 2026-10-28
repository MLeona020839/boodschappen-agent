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
    handler = webpage._SameStoreRedirects()
    req = urllib.request.Request("https://www.dirk.nl/aanbiedingen")
    with pytest.raises(webpage.PageError, match="another website"):
        handler.redirect_request(req, None, 302, "Found", {}, "https://example.com/")
