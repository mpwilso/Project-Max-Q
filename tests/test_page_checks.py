"""Tests for the two page checks that run before a reviewer sees a draft: a figure with a unit that no
claim states (metrics), and a resume longer than one page (max_lines). Both are offline."""
import contextlib, io, json, sys, tempfile, unittest
from pathlib import Path

from tests.stubs import ROOT
import claimcheck as cc

CFG = json.loads((ROOT / "examples" / "john_doe" / "claims.json").read_text(encoding="utf-8"))
FACTS = {"420", "3", "9", "6", "1,800", "4.5", "working days", "$2.4m"}


def reasons(text, cfg=CFG):
    return " | ".join(r for _, _, rs in cc.check(text, cfg) for r in rs)


class Metrics(unittest.TestCase):
    def test_a_figure_with_a_unit_no_fact_states_is_named_with_its_unit(self):
        for s, want in (("Saved $1.2M a year", "$1.2M"), ("Cut spend by 40%", "40%"),
                        ("Cut spend 40 percent", "40 percent"), ("Grew throughput 10x", "10x"),
                        ("Serves 400+ users", "400+ users"), ("12 teams across 4 countries", "12 teams"),
                        ("a $95K to $118K band", "$95K"), ("5 million rows", "5 million rows"),
                        ("10 years building finance systems", "10 years"), ("Led 2 pods", None)):
            hits = cc.metric_hits(s, FACTS)
            if want is None:
                self.assertEqual(hits, [], s)
            else:
                self.assertIn(want, hits, s)

    def test_a_figure_a_fact_states_passes_with_its_unit_adjectives_and_scale(self):
        for s in ("Led the rollout to 420 requesters in 3 business units", "Cut the close from 9 working days to 6",
                  "Cleaned 1,800 duplicate supplier records", "4.5 years at Northwind", "Cleaned 1800 records",
                  "a $2.4M budget", "$2.4 M a year"):
            self.assertEqual(cc.metric_hits(s, FACTS), [], s)
        # the bare fact 3 is a count, and a count does not support a multiplier
        self.assertEqual(cc.metric_hits("Grew throughput 3x", FACTS), ["3x"])

    def test_years_year_ranges_and_bare_numbers_are_not_metrics(self):
        for s in ("2021 to present", "2019 to 2022", "Class of 2019", "A 10-year career", "Ten years in finance",
                  "Cut the close from 9 days to 6"):
            self.assertEqual(cc.metric_hits(s, {"9", "days"}), [], s)
        self.assertEqual(cc.metric_hits("Built it in 2 x 2 sprints", set()), ["2 sprints"])

    def test_a_bullet_is_held_to_its_cited_claims(self):
        self.assertIn('metric "600 requesters" is not in the cited claim(s)',
                      reasons("- Led the Coupa rollout to 600 requesters. [coupa-rollout]"))
        self.assertNotIn("metric", reasons("- Led the Coupa rollout to 420 requesters. [coupa-rollout]"))
        # 9 is a fact of the close program, not of the rollout
        self.assertIn('metric "9 business units"', reasons("- Rolled Coupa out to 9 business units. [coupa-rollout]"))

    def test_a_line_nobody_cites_is_held_to_every_written_claim(self):
        self.assertIn('metric "10 years" is not in any written claim', reasons("10 years building finance systems"))
        self.assertEqual(reasons("Product owner with 420 requesters live on Coupa"), "")
        self.assertIn('metric "7 years" is not in any written claim', reasons("- 7 years in finance systems."))

    def test_a_spoken_claims_figure_does_not_support_a_printed_line(self):
        # 120 analyst hours is a spoken-only claim; printed, it is both a voice and a metric problem
        r = reasons("Freed 120 analyst hours a month")
        self.assertIn('metric "120 analyst hours"', r)
        self.assertIn("spoken-only", r)

    def test_the_example_resumes_are_unchanged(self):
        res = cc.check((ROOT / "examples/john_doe/resume.md").read_text(encoding="utf-8"), CFG)
        self.assertEqual((len(res), len([r for r in res if r[2]])), (7, 0))
        res = cc.check((ROOT / "examples/john_doe/resume_blocked.md").read_text(encoding="utf-8"), CFG)
        self.assertEqual((len(res), len([r for r in res if r[2]])), (6, 5))


