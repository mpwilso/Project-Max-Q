"""
directemployers - DirectEmployers "microsite" career sites (careers.<employer>.com, Nuxt + NLX/DE).

    {"company": "Northwind Traders", "ats": "directemployers", "base": "https://careers.northwindtraders.example"}
    optional: "folder" (the site's DE job folder; defaults to the host with dots turned to dashes)

List:   GET <base>/sitemaps/index.xml -> every jobs*.xml in it -> <loc>/<lastmod> per job page.
Detail: GET https://microsites.dejobs.org/<folder>/data/<GUID>.json

RSS IS OFF LIMITS HERE. <base>/robots.txt is "Disallow: /*feed/" and "/*feeds/" and
then names the sitemap itself; the sitemap route is the allowed one and the only one used.

NOT ENUMERABLE. The jobs sitemap is a periodic build, not a live index: every url in jobs_1.xml
carries the same <lastmod>, and the file's own Last-Modified header can be several days old.
A req posted after that build is simply not in the file yet, so absence here cannot mean closed.
sweep.py carries a missing req forward instead, and lookup() re-checks a tracked one by GUID.

Traps, verified against a live microsite:
- The job page renders nothing server-side. It is Nuxt with data-ssr="false": the served markup has
  no title, no location and no JSON-LD (the page builds its schema.org JobPosting in the browser).
  So detail() reads the same JSON the page reads, from the site's own job folder on
  microsites.dejobs.org, rather than scraping a shell.
- That folder is the site's `job-folder` runtime config, printed in the job page's inline Nuxt
  config ("job-folder":"careers-northwindtraders-example"). It matched the host with dots turned to dashes,
  which is the default here; set "folder" in targets.json for a site where it does not.
- <lastmod> is the sitemap build stamp, NOT a posting date: every row shares one value. It goes to
  extra["refreshed"] with date_kind "refreshed" and posted stays None until detail() reads a date.
- The DE record carries three dates. date_new/date_updated is the posting date and date_added is
  when this microsite indexed it, which can be weeks later. The site's own JSON-LD uses date_added;
  this adapter uses the OLDER date_new, because overstating freshness is the costlier error.
  date_added is kept in extra.
- The id is the 32-char GUID in the url, not the slug: the slug carries the title and moves on a
  retitle (the page itself rewrites its own url when title_slug or location disagree).
- The slug also carries the list-level title and location, which is all the gates need; the real
  title_exact and location arrive with detail(). "manager-ai" -> "Manager Ai" until then.
- An expired record keeps its json and gains a "deleted_at" key; a removed one 404s. Both mean
  closed to lookup(); 404 is not retried into an error.
"""
import re
import xml.etree.ElementTree as ET

NAME = "directemployers"
ENUMERABLE = False                      # weekly sitemap build; absence proves nothing (see above)
REQUIRED = ["base"]

DATA = "https://microsites.dejobs.org/{folder}/data/{guid}.json"
SITEMAP = "/sitemaps/index.xml"
_JOB_URL = re.compile(r"^https?://[^/]+/([^/]+)/([^/]+)/([0-9A-Fa-f]{32})/job/?$")
_STATE = re.compile(r"^[A-Za-z]{2}$")


def _tag(el):
    return el.tag.rpartition("}")[2]


def _children(root, name):
    return [e for e in root.iter() if _tag(e) == name]


def _base(t):
    return str(t["base"]).rstrip("/")


def _folder(t):
    if t.get("folder"): return str(t["folder"])
    host = re.sub(r"^https?://", "", _base(t)).split("/")[0]
    return host.replace(".", "-")


def title_from_slug(slug):
    return " ".join(w.capitalize() for w in (slug or "").split("-") if w)


def location_from_slug(slug):
    """'colorado-springs-co' -> 'Colorado Springs, CO'; a bare Remote segment when the slug says remote."""
    words = [w for w in (slug or "").split("-") if w]
    if not words: return ""
    if len(words) > 1 and _STATE.match(words[-1]):
        label = " ".join(w.capitalize() for w in words[:-1]) + ", " + words[-1].upper()
    else:
        label = " ".join(w.capitalize() for w in words)
    segs = [label]
    if re.search(r"remote|telecommut", label, re.I):
        segs.append("Remote")
    return " | ".join(dict.fromkeys(segs))


def parse_sitemap_index(xml, base):
    """-> [absolute url of every jobs*.xml]. Other sitemaps (pages, search) are skipped."""
    root = _root(xml, "sitemap index")
    out = []
    for sm in _children(root, "sitemap"):
        loc = next((e.text or "" for e in sm if _tag(e) == "loc"), "").strip()
        if not loc: continue
        if not loc.startswith("http"): loc = base + "/" + loc.lstrip("/")
        if re.search(r"/jobs[^/]*\.xml$", loc): out.append(loc)
    return out


