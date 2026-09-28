"""
gem - Gem ATS public job boards (jobs.gem.com/<slug>).

    {"company": "Globex Systems", "ats": "gem", "slug": "globex"}

Endpoint: GET https://api.gem.com/job_board/v0/<slug>/job_posts/   (unauthenticated JSON array)

The response is the whole board in one array with full JD text inline (content HTML and
content_plain), so there is no pagination and no detail pass. The shape is Greenhouse's job-board
schema (ids like 4003629005, departments/offices trees), which is where Gem boards came from.

Traps (verified against a live board):
- No paging: ?page=2 and ?limit=1 are ignored and return the identical full list. An unknown slug
  answers 404, so a 200 with [] is a silent-zero and raises.
- first_published_at is the posting date (an evergreen "Software Engineer" req can date to 2022).
  updated_at moves on edits and is a refresh stamp; created_at is the draft's creation. Only
  first_published_at goes to posted.
- location.name is "Denver, United States" or "United States - Remote"; offices[] can list
  more. location_type is hybrid / in_office / remote; remote adds a bare "Remote" segment.
- Ids come in two shapes on one board: numeric Greenhouse-era ids (4003629005) and Gem-native
  base64 ids ("am9icG9zdDpd..." = "jobpost:..."). Both are the post's own id and are used as-is;
  never parse them as int.
- The trailing slash on job_posts/ matters less than it looks (both answer), but keep it: it is
  the documented path.
"""
NAME = "gem"
ENUMERABLE = True
REQUIRED = ["slug"]

API = "https://api.gem.com/job_board/v0"


def _location(j):
    names = [((j.get("location") or {}).get("name") or "").strip()]
    for o in j.get("offices") or []:
        names.append((((o or {}).get("location") or {}).get("name") or "").strip())
    if (j.get("location_type") or "").lower() == "remote":
        names.append("Remote")
    return " | ".join(dict.fromkeys(n for n in names if n))


def list_jobs(t, h, smoke=False):
    slug = t["slug"]
    d = h.get(f"{API}/{h.quote(slug)}/job_posts/")
    if not isinstance(d, list):
        raise RuntimeError(f"gem {slug}: expected a JSON array, got {type(d).__name__}")
    if not d:
        raise RuntimeError(f"gem {slug}: board answered 200 with no job posts")
    out = []
    for j in d:
        if not j.get("id"): continue
        desc = j.get("content_plain") or h.strip_html(j.get("content"))
        out.append(h.norm(t["company"], NAME, j["id"], j.get("title"), _location(j),
                          j.get("absolute_url") or f"https://jobs.gem.com/{slug}/{j['id']}",
                          h.iso_date(j.get("first_published_at")), desc, None,
                          {"refreshed": h.iso_date(j.get("updated_at")),
                           "location_type": j.get("location_type"),
                           "employment_type": j.get("employment_type"),
                           "requisition_id": j.get("requisition_id"),
                           "departments": [x.get("name") for x in j.get("departments") or [] if x]}))
    return out