LICENCE = {"id": "licence", "text": "Cut licence spend by $12,000 a year, 30% of the bill.",
           "facts": ["licence", "$12,000", "30%"]}


class UnitAndScale(unittest.TestCase):
    """A figure is supported by a fact with the same value, unit and scale, never by its bare number."""

    def test_a_bare_number_fact_never_supports_money_a_percentage_or_a_multiplier(self):
        self.assertIn('metric "$3M" is not in the cited claim(s)',
                      reasons("- Led the Coupa rollout to 420 requesters and saved $3M. [coupa-rollout]"))
        self.assertIn('metric "$420M" is not in any written claim',
                      reasons("Delivered $420M in savings across 9 countries"))
        for s, want in (("Grew throughput 3x", "3x"), ("Cut spend 3%", "3%"), ("Saved $3", "$3"),
                        ("Saved 3M", "3M")):
            self.assertEqual(cc.metric_hits(s, {"3"}), [want], s)
        self.assertEqual(cc.metric_hits("Served 3 regions", {"3"}), [])         # a bare count: supported

    def test_the_scale_and_the_currency_must_match(self):
        for s, facts in (("5 million rows", {"5"}), ("$2.4B", {"$2.4m"}), ("€2.4M", {"$2.4m"}),
                         ("$2.4M", {"2.4m"}), ("$2,400", {"$2.4k"}), ("40 percentage points", {"40%"})):
            self.assertTrue(cc.metric_hits(s, facts), (s, facts))

    def test_a_supported_figure_passes_in_another_spelling(self):
        for s, facts in (("Cut spend 40 percent", {"40%"}), ("Saved $2.4 million", {"$2.4m"}),
                         ("Saved 2.4M", {"$2.4m"}), ("Saved $2.40M", {"$2.4m"}), ("Grew throughput 3x", {"3 times"}),
                         ("Cut licence spend by $12,000, a 30% cut", {"$12,000", "30%"}),
                         ("Cut cost 10-20%", {"10-20%"}), ("Cut cost 10-20%", {"10%", "20%"}),
                         ("Serves 400+ users", {"400"}), ("Cleaned 1800 records", {"1,800"})):
            self.assertEqual(cc.metric_hits(s, facts), [], (s, facts))

    def test_both_ends_of_a_range_are_checked(self):
        self.assertEqual(cc.metric_hits("Cut cost 10-20%", {"20%"}), ["10-20%"])
        self.assertEqual(cc.metric_hits("Saved $1-2M", {"$2m"}), ["$1-2M"])
        self.assertEqual(cc.metric_hits("Saved $1-2M", {"$1m", "$2m"}), [])
        en = "10" + chr(0x2013) + "20%"                                      # an en dash between the ends
        self.assertEqual(cc.metric_hits("Cut cost " + en, {"20%"}), [en])

    def test_a_bullet_with_its_claims_money_and_percentage_passes(self):
        cfg = {**CFG, "claims": CFG["claims"] + [LICENCE]}
        self.assertEqual(reasons("- Cut licence spend by $12,000, a 30% cut. [licence]", cfg), "")
        self.assertEqual(reasons("- Cleaned 1,800 duplicate supplier records. [cutover-rollback]", CFG).count("figure"), 0)
        r = reasons("- Cut licence spend by $13,000, a 30% cut. [licence]", cfg)
        self.assertIn('metric "$13,000"', r)
        self.assertIn("figure 13,000", r)


