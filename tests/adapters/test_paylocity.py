import importlib.util, unittest

from tests.stubs import ROOT, StubH, fixture, local_date

NAME = "paylocity"
GUID = "3f2a9c1e-5b7d-4e8f-9a0b-1c2d3e4f5a6b"


def load():
    spec = importlib.util.spec_from_file_location(f"test_adapter_{NAME}", ROOT / "adapters" / f"{NAME}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


T = {"company": "Fabrikam Freight", "ats": NAME, "guid": GUID}


class PaylocityTest(unittest.TestCase):
    def setUp(self):
        self.m = load()
        self.h = StubH([(f"/Recruiting/Jobs/All/{GUID}", fixture(NAME, "board.html", as_json=False)),
                        ("/Recruiting/Jobs/Details/5100001", fixture(NAME, "detail.html", as_json=False))])

    def test_contract(self):
        self.assertEqual(self.m.NAME, NAME)
        self.assertTrue(self.m.ENUMERABLE)
        self.assertEqual(self.m.REQUIRED, ["guid"])

    def test_list_reads_page_data(self):
        rows = self.m.list_jobs(T, self.h)
        self.assertEqual(len(self.h.calls), 1)
        self.assertEqual(len(rows), 3)
        keys = [r["key"] for r in rows]
        self.assertEqual(len(keys), len(set(keys)))
        by = {r["id"]: r for r in rows}
        it = by["5100001"]
        self.assertEqual(it["title"], "IT Operations Manager")
        self.assertEqual(it["posted"], local_date("2026-09-16T03:45:27-05:00"))
        self.assertEqual(it["extra"]["date_kind"], "published")
        self.assertEqual(it["location"], "Columbus, OH, United States")
        self.assertEqual(it["url"], "https://recruiting.paylocity.com/Recruiting/Jobs/Details/5100001")
        remote = by["5100002"]                                       # free-text name, country from JobLocation
        self.assertEqual(remote["location"], "Remote Worker - N/A (United States) | Remote")
        india = by["5100003"]
        self.assertIn("India", india["location"])
        self.assertNotIn("Remote", india["location"])

    def test_detail_fills_description(self):
        row = {r["id"]: r for r in self.m.list_jobs(T, self.h)}["5100001"]
        r = self.m.detail(T, row, self.h)
        self.assertTrue(len(r["description"]) > 2000)
        self.assertIn("Requirements", r["description"])
        self.assertIn("ITIL Foundation", r["description"])
        self.assertNotIn("<li>", r["description"])
        self.assertNotIn("Apply", r["description"][:40])

    def test_empty_board_raises(self):
        h = StubH([("/Recruiting/Jobs/All/", '<script>\n window.pageData = {"Jobs":[],"ModuleTitle":"X"};\n</script>')])
        with self.assertRaises(RuntimeError):
            self.m.list_jobs(T, h)


if __name__ == "__main__":
    unittest.main()
