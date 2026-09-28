"""Offline test for adapters/successfactors.py against synthetic RMK pages (fictional tenants)."""
import importlib.util
import re
import unittest

from tests.stubs import ROOT, StubH, fixture, gates
import sweep

_spec = importlib.util.spec_from_file_location("adapter_successfactors", ROOT / "adapters" / "successfactors.py")
sf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sf)

GX = {"company": "Globex", "ats": "successfactors", "origin": "https://jobs.globex.example"}
IN = {"company": "Initech", "ats": "successfactors", "origin": "https://careers.initech.example"}


def html(name):
    return fixture("successfactors", name, as_json=False)


def tile_pages(url, body=None):
    start = int(re.search(r"startrow=(\d+)", url).group(1))
    return {0: html("tiles_page0.html"), 2: html("tiles_page2.html")}[start]


class SuccessFactorsListTest(unittest.TestCase):
    def test_tiles_paginate_on_returned_tiles(self):
        h = StubH([("jobs.globex.example/search/", tile_pages)])
        rows = sf.list_jobs(GX, h)
        self.assertEqual(len(rows), 3)
        self.assertEqual(len(h.calls), 2)                 # stops on "of 3 Jobs", no third call
        self.assertIn("startrow=2", h.calls[1][0])        # advanced by 2 tiles, not a fixed 25
        for r in rows:
            self.assertRegex(r["key"], r"^successfactors:Globex:\d+$")
            self.assertTrue(r["title"])
            self.assertTrue(r["url"].startswith("https://jobs.globex.example/"))
            self.assertIsNone(r["posted"])                # this tenant's tiles carry no date column
        r = next(r for r in rows if r["id"] == "1418830200")
        self.assertEqual(r["title"], "Treasury Analyst, Cash Management")
        self.assertEqual(r["location"], "Toronto, ON, CA")
        self.assertEqual(r["extra"]["department"], "Production & Development")   # desktop copy only
        self.assertIn("/GlobexCanada/job/", r["url"])
        self.assertFalse(sweep.us_location_ok(r["location"], gates())[0])

    def test_smoke_one_page(self):
        h = StubH([("jobs.globex.example/search/", tile_pages)])
        self.assertEqual(len(sf.list_jobs(GX, h, smoke=True)), 2)
        self.assertEqual(len(h.calls), 1)

    def test_tile_date_is_refresh_not_posted(self):
        h = StubH([("careers.initech.example/search/", html("tiles_dated_page0.html"))])
        rows = sf.list_jobs(IN, h)
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(h.calls), 1)
        r = next(r for r in rows if r["id"] == "1413907100")
        self.assertEqual(r["key"], "successfactors:Initech:1413907100")
        self.assertEqual(r["title"], "Account Manager, Partner Sales (Chicago or Denver)")
        self.assertEqual(r["location"], "Chicago, IL, US, 60601")
        self.assertTrue(sweep.us_location_ok(r["location"], gates())[0])
        self.assertIsNone(r["posted"])
        self.assertEqual(r["extra"]["date_kind"], "refreshed")
        self.assertRegex(r["extra"]["refreshed"], r"^\d{4}-\d{2}-\d{2}$")

    def test_no_tiles_raises(self):
        h = StubH([("search/", "<html><body>nothing here</body></html>")])
        with self.assertRaises(RuntimeError):
            sf.list_jobs(IN, h)


class SuccessFactorsDetailTest(unittest.TestCase):
    def test_detail_fills_text_and_all_locations(self):
        h = StubH([("careers.initech.example/search/", html("tiles_dated_page0.html")),
                   ("/job/", html("tile_job_detail.html"))])
        row = next(r for r in sf.list_jobs(IN, h) if r["id"] == "1413907100")
        sf.detail(IN, row, h)
        self.assertTrue(row["description"].startswith("About Initech"))
        self.assertIn("Comfort with spreadsheets and a CRM.", row["description"])   # nested block read through
        self.assertTrue(row["description"].endswith("equal opportunity employer."))
        self.assertNotIn("<", row["description"])
        self.assertEqual(row["location"], "Denver, CO, US | Chicago, IL, US")
        self.assertIsNone(row["posted"])
        self.assertEqual(row["extra"]["refreshed"], "2026-09-16")

    def test_java_date(self):
        self.assertEqual(sf.java_date("Wed Sep 16 07:00:00 UTC 2026"), "2026-09-16")
        self.assertEqual(sf.java_date("Fri Sep 04 07:00:00 UTC 2026"), "2026-09-04")
        self.assertIsNone(sweep.iso_date("Wed Sep 16 07:00:00 UTC 2026"))   # why java_date exists
        self.assertIsNone(sf.java_date("garbage"))


NW = {"company": "Northwind", "ats": "successfactors", "origin": "https://jobs.northwind.example"}
TS = {"company": "Tailspin Toys", "ats": "successfactors", "origin": "https://jobs.tailspin.example",
      "path": "/corporate"}
LW = {"company": "Litware", "ats": "successfactors", "origin": "https://jobs.litware.example"}
FB = {"company": "Fabrikam Freight", "ats": "successfactors", "origin": "https://careers.fabrikam.example"}


def table_pages(url, body=None):
    start = int(re.search(r"startrow=(\d+)", url).group(1))
    return {0: html("table_page0.html"), 2: html("table_page2.html")}[start]


