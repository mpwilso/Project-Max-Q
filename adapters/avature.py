"""
Avature career portals (the vendor's hosted "<locale>/<portal>/SearchJobs" sites).

targets.json:
  {"company": "Tailspin Toys", "ats": "avature", "list_url": "https://careers.tailspin.example/en_US/careers/SearchJobs"}
  {"company": "Fabrikam Freight", "ats": "avature", "list_url": "https://jobs.fabrikam.example/en_US/jobs/Jobs",
   "params": {"31207": "2204518"}, "list_location": "United States"}
  list_url       the portal's search list page, exactly as the site links it (locale path included).
  params         optional GET filters. Avature names filters by numeric form-field id, and the ids are
                 per portal: on one portal the Country/Region select was field 31207 and United
                 States was option 2204518 (read off the <select name="31207"> on the list page).
  list_location  optional. Stamped as the list-level location when the list page shows none. Only
                 truthful when `params` itself enforces it (the US country filter above). detail()
                 replaces it with the job page's city/state.

Enumerable: the list page with no keyword is the whole board (or the whole filtered board).

Traps, each verified live:
  - Page size is fixed by the portal (20 on the portals checked) and jobRecordsPerPage /
    folderRecordsPerPage in the URL are ignored. The offset parameter's NAME differs per portal:
    jobOffset on /JobDetail/ portals, folderOffset on /FolderDetail/ ones. Both are read off the
    pager links on the first page.
  - The results count is sometimes lazy: an unfiltered folder portal said "of 999+ results", and an
    odd query said "of 21+". A count with "+" is a floor, never a stop condition; the walk stops on
    an empty page or a page with no new ids.
  - On some portals the RSS feed (<list_url>/feed/) is the only place a date is published, and it is
    useless for enumeration: it always returns 20 items, oldest first, ignoring jobOffset,
    jobRecordsPerPage and every sort parameter. A keyword search there is fuzzy, so it cannot be used
    to look a date up per job either. Rows from such portals therefore carry NO posted date;
    freshness comes from first_seen. Folder portals' job pages carry a schema.org datePosted, and it
    is a real posting date (it matched the feed's pubDate where both existed), so detail() fills it.
  - A /JobDetail/ portal's list location can be the PRIMARY location only. A req whose primary is
    abroad and whose secondary is a US site fails the fail-closed US gate at list level and never
    reaches detail(). That costs a small share of rows; accepted rather than fetching every job page.
  - Slugs go stale on retitle (the slug can omit words the current title has); the id is the key.
  - Some list tiles have a location ("Austin, United States of America"); folder portals' tiles have
    only title and id (the rest is fetched on click from /JobInfo?jobId=), hence list_location.
  - Folder-portal location fields on the job page are three unlabeled posting-location values
    (country, state, city). /JobDetail/ portals use a single "Locations: a<br>b" value.
"""
import html as _html
import re
from urllib.parse import urlencode

NAME = "avature"
ENUMERABLE = True
REQUIRED = ["list_url"]
MAX_PAGES = 300

# The slug segment is optional: some portals link /JobDetail/<slug>/<id>, others /JobDetail/<id>.
_DETAIL_HREF = re.compile(r'href="(https?://[^"]+/(?:JobDetail|FolderDetail)/(?:[^"]*?/)?(\d+))"[^>]*>(.*?)</a>', re.S)


def _text(s):
    s = re.sub(r"<br\s*/?>", "\n", s or "", flags=re.I)
    s = _html.unescape(re.sub(r"<[^>]+>", " ", s))
    return re.sub(r"[ \t ]+", " ", s).strip()


def _one_line(s):
    return re.sub(r"\s+", " ", _text(s)).strip()


def offset_param(page):
    m = re.search(r"[?&;](jobOffset|folderOffset)=", page)
    return m.group(1) if m else None


def total_count(page):
    """-> (count, exact). 'of 318 results' -> (318, True); 'of 999+ results' -> (999, False)."""
    m = re.search(r"of\s+([\d,]+)(\+?)\s+results", page)
    if not m: return None, False
    return int(m.group(1).replace(",", "")), not m.group(2)


def parse_list(page):
    """-> [(id, title, url, location, extra)] from one list page."""
    out = []
    for art in re.split(r'<article class="article article--result', page)[1:]:
        m = _DETAIL_HREF.search(art)
        if not m: continue
        url, jid, title = m.group(1), m.group(2), _one_line(m.group(3))
        # Some portals nest city/state/country spans inside list-item-location, so a lazy match to
        # the first </span> would keep only the city. Read the parts when they exist.
        parts = [_one_line(re.search(rf'class="list-item-{k}">(.*?)</span>', art, flags=re.S).group(1))
                 for k in ("jobCity", "jobState", "jobCountry")
                 if re.search(rf'class="list-item-{k}">(.*?)</span>', art, flags=re.S)]
        loc = None if parts else re.search(r'class="list-item-location">(.*?)</span>', art, flags=re.S)
        extra = {}
        for k in ("workerType", "department"):
            v = re.search(rf'class="list-item-{k}">(.*?)</span>', art, flags=re.S)
            if v: extra[k] = _one_line(v.group(1))
        location = ", ".join(p for p in parts if p) if parts else (_one_line(loc.group(1)) if loc else "")
        out.append((jid, title, url, location, extra))
    return out


