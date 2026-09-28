"""
radancy_v2 - Radancy / TalentBrew career sites read as a WHOLE board, for sites where the built-in
sweep.radancy adapter returns 0 rows (e.g. a board like jobs.wideworld.example, fictional).

Why not the built-in (verified against a live board):
  - Some sites' tile links end in a fragment: href="/job/phoenix/category-manager-outdoor/7301/100814276352/#job-details-section".
    sweep.radancy's link regex requires the href to END at the job id (".../(\\d+)/(\\d+)\""), so every
    tile is skipped and the target reads as an empty board. The built-in adapter has since learned
    to accept the fragment; this plugin is kept for targets already configured with it.
  - Such a board is small (a few hundred rows), and an empty Keywords= returns all of it at
    RecordsPerPage=100 (data-total-results carries the full count on every page).

Target keys: origin (required, e.g. "https://jobs.wideworld.example"); path (optional locale prefix
such as "/en", as on the built-in).
Tiles carry title, location ("Phoenix, Arizona"), brand (data-brand) and "Job function: X". The job page
carries a schema.org JobPosting block with datePosted ("2026-5-19"), full text and jobLocation.
"""
import json
import re

NAME = "radancy_v2"
ENUMERABLE = True
REQUIRED = ["origin"]
PAGE = 100
MAX_PAGES = 50

_HREF = re.compile(r'href="(/[^"]*?/?job/[^"#]+?/(\d+)/(\d+))/?(?:#[^"]*)?"')


def results_url(base, page):
    return (f"{base}/search-jobs/results?ActiveFacetID=0&CurrentPage={page}&RecordsPerPage={PAGE}"
            f"&Distance=50&RadiusUnitType=0&Keywords=&Location=&ShowRadius=False&IsPagination=True"
            f"&CustomFacetName=&FacetTerm=&FacetType=0&FacetFilters=&SearchResultsModuleName=Search+Results"
            f"&SearchFiltersModuleName=Search+Filters&SortCriteria=0&SortDirection=0&SearchType=5")


def parse_results(fragment, h):
    """-> (total or None, [(id, href, title, location, extra)]) from the 'results' HTML fragment."""
    tot = re.search(r'data-total-results="(\d+)"', fragment or "")
    out = []
    for tile in re.findall(r"<li\b[^>]*>.*?</li>", fragment or "", flags=re.S):
        m = _HREF.search(tile)
        if not m: continue
        title = re.search(r"<h2[^>]*>(.*?)</h2>", tile, flags=re.S)
        loc = re.search(r'class="job-location"[^>]*>(.*?)</span>', tile, flags=re.S)
        brand = re.search(r'class="brand-col"[^>]*>(.*?)</span>', tile, flags=re.S)
        func = re.search(r"Job function:\s*([^<]+)", tile)
        extra = {"brand": h.strip_html(brand.group(1)) if brand else None,
                 "function": func.group(1).strip() if func else None}
        out.append((m.group(3), m.group(1) + "/", h.strip_html(title.group(1)) if title else "",
                    h.strip_html(loc.group(1)) if loc else "", extra))
    return (int(tot.group(1)) if tot else None), out


def list_jobs(t, h, smoke=False):
    origin = t["origin"].rstrip("/")
    base = origin + t.get("path", "")
    seen, total = {}, None
    for page in range(1, MAX_PAGES + 1):
        d = h.get(results_url(base, page), headers={"X-Requested-With": "XMLHttpRequest",
                                                    "Accept": "application/json",
                                                    "Referer": base + "/search-jobs"})
        tot, tiles = parse_results(d.get("results"), h)
        if total is None: total = tot
        new = 0
        for jid, href, title, loc, extra in tiles:
            if jid in seen: continue
            new += 1
            seen[jid] = h.norm(t["company"], NAME, jid, title, loc, origin + href, None, "", None, extra)
        if smoke or len(tiles) < PAGE or not new or (total is not None and len(seen) >= total): break
    if not seen:
        raise RuntimeError(f"radancy_v2 {base}: 0 job tiles parsed (data-total-results={total})")
    return list(seen.values())


def detail(t, row, h):
    txt = h.get_text(row["url"])
    for blob in re.findall(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', txt, flags=re.S):
        try:
            d = json.loads(blob.strip())
        except ValueError:
            continue
        if not isinstance(d, dict) or d.get("@type") != "JobPosting": continue
        row["description"] = h.strip_html(d.get("description"))
        row["posted"] = h.iso_date(d.get("datePosted")) or row.get("posted")
        locs = d.get("jobLocation") or []
        if isinstance(locs, dict): locs = [locs]
        names = []
        for L in locs:
            a = (L or {}).get("address") or {}
            names.append(", ".join(x for x in (a.get("addressLocality"), a.get("addressRegion"),
                                               a.get("addressCountry")) if x))
        names = list(dict.fromkeys(n for n in names if n))
        if names: row["location"] = " | ".join(names)
        row["extra"]["employmentType"] = d.get("employmentType")
        break
    if not row.get("comp"):
        row["comp"] = h.comp_from_description(row["description"])
    return row
