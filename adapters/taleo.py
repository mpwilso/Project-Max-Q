"""
taleo - Oracle Taleo classic career sections (<tenant>.taleo.net/careersection/<section>/). Reference
tenant shape: <tenant>.taleo.net/careersection/10000.

targets.json: {"company": "Northwind Timber", "ats": "taleo", "base": "https://northwind.taleo.net/careersection/10000"}

Enumerable: the pager walks the whole section (the row count equals listRequisition.nbElements).

Traps, each verified live:
  - The REST route newer Taleo sections use (POST /careersection/rest/jobboard/searchjobs) answers
    200 with {"careerSectionUnAvailable": true} here, with or without a portal id. Classic sections
    are "FTL" pages: the job list is a "!|!"-delimited value stream, in the jobsearch.ftl
    initialHistory field for page 1 and in the <td id="response"> of POST jobsearch.ajax (the
    pager's own XHR) for every page. The POST needs no cookie or token.
  - PAGE SIZE 25 LOSES ROWS. At the default 25 per page, a full walk returns the right number of
    rows but about a tenth of them are repeats (the server re-sorts between requests), so some ids
    never appear. dropListSize=100 with listRequisition.size=100 returns every id. 100 is the largest option offered.
  - A page past the end re-serves the last page, so the walk stops on "nothing new".
  - Each record is id, title, id, title, id x5, primary locations, a remote-less flag, four
    "other locations" fields, posting date, closing date, contest number. Titles are partly
    percent-encoded ("Time %26 Attendance Systems Analyst"); "%5C:" is an escaped colon.
  - The posting date is the section's "Posting Date" column (dates run back several weeks,
    with closing dates 15-30 days out), stored as posted. The closing date goes to extra.closing.
  - The job page is jobdetail.ftl?job=<contest number>. Its JD is in initialHistory too: the
    fields that start with "!*!" are percent-encoded HTML (description, qualifications), each
    repeated twice.
  - Some tenants' Taleo sections (<tenant>.taleo.net/careersection/external) redirect job search
    to a login page; their public board is elsewhere (e.g. TalentBrew, built-in radancy), not this.
"""
import re
from urllib.parse import unquote

NAME = "taleo"
ENUMERABLE = True
REQUIRED = ["base"]
PAGE = 100
MAX_PAGES = 60

_REC = re.compile(r"!\|!(\d+)!\|!([^!]*)!\|!\1!\|!\2!\|!\1!\|!\1!\|!\1!\|!\1!\|!\1!\|!([^!]*)!\|!(?:true|false)!\|!"
                  r"([^!]*)!\|!([^!]*)!\|!([^!]*)!\|!([^!]*)!\|!([^!]*)!\|!([^!]*)!\|!([^!]*)!\|!")


def _txt(s):
    return re.sub(r"\s+", " ", unquote(s or "").replace("\\:", ":")).strip()


def parse_list(payload):
    """-> (nbElements or None, [(id, title, locations, posted_text, closing_text, contest_no)])."""
    nb = re.search(r"listRequisition\.nbElements!\|!(\d+)", payload or "")
    out = []
    for jid, title, loc, _o1, _o2, _o3, other, posted, closing, contest in _REC.findall(payload or ""):
        locs = [x.strip() for x in _txt(loc).split(",") if x.strip()]
        locs += [x.strip() for x in _txt(other).split(",") if x.strip()]
        out.append((jid, _txt(title), list(dict.fromkeys(locs)), posted, closing, contest))
    return (int(nb.group(1)) if nb else None), out


def _form(page):
    return {"iframemode": "1", "ftlpageid": "reqListBasicPage", "ftlinterfaceid": "requisitionListInterface",
            "ftlcompid": "rlPager", "jsfCmdId": "rlPager", "ftlcompclass": "PagerComponent",
            "ftlcallback": "ftlPager_processResponse", "ftlajaxid": "ftlx1", "rlPager.currentPage": str(page),
            "lang": "en", "dropListSize": str(PAGE), "listRequisition.size": str(PAGE)}


def list_jobs(t, h, smoke=False):
    base = t["base"].rstrip("/")
    seen, total = {}, None
    for page in range(1, MAX_PAGES + 1):
        r = h.request("POST", f"{base}/jobsearch.ajax", {"User-Agent": h.UA, "Referer": f"{base}/jobsearch.ftl?lang=en"},
                      data=_form(page))
        nb, recs = parse_list(r.text)
        if total is None: total = nb
        new = 0
        for jid, title, locs, posted, closing, contest in recs:
            if jid in seen: continue
            new += 1
            seen[jid] = h.norm(t["company"], NAME, jid, title, " | ".join(locs),
                               f"{base}/jobdetail.ftl?job={contest}&lang=en", h.iso_date(posted), "", None,
                               {"contest": contest, "closing": h.iso_date(closing)})
        if smoke or not recs or not new or (total is not None and len(seen) >= total): break
    else:
        raise RuntimeError(f"taleo {base}: page cap {MAX_PAGES} hit")
    if not seen:
        raise RuntimeError(f"taleo {base}: 0 rows parsed (nbElements={total}); section closed or markup changed")
    if total and len(seen) < total * 0.9:
        raise RuntimeError(f"taleo {base}: parsed {len(seen)} of nbElements {total}; record layout changed")
    return list(seen.values())


def parse_detail(page):
    m = re.search(r'name="initialHistory" id="initialHistory" value="([^"]*)"', page or "")
    if not m: return ""
    blocks = [unquote(p[3:]) for p in m.group(1).split("!|!") if p.startswith("!*!")]
    return "\n\n".join(dict.fromkeys(b for b in blocks if b.strip()))


def detail(t, row, h):
    html = parse_detail(h.get_text(row["url"]))
    if html:
        row["description"] = h.strip_html(html)
    if not row.get("comp"):
        row["comp"] = h.comp_from_description(row["description"])
    return row
