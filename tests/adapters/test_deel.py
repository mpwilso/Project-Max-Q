"""Offline tests for adapters/deel.py.

careers.html: five synthetic job objects in the shape of the www.deel.com/careers flight data,
split across two __next_f.push chunks (the live page splits the jobs array mid-string too). The
fifth object repeats the second to exercise de-duplication. job_overview.html: synthetic JSON-LD
blocks in the shape of a job page (JobPosting plus BreadcrumbList). Postings and text are invented.
"""
import importlib.util, unittest

from tests.stubs import ROOT, StubH, fixture

spec = importlib.util.spec_from_file_location("adapter_deel", ROOT / "adapters" / "deel.py")
deel = importlib.util.module_from_spec(spec); spec.loader.exec_module(deel)

T = {"company": "Deel", "ats": "deel"}
SPM = "5f0c2a1e-7b3d-4c8e-9a21-3d4e5f6a7b8c"


class DeelTest(unittest.TestCase):
    def setUp(self):
        self.h = StubH([("www.deel.com/careers/", fixture("deel", "careers.html", as_json=False)),
                        (f"job-details/{SPM}/overview", fixture("deel", "job_overview.html", as_json=False))])
        self.rows = deel.list_jobs(T, self.h)
        self.by = {r["id"]: r for r in self.rows}

    def test_rows_dedupe_single_request(self):
        self.assertEqual(len(self.rows), 4)
        self.assertEqual(len({r["key"] for r in self.rows}), 4)
        self.assertEqual(len(self.h.calls), 1)

    def test_key_title_location_date(self):
        r = self.by[SPM]
        self.assertEqual(r["key"], f"deel:Deel:{SPM}")
        self.assertEqual(r["title"], "Senior Product Manager, Contractor Payments")
        self.assertEqual(r["location"], "Brazil | Canada | Mexico | United States")
        self.assertEqual(r["posted"], "2026-09-14")
        self.assertEqual(r["url"], f"https://jobs.deel.com/deel/job-details/{SPM}/overview")
        for r in self.rows:
            self.assertRegex(r["key"], r"^deel:Deel:[0-9a-f-]{36}$")
            self.assertRegex(r["posted"], r"^\d{4}-\d{2}-\d{2}$")

    def test_comp_doubled_dollar_normalised(self):
        self.assertEqual(self.by["6a1b2c3d-4e5f-4a6b-9c7d-8e9f0a1b2c3d"]["comp"], "$70,000 - $110,000")
        self.assertEqual(self.by["7b2c3d4e-5f6a-4b7c-8d8e-9f0a1b2c3d4e"]["comp"], "From $65,000 USD")
        self.assertIsNone(self.by[SPM]["comp"])
        self.assertIsNone(self.by["8c3d4e5f-6a7b-4c8d-9e9f-0a1b2c3d4e5f"]["comp"])   # band hidden on the board

    def test_detail_fills_description(self):
        r = deel.detail(T, self.by[SPM], self.h)
        self.assertGreater(len(r["description"]), 2000)
        self.assertEqual(r["posted"], "2026-09-14")
        self.assertIn("United States", r["location"])

    def test_missing_jobs_array_raises(self):
        h = StubH([("careers", "<html><script>self.__next_f.push([1,\"no jobs here\"])</script></html>")])
        with self.assertRaises(RuntimeError):
            deel.list_jobs(T, h)

    def test_detail_without_jsonld_raises(self):
        h = StubH([("job-details", "<html></html>")])
        with self.assertRaises(RuntimeError):
            deel.detail(T, dict(self.by[SPM]), h)


if __name__ == "__main__":
    unittest.main()
