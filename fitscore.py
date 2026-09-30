"""fitscore.py - score gate-passing postings for fit against a candidate profile, and rank them.

The score SORTS the list; it never removes anything from it. Every posting that passed the gates is
still shown, and a person still decides. Three scorers share one interface:

    baseline   the keyword lane hint from gates.json (`prerank`) plus the gate verdict. Offline, free.
    claude     Claude reads the profile, the rubric and the posting and returns a structured score.
               Needs `pip install -r requirements-ai.txt` and an Anthropic API key.
    claude-code  the same prompt through the Claude Code CLI (`claude -p`) on a Claude subscription,
               with no API account.
    replay     answers recorded from an earlier run (evals/recorded/*.json), so an evaluation can be
               re-run without paying for it again.

    python fitscore.py rank                       # baseline ranking of the last sweep's passes
    python fitscore.py rank --scorer claude       # the same, scored by Claude (cached per posting)
    python fitscore.py rank --scorer claude --profile me/profile.md --rubric me/rubric.md   # your own

How well each scorer agrees with a person is measured by evals/run_fit_eval.py, not assumed.
"""
import argparse, hashlib, json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROFILE = ROOT / "examples" / "john_doe" / "profile.md"
RUBRIC = ROOT / "examples" / "john_doe" / "rubric.md"
CACHE = ROOT / "data" / "fit_scores.json"

MODEL = "claude-opus-5"
VERDICTS = ("apply", "maybe", "skip")
APPLY_AT, MAYBE_AT = 75, 55                      # the rubric's verdict thresholds
AREAS = {"role_match": 30, "skills": 25, "seniority": 20, "domain": 15, "logistics": 10}

# The answer Claude must return. Structured outputs guarantee valid JSON of this shape; the numbers are
# still checked in code (validate) because the schema cannot express the per-area maximums.
SCHEMA = {
    "type": "object",
    "properties": {
        "areas": {"type": "object",
                  "properties": {k: {"type": "integer"} for k in AREAS},
                  "required": list(AREAS), "additionalProperties": False},
        "score": {"type": "integer"},
        "strengths": {"type": "array", "items": {"type": "string"}},
        "gaps": {"type": "array", "items": {"type": "string"}},
        "dealbreakers": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
    },
    "required": ["areas", "score", "strengths", "gaps", "dealbreakers", "summary"],
    "additionalProperties": False,
}


def verdict_for(score):
    """The verdict comes from the score in code (and any dealbreaker makes it skip; see validate)."""
    return "apply" if score >= APPLY_AT else "maybe" if score >= MAYBE_AT else "skip"


def posting_text(p):
    return (f"Company: {p.get('company', '')}\nTitle: {p.get('title', '')}\n"
            f"Location: {p.get('location', '')}\n\n{p.get('description', '')}").strip()


def validate(raw):
    """Check a scorer's answer and normalize it. Raises ValueError on anything out of range, so a bad
    answer is reported as an error rather than ranked."""
    areas = raw.get("areas") or {}
    for k, cap in AREAS.items():
        v = areas.get(k)
        if not isinstance(v, int) or not 0 <= v <= cap:
            raise ValueError(f"area {k}={v!r} is outside 0..{cap}")
    score = raw.get("score")
    if not isinstance(score, int) or not 0 <= score <= 100:
        raise ValueError(f"score {score!r} is outside 0..100")
    total = sum(areas[k] for k in AREAS)
    if abs(total - score) > 2:                       # the parts must add up to the whole
        raise ValueError(f"score {score} does not match its areas (sum {total})")
    out = {k: raw.get(k) or [] for k in ("strengths", "gaps", "dealbreakers")}
    # A dealbreaker decides the verdict whatever the points say: the first evaluation showed the model
    # naming "onsite in New York, he will not relocate" and still scoring the job 90, because location
    # is only 10 points of the rubric.
    verdict = "skip" if out["dealbreakers"] else verdict_for(score)
    out.update(areas=areas, score=score, verdict=verdict, summary=raw.get("summary", ""))
    return out


