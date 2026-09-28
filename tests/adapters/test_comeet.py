import importlib.util, unittest

from tests.stubs import ROOT, StubH, fixture

NAME = "comeet"


def load():
    spec = importlib.util.spec_from_file_location(f"test_adapter_{NAME}", ROOT / "adapters" / f"{NAME}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


T = {"company": "Litware", "ats": NAME, "uid": "12.00A", "token": "EXAMPLE0FAKE0TOKEN000000000000000000000"}


class ComeetTest(unittest.TestCase):
    def setUp(self):
        self.m = load()
        self.h = StubH([("/company/12.00A/positions?token=", fixture(NAME, "positions.json"))])

    def test_contract(self):
        self.assertEqual(self.m.NAME, NAME)
        self.assertTrue(self.m.ENUMERABLE)
        self.assertEqual(self.m.REQUIRED, ["uid", "token"])
        self.assertFalse(hasattr(self.m, "detail"))

    def test_single_read_whole_board(self):
        rows = self.m.list_jobs(T, self.h)
        self.assertEqual(len(self.h.calls), 1)
        self.assertIn("details=true", self.h.calls[0][0])
        self.assertEqual(len(rows), 3)
        keys = [r["key"] for r in rows]
        self.assertEqual(len(keys), len(set(keys)))
        for r in rows:
            self.assertRegex(r["key"], r"^comeet:Litware:[0-9A-F]{2}\.[0-9A-F]{3}$")
            self.assertTrue(r["title"])
            self.assertIsNone(r["posted"])                          # the API has no posting date
            self.assertEqual(r["extra"]["date_kind"], "refreshed")
            self.assertRegex(r["extra"]["refreshed"], r"^\d{4}-\d{2}-\d{2}$")
            self.assertTrue(len(r["description"]) > 200)
            self.assertNotIn("<p>", r["description"])
            self.assertNotIn("?", r["url"])
            self.assertIn(r["id"].lower(), r["url"].lower())

    def test_locations(self):
        by = {r["id"]: r for r in self.m.list_jobs(T, self.h)}
        us = by["2A.01B"]
        self.assertTrue(us["location"].endswith("United States | Remote"))
        hybrid = by["3C.02E"]                                        # is_remote true, workplace Hybrid
        self.assertNotIn("Remote", hybrid["location"])
        self.assertIn("Israel", hybrid["location"])
        self.assertNotIn(", IL", by["4D.05F"]["location"])           # IL would read as Illinois

    def test_empty_board_raises(self):
        with self.assertRaises(RuntimeError):
            self.m.list_jobs(T, StubH([("positions", [])]))


if __name__ == "__main__":
    unittest.main()
