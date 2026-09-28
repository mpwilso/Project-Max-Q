"""
icims_jibe - iCIMS career sites served through the Jibe (Google Hire / "careers-home") front end.

    {"company": "Initech",        "ats": "icims_jibe", "base": "https://careers.initech.example"}
    {"company": "Contoso Health", "ats": "icims_jibe", "base": "https://careers.contoso-health.example",
     "params": {"country": "United States"}}

Endpoint: GET <base>/api/jobs?page=N&limit=100[&<params>]. Unauthenticated JSON, no browser headers
needed. Each job is wrapped as {"data": {...}} and the list carries the full JD (description, plus
separate responsibilities / qualifications on some tenants), posted_date, and structured locations,
so there is no detail pass.

Traps (verified against live tenants):
- limit is capped at 100. limit=500 answers HTTP 422 "An unexpected error occurred", not a clamp.
- page is 1-based; a page past the end answers 200 with jobs=[]. totalCount is the filtered total.
- posted_date is the posting date (e.g. posted 2026-08-05, update_date 2026-09-15 on the same req).
  update_date is a refresh stamp and is kept only in extra.
- salary_*_value fields exist but are "0" on every tenant probed; treat "0" as no band.
- Some tenants' remote reqs have no city: location_name "US Remote", country "United States". The
  country name is what carries the US marker, so it always goes into the segment.
- params narrows the board (e.g. country=United States), which makes the read enumerable for
  that slice only; absence still means closed within the slice.
- params values are sent URL-quoted, so a multi-value filter is written literally:
  {"categories": "Home/Regional Offices|Litware Travel"}. Jibe treats "|" in categories as OR
  (the piped pair returns the sum of the two single-category counts).
- Page cap. One retailer's unfiltered board runs past 20,000 reqs, mostly store roles: more than
  MAX_PAGES pages of 100. The loop raises at MAX_PAGES while totalCount is unconsumed, so a board
  that outgrows the cap is an ERROR, not a quiet partial read.
- One tenant answers /api/jobs at the host root although its pages live under
  /<tenant>/jobs/<id>; /jobs/<id> redirects there, so row urls stay <base>/jobs/<id>.
"""
NAME = "icims_jibe"
ENUMERABLE = True
REQUIRED = ["base"]

PAGE = 100          # server maximum; larger values 422
MAX_PAGES = 100     # 10,000 rows; a board past this raises instead of truncating quietly


def _num(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _segment(loc):
    parts = [loc.get("city"), loc.get("state"), loc.get("country")]
    seg = ", ".join(str(p).strip() for p in parts if p and str(p).strip())
    return seg or (loc.get("location_name") or "").strip()


def _location(j):
    segs = [_segment(j)]
    for extra in j.get("additional_locations") or []:
        if isinstance(extra, dict): segs.append(_segment(extra))
    if not any(segs):
        segs = [x.strip() for x in (j.get("full_location") or "").split(";")]
    remote = "remote" in (j.get("location_name") or "").lower() or any(
        isinstance(v, list) and any(str(x).strip().lower() == "remote" for x in v)
        for k, v in j.items() if k.startswith("tags"))
    if remote: segs.append("Remote")
    return " | ".join(dict.fromkeys(s for s in segs if s))


def _description(j, h):
    desc = h.strip_html(j.get("description"))
    for k in ("responsibilities", "qualifications"):
        s = h.strip_html(j.get(k))
        if s and s[:80] not in desc:
            desc = (desc + "\n\n" + s).strip()
    return desc


def _row(t, j, h):
    base = t["base"].rstrip("/")
    jid = j.get("req_id") or j.get("slug")
    lo, hi = _num(j.get("salary_min_value")), _num(j.get("salary_max_value"))
    comp = f"${lo:,.0f} - ${hi:,.0f}" if lo > 0 and hi >= lo else None
    return h.norm(t["company"], NAME, jid, j.get("title"), _location(j),
                  f"{base}/jobs/{j.get('slug') or jid}", h.iso_date(j.get("posted_date")),
                  _description(j, h), comp,
                  {"refreshed": h.iso_date(j.get("update_date")),
                   "categories": [c.get("name") for c in j.get("categories") or [] if isinstance(c, dict)],
                   "employment_type": j.get("employment_type"),
                   "apply_url": j.get("apply_url")})


def list_jobs(t, h, smoke=False):
    base = t["base"].rstrip("/")
    params = t.get("params") or {}
    qs = "".join(f"&{h.quote(str(k))}={h.quote(str(v))}" for k, v in params.items())
    seen, page, total = {}, 1, None
    while True:
        d = h.get(f"{base}/api/jobs?page={page}&limit={PAGE}{qs}")
        jobs = [x.get("data") or {} for x in (d.get("jobs") or [])]
        if total is None:
            total = int(d.get("totalCount") or 0)
            if not jobs:
                raise RuntimeError(f"icims_jibe {base}: page 1 returned no jobs (totalCount={total})")
        for j in jobs:
            jid = j.get("req_id") or j.get("slug")
            if jid: seen[str(jid)] = _row(t, j, h)
        # Paginate on what came back: stop on an empty page or once totalCount is consumed.
        if smoke or not jobs or len(seen) >= total:
            break
        if page >= MAX_PAGES:
            # Stopping quietly here would truncate a very large unfiltered board at 10,000 rows.
            raise RuntimeError(f"icims_jibe {base}: stopped at the {MAX_PAGES}-page cap with {len(seen)} of "
                               f"totalCount {total} read; narrow the board with params")
        page += 1
    return list(seen.values())
