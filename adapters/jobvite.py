"""
jobvite - Jobvite's public careers XML feed.

Finding the company code: an employer's careers site (careers.example.com, say) links every
job's Apply button to app.jobvite.com/CompanyJobs/Careers.aspx?c=<code>. That code opens the
public feed
    https://app.jobvite.com/CompanyJobs/Xml.aspx?c=<code>
which returns the whole board in one response, JD html included (several MB on a large board).

Enumerable: one response is the whole feed. Checked against both public listings of one large
employer: every req paged through its own careers site and every id on jobs.jobvite.com/<slug> is
in the feed.

Multi-location postings. Many nodes carry <parentId>: a copy of a parent posting for one more city
(same title, requisition, date and description; id "<parent>-<suffix>"). The copy has no job page
of its own (jobs.jobvite.com/<slug>/job/<child id> redirects to ?error=404), so copies are folded
into their parent: one row per parent id, locations joined.

Dates: <date> ("9/14/2026") is the posting date Jobvite shows on the job; there is no refresh date.
Locations: "Denver, CO, United States". The feed has no remote field, but some employers' JDs carry a
labelled "Work Arrangement" section ("Remote: This position is primarily remote...", "Hybrid: ...",
"On Site: ..."). A bare "Remote" segment is added only when that section says remote, and the
section text goes to extra.work_arrangement.
Comp: some employers write "between USD $ 202,000 and USD $ 302,000 per year", which MONEY_RANGE_RE
cannot read ("and", repeated currency), so norm() almost never finds a band. USD bands in that
shape are parsed here; CAD/EUR bands are left alone.

The feed also carries internal-looking fields (hiring team names, referral bonus, agency access).
They are deliberately NOT copied into extra.

Target keys: code (required, the c= value), slug (optional, the jobs.jobvite.com/<slug> path used
for row urls; without it the feed's detail-url is used), subsidiary (optional, see below).

Parent-company feeds. A subsidiary's jobs.jobvite.com/<slug> page can carry a company code whose
feed is the whole parent company, with only part of its rows belonging to that subsidiary (the
other brands' /<slug>/job/<id> urls redirect to ?error=404). Each node carries a "Subsidiary Career
Site" field (<subsidiary_x0020_career_x0020_site_x002A_>Initech</...>; some nodes spell the tag
without the _x002A_). `subsidiary` keeps only nodes whose value matches (case-insensitive), copies
included; on the tenant probed, the parents it kept were exactly the ids on the subsidiary's
public /<slug>/jobs page. A feed that has jobs
but none for the subsidiary raises (renamed label) rather than returning a quiet zero.
"""
import re
import xml.etree.ElementTree as ET

NAME = "jobvite"
ENUMERABLE = True
REQUIRED = ["code"]

FEED = "https://app.jobvite.com/CompanyJobs/Xml.aspx?c={}"
_ARRANGEMENT = re.compile(r"Work Arrangement\s*:?\s*(.{0,160})", re.S)
_REMOTE = re.compile(r"^(?:remote\b|this position is primarily remote|this role is remote)", re.I)
_USD_BAND = re.compile(r"between\s+USD\s*\$\s?(\d[\d,]*\d)\s*(?:and|to|-|–)\s*(?:USD\s*)?\$\s?(\d[\d,]*\d)", re.I)


def work_arrangement(desc):
    m = _ARRANGEMENT.search(desc or "")
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else None


def usd_band(desc):
    m = _USD_BAND.search(desc or "")
    return f"${m.group(1)} - ${m.group(2)}" if m else None


def _txt(node, tag):
    return (node.findtext(tag) or "").strip()


def parse_feed(xml_text):
    if isinstance(xml_text, str): xml_text = xml_text.encode("utf-8")
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise RuntimeError(f"jobvite: feed is not XML ({e}); the company code may be wrong or the feed disabled")
    return root.findall("job")


def _feed_text(h, url):
    # get_text decodes with the response's charset; the feed declares utf-8 in both header and prolog.
    return h.get_text(url, headers={"Accept": "text/xml,application/xml,*/*"})


def subsidiary(node):
    """The node's "Subsidiary Career Site" value. The XML tag is the escaped field label and comes in
    two spellings on one feed (subsidiary_x0020_career_x0020_site_x002A_ and ..._site), so match on
    the prefix."""
    for c in node:
        if c.tag.lower().startswith("subsidiary_x0020_career_x0020_site"):
            return (c.text or "").strip()
    return ""


def list_jobs(t, h, smoke=False):
    jobs = parse_feed(_feed_text(h, FEED.format(t["code"])))
    if t.get("subsidiary"):
        want = t["subsidiary"].strip().lower()
        total = len(jobs)
        jobs = [j for j in jobs if subsidiary(j).lower() == want]
        if total and not jobs:
            raise RuntimeError(f"jobvite: feed {t['code']!r} has {total} jobs but none for subsidiary "
                               f"{t['subsidiary']!r}; the subsidiary label may have been renamed")
    parents, children = {}, {}
    for j in jobs:
        jid, pid = _txt(j, "id"), _txt(j, "parentId")
        if not jid: continue
        if pid: children.setdefault(pid, []).append(j)
        else: parents.setdefault(jid, j)
    out = []
    for jid, j in parents.items():
        locs = [_txt(j, "location")] + [_txt(c, "location") for c in children.get(jid, [])]
        desc = h.strip_html(j.findtext("description") or "")
        arr = work_arrangement(desc)
        if arr and _REMOTE.search(arr): locs.append("Remote")
        url = (f"https://jobs.jobvite.com/{t['slug']}/job/{jid}" if t.get("slug")
               else _txt(j, "detail-url").replace("http://", "https://"))
        out.append(h.norm(t["company"], NAME, jid, _txt(j, "title"),
                          " | ".join(dict.fromkeys(x for x in locs if x)), url,
                          h.iso_date(_txt(j, "date")), desc, usd_band(desc),
                          {"requisition": _txt(j, "requisitionid"), "category": _txt(j, "category"),
                           "work_arrangement": arr,
                           "jobtype": _txt(j, "jobtype"), "region": _txt(j, "region"),
                           "locations_folded": len(children.get(jid, []))}))
    # Orphan copies (parent absent from the feed) would otherwise vanish.
    for pid, cs in children.items():
        if pid in parents: continue
        c = cs[0]
        out.append(h.norm(t["company"], NAME, _txt(c, "id"), _txt(c, "title"),
                          " | ".join(dict.fromkeys(_txt(x, "location") for x in cs if _txt(x, "location"))),
                          _txt(c, "detail-url").replace("http://", "https://"), h.iso_date(_txt(c, "date")),
                          h.strip_html(c.findtext("description") or ""), None,
                          {"requisition": _txt(c, "requisitionid"), "orphan_of": pid}))
    if not out:
        raise RuntimeError(f"jobvite: 0 jobs in the feed for code {t['code']!r}")
    return out
