"""Offline tests for adapters/google.py.

Fixtures are synthetic job arrays in the board's positional shape, inside the page's own
AF_initDataCallback wrapper, with total 6. Page 2 repeats a page-1 job.
"""
import importlib.util, unittest

from tests.stubs import ROOT, StubH, fixture

spec = importlib.util.spec_from_file_location("adapter_google", ROOT / "adapters" / "google.py")
google = importlib.util.module_from_spec(spec); spec.loader.exec_module(google)

T = {"company": "Google", "ats": "google"}


def pages(*names):
    html = [fixture("google", n, as_json=False) for n in names]
    empty = fixture("google", "results_empty.html", as_json=False)
    def answer(url, body):
        p = int(url.rsplit("page=", 1)[1])
        return html[p - 1] if p <= len(html) else empty
    return answer


class GoogleListTest(unittest.TestCase):
    def setUp(self):
        self.h = StubH([("jobs/results/?location=United%20States&sort_by=date&page=",
                         pages("results_page1.html", "results_page2.html"))])
        self.rows = google.list_jobs(T, self.h)
        self.by = {r["id"]: r for r in self.rows}

    def test_rows_dedupe_and_stop(self):
        self.assertEqual(len(self.rows), 6)
        self.assertEqual(len({r["key"] for r in self.rows}), 6)
        self.assertEqual(len(self.h.calls), 2)          # 6 unique ids == total, no third request

    def test_key_title_location(self):
        r = self.by["113528806409117265"]
        self.assertEqual(r["key"], "google:Google:113528806409117265")
        self.assertEqual(r["title"], "Technical Program Manager, Service Reliability")
        self.assertEqual(r["location"], "Columbus, OH, USA")
        self.assertEqual(r["url"], google.BASE + "113528806409117265")
        for r in self.rows: self.assertRegex(r["key"], r"^google:Google:\d+$")

    def test_remote_flag_and_multi_country(self):
        self.assertEqual(self.by["101946628315037742"]["location"], "Texas, USA | Remote")
        self.assertEqual(self.by["104470592186233518"]["location"], "Toronto, ON, Canada | Austin, TX, USA")

    def test_dates_posted_is_earlier_stamp(self):
        r = self.by["88520193346627105"]
        self.assertEqual(r["posted"], "2026-09-14")
        self.assertEqual(r["extra"]["updated"], "2026-09-16")
        for r in self.rows: self.assertRegex(r["posted"], r"^\d{4}-\d{2}-\d{2}$")

    def test_description_and_comp_from_list(self):
        r = self.by["108841207735519026"]
        self.assertIn("Minimum qualifications", r["description"])
        self.assertTrue(r["comp"] and r["comp"].startswith("$"))

    def test_brand_filter(self):
        h = StubH([("sort_by=date", pages("results_page1.html", "results_page2.html"))])
        rows = google.list_jobs(dict(T, brands=["DeepMind"]), h)
        self.assertEqual([r["id"] for r in rows], ["94417729061348813"])

    def test_smoke_one_page(self):
        h = StubH([("sort_by=date", pages("results_page1.html", "results_page2.html"))])
        self.assertEqual(len(google.list_jobs(T, h, smoke=True)), 3)
        self.assertEqual(len(h.calls), 1)

    def test_stops_on_empty_page(self):
        h = StubH([("sort_by=date", pages("results_page1.html"))])
        self.assertEqual(len(google.list_jobs(T, h)), 3)
        self.assertEqual(len(h.calls), 2)

    def test_missing_blob_raises(self):
        h = StubH([("sort_by=date", "<html>unusual traffic</html>")])
        with self.assertRaises(RuntimeError):
            google.list_jobs(T, h)

    def test_empty_board_raises(self):
        h = StubH([("sort_by=date", fixture("google", "results_empty.html", as_json=False))])
        with self.assertRaises(RuntimeError):
            google.list_jobs(T, h)


if __name__ == "__main__":
    unittest.main()
