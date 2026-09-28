"""
bamboohr - BambooHR hosted careers pages (<slug>.bamboohr.com/careers).

    {"company": "Initech", "ats": "bamboohr", "slug": "initech"}

List:   GET https://<slug>.bamboohr.com/careers/list          {"meta": {"totalCount"}, "result": [...]}
Detail: GET https://<slug>.bamboohr.com/careers/<id>/detail   {"result": {"jobOpening": {...}}}

Both unauthenticated JSON. The list is the whole board in one response (meta.totalCount equals
len(result); no paging parameters exist) and carries no date and no JD, so detail fills both.

Traps (verified against live boards):
- Two location shapes: `location` {city, state} is a company office (no country in the list),
  `atsLocation` {country, state, province, city} is a free-entered location, used on remote reqs.
  Either can be all-null; both are read. Detail adds location.addressCountry.
- locationType is a string enum read here as "0" on-site, "1" remote, "2" hybrid. That mapping is
  INFERRED, not read from a label: the careers bundle loads its labels lazily and none was found.
  The data fits it (on the board checked, every "1" row has no office and a free atsLocation such
  as "Denver, Colorado, United States" for an Americas sales lead; every "2" row is a company office).
  "1" or a truthy legacy isRemote adds a bare "Remote" segment; the raw value is kept in extra.
- An unknown slug and a real board with no openings both answer 200 {"result": []}; zero raises.
- datePosted (detail) is YYYY-MM-DD and is the posting date; compensation is a free-text string
  or null.
"""
NAME = "bamboohr"
ENUMERABLE = True
REQUIRED = ["slug"]

LOCATION_TYPES = {"0": "On-site", "1": "Remote", "2": "Hybrid"}


def _base(slug):
    return f"https://{slug}.bamboohr.com/careers"


def _join(*parts):
    return ", ".join(dict.fromkeys(p.strip() for p in parts if isinstance(p, str) and p.strip()))


def _location(j):
    l = j.get("location") or {}
    a = j.get("atsLocation") or {}
    segs = [_join(l.get("city"), l.get("state"), l.get("addressCountry")),
            _join(a.get("city"), a.get("state") or a.get("province"), a.get("country"))]
    segs = list(dict.fromkeys(s for s in segs if s))
    if str(j.get("locationType")) == "1" or j.get("isRemote"):
        segs.append("Remote")
    return " | ".join(segs)


def list_jobs(t, h, smoke=False):
    slug = t["slug"]
    d = h.get(f"{_base(slug)}/list")
    res = d.get("result") if isinstance(d, dict) else None
    if res is None:
        raise RuntimeError(f"bamboohr {slug}: unexpected list response: {str(d)[:200]}")
    if not res:
        raise RuntimeError(f"bamboohr {slug}: board answered with no openings (totalCount="
                           f"{(d.get('meta') or {}).get('totalCount')})")
    out = []
    for j in res:
        jid = j.get("id")
        if not jid: continue
        lt = str(j.get("locationType")) if j.get("locationType") is not None else None
        out.append(h.norm(t["company"], NAME, jid, j.get("jobOpeningName"), _location(j),
                          f"{_base(slug)}/{jid}", None, "", None,
                          {"department": j.get("departmentLabel"),
                           "employment_status": j.get("employmentStatusLabel"),
                           "location_type": LOCATION_TYPES.get(lt, lt)}))
    return out


def detail(t, row, h):
    d = h.get(f"{_base(t['slug'])}/{row['id']}/detail")
    jo = ((d or {}).get("result") or {}).get("jobOpening") or {}
    row["description"] = h.strip_html(jo.get("description"))
    row["posted"] = h.iso_date(jo.get("datePosted")) or row.get("posted")
    loc = _location(jo)
    if loc: row["location"] = loc
    comp = h.money_range(jo.get("compensation")) if jo.get("compensation") else None
    comp = comp or h.comp_from_description(row["description"])
    if comp: row["comp"] = comp
    row["extra"].update({"date_kind": "posted", "status": jo.get("jobOpeningStatus"),
                         "compensation_text": jo.get("compensation")})
    return row
