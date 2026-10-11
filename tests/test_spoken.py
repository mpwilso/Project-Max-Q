"""Tests for spokencheck.py (claimcheck.py --spoken): the truth check for interview prep, story files and
mock transcripts. Each flaw below is one the check exists to catch, planted in a small fixture."""
import contextlib, io, json, sys, tempfile, unittest
from pathlib import Path

from tests.stubs import ROOT
import claimcheck
import spokencheck as sc

CFG = json.loads((ROOT / "examples" / "john_doe" / "claims.json").read_text(encoding="utf-8"))
EX = ROOT / "examples" / "john_doe"


def run(md, stage=None, base=None):
    """{check: (status, detail)} for a fixture."""
    return {name: (status, detail) for status, name, detail in sc.check_text(md, CFG, stage, base=base)}


def status(md, check, **kw):
    return run(md, **kw).get(check, ("ABSENT", ""))[0]


def doc(body, traces="tenure; erp-owner; coupa-rollout; close-program; backlog"):
    return f"<!--\nid: fixture\ntraces: {traces}\n-->\n# Fixture\n\n{body}\n"


class Examples(unittest.TestCase):
    def test_the_story_bank_and_both_prep_files_pass_with_no_warning(self):
        for target in (EX / "stories", EX / "prep"):
            for f in sc.files_for(target):
                res = sc.check_file(f, CFG)
                bad = [r for r in res if r[0] in ("FAIL", "WARN")]
                self.assertEqual(bad, [], f.name)

    def test_index_and_readme_are_skipped_in_a_directory(self):
        names = [f.name for f in sc.files_for(EX / "stories")]
        self.assertNotIn("INDEX.md", names)
        self.assertIn("intro.md", names)
        self.assertIn("landmines.md", names)

    def test_the_command_exits_0_clean_1_on_a_fail_2_on_a_missing_path(self):
        saved = sys.argv
        try:
            with tempfile.TemporaryDirectory() as d:
                bad = Path(d) / "bad.prep.md"
                bad.write_text(doc("## Tell me about your team\nI hired four analysts for the close.\n"), encoding="utf-8")
                for args, want in (([str(EX / "prep")], 0), ([str(bad)], 1), (["no/such/prep.md"], 2)):
                    sys.argv = ["claimcheck.py", "--spoken", *args]
                    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                        self.assertEqual(claimcheck.main(), want, args)
        finally:
            sys.argv = saved


class NeverClaimsAloud(unittest.TestCase):
    def test_a_never_claim_said_aloud_fails(self):
        r = run(doc("## Tell me about your team\nI hired four analysts for the close.\n"))
        self.assertEqual(r["never_claims"][0], "FAIL")
        self.assertIn('"hired"', r["never_claims"][1])
        self.assertEqual(r["people_management"][0], "FAIL")

    def test_the_truthful_no_passes(self):
        md = doc("## Do you manage people?\nNo direct reports, and I've never hired anyone. "
                 "I'm not certified, and I haven't worked in SAP.\n")
        r = run(md)
        self.assertEqual(r["never_claims"][0], "PASS", r["never_claims"][1])
        self.assertEqual(r["people_management"][0], "PASS", r["people_management"][1])

    def test_a_certification_worked_toward_is_not_one_held(self):
        self.assertEqual(status(doc("## Certifications?\nI'm working toward a certification in NetSuite.\n"),
                                "never_claims"), "PASS")


class PeopleManagement(unittest.TestCase):
    def test_management_said_from_either_end_fails(self):
        for line in ("Eight analysts reported to me during the rollout.", "I met with my reports every week.",
                     "I led a pod of 4 on the close.", "I managed a pod for the cutover.",
                     "I supervised four developers.", "I had a team of six."):
            self.assertEqual(status(doc(f"## Team\n{line}\n"), "people_management"), "FAIL", line)

    def test_negated_sentences_pass(self):
        for line in ("Nobody reports to me.", "I have no direct reports.", "I don't run a pod."):
            self.assertEqual(status(doc(f"## Team\n{line}\n"), "people_management"), "PASS", line)


class HeldTitles(unittest.TestCase):
    def test_a_title_not_held_fails_and_naming_the_posting_passes(self):
        self.assertEqual(status(doc("## Background\nAs Senior Product Manager I owned the ERP.\n"), "held_titles"), "FAIL")
        self.assertEqual(status(doc("## Why this?\nThe Product Manager role is the work I do now.\n"), "held_titles"), "PASS")

    def test_case_decides_whether_it_is_a_title(self):
        self.assertEqual(status(doc("## Background\nI think like a product manager about the backlog.\n"), "held_titles"), "PASS")