class BaselineScorer:
    """The deterministic comparison point: gate verdict plus the prerank lexicon, mapped onto 0..100."""
    name = "baseline"

    def __init__(self, gates=None):
        import sweep                                    # imported here: sweep sets up data/ on import
        self.sweep = sweep
        self.g = gates or json.loads((ROOT / "gates.json").read_text(encoding="utf-8"))

    def score(self, p):
        row = self.sweep.norm(p.get("company", "X"), "eval", p.get("id", "0"), p.get("title"),
                              p.get("location"), "https://example.invalid/", None, p.get("description", ""))
        verdict, reasons = self.sweep.gate(row, self.g)
        hint = self.sweep.prerank(row, self.g) or 0
        if verdict in ("FAIL", "TENURE", "RESCUE-FETCH"):
            s = 20
        else:
            s = max(0, min(100, 50 + 3 * hint))
        return {"score": s, "verdict": verdict_for(s), "areas": {}, "strengths": [], "gaps": reasons,
                "dealbreakers": [], "summary": f"gate {verdict}, lane hint {hint}"}


class ClaudeScorer:
    """Claude scores one posting per request against the profile and rubric.

    The profile and rubric are the same for every posting, so they sit in a cached system prompt and
    only the posting changes per request. Server-side fallbacks are on: if a request is declined, the
    API retries it on a fallback model inside the same call, and a request that is still declined is
    reported, never guessed at."""
    name = "claude"

    def __init__(self, client=None, model=MODEL, effort="medium", profile=PROFILE, rubric=RUBRIC):
        if client is None:
            try:
                import anthropic
            except ImportError:
                sys.exit("the claude scorer needs the SDK: pip install -r requirements-ai.txt")
            client = anthropic.Anthropic()          # key from ANTHROPIC_API_KEY or an `ant auth login` profile
        self.client, self.model, self.effort = client, model, effort
        self.system = (
            "You score how well a job posting fits a candidate. Use only the facts in the candidate "
            "profile; never assume experience it does not state. Follow the rubric exactly, fill every "
            "area, make `score` the sum of the areas, and keep each list item to one short sentence.\n\n"
            f"<profile>\n{Path(profile).read_text(encoding='utf-8')}\n</profile>\n\n"
            f"<rubric>\n{Path(rubric).read_text(encoding='utf-8')}\n</rubric>")
        self.usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0}
        # Cached scores are keyed by posting; a profile or rubric other than the example also keys them
        # by the prompt, so switching profiles never serves another profile's scores.
        example = (Path(profile).resolve(), Path(rubric).resolve()) == (PROFILE.resolve(), RUBRIC.resolve())
        self.cache_tag = "" if example else ":" + hashlib.sha256(self.system.encode("utf-8")).hexdigest()[:12]

    def score(self, p):
        r = self.client.beta.messages.create(
            model=self.model,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            thinking={"type": "adaptive"},
            output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": SCHEMA}},
            system=[{"type": "text", "text": self.system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": f"<posting>\n{posting_text(p)}\n</posting>"}],
        )
        u = getattr(r, "usage", None)
        for k in self.usage:
            self.usage[k] += getattr(u, k, 0) or 0
        if r.stop_reason == "refusal":
            raise ValueError("the model declined to score this posting")
        if r.stop_reason == "max_tokens":
            raise ValueError("the answer was cut off (max_tokens)")
        text = next((b.text for b in r.content if b.type == "text"), None)
        if text is None:
            raise ValueError("no text in the response")
        return validate(json.loads(text))


class ClaudeCodeScorer(ClaudeScorer):
    """The same prompt and schema, run through the Claude Code CLI (`claude -p`) on a Claude
    subscription, so no API account is needed. Each posting is a fresh, locked-down session: no tools,
    no settings or MCP configuration, no saved session, run from an empty temporary folder."""
    name = "claude-code"

    def __init__(self, model=None, runner=None, **kw):
        super().__init__(client=object(), **kw)       # the base class only needs the prompt text
        import shutil
        self.exe = shutil.which("claude")
        if self.exe is None and runner is None:
            sys.exit("the claude-code scorer needs the Claude Code CLI on PATH (`claude`)")
        self.model, self.runner = model, runner
        self.usage = {"equivalent_api_cost_usd": 0.0}

    def command(self, p):
        cmd = [self.exe or "claude", "-p", f"<posting>\n{posting_text(p)}\n</posting>",
               "--output-format", "json", "--no-session-persistence", "--setting-sources", "",
               "--strict-mcp-config", "--tools", "", "--system-prompt", self.system,
               "--json-schema", json.dumps(SCHEMA)]
        return cmd + (["--model", self.model] if self.model else [])

    def score(self, p):
        import subprocess, tempfile
        with tempfile.TemporaryDirectory() as empty:
            run = self.runner or (lambda cmd, cwd: subprocess.run(
                cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", timeout=600))
            done = run(self.command(p), empty)
        try:
            out = json.loads(done.stdout)
        except (json.JSONDecodeError, TypeError):
            raise ValueError(f"claude -p returned no JSON (exit {done.returncode}): {(done.stderr or '')[:200]}")
        if out.get("is_error") or out.get("subtype") != "success":
            raise ValueError(f"claude -p failed: {out.get('subtype')} {str(out.get('result'))[:200]}")
        self.usage["equivalent_api_cost_usd"] += out.get("total_cost_usd") or 0.0
        self.model = self.model or next(iter(out.get("modelUsage") or {}), None)
        if not isinstance(out.get("structured_output"), dict):
            raise ValueError("claude -p returned no structured output")
        return validate(out["structured_output"])


class ReplayScorer:
    """Answers recorded from an earlier run, keyed by posting id."""
    name = "replay"

    def __init__(self, path):
        rec = json.loads(Path(path).read_text(encoding="utf-8"))
        self.recorded = rec["answers"]
        self.source, self.model = rec.get("scorer"), rec.get("model")

    def score(self, p):
        a = self.recorded.get(str(p["id"]))
        if a is None:
            raise ValueError(f"no recorded answer for {p['id']}")
        return validate(a) if a.get("areas") else a


def make_scorer(name, replay=None, profile=PROFILE, rubric=RUBRIC):
    if name == "baseline": return BaselineScorer()
    if name == "claude": return ClaudeScorer(profile=profile, rubric=rubric)
    if name == "claude-code": return ClaudeCodeScorer(profile=profile, rubric=rubric)
    if name == "replay":
        if not replay: raise SystemExit("--scorer replay needs --replay <recorded answers file>")
        return ReplayScorer(replay)
    raise SystemExit(f"unknown scorer {name!r}")


def rank(scorer):
    """Score every gate-passing and review posting in the last snapshot and print them best first.
    Nothing is dropped: a posting that could not be scored is listed at the end with the reason."""
    import sweep
    g = sweep.load_config(ROOT / "gates.json")
    snap = sweep.load_json(sweep.DATA / "latest.json", None)
    if not snap:
        sys.exit("no data/latest.json yet: run a sweep first (see Quick start)")
    live = scorer.name in ("claude", "claude-code")
    cache = sweep.load_json(CACHE, {}) if live else {}
    rows = [r for r in snap["rows"].values() if sweep.gate(r, g)[0] in ("PASS", "REVIEW")]
    scored, failed = [], []
    for r in rows:
        ck = f"{r['key']}:{sweep.jd_hash(r.get('description'))}{getattr(scorer, 'cache_tag', '')}"
        try:
            res = cache.get(ck) or scorer.score({**r, "id": r["key"]})
        except ValueError as e:
            failed.append((r, str(e))); continue
        if live: cache[ck] = res
        scored.append((res, r))
    if live:
        sweep.write_json_atomic(CACHE, cache)
    scored.sort(key=lambda x: -x[0]["score"])
    print(f"{len(scored)} posting(s) ranked by the {scorer.name} scorer; a person decides on every one.\n")
    for res, r in scored:
        print(f"{res['score']:3}  {res['verdict']:5}  {r['company']} - {r['title']}  ({r['location']})")
        print(f"            {r['url']}")
    for r, why in failed:
        print(f"  ?  unscored  {r['company']} - {r['title']}: {why}")
    if getattr(scorer, "usage", None):
        print(f"\ntokens: {scorer.usage}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    rk = sub.add_parser("rank", help="rank the last sweep's passing postings by fit")
    rk.add_argument("--scorer", default="baseline", choices=["baseline", "claude", "claude-code", "replay"])
    rk.add_argument("--replay", help="recorded answers file for --scorer replay")
    rk.add_argument("--profile", default=str(PROFILE),
                    help="candidate profile the claude scorers read (default: the John Doe example)")
    rk.add_argument("--rubric", default=str(RUBRIC),
                    help="scoring rubric the claude scorers read (default: the John Doe example)")
    a = ap.parse_args()
    if a.cmd == "rank":
        if a.scorer == "replay" and not a.replay:
            rk.error("--scorer replay needs --replay <recorded answers file>")
        for flag, path in (("--replay", a.replay), ("--profile", a.profile), ("--rubric", a.rubric)):
            if path and not Path(path).is_file():
                rk.error(f"{flag}: file not found: {path}")
        rank(make_scorer(a.scorer, a.replay, a.profile, a.rubric))


if __name__ == "__main__":
    main()
