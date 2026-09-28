"""
recruitee - Recruitee public careers API (<slug>.recruitee.com, or the employer's custom domain).

    {"company": "Northwind Traders", "ats": "recruitee", "slug": "northwind"}

List: GET https://<slug>.recruitee.com/api/offers/    {"offers": [...]}

Unauthenticated JSON; a custom domain (careers.<employer>.com/api/offers/) answers the same set.
One response is the whole published board with the JD inline (description + requirements HTML),
so there is no paging and no detail pass.

Traps (verified against a live board):
- Three dates: created_at (req created), published_at (went live), updated_at (edits). The
  timestamps are "2026-09-16 17:31:05 UTC". published_at is posted; updated_at goes to
  extra["refreshed"].
- location is a display string ("Remote job") and country/city/state_name describe the nominal
  office; locations[] can list several. remote: true adds a bare "Remote" segment. Employers post
  "100% Remote - EMEA" reqs with a Moroccan nominal office, so the country must stay in the string.
- salary is {min, max, period, currency}, all null when unpublished.
- An unknown slug answers 404; a 200 with no offers raises.
"""
NAME = "recruitee"
ENUMERABLE = True
REQUIRED = ["slug"]


def _join(*parts):
    return ", ".join(dict.fromkeys(p.strip() for p in parts if isinstance(p, str) and p.strip()))


def _location(o):
    segs = []
    for l in o.get("locations") or []:
        if isinstance(l, dict):
            segs.append(_join(l.get("city"), l.get("state"), l.get("country")))
    if not segs:
        segs.append(_join(o.get("city"), o.get("state_name"), o.get("country")))
    segs = list(dict.fromkeys(s for s in segs if s))
    if o.get("remote"):
        segs.append("Remote")
    return " | ".join(segs)


def _comp(s):
    s = s or {}
    lo, hi = s.get("min"), s.get("max")
    if not (lo and hi) or (s.get("currency") or "USD") != "USD" or (s.get("period") or "year") != "year":
        return None
    try:
        return f"${float(lo):,.0f} - ${float(hi):,.0f}"
    except (TypeError, ValueError):
        return None


def list_jobs(t, h, smoke=False):
    slug = t["slug"]
    d = h.get(f"https://{h.quote(slug)}.recruitee.com/api/offers/")
    offers = d.get("offers") if isinstance(d, dict) else None
    if offers is None:
        raise RuntimeError(f"recruitee {slug}: unexpected response {str(d)[:200]}")
    if not offers:
        raise RuntimeError(f"recruitee {slug}: board answered with no offers")
    out = []
    for o in offers:
        if not o.get("id") or (o.get("status") or "published") != "published": continue
        desc = "\n\n".join(x for x in (h.strip_html(o.get("description")), h.strip_html(o.get("requirements"))) if x)
        out.append(h.norm(t["company"], NAME, o["id"], o.get("title"), _location(o),
                          o.get("careers_url"), h.iso_date(o.get("published_at") or o.get("created_at")),
                          desc, _comp(o.get("salary")),
                          {"refreshed": h.iso_date(o.get("updated_at")), "created": h.iso_date(o.get("created_at")),
                           "department": o.get("department"), "hybrid": o.get("hybrid"),
                           "on_site": o.get("on_site"), "remote": o.get("remote"),
                           "employment_type": o.get("employment_type_code"), "guid": o.get("guid")}))
    if not out:
        raise RuntimeError(f"recruitee {slug}: {len(offers)} offers, none published")
    return out
