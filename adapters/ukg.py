"""
ukg - UKG Pro Recruiting (UltiPro) public job boards.

    {"company": "Wide World Importers", "ats": "ukg",
     "base": "https://recruiting2.ultipro.com/WWI1000WWIMP/JobBoard/0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d"}

base is the board URL up to and including the JobBoard guid, no trailing slash. The host varies
by tenant and is part of the key: recruiting.ultipro.com, recruiting2.ultipro.com, and
<tenant>.rec.pro.ukg.net all occur.

List:   POST <base>/JobBoardView/LoadSearchResults   {"opportunitySearch": {"Top", "Skip", ...}}
        -> {"opportunities": [...], "totalCount": N}
Detail: GET  <base>/OpportunityDetail?opportunityId=<guid>   (HTML; the job is a JSON literal passed
        to `new US.Opportunity.CandidateOpportunityDetail({...})`)

Traps (verified against several live tenants):
- The list PostedDate is NOT the posting date. It is the date the req was (re)published to this
  board and moves on repost: a credit-union tenant's "AVP Branch Manager" lists 2026-09-14 but its
  detail PostedDate is 2026-05-19; another tenant's req lists 2026-09-16 against a detail 2026-06-30. The
  list value goes to extra["refreshed"]; the detail PostedDate is the posted date.
- JobLocationType is an enum whose labels live in the site's locale file
  (/Content/locales/en-US/translation.json): 0 Hybrid, 1 On-site, 2 Remote, null not set.
  Only 2 adds a bare "Remote" segment.
- Address.State.Code is only trusted for USA addresses: an Australian "WA" or "NSW" state code
  would otherwise read as Washington to the uppercase state-code check. Non-US addresses use the
  state name. Country.Name is always appended ("United States", "United Kingdom").
- Top is honoured to at least 500 but pages advance on what came back (Skip += len), stopping at
  totalCount or an empty page. An unknown board guid answers 200 with totalCount 0, so an empty
  first page raises.
- Structured pay is almost always empty: PayRange is {PayRangeMinimum, PayRangeMaximum} gated by
  PayRangeVisible (false on every req of the tenants checked); some tenants print their US bands
  in the Description body instead ("$119,800 - $138,000"), which comp_from_description reads.
  CompensationAmount can carry a single hidden figure (USD 185000.00 with PayRangeVisible
  false); it is not a published band and is ignored.
- A <tenant>.ultipro.com host can be the employee SSO login, not the job board; the public board
  is the one linked from the employer's own careers page.
"""
import json

NAME = "ukg"
ENUMERABLE = True
REQUIRED = ["base"]

PAGE = 50
MAX_PAGES = 200
LOCATION_TYPES = {0: "Hybrid", 1: "On-site", 2: "Remote"}
_MARK = "CandidateOpportunityDetail("


def _body(skip):
    return {"opportunitySearch": {"Top": PAGE, "Skip": skip, "QueryString": "",
                                  "OrderBy": [{"Value": "postedDateDesc", "PropertyName": "PostedDate",
                                               "Ascending": False}],
                                  "Filters": [{"t": "TermsSearchFilterDto", "fieldName": 4, "extra": None, "values": []},
                                              {"t": "TermsSearchFilterDto", "fieldName": 5, "extra": None, "values": []},
                                              {"t": "TermsSearchFilterDto", "fieldName": 6, "extra": None, "values": []}]},
            "matchCriteria": {"PreferredJobs": [], "Educations": [], "LicenseAndCertifications": [], "Skills": [],
                              "hasNoLicenses": False, "SkippedSkills": []}}


def _place(loc):
    a = loc.get("Address") or {}
    country = a.get("Country") or {}
    state = a.get("State") or {}
    is_us = (country.get("Code") or "").upper() in ("USA", "US")
    st = (state.get("Code") if is_us else state.get("Name")) or state.get("Name")
    parts = [a.get("City"), st, country.get("Name")]
    s = ", ".join(p.strip() for p in parts if p and p.strip())
    return s or (loc.get("LocalizedDescription") or loc.get("LocalizedName") or "").strip()


def _location(o):
    segs = [_place(l) for l in o.get("Locations") or [] if isinstance(l, dict)]
    segs = list(dict.fromkeys(s for s in segs if s))
    if o.get("JobLocationType") == 2:
        segs.append("Remote")
    return " | ".join(segs)


def list_jobs(t, h, smoke=False):
    base = t["base"].rstrip("/")
    out, skip, total = {}, 0, None
    for _ in range(MAX_PAGES):
        d = h.post_json(f"{base}/JobBoardView/LoadSearchResults", _body(skip))
        ops = d.get("opportunities") or []
        total = d.get("totalCount") if total is None else total
        if skip == 0 and not ops:
            raise RuntimeError(f"ukg {base}: board answered with no opportunities (totalCount={total})")
        for o in ops:
            jid = o.get("Id")
            if not jid: continue
            lt = o.get("JobLocationType")
            out[jid] = h.norm(t["company"], NAME, jid, o.get("Title"), _location(o),
                              f"{base}/OpportunityDetail?opportunityId={jid}", None,
                              o.get("BriefDescription") or "", None,
                              {"refreshed": h.iso_date(o.get("PostedDate")), "date_kind": "refreshed",
                               "requisition": o.get("RequisitionNumber"),
                               "category": o.get("JobCategoryName"), "full_time": o.get("FullTime"),
                               "location_type": LOCATION_TYPES.get(lt, lt)})
        skip += len(ops)
        if smoke or not ops or (total is not None and skip >= total):
            break
    else:
        raise RuntimeError(f"ukg {base}: page cap {MAX_PAGES} hit at skip {skip}, total {total}")
    return list(out.values())


def parse_detail(markup):
    i = markup.find(_MARK)
    if i < 0:
        raise RuntimeError("ukg detail: CandidateOpportunityDetail JSON not found in page")
    d, _ = json.JSONDecoder().raw_decode(markup[i + len(_MARK):])
    return d


def _comp(d):
    lo, hi = d.get("CompensationAnnualMinimum"), d.get("CompensationAnnualMaximum")
    if lo and hi and (d.get("CompensationCurrencyCode") or "USD") in ("USD", None):
        return f"${lo:,.0f} - ${hi:,.0f}"
    pr = d.get("PayRange") if d.get("PayRangeVisible") else None
    if isinstance(pr, dict) and pr.get("PayRangeMinimum") and pr.get("PayRangeMaximum") \
            and (d.get("PayRangeCurrencyCode") or "USD") == "USD":
        return f"${float(pr['PayRangeMinimum']):,.0f} - ${float(pr['PayRangeMaximum']):,.0f}"
    return None


def detail(t, row, h):
    d = parse_detail(h.get_text(row["url"]))
    desc = h.strip_html(d.get("Description"))
    if desc:
        row["description"] = desc
    row["posted"] = h.iso_date(d.get("PostedDate")) or row.get("posted")
    comp = _comp(d) or h.comp_from_description(row["description"])
    if comp: row["comp"] = comp
    loc = _location(d)
    if loc: row["location"] = loc
    row["extra"].update({"date_kind": "posted", "updated": h.iso_date(d.get("UpdatedDate")),
                         "closed": d.get("OpportunityIsClosed")})
    return row
