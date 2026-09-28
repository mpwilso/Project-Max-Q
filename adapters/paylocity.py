"""
paylocity - Paylocity Recruiting public job boards (recruiting.paylocity.com/Recruiting/Jobs/All/<guid>).

    {"company": "Fabrikam Freight", "ats": "paylocity", "guid": "3f2a9c1e-5b7d-4e8f-9a0b-1c2d3e4f5a6b"}

List:   GET https://recruiting.paylocity.com/Recruiting/Jobs/All/<guid>
        HTML; the whole board is the `window.pageData = {...};` literal (pageData.Jobs).
Detail: GET https://recruiting.paylocity.com/Recruiting/Jobs/Details/<JobId>
        HTML; the JD is the markup under div.job-preview-details (Description, Requirements).

Traps (verified against live boards):
- The JSON feed /recruiting/v2/api/feed/jobs/<guid> answers 200 with "jobs": [] even for a
  populated board (the feed is a per-tenant opt-in). Do not use it; an empty feed is not an empty board.
- pageData.Jobs[].Description is a 110-character teaser, so detail is needed for the JD.
- PublishedDate is the last publish, not the req's creation: a JobId from a year-older range
  can carry a much later PublishedDate. It is the only date the board exposes, so it is
  the posted date with extra["date_kind"] = "published"; treat an old JobId with a new date as a
  repost.
- LocationName is free text (a company name, "Remote Worker - N/A", "Dallas") with
  no US marker; JobLocation carries City/State/Country ("USA"), which is appended.
- IsRemote is the board's remote flag and adds a bare "Remote" segment, but employers set it
  loosely (a "Business Development Representative - Hybrid 3x/Week" req can be IsRemote true).
- A company group can run several modules, each with its own guid and its own (possibly empty)
  board; the group's websites may link different ones. One guid is one module.
"""
import json, re

NAME = "paylocity"
ENUMERABLE = True
REQUIRED = ["guid"]

HOST = "https://recruiting.paylocity.com"
_PAGEDATA = re.compile(r"window\.pageData\s*=\s*(\{.*?\});\s*\n", re.S)
COUNTRIES = {"USA": "United States", "US": "United States", "CAN": "Canada", "GBR": "United Kingdom",
             "IND": "India", "AUS": "Australia", "MEX": "Mexico", "DEU": "Germany", "IRL": "Ireland",
             "PHL": "Philippines", "NZL": "New Zealand", "FRA": "France", "NLD": "Netherlands"}


def page_data(markup):
    m = _PAGEDATA.search(markup or "")
    if not m:
        raise RuntimeError("paylocity: window.pageData not found in board page")
    return json.loads(m.group(1))


def _location(j):
    jl = j.get("JobLocation") or {}
    code = (jl.get("Country") or "").strip()
    country = COUNTRIES.get(code.upper(), code.lower())
    is_us = country == "United States"
    place = ", ".join(p.strip() for p in (jl.get("City"), jl.get("State") if is_us else None, country) if p and p.strip())
    name = (j.get("LocationName") or jl.get("Name") or "").strip()
    seg = name
    if place and place.lower() not in name.lower():
        seg = f"{name} ({place})" if name else place
    segs = [seg] if seg else []
    if j.get("IsRemote"):
        segs.append("Remote")
    return " | ".join(segs)


def list_jobs(t, h, smoke=False):
    guid = t["guid"]
    pd = page_data(h.get_text(f"{HOST}/Recruiting/Jobs/All/{guid}"))
    jobs = pd.get("Jobs")
    if jobs is None:
        raise RuntimeError(f"paylocity {guid}: pageData has no Jobs key")
    if not jobs:
        raise RuntimeError(f"paylocity {guid}: board '{pd.get('ModuleTitle')}' answered with no jobs")
    out = []
    for j in jobs:
        if not j.get("JobId") or j.get("IsInternal"): continue
        jid = j["JobId"]
        out.append(h.norm(t["company"], NAME, jid, j.get("JobTitle"), _location(j),
                          f"{HOST}/Recruiting/Jobs/Details/{jid}", h.iso_date(j.get("PublishedDate")),
                          "", None,
                          {"date_kind": "published", "department": j.get("HiringDepartment"),
                           "module": pd.get("ModuleTitle"), "teaser": j.get("Description")}))
    return out


def parse_detail(markup):
    i = markup.find('class="job-preview-details"')
    if i < 0:
        raise RuntimeError("paylocity detail: job-preview-details not found")
    body = markup[i:]
    k = body.find('<div class="job-listing-header"')
    if k >= 0: body = body[k:]
    for end in ('class="preview-bottom-apply-btn"', "<footer"):
        e = body.find(end)
        if e >= 0:
            body = body[:e]
            break
    return body


def detail(t, row, h):
    body = parse_detail(h.get_text(row["url"]))
    row["description"] = h.strip_html(re.sub(r"<div class=\"job-listing-header\">([^<]*)</div>", r"<p>\1</p>", body))
    comp = h.comp_from_description(row["description"])
    if comp: row["comp"] = comp
    return row
