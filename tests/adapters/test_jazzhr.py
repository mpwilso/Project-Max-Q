import importlib.util, unittest

from tests.stubs import ROOT, StubH, fixture

NAME = "jazzhr"


def load():
    spec = importlib.util.spec_from_file_location(f"test_adapter_{NAME}", ROOT / "adapters" / f"{NAME}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


T = {"company": "Fabrikam Freight", "ats": NAME, "slug": "fabrikamfreight"}


class JazzhrTest(unittest.TestCase):
    def setUp(self):
        self.m = load()
        self.h = StubH([("app.jazz.co/feeds/export/jobs/fabrikamfreight", fixture(NAME, "feed.xml", as_json=False)),
                        ("fabrikamfreight.applytojob.com/apply", fixture(NAME, "board.html", as_json=False))])

    def test_contract(self):
        self.assertEqual(self.m.NAME, NAME)
        self.assertTrue(self.m.ENUMERABLE)
        self.assertEqual(self.m.REQUIRED, ["slug"])
        self.assertFalse(hasattr(self.m, "detail"))

    def test_feed_rows(self):
        rows = self.m.list_jobs(T, self.h)
        self.assertEqual(len(rows), 3)
        keys = [r["key"] for r in rows]
        self.assertEqual(len(keys), len(set(keys)))
        by = {r["extra"]["board_code"]: r for r in rows}
        acsm = by["Rk4vT8qLm2"]
        self.assertEqual(acsm["id"], "job_20260729083751_M4HD8QXC2NVAYR6E")
        self.assertEqual(acsm["posted"], "2026-07-29")                # from the id's creation stamp
        self.assertEqual(acsm["extra"]["date_kind"], "created")
        self.assertEqual(acsm["location"], "Raleigh, NC, United States | Remote")   # board label says Remote
        self.assertTrue(acsm["url"].startswith("https://fabrikamfreight.applytojob.com/apply/Rk4vT8qLm2/"))
        self.assertIn("Run quarterly reviews", acsm["description"])
        self.assertNotIn("<p>", acsm["description"])
        self.assertEqual(by["Hd2yK9cQa1"]["location"], "Pune, India")
        self.assertEqual(by["Pz7wB3nXe5"]["posted"], "2025-02-04")

    def test_board_labels(self):
        labels = self.m.board_labels(fixture(NAME, "board.html", as_json=False))
        self.assertEqual(labels["Rk4vT8qLm2"], "Remote")
        self.assertEqual(labels["Hd2yK9cQa1"], "Pune, India")

    def test_empty_feed_raises(self):
        h = StubH([("feeds/export", "<?xml version='1.0'?><jobs><publisher>JazzHR</publisher></jobs>"),
                   ("applytojob", "<html></html>")])
        with self.assertRaises(RuntimeError):
            self.m.list_jobs(T, h)


if __name__ == "__main__":
    unittest.main()
