import importlib.util, unittest

from tests.stubs import ROOT, StubH, fixture

NAME = "taleo_tbe"


def load():
    spec = importlib.util.spec_from_file_location(f"test_adapter_{NAME}", ROOT / "adapters" / f"{NAME}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


T = {"company": "Northwind IT", "ats": NAME, "org": "NORTHWIND", "cws": 12}


class TaleoTbeTest(unittest.TestCase):
    def setUp(self):
        self.m = load()
        self.h = StubH([("servlet/Rss", fixture(NAME, "feed.xml", as_json=False)),
                        ("rid=20352", fixture(NAME, "requisition.html", as_json=False)),
                        ("rid=20001", fixture(NAME, "requisition_closed.html", as_json=False))])

    def test_contract(self):
        self.assertEqual(self.m.NAME, NAME)
        self.assertFalse(self.m.ENUMERABLE)          # capped RSS sample: absence proves nothing
        self.assertEqual(self.m.REQUIRED, ["org", "cws"])
        self.assertTrue(callable(self.m.detail))
        self.assertTrue(callable(self.m.lookup))

    def test_urls_are_parameterised(self):
        self.assertEqual(self.m.rss_url(T),
                         "https://phf.tbe.taleo.net/phf02/ats/servlet/Rss?org=NORTHWIND&cws=12"
                         "&WebPage=SRCHR_V2&WebVersion=0&_rss_version=2")
        self.assertEqual(self.m.req_url({"org": "ACME", "cws": "7", "host": "chp.tbe.taleo.net", "inst": "chp01"}, 42),
                         "https://chp.tbe.taleo.net/chp01/ats/careers/requisition.jsp?org=ACME&cws=7&rid=42")
        with self.assertRaises(ValueError):
            self.m.rss_url({"org": "A&b=1", "cws": "1"})

    def test_feed_rows(self):
        rows = self.m.list_jobs(T, self.h)
        self.assertEqual(len(rows), 3)
        self.assertEqual(len({r["key"] for r in rows}), 3)
        by = {r["id"]: r for r in rows}
        tsr = by["20417"]
        self.assertEqual(tsr["title"], "Service Desk Analyst - IT Support")
        self.assertEqual(tsr["location"], "Chicago (Loop), IL, US")   # country appended once
        self.assertEqual(tsr["posted"], "2026-09-16")
        self.assertEqual(tsr["extra"]["date_kind"], "posted")
        self.assertEqual(tsr["extra"]["department"], "412 - Service Management")
        self.assertTrue(tsr["url"].endswith("requisition.jsp?org=NORTHWIND&cws=12&rid=20417"))
        self.assertIn("every Northwind Traders warehouse", tsr["description"])
        self.assertNotIn("<span", tsr["description"])                     # html-description stripped
        self.assertEqual(by["20388"]["location"], "Columbus, OH")            # no locationCountry on this row
        self.assertEqual(by["20388"]["posted"], "2026-09-09")
        self.assertEqual(by["20352"]["title"], "IT Product Owner - Catalog Browse")
        self.assertEqual(by["20352"]["posted"], "2026-08-20")

    def test_rfc822_date_is_not_readable_by_iso_date(self):
        # the trap: h.iso_date is month-first, so the feed's own pubDate returns None through it
        self.assertIsNone(self.h.iso_date("Thu, 20 Aug 2026 16:53:36 GMT"))
        self.assertEqual(self.m.rfc822_date("Thu, 20 Aug 2026 16:53:36 GMT"), "2026-08-20")
        self.assertIsNone(self.m.rfc822_date("not a date"))

    def test_remote_segment(self):
        self.assertEqual(self.m.location_label("Remote - US", "", "", ""), "Remote - US | Remote")
        self.assertEqual(self.m.location_label("", "Austin", "TX", "US"), "Austin, TX, US")

    def test_empty_feed_raises(self):
        h = StubH([("servlet/Rss", "<?xml version='1.0'?><rss version='2.0'><channel>"
                                   "<title>Empty</title></channel></rss>")])
        with self.assertRaises(RuntimeError):
            self.m.list_jobs(T, h)

    def test_detail_reads_the_requisition_json_ld(self):
        row = self.m.list_jobs(T, self.h)[2]
        self.assertEqual(row["id"], "20352")
        row["description"] = ""
        self.m.detail(T, row, self.h)
        self.assertEqual(row["title"], "IT Product Owner - Catalog Browse")
        self.assertEqual(row["location"], "Chicago (Loop), IL, US")
        self.assertEqual(row["posted"], "2026-08-20")
        self.assertEqual(row["extra"]["employment_type"], "Full-Time")
        self.assertGreater(len(row["description"]), 200)
        self.assertNotIn("<p>", row["description"])

    def test_lookup_open_closed_and_broken(self):
        row = self.m.lookup(T, "20352", self.h)
        self.assertEqual(row["id"], "20352")
        self.assertEqual(row["title"], "IT Product Owner - Catalog Browse")
        self.assertEqual(row["posted"], "2026-08-20")
        self.assertIsNone(self.m.lookup(T, "20001", self.h))               # "no longer available"
        h = StubH([("rid=1", "<html><body>totally different markup</body></html>")])
        with self.assertRaises(RuntimeError):
            self.m.lookup(T, "1", h)


if __name__ == "__main__":
    unittest.main()