class SuccessFactorsTableLayoutTest(unittest.TestCase):
    """Synthetic pages in the <tr class="data-row"> table layout."""

    def test_table_paginates_and_reads_columns(self):
        h = StubH([("jobs.northwind.example/search/", table_pages)])
        rows = sf.list_jobs(NW, h)
        self.assertEqual([r["id"] for r in rows], ["1431572833", "1431509233", "1439811633"])
        self.assertEqual(len(h.calls), 2)                 # stops on "of 3"
        self.assertIn("startrow=2", h.calls[1][0])
        r = rows[0]
        self.assertEqual(r["key"], "successfactors:Northwind:1431572833")
        self.assertEqual(r["title"], "Program Manager")
        self.assertEqual(r["location"], "Columbus, OH, US, 43215")
        self.assertEqual(r["url"], "https://jobs.northwind.example/job/Columbus-Program-Manager-OH-43215/1431572833/")
        self.assertEqual(r["extra"]["department"], "Product Planning")
        self.assertEqual(r["extra"]["facility"], "Northwind Truck Works")
        self.assertIsNone(r["posted"])
        self.assertEqual(r["extra"]["refreshed"], "2026-09-16")
        self.assertEqual(r["extra"]["date_kind"], "refreshed")
        self.assertTrue(sweep.us_location_ok(r["location"], gates())[0])
        self.assertEqual(rows[2]["extra"]["department"], "Manufacturing & Distribution")

    def test_more_marker_stripped_from_location(self):
        h = StubH([("careers.fabrikam.example/search/", html("table_more_marker.html"))])
        rows = sf.list_jobs(FB, h)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["location"], "Austin, TX, US, 78701")
        self.assertNotIn("more", rows[0]["location"])

    def test_brand_path_prefixes_search(self):
        h = StubH([("jobs.tailspin.example/corporate/search/", html("table_brand_path.html"))])
        rows = sf.list_jobs(TS, h)
        self.assertEqual(len(rows), 2)
        self.assertTrue(h.calls[0][0].startswith("https://jobs.tailspin.example/corporate/search/?q="))
        r = rows[0]
        self.assertEqual(r["location"], "Hybrid, Remote, US")
        self.assertTrue(r["url"].startswith("https://jobs.tailspin.example/corporate/job/"))
        self.assertEqual(r["extra"]["facility"], "Tailspin Corporate")
        self.assertEqual(r["extra"]["shifttype"], "Year Round")

    def test_detail_keeps_tile_location_when_page_has_one_address(self):
        h = StubH([("jobs.litware.example/search/", html("table_remote.html")),
                   ("/job/", html("table_job_detail.html"))])
        row = sf.list_jobs(LW, h)[0]
        self.assertEqual(row["location"], "Remote, US")
        sf.detail(LW, row, h)
        self.assertEqual(row["location"], "Remote, US")   # page says "Remote, OR, US"
        self.assertTrue(row["description"].startswith("About Litware:"))
        self.assertIn("Partner with sales on renewals.", row["description"])
        self.assertEqual(row["extra"]["refreshed"], "2026-09-16")


CO = {"company": "Contoso", "ats": "successfactors", "origin": "https://contosocareers.example", "api": "unify",
      "facets": {"jobType": ["Corporate Support"]}}


def unify_pages(url, body):
    return {0: fixture("successfactors", "unify_p0.json"),
            1: fixture("successfactors", "unify_p1.json")}.get(body["pageNumber"],
                                                                 fixture("successfactors", "unify_past_end.json"))


class SuccessFactorsUnifyTest(unittest.TestCase):
    """Synthetic Unify API pages and job page."""

    def test_unify_walk_body_and_rows(self):
        h = StubH([("contosocareers.example/services/recruiting/v1/jobs", unify_pages)])
        rows = sf.list_jobs(CO, h)
        self.assertEqual([r["id"] for r in rows], ["671482", "683117", "683109", "682240", "682356"])
        self.assertEqual(len(h.calls), 2)                 # stops on totalJobs=5
        body = h.calls[0][1]
        self.assertEqual(body["sortBy"], "date")          # the only stable order
        self.assertEqual(body["facetFilters"], {"jobType": ["Corporate Support"]})
        self.assertEqual(h.calls[1][1]["pageNumber"], 1)
        r = rows[0]
        self.assertEqual(r["key"], "successfactors:Contoso:671482")
        self.assertEqual(r["title"], "Regional Recruiting Coordinator")
        self.assertEqual(r["location"], "DENVER, CO, USA 80202")
        self.assertTrue(sweep.us_location_ok(r["location"], gates())[0])
        self.assertEqual(r["url"], "https://contosocareers.example/UnitedStates/job/Regional-Recruiting-Coordinator/671482-en_US/")
        self.assertIsNone(r["posted"])
        self.assertEqual(r["extra"]["refreshed"], "2026-09-16")
        hr = rows[3]                                      # slug arrives already percent-encoded
        self.assertNotIn("%25", hr["url"])
        self.assertTrue(hr["url"].endswith("/682240-en_US/"))
        self.assertEqual(rows[1]["title"], "Customer Growth & Loyalty Marketing Specialist")

    def test_unify_empty_first_page_raises(self):
        h = StubH([("services/recruiting/v1/jobs", {"totalJobs": 0})])
        with self.assertRaises(RuntimeError):
            sf.list_jobs(CO, h)

    def test_unify_job_page_skips_empty_description_span(self):
        page = html("unify_job_detail.html")
        self.assertGreaterEqual(page.count('itemprop="description"'), 2)
        d = sf.parse_job(page)
        self.assertTrue(d["description"].startswith("Job Description"))
        self.assertIn("As a Site Manager", d["description"])
        self.assertEqual(d["locations"], [])

    def test_short_date(self):
        self.assertEqual(sf._short_date("9/9/26"), "2026-09-09")
        self.assertEqual(sf._short_date("12/31/2026"), "2026-12-31")
        self.assertIsNone(sf._short_date("Sep 9"))


if __name__ == "__main__":
    unittest.main()
