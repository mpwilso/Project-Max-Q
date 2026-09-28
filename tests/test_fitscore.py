"""Tests for the fit scorer and its evaluation. Offline: the Claude scorer runs against a stand-in
client that records the request and returns a canned response, so no key or network is needed."""
import contextlib, io, json, tempfile, unittest
from pathlib import Path
from types import SimpleNamespace

from tests.stubs import ROOT, sweep
import fitscore
from evals import run_fit_eval as ev

GOOD = {"areas": {"role_match": 27, "skills": 22, "seniority": 16, "domain": 14, "logistics": 9},
        "score": 88, "strengths": ["NetSuite ownership"], "gaps": [], "dealbreakers": [], "summary": "Strong fit."}


class FakeClient:
    """Stands in for anthropic.Anthropic(): records each request and returns a scripted response."""
    def __init__(self, text=None, stop_reason="end_turn", usage=(1200, 300, 1000)):
        self.calls, self.text, self.stop_reason, self.usage = [], text, stop_reason, usage
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        content = [SimpleNamespace(type="thinking", thinking="")]
        if self.text is not None:
            content.append(SimpleNamespace(type="text", text=self.text))
        i, o, c = self.usage
        return SimpleNamespace(content=content, stop_reason=self.stop_reason,
                               usage=SimpleNamespace(input_tokens=i, output_tokens=o, cache_read_input_tokens=c))


POSTING = {"id": "t1", "company": "Acme Analytics", "title": "Product Manager, Finance Systems",
           "location": "Remote, United States", "description": "Own the NetSuite roadmap."}


class Validate(unittest.TestCase):
    def test_verdict_comes_from_the_score(self):
        self.assertEqual([fitscore.verdict_for(s) for s in (75, 74, 55, 54)], ["apply", "maybe", "maybe", "skip"])
        self.assertEqual(fitscore.validate(GOOD)["verdict"], "apply")

    def test_a_dealbreaker_makes_the_verdict_skip_whatever_the_score(self):
        onsite = dict(GOOD, dealbreakers=["Five days onsite in New York; he will not relocate."])
        res = fitscore.validate(onsite)
        self.assertEqual((res["score"], res["verdict"]), (88, "skip"))

    def test_an_area_over_its_maximum_is_rejected(self):
        bad = json.loads(json.dumps(GOOD)); bad["areas"]["logistics"] = 11
        with self.assertRaises(ValueError): fitscore.validate(bad)

    def test_a_score_that_does_not_match_its_areas_is_rejected(self):
        bad = dict(GOOD, score=60)
        with self.assertRaises(ValueError): fitscore.validate(bad)


class ClaudeRequest(unittest.TestCase):
    def scorer(self, **kw):
        return fitscore.ClaudeScorer(client=FakeClient(**kw))

    def test_the_request_asks_for_structured_output_with_fallbacks_and_a_cached_system_prompt(self):
        s = self.scorer(text=json.dumps(GOOD))
        s.score(POSTING)
        kw = s.client.calls[0]
        self.assertEqual(kw["model"], "claude-opus-5")
        self.assertEqual(kw["fallbacks"], "default")
        self.assertIn("server-side-fallback-2026-07-01", kw["betas"])
        self.assertEqual(kw["output_config"]["format"], {"type": "json_schema", "schema": fitscore.SCHEMA})
        self.assertEqual(kw["system"][0]["cache_control"], {"type": "ephemeral"})
        self.assertIn("John Doe", kw["system"][0]["text"])                 # profile rides in the cached prefix
        self.assertNotIn("temperature", kw)
        self.assertIn("NetSuite roadmap", kw["messages"][0]["content"])     # only the posting varies

    def test_a_good_answer_is_validated_and_usage_is_counted(self):
        s = self.scorer(text=json.dumps(GOOD))
        self.assertEqual(s.score(POSTING)["score"], 88)
        self.assertEqual(s.usage, {"input_tokens": 1200, "output_tokens": 300, "cache_read_input_tokens": 1000})

    def test_a_refusal_is_reported_never_guessed(self):
        with self.assertRaises(ValueError) as cm:
            self.scorer(text=None, stop_reason="refusal").score(POSTING)
        self.assertIn("declined", str(cm.exception))

    def test_a_cut_off_answer_is_an_error(self):
        with self.assertRaises(ValueError):
            self.scorer(text='{"areas":', stop_reason="max_tokens").score(POSTING)


