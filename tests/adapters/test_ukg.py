import importlib.util, unittest

from tests.stubs import ROOT, StubH, fixture, local_date

NAME = "ukg"
BASE = "https://wideworld.rec.pro.ukg.net/WWI1000WWIMP/JobBoard/0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d"


def load():
    spec = importlib.util.spec_from_file_location(f"test_adapter_{NAME}", ROOT / "adapters" / f"{NAME}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


T = {"company": "Wide World Importers", "ats": NAME, "base": BASE}


class UkgTest(unittest.TestCase):
    def setUp(self):
        self.m = load()
        p0, p1 = fixture(NAME, "list_page0.json"), fixture(NAME, "list_page1.json")
        self.first = p0["opportunities"][0]["Id"]

        def search(url, body):
            skip = body["opportunitySearch"]["Skip"]
            return {0: p0, 2: p1}.get(skip, {"opportunities": [], "totalCount": 3})

        self.h = StubH([("/JobBoardView/LoadSearchResults", search),
                        ("/OpportunityDetail?opportunityId=", fixture(NAME, "detail.html", as_json=False))])

    def test_contract(self):
        self.assertEqual(self.m.NAME, NAME)
        self.assertTrue(self.m.ENUMERABLE)
        self.assertEqual(self.m.REQUIRED, ["base"])

    def test_paginates_on_rows_returned_until_total(self):
        rows = self.m.list_jobs(T, self.h)
        self.assertEqual([c[1]["opportunitySearch"]["Skip"] for c in self.h.calls], [0, 2])
        self.assertEqual(len(rows), 3)
        keys = [r["key"] for r in rows]
        self.assertEqual(len(keys), len(set(keys)))
        for r in rows:
            self.assertRegex(r["key"], r"^ukg:Wide World Importers:[0-9a-f-]{36}$")
            self.assertIsNone(r["posted"])                          # list PostedDate is a repost stamp
            self.assertEqual(r["extra"]["date_kind"], "refreshed")
            self.assertTrue(r["url"].startswith(BASE + "/OpportunityDetail?opportunityId="))

    def test_locations(self):
        rows = self.m.list_jobs(T, self.h)
        by = {r["extra"]["location_type"]: r for r in rows}
        self.assertEqual(by["Remote"]["location"], "Austin, TX, United States | Remote")
        self.assertNotIn("Remote", by["Hybrid"]["location"])
        au = [r for r in rows if "Australia" in r["location"]][0]
        self.assertNotIn(", NSW", au["location"])                    # non-US state codes are not used
        self.assertIn("New South Wales", au["location"])

    def test_detail_fills_text_and_true_posted_date(self):
        row = {r["id"]: r for r in self.m.list_jobs(T, self.h)}[self.first]
        r = self.m.detail(T, row, self.h)
        self.assertEqual(r["posted"], local_date("2026-09-07T15:00:18.553Z"))
        self.assertEqual(r["extra"]["date_kind"], "posted")
        self.assertTrue(len(r["description"]) > 1000)
        self.assertNotIn("<p>", r["description"])
        self.assertEqual(r["comp"], "$119,800 - $138,000")          # band sits in the JD body; hidden PayRange ignored

    def test_smoke_reads_one_page(self):
        self.m.list_jobs(T, self.h, smoke=True)
        self.assertEqual(len(self.h.calls), 1)

    def test_empty_board_raises(self):
        h = StubH([("LoadSearchResults", {"opportunities": [], "totalCount": 0})])
        with self.assertRaises(RuntimeError):
            self.m.list_jobs(T, h)


if __name__ == "__main__":
    unittest.main()
