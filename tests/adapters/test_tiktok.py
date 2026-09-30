"""Offline tests for adapters/tiktok.py against synthetic responses in the API's shape.

page1/page2 carry invented rows with count 5 and three rows a page: a US product req, a Singapore
req, a Canada req (repeated on page 2, the shape a req posted mid-walk produces), a US
"Third-party Associate" (agency contract) and a US intern.
"""
import importlib.util, unittest

from tests.stubs import ROOT, StubH, fixture

spec = importlib.util.spec_from_file_location("adapter_tiktok", ROOT / "adapters" / "tiktok.py")
tiktok = importlib.util.module_from_spec(spec); spec.loader.exec_module(tiktok)

T = {"company": "TikTok", "ats": "tiktok"}
PM = "7000000000000000001"
SINGAPORE = "7000000000000000002"
CANADA = "7000000000000000003"
AGENCY = "7000000000000000004"


def pages(*names):
    data = [fixture("tiktok", n) for n in names]
    empty = fixture("tiktok", "page_empty.json")
    def answer(url, body):
        i = body["offset"] // 3                      # fixture pages hold 3 rows
        return data[i] if i < len(data) else empty
    return answer


class TikTokListTest(unittest.TestCase):
    def setUp(self):
        self.h = StubH([("api.lifeattiktok.com", pages("page1.json", "page2.json"))])
        self.rows = tiktok.list_jobs(T, self.h)
        self.by = {r["id"]: r for r in self.rows}

    def test_country_filter_and_dedupe(self):
        # 5 unique ids on the board; Singapore is dropped, the repeated Canada row counted once.
        self.assertEqual(len(self.rows), 4)
        self.assertEqual(len({r["key"] for r in self.rows}), 4)
        self.assertNotIn(SINGAPORE, self.by)
        self.assertEqual(len(self.h.calls), 2)       # offset 6 >= count 5: no third request

    def test_body_contract(self):
        body = self.h.calls[0][1]
        self.assertEqual(body["limit"], tiktok.LIMIT)
        self.assertEqual(body["offset"], 0)
        self.assertEqual(body["location_code_list"], [])
        self.assertEqual(self.h.calls[1][1]["offset"], 3)   # paged on rows returned, not on the limit

    def test_key_title_location_url(self):
        r = self.by[PM]
        self.assertEqual(r["key"], f"tiktok:TikTok:{PM}")
        self.assertEqual(r["title"], "Senior Product Manager, Example Creator Tools")   # double space collapsed
        self.assertEqual(r["location"], "Chicago, Illinois, United States")
        self.assertEqual(r["url"], "https://lifeattiktok.com/search/" + PM)
        self.assertEqual(r["extra"]["code"], "A10001A")
        self.assertEqual(r["extra"]["category"], "Product manager")
        self.assertIn("Canada", self.by[CANADA]["location"])
        self.assertEqual(self.by["7000000000000000005"]["extra"]["subject"], "Intern Program")

    def test_posted_is_id_timestamp(self):
        r = self.by[PM]
        # The top 32 bits of the id are epoch seconds: a moment, dated in the time zone the sweep runs in.
        self.assertEqual(r["posted"], tiktok.dt.datetime.fromtimestamp(int(PM) >> 32).date().isoformat())
        self.assertIn(r["posted"], ("2021-08-24", "2021-08-25"))
        self.assertEqual(r["extra"]["date_kind"], "id_timestamp")
        self.assertIsNone(tiktok.id_date("not-a-number"))

    def test_description_joins_requirement(self):
        d = self.by[PM]["description"]
        self.assertIn("Example Creator Tools team", d)
        self.assertIn("Minimum Qualifications", d)

    def test_city_named_for_its_state_is_written_once(self):
        self.assertEqual(self.by["7000000000000000005"]["location"], "New York, United States")

    def test_recruit_type_exclusion(self):
        h = StubH([("api.lifeattiktok.com", pages("page1.json", "page2.json"))])
        rows = tiktok.list_jobs(dict(T, exclude_recruit_types=["Third-party Associate"]), h)
        self.assertNotIn(AGENCY, {r["id"] for r in rows})
        self.assertEqual(len(rows), 3)

    def test_smoke_one_page(self):
        h = StubH([("api.lifeattiktok.com", pages("page1.json", "page2.json"))])
        tiktok.list_jobs(T, h, smoke=True)
        self.assertEqual(len(h.calls), 1)

    def test_empty_board_raises(self):
        h = StubH([("api.lifeattiktok.com", fixture("tiktok", "page_empty.json"))])
        with self.assertRaises(RuntimeError):
            tiktok.list_jobs(T, h)

    def test_error_answer_raises(self):
        h = StubH([("api.lifeattiktok.com", {"code": 1, "message": "website-path missing"})])
        with self.assertRaises(RuntimeError):
            tiktok.list_jobs(T, h)


class TikTokDetailTest(unittest.TestCase):
    def test_detail_reads_pay_band(self):
        h = StubH([("lifeattiktok.com/search/", fixture("tiktok", "job_page.html", as_json=False))])
        row = {"id": PM, "description": "listed", "comp": None}
        tiktok.detail(T, row, h)
        self.assertEqual(row["comp"], "$151200 - $243800")
        self.assertEqual(row["description"], "listed")    # the list text is never overwritten


if __name__ == "__main__":
    unittest.main()
