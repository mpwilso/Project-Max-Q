"""
deel - Deel's own ATS ("Deel ATS", jobs.deel.com).

Not a vendor board. Checked and ruled out: api.ashbyhq.com/posting-api/job-board/deel
answers 200 with {"jobs": []} (an empty Ashby board, NOT the live one, even though Deel's CMS still
names its fields ashby_id / ashby_published_date), greenhouse slug deel 404, lever deel 404.
jobs.deel.com/deel (the board root) 307-redirects to www.deel.com/careers/, and that page is the
listing: a Next.js page whose flight data (`self.__next_f.push([1, "..."])` chunks) carries the
whole board as one `"jobs":[...]` array under the product.career-job-listing component.
Every row is is_listed and the newest ashby_published_date is current, so it is not a stale CMS
export.

Enumerable: the one page is the whole board.

Fields: ashby_id is the posting id Deel's own URLs and JSON-LD use ("Deel ATS Job Posting ID");
job_id is the underlying requisition and can back more than one posting. all_locations is a list of
COUNTRIES ("United States", "Canada"), sometimes a city; the board carries no remote flag, so no
"Remote" segment is invented even though most Deel roles are remote-in-country. Onsite roles say so
in the title ("..., Houston, Texas, On-site").
compensation_tier_summary is written with a doubled dollar sign ("$$70,000 - $110,000 USD", or
"From $65,000 USD"); it is normalised to a single "$".

Detail: the list carries no JD text (full_job_description is always ""). The job page
(jobs.deel.com/deel/job-details/<id>/overview) is server-rendered with a schema.org JobPosting block:
description, datePosted (equal to the list's ashby_published_date) and jobLocation.
"""
import json
import re

NAME = "deel"
ENUMERABLE = True
REQUIRED = []

CAREERS = "https://www.deel.com/careers/"
JOB = "https://jobs.deel.com/deel/job-details/{}/overview"
_PUSH = re.compile(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)')
_LD = re.compile(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', re.S)


def parse_jobs(markup):
    flight = "".join(json.loads(m.group(1)) for m in _PUSH.finditer(markup or ""))
    k = flight.find('"jobs":[{')
    if k < 0:
        raise RuntimeError("deel: no \"jobs\" array in the careers page flight data (page layout changed)")
    arr, _ = json.JSONDecoder().raw_decode(flight, k + len('"jobs":'))
    return [a.get("attributes") or a for a in arr]


def _comp(s):
    return re.sub(r"\${2,}", "$", s).strip() if s else None


def list_jobs(t, h, smoke=False):
    jobs = parse_jobs(h.get_text(t.get("url", CAREERS)))
    out, seen = [], set()
    for a in jobs:
        jid = a.get("ashby_id")
        if not jid or jid in seen or a.get("is_listed") is False: continue
        seen.add(jid)
        locs = a.get("all_locations") or [L.get("location") for L in a.get("locations_list") or []] \
            or [a.get("location_name")]
        comp = _comp(a.get("compensation_tier_summary")) if a.get("should_display_compensation_on_Job_board") is not False else None
        out.append(h.norm(t.get("company", "Deel"), NAME, jid, (a.get("title") or "").strip(),
                          " | ".join(dict.fromkeys(x.strip() for x in locs if x and x.strip())),
                          JOB.format(jid), h.iso_date(a.get("ashby_published_date")), "",
                          h.money_range(comp) or comp,
                          {"job_id": a.get("job_id"), "team": a.get("team_name"),
                           "department": a.get("department_name"),
                           "employment_type": a.get("employment_type"),
                           "updated": h.iso_date(a.get("ashby_updated_at")),
                           "deadline": h.iso_date(a.get("application_deadline"))}))
    if not out:
        raise RuntimeError("deel: 0 rows parsed from the careers page; the jobs array was empty")
    return out


def detail(t, row, h):
    markup = h.get_text(row["url"])
    for blob in _LD.findall(markup):
        try:
            d = json.loads(blob.strip())
        except ValueError:
            continue
        if not isinstance(d, dict) or d.get("@type") != "JobPosting": continue
        row["description"] = h.strip_html(d.get("description"))
        row["posted"] = h.iso_date(d.get("datePosted")) or row.get("posted")
        locs = d.get("jobLocation") or []
        if isinstance(locs, dict): locs = [locs]
        names = []
        for L in locs:
            a = (L or {}).get("address") or {}
            names.append(", ".join(x for x in (a.get("addressLocality"), a.get("addressRegion"),
                                               a.get("addressCountry")) if x))
        if d.get("jobLocationType") == "TELECOMMUTE": names.append("Remote")
        if any(names) and not row.get("location"):
            row["location"] = " | ".join(dict.fromkeys(n for n in names if n))
        if not row.get("comp"): row["comp"] = h.comp_from_description(row["description"])
        row["extra"]["employmentType"] = d.get("employmentType")
        return row
    raise RuntimeError(f"deel: no JobPosting JSON-LD on {row['url']}")
