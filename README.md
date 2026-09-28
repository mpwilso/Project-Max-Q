# Project Max Q

[![tests](https://github.com/mpwilso/Project-Max-Q/actions/workflows/tests.yml/badge.svg)](https://github.com/mpwilso/Project-Max-Q/actions/workflows/tests.yml)

Job hunting at volume is mostly noise. Listing sites are stale and incomplete, and a slow week looks
exactly like a broken search. I built Max Q to fix that for myself: it reads job postings straight from
each company's own careers site, filters out the ones that don't fit, and gives me a short list to
decide on. It never applies to anything. I make every call that matters.

I also built it as an experiment in working with an AI coding agent. Claude Code wrote most of the
code. I wrote the rules it had to follow, decided what it could and couldn't decide on its own, and
held it to one standard: any bug we found in real data got a test before we moved on. There are 389
of those tests now, and they run on every change.

The code is named `maxq` inside the repo. Max Q is the moment a rocket takes the most stress on the
way up; job searching felt about the same.

## What's worth looking at

- **How the AI agent was managed.** The rules it worked under are in
  [docs/AGENT_RULES.md](docs/AGENT_RULES.md), and the real bugs it hit, with the test that now guards
  each one, are in [docs/DEFECT_LOG.md](docs/DEFECT_LOG.md).
- **Designing for the ways things break.** A suspiciously small result is treated as a bug until the
  numbers explain it. A careers site that can't be read gets reported, not quietly skipped. Nothing is
  marked closed unless the whole board was read cleanly.
- **Measuring an AI feature before trusting it.** A Claude-based fit scorer ranks the shortlist, and an
  evaluation harness measures it against postings a person labeled, next to a plain keyword baseline.
  See [Ranking by fit](#ranking-by-fit-and-measuring-it).
- **Keeping the decisions straight.** Code handles the hard filters, the AI sorts and drafts, and a
  person decides and submits. Whether I'm a fit and whether an application will reach a human are
  scored separately, on purpose.
- **Engineering basics done properly.** A plugin setup for adding new job boards, tests built on
  made-up data, automatic checks on every push, and a privacy scan before anything leaves my machine.

## Quick start

Python 3.12. About two minutes to a first report.

```
python -m venv .venv
.venv\Scripts\activate
# (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt

# offline: run sample postings through the filters
python sweep.py --selftest

# offline: the full test suite
python -m unittest discover -s tests -t .

# live: read three small public job boards
python sweep.py --targets examples/targets.quickstart.json
```

The last command writes `reports/SWEEP_REPORT_<date>.md`. Open it; `reports/SAMPLE_REPORT.md` shows
what a larger run looks like. To see one board's postings and verdicts without touching stored state,
run `python adapters/probe.py --company "Notion"`.

## How it works

```mermaid
flowchart TD
    T[targets.json: the companies to read] --> A[38 job board readers]
    A --> G{filters in gates.json}
    G -->|passes| R[report with a health check]
    G -->|title doesn't match,<br/>description does| B[flagged for review,<br/>never counted as a pass]
    B --> R
    G -->|fails| X[counted, not shown]
    R --> AI[AI agent sorts and drafts a fit read]
    AI --> P([a person decides and applies])
```

| Step | Who decides |
|---|---|
| Read boards, apply hard gates, detect coverage gaps | Code (deterministic, tested) |
| Sort the unscored pile, draft a fit assessment | AI agent |
| Which roles are worth an application | Person |
| Submitting anything | Person, always |

What the sweep does on every run:

- **Reads each employer's own board**, never an aggregator. 13 readers live in `sweep.py`; 25 are
  plugins in `adapters/`, each behind one small contract ([adapters/README.md](adapters/README.md)).
- **Applies hard gates in code**: title lane, a fail-closed location policy (a posting passes only on a
  positive location marker), a required-years bar with degree tiers and escape clauses, and
  out-of-scope domains. Every threshold lives in `gates.json`.
- **Rescues on the description**: a posting whose title is outside the lane still runs every other
  gate, and a strong description surfaces it for review, never as a pass.
- **Reports coverage honestly**: an employer that errors is retried once, then reported as UNCOVERED.
  A posting missing from a board that errored is carried forward for up to 14 days, not closed. A read
  that comes back under half its usual size is treated as truncated.
- **Prints a health block**: the full funnel, uncovered employers, big count drops, and a pass volume
  that fell sharply. In the sample run, 2,364 postings from seven employers came down to 22 passes and
  17 for review, with nothing uncovered.

## Make it yours

`gates.json` ships with a fictional candidate, **John Doe**: a product and program manager for finance
and business systems (NetSuite, Coupa, ERP) in Denver, open to remote, with 4.5 years in role. None of
it describes a real person. These are the keys to change first; leave the rest as they are until you
need them.

| Key | What it does |
|---|---|
| `title_include_any` / `title_exclude_any` | Title phrases that put a posting in or out of your lane |
| `title_lane_override` | Phrases that let a title past the engineering-title guard (below) |
| `endorsed_onsite_markets` | Cities you would work in onsite or hybrid |
| `blocked_us_markets` | US cities you rule out (also list them in `location_fail_any`) |
| `endorsed_foreign_markets` | Non-US cities you would take, with the knockout note to show |
| `candidate_years_total` | Your years of relevant experience, for the years-gap read |
| `reach_years_from` / `years_hard_fail_over` | A required-years bar from this is labelled REACH; over this it fails |
| `never_claim_terms_in_requirements` | Phrases that flag a posting (never fail it), such as "security clearance" |
| `prerank` | The lexicon that sorts unscored postings and scores descriptions for rescue |

Two things to know:
- The title gate is tuned for product and program roles. Titles naming an engineer, developer or
  architect fail unless they also name product or program work, or match `title_lane_override`.
- After editing `gates.json`, run `python sweep.py --report-only`. It re-gates the last snapshot
  without a network call and lists what became eligible: that is the edit's blast radius.

Replace `targets.json` with the employers you want. Each entry names an `ats` (the adapter) and where
the board lives; the 53 entries here are examples covering 33 board types.

## Reading the output

- `reports/SWEEP_REPORT_<date>.md`: the report. Section 1 is new gate-passing postings, section 2 is
  postings flagged for a person to review, section 5 is coverage per employer.
- `data/`: the sweep's state (snapshots, first-seen dates, stored job descriptions). Delete it to start
  over.

| Term | Meaning |
|---|---|
| req | One job posting |
| lane | The title families you are looking for |
| PASS | Cleared every gate |
| REVIEW | Worth a person's look, never counted as a pass (a rescued title, an IC role under a manager title) |
| LOCATION-POLICY | A US city that is not one of your markets |
| TENURE / REACH | A required-years bar over your limit / above your years but under the limit |
| UNCOVERED | The board could not be read this run; its postings are carried, not closed |
| carried forward | Kept open because the read that would close it was incomplete |
| conversion read | Separate from fit: how likely an application reaches a human (years gap, level, posting age, crowded board, known contacts) |

## Ranking by fit, and measuring it

After the filters, `fitscore.py` ranks what's left by how well each posting fits the profile. The
ranking only changes the order: every posting that passed is still listed, and a person still decides.

```
python fitscore.py rank                         # keyword baseline, offline
python fitscore.py rank --scorer claude-code    # Claude, through the Claude Code CLI (no API account)
python fitscore.py rank --scorer claude         # Claude, through the API
```

The Claude scorer sends the profile and rubric ([examples/john_doe/](examples/john_doe/)) with each
posting and gets back a structured answer: points per rubric area, strengths, gaps and dealbreakers.
Code checks that the areas are in range and add up to the score, and sets the verdict from the score,
so the label and the number can never disagree, and any dealbreaker makes the verdict skip. A declined
or cut-off answer is reported, not guessed. The `claude-code` scorer runs each posting as a locked-down
`claude -p` session (no tools, no settings, nothing saved) on a Claude subscription; the `claude` scorer
calls the API and needs `pip install -r requirements-ai.txt` and an API key.

Whether it is any good is measured, not assumed. `evals/run_fit_eval.py` scores 24 made-up postings
labeled apply, maybe or skip, including traps: the right title on the wrong job, the right job under an
odd title, a good job in a place he can't work.

| Scorer | Agrees with label | Apply precision | Apply recall | Bad jobs pushed to the top |
|---|---|---|---|---|
| Keyword baseline | 46% | 42% | 83% | 3 of 12 |
| Claude (Opus 5.5), first run | 67% | 50% | 100% | 3 of 12 |
| Claude, plus the dealbreaker rule | 79% | 67% | 100% | 0 of 12 |

The first Claude run caught every good job but pushed three bad ones to the top: five days onsite in
New York, a role in Canada, and a six-month contract. In each case the model had named the problem as a
dealbreaker and still scored the job 78 to 90, because location is only 10 points of the rubric. The
fix went in code, not the prompt: a dealbreaker now makes the verdict skip. The third row replays the
same recorded answers under the new rule, so no model call changed; it also means the rule was chosen
after seeing this set, and a fresh set is the real test.

Two honest caveats. The labels were written against John Doe's profile by the project's AI coding
agent, so this is a consistency check rather than independent ground truth. And the scorer and the
labeler are the same model family. Recorded answers are in [evals/recorded/](evals/recorded/), full
reports in [evals/results/](evals/results/). Your own labeled postings can go in `evals/local/`, which
is gitignored, so real job data never leaves your machine.

## Truth checks on the resume (Phase 2, first slice)

A tailored resume is where an AI assistant is most tempted to embellish. `claimcheck.py` stops that
in code: every bullet must cite a verified claim about the candidate (`[claim-id]`), and a line is
blocked if it states a figure, a system or a title those claims don't support, or anything on the
never-claim list.

```
python claimcheck.py examples/john_doe/resume.md            # every line traces: passes
python claimcheck.py examples/john_doe/resume_blocked.md    # 5 of 6 lines blocked, with reasons
```

The blocked example shows each rule catching something: an inflated number (600 requesters, not
420), people management he never did, a tool none of his claims mention, a title he never held, and a
line with no source at all. One blocked line blocks the whole resume, so the fix is always to change
the wording or add a verified claim, never to talk the check out of it.

## Roadmap

Phase 1, discovery, is done. Phase 2 is the checks between a tailored resume and anything that gets
sent. Done so far: claim tracing (above). Next:
- a second, independently labeled set to confirm the fit scorer's numbers;
- more page checks before any reviewer sees a draft (one page, no hedged or unsupported metrics);
- an independent review pass and a person's approval before a file is released.

## License

MIT. See `LICENSE`.
