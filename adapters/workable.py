"""
workable - Workable hosted career pages (apply.workable.com/<slug>/).

    {"company": "Initech", "ats": "workable", "slug": "initech"}

Endpoint: GET https://apply.workable.com/api/v1/widget/accounts/<slug>?details=true

The embed-widget feed returns the whole published board in one call with no paging, and
details=true adds the full JD HTML to every job, so there is no detail pass. Unauthenticated,
no browser headers needed. An unknown slug answers 404.

Checked against a live board: the widget's shortcodes are exactly the set returned by the SPA's
own POST /api/v3/accounts/<slug>/jobs ({"query": ""} -> {"total": N, "results"}),
so the widget is not a truncated view. v3 pages with a nextPage token and has no description;
per-job text there is GET /api/v2/accounts/<slug>/jobs/<shortcode>. Neither is needed here.

Traps:
- Without details=true there is no description at all (same URL, key simply absent).
- published_on is the posting date (created_at matches it on most rows seen; an evergreen
  "General Application" req can keep a published_on years old).
- Remote reqs carry a placeholder office flagged "hidden": true (every "US Remote" role on one
  board said New York, New York). A hidden city is NOT a work location: only its country is kept, plus a
  bare "Remote" segment when telecommuting is true, so an NYC placeholder is never read as
  an onsite location.
- Multi-country postings appear once PER LOCATION in the widget (on one board the widget lists more
  entries than shortcodes, and the shortcode count matches the v3 total). Entries are folded by shortcode and their locations joined,
  otherwise the same req is a duplicate key.
- The shortcode is the stable id (apply.workable.com/j/<shortcode>); the numeric id only appears
  in v3.
"""
NAME = "workable"
ENUMERABLE = True
REQUIRED = ["slug"]

API = "https://apply.workable.com/api/v1/widget/accounts"


def _loc_segments(j):
    locs = j.get("locations") or [{"city": j.get("city"), "region": j.get("state"),
                                   "country": j.get("country"), "hidden": False}]
    segs = []
    for l in locs:
        if not isinstance(l, dict): continue
        if l.get("hidden"):
            parts = [l.get("country")]
        else:
            parts = [l.get("city"), l.get("region"), l.get("country")]
        segs.append(", ".join(str(p).strip() for p in parts if p and str(p).strip()))
    return [s for s in segs if s]


def _segments(j, extra_segs=()):
    segs = _loc_segments(j) + list(extra_segs)
    if j.get("telecommuting"):
        segs.append("Remote")
    return " | ".join(dict.fromkeys(s for s in segs if s))


def list_jobs(t, h, smoke=False):
    slug = t["slug"]
    d = h.get(f"{API}/{h.quote(slug)}?details=true")
    jobs = d.get("jobs") if isinstance(d, dict) else None
    if not jobs:
        raise RuntimeError(f"workable {slug}: widget answered with no jobs")
    # One widget entry per posting LOCATION on some tenants (e.g. D4E5F6A7B8: same shortcode, JD and
    # dates three times, once each for Germany / France / United Kingdom). Fold by shortcode.
    first, more = {}, {}
    for j in jobs:
        sc = j.get("shortcode")
        if not sc: continue
        if sc in first:
            more.setdefault(sc, []).extend(_loc_segments(j))
        else:
            first[sc] = j
    out = []
    for sc, j in first.items():
        out.append(h.norm(t["company"], NAME, sc, j.get("title"), _segments(j, more.get(sc, ())),
                          j.get("shortlink") or j.get("url") or f"https://apply.workable.com/j/{sc}",
                          h.iso_date(j.get("published_on") or j.get("created_at")),
                          h.strip_html(j.get("description")), None,
                          {"department": j.get("department"), "employment_type": j.get("employment_type"),
                           "experience": j.get("experience"), "remote": bool(j.get("telecommuting"))}))
    return out
