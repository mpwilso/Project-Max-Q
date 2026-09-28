import importlib.util, unittest

from tests.stubs import ROOT, StubH, fixture

NAME = "zohorecruit"
URL = "https://careers.northwind.example/jobs/Careers"


def load():
    spec = importlib.util.spec_from_file_location(f"test_adapter_{NAME}", ROOT / "adapters" / f"{NAME}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


T = {"company": "Northwind Traders", "ats": NAME, "url": URL}


class ZohoRecruitTest(unittest.TestCase):
    def setUp(self):
        self.m = load()
        self.h = StubH([("careers.northwind.example/jobs/Careers", fixture(NAME, "careers.html", as_json=False))])

    def test_contract(self):
        self.assertEqual(self.m.NAME, NAME)
        self.assertTrue(self.m.ENUMERABLE)
        self.assertEqual(self.m.REQUIRED, ["url"])
        self.assertFalse(hasattr(self.m, "detail"))

    def test_rows(self):
        rows = self.m.list_jobs(T, self.h)
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["key"], "zohorecruit:Northwind Traders:412300000000456789")
        self.assertEqual(r["title"], "Technical Recruiter")
        self.assertEqual(r["posted"], "2025-04-02")
        self.assertEqual(r["location"], "Raleigh, North Carolina, United States")
        self.assertEqual(r["url"], URL + "/412300000000456789")
        self.assertTrue(len(r["description"]) > 200)

    def test_remote_string_flag_and_country1(self):
        self.assertEqual(self.m._location({"City": "Austin", "Country1": "United States", "Remote_Job": "true"}),
                         "Austin, United States | Remote")

    def test_missing_input_and_empty_board_raise(self):
        with self.assertRaises(RuntimeError):
            self.m.list_jobs(T, StubH([("jobs/Careers", "<html></html>")]))
        with self.assertRaises(RuntimeError):
            self.m.list_jobs(T, StubH([("jobs/Careers", '<input type="hidden" value="[]" id="jobs">')]))


if __name__ == "__main__":
    unittest.main()
