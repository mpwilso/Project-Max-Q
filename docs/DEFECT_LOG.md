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

### 10. Several scores recorded, one kept
- **Symptom:** a batch of `--set-score` flags in one command reported success, and only the last score
  was in the ledger.
- **Cause:** a single-valued command-line option; each repeat overwrote the one before, without a word.
- **Fix:** the flag repeats. Every entry is parsed before any is written, so one malformed entry or a
  key given twice writes nothing. A hand-typed Conversion label that disagrees with the computed
  signals is stored as given and named in a warning.
- **Pinned by:** `tests/test_sweep.py::SetScore` (`test_every_repeated_flag_is_stored`,
  `test_one_bad_entry_writes_nothing`).

### 11. "CA" meant Canada
- **Symptom:** endorsing California by its state code would also have endorsed "Montreal, QC, CA".
- **Cause:** boards write both California and Canada as "CA", and the US check already reads uppercase
  two-letter codes as states.
- **Fix:** a segment carrying a Canadian province code, the word Canada, or an endorsed foreign market
  never endorses on a state code. The same change added whole-segment matching, so "New York" can mean
  the city without endorsing "Tarrytown, New York".
- **Pinned by:** `tests/test_sweep.py::StateCodeMarkets`.

### 12. A one-line edit that rewrote the whole file
- **Symptom:** commits where every line of a file changed: line endings flipped from CRLF to LF, or a
  JSON file re-serialized with a different indent. The real change was one line, buried.
- **Cause:** the coding agent edited with `sed -i` under Git Bash, or loaded and dumped JSON with default
  settings. A rule against it in the agent's instructions did not stop it recurring.
- **Fix:** a pre-commit check compares each staged file with HEAD and refuses a line-ending flip or a
  JSON file where most lines changed. An environment variable overrides it, and only the maintainer sets it.
- **Pinned by:** `tests/test_guard_rewrite.py`.

### 13. Postings dated tomorrow
- **Symptom:** a sweep run in the evening stored many postings with tomorrow's date, and their age
  printed as "-1d".
- **Cause:** the date was sliced off UTC timestamps and epoch values (`2030-03-01T01:15:00Z` became
  March 1), while "today" is the local date. West of Greenwich, an evening posting is already
  tomorrow in UTC.
- **Fix:** a timestamp with a clock time and a zone (Z or an offset), and every epoch value, is converted
  to the local date before it is stored. A bare date keeps its date, and so does a timestamp at exactly
  midnight, which is how some boards write a plain date. A posting one day in the future is displayed
  as 0 days old. Adapter tests compute their expected dates the same way, so they hold in any time zone.
- **Pinned by:** `tests/test_discovery_fixes.py::UtcDatesAreLocal`.

### 14. A rejected score batch that had already written half
- **Symptom:** a `--set-score` command with several entries exited with an error, and the entries before
  the bad one were in the ledger anyway.
- **Cause:** parsing checked every entry up front, but an empty verdict on a key with no stored verdict
  only failed when that entry was applied, and each entry was saved as it went.
- **Fix:** every entry is validated before anything is written, all of them are applied to one
  in-memory copy, and the ledger is saved once, only if every entry held.
- **Pinned by:** `tests/test_discovery_fixes.py::SetScoreIsAllOrNothing`.

### 15. One refused page marked a whole employer uncovered
- **Symptom:** an employer whose board read cleanly was reported UNCOVERED, and the same posting's
  page was requested, and refused, on every run.
- **Cause:** the optional description read for an out-of-lane title shared the failure path of a
  required detail read, which treats a failure as a coverage hole.
- **Fix:** a failed optional read is a FAIL for that one posting, with the reason stated. The failure
  is recorded on the row and the read is retried after seven days, and the run summary counts both.
- **Pinned by:** `tests/test_discovery_fixes.py::RescueFetchFailure`.

### 16. Postings a gate edit freed were never owed a score
- **Symptom:** a posting first seen as out of lane, then passing a few days later after the lane
  learned its wording, never appeared in any `--window` and was never scored. It turned up again weeks
  later as a repost, and scored well.
- **Cause:** `--window` aged a posting by the day the sweep first saw it or by its board date. On the day
  a gate edit freed it, it was already older than any short window. The report named it once as newly
  eligible, and a same-day re-run overwrote that report.
- **Fix:** `data/eligible.json` records the first day each posting was PASS or REVIEW; the sweep,
  `--merge` and `--report-only` stamp it, and `--backfill-eligible` builds it from the dated snapshots.
  `--window` lets a posting in on the day it was freed while it was posted 30 days ago or less, and
  marks it FREED.
- **Pinned by:** `tests/test_freed_reqs.py`.

### 17. A founder's career read as the job's years bar
- **Symptom:** every posting from one employer carried an 18+ years REACH label and two Conversion
  drags, though none of them stated a years requirement.
- **Cause:** the About section said a founder "spent 18 years at" a previous employer, and the years
  pattern read it as a bar.
