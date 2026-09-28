"""Offline test for adapters/avature.py against synthetic Avature pages.

Three portal shapes, all invented employers on .example hosts:
  list_page0/2.html, job_detail.html      slugged /JobDetail/<slug>/<id> links, jobOffset pager,
                                          exact "of 3 results", single "Locations:" value plus data sets
  list_filtered_page0.html,               /FolderDetail/ links, folderOffset pager, lazy "999+" count,
  job_detail_posting_location.html        no list location, unlabeled posting-location fields, datePosted
  list_nested_location.html               slug-less /JobDetail/<id>, nested city/state/country spans
"""
import importlib.util
import re
import unittest

from tests.stubs import ROOT, StubH, fixture, gates
import sweep

_spec = importlib.util.spec_from_file_location("adapter_avature", ROOT / "adapters" / "avature.py")
av = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(av)

TT = {"company": "Tailspin Toys", "ats": "avature", "list_url": "https://careers.tailspin.example/en_US/careers/SearchJobs"}
FF = {"company": "Fabrikam Freight", "ats": "avature", "list_url": "https://jobs.fabrikam.example/en_US/jobs/Jobs",
      "params": {"31207": "2204518"}, "list_location": "United States"}


def html(name):
    return fixture("avature", name, as_json=False)


def slug_pages(url, body=None):
    m = re.search(r"jobOffset=(\d+)", url)
    off = int(m.group(1)) if m else 0
    return {0: html("list_page0.html"), 2: html("list_page2.html")}[off]


class AvatureSluggedPortalTest(unittest.TestCase):
    def setUp(self):
        self.h = StubH([("careers.tailspin.example/en_US/careers/SearchJobs", slug_pages),
                        ("/JobDetail/", html("job_detail.html"))])
        self.rows = av.list_jobs(TT, self.h)

    def test_rows_keys_pagination(self):
        self.assertEqual(len(self.rows), 3)
        self.assertEqual(len(self.h.calls), 2)                   # stops on exact "of 3 results"
        self.assertIn("jobOffset=2", self.h.calls[1][0])         # offset param read off the pager
        for r in self.rows:
            self.assertRegex(r["key"], r"^avature:Tailspin Toys:\d+$")
            self.assertTrue(r["title"])
            self.assertIsNone(r["posted"])                       # this portal shape publishes no date

    def test_list_fields(self):
        r = next(r for r in self.rows if r["id"] == "410517")
        self.assertEqual(r["title"], "Technical Program Manager - ERP Tooling, Finance Operations")
        self.assertEqual(r["location"], "Austin, United States of America")   # primary location only
        self.assertTrue(sweep.us_location_ok(r["location"], gates())[0])
        self.assertEqual(r["extra"]["workerType"], "Regular Employee")
        # The slug is stale (the req was retitled after posting): the id, not the slug, is the key.
        self.assertEqual(r["url"], "https://careers.tailspin.example/en_US/careers/JobDetail/"
                                   "Technical-Program-Manager-Finance-Operations/410517")

    def test_detail(self):
        r = next(r for r in self.rows if r["id"] == "410517")
        av.detail(TT, r, self.h)
        self.assertGreater(len(r["description"]), 400)
        self.assertIn("Finance Operations group", r["description"])
        self.assertNotIn("External careers site", r["description"])   # labelled hidden field, not JD text
        self.assertEqual(r["location"], "Austin, Texas, United States of America | Denver, Colorado, United States of America"
                                        " | Toronto, Ontario, Canada")
        self.assertEqual(r["extra"]["Work Model"], "Hybrid")
        self.assertIsNone(r["posted"])

    def test_smoke_one_page(self):
        h = StubH([("careers.tailspin.example", slug_pages)])
        self.assertEqual(len(av.list_jobs(TT, h, smoke=True)), 2)
        self.assertEqual(len(h.calls), 1)


class AvatureFilteredLazyCountTest(unittest.TestCase):
    def test_lazy_count_stops_on_empty_page(self):
        def pages(url, body=None):
            self.assertIn("31207=2204518", url)                  # country filter kept on every page
            return html("list_filtered_page0.html") if "folderOffset" not in url else "<html><body></body></html>"
        h = StubH([("jobs.fabrikam.example/en_US/jobs/Jobs", pages)])
        rows = av.list_jobs(FF, h)
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(h.calls), 2)                        # "999+" is not a stop; the empty page is
        self.assertIn("folderOffset=2", h.calls[1][0])
        for r in rows:
            self.assertRegex(r["key"], r"^avature:Fabrikam Freight:\d+$")
            self.assertEqual(r["location"], "United States")     # list_location stamped
            self.assertTrue(sweep.us_location_ok(r["location"], gates())[0])
        self.assertEqual(rows[0]["title"], "Senior Reliability Engineer")   # "Hot Job" tag is outside the link

    def test_detail_posted_and_locations(self):
        h = StubH([("/FolderDetail/", html("job_detail_posting_location.html"))])
        row = h.norm("Fabrikam Freight", "avature", "520301", "Commercial Project Manager",
                     "United States", "https://jobs.fabrikam.example/en_US/jobs/FolderDetail/x/520301")
        av.detail(FF, row, h)
        self.assertEqual(row["posted"], "2026-09-09")
        self.assertRegex(row["posted"], r"^\d{4}-\d{2}-\d{2}$")
        # posting-location fields arrive country, state, city and are reversed.
        self.assertEqual(row["location"].split(" | ")[0], "Denver, Colorado, United States of America")
        # Additional locations: capitalised country title-cased, "of" kept lower.
        self.assertIn("Columbus, Ohio, United States of America", row["location"])
        self.assertIn("Phoenix, Arizona, United States of America", row["location"])
        self.assertGreater(len(row["description"]), 400)
        self.assertNotIn("Job ID", row["description"][:50])
        self.assertEqual(row["extra"]["Business Unit"], "Terminal Projects")


NL = {"company": "Contoso Health", "ats": "avature",
      "list_url": "https://careers.contoso.example/en_US/searchjobs/SearchJobs"}


class AvatureSlugLessLinksTest(unittest.TestCase):
    """/JobDetail/<id> with no slug segment, and the list location split into nested
    city/state/country spans. Both must parse rather than yield zero rows."""

    def test_list_reads_sluggless_ids_and_full_location(self):
        h = StubH([("careers.contoso.example/en_US/searchjobs/SearchJobs", html("list_nested_location.html"))])
        rows = av.list_jobs(NL, h)
        self.assertEqual([r["id"] for r in rows], ["614434", "613149"])
        self.assertEqual(len(h.calls), 1)                        # "of 2 results", exact
        r = rows[0]
        self.assertEqual(r["url"], "https://careers.contoso.example/en_US/searchjobs/JobDetail/614434")
        self.assertEqual(r["location"], "Yokohama, Kanagawa, Japan")  # not just "Yokohama"
        self.assertTrue(r["title"])
        self.assertFalse(sweep.us_location_ok(r["location"], gates())[0])

if __name__ == "__main__":
    unittest.main()
