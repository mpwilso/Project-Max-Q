"""Regression tests from a discovery audit: what the list reads dropped, what the retry pass wasted, and
what a re-gate let through.

- Lever's closing "additional" section held the only pay band on about one posting in nine and the
  only years line on some others; its structured salaryRange was ignored; and Lever "country" and Ashby
  "addressCountry" were dropped, so an "Any State" row with country US failed closed.
- The end-of-read retry re-read boards that were gone (404) or empty, which no second read can fix.
- A row whose JD text was stripped while its title was out of lane went PASS, unread, after a gate edit
  widened the title list.
- Titles that ARE a marketing role (gates.json title_hard_exclude) fail; product or program work on a
  marketing team does not.
- Dated snapshots piled up with nothing deleted.

Offline: the row builders and gate are called on synthetic postings."""
import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.stubs import sweep, gates

G = gates()


def lever_posting(**kw):
    p = {"id": "abc", "text": "Product Manager", "createdAt": 1760000000000, "hostedUrl": "https://jobs.lever.co/x/abc",
         "categories": {"location": "Any State", "team": "Product"}, "descriptionPlain": "Build products.",
         "lists": [{"text": "Requirements", "content": "<li>Ship things</li>"}]}
    p.update(kw)
    return p


class LeverRows(unittest.TestCase):
    def test_the_additional_section_is_part_of_the_description(self):
        r = sweep._lever_rows({"company": "L"}, [lever_posting(additionalPlain="The base salary range is $150,000 - $190,000.")])[0]
        self.assertIn("base salary range", r["description"])
        self.assertEqual(r["comp"], "$150,000 - $190,000")

    def test_html_additional_is_used_when_there_is_no_plain_form(self):
        r = sweep._lever_rows({"company": "L"}, [lever_posting(additional="<p>6+ years of product experience</p>")])[0]
        self.assertIn("6+ years of product experience", r["description"])

    def test_structured_salary_range_becomes_the_band(self):
        sr = {"currency": "USD", "interval": "per-year-salary", "min": 160000, "max": 205000}
        r = sweep._lever_rows({"company": "L"}, [lever_posting(salaryRange=sr)])[0]
        self.assertEqual(r["comp"], "$160,000 - $205,000")

    def test_non_usd_or_hourly_salary_range_is_not_a_band(self):
        for sr in ({"currency": "AUD", "interval": "per-year-salary", "min": 86000, "max": 98000},
                   {"currency": "USD", "interval": "per-hour-wage", "min": 60, "max": 80}):
            self.assertIsNone(sweep._lever_rows({"company": "L"}, [lever_posting(salaryRange=sr)])[0]["comp"])

    def test_us_country_is_appended_once_and_only_for_the_us(self):
        us = sweep._lever_rows({"company": "L"}, [lever_posting(country="US")])[0]
        self.assertEqual(us["location"], "Any State, US")
        already = sweep._lever_rows({"company": "L"}, [lever_posting(country="US", categories={"location": "Remote - US"})])[0]
        self.assertEqual(already["location"], "Remote - US")
        ca = sweep._lever_rows({"company": "L"}, [lever_posting(country="CA", categories={"location": "Toronto"})])[0]
        self.assertEqual(ca["location"], "Toronto")   # never ", CA": that reads as California


class AshbyCountry(unittest.TestCase):
    def test_address_country_rides_beside_the_location(self):
        board = {"jobs": [{"id": "j1", "title": "AI Deployment Manager", "location": "San Francisco",
                           "address": {"postalAddress": {"addressCountry": "United States"}},
                           "jobUrl": "https://jobs.ashbyhq.com/c/j1", "publishedAt": "2026-10-01T00:00:00Z",
                           "descriptionPlain": "Deploy agents."}]}
        with mock.patch.object(sweep, "etag_get", return_value=board):
            r = sweep.ashby({"company": "C", "slug": "c"})[0]
        self.assertEqual(r["location"], "San Francisco, United States")


class RetryOnlyWhatCanSucceed(unittest.TestCase):
    def test_gone_and_empty_boards_are_not_retried(self):
        for s in ("ERROR HTTPError: 404 Client Error: Not Found for url: https://boards-api.greenhouse.io/v1/boards/x/jobs",
                  "ERROR EmptyBoard: verified target returned 0 reqs, re-resolve the slug",
                  "ERROR RuntimeError: workable initech: widget answered with no jobs",
                  "ERROR RuntimeError: ukg https://x/JobBoard/y: board answered with no opportunities (totalCount=0)",
                  "ERROR RuntimeError: gem examplecorp: board answered 200 with no job posts"):
            self.assertTrue(sweep.PERMANENT_ERROR.search(s), s)

    def test_transient_errors_are_still_retried(self):
        for s in ("ERROR ReadTimeout: HTTPSConnectionPool(host='x', port=443): Read timed out.",
                  "ERROR HTTPError: 503 Server Error: Service Unavailable for url: https://x",
                  "ERROR ConnectionError: ('Connection aborted.', RemoteDisconnected())",
                  "ERROR HTTPError: 429 Client Error: Too Many Requests for url: https://x"):
            self.assertFalse(sweep.PERMANENT_ERROR.search(s), s)


