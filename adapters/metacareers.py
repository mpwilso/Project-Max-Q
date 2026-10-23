"""
metacareers - Meta careers (metacareers.com), Meta's own board. Browser impersonation, opt-in only.

    {"company": "Meta", "ats": "metacareers"}

This reader pretends to be a browser, which is why it lives here and not in sweep.py: it sends a
Chrome user agent and the Sec-Fetch-* navigation headers, scrapes an LSD token off the jobs page and
calls a GraphQL endpoint with a pinned doc_id. It runs only on a run that opted in with
--allow-browser-impersonation or MAXQ_ALLOW_BROWSER_IMPERSONATION=1 (h.browser_impersonation_allowed);
otherwise list_jobs raises h.OptInRequired before any request and coverage shows the board as SKIPPED
with the reason, never as ERROR and never as a quiet zero. Every URL still goes through h.robots_check,
because this reader keeps its own requests.Session and so bypasses the shared HTTP layer's check.

Two things about this board that are not obvious:
  1. Meta 400s any request that does not look like a browser navigation. A bare urllib or requests
     GET with Accept: application/json gets 400 Bad Request, not 403, which reads like a broken URL.
     The Sec-Fetch-* set and a real UA are what make it answer.
  2. The search result node carries ONLY id, title, locations, teams, sub_teams. There is no posted
     date anywhere in the search response, so freshness is impossible from the list pass alone. The
     date lives on the job page, in a schema.org JobPosting block, alongside the full description,
     responsibilities and qualifications. Detail pass is mandatory here for any date-aware question,
     unlike Greenhouse or Ashby where the list carries the date.

The GraphQL call needs an LSD token and doc_id, both scraped from the jobs page and its Relay bundle.
doc_id is pinned below; if Meta ships a new Relay build it changes and the call starts returning an
error payload rather than failing loudly, so the adapter raises on an empty board (silent-zero trap).

Enumerable: the unfiltered search returns the entire board in one call, and team-filtered calls were
checked against it and returned strict subsets, no extra rows. So absence from a clean read closes a req.

One session for the whole run: the LSD token and the datr cookie are bootstrapped once (_meta_session).
"""
import html
import json
import re
import time

NAME = "metacareers"
ENUMERABLE = True
REQUIRED = []

JOBS_PAGE = "https://www.metacareers.com/jobs/"
GRAPHQL = "https://www.metacareers.com/graphql"
_META_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")
_META_NAV = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
             "Accept-Language": "en-US,en;q=0.9", "Sec-Fetch-Dest": "document",
             "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Site": "none", "Sec-Fetch-User": "?1",
             "Upgrade-Insecure-Requests": "1"}
_META_DOC_ID = "27506805582236862"          # CareersJobSearchResultsDataQuery
_meta_sess = None


def _meta_session(h):
    """One session for the whole run: the LSD token and the datr cookie are bootstrapped once."""
    global _meta_sess
    if not h.browser_impersonation_allowed():
        raise h.OptInRequired(f"Meta's careers site needs browser impersonation ({h.OPT_IN_HINT})")
    if _meta_sess is not None: return _meta_sess
    s = h.requests.Session()
    s.headers.update({"User-Agent": _META_UA, "Accept-Language": "en-US,en;q=0.9"})
    h.robots_check(JOBS_PAGE)
    r = s.get(JOBS_PAGE, headers=_META_NAV, timeout=30)
    r.raise_for_status()
    lsd = re.search(r'"LSD",\[\],\{"token":"([^"]+)"', r.text)
    rev = re.search(r'"server_revision":(\d+)', r.text)
    if not lsd:
        raise RuntimeError("metacareers: no LSD token on the jobs page; markup changed")
    s._lsd = lsd.group(1)
    s._rev = rev.group(1) if rev else ""
    _meta_sess = s
    return s


def list_jobs(t, h, smoke=False):
    s = _meta_session(h)
    body = {"lsd": s._lsd, "doc_id": _META_DOC_ID, "fb_api_caller_class": "RelayModern",
            "fb_api_req_friendly_name": "CareersJobSearchResultsDataQuery",
            "server_timestamps": "true", "__rev": s._rev, "__a": "1",
            "variables": json.dumps({"isLoggedIn": False, "search_input": {}, "viewasUserID": None})}
    time.sleep(h.DELAY)
    h.robots_check(GRAPHQL)
    r = s.post(GRAPHQL, data=body, timeout=45, headers={
        "Content-Type": "application/x-www-form-urlencoded", "X-FB-LSD": s._lsd,
        "Origin": "https://www.metacareers.com", "Referer": JOBS_PAGE,
        "Accept": "*/*", "Sec-Fetch-Dest": "empty", "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin", "X-Requested-With": "XMLHttpRequest"})
    r.raise_for_status()
    txt = r.text[9:] if r.text.startswith("for (;;);") else r.text
    d = ((json.loads(txt).get("data") or {}).get("job_search_with_featured_jobs") or {})
    jobs = (d.get("all_jobs") or []) + (d.get("featured_jobs") or [])
    if not jobs:
        # Silent-zero trap: a rotated doc_id returns a well-formed payload with no rows; never let
        # that read as "board empty".
        raise RuntimeError("metacareers: 0 rows. doc_id likely rotated; re-scrape it from the "
                           "Relay bundle (grep CareersJobSearchResultsDataQuery_candidate_portalRelayOperation)")
    out, seen = [], set()
    for j in jobs:
        if j["id"] in seen: continue
        seen.add(j["id"])
        out.append(h.norm(t.get("company", "Meta"), NAME, j["id"], j.get("title"),
                          " | ".join(j.get("locations") or []),
                          f"https://www.metacareers.com/jobs/{j['id']}/", None, "", None,
                          {"teams": j.get("teams") or [], "sub_teams": j.get("sub_teams") or []}))
        if smoke and len(out) >= 5: break
    return out


def detail(t, row, h):
    """Date, full text and the remote flag all come off the job page's JobPosting block."""
    s = _meta_session(h)
    time.sleep(h.DELAY)
    h.robots_check(row["url"])
    r = s.get(row["url"], headers=_META_NAV, timeout=30)
    r.raise_for_status()
    for blob in re.findall(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', r.text, flags=re.S):
        try:
            d = json.loads(html.unescape(blob.strip()))
        except Exception:
            continue
        if not isinstance(d, dict) or d.get("@type") != "JobPosting": continue
        # Meta splits the posting across three fields; the qualifications block is where the
        # years bar lives, so concatenating all three is what makes the tenure gate work.
        row["description"] = "\n\n".join(h.strip_html(d.get(k)) for k in
                                         ("description", "responsibilities", "qualifications") if d.get(k))
        row["posted"] = h.iso_date(d.get("datePosted"))
        locs = d.get("jobLocation") or []
        if isinstance(locs, dict): locs = [locs]
        names = [(L or {}).get("name") or "" for L in locs]
        if d.get("jobLocationType") == "TELECOMMUTE": names.append("Remote")
        if any(names): row["location"] = " | ".join(dict.fromkeys(n for n in names if n))
        row["extra"]["employmentType"] = d.get("employmentType")
        row["extra"]["validThrough"] = h.iso_date(d.get("validThrough"))
        break
    return row
