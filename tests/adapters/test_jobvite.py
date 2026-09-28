"""Offline tests for adapters/jobvite.py.

feed.xml: synthetic <job> nodes in the feed's shape for a fictional employer (short JDs with a
Work Arrangement section and a pay sentence; one node carries internal hiring-team and referral
fields). Holds a US hybrid req, a US remote req, a Paris parent with its France copy, and an orphan
copy whose parent id is absent.
"""
import importlib.util, unittest

from tests.stubs import ROOT, StubH, fixture

spec = importlib.util.spec_from_file_location("adapter_jobvite", ROOT / "adapters" / "jobvite.py")
jobvite = importlib.util.module_from_spec(spec); spec.loader.exec_module(jobvite)

T = {"company": "Acme Analytics", "ats": "jobvite", "code": "aB3cD4eF", "slug": "acmeanalytics"}


class JobviteTest(unittest.TestCase):
    def setUp(self):
        self.h = StubH([("Xml.aspx?c=aB3cD4eF", fixture("jobvite", "feed.xml", as_json=False))])
        self.rows = jobvite.list_jobs(T, self.h)
        self.by = {r["id"]: r for r in self.rows}

    def test_rows_fold_copies_one_request(self):
        # 5 nodes: 3 parents + 1 copy folded into its parent + 1 orphan copy kept
        self.assertEqual(len(self.rows), 4)
        self.assertEqual(len({r["key"] for r in self.rows}), 4)
        self.assertEqual(len(self.h.calls), 1)

    def test_key_title_location_date_comp(self):
        r = self.by["pQ7rStUv"]
        self.assertEqual(r["key"], "jobvite:Acme Analytics:pQ7rStUv")
        self.assertEqual(r["title"], "Program Manager, Professional Services PMO")
        self.assertEqual(r["location"], "United States, United States")
        self.assertEqual(r["posted"], "2026-08-26")
        self.assertEqual(r["url"], "https://jobs.jobvite.com/acmeanalytics/job/pQ7rStUv")
        self.assertEqual(r["comp"], "$168,500 - $213,400")
        self.assertTrue(r["extra"]["work_arrangement"].startswith("Hybrid"))
        for r in self.rows:
            self.assertRegex(r["key"], r"^jobvite:Acme Analytics:[A-Za-z0-9-]+$")
            self.assertRegex(r["posted"], r"^\d{4}-\d{2}-\d{2}$")

    def test_remote_from_work_arrangement(self):
        r = self.by["k4mNoPqR"]
        self.assertEqual(r["location"], "Denver, CO, United States | Remote")
        self.assertEqual(r["comp"], "$118,300 - $221,700")

    def test_copy_folded_into_parent(self):
        r = self.by["w2xYzAbC"]
        # This req's Work Arrangement section says Remote, hence the trailing segment.
        self.assertEqual(r["location"], "Paris, France | France, France | Remote")
        self.assertEqual(r["extra"]["locations_folded"], 1)
        self.assertNotIn("w2xYzAbC-CdEfGhIj", self.by)
        self.assertEqual(self.by["oORPHAN1-Cxxxx"]["extra"]["orphan_of"], "oMISSING")

    def test_no_internal_fields_in_extra(self):
        for r in self.rows:
            self.assertFalse({"hiring_x0020_team", "referral_x0020_bonus"} & set(r["extra"]))

    def test_subsidiary_filter_on_parent_company_feed(self):
        # parent_company_feed.xml: synthetic nodes of a fictional parent company (code gH5jK6lM).
        # Initech parent + its copy, a plain Initech parent, a Litware parent + copy, and a
        # Northwind node whose subsidiary tag lacks the _x002A_ suffix.
        t = {"company": "Initech", "ats": "jobvite", "code": "gH5jK6lM", "slug": "initech", "subsidiary": "Initech"}
        feed = fixture("jobvite", "parent_company_feed.xml", as_json=False)
        rows = jobvite.list_jobs(t, StubH([("Xml.aspx?c=gH5jK6lM", feed)]))
        by = {r["id"]: r for r in rows}
        self.assertEqual(sorted(by), ["r5sTuVwX", "y8zAbCdE"])
        self.assertEqual(by["r5sTuVwX"]["location"], "Phoenix, AZ, United States | Austin, TX, United States")
        self.assertEqual(by["y8zAbCdE"]["url"], "https://jobs.jobvite.com/initech/job/y8zAbCdE")
        # Without the filter the whole parent-company feed comes back (copy folded, no orphans).
        allrows = jobvite.list_jobs({**t, "subsidiary": None}, StubH([("Xml.aspx", feed)]))
        self.assertEqual(len(allrows), 4)
        self.assertFalse(any("orphan_of" in r["extra"] for r in allrows))
        nw = jobvite.list_jobs({**t, "subsidiary": "northwind"}, StubH([("Xml.aspx", feed)]))
        self.assertEqual([r["id"] for r in nw], ["t6uVwXyZ"])
        with self.assertRaises(RuntimeError):
            jobvite.list_jobs({**t, "subsidiary": "Renamed"}, StubH([("Xml.aspx", feed)]))

    def test_non_xml_and_empty_feed_raise(self):
        with self.assertRaises(RuntimeError):
            jobvite.list_jobs(T, StubH([("Xml.aspx", "<html>not a feed")]))
        with self.assertRaises(RuntimeError):
            jobvite.list_jobs(T, StubH([("Xml.aspx", "<?xml version='1.0'?><result></result>")]))


if __name__ == "__main__":
    unittest.main()
