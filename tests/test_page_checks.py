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
                  "a $2.4M budget", "$2.4 M a year", "Grew throughput 3x"):
            self.assertEqual(cc.metric_hits(s, FACTS), [], s)

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
