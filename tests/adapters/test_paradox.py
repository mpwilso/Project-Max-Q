"""Offline tests for adapters/paradox.py against synthetic Paradox career-site pages.

jobs_page1/2.html: a tenant <tenant>.recruiting.com board's __PRELOAD_STATE__ for page_number=1 and 2,
3 invented jobs each, totalJob 6. Hiring-leader/recruiter custom fields are present (placeholder
names) so the test proves they are dropped.
jobs_page_past_end.html: what the site renders for a page past the end - page 1 again, params dropped.
own_domain_page1.html / own_domain_job.html: a Paradox site on its own domain, list at /page/N,
relative originalURL, and a server-rendered job page with a JobPosting block.
"""
import importlib.util, unittest

from tests.stubs import ROOT, StubH, fixture

spec = importlib.util.spec_from_file_location("adapter_paradox", ROOT / "adapters" / "paradox.py")
paradox = importlib.util.module_from_spec(spec); spec.loader.exec_module(paradox)

T = {"company": "Northwind Traders", "ats": "paradox", "base": "https://northwindcorp.recruiting.com"}


def pages(total=None):
    html = {1: fixture("paradox", "jobs_page1.html", as_json=False),
            2: fixture("paradox", "jobs_page2.html", as_json=False)}
    past_end = fixture("paradox", "jobs_page_past_end.html", as_json=False)
    def answer(url, body):
        p = int(url.rsplit("page_number=", 1)[1])
        out = html.get(p, past_end)
        return out.replace('"totalJob": 6', f'"totalJob": {total}') if total else out
    return answer


class ParadoxTest(unittest.TestCase):
    def setUp(self):
        self.h = StubH([("northwindcorp.recruiting.com/jobs?page_number=", pages())])
        self.rows = paradox.list_jobs(T, self.h)

    def test_rows_and_stop_on_total(self):
        self.assertEqual(len(self.rows), 6)
        self.assertEqual(len({r["key"] for r in self.rows}), 6)
        self.assertEqual(len(self.h.calls), 2)

    def test_key_title_location_no_date(self):
        r = self.rows[0]
        self.assertEqual(r["key"], "paradox:Northwind Traders:PDX_NWT_3C51A0D2-7E14-4B9A-9F20-5A1D6E8B4C01_40117")
        self.assertEqual(r["title"], "Analyst, Loyalty Programs")
        self.assertEqual(r["location"], "Columbus, OH, United States")
        self.assertIsNone(r["posted"])                 # the board publishes no date
        self.assertEqual(r["description"], "")
        self.assertTrue(r["url"].startswith("https://northwind.paradox.ai/co/NorthwindTradersSupportCenter/Job?job_id="))
        for r in self.rows: self.assertRegex(r["key"], r"^paradox:Northwind Traders:PDX_[A-Z]+_[0-9A-F-]{36}_\d+$")

    def test_names_not_copied(self):
        for r in self.rows:
            self.assertFalse({"cf_hiring_leader", "cf_recruiter_name", "cf_job_openings"} & set(r["extra"]))
            self.assertNotIn("Pat Example", r["extra"].values())
        self.assertIn("cf_functional_area", self.rows[0]["extra"])

    def test_past_end_rerender_of_page_one_stops(self):
        h = StubH([("page_number=", pages(total=99))])      # totalJob overstated
        rows = paradox.list_jobs(T, h)
        self.assertEqual(len(rows), 6)
        self.assertEqual(len(h.calls), 3)                   # page 3 re-renders page 1: nothing new, stop

    def test_smoke_one_page(self):
        h = StubH([("page_number=", pages())])
        self.assertEqual(len(paradox.list_jobs(T, h, smoke=True)), 3)
        self.assertEqual(len(h.calls), 1)

    def test_challenge_page_raises(self):
        h = StubH([("page_number=", "<html><script src='challenge.js'></script></html>")])
        with self.assertRaises(RuntimeError):
            paradox.list_jobs(T, h)


OWN = {"company": "Litware Stores", "ats": "paradox", "base": "https://careers.litware.example",
       "list_path": "/page/{page}"}


class ParadoxOwnDomainTest(unittest.TestCase):
    """A Paradox site on its own domain: list at /page/N, relative originalURL, JobPosting job page."""

    def setUp(self):
        self.h = StubH([("careers.litware.example/page/", fixture("paradox", "own_domain_page1.html", as_json=False)),
                        ("/job/P1-7304418-1", fixture("paradox", "own_domain_job.html", as_json=False))])
        self.rows = paradox.list_jobs(OWN, self.h)

    def test_list_path_and_career_site_url(self):
        self.assertEqual(self.h.calls[0][0], "https://careers.litware.example/page/1")
        self.assertEqual(len(self.rows), 2)
        r = self.rows[0]
        self.assertEqual(r["title"], "Analyst, Financial Planning")
        self.assertEqual(r["location"], "Denver, CO, United States")
        self.assertEqual(r["url"], "https://careers.litware.example/analyst-financial-planning/job/P1-7304418-1")
        self.assertEqual(r["extra"]["page"], r["url"])

    def test_detail_reads_jobposting_as_refresh(self):
        r = paradox.detail(OWN, self.rows[0], self.h)
        self.assertGreater(len(r["description"]), 400)
        self.assertNotIn("<p>", r["description"])
        self.assertIsNone(r["posted"])
        self.assertEqual(r["extra"]["refreshed"], "2026-09-11")
        self.assertEqual(r["extra"]["date_kind"], "refreshed")
        self.assertEqual(r["location"], "Denver, CO, United States")

    def test_detail_is_noop_without_career_site_page(self):
        h = StubH([("northwindcorp.recruiting.com/jobs?page_number=", pages())])
        row = paradox.list_jobs(T, h)[0]
        n = len(h.calls)
        self.assertNotIn("page", row["extra"])
        paradox.detail(T, row, h)
        self.assertEqual(len(h.calls), n)                   # never touches the WAF-challenged host
        self.assertEqual(row["description"], "")


if __name__ == "__main__":
    unittest.main()
