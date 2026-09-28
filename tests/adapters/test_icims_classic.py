"""Offline tests for adapters/icims_classic.py.

Fixtures are synthetic portal markup for fictional tenants, in the portal's own card and
paginator shape:
- search_0.html: 3 cards (onsite / "Remote work allowed 100%" / occasional telework),
  "Page 1 of 2".
- search_1.html: 1 new card plus a repeat of a page-0 card.
- job_remote.html: the JSON-LD block of job 131907 (Product Owner), with a
  "Target Salary Range" paragraph and an all-UNAVAILABLE jobLocation.
- no_posted_column_search_0.html: 2 cards from a portal with no Posted Date column, one location
  written "US-TX-Austin, TX".
"""
import importlib.util, json, unittest

from tests.stubs import ROOT, StubH, fixture

NAME = "icims_classic"


def load():
    spec = importlib.util.spec_from_file_location(f"test_adapter_{NAME}", ROOT / "adapters" / f"{NAME}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


T = {"company": "Globex Systems", "ats": NAME, "host": "careers-globexsystems.icims.com"}


def html(name):
    return fixture(NAME, name, as_json=False)


class IcimsClassicTest(unittest.TestCase):
    def setUp(self):
        self.m = load()
        self.h = StubH([("pr=0&", html("search_0.html")), ("pr=1&", html("search_1.html")),
                        ("/jobs/131907/", html("job_remote.html"))])

    def test_contract(self):
        self.assertEqual(self.m.NAME, NAME)
        self.assertTrue(self.m.ENUMERABLE)
        self.assertEqual(self.m.REQUIRED, ["host"])
        self.assertTrue(callable(self.m.detail))

    def test_paginates_to_total_and_dedupes(self):
        rows = self.m.list_jobs(T, self.h)
        urls = [u for u, _ in self.h.calls]
        self.assertEqual(urls, ["https://careers-globexsystems.icims.com/jobs/search?ss=1&pr=0&in_iframe=1",
                                "https://careers-globexsystems.icims.com/jobs/search?ss=1&pr=1&in_iframe=1"])
        self.assertEqual(sorted(r["id"] for r in rows), ["131907", "183977", "184196", "184215"])
        keys = [r["key"] for r in rows]
        self.assertEqual(len(keys), len(set(keys)))
        for r in rows:
            self.assertRegex(r["key"], r"^icims_classic:Globex Systems:\d+$")
            self.assertRegex(r["posted"], r"^\d{4}-\d{2}-\d{2}$")
            self.assertTrue(r["url"].startswith("https://careers-globexsystems.icims.com/jobs/"))
            self.assertTrue(r["url"].endswith("/job"))
            self.assertIn("United States", r["location"])

    def test_card_fields_location_and_remote(self):
        by = {r["id"]: r for r in self.m.list_jobs(T, self.h)}
        r = by["184215"]
        self.assertEqual(r["title"], "Network Operations Analyst")
        self.assertEqual(r["location"], "Denver, CO, United States")       # US-CO-Denver
        self.assertEqual(r["posted"], "2026-09-16")
        self.assertEqual(r["extra"]["Requisition ID"], "2026-184215")
        self.assertEqual(by["131907"]["location"], "United States | Remote")   # bare "US" + 100% remote
        self.assertEqual(by["184196"]["extra"]["Telecommute Options"], "Flexible for occasional telework")
        self.assertNotIn("Remote", by["184196"]["location"])

    def test_smoke_reads_one_page(self):
        rows = self.m.list_jobs(T, self.h, smoke=True)
        self.assertEqual(len(rows), 3)
        self.assertEqual(len(self.h.calls), 1)

    def test_detail_fills_jd_and_comp(self):
        rows = {r["id"]: r for r in self.m.list_jobs(T, self.h)}
        r = self.m.detail(T, rows["131907"], self.h)
        self.assertTrue(self.h.calls[-1][0].endswith("/jobs/131907/product-owner/job?in_iframe=1"))
        self.assertIn("Target Salary Range", r["description"])
        self.assertNotIn("<p", r["description"])
        self.assertEqual(r["comp"], "$91,400 - $142,600")
        self.assertEqual(r["posted"], "2026-09-16")                 # card date kept; JSON-LD date ignored
        self.assertEqual(r["location"], "United States | Remote")   # single UNAVAILABLE-city place: list kept
        self.assertEqual(r["extra"]["occupationalCategory"], "Project Management")

    def test_detail_multi_location_from_jsonld(self):
        jp = {"@type": "JobPosting", "description": "<p>x</p>", "datePosted": "2024-09-16T21:18:45.557Z",
              "jobLocation": [{"address": {"addressCountry": "US", "addressLocality": "Raleigh", "addressRegion": "NC"}},
                              {"address": {"addressCountry": "US", "addressLocality": "UNAVAILABLE", "addressRegion": "VA"}}]}
        page = '<script type="application/ld+json">' + json.dumps(jp) + "</script>"
        row = {"id": "1", "url": "https://h/jobs/1/x/job", "location": "United States", "posted": None, "extra": {}}
        self.m.detail(T, row, StubH([("/jobs/1/", page)]))
        self.assertEqual(row["location"], "Raleigh, NC, United States | VA, United States")
        self.assertIsNone(row["posted"])

    def test_portal_without_posted_column(self):
        t = {"company": "Northwind Traders", "ats": NAME, "host": "careers-na-northwindtraders.icims.com"}
        h = StubH([("pr=0&", html("no_posted_column_search_0.html"))])
        by = {r["id"]: r for r in self.m.list_jobs(t, h)}
        self.assertEqual(len(h.calls), 1)                            # "Page 1 of 1"
        self.assertEqual(by["21408"]["location"], "Columbus, OH, United States")
        self.assertEqual(by["21407"]["location"], "Austin, TX, United States")   # "US-TX-Austin, TX"
        self.assertTrue(all(r["posted"] is None for r in by.values()))
        self.assertEqual(by["21407"]["extra"]["Employment Type"], "Part-Time")

    def test_location_text(self):
        f = self.m.location_text
        self.assertEqual(f("US"), "United States")
        self.assertEqual(f("CA-ON-Toronto"), "Toronto, ON, Canada")
        self.assertEqual(f("Remote"), "Remote")

    def test_empty_and_walled_pages_raise(self):
        with self.assertRaises(RuntimeError):
            self.m.list_jobs(T, StubH([("pr=0&", "<html><title>Job Listings</title><ul></ul></html>")]))
        with self.assertRaisesRegex(RuntimeError, "Human Verification"):
            self.m.list_jobs(T, StubH([("pr=0&", "<html><title>Human Verification</title></html>")]))


if __name__ == "__main__":
    unittest.main()
