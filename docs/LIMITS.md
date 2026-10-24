# Limits

What the code does under a condition worth knowing, and what the numbers in the README do not show.

- **Browser impersonation is off by default.** Meta's careers site answers requests that look like
  Chrome and no others, and a few Workday sites refuse plain requests. `--allow-browser-impersonation`
  (or `MAXQ_ALLOW_BROWSER_IMPERSONATION=1`) lets the Meta reader send a Chrome user agent and browser
  headers, and with `USE_CURL_CFFI=1` (after `pip install curl_cffi`) sends Workday calls through
  curl_cffi as Chrome. It is off because it hides who is asking. Without it, Meta is reported as skipped
  with that reason, never as a quiet empty board. The Meta reader is the one reader that impersonates a
  browser, and it lives outside the core file, in [adapters/metacareers.py](../adapters/metacareers.py),
  behind the same opt-in.
- **robots.txt is checked, with two conditions.** A `robots.txt` that cannot be fetched (a 404 or a
  connection error) allows every path, as the standard says, and the health block names the host.
  `--ignore-robots` (or `MAXQ_IGNORE_ROBOTS=1`) turns the check off for a run. One reader was written
  around a robots rule before the check existed: the DirectEmployers reader uses the sitemap, not the
  RSS feeds robots.txt rules out.
- **The fit scorer's numbers come from one set of 24 made-up postings.** The labels were written
  against John Doe's profile by the project's AI coding agent, so the measurement is a consistency check
  rather than independent ground truth. The scorer and the labeler are the same model family. The
  dealbreaker rule was chosen after seeing this set, so a fresh set is the real test. The report from
  the first Claude run, before that rule, was not kept; its row in the README is recomputed from the
  recorded answers in [evals/recorded/](../evals/recorded/) with the verdict taken from the score alone.
- **Linux and macOS are what the commands are written for.** Windows creates and activates the virtual
  environment differently; the lines are in [SETUP.md](SETUP.md), and the rest is the same.
- **Six kinds of careers site have a reader but no example company in `targets.json`**: directemployers,
  paradox, radancy_v2, taleo, taleo_tbe and tiktok. Each has a fixture-backed test; a company on one of
  them is added by probing it first ([adapters/README.md](../adapters/README.md)).
