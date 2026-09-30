"""Offline test helpers. No test in tests/ may touch the network or write under data/ or reports/."""
import datetime as dt, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import sweep  # noqa: E402


def local_date(ts):
    """The local calendar date of a board timestamp (ISO 8601 with Z or an offset, or epoch seconds).
    The sweep dates a moment in the time zone it runs in, so a fixture's expected date depends on
    where the tests run; computing it here keeps every adapter test true in any time zone."""
    t = (dt.datetime.fromtimestamp(ts, dt.timezone.utc) if isinstance(ts, (int, float))
         else dt.datetime.fromisoformat(ts))
    return t.astimezone().date().isoformat()


def fixture(*parts, as_json=True):
    p = FIXTURES.joinpath(*parts)
    txt = p.read_text(encoding="utf-8")
    return json.loads(txt) if as_json else txt


class StubH:
    """A drop-in for sweep.plugin_helpers() whose HTTP calls are answered from a route table.

    routes: list of (substring_of_url, response) checked in order. response is a dict/list (JSON),
    a str (markup), or a callable(url, body) returning either. Every call is recorded in .calls,
    and an unmatched URL raises, so a test cannot pass by quietly reaching the network.
    """

    def __init__(self, routes):
        self.routes = list(routes)
        self.calls = []
        real = sweep.plugin_helpers()
        for k in ("norm", "strip_html", "iso_date", "days_since", "money_range",
                  "comp_from_description", "UA", "DELAY", "TODAY"):
            setattr(self, k, getattr(real, k))
        self.quote = lambda s: __import__("urllib.parse").parse.quote(s)
        self.requests = None

    def _answer(self, url, body=None):
        self.calls.append((url, body))
        for sub, resp in self.routes:
            if sub in url:
                return resp(url, body) if callable(resp) else resp
        raise AssertionError(f"StubH: no route for {url}")

    def get(self, url, headers=None, **kw):
        if kw.get("params"):
            from urllib.parse import urlencode
            url = url + ("&" if "?" in url else "?") + urlencode(kw["params"])
        return self._answer(url)

    def post_json(self, url, body, headers=None):
        return self._answer(url, body)

    def get_text(self, url, headers=None, **kw):
        return self._answer(url)

    def request(self, method, url, headers=None, **kw):
        raise AssertionError("StubH.request: use get/post_json/get_text in adapters, or stub this explicitly")


def gates():
    return json.loads((ROOT / "gates.json").read_text(encoding="utf-8"))
