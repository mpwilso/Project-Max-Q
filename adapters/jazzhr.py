"""
jazzhr - JazzHR (Resumator) public job boards (<slug>.applytojob.com/apply).

    {"company": "Fabrikam Freight", "ats": "jazzhr", "slug": "fabrikamfreight"}

List:  GET https://app.jazz.co/feeds/export/jobs/<slug>    (XML job feed, the whole board, JD inline)
Flags: GET https://<slug>.applytojob.com/apply            (HTML board, read only for the remote label)

Traps (verified against three live boards):
- Nothing on the board carries a date: no datePosted in the list, the detail page or the feed.
  The feed's job <id> is "job_YYYYMMDDhhmmss_<RANDOM>", the req's creation timestamp, so posted is
  read from it with extra["date_kind"] = "created" (an evergreen "Support Engineer" req can carry
  job_20250204... a year and more later).
- The feed gives the req's office address (Austin TX United States) and no remote flag; the HTML
  board prints "Remote" in place of the address for remote reqs. The two reads are joined on the
  board code (the /apply/<code>/ path segment), and a remote label adds a bare "Remote" segment.
- /apply/jobs/feed and /rss are 404/410 or redirect to jazzhr.com; the XML feed on app.jazz.co is
  the working one. An unknown slug answers a feed with no <job> elements, so zero jobs raises.
- Links on employer careers pages go stale: a careers site can link an /apply/<code> that is no
  longer on the board or in the feed.
- The feed's <id> is the stable id; the board code in the URL is a separate public code.
"""
import re
import xml.etree.ElementTree as ET

NAME = "jazzhr"
ENUMERABLE = True
REQUIRED = ["slug"]

FEED = "https://app.jazz.co/feeds/export/jobs/{slug}"
BOARD = "https://{slug}.applytojob.com/apply"
_CODE = re.compile(r"/apply/([A-Za-z0-9]+)(?:/|$)")
_ITEM = re.compile(r'<li class="list-group-item">(.*?)</li>\s*</ul>\s*</li>', re.S)
_ID_TS = re.compile(r"job_(\d{4})(\d{2})(\d{2})\d{6}_")


def board_labels(markup):
    """{board_code: location label} from the HTML board."""
    out = {}
    for item in (m.group(0) for m in _ITEM.finditer(markup or "")):
        m = re.search(r'href="[^"]*/apply/([A-Za-z0-9]+)/', item)
        loc = re.search(r"fa-map-marker'></i>([^<]*)<", item)
        if m:
            out[m.group(1)] = (loc.group(1).strip() if loc else "")
    return out


def _text(j, k):
    return (j.findtext(k) or "").strip()


def list_jobs(t, h, smoke=False):
    slug = t["slug"]
    xml = h.get_text(FEED.format(slug=h.quote(slug)))
    try:
        root = ET.fromstring(xml.encode("utf-8") if isinstance(xml, str) else xml)
    except ET.ParseError as e:
        raise RuntimeError(f"jazzhr {slug}: feed is not XML ({e}): {str(xml)[:200]}")
    jobs = root.findall("job")
    if not jobs:
        raise RuntimeError(f"jazzhr {slug}: feed has no jobs")
    labels = board_labels(h.get_text(BOARD.format(slug=slug)))
    out = []
    for j in jobs:
        jid = _text(j, "id")
        if not jid or _text(j, "status").lower() not in ("open", ""): continue
        url = _text(j, "url").replace("http://", "https://", 1)
        m = _CODE.search(url)
        code = m.group(1) if m else None
        place = ", ".join(x for x in (_text(j, "city"), _text(j, "state"), _text(j, "country")) if x)
        label = labels.get(code, "")
        segs = [place] if place else ([label] if label else [])
        if "remote" in label.lower():
            segs.append("Remote")
        ts = _ID_TS.match(jid)
        posted = h.iso_date(f"{ts.group(1)}-{ts.group(2)}-{ts.group(3)}") if ts else None
        out.append(h.norm(t["company"], NAME, jid, _text(j, "title"), " | ".join(dict.fromkeys(segs)), url, posted,
                          h.strip_html(_text(j, "description")), None,                          {"date_kind": "created", "board_code": code, "board_label": label,
                           "department": _text(j, "department"), "type": _text(j, "type"),
                           "experience": _text(j, "experience")}))
    if not out:
        raise RuntimeError(f"jazzhr {slug}: {len(jobs)} feed jobs, none open")
    return out
