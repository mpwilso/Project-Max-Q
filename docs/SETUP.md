# Setup and running

The three commands a visitor needs are in the README. This page has the rest: Windows, a full run,
probing one company, and the maintainer's hooks.

## Install

Python 3.12. No API key or account is needed.

```
python3 -m venv .venv
source .venv/bin/activate        # Linux and macOS
pip install -r requirements.txt
```

On Windows, create the environment with `py -m venv .venv` and activate it with
`.venv\Scripts\activate` (cmd), `.venv\Scripts\Activate.ps1` (PowerShell) or
`source .venv/Scripts/activate` (Git Bash); the rest is the same. The commands in the README are
written for Linux and macOS.

Every flag, with a line on each: `python sweep.py --help`.

## A first run, and a full run

`python sweep.py --targets examples/targets.quickstart.json` reads three small public careers sites,
takes about a second and writes `reports/SWEEP_REPORT_<date>.md`. Open it; `reports/SAMPLE_REPORT.md`
shows what a larger run looks like.

A plain `python sweep.py` reads every company in `targets.json`. With the 53 examples it used to ship
with, that was about 25,000 postings and 20 minutes or more on the first run, most of it fetching full
job descriptions. The list is now one example per kind of careers site, and a full run of it has not
been timed. The sweep prints a progress line about every 30 seconds, and the console lists the first
25 new matches (the report has all of them). Later runs are faster, because unchanged postings aren't
fetched again.

The sweep does not apply to jobs, log in anywhere, read LinkedIn or job-listing sites, or call an AI
model unless you choose the Claude scorer.

## Probing one company

To test one company's careers site without touching stored state, run
`python adapters/probe.py --company "Notion"`: it prints how many postings it read, how the filters
sorted them, and a few samples. Adding a company and a new kind of site is covered in
[adapters/README.md](../adapters/README.md).

## Tests and hooks

Tests run with `python -m unittest discover -s tests -t .` and on every push
([.github/workflows/tests.yml](../.github/workflows/tests.yml)). `.githooks/` holds the maintainer's
privacy guard: it refuses every commit until `.privacy/config.json` (gitignored) lists the names and
addresses to block, for example `{"patterns": ["your full name"]}`. Create that file before running
`git config core.hooksPath .githooks`, or leave the hooks off.