class NotMetrics(unittest.TestCase):
    """Years, versions and quarters are not figures with a unit, and filler words reach only real nouns."""

    def test_years_versions_and_quarters_on_a_line_nobody_cites_pass(self):
        for s in ("In 2023 the team moved procurement to Coupa", "Since 2019 our systems run on NetSuite",
                  "Upgraded to Python 3.12 for all systems", "Promoted in 2022 after two years",
                  "Released 2.0 to all users", "Q3 2024 release", "Ran Python 3.12.1 everywhere",
                  "Shipped the 2024 roadmap to 2025 teams"):
            self.assertEqual(cc.metric_hits(s, set()), [], s)
            self.assertEqual(reasons(s), "", s)

    def test_filler_words_reach_only_a_metric_noun_and_never_a_preposition(self):
        self.assertEqual(cc.metric_hits("Served 400 active users", set()), ["400 active users"])
        self.assertEqual(cc.metric_hits("12 teams across 4 countries", set()), ["12 teams", "4 countries"])
        self.assertEqual(cc.metric_hits("Moved 40 of the users", set()), [])
        self.assertEqual(cc.metric_hits("Moved 40 to all teams", set()), [])
        self.assertEqual(cc.metric_hits("Built 3 new systems", set()), [])
        self.assertEqual(cc.metric_hits("Built 3 systems", set()), ["3 systems"])

    def test_a_dotted_figure_with_a_metric_unit_is_never_a_version(self):
        self.assertEqual(cc.metric_hits("Upgraded the close to 2.5 days", set()), ["2.5 days"])
        self.assertEqual(cc.metric_hits("Release 2.5% faster", set()), ["2.5%"])
        self.assertEqual(cc.metric_hits("the 2.0 release", set()), [])


class Speed(unittest.TestCase):
    def test_a_long_run_of_digits_and_separators_checks_in_linear_time(self):
        import time
        for line in ("1," * 10000, "1." * 10000, "1 " * 10000, "1" * 20000, "1,000" * 4000,
                     "$1," * 6667, "1, " * 6667, "- " + "1," * 10000 + " [coupa-rollout]"):
            t = time.perf_counter()
            cc.check(line, CFG)
            self.assertLess(time.perf_counter() - t, 0.5, line[:12])


class OnePage(unittest.TestCase):
    def page(self, lines):
        return "# John Doe\n\n" + "".join(f"Line {i} of the page.\n\n" for i in range(1, lines))

    def test_the_default_budget_is_fifty_non_blank_lines(self):
        self.assertEqual(cc.MAX_LINES, 50)
        self.assertEqual(reasons(self.page(50)), "")
        self.assertIn("over one page: non-blank line 51, past the budget of 50", reasons(self.page(51)))

    def test_the_first_line_over_the_budget_is_the_one_named_and_once(self):
        res = cc.check(self.page(60), {**CFG, "max_lines": 10})
        over = [(n, line) for n, line, rs in res if any(r.startswith("over one page") for r in rs)]
        self.assertEqual(over, [(21, "Line 10 of the page.")])   # blank lines between the shown ones don't count

    def test_a_bullet_or_heading_over_the_budget_keeps_its_other_reasons(self):
        text = "# John Doe\n- Own the NetSuite ERP roadmap. [erp-owner]\n- Led the rollout to 600 requesters. [coupa-rollout]\n"
        rs = {n: r for n, _, r in cc.check(text, {**CFG, "max_lines": 2})}
        self.assertTrue(rs[3][0].startswith("over one page"))
        self.assertIn("figure 600 is not in the cited claim(s)", rs[3])
        self.assertEqual(rs[2], [])

    def test_max_lines_must_be_a_whole_number_above_zero(self):
        for bad in (0, -1, "50", 2.5, True):
            self.assertTrue(cc.config_problems({**CFG, "max_lines": bad}), repr(bad))
        self.assertEqual(cc.config_problems({**CFG, "max_lines": 45}), [])

    def test_a_long_resume_exits_1(self):
        saved = sys.argv
        try:
            with tempfile.TemporaryDirectory() as d:
                p = Path(d) / "resume.md"
                p.write_text(self.page(51), encoding="utf-8")
                sys.argv = ["claimcheck.py", str(p)]
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    self.assertEqual(cc.main(), 1)
                self.assertIn("over one page", out.getvalue())
        finally:
            sys.argv = saved


if __name__ == "__main__":
    unittest.main()
