"""
google - Google Careers (www.google.com/about/careers/applications).

There is no public JSON API, but the server-rendered results page embeds the whole result page as
`AF_initDataCallback({key: 'ds:1', hash: '<n>', data:[...], sideChannel: {}})`. The data is plain
JSON once the wrapper is cut away: data = [jobs, null, total, page_size]. Verified against the
live board:
  - ?location=United%20States&sort_by=date&page=N honours all three params. Pages are 20 rows.
    A full US walk returns exactly `total` rows, all with unique ids, and the page after the last
    still embeds ds:1 with an EMPTY job list (total unchanged).
  - ds:0 is the brand list (Google, YouTube, DeepMind, Waymo, Wing, Verily, GFiber), not jobs.
    DeepMind has no board of its own any more (its greenhouse slug 404s); its reqs are these rows
    with brand "DeepMind", kept in extra.brand. A target may restrict with "brands": [...].

Each job is a positional array (no keys). Indices used, all checked across a full US walk:
  0 id (stable numeric job id, the id in Google's own /jobs/results/<id> URLs)
  1 title    3 responsibilities html    4 min + preferred qualifications html
  7 brand    9 locations [[display "New York, NY, USA", [addresses], city, zip, state, country]]
  10 about-the-job html (carries the pay line "US: $174000 - $252000 (USD)" on nearly every row)
  12 / 13 [epoch seconds, nanos]    15 location/onsite notes html    16 == 1 on remote reqs
  18 location note html ("Remote location: Texas, USA." on exactly the rows where 16 == 1)

Dates: Google publishes NO posted date on the page. The embedded data carries two timestamps.
[12] is always <= [13] (they often differ, never the other way round); sort_by=date orders on
[13] (no inversions) and not on [12] (many). So [13] behaves as last-updated and [12] as the
original publish/create time. posted = [12], the earlier and therefore conservative date;
[13] goes to extra.updated. Neither is documented, so treat freshness here as inferred.

Enumerable: the no-keyword US walk returned exactly `total` unique rows. Sorting on update time
means an edit mid-walk can move a req across a page boundary; rows are de-duplicated on id.
No detail pass: the list page already carries the full JD text.

Non-US cities appear on multi-country reqs ("Toronto, ON, Canada"); every display string ends
in its country, so US segments carry "USA" for the fail-closed gate.
"""
import datetime as dt
import json
import re

NAME = "google"
ENUMERABLE = True
REQUIRED = []

BASE = "https://www.google.com/about/careers/applications/jobs/results/"
MAX_PAGES = 250                      # a full US walk needs ~95
_DS1 = re.compile(r"AF_initDataCallback\(\{key: 'ds:1', hash: '\d+', data:(.*?), sideChannel: \{\}\}\);</script>", re.S)


def _at(a, i):
    return a[i] if isinstance(a, list) and len(a) > i else None


def _html(a, i):
    v = _at(a, i)
    return v[1] if isinstance(v, list) and len(v) > 1 and isinstance(v[1], str) else ""


def _date(ts):
    if not (isinstance(ts, list) and ts and isinstance(ts[0], (int, float))): return None
    return dt.datetime.fromtimestamp(ts[0], dt.timezone.utc).astimezone().date().isoformat()


def parse_page(markup):
    """-> (jobs, total). Raises when the ds:1 blob is missing: a consent/'sorry' interstitial
    or a markup change, never an empty board."""
    m = _DS1.search(markup or "")
    if not m:
        raise RuntimeError("google: no ds:1 AF_initDataCallback blob on the results page "
                           "(markup changed, or an interstitial was served instead of results)")
    data = json.loads(m.group(1))
    return (_at(data, 0) or []), (_at(data, 2) or 0)


def to_row(t, j, h):
    jid = str(_at(j, 0))
    locs = [(_at(L, 0) or "").strip() for L in (_at(j, 9) or [])]
    if _at(j, 16) == 1: locs.append("Remote")
    desc = "\n\n".join(x for x in (h.strip_html(_html(j, 10)), h.strip_html(_html(j, 3)),
                                   h.strip_html(_html(j, 4)), h.strip_html(_html(j, 15)),
                                   h.strip_html(_html(j, 18))) if x)
    return h.norm(t.get("company", "Google"), NAME, jid, _at(j, 1), " | ".join(dict.fromkeys(x for x in locs if x)),
                  BASE + jid, _date(_at(j, 12)), desc, None,
                  {"brand": _at(j, 7), "updated": _date(_at(j, 13)),
                   "countries": sorted({_at(L, 5) for L in (_at(j, 9) or []) if _at(L, 5)})})


def list_jobs(t, h, smoke=False):
    loc = t.get("location", "United States")
    brands = {b.lower() for b in t.get("brands") or []}
    out, seen, page, total = [], set(), 1, None
    while page <= MAX_PAGES:
        jobs, tot = parse_page(h.get_text(f"{BASE}?location={h.quote(loc)}&sort_by=date&page={page}"))
        if total is None: total = tot
        for j in jobs:
            jid = _at(j, 0)
            if not jid or jid in seen: continue
            seen.add(jid)
            if brands and (_at(j, 7) or "").lower() not in brands: continue
            out.append(to_row(t, j, h))
        page += 1
        if smoke or not jobs or len(seen) >= total: break
        if getattr(h, "incremental_stop", None) and h.incremental_stop(t, [_at(j, 0) for j in jobs]): break
    if not seen:
        raise RuntimeError(f"google: 0 rows for location={loc!r} (total={total}); the results page "
                           "rendered but embedded no jobs, check the query params")
    return out
