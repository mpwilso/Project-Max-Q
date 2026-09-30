"""Regression tests for defects a first run from a fresh clone hits.

A false onsite flag from a bare "to be in", config files that die with a traceback on a BOM or a typo,
a --targets path read only from the repo root, fitscore and claimcheck tracebacks on a missing
argument or file, fitscore pinned to the example profile, a half-year gap printed as +0, a full sweep
silent for many minutes, the eval rewriting a tracked report with only a new date, raw tuples in
--selftest, an empty NOISY heading in --yield, and a privacy hook that refuses without saying why.
Offline; every write goes to a temp directory."""
import contextlib, io, json, os, subprocess, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

from tests.stubs import ROOT, sweep, gates
import claimcheck
import fitscore
from evals import run_fit_eval as ev
from tools import privacy_scan

G = gates()
PY = sys.executable


def run_cli(*args, cwd=ROOT):
    return subprocess.run([PY, *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                          env={**os.environ, "PYTHONIOENCODING": "utf-8"})


class OnsiteTermsToBeIn(unittest.TestCase):
    """"to be in" flags only when a workplace follows it."""

    def test_false_positives_do_not_flag(self):
        for body in (
            "We expect every teammate to be intellectually curious, drawn to tinkering and discovery.",
            "We expect candidates to be incredibly strong across all of the following areas.",
            "We expect the base salary for this position to be in the range of $137,800 to $170,000.",
            "You should expect to be in the room for the technical parts of both the sale and the renewal.",
            "Required skills: experience or desire to be in product management.",
            "A degree is expected to be in computer science, engineering or a related field.",
        ):
            with self.subTest(body=body):
                self.assertIsNone(sweep.onsite_terms(body))

    def test_true_positives_still_flag(self):
        for body in (
            "We expect all staff to be in one of our offices at least 25% of the time.",
            "We require you to be in person Tuesdays and Thursdays.",
            "You are expected to be in the office at least three days per week.",
            "We expect the team to be in our Springfield office five days a week.",
            "Employees are required to be in or near an office frequently.",
            "This role is required to be in Springfield, IL.",
            "You are expected to be in-office Monday, Wednesday, and Friday.",
            "We expect you to be in-person at our HQ about three days a week.",
            "Staff are expected to be in the office 3 days/week.",
            "You are expected to be in office 3x/week.",
        ):
            with self.subTest(body=body):
                self.assertIsNotNone(sweep.onsite_terms(body))

    def test_a_remote_listing_is_not_flagged_on_a_culture_line(self):
        r = sweep.norm("Acme", "greenhouse", 1, "Senior Product Manager", "Remote, United States",
                       "https://jobs.example/acme/1", None,
                       "We expect every teammate to be intellectually curious. 5+ years of experience.")
        self.assertFalse([x for x in sweep.gate(r, G)[1] if "ONSITE TERMS" in x])


class ConfigLoading(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="maxq_cfg_"))

    def _exit(self, path):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as cm:
            sweep.load_config(path)
        return cm.exception.code, err.getvalue()

    def test_a_bom_is_read(self):
        p = self.tmp / "gates.json"
        p.write_bytes(b"\xef\xbb\xbf" + json.dumps({"a": 1}).encode())
        self.assertEqual(sweep.load_config(p), {"a": 1})

    def test_a_syntax_error_names_file_line_and_column(self):
        p = self.tmp / "targets.json"
        p.write_text('{"targets": [\n  {"company": "Acme",}\n]}', encoding="utf-8")
        code, err = self._exit(p)
        self.assertEqual(code, 2)
        self.assertIn("targets.json", err)
        self.assertIn("line 2, column", err)
        self.assertEqual(len(err.strip().splitlines()), 1)

    def test_a_missing_file_exits_2(self):
        code, err = self._exit(self.tmp / "nope.json")
        self.assertEqual(code, 2)
        self.assertIn("not found", err)

    def test_a_targets_file_without_a_targets_list_exits_2(self):
        p = self.tmp / "t.json"
        p.write_text("[]", encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as cm:
            sweep.load_targets(p)
        self.assertEqual(cm.exception.code, 2)

    def test_the_cli_prints_one_line_not_a_traceback(self):
        p = self.tmp / "bad.json"
        p.write_text('{"targets": [}', encoding="utf-8")
        r = run_cli(str(ROOT / "sweep.py"), "--targets", str(p), "--show-cadence")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("bad.json: invalid JSON at line 1, column", r.stderr)


class TargetsPath(unittest.TestCase):
    def test_relative_path_is_read_from_the_current_directory_first(self):
        tmp = Path(tempfile.mkdtemp(prefix="maxq_tgt_"))
        (tmp / "mine.json").write_text('{"targets": []}', encoding="utf-8")
        with contextlib.chdir(tmp):
            self.assertEqual(sweep.resolve_targets("mine.json"), tmp / "mine.json")
            self.assertEqual(sweep.load_targets("mine.json"), [])
            # not in the current directory: the repo root's file
            self.assertEqual(sweep.resolve_targets("targets.json"), ROOT / "targets.json")

    def test_an_absolute_path_is_used_as_given(self):
        p = Path(tempfile.gettempdir()) / "x.json"
        self.assertEqual(sweep.resolve_targets(str(p)), p)


class CliArgumentErrors(unittest.TestCase):
    def test_replay_without_a_file_is_an_argument_error(self):
        r = run_cli("fitscore.py", "rank", "--scorer", "replay")
        self.assertEqual(r.returncode, 2)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("--replay", r.stderr)

    def test_make_scorer_refuses_replay_without_a_file(self):
        with self.assertRaises(SystemExit):
            fitscore.make_scorer("replay")

    def test_a_missing_profile_is_an_argument_error(self):
        r = run_cli("fitscore.py", "rank", "--profile", "no/such/profile.md")
        self.assertEqual(r.returncode, 2)
        self.assertIn("--profile: file not found", r.stderr)

    def test_claimcheck_missing_resume_is_one_line_exit_2(self):
        r = run_cli("claimcheck.py", "no_such_resume.md")
        self.assertEqual(r.returncode, 2)
        self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(r.stderr.strip(), "claimcheck: file not found: no_such_resume.md")

    def test_claimcheck_missing_claims_file_exits_2(self):
        err = io.StringIO()
        with mock.patch.object(sys, "argv", ["claimcheck.py", str(ROOT / "examples/john_doe/resume.md"),
                                             "--claims", "no_such_claims.json"]), \
                contextlib.redirect_stderr(err):
            self.assertEqual(claimcheck.main(), 2)
        self.assertIn("no_such_claims.json", err.getvalue())


class ProfileAndRubricFlags(unittest.TestCase):
    def test_make_scorer_passes_them_through(self):
        seen = {}

        class Rec:
            def __init__(self, **kw): seen.update(kw)

        with mock.patch.object(fitscore, "ClaudeScorer", Rec):
            fitscore.make_scorer("claude", profile="p.md", rubric="r.md")
        self.assertEqual(seen, {"profile": "p.md", "rubric": "r.md"})

    def test_the_prompt_reads_them_and_the_cache_is_keyed_apart(self):
        tmp = Path(tempfile.mkdtemp(prefix="maxq_prof_"))
        (tmp / "p.md").write_text("Jane Roe, eight years of payroll systems.", encoding="utf-8")
        (tmp / "r.md").write_text("Score payroll depth first.", encoding="utf-8")
        own = fitscore.ClaudeScorer(client=object(), profile=tmp / "p.md", rubric=tmp / "r.md")
        self.assertIn("Jane Roe", own.system)
        self.assertIn("Score payroll depth first.", own.system)
        self.assertTrue(own.cache_tag)
        self.assertEqual(fitscore.ClaudeScorer(client=object()).cache_tag, "")

    def test_defaults_are_the_example(self):
        self.assertEqual(fitscore.PROFILE, ROOT / "examples" / "john_doe" / "profile.md")
        self.assertEqual(fitscore.RUBRIC, ROOT / "examples" / "john_doe" / "rubric.md")


class YearsGapDisplay(unittest.TestCase):
    def test_a_half_year_gap_is_not_printed_as_zero(self):
        sig = {"years_bar": 6, "years_bar_kind": "required", "years_gap": 0.5, "candidate_years": 5.5,
               "level_up": "", "days_posted": None, "flooded_board": False, "known_contacts": [],
               "label": "HIGH"}
        with mock.patch.object(sweep, "conversion_signals", return_value=sig):
            _, parts = sweep.conversion_read({}, G)
        self.assertEqual(parts[0], "years gap +0.5 (6+ required vs 5.5 held)")


class SweepProgress(unittest.TestCase):
    def test_at_most_one_line_per_interval(self):
        now, lines = [0.0], []
        p = sweep.FetchProgress(10, 4, every=30, clock=lambda: now[0], out=lines.append)
        for i in range(5):
            now[0] += 10
            p(rescue=i % 2 == 1)
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0], "  progress: detail pass fetched 2 of 10; rescue pass fetched 1 of 4 (0m 30s)")

    def test_a_pass_with_nothing_to_do_is_not_named(self):
        lines = []
        p = sweep.FetchProgress(3, 0, every=0, clock=lambda: 0.0, out=lines.append)
        p(rescue=False)
        self.assertEqual(lines, ["  progress: detail pass fetched 1 of 3 (0m 00s)"])

    def test_gate_all_reports_detail_fetches(self):
        ats = "smartrecruiters"
        rows = {}
        for i in range(3):
            r = sweep.norm("Acme", ats, i, "Senior Product Manager", "Remote, United States",
                           f"https://jobs.example/acme/{i}", None, "")
            r["_target"] = {"company": "Acme", "ats": ats, "base": "https://jobs.example/acme"}
            rows[r["key"]] = r

        def fake_detail(target, row):
            row["description"] = "Own the roadmap. 5+ years of experience."

        out = io.StringIO()
        with mock.patch.dict(sweep.DETAIL, {ats: fake_detail}), mock.patch.object(sweep, "PROGRESS_EVERY_S", 0), \
                mock.patch.dict(os.environ, {"MAXQ_SERIAL": "1"}), contextlib.redirect_stdout(out):
            _, stats = sweep.gate_all(rows, {}, G)
        self.assertEqual(stats["fetched"], 3)
        self.assertIn("progress: detail pass fetched 3 of 3", out.getvalue())


