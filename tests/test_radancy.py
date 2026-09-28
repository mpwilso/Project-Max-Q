"""Offline tests for the built-in radancy adapter (sweep.radancy).

Fixtures under tests/fixtures/radancy/ are synthetic payloads (invented employers on .example hosts)
in the shape a TalentBrew results endpoint returns, 2-3 tiles each:
  results_multiclass_location.json   location span carries three classes, category span alongside
  results_placeholder_location.json  every tile says "Multiple Locations"
  results_tile_date.json             tile carries job-date-posted
  job_detail*.html: a job page's JobPosting block only.
Each test covers a defect seen on a live board."""
import types
import unittest

from tests.stubs import fixture, gates
import sweep

G = gates()
MC = {"company": "Globex Systems", "ats": "radancy", "origin": "https://careers.globex.example", "path": "/en"}
PH = {"company": "Initech", "ats": "radancy", "origin": "https://jobs.initech.example"}
TD = {"company": "Acme Analytics", "ats": "radancy", "origin": "https://jobs.acme.example", "path": "/en"}


class _Stub:
    """Replaces sweep.get / sweep.get_text for one test; records every URL asked for."""

    def __init__(self, get=None, get_text=None):
        self.get, self.get_text, self.calls = get, get_text, []

    def __enter__(self):
        self._orig = (sweep.get, sweep.get_text)
        if self.get:
            sweep.get = lambda url, headers=None, **kw: (self.calls.append(url), self.get(url))[1]
        if self.get_text:
            sweep.get_text = lambda url, headers=None, **kw: (self.calls.append(url), self.get_text(url))[1]
        return self

    def __exit__(self, *a):
        sweep.get, sweep.get_text = self._orig


class MultiClassLocationSpan(unittest.TestCase):
    def test_multiclass_location_span_is_read(self):
        # The location span carries several classes (class="results-facet job-location test3"). An
        # exact class match finds nothing, every row reads blank, and every row fails the location gate.
        with _Stub(get=lambda url: fixture("radancy", "results_multiclass_location.json")):
            rows = sweep.radancy(MC)
        self.assertEqual(len(rows), 3)
        self.assertEqual([r["location"] for r in rows if not r["location"]], [])
        by_id = {r["id"]: r for r in rows}
        self.assertEqual(by_id["100215530112"]["location"], "Raleigh, NC")
        self.assertEqual(by_id["100215530112"]["extra"]["category"], "Program, Management")
        self.assertEqual(by_id["100215530112"]["url"],
                         "https://careers.globex.example/en/job/raleigh/senior-specialist-program-management-1/5120/100215530112")
        # A blank location is "no location stated"; a read one is judged on its market.
        self.assertEqual(sweep.gate(by_id["99512073216"], G)[0], "LOCATION-POLICY")    # Program Manager, Columbus, OH
        v, why = sweep.gate(by_id["100863104528"], G)                                  # Program Manager, Brisbane
        self.assertEqual(v, "FAIL")
        self.assertIn("Brisbane", " ".join(why))
        self.assertNotIn("no location stated", " ".join(why))

    def test_detail_block_fills_date_and_text(self):
        with _Stub(get=lambda url: fixture("radancy", "results_multiclass_location.json")):
            r = next(x for x in sweep.radancy(MC) if x["id"] == "100215530112")
        with _Stub(get_text=lambda url: fixture("radancy", "job_detail.html", as_json=False)):
            sweep.radancy_detail(MC, r)
        self.assertEqual(r["posted"], "2026-09-03")
        self.assertEqual(r["location"], "Raleigh, NC, US")
        self.assertGreater(len(r["description"]), 400)


class PlaceholderLocation(unittest.TestCase):
    def test_multiple_locations_is_a_placeholder(self):
        for s in ("Multiple Locations", "2 Locations", " 3 locations ", "Various Locations"):
            self.assertTrue(sweep.PLACEHOLDER_LOCATION.fullmatch(s), s)
        for s in ("Phoenix, Arizona", "Multiple Locations, CA", "", "Remote"):
            self.assertFalse(sweep.PLACEHOLDER_LOCATION.fullmatch(s), s)

    def test_placeholder_row_earns_the_detail_fetch_and_resolves(self):
        # Some boards' tiles say "Multiple Locations". The placeholder must earn the detail fetch, or
        # in-lane rows fail the location gate without radancy_detail ever running.
        with _Stub(get=lambda url: fixture("radancy", "results_placeholder_location.json")):
            r = next(x for x in sweep.radancy(PH) if x["id"] == "100517209344")
        self.assertEqual(r["location"], "Multiple Locations")
        r["_target"] = PH
        verdict = sweep.gate(r, G)
        self.assertEqual(verdict[0], "FAIL")
        self.assertTrue(sweep.placeholder_location_fail(r, verdict))
        with _Stub(get_text=lambda url: fixture("radancy", "job_detail_multi_location.html", as_json=False)) as st:
            verdicts, stats = sweep.gate_all({r["key"]: r}, {}, G)
        self.assertEqual(stats["fetched"], 1)
        self.assertEqual(st.calls, [r["url"]])
        self.assertEqual(r["location"],
                         "Denver, Colorado, United States | Austin, Texas, United States")
        self.assertEqual(r["posted"], "2026-09-09")
        self.assertNotEqual(verdicts[r["key"]][0], "FAIL")
        self.assertNotIn("no location stated", " ".join(verdicts[r["key"]][1]))


