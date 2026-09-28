# Defect log

Real defects found while running maxq against live job boards, and the test that now pins each one.
The rule the project runs under: a defect found in real data gets a regression test in the same
session, so it cannot come back quietly. This is a selection; the test suite has the rest.

Each entry: what it looked like, why it happened, what changed, and where it is pinned.

### 1. A quiet day that was really a bug
- **Symptom:** a sweep returned far fewer new postings than usual, and it read like a slow day.
- **Cause:** on separate occasions, a stale cache and a filter bug dropped rows before they were counted.
- **Fix:** every sweep reads every employer live by default, and each run prints a health block (the
  full funnel, uncovered employers, count drops of 50% or more, a pass volume that fell 40% or more).
  A small result is treated as a suspected defect until the funnel explains it.
- **Pinned by:** `tests/test_sweep.py::HealthWarnings`.

### 2. An empty answer reported as "no jobs"
- **Symptom:** a board answered HTTP 200 with an empty list, and the employer showed as read, with zero
  openings.
- **Cause:** "200 with nothing in it" was treated as proof of an empty board.
- **Fix:** a verified employer that returns zero rows is reported as an error (UNCOVERED), never as OK.
- **Pinned by:** `tests/adapters/test_apple.py::test_silent_zero_raises`.

### 3. A truncated read recorded as a complete one
- **Symptom:** one board sometimes answered a page in the middle of the results with an empty page, and
  the walk stopped there, recording a fraction of the board as all of it.
- **Cause:** a stop-on-empty-page rule.
- **Fix:** the walk stops on the board's own total, retries an empty page inside that total, and raises
  if it still comes back short. Postings missing from a read that is less than half its usual size are
  carried for a few days, not closed.
- **Pinned by:** `test_empty_page_inside_total_raises_not_truncates`, `test_transient_empty_page_is_retried`.

### 4. A location check that let everything through
- **Symptom:** postings in any country passed the location gate.
- **Cause:** a truthiness bug: an empty match result was read as "no reason to fail".
- **Fix:** the location gate is fail-closed. A posting passes only on a positive marker (remote in the
  US, a US state or city, an endorsed market); an empty or foreign location fails.
- **Pinned by:** `tests/test_sweep.py::LocationGate::test_fail_closed_needs_a_us_marker`.

### 5. "Intern" rejected "Internal Product Manager"
- **Symptom:** in-lane titles were excluded by words they merely contained.
- **Cause:** substring matching on exclude terms.
- **Fix:** title terms match as whole words.
- **Pinned by:** `test_word_boundaries`, `test_interns_new_grads_and_engineer_grade_codes_are_excluded`.

### 6. "2 Locations" failed the location gate
- **Symptom:** hundreds of in-lane postings on one board type failed with a location of "2 Locations".
- **Cause:** the list view shows a placeholder instead of the places.
- **Fix:** a placeholder location earns a detail-page read, and the gate runs on the real locations.
- **Pinned by:** `test_placeholder_location_earns_detail_and_passes`.

### 7. Good postings thrown away on the title alone
- **Symptom:** relevant roles kept turning up outside the sweep, titled in some house dialect.
- **Cause:** the title check was terminal; most postings failed on title before the description was read.
- **Fix:** an out-of-lane title still runs every other gate, and its description is scored against a
  lexicon. A strong description surfaces it as REVIEW, never PASS, so the pass count stays honest.
- **Pinned by:** `tests/test_rescue.py` (`test_out_of_lane_with_a_matching_body_is_rescued_as_review`,
  `test_a_rescue_is_never_a_pass`).

### 8. One board read twice under two names
- **Symptom:** the same postings were counted twice.
- **Cause:** two employer entries pointed at the same board.
- **Fix:** an integrity test over the target list.
- **Pinned by:** `tests/test_targets.py::test_no_board_is_listed_twice`.

### 9. The AI scorer saw the dealbreaker and scored the job 90 anyway
- **Symptom:** the first evaluation of the Claude fit scorer ranked three bad jobs as apply: five days
  onsite in a city the candidate won't move to, a role in Canada, and a six-month contract.
- **Cause:** the model named each problem as a dealbreaker, but the rubric gives location and terms only
  10 of 100 points, so the total stayed high. The judgment was right; the scoring design let it through.
- **Fix:** any dealbreaker makes the verdict skip, in code. Re-measured by replaying the recorded answers:
  agreement 67% to 79%, bad jobs pushed up 3 to 0, with no new model calls.
- **Pinned by:** `tests/test_fitscore.py::Validate::test_a_dealbreaker_makes_the_verdict_skip_whatever_the_score`.