class EstimatesKeepTheirWord(unittest.TestCase):
    def test_an_estimate_without_its_word_fails(self):
        md = doc("## Impact\nThe close freed 120 analyst hours a month.\n", traces="close-hours")
        r = run(md)
        self.assertEqual(r["spoken_estimate"][0], "FAIL")
        self.assertIn("close-hours", r["spoken_estimate"][1])

    def test_the_word_must_be_in_the_same_sentence(self):
        md = doc("## Impact\nThis is an estimate. The close freed 120 analyst hours a month.\n", traces="close-hours")
        self.assertEqual(status(md, "spoken_estimate"), "FAIL")
        md = doc("## Impact\nBy my estimate the close freed 120 analyst hours a month.\n", traces="close-hours")
        self.assertEqual(status(md, "spoken_estimate"), "PASS")


class Volunteered(unittest.TestCase):
    ROLLBACK = "One cutover was rolled back after 2 days, and I fixed it."

    def test_an_interview_only_claim_in_the_intro_warns(self):
        md = doc(f"## Tell me about yourself\nI own our NetSuite ERP. {self.ROLLBACK}\n", traces="erp-owner; cutover-rollback")
        r = run(md)
        self.assertEqual(r["volunteered"][0], "WARN")
        self.assertIn("cutover-rollback", r["volunteered"][1])

    def test_the_same_line_when_asked_passes(self):
        md = doc(f"## Tell me about a failure\n{self.ROLLBACK}\n", traces="cutover-rollback")
        self.assertEqual(status(md, "volunteered"), "PASS")
        self.assertIn("only when asked", run(md)["voice"][1])

    def test_short_and_full_under_an_intro_title_are_the_intro(self):
        md = ("<!--\ntraces: cutover-rollback\n-->\n# Tell me about yourself\n\n## Short\n"
              f"{self.ROLLBACK}\n\n## Full (STARR)\nI own the ERP.\n")
        self.assertEqual(status(md, "volunteered"), "WARN")


class FigureTrace(unittest.TestCase):
    def test_a_figure_no_claim_states_fails(self):
        r = run(doc("## Impact\nI cut the close time by 40 percent.\n", traces="close-program"))
        self.assertEqual(r["figure_trace"][0], "FAIL")
        self.assertIn(": 40 ", r["figure_trace"][1])

    def test_a_figure_from_a_claim_the_answer_does_not_cite_warns(self):
        r = run(doc("## Impact\nThe close went from 9 working days to 6 for 420 people.\n", traces="close-program"))
        self.assertEqual(r["figure_trace"][0], "PASS")
        self.assertEqual(r["figure_cites"][0], "WARN")
        self.assertIn("420", r["figure_cites"][1])

    def test_a_section_cite_adds_a_claim_for_that_answer_only(self):
        md = doc("## Rollout\n<!-- cites: coupa-rollout -->\nI moved 420 requesters.\n\n## Close\nAnd 420 more.\n",
                 traces="close-program")
        r = run(md)
        self.assertIn('"Close": 420', r["figure_cites"][1])
        self.assertNotIn('"Rollout"', r["figure_cites"][1])

    def test_a_citation_to_a_claim_that_does_not_exist_fails(self):
        r = run(doc("## Impact\nI did it.\n", traces="close-program; made-up"))
        self.assertEqual(r["figure_trace"][0], "FAIL")
        self.assertIn("made-up", r["figure_trace"][1])

    def test_spelled_numbers_with_a_unit_are_figures(self):
        self.assertEqual(status(doc("## Impact\nIt took eleven weeks.\n", traces="close-program"), "figure_trace"), "FAIL")
        self.assertEqual(status(doc("## Impact\nTwo angles matter here.\n", traces="close-program"), "figure_trace"), "PASS")

    def test_years_and_digits_inside_words_are_not_figures(self):
        md = doc("## When\nI joined in 2021, and the Q3 release went out.\n", traces="tenure")
        self.assertEqual(status(md, "figure_trace"), "PASS")


class UntracedOk(unittest.TestCase):
    def test_an_entry_allows_only_its_own_phrase(self):
        md = doc("## Pay\n<!-- untraced-ok: $95K -->\nThe posting lists $95K.\n", traces="tenure")
        self.assertEqual(status(md, "figure_trace"), "PASS")
        md = doc("## Pay\n<!-- untraced-ok: $95K -->\nThe posting lists $95K, and I moved 95+ records.\n", traces="tenure")
        r = run(md)
        self.assertEqual(r["figure_trace"][0], "FAIL")
        self.assertIn("95+", r["figure_trace"][1])

    def test_a_bare_number_entry_never_allows_a_bigger_one(self):
        md = doc("## Pay\n<!-- untraced-ok: 95 -->\nIt was 1,095 records.\n", traces="tenure")
        self.assertEqual(status(md, "figure_trace"), "FAIL")


