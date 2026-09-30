"""
tiktok - TikTok careers (lifeattiktok.com), TikTok's own board.

    {"company": "TikTok", "ats": "tiktok"}

Endpoint: POST https://api.lifeattiktok.com/api/v1/public/supplier/search/job/posts with header
`website-path: tiktok` (the only non-default header needed; without it the API does not answer
for this portal). Body:
    {"recruitment_id_list": [], "job_category_id_list": [], "subject_id_list": [],
     "location_code_list": [], "keyword": "", "limit": 500, "offset": N}
-> {"code": 0, "data": {"job_post_list": [...], "count": <whole board>}}

Verified against the live board:
  - limit is honoured up to 500. A no-filter walk returned exactly `count` unique ids, so it is the
    whole global board (a few thousand rows, under ten pages). `count` drifts by one between pages
    while the board changes; rows are de-duplicated on id and the walk stops on a short page.
  - location_code_list only filters at CITY level. State and country codes answer count 0, not an
    error, which is indistinguishable from an empty board. So the adapter walks the whole board and
    keeps the target's countries in code (default United States + Canada).
  - Each row carries the full JD: description + requirement. The list has NO pay band; the band
    ("The base salary range for this position in the selected city is $X - $Y") is only in the
    server-rendered page at https://lifeattiktok.com/search/<id>. detail() reads it, but sweep.py
    only calls detail on rows without a description, so a sweep leaves comp empty: fetch the band
    with detail() when scoring a req.
  - NO posted date anywhere, on the list or the page. The job id is a snowflake whose top 32 bits
    are epoch seconds (the newest ids on the board decode to the day before the walk). posted =
    that date, with extra.date_kind = "id_timestamp": it is the req's creation, inferred, never a
    refresh.
  - recruit_type is Regular, Intern or "Third-party Associate" (agency contract). A target may drop
    types with "exclude_recruit_types": [...].
  - city_info is a chain city -> state -> country (codes CT_/ST_/CN_), present on every row.
    "United States of America" is written "United States" for the fail-closed location gate.

The id is the numeric job id in the public URL; the short `code` goes to extra.
"""
import datetime as dt
import re

NAME = "tiktok"
ENUMERABLE = True
REQUIRED = []

API = "https://api.lifeattiktok.com/api/v1/public/supplier/search/job/posts"
PAGE = "https://lifeattiktok.com/search/"
HEADERS = {"website-path": "tiktok"}
LIMIT = 500
MAX_PAGES = 40                       # a whole-board walk needs under ten
DEFAULT_COUNTRIES = ["United States of America", "Canada"]


def _chain(j):
    out, c = [], j.get("city_info")
    while isinstance(c, dict):
        out.append((c.get("en_name") or c.get("i18n_name") or "").strip())
        c = c.get("parent")
    return [x for x in out if x]


def country(j):
    ch = _chain(j)
    return ch[-1] if ch else None


def location(j):
    ch = ["United States" if x == "United States of America" else x for x in _chain(j)]
    # "Dublin, Dublin, Ireland": a city named for its state is written once.
    return ", ".join(dict.fromkeys(ch))


def id_date(jid):
    try:
        return dt.datetime.fromtimestamp(int(jid) >> 32, dt.timezone.utc).date().isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _name(o):
    return (o or {}).get("en_name") or (o or {}).get("i18n_name") if isinstance(o, dict) else None


def to_row(t, j, h):
    jid = str(j.get("id"))
    desc = "\n\n".join(x.strip() for x in (j.get("description"), j.get("requirement")) if x and x.strip())
    title = re.sub(r"\s+", " ", j.get("title") or "").strip()
    return h.norm(t.get("company", "TikTok"), NAME, jid, title, location(j), PAGE + jid, id_date(jid),
                  desc, None,
                  {"code": j.get("code"), "recruit_type": _name(j.get("recruit_type")),
                   "category": _name(j.get("job_category")), "subject": _name(j.get("job_subject")),
                   "date_kind": "id_timestamp"})


def list_jobs(t, h, smoke=False):
    countries = set(t.get("countries") or DEFAULT_COUNTRIES)
    drop_types = {x.lower() for x in t.get("exclude_recruit_types") or []}
    out, seen, offset, total = [], set(), 0, None
    for _ in range(MAX_PAGES):
        body = {"recruitment_id_list": [], "job_category_id_list": [], "subject_id_list": [],
                "location_code_list": [], "keyword": "", "limit": LIMIT, "offset": offset}
        d = h.post_json(API, body, headers=HEADERS)
        if not isinstance(d, dict) or d.get("code") not in (0, None) or not isinstance(d.get("data"), dict):
            raise RuntimeError(f"tiktok: unexpected answer at offset {offset}: {str(d)[:200]}")
        data = d["data"]
        jobs = data.get("job_post_list") or []
        if total is None: total = data.get("count") or 0
        for j in jobs:
            jid = str(j.get("id") or "")
            if not jid or jid in seen: continue
            seen.add(jid)
            if country(j) not in countries: continue
            if (_name(j.get("recruit_type")) or "").lower() in drop_types: continue
            out.append(to_row(t, j, h))
        offset += len(jobs)
        if smoke or not jobs or offset >= (data.get("count") or total): break
    else:
        raise RuntimeError(f"tiktok: still paging after {MAX_PAGES} pages (count={total}); raise MAX_PAGES")
    if not seen:
        raise RuntimeError(f"tiktok: 0 rows on the board (count={total}); header or body contract changed")
    return out


def detail(t, row, h):
    """The pay band lives only on the server-rendered job page; the house parser reads its sentence."""
    text = h.strip_html(h.get_text(PAGE + row["id"]))
    if not row.get("comp"): row["comp"] = h.comp_from_description(text)
    if not row.get("description"): row["description"] = text
    return row