- **Fix:** a third-person "spent N years at" is skipped. "You have spent N years at" is still a bar.
- **Pinned by:** `tests/test_sweep.py::Tenure::test_founder_bio_spent_n_years_at_is_not_a_bar`.

### 18. A preferred-only years figure charged as a required gap
- **Symptom:** a posting whose only years figure, 10+, sat under "Preferred Qualifications" got a
  years-gap drag of two in the Conversion read, and a LOW label.
- **Cause:** with no figure in the required block, the Conversion read fell back to the largest figure
  anywhere in the description, preferred section included.
- **Fix:** the fallback skips a figure that sits under a Preferred section heading. The bare word
  "preferred" mid-sentence ("master's degree preferred") is not a heading, so a bar after it still reads.
- **Pinned by:** `tests/test_sweep.py::Tenure::test_a_bar_only_under_a_preferred_heading_is_no_bar`.

### 19. A comma that joined two countries
- **Symptom:** "Canada - Remote, US - Remote" and "United States & Canada | Remote" failed the location
  gate, though each offers remote work in the US.
- **Cause:** the location is split into segments on bars and slashes, never on commas, so the Canada
  half and the US half sat in one segment and the Canada half blocked it.
- **Fix:** a comma cuts when the text on both sides of it says remote, and an "&" or "and" cuts when one
  side is just the US. "Remote, Canada" and "San Francisco, CA" stay whole.
- **Pinned by:** `tests/test_gate_false_rejections.py::CommaAndJoinedCountries`.

### 20. "Dublin, CA" read as Ireland
- **Symptom:** "Dublin, CA | Remote" hard-failed, and a suburb of an endorsed metro written with no
  state ("GREENWOOD VILLAGE CAMPUS") failed closed.
- **Cause:** "dublin" is on the fail list as a foreign city, and the US check did not count the
  endorsed markets as American.
- **Fix:** `gates.json us_twin_cities` names foreign cities with US namesakes; such a hit is ignored when
  the segment names one of the listed states (uppercase codes only, so "Toronto, CA" still fails). An
  endorsed market counts as a US marker.
- **Pinned by:** `tests/test_gate_false_rejections.py::EndorsedCitiesAndTwins`.

### 21. "New York - Hybrid" was not "New York"
- **Symptom:** with "new york" endorsed as an exact segment, "New York, US, 10003", "New York - Hybrid"
  and "NY New York 30 Hudson Yards" were sent for approval as unendorsed markets.
- **Cause:** the exact-segment test compared the whole segment, so a ZIP, a street address or a
  work-style word made it a different place.
- **Fix:** the comparison strips ZIPs, street addresses, work-style and country words and state codes
  first; anything else left over still keeps the segment out ("New York - Albany"). Optional
  `endorsed_exact_zip_prefixes` lets a segment that is only a ZIP match.
- **Pinned by:** `tests/test_gate_false_rejections.py::ExactSegments`.

### 22. "Virtual" and "U.S. Anywhere" were not remote
- **Symptom:** "Virtual" and "Remote/Road/Home Office" hard-failed, "U.S. Anywhere" was treated as an
  onsite market, and "North America | Remote" failed.
- **Cause:** the remote words were a short list, and the description was consulted only when every
  location segment was exactly a remote marker.
- **Fix:** "anywhere", "virtual" and "home based" are remote words; remote across North America
  includes the US. The description decides any location that names no place once remote and
  placeholder words are removed. A bare "Remote" beside named foreign places ("Singapore | Remote") is
  let through only by an explicit "the role is based in the US", because one board type appends
  "Remote" to every remote posting and boilerplate often mentions US pay or US customers.
- **Pinned by:** `tests/test_gate_false_rejections.py::RemoteSynonyms`, `::BodyCheck`.

### 23. Years bars read from the wrong line
- **Symptom:** postings failed TENURE on 10+ or 12+ years that sat under a preferred heading; a
  two-level posting was gated on its senior level's bar; "in absence of a degree, 12 years" counted as
  the bar; and "operators who have been doing it one way for 30 years" read as a 30-year requirement.
- **Cause:** "Required:", "Additional Qualifications" and "Qualifications We Prefer" were not
  recognised as headings, a preferred-only figure was used as the bar, the lower level's figure was not
  preferred, and a third party's "for N years" looked like the candidate's.
