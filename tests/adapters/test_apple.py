"""Offline tests for adapters/apple.py against synthetic responses in the API's shape.

search_page1/2 carry invented rows with totalRecords 5 so the walk ends on the count;
page 2 repeats one page-1 row, the shape a req posted mid-walk produces under sort=newest.
"""
import copy, importlib.util, re, unittest

from tests.stubs import ROOT, StubH, fixture

spec = importlib.util.spec_from_file_location("adapter_apple", ROOT / "adapters" / "apple.py")
apple = importlib.util.module_from_spec(spec); spec.loader.exec_module(apple)

T = {"company": "Apple", "ats": "apple"}
apple.time.sleep = lambda s: None         # the empty-page retry backs off; tests do not wait
EMPTY = {"res": {"searchResults": [], "totalRecords": 0}}


def search_route(pages):
    def answer(url, body):
        assert body["format"], "the search body must carry `format` or Apple answers an empty board"
        p = body["page"]
        return copy.deepcopy(pages[p - 1]) if p <= len(pages) else {"res": {"searchResults": [], "totalRecords": 0}}
    return answer


class AppleListTest(unittest.TestCase):
    def setUp(self):
        self.pages = [fixture("apple", "search_page1.json"), fixture("apple", "search_page2.json")]
        self.h = StubH([("api/v1/search", search_route(self.pages))])
        self.rows = apple.list_jobs(T, self.h)

    def test_rows_and_dedupe(self):
        self.assertEqual(len(self.rows), 5)
        keys = [r["key"] for r in self.rows]
        self.assertEqual(len(keys), len(set(keys)))

    def test_key_shape(self):
        for r in self.rows:
            self.assertRegex(r["key"], r"^apple:Apple:[A-Z0-9]+-\d+$")

    def test_pagination_stops_on_total(self):
        self.assertEqual([b["page"] for _, b in self.h.calls], [1, 2])

    def test_title_location_date(self):
        r = next(r for r in self.rows if r["id"] == "741203958-2210")
        self.assertEqual(r["title"], "Program Manager, Data Platform Reliability")
        self.assertEqual(r["location"], "Denver, United States")
        self.assertEqual(r["posted"], "2026-09-14")
        self.assertTrue(r["url"].startswith("https://jobs.apple.com/en-us/details/741203958-2210/"))
        for r in self.rows:
            if r["posted"]: self.assertRegex(r["posted"], r"^\d{4}-\d{2}-\d{2}$")

    def test_home_office_adds_remote(self):
        r = next(r for r in self.rows if r["id"] == "300588114-4417")
        self.assertEqual(r["location"], "Chicago, United States | Remote")

    def test_live_stamp_is_refreshed_not_posted(self):
        r = next(r for r in self.rows if r["id"].startswith("PIPE-"))
        self.assertIsNone(r["posted"])
        self.assertEqual(r["extra"]["date_kind"], "refreshed")
        self.assertRegex(r["extra"]["refreshed"], r"^\d{4}-\d{2}-\d{2}$")

    def test_smoke_reads_one_page(self):
        h = StubH([("api/v1/search", search_route(self.pages))])
        apple.list_jobs(T, h, smoke=True)
        self.assertEqual(len(h.calls), 1)

    def test_silent_zero_raises(self):
        h = StubH([("api/v1/search", {"res": {"searchResults": [], "totalRecords": 0}})])
        with self.assertRaises(RuntimeError):
            apple.list_jobs(T, h)

    def test_empty_page_inside_total_raises_not_truncates(self):
        # Not the end of the board: a page inside totalRecords that stays empty through every
        # retry is a hole, and the read must error so its reqs are carried.
        pages = copy.deepcopy(self.pages)
        for p in pages: p["res"]["totalRecords"] = 999
        h = StubH([("api/v1/search", search_route(pages))])
        with self.assertRaises(RuntimeError):
            apple.list_jobs(T, h)
        self.assertEqual([b["page"] for _, b in h.calls], [1, 2] + [3] * (apple.EMPTY_RETRIES + 1))

    def test_transient_empty_page_is_retried(self):
        # The API answers random in-range pages with an empty 200; taken as the end of the board,
        # that records a truncated walk as a full read. A retry returns the page.
        # totalRecords 25 puts page 2 inside the board (rows 21-40 of 20-row pages); the trimmed
        # fixture pages carry fewer rows, so page 3 is past the end and its empty answer is the stop.
        pages = copy.deepcopy(self.pages)
        for p in pages: p["res"]["totalRecords"] = 25
        flaky = {"n": 0}
        good = search_route(pages)
        def answer(url, body):
            if body["page"] == 2 and flaky["n"] < 2:
                flaky["n"] += 1
                return copy.deepcopy(EMPTY)
            return good(url, body)
        h = StubH([("api/v1/search", answer)])
        self.assertEqual(len(apple.list_jobs(T, h)), 5)
        self.assertEqual([b["page"] for _, b in h.calls], [1, 2, 2, 2, 3])

    def test_empty_first_page_is_retried(self):
        flaky = {"n": 0}
        good = search_route(self.pages)
        def answer(url, body):
            if body["page"] == 1 and not flaky["n"]:
                flaky["n"] = 1
                return copy.deepcopy(EMPTY)
            return good(url, body)
        h = StubH([("api/v1/search", answer)])
        self.assertEqual(len(apple.list_jobs(T, h)), 5)


class AppleDetailTest(unittest.TestCase):
    def test_detail_fills_text_city_and_this_citys_band(self):
        h = StubH([("api/v1/search", search_route([fixture("apple", "search_page2.json")])),
                   ("jobDetails/300640271-0231", fixture("apple", "detail.json"))])
        row = next(r for r in apple.list_jobs(T, h) if r["id"] == "300640271-0231")
        apple.detail(T, row, h)
        for part in ("bring-up and validation", "Responsibilities", "Minimum Qualifications",
                     "Preferred Qualifications"):
            self.assertIn(part, row["description"])
        self.assertEqual(row["location"], "Austin, Texas, United States")
        self.assertEqual(row["posted"], "2026-09-14")
        self.assertEqual(len(row["extra"]["allLocations"]), 3)
        # Austin has no pay footer; Phoenix's and Raleigh's bands must not leak onto this row.
        self.assertIsNone(row["comp"])
        self.assertNotIn("$158,300", row["description"])

    def test_detail_band_for_raleigh_posting(self):
        d = fixture("apple", "detail.json")
        d["res"]["selectedLocation"] = next(L for L in d["res"]["locations"] if L["id"] == "postLocation-RAL")
        h = StubH([("jobDetails/", d)])
        row = {"id": "300640271-0588", "location": "Raleigh, United States", "posted": None,
               "description": "", "comp": None, "extra": {}}
        apple.detail(T, row, h)
        self.assertEqual(row["comp"], "$171,200 - $298,400")
        self.assertTrue(re.search(r"Raleigh, North Carolina, United States$", row["location"]))


if __name__ == "__main__":
    unittest.main()
