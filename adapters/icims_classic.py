"""
icims_classic - classic iCIMS career portals (careers-<tenant>.icims.com), not the Jibe front end.

    {"company": "Globex Systems", "ats": "icims_classic", "host": "careers-globexsystems.icims.com"}

List:   GET https://<host>/jobs/search?ss=1&pr=N&in_iframe=1      (0-based pr, 50 cards per page)
Detail: GET <job url>?in_iframe=1                                  (schema.org JobPosting in ld+json)

Tested against a large (30-page) portal and a small regional one (a careers-na-<tenant> host).

The in_iframe=1 view is the bare portal markup: one <li class="iCIMS_JobCardItem"> per req, holding
the job link (/jobs/<id>/<slug>/job), the title, a header-left location, an optional header-right
"Posted Date" and a <dl> of tenant-configured fields (Requisition ID, Telecommute Options,
Employment Type ...). The card description is a truncated teaser, so the JD comes from detail.

Traps (verified against live portals):
- User-Agent. A browser-like UA ("Mozilla/5.0 ... Chrome/128") answers HTTP 405 "Human
  Verification" on every path. The pipeline's own honest UA (h.get_text sends sweep.UA) answers
  200. Nothing is solved or spoofed; do not add a browser UA here.
- ss=1 matters on some tenants: without it a portal can render only the search form. Always sent.
- "Page X of N" in the paginator gives N; paging stops at N, on a page with no cards, or on a page
  that adds no new ids (a pr past the end repeats the last page on some tenants).
- Locations are "US-CO-Denver" (country-state-city) or a bare "US"; some tenants also write
  "US-TX-Austin, TX". They are rewritten to "Denver, CO, United States" so the location
  gate sees a US marker. Detail replaces the list location with the JSON-LD jobLocation list when
  that list names more than one place.
- Posted date comes ONLY from the card's "Posted Date" column (some portals show it; others
  have no such column). JSON-LD datePosted is not trusted: on one tenant it is the request time minus
  two years to the millisecond, on a current req, i.e. synthesized. Rows from a portal without the column therefore carry posted=None and the sweep's first_seen.
- Telecommute Options (where a tenant configures it) takes three values: "No remote/telework allowed", "Flexible for
  occasional telework" and "Remote work allowed 100%". A bare "Remote" segment
  is added only for a value that says the role is remote, never for "No remote" or "occasional".
- Ids are the numeric iCIMS job id from the URL (stable across retitles; the slug is not).
"""
import html as _html
import json
import re

NAME = "icims_classic"
ENUMERABLE = True
REQUIRED = ["host"]

MAX_PAGES = 400
COUNTRIES = {"US": "United States", "USA": "United States", "CA": "Canada", "GB": "United Kingdom",
             "UK": "United Kingdom", "MX": "Mexico", "AU": "Australia", "DE": "Germany", "IN": "India"}

_CARD_SPLIT = re.compile(r'<li class="iCIMS_JobCardItem"[^>]*>')
_LINK = re.compile(r'<a href="(https?://[^"]+/jobs/(\d+)/[^"]*?/job)(?:\?[^"]*)?"[^>]*class="iCIMS_Anchor"', re.S)
_TITLE = re.compile(r"<h3[^>]*>(.*?)</h3>", re.S)
_HEADER_LEFT = re.compile(r'header left">\s*(?:<span class="sr-only field-label">[^<]*</span>)?\s*<span[^>]*>(.*?)</span>', re.S)
_POSTED = re.compile(r'field-label">[^<]*Posted Date[^<]*</span>\s*<span title="([^"]+)"', re.S)
_FIELDS = re.compile(r'iCIMS_JobHeaderField">(.*?)</dt>\s*<dd class="iCIMS_JobHeaderData">(.*?)</dd>', re.S)
_PAGES = re.compile(r"Page\s*\d+\s*of\s*(\d+)")
_LD = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)
_REMOTE_OK = re.compile(r"(fully|100\s*%|full[- ]time)\s*remote|^remote\b|remote\s*(eligible|allowed|position|role)", re.I)
_REMOTE_NO = re.compile(r"\bno\s+remote|not\s+remote|occasional", re.I)


def _clean(s):
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def location_text(raw):
    """'US-CO-Denver' -> 'Denver, CO, United States'; 'US' -> 'United States'."""
    out = []
    for part in re.split(r"\s*\|\s*|\s*;\s*", raw or ""):
        part = part.strip()
        if not part: continue
        m = re.match(r"^([A-Z]{2,3})(?:-([A-Z0-9]{1,3}))?(?:-(.+))?$", part)
        if not m:
            out.append(part); continue
        country = COUNTRIES.get(m.group(1), m.group(1))
        state, city = m.group(2), (m.group(3) or "").strip()
        if city and state and re.search(rf",\s*{re.escape(state)}$", city):
            city = city[: city.rfind(",")].strip()             # "Austin, TX" under US-TX
        out.append(", ".join(x for x in (city, state, country) if x))
    return " | ".join(dict.fromkeys(out))


