# Configuration and output

How to set up your own search, what the filters do, how to rank against your own profile, and what
the report's terms mean.

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
| `endorsed_state_codes` | Whole US states you would work in, by two-letter code (list the state's full name in `endorsed_onsite_markets` too). A segment with a Canadian province code never counts, because boards also write Canada as "CA" |
| `endorsed_exact_segments` | Locations that endorse only as a whole segment: a bare "New York" is the city, while the words inside "Tarrytown, New York" are the state |
| `blocked_us_markets` | US cities you rule out (also list them in `location_fail_any`) |
| `endorsed_foreign_markets` | Non-US cities you would take, with the knockout note to show |
| `candidate_years_total` | Your years of relevant experience, for the years-gap read |
| `candidate_profile` | Your facts for the application-form check (copy `examples/john_doe/candidate.json`): years, home, places you would move to, work authorization, degree, certifications |
| `reach_years_from` / `years_hard_fail_over` | A required-years bar from this is labelled REACH; over this it fails |
| `never_claim_terms_in_requirements` | Phrases that flag a posting (never fail it), such as "security clearance" |
| `title_hard_exclude` | Title families that fail outright (sales and marketing roles here), each with phrases that spare a title |
| `snapshot_keep_days` | How long dated snapshots in `data/` are kept (0 keeps them all) |
| `prerank` | The lexicon that sorts unscored postings and scores descriptions for rescue |

Two things to know:
- The title filter is tuned for product and program roles. Titles naming an engineer, developer or
  architect fail unless they also name product or program work, or match `title_lane_override`.
- After editing `gates.json`, run `python sweep.py --report-only`. It re-filters the last snapshot
  without a network call and lists what became eligible: that is the edit's blast radius.

Replace `targets.json` with the companies you want. Each entry names an `ats` (the reader to use) and
where the careers site lives; the entries shipped are examples, one per supported kind of site.
The easiest way to add a company is to copy an example of the same site type, change the company's
identifier (usually the name in its careers URL), and run `python adapters/probe.py --company "<name>"`
to confirm it reads. A typo in `gates.json` or a targets file stops the run with one line naming the
file, line and column.

## What the filters do

- **Filters in code**: job title, a location rule that fails closed (a posting passes when its
  location clearly matches, and not otherwise), a required-years limit that understands degree tiers
  and escape clauses, and out-of-scope fields. Every threshold lives in `gates.json`.
- **A second look on the description**: a posting whose title is off still runs every other filter,
  and a strong description flags it for review, never as a pass.
- **The application form is read** on Greenhouse sites, for postings you scored 60 or more: each
  question is checked against the candidate's facts (`examples/john_doe/candidate.json`) and marked
  KNOCKOUT when the truthful answer would likely get the application rejected ("Do you have 7+ years
  ...?", "Are you authorized to work in Canada?", "Do you live within 50 miles of our office?"), ASK
  when it needs the candidate's own answer, or OK. It is a flag shown in `--window`, never a filter or
  a score. The same call fills in a missing pay band. `python sweep.py --form-check [KEY ...]` reads
  forms on demand.
- **The conversion read** is a separate question from fit: not "could I do this job?" but "will this
  application reach a person?" Required years, a level jump, an old posting and a flooded board
  (hundreds of applicants per posting) each count against it.

## Ranking by fit

After the filters, `fitscore.py` ranks what's left by how well each posting fits the profile. The
ranking changes the order and nothing else: every posting that passed is still listed, and a person
still decides.

```
python fitscore.py rank                        # keyword baseline, offline
python fitscore.py rank --scorer claude-code   # Claude via Claude Code, no API key
python fitscore.py rank --scorer claude        # Claude via the API
```

The Claude scorer sends the profile and rubric ([examples/john_doe/](../examples/john_doe/)) with each
posting and gets back a structured answer: points per rubric area, strengths, gaps and dealbreakers.
Code checks that the areas are in range and add up to the score, and sets the verdict from the score,
so the label and the number can never disagree, and any dealbreaker makes the verdict skip. A declined
or cut-off answer is reported, not guessed. The `claude-code` scorer runs each posting as a locked-down
`claude -p` session (no tools, no settings, nothing saved) on a Claude subscription; the `claude` scorer
calls the API and needs `pip install -r requirements-ai.txt` and an API key.

To rank against your own profile instead of John Doe's, write your own profile and rubric (copy the
ones in [examples/john_doe/](../examples/john_doe/)) and pass them in:
`python fitscore.py rank --scorer claude-code --profile me/profile.md --rubric me/rubric.md`.
Your own labeled postings can go in `evals/local/`, which is gitignored, so real job data never leaves
your machine.

## Reading the output: files and terms

- `reports/SWEEP_REPORT_<date>.md`: the report. Section 1 is new postings that passed, section 2 is
  postings flagged for a person to review, section 5 is coverage per company.
- `data/`: the sweep's state (snapshots, first-seen dates, stored job descriptions). Delete it to start
  over.

| Term | Meaning |
|---|---|
| req | One job posting |
| board | One company's careers site |
| lane | The title families you are looking for |
| PASS | Cleared every filter |
| REVIEW | Worth a person's look, never counted as a pass (a rescued title, an IC role under a manager title) |
| LOCATION-POLICY | A US city that is not one of your markets |
| TENURE / REACH | A required-years bar over your limit / above your years but under the limit |
| UNCOVERED | The site, or one posting's detail page, could not be read this run; those postings are carried, not closed |
| carried forward | Kept open because the read that would close it was incomplete |
| conversion read | Separate from fit: how likely an application reaches a person (years gap, level, posting age, crowded board, known contacts) |
