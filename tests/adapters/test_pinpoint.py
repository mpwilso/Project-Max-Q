import importlib.util, unittest

from tests.stubs import ROOT, StubH, fixture

NAME = "pinpoint"


def load():
    spec = importlib.util.spec_from_file_location(f"test_adapter_{NAME}", ROOT / "adapters" / f"{NAME}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


T = {"company": "Tailspin Toys", "ats": NAME, "sub": "tailspin"}


class PinpointTest(unittest.TestCase):
    def setUp(self):
        self.m = load()
        data = fixture(NAME, "postings.json")
        self.first = data["data"][0]
        self.h = StubH([("tailspin.pinpointhq.com/postings.json", data),
                        (self.first["path"], fixture(NAME, "posting.html", as_json=False))])

    def test_contract(self):
        self.assertEqual(self.m.NAME, NAME)
        self.assertTrue(self.m.ENUMERABLE)
        self.assertEqual(self.m.REQUIRED, ["sub"])

    def test_rows(self):
        rows = self.m.list_jobs(T, self.h)
        self.assertEqual(len(self.h.calls), 1)
        self.assertEqual(len(rows), 3)
        keys = [r["key"] for r in rows]
        self.assertEqual(len(keys), len(set(keys)))
        by = {r["id"]: r for r in rows}
        for r in rows:
            self.assertIsNone(r["posted"])                           # postings.json has no date
            self.assertTrue(len(r["description"]) > 200)
            self.assertNotIn("<li>", r["description"])
        self.assertEqual(by["610001"]["location"], "United States - Denver")
        self.assertEqual(by["610002"]["location"], "Canada - Toronto | Remote")
        self.assertEqual(by["610003"]["comp"], "$70,000 - $90,000")

    def test_detail_reads_date_from_json_ld(self):
        row = {r["id"]: r for r in self.m.list_jobs(T, self.h)}[self.first["id"]]
        r = self.m.detail(T, row, self.h)
        self.assertEqual(r["posted"], "2026-06-16")
        self.assertEqual(r["extra"]["date_kind"], "posted")

    def test_empty_board_raises(self):
        with self.assertRaises(RuntimeError):
            self.m.list_jobs(T, StubH([("postings.json", {"data": []})]))


if __name__ == "__main__":
    unittest.main()
