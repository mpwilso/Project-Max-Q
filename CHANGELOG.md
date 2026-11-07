# Changelog

What changed in each release. The full notes are on the [releases page](https://github.com/mpwilso/Project-Max-Q/releases).

## Unreleased

- A second fit-scorer set, `evals/fit/labeled-2.jsonl`: 24 new made-up postings in the first set's shape and label mix, labeled under rules written first in `evals/fit/SET2.md` by a different Claude model, committed before the scorer ran. Scored once on 2026-11-05 with Claude Opus 5.5 through Claude Code: 79% agreement, apply precision 55%, apply recall 100%, no good job buried and no bad job pushed up; the five misses were maybes scored apply. Reports in `evals/results/fit2_baseline.md` and `fit2_claude-code.md`, answers recorded for replay, and CI runs the baseline on both sets. The README's limits and next steps say that no person has labeled either set.
- claimcheck compares a figure's unit and scale, not just its number. Each figure is read as a value, a unit (a currency sign, percent, percentage points, a multiplier or a count) and a scale (thousand, million, billion), and a fact supports it only when it reads as the same three. Before, a figure passed when its bare number was any fact: "saved $3M" passed on a claim that says 3 business units, and "Delivered $420M" passed on an uncited line because 420 is a fact. A bare number in a claim now supports only a bare count. New tests.
- claimcheck no longer blocks years, versions and quarters as metrics: "In 2023 the team", "Since 2019 our systems", "Python 3.12 for all systems", "Promoted in 2022 after two years", "Released 2.0 to all users" and "Q3 2024 release" pass. Filler words before a count reach only a noun on a fixed list (users, teams, records, days and the like), never across a preposition. docs/LIMITS.md says what this leaves unchecked. New tests.
- claimcheck accepts a supported figure in another spelling: "40 percent" for a fact of 40%, "$2.4 million" and "2.4M" for $2.4m, "1800" for 1,800, a range such as 10-20% with both ends checked, and "$12,000, a 30% cut", where the trailing comma used to be read as part of the number. On a bullet, a number inside a supported figure no longer trips the bare-number check. New tests.
- claimcheck reads a line in one pass. The number pattern could backtrack on a run like "1,1,1,...", and a 20,000-character line took 26 seconds; the hedge check's "or so" pattern had the same problem. Both are now linear, and a test holds such a line under half a second.
- `evals/run_fit_eval.py` names a report for its data file when `--out` is not given: labeled.jsonl keeps `fit_<scorer>.md`, labeled-2.jsonl writes `fit2_<scorer>.md`, so set 2 no longer overwrites set 1's reports. `--record` names the file for the scorer, the data file and the day, and never overwrites a recording. A recording stores its data file's path and sha256, and a replay on any other data is refused, where it used to write a 0% report without a word. The two committed recordings now carry the sha256 of the data they were made on, which has not changed since. New tests.
- A second CI job installs requirements-ai.txt and runs `tests/test_sdk_request.py`: the Claude scorer, through the installed anthropic SDK with a dummy key and a mocked HTTP transport, scores one eval posting, and the test checks the request path, the server-side fallback beta header, the body (fallbacks, adaptive thinking, effort, the JSON schema format, the cached system prompt) and the parsed verdict. Nothing calls the network. Without the SDK the test skips, so the main job is unchanged.
- Missing from the 0.4.0 notes: 0.4.0 moved the anthropic SDK requirement in requirements-ai.txt to `>=2.3.0,<3`. The 2.x SDK needs Python 3.10 or later, sends `anthropic-version: 2026-10-21`, is built on httpx2 rather than httpx, and sends the API key as `Authorization: Bearer`. Checked on 2.3.0 and 2.6.0, it sends no `X-Api-Key` header, so a proxy that reads `X-Api-Key` alone needs a change.
- README: the status line names both fit sets, who labeled each and set 2's numbers; the funnel adds up from the sample report (2269 filtered out, then 51, 5, 17 and 22 of the other 95); the test count is checked against the number the test loader finds, by a new test; terms are defined at first use (fails closed, adapters, UNCOVERED, PASS, REACH, apply precision and recall, rubric, AI-tell word, why browser impersonation exists); the claimcheck rules are a list.
- 723 tests.

## 0.4.0 (2026-11-05)

- README restructured to about a third of its length: the plain sentence and status line up top, then What it does, Try it, What was measured, Keeping the resume truthful, Limits, How it was built, The other tools and License. The job search is spoken of in the past tense. Setup detail moved to docs/SETUP.md, the gates.json keys, filters, ranking commands and glossary to docs/CONFIG.md, how the sweep reads sites to docs/CRAWLING.md, and the full limits list to docs/LIMITS.md. A test, tests/test_readme_rules.py, holds the README under 155 shown lines with no dashes, no broken links and no overclaiming words.
- targets.json trimmed from 53 example boards to 35: the three quickstart boards plus one per kind of careers site that had an example, with no tiers. Six readers (directemployers, paradox, radancy_v2, taleo, taleo_tbe, tiktok) have a test but no example board; docs/LIMITS.md says so.
- The user agent now carries the release number (`maxq-sweep/0.4.0` in this release), where it had said 0.1 since the first release.
- The sample report's one quoted posting fragment that read as a typo is trimmed.
- Two page checks in `claimcheck.py`, before a reviewer sees a draft. A figure with a unit ($1.2M, 40%, 3x, 400+ users, 9 working days) that no claim states is blocked and quoted with its unit: on a bullet, the cited claims decide; on a line nobody cites, such as a summary line, every written claim does. A year range is not a figure. A resume with more non-blank lines than `max_lines` in the claims file (50 when unset) is blocked at the line that crosses the budget. The README's next steps drop the page checks. 12 new tests.
- 707 tests.
- robots.txt is checked, on by default: each host's file is read once per run with the tool's own user agent, a path it disallows is not fetched, and the company is reported NOT READ with the path in coverage and in the health block. A file that cannot be fetched allows, and the host is noted. `--ignore-robots` or `MAXQ_IGNORE_ROBOTS=1` turns the check off. 19 new tests.
- The Meta careers reader (Chrome user agent, Sec-Fetch headers, scraped LSD token, pinned GraphQL doc_id) moved out of `sweep.py` into `adapters/metacareers.py`, under the plugin contract and behind the same `--allow-browser-impersonation` opt-in. Same `ats` key, same req keys, same behaviour; plugins now get `h.browser_impersonation_allowed`, `h.OptInRequired`, `h.OPT_IN_HINT` and `h.robots_check`. A fixture-backed adapter test on made-up data.
- README: the limits that were spread across How it reads sites and the eval section (browser impersonation, the robots.txt conditions, the one 24-posting labeled set, Linux and macOS first) are gathered into one Limits section near the end.
- A `</posting>` in a posting's own text can no longer end the prompt block: the scorer disarms it before the text goes to the model, for both the API and the Claude Code paths. New test.
- The API scorer's default model is `claude-opus-5-5`, the model the recorded eval ran on.
- CLAUDE.md for agents working here, this changelog, the owner's name in LICENSE, a `--help` pointer in Quick start, the README's line count for sweep.py, and the sample report's dashes and paths as the current code prints them.

## 0.3.1 (2026-10-05)

- Reqs freed by a gate edit now reach `--window`: `data/eligible.json` records each req's first eligible day, and freed reqs are marked FREED. `--backfill-eligible` builds the ledger from existing snapshots.
- A company bio ("Our founder spent 18 years at ...") is no longer read as a years requirement, and a figure under a Preferred qualifications heading no longer counts as a required bar.
- `--set-score-file FILE.jsonl` records a batch of scores from a file, all or nothing.
- 471 tests. Defect log entries 16 to 18.

## 0.3.0 (2026-09-30)

- The logo, animated light and dark, regenerated by `docs/brand/build_brand.py`.
- A TikTok careers reader, tested on made-up data.
- `fitscore.py --profile` and `--rubric`, so ranking works against your own profile.
- A progress line about every 30 seconds during long sweeps.
- Fixes: posting dates use the local date; `--set-score` writes all entries or none; one job page that refuses to load fails that posting, not the whole company; company names match on whole words; a country-code location is not a US city; "average 15+ years" is not a years requirement; clear errors instead of tracebacks in `fitscore.py` and `claimcheck.py`; adapter tests pass in any time zone.
- README rework and defect log entries 13 to 15.

## 0.2.1 (2026-09-28)

- `--set-score` records every repeated entry, checked before anything is written.
- `endorsed_state_codes` and `endorsed_exact_segments` in gates.json.
- `tools/guard_rewrite.py`, run by the pre-commit hook, refuses a commit where a file's line endings flipped or a JSON file was reformatted top to bottom.
- Defect log entries 10 to 12 and one new rule in docs/AGENT_RULES.md.

## 0.2.0 (2026-09-28)

- AI fit scoring: Claude ranks the short list with a structured answer that code checks before trusting it, through the API or Claude Code on a subscription. Measured on 24 labeled postings: keyword baseline 46%, Claude 67%, and 79% once a code rule makes any named dealbreaker a skip.
- Resume truth checks: every resume line must cite a verified claim, and one blocked line blocks the whole resume.

## 0.1.0 (2026-09-28)

- The discovery half: reads postings from 38 kinds of job board, filters them against a profile, flags borderline ones for a person, and reports its own coverage.
- Built with Claude Code under the rules in docs/AGENT_RULES.md; every real bug became a test.
