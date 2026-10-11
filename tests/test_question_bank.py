"""tools/question_bank.py: the roll-up a prep pack is checked against before it is used."""
import contextlib, io, json, tempfile, unittest
from pathlib import Path

from tests.stubs import ROOT
from tools import question_bank as qb

ROWS = [
    {"type": "question", "company": "Acme", "question": "Tell me about a time you drove adoption of a new tool.",
     "story_used": "coupa-rollout", "landed": "yes"},
    {"type": "question", "company": "Globex", "question": "How did you drive adoption of the tool you rolled out?",
     "landed": "unsure"},
    {"type": "question", "company": "Acme", "question": "How do you shorten a month-end close?"},
    {"type": "signal", "company": "Acme", "signal": "They want someone who has run a procurement rollout.",
     "use_next": "hm: lead with coupa-rollout"},
]
EXAMPLE = ROOT / "examples" / "john_doe" / "interviews" / "questions.jsonl"


def cli(*args):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = qb.main(list(args))
    return code, buf.getvalue()


class Grouping(unittest.TestCase):
    def test_near_duplicate_wordings_fold_together_most_asked_first(self):
        groups = qb.group_questions(ROWS)
        self.assertEqual(len(groups), 2)
        self.assertEqual(len(groups[0][1]), 2)
        self.assertIn("adoption", groups[0][0])

    def test_signals_are_not_questions(self):
        self.assertTrue(all(r["type"] == "question" for _, rs in qb.group_questions(ROWS) for r in rs))

    def test_a_malformed_line_is_skipped_and_a_missing_file_is_empty(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "q.jsonl"
            p.write_text(json.dumps(ROWS[0]) + "\n{not json\n\n", encoding="utf-8")
            self.assertEqual(len(qb.load(p)), 1)
            self.assertEqual(qb.load(Path(d) / "none.jsonl"), [])


class Check(unittest.TestCase):
    def test_a_pack_without_an_adoption_answer_is_told_so(self):
        prep = "# Pack\n\n## Tell me about yourself\nx\n\n## How do you shorten a close?\ny\n"
        miss = qb.uncovered(prep, ROWS)
        self.assertEqual(len(miss), 1)
        self.assertIn("adoption", miss[0][0])

    def test_a_pack_that_answers_both_is_clean(self):
        prep = "## How did you drive adoption of your tool?\nx\n## How do you shorten a month-end close?\ny\n"
        self.assertEqual(qb.uncovered(prep, ROWS), [])

    def test_the_example_pack_answers_every_question_in_the_example_bank(self):
        code, out = cli("--bank", str(EXAMPLE), "--check", str(ROOT / "examples/john_doe/prep/ExampleCo_PM.prep.md"))
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("0 question(s)"), out)


class Cli(unittest.TestCase):
    def test_company_filter_prints_signals_and_questions(self):
        with tempfile.TemporaryDirectory() as d:
            bank = Path(d) / "q.jsonl"
            bank.write_text("\n".join(json.dumps(r) for r in ROWS), encoding="utf-8")
            _, out = cli("--bank", str(bank), "--company", "acme")
        self.assertIn("run a procurement rollout", out)
        self.assertIn("use next: hm: lead with coupa-rollout", out)
        self.assertIn("Questions (2 asked", out)

    def test_the_example_bank_rolls_up(self):
        _, out = cli("--bank", str(EXAMPLE), "--company", "ExampleCo")
        self.assertIn("Signals (1)", out)
        self.assertIn("Questions (2 asked, 2 distinct)", out)

    def test_an_empty_default_bank_points_at_the_example(self):
        with tempfile.TemporaryDirectory() as d:
            _, out = cli("--bank", str(Path(d) / "none.jsonl"))
        self.assertIn("no entries", out)
        self.assertIn("examples/john_doe/interviews/questions.jsonl", out)


if __name__ == "__main__":
    unittest.main()
