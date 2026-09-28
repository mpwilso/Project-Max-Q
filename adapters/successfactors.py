"""
SAP SuccessFactors Recruiting Marketing (RMK) career sites, served from the employer's own careers host.

targets.json:  {"company": "Initech", "ats": "successfactors", "origin": "https://careers.initech.example"}
  origin   scheme + host of the RMK site. Use the host ROOT, not a brand path: /Globex/ is one
           brand page, while /search/ at the root lists every brand on the tenant (Globex and
           GlobexCanada), which is what the sitemap also lists.

Enumerable. /search/?q=&startrow=N with an empty query returns the whole board. Verified against
the live boards: the row count equals both the "of N Jobs" total and the sitemap's URL count.

Traps, each verified live:
  - Page size is set per tenant and ignores the URL: one tenant serves 25 tiles, another 5.
    Advance startrow by the tiles that came back and stop on the "Showing a to b of N Jobs" total.
  - The tile columns are per tenant too: one tenant shows a Date column, another does not. Fields
    are read by id (job-<id>-desktop-section-<field>-value); each tile repeats every field for the
    tablet and mobile layouts, so reading by class alone triples them.
  - THE DATE IS A REFRESH STAMP, NOT A POSTING DATE. Every row on both tenants is dated within the
    last four weeks, including an evergreen freelance req on one tenant whose id is
    hundreds of millions below the current ones. RMK re-stamps the
    posting start date on each repost cycle. Stored as extra["refreshed"] with date_kind
    "refreshed", never as posted, so freshness falls back to first_seen.
  - The job page's schema.org datePosted is Java Date.toString(): "Wed Sep 16 07:00:00 UTC 2026".
    h.iso_date cannot read it (the day-of-month is followed by a time, not a year) and returns None,
    so it is parsed here. It is the same refreshed stamp as the tile date.
  - The tile shows only the primary location. The job page lists every PostalAddress (a req can
    be "Chicago" on the tile, Denver and Chicago on the page); detail() widens it.
  - The sitemap's lastmod is the sitemap build date (the same on every URL), not a job date.
  - The board carries no remote flag. Remote is only ever in the title or the JD text.
  - Older RMK tenants use a <tr class="data-row"> table layout instead of tiles. Some tenants
    are tiles, several others are the table. Both are parsed. The table total
    is "Results <b>1 – 25</b> of <b>212</b>"; columns are per tenant (td class="colLocation",
    colDate, colDepartment, colFacility, colShifttype), each cell holding one span.job<Field>. The
    location cell may carry a "<small>+1 more…</small>" marker, which is stripped (detail widens it).
    The table date is the same refresh stamp (-> extra.refreshed).

Optional key:
  path     brand path prefix on a multi-brand host, e.g. "/Litware_Learning" on a group host
           (the whole group at the root, only that brand under the prefix) or "/corporate" on
           jobs.tailspin.example (every location's rows at the root, only corporate roles under
           the path). The search then runs at <origin><path>/search/ and lists that brand only.
  api      "unify" for the newer RMK Unify front end (contosocareers.example), whose /search/ HTML carries
           no rows. The list is POST /services/recruiting/v1/jobs (10 per page, totalJobs). The
           Unify id is the requisition id, and the job page is <origin>/<brandUrl>/job/<slug>/<id>-en_US/
           (the slug is not checked). That page opens with an EMPTY itemprop="description" span and
           carries the JD in the next one; it has no PostalAddress, so the list location stays.
  facets   Unify only: facetFilters, e.g. {"department": ["Corporate Support"]} (on one tenant a
           small slice of the board; the value must match the row's string exactly). Facet KEYS are
           tenant-defined and get retired (one tenant has retired `jobType`), and a filter on a
           retired key answers 400 InvalidQuery, which reads like an outage.
"""
import html as _html
import re
from html.parser import HTMLParser
from urllib.parse import unquote

NAME = "successfactors"
ENUMERABLE = True
REQUIRED = ["origin"]
MAX_PAGES = 400

_MON = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
                                     "nov", "dec"], 1)}


def java_date(s):
    """'Wed Sep 16 07:00:00 UTC 2026' -> '2026-09-16'. None when it does not match."""
    m = re.search(r"([A-Za-z]{3})\s+(\d{1,2})\s+\d{1,2}:\d{2}(?::\d{2})?\s+\S+\s+(\d{4})", s or "")
    if not m or m.group(1).lower() not in _MON: return None
    return f"{m.group(3)}-{_MON[m.group(1).lower()]:02d}-{int(m.group(2)):02d}"


def _field(tile, jid, name):
    m = re.search(rf'id="job-{jid}-desktop-section-{name}-value"[^>]*>(.*?)</div>', tile, flags=re.S)
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", m.group(1)))).strip() if m else ""


def _total(page):
    m = re.search(r"Showing\s+[\d,]+\s+to\s+[\d,]+\s+of\s+([\d,]+)\s+Jobs", page)
    return int(m.group(1).replace(",", "")) if m else None