class ClaudeCodeCommand(unittest.TestCase):
    """The claude-code scorer runs `claude -p` locked down and reads its structured output."""
    def run_with(self, payload, rc=0):
        seen = {}
        def runner(cmd, cwd):
            seen["cmd"], seen["cwd"] = cmd, cwd
            return SimpleNamespace(stdout=json.dumps(payload), stderr="", returncode=rc)
        return fitscore.ClaudeCodeScorer(runner=runner), seen

    def test_the_session_is_isolated_and_asks_for_the_schema(self):
        ok = {"is_error": False, "subtype": "success", "structured_output": GOOD,
              "total_cost_usd": 0.04, "modelUsage": {"claude-opus-5-5": {}}}
        s, seen = self.run_with(ok)
        self.assertEqual(s.score(POSTING)["score"], 88)
        cmd = seen["cmd"]
        for flag in ("-p", "--no-session-persistence", "--strict-mcp-config", "--json-schema"):
            self.assertIn(flag, cmd)
        self.assertEqual(cmd[cmd.index("--tools") + 1], "")               # no tools at all
        self.assertEqual(cmd[cmd.index("--setting-sources") + 1], "")     # no user or project settings
        self.assertEqual(json.loads(cmd[cmd.index("--json-schema") + 1]), fitscore.SCHEMA)
        self.assertIn("John Doe", cmd[cmd.index("--system-prompt") + 1])
        self.assertEqual(s.model, "claude-opus-5-5")
        self.assertAlmostEqual(s.usage["equivalent_api_cost_usd"], 0.04)

    def test_a_failed_session_or_missing_output_is_an_error(self):
        for payload in ({"is_error": True, "subtype": "error_during_execution", "result": "boom"},
                        {"is_error": False, "subtype": "success", "result": "text only"}):
            s, _ = self.run_with(payload)
            with self.assertRaises(ValueError):
                s.score(POSTING)

    def test_output_that_is_not_json_is_an_error(self):
        s = fitscore.ClaudeCodeScorer(runner=lambda cmd, cwd: SimpleNamespace(stdout="oops", stderr="", returncode=1))
        with self.assertRaises(ValueError):
            s.score(POSTING)


class Metrics(unittest.TestCase):
    def test_counts_and_costly_errors(self):
        m = ev.metrics([("apply", "apply"), ("apply", "skip"), ("skip", "apply"), ("maybe", "maybe"), ("skip", None)])
        self.assertEqual((m["n"], m["scored"], m["errors"]), (5, 4, 1))
        self.assertEqual(m["agreed"], 2)
        self.assertEqual((m["buried"], m["pushed"]), (1, 1))
        self.assertEqual(m["apply_precision"], 0.5)
        self.assertEqual(m["apply_recall"], 0.5)

    def test_no_apply_predictions_is_not_a_zero_division(self):
        self.assertIsNone(ev.metrics([("skip", "skip")])["apply_precision"])


class LabeledSet(unittest.TestCase):
    def test_the_synthetic_set_is_well_formed(self):
        items = ev.load(ev.DEFAULT_DATA)
        self.assertGreaterEqual(len(items), 20)
        self.assertEqual(len({i["id"] for i in items}), len(items))
        self.assertEqual({i["label"] for i in items}, set(fitscore.VERDICTS))
        for i in items:
            self.assertTrue(i["why"] and i["description"], i["id"])

    def test_the_baseline_scores_every_posting_offline(self):
        s = fitscore.BaselineScorer()
        for i in ev.load(ev.DEFAULT_DATA):
            self.assertIn(s.score(i)["verdict"], fitscore.VERDICTS)

    def test_replay_returns_recorded_answers(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "rec.json"
            p.write_text(json.dumps({"answers": {"t1": GOOD}}), encoding="utf-8")
            r = fitscore.ReplayScorer(p)
            self.assertEqual(r.score(POSTING)["verdict"], "apply")
            with self.assertRaises(ValueError):
                r.score(dict(POSTING, id="missing"))


class RankNeverFilters(unittest.TestCase):
    """The score sorts; it must never make a posting disappear."""
    def test_an_unscorable_posting_is_still_listed(self):
        class Picky:
            name = "picky"
            def score(self, p):
                if "Broken" in p["title"]: raise ValueError("no answer")
                return fitscore.validate(GOOD)
        rows = {}
        for i, title in enumerate(["Product Manager, Finance Systems", "Broken Product Manager"]):
            r = sweep.norm("Acme", "greenhouse", i, title, "Remote, United States", f"https://x/{i}", None,
                           "Own the NetSuite roadmap. 4+ years.")
            rows[r["key"]] = r
        saved = sweep.DATA
        with tempfile.TemporaryDirectory() as d:
            try:
                sweep.DATA = Path(d)
                (Path(d) / "latest.json").write_text(json.dumps({"rows": rows}), encoding="utf-8")
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    fitscore.rank(Picky())
            finally:
                sweep.DATA = saved
        text = out.getvalue()
        self.assertIn("Product Manager, Finance Systems", text)
        self.assertIn("unscored", text)
        self.assertIn("Broken Product Manager", text)


if __name__ == "__main__":
    unittest.main()
