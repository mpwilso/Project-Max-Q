import importlib.util, unittest

from tests.stubs import ROOT, StubH, fixture

NAME = "rippling"
MULTI = "9e8d7c6b-5a4f-4e3d-8c2b-1a0f9e8d7c6b"


def load():
    spec = importlib.util.spec_from_file_location(f"test_adapter_{NAME}", ROOT / "adapters" / f"{NAME}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


T = {"company": "Contoso Health", "ats": NAME, "slug": "contoso"}


class RipplingTest(unittest.TestCase):
    def setUp(self):
        self.m = load()
        self.h = StubH([("jobs?page=0&", fixture(NAME, "list_page0.json")),
                        ("jobs?page=1&", fixture(NAME, "list_page1.json")),
                        (f"/jobs/{MULTI}", fixture(NAME, "detail.json"))])

    def test_contract(self):
        self.assertEqual(self.m.NAME, NAME)
        self.assertTrue(self.m.ENUMERABLE)
        self.assertEqual(self.m.REQUIRED, ["slug"])

    def test_list_groups_location_items_and_stops_on_total_pages(self):
        rows = self.m.list_jobs(T, self.h)
        self.assertEqual(len(self.h.calls), 2)            # totalPages=2
        self.assertEqual(len(rows), 3)                    # 4 location items, 3 distinct uuids
        keys = [r["key"] for r in rows]
        self.assertEqual(len(keys), len(set(keys)))
        for r in rows:
            self.assertRegex(r["key"], r"^rippling:Contoso Health:[0-9a-f-]{36}$")
            self.assertTrue(r["title"])
            self.assertIsNone(r["posted"])               # list has no date; detail fills it
        by = {r["id"]: r for r in rows}
        self.assertEqual(by[MULTI]["title"], "Business Operations Manager")
        self.assertEqual(by[MULTI]["location"], "Denver, CO, United States | Chicago, IL, United States")
        self.assertEqual(by["1f2e3d4c-5b6a-4978-8a6b-5c4d3e2f1a0b"]["location"], "Remote (United States) | Remote")
        self.assertEqual(by["2a3b4c5d-6e7f-4a8b-9c0d-1e2f3a4b5c6d"]["location"], "Toronto, ON, Canada")

    def test_detail_fills_text_date_band(self):
        rows = {r["id"]: r for r in self.m.list_jobs(T, self.h)}
        r = self.m.detail(T, rows[MULTI], self.h)
        self.assertRegex(r["posted"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertEqual(r["posted"], "2026-03-31")
        self.assertTrue(len(r["description"]) > 200)
        self.assertNotIn("<p", r["description"])
        self.assertEqual(r["comp"], "$170,000 - $250,000")
        self.assertEqual(r["extra"]["date_kind"], "created")

    def test_smoke_reads_one_page(self):
        self.m.list_jobs(T, self.h, smoke=True)
        self.assertEqual(len(self.h.calls), 1)

    def test_empty_board_raises(self):
        h = StubH([("jobs?page=0&", {"items": [], "page": 0, "pageSize": 100, "totalItems": 0, "totalPages": 0})])
        with self.assertRaises(RuntimeError):
            self.m.list_jobs(T, h)


if __name__ == "__main__":
    unittest.main()