class TileDate(unittest.TestCase):
    def test_posted_date_comes_from_the_tile(self):
        with _Stub(get=lambda url: fixture("radancy", "results_tile_date.json")):
            rows = sweep.radancy(TD)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r["posted"] and r["posted"].startswith("2026-") for r in rows), rows)
        self.assertEqual(rows[0]["posted"], "2026-08-12")        # MM/DD/YYYY on the tile
        self.assertEqual(rows[0]["location"], "Austin, TX")


class WholeBoardWalk(unittest.TestCase):
    def test_enumerates_at_500_and_stops_on_total(self):
        self.assertIn("radancy", sweep.ENUMERABLE_ATS)
        with _Stub(get=lambda url: fixture("radancy", "results_multiclass_location.json")) as st:
            rows = sweep.radancy(MC)
        self.assertEqual(len(rows), 3)
        self.assertEqual(len(st.calls), 1)                       # total 3, page 1 had 3: no second page
        self.assertIn("RecordsPerPage=500", st.calls[0])
        self.assertIn("Keywords=&", st.calls[0])                 # the whole board, not a lane query
        self.assertTrue(st.calls[0].startswith("https://careers.globex.example/en/search-jobs/results?"))

    def test_short_read_raises_instead_of_closing_reqs(self):
        # A capped walk on an enumerable board would record every unread req as closed.
        d = fixture("radancy", "results_multiclass_location.json")
        d["results"] = d["results"].replace('data-total-results="3"', 'data-total-results="3000"')
        with _Stub(get=lambda url: d):
            with self.assertRaises(RuntimeError):
                sweep.radancy(MC)

    def setUp(self):
        self._wait = sweep.RADANCY_RETRY_WAIT
        sweep.RADANCY_RETRY_WAIT = (0, 0, 0)

    def tearDown(self):
        sweep.RADANCY_RETRY_WAIT = self._wait

    @staticmethod
    def _failing(n_fail, status=400):
        """A get() that answers `status` n_fail times, then the tile-date fixture."""
        import requests
        left = [n_fail]

        def get(url):
            if left[0] > 0:
                left[0] -= 1
                raise requests.exceptions.HTTPError(response=types.SimpleNamespace(status_code=status))
            return fixture("radancy", "results_tile_date.json")
        return get

    def test_cached_error_page_is_retried_on_a_fresh_url(self):
        # A results URL can answer 400 (a 302 to /error/jsonrequesterror) on every read of that exact
        # URL, while the same slice under any other URL renders. Fresh URL each retry.
        with _Stub(get=self._failing(1)) as st:
            rows = sweep.radancy(TD)
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(st.calls), 2)
        self.assertNotIn("_cb=", st.calls[0])
        self.assertIn("&_cb=", st.calls[1])

    def test_a_poisoned_retry_url_gets_another_fresh_one(self):
        # A fresh URL is a fresh roll: a plain URL and a '&_cb=abc' one can both answer 400 while
        # their neighbours render. Two poisoned reads, then a clean one.
        with _Stub(get=self._failing(2)) as st:
            rows = sweep.radancy(TD)
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(st.calls), 3)
        self.assertNotEqual(st.calls[1], st.calls[2])            # never the same URL twice

    def test_persistent_400_raises_after_the_last_fresh_url(self):
        import requests
        with _Stub(get=self._failing(10)) as st:
            with self.assertRaises(requests.exceptions.HTTPError):
                sweep.radancy(TD)
        self.assertEqual(len(st.calls), 1 + len(sweep.RADANCY_RETRY_WAIT))

    def test_other_http_errors_still_raise(self):
        import requests
        with _Stub(get=self._failing(10, status=403)) as st:
            with self.assertRaises(requests.exceptions.HTTPError):
                sweep.radancy(TD)
        self.assertEqual(len(st.calls), 1)                       # no blind retry into a bot wall


if __name__ == "__main__":
    unittest.main()