def parse_jobs_sitemap(xml):
    """-> [(url, lastmod)] for every entry that looks like a job page."""
    root = _root(xml, "jobs sitemap")
    out = []
    for u in _children(root, "url"):
        loc = next((e.text or "" for e in u if _tag(e) == "loc"), "").strip()
        lastmod = next((e.text or "" for e in u if _tag(e) == "lastmod"), "").strip()
        if _JOB_URL.match(loc): out.append((loc, lastmod))
    return out


def _root(xml, what):
    try:
        return ET.fromstring(xml.encode("utf-8") if isinstance(xml, str) else xml)
    except ET.ParseError as e:
        raise RuntimeError(f"directemployers: {what} is not XML ({e}): {str(xml)[:200]}")


def list_jobs(t, h, smoke=False):
    base = _base(t)
    maps = parse_sitemap_index(h.get_text(base + str(t.get("sitemap") or SITEMAP)), base)
    if not maps:
        raise RuntimeError(f"directemployers {base}: sitemap index names no jobs sitemap")
    rows, seen = [], set()
    for m in (maps[:1] if smoke else maps):
        entries = parse_jobs_sitemap(h.get_text(m))
        if not entries:
            raise RuntimeError(f"directemployers {base}: {m} holds no job urls")
        for url, lastmod in entries:
            loc_slug, title_slug, guid = _JOB_URL.match(url).groups()
            guid = guid.upper()
            if guid in seen: continue
            seen.add(guid)
            rows.append(h.norm(t["company"], NAME, guid, title_from_slug(title_slug),
                               location_from_slug(loc_slug), url, None, "", None,
                               {"refreshed": h.iso_date(lastmod), "date_kind": "refreshed",
                                "slug": title_slug, "sitemap": m.rsplit("/", 1)[-1],
                                "title_source": "url-slug"}))
    if not rows:
        raise RuntimeError(f"directemployers {base}: sitemap parsed, 0 job urls matched")
    return rows


def data_url(t, guid):
    return DATA.format(folder=_folder(t), guid=str(guid).upper())


def _remote(*parts):
    return bool(re.search(r"remote|telecommut|work from home", " ".join(p or "" for p in parts), re.I))


def _fill(t, row, d, h):
    title = (d.get("title_exact") or d.get("title") or "").strip()
    if title:
        row["title"] = title
        row["extra"]["title_source"] = "detail"
    label = (d.get("location_exact") or d.get("location") or "").strip()
    if not label:
        label = ", ".join(x for x in ((d.get("city") or "").strip(), (d.get("state_short") or "").strip()) if x)
    if label:
        segs = [label] + (["Remote"] if _remote(label, title) else [])
        row["location"] = " | ".join(dict.fromkeys(segs))
    body = h.strip_html(d.get("html_description") or "") or (d.get("description") or d.get("text") or "")
    if body: row["description"] = body
    posted = h.iso_date(d.get("date_new") or d.get("date_updated"))
    if posted:
        row["posted"] = posted
        row["extra"]["date_kind"] = "posted"
    row["extra"].update({k: v for k, v in (
        ("reqid", d.get("reqid")), ("buid", d.get("buid")), ("job_type", d.get("job_type")),
        ("added", h.iso_date(d.get("date_added"))), ("updated", h.iso_date(d.get("date_updated"))),
        ("source", d.get("job_source_name")), ("apply", d.get("link"))) if v})
    if d.get("deleted_at"): row["extra"]["expired"] = True
    if not row.get("comp"):
        row["comp"] = h.comp_from_description(row.get("description"))
    return row


def detail(t, row, h):
    return _fill(t, row, h.get(data_url(t, row["id"])), h)


def _status(e):
    return getattr(getattr(e, "response", None), "status_code", None)


def lookup(t, jid, h):
    """One req by GUID: the row if open, None if 404 or flagged deleted, raise if unreadable."""
    try:
        d = h.get(data_url(t, jid))
    except Exception as e:
        if _status(e) == 404: return None
        raise
    if not isinstance(d, dict) or not (d.get("guid") or d.get("title_exact") or d.get("title")):
        raise RuntimeError(f"directemployers {jid}: job json has no title or guid")
    if d.get("deleted_at"): return None
    guid = (d.get("guid") or str(jid)).upper()
    url = f"{_base(t)}/{(d.get('city_slug') or '')}-{(d.get('state_short') or '').lower()}/" \
          f"{d.get('title_slug') or ''}/{guid}/job/"
    return _fill(t, h.norm(t["company"], NAME, guid, "", "", url, None, "", None, {"via": "lookup"}), d, h)