class ConsolePassCap(unittest.TestCase):
    def test_console_lists_25_and_points_at_the_report(self):
        rows = {}
        for i in range(30):
            r = sweep.norm("Acme", "greenhouse", i, "Technical Program Manager", "Remote, United States",
                           f"https://jobs.example/acme/{i}", sweep.TODAY.isoformat(), "5+ years of program management.")
            rows[r["key"]] = r
        verd = {k: sweep.gate(r, G) for k, r in rows.items()}
        self.assertTrue(all(v[0] == "PASS" for v in verd.values()), verd)
        tmp = Path(tempfile.mkdtemp(prefix="maxq_cap_"))
        (tmp / "jds").mkdir()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            sweep.write_report(rows, verd, list(rows), {}, {k: sweep.TODAY.isoformat() for k in rows},
                               [("Acme", "greenhouse", "OK", 30)], G, reports_dir=tmp, jds_dir=tmp / "jds")
        console = out.getvalue().split("New and gate-passing (30):", 1)[1]
        self.assertEqual(console.count("https://jobs.example/acme/"), 25)
        self.assertIn(f"... 5 more in the report (SWEEP_REPORT_{sweep.TODAY.isoformat()}.md)", console)
        report = (tmp / f"SWEEP_REPORT_{sweep.TODAY.isoformat()}.md").read_text(encoding="utf-8")
        self.assertEqual(sum(f"https://jobs.example/acme/{i})" in report for i in range(30)), 30)