class StrippedBodyIsNotAPass(unittest.TestCase):
    def setUp(self):
        self.row = {"key": "greenhouse:X:1", "company": "X", "ats": "greenhouse", "id": "1", "url": "",
                    "title": "Product Manager, Finance Systems", "location": "Remote, US", "posted": None,
                    "description": "", "comp": None, "extra": {}}

    def test_a_row_whose_text_was_dropped_is_review_with_the_reason(self):
        v, why = sweep.gate_final(dict(self.row, _text_stripped=True), G)
        self.assertEqual(v, "REVIEW")
        self.assertEqual(why[0], sweep.STRIPPED_BODY_NOTE)

    def test_a_refilled_body_gates_normally(self):
        row = dict(self.row, _text_stripped=True, description="Own the finance systems roadmap. 5+ years of experience.")
        self.assertEqual(sweep.gate_final(row, G)[0], "PASS")

    def test_a_stripped_fail_stays_a_fail(self):
        row = dict(self.row, _text_stripped=True, title="Senior Backend Engineer")
        self.assertEqual(sweep.gate_final(row, G)[0], "FAIL")


class MarketingRoleTitles(unittest.TestCase):
    """A title that IS a marketing role fails; product, program or project work on a marketing team
    stays gate-relevant."""
    def gate(self, title):
        row = {"key": "greenhouse:X:1", "company": "X", "ats": "greenhouse", "id": "1", "url": "", "title": title,
               "location": "Remote, US", "posted": None, "comp": None, "extra": {},
               "description": "Own the roadmap and partner with engineering. 5+ years of experience."}
        return sweep.gate_final(row, G)

    def test_marketing_role_titles_fail(self):
        for t in ("Global Integrated Marketing Programs Lead", "Growth Marketing Strategy & Planning Manager",
                  "Partner Marketing Programs Manager, Global", "Senior Marketing Strategy & Planning Manager"):
            v, why = self.gate(t)
            self.assertEqual(v, "FAIL", t)
            self.assertIn("marketing role", why[0], t)

    def test_product_and_program_work_on_a_marketing_team_is_spared(self):
        for t in ("Sr Product Manager - Growth Marketing Enablement", "Program Manager, Partner Marketing",
                  "Marketing Program Manager", "Associate Product Manager - Marketing Operations and Automation"):
            v, why = self.gate(t)
            self.assertFalse(v == "FAIL" and "marketing role" in (why or [""])[0], t)

    def test_an_empty_family_list_turns_it_off(self):
        g = dict(G, title_hard_exclude=[])
        row = {"key": "greenhouse:X:1", "company": "X", "ats": "greenhouse", "id": "1", "url": "",
               "title": "Field Marketing Program Manager", "location": "Remote, US", "posted": None, "comp": None,
               "extra": {}, "description": "5+ years of experience."}
        self.assertIsNone(sweep.title_hard_exclude("Growth Marketing Strategy Lead", g))
        self.assertEqual(sweep.gate_final(row, g)[0], "PASS")


class SnapshotRetention(unittest.TestCase):
    """Dated snapshots older than snapshot_keep_days are deleted; latest.json and the newest two stay."""
    def test_old_snapshots_go_and_recent_ones_stay(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            names = {"snapshot_2026-09-10.json.gz": True, "snapshot_2026-10-15.json": True,
                     "snapshot_2026-11-05.json.gz": False, "snapshot_2026-12-30.json.gz": False}
            for n in names: (d / n).write_bytes(b"x" * 10)
            (d / "latest.json").write_text("{}")
            with mock.patch.object(sweep, "DATA", d), mock.patch.object(sweep, "TODAY", dt.date(2026, 12, 31)):
                n, _ = sweep.prune_old_snapshots(60)
            self.assertEqual(n, 2)
            for name, gone in names.items():
                self.assertEqual(not (d / name).exists(), gone, name)
            self.assertTrue((d / "latest.json").exists())

    def test_the_newest_two_survive_however_old(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            for n in ("snapshot_2026-01-01.json.gz", "snapshot_2026-01-02.json.gz", "snapshot_2026-01-03.json.gz"):
                (d / n).write_bytes(b"x")
            with mock.patch.object(sweep, "DATA", d), mock.patch.object(sweep, "TODAY", dt.date(2026, 12, 31)):
                sweep.prune_old_snapshots(60)
            self.assertEqual(sorted(p.name for p in d.iterdir()), ["snapshot_2026-01-02.json.gz", "snapshot_2026-01-03.json.gz"])

    def test_zero_keeps_everything(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            for n in ("snapshot_2025-01-01.json.gz", "snapshot_2025-01-02.json.gz", "snapshot_2025-01-03.json.gz"):
                (d / n).write_bytes(b"x")
            with mock.patch.object(sweep, "DATA", d), mock.patch.object(sweep, "TODAY", dt.date(2026, 12, 31)):
                self.assertEqual(sweep.prune_old_snapshots(0), (0, 0.0))
            self.assertEqual(len(list(d.iterdir())), 3)

    def test_the_example_gates_set_the_window(self):
        self.assertEqual(G["snapshot_keep_days"], 60)


class DiffIsMeaningfulMemo(unittest.TestCase):
    def test_a_true_answer_is_remembered_and_a_false_one_is_rechecked(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            calls = []
            def count():
                calls.append(1)
                return len(list(d.glob("snapshot_*")))
            with mock.patch.object(sweep, "DATA", d), mock.patch.object(sweep, "snapshot_count", count):
                self.assertFalse(sweep.diff_is_meaningful())
                self.assertFalse(sweep.diff_is_meaningful())
                self.assertEqual(len(calls), 2)
                for n in ("snapshot_2026-10-01.json.gz", "snapshot_2026-10-02.json.gz"):
                    (d / n).write_bytes(b"x")
                self.assertTrue(sweep.diff_is_meaningful())
                self.assertTrue(sweep.diff_is_meaningful())
                self.assertEqual(len(calls), 3)
            sweep._DIFF_MEANINGFUL.discard(str(d))


if __name__ == "__main__":
    unittest.main()
