"""
zohorecruit - Zoho Recruit career sites (<portal>.zohorecruit.com/jobs/<Page>, or a custom domain).

    {"company": "Northwind Traders", "ats": "zohorecruit", "url": "https://careers.northwind.example/jobs/Careers"}

List: GET <url>   HTML. The whole board is server-rendered into a hidden input:
      <input type="hidden" value="[...html-escaped JSON...]" id="jobs">

Traps (verified against one live portal; checked for completeness against three other Zoho
portals, the largest with several hundred rows in one page and no duplicate ids):
- No paging: the input carries every job on the page, so one read is the board.
- Field names are tenant layouts, not a fixed schema: Country vs Country1, Job_Description present
  on some tenants and absent on others, Date_Opened optional, Mode_of_Work optional.
  Every read is defensive.
- The single-job page (/jobs/Careers/<id>) is a JS app with no job data in the markup, so there
  is no detail pass; tenants that omit Job_Description leave the description empty.
- Date_Opened is the recruiter-entered opening date (e.g. 2025-04-02): posted,
  with extra["date_kind"] = "opened".
- Publish can be false on a job that Keep_on_Career_Site keeps visible (seen on a one-job board);
  the page shows it, so it is kept.
- Remote_Job arrives as a bool on some tenants and "true"/"false" strings on others.
"""
import html, json, re

NAME = "zohorecruit"
ENUMERABLE = True
REQUIRED = ["url"]

_JOBS = re.compile(r'<input type="hidden" value="([^"]*)" id="jobs"')


def embedded_jobs(markup):
    m = _JOBS.search(markup or "")
    if not m:
        raise RuntimeError("zohorecruit: hidden 'jobs' input not found on the career page")
    return json.loads(html.unescape(m.group(1)))


def _truthy(v):
    return v is True or str(v).strip().lower() == "true"


def _location(j):
    country = j.get("Country") or j.get("Country1")
    seg = ", ".join(dict.fromkeys(str(p).strip() for p in (j.get("City"), j.get("State"), country)
                                  if p and str(p).strip()))
    segs = [seg] if seg else []
    if _truthy(j.get("Remote_Job")):
        segs.append("Remote")
    return " | ".join(segs)


def list_jobs(t, h, smoke=False):
    url = t["url"].rstrip("/")
    jobs = embedded_jobs(h.get_text(url))
    if not jobs:
        raise RuntimeError(f"zohorecruit {url}: career page lists no jobs")
    out = []
    for j in jobs:
        if not j.get("id"): continue
        title = j.get("Posting_Title") or j.get("Job_Opening_Name")
        out.append(h.norm(t["company"], NAME, j["id"], title, _location(j), f"{url}/{j['id']}",
                          h.iso_date(j.get("Date_Opened")), h.strip_html(j.get("Job_Description")), None,
                          {"date_kind": "opened", "job_type": j.get("Job_Type"),
                           "mode_of_work": j.get("Mode_of_Work"), "industry": j.get("Industry"),
                           "publish": j.get("Publish")}))
    return out
