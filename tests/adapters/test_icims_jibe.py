import importlib.util, re, unittest

from tests.stubs import ROOT, StubH, fixture

NAME = "icims_jibe"


def load():
    spec = importlib.util.spec_from_file_location(f"test_adapter_{NAME}", ROOT / "adapters" / f"{NAME}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


T = {"company": "Contoso Health", "ats": NAME, "base": "https://careers.contoso-health.example",
     "params": {"country": "United States"}}


class IcimsJibeTest(unittest.TestCase):
    def setUp(self):
        self.m = load()
        self.h = StubH([("page=1&", fixture(NAME, "page1.json")),
                        ("page=2&", fixture(NAME, "page2.json")),
                        ("page=3&", fixture(NAME, "page3.json"))])

    def test_contract(self):
        self.assertEqual(self.m.NAME, NAME)
        self.assertTrue(self.m.ENUMERABLE)
        self.assertEqual(self.m.REQUIRED, ["base"])
        self.assertFalse(hasattr(self.m, "detail"))

    def test_list_paginates_to_total_and_stops(self):
        rows = self.m.list_jobs(T, self.h)
        self.assertEqual(len(rows), 3)
        urls = [u for u, _ in self.h.calls]
        self.assertEqual(len(urls), 2, urls)          # totalCount=3 consumed on page 2; page 3 never read
        self.assertIn("limit=100", urls[0])
        self.assertIn("country=United%20States", urls[0])
        keys = [r["key"] for r in rows]
        self.assertEqual(len(keys), len(set(keys)))
        for r in rows:
            self.assertRegex(r["key"], r"^icims_jibe:Contoso Health:\d+$")
            self.assertTrue(r["title"])
            self.assertRegex(r["posted"], r"^\d{4}-\d{2}-\d{2}$")
            self.assertTrue(r["description"])
            self.assertTrue(r["url"].startswith("https://careers.contoso-health.example/jobs/"))

    def test_rows_and_locations(self):
        rows = {r["id"]: r for r in self.m.list_jobs(T, self.h)}
        r = rows["40117"]
        self.assertEqual(r["title"], "Financial Analyst, Clinical Operations")
        self.assertEqual(r["posted"], "2026-08-05")
        self.assertIn("United States", r["location"])
        self.assertIn(" | Chicago, Illinois, United States", r["location"])   # additional_locations joined
        self.assertIsNone(r["comp"])                                            # salary_*_value "0" is no band
        self.assertEqual(rows["40362"]["location"], "United States | Remote")    # tags2 Remote -> bare segment
        self.assertNotIn("<", r["description"])
        self.assertIn("monthly forecast", r["description"])                  # responsibilities appended

    def test_smoke_reads_one_page(self):
        rows = self.m.list_jobs(T, self.h, smoke=True)
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(self.h.calls), 1)

    def test_page_cap_raises_instead_of_truncating(self):
        # A very large board: totalCount far past what MAX_PAGES can read. Every page returns fresh ids.
        page1 = fixture(NAME, "page1.json")
        tmpl = page1["jobs"][0]

        def page(url, body):
            n = int(url.split("page=")[1].split("&")[0])
            jobs = []
            for i in range(2):
                d = dict(tmpl["data"]); d["req_id"] = f"{n}{i:03d}"; d["slug"] = d["req_id"]
                jobs.append({"data": d})
            return {"jobs": jobs, "totalCount": 20075}

        self.m.MAX_PAGES = 3
        h = StubH([("/api/jobs", page)])
        with self.assertRaisesRegex(RuntimeError, "page cap"):
            self.m.list_jobs(T, h)
        self.assertEqual(len(h.calls), 3)

    def test_multi_value_params_are_quoted(self):
        t = {"company": "Litware", "ats": NAME, "base": "https://careers.litware.example",
             "params": {"categories": "Home/Regional Offices|Litware Travel"}}
        h = StubH([("page=1&", fixture(NAME, "page2.json"))])
        self.m.list_jobs(t, h, smoke=True)
        self.assertIn("categories=Home/Regional%20Offices%7CLitware%20Travel", h.calls[0][0])

    def test_empty_board_raises(self):
        h = StubH([("page=1&", fixture(NAME, "page3.json"))])
        with self.assertRaises(RuntimeError):
            self.m.list_jobs(T, h)


if __name__ == "__main__":
    unittest.main()