class EvalReportOnlyOnChange(unittest.TestCase):
    def test_a_new_date_alone_does_not_rewrite(self):
        p = Path(tempfile.mkdtemp(prefix="maxq_eval_")) / "fit_baseline.md"
        a = "# Fit\n\nData: `x.jsonl` (3 labeled postings). Run 2026-01-01.\n\n| Agrees | 50% |\n"
        self.assertTrue(ev.write_if_changed(p, a))
        self.assertFalse(ev.write_if_changed(p, a.replace("2026-01-01", "2026-02-02")))
        self.assertEqual(p.read_text(encoding="utf-8"), a)
        self.assertTrue(ev.write_if_changed(p, a.replace("50%", "60%")))
        self.assertIn("60%", p.read_text(encoding="utf-8"))

    def test_the_tracked_baseline_is_current(self):
        # CI runs the eval; the committed report must already match, or every CI run would differ.
        p = ROOT / "evals" / "results" / "fit_baseline.md"
        items = ev.load(ev.DEFAULT_DATA)
        scorer = fitscore.make_scorer("baseline")
        rows = []
        for it in items:
            res = scorer.score(it)
            rows.append({**it, "pred": res["verdict"], "score": res["score"], "error": None})
        m = ev.metrics([(r["label"], r["pred"]) for r in rows])
        fresh = ev.report(scorer.name, ev.DEFAULT_DATA, rows, m)
        self.assertEqual(ev.RUN_DATE.sub("", fresh), ev.RUN_DATE.sub("", p.read_text(encoding="utf-8")))


