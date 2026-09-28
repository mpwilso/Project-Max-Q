"""
apple - jobs.apple.com, Apple's own board.

Request shape (verified against the live board):
  - POST https://jobs.apple.com/api/v1/search answers 200 with NO cookie, CSRF token or browser
    headers, but ONLY when the body carries a `format` object ({"longDate", "mediumDate"}).
    Drop `format` and the same request answers 200 {"searchResults": [], "totalRecords": 0},
    with or without the x-apple-csrf-token from /api/v1/CSRFToken. That empty payload is
    indistinguishable from an empty board, which is why list_jobs raises on zero rows.
  - Pages are a fixed 20 rows. Reading past the last page ALSO answers totalRecords 0, so the
    stop condition is reaching totalRecords, never the page-1 count alone. An in-range page can
    also answer empty at random; _page retries it (see there).
  - The whole US board is roughly 4,500 rows / 225 pages (a little over 2 minutes at a 0.15s delay).

Enumerable. The walk with no query and filters.locations=[postLocation-USA] returns the whole US
board, and sort=newest is a strict order on postDateInGMT. The board moves during a walk (reqs
posted and closed while it runs), and as with oracle a closure mid-walk can shift one row across
a page boundary, so a single missing row is not proof of closure on its own.

Ids: `id` is the posting number per location ("300640271-0231" Austin, "300640271-0588"
Raleigh would be the same req in two cities). That is the stable, per-row id Apple's own URLs use.

Dates: `postDateInGMT` is the posting date (the board runs back years, so it is not a nightly
refresh). One exception: the evergreen retail pipeline row (id "PIPE-114438158", "US - Specialist")
stamps postDateInGMT with the current time on EVERY request (nanosecond precision, different on
each call). Rows like that are stored as extra.refreshed, not posted.

Locations: list rows carry only a site name with no state ("Cupertino", "San Francisco Bay Area",
"Austin"), so the country is appended ("Austin, United States") to give the fail-closed gate its
US marker. Detail replaces it with city, state, country. homeOffice=true adds a bare "Remote".

Comp: the band lives in postingFooters ("The base pay range for this role is between $221,500 and
$333,100"), phrased with "between ... and", which MONEY_RANGE_RE does not read, so detail parses it
explicitly.
"""
import re
import time

NAME = "apple"
ENUMERABLE = True
REQUIRED = []

SEARCH = "https://jobs.apple.com/api/v1/search"
DETAIL = "https://jobs.apple.com/api/v1/jobDetails/"
FORMAT = {"longDate": "MMMM D, YYYY", "mediumDate": "MMM D, YYYY"}
PAGE_SIZE = 20                       # fixed by the API
EMPTY_RETRIES = 5                    # empty-200 pages mid-board; see _page
MAX_PAGES = 400                      # a full US walk needs ~225; a runaway walk stops here
_LIVE_STAMP = re.compile(r"T\d\d:\d\d:\d\d\.\d{7,}")     # nanosecond stamp = generated per request
_PAY = re.compile(r"between\s+(\$\d[\d,]*\d(?:\.\d+)?)\s+and\s+(\$\d[\d,]*\d(?:\.\d+)?)", re.I)   # "$311,700," ends a clause


def _country(name):
    return "United States" if (name or "").lower() in ("united states of america", "usa") else (name or "")


def _list_location(j):
    segs = []
    for L in j.get("locations") or []:
        name, country = (L.get("name") or "").strip(), _country(L.get("countryName"))
        segs.append(name if not country or name == country else f"{name}, {country}")
    if j.get("homeOffice"): segs.append("Remote")
    return " | ".join(dict.fromkeys(s for s in segs if s))


def _dates(stamp):
    """(posted, refreshed) from postDateInGMT."""
    if stamp and _LIVE_STAMP.search(stamp):
        return None, stamp[:10]
    return stamp, None


def _page(h, locs, page, total, have):
    """One search page, retrying the empty 200s the API now answers mid-board.

    The API answers a random page (1 in 5 on a full walk, a different page each time) with
    {"searchResults": [], "totalRecords": 0}. A stop-on-empty rule reads that as the end of the
    board and records a truncated walk as a complete one. A retry returns the page. An empty page
    that should hold rows and stays empty raises, so the read errors and its reqs are carried,
    never closed."""
    body = {"query": "", "filters": {"locations": locs}, "page": page,
            "locale": "en-us", "sort": "newest", "format": FORMAT}
    for attempt in range(EMPTY_RETRIES + 1):
        d = (h.post_json(SEARCH, body) or {}).get("res") or {}
        rows = d.get("searchResults") or []
        if rows or (total is not None and (have >= total or (page - 1) * PAGE_SIZE >= total)):
            return d, rows
        if attempt < EMPTY_RETRIES:
            time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(f"apple: page {page} answered empty {EMPTY_RETRIES + 1} times"
                       f" (totalRecords {total}); a hole mid-board, not the end of it")