class Timing(unittest.TestCase):
    def test_a_long_answer_warns_and_names_its_seconds(self):
        r = run(doc("## Rollout\n" + "I moved every requester onto the new system. " * 40 + "\n"))
        self.assertEqual(r["answer_length"][0], "WARN")
        self.assertIn('"Rollout": ', r["answer_length"][1])

    def test_a_screen_holds_answers_to_30_seconds_but_not_the_intro(self):
        body = "I moved every requester onto the new system step by step. " * 8   # 88 words, 35 seconds
        md = doc(f"## Rollout\n{body}\n\n## Tell me about yourself\n{body}\n")
        self.assertEqual(status(md, "answer_length"), "INFO")
        r = run(md, stage="screen")
        self.assertEqual(r["answer_length"][0], "WARN")
        self.assertIn('"Rollout"', r["answer_length"][1].split(" (over")[0])
        self.assertNotIn('"Tell me about yourself"', r["answer_length"][1].split(" (over")[0])

    def test_a_heading_note_after_an_answer_is_not_counted_in_it(self):
        notes = "Background reading for the round, not said aloud. " * 60
        md = doc(f"## Rollout\nI led the rollout to 420 requesters.\n\n### Notes\n{notes} 999 widgets.\n",
                 traces="coupa-rollout")
        r = run(md)
        self.assertEqual(r["answer_length"][0], "INFO")
        self.assertEqual(r["figure_trace"][0], "PASS")
        self.assertIn('"Rollout" 3s', r["answer_length"][1])


class Voice(unittest.TestCase):
    def test_an_answer_that_leans_on_we_warns(self):
        we = ("We planned the rollout, we trained the requesters, and we ran the cutover together. "
              "Our team owned the checklist and we kept it current, so our go-lives were calm and we hit our dates. ")
        self.assertEqual(status(doc(f"## Rollout\n{we * 2}\n"), "i_not_we"), "WARN")

    def test_ai_tell_words_warn(self):
        self.assertEqual(status(doc("## Rollout\nI leveraged the checklist.\n"), "ai_tells"), "WARN")

    def test_a_system_no_claim_mentions_warns_unless_negated(self):
        self.assertEqual(status(doc("## Reporting\nI built the dashboards in Tableau.\n"), "unsupported_system"), "WARN")
        self.assertEqual(status(doc("## Reporting\nI haven't used Tableau.\n"), "unsupported_system"), "PASS")


class FileConvention(unittest.TestCase):
    def test_a_file_with_no_answer_heading_fails(self):
        self.assertEqual(status("Just some notes.\n", "answers_parsed"), "FAIL")

    def test_a_done_marker_drops_warnings_and_keeps_failures(self):
        md = "<!-- done: debriefed 2030-11-06 -->\n" + doc("## Rollout\nI leveraged it. I hired four analysts.\n")
        res = sc.check_text(md, CFG)
        self.assertFalse([r for r in res if r[0] == "WARN"])
        self.assertIn(("FAIL", "never_claims"), [(r[0], r[1]) for r in res])
        self.assertTrue(any(r[1] == "done" and "ai_tells" in r[2] for r in res))

    def test_an_empty_traces_line_does_not_run_into_the_next(self):
        md = "<!--\ntraces:\nsources: close-program\n-->\n## Impact\nI ran the close.\n"
        self.assertEqual(sc.parse(md)["traces"], [])

    def test_resume_coverage_lists_page_figures_the_prep_never_says(self):
        md = "<!--\ntraces: close-program\nresume: ../resume.md\n-->\n## Impact\nThe close went from 9 working days to 6.\n"
        r = run(md, base=EX / "prep")
        self.assertEqual(r["resume_coverage"][0], "INFO")
        self.assertIn("420", r["resume_coverage"][1])


class MockTranscripts(unittest.TestCase):
    """A transcript carries the pack's header, so an answer is held to the pack's traces. Without it, any
    figure some claim happens to state passes, which is how a number said by coincidence slips through."""
    ANSWER = "## How long did the fix take?\nThe unit went live 5 weeks later.\n"

    def test_without_the_header_a_coincidental_figure_passes_quietly(self):
        r = run("# Mock\n\n" + self.ANSWER)
        self.assertEqual(r["figure_cites"][0], "PASS")
        self.assertEqual(r["uncited"][0], "INFO")

    def test_with_the_packs_header_it_is_flagged(self):
        head = (EX / "prep" / "ExampleCo_PM.prep.md").read_text(encoding="utf-8").split("-->")[0] + "-->\n"
        r = run(head + "# Mock\n\n" + self.ANSWER)
        self.assertEqual(r["figure_cites"][0], "WARN")


if __name__ == "__main__":
    unittest.main()