def parse_search(page, origin):
    """-> [(id, title, location, date_text, extra)] from one /search/ page of tiles."""
    out = []
    for tile in re.split(r'<li class="job-tile ', page)[1:]:
        m = re.match(r"job-id-(\d+)", tile)
        if not m: continue
        jid = m.group(1)
        href = re.search(r'data-url="([^"]+)"', tile)
        title = re.search(r'<a class="jobTitle-link[^"]*"[^>]*>(.*?)</a>', tile, flags=re.S)
        extra = {k: v for k in ("brand", "department", "customfield1", "customfield2", "customfield3")
                 for v in [_field(tile, jid, k)] if v}
        out.append((jid, re.sub(r"\s+", " ", _html.unescape(title.group(1))).strip() if title else "",
                    _field(tile, jid, "location"), _field(tile, jid, "date"),
                    dict(extra, path=_html.unescape(href.group(1)) if href else None)))
    return out


def _cell_text(s):
    s = re.sub(r"<small\b.*?</small>", " ", s or "", flags=re.S)
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def parse_table(page):
    """-> [(id, title, location, date_text, extra)] from one /search/ page of the table layout."""
    out = []
    for row in re.split(r'<tr class="data-row', page)[1:]:
        row = row.split("</tr>")[0]
        a = re.search(r'<a\s[^>]*?href="([^"]*?/job/[^"]*?/(\d+)/?)"[^>]*>(.*?)</a>', row, flags=re.S)
        if not a: continue
        cells = {}
        for name, body in re.findall(r'<td class="col(\w+) hidden-phone"[^>]*>(.*?)</td>', row, flags=re.S):
            v = _cell_text(body)
            if v: cells.setdefault(name.lower(), v)
        extra = {k: cells[k] for k in ("brand", "department", "facility", "shifttype") if k in cells}
        out.append((a.group(2), _cell_text(a.group(3)), cells.get("location", ""), cells.get("date", ""),
                    dict(extra, path=_html.unescape(a.group(1)))))
    return out


def _table_total(page):
    m = re.search(r"Results\s*<b>[^<]*</b>\s*of\s*<b>([\d,]+)</b>", page)
    return int(m.group(1).replace(",", "")) if m else None


def _short_date(s):
    """Unify's unifiedStandardStart is 'M/D/YY' ('9/9/26'). -> '2026-09-09', or None."""
    m = re.fullmatch(r"\s*(\d{1,2})/(\d{1,2})/(\d{2}|\d{4})\s*", s or "")
    if not m: return None
    y = int(m.group(3)); y += 2000 if y < 100 else 0
    return f"{y}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"


def _unify_body(page, facets):
    return {"locale": "en_US", "pageNumber": page, "sortBy": "date", "keywords": "", "location": "",
            "facetFilters": facets or {}, "brand": "", "skills": [], "categoryId": 0, "alertId": "",
            "rcmCandidateId": ""}


def list_unify(t, h, smoke=False):
    """RMK 'Unify' sites render results in the browser from POST /services/recruiting/v1/jobs
    (10 per page, pageNumber from 0, totalJobs on every page). The /search/ HTML has no rows.
    sortBy MUST be "date": with "" / "recent" / "relevance" / "title" the order is unstable across
    pages, and a full walk of one tenant's corporate slice misses about a fifth of the ids.
    "date" returns every row exactly once. A page past the end answers {"totalJobs": N} with no result list."""
    origin = t["origin"].rstrip("/")
    seen, total = {}, None
    for page in range(MAX_PAGES):
        d = h.post_json(f"{origin}/services/recruiting/v1/jobs", _unify_body(page, t.get("facets")))
        items = d.get("jobSearchResult") or []
        if total is None: total = d.get("totalJobs")
        if page == 0 and not items:
            raise RuntimeError(f"successfactors unify {origin}: 0 jobs (totalJobs={total}, facets={t.get('facets')})")
        new = 0
        for it in items:
            j = it.get("response") or {}
            jid = str(j.get("id") or "")
            if not jid or jid in seen: continue
            new += 1
            locs = [re.sub(r"\s+", " ", _html.unescape(x)).strip() for x in j.get("jobLocationShort") or []]
            title = _html.unescape(j.get("unifiedStandardTitle") or "")
            # The slug arrives HTML-escaped and sometimes already percent-encoded ("%28%245%2C000");
            # the server ignores it, so reduce it to URL-safe characters instead of quoting it twice.
            slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", unquote(_html.unescape(
                j.get("unifiedUrlTitle") or j.get("urlTitle") or "job"))).strip("-") or "job"
            brand = j.get("brandUrl")
            url = f"{origin}/{brand + '/' if brand else ''}job/{slug}/{jid}-en_US/"
            extra = {"jobType": j.get("jobType")}
            refreshed = _short_date(j.get("unifiedStandardStart"))
            if refreshed: extra.update(refreshed=refreshed, date_kind="refreshed")
            seen[jid] = h.norm(t["company"], NAME, jid, title, " | ".join(dict.fromkeys(x for x in locs if x)),
                               url, None, "", None, extra)
        if smoke or not items or not new or (total is not None and len(seen) >= total): break
    else:
        raise RuntimeError(f"successfactors unify {origin}: page cap {MAX_PAGES} hit")
    return list(seen.values())