class SelftestAndYieldOutput(unittest.TestCase):
    def test_verdicts_read_as_text(self):
        self.assertEqual(sweep.fmt_verdict(("PASS", [])), "PASS")
        self.assertEqual(sweep.fmt_verdict(("FAIL", ["a", "b"])), "FAIL: a; b")

    def test_selftest_prints_no_tuples(self):
        r = run_cli("sweep.py", "--selftest")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("-> PASS", r.stdout)
        self.assertNotIn("-> (", r.stdout)

    def _yield(self, rows):
        snap = {"date": "2026-01-01", "rows": {r["key"]: r for r in rows}}
        out = io.StringIO()
        with mock.patch.object(sweep, "load_json", return_value=snap), \
                mock.patch.object(sweep, "load_scored", return_value={}), contextlib.redirect_stdout(out):
            sweep.yield_report(G, min_rows_noisy=2)
        return out.getvalue()

    def test_no_noisy_heading_without_noisy_boards(self):
        r = sweep.norm("Acme", "greenhouse", 1, "Technical Program Manager", "Remote, United States",
                       "https://jobs.example/acme/1", None, "")
        self.assertNotIn("NOISY", self._yield([r]))

    def test_noisy_heading_when_a_board_is_noisy(self):
        rows = [sweep.norm("Acme", "greenhouse", i, "Account Executive", "Remote, United States",
                           f"https://jobs.example/acme/{i}", None, "") for i in range(3)]
        self.assertIn("NOISY", self._yield(rows))


class PrivacyHookWithoutConfig(unittest.TestCase):
    def test_fails_closed_and_says_what_to_do(self):
        tmp = Path(tempfile.mkdtemp(prefix="maxq_priv_"))
        out = io.StringIO()
        with mock.patch.object(privacy_scan, "ROOT", tmp), \
                mock.patch.object(privacy_scan, "CONFIG", tmp / ".privacy" / "config.json"), \
                mock.patch.object(sys, "argv", ["privacy_scan.py", "--staged"]), contextlib.redirect_stdout(out):
            self.assertEqual(privacy_scan.main(), 2)
        msg = out.getvalue()
        self.assertIn(".privacy/config.json not found", msg)
        self.assertIn('{"patterns":', msg)
        self.assertIn("git config --unset core.hooksPath", msg)


if __name__ == "__main__":
    unittest.main()
