import importlib.util, unittest

from tests.stubs import ROOT, StubH, fixture

NAME = "workable"


def load():
    spec = importlib.util.spec_from_file_location(f"test_adapter_{NAME}", ROOT / "adapters" / f"{NAME}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


T = {"company": "Initech", "ats": NAME, "slug": "initech"}


class WorkableTest(unittest.TestCase):
    def setUp(self):
        self.m = load()
        self.h = StubH([("/api/v1/widget/accounts/initech", fixture(NAME, "widget.json"))])

    def test_contract(self):
        self.assertEqual(self.m.NAME, NAME)
        self.assertTrue(self.m.ENUMERABLE)
        self.assertEqual(self.m.REQUIRED, ["slug"])
        self.assertFalse(hasattr(self.m, "detail"))

    def test_single_read_with_details(self):
        rows = self.m.list_jobs(T, self.h)
        self.assertEqual(len(self.h.calls), 1)
        self.assertIn("details=true", self.h.calls[0][0])
        self.assertEqual(len(rows), 3)
        keys = [r["key"] for r in rows]
        self.assertEqual(len(keys), len(set(keys)))
        for r in rows:
            self.assertRegex(r["key"], r"^workable:Initech:[0-9A-F]{10}$")
            self.assertTrue(r["title"])
            self.assertRegex(r["posted"], r"^\d{4}-\d{2}-\d{2}$")
            self.assertTrue(r["description"])
            self.assertNotIn("<p>", r["description"])

    def test_hidden_placeholder_city_dropped(self):
        by = {r["id"]: r for r in self.m.list_jobs(T, self.h)}
        us = by["A1B2C3D4E5"]
        self.assertEqual(us["posted"], "2026-07-30")
        self.assertEqual(us["location"], "United States | Remote")   # hidden "Chicago" placeholder not kept
        self.assertEqual(by["B2C3D4E5F6"]["location"], "Paris, Île-de-France, France | Remote")

    def test_per_location_entries_fold_by_shortcode(self):
        t = {"company": "Globex Systems", "ats": NAME, "slug": "globex"}
        h = StubH([("/api/v1/widget/accounts/globex", fixture(NAME, "widget_multiloc.json"))])
        rows = self.m.list_jobs(t, h)
        self.assertEqual(len(rows), 2)                                   # 4 widget entries, 2 shortcodes
        by = {r["id"]: r for r in rows}
        self.assertEqual(by["D4E5F6A7B8"]["location"], "Germany | France | United Kingdom | Remote")
        self.assertEqual(by["D4E5F6A7B8"]["posted"], "2026-08-17")
        self.assertEqual(by["C3D4E5F6A7"]["location"], "United States | Remote")

    def test_empty_board_raises(self):
        with self.assertRaises(RuntimeError):
            self.m.list_jobs(T, StubH([("widget", {"name": "Initech", "jobs": []})]))


if __name__ == "__main__":
    unittest.main()
