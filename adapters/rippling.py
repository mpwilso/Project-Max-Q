"""
rippling - Rippling ATS public job boards (ats.rippling.com/<slug>/jobs).

    {"company": "Contoso Health", "ats": "rippling", "slug": "contoso"}

List:   GET https://ats.rippling.com/api/v2/board/<slug>/jobs?page=N&pageSize=100   (0-based page)
Detail: GET https://ats.rippling.com/api/v2/board/<slug>/jobs/<uuid>

Both unauthenticated JSON. The older https://api.rippling.com/platform/api/ats/v1/board/<slug>/jobs
returns the same set as one flat array (no paging) but with a bare "Austin, TX" label and no
country or workplace type, so v2 is used for the list.

Traps (verified against a live board):
- The list is one item PER LOCATION, not per job: items run to about twice the distinct job uuids. Rows are
  grouped by uuid and the locations joined, otherwise every multi-city req is a duplicate key.
- The list carries no date and no JD text. Detail has description as a dict {"company": html,
  "role": html}, createdOn (the job post's creation timestamp; treated as the posted date - an
  old evergreen req keeps its original createdOn), and payRangeDetails [{rangeStart, rangeEnd,
  currency, frequency, location}] which is the structured band (empty outside the US).
- pageSize is honoured up to at least 1000; totalPages is what the paging stops on, with an
  empty page as the backstop.
- Remote locations are named like "Remote (United States)" or "Remote (Utah, US)" with
  workplaceType REMOTE; a bare "Remote" segment is added for those.
"""
NAME = "rippling"
ENUMERABLE = True
REQUIRED = ["slug"]

API = "https://ats.rippling.com/api/v2/board"
PAGE = 100


def _loc_name(l):
    name = (l.get("name") or "").strip()
    country = (l.get("country") or "").strip()
    if country and country.lower() not in name.lower():
        name = f"{name}, {country}" if name else country
    return name


def list_jobs(t, h, smoke=False):
    slug = t["slug"]
    jobs, page = {}, 0
    while True:
        d = h.get(f"{API}/{h.quote(slug)}/jobs?page={page}&pageSize={PAGE}")
        items = d.get("items") or []
        if page == 0 and not items:
            raise RuntimeError(f"rippling {slug}: board answered with no jobs (totalItems={d.get('totalItems')})")
        for it in items:
            jid = it.get("id")
            if not jid: continue
            j = jobs.setdefault(jid, {"item": it, "locs": [], "remote": False})
            for l in it.get("locations") or []:
                j["locs"].append(_loc_name(l))
                if (l.get("workplaceType") or "").upper() == "REMOTE": j["remote"] = True
        page += 1
        if smoke or not items or page >= int(d.get("totalPages") or 0) or page > 200:
            break
    out = []
    for jid, j in jobs.items():
        it = j["item"]
        locs = list(dict.fromkeys(x for x in j["locs"] if x))
        if j["remote"]: locs.append("Remote")
        out.append(h.norm(t["company"], NAME, jid, it.get("name"), " | ".join(locs),
                          it.get("url") or f"https://ats.rippling.com/{slug}/jobs/{jid}", None, "", None,
                          {"department": (it.get("department") or {}).get("name")}))
    return out


def _comp(ranges):
    for r in ranges or []:
        if (r.get("currency") or "USD") != "USD" or (r.get("frequency") or "YEAR") != "YEAR": continue
        lo, hi = r.get("rangeStart"), r.get("rangeEnd")
        if lo and hi:
            return f"${lo:,.0f} - ${hi:,.0f}"
    return None


def detail(t, row, h):
    d = h.get(f"{API}/{h.quote(t['slug'])}/jobs/{row['id']}")
    desc = d.get("description")
    if isinstance(desc, dict):
        parts = [desc.get("role"), desc.get("company")] + [v for k, v in desc.items() if k not in ("role", "company")]
        row["description"] = "\n\n".join(h.strip_html(p) for p in parts if isinstance(p, str) and p.strip())
    else:
        row["description"] = h.strip_html(desc)
    row["posted"] = h.iso_date(d.get("createdOn")) or row.get("posted")
    comp = _comp(d.get("payRangeDetails")) or h.comp_from_description(row["description"])
    if comp: row["comp"] = comp
    if not row.get("location") and d.get("workLocations"):
        row["location"] = " | ".join(d["workLocations"])
    row["extra"].update({"date_kind": "created",
                         "employmentType": (d.get("employmentType") or {}).get("id"),
                         "payRangeDetails": d.get("payRangeDetails") or []})
    return row