def parse_list(markup):
    """Return (cards, total_pages). Each card: id, url, title, location, posted_raw, fields."""
    pages = [int(x) for x in _PAGES.findall(markup)]
    cards = []
    for chunk in _CARD_SPLIT.split(markup)[1:]:
        chunk = chunk.split("</li>")[0] if "</li>" in chunk else chunk
        link = _LINK.search(chunk)
        if not link: continue
        title = _TITLE.search(chunk)
        loc = _HEADER_LEFT.search(chunk)
        posted = _POSTED.search(chunk)
        fields = {_clean(k): _clean(v) for k, v in _FIELDS.findall(chunk)}
        cards.append({"id": link.group(2), "url": link.group(1), "title": _clean(title.group(1)) if title else "",
                      "location": _clean(loc.group(1)) if loc else "",
                      "posted_raw": posted.group(1) if posted else None, "fields": fields})
    return cards, (max(pages) if pages else None)


def _remote(fields):
    v = fields.get("Telecommute Options") or fields.get("Remote") or ""
    return bool(v) and not _REMOTE_NO.search(v) and bool(_REMOTE_OK.search(v))


def list_jobs(t, h, smoke=False):
    host = t["host"].replace("https://", "").replace("http://", "").strip("/")
    seen, pr, total = {}, 0, None
    while True:
        markup = h.get_text(f"https://{host}/jobs/search?ss=1&pr={pr}&in_iframe=1")
        cards, pages = parse_list(markup)
        if total is None:
            total = pages or 1
            if not cards:
                if "Human Verification" in markup:
                    raise RuntimeError(f"icims_classic {host}: 'Human Verification' wall on page 0")
                raise RuntimeError(f"icims_classic {host}: page 0 returned no job cards")
        new = 0
        for c in cards:
            if c["id"] in seen: continue
            new += 1
            loc = location_text(c["location"])
            if _remote(c["fields"]): loc = " | ".join(x for x in (loc, "Remote") if x)
            seen[c["id"]] = h.norm(t["company"], NAME, c["id"], c["title"], loc, c["url"],
                                   h.iso_date(c["posted_raw"]), "", None,
                                   {k: v for k, v in c["fields"].items()
                                    if k in ("Requisition ID", "Job ID", "Telecommute Options", "Employment Type",
                                             "Position Category", "Category", "Clearance")})
        pr += 1
        if smoke or not cards or not new or pr >= total or pr >= MAX_PAGES:
            break
    return list(seen.values())


def _jobposting(markup):
    for m in _LD.finditer(markup):
        try:
            d = json.loads(m.group(1))
        except ValueError:
            continue
        for x in (d if isinstance(d, list) else [d]):
            if isinstance(x, dict) and x.get("@type") == "JobPosting":
                return x
    return None


def _ld_locations(jp):
    locs = jp.get("jobLocation") or []
    if isinstance(locs, dict): locs = [locs]
    out = []
    for L in locs:
        a = (L or {}).get("address") or {}
        parts = [a.get(k) for k in ("addressLocality", "addressRegion")]
        country = COUNTRIES.get(str(a.get("addressCountry") or "").upper(), a.get("addressCountry"))
        seg = ", ".join(str(p).strip() for p in parts + [country] if p and str(p).strip() != "UNAVAILABLE")
        if seg: out.append(seg)
    return list(dict.fromkeys(out))


def detail(t, row, h):
    markup = h.get_text(row["url"] + ("&" if "?" in row["url"] else "?") + "in_iframe=1")
    jp = _jobposting(markup)
    if not jp:
        raise RuntimeError(f"icims_classic {row['url']}: no JobPosting JSON-LD on the job page")
    row["description"] = h.strip_html(jp.get("description"))
    comp = h.comp_from_description(row["description"])
    if comp: row["comp"] = comp
    ld = _ld_locations(jp)
    if len(ld) > 1 or not row.get("location"):
        remote = row.get("location", "").endswith("Remote")
        row["location"] = " | ".join(ld + (["Remote"] if remote else []))
    if jp.get("occupationalCategory"): row["extra"]["occupationalCategory"] = jp["occupationalCategory"]
    if jp.get("employmentType"): row["extra"]["employmentType"] = jp["employmentType"]
    return row
