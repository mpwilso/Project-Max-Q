"""Tests for the claimcheck rules that hold for any candidate: people-management phrasing, hedges, AI-tell
words, em-dashes and invisible characters, titles not held, the voice of a claim, and a malformed claims
file. Every phrase below passed a plain word list before these rules existed."""
import contextlib, io, json, sys, tempfile, unittest
from pathlib import Path

from tests.stubs import ROOT
import claimcheck as cc

CFG = json.loads((ROOT / "examples" / "john_doe" / "claims.json").read_text(encoding="utf-8"))


def reasons(text):
    return " | ".join(r for _, _, rs in cc.check(text, CFG) for r in rs)


class PeopleManagement(unittest.TestCase):
    def test_the_management_verbs_are_caught(self):
        for s in ("Managed 8 engineers", "Supervised four developers", "Oversaw a team of eight analysts",
                  "Hired two analysts", "Headed the reporting staff", "Built it with a team of 6",
                  "Six analysts reported to me", "Ran weekly one-on-ones with my reports", "Led a pod of 4",
                  "Was given direct reports in 2023"):
            self.assertTrue(cc.people_management_hits(s), s)

    def test_adoption_mentoring_and_the_truthful_no_pass(self):
        for s in ("Prioritized the backlog for the finance systems team", "Mentored two new analysts",
                  "I have no direct reports", "Nobody reports to me", "Sent my reports on the close to finance",
                  "Managed the finance systems team's backlog"):
            self.assertEqual(cc.people_management_hits(s), [], s)

    def test_a_bullet_with_it_is_blocked(self):
        self.assertIn("people management", reasons("- Supervised four developers on the close. [close-program]"))


class Hedges(unittest.TestCase):
    def test_hedges_before_a_figure_are_caught(self):
        for s in ("some 400 requesters", "upward of 400 requesters", "≈400 requesters", "∼400 requesters",
                  "~400 requesters", "about half the close", "400 requesters or so", "approximately 420",
                  "over 400 requesters", "nearly 9 days", "more than $1,000"):
            self.assertTrue(cc.hedge_hits(s), s)

    def test_prose_that_only_looks_like_a_hedge_passes(self):
        for s in ("handed over 3 systems", "over 6 months", "a talk about the 3 units", "some requesters",
                  "Cut the close from 9 working days to 6"):
            self.assertEqual(cc.hedge_hits(s), [], s)


class AiTells(unittest.TestCase):
    def test_hyphen_optional_and_adverb_forms(self):
        for s in ("cutting edge", "cutting-edge", "cuttingedge", "world class", "robustly", "holistically",
                  "utilizing", "leveraged"):
            self.assertTrue(cc.ai_tell_hits(s), s)

    def test_plain_words_pass(self):
        self.assertEqual(cc.ai_tell_hits("Led the Coupa rollout to 420 requesters"), [])


class DashesAndInvisibleText(unittest.TestCase):
    def test_every_em_dash_form_and_invisible_character_is_caught(self):
        for s in ("Cut the close — 9 days to 6", "Cut the close ― 9 days to 6", "Cut the close -- 9 days to 6",
                  "Cut the close--9 days", "Cut​the close", "Cut the clo­se", "Cut⁠the close"):
            self.assertTrue(cc.dash_hits(s), repr(s))

    def test_hyphens_en_dashes_and_table_rules_pass(self):
        for s in ("order-to-cash", "2021 – present", "|---|---|", "---"):
            self.assertEqual(cc.dash_hits(s), [], s)

    def test_a_dash_on_any_line_blocks_and_a_clean_header_line_is_not_listed(self):
        text = "# John Doe\nDenver, CO — open to remote\n\n### Product Owner, Finance Systems · Northwind Traders · 2021\n"
        res = cc.check(text, CFG)
        self.assertEqual([n for n, _, rs in res if rs], [2])
        self.assertEqual([n for n, _, _ in res], [2, 4])


