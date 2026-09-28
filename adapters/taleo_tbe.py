"""
taleo_tbe - Taleo Business Edition (TBE) public RSS feeds (phf.tbe.taleo.net/<inst>/ats/servlet/Rss).

    {"company": "Northwind IT", "ats": "taleo_tbe", "org": "NORTHWIND", "cws": 12}
    optional: "host" (default phf.tbe.taleo.net), "inst" (default phf02)

TBE is NOT the same product as adapters/taleo.py (Oracle Taleo Enterprise career sections). The
enterprise adapter's careersection/jobsearch.ajax pager does not exist here; TBE publishes a
career-site feed per "cws" (career website) and a requisition page per "rid".

List:   GET /<inst>/ats/servlet/Rss?org=<ORG>&cws=<N>&WebPage=SRCHR_V2&WebVersion=0&_rss_version=2
Detail: GET /<inst>/ats/careers/requisition.jsp?org=<ORG>&cws=<N>&rid=<RID>   (302 -> careers/v2/viewRequisition)

NOT ENUMERABLE. The feed is the career site's recent-postings RSS, not the board: TBE caps a feed
(one tenant's feed answers fewer items than the same career site's search lists),
and there is no paging parameter on the Rss servlet. Absence from this read proves nothing, so
sweep.py carries a missing req forward and `lookup()` below re-checks a tracked req by rid.

Traps, each verified live against one production tenant:
- The list read already carries the JD. Each <item> has BOTH a plain <description> and a
  <taleo:html-description>; the html one is the real body (the plain one is the same text with the
  markup flattened). Every item seen carries one. `detail()` exists only for an item that arrives
  without a body, and for the shared requisition parse that `lookup()` needs.
- pubDate is RFC 822 ("Thu, 20 Aug 2026 16:53:36 GMT") and h.iso_date CANNOT read it: its patterns
  are month-first, so a day-first RFC 822 string silently returns None and every row would have
  lost its date. Parsed here with email.utils and handed to h.iso_date as ISO. Cross-checked
  against the requisition page's JSON-LD datePosted (one rid: RSS Aug 20, JSON-LD 2026-08-20),
  so this is the posting date, not a refresh stamp.
- The channel's own <pubDate> equals the newest item's, not the fetch time; it is not a row date.
- The stable id is <taleo:reqId>, which is also the rid in the link. A retitle keeps it.
- Location lives in <taleo:location> ("Chicago (Loop), IL") with city/state/country siblings.
  locationCountry is missing on some rows (two of the rows read lacked it), so the
  country is appended only when the feed states it and the label does not already name it.
- requisition.jsp redirects to careers/v2/viewRequisition and carries one application/ld+json
  JobPosting block (title, datePosted, jobLocation.address, description). No session or cookie
  needed. A CLOSED rid answers 200 with no JSON-LD and the string "no longer available"
  (document.title = "Job Not Available"), which is how lookup() tells closed from broken.
"""
import json
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

NAME = "taleo_tbe"
ENUMERABLE = False                      # feed is a capped sample of the career site, never the board
REQUIRED = ["org", "cws"]

HOST = "phf.tbe.taleo.net"
INST = "phf02"
TNS = "{urn:TBERss}"
_SAFE = re.compile(r"^[A-Za-z0-9_.\-]+$")
_LD = re.compile(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.S | re.I)
_CLOSED = re.compile(r"no longer available|Job Not Available", re.I)
_RID = re.compile(r"[?&]rid=(\d+)")


def _parts(t):
    org, cws = str(t["org"]), str(t["cws"])
    host, inst = str(t.get("host") or HOST), str(t.get("inst") or INST)
    for v in (org, cws, host, inst):
        if not _SAFE.match(v):
            raise ValueError(f"taleo_tbe: unsafe target value {v!r}")
    return host, inst, org, cws


def rss_url(t):
    host, inst, org, cws = _parts(t)
    return (f"https://{host}/{inst}/ats/servlet/Rss?org={org}&cws={cws}"
            f"&WebPage=SRCHR_V2&WebVersion=0&_rss_version=2")


def req_url(t, rid):
    host, inst, org, cws = _parts(t)
    return f"https://{host}/{inst}/ats/careers/requisition.jsp?org={org}&cws={cws}&rid={rid}"


def rfc822_date(s):
    """'Thu, 20 Aug 2026 16:53:36 GMT' -> '2026-08-20'. h.iso_date reads month-first only."""
    if not s: return None
    try:
        return parsedate_to_datetime(s.strip()).date().isoformat()
    except (TypeError, ValueError):
        return None


def location_label(loc, city, state, country, remote_hint=""):
    """One ' | '-joined location string, with a bare Remote segment when the req says so."""
    label = (loc or "").strip() or ", ".join(x for x in ((city or "").strip(), (state or "").strip()) if x)
    country = (country or "").strip()
    if country and not re.search(rf"(?<![A-Za-z]){re.escape(country)}(?![A-Za-z])", label, re.I):
        label = f"{label}, {country}" if label else country
    segs = [label] if label else []
    if re.search(r"remote|telecommut|work from home", f"{label} {remote_hint}", re.I):
        segs.append("Remote")
    return " | ".join(dict.fromkeys(s for s in segs if s))


