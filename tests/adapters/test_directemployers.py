import importlib.util, unittest

from tests.stubs import ROOT, StubH, fixture, local_date

NAME = "directemployers"


def load():
    spec = importlib.util.spec_from_file_location(f"test_adapter_{NAME}", ROOT / "adapters" / f"{NAME}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


T = {"company": "Northwind Traders", "ats": NAME, "base": "https://careers.northwindtraders.example"}
GUID = "5B2E91C47D0A4F3E8C61A9D27E4B3F05"
# date_new is a moment in time, dated in the time zone the sweep runs in.
POSTED = local_date("2026-06-17T00:24:25Z")


class Status404(Exception):
    class response:                     # mimics requests.HTTPError.response.status_code
        status_code = 404


def raise404(url, body=None):
    raise Status404(url)


class DirectEmployersTest(unittest.TestCase):
    def setUp(self):
        self.m = load()
        self.h = StubH([("sitemaps/index.xml", fixture(NAME, "sitemap_index.xml", as_json=False)),
                        ("sitemaps/jobs_1.xml", fixture(NAME, "jobs_1.xml", as_json=False)),
                        ("microsites.dejobs.org", fixture(NAME, "job.json"))])

    def test_contract(self):
        self.assertEqual(self.m.NAME, NAME)
        self.assertFalse(self.m.ENUMERABLE)     # sitemap is a weekly build, not a live index
        self.assertEqual(self.m.REQUIRED, ["base"])
        self.assertTrue(callable(self.m.detail))
        self.assertTrue(callable(self.m.lookup))

    def test_sitemap_index_only_yields_job_sitemaps(self):
        maps = self.m.parse_sitemap_index(fixture(NAME, "sitemap_index.xml", as_json=False),
                                          "https://careers.northwindtraders.example")
        self.assertEqual(maps, ["https://careers.northwindtraders.example/sitemaps/jobs_1.xml"])

    def test_list_rows(self):
        rows = self.m.list_jobs(T, self.h)
        self.assertEqual(len(rows), 6)
        self.assertEqual(len({r["key"] for r in rows}), 6)
        self.assertEqual([c[0] for c in self.h.calls],
                         ["https://careers.northwindtraders.example/sitemaps/index.xml",
                          "https://careers.northwindtraders.example/sitemaps/jobs_1.xml"])
        by = {r["id"]: r for r in rows}
        r = by[GUID]
        self.assertEqual(r["title"], "Manager Ai")             # url slug until detail() runs
        self.assertEqual(r["extra"]["title_source"], "url-slug")
        self.assertEqual(r["location"], "Denver, CO")
        self.assertIsNone(r["posted"])                         # lastmod is a build stamp, not a date
        self.assertEqual(r["extra"]["date_kind"], "refreshed")
        self.assertEqual(r["extra"]["refreshed"], "2026-09-13")
        self.assertEqual(r["url"], f"https://careers.northwindtraders.example/denver-co/manager-ai/{GUID}/job/")
        self.assertEqual(by["A93F2D5B7C1E4086B4D2E9F16C3A7B50"]["location"], "Colorado Springs, CO")
        self.assertEqual(by["C7E1B39A5D2F4C80A6B4E8D1F9032A7C"]["title"], "Dock Service Agent")

    def test_slug_helpers(self):
        self.assertEqual(self.m.location_from_slug("colorado-springs-co"), "Colorado Springs, CO")
        self.assertEqual(self.m.location_from_slug("remote-us"), "Remote, US | Remote")
        self.assertEqual(self.m.location_from_slug("anywhere"), "Anywhere")
        self.assertEqual(self.m.title_from_slug("principal-data-engineer-platform"),
                         "Principal Data Engineer Platform")

    def test_index_without_a_jobs_sitemap_raises(self):
        h = StubH([("sitemaps/index.xml",
                    '<?xml version="1.0" encoding="UTF-8"?><sitemapindex '
                    'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><sitemap>'
                    "<loc>https://careers.northwindtraders.example/sitemaps/pages.xml</loc></sitemap></sitemapindex>")])
        with self.assertRaises(RuntimeError):
            self.m.list_jobs(T, h)

    def test_data_url_defaults_to_the_host_folder(self):
        self.assertEqual(self.m.data_url(T, GUID.lower()),
                         f"https://microsites.dejobs.org/careers-northwindtraders-example/data/{GUID}.json")
        self.assertEqual(self.m.data_url({"base": "https://careers.x.com", "folder": "custom-folder"}, GUID),
                         f"https://microsites.dejobs.org/custom-folder/data/{GUID}.json")

    def test_detail_fills_from_the_job_json(self):
        row = next(r for r in self.m.list_jobs(T, self.h) if r["id"] == GUID)
        self.m.detail(T, row, self.h)
        self.assertEqual(row["title"], "Manager, AI")
        self.assertEqual(row["extra"]["title_source"], "detail")
        self.assertEqual(row["location"], "Denver, CO")
        self.assertEqual(row["posted"], POSTED)                # date_new, not date_added
        self.assertEqual(row["extra"]["added"], local_date("2026-07-24T19:02:03.590Z"))
        self.assertEqual(row["extra"]["date_kind"], "posted")
        self.assertEqual(row["extra"]["reqid"], "2026-20417")
        self.assertEqual(row["extra"]["job_type"], "Full-Time")
        self.assertIn("machine learning tools", row["description"])
        self.assertIn("Qualifications", row["description"])
        self.assertNotIn("<p>", row["description"])

    def test_lookup_open_deleted_and_missing(self):
        row = self.m.lookup(T, GUID, self.h)
        self.assertEqual(row["id"], GUID)
        self.assertEqual(row["title"], "Manager, AI")
        self.assertEqual(row["posted"], POSTED)
        self.assertEqual(row["url"], f"https://careers.northwindtraders.example/denver-co/manager-ai/{GUID}/job/")

        gone = dict(fixture(NAME, "job.json")); gone["deleted_at"] = "2026-09-18T00:00:00Z"
        self.assertIsNone(self.m.lookup(T, GUID, StubH([("microsites.dejobs.org", gone)])))
        self.assertIsNone(self.m.lookup(T, GUID, StubH([("microsites.dejobs.org", raise404)])))
        with self.assertRaises(RuntimeError):
            self.m.lookup(T, GUID, StubH([("microsites.dejobs.org", {"nothing": "useful"})]))


if __name__ == "__main__":
    unittest.main()
