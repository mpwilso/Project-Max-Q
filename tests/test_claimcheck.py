"""Tests for claimcheck.py: a resume line that the verified claims don't support is blocked."""
import json, unittest

from tests.stubs import ROOT
import claimcheck

CFG = json.loads((ROOT / "examples" / "john_doe" / "claims.json").read_text(encoding="utf-8"))


def reasons(text):
    return {n: r for n, _, r in claimcheck.check(text, CFG)}


class ExampleResumes(unittest.TestCase):
    def test_the_clean_resume_passes_every_line(self):
        res = claimcheck.check((ROOT / "examples" / "john_doe" / "resume.md").read_text(encoding="utf-8"), CFG)
        self.assertTrue(res)
        self.assertEqual([r for r in res if r[2]], [])

    def test_the_blocked_resume_is_blocked_for_each_planted_reason(self):
        text = (ROOT / "examples" / "john_doe" / "resume_blocked.md").read_text(encoding="utf-8")
        found = " | ".join(x for _, _, rs in claimcheck.check(text, CFG) for x in rs)
        for expected in ("title not held", "figure 600", "never-claim: managed a team",
                         "names python", "cites no claim"):
            self.assertIn(expected, found)


class Rules(unittest.TestCase):
    def test_a_figure_must_come_from_a_cited_claim(self):
        self.assertEqual(reasons("- Rolled Coupa out to 420 requesters. [coupa-rollout]"), {1: []})
        self.assertIn("figure 9 is not in the cited claim(s)",
                      reasons("- Rolled Coupa out to 9 business units. [coupa-rollout]")[1])

    def test_citing_a_claim_does_not_lend_another_claims_figures(self):
        # 9 and 6 belong to the close program, not the Coupa rollout
        self.assertTrue(reasons("- Cut the close from 9 days to 6. [coupa-rollout]")[1])

    def test_a_system_the_claims_never_mention_is_blocked(self):
        self.assertIn("names snowflake, which the cited claim(s) don't support",
                      reasons("- Built SQL reporting in Snowflake. [reporting]")[1])

    def test_never_claims_match_whole_words_only(self):
        self.assertIn("never-claim: hired", reasons("- Hired four analysts. [backlog]")[1])
        self.assertEqual(reasons("- Prioritized the backlog for the finance systems team. [backlog]")[1], [])

    def test_an_unknown_citation_is_blocked(self):
        self.assertIn("cites unknown claim(s): made-up", reasons("- Did a thing. [made-up]")[1])

    def test_a_role_heading_must_be_a_title_held(self):
        self.assertEqual(reasons("### Product Owner, Finance Systems · Northwind Traders · 2021")[1], [])
        self.assertTrue(reasons("### Director of Finance Systems · Northwind Traders · 2021")[1])

    def test_the_command_exits_nonzero_when_anything_is_blocked(self):
        import contextlib, io, sys
        saved = sys.argv
        try:
            for name, want in (("resume.md", 0), ("resume_blocked.md", 1)):
                sys.argv = ["claimcheck.py", str(ROOT / "examples" / "john_doe" / name)]
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(claimcheck.main(), want, name)
        finally:
            sys.argv = saved


if __name__ == "__main__":
    unittest.main()