def _text(item, tag):
    return (item.findtext(tag) or "").strip()


def parse_feed(xml):
    """RSS bytes/str -> [{id, title, url, posted, location, department, html, text}]. Pure."""
    try:
        root = ET.fromstring(xml.encode("utf-8") if isinstance(xml, str) else xml)
    except ET.ParseError as e:
        raise RuntimeError(f"taleo_tbe: feed is not XML ({e}): {str(xml)[:200]}")
    channel = root.find("channel")
    items = channel.findall("item") if channel is not None else root.findall(".//item")
    out = []
    for it in items:
        url = _text(it, "link") or _text(it, "guid")
        rid = _text(it, TNS + "reqId") or (_RID.search(url).group(1) if _RID.search(url) else "")
        if not rid: continue
        out.append({
            "id": rid,
            "title": _text(it, "title"),
            "url": url,
            "posted": rfc822_date(_text(it, "pubDate")),
            "location": location_label(_text(it, TNS + "location"), _text(it, TNS + "locationCity"),
                                       _text(it, TNS + "locationState"), _text(it, TNS + "locationCountry"),
                                       _text(it, "title")),
            "department": _text(it, TNS + "department"),
            "html": _text(it, TNS + "html-description"),
            "text": _text(it, "description"),
        })
    return out


def list_jobs(t, h, smoke=False):
    rows = parse_feed(h.get_text(rss_url(t)))
    if not rows:
        raise RuntimeError(f"taleo_tbe {t['company']}: RSS returned no items (org={t['org']} cws={t['cws']})")
    out = []
    for r in rows:
        out.append(h.norm(t["company"], NAME, r["id"], r["title"], r["location"], r["url"],
                          h.iso_date(r["posted"]), h.strip_html(r["html"] or r["text"]), None,
                          {"date_kind": "posted", "department": r["department"], "rid": r["id"],
                           "org": str(t["org"]), "cws": str(t["cws"]), "feed": "sample"}))
    return out


def parse_requisition(page):
    """Requisition page -> the JSON-LD JobPosting dict, or None when the page has none."""
    for block in _LD.findall(page or ""):
        try:
            d = json.loads(block)
        except ValueError:
            continue
        if isinstance(d, list):
            d = next((x for x in d if isinstance(x, dict) and x.get("@type") == "JobPosting"), None)
        if isinstance(d, dict) and d.get("@type") == "JobPosting":
            return d
    return None


def is_closed(page):
    return bool(_CLOSED.search(page or "")) and parse_requisition(page) is None


def _ld_location(ld):
    place = ld.get("jobLocation") or {}
    if isinstance(place, list):
        place = place[0] if place else {}
    addr = (place or {}).get("address") or {}
    country = addr.get("addressCountry")
    if isinstance(country, dict):
        country = country.get("name")
    return location_label(addr.get("addressLocality"), addr.get("addressLocality"),
                          addr.get("addressRegion"), country, ld.get("title") or "")


def _fill(row, ld, h):
    desc = h.strip_html(ld.get("description") or "")
    if desc: row["description"] = desc
    posted = h.iso_date(ld.get("datePosted"))
    if posted:
        row["posted"] = posted
        row["extra"]["date_kind"] = "posted"
    loc = _ld_location(ld)
    if loc: row["location"] = loc
    if ld.get("title"): row["title"] = ld["title"].strip()
    if ld.get("employmentType"): row["extra"]["employment_type"] = ld["employmentType"]
    if not row.get("comp"):
        row["comp"] = h.comp_from_description(row.get("description"))
    return row


def detail(t, row, h):
    """Only fires for a row the feed handed over without a body; also the lookup() parse."""
    page = h.get_text(row.get("url") or req_url(t, row["id"]))
    ld = parse_requisition(page)
    if ld is None:
        if is_closed(page):
            row["extra"]["closed"] = True
            return row
        raise RuntimeError(f"taleo_tbe {row['id']}: no JSON-LD JobPosting and no closed marker")
    return _fill(row, ld, h)


def lookup(t, jid, h):
    """One req by rid: the row if open, None if the board says it is gone, raise if unreadable."""
    page = h.get_text(req_url(t, jid))
    ld = parse_requisition(page)
    if ld is None:
        if is_closed(page):
            return None
        raise RuntimeError(f"taleo_tbe {jid}: requisition page is neither a JobPosting nor closed")
    row = h.norm(t["company"], NAME, jid, ld.get("title") or "", "", ld.get("url") or req_url(t, jid),
                 None, "", None, {"rid": str(jid), "org": str(t["org"]), "cws": str(t["cws"]),
                                  "via": "lookup"})
    return _fill(row, ld, h)
