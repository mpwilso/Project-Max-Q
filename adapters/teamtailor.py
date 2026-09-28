"""
teamtailor - Teamtailor career sites (custom domain or <slug>.teamtailor.com).

    {"company": "Acme Analytics", "ats": "teamtailor", "base": "https://careers.acme-analytics.example"}

Endpoint: GET <base>/jobs.json?per_page=100   (JSON Feed 1.1, unauthenticated)

Each item carries content_html (full JD) and _jobposting, the schema.org JobPosting block with
datePosted, identifier.value (the numeric job id that also prefixes the job URL) and jobLocation.
So there is no detail pass.

Traps (verified against a live board):
- Use jobs.json, not jobs.rss. The RSS feed honours per_page but IGNORES page (page=2..4 with
  per_page=3 all returned the same first 3 jobs), so a board larger than the RSS default page
  can never be read to completion through it. jobs.json honours both and emits next_url while
  more pages exist; past the end it answers 200 with items=[].
- Paging follows next_url, with an empty page or an already-seen page as the backstop.
- The item "id" is a guid; the stable Teamtailor job id is _jobposting.identifier.value (700101
  in /jobs/700101-senior-software-engineer). The URL slug after the id changes on retitle.
- jobLocation.address.addressCountry is an ISO code ("US"). It is expanded to "United States"
  because the location gate needs a US marker and a bare "US" is not one. A job with no office
  has no jobLocation at all and an empty location, which the fail-closed gate
  holds as "no location stated" rather than guessing.
- The JSON feed has no remote flag of its own. jobLocationType TELECOMMUTE (schema.org) adds a
  bare "Remote" segment; the RSS <remoteStatus> is not used.
- datePosted is the publish date.
"""
NAME = "teamtailor"
ENUMERABLE = True
REQUIRED = ["base"]

PER_PAGE = 100
COUNTRIES = {"US": "United States", "USA": "United States", "GB": "United Kingdom", "UK": "United Kingdom",
             "CA": "Canada", "DE": "Germany", "FR": "France", "SE": "Sweden", "NL": "Netherlands",
             "IE": "Ireland", "ES": "Spain", "IN": "India"}


def _location(jp):
    locs = jp.get("jobLocation") or []
    if isinstance(locs, dict): locs = [locs]
    segs = []
    for L in locs:
        a = (L or {}).get("address") or {}
        country = a.get("addressCountry")
        if isinstance(country, dict): country = country.get("name")
        country = COUNTRIES.get(str(country or "").strip().upper(), country)
        segs.append(", ".join(str(x).strip() for x in (a.get("addressLocality"), a.get("addressRegion"), country)
                              if x and str(x).strip()))
    lt = jp.get("jobLocationType")
    if lt and "TELECOMMUTE" in str(lt).upper():
        reqs = jp.get("applicantLocationRequirements") or []
        if isinstance(reqs, dict): reqs = [reqs]
        for req in reqs:
            name = (req or {}).get("name") if isinstance(req, dict) else None
            if name: segs.append(COUNTRIES.get(str(name).upper(), name))
        segs.append("Remote")
    return " | ".join(dict.fromkeys(s for s in segs if s))


def list_jobs(t, h, smoke=False):
    base = t["base"].rstrip("/")
    url = f"{base}/jobs.json?per_page={PER_PAGE}"
    seen, pages = {}, 0
    while url:
        d = h.get(url)
        items = d.get("items") or []
        pages += 1
        if pages == 1 and not items:
            raise RuntimeError(f"teamtailor {base}: jobs.json answered with no items")
        new = 0
        for it in items:
            jp = it.get("_jobposting") or {}
            jid = (jp.get("identifier") or {}).get("value") or it.get("id")
            if not jid or str(jid) in seen: continue
            new += 1
            seen[str(jid)] = h.norm(t["company"], NAME, jid, it.get("title") or jp.get("title"), _location(jp),
                                    it.get("url"), h.iso_date(jp.get("datePosted") or it.get("date_published")),
                                    h.strip_html(it.get("content_html") or jp.get("description")), None,
                                    {"guid": it.get("id"), "employmentType": jp.get("employmentType")})
        if smoke or not items or not new or pages >= 200:
            break
        url = d.get("next_url")
    return list(seen.values())
