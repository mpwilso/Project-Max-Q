"""
paradox - Paradox career sites (the "<tenant>.recruiting.com" CMS), e.g. a corporate board at
northwindcorp.recruiting.com (a fictional tenant; the shape is what matters).

DETAIL ONLY WHERE THE CAREER SITE HOSTS THE JOB PAGE (see list_path below). Read this first:
  - The career site server-renders `window.__PRELOAD_STATE__ = {...}` with jobSearch.jobs (10 per
    page), jobSearch.totalJob and structured locations. /jobs?page_number=N is honoured by the
    server render (echoed in jobSearch.params); page_size is clamped to 10 whatever is asked.
  - The XHR the page itself uses, POST /api/get-jobs?..., answers 403 "Access Denied" to a
    cookieless client and 200 once the site's `ct` cookie is set by a page load. The server render
    carries the same rows, so the adapter reads that and never needs the cookie.
  - There is NO posted date and NO JD text anywhere in the career-site payload. Every job links out
    to the Paradox apply page (<tenant>.paradox.ai/co/<company>/Job?job_id=...), and that host
    answers HTTP 202 with an AWS WAF JavaScript challenge (token.awswaf.com challenge.js) instead
    of the posting. That is active bot protection, so it is NOT fetched. On such boards detail() is
    a no-op: posted is always None (freshness falls back to first_seen), description is "", and the
    gates that read JD text cannot see this board's postings. Rows are for noticing a title.

Enumerable: the paged server render walks the whole board (the row count equals totalJob).
Ids: uniqueID ("PDX_<TENANT>_<guid>_<locationID>"), the id the apply URL uses.
Locations: city, state abbreviation, country from the structured location ("Columbus, OH, United
States"); isRemote adds "Remote".
customFields carry hiring-leader and recruiter names; those are deliberately NOT copied to extra.

Target keys: base (required, e.g. "https://northwindcorp.recruiting.com").
  list_path  optional page template, default "/jobs?page_number={page}". Paradox sites on their own
             domain (e.g. careers.litware.example) serve the list at the root and page it as
             /page/N, and /jobs answers 404 there: use "/page/{page}". Same __PRELOAD_STATE__
             payload; their applyURL is olivia.paradox.ai/co/<company>/Job?job_id=
             (AWS WAF challenge, HTTP 202, same as the tenant boards').
             On these sites originalURL is RELATIVE ("analyst-financial-planning/job/P1-7304418-1")
             and <base>/<originalURL> is a server-rendered job page with a schema.org JobPosting
             block: full JD, jobLocation, datePosted. The row url is that page, and detail() reads it.
             datePosted is a REFRESH stamp: evergreen reqs with low, long-reposted ids (e.g.
             P1-3018257-5) read today or yesterday. -> extra.refreshed,
             never posted.
"""
import json
import re

NAME = "paradox"
ENUMERABLE = True
REQUIRED = ["base"]

MAX_PAGES = 99          # the site's own pageNumber validator rejects >= 100
_STATE = re.compile(r"window\.__PRELOAD_STATE__\s*=\s*(\{.*?\});\s*window\.__BUILD__", re.S)
_KEEP_CF = {"cf_shorten_job_id", "cf_position_number", "cf_exempt_status", "cf_functional_area",
            "cf_brand", "cf_salary_grade"}


def parse_state(markup):
    m = _STATE.search(markup or "")
    if not m:
        raise RuntimeError("paradox: no __PRELOAD_STATE__ on the career-site jobs page (markup changed "
                           "or a challenge page was served)")
    js = json.loads(m.group(1)).get("jobSearch") or {}
    return js.get("jobs") or [], js.get("totalJob") or 0


def _location(j):
    segs = []
    for L in j.get("locations") or []:
        segs.append(", ".join(x for x in (L.get("city"), L.get("stateAbbr") or L.get("state"),
                                          L.get("country")) if x) or L.get("locationText") or "")
    if j.get("isRemote") or any((L or {}).get("isRemote") for L in j.get("locations") or []):
        segs.append("Remote")
    return " | ".join(dict.fromkeys(s for s in segs if s))


def list_jobs(t, h, smoke=False):
    base = t["base"].rstrip("/")
    out, seen, page, total = [], set(), 1, None
    while page <= MAX_PAGES:
        path = (t.get("list_path") or "/jobs?page_number={page}").format(page=page)
        jobs, tot = parse_state(h.get_text(f"{base}{path}"))
        if total is None: total = tot
        new = 0
        for j in jobs:
            jid = j.get("uniqueID")
            if not jid or jid in seen: continue
            seen.add(jid); new += 1
            cf = {c.get("cfKey"): c.get("value") for c in j.get("customFields") or [] if c.get("cfKey") in _KEEP_CF}
            extra = {"reference": j.get("reference"), "account": j.get("accountName"), **cf}
            orig = j.get("originalURL") or ""
            url = j.get("applyURL") or orig
            if orig and not re.match(r"https?://", orig):
                url = extra["page"] = f"{base}/{orig.lstrip('/')}"
            out.append(h.norm(t["company"], NAME, jid, j.get("title"), _location(j), url, None, "", None, extra))
        page += 1
        # A page past the end re-renders page 1 on some tenants, so "nothing new" also stops.
        if smoke or not jobs or not new or len(seen) >= total: break
    if not out:
        raise RuntimeError(f"paradox: 0 rows from {base}/jobs (totalJob={total})")
    return out


def job_posting(markup):
    """The schema.org JobPosting dict from a career-site job page, or None."""
    for blob in re.findall(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', markup or "", flags=re.S):
        try:
            d = json.loads(blob.strip())
        except ValueError:
            continue
        if isinstance(d, dict) and d.get("@type") == "JobPosting":
            return d
    return None


def detail(t, row, h):
    page = (row.get("extra") or {}).get("page")
    if not page:
        return row          # the only job page is the WAF-challenged paradox.ai apply host
    d = job_posting(h.get_text(page))
    if not d:
        raise RuntimeError(f"paradox: no JobPosting block on {page}")
    row["description"] = h.strip_html(d.get("description"))
    refreshed = h.iso_date(d.get("datePosted"))
    if refreshed:
        row["extra"].update(refreshed=refreshed, date_kind="refreshed")
    locs = d.get("jobLocation") or []
    if isinstance(locs, dict): locs = [locs]
    names = []
    for L in locs:
        a = (L or {}).get("address") or {}
        names.append(", ".join(x for x in (a.get("addressLocality"), a.get("addressRegion"), a.get("addressCountry")) if x))
    names = list(dict.fromkeys(n for n in names if n))
    if len(names) > 1:
        remote = "Remote" in row["location"].split(" | ") or d.get("jobLocationType") == "TELECOMMUTE"
        row["location"] = " | ".join(names + (["Remote"] if remote else []))
    elif d.get("jobLocationType") == "TELECOMMUTE" and "Remote" not in row["location"].split(" | "):
        row["location"] = (row["location"] + " | Remote").strip(" |")
    if not row.get("comp"):
        row["comp"] = h.comp_from_description(row["description"])
    return row
