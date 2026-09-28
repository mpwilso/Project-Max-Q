import importlib.util, unittest

from tests.stubs import ROOT, StubH, fixture

NAME = "teamtailor"


def load():
    spec = importlib.util.spec_from_file_location(f"test_adapter_{NAME}", ROOT / "adapters" / f"{NAME}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


T = {"company": "Acme Analytics", "ats": NAME, "base": "https://careers.acme-analytics.example"}


class TeamtailorTest(unittest.TestCase):
    def setUp(self):
        self.m = load()
        # order matters: StubH matches substrings in order, and both URLs contain jobs.json
        self.h = StubH([("jobs.json?page=2", fixture(NAME, "jobs_page2.json")),
                        ("jobs.json?per_page=", fixture(NAME, "jobs_page1.json"))])

    def test_contract(self):
        self.assertEqual(self.m.NAME, NAME)
        self.assertTrue(self.m.ENUMERABLE)
        self.assertEqual(self.m.REQUIRED, ["base"])
        self.assertFalse(hasattr(self.m, "detail"))

    def test_follows_next_url_then_stops(self):
        rows = self.m.list_jobs(T, self.h)
        urls = [u for u, _ in self.h.calls]
        self.assertEqual(len(urls), 2, urls)               # page 2 has no next_url
        self.assertEqual(len(rows), 3)
        keys = [r["key"] for r in rows]
        self.assertEqual(len(keys), len(set(keys)))
        for r in rows:
            self.assertRegex(r["key"], r"^teamtailor:Acme Analytics:\d+$")    # numeric job id, not the guid
            self.assertTrue(r["title"])
            self.assertRegex(r["posted"], r"^\d{4}-\d{2}-\d{2}$")
            self.assertTrue(r["description"])
            self.assertIn(f"/jobs/{r['id']}-", r["url"])

    def test_locations(self):
        by = {r["id"]: r for r in self.m.list_jobs(T, self.h)}
        self.assertEqual(by["700102"]["title"], "Head of Engineering")
        self.assertEqual(by["700102"]["location"], "Denver, United States")          # "US" expanded
        self.assertEqual(by["700102"]["posted"], "2026-08-13")
        self.assertEqual(by["700101"]["location"], "")                              # no office on the post
        self.assertEqual(by["700103"]["location"], "United States | Remote")         # TELECOMMUTE

    def test_smoke_reads_one_page(self):
        self.m.list_jobs(T, self.h, smoke=True)
        self.assertEqual(len(self.h.calls), 1)

    def test_page_that_repeats_stops(self):
        page1 = fixture(NAME, "jobs_page1.json")
        h = StubH([("jobs.json", page1)])                  # a server ignoring page= would loop forever
        rows = self.m.list_jobs(T, h)
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(h.calls), 2)

    def test_empty_board_raises(self):
        with self.assertRaises(RuntimeError):
            self.m.list_jobs(T, StubH([("jobs.json", {"version": "https://jsonfeed.org/version/1.1", "items": []})]))


if __name__ == "__main__":
    unittest.main()