class TitlesNotHeld(unittest.TestCase):
    def test_a_title_cased_title_he_does_not_hold_is_blocked_anywhere(self):
        self.assertIn('title not held: "Senior Product Manager"',
                      reasons("- Acted as Senior Product Manager for the ERP. [erp-owner]"))
        self.assertIn("title not held", reasons("John Doe, Product Manager for finance systems"))
        self.assertIn('"Lead Product Owner"', reasons("- Lead Product Owner for NetSuite. [erp-owner]"))

    def test_the_titles_held_and_lowercase_descriptions_pass(self):
        self.assertEqual(cc.held_title_hits("Product Owner, Finance Systems at Northwind Traders", CFG), [])
        self.assertEqual(cc.held_title_hits("Thinks like a product manager about the backlog", CFG), [])


class VoiceOnThePage(unittest.TestCase):
    def test_a_spoken_claim_is_never_printed(self):
        self.assertIn("close-hours, a spoken-only claim", reasons("- Freed analyst time. [close-hours]"))
        r = reasons("- Freed 120 analyst hours a month. [close-program]")
        self.assertIn("states close-hours", r)
        self.assertIn("figure 120", r)

    def test_an_interview_claim_is_never_printed(self):
        self.assertIn("interview-only", reasons("- Ran a cutover that was rolled back. [rollout-approach]"))


class ClaimsFile(unittest.TestCase):
    def cfg(self, **claim):
        return {"claims": [{"id": "x", "text": "t", "facts": [], **claim}]}

    def test_a_backspace_in_a_pattern_is_refused(self):
        bad = json.loads('{"claims": [{"id": "x", "voice": "spoken", "facts": [], "patterns": ["\\bfoo\\b"]}]}')
        self.assertEqual(bad["claims"][0]["patterns"][0], "\bfoo\b")   # what JSON "\b" decodes to
        self.assertIn("control character", " ".join(cc.config_problems(bad)))
        good = json.loads('{"claims": [{"id": "x", "voice": "spoken", "facts": [], "patterns": ["\\\\bfoo\\\\b"]}]}')
        self.assertEqual(cc.config_problems(good), [])

    def test_a_spoken_claim_or_a_requires_needs_patterns(self):
        self.assertTrue(cc.config_problems(self.cfg(voice="spoken")))
        self.assertTrue(cc.config_problems(self.cfg(requires="estimated")))
        self.assertTrue(cc.config_problems(self.cfg(voice="loud", patterns=["x"])))
        self.assertTrue(cc.config_problems(self.cfg(voice="interview", patterns=["(unclosed"])))
        self.assertEqual(cc.config_problems(self.cfg()), [])

    def test_the_example_claims_file_is_clean(self):
        self.assertEqual(cc.config_problems(CFG), [])

    def test_a_malformed_claims_file_exits_2_with_one_line(self):
        saved = sys.argv
        try:
            with tempfile.TemporaryDirectory() as d:
                p = Path(d) / "claims.json"
                p.write_text('{"claims": [{"id": "x", "voice": "spoken", "facts": [], "patterns": ["\\bfoo\\b"]}]}',
                             encoding="utf-8")
                err = io.StringIO()
                sys.argv = ["claimcheck.py", str(ROOT / "examples/john_doe/resume.md"), "--claims", str(p)]
                with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(cc.main(), 2)
                self.assertIn("backspace", err.getvalue())
                self.assertEqual(len(err.getvalue().strip().splitlines()), 1)
        finally:
            sys.argv = saved


class ExistingBehaviourHolds(unittest.TestCase):
    def test_the_clean_resume_still_checks_seven_lines(self):
        res = cc.check((ROOT / "examples/john_doe/resume.md").read_text(encoding="utf-8"), CFG)
        self.assertEqual(len(res), 7)
        self.assertFalse([r for r in res if r[2]])

    def test_the_blocked_resume_still_blocks_five_of_six(self):
        res = cc.check((ROOT / "examples/john_doe/resume_blocked.md").read_text(encoding="utf-8"), CFG)
        self.assertEqual((len(res), len([r for r in res if r[2]])), (6, 5))


if __name__ == "__main__":
    unittest.main()