def list_jobs(t, h, smoke=False):
    locs = t.get("locations") or ["postLocation-USA"]
    company = t.get("company", "Apple")
    out, seen, page, total = [], set(), 1, None
    while page <= MAX_PAGES:
        d, rows = _page(h, locs, page, total, len(seen))
        if total is None: total = d.get("totalRecords") or 0
        for j in rows:
            jid = j.get("id")
            if not jid or jid in seen: continue
            seen.add(jid)
            posted, refreshed = _dates(j.get("postDateInGMT"))
            extra = {"positionId": j.get("positionId"), "type": j.get("type"),
                     "team": (j.get("team") or {}).get("teamName"), "homeOffice": j.get("homeOffice")}
            if refreshed: extra.update({"refreshed": refreshed, "date_kind": "refreshed"})
            out.append(h.norm(company, NAME, jid, j.get("postingTitle"), _list_location(j),
                              f"https://jobs.apple.com/en-us/details/{jid}/{j.get('transformedPostingTitle') or ''}",
                              h.iso_date(posted), "", None, extra))
        page += 1
        if smoke or not rows or len(seen) >= total: break
        if getattr(h, "incremental_stop", None) and h.incremental_stop(t, [j.get("id") for j in rows]): break
    if not out:
        raise RuntimeError("apple: 0 rows. The search API answers an empty 200 when the body lacks "
                           "`format` or the filter id is wrong; check the request body before "
                           "reading this as an empty board")
    return out


def detail(t, row, h):
    d = (h.get(DETAIL + h.quote(row["id"])) or {}).get("res") or {}
    if not d:
        raise RuntimeError(f"apple: empty jobDetails for {row['id']}")
    parts = []
    for label, key in (("", "jobSummary"), ("", "description"), ("Responsibilities", "responsibilities"),
                       ("Minimum Qualifications", "minimumQualifications"),
                       ("Preferred Qualifications", "preferredQualifications")):
        txt = h.strip_html(d.get(key))
        if txt: parts.append(f"{label}\n{txt}" if label else txt)
    # A multi-city req carries one pay footer per city with DIFFERENT bands (the test fixture:
    # Raleigh and Phoenix each state their own range, Austin none). Keep the footers for the city this
    # posting id belongs to, so the band read is this row's band and not the first city's.
    sel_id = (d.get("selectedLocation") or {}).get("id")
    fl = d.get("postingFooters") or []
    if sel_id and any((f or {}).get("postLocationId") == sel_id for f in fl):
        fl = [f for f in fl if (f or {}).get("postLocationId") == sel_id]
    footers = []
    for f in fl:
        for loc_items in ((f or {}).get("localizations") or {}).values():
            footers += [h.strip_html((i or {}).get("content")) for i in loc_items or []]
    footers = [x for x in footers if x]
    row["description"] = "\n\n".join(parts + footers)

    # A multi-city req returns every city in `locations`, but each city is its own posting id with
    # its own row, so the row keeps the city its id was posted to (selectedLocation). The full set
    # goes to extra so a Seattle row of a Seattle|Cupertino req is still visible as such.
    fmt = lambda L: ", ".join(x for x in (L.get("city") or L.get("name"), L.get("stateProvince"),
                                          _country(L.get("countryName"))) if x)
    sel = d.get("selectedLocation")
    segs = [fmt(sel)] if sel else [fmt(L) for L in d.get("locations") or []]
    row["extra"]["allLocations"] = [fmt(L) for L in d.get("locations") or []]
    if d.get("homeOffice"): segs.append("Remote")
    segs = [s for s in dict.fromkeys(segs) if s]
    if segs: row["location"] = " | ".join(segs)

    # jobDetails truncates the live stamp to milliseconds, so the list pass is the only place the
    # per-request stamp is recognisable; a row it marked refreshed stays refreshed.
    posted, refreshed = _dates(d.get("postDateInGMT"))
    if row["extra"].get("date_kind") == "refreshed":
        refreshed = (posted or "")[:10] or row["extra"].get("refreshed")
    if refreshed:
        row["posted"] = None
        row["extra"].update({"refreshed": refreshed, "date_kind": "refreshed"})
    else:
        row["posted"] = h.iso_date(d.get("postingDateMeta") or posted) or row.get("posted")

    if not row.get("comp"):
        m = next((m for m in (_PAY.search(x) for x in footers) if m), None)
        row["comp"] = f"{m.group(1)} - {m.group(2)}" if m else h.comp_from_description(row["description"])
    row["extra"].update({"jobType": d.get("jobType"), "level": d.get("highJobTitle"),
                         "teams": d.get("teamNames"), "employmentType": d.get("employmentType"),
                         "reqId": d.get("reqId")})
    return row
