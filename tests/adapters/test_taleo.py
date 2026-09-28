"""Offline tests for adapters/taleo.py (a classic Taleo section, synthetic data).

ajax_p1.xml / ajax_p2.xml: synthetic jobsearch.ajax responses at 100 per page, 3 and 2 records,
nbElements 5. job_detail.html: a synthetic jobdetail.ftl initialHistory input only.
"""
import importlib.util
import unittest
from types import SimpleNamespace

from tests.stubs import ROOT, StubH, fixture, gates
import sweep

_spec = importlib.util.spec_from_file_location("adapter_taleo", ROOT / "adapters" / "taleo.py")
taleo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(taleo)

T = {"company": "Northwind Timber", "ats": "taleo", "base": "https://northwind.taleo.net/careersection/10000"}


class FormStub(StubH):
    """taleo posts a form through h.request; answer it from the same route table."""
    def request(self, method, url, headers=None, **kw):
        return SimpleNamespace(text=self._answer(url, kw.get("data")))


def pages(url, body):
    p = int(body["rlPager.currentPage"])
    return fixture("taleo", "ajax_p1.xml" if p == 1 else "ajax_p2.xml", as_json=False)


class TaleoTest(unittest.TestCase):
    def setUp(self):
        self.h = FormStub([("/careersection/10000/jobsearch.ajax", pages),
                           ("jobdetail.ftl?job=01031742", fixture("taleo", "job_detail.html", as_json=False))])
        self.rows = taleo.list_jobs(T, self.h)

    def test_walk_uses_page_size_100_and_stops_on_total(self):
        self.assertEqual(len(self.rows), 5)
        self.assertEqual(len(self.h.calls), 2)
        form = self.h.calls[0][1]
        self.assertEqual(form["dropListSize"], "100")        # 25 per page loses rows
        self.assertEqual(form["listRequisition.size"], "100")
        self.assertEqual(self.h.calls[1][1]["rlPager.currentPage"], "2")

    def test_row_fields(self):
        r = self.rows[0]
        self.assertEqual(r["key"], "taleo:Northwind Timber:472915")
        self.assertEqual(r["title"], "Data Platform Engineer")
        self.assertEqual(r["location"], "USA-CO-Denver")
        self.assertEqual(r["posted"], "2026-09-16")
        self.assertEqual(r["extra"]["closing"], "2026-10-17")
        self.assertEqual(r["url"], "https://northwind.taleo.net/careersection/10000/jobdetail.ftl?job=01031742&lang=en")
        self.assertTrue(sweep.us_location_ok(r["location"], gates())[0])
        multi = self.rows[1]                                 # "Position available in other locations"
        self.assertIn("USA-OH-Columbus", multi["location"].split(" | "))
        self.assertEqual(multi["title"], "Time & Attendance Systems Analyst")   # %26 decoded
        self.assertFalse(sweep.us_location_ok(self.rows[3]["location"], gates())[0])  # CAN-ON-Toronto

    def test_past_end_repeat_stops(self):
        h = FormStub([("jobsearch.ajax", lambda url, body: fixture("taleo", "ajax_p1.xml", as_json=False))])
        with self.assertRaises(RuntimeError):                # 3 unique of nbElements 5: layout guard trips
            taleo.list_jobs(T, h)
        self.assertEqual(len(h.calls), 2)                    # page 2 brought nothing new

    def test_detail_decodes_description(self):
        r = taleo.detail(T, self.rows[0], self.h)
        self.assertIn("Northwind Timber manages working forests", r["description"])
        self.assertIn("4+ years building data pipelines", r["description"])   # qualifications block too
        self.assertNotIn("%3C", r["description"])
        self.assertNotIn("<p>", r["description"])
        self.assertEqual(r["description"].count(r["description"][:80]), 1)   # repeated blocks deduped

    def test_empty_raises(self):
        h = FormStub([("jobsearch.ajax", "<html><td id='response'>ftlx1!|!</td></html>")])
        with self.assertRaises(RuntimeError):
            taleo.list_jobs(T, h)


if __name__ == "__main__":
    unittest.main()
