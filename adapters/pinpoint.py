"""
pinpoint - Pinpoint ATS public postings (<sub>.pinpointhq.com, or the employer's careers domain).

    {"company": "Tailspin Toys", "ats": "pinpoint", "sub": "tailspin"}

List:   GET https://<sub>.pinpointhq.com/postings.json    {"data": [...]}   (the whole board, JD inline)
Detail: GET the posting url (HTML); schema.org JobPosting JSON-LD carries datePosted.

Traps (verified against a live board):
- postings.json has NO date field at all. The only date is datePosted in the posting page's
  JSON-LD, so detail exists for the date alone (and validThrough).
- No paging: ?page=2 returns the identical full list.
- location has no country: name is "<Country> - <City>" ("US - Denver", "UK - Leeds ",
  "US - Home/Remote", "Canada - Remote"). A leading "US - " is expanded to "United States - " so the
  US marker is explicit. workplace_type is onsite / hybrid / remote; remote adds "Remote".
- The JD is split over description, key_responsibilities, skills_knowledge_expertise and benefits,
  each with its own header field; all are joined.
- Compensation is structured (compensation_minimum/maximum/currency/frequency) but only shown when
  compensation_visible is true.
- url points at the employer domain (careers.<employer>.com/en/postings/<uuid>); id is Pinpoint's numeric
  posting id.
"""
import json, re

NAME = "pinpoint"
ENUMERABLE = True
REQUIRED = ["sub"]

_LD = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)


def _location(p):
    name = ((p.get("location") or {}).get("name") or "").strip()
    name = re.sub(r"^US\s*-\s*", "United States - ", name)
    segs = [name] if name else []
    if (p.get("workplace_type") or "").lower() == "remote":
        segs.append("Remote")
    return " | ".join(segs)


def _desc(p, h):
    out = []
    for body, head in (("description", None), ("key_responsibilities", "key_responsibilities_header"),
                       ("skills_knowledge_expertise", "skills_knowledge_expertise_header"),
                       ("benefits", "benefits_header")):
        txt = h.strip_html(p.get(body))
        if txt:
            out.append(f"{p.get(head)}\n{txt}" if head and p.get(head) else txt)
    return "\n\n".join(out)


def _comp(p):
    if not p.get("compensation_visible"): return None
    lo, hi = p.get("compensation_minimum"), p.get("compensation_maximum")
    if lo and hi and (p.get("compensation_currency") or "USD").upper() == "USD" \
            and (p.get("compensation_frequency") or "year").lower() in ("year", "yearly", "annually", "annual"):
        return f"${float(lo):,.0f} - ${float(hi):,.0f}"
    return None


def list_jobs(t, h, smoke=False):
    sub = t["sub"]
    d = h.get(f"https://{h.quote(sub)}.pinpointhq.com/postings.json")
    data = d.get("data") if isinstance(d, dict) else None
    if data is None:
        raise RuntimeError(f"pinpoint {sub}: unexpected response {str(d)[:200]}")
    if not data:
        raise RuntimeError(f"pinpoint {sub}: board answered with no postings")
    out = []
    for p in data:
        if not p.get("id"): continue
        job = p.get("job") or {}
        out.append(h.norm(t["company"], NAME, p["id"], p.get("title"), _location(p),
                          p.get("url") or f"https://{sub}.pinpointhq.com{p.get('path') or ''}", None,
                          _desc(p, h), _comp(p),
                          {"workplace_type": p.get("workplace_type"), "employment_type": p.get("employment_type"),
                           "department": (job.get("department") or {}).get("name"),
                           "job_id": job.get("id"), "deadline": h.iso_date(p.get("deadline_at"))}))
    return out


def job_posting_ld(markup):
    for block in _LD.findall(markup or ""):
        try:
            d = json.loads(block)
        except ValueError:
            continue
        for x in d if isinstance(d, list) else [d]:
            if isinstance(x, dict) and x.get("@type") == "JobPosting":
                return x
    return None


def detail(t, row, h):
    ld = job_posting_ld(h.get_text(row["url"]))
    if not ld:
        raise RuntimeError(f"pinpoint {row['id']}: no JobPosting JSON-LD on {row['url']}")
    row["posted"] = h.iso_date(ld.get("datePosted")) or row.get("posted")
    if not row.get("description"):
        row["description"] = h.strip_html(ld.get("description"))
    row["extra"].update({"date_kind": "posted", "valid_through": h.iso_date(ld.get("validThrough"))})
    return row
