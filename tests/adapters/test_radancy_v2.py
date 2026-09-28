"""Offline tests for adapters/radancy_v2.py against a synthetic Radancy / TalentBrew board.

results_page1.json: a results payload with 3 invented tiles whose links end in "/#job-details-section",
data-total-results 3.
job_detail.html: a job page's JobPosting block only.
"""
import importlib.util
import re
import unittest

from tests.stubs import ROOT, StubH, fixture, gates
import sweep

_spec = importlib.util.spec_from_file_location("adapter_radancy_v2", ROOT / "adapters" / "radancy_v2.py")
rv2 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rv2)

T = {"company": "Wide World Importers", "ats": "radancy_v2", "origin": "https://jobs.wideworld.example"}


class RadancyV2Test(unittest.TestCase):
    def setUp(self):
        self.h = StubH([("search-jobs/results", fixture("radancy_v2", "results_page1.json")),
                        ("/job/", fixture("radancy_v2", "job_detail.html", as_json=False))])
        self.rows = rv2.list_jobs(T, self.h)

    def test_fragment_links_parse(self):
        self.assertEqual(len(self.rows), 3)
        self.assertEqual(len(self.h.calls), 1)
        self.assertIn("Keywords=&", self.h.calls[0][0])            # whole board, no keyword
        self.assertIn("RecordsPerPage=100", self.h.calls[0][0])
        for r in self.rows:
            self.assertRegex(r["key"], r"^radancy_v2:Wide World Importers:\d+$")
            self.assertNotIn("#", r["url"])
            self.assertTrue(r["url"].startswith("https://jobs.wideworld.example/job/"))
        r = next(r for r in self.rows if r["title"] == "Analyst, Workforce Planning")
        self.assertEqual(r["location"], "Phoenix, Arizona")
        self.assertTrue(sweep.us_location_ok(r["location"], gates())[0])
        self.assertTrue(r["extra"]["brand"])
        self.assertEqual(r["extra"]["function"], "Commercial Operations")

    def test_builtin_regex_misses_these_links(self):
        body = fixture("radancy_v2", "results_page1.json")["results"]
        builtin = re.findall(r'href="(/[^"]*?/?job/[^"]+?/(\d+)/(\d+))"', body)
        self.assertEqual(builtin, [])                              # why this plugin exists

    def test_detail_jobposting(self):
        r = rv2.detail(T, self.rows[0], self.h)
        self.assertGreater(len(r["description"]), 400)
        self.assertEqual(r["posted"], "2026-05-19")                # "2026-5-19" normalised
        self.assertEqual(r["location"], "Phoenix, AZ, United States")

    def test_zero_tiles_raises(self):
        h = StubH([("search-jobs/results", {"results": '<section data-total-results="0"></section>'})])
        with self.assertRaises(RuntimeError):
            rv2.list_jobs(T, h)


if __name__ == "__main__":
    unittest.main()
