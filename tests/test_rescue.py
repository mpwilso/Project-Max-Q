"""Regression tests for the out-of-lane body rescue.

A terminal title gate fails most reqs on title alone, before location or tenure are evaluated and
without reading the JD body, so in-lane roles posted under a house-dialect title are thrown away.
A req whose title is out of lane runs the whole gate anyway and is then judged on its BODY, and a
rescued req is REVIEW, never PASS.

Offline: no network. These assert behaviour, not the tuning constants."""
import unittest

from tests.stubs import sweep, gates

G = gates()

STRONG_BODY = (
    "Own the roadmap and backlog for our internal tools platform. Partner with stakeholders across "
    "finance and engineering. You will lead the ERP roadmap across procure-to-pay and order-to-cash, "
    "with data quality checks on every integration. Experience with NetSuite or Coupa and month-end close "
    "is required. Cross-functional release and UAT coordination. 5+ years."
)
WEAK_BODY = (
    "Operate the forklift on second shift. Load and unload freight, scan pallets, and keep the dock "
    "clean. Must lift 50 pounds. No experience required."
)


def out_of_lane_row(title="Marathon Producer", location="United States, Remote", desc=""):
    """A title deliberately absent from gates.json title_include_any."""
    return sweep.norm("TestCo", "greenhouse", 1, title, location,
                      "https://example.test/TestCo/1", "2026-09-18", desc)


class TitleIsNoLongerTerminal(unittest.TestCase):
    def test_out_of_lane_with_no_body_asks_for_a_fetch(self):
        # Not a terminal FAIL['title out of lane']: the body has not been read yet.
        v, why = sweep.gate(out_of_lane_row(desc=""), G)
        self.assertEqual(v, "RESCUE-FETCH")
        self.assertIn("body not read", why[0])

    def test_out_of_lane_with_a_matching_body_is_rescued_as_review(self):
        v, why = sweep.gate(out_of_lane_row(desc=STRONG_BODY), G)
        self.assertEqual(v, "REVIEW")
        self.assertIn("RESCUED", why[0])

    def test_a_rescue_is_never_a_pass(self):
        # REVIEW is a req for a human to look at, not a gate endorsement. If this ever returns PASS the
        # rescued req enters the PASS count the health funnel watches and a warning stops meaning anything.
        self.assertNotEqual(sweep.gate(out_of_lane_row(desc=STRONG_BODY), G)[0], "PASS")

    def test_out_of_lane_with_an_unrelated_body_still_fails(self):
        v, why = sweep.gate(out_of_lane_row(desc=WEAK_BODY), G)
        self.assertEqual(v, "FAIL")
        self.assertIn("body scored", why[0])


class RescueTitleExcludes(unittest.TestCase):
    """Title families configured out of scope (contract, GTM, sales, HR, audit) would otherwise flood
    REVIEW. gates.json rescue.title_exclude_any ends them before a body fetch is spent."""

    def test_a_declined_family_is_not_fetched_or_rescued(self):
        for title in ("Change Management Specialist, Finance Transformation (Contract)",
                      "Revenue Operations Lead", "Senior HR Operations Specialist", "IT Auditor"):
            for desc in ("", STRONG_BODY):
                v, why = sweep.gate(out_of_lane_row(title=title, desc=desc), G)
                self.assertEqual(v, "FAIL", title)
                self.assertTrue(why[0].startswith("title out of lane: rescue exclude"), why)

    def test_business_systems_titles_stay_rescuable(self):
        # Left out of the exclude list on purpose: these are business-systems work.
        for title in ("FP&A Systems and AI Manager", "Audit Innovation Lead, Agents",
                      "Senior HRIS Analyst (Workday)"):
            v, _ = sweep.gate(out_of_lane_row(title=title, desc=STRONG_BODY), G)
            self.assertEqual(v, "REVIEW", title)


class RescueRunsTheWholeGateFirst(unittest.TestCase):
    """An out-of-lane req earns a body read only if it would otherwise have been worth reading."""

    def test_a_non_us_location_is_never_rescued(self):
        v, why = sweep.gate(out_of_lane_row(location="London, United Kingdom", desc=STRONG_BODY), G)
        self.assertEqual(v, "FAIL")

    def test_an_excluded_title_is_never_rescued(self):
        # 'director' is an exclude, and exclude still wins over everything.
        v, why = sweep.gate(out_of_lane_row(title="Director of Production", desc=STRONG_BODY), G)
        self.assertEqual(v, "FAIL")
        self.assertIn("title excluded", why[0])

    def test_funnel_wording_is_preserved_for_anything_not_rescued(self):
        # The health funnel counts fail reasons by their first word-group. An out-of-lane req that dies
        # on location must still read 'title out of lane', or the funnel's largest bucket silently moves.
        _, why = sweep.gate(out_of_lane_row(location="Berlin, Germany", desc=""), G)
        self.assertEqual(why[0], "title out of lane")


class InLaneIsUnaffected(unittest.TestCase):
    def test_an_in_lane_req_still_passes(self):
        r = sweep.norm("TestCo", "greenhouse", 2, "Product Manager, Finance Systems",
                       "Remote, United States", "https://example.test/TestCo/2", "2026-09-18",
                       "Own the backlog. 5+ years.")
        self.assertEqual(sweep.gate(r, G)[0], "PASS")

    def test_an_in_lane_req_is_never_routed_through_rescue(self):
        r = sweep.norm("TestCo", "greenhouse", 3, "Technical Program Manager", "Remote, United States",
                       "https://example.test/TestCo/3", "2026-09-18", "")
        self.assertNotEqual(sweep.gate(r, G)[0], "RESCUE-FETCH")


class DisablingTheTierRestoresTheOldBehaviour(unittest.TestCase):
    def test_rescue_disabled_is_the_old_terminal_fail(self):
        g = dict(G, rescue={"enabled": False})
        v, why = sweep.gate(out_of_lane_row(desc=STRONG_BODY), g)
        self.assertEqual((v, why[0]), ("FAIL", "title out of lane"))


class MeasuredLimit(unittest.TestCase):
    """Documents what this tier does NOT do.

    An event 'Producer' req is not saved by the rescue tier: its body really is live-event production
    work and scores below the floor, so reading it does not help. Catching it would need the title
    word 'producer', which on US boards is almost all video, media and event producers. A req like
    that is reviewed by hand, not by the sweep."""

    def test_a_genuinely_out_of_lane_body_is_not_rescued(self):
        event_body = (
            "Produce corporate conferences and trade-show booths from brief to load-out. Book venues, "
            "caterers and AV crews, and run show-day timelines. You have produced events for 500+ "
            "attendees. Comfortable on your feet for long days and with frequent travel."
        )
        self.assertEqual(sweep.gate(out_of_lane_row(desc=event_body), G)[0], "FAIL")


if __name__ == "__main__":
    unittest.main()