def list_jobs(t, h, smoke=False):
    if t.get("api") == "unify":
        return list_unify(t, h, smoke)
    origin = t["origin"].rstrip("/")
    prefix = "/" + t["path"].strip("/") if t.get("path") else ""
    seen, start, total = {}, 0, None
    for _ in range(MAX_PAGES):
        page = h.get_text(f"{origin}{prefix}/search/?q=&sortColumn=referencedate&sortDirection=desc&startrow={start}")
        tiles = parse_search(page, origin)
        if tiles:
            total = _total(page) if total is None else total
        else:
            tiles = parse_table(page)
            total = _table_total(page) if total is None else total
        if start == 0 and not tiles:
            if total == 0: raise RuntimeError(f"successfactors {origin}: board says 0 jobs")
            raise RuntimeError(f"successfactors {origin}: no job tiles parsed (total={total}); "
                               "table layout or markup change")
        for jid, title, loc, date_txt, extra in tiles:
            path = extra.pop("path") or f"/job/{jid}/"
            refreshed = h.iso_date(date_txt)
            if refreshed:
                extra.update(refreshed=refreshed, date_kind="refreshed")
            seen[jid] = h.norm(t["company"], NAME, jid, title, loc, origin + path, None, "", None, extra)
        start += len(tiles)
        if smoke or not tiles or (total is not None and start >= total): break
    else:
        raise RuntimeError(f"successfactors {origin}: page cap {MAX_PAGES} hit at startrow {start}")
    return list(seen.values())


class _ItempropText(HTMLParser):
    """Collects the inner HTML-free text of the first element carrying itemprop="description".
    Regex cannot do this: the description is nested spans and divs of arbitrary depth."""
    VOID = {"br", "img", "meta", "hr", "input", "link", "area", "base", "col", "embed", "source", "wbr"}
    BLOCK = {"p", "div", "li", "br", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth, self.done, self.parts = 0, False, []

    def handle_starttag(self, tag, attrs):
        if self.done: return
        if self.depth:
            if tag in self.BLOCK: self.parts.append("\n")
            if tag not in self.VOID: self.depth += 1
        elif dict(attrs).get("itemprop") == "description" and tag not in self.VOID:
            self.depth = 1

    def handle_startendtag(self, tag, attrs):
        if self.depth and tag in self.BLOCK: self.parts.append("\n")

    def handle_endtag(self, tag):
        if self.done or not self.depth or tag in self.VOID: return
        if tag in self.BLOCK: self.parts.append("\n")
        self.depth -= 1
        if self.depth == 0:
            # Unify job pages open with an EMPTY itemprop="description" span;
            # the JD is the next one. Keep looking until a block has text.
            if "".join(self.parts).strip(): self.done = True
            else: self.parts = []

    def handle_data(self, data):
        if self.depth: self.parts.append(data)


def parse_job(page):
    """-> dict(description, refreshed, locations) from an RMK job page's itemprop markup."""
    p = _ItempropText()
    p.feed(page)
    desc = "".join(p.parts)
    desc = re.sub(r"[ \t ]+", " ", desc)
    desc = re.sub(r"\s*\n\s*", "\n", desc)
    desc = re.sub(r"\n{3,}", "\n\n", desc).strip()
    locs = []
    for block in re.findall(r'itemtype="http://schema.org/PostalAddress">(.*?)</span>', page, flags=re.S):
        a = dict(re.findall(r'itemprop="(\w+)" content="([^"]*)"', block))
        name = ", ".join(_html.unescape(x) for x in (a.get("addressLocality"), a.get("addressRegion"),
                                                     a.get("addressCountry")) if x)
        if name: locs.append(name)
    dp = re.search(r'itemprop="datePosted" content="([^"]+)"', page)
    return {"description": desc, "refreshed": java_date(dp.group(1)) if dp else None,
            "locations": list(dict.fromkeys(locs))}


def detail(t, row, h):
    d = parse_job(h.get_text(row["url"]))
    if d["description"]:
        row["description"] = d["description"]
    # Widen only when the page lists MORE places than the tile. On a single location the tile is the
    # better string: the page's addressLocality is cut from the URL slug on some tenants
    # ("Hybrid, Remote, US" -> "Hybrid, Remo, US") or given a made-up region on others
    # ("Remote, US" -> "Remote, OR, US"), which would lose the remote marker.
    was_remote = bool(re.search(r"\bremote\b", row.get("location") or "", re.I))
    if len(d["locations"]) > 1 or (d["locations"] and not row.get("location")):
        row["location"] = " | ".join(d["locations"])
        if was_remote and not re.search(r"\bremote\b", row["location"], re.I):
            row["location"] += " | Remote"
    if d["refreshed"]:
        row["extra"].update(refreshed=d["refreshed"], date_kind="refreshed")
    if not row.get("comp"):
        row["comp"] = h.comp_from_description(row["description"])
    return row
