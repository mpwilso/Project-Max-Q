import importlib.util, unittest

from tests.stubs import ROOT, StubH, fixture

NAME = "bamboohr"


def load():
    spec = importlib.util.spec_from_file_location(f"test_adapter_{NAME}", ROOT / "adapters" / f"{NAME}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


T = {"company": "Initech", "ats": NAME, "slug": "initech"}


class BamboohrTest(unittest.TestCase):
    def setUp(self):
        self.m = load()
        self.h = StubH([("initech.bamboohr.com/careers/list", fixture(NAME, "list.json")),
                        ("initech.bamboohr.com/careers/2107/detail", fixture(NAME, "detail_2107.json"))])

    def test_contract(self):
        self.assertEqual(self.m.NAME, NAME)
        self.assertTrue(self.m.ENUMERABLE)
        self.assertEqual(self.m.REQUIRED, ["slug"])

    def test_list(self):
        rows = self.m.list_jobs(T, self.h)
        self.assertEqual(len(self.h.calls), 1)
        self.assertEqual(len(rows), 3)
        keys = [r["key"] for r in rows]
        self.assertEqual(len(keys), len(set(keys)))
        by = {r["id"]: r for r in rows}
        for r in rows:
            self.assertIsNone(r["posted"])
            self.assertEqual(r["url"], f"https://initech.bamboohr.com/careers/{r['id']}")
        self.assertEqual(by["2107"]["location"], "Denver, Colorado, United States | Remote")   # locationType "1"
        self.assertEqual(by["2093"]["location"], "Chicago, Illinois")                         # hybrid office
        self.assertEqual(by["2051"]["location"], "Toronto, ON, Canada | Remote")              # province, no state

    def test_detail(self):
        row = {r["id"]: r for r in self.m.list_jobs(T, self.h)}["2107"]
        r = self.m.detail(T, row, self.h)
        self.assertEqual(r["posted"], "2026-07-23")
        self.assertTrue(len(r["description"]) > 500)
        self.assertNotIn("<p>", r["description"])
        self.assertIn("United States", r["location"])

    def test_empty_board_raises(self):
        h = StubH([("careers/list", {"meta": {"totalCount": 0}, "result": []})])
        with self.assertRaises(RuntimeError):
            self.m.list_jobs(T, h)


if __name__ == "__main__":
    unittest.main()
