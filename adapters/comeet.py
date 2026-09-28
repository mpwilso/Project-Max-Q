"""
comeet - Comeet careers API (www.comeet.com/jobs/<slug>/<uid>, and employer sites embedding it).

    {"company": "Litware", "ats": "comeet", "uid": "12.00A", "token": "EXAMPLE0FAKE0TOKEN000000000000000000000"}

List: GET https://www.comeet.co/careers-api/2.0/company/<uid>/positions?token=<token>&details=true

Unauthenticated in practice: the token is the public careers-site token that every Comeet embed
ships in its page source. Where it lives:
- comeet.com hosted page (comeet.com/jobs/<slug>/<uid>): `"token":"..."` in COMPANY_DATA.
- WordPress plugin: `var comeetvar = {"comeet_token": ..., "comeet_uid": ...}`.
- COMEET.init({"token": ..., "company-uid": ...}) on the site or on a single position page;
  `const COMPANY_UID` / TOKEN in an inline fetch.
- Some employers' hosted pages redirect to comeet.com; the token is then on the apply iframe
  (comeet.co/jobs/<uid>/<pos>/apply?...) of any position page.

One unpaged JSON array = the whole board, with the JD inline (details=true) as a list of
{name, value(html), order} sections, so there is no detail pass.

Traps:
- No posting date exists anywhere in the API or the hosted page, only time_updated, which moves on
  edits. It goes to extra["refreshed"] with date_kind "refreshed"; posted stays None, so freshness
  falls back to first_seen.
- location.is_remote is True for Hybrid roles too. Only workplace_type == "Remote" adds
  the bare "Remote" segment.
- location.country is an ISO-2 code. Written raw, "IL" (Israel) and "IN" (India) read as Illinois
  and Indiana to the uppercase state-code check, so codes are spelled out as country names.
- One req posted in several cities is several positions: an "Account Executive - ANZ" req can be
  both 3A.16F and 3A.16F-B3.500. Each uid is its own position, so they stay separate rows.
- url_active_page is whatever the employer configured: tracking junk (?_gl=...&gclid=...,
  ?%20job%20board=) or a generic careers URL shared by every position. The query is
  dropped, and a URL that does not name the position's uid falls back to the Comeet hosted page.
- location can be null; the row then has no location, or just "Remote".
- uid values look like floats ("19.000", "43.00A"); they are strings and must stay strings.
"""
NAME = "comeet"
ENUMERABLE = True
REQUIRED = ["uid", "token"]

API = "https://www.comeet.co/careers-api/2.0/company"

COUNTRIES = {
    "US": "United States", "IL": "Israel", "GB": "United Kingdom", "UK": "United Kingdom", "IN": "India",
    "CA": "Canada", "DE": "Germany", "FR": "France", "NL": "Netherlands", "ES": "Spain", "PT": "Portugal",
    "IT": "Italy", "CH": "Switzerland", "AT": "Austria", "BE": "Belgium", "IE": "Ireland", "PL": "Poland",
    "RO": "Romania", "UA": "Ukraine", "CZ": "Czechia", "SE": "Sweden", "DK": "Denmark", "NO": "Norway",
    "FI": "Finland", "AU": "Australia", "NZ": "New Zealand", "SG": "Singapore", "JP": "Japan",
    "KR": "South Korea", "CN": "China", "HK": "Hong Kong", "TW": "Taiwan", "PH": "Philippines",
    "BR": "Brazil", "MX": "Mexico", "CO": "Colombia", "AR": "Argentina", "CL": "Chile", "AE": "United Arab Emirates",
    "CY": "Cyprus", "GR": "Greece", "BG": "Bulgaria", "RS": "Serbia", "HU": "Hungary", "ZA": "South Africa",
}


def _location(j):
    l = j.get("location") or {}
    code = (l.get("country") or "").strip()
    country = COUNTRIES.get(code.upper(), code.lower())
    parts = [l.get("city") or l.get("name"), l.get("state"), country]
    seg = ", ".join(dict.fromkeys(p.strip() for p in parts if p and p.strip()))
    segs = [seg] if seg else []
    if (j.get("workplace_type") or "").strip().lower() == "remote":
        segs.append("Remote")
    return " | ".join(segs)


def _details(details, h):
    if isinstance(details, str):
        return h.strip_html(details)
    parts = sorted((d for d in details or [] if isinstance(d, dict)), key=lambda d: d.get("order") or 0)
    out = []
    for d in parts:
        body = h.strip_html(d.get("value"))
        if body:
            out.append(f"{d.get('name')}\n{body}" if d.get("name") else body)
    return "\n\n".join(out)


def _url(j):
    """Employer page when it names this position, else the Comeet hosted page; tracking query dropped."""
    uid = (j.get("uid") or "").lower()
    keys = {uid, uid.replace(".", "_")}
    for u in (j.get("url_active_page"), j.get("url_comeet_hosted_page"), j.get("url_recruit_hosted_page")):
        u = (u or "").split("?")[0]
        if u and any(k and k in u.lower() for k in keys):
            return u
    return (j.get("url_comeet_hosted_page") or j.get("url_active_page") or "").split("?")[0]


def list_jobs(t, h, smoke=False):
    uid = str(t["uid"])
    d = h.get(f"{API}/{uid}/positions?token={t['token']}&details=true")
    if not isinstance(d, list):
        raise RuntimeError(f"comeet {uid}: expected a JSON array, got {type(d).__name__}: {str(d)[:200]}")
    if not d:
        raise RuntimeError(f"comeet {uid}: board answered 200 with no positions")
    out = []
    for j in d:
        if not j.get("uid") or j.get("is_internal"): continue
        out.append(h.norm(t["company"], NAME, j["uid"], j.get("name"), _location(j),
                          _url(j), None, _details(j.get("details"), h), None,
                          {"refreshed": h.iso_date(j.get("time_updated")), "date_kind": "refreshed",
                           "workplace_type": j.get("workplace_type"),
                           "department": j.get("department"),
                           "employment_type": j.get("employment_type"),
                           "experience_level": j.get("experience_level"),
                           "hosted_url": j.get("url_comeet_hosted_page")}))
    if not out:
        raise RuntimeError(f"comeet {uid}: {len(d)} positions, none public")
    return out
