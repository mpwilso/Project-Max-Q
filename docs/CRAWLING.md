# How it reads careers sites

- **It says who it is.** Every request carries the user agent
  `maxq-sweep/0.3.1 (+https://github.com/mpwilso/Project-Max-Q)`.
- **It goes slowly.** It waits 0.15 seconds before every request (0.6 seconds on Eightfold's newer
  sites, which rate-limit hard), and backs off on a 429 or 5xx, honoring `Retry-After`.
- **One request at a time per site.** Requests are grouped by host. Most hosts get one at a time; a few
  large shared job-board hosts (Greenhouse, Ashby, Lever, SmartRecruiters, Workday) get two to five.
- **Public sources.** It reads public job-board APIs and companies' own careers pages, with no login.
  It does not read LinkedIn or job-listing aggregators.
- **robots.txt is checked.** Before the first request to a host, the sweep reads that host's
  `robots.txt` once, with the user agent above, and keeps it for the run. A path it disallows for
  `maxq-sweep` or for `*` is not fetched; the company is reported as NOT READ with the path, in the
  coverage table and in the health block, never as a quiet empty board. What happens when the file
  cannot be fetched, and the switch that turns the check off, are in [LIMITS.md](LIMITS.md).
- **Browser impersonation is off by default.** One reader, Meta's, needs it; the conditions are in
  [LIMITS.md](LIMITS.md).

## What a run reports about coverage

- **Each company's own careers site is read**, never a job-listing site. A few readers live in
  `sweep.py`; the rest are plugins in `adapters/`, each behind one small contract
  ([adapters/README.md](../adapters/README.md)).
- **What it couldn't read is reported**: a company whose site errors is retried once, then reported as
  UNCOVERED. A posting missing from a site that errored is carried forward for up to 14 days, not
  closed. A read that comes back under half its usual size is treated as cut off. A detail page that
  comes back with no description counts as a failed read.
- **A health block opens every report**: the full funnel, unread companies, big count drops, and a pass
  volume that fell sharply, overall and per kind of careers site, so one site type changing its format
  cannot hide inside normal totals.