- **Fix:** the headings are recognised; a figure found only under a preferred heading gives a REACH
  label, never TENURE; a dual-level posting uses its lowest level's figure; the no-degree tier and
  "University Degree" (a bachelor's) are tiers; a third-party "for N years" with no "experience" or
  "you" in the sentence is skipped.
- **Pinned by:** `tests/test_gate_false_rejections.py::PreferredYears`, `::DualLevelDegreeAndHistory`.

### 24. "Backend" in a program manager's title
- **Symptom:** "Business Program Manager - Capacity Automation & Backend Tools" and "Group Product
  Manager, AI-Native Data Warehouse" failed on title.
- **Cause:** engineering and physical-operations terms ("backend", "warehouse") match anywhere in a
  title, including where they name the product area.
- **Fix:** `title_exclude_skip_if_pm` skips stack words in a title that names product or program
  management; "data warehouse" joins `title_noise_override`.
- **Pinned by:** `tests/test_gate_false_rejections.py::TitleSubstrings`.

### 25. The pay band in a section nobody read
- **Symptom:** many Lever postings showed no pay band although the posting page stated one, and a
  posting with location "Any State" and country US failed the location gate.
- **Cause:** the closing "additional" section and the structured `salaryRange` were never read, and
  Lever's `country` and Ashby's `addressCountry` were dropped.
- **Fix:** the additional section is part of the description; an annual USD `salaryRange` becomes the
  band; a US country is appended to the location (US only: a bare "CA" would read as California).
- **Pinned by:** `tests/test_board_reads.py::LeverRows`, `::AshbyCountry`.

### 26. A flag that never took effect, and a retry that could not help
- **Symptom:** three board types reported an empty board as an error even when the target was marked
  `empty_ok`, and the end-of-run retry spent 20 seconds plus a full re-read on boards that answer 404.
- **Cause:** those adapters raised before the sweep's empty-board guard could read the flag, and the
  retry pass retried every error.
- **Fix:** the adapters return an empty list for an `empty_ok` target; the retry skips 401, 403, 404,
  410 and empty-board errors, which a second read cannot change.
- **Pinned by:** `test_empty_ok_board_returns_nothing_instead` in `tests/adapters/` (gem, ukg,
  workable), `tests/test_board_reads.py::RetryOnlyWhatCanSucceed`.

### 27. A posting passed with no description at all
- **Symptom:** after a gate edit widened the title list, `--report-only` passed postings whose years,
  contract and people-management checks had never run. Separately, a few detail pages that returned no
  text left their postings as PASS.
- **Cause:** descriptions of out-of-lane postings are stripped to keep snapshots small, and a re-gate
  ran on the empty text; and some detail readers returned the row untouched instead of raising, which
  counted as a successful fetch.
- **Fix:** a stripped row that would pass is REVIEW with a "body not read" note until a live read
  refills it. Every detail call is checked centrally: an empty description is a failed read, counted in
  the detail-failed health warning, and the stored body is used when there is one.
- **Pinned by:** `tests/test_board_reads.py::StrippedBodyIsNotAPass`, `tests/test_empty_detail.py`.

### 28. One vendor broke and the totals looked normal
- **Symptom:** none yet; found by asking what the health block would miss. One board type serves about
  a quarter of the passing postings, so a renamed location field there would fail every row it serves
  and stay under the board-wide 40% warning.
- **Fix:** the health block warns per board type when its passes fall by half (from 30 or more), when
  60% or more of its rows have an empty location, title or expected description, or when its share of
  empty locations rises 30 points.
- **Pinned by:** `tests/test_ats_health.py`.

### 29. A test that failed on a date
- **Symptom:** a Workday carry-forward test started failing with no code change.
- **Cause:** its fixture hard-coded a carried-since date, and once that date was more than 14 days old
  the row closed, as designed.
- **Fix:** the date is relative to today.
- **Pinned by:** `tests/test_workday_close.py`.

### 30. Knockout questions found only at the end of the application
- **Symptom:** a posting that passed every filter and scored well asked, on its application form, "Are
  you legally entitled to work in Canada?" or "Do you have 7+ years ...?", and the truthful answer
  would likely get it rejected. Nothing showed that before the time was spent.
- **Cause:** the list read never sees the form; Greenhouse returns it only from the job-detail call.
- **Fix:** for scored Greenhouse postings the sweep reads the form (once per description version) and
  classifies each question against the candidate's facts in `gates.json candidate_profile`: KNOCKOUT,
  ASK or OK. `--window` prints each knockout. A flag, never a filter or a score. The same call's
  `pay_input_ranges` fills a missing pay band (USD and annual only; the range's title is not trusted).
- **Pinned by:** `tests/test_greenhouse_form.py`.

### 31. The classifier's first live misreads
- **Symptom:** on its first live run over about 300 forms, a sponsorship question that named Canada as
  the exception, a "Certification and confidentiality" attestation, and "Do you acknowledge that this is
  a hybrid role based in ..." were all flagged as knockouts. Porting it here found a fourth: "require
  company sponsorship to retain or extend your work authorization" answered with the first "Yes, I will
  require sponsorship" option.
- **Cause:** keyword rules matched the wrong part of the question: "Canada", "certify", "based in",
  and "work authorization" inside a sponsorship question.
- **Fix:** a "skip this question if ... Canada" clause is not a Canada question; attestation wording is
  never a certification; acknowledging a role's location is the onsite question, not a residence
  question; a question that asks whether sponsorship is required is the sponsorship question.
- **Pinned by:** `tests/test_greenhouse_form.py::LiveMisreads`, `::RealForms::test_years_buckets_and_a_specialty_question`.
