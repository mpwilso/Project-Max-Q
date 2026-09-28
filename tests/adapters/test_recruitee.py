import importlib.util, unittest

from tests.stubs import ROOT, StubH, fixture

NAME = "recruitee"


def load():
    spec = importlib.util.spec_from_file_location(f"test_adapter_{NAME}", ROOT / "adapters" / f"{NAME}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


T = {"company": "Northwind Traders", "ats": NAME, "slug": "northwind"}


class RecruiteeTest(unittest.TestCase):
    def setUp(self):
        self.m = load()
        self.h = StubH([("northwind.recruitee.com/api/offers/", fixture(NAME, "offers.json"))])

    def test_contract(self):
        self.assertEqual(self.m.NAME, NAME)
        self.assertTrue(self.m.ENUMERABLE)
        self.assertEqual(self.m.REQUIRED, ["slug"])
        self.assertFalse(hasattr(self.m, "detail"))

    def test_rows(self):
        rows = self.m.list_jobs(T, self.h)
        self.assertEqual(len(self.h.calls), 1)
        self.assertEqual(len(rows), 3)
        keys = [r["key"] for r in rows]
        self.assertEqual(len(keys), len(set(keys)))
        by = {r["id"]: r for r in rows}
        na = by["3100001"]
        self.assertEqual(na["posted"], "2026-09-04")                  # published_at
        self.assertEqual(na["extra"]["refreshed"], "2026-09-14")      # updated_at stays out of posted
        self.assertTrue(na["location"].startswith("Austin, Texas, United States | "))
        self.assertTrue(na["location"].endswith(" | Remote"))
        self.assertTrue(na["url"].startswith("https://careers.northwind.example/o/"))
        self.assertTrue(len(na["description"]) > 300)
        self.assertNotIn("<p>", na["description"])
        self.assertIn("Morocco", by["3100002"]["location"])            # nominal office stays in the string
        self.assertEqual(by["3100003"]["comp"], "$85,000 - $105,000")

    def test_empty_board_raises(self):
        with self.assertRaises(RuntimeError):
            self.m.list_jobs(T, StubH([("api/offers", {"offers": []})]))


if __name__ == "__main__":
    unittest.main()
