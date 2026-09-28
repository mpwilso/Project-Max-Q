import importlib.util, unittest

from tests.stubs import ROOT, StubH, fixture

NAME = "gem"


def load():
    spec = importlib.util.spec_from_file_location(f"test_adapter_{NAME}", ROOT / "adapters" / f"{NAME}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


T = {"company": "Globex Systems", "ats": NAME, "slug": "globex"}


class GemTest(unittest.TestCase):
    def setUp(self):
        self.m = load()
        self.h = StubH([("/job_board/v0/globex/job_posts/", fixture(NAME, "job_posts.json"))])

    def test_contract(self):
        self.assertEqual(self.m.NAME, NAME)
        self.assertTrue(self.m.ENUMERABLE)
        self.assertEqual(self.m.REQUIRED, ["slug"])
        self.assertFalse(hasattr(self.m, "detail"))

    def test_single_read_whole_board(self):
        rows = self.m.list_jobs(T, self.h)
        self.assertEqual(len(self.h.calls), 1)            # no pagination on this API
        self.assertEqual(len(rows), 3)
        keys = [r["key"] for r in rows]
        self.assertEqual(len(keys), len(set(keys)))
        for r in rows:
            self.assertRegex(r["key"], r"^gem:Globex Systems:[A-Za-z0-9_-]+$")
            self.assertTrue(r["title"])
            self.assertRegex(r["posted"], r"^\d{4}-\d{2}-\d{2}$")
            self.assertTrue(r["description"])
            self.assertTrue(r["url"].startswith("https://jobs.gem.com/globex/"))

    def test_fields(self):
        by = {r["title"]: r for r in self.m.list_jobs(T, self.h)}
        se = by["Software Engineer"]
        self.assertEqual(se["id"], "4001234005")
        self.assertEqual(se["posted"], "2022-03-25")               # first_published_at, not updated_at
        self.assertEqual(se["extra"]["refreshed"], "2026-03-18")
        self.assertIn("United States", se["location"])
        remote = [r for r in by.values() if r["extra"]["location_type"] == "remote"][0]
        self.assertTrue(remote["location"].endswith("| Remote"))
        self.assertIn("United States", remote["location"])

    def test_empty_board_raises(self):
        with self.assertRaises(RuntimeError):
            self.m.list_jobs(T, StubH([("job_posts", [])]))


if __name__ == "__main__":
    unittest.main()