def list_jobs(t, h, smoke=False):
    base = t["list_url"].rstrip("/")
    params = dict(t.get("params") or {})
    seen, offset, param = {}, 0, None
    total, exact = None, False
    for page_no in range(MAX_PAGES):
        q = dict(params)
        if page_no: q[param] = offset
        url = base + ("/?" + urlencode(q) if q else "")
        page = h.get_text(url)
        items = parse_list(page)
        if page_no == 0:
            param = offset_param(page) or t.get("offset_param") or "jobOffset"
            total, exact = total_count(page)
            if not items:
                if total == 0: raise RuntimeError(f"avature {base}: portal says 0 results for {params or 'no filter'}")
                raise RuntimeError(f"avature {base}: no result articles parsed (count {total}); markup changed")
        new = 0
        for jid, title, jurl, loc, extra in items:
            if jid in seen: continue
            new += 1
            seen[jid] = h.norm(t["company"], NAME, jid, title, loc or t.get("list_location") or "", jurl,
                               None, "", None, extra)
        offset += len(items)
        if smoke or not items or not new or (exact and total is not None and offset >= total): break
    else:
        raise RuntimeError(f"avature {base}: page cap {MAX_PAGES} hit at offset {offset}")
    return list(seen.values())


def _dataset_locations(val_html):
    """Additional locations come as <ul class="MultipleDataSetFields"> blocks of label/value pairs.
    The labels differ per portal (one: Location/State/Country; another: Country/
    State/Province/County/City, with the country in capitals), so they are read by keyword."""
    out = []
    for ul in re.findall(r'<ul class="MultipleDataSetFields">(.*?)</ul>', val_html, flags=re.S):
        parts = {"city": "", "state": "", "country": ""}
        for lab, val in re.findall(r'MultipleDataSetFieldLabel">(.*?)</span>\s*<span class="MultipleDataSetFieldValue">'
                                   r'(.*?)</span>', ul, flags=re.S):
            lab, val = _one_line(lab).lower(), _one_line(val)
            if val.isupper() and len(val) > 3: val = val.title().replace(" Of ", " of ").replace(" And ", " and ")
            key = "country" if "country" in lab else "state" if "state" in lab else "city"
            if not parts[key]: parts[key] = val
        name = ", ".join(v for v in (parts["city"], parts["state"], parts["country"]) if v)
        if name: out.append(name)
    return out


def parse_job(page):
    """-> dict(posted, locations, remote, fields, description) from an Avature job detail page."""
    posted = None
    m = re.search(r'"datePosted"\s*:\s*"([^"]+)"', page)
    if m: posted = m.group(1)
    fields, desc_parts, posting_loc, locations, extra_locs = {}, [], [], [], []
    for m in re.finditer(r'<div class="article__content__view__field([^"]*)"[^>]*>(.*?)'
                         r'(?=<div class="article__content__view__field[ "]|</article>)', page, flags=re.S):
        cls, body = m.group(1), m.group(2)
        lab = re.search(r'article__content__view__field__label[^>]*>(.*?)</div>', body, flags=re.S)
        val = re.search(r'article__content__view__field__value[^>]*>(.*)', body, flags=re.S)
        val_html = val.group(1) if val else ""
        if "additional-posting-locations" in cls:
            extra_locs += _dataset_locations(val_html)
        elif "posting-location" in cls:
            v = _one_line(val_html)
            if v: posting_loc.append(v)
        elif "field--locations" in cls:
            head = re.split(r'<ul class="MultipleDataSetFields"', val_html)[0]
            head = re.sub(r"<strong>\s*Locations?\s*</strong>\s*:?", "", head, flags=re.I)
            locations += [x.strip(" ,") for x in _text(head).split("\n") if x.strip(" ,")]
            extra_locs += _dataset_locations(val_html)
        elif lab:
            fields[_one_line(lab.group(1))] = _one_line(val_html)
        else:
            txt = _text(val_html)
            if len(txt) > 80: desc_parts.append(val_html)
    if posting_loc:
        locations.append(", ".join(reversed(posting_loc)))     # city, state, country
    locations += extra_locs
    mode = (fields.get("Work Model") or fields.get("Remote vs. Office") or "").lower()
    return {"posted": posted, "locations": list(dict.fromkeys(locations)),
            "remote": "remote" in mode and "office" not in mode and "hybrid" not in mode,
            "fields": fields, "description_html": "\n".join(desc_parts)}


def detail(t, row, h):
    d = parse_job(h.get_text(row["url"]))
    if d["description_html"]:
        row["description"] = h.strip_html(d["description_html"])
    locs = list(d["locations"])
    if d["remote"]: locs.append("Remote")
    if locs:
        row["location"] = " | ".join(locs)
    if d["posted"]:
        row["posted"] = h.iso_date(d["posted"])
    keep = ("Work Model", "Remote vs. Office", "Worker Type", "Studio/Department", "Organization",
            "Business Unit", "Experience Level", "Full / Part time", "Company")
    row["extra"].update({k: v for k, v in d["fields"].items() if k in keep})
    if not row.get("comp"):
        row["comp"] = h.comp_from_description(row["description"])
    return row
