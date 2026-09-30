#!/usr/bin/env python3
"""
sweep.py - job-board sweep: discovery, hard gates and change detection across employer ATS boards.

Reads targets.json (employers and their boards) and gates.json (title, location, tenure and other
rules), pulls every open req from each employer's public ATS JSON endpoint (the same JSON the
careers pages load in a browser), applies the hard gates in code, diffs against the previous
snapshot, and writes reports/SWEEP_REPORT_<date>.md plus one JD file per gate-passing req under
data/jds/<date>/. Scoring is out of scope; scores are recorded back into data/scored.json.

    python sweep.py                      # full live sweep of every target
    python sweep.py --smoke              # one page per target, prints counts and errors, writes nothing
    python sweep.py --only gitlab        # read one employer (substring match), print, write nothing
    python sweep.py --merge gitlab       # read one employer and merge it into the snapshot
    python sweep.py --resume             # continue a crashed full sweep from today's checkpoints
    python sweep.py --report-only        # rebuild the report from data/latest.json, no network
    python sweep.py --window 7d          # gate-relevant reqs inside a window (--company, --floor)
    python sweep.py --set-score KEY=80:Apply[:built][:conv=HIGH]
    python sweep.py --yield              # which employers earn their read
    python sweep.py --prerank-check      # validate the lane pre-rank against recorded scores
    python sweep.py --selftest           # gates and report on synthetic data, no network

Other flags: --full (no incremental stop), --force (ignore the fresh-snapshot shortcut), --cached
(opt into tier caching), --fresh, --digest, --show-cadence.

Adapters: greenhouse ashby lever workday eightfold smartrecruiters amazon atlassian phenom radancy
metacareers oracle pcsx, plus plugins loaded from adapters/<vendor>.py (see adapters/README.md).

Design principles:
  - Coverage is never silent. An employer that errors, or a verified board that answers with zero
    reqs, is reported UNCOVERED/ERROR, never as a quiet day; a health block checks the funnel and
    count drops on every run.
  - Absence is only evidence of closure on a board read whole and cleanly. Query-scoped, errored,
    truncated or country-scoped reads carry missing reqs forward for a bounded time instead.
  - State writes are atomic and ordered, reads are checkpointed per employer, and a partial run
    (--only) never writes state.
  - Adapters share one HTTP retry policy and are polite: one lane per host, a fixed delay per call.

Dependencies: requests. Optional: curl_cffi (pip install curl_cffi) if a Workday tenant refuses
plain requests; set USE_CURL_CFFI=1 to route Workday calls through it.
"""
import argparse, functools, json, os, re, sys, time, html, tempfile, hashlib, datetime as dt
from pathlib import Path

try:
    import requests
except ImportError:
    requests = None

# File IO is explicitly utf-8 everywhere. stdout keeps the console's own codec (cp1252 on a Windows
# console), where one scraped title with an unmappable character (a narrow no-break space, say)
# would kill the whole print. Keep the codec so everything that encodes is unchanged, and replace
# only what would have raised.
def _never_crash_on_print():
    for s in (sys.stdout, sys.stderr):
        try: s.reconfigure(errors="replace")
        except (AttributeError, ValueError): pass   # not a TextIOWrapper (piped, captured, stubbed)
_never_crash_on_print()

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"; JDS = DATA / "jds"; REPORTS = ROOT / "reports"
for p in (DATA, JDS, REPORTS): p.mkdir(parents=True, exist_ok=True)
READLOG = DATA / "read_log.json"      # per-employer last successful live read
SCORED = DATA / "scored.json"         # score ledger, written via --set-score

# --------------------------------------------------------------- cadence policy
# Tier A is always read live; lower tiers may reuse a cached read until it goes stale.
# A named employer is ALWAYS read live regardless of tier.
TIER_A_ALWAYS_LIVE = True
CACHE_TTL_HOURS = 24        # tier B and below: re-read only past this age
SNAPSHOT_SKIP_HOURS = 6     # a snapshot younger than this needs no refetch at all
STALE_WARN_HOURS = 24       # any window answer older than this must say so

def now_utc():
    return dt.datetime.now(dt.timezone.utc)

def iso_now():
    return now_utc().isoformat(timespec="seconds")

def hours_since(iso_ts):
    """Age in hours of an ISO timestamp, or None. Never raises."""
    if not iso_ts: return None
    try:
        t = dt.datetime.fromisoformat(str(iso_ts))
        if t.tzinfo is None: t = t.replace(tzinfo=dt.timezone.utc)
        return (now_utc() - t).total_seconds() / 3600.0
    except (ValueError, TypeError):
        return None

def fmt_age(hrs):
    if hrs is None: return "never read"
    if hrs < 1: return f"{int(hrs*60)}m ago"
    if hrs < 48: return f"{hrs:.1f}h ago"
    return f"{hrs/24:.1f}d ago"

# --------------------------------------------------------------- terminal links
# OSC 8 hyperlinks so the TITLE itself is clickable in the VS Code terminal.
# Falls back to a plain URL on its own line when output is piped or colour is
# switched off, so grep and redirection still see the URL.
def links_enabled():
    if os.environ.get("MAXQ_FORCE_LINKS"): return True      # for testing
    if os.environ.get("NO_COLOR"): return False
    if os.environ.get("TERM", "") == "dumb": return False
    return sys.stdout.isatty()

def link(text, url):
    """Clickable title when the terminal supports it, else 'text' + URL on its own line."""
    text, url = _no_ctrl(text), _no_ctrl(url)
    if not url or not SAFE_URL.match(url): return text
    if links_enabled():
        return f"\033]8;;{url}\033\\{text}\033]8;;\033\\"
    return f"{text}\n    {url}"

def jd_hash(desc):
    """Stable hash of JD text, so a quietly rewritten req stops looking unchanged."""
    return hashlib.sha256(re.sub(r"\s+", " ", (desc or "")).strip().encode("utf-8")).hexdigest()[:16]

UA = "maxq-sweep/0.1 (job-search research tool; polite; 150ms delay)"
DELAY = 0.15
TODAY = dt.date.today()
# Amazon is SCOPED, not enumerated: its board is too large to walk, and broad title queries return
# thousands of AWS infrastructure, logistics, devices and retail reqs that pass the title gate and
# are still outside the configured lane. Amazon work outside the lane terms below is out of scope by
# policy, not filtered by volume. Re-measure before widening them.
_AMZ_LANE = ["business systems", "enterprise applications", "internal tools", "corporate systems",
             "finance systems",
             # Terms for in-lane orgs the first five never return. AWS, Leo, logistics and devices stay out.
             "finance technology", "payroll technology", "procurement technology", "accounting systems", "ERP"]
AMAZON_QUERIES = _AMZ_LANE + [f"{role} {lane}" for role in ("technical program manager", "product manager")
                              for lane in _AMZ_LANE]
# Wider bare terms that reach in-lane reqs the lane terms miss. search.json does not AND a role onto
# them ("product manager AI tools" returns 0), and they bring AWS, Leo and devices with them, so a
# row found ONLY by one of them is kept only outside the excluded business categories below (about
# half of what they return).
_AMZ_WIDE = ["automation", "billing", "order to cash", "procure to pay", "merchant"]
_AMZ_WIDE_OUT_CATEGORIES = {"aws", "alexa-and-amazon-devices", "transportation-and-logistics", "operations",
                            "fulfillment-and-operations", "fulfillment-ops"}
_AMZ_WIDE_OUT_TEAM = re.compile(r"(?<![a-z])(?:aws|kuiper|leo)(?![a-z])")
EIGHTFOLD_QUERIES = ["program manager", "product manager", "technical program"]
PHENOM_QUERIES = ["program manager", "product manager", "product owner", "technical program"]
# Radancy needs no query list: an empty Keywords= returns the whole board on every TalentBrew site
# probed, so the adapter enumerates instead; see radancy().

# ----------------------------------------------------------------------------- http
RETRY_STATUS = (429, 500, 502, 503, 504, 520, 521, 522, 523, 524)   # 52x: Cloudflare origin errors (T-Mobile)
_tls = __import__("threading").local()

def http_session():
    """One requests.Session per thread (each lane is a thread), so repeat calls to a host reuse the
    TCP/TLS connection instead of paying a fresh handshake on every page."""
    s = getattr(_tls, "session", None)
    if s is None:
        s = requests.Session()
        s.mount("https://", requests.adapters.HTTPAdapter(pool_connections=16, pool_maxsize=16))
        _tls.session = s
    return s

def _request(method, url, headers, attempts=4, **kw):
    """One retry policy for every HTTP call. Retries 429 and 5xx, and connection drops.

    Across hundreds of employers a single RemoteDisconnected or 502 is routine; without a retry
    each one leaves an employer UNCOVERED for the whole run."""
    last = None
    for attempt in range(attempts):
        time.sleep(DELAY)
        try:
            if method == "POST" and os.environ.get("USE_CURL_CFFI"):
                from curl_cffi import requests as cr
                r = cr.post(url, headers=headers, impersonate="chrome", timeout=30, **kw)
            else:
                r = http_session().request(method, url, headers=headers, timeout=30, **kw)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            last = e
            if attempt < attempts - 1:
                time.sleep(min(2 ** attempt * 3, 30)); continue
            raise
        if r.status_code in RETRY_STATUS and attempt < attempts - 1:
            try: wait = float(r.headers.get("Retry-After") or 0)
            except ValueError: wait = 0
            time.sleep(min(wait or 2 ** attempt * 5, 60))
            continue
        r.raise_for_status()
        return r
    raise last or RuntimeError(f"request failed: {url}")

def get(url, headers=None, **kw):
    # headers merges into the defaults rather than colliding with them (passing headers= straight
    # through to _request raises TypeError). Retries matter here: a fully paginated Eightfold board
    # can trip Netflix's rate limit. See _request().
    h = {"User-Agent": UA, "Accept": "application/json"}
    if headers: h.update(headers)
    return _request("GET", url, h, **kw).json()

def post_json(url, body, headers=None):
    h = {"User-Agent": UA, "Accept": "application/json", "Content-Type": "application/json",
         "Accept-Language": "en-US"}
    if headers: h.update(headers)
    return _request("POST", url, h, json=body).json()

def get_text(url, headers=None, **kw):
    """Same as get() but returns markup. Radancy job detail lives in JSON-LD inside the page."""
    h = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
         "Accept-Language": "en-US,en;q=0.9"}
    if headers: h.update(headers)
    return _request("GET", url, h, **kw).text

# ----------------------------------------------------------------------------- etags
# Greenhouse boards-api, Ashby posting-api and Lever all answer If-None-Match with a 304 and an
# empty body. An unchanged board then costs one header exchange instead of megabytes plus parsing.
# Two modes, switched by gates.json etag_trust:
#   measurement (default): every clean list read stores the board's ETag in read_log (etag,
#   etag_seen) and sends it next time. A 304 is COUNTED (etag_304 / etag_200 / etag_304_mismatch
#   in the read_log entry, one "etag:" line per run) and the body is fetched again plainly, so
#   nothing about the sweep's results changes. Three sweeps of counts say whether the tags hold
#   across hours before any read logic trusts them.
#   trust (etag_trust: true): a 304 is a clean full read whose content happened to be identical.
#   The last snapshot's rows for that employer are re-stamped as read live this run, never carried
#   (coverage "OK (304 unchanged)"). See unchanged_rows() and the 304 branch of carry_forward().
# The tag is bound to the snapshot rows it was read with (a full run or --merge writes both, --only
# writes neither) and to the gates it was stripped under (etag_gates), so a 304 can never re-stamp
# rows whose text a widened title list would have kept.
ETAG_ATS = {"greenhouse", "ashby", "lever"}
BOARD_UNCHANGED = object()      # an adapter's answer for a trusted 304: the board is what it was
ETAG_FIELDS = ("etag", "etag_seen", "etag_gates", "etag_304", "etag_200", "etag_304_mismatch")

class Etag304NoRows(Exception):
    """The board said 304 but the snapshot holds nothing to re-stamp (carry expired, rows removed)."""

def get_conditional(url, etag, headers=None):
    """GET with If-None-Match when an ETag is known. -> (status, JSON or None on 304, ETag or None)."""
    h = {"User-Agent": UA, "Accept": "application/json"}
    if headers: h.update(headers)
    if etag: h["If-None-Match"] = etag
    r = _request("GET", url, h)
    if r.status_code == 304:
        return 304, None, etag
    return r.status_code, r.json(), r.headers.get("ETag")

def etag_list(v):
    return [] if not v else ([v] if isinstance(v, str) else list(v))

# Vendors a 304 may be trusted on once etag_trust is on (gates.json etag_trust_ats). Ashby is left out.
ETAG_TRUST_ATS_DEFAULT = ("greenhouse", "lever")

def etag_gates_sig(gates):
    """The gates a snapshot's rows were gated and stripped under. Notes and the switch itself do not count."""
    return jd_hash(json.dumps({k: v for k, v in gates.items() if not k.startswith("_") and k not in ("etag_trust", "etag_trust_ats")},
                              sort_keys=True))

def etag_get(t, url, page=0):
    """The list read for an ETAG_ATS board. run() arms t["_etag_prev"] (the stored tags, one per page)
    and t["_etag_trust"]; a bare target (smoke, probe, tests) reads plainly. Hits, misses and the tags
    seen are kept on t["_etag_stat"]. Returns the JSON, or BOARD_UNCHANGED for a trusted 304."""
    prev = t.get("_etag_prev")
    if prev is None:
        return get(url)
    tag = prev[page] if page < len(prev) else None
    status, d, new = get_conditional(url, tag)
    st = t.setdefault("_etag_stat", {"hit": 0, "miss": 0, "tags": []})
    if status == 304:
        st["hit"] += 1; st["tags"].append(tag)
        if t.get("_etag_trust"): return BOARD_UNCHANGED
        return get(url)                 # measurement: the read the sweep would have made anyway
    if tag: st["miss"] += 1
    st["tags"].append(new)
    return d

def etag_log_fields(t, prior):
    """The read_log fields for one employer's read: the tag(s) to send next time (kept only when
    every page carried one), when that tag was FIRST seen, the gates it was read under, and the
    running counters. {} for a board that did not read through etag_get."""
    st = t.get("_etag_stat")
    if st is None: return {}
    out = {"etag_304": (prior.get("etag_304") or 0) + st["hit"],
           "etag_200": (prior.get("etag_200") or 0) + st["miss"],
           "etag_304_mismatch": (prior.get("etag_304_mismatch") or 0) + st.get("mismatch", 0)}
    tags = st["tags"]
    if tags and all(tags):
        stored = tags[0] if len(tags) == 1 else tags
        out["etag"] = stored
        out["etag_seen"] = (prior.get("etag_seen") if prior.get("etag") == stored and prior.get("etag_seen")
                            else iso_now())
        out["etag_gates"] = t.get("_etag_gates")
    return out

def rescue_summary(dstats, gates):
    """The one "rescue:" line per run. None when no out-of-lane req needed a body read."""
    if not dstats.get("rescue_candidates"): return None
    return (f"rescue: {dstats['rescue_candidates']} out-of-lane req(s) needed a body read, "
            f"{dstats['rescue_fetched']} fetched, {dstats.get('rescue_known', 0)} already read and settled, "
            f"{dstats['rescue_skipped']} left for a later run "
            f"(budget {(gates.get('rescue') or {}).get('max_fetch_per_run', 250)}/run)"
            + (f"; {dstats['rescue_failed']} fetch(es) failed" if dstats.get("rescue_failed") else "")
            + (f"; {dstats['rescue_fail_wait']} earlier failure(s) waiting out the {RESCUE_FAIL_RETRY_DAYS}-day retry"
               if dstats.get("rescue_fail_wait") else ""))

def etag_summary(read_targets, trust):
    """The one "etag:" line per run. None when no ETag board was read live."""
    stats = [t["_etag_stat"] for t in read_targets if t.get("_etag_stat") is not None]
    if not stats: return None
    hit, miss = sum(s["hit"] for s in stats), sum(s["miss"] for s in stats)
    stored = sum(1 for s in stats if s["tags"] and all(s["tags"]))
    tail = f"{stored} of {len(stats)} ETag boards returned a tag to store"
    if trust:
        restamped = sum(1 for t in read_targets if t.get("_etag_restamped"))
        return (f"etag: {hit} of {hit + miss} conditional reads were 304, {restamped} board(s) re-stamped from "
                f"the snapshot; {tail} (trust mode, gates.json etag_trust on)")
    mm = sum(s.get("mismatch", 0) for s in stats)
    bad = sorted(t["company"] for t in read_targets if (t.get("_etag_stat") or {}).get("mismatch"))
    streak = etag_clean_streak(load_json(_etag_runs_path(), []))
    return (f"etag: {hit} of {hit + miss} conditional reads would have been 304 ({mm} with a body that differed "
            f"from the snapshot{': ' + ', '.join(bad[:8]) if bad else ''}); {tail} (measurement mode, gates.json "
            f"etag_trust off); clean streak {streak} of {ETAG_CLEAN_SWEEPS_NEEDED} full sweeps")

# The rule for turning etag_trust on is three FULL sweeps in a row with no mismatch. The
# read_log counter is cumulative per employer, so a streak cannot be read off it. Each full,
# non-resumed sweep in measurement mode appends one record here; --merge, --only and --resume never do.
ETAG_CLEAN_SWEEPS_NEEDED = 3

def _etag_runs_path():
    return DATA / "etag_runs.json"

def etag_clean_streak(runs):
    """Full sweeps in a row, newest back, whose 304s all matched the snapshot. A run with no 304 at all
    proves nothing and ends the streak too."""
    n = 0
    for r in reversed(runs or []):
        if r.get("mismatch") or not r.get("hits"): break
        n += 1
    return n

def etag_record_run(read_targets):
    """Append this full sweep's ETag measurement to data/etag_runs.json. Returns the record, or None
    when no ETag board was read live."""
    stats = [(t["company"], t["_etag_stat"]) for t in read_targets if t.get("_etag_stat") is not None]
    if not stats: return None
    rec = {"date": TODAY.isoformat(), "at": iso_now(), "boards": len(stats),
           "hits": sum(s["hit"] for _, s in stats), "misses": sum(s["miss"] for _, s in stats),
           "mismatch": sum(s.get("mismatch", 0) for _, s in stats),
           "mismatch_boards": sorted(c for c, s in stats if s.get("mismatch"))}
    runs = load_json(_etag_runs_path(), [])
    runs.append(rec)
    write_json_atomic(_etag_runs_path(), runs)
    return rec

MONTHS = {m: i + 1 for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"])}

_TS_ZONED = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})[T ](\d{1,2}):(\d{2})(?::(\d{2})(?:[.,](\d+))?)?\s*(Z|[+-]\d{2}:?\d{2})(?!\d)", re.I)

def epoch_date(v):
    """A Unix epoch (seconds, or milliseconds when 13 digits / over 1e11) -> the LOCAL 'YYYY-MM-DD', or
    None. Several boards publish epochs; dated in UTC, a req posted in the evening west of Greenwich
    would be dated tomorrow, because TODAY is the local date. Never raises."""
    try:
        x = float(str(v).strip()) if not isinstance(v, (int, float)) else float(v)
        if x > 1e11: x /= 1000.0
        if x <= 0: return None
        return dt.datetime.fromtimestamp(x, dt.timezone.utc).astimezone().date().isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return None

def iso_date(s):
    """Everything the boards emit -> 'YYYY-MM-DD', or None. Never raises.

    Seen in the wild: '2026-09-02T00:00:00.000+0000' (Phenom), '2026-9-9' (Radancy JSON-LD),
    'September 2, 2026' (Workday startDate; slicing that to [:10] gives 'September '),
    '9/2/2026', 'Posted Sep 2, 2026'.
    """
    if s is None or s == "" or isinstance(s, bool): return None
    if isinstance(s, (int, float)) or (isinstance(s, str) and re.fullmatch(r"\s*\d{10}(?:\d{3})?\s*", s)):
        return epoch_date(s)
    s = str(s).strip()
    # A moment in time (a clock time plus Z or an offset) is dated in the sweep's own time zone, the
    # same one TODAY uses. Slicing the date off a UTC timestamp dated an evening run's reqs tomorrow.
    # Exactly midnight is a date written as a timestamp ('...T00:00:00.000+0000') and keeps its date.
    m = _TS_ZONED.search(s)
    if m and any(int(x or 0) for x in m.group(4, 5, 6, 7)):
        try:
            tz = m.group(8).upper().replace(":", "")
            off = dt.timezone.utc if tz == "Z" else dt.timezone(
                (1 if tz[0] == "+" else -1) * dt.timedelta(hours=int(tz[1:3]), minutes=int(tz[3:5])))
            t = dt.datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                            int(m.group(4)), int(m.group(5)), int(m.group(6) or 0), tzinfo=off)
            return t.astimezone().date().isoformat()
        except (ValueError, OverflowError, OSError):
            pass
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m: return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.search(r"([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})", s)
    if m and m.group(1).lower()[:3] in [k[:3] for k in MONTHS]:
        mon = next(v for k, v in MONTHS.items() if k.startswith(m.group(1).lower()[:3]))
        return f"{m.group(3)}-{mon:02d}-{int(m.group(2)):02d}"
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m: return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    return None

def days_between(iso, as_of=None):
    """Age in days at as_of (a date or ISO string; default today), or None. Never raises.
    as_of lets a caller measure age at a fixed past date rather than today."""
    try:
        if not iso: return None
        ref = as_of or TODAY
        if isinstance(ref, str): ref = dt.date.fromisoformat(ref)
        return (ref - dt.date.fromisoformat(iso)).days
    except (ValueError, TypeError):
        return None

def days_since(iso):
    """Age in days from an already-normalised date, or None. Never raises. One day in the future is
    date-line skew (a board that dates in UTC, read in the evening), shown as 0, never "-1d"."""
    d = days_between(iso, None)
    return 0 if d == -1 else d

# The K/M suffix must not be the first letter of the next word: in "$95,000 to $120,000 Medical,
# dental and vision" the M of "Medical" would otherwise read as a suffix and make the top of the
# band $120 billion. "$95k - $120k" is unaffected: the k ends the word.
# The boundary sits INSIDE the optional group. A bare lookahead after it lets the engine give a
# digit back instead ("$155,000 - $170,000" matched as "$170,00"); with the group empty, nothing is
# asserted about the next character at all.
MONEY_RANGE_RE = re.compile(
    r"\$\s?([\d,]+(?:\.\d+)?)\s?(?:([KkMm])(?![A-Za-z]))?\s*(?:-|–|—|to)"
    r"\s*\$?\s?([\d,]+(?:\.\d+)?)\s?(?:([KkMm])(?![A-Za-z]))?")
PAY_CONTEXT = ("salary", "compensation", "pay range", "base pay", "pay transparency",
               "on target earnings", "ote", "annual", "per year", "usd", "base range")

def money_range(s):
    """Pull a pay range out of prose, or None. Some structured comp fields (Atlassian's) are a
    boilerplate paragraph with no numbers in it, so storing the field verbatim would paste legal
    text into the report where the band belongs."""
    if not s: return None
    m = MONEY_RANGE_RE.search(s)
    return re.sub(r"\s+", " ", m.group(0)).strip() if m else None

def _money_num(digits, suffix):
    try:
        v = float(digits.replace(",", ""))
    except ValueError:
        return None
    if suffix and suffix.lower() == "k": v *= 1_000
    elif suffix and suffix.lower() == "m": v *= 1_000_000
    return v

def comp_from_description(desc):
    """Fallback when a board exposes no structured comp field. Greenhouse buries the band in a
    pay-transparency div inside the JD body, where a structured-field reader sees 'not posted'.

    Only accepts a range that sits near pay language AND looks like an annual salary, so a
    '$50M - $100M revenue' or '$5 - $10 per unit' in the body can never be mistaken for a band.
    """
    if not desc: return None
    low = desc.lower()
    for m in MONEY_RANGE_RE.finditer(desc):
        window = low[max(0, m.start() - 400): m.end() + 160]
        if not any(k in window for k in PAY_CONTEXT): continue
        if NON_USD.search(low[max(0, m.start() - 40): m.end() + 30]): continue      # "$150,000 to $200,000 CAD"
        lo, hi = _money_num(m.group(1), m.group(2)), _money_num(m.group(3), m.group(4))
        if lo is None or hi is None: continue
        if not (20_000 <= lo <= 2_000_000 and lo <= hi <= 3_000_000): continue
        return re.sub(r"\s+", " ", m.group(0)).strip()
    return band_from_description(desc)

# Second pass. MONEY_RANGE_RE wants "$lo - $hi" back to back; many JDs state a band in another
# shape: "USA, IL, Chicago - 151,200.00 - 204,600.00 USD annually" (Amazon), no "$" at all,
# "between $X and $Y", a currency code or per-year between the figures ("USD $124,000.00 - USD
# $329,200.00", "/year to"), a note between them ("(Developing Minimum for Tier 3)", "[minimum
# salary in our lowest ...]"), and labelled Minimum:/Maximum: pairs. Only runs when the first pass
# finds nothing, so every band the first pass parses keeps its exact text. Figures must be
# comma-grouped, so typos like "$190,00" are refused rather than guessed at, and the same 20k-2M
# annual check applies.
_AMT = r"(\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?)(?!\d)"
_CUR = r"(?:USD|US\$|\$)"
_BETWEEN = (r"(?:\s*(?:USD|usd))?(?:\s*(?:/\s*(?:year|yr|annum)|per\s+(?:year|annum)|annually|annual))?"
            r"(?:\s*(?:\([^()\n]{0,60}\)|\[[^\[\]\n]{0,120}\]))?")
BAND_RE = re.compile(
    r"(?<![A-Za-z$\d,.])(?:(?:min(?:imum)?\.?:?)\s*)?(" + _CUR + r"\s?)?(?:\s*" + _CUR + r"\s?)?" + _AMT + _BETWEEN
    + r"\s*(?:-|–|—|\bto\b|\band\b)\s*(?:max(?:imum)?\.?:?\s*)?((?:USD\s*)?\$?\s?)" + _AMT, re.I)
MINMAX_RE = re.compile(r"minimum\s*:\s*(?:/\s*)?(\$)\s?" + _AMT + r"[^$\d]{0,160}?maximum\s*:\s*(?:/\s*)?\$\s?" + _AMT,
                       re.I)
BAND_CONTEXT = PAY_CONTEXT + ("pay scale", "hiring rate", "pay range")
# A bare figure pair (no $ and no USD) is only a band right after a strong pay phrase.
BARE_BAND_CONTEXT = ("salary", "pay range", "base pay", "compensation", "pay scale", "hiring rate")
NON_USD = re.compile(r"(?<![a-z])(cad|eur|gbp|aud|inr|mxn|chf|sgd|nzd|jpy|brl|pln|sek|dkk|nok|ils)(?![a-z])"
                     r"|[£€¥₹]|(?:ca|c|a|au|nz|s|mx|r)\$|local currency", re.I)

def _band_fmt(lo, hi):
    f = lambda s: s[:-3] if s.endswith(".00") else s
    return f"${f(lo)} - ${f(hi)}"

def band_from_description(desc):
    low = desc.lower()
    found = []                                        # (start, "lo-hi") in reading order
    for m in BAND_RE.finditer(desc):
        lo_s, hi_s = m.group(2), m.group(4)
        lo, hi = _money_num(lo_s, None), _money_num(hi_s, None)
        if lo is None or hi is None or not (20_000 <= lo <= 2_000_000 and lo <= hi <= 3_000_000): continue
        text = m.group(0)
        near = desc[max(0, m.start() - 40): m.end() + 30]
        if NON_USD.search(near): continue
        before, after = low[max(0, m.start() - 400): m.start()], low[m.end(): m.end() + 160]
        has_cur = "$" in text or "usd" in text.lower() or re.match(r"\s*usd\b", after)
        if has_cur:
            if not any(k in before + text.lower() + after for k in BAND_CONTEXT): continue
        else:
            if not any(k in low[max(0, m.start() - 120): m.start()] for k in BARE_BAND_CONTEXT): continue
            # Fortive: "salary range ... (in local currency) is 88,000.00 - 147,000.00". No currency
            # is named, so it is not reported as dollars; nearby bands inherit the same doubt.
            if "local currency" in low[max(0, m.start() - 250): m.start()]: continue
        found.append((m.start(), _band_fmt(lo_s, hi_s)))
    for m in MINMAX_RE.finditer(desc):
        lo, hi = _money_num(m.group(2), None), _money_num(m.group(3), None)
        if lo is None or hi is None or not (20_000 <= lo <= 2_000_000 and lo <= hi <= 3_000_000): continue
        if NON_USD.search(desc[max(0, m.start() - 40): m.end() + 30]): continue
        if not any(k in low[max(0, m.start() - 200): m.start()] for k in BAND_CONTEXT + ("salary range",)): continue
        found.append((m.start(), _band_fmt(m.group(2), m.group(3))))
    if not found: return None
    found.sort()
    bands = list(dict.fromkeys(b for _, b in found))
    # Amazon lists one band per city, NVIDIA one per level: say the first and that others exist.
    return bands[0] + (f" (+{len(bands) - 1} more in JD)" if len(bands) > 1 else "")

ONSITE_PATTERNS = [
    r"(\d{1,3}\s*%[^.]{0,80}?(?:in[- ]office|in one of our offices|onsite|on-site|in the office))",
    r"((?:in[- ]office|onsite|on-site|in the office|in office)[^.]{0,60}?\d{1,2}\s*(?:\+)?\s*days?\s*(?:per|a|each)\s*week)",
    r"(\d{1,2}\s*(?:\+)?\s*days?\s*(?:per|a|each)\s*week[^.]{0,60}?(?:in[- ]office|onsite|on-site|in the office|in office))",
    # "to be in" only when a workplace follows: in person, an office / hub / campus / HQ, a days-per-week
    # cadence, or a "City, ST" place. A bare "to be in" also matched "to be intellectually curious",
    # "to be in the range of $X" and "to be in the room", flagging remote postings as onsite.
    r"((?:expect|require)[a-z]{0,3}\s+[^.]{0,80}?"
    r"(?:to be in\b(?:[- ]person\b|[^.]{0,40}?\b(?:offices?|hub|campus|headquarters|hq|days?\s*(?:/|a|per|each)\s*week)\b"
    r"|\s+[a-z .]{2,30},\s*[a-z]{2}\b)|in one of our offices)[^.]{0,60})",
]

def onsite_terms(desc):
    """Onsite expectations stated in the JD body but not in the board's location field.

    Some boards advertise 'Remote-Friendly' and then say staff are expected in an office at
    least 25% of the time. The location layer cannot see that, so this surfaces it as a flag.
    Never gates: it is information for scoring, not a reject reason.
    """
    if not desc: return None
    low = re.sub(r"\s+", " ", desc.lower())
    for pat in ONSITE_PATTERNS:
        m = re.search(pat, low)
        if m: return re.sub(r"\s+", " ", m.group(1)).strip()[:120]
    return None

def strip_html(s):
    """Unescape FIRST, then strip tags. Greenhouse ships its JD body entity-escaped, so stripping
    first would match nothing and the unescape would then turn &lt;div&gt; back into a live <div>.
    Loop the unescape a couple of times because some boards double-encode."""
    if not s: return ""
    for _ in range(3):
        prev = s
        s = html.unescape(s)
        if s == prev: break
    s = re.sub(r"<(br|/p|/li|/div|/h\d|/tr)[^>]*>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"[ \t]+", " ", s)
    return re.sub(r"\n{3,}", "\n\n", s).strip()

# Remote text is printed to a terminal and written into markdown: control characters (an ESC could
# open an escape sequence) are removed, and a url with a scheme other than http(s) is dropped.
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
SAFE_URL = re.compile(r"https?://", re.I)

def _no_ctrl(s):
    return _CTRL.sub("", s) if isinstance(s, str) else s

def _safe_url(url):
    url = _no_ctrl(url)
    if isinstance(url, str) and re.match(r"[a-z][a-z0-9+.-]*:", url, re.I) and not SAFE_URL.match(url):
        return ""
    return url

def norm(company, ats, jid, title, location, url, posted=None, description="", comp=None, extra=None):
    # Every adapter gets the description fallback, not just Greenhouse: any board that hides the
    # band in the JD body rather than a structured field still reports it.
    if not comp:
        comp = comp_from_description(description)
    return {"key": f"{ats}:{company}:{jid}", "company": company, "ats": ats, "id": str(jid),
            "title": _no_ctrl((title or "").strip()), "location": _no_ctrl((location or "").strip()),
            "url": _safe_url(url),
            "posted": posted, "description": description or "", "comp": comp, "extra": extra or {}}

# ----------------------------------------------------------------------------- adapters
# A Greenhouse location label that names a work style and no place ("Hybrid", "Distributed; Hybrid",
# "Hybrid or Remote"). Only these borrow the offices list; a label that names a place is left alone.
WORK_STYLE_ONLY = re.compile(r"(?:\s|[;,/|]|\bor\b|hybrid|distributed|in[- ]office|on[- ]?site|remote|flexible)+", re.I)

def greenhouse(t, smoke=False):
    d = etag_get(t, f"https://boards-api.greenhouse.io/v1/boards/{t['slug']}/jobs?content=true")
    if d is BOARD_UNCHANGED: return d
    out = []
    for j in d.get("jobs", []):
        posted = iso_date(j.get("first_published") or j.get("updated_at"))
        loc = ((j.get("location") or {}).get("name") or "").strip()
        if loc.lower() in ("", "n/a", "na", "tbd", "various"):
            # Stripe publishes location "N/A" and puts the real scope in offices ("US"); without this
            # the fail-closed location gate rejects the req.
            offices = " | ".join(o.get("name") for o in j.get("offices") or [] if o.get("name"))
            loc = offices or loc
        elif WORK_STYLE_ONLY.fullmatch(loc):
            # Cloudflare labels every req "Hybrid", "Distributed" or "In-Office" and names the cities
            # only in offices, so every row would fail the location gate on the label alone.
            # The label is kept so the remote reading of "Distributed" survives.
            offices = [o.get("location") or o.get("name") for o in j.get("offices") or []]
            offices = [x for x in dict.fromkeys(offices) if x and x.lower() != loc.lower()]
            if offices: loc = " | ".join([loc] + offices)
        out.append(norm(t["company"], "greenhouse", j["id"], j.get("title"),
                        loc, j.get("absolute_url"), posted,
                        strip_html(j.get("content")), None,
                        {"departments": [x.get("name") for x in j.get("departments", []) if x]}))
    return out

def ashby(t, smoke=False):
    d = etag_get(t, f"https://api.ashbyhq.com/posting-api/job-board/{t['slug']}?includeCompensation=true")
    if d is BOARD_UNCHANGED: return d
    out = []
    for j in d.get("jobs", []):
        locs = [j.get("location") or ""] + [x.get("location", "") for x in j.get("secondaryLocations", [])]
        if j.get("isRemote"): locs.append("Remote")
        comp = (j.get("compensation") or {}).get("compensationTierSummary")
        out.append(norm(t["company"], "ashby", j["id"], j.get("title"), " | ".join(x for x in locs if x),
                        j.get("jobUrl"), iso_date(j.get("publishedAt")),
                        j.get("descriptionPlain") or strip_html(j.get("descriptionHtml")), comp,
                        {"employmentType": j.get("employmentType")}))
    return out

def _lever_rows(t, d):
    out = []
    for j in d:
        cats = j.get("categories") or {}
        locs = [cats.get("location") or ""] + list(cats.get("allLocations") or [])
        if j.get("workplaceType") == "remote": locs.append("Remote")
        posted = epoch_date(j["createdAt"]) if j.get("createdAt") else None
        desc = (j.get("descriptionPlain") or "") + "\n" + "\n".join(
            f"{l.get('text','')}\n{strip_html(l.get('content',''))}" for l in j.get("lists", []))
        out.append(norm(t["company"], "lever", j["id"], j.get("text"), " | ".join(x for x in locs if x),
                        j.get("hostedUrl"), posted, desc, None, {"team": cats.get("team")}))
    return out

def lever(t, smoke=False):
    out, skip, page, unread = [], 0, 0, []
    while True:
        url = f"https://api.lever.co/v0/postings/{t['slug']}?mode=json&limit=100&skip={skip}"
        d = etag_get(t, url, page)
        if d is BOARD_UNCHANGED:
            # A trusted 304 on ONE page (Lever tags each page). The board is unchanged only when
            # every page the stored tags cover says so; a later page that did change needs this
            # one's rows after all, so it is read plainly below.
            unread.append(url)
            if page + 1 >= len(t["_etag_prev"]) or smoke: return BOARD_UNCHANGED
            skip += 100; page += 1; continue
        for u in unread: out += _lever_rows(t, get(u))
        unread = []
        if not isinstance(d, list) or not d: break
        out += _lever_rows(t, d)
        if len(d) < 100 or smoke: break
        skip += 100; page += 1
    return out

WORKDAY_CAP = 2000
WORKDAY_SLICE_WORKERS = 4
WORKDAY_SPLIT_AT = 1000
# Workday CXS fails silently in three documented ways: a limit above 20 answers 200 with an empty
# list, `total` is only trustworthy on page one, and paging can stop at a high offset without
# saying so. Each of those reads as a smaller board, never as an error. The page-1 total for every
# walk is kept here so the health block can say "reported N, read M" instead of nothing.
# company -> {"reported": int, "read": int, "complete": bool}. Reset at the top of every run().
WORKDAY_TOTALS = {}
# company -> the scope of a walk that reached its own total (None = the whole board, WORKDAY_SCOPE_LABEL,
# or "facets" for a target's explicit facets). carry_forward closes on these instead of carrying 14 days.
WORKDAY_COMPLETE = {}
WORKDAY_GAP_FRACTION = 0.95      # a shortfall past 5% of the reported total is worth a line
WORKDAY_GAP_MIN_ROWS = 2         # one row short is a req that closed mid-walk, not a defect

def _workday_facets(d):
    """Top-level facets with counted values. Groups such as locationMainGroup nest whole facets
    inside `values`; those are flattened so their inner facets are usable too."""
    out = []
    for f in d.get("facets") or []:
        vals = f.get("values") or []
        if vals and all("facetParameter" in v for v in vals):
            out += [v for v in vals if v.get("values")]
        elif vals:
            out.append(f)
    return out

# Country scope. Workday tenants are a large share of every sweep's rows, and much of that sits
# outside the US: read only to fail the location gate. The search endpoint takes appliedFacets, so
# the walk is scoped to the configured countries, the US and Canada. No two tenants shape the
# location facet alike: NVIDIA has a country
# facet (locationHierarchy1, labelled "Locations", value "United States"), another tenant has only city
# values ("Irvine, CA, USA") under `locations`, and neither carries a "Location Country" label.
# So the facet is found by its VALUES in the tenant's own page-1 response, and its ids are read
# from there, never hard-coded: a country-level value naming the United States wins; otherwise
# every city value whose comma segments carry the country. Both ids go in ONE walk, OR'd on the
# same facet: Workday counts a posting listed in both countries once, so the scoped page-1 total
# is exact and the total guard compares against it (two walks would double-count that posting).
# Explicit per-target `facets` take precedence and are walked as written; a target may
# also set "country_scope": false to keep the full walk.
WORKDAY_SCOPE_COUNTRIES = {"US": {"united states", "united states of america", "usa", "us", "u.s.", "u.s.a."},
                           "CA": {"canada"}}
WORKDAY_SCOPE_LABEL = "US+CA"            # stored in the read log; count_drops reads it
WORKDAY_SCOPE_MARK = "country-scoped"    # in the coverage status of every scoped read

def _workday_country_ids(d):
    """Find the US and Canada ids in a page-1 response. -> (facetParameter, [ids], how) or None.
    Only location-ish facets (key or label mentions location/country) are read, and only a facet
    whose values name the United States counts; Canada rides along when present."""
    def which(text):
        s = (text or "").strip().lower()
        return next((c for c, names in WORKDAY_SCOPE_COUNTRIES.items() if s in names), None)
    country_level, city_level = [], []
    for f in _workday_facets(d):
        key = f.get("facetParameter") or ""
        if not re.search(r"location|country", key + " " + (f.get("descriptor") or ""), re.I):
            continue
        whole = {v["id"]: which(v.get("descriptor")) for v in f["values"] if v.get("id")}
        whole = {i: c for i, c in whole.items() if c}
        if "US" in whole.values():
            country_level.append((len(f["values"]), key, whole)); continue
        parts = {}
        for v in f["values"]:
            if not v.get("id"): continue
            c = next((which(seg) for seg in str(v.get("descriptor") or "").split(",") if which(seg)), None)
            if c: parts[v["id"]] = c
        if "US" in parts.values():
            city_level.append((-len(parts), key, parts))
    if country_level:                     # the most general facet: fewest values
        _, key, ids = min(country_level, key=lambda x: (x[0], x[1]))
        how = f"country facet {key}"
    elif city_level:                      # the facet naming the most US/Canada places
        _, key, ids = min(city_level, key=lambda x: (x[0], x[1]))
        how = f"{sum(1 for c in ids.values() if c == 'US')} US and {sum(1 for c in ids.values() if c == 'CA')} Canada values in facet {key}"
    else:
        return None
    return key, list(ids), how

def _workday_walk(api, applied, seen, smoke, depth=0):
    """Enumerate every posting under `applied`. Workday reports total as min(real, 2000) and will
    not page past 2000, so a capped slice is split on a facet whose values each fit under the cap
    and walked per value (recursively). Returns (true_total_estimate, complete)."""
    def page(off):
        return post_json(f"{api}/jobs", {"appliedFacets": applied, "limit": 20, "offset": off, "searchText": ""})
    d = page(0)
    total = d.get("total", 0)                      # reliable on page one only
    facets = [f for f in _workday_facets(d) if f.get("facetParameter") not in applied]
    sums = [sum(v.get("count", 0) for v in f["values"]) for f in facets]
    # Multi-valued facets (locations) double-count a req listed in several places, so the board size
    # is the SMALLEST facet sum that still covers the capped total, not the largest (Leidos read as
    # ~2,926 when it holds 2,205).
    covering = [s for s in sums if s >= total]
    true_total = total if total < WORKDAY_CAP else (min(covering) if covering else total)
    parts = [(f, s) for f, s in zip(facets, sums)
             if s >= true_total and len([v for v in f["values"] if v.get("count")]) > 1]
    splittable = bool(parts) and depth < 3
    # Deep offsets are not stable on Workday: a slice near the cap can page its full count of rows yet
    # return duplicates and skip live reqs. Walk a slice directly only while it is small; split
    # anything over WORKDAY_SPLIT_AT, and re-split a walked slice that came back short.
    if smoke or (total < WORKDAY_CAP and (total <= WORKDAY_SPLIT_AT or not splittable)):
        local, posts, off = set(), d.get("jobPostings") or [], 0
        while True:
            for p in posts:
                if p.get("externalPath"):
                    seen.setdefault(p["externalPath"], p); local.add(p["externalPath"])
            off += 20
            if smoke or not posts or off >= total: break
            posts = page(off).get("jobPostings") or []
        if smoke or len(local) >= total or not splittable:
            return true_total, smoke or len(local) >= total
    if not splittable:
        for off in range(0, WORKDAY_CAP, 20):                   # best effort: the first 2,000
            posts = page(off).get("jobPostings") or []
            for p in posts:
                if p.get("externalPath"): seen.setdefault(p["externalPath"], p)
            if not posts: break
        return true_total, False
    # The partition whose largest slice is smallest keeps every walk shallow.
    f = min(parts, key=lambda x: max(v.get("count", 0) for v in x[0]["values"]))[0]
    # Slices of a capped board are walked WORKDAY_SLICE_WORKERS at a time. These tenants answer deep
    # pages slowly (several seconds a page), so a capped board walked one slice at a time takes minutes.
    # Only the handful of boards over the cap ever take this path, so the extra concurrency against
    # one tenant is bounded and rare.
    from concurrent.futures import ThreadPoolExecutor
    slices = [dict(applied, **{f["facetParameter"]: [v["id"]]}) for v in f["values"] if v.get("count")]
    workers = 1 if (depth or os.environ.get("MAXQ_SERIAL")) else WORKDAY_SLICE_WORKERS
    with ThreadPoolExecutor(max_workers=workers) as ex:
        oks = list(ex.map(lambda a: _workday_walk(api, a, seen, smoke, depth + 1)[1], slices))
    return true_total, all(oks)

def workday(t, smoke=False):
    """One unfiltered walk of the whole board. Workday keyword search is fuzzy (a phrase can match
    nearly every req on a board), so several queries re-page the same rows and still miss postings
    an empty query returns. Boards over Workday's 2,000-row page cap are split by facet.
    The walk is scoped to the US and Canada when the tenant's own facets allow it
    (see WORKDAY_SCOPE_COUNTRIES); the coverage status says which walk ran."""
    base = f"https://{t['tenant']}.{t['shard']}.myworkdayjobs.com"
    api = f"{base}/wday/cxs/{t['tenant']}/{t['site']}"
    seen, rows = {}, []
    t["_scope"], t["_scope_note"] = None, None
    applied, board_total, found = dict(t.get("facets") or {}), None, None
    if applied:
        t["_scope_note"] = "scoped by target facets"
    elif t.get("country_scope", True) and not smoke:
        # One unfiltered page for the facet block (see WORKDAY_SCOPE_COUNTRIES). Its total is the
        # whole board, capped at 2,000 like any page-1 total, and the scoped walk is checked against it.
        d0 = post_json(f"{api}/jobs", {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""})
        board_total, found = d0.get("total", 0), _workday_country_ids(d0)
        if found:
            applied = {found[0]: found[1]}
        else:
            t["_scope_note"] = "full walk: no location facet names the United States"
    true_total, complete = _workday_walk(api, applied, seen, smoke)
    if found:
        capped = board_total >= WORKDAY_CAP
        if true_total <= 0 or (not capped and true_total > board_total):
            # A scope that reads as empty, or as bigger than the board, is not trusted: the whole
            # board is walked instead and the coverage line says why.
            t["_scope_note"] = (f"full walk: country scope looked wrong ({true_total} of {board_total} via "
                                f"{found[2]}), fell back")
            seen.clear()
            true_total, complete = _workday_walk(api, {}, seen, smoke)
        else:
            t["_scope"] = WORKDAY_SCOPE_LABEL
            t["_scope_note"] = (f"{WORKDAY_SCOPE_MARK}: {true_total} of {board_total}{'+' if capped else ''} "
                                f"board rows, {found[2]}")
    # true_total is the page-1 total on an unsplit walk, and the covering facet sum when the board
    # sits on the 2,000 cap, so one comparison covers both paths. Under a country scope it is the
    # SCOPED page-1 total, so the guard still holds for the subset that was walked. Recorded even
    # when it matches: the health block needs the employers that were fine as well as the ones that
    # were not.
    if not smoke:
        WORKDAY_TOTALS[t["company"]] = {"reported": true_total, "read": len(seen), "complete": complete}
        if complete:
            WORKDAY_COMPLETE[t["company"]] = t["_scope"] or ("facets" if t.get("facets") else None)
    # A slice a row or two short is a posting that closed mid-walk; only a real shortfall is worth a line.
    if not smoke and not complete and len(seen) < 0.99 * true_total:
        print(f"  (workday {t['company']}: {len(seen)} reqs read, board reports ~{true_total}; at least one "
              f"slice paged short and could not be split further)", flush=True)
    for path, p in seen.items():
        jid = path.rsplit("_", 1)[-1] if "_" in path else path
        rows.append(norm(t["company"], "workday", jid, p.get("title"), p.get("locationsText"),
                         f"{base}/en-US/{t['site']}{path}", None, "", None,
                         {"postedOn": p.get("postedOn"), "externalPath": path, "bullets": p.get("bulletFields")}))
    return rows

def workday_detail(t, row):
    """Called only for gate-passing reqs: fills description, posted date, remote type."""
    base = f"https://{t['tenant']}.{t['shard']}.myworkdayjobs.com"
    d = get(f"{base}/wday/cxs/{t['tenant']}/{t['site']}{row['extra']['externalPath']}",
            headers={"Accept-Language": "en-US"})
    info = d.get("jobPostingInfo") or {}
    row["description"] = strip_html(info.get("jobDescription"))
    row["posted"] = iso_date(info.get("startDate"))   # Workday localises this: 'September 2, 2026'
    row["extra"].update({"remoteType": info.get("remoteType"), "timeType": info.get("timeType"),
                         "endDate": info.get("endDate"), "jobReqId": info.get("jobReqId"),
                         "additionalLocations": info.get("additionalLocations")})
    # Multi-location reqs list as "2 Locations" at list level; the real places are location plus
    # additionalLocations. Join them so the location gate sees every city rather than failing on the
    # literal text "N Locations".
    locs = [info.get("location")] + list(info.get("additionalLocations") or [])
    locs = [x for x in dict.fromkeys(l.strip() for l in locs if l and l.strip())]
    if locs: row["location"] = " | ".join(locs)
    # remoteType is not in the location text: a "Remote" req listed as "Dallas" or "Chicago" (Cisco)
    # would fail the location policy as an onsite market. A bare Remote segment is what
    # location_policy_ok reads; the US check still needs the city.
    if "remote" in (info.get("remoteType") or "").lower() and not re.search(r"\bremote\b", row["location"], re.I):
        row["location"] = (row["location"] + " | Remote") if row["location"] else "Remote"
    return row

def eightfold(t, smoke=False):
    # Eightfold hard-caps a page at 10 rows regardless of num=; paginate on start= until the
    # reported count is consumed (num=100 still returns 10).
    seen, out = {}, []
    for q in (EIGHTFOLD_QUERIES[:1] if smoke else EIGHTFOLD_QUERIES):
        start, t["_stale_pages"] = 0, 0
        while True:
            d = get(f"{t['base']}/api/apply/v2/jobs?domain={t['domain']}&query={requests.utils.quote(q)}"
                    f"&num=100&start={start}&sort_by=timestamp")
            pos = d.get("positions") or []
            for p in pos: seen[p["id"]] = p
            total = d.get("count") or 0
            start += len(pos)
            if not pos or start >= total or start >= 500 or smoke: break
            if incremental_stop(t, [p["id"] for p in pos]): break
    for pid, p in seen.items():
        posted = epoch_date(p["t_create"]) if p.get("t_create") else None
        out.append(norm(t["company"], "eightfold", pid, p.get("name"),
                        " | ".join(p.get("locations") or [p.get("location") or ""]),
                        p.get("canonicalPositionUrl") or f"{t['base']}/careers/job/{pid}", posted, "", None,
                        {"t_update": p.get("t_update"), "note": "t_create is a refresh date on Netflix; trust first_seen"}))
    return out

def eightfold_detail(t, row):
    d = get(f"{t['base']}/api/apply/v2/jobs/{row['id']}?domain={t['domain']}")
    row["description"] = strip_html(d.get("job_description") or (d.get("position") or {}).get("job_description"))
    return row

def smartrecruiters(t, smoke=False):
    out, offset = [], 0
    while True:
        d = get(f"https://api.smartrecruiters.com/v1/companies/{t['slug']}/postings?limit=100&offset={offset}")
        c = d.get("content") or []
        for j in c:
            loc = j.get("location") or {}
            locs = ", ".join(x for x in [loc.get("city"), loc.get("region"), loc.get("country")] if x)
            if loc.get("remote"): locs += " | Remote"
            out.append(norm(t["company"], "smartrecruiters", j["id"], j.get("name"), locs,
                            f"https://jobs.smartrecruiters.com/{t['slug']}/{j['id']}",
                            iso_date(j.get("releasedDate")), "", None, {"ref": j.get("ref")}))
        offset += 100
        if len(c) < 100 or smoke: break
    return out

def smartrecruiters_detail(t, row):
    d = get(f"https://api.smartrecruiters.com/v1/companies/{t['slug']}/postings/{row['id']}")
    secs = (d.get("jobAd") or {}).get("sections") or {}
    row["description"] = "\n".join(strip_html(s.get("text")) for s in secs.values() if isinstance(s, dict))
    return row

def amazon_wide_out_of_scope(j):
    """True for a req the wide queries found in an excluded business category or team."""
    team = j.get("team")
    team = (team.get("label") if isinstance(team, dict) else team) or ""
    return (j.get("business_category") or "") in _AMZ_WIDE_OUT_CATEGORIES or bool(_AMZ_WIDE_OUT_TEAM.search(team.lower()))

def amazon(t, smoke=False):
    seen, lane_ids = {}, set()
    queries = AMAZON_QUERIES[:1] if smoke else AMAZON_QUERIES + _AMZ_WIDE
    for q in queries:
        offset = 0
        while True:
            d = get(f"https://www.amazon.jobs/en/search.json?base_query={requests.utils.quote(q)}"
                    f"&sort=recent&country=USA&result_limit=100&offset={offset}")
            jobs = d.get("jobs") or []
            for j in jobs:
                seen[j["id_icims"]] = j
                if q not in _AMZ_WIDE: lane_ids.add(j["id_icims"])
            offset += 100
            if len(jobs) < 100 or smoke: break
    return [_amazon_row(jid, j) for jid, j in seen.items()
            if jid in lane_ids or not amazon_wide_out_of_scope(j)]

def _amazon_row(jid, j):
    desc = "\n".join(strip_html(j.get(k)) for k in ("description", "basic_qualifications", "preferred_qualifications"))
    return norm("Amazon", "amazon", jid, j.get("title"), j.get("location") or j.get("normalized_location"),
                "https://www.amazon.jobs" + (j.get("job_path") or ""), iso_date(j.get("posted_date")),
                desc, None, {"team": j.get("team", {}).get("label") if isinstance(j.get("team"), dict) else None,
                             "business_category": j.get("business_category")})

def amazon_lookup(t, jid):
    """One req by id. search.json matches base_query against the id exactly: an open req comes
    back as the single hit, a closed one returns hits=0 (its job page 404s). No country filter,
    so a req that moved abroad still resolves."""
    d = get(f"https://www.amazon.jobs/en/search.json?base_query={requests.utils.quote(str(jid))}&result_limit=10")
    for j in d.get("jobs") or []:
        if str(j.get("id_icims")) == str(jid):
            return _amazon_row(j["id_icims"], j)
    return None

def atlassian(t, smoke=False):
    """Atlassian publishes its whole board as one JSON array. Full JD text is inline, so no
    detail pass is needed. updatedDate is a refresh date, not a posted date, so posted stays
    None and freshness falls back to the first_seen ledger."""
    d = get("https://www.atlassian.com/endpoint/careers/listings",
            headers={"Referer": "https://www.atlassian.com/company/careers/all-jobs"})
    # This feed repeats some reqs verbatim. Deduping is NOT done here:
    # run() collapses duplicate keys for every adapter and reports the count, so a feed that
    # starts repeating itself shows up in the coverage table instead of being silently absorbed.
    out = []
    for j in d if isinstance(d, list) else []:
        pjp = j.get("portalJobPost") or {}
        desc = "\n".join(strip_html(j.get(k)) for k in ("overview", "responsibilities", "qualifications"))
        out.append(norm(t["company"], "atlassian", j.get("id"), j.get("title"),
                        " | ".join(j.get("locations") or []),
                        pjp.get("portalUrl") or j.get("applyUrl"), None, desc,
                        money_range(strip_html(j.get("compensation"))),
                        {"category": j.get("category"),
                         "refreshed": iso_date(pjp.get("updatedDate")) or pjp.get("updatedDate"),
                         "date_kind": "refreshed",
                         "note": "Atlassian publishes a refresh date, never a posting date; "
                                 "freshness comes from the first_seen ledger"}))
    return out

def _phenom_body(kw, frm, size, **extra):
    b = {"lang": "en_us", "deviceType": "desktop", "country": "us", "pageName": "search-results",
         "ddoKey": "refineSearch", "sortBy": "", "subsearch": "", "from": frm, "jobs": True,
         "counts": True, "all_fields": [], "size": size, "clearAll": False, "jdsource": "facets",
         "isSliderEnable": False, "pageId": "page11", "siteType": "external", "keywords": kw,
         "global": True, "selected_fields": {}, "locationData": {}}
    b.update(extra)
    return b

def phenom(t, smoke=False):
    """Phenom People career sites: POST <base>/widgets with ddoKey=refineSearch. The page size
    is capped at 10 whatever size= says, same trap as Eightfold, so paginate on from= against
    the reported totalHits."""
    base = t["base"].rstrip("/")
    url = base + t.get("path", "/widgets")
    ref = t.get("referer", base)
    seen = {}
    for q in (PHENOM_QUERIES[:1] if smoke else PHENOM_QUERIES):
        frm = 0
        while True:
            d = post_json(url, _phenom_body(q, frm, 10), {"Referer": ref})
            rs = d.get("refineSearch") or {}
            jobs = ((rs.get("data") or {}).get("jobs")) or []
            for j in jobs:
                seen[j.get("jobId") or j.get("jobSeqNo")] = j
            total = rs.get("totalHits") or 0
            frm += len(jobs)
            if not jobs or frm >= total or frm >= 500 or smoke: break
    out = []
    for jid, j in seen.items():
        out.append(norm(t["company"], "phenom", jid, j.get("title"),
                        j.get("location") or j.get("cityStateCountry") or j.get("cityState"),
                        j.get("applyUrl") or j.get("jobUrl"),
                        iso_date(j.get("postedDate") or j.get("dateCreated")),
                        strip_html(j.get("descriptionTeaser")), None,
                        {"jobSeqNo": j.get("jobSeqNo"), "category": j.get("category"),
                         "remoteType": j.get("RemoteType"), "backing_ats": j.get("ats")}))
    return out

def phenom_detail(t, row):
    base = t["base"].rstrip("/")
    d = post_json(base + t.get("path", "/widgets"),
                  _phenom_body("", 0, 10, ddoKey="jobDetail", pageName="job-details",
                               pageId="page12", jobSeqNo=row["extra"].get("jobSeqNo")),
                  {"Referer": t.get("referer", base)})
    j = ((d.get("jobDetail") or {}).get("data") or {}).get("job") or {}
    row["description"] = strip_html(j.get("description"))
    row["posted"] = iso_date(j.get("postedDate")) or row.get("posted")
    return row

# Radancy / TalentBrew (vendor-hosted career sites, usually jobs.<employer>.com or
# careers.<employer>.com). Board behaviour each constant below answers:
#   - RecordsPerPage=500 is honoured on every site, and a page's cost is almost all fixed overhead
#     (a 500-tile page is barely larger than a 100-tile one), so requests, not rows, are the cost.
#     The whole board at 500/page takes fewer requests than a few keyword queries at 100/page.
#   - A fixed page cap silently truncates a large board, so the walk stops on data-total-results
#     and raises on a short read.
#   - Class attributes carry extras (one site: class="results-facet job-location test3"), so span
#     classes are matched as words, never as the whole attribute.
#   - Some tiles carry the posted date (class="job-date-posted", MM/DD/YYYY); taken when present.
RADANCY_PAGE = 500
RADANCY_MAX_PAGES = 40
_RADANCY_HREF = re.compile(r'href="(/[^"]*?/?job/[^"#]+?/(\d+)/(\d+))/?(?:#[^"]*)?"')
_RADANCY_SPAN = r'<span[^>]*class="[^"]*\b{cls}\b[^"]*"[^>]*>(.*?)</span>'

def radancy_results_url(base, page, per=RADANCY_PAGE, keywords=""):
    return (f"{base}/search-jobs/results?ActiveFacetID=0&CurrentPage={page}&RecordsPerPage={per}"
            f"&Distance=50&RadiusUnitType=0&Keywords={requests.utils.quote(keywords)}&Location="
            f"&ShowRadius=False&IsPagination=True&CustomFacetName=&FacetTerm=&FacetType=0"
            f"&FacetFilters=&SearchResultsModuleName=Search+Results"
            f"&SearchFiltersModuleName=Search+Filters&SortCriteria=0&SortDirection=0&SearchType=5")

RADANCY_RETRY_WAIT = (2, 5, 10)

def radancy_page(base, page, per=RADANCY_PAGE, keywords=""):
    """One results page as the board's JSON. A TalentBrew site now and then fails to render the JSON
    for a request, answers 400 (a 302 to /error/jsonrequesterror), and caches that failure per URL: the
    same URL then answers 400 on every read until the entry expires, while any other URL for the same
    rows renders. It can hit any page, including page 1 and a URL that was clean minutes earlier.
    Every retry is a fresh URL (cache-busting
    parameter) after a short wait, because a fresh URL is a fresh roll and the failures cluster in time.
    A 400 is the only status retried here: 429 and 5xx belong to _request, and anything else is real."""
    hdr = {"X-Requested-With": "XMLHttpRequest", "Accept": "application/json",
           "Referer": base + "/search-jobs"}
    u = radancy_results_url(base, page, per, keywords)
    for attempt in range(len(RADANCY_RETRY_WAIT) + 1):
        try:
            return get(u if attempt == 0 else f"{u}&_cb={int(time.time() * 1000)}{attempt}", headers=hdr)
        except requests.exceptions.HTTPError as e:
            if getattr(e.response, "status_code", None) != 400 or attempt >= len(RADANCY_RETRY_WAIT): raise
            time.sleep(RADANCY_RETRY_WAIT[attempt])

def radancy_tiles(fragment):
    """-> (data-total-results or None, [(jid, href, title, location, posted, extra)]) from the
    'results' HTML fragment. Some sites' tiles end the href in "/#job-details-section", so an optional
    trailing slash and fragment are accepted after the job id."""
    fragment = fragment or ""
    tot = re.search(r'data-total-results="(\d+)"', fragment)
    out = []
    for tile in re.findall(r"<li\b[^>]*>.*?</li>", fragment, flags=re.S):
        m = _RADANCY_HREF.search(tile)
        if not m: continue
        href, _cid, jid = m.groups()
        title = re.search(r"<h2[^>]*>(.*?)</h2>", tile, flags=re.S)
        loc = re.search(_RADANCY_SPAN.format(cls="job-location"), tile, flags=re.S)
        posted = re.search(_RADANCY_SPAN.format(cls="job-date-posted"), tile, flags=re.S)
        cat = re.search(r"data-category='([^']*)'", tile) or re.search(_RADANCY_SPAN.format(cls="job-category"), tile, flags=re.S)
        out.append((jid, href, strip_html(title.group(1)) if title else "",
                    strip_html(loc.group(1)) if loc else "",
                    iso_date(strip_html(posted.group(1))) if posted else None,
                    {"category": strip_html(cat.group(1)).strip() if cat else None}))
    return (int(tot.group(1)) if tot else None), out

def radancy(t, smoke=False):
    """Whole-board walk of a Radancy / TalentBrew site: empty Keywords=, 500 tiles a page, stop on
    data-total-results. Enumerable, so absence closes a req (see ENUMERABLE_ATS). Posted date and JD
    text come from the detail pass unless the tile carries the date."""
    origin = t["origin"].rstrip("/")
    base = origin + t.get("path", "")
    seen, total, parsed = {}, None, 0
    for page in range(1, RADANCY_MAX_PAGES + 1):
        d = radancy_page(base, page)
        tot, tiles = radancy_tiles(d.get("results"))
        if total is None: total = tot
        parsed += len(tiles)
        new = 0
        for jid, href, title, loc, posted, extra in tiles:
            if jid in seen: continue
            new += 1
            seen[jid] = norm(t["company"], "radancy", jid, title, loc, origin + href, posted, "", None, extra)
        if smoke or len(tiles) < RADANCY_PAGE or not new or (total is not None and parsed >= total): break
    if not smoke and total and parsed < total * 0.95:
        # A capped or truncated walk must not read as a clean enumeration: on an enumerable board every
        # missing req would be recorded closed. An ERROR carries them instead. 5% covers reqs that close
        # between page 1's count and the last page.
        raise RuntimeError(f"radancy {base}: short read, {parsed} tiles of data-total-results={total}")
    return list(seen.values())

def radancy_detail(t, row):
    """Radancy job pages carry a schema.org JobPosting block with the date and full text."""
    txt = get_text(row["url"])
    for blob in re.findall(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', txt, flags=re.S):
        try:
            d = json.loads(blob.strip())
        except Exception:
            continue
        if not isinstance(d, dict) or d.get("@type") != "JobPosting": continue
        row["description"] = strip_html(d.get("description"))
        row["posted"] = iso_date(d.get("datePosted")) or row.get("posted")
        locs = d.get("jobLocation") or []
        if isinstance(locs, dict): locs = [locs]
        names = []
        for L in locs:
            a = (L or {}).get("address") or {}
            names.append(", ".join(x for x in [a.get("addressLocality"), a.get("addressRegion"),
                                               a.get("addressCountry")] if x))
        if names: row["location"] = " | ".join(dict.fromkeys(n for n in names if n))
        row["extra"]["employmentType"] = d.get("employmentType")
        break
    return row

# ------------------------------------------------------------------ metacareers (Meta)
# Two things about this board that are not obvious:
#   1. Meta 400s any request that does not look like a browser navigation. A bare urllib or
#      requests GET with Accept: application/json gets 400 Bad Request, not 403, which reads
#      like a broken URL. The Sec-Fetch-* set and a real UA are what make it answer.
#   2. The search result node carries ONLY id, title, locations, teams, sub_teams. There is no
#      posted date anywhere in the search response, so freshness is impossible from the list
#      pass alone. The date lives on the job page, in a schema.org JobPosting block, alongside
#      the full description, responsibilities and qualifications. Detail pass is mandatory here
#      for any date-aware question, unlike Greenhouse or Ashby where the list carries the date.
# The GraphQL call needs an LSD token and doc_id, both scraped from the jobs page and its Relay
# bundle. doc_id is pinned below; if Meta ships a new Relay build it changes and the call starts
# returning an error payload rather than failing loudly, so the adapter raises on an empty board.
_META_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")
_META_NAV = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
             "Accept-Language": "en-US,en;q=0.9", "Sec-Fetch-Dest": "document",
             "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Site": "none", "Sec-Fetch-User": "?1",
             "Upgrade-Insecure-Requests": "1"}
_META_DOC_ID = "27506805582236862"          # CareersJobSearchResultsDataQuery
_meta_sess = None

def _meta_session():
    """One session for the whole run: the LSD token and the datr cookie are bootstrapped once."""
    global _meta_sess
    if _meta_sess is not None: return _meta_sess
    s = requests.Session()
    s.headers.update({"User-Agent": _META_UA, "Accept-Language": "en-US,en;q=0.9"})
    r = s.get("https://www.metacareers.com/jobs/", headers=_META_NAV, timeout=30)
    r.raise_for_status()
    lsd = re.search(r'"LSD",\[\],\{"token":"([^"]+)"', r.text)
    rev = re.search(r'"server_revision":(\d+)', r.text)
    if not lsd:
        raise RuntimeError("metacareers: no LSD token on the jobs page; markup changed")
    s._lsd = lsd.group(1)
    s._rev = rev.group(1) if rev else ""
    _meta_sess = s
    return s

def metacareers(t, smoke=False):
    s = _meta_session()
    body = {"lsd": s._lsd, "doc_id": _META_DOC_ID, "fb_api_caller_class": "RelayModern",
            "fb_api_req_friendly_name": "CareersJobSearchResultsDataQuery",
            "server_timestamps": "true", "__rev": s._rev, "__a": "1",
            "variables": json.dumps({"isLoggedIn": False, "search_input": {}, "viewasUserID": None})}
    time.sleep(DELAY)
    r = s.post("https://www.metacareers.com/graphql", data=body, timeout=45, headers={
        "Content-Type": "application/x-www-form-urlencoded", "X-FB-LSD": s._lsd,
        "Origin": "https://www.metacareers.com", "Referer": "https://www.metacareers.com/jobs/",
        "Accept": "*/*", "Sec-Fetch-Dest": "empty", "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin", "X-Requested-With": "XMLHttpRequest"})
    r.raise_for_status()
    txt = r.text[9:] if r.text.startswith("for (;;);") else r.text
    d = ((json.loads(txt).get("data") or {}).get("job_search_with_featured_jobs") or {})
    jobs = (d.get("all_jobs") or []) + (d.get("featured_jobs") or [])
    if not jobs:
        # Silent-zero trap: a rotated doc_id returns a well-formed payload with no rows; never let
        # that read as "board empty".
        raise RuntimeError("metacareers: 0 rows. doc_id likely rotated; re-scrape it from the "
                           "Relay bundle (grep CareersJobSearchResultsDataQuery_candidate_portalRelayOperation)")
    out, seen = [], set()
    for j in jobs:
        if j["id"] in seen: continue
        seen.add(j["id"])
        out.append(norm("Meta", "metacareers", j["id"], j.get("title"),
                        " | ".join(j.get("locations") or []),
                        f"https://www.metacareers.com/jobs/{j['id']}/", None, "", None,
                        {"teams": j.get("teams") or [], "sub_teams": j.get("sub_teams") or []}))
        if smoke and len(out) >= 5: break
    return out

def metacareers_detail(t, row):
    """Date, full text and the remote flag all come off the job page's JobPosting block."""
    s = _meta_session()
    time.sleep(DELAY)
    r = s.get(row["url"], headers=_META_NAV, timeout=30)
    r.raise_for_status()
    for blob in re.findall(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', r.text, flags=re.S):
        try:
            d = json.loads(html.unescape(blob.strip()))
        except Exception:
            continue
        if not isinstance(d, dict) or d.get("@type") != "JobPosting": continue
        # Meta splits the posting across three fields; the qualifications block is where the
        # years bar lives, so concatenating all three is what makes the tenure gate work.
        row["description"] = "\n\n".join(strip_html(d.get(k)) for k in
                                         ("description", "responsibilities", "qualifications") if d.get(k))
        row["posted"] = iso_date(d.get("datePosted"))
        locs = d.get("jobLocation") or []
        if isinstance(locs, dict): locs = [locs]
        names = [(L or {}).get("name") or "" for L in locs]
        if d.get("jobLocationType") == "TELECOMMUTE": names.append("Remote")
        if any(names): row["location"] = " | ".join(dict.fromkeys(n for n in names if n))
        row["extra"]["employmentType"] = d.get("employmentType")
        row["extra"]["validThrough"] = iso_date(d.get("validThrough"))
        break
    return row

def oracle(t, smoke=False):
    """Oracle Fusion Recruiting (Candidate Experience). Oracle's own ATS, not a vendor board.

    Enumerable. findReqs returns the whole site sorted POSTING_DATES_DESC, paginated on offset.
    Two traps:
      - the page is hard-capped at 200 rows. limit=500 is echoed back in the response as
        Limit: 500 and still returns exactly 200. Trusting the echo would silently truncate
        the board to its first 200 reqs.
      - `expand=` is NOT optional decoration. Drop it and the endpoint answers 200 with a null
        requisitionList at EVERY offset, including offset 0, which reads as an empty board and
        would trip the silent-zero guard as a slug problem that is not one.
    TotalJobsCount is reliable on every page, so pagination stops on it rather than on a short
    page. No JD text at list level: ShortDescriptionStr is one sentence, so the detail pass is
    mandatory before anything here can be scored.
    """
    host, site = t.get("host", "eeho.fa.us2.oraclecloud.com"), t.get("site", "CX_1")
    out, offset = [], 0
    while True:
        d = get(f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
                f"?onlyData=true&expand=requisitionList.secondaryLocations"
                f"&finder=findReqs;siteNumber={site},limit=200,offset={offset},sortBy=POSTING_DATES_DESC")
        item = (d.get("items") or [{}])[0]
        reqs = item.get("requisitionList") or []
        total = item.get("TotalJobsCount") or 0
        for j in reqs:
            locs = [j.get("PrimaryLocation") or ""] + [
                (s or {}).get("Name") or "" for s in (j.get("secondaryLocations") or [])]
            if "remote" in (j.get("WorkplaceType") or "").lower(): locs.append("Remote")
            out.append(norm(t["company"], "oracle", j["Id"], j.get("Title"),
                            " | ".join(dict.fromkeys(x for x in locs if x)),
                            # Oracle itself publishes at careers.oracle.com; any other Fusion tenant
                            # (Icertis) links through its own host and a named site (job_url_site).
                            (f"https://{host}/hcmUI/CandidateExperience/en/sites/{t['job_url_site']}/job/{j['Id']}"
                             if t.get("job_url_site") else
                             f"https://careers.oracle.com/jobs/#en/sites/jobsearch/job/{j['Id']}"),
                            iso_date(j.get("PostedDate")), "", None,
                            {"country": j.get("PrimaryLocationCountry"),
                             "workplaceType": j.get("WorkplaceType"),
                             "short": j.get("ShortDescriptionStr")}))
        offset += len(reqs)
        if not reqs or offset >= total or smoke: break
        if incremental_stop(t, [j["Id"] for j in reqs]): break
    return out

def oracle_detail(t, row):
    """Called only for gate-passing reqs: fills the JD body, the exact posted date and the
    workplace type.

    The finder is ById with a QUOTED Id, not the ByRequisitionId the resource name suggests.
    Every ByRequisitionId spelling answers 400 with an EMPTY body, so there is nothing in the
    error to read; the working form came out of the CE bundle
    (static.oracle.com/cdn/fa/oj-hcm-ce/.../main-minimal.js, `finder=ById;:findParams:` with
    findParams {Id: '"<id>"', siteNumber}). Do not "simplify" the %22 away.
    """
    host, site = t.get("host", "eeho.fa.us2.oraclecloud.com"), t.get("site", "CX_1")
    d = get(f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails"
            f"?expand=all&onlyData=true&finder=ById;Id=%22{row['id']}%22,siteNumber={site}")
    j = (d.get("items") or [{}])[0]
    row["description"] = "\n".join(strip_html(p) for p in (
        j.get("ExternalDescriptionStr"), j.get("ExternalResponsibilitiesStr"),
        j.get("ExternalQualificationsStr")) if p)
    row["posted"] = iso_date(j.get("ExternalPostedStartDate")) or row.get("posted")
    # norm() ran at LIST time, when there was no description to read a band out of, so the comp
    # fallback found nothing. Oracle states a band on every US req ("Hiring Range in USD from:
    # $92,900 to $209,500 per annum"), so re-run the fallback now that the body is here.
    # Most other detail adapters do not re-run it, so their rows carry comp only from a structured
    # field; changing that would re-band rows already in the ledger.
    if not row.get("comp"): row["comp"] = comp_from_description(row["description"])
    row["extra"].update({"workplaceType": j.get("WorkplaceType"),
                         "postedEnd": iso_date(j.get("ExternalPostedEndDate")),
                         "requisitionNumber": j.get("RequisitionId"),
                         "jobFunction": j.get("JobFunction"), "careerLevel": j.get("ManagerLevel")})
    return row

def _pcsx_get(url, params):
    """Eightfold's newer PCSX surface rate-limits hard: a 0.15s walk drew a run of 429s whose body
    is plain text ('Please try again later') with no Retry-After, so get() both gave up too early
    and would have crashed on .json(). Pace at 0.6s and back off up to a minute."""
    h = {"User-Agent": UA, "Accept": "application/json"}
    for attempt in range(8):
        time.sleep(0.6)
        r = http_session().get(url, params=params, headers=h, timeout=30)
        if r.status_code in (429, 503):
            time.sleep(min(60, 5 * (attempt + 1)))
            continue
        r.raise_for_status()
        return r.json().get("data") or {}
    r.raise_for_status()
    raise RuntimeError(f"pcsx: still rate-limited after 8 attempts: {url}")

def pcsx(t, smoke=False):
    """Eightfold PCSX (e.g. apply.careers.microsoft.com).

    The legacy /api/apply/v2/jobs route that the eightfold adapter uses answers
    403 'Not authorized for PCSX' on these tenants; /api/pcsx/search on the same host answers 200.
    Any Eightfold tenant that returns that 403 is worth trying here.

    Walks the country-scoped board with no keyword. Pages are 10 rows. Query-scoped, NOT
    enumerable: sort_by=timestamp is not a strict order on postedTs, so offset paging can shift a
    row across a page boundary, and absence is therefore not proof of closure.
    List-level workLocationOption says 'onsite' on nearly every row and is not the real work
    site; the detail field efcustomTextWorkSite is ('0 days / week in-office – remote').
    """
    loc = t.get("location", "United States")
    seen, start = {}, 0
    while True:
        # Optional `params` in the target (e.g. filter_job_category) narrow a huge board such as
        # Starbucks' 21k store roles to corporate work without a city scope.
        d = _pcsx_get(f"{t['base']}/api/pcsx/search",
                      dict({"domain": t["domain"], "location": loc, "start": start, "sort_by": "timestamp"},
                           **(t.get("params") or {})))
        pos = d.get("positions") or []
        for p in pos: seen.setdefault(p["id"], p)
        start += len(pos)
        if not pos or start >= (d.get("count") or 0) or smoke: break
        if incremental_stop(t, [p["id"] for p in pos]): break
    out = []
    for pid, p in seen.items():
        locs = list(p.get("locations") or [])
        if (p.get("workLocationOption") or "").startswith("remote"): locs.append("Remote")
        posted = epoch_date(p["postedTs"]) if p.get("postedTs") else None
        created = epoch_date(p["creationTs"]) if p.get("creationTs") else None
        out.append(norm(t["company"], "pcsx", pid, p.get("name"), " | ".join(locs),
                        f"{t['base']}/careers/job/{pid}", posted, "", None,
                        {"displayJobId": p.get("displayJobId"), "department": p.get("department"),
                         "created": created}))
    return out

def pcsx_detail(t, row):
    d = _pcsx_get(f"{t['base']}/api/pcsx/position_details",
                  {"domain": t["domain"], "position_id": row["id"], "hl": "en"})
    row["description"] = strip_html(d.get("jobDescription"))
    site = "; ".join(d.get("efcustomTextWorkSite") or [])
    if ("remote" in site.lower() or site.startswith("0 days")) and "Remote" not in row["location"]:
        row["location"] += " | Remote"
    if not row.get("comp"): row["comp"] = comp_from_description(row["description"])
    row["extra"].update({"workSite": site or None,
                         "roleType": "; ".join(d.get("efcustomTextRoletype") or []) or None,
                         "travel": "; ".join(d.get("efcustomTextRequiredTravel") or []) or None,
                         "profession": "; ".join(d.get("efcustomTextCurrentProfession") or []) or None})
    return row

ADAPTERS = {"greenhouse": greenhouse, "ashby": ashby, "lever": lever, "workday": workday,
            "eightfold": eightfold, "smartrecruiters": smartrecruiters, "amazon": amazon,
            "atlassian": atlassian, "phenom": phenom, "radancy": radancy,
            "metacareers": metacareers, "oracle": oracle, "pcsx": pcsx}

# Which adapters ENUMERATE the whole board, and which only SAMPLE it with queries.
# This decides whether "absent from this read" is evidence a req closed.
#   Enumerable: the endpoint returns the entire board, so absence is meaningful.
#   Query-scoped: the adapter runs a fixed list of search terms and takes what comes
#   back. Amazon's search is not stable: the same queries a day apart can return mostly
#   different rows with nothing closed. Treating absence as closure there deletes live reqs.
#   metacareers is enumerable: the unfiltered search returns the entire board in one call, and
#   team-filtered calls were checked against it and returned strict subsets, no extra rows.
#   oracle is enumerable: findReqs with no keyword walks the entire site by offset and
#   TotalJobsCount agrees with what comes back, so absence is real.
#   radancy is enumerable: an empty Keywords= walks the whole site at 500 a page, the walk stops
#   on data-total-results and raises on a short read, and keyword queries return strict subsets
#   of it (tests/test_radancy.py has the fixtures).
ENUMERABLE_ATS = {"greenhouse", "ashby", "lever", "atlassian", "smartrecruiters", "metacareers",
                  "oracle", "radancy"}
DETAIL = {"workday": workday_detail, "eightfold": eightfold_detail, "smartrecruiters": smartrecruiters_detail,
          "phenom": phenom_detail, "radancy": radancy_detail, "metacareers": metacareers_detail,
          "oracle": oracle_detail, "pcsx": pcsx_detail}
# Single-req lookup for QUERY-SCOPED boards: (target, id) -> row if open, None if closed, raises
# if the lookup itself failed. A req already scored must not fall out of a sampled board just
# because the queries stopped returning it: carry-forward only looks one snapshot back, so a req
# that drops out of a snapshot while still open would otherwise never come back. See recover_scored().
LOOKUP = {"amazon": amazon_lookup}

# Which verdicts earn a detail fetch. Without a detail fetch a req has no posted date and so can
# never enter a --window. LOCATION-POLICY and TENURE reqs can still be approved or overridden per
# req, and nobody can decide that about a req with no date or text. The added cost is small next
# to the PASS fetches.
DETAIL_VERDICTS = ("PASS", "LOCATION-POLICY", "TENURE")

# ----------------------------------------------------------------------------- adapter plugins
# Newer adapters live in adapters/<vendor>.py, one file per ATS, so a new board never means
# another edit to this file. Contract (adapters/README.md):
#   NAME = "<ats key used in targets.json>"
#   ENUMERABLE = True | False        # does list_jobs return the WHOLE board? (see ENUMERABLE_ATS)
#   REQUIRED = ["base", ...]         # keys a targets.json entry must carry
#   def list_jobs(t, h, smoke=False) -> [h.norm(...), ...]
#   def detail(t, row, h) -> row     # optional; fills description / posted / location / comp
# `h` is the helper namespace below, so plugins share this file's HTTP retry policy, date
# parsing and band extraction instead of growing their own copies.
ADAPTER_DIR = ROOT / "adapters"
PLUGIN_ERRORS = {}          # ats name or file -> import error, surfaced as ERROR in coverage

def plugin_helpers():
    import types
    return types.SimpleNamespace(
        get=get, post_json=post_json, get_text=get_text, request=_request, norm=norm,
        strip_html=strip_html, iso_date=iso_date, days_since=days_since, money_range=money_range,
        comp_from_description=comp_from_description, requests=requests, UA=UA, DELAY=DELAY,
        incremental_stop=lambda t, ids: incremental_stop(t, ids),
        quote=(lambda s: requests.utils.quote(s)) if requests else None, TODAY=TODAY)

def load_plugins(adapter_dir=None):
    import importlib.util
    d = Path(adapter_dir or ADAPTER_DIR)
    if not d.is_dir(): return []
    loaded, h = [], plugin_helpers()
    for p in sorted(d.glob("*.py")):
        if p.name.startswith("_") or p.stem in ("probe",): continue
        try:
            spec = importlib.util.spec_from_file_location(f"maxq_adapter_{p.stem}", p)
            mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
            name = mod.NAME
            if name in ADAPTERS and getattr(ADAPTERS[name], "_plugin", None) != p.name:
                raise RuntimeError(f"adapter name {name!r} already defined")
        except Exception as e:
            PLUGIN_ERRORS[p.stem] = f"{type(e).__name__}: {e}"
            continue
        def _list(t, smoke=False, _m=mod):
            missing = [k for k in getattr(_m, "REQUIRED", []) if not t.get(k)]
            if missing: raise ValueError(f"targets.json entry missing {missing} for adapter {_m.NAME}")
            return _m.list_jobs(t, h, smoke=smoke)
        _list._plugin = p.name
        ADAPTERS[name] = _list
        if callable(getattr(mod, "detail", None)):
            DETAIL[name] = lambda t, row, _m=mod: _m.detail(t, row, h)
        if callable(getattr(mod, "lookup", None)):
            LOOKUP[name] = lambda t, jid, _m=mod: _m.lookup(t, jid, h)
        if getattr(mod, "ENUMERABLE", False): ENUMERABLE_ATS.add(name)
        loaded.append(name)
    return loaded

load_plugins()

# ----------------------------------------------------------------------------- gates
@functools.lru_cache(maxsize=None)
def _word_re(term):
    return re.compile(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])")

def _has_word(s, term):
    """Word-boundary containment. Plain 'in' would make 'india' match 'Indianapolis, IN', and
    'india' as a substring already matches 'Indiana'. Both are real strings off these boards.
    The pattern is compiled once per term: a full gate makes tens of millions of these calls."""
    return _word_re(term).search(s) is not None

_LOC_SPLIT = re.compile(r"[|;\n]|\s/\s|\s+or\s+")

def _loc_segments(original):
    """One place a location string is cut into places. | ; and newline always; a SPACED slash and a
    lowercase " or " too, because boards write "Jersey City, NJ / Boulder, CO" and
    "San Francisco, CA or Remote, US", and a blocked-market half would otherwise sink the whole
    string. Never commas ('San Francisco, CA' must stay one segment), never an unspaced slash
    ("Hybrid/Remote"), and "Portland, OR" is uppercase so it is not the word "or"."""
    return [s for s in _LOC_SPLIT.split(original or "") if s and s.strip()]

_COUNTRY_PREFIX = re.compile(r"\s*([A-Z]{2}):\s")

def _has_us_marker(seg, g):
    # Some boards lead each office with its country code ("AU: Melbourne", "CA: Toronto"). The
    # prefix names the country, so the rest of the segment never gets a vote: a foreign city that
    # shares a US city's name, or a code that doubles as a state abbreviation (CA, DE, IN), would
    # otherwise read as American.
    m = _COUNTRY_PREFIX.match(seg)
    if m and m.group(1) != "US": return False
    s = seg.lower()
    return bool(any(_has_word(s, q) for q in g.get("us_qualifiers", []))
                or any(_has_word(s, st) for st in g.get("us_states_full", []))
                or any(_has_word(s, c) for c in g.get("us_cities", []))
                or (set(re.findall(r"(?<![A-Za-z])[A-Z]{2}(?![A-Za-z])", seg)) & set(g.get("us_state_codes", []))))

def remote_beside_blocked_us(original, g):
    """True for "San Francisco, CA | Remote": a bare remote segment (it names no place at all) next to a
    segment that is blocked but American. The Ashby adapter appends a bare "Remote" when the board
    sets isRemote, so this shape is common. The blocked American segment cannot pass and the bare
    "Remote" carries no US marker, so without this the req fails closed while "Dallas, TX | Remote"
    passes. A blocked FOREIGN segment lends nothing: "Toronto | Remote" still fails."""
    segs = _loc_segments(original)
    markers = g.get("remote_markers", ["remote"])
    def bare_remote(seg):
        s = seg.lower()
        if not any(m in s for m in markers): return False
        for m in sorted(markers, key=len, reverse=True): s = s.replace(m, " ")
        return not re.sub(r"[^a-z]+", "", s)
    # location_fail_any mixes blocked US markets with foreign countries, and many rows say just
    # "San Francisco" with no state, so gates.json names the blocked AMERICAN markets outright.
    us_blocked = g.get("blocked_us_markets", [])
    def blocked_and_american(seg):
        s = seg.lower()
        hits = [b for b in g["location_fail_any"] if _has_word(s, b)]
        return bool(hits) and all(b in us_blocked for b in hits)
    return any(blocked_and_american(seg) for seg in segs) and any(bare_remote(seg) for seg in segs)

def unblocked_segments(original, g):
    """Segments that carry no blocked marker, whether or not they name a US location. 'Remote'
    on its own is one of these: it says nothing about country, so it never satisfies the US
    check, but it is exactly what makes 'USA | Remote' a remote role under the location policy."""
    out = []
    for seg in _loc_segments(original):
        s = seg.lower()
        if any(_has_word(s, b) for b in g["location_fail_any"]): continue
        out.append(seg)
    return out

def us_segments(original, g):
    """Segments that are neither blocked nor missing a US marker.

    Split on | ; and newline only, never on commas: 'San Francisco, CA' has to stay one segment
    so a block on 'san francisco' still bites, while 'SF, CA | Denver, CO' still qualifies on Denver.
    Two-letter state codes are matched UPPERCASE against the original string, because these
    boards write country codes lowercase ('Munich, de', 'Hyderabad, in') and DE/IN are also
    Delaware and Indiana.
    """
    out = []
    for seg in _loc_segments(original):
        if any(_has_word(seg.lower(), b) for b in g["location_fail_any"]):
            continue                                   # blocked segment, try the next one
        if _has_us_marker(seg, g):
            out.append(seg)
    return out

def us_location_ok(original, g):
    """Fail-closed US location gate. A req passes only on a positive US marker."""
    if not original or not original.strip():
        return False, "no location stated"
    if us_segments(original, g): return True, ""
    if remote_beside_blocked_us(original, g): return True, ""
    return False, "location gate (fail-closed, no US marker): " + original[:90]

def bare_remote_only(original, g):
    """True when every segment is a remote marker plus, at most, a region that may include the US
    ("Remote", "Remote | Remote", "Remote-AMER", "Remote (North America)", "Distributed"). No segment
    names a place, so the list cannot say which country; the JD body has to."""
    segs = _loc_segments(original)
    if not segs: return False
    markers = sorted(g.get("remote_markers", ["remote"]) + (g.get("bare_remote") or {}).get("region_tokens", []),
                     key=len, reverse=True)
    for seg in segs:
        s = seg.lower()
        if not any(m in s for m in g.get("remote_markers", ["remote"])): return False
        for m in markers: s = s.replace(m, " ")
        if re.sub(r"[^a-z]+", "", s): return False
    return True

_USD_BAND = re.compile(r"\$\s?\d{2,3}(?:,\d{3}|\.\d+)?\s?k?\s*(?:-|–|—|to)\s*\$?\s?\d{2,3}(?:,\d{3}|\.\d+)?\s?k?", re.I)

def bare_remote_us_evidence(row, g):
    """What in the JD body says a bare-remote req is open in the US, or None.

    A req listed as plain "Remote" fails the fail-closed gate on the list text alone, but the
    body usually says where: "remote
    within the United States", a state list, or a USD pay band (state pay-transparency law). A body
    that names a foreign country and no US marker still fails; so does no body at all."""
    if not (g.get("bare_remote") or {}).get("body_check"): return None
    desc = row.get("description") or ""
    if not desc.strip() or not bare_remote_only(row.get("location") or "", g): return None
    low = desc.lower()
    for q in (g.get("bare_remote") or {}).get("us_phrases", []):
        if _has_word(low, q): return f'"{q}"'
    st = next((x for x in g.get("us_states_full", []) if _has_word(low, x) and x != "washington"), None)
    if st: return f"a US state ({st.title()})"
    m = _USD_BAND.search(desc)
    if m: return f"a USD pay band ({m.group(0).strip()[:30]})"
    return None

# Words a remote segment can carry that name no place: "Remote-Friendly (Travel-Required)", "Remote/Hybrid".
REMOTE_QUALIFIER_WORDS = ("friendly", "travel", "required", "hybrid", "optional", "eligible", "flexible",
                          "possible", "preferred", "work", "from", "home", "based", "in", "the", "or", "and")

def remote_names_foreign_place(seg, g):
    """True for "Remote - Colombia": a remote segment that names a place with no US marker. It is remote
    work in that country, so it cannot endorse the req under the location policy (country-scoped
    remote outside the US fails): "Remote - Colombia | New York" must not pass as remote on
    Colombia's word. A bare "Remote", a region ("Remote - AMER") or a US place ("Remote - US")
    still endorses."""
    if _has_us_marker(seg, g): return False
    s = seg.lower()
    for m in sorted(g.get("remote_markers", ["remote"]) + (g.get("bare_remote") or {}).get("region_tokens", []),
                    key=len, reverse=True):
        s = s.replace(m, " ")
    words = re.findall(r"[a-z]+", s)
    return any(w not in REMOTE_QUALIFIER_WORDS for w in words)

def location_policy_ok(original, g):
    """The location policy layered on top of the US check: remote and the markets in gates.json
    endorsed_onsite_markets pass; every other onsite market needs approval first.

    Only segments that already cleared the US check are considered, so a blocked 'Remote -
    Canada' segment can never endorse a US-onsite req on the strength of the word 'remote'.
    Remote alongside any city still passes, which is the common 'SF | NYC | Remote' shape.
    """
    endorsed = g.get("endorsed_onsite_markets", [])
    remote_markers = g.get("remote_markers", ["remote"])
    # Remote is read across every unblocked segment, because boards write it as its own
    # bare segment ('USA | Remote'). A blocked segment is still excluded, so 'Remote, Canada;
    # New York, NY' cannot endorse a NYC-only req on Canada's remote.
    for seg in unblocked_segments(original, g):
        if any(m in seg.lower() for m in remote_markers) and not remote_names_foreign_place(seg, g):
            return True, "remote"
    # Endorsed onsite markets must come from a segment that actually cleared the US check.
    # A whole state can be endorsed by its UPPERCASE code (endorsed_state_codes), read the way the US
    # check reads codes. Boards also write Canada as "CA" ('CA, BC, Vancouver'), so a segment with a
    # Canadian province code, the word canada, or an endorsed foreign market never endorses on a code.
    # endorsed_exact_segments matches a whole segment: a bare "New York" is the city, while the word
    # would also match "Tarrytown, New York", the state.
    exact = set(g.get("endorsed_exact_segments", []))
    codes = set(g.get("endorsed_state_codes", []))
    not_codes = set(g.get("endorsed_state_code_not_if_codes", []))
    for seg in us_segments(original, g):
        if any(_has_word(seg.lower(), c) for c in endorsed): return True, "market"
        if re.sub(r"\s+", " ", seg.strip().lower()) in exact: return True, "market"
        seg_codes = set(re.findall(r"(?<![A-Za-z])[A-Z]{2}(?![A-Za-z])", seg))
        if (codes & seg_codes and not (not_codes & seg_codes) and not _has_word(seg.lower(), "canada")
                and not endorsed_foreign_market(seg, g)):
            return True, "market"
    # A segment naming only the country says nothing about onsite, so the location policy cannot
    # judge it. It passes as "bare country" and _gate_core flags it for verification.
    if g.get("bare_country_passes") and any(_bare_country(seg, g) for seg in us_segments(original, g)):
        return True, "bare country"
    return False, ("location policy: unendorsed onsite market, needs approval: " + (original or "")[:90])

def _bare_country(seg, g):
    """True when a segment is only the country ('United States', 'US', 'United States, Multiple
    Locations'). 'US FL JAX 347' and 'US, VA, Arlington' name a place and are not bare."""
    s = seg.lower()
    for t in sorted(g.get("bare_country_tokens", []), key=len, reverse=True):
        s = re.sub(r"(?<![a-z])" + re.escape(t) + r"(?![a-z])", " ", s)
    return not re.sub(r"[\W_]+", "", s)

def endorsed_foreign_market(original, g):
    """The flag for the first segment naming an endorsed market outside the US, else None.

    An endorsed foreign city and its country can also sit in location_fail_any, so this runs on
    raw segments, ahead of the fail list. A segment
    carrying a US marker (a same-named US city: 'Vancouver, WA', 'US, WA, Vancouver') is excluded."""
    for m in g.get("endorsed_foreign_markets", []):
        for seg in _loc_segments(original):
            s = seg.lower()
            if not any(_has_word(s, c) for c in m["cities"]): continue
            if any(_has_word(s, x) for x in m.get("not_if_any", [])): continue
            if set(re.findall(r"(?<![A-Za-z])[A-Z]{2}(?![A-Za-z])", seg)) & set(m.get("not_if_codes", [])): continue
            return m["flag"]
    return None

# Year figures that are not an experience bar, e.g. "10 years to exercise options".
NON_EXPERIENCE_YEARS = ("exercise", "option", "vest", "equity", "warranty", " ago", " old", "history",
                        "anniversary", "in business", "founded", "track record of growth", "retention",
                        "post-termination", "runway", "contract term",
                        # "at least 18 years of age", "15 years in a row"
                        "of age", "years or older", "in a row", "consecutive")

# Company history, not a bar: "for over 20 years, smartsheet has", "for more than 20 years, payscale
# has", "in the past 10 years we grew". Only the words BEFORE the figure tell these apart from "more
# than 10 years in product management", and a tail that says "experience" always keeps the bar.
_COMPANY_HISTORY_HEAD = re.compile(r"(?:\bfor (?:over|more than|nearly|almost|the past|the last)|"
                                   r"\b(?:in|over) the (?:past|last))\s+$")

# Team seniority, not a bar: "our teams average 15+ years of experience" describes the people already
# there. Read as a bar it would stamp a 15+ tenure fail on a role whose required line is far lower.
_TEAM_AVERAGE_HEAD = re.compile(r"\b(?:teams?|engineers|people|staff|consultants|employees)\s+"
                                r"(?:average|averages|averaging|with an average of)\s+$")

# "in business" is there for the company-age idiom ("40 years in business"), but it also matches
# the FIELD NAME in "10+ years of progressive experience in Business Systems Analysis", which would
# make a real bar read as no bar at all. Keep the bar when "in business" is heading a field name.
# Wording that makes a stated years bar soft, so a req over years_hard_fail_over stays PASS with a
# REACH label instead of being stamped TENURE.
ESCAPE_NOT_EQUIVALENCE = re.compile(r"do(?: not|n['’]t) meet every|correlate with")   # the clauses that are not "or equivalent"
ESCAPE_CLAUSES = re.compile(r"do(?: not|n['’]t) meet every|correlate with|"
                            r"or (?:an? )?equivalent(?: [a-z-]+){0,3}? (?:experience|combination)|"
                            r"equivalent combination")

# DELIBERATE LANE EXCEPTION: not a typo, and not an escape clause.
# Business-systems postings inflate years harder than any other category: a "Senior Business
# Systems Analyst" asking 20+ years is not a serious number. So a JD that names the lane is never
# hard-gated on tenure; it stays PASS and carries a REACH label that says plainly that the JD offers
# no escape clause. It is kept apart from ESCAPE_CLAUSES so the label stays honest.
LANE_TENURE_EXCEPTION = "business systems"

_IN_BUSINESS_FIELD = re.compile(
    r"in business\s+(systems|process(?:es)?|program|operations|analysis|analytics|administration|"
    r"intelligence|technology|applications|application|development|transformation|management|partner)")

def _same_sentence_context(desc, m, after=60, before=40):
    """Text around a match, cut at sentence and line breaks. A plain 60-character window lets the
    NEXT sentence veto a real bar: in '3+ years of product management. You will have 10 years to
    exercise options.' the 3 would drop because 'exercise' falls inside the window."""
    tail = re.split(r"[.;\n]\s", desc[m.end(): m.end() + after], maxsplit=1)[0]
    head = re.split(r"[.;\n]\s", desc[max(0, m.start() - before): m.start()])[-1]
    return tail + " " + head

# Degree tiers. Taking the MAX figure misreads degree-tiered bars: "5 years experience with BS/BA;
# 3 years with MS/MA; 9 years experience with a high school diploma" would gate at 9+, and
# "6–10+ years of experience" at 10+. Rule: where the JD tiers by degree, the bar is the Bachelor's
# tier; the Master's/PhD, Associate's, high-school and in-lieu-of-degree tiers do not apply; a
# range's bar is its low end. Order matters: "in lieu of a BS/BA degree" names a bachelor's but is
# the no-degree tier.
_DEGREE_TIERS = (
    ("none", r"in lieu|without a degree|no degree"),
    ("hs", r"high school|\bhs\b|\bged\b|secondary school"),
    ("assoc", r"associate[’']?s?\b(?! director| manager| product)|\bas/aa\b|\baa/as\b"),
    ("master", r"(?<!scrum )master(?:[’']?s\b| of| degree)|\bms/ma\b|\bma/ms\b|\bm\.s\.|\bmba\b"),
    ("phd", r"ph\.?\s?d|doctorate|doctoral"),
    ("bachelor", r"bachelor|\bbs/ba\b|\bba/bs\b|\bb\.s\.|\bb\.a\.|\bbs\b|\bba degree"),
)

def _degree_tier(text):
    for tier, pat in _DEGREE_TIERS:
        if re.search(pat, text): return tier
    return None

def _tier_of_match(desc, m):
    """Degree tier of the alternative a year figure belongs to, or None. The alternative is the
    stretch between ';', '. ', newline, ',' and ' or ' around the figure; a figure-less alternative
    just before it in the same sentence ("Bachelor's Degree in Computer Science OR related field AND
    6+ years") lends its tier."""
    s0 = max(desc.rfind(c, 0, m.start()) for c in (";", "\n", ". "))
    e_cands = [i for i in (desc.find(c, m.end()) for c in (";", "\n", ". ")) if i != -1]
    sentence = desc[s0 + 1: min(e_cands) if e_cands else len(desc)]
    rel = m.start() - (s0 + 1)
    parts, pos = [], 0
    for piece in re.split(r"(,\s*|\s+or\s+)", sentence):
        parts.append((pos, piece)); pos += len(piece)
    alts = [(p, t) for p, t in parts if not re.fullmatch(r",\s*|\s+or\s+", t)]
    idx = max(i for i, (p, _) in enumerate(alts) if p <= rel) if alts else None
    if idx is None: return None
    tier = _degree_tier(alts[idx][1])
    if tier is None and idx > 0 and not re.search(r"\d", alts[idx - 1][1]):
        tier = _degree_tier(alts[idx - 1][1])
    return tier

def _bar_figures(desc, g):
    """(figure, match) for every year figure that plausibly states the JD's experience bar."""
    found = []
    for m in re.finditer(g["years_regex"], desc):
        n = int(m.group(1))
        if n > 30: continue
        ctx = _same_sentence_context(desc, m)
        sup = [w for w in NON_EXPERIENCE_YEARS if w in ctx]
        if sup == ["in business"] and _IN_BUSINESS_FIELD.search(ctx): sup = []
        if sup: continue
        if _COMPANY_HISTORY_HEAD.search(desc[max(0, m.start() - 24): m.start()]) \
                and "experience" not in _same_sentence_context(desc, m, before=0): continue
        if _TEAM_AVERAGE_HEAD.search(desc[max(0, m.start() - 40): m.start()]): continue
        # "6–10+ years", and the legal style "five (5) to seven (7) years"
        lo = re.search(r"(\d{1,2})\)?\s*(?:-|–|—|to)\s*(?:[a-z]+\s*\()?$", desc[max(0, m.start() - 16): m.start()])
        if lo and int(lo.group(1)) < n: n = int(lo.group(1))          # "6–10+ years": the low end
        found.append((n, m, _tier_of_match(desc, m)))
    tiers = {t for _, _, t in found if t}
    if "bachelor" in tiers and len(tiers) > 1:
        found = [f for f in found if f[2] in (None, "bachelor")]
    # The bar is the REQUIRED line. Microsoft writes "Required/minimum qualifications ... 5+ years"
    # then "Additional or preferred qualifications ... 8+ years", and the max over both would read
    # 8+ where the bar is 5. Only when the JD labels both sections, and never down to nothing.
    spans = _preferred_spans(desc)
    if spans:
        kept = [f for f in found if not any(a <= f[1].start() < b for a, b in spans)]
        if kept: found = kept
    return [(n, m) for n, m, _ in found]

_REQUIRED_HEAD = re.compile(r"(?:required|minimum|basic)(?:\s*/\s*minimum)?\s+qualifications|requirements\s*:|must[- ]haves?\s*:")
_PREFERRED_HEAD = re.compile(r"(?:preferred|additional or preferred|desired|bonus)\s+qualifications|nice[- ]to[- ]haves?\s*:|preferred\s*:")

def _preferred_spans(desc):
    """(start, end) character spans of preferred-qualification sections, each running to the next
    required heading or the end. Empty unless the JD also has a required heading."""
    if not _REQUIRED_HEAD.search(desc): return []
    spans = []
    for m in _PREFERRED_HEAD.finditer(desc):
        nxt = _REQUIRED_HEAD.search(desc, m.end())
        spans.append((m.start(), nxt.start() if nxt else len(desc)))
    return spans

def experience_years(desc, g):
    """Year figures from the JD that plausibly state an experience bar, skipping equity, company-age
    and similar figures that share the 'N years' shape, non-Bachelor's degree tiers, and the top of
    a range."""
    return [n for n, _ in _bar_figures(desc, g)]

# PASS only. LOCATION-POLICY and TENURE reqs would dominate the rescue candidates (most sit in
# markets not endorsed) and each costs a body fetch from the per-run budget.
RESCUE_OK = ("PASS",)

def rescue_verdict(row, g, v, reasons):
    """The out-of-lane decision. A title the lexicon does not recognise is not a terminal FAIL:
    the req still has to clear every other gate, and then its JD BODY casts the deciding vote
    through the same `prerank` lexicon the window hint uses.

    Why this tier exists: most reqs fail on title alone, before location or tenure are evaluated,
    and in-lane roles are often titled in some house dialect ('Order-to-Cash Transformation Advisor',
    'Special Projects, Close Automation'). Widening the title lexicon to cover those costs hundreds
    of false positives per real catch; reading the body costs one fetch and is precise.

    A rescued req is REVIEW, never PASS: it is a req a human should look at, not one the gates
    endorse. Verdicts that are not rescued keep the wording 'title out of lane' so the health
    funnel still counts the same bucket it always did."""
    cfg = g.get("rescue") or {}
    if not cfg.get("enabled"): return "FAIL", ["title out of lane"]
    # Title families excluded outright (gates.json rescue.title_exclude_any). Checked before
    # the body, so they never spend a fetch from the rescue budget.
    title = (row.get("title") or "").lower()
    excl = next((x for x in cfg.get("title_exclude_any") or [] if _has_word(title, x.strip())), None)
    if excl: return "FAIL", [f"title out of lane: rescue exclude: {excl}"]
    if not (row.get("description") or "").strip():
        return "RESCUE-FETCH", ["title out of lane: body not read yet"]
    s = prerank(row, g, None)
    if s is None: return "FAIL", ["title out of lane"]           # lexicon unusable; it already warned
    floor = int(cfg.get("min_body_score", 8))
    if s >= floor:
        note = f"RESCUED on body score {s} (floor {floor})" + ("" if v == "PASS" else f"; also {v}")
        return "REVIEW", [note] + list(reasons)
    return "FAIL", [f"title out of lane: body scored {s} (floor {floor})"]

MANAGER_REVIEW_OK = ("PASS", "LOCATION-POLICY", "TENURE")
MANAGER_BODY_PENDING = ": body not read yet"

def people_management_evidence(desc, cfg):
    """The phrase that shows a JD manages people, or None. A negated mention ("no direct reports",
    "without direct reports") is not evidence."""
    low = (desc or "").lower()
    for t in cfg.get("people_terms", []):
        for m in _word_re(t).finditer(low):
            if re.search(r"(?<![a-z])(?:no|without|not|zero)(?![a-z])[^.]{0,20}$", low[max(0, m.start() - 25):m.start()]):
                continue
            return re.sub(r"\s+", " ", low[max(0, m.start() - 30):m.end() + 30]).strip()
    return None

def manager_title_verdict(row, g, excl, v, reasons):
    """A people-manager title exclude that the JD body overrules. Body names people management: still
    excluded, with the evidence. Body silent on it: REVIEW (never PASS; a person reads it), or the
    LOCATION-POLICY / TENURE verdict the rest of the gate gave, with the note. No body yet: FAIL with
    MANAGER_BODY_PENDING, which earns the detail fetch in gate_all."""
    desc = row.get("description") or ""
    if not desc.strip():
        return "FAIL", [f"title excluded: {excl}{MANAGER_BODY_PENDING}"]
    hit = people_management_evidence(desc, g.get("manager_title_review") or {})
    if hit:
        return "FAIL", [f'title excluded: {excl} (the JD manages people: "{hit[:80]}")']
    note = f"MANAGER TITLE with no people management in the JD ({excl}): confirm it is an IC role"
    if v == "PASS": return "REVIEW", [note] + list(reasons)
    return v, list(reasons) + [note]

def _norm_title(t):
    """Lowercase, one ordinary space between words, so "Portfolio\\xa0Program\\xa0Manager" (no-break
    spaces) matches the lexicon like any other title."""
    return re.sub(r"\s+", " ", t or "").strip().lower()

def gate(row, g):
    """Returns (verdict, reasons). verdict in PASS / FAIL / TENURE / LOCATION-POLICY / REVIEW /
    RESCUE-FETCH. An out-of-lane title runs the whole gate anyway and is decided by rescue_verdict."""
    title = _norm_title(row["title"])
    # These two run BEFORE the include check, so they keep their own reasons: folding them into
    # "title out of lane" would silently empty the 'title excluded: ...' buckets the health funnel
    # counts. Everything the core gate checks after the include point stays worded "title out of lane".
    if row["company"].lower() in g["avoid_domain_companies"]: return "FAIL", ["avoid-domain company"]
    # Word boundaries, not substrings: 'intern' must not kill 'Internal Product Manager'.
    excl = next((x for x in g["title_exclude_any"] if _has_word(title, x.strip())), None)
    # Any GTM title is out, unless it names a platform in platform_title_exempt ("Senior NetSuite Developer, GTM").
    if not excl and not any(_has_word(title, p) for p in g.get("platform_title_exempt", [])):
        excl = next((x for x in g.get("title_exclude_gtm", []) if _has_word(title, x)), None)
    if excl and excl.strip() == "ai engineer" and any(x in title for x in g.get("title_lane_override", [])):
        # An "ai engineer" title that also carries a lane-override phrase goes to the BODY stage as
        # REVIEW, never PASS. The exclude runs before the override could see it; it is an engineering
        # role, so a person reads the JD before it counts.
        v, reasons = _gate_core(row, g)
        if v not in RESCUE_OK: return "FAIL", ["title excluded: ai engineer"]
        return rescue_verdict(row, g, v, reasons)
    if excl and excl.strip() in (g.get("manager_title_review") or {}).get("titles", []):
        # "Sr. Manager, Product Management" is an IC title at AMD, Amazon and Netflix
        # (one AMD JD says "without direct reporting authority"). The JD decides.
        v, reasons = _gate_core(row, g)
        if v not in MANAGER_REVIEW_OK: return "FAIL", ["title excluded: " + excl.strip()]
        return manager_title_verdict(row, g, excl.strip(), v, reasons)
    if excl: return "FAIL", ["title excluded: " + excl.strip()]
    # title_include_words are whole-word matches: a short abbreviation ("erp") as a substring is a
    # hazard waiting for a title to contain it; as a word it names the lane.
    in_lane = (any(x in title for x in g["title_include_any"])
               or any(_has_word(title, w) for w in g.get("title_include_words", [])))
    v, reasons = _gate_core(row, g)
    if in_lane: return v, reasons
    if v not in RESCUE_OK: return "FAIL", ["title out of lane"]
    return rescue_verdict(row, g, v, reasons)

def gate_final(row, g):
    """gate() for every caller that cannot fetch a body (--report-only, --window, --yield, --merge).
    RESCUE-FETCH is an instruction to gate_all, never a verdict: left as one it would be stored in
    the snapshot, counted by no line of the health funnel, and reported by --report-only as a
    changed verdict after every gate edit."""
    v, why = gate(row, g)
    return ("FAIL", why) if v == "RESCUE-FETCH" else (v, why)

def _gate_core(row, g):
    title = _norm_title(row["title"]); loc = row["location"].lower(); desc = row["description"].lower()
    reasons = []
    # Plurals too: 'Principal TPM, Data Centers' must not slip past 'data center'.
    noise = next((x for x in g.get("title_noise_exclude", []) if _has_word(title, x) or _has_word(title, x + "s")), None)
    if noise and not any(o in title + " " for o in g.get("title_noise_override", [])):
        return "FAIL", ["title excluded (physical-ops noise): " + noise]
    # An engineer/developer title only stays in lane when it also names product or program work
    # ("SWE, Finance Systems Program" yes; "Staff SWE, Developer Platform" or "NetSuite Developer" no).
    # title_lane_override exempts title families (solution management, for example) whose titles carry
    # an engineering noun but whose work is deployment and ownership. Without it every architect and
    # engineer among them dies here. Nothing else is relaxed; the excludes above have already run.
    # Whole words: as a substring "engineering" would let a department name kill product and
    # program titles ("Principal TPM, Enterprise Engineering", "Engineering Delivery Manager").
    # tpm/pmt/analyst/delivery manager are program and product work by name ("Sr. PMT, Developer Tools").
    # Business-systems engineering titles (engineering_title_review) run the rest of the gate and
    # come out REVIEW, never PASS.
    bizsys_eng = False
    if re.search(r"\b(?:engineer|developer|architect)s?\b", title) and not re.search(
            r"product|program|project|enablement|adoption|transformation|\btpm\b|\bpmt\b|analyst|delivery manager", title) \
            and not any(x in title for x in g.get("title_lane_override", [])):
        if not any(x in title for x in g.get("engineering_title_review", [])):
            return "FAIL", ["engineering title without product/program work"]
        # Excluded families end here too, or "Principal GTM Systems Architect" would reach REVIEW.
        excl = None if any(_has_word(title, p) for p in g.get("platform_title_exempt", [])) else next(
            (x for x in (g.get("rescue") or {}).get("title_exclude_any") or [] if _has_word(title, x.strip())), None)
        if excl: return "FAIL", [f"title out of lane: rescue exclude: {excl}"]
        bizsys_eng = True
    if g.get("location_mode") == "fail_closed":
        ok, why = us_location_ok(row["location"], g)
        abroad = endorsed_foreign_market(row["location"], g)
        bare_remote_ev = None if ok or abroad else bare_remote_us_evidence(row, g)
        if bare_remote_ev:
            ok, why = True, ""
            reasons.append("VERIFY REMOTE: the board says remote with no country; the JD names "
                           + bare_remote_ev + ". Confirm US eligibility before building")
        if not ok:
            if not abroad: return "FAIL", [why]
            reasons.append(abroad)                     # endorsed foreign market: pass, knockout flagged
        elif g.get("location_policy_layer"):
            ok, why = location_policy_ok(row["location"], g)
            # Listed in section 2, never built, and never counted as a hard FAIL: the market is
            # a preference that can be overridden, not a disqualification like a non-US location.
            if not ok:
                if not abroad: return "LOCATION-POLICY", [why]
                reasons.append(abroad)                 # 'Pleasanton | Toronto': the foreign option carries it
            if why == "bare country":
                reasons.append("VERIFY LOCATION: the board names only the country (" + row["location"][:60]
                               + "); confirm remote or an endorsed market before building")
                terms = onsite_terms(row["description"])
                if terms: reasons.append("ONSITE TERMS in the JD: " + terms)
            if why == "remote":
                # It passed on a remote marker. The board says remote; the JD may not agree.
                terms = onsite_terms(row["description"])
                if terms: reasons.append("ONSITE TERMS despite remote listing: " + terms)
                if not us_segments(row["location"], g) and not bare_remote_ev:
                    reasons.append("VERIFY REMOTE: the only named office is a blocked market (" + row["location"][:60]
                                   + "); the board also lists a bare Remote option")
    else:
        passes = any(x in loc for x in g["location_pass_any"]); fails = any(x in loc for x in g["location_fail_any"])
        if fails and not passes: return "FAIL", ["location gate: " + row["location"]]
        if not passes and not fails and loc: reasons.append("location unrecognized, check onsite terms: " + row["location"])
    if desc:
        nc = [x for x in g["never_claim_terms_in_requirements"] if x in desc]
        if nc: reasons.append("never-claim terms in JD: " + ", ".join(nc))
        yrs = experience_years(desc, g)
        if yrs:
            top = max(yrs)
            escape = bool(ESCAPE_CLAUSES.search(desc))
            if escape and any(row["company"].lower().startswith(c) for c in g.get("equivalence_not_years_companies", [])) \
                    and not ESCAPE_NOT_EQUIVALENCE.search(desc):
                # Some employers print "or equivalent experience" on every JD, where it substitutes for
                # the DEGREE, never the years; for them it must not soften a years bar (gates.json).
                escape = False
            lane = LANE_TENURE_EXCEPTION in desc
            if top > g["years_hard_fail_over"] and not (escape or lane):
                return "TENURE", [f"{top}+ years, no escape clause"] + reasons
            # A bar at or under the configured held years is cleared, so REACH starts at gates.json reach_years_from.
            if top >= g.get("reach_years_from", 6):
                note = (" with escape clause" if escape else
                        " (business-systems lane exception; the JD states no escape clause)" if lane else "")
                reasons.append(f"REACH: {top}+ years stated" + note)
    if "repost" in desc or "reposted" in title: reasons.append("marked reposted")
    if bizsys_eng:
        return "REVIEW", ["business-systems engineering title: REVIEW, never PASS (an engineering role; read the JD)"] + reasons
    return "PASS", reasons

# ----------------------------------------------------------------------------- posting risk
# Jurisdictions with pay-transparency laws. The comp extractor already recovers
# bands from JD bodies, so "posted in a place that legally requires a band, and
# still has none" is nearly free to detect and is a real signal about the poster.
PAY_TRANSPARENCY_MARKERS = ["colorado", ", co", "california", ", ca", "new york", ", ny", "nyc",
                            "washington", ", wa", "illinois", ", il", "maryland", ", md",
                            "hawaii", ", hi", "minnesota", ", mn", "vermont", ", vt",
                            "jersey city", "ithaca", "westchester"]
# A req that names no concrete tool or system is a req nobody specific wrote.
NAMED_TOOL_MARKERS = ["airflow", "api", "asana", "aws", "azure", "confluence", "coupa", "databricks", "dbt",
                      "docker", "excel", "gcp", "github", "gitlab", "jira", "kubernetes", "looker", "netsuite",
                      "oracle", "power bi", "python", "sap", "slack", "snowflake", "sql", "tableau",
                      "terraform", "workday", "zendesk"]

def risk_signals(row, ledger, prev_row=None, repost_of=None):
    """Posting-risk signals, reported individually and never gated on.

    Five signals, each cheap and each independently meaningful. The point is to
    show WHICH fired, not to collapse them into one opaque number.
    """
    out = []
    age = days_since(row.get("posted")) or days_since(ledger.get(row["key"]))
    if age is not None and age >= 30:
        out.append(f"live {age}d")
    if prev_row is not None:
        old, new = prev_row.get("posted"), row.get("posted")
        if old and new and new > old:
            out.append(f"reposted (date reset {old} -> {new})")
    if repost_of:
        out.append(f"reposted under a new id (was {repost_of.split(':')[-1]}, first seen {ledger.get(repost_of, '?')})")
    loc = (row.get("location") or "").lower()
    if not row.get("comp") and any(m in loc for m in PAY_TRANSPARENCY_MARKERS):
        out.append("no band where disclosure is required")
    desc = (row.get("description") or "").lower()
    if desc and not any(m in desc for m in NAMED_TOOL_MARKERS):
        out.append("generic requirements, no named tools")
    return out

# ----------------------------------------------------------------------------- conversion read
# Fit says whether the candidate could do the job. Conversion says whether the application reaches a
# human: the years gap against the stated bar, a level-up title, days since posting, the
# board's applicant volume, and whether a known contact exists. Shown beside the score,
# never folded into it. Every signal is stated so the reader can see which one is the drag. Held
# years come from gates.json candidate_years_total.
CONTACTS = DATA / "contacts.json"

def stated_years_bar(desc, g):
    """The years bar that decides the knockout: the smallest figure inside a REQUIRED /
    MINIMUM / BASIC block when one exists, else the largest figure anywhere. gate() takes the
    max, which at Microsoft picks up the PREFERRED bar (8+/12+/14+) when the required line
    says 4+. The gate is left as is so verdicts do not move; this is the
    Conversion read's number."""
    req, bach, anywhere = [], [], []
    for n, m in _bar_figures(desc, g):       # degree tiers and ranges read the same as the gate
        ctx = desc[max(0, m.start() - 400): m.start()]
        cut = max(ctx.rfind("preferred"), ctx.rfind("nice to have"), ctx.rfind("bonus"), ctx.rfind("additional qualifications"))
        head = max(ctx.rfind("required"), ctx.rfind("minimum"), ctx.rfind("basic qualifications"), ctx.rfind("must have"))
        anywhere.append(n)
        if head > cut:
            req.append(n)
            # Microsoft-style degree tiers: "Bachelor's AND 6+ ... OR Master's AND 4+ ... OR
            # Doctorate AND 3+". The figure attached to the Bachelor's clause is the bar.
            near = ctx[-120:]
            if "bachelor" in near and "master" not in near[near.rfind("bachelor"):] and "doctorate" not in near[near.rfind("bachelor"):]:
                bach.append(n)
    if bach: return min(bach), "required, Bachelor's tier"
    if req: return min(req), "required"
    if anywhere: return max(anywhere), "stated"
    return None, None

def conversion_signals(row, g, ledger=None, as_of=None, contacts=None):
    """The Conversion read as data. conversion_read() formats THIS and nothing else, so the
    printed read and a stored tracker feature can never drift apart.

    as_of dates the posting age against the day the application went out, not today.
    contacts overrides data/contacts.json, so a backfill can use who was known on submit day
    rather than today's file.
    """
    desc = (row.get("description") or "").lower()
    title = (row.get("title") or "").lower()
    held = float(g.get("candidate_years_total", 4.5))
    bar, kind = stated_years_bar(desc, g)
    gap = (bar - held) if bar is not None else None
    # Whole words, markers stripped, so "Product Lead", "Staff, Product Manager" and "Lead, AI
    # Enablement" match a marker written with a trailing space. "Chief of Staff" and "Member of Technical Staff" are roles, not the Staff level.
    t_lvl = re.sub(r"chief of staff|member of (?:the )?technical staff", " ", title)
    lvl = next((m.strip() for m in g.get("level_up_title_markers", []) if _has_word(t_lvl, m.strip())), None)
    age = days_between(row.get("posted"), as_of)
    age_source = "posted" if age is not None else None
    if age is None and ledger:
        age = days_between(ledger.get(row.get("key")), as_of)
        # first_seen is when the SWEEP first saw the req. On a board's first read that is a
        # coverage stamp, not a posting date, so a consumer that learns from this must know.
        if age is not None: age_source = "first_seen"
    co = (row.get("company") or "").lower()
    # Whole words, not substrings: as a substring "meta" matches "Metalworks Example", which would then
    # read as a flooded board and pick up that employer's contacts.
    flooded = any(_has_word(co, b) for b in g.get("flooded_boards", []))
    if contacts is None: contacts = load_json(CONTACTS, {})
    # Keys starting "_" are the file's own notes, never an employer. Without this an empty
    # company name matched "_comment" (its value is a string, and .get() on it raised).
    known = next((v for k, v in contacts.items()
                  if not k.startswith("_") and co and (_has_word(co, k.lower()) or _has_word(k.lower(), co))), None)
    people = [p.get("name", "?") for p in (known or {}).get("people", [])]
    drags = 0
    if gap is not None and gap > 0: drags += 1 if gap < 3 else 2
    if lvl: drags += 1
    if age is not None and age > 14: drags += 1
    if flooded: drags += 1
    if not people: drags += 1
    return {"years_bar": bar, "years_bar_kind": kind, "years_gap": (None if gap is None else round(gap, 1)),
            "level_up": lvl.strip() if lvl else "", "days_posted": age, "days_posted_source": age_source,
            "flooded_board": flooded,
            "known_contacts": people, "candidate_years": held, "drags": drags,
            "label": "HIGH" if drags <= 1 else ("MEDIUM" if drags <= 3 else "LOW")}

def conversion_read(row, g, ledger=None):
    """The five signals as the reader sees them. Formatting only; every value comes from
    conversion_signals()."""
    s = conversion_signals(row, g, ledger)
    held = s["candidate_years"]
    out = []
    if s["years_bar"] is None:
        out.append("no years bar stated")
    elif s["years_gap"] > 0:
        out.append(f"years gap +{s['years_gap']:.1f} ({s['years_bar']}+ {s['years_bar_kind']} vs {held:g} held)")
    else:
        out.append(f"years bar {s['years_bar']}+ ({s['years_bar_kind']}) cleared")
    if s["level_up"]:
        out.append(f"level-up title ({s['level_up']})")
    if s["days_posted"] is not None:
        # -1 is date-line skew (see days_since), displayed as 0; the stored signal keeps its value.
        out.append(f"posted {0 if s['days_posted'] == -1 else s['days_posted']}d ago"
                   +(" (past 14 days: a drag)" if s["days_posted"] > 14 else ""))
    if s["flooded_board"]:
        out.append("flooded board (hundreds of applicants per posting)")
    if s["known_contacts"]:
        out.append("known contact(s): " + ", ".join(s["known_contacts"]))
    else:
        out.append("no known contact")
    return s["label"], out

# ----------------------------------------------------------------------------- pre-rank
# The unscored pile outnumbers the scored reqs several to one, and most scores land under 70, so most
# scoring effort goes to reqs that were never going to be built. This is a lane hint to sort that pile
# by: a SORT ORDER, never a gate and never a filter. `--prerank-check` measures how well it agrees
# with whatever scored.json holds today.
def jd_stem(key):
    """The file stem write_report stores a req's JD under. The one spelling; the index and every
    lookup go through it."""
    return re.sub(r"[^a-z0-9]+", "_", (key or "").lower())

def jd_index():
    """jd_stem(key) -> stored JD file. Newest dated folder wins. The snapshot keeps the JD body for
    only a small fraction of rows, so the stored file is the real source for a body.

    Keyed on the WHOLE stem: ids with a hyphen, an underscore or a capital ("R-107452", Ashby and
    Lever UUIDs) must match, and two employers sharing an ats and an id suffix must not collide."""
    idx = {}
    for p in sorted(JDS.glob("*/*.md")):
        idx[p.stem.lower()] = p
    return idx

def jd_text_for(key, idx):
    p = idx.get(jd_stem(key)) if key else None
    if not p: return None
    try: return p.read_text(encoding="utf-8", errors="replace")
    except OSError: return None

def prerank(row, g, jd_text=None):
    """How much this req looks like the target lane described by the lexicon. Higher is more like it.
    Returns None when the lexicon is not configured."""
    cfg = g.get("prerank") or {}
    if not cfg: return None
    title = (row.get("title") or "").lower()
    # A stored JD opens "# <title>"; use it when the req has left the snapshot and the row has none.
    if not title and jd_text and jd_text.startswith("# "): title = jd_text.split("\n", 1)[0][2:].lower()
    body = (jd_text if jd_text is not None else (row.get("description") or "")).lower()
    cap = int(cfg.get("body_cap", 3))
    try:
        t = sum(w for pat, w in cfg.get("title_positive", []) + cfg.get("title_negative", []) if re.search(pat, title))
        b = 0
        for pat, w in cfg.get("body_positive", []) + cfg.get("body_negative", []):
            n = len(re.findall(pat, body))
            if n: b += w * min(cap, n)
    except (re.error, TypeError, ValueError) as e:
        # The hint is config, edited by hand. A typo there must cost the hint, never --window.
        if not getattr(prerank, "_warned", False):
            print(f"WARNING: gates.json `prerank` is unusable ({e}); no lane hint this run. Fix it and run --prerank-check.")
            prerank._warned = True
        return None
    return int(cfg.get("title_weight", 2)) * t + b

def _spearman(xs, ys):
    """Rank correlation, ties averaged. No scipy."""
    def rank(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v); i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]: j += 1
            for k in range(i, j + 1): r[order[k]] = (i + j) / 2 + 1
            i = j + 1
        return r
    if len(xs) < 3: return 0.0
    rx, ry = rank(xs), rank(ys); n = len(xs)
    mx, my = sum(rx) / n, sum(ry) / n
    den = (sum((p - mx) ** 2 for p in rx) * sum((q - my) ** 2 for q in ry)) ** 0.5
    return (sum((p - mx) * (q - my) for p, q in zip(rx, ry)) / den) if den else 0.0

def prerank_check(g, verbose=True):
    """Recompute the hint's validation against today's scored.json. Read-only; exit 0 always."""
    snap = load_json(DATA / "latest.json", None)
    if not snap: sys.exit("no data/latest.json; run a sweep first")
    scored, idx, rows = load_scored(), jd_index(), snap["rows"]
    pairs = []
    for key, rec in scored.items():
        s = rec.get("score")
        if not isinstance(s, (int, float)): continue
        body = jd_text_for(key, idx)
        row = rows.get(key)
        if body is None and not (row or {}).get("description"): continue
        pairs.append((key, s, prerank(row or {"title": "", "description": ""}, g, body)))
    pairs = [p for p in pairs if p[2] is not None]        # an unusable lexicon yields no hint at all
    if len(pairs) < 10:
        print(f"prerank check: only {len(pairs)} scored req(s) still have a readable JD; not enough to validate.")
        return 0.0
    sims = [p[2] for p in pairs]; scores = [p[1] for p in pairs]
    rho = _spearman(sims, scores)
    order = sorted(range(len(pairs)), key=lambda i: -sims[i])
    pos = {i: k for k, i in enumerate(order)}
    half = len(pairs) // 2
    hi = [i for i, s in enumerate(scores) if s >= 80]
    lo = [i for i, s in enumerate(scores) if s < 60]
    top_half = sum(1 for i in hi if pos[i] < half) / len(hi) if hi else 0.0
    bot_half = sum(1 for i in lo if pos[i] >= half) / len(lo) if lo else 0.0
    if verbose:
        print(f"PRERANK CHECK against {len(pairs)} scored req(s) with a readable JD "
              f"({len(hi)} at 80+, {len(lo)} under 60)\n")
        print(f"  Spearman rho vs Fit          {rho:+.3f}")
        print(f"  80+ reqs in the top half     {top_half:.0%}")
        print(f"  under-60 reqs in the bottom  {bot_half:.0%}\n")
        if rho < 0.35 or top_half < 0.80:
            print("  WARNING: THE HINT NO LONGER SORTS THE WAY IT WAS VALIDATED. "
                  "Re-tune gates.json `prerank` or stop relying on the order.\n")
        buried = [i for i in hi if pos[i] >= half]
        print(f"  80+ reqs the hint would bury in the bottom half: {len(buried)}")
        for i in sorted(buried, key=lambda i: -scores[i]):
            print(f"    score {scores[i]:>3}  pre {sims[i]:>4}  {pairs[i][0]}")
        print("\n  A sort order, not a verdict: it never gates, never filters and never shows on a scored req.")
    return rho

# ----------------------------------------------------------------------------- score ledger
APPLICATIONS = DATA / "applications.json"

# Query parameters that say where a click came from, never which req it is. Everything else stays:
# some boards' whole req identity is a query parameter (?gh_jid=7206060434).
TRACKING_PARAMS = {"gh_src", "source", "src", "ref", "referrer", "lever-source", "lever-origin", "trk",
                   "trackingid", "refid", "fbclid", "gclid", "iis", "iisn"}
HOST_ALIASES = {"boards.greenhouse.io": "job-boards.greenhouse.io"}

def canon_url(url):
    """One spelling per req url, shared with tracker.py. Without it ?gh_src=, http://, a capitalised
    host or boards.greenhouse.io for job-boards.greenhouse.io each read as a DIFFERENT req."""
    from urllib.parse import urlsplit, parse_qsl, urlencode
    u = (url or "").strip()
    if not u: return ""
    sp = urlsplit(u if "://" in u else "https://" + u)
    host = sp.netloc.lower()
    if host.startswith("www."): host = host[4:]
    host = HOST_ALIASES.get(host, host)
    q = [(k, v) for k, v in parse_qsl(sp.query, keep_blank_values=True)
         if k.lower() not in TRACKING_PARAMS and not k.lower().startswith("utm_")]
    # A fragment that is a path IS the req: careers.oracle.com/jobs/#en/sites/jobsearch/job/678530.
    # Dropping every fragment would collapse every Oracle req into one url. "#app" is an anchor.
    frag = "#" + sp.fragment.rstrip("/") if "/" in sp.fragment else ""
    return host + sp.path.rstrip("/") + ("?" + urlencode(sorted(q)) if q else "") + frag

def applied_index():
    """{ats key} and {url} for every application already on file, for the ALREADY APPLIED line.

    The unscored backlog is filtered on scored.json, which says nothing about applications, so
    `--window` consults this too. Both indexes are needed: older rows on file predate the key
    column and can only be matched on url."""
    try:
        apps = load_json(APPLICATIONS, [])
    except Exception:
        return {}, {}
    rows = apps if isinstance(apps, list) else list(apps.values())
    by_key, by_url = {}, {}
    for a in rows:
        if not isinstance(a, dict): continue
        if a.get("key"): by_key[a["key"]] = a
        if a.get("url"): by_url[canon_url(a["url"])] = a
    return by_key, by_url

def applied_row(k, r, by_key, by_url):
    """The application already on file for this req, or None. Key, then canonical url, then (for rows
    that predate the key column) the req id as a whole path segment of the stored url at the same
    employer: the tracker may hold amazon.jobs/en/jobs/<id> while the board now serves
    .../<id>/<title-slug>."""
    hit = by_key.get(k) or by_url.get(canon_url(r.get("url")))
    if hit: return hit
    rid = k.split(":")[-1].lower()
    if len(rid) < 5: return None
    co = set(re.sub(r"[^a-z0-9]+", " ", (r.get("company") or "").lower()).split())
    for cu, a in by_url.items():
        if a.get("key") or rid not in cu.lower().replace("?", "/").replace("=", "/").replace("&", "/").split("/"): continue
        ac = set(re.sub(r"[^a-z0-9]+", " ", (a.get("company") or "").lower()).split())
        if co and ac and (co <= ac or ac <= co): return a
    return None

def load_scored():
    return load_json(SCORED, {})

def save_scored(d):
    write_json_atomic(SCORED, d, indent=1, sort_keys=True)

CONVERSION_LABELS = ("HIGH", "MEDIUM", "LOW")

def parse_set_score(arg):
    """KEY=SCORE:VERDICT[:built|:unbuilt][:conv=HIGH|MEDIUM|LOW] -> (key, score, verdict, built, conversion).

    Suffixes are read from the RIGHT, so a colon inside the verdict survives ("Apply: 8+ with a
    clause" stays whole). A conv= that is not its own colon-separated suffix ("Apply=conv=LOW")
    raises ValueError rather than being glued into the verdict.
    built is None unless a suffix says so: a re-score carrying only :conv= must not un-build a row.
    An empty verdict returns None, which set_score reads as "keep the stored one"."""
    key, sep, rest = arg.partition("=")
    key = key.strip()
    if not sep or not key:
        raise ValueError("expected KEY=SCORE:VERDICT[:built][:conv=HIGH|MEDIUM|LOW]")
    score_s, _, tail = rest.partition(":")
    try:
        score = int(score_s.strip())
    except ValueError:
        raise ValueError(f"score {score_s.strip()!r} is not a whole number")
    if not 0 <= score <= 100:
        raise ValueError(f"score {score} is outside 0 to 100")
    tokens = tail.split(":") if tail else []
    built = conversion = None
    while tokens:
        tok = tokens[-1].strip().lower()
        if tok in ("built", "unbuilt") and built is None:
            built = tok == "built"
        elif tok.startswith("conv=") and conversion is None:
            conversion = tok[5:].strip().upper()
            if conversion not in CONVERSION_LABELS:
                raise ValueError(f"conv={tok[5:].strip()!r} is not one of {'|'.join(CONVERSION_LABELS)}")
        else:
            break
        tokens.pop()
    verdict = ":".join(tokens).strip()
    if re.search(r"\bconv\s*=", verdict, re.I):
        raise ValueError(f"conv= is inside the verdict text ({verdict!r}): it must be its own suffix, "
                         "colon-separated and last, e.g. KEY=80:Apply:conv=LOW")
    if re.search(r"=\s*(un)?built\s*$", verdict, re.I):
        raise ValueError(f"the built flag is inside the verdict text ({verdict!r}): write it as :built")
    return key, score, verdict or None, built, conversion

def cmd_set_score(entries, g):
    """--set-score, one or more entries. Every entry is parsed before any is written, so one
    malformed entry writes nothing. With a single-valued flag, only the last of several --set-score
    flags was stored and the rest vanished without a word. Exits with a message on a rejected run."""
    try:
        parsed = [parse_set_score(x) for x in entries]
    except ValueError as ex:
        sys.exit(f"--set-score REJECTED, nothing written: {ex}")
    keys = [p[0] for p in parsed]
    dupes = sorted({k for k in keys if keys.count(k) > 1})
    if dupes:
        sys.exit(f"--set-score REJECTED, nothing written: key given twice: {', '.join(dupes)}")
    # All or nothing. Saving once per entry meant an entry that failed validation (an empty verdict
    # on an unscored key) left the entries before it written. Every entry is now checked, then
    # applied to one in-memory copy, and the file is written once, only if all of them held.
    scored_before = load_scored()
    d = json.loads(json.dumps(scored_before))
    bad = [f"{key} has no stored verdict to keep; give one"
           for key, _s, verdict, _b, _c in parsed if verdict is None and "verdict" not in scored_before.get(key, {})]
    if bad:
        sys.exit("--set-score REJECTED, nothing written: " + "; ".join(bad))
    rows = load_json(DATA / "latest.json", {}).get("rows") or {}
    seen = load_json(DATA / "seen.json", {})
    written = []
    try:
        for key, score, verdict, built_flag, conv in parsed:
            jdh = (rows.get(key) or {}).get("jd_hash")
            written.append((key, apply_score(d, key, score, verdict, built=built_flag, conversion=conv,
                                             jdh=jdh if jdh and jdh != jd_hash("") else None)))
    except ValueError as ex:
        sys.exit(f"--set-score REJECTED, nothing written: {ex}")
    save_scored(d)
    for key, e in written:
        snap_row = rows.get(key) or {}
        print(f"scored.json: {key} -> {e}")
        if key not in scored_before and key not in seen:
            print(f"WARNING: {key} is not a req the sweep has ever seen. Fine for a pasted JD off a board "
                  "that is not swept; otherwise check the key for a typo, because --window will never match it.")
        # A hand-typed Conversion label is stored as given (a referral in hand is a real override the
        # sweep cannot see), but a label that disagrees with the computed signals is named.
        if snap_row:
            sig = conversion_signals(dict(snap_row, key=key), g, seen)
            if conv and conv != sig["label"]:
                print(f"WARNING: {key} conv={conv} but the sweep computes {sig['label']} ({sig['drags']} drag(s)). "
                      "Keep it only if something the sweep cannot see justifies it, and say what.")
            elif not conv and not (scored_before.get(key) or {}).get("conversion"):
                print(f"NOTE: {key} has no Conversion stored; the sweep computes {sig['label']} "
                      f"({sig['drags']} drag(s)). Add :conv={sig['label']} to store it.")

def set_score(key, score, verdict, built=None, jdh=None, conversion=None):
    """Record a score so a standing target is not re-litigated every day. verdict=None keeps the
    stored verdict (marking a row :built after delivery should not blank its reasons)."""
    d = load_scored()
    e = apply_score(d, key, score, verdict, built=built, jdh=jdh, conversion=conversion)
    save_scored(d)
    return e

def apply_score(d, key, score, verdict, built=None, jdh=None, conversion=None):
    """set_score's change, made to the scored dict d in memory; nothing is written. Raises ValueError
    (leaving d untouched) when verdict is None and nothing is stored to keep."""
    e = dict(d.get(key, {}))
    if verdict is None:
        if "verdict" not in e:
            raise ValueError(f"{key} has no stored verdict to keep; give one")
        verdict = e["verdict"]
    e.update({"score": int(score), "verdict": verdict, "scored_on": TODAY.isoformat()})
    if built is not None: e["built"] = bool(built)
    if jdh: e["jd_hash"] = jdh
    if conversion: e["conversion"] = conversion
    e.setdefault("built", False)
    d[key] = e
    return e

# ----------------------------------------------------------------------------- snapshot / diff
def load_json(p, default):
    # utf-8-sig, not utf-8: any Windows tool that touches these files (PowerShell's
    # Set-Content -Encoding utf8 on 5.1, Notepad) writes a BOM, and json.loads then
    # dies on the leading U+FEFF. utf-8-sig reads BOM and no-BOM files identically.
    p = Path(p)
    if not p.exists(): return default
    if p.name.endswith(".gz"):
        import gzip
        with gzip.open(p, "rt", encoding="utf-8-sig") as f:
            return json.load(f)
    return json.loads(p.read_text(encoding="utf-8-sig"))

def load_config(path):
    """A hand-edited config file (gates.json, a targets file). A missing file or a JSON syntax error
    stops the run with one line naming the file, line and column, exit code 2, instead of a traceback.
    utf-8-sig for the same BOM reason as load_json."""
    p = Path(path)
    try:
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        msg = f"{p}: file not found"
    except json.JSONDecodeError as e:
        msg = f"{p}: invalid JSON at line {e.lineno}, column {e.colno}: {e.msg}"
    except (OSError, UnicodeDecodeError) as e:
        msg = f"{p}: cannot read ({e})"
    print(f"config error: {msg}", file=sys.stderr)
    raise SystemExit(2)

def resolve_targets(arg):
    """--targets: a relative path is looked up from the current directory first, then the repo root,
    so `--targets my_targets.json` works from anywhere and the default still finds the repo's file."""
    p = Path(arg)
    if p.is_absolute(): return p
    here = Path.cwd() / p
    return here if here.exists() else ROOT / p

def load_targets(arg):
    d = load_config(resolve_targets(arg))
    if not isinstance(d, dict) or not isinstance(d.get("targets"), list):
        print(f"config error: {resolve_targets(arg)}: expected an object with a \"targets\" list", file=sys.stderr)
        raise SystemExit(2)
    return d["targets"]

def snapshot_dates():
    """Dates with a dated snapshot on disk, raw or gzipped, each counted once."""
    out = set()
    for p in list(DATA.glob("snapshot_*.json")) + list(DATA.glob("snapshot_*.json.gz")):
        out.add(p.name.replace("snapshot_", "").split(".json")[0])
    return sorted(out)

def snapshot_count():
    """How many full snapshots exist. Two is the threshold where a diff means anything."""
    return len(snapshot_dates())

def snapshot_path(date):
    """The dated snapshot for an ISO date, .json.gz preferred, or None. Read it with load_json()."""
    for name in (f"snapshot_{date}.json.gz", f"snapshot_{date}.json"):
        if (DATA / name).exists(): return DATA / name
    return None

# Disk and file-sync churn. A snapshot is on the order of 100 MB of JSON. No code reads a dated
# snapshot (every consumer reads latest.json; the dated files exist for audits and restores), so
# today's is written gzipped straight from latest.json and no raw dated snapshot is kept at all.
# latest.json stays raw: tracker.py and every sweep mode read it.
SNAPSHOT_RAW_DAYS = 0

def replace_with_retry(tmp, path, tries=6, first_wait=0.25):
    """os.replace that outlasts a reader. On Windows the replace fails with PermissionError [WinError 5]
    while ANY process holds the destination open without delete sharing: a concurrent --window,
    tracker.py, Defender or the indexer scanning a fresh latest.json, an editor on seen.json.
    It succeeds the moment the reader lets go, so one such reader must not kill a long sweep at its
    last step. About 16 seconds of backoff, then the temp file is removed and the error says how to
    recover."""
    wait = first_wait
    for attempt in range(tries):
        try:
            os.replace(tmp, path); return
        except PermissionError as e:
            if attempt == tries - 1:
                try: Path(tmp).unlink()
                except OSError: pass
                raise PermissionError(
                    f"could not replace {Path(path).name}: another program holds it open, or writes are "
                    f"blocked in {Path(path).parent}. Nothing was lost: today's reads are checkpointed, "
                    f"so re-run with --resume once the file is free. ({e})") from e
            time.sleep(wait); wait *= 2

def gzip_file_atomic(src, dst, level=6):
    import gzip, shutil
    dst = Path(dst); tmp = dst.with_name(dst.name + ".tmp")
    with open(src, "rb") as s, gzip.open(tmp, "wb", compresslevel=level) as d:
        shutil.copyfileobj(s, d, 1 << 20)
    replace_with_retry(tmp, dst)

def write_dated_snapshot(latest=None):
    """snapshot_<today>.json.gz from latest.json; drops a same-day raw copy. Returns the path."""
    gz = DATA / f"snapshot_{TODAY.isoformat()}.json.gz"
    gzip_file_atomic(latest or DATA / "latest.json", gz)
    raw = DATA / f"snapshot_{TODAY.isoformat()}.json"
    if raw.exists(): raw.unlink()
    return gz

def compress_old_snapshots(keep_days=SNAPSHOT_RAW_DAYS):
    """Gzip raw dated snapshots at least keep_days old (latest.json is never touched). They are
    around 100 MB of JSON each and compress about 8x; load_json() reads either form. A raw file whose
    .gz is already newer (a same-day rerun wrote the .gz) is the stale copy and is removed rather
    than compressed over the newer data. Returns (files, MB saved)."""
    n, saved = 0, 0
    for p in sorted(DATA.glob("snapshot_*.json")):
        try:
            d = dt.date.fromisoformat(p.stem.replace("snapshot_", ""))
        except ValueError:
            continue
        if (TODAY - d).days < keep_days:
            continue
        gz = p.with_name(p.name + ".gz")
        size = p.stat().st_size
        if gz.exists() and gz.stat().st_mtime >= p.stat().st_mtime:
            p.unlink(); n += 1; saved += size
            continue
        gzip_file_atomic(p, gz)
        saved += size - gz.stat().st_size
        p.unlink(); n += 1
    return n, saved / 1e6

def diff_is_meaningful():
    """first_seen is only a freshness SIGNAL once consecutive runs exist. The first run
    stamps every key with the same date, so presenting it as signal would be a lie."""
    return snapshot_count() >= 2

def should_read_live(t, named, readlog, fresh=False):
    """Tiered refresh. Returns (live: bool, why: str)."""
    if named and named.lower() in t["company"].lower(): return True, "named on the command line"
    # --fresh answers the question --force does not: --force only bypasses the snapshot-age
    # shortcut, so a target inside its 24h TTL is still served from cache. --fresh bypasses the
    # TTL too, for a run whose whole point is "what is new today".
    if fresh: return True, "--fresh: TTL bypassed"
    if TIER_A_ALWAYS_LIVE and (t.get("tier") or "C").upper() == "A": return True, "tier A"
    age = hours_since(((readlog.get(t["company"]) or {}).get("last_read")))
    if age is None: return True, "never read"
    if age > CACHE_TTL_HOURS: return True, f"cache expired, last read {fmt_age(age)}"
    return False, f"cache {fmt_age(age)}"

def stale_banner(ts, named=None):
    """Any window answer drawn from a stale snapshot has to say so BEFORE the results.
    Silent stale data is the failure mode that matters most here."""
    age = hours_since(ts)
    if age is None:
        return "!! NO SNAPSHOT TIMESTAMP: age of this data is unknown. Treat every date below as unverified."
    if age > STALE_WARN_HOURS:
        return (f"!! STALE DATA: this answer comes from a snapshot {fmt_age(age)}, older than "
                f"{STALE_WARN_HOURS}h. Reqs may have closed or been posted since."
                + ("" if named else " Re-run without --report-only to refresh."))
    return None

# ----------------------------------------------------------------------------- parallel lanes
# A read of hundreds of employers done one at a time takes well over an hour. Employers on
# DIFFERENT hosts share nothing, so each host gets its own lane (a thread), and a lane walks its
# employers sequentially at the usual DELAY. MAXQ_SERIAL=1 forces one lane total.
# Lanes are keyed by HOST, not by ATS: two Eightfold tenants on different hosts should not queue
# behind each other. Shared multi-tenant APIs (boards-api.greenhouse.io, api.ashbyhq.com) get a few
# lanes; Workday gets a few lanes per shard (wd1, wd5, ...); every other host gets one.
LANE_WIDTH = {"greenhouse": 4, "ashby": 4, "lever": 2, "smartrecruiters": 2, "workday": 3}
MAX_LANES = 40

def lane_key(t):
    """The host an adapter talks to, which is what politeness is owed to."""
    from urllib.parse import urlparse
    ats = t.get("ats") or "?"
    if ats == "workday":
        return f"workday:{t.get('shard')}"
    for k in ("base", "origin", "list_url", "board_url", "url"):
        v = t.get(k)
        if isinstance(v, str) and "://" in v:
            return urlparse(v).netloc.lower() or ats
    if t.get("host"):
        return str(t["host"]).lower()
    return ats

def run_laned(items, lane_of, fn, widths=None):
    """Apply fn to every item, one thread per lane, sequential within a lane. Returns results in
    completion order. fn must catch its own exceptions; anything that escapes is re-raised."""
    from concurrent.futures import ThreadPoolExecutor
    items = list(items)
    if not items: return []
    if os.environ.get("MAXQ_SERIAL"):
        return [fn(x) for x in items]
    widths = LANE_WIDTH if widths is None else widths
    queues = {}
    for it in items:
        name = lane_of(it)
        w = widths.get(name) or widths.get(str(name).split(":")[0]) or 1
        qs = queues.setdefault(name, [[] for _ in range(max(1, w))])
        min(qs, key=len).append(it)
    # Longest queues first, so the slow boards start immediately when lanes exceed MAX_LANES.
    flat = sorted((q for qs in queues.values() for q in qs if q), key=len, reverse=True)
    out = []
    with ThreadPoolExecutor(max_workers=min(len(flat), MAX_LANES)) as ex:
        for f in [ex.submit(lambda q: [fn(x) for x in q], q) for q in flat]:
            out.extend(f.result())
    return out

def write_json_atomic(path, obj, **dump_kw):
    """Write to a temp file in the same directory, then replace. A crash or a full disk mid-write
    leaves the previous file intact instead of a truncated latest.json."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, **dump_kw), encoding="utf-8")
    replace_with_retry(tmp, path)

# Incremental reads. Boards that sort newest-first (Oracle POSTING_DATES_DESC, Eightfold/PCSX
# timestamp, Apple newest, Google date) put today's postings on the first pages, so a daily walk of
# a board with thousands of rows re-reads known reqs to learn nothing. When
# the employer had a FULL walk within FULL_WALK_DAYS, the adapter stops after STALE_PAGES consecutive
# pages holding only ids already in the snapshot; the unread tail is carried forward, never closed.
# Sort orders here are not strict (a refreshed req can move), which is why it takes several all-known
# pages in a row, and why a full walk still runs weekly and on --full.
INCREMENTAL_ATS = {"oracle", "pcsx", "eightfold", "apple", "google"}
FULL_WALK_DAYS = 7
STALE_PAGES = 3
CARRY_INCREMENTAL = "incremental read stopped at known reqs; tail not re-read this run"

def incremental_stop(t, page_ids):
    """Call once per page with that page's ids. True means stop paging (and marks the read partial)."""
    known = t.get("_known_ids")
    ids = [str(i) for i in page_ids if i is not None]
    if not known or not ids:
        return False
    if any(i not in known for i in ids):
        t["_stale_pages"] = 0
        return False
    t["_stale_pages"] = t.get("_stale_pages", 0) + 1
    if t["_stale_pages"] >= STALE_PAGES:
        t["_partial"] = True
        return True
    return False

# Detail reuse. Most detail fetches are for reqs whose list-level title, location and date have not
# moved since the day before, so the page fetched is the page already on disk. A req whose list
# signature is unchanged reuses the stored detail for
# DETAIL_TTL_DAYS, then is fetched again, so a quiet JD rewrite is still caught within a week.
DETAIL_TTL_DAYS = 7

def list_signature(r):
    return jd_hash(f"{r.get('title')}|{r.get('location')}|{r.get('posted')}")

def reuse_detail(r, p):
    """Copy the previous run's detail into r when the list read is unchanged and the detail is fresh.
    Returns True when reused."""
    if not p or not p.get("description") or not r.get("_list_sig"):
        return False
    if p.get("_list_sig") != r["_list_sig"]:
        return False
    age = days_since(p.get("_detail_on"))
    if age is None or age >= DETAIL_TTL_DAYS:
        return False
    r["description"] = p["description"]
    for f in ("posted", "comp", "location", "_detail_on"):
        if p.get(f): r[f] = p[f]
    r["extra"] = dict(p.get("extra") or {}, **(r.get("extra") or {}))
    return True

# "2 Locations" (Workday), "Multiple Locations" (Radancy). Either way the req would fail the
# location gate without ever earning the detail fetch that carries its real cities.
PLACEHOLDER_LOCATION = re.compile(r"\s*(?:\d+|multiple|various|several)\s+locations?\s*", re.I)

def placeholder_location_fail(row, verdict, g=None):
    """A req that passed the title gates and failed location only because the board wrote a count
    ("3 Locations", "Multiple Locations") instead of places. The detail pass supplies the real
    locations, so it earns a fetch like a gate-passing req. gate() checks title before location, so
    a location FAIL here already means the title is in lane."""
    v, why = verdict
    loc = row.get("location") or ""
    # A bare "Remote" row earns the fetch too: its body is what decides (bare_remote_us_evidence).
    return (v == "FAIL" and bool(why) and why[0].startswith("location gate")
            and bool(PLACEHOLDER_LOCATION.fullmatch(loc)
                     or bool(g and (g.get("bare_remote") or {}).get("body_check") and bare_remote_only(loc, g))))

RESCUE_RECHECK_DAYS = 30
# A rescue body fetch that failed (a 403 on the detail page, say) is a req-level FAIL, never an
# employer coverage hole: the rescue read is budgeted and optional, and the board itself was read.
# The failure is stamped on the row and the fetch is retried after this many days, so the budget does
# not spend itself on the same refusal every run.
RESCUE_FAIL_RETRY_DAYS = 7

def _rescue_lexicon_sig(gates):
    return jd_hash(json.dumps(gates.get("prerank") or {}, sort_keys=True))

def rescue_stamp_verdict(r, p, gates, lex):
    """The FAIL a body read already earned, or None when the body has to be read (again).

    A rescued body under the floor gets a reason starting "title", so strip_irrelevant_text blanks it.
    Without a stamp the next run would see no description, reuse_detail would refuse, and the req
    would go back into the pool with the same title score: the SAME top of the pool would win the
    budget every run and the backlog would never advance. The score is kept on the row instead; it
    stands until the list read, the lexicon or 30 days change, or the floor drops to where it would pass."""
    st = (p or {}).get("_rescue")
    if st and st.get("failed") and st.get("sig") == r.get("_list_sig"):
        age = days_since(st.get("on"))
        if age is None or age >= RESCUE_FAIL_RETRY_DAYS: return None
        r["_rescue"] = st
        return "FAIL", [f"title out of lane: rescue fetch failed {st['on']}, retried after {RESCUE_FAIL_RETRY_DAYS} days"]
    if not st or st.get("sig") != r.get("_list_sig") or st.get("lex") != lex: return None
    age = days_since(st.get("on"))
    if age is None or age >= RESCUE_RECHECK_DAYS: return None
    floor = int((gates.get("rescue") or {}).get("min_body_score", 8))
    if st.get("score") is None or st["score"] >= floor: return None
    r["_rescue"] = st
    return "FAIL", [f"title out of lane: body scored {st['score']} (floor {floor}; body read {st['on']})"]

# A full sweep spends most of its time in the detail and rescue fetches after the list reads, and
# printed nothing there. FetchProgress is the heartbeat: at most one line per PROGRESS_EVERY_S, so a
# short pass prints nothing and every other line of the output is unchanged.
PROGRESS_EVERY_S = 30

class FetchProgress:
    def __init__(self, n_detail, n_rescue, every=None, clock=time.monotonic,
                 out=lambda s: print(s, flush=True)):
        self.total = {"detail": n_detail, "rescue": n_rescue}
        self.done = {"detail": 0, "rescue": 0}
        self.every = PROGRESS_EVERY_S if every is None else every
        self.clock, self.out = clock, out
        self.start = self.last = clock()
        self.lock = __import__("threading").Lock()

    def __call__(self, rescue):
        with self.lock:
            self.done["rescue" if rescue else "detail"] += 1
            now = self.clock()
            if now - self.last < self.every: return
            self.last = now
            parts = [f"{name} pass fetched {self.done[name]} of {self.total[name]}"
                     for name in ("detail", "rescue") if self.total[name]]
            secs = int(now - self.start)
            self.out(f"  progress: {'; '.join(parts)} ({secs // 60}m {secs % 60:02d}s)")

def gate_all(rows, prev, gates, fetch=True):
    """Gate every row, filling in the JD text a board withholds at list level.

    Three things happen here and the order matters.

    1. Gate on what the list read gave us. Title and location always arrive.
    2. For a req on a DETAIL board with no text whose verdict is in DETAIL_VERDICTS,
       fetch the req page. Every run, not only the run a req first appears, so a
       single failure or a cached employer never makes the gap permanent.
    3. For anything still empty, inherit the previous snapshot's text, date and band.
       Without this, rebuilding `rows` from a live read DELETES detail the last run
       paid for: the fresh list-level row has no description, so it overwrites the
       detailed one, the posted date is lost, and the JD hash flips to the empty string
       and reads as "JD text changed" when nothing was rewritten.

    Fetching before inheriting is deliberate. A PASS req gets fresh text every run, so
    jd_hash tracks real rewrites; inheritance is the fallback that stops a miss from
    ever being silent. The cost stays small because the title and location gates run
    first and kill everything else.
    """
    verdicts, failed, healed = {}, set(), set()
    stats = {"fetched": 0, "failed": 0, "inherited": 0, "rebanded": 0, "reused": 0,
             "rescue_candidates": 0, "rescue_fetched": 0, "rescue_skipped": 0, "rescue_known": 0,
             "rescue_failed": 0, "rescue_fail_wait": 0}
    todo, rescue_pool = [], []
    lex = _rescue_lexicon_sig(gates)
    for k, r in rows.items():
        verdicts[k] = gate(r, gates)
        # _target is attached only to rows this run read LIVE. A cached employer's rows are
        # copies of the last snapshot, so they already carry whatever detail existed and there
        # is nothing to fetch; without this guard they raise KeyError and read as failed fetches.
        fetchable = (fetch and r["ats"] in DETAIL and not r.get("description") and r.get("_target"))
        # REVIEW here is the business-systems engineering title (engineering_title_review): gate-
        # relevant, so it earns the fetch rather than sitting textless on a detail board.
        manager_pending = (verdicts[k][0] == "FAIL" and bool(verdicts[k][1])
                           and verdicts[k][1][0].endswith(MANAGER_BODY_PENDING))
        if fetchable and (verdicts[k][0] in DETAIL_VERDICTS + ("REVIEW",) or manager_pending
                          or placeholder_location_fail(r, verdicts[k], gates)):
            if reuse_detail(r, prev.get(k)):
                stats["reused"] += 1
                verdicts[k] = gate(r, gates)
            else:
                todo.append(k)
        elif fetchable and verdicts[k][0] == "RESCUE-FETCH":
            # An out-of-lane req needs its body to be judged, and there are tens of thousands of
            # them. This is the only fetch in the sweep that is BUDGETED rather than needed, so it
            # never competes with a gate-passing req: newest-posted first, reqs new to the record
            # ahead of the standing backlog, and a per-employer cap so one big board cannot eat
            # the run. Whatever the budget does not reach stays FAIL and is counted, not silent.
            known = rescue_stamp_verdict(r, prev.get(k), gates, lex)
            if known:
                stats["rescue_fail_wait" if (r.get("_rescue") or {}).get("failed") else "rescue_known"] += 1
                verdicts[k] = known
            elif reuse_detail(r, prev.get(k)):
                stats["reused"] += 1
                verdicts[k] = gate(r, gates)
            else:
                rescue_pool.append(k)

    if rescue_pool:
        rcfg = gates.get("rescue") or {}
        cap = int(rcfg.get("max_fetch_per_run", 250))
        per_emp = int(rcfg.get("max_fetch_per_employer", 15))
        from collections import Counter as _C
        # Three stable sorts, least significant first. Title relevance decides the budget because it
        # is the only signal available before the fetch is paid for: far more reqs want a body read
        # than the budget covers, and 'AV Operations Specialist' must not spend the budget that 'Order-to-Cash Transformation Advisor'
        # needs. Scored on the title alone (empty body), so this costs nothing.
        rescue_pool.sort(key=lambda k: rows[k].get("posted") or "", reverse=True)
        rescue_pool.sort(key=lambda k: k in prev)          # stable: new to the record first
        rescue_pool.sort(key=lambda k: -(prerank(rows[k], gates, "") or 0))
        picked, seen = [], _C()
        for k in rescue_pool:
            if len(picked) >= cap: break
            c = rows[k]["company"]
            if seen[c] >= per_emp: continue
            seen[c] += 1; picked.append(k)
        for k in set(rescue_pool) - set(picked):
            verdicts[k] = ("FAIL", ["title out of lane: body not read (rescue budget)"])
        stats["rescue_candidates"] = len(rescue_pool)
        stats["rescue_fetched"] = len(picked)
        stats["rescue_skipped"] = len(rescue_pool) - len(picked)
        todo += picked
    rescued_now = set(rescue_pool) & set(todo)

    tick = FetchProgress(len(todo) - len(rescued_now), len(rescued_now))
    def _detail(k):
        r = rows[k]
        try:
            DETAIL[r["ats"]](r["_target"], r)
            return k, None
        except Exception as e:
            return k, e
        finally:
            tick(k in rescued_now)
    # Detail fetches run one lane per ATS in parallel (run_laned), sequential inside a lane, so
    # each vendor sees the same polite pace it always did.
    for k, err in run_laned(todo, lambda k: lane_key(rows[k]["_target"]), _detail):
        if err is None:
            stats["fetched"] += 1
            rows[k]["_detail_on"] = TODAY.isoformat()
            verdicts[k] = gate(rows[k], gates)
            if k in rescued_now and (rows[k].get("description") or "").strip():
                # Paid for once: the score outlives the body that strip_irrelevant_text is about to blank.
                rows[k]["_rescue"] = {"score": prerank(rows[k], gates, None), "sig": rows[k].get("_list_sig"),
                                      "lex": lex, "on": TODAY.isoformat()}
        elif k in rescued_now:
            # Req-level FAIL, stamped so the budget does not spend itself on the same refusal every run.
            verdicts[k] = ("FAIL", [f"title out of lane: rescue fetch failed: {str(err)[:160]}"])
            rows[k]["_rescue"] = {"score": None, "failed": str(err)[:160], "sig": rows[k].get("_list_sig"),
                                  "lex": lex, "on": TODAY.isoformat()}
            stats["rescue_failed"] += 1
        else:
            verdicts[k][1].append(f"detail fetch failed: {err}")
            stats["failed"] += 1; failed.add(k)
    for k, r in rows.items():
        p = prev.get(k)
        if not p or r.get("description") or not p.get("description"): continue
        r["description"] = p["description"]
        if not r.get("posted"): r["posted"] = p.get("posted")
        if not r.get("comp"):   r["comp"]   = p.get("comp")
        stats["inherited"] += 1; healed.add(k)
    for k in healed:
        if (rows[k].get("_rescue") or {}).get("failed"): rows[k].pop("_rescue")   # the old body decides
        verdicts[k] = gate(rows[k], gates)
    # Comp re-derivation for EVERY detail board. norm() runs the band fallback at list time, when a
    # detail-board row has no body yet, so those rows would otherwise never carry a band. One pass
    # here, after fetch and inherit, covers all of them. Scores already in scored.json do not move by
    # themselves; a re-banded req that was scored without a band is listed by --window with its band.
    for r in rows.values():
        if r.get("description") and not r.get("comp"):
            band = comp_from_description(r["description"])
            if band: r["comp"] = band; stats["rebanded"] += 1
    # A fetch that failed with nothing on disk to fall back on is a coverage hole, and
    # this codebase never lets one be quiet.
    for k in failed - healed:
        verdicts[k] = ("UNCOVERED", verdicts[k][1])
    # Anything still RESCUE-FETCH was never placed in the pool: a carried or cached row (no _target),
    # a board with no detail adapter, or a body that came back empty. See gate_final().
    for k, (v, why) in verdicts.items():
        if v == "RESCUE-FETCH":
            verdicts[k] = ("FAIL", ["title out of lane: body not read (row not read live, or no detail read for this board)"])
    return verdicts, stats

def run(targets, gates, only=None, smoke=False, merge=None, digest=False, force=False, fresh=False,
        resume=False, full=False):
    seen_ledger = load_json(DATA / "seen.json", {})
    snap_prev = load_json(DATA / "latest.json", {"rows": {}})
    prev = snap_prev.get("rows", {})
    readlog = load_json(READLOG, {})
    named = merge or only
    WORKDAY_TOTALS.clear(); WORKDAY_COMPLETE.clear()

    # Snapshot-freshness shortcut. If the last full snapshot is young and no employer
    # was named, there is nothing to learn from refetching every board.
    if not smoke and not named and not force and not fresh:
        age = hours_since(snap_prev.get("ts"))
        if age is not None and age < SNAPSHOT_SKIP_HOURS:
            print(f"SNAPSHOT IS {fmt_age(age)} (under the {SNAPSHOT_SKIP_HOURS}h refetch threshold): "
                  f"skipping the network entirely and filtering what is already on disk. "
                  f"Use --force to sweep anyway.")
            report_only(gates, digest=digest)
            return

    coverage, rows, live_reads, cached_reads = [], {}, [], []
    prev_counts = {c: (v or {}).get("count") for c, v in readlog.items()}
    prev_scopes = {c: (v or {}).get("scope") for c, v in readlog.items()}
    ckdir = DATA / "checkpoint" / TODAY.isoformat()
    full_run = not (smoke or only or merge)
    if full_run and resume and snap_prev.get("date") == TODAY.isoformat():
        cks = list(ckdir.glob("*.json")) if ckdir.exists() else []
        snap_ts = hours_since(snap_prev.get("ts"))
        newest_ck = min(((time.time() - c.stat().st_mtime) / 3600 for c in cks), default=None)
        if not cks or (snap_ts is not None and newest_ck is not None and snap_ts <= newest_ck):
            # Today's snapshot exists and there is no crashed read left to continue (no checkpoints, or
            # every checkpoint is OLDER than the snapshot): that run had already written its state.
            # Resuming would diff today against today and write a quiet-day report over it.
            print("--resume REFUSED: today's data/latest.json is already written and no unfinished read remains, "
                  "so there is nothing to resume. Rebuilding the report from it instead (what --report-only "
                  "does). For a second sweep today, run without --resume.")
            for c in cks: c.unlink()
            report_only(gates, digest=digest)
            return
    if full_run and not resume and ckdir.exists():
        # A new full run never mixes with a crashed one's partial reads unless --resume says so.
        for f in ckdir.glob("*.json"): f.unlink()
    to_read = []
    for t in targets:
        if only and only.lower() not in t["company"].lower(): continue
        if merge and merge.lower() not in t["company"].lower(): continue
        fn = ADAPTERS.get(t["ats"])
        if not fn:
            err = PLUGIN_ERRORS.get(t["ats"])
            coverage.append((t["company"], t["ats"],
                             f"ERROR NoAdapter: plugin failed to import: {err}" if err else "NO ADAPTER", 0))
            continue

        live, why = should_read_live(t, named, readlog, fresh) if not (only or merge) else (True, "named on the command line")
        if not live:
            # Serve this employer from the last snapshot rather than hitting the board.
            cached = {k: r for k, r in prev.items() if r.get("company") == t["company"]}
            for k, r in cached.items(): rows[k] = dict(r)
            cached_reads.append((t["company"], why, len(cached)))
            coverage.append((t["company"], t["ats"], f"CACHED ({why})", len(cached)))
            continue
        ck = ckdir / (re.sub(r"[^A-Za-z0-9]+", "_", t["company"]) + ".json")
        if full_run and resume and ck.exists():
            # --resume: this employer was read cleanly before the crash. Reuse that read.
            saved = load_json(ck, {})
            for r in saved.get("rows", []): r["_target"] = t; rows[r["key"]] = r
            coverage.append(tuple(saved["coverage"][:2]) + (saved["coverage"][2] + " (resumed from checkpoint)",
                                                           saved["coverage"][3]))
            if saved.get("readlog"): readlog[t["company"]] = saved["readlog"]
            live_reads.append((t["company"], "resumed from checkpoint"))
            continue
        live_reads.append((t["company"], why))
        to_read.append((t, fn, ck))

    total, done, plock = len(to_read), [0], __import__("threading").Lock()
    # Incremental reads: known ids per employer from the last snapshot, and whether a full walk is due.
    ids_by_co = {}
    if full_run and not full:
        # Carried rows count as known: they are the unread tail of yesterday's incremental read, and
        # leaving them out would make that tail look new and force a full walk every other day.
        for r in prev.values():
            ids_by_co.setdefault(r.get("company"), set()).add(str(r.get("id")))
    gsig = etag_gates_sig(gates)
    for t, _, _ in to_read:
        for f in ("_known_ids", "_partial", "_stale_pages", "_scope", "_scope_note",
                  "_etag_prev", "_etag_trust", "_etag_gates", "_etag_stat", "_etag_restamped"):
            t.pop(f, None)
        last_full = hours_since((readlog.get(t["company"]) or {}).get("last_full"))
        if (t["ats"] in INCREMENTAL_ATS and ids_by_co.get(t["company"])
                and last_full is not None and last_full < FULL_WALK_DAYS * 24):
            t["_known_ids"] = ids_by_co[t["company"]]
        if t["ats"] in ETAG_ATS and not smoke:
            # Arm the conditional read. A 304 is trusted only under the gates the stored rows were
            # gated under: after a gates.json edit the board is read whole once more.
            prior = readlog.get(t["company"]) or {}
            t["_etag_prev"] = etag_list(prior.get("etag"))
            t["_etag_gates"] = gsig
            t["_etag_trust"] = (bool(gates.get("etag_trust")) and prior.get("etag_gates") == gsig
                                and t["ats"] in gates.get("etag_trust_ats", ETAG_TRUST_ATS_DEFAULT))

    def _read(item):
        t, fn, ck = item
        started = time.time()
        t["_partial"], t["_stale_pages"] = False, 0
        t.pop("_etag_stat", None)
        try:
            got = fn(t, smoke=smoke)
            prior = readlog.get(t["company"]) or {}
            if got is BOARD_UNCHANGED:
                # Trusted 304: the board is what the last snapshot holds, so those rows are read
                # live now (descriptions, hashes and detail stamps intact; carry stamps off). Nothing
                # to re-stamp is an ERROR, never a quiet zero, and the armed tags come off so the
                # retry pass reads the board plainly.
                uniq = unchanged_rows(prev, t)
                if not uniq:
                    t.pop("_etag_prev", None); t.pop("_etag_trust", None)
                    raise Etag304NoRows("board unchanged (304) but the snapshot holds no rows to re-stamp")
                t["_etag_restamped"] = True
                cov = (t["company"], t["ats"], "OK (304 unchanged)", len(uniq))
                log = dict({"last_read": iso_now(), "count": prior.get("count") or len(uniq), "last_full": iso_now()},
                           **etag_log_fields(t, prior))
            else:
                # One dedupe for every adapter, before anything is counted. Reqs are keyed
                # ats:company:id, so a board that lists the same req twice (Atlassian's feed
                # does) collapses here rather than inflating its coverage number.
                uniq = {}
                for r in got:
                    r["_list_sig"] = list_signature(r)       # before any detail pass rewrites location
                    uniq[r["key"]] = r
                dupes = len(got) - len(uniq)
                log = None
                st = t.get("_etag_stat")
                if st and st["hit"] and not st["miss"]:
                    # Measurement: every page said 304 and the body was read anyway. Does it match the
                    # snapshot? This mismatch count is the number that decides whether trust is safe.
                    base = unchanged_rows(prev, t)
                    same = ({k: (r.get("_list_sig"), jd_hash(r.get("description"))) for k, r in uniq.items()}
                            == {k: (r.get("_list_sig"), r.get("jd_hash")) for k, r in base.items()})
                    st["mismatch"] = 0 if same or not base else 1
                # Silent-zero guard. A confirmed board that answers 200 with an empty array is a
                # coverage hole, not a quiet day: it is how a stale slug reads on Greenhouse and
                # SmartRecruiters. Never report it OK.
                if not uniq and t.get("verified") and t.get("empty_ok"):
                    # The slug was re-proved against the employer's own careers page, which reads the same
                    # empty board. Covered, not quiet-by-defect.
                    cov = (t["company"], t["ats"], "OK (empty board: empty_ok, slug proved from the careers page)", 0)
                elif not uniq and t.get("verified"):
                    cov = (t["company"], t["ats"], "ERROR EmptyBoard: verified target returned 0 reqs, re-resolve the slug", 0)
                elif not uniq:
                    cov = (t["company"], t["ats"], "UNVERIFIED-ZERO: 0 reqs from an unconfirmed slug, resolve from a real job URL", 0)
                elif t.get("_partial"):
                    # Stopped at known reqs: keep the full count and the last full-walk time.
                    cov = (t["company"], t["ats"], "OK (incremental: stopped at known reqs)", len(uniq))
                    log = {"last_read": iso_now(), "count": prior.get("count", len(uniq)),
                           "last_full": prior.get("last_full"), "incremental_rows": len(uniq)}
                else:
                    status = "OK" if not dupes else f"OK, {dupes} duplicate row(s) collapsed by the board"
                    if t.get("_scope_note"):
                        # Which walk a scoped adapter ran (Workday country scope): the status is what
                        # --resume carries and what count_drops reads.
                        status += f" ({t['_scope_note']})"
                    cov = (t["company"], t["ats"], status, len(uniq))
                    log = {"last_read": iso_now(), "count": len(uniq), "last_full": iso_now()}
                if log:
                    log.update(etag_log_fields(t, prior))
                    if t.get("_scope"): log["scope"] = t["_scope"]
        except Exception as e:
            uniq, log = {}, None
            cov = (t["company"], t["ats"], f"ERROR {type(e).__name__}: {str(e)[:120]}", 0)
        if full_run and log:
            # Checkpoint every clean read, so a crash at employer 300 costs one employer, not the run.
            ckdir.mkdir(parents=True, exist_ok=True)
            try:
                write_json_atomic(ck, {"coverage": list(cov), "readlog": log, "rows": list(uniq.values())})
            except OSError as e:
                print(f"  (checkpoint write failed for {t['company']}: {e})")
        with plock:
            done[0] += 1
            if not smoke:
                print(f"  [{done[0]:3}/{total}] {t['company'][:30]:30} {t['ats']:15} {cov[3]:5}  "
                      f"{cov[2][:70]}  ({time.time() - started:.0f}s)", flush=True)
        return t, uniq, cov, log

    def _absorb(results):
        for t, uniq, cov, log in results:
            coverage.append(cov)
            if log: readlog[t["company"]] = log
            for r in uniq.values(): r["_target"] = t; rows[r["key"]] = r

    if total and not smoke:
        print(f"reading {total} employer(s) live across {len({lane_key(t) for t, _, _ in to_read})} host(s)", flush=True)
    results = run_laned(to_read, lambda item: lane_key(item[0]), _read)
    # One retry pass for employers that errored. Most errors on a long run are transient
    # (dropped connections, a 502), and an ERROR employer is UNCOVERED for the whole run.
    by_co = {item[0]["company"]: item for item in to_read}
    retry = [by_co[t["company"]] for t, uniq, cov, log in results
             if cov[2].startswith("ERROR") and not smoke]
    if retry:
        print(f"retrying {len(retry)} errored employer(s) once after a 20s pause: "
              + ", ".join(t["company"] for t, _, _ in retry), flush=True)
        time.sleep(20)
        done[0], total = 0, len(retry)
        again = {r[0]["company"]: r for r in run_laned(retry, lambda item: lane_key(item[0]), _read)}
        results = [again.get(r[0]["company"], r) for r in results]
    _absorb(results)
    order = {t["company"]: i for i, t in enumerate(targets)}
    coverage.sort(key=lambda c: order.get(c[0], 1 << 30))
    if smoke:
        print_coverage(coverage); return
    if full_run and not resume and not gates.get("etag_trust"):
        etag_record_run([t for t, _, _ in to_read])
    etag_line = etag_summary([t for t, _, _ in to_read], bool(gates.get("etag_trust")))

    # Say plainly what was read live and what came out of cache, with cache age.
    if live_reads or cached_reads:
        # Name each employer only when the reason varies; hundreds of copies of "--fresh" is noise.
        reasons = {}
        for c, w in live_reads: reasons.setdefault(w, []).append(c)
        print(f"\nREAD LIVE ({len(live_reads)}): " + "; ".join(
            f"{len(cs)} [{w}]" if len(cs) > 10 else f"{', '.join(cs)} [{w}]" for w, cs in reasons.items()))
        if cached_reads:
            print(f"FROM CACHE ({len(cached_reads)}): " + ", ".join(f"{c} [{w}, {n} reqs]" for c, w, n in cached_reads))
        print()
    # ---- merge mode: fold one employer's live rows into the existing snapshot -----
    # A company-scoped run must never make the other employers look closed, and
    # must never restamp their first_seen. --only stays read-only; this is the
    # writing version, and it writes exactly one employer's worth of state.
    if merge:
        merged_companies = sorted({r["company"] for r in rows.values()})
        if not merged_companies:
            print(f"--merge {merge}: matched no target. Nothing read, nothing written.")
            return
        before_dates = dict(seen_ledger)                   # for the untouched-dates proof
        recovered, confirmed_closed, lookup_failed = recover_scored(rows, coverage, targets, load_scored())
        # Without a detail pass here, `--merge Meta` would re-empty every row it touched. Fetch
        # and inherit here too, before anything is written. Runs while _target is still attached.
        _, dstats = gate_all(rows, prev, gates)
        base_rows = {k: v for k, v in prev.items() if v.get("company") not in merged_companies}
        new_against_full = [k for k in rows if k not in prev and k not in recovered]
        for k in rows:                                     # first_seen for THIS employer only
            seen_ledger.setdefault(k, TODAY.isoformat())
        # Absence is only evidence of closure on a board this adapter ENUMERATES.
        # On a query-scoped board the read is a sample, so rows it did not return are
        # carried forward untouched rather than deleted. Without this, one --merge against
        # Amazon would silently drop hundreds of live reqs, none of which had closed.
        enumerable = {t["ats"] in ENUMERABLE_ATS
                      for t in targets if t["company"] in merged_companies}
        authoritative = enumerable == {True}
        carried, dropped = {}, []
        for k, v in prev.items():
            if v.get("company") not in merged_companies or k in rows: continue
            co = v.get("company")
            if (authoritative or k in confirmed_closed or
                    (co in WORKDAY_COMPLETE and closes_on_complete_walk(v, WORKDAY_COMPLETE[co], prev_scopes.get(co)))):
                dropped.append(k)
            else: carried[k] = v
        merged_rows = dict(base_rows); merged_rows.update(carried); merged_rows.update(rows)
        for r in merged_rows.values(): r.pop("_target", None)
        # The full run stamps jd_hash below; a merge never reaches that code, so stamp it here or
        # the next run's "JD text changed" check has nothing to compare.
        for r in rows.values():
            if r.get("description") or not r.get("jd_hash"):
                r["jd_hash"] = jd_hash(r.get("description"))
        # Record-count proof before anything is written. Every previous row is either still here
        # or explicitly listed as closed on an enumerable board; anything else is a merge bug, and
        # the snapshot is left untouched rather than written with rows silently missing.
        lost = [k for k in prev if k not in merged_rows and k not in dropped]
        if lost or len(merged_rows) != len(base_rows) + len(carried) + len(rows):
            raise RuntimeError(f"merge integrity check failed: {len(lost)} previous row(s) would vanish "
                               f"without being recorded as closed (e.g. {lost[:3]}); snapshot NOT written")
        verdicts_all = {k: gate_final(r, gates) for k, r in merged_rows.items()}
        snap = dict(snap_prev)
        snap.update({"date": TODAY.isoformat(), "ts": iso_now(), "rows": merged_rows,
                     "verdicts": {k: v[0] for k, v in verdicts_all.items()},
                     "reasons": {k: v[1] for k, v in verdicts_all.items()},
                     "coverage": coverage, "new_keys": new_against_full,
                     "closed": {k: prev[k] for k in dropped},
                     "merged_from": snap_prev.get("date"),
                     "merged_employer": merged_companies,
                     "merge_authoritative": authoritative})
        write_json_atomic(DATA / "latest.json", snap)
        write_json_atomic(DATA / "seen.json", seen_ledger, indent=0)
        write_json_atomic(READLOG, readlog, indent=1)
        # Prove, do not assert: every other employer's first_seen must be byte-identical.
        # A recovered scored req is in the ledger but not in prev; classify it by its merged row.
        others_before = {k: v for k, v in before_dates.items()
                         if (prev.get(k) or merged_rows.get(k) or {}).get("company") not in merged_companies}
        others_after = {k: v for k, v in seen_ledger.items()
                        if (merged_rows.get(k) or {}).get("company") not in merged_companies}
        changed = [k for k in others_before if others_after.get(k) != others_before[k]]
        print(f"MERGE {', '.join(merged_companies)}")
        print(f"  board read is       : {'ENUMERABLE (absence means closed)' if authoritative else 'QUERY-SCOPED (a sample; absence proves nothing)'}")
        print(f"  reqs read live      : {len(rows)}")
        print(f"  detail pass         : {dstats['fetched']} fetched, {dstats['inherited']} inherited "
              f"from the last snapshot, {dstats['failed']} failed")
        if etag_line: print("  " + etag_line)
        print(f"  new vs last full run: {len(new_against_full)}")
        if authoritative:
            print(f"  no longer on board  : {len(dropped)} (recorded as closed)")
        else:
            print(f"  carried forward     : {len(carried)} previously-known req(s) this read did not return, kept")
        print_scored_lookup(recovered, confirmed_closed, lookup_failed, merged_rows, indent="  ")
        print(f"  merged into snapshot: {snap_prev.get('date')} ({len(base_rows)} rows from other employers preserved)")
        print(f"  other employers' first_seen entries checked: {len(others_before)}")
        print(f"  other employers' first_seen entries CHANGED: {len(changed)}"
              + (" <-- BUG" if changed else "  (verified unchanged)"))
        for k in sorted(new_against_full)[:20]:
            r = merged_rows[k]
            print("  NEW  " + link(f"{r['company']} · {r['title']}", r["url"]))
        return

    # Carry-forward. Missing from this run's rows is not by itself evidence of closure: an employer
    # that ERRORED would have every one of its reqs recorded closed (then re-announced as NEW when
    # the board came back), and a QUERY-SCOPED board's sample is not the whole board. Those rows
    # stay, stamped with why, for up to CARRY_MAX_DAYS; after that they close normally.
    recovered, confirmed_closed, lookup_failed = recover_scored(rows, coverage, targets, load_scored())
    print_scored_lookup(recovered, confirmed_closed, lookup_failed, rows)
    dropped = {c for c, _p, _n in count_drops(coverage, prev_counts, prev_scopes)}
    carried = carry_forward(rows, prev, coverage, targets, only, confirmed_closed, dropped,
                            complete_walks=WORKDAY_COMPLETE, prev_scopes=prev_scopes)
    if carried:
        print(f"carried forward: {len(carried)} previously-seen req(s) not returned this run "
              f"({sum(1 for v in carried.values() if v == CARRY_ERROR)} from employers not read cleanly, "
              f"{sum(1 for v in carried.values() if v == CARRY_DROP)} from boards whose count fell 50%+, "
              f"{sum(1 for v in carried.values() if v == CARRY_SAMPLE)} from query-scoped boards, "
              f"{sum(1 for v in carried.values() if v == CARRY_SCOPE)} from country-scoped Workday reads, "
              f"{sum(1 for v in carried.values() if v == CARRY_INCREMENTAL)} from incremental reads)")
    # first_seen ledger
    for k in rows:
        seen_ledger.setdefault(k, TODAY.isoformat())
    new_keys = [k for k in rows if k not in prev and k not in recovered]
    closed = {k: v for k, v in prev.items() if k not in rows}
    # gate everything; fetch detail for every title/location-passing req the board
    # gives no JD text for, then inherit anything still empty. See gate_all().
    verdicts, dstats = gate_all(rows, prev, gates)
    print(f"detail pass: {dstats['fetched']} fetched, {dstats['reused']} reused (list unchanged, detail "
          f"under {DETAIL_TTL_DAYS}d old), {dstats['inherited']} inherited from the "
          f"last snapshot, {dstats['failed']} failed, {dstats['rebanded']} band(s) recovered from JD text")
    rescue_line = rescue_summary(dstats, gates)
    if rescue_line: print(rescue_line)
    if etag_line: print(etag_line)
    for r in rows.values(): r.pop("_target", None)
    # Repost detection: the same normalized title in the same first location at the
    # same employer, under a NEW id, is a repost. The new id's first_seen is today, which
    # would read as fresh; the honest age is the old id's. Recorded in the snapshot and shown
    # in the report, --window and the risk signals.
    reposts = detect_reposts(rows, prev, new_keys)
    repost_fps = load_json(_repost_fps_path(), {})
    reposts.update(detect_gap_reposts(rows, new_keys, reposts, repost_fps))
    if reposts:
        print(f"reposts: {len(reposts)} new id(s) match a previously seen title+location at the same employer")
    # A repost stays a repost for as long as the new id is open, not just on the day it appeared.
    reposts = dict({k: v for k, v in (snap_prev.get("reposts") or {}).items() if k in rows}, **reposts)
    if only:
        # A filtered run has only read part of the board. Writing the snapshot here would make
        # every unread target look closed and would stamp first_seen for the ones it did read,
        # silently wrecking the ledger the whole freshness model rests on. Read-only instead.
        print(f"PARTIAL RUN (--only {only}): {len(rows)} reqs read, state NOT updated. "
              f"No snapshot, no seen.json, no report written. Run without --only to update state.")
        for k, r in sorted(rows.items()):
            v, why = verdicts[k]
            if v == "PASS": print("  PASS  " + link(f"{r['company']} · {r['title']} · {r['location']}", r["url"]))
        print_coverage(coverage)
        return
    # Coverage and the diff go into the snapshot too, so --report-only can rebuild the report
    # from disk without re-fetching, so a crash in write_report never costs the whole run.
    # JD text hash per row, so a quietly rewritten req (raised tenure bar, narrowed
    # scope) stops looking unchanged. Compared against the previous snapshot below.
    for k, r in rows.items():
        # A carried or cached copy of a STRIPPED row has no text; rehashing it would store the
        # empty-string hash over the kept one, which is the opposite of "hash kept".
        if r.get("description") or not r.get("jd_hash"):
            r["jd_hash"] = jd_hash(r.get("description"))
    # Core hash: the JD with the employer's boilerplate lines removed. An employer that edits the
    # About/benefits block every one of its postings shares would otherwise flag every req as
    # "JD text changed". A line that appears in
    # a third of a company's reqs is boilerplate, not the req; hashing what is left tracks
    # the rewrites that matter (a raised bar, a narrowed scope).
    core_hashes(rows)
    # A req whose text went missing, or arrived for the first time, is a COVERAGE event,
    # not a rewrite: comparing the empty-string hash against real text would flag "JD text
    # changed" and trigger pointless re-scores. And a rewrite of a req that fails the title or
    # location gate, and was never scored, is nobody's business: only gate-relevant or scored
    # reqs are flagged.
    empty_hash = jd_hash("")
    scored_keys = set(load_scored())
    # The stored jd_core_hash is NEVER compared across runs. It is cut against the boilerplate of the
    # req population it was computed in, so when an employer's reqs come and go the set of "shared"
    # lines moves and the hash of byte-identical text moves with it, flagging reqs that have not
    # changed by a byte. The raw hash decides WHETHER the text changed; the core, cut against one
    # boilerplate set built from BOTH runs, only decides whether the change was boilerplate. Both
    # runs, because today's set does not contain yesterday's wording of an edited About block.
    boil_now, boil_prev = boilerplate_by_company(rows), boilerplate_by_company(prev)
    def _changed(k, r):
        p = prev.get(k)
        if not p or not p.get("jd_hash"): return False
        if empty_hash in (p["jd_hash"], r["jd_hash"]): return False
        if p["jd_hash"] == r["jd_hash"]: return False
        if verdicts[k][0] not in DETAIL_VERDICTS + ("REVIEW",) and k not in scored_keys: return False
        if p.get("description") and r.get("description"):
            b = boil_now.get(r["company"], set()) | boil_prev.get(r["company"], set())
            return core_hash(p["description"], b) != core_hash(r["description"], b)
        return True
    changed_jds = [k for k, r in rows.items() if _changed(k, r)]

    stripped = strip_irrelevant_text(rows, verdicts)
    if stripped:
        print(f"snapshot: JD text dropped from {stripped} out-of-lane req(s) (hash kept)")
    health = sweep_health(rows, verdicts, new_keys, coverage, prev_counts, snap_prev, dstats, len(live_reads),
                          prev_scopes=prev_scopes)
    # No sweep log is kept, so the rescue: and etag: lines ride in the health block (and the
    # snapshot) rather than living only in the console scrollback.
    health["notes"] = [x for x in (rescue_line, etag_line) if x]
    print_health(health)
    snap = {"date": TODAY.isoformat(), "ts": iso_now(), "rows": rows,
            "verdicts": {k: v[0] for k, v in verdicts.items()},
            "reasons": {k: v[1] for k, v in verdicts.items()}, "coverage": coverage,
            "new_keys": new_keys, "closed": closed, "changed_jds": changed_jds, "reposts": reposts,
            "health": health,
            # What each req's verdict WAS before this run changed it. --report-only re-gates this very
            # snapshot, so without this a rebuilt report would lose every newly-eligible req the sweep
            # found, and --report-only is the documented recovery after a write_report crash.
            "flipped": {k: pv for k, pv in (snap_prev.get("verdicts") or {}).items()
                        if k in verdicts and pv != verdicts[k][0]}}
    # Serialize once (a snapshot dump takes seconds): latest.json raw, the dated snapshot
    # gzipped from it (see SNAPSHOT_RAW_DAYS for the reason).
    write_json_atomic(_repost_fps_path(), update_repost_fps(repost_fps, rows, verdicts), indent=0)
    # Order matters. The ledger first: if latest.json then fails, --resume still diffs against
    # YESTERDAY and today's first_seen stamps are already safe. The checkpoints go the moment
    # latest.json lands, because from then on a --resume would load TODAY's snapshot as "previous",
    # find nothing new, and write a quiet-day report over a day that had new reqs. After this point
    # the recovery is --report-only.
    write_json_atomic(DATA / "seen.json", seen_ledger, indent=0)
    write_json_atomic(DATA / "latest.json", snap)
    for f in ckdir.glob("*.json"): f.unlink()
    try:
        write_dated_snapshot()
    except OSError as e:
        print(f"(dated snapshot write failed, latest.json is intact: {e})")
    write_json_atomic(READLOG, readlog, indent=1)
    try:
        write_report(rows, verdicts, new_keys, closed, seen_ledger, coverage, gates,
                     changed_jds=changed_jds, digest=digest, prev=prev, reposts=reposts, health=health,
                     prev_verdicts=snap_prev.get("verdicts", {}))
    except Exception as e:
        print(f"\nREPORT NOT WRITTEN ({type(e).__name__}: {e}). Every read is saved in data/latest.json; "
              "rebuild the report with: python sweep.py --report-only   (do NOT use --resume)")
        raise
    try:
        n, mb = compress_old_snapshots()
        if n: print(f"compressed {n} raw dated snapshot(s), {mb:.0f} MB freed")
    except OSError as e:
        print(f"(snapshot compression skipped: {e})")

# ----------------------------------------------------------------------------- reposts / core hash
def repost_fingerprint(r):
    """Same employer, same normalized title, same first location segment."""
    title = re.sub(r"[^a-z0-9 ]+", " ", (r.get("title") or "").lower())
    title = re.sub(r"\s+", " ", title).strip()
    loc = re.split(r"[|;\n]", r.get("location") or "")[0].strip().lower()
    return f"{r.get('company','').lower()}|{title}|{loc}"

def _repost_fps_path():                  # fingerprint -> [key, last_seen] for gate-relevant reqs
    return DATA / "repost_fps.json"

REPOST_FP_DAYS = 120

def detect_gap_reposts(rows, new_keys, found, fps):
    """new_key -> old_key for a repost whose old id closed more than one snapshot ago. detect_reposts
    only looks one snapshot back, so a req pulled on Monday and reposted on Thursday would read as
    fresh. The fingerprint ledger remembers gate-relevant reqs for REPOST_FP_DAYS."""
    out = {}
    for k in new_keys:
        if k in found: continue
        e = fps.get(repost_fingerprint(rows[k]))
        if e and e[0] != k and e[0] not in rows: out[k] = e[0]
    return out

def update_repost_fps(fps, rows, verdicts):
    """Record today's gate-relevant reqs (anything but FAIL) and drop entries unseen for REPOST_FP_DAYS.
    The latest open key is kept: a sibling that is still open is never mistaken for a closed original."""
    today = TODAY.isoformat()
    for k, r in rows.items():
        if verdicts.get(k, ("FAIL",))[0] == "FAIL": continue
        fps[repost_fingerprint(r)] = [k, today]
    return {fp: e for fp, e in fps.items() if (days_since(e[1]) or 0) <= REPOST_FP_DAYS}

def detect_reposts(rows, prev, new_keys):
    """new_key -> old_key for every new req whose fingerprint matches a previously seen req
    with a different id. prev includes reqs that closed this run, which is the usual shape:
    the old id disappears and the new one appears on the same day."""
    by_fp = {}
    for k, r in prev.items():
        by_fp.setdefault(repost_fingerprint(r), []).append(k)
    def body_hash(r):
        return r.get("jd_hash") or (jd_hash(r["description"]) if r.get("description") else None)
    out = {}
    for k in new_keys:
        for old in by_fp.get(repost_fingerprint(rows[k]), []):
            if old == k:
                continue
            # A generic title ("Principal Product Manager" in one city) matches unrelated reqs. When
            # the old id is still open and both bodies are known and differ, it is a sibling req,
            # not a repost.
            if old in rows:
                a, b = body_hash(rows[k]), body_hash(rows[old])
                if a and b and a != b:
                    continue
            out[k] = old; break
    return out

def boilerplate_by_company(rows, min_share=0.30, min_rows=3):
    """company -> the lines shared by >= min_share of that employer's reqs that still hold text (and
    by at least min_rows of them). An employer with fewer than min_rows such reqs has none."""
    by_co = {}
    for r in rows.values():
        if r.get("description"): by_co.setdefault(r["company"], []).append(r)
    out = {}
    for co, rs in by_co.items():
        if len(rs) < min_rows: continue
        freq = {}
        for r in rs:
            for line in {ln.strip() for ln in r["description"].split("\n") if len(ln.strip()) > 40}:
                freq[line] = freq.get(line, 0) + 1
        out[co] = {ln for ln, n in freq.items() if n >= min_rows and n / len(rs) >= min_share}
    return out

def core_hash(desc, boiler):
    return jd_hash("\n".join(ln for ln in (desc or "").split("\n") if ln.strip() not in boiler))

def core_hashes(rows, min_share=0.30, min_rows=3):
    """Set r['jd_core_hash'] = hash of the description minus this run's boilerplate for its employer.
    Rows with no text get none. A within-run fingerprint only: run() never compares it across runs."""
    boil = boilerplate_by_company(rows, min_share, min_rows)
    for r in rows.values():
        if not r.get("description"): continue
        b = boil.get(r["company"])
        r["jd_core_hash"] = core_hash(r["description"], b) if b is not None else (r.get("jd_hash") or jd_hash(r["description"]))

# ----------------------------------------------------------------------------- snapshot size
# Boards that return full JD text at list level (Google, iCIMS, Greenhouse, Lever, Comeet ...) would
# store it for every req, and most reqs fail the TITLE gate and are never read again, so the
# snapshot would run to hundreds of MB. Text is kept wherever it can matter: any verdict other than a title/domain
# FAIL (a location FAIL is kept, since a market can be approved and re-gated from disk), and every
# scored req. jd_hash / jd_core_hash stay, so change detection is unaffected; a later widening of the
# title list is picked up on the next live read, which returns the text again.
STRIP_TEXT_REASONS = ("title", "avoid-domain", "engineering title")

def strip_irrelevant_text(rows, verdicts):
    scored = set(load_scored())
    n = 0
    for k, r in rows.items():
        v, why = verdicts.get(k, ("PASS", []))
        if v != "FAIL" or k in scored or not r.get("description"):
            continue
        if why and why[0].startswith(STRIP_TEXT_REASONS):
            r["description"] = ""
            r["_text_stripped"] = True
            n += 1
    return n

# ----------------------------------------------------------------------------- carry-forward / health
CARRY_MAX_DAYS = 14
CARRY_ERROR = "employer not read cleanly this run"
CARRY_SAMPLE = "query-scoped board; absence from a sample is not closure"

CARRY_SCORED = "scored req missing from a query-scoped sample; confirmed open by id lookup"
# A Workday board read under the US+Canada scope. A req missing from that read is abroad or
# closed, and a list-level "2 Locations" row cannot say which, so it is carried like a sampled board's
# and closes after CARRY_MAX_DAYS. On the first scoped run this can be a large share of a tenant's
# rows, by design: the carried-forward line names the count so it is not read as a defect.
CARRY_SCOPE = "country-scoped read; a req outside the US and Canada is not returned"

def recover_scored(rows, coverage, targets, scored):
    """Every scored req on a QUERY-SCOPED board that this run read cleanly but did not return is
    looked up by id. Open: the live row goes back in (no expiry; it was just read). Closed: its key
    is returned so carry-forward does not keep it alive for 14 more days. Lookup failure: left to
    carry-forward. Mutates rows; returns (recovered keys, confirmed-closed keys, failures)."""
    status = {c: s for c, a, s, n in coverage}
    by_co = {t["company"]: t for t in targets}
    recovered, closed, failed = [], set(), []
    for k in sorted(scored):
        if k in rows: continue
        parts = k.split(":", 2)
        if len(parts) != 3: continue
        ats, co, jid = parts
        t = by_co.get(co)
        if not t or t["ats"] != ats or ats in ENUMERABLE_ATS or ats not in LOOKUP: continue
        if not status.get(co, "").startswith(("OK", "CACHED")): continue
        try:
            r = LOOKUP[ats](t, jid)
        except Exception as e:
            failed.append((k, f"{type(e).__name__}: {e}")); continue
        if r is None:
            closed.add(k); continue
        r["_carried_reason"] = CARRY_SCORED
        r["_target"] = t
        rows[r["key"]] = r
        recovered.append(r["key"])
    return recovered, closed, failed

def print_scored_lookup(recovered, closed, failed, rows, indent=""):
    if not (recovered or closed or failed): return
    print(f"{indent}scored-req lookup   : {len(recovered)} recovered (open, missing from the sample), "
          f"{len(closed)} confirmed closed, {len(failed)} lookup failed")
    for k in recovered:
        print(f"{indent}  RECOVERED " + link(f"{rows[k]['company']} · {rows[k]['title']}", rows[k]["url"]))
    for k in sorted(closed):
        print(f"{indent}  CLOSED    {k}")
    for k, e in failed:
        print(f"{indent}  LOOKUP FAILED {k}: {e[:120]}")

CARRY_DROP = "board count fell 50%+ against its last clean read; suspected truncated read"
CARRY_DROP_DAYS = 3        # long enough to ride out a short read, short enough that a real cut closes

def count_drops(coverage, prev_counts, prev_scopes=None):
    """[(company, previous count, count now)] for clean, complete reads that came back under half.

    `prev_scopes` is the read log's scope per employer. A board read under a country scope for the
    first time (Workday) is smaller by design: that is a scope change, not a truncated read, so it
    is neither warned about nor carried for 3 days; the out-of-scope reqs close. Once both reads carry the scope, a drop means what it always did."""
    out = []
    for c, a, s, n in coverage:
        if not (s.startswith("OK") and "incremental" not in s): continue
        if (prev_counts.get(c) or 0) < 20 or n >= 0.5 * prev_counts[c]: continue
        if WORKDAY_SCOPE_MARK in s and not (prev_scopes or {}).get(c): continue
        out.append((c, prev_counts[c], n))
    return out

def unchanged_rows(prev, t):
    """The last snapshot's rows a 304 vouches for: this employer's rows on this board, minus rows
    carried for any reason other than an errored read. CARRY_ERROR rows ARE included: they are the
    last clean read's rows, kept through the failures since, and the 304 says that read still
    stands. A CARRY_DROP row was already missing from the read the tag came from, so the 304 says
    nothing about it; carry_forward keeps its hedge and its clock running (the 304 branch there).
    Known corner: a short read, then an errored read, then a 304 re-stamps the DROP rows too,
    because the errored run rewrote their reason to CARRY_ERROR."""
    out = {}
    for k, r in prev.items():
        if r.get("company") != t["company"] or r.get("ats") != t["ats"]: continue
        if r.get("_carried_reason") not in (None, CARRY_ERROR): continue
        r = dict(r); r.pop("_carried_since", None); r.pop("_carried_reason", None)
        out[k] = r
    return out

def closes_on_complete_walk(p, scope, prev_scope):
    """True when a Workday walk that reached its own page-1 total proves this missing req closed.

    Workday is not in ENUMERABLE_ATS because a walk can come back short, but a complete unscoped
    walk is as good as an enumerable board; without this, closed reqs would stay listed for up to
    CARRY_MAX_DAYS. A complete US+CA walk proves closure only for a
    row the last scoped read returned live: that row was in scope, so its absence is not "abroad".
    A target's explicit facets never close (the facet set can change under it)."""
    if scope is None: return True
    if scope == WORKDAY_SCOPE_LABEL:
        return prev_scope == WORKDAY_SCOPE_LABEL and not p.get("_carried_reason")
    return False

def carry_forward(rows, prev, coverage, targets, only=None, confirmed_closed=(), dropped=(),
                  complete_walks=None, prev_scopes=None):
    """Put back previous rows whose absence proves nothing. Mutates rows; returns {key: reason}.

    `dropped` names employers whose clean read came back under half its last count. Closing every
    missing req there would re-announce them all as newly discovered when the board reads whole
    again (40 -> 10 -> 40 would close 30 and re-list 30). A short read is not evidence of closure
    for CARRY_DROP_DAYS."""
    status = {c: s for c, a, s, n in coverage}
    ats_of = {t["company"]: t["ats"] for t in targets}
    out = {}
    for k, p in prev.items():
        if k in rows or k in confirmed_closed: continue
        co = p.get("company")
        if co not in ats_of or p.get("ats") != ats_of[co]:
            continue                      # employer dropped or moved boards: its old reqs close
        if only and only.lower() not in (co or "").lower():
            continue                      # a --only run reads one employer; the rest were never asked for
        s = status.get(co, "")
        if not (s.startswith("OK") or s.startswith("CACHED")):
            reason = CARRY_ERROR
        elif "304 unchanged" in s and p.get("_carried_reason"):
            # A 304 vouches for the read its tag came from. A row that read had already missed
            # (unchanged_rows left it out) keeps the hedge and the clock it already had.
            reason = p["_carried_reason"]
        elif co in dropped:
            reason = CARRY_DROP
        elif "incremental" in s:
            reason = CARRY_INCREMENTAL
        elif co in (complete_walks or {}) and closes_on_complete_walk(
                p, complete_walks[co], (prev_scopes or {}).get(co)):
            continue                      # Workday walked to its own total: absence means closed
        elif WORKDAY_SCOPE_MARK in s:
            reason = CARRY_SCOPE
        elif ats_of[co] not in ENUMERABLE_ATS:
            reason = CARRY_SAMPLE
        else:
            continue                      # enumerable board read cleanly: absence means closed
        since = p.get("_carried_since") or TODAY.isoformat()
        if (days_since(since) or 0) > (CARRY_DROP_DAYS if reason == CARRY_DROP else CARRY_MAX_DAYS):
            continue
        r = dict(p); r["_carried_since"] = since; r["_carried_reason"] = reason
        rows[k] = r; out[k] = reason
    return out

# A dead board and a moved board look identical from the outside: both stop answering. The
# difference is the record. Workday tenants do move shards (wd5 -> wd115, say), and the old host
# then answers 404/410/422, which is easy to misread as a bot wall. A 4xx of that shape on an
# employer with a prior clean read is named here so it prompts a re-resolve.
MIGRATION_CODES = ("404", "410", "422")
MIGRATION_STATUS_RE = re.compile(r"(?:HTTPError|HTTP|status(?:\s+code)?)\D{0,3}(" + "|".join(MIGRATION_CODES) + r")\b",
                                 re.I)

def migration_suspects(coverage, prev_counts):
    """-> [(company, status)] for employers whose ERROR is a 404/410/422 and that have a stored
    count from an earlier clean read. The status is truncated at ' for url' before matching, so a
    host or path that happens to contain 404/410/422 cannot invent one."""
    out = []
    for c, _a, s, _n in coverage:
        if not s.startswith("ERROR"): continue
        if not MIGRATION_STATUS_RE.search(s.split(" for url", 1)[0]): continue
        if prev_counts.get(c): out.append((c, s))
    return out

def workday_total_gaps(totals=None):
    """-> [(company, reported, read)] for Workday boards that came back short of their own page-1
    total. Nothing here changes what is read; a short walk is reported, never patched over."""
    out = []
    for c, d in sorted((totals if totals is not None else WORKDAY_TOTALS).items()):
        reported, read = d.get("reported") or 0, d.get("read") or 0
        if reported <= 0: continue
        if read < WORKDAY_GAP_FRACTION * reported and reported - read >= WORKDAY_GAP_MIN_ROWS:
            out.append((c, reported, read))
    return out

def sweep_health(rows, verdicts, new_keys, coverage, prev_counts, snap_prev, dstats, n_live, prev_scopes=None):
    """The funnel and the anomaly checks, on every run. The worst sweep failures look like quiet
    market days: cache gating, a truthiness bug that passes every location, a filter that drops
    most qualifying roles. Each shows up here as a number that moved."""
    from collections import Counter
    vc = Counter(v for v, _ in verdicts.values())
    reasons = Counter((why[0].split(":")[0] if why else "unspecified")
                      for v, why in verdicts.values() if v == "FAIL")
    errors = [(c, s) for c, a, s, n in coverage if not (s.startswith("OK") or s.startswith("CACHED"))]
    drops = count_drops(coverage, prev_counts, prev_scopes)
    new_pass = sum(1 for k in new_keys if verdicts[k][0] == "PASS")
    prev_pass = sum(1 for v in (snap_prev.get("verdicts") or {}).values() if v == "PASS")
    carried = sum(1 for r in rows.values() if r.get("_carried_since"))
    migrated = migration_suspects(coverage, prev_counts)
    wd_gaps = workday_total_gaps()
    w = []
    if errors:
        w.append(f"{len(errors)} employer(s) UNCOVERED this run: " + ", ".join(c for c, _ in errors))
    for c, s in migrated:
        w.append(f"{c}: {s} on a board that read cleanly before; the tenant may have migrated hosts, "
                 f"re-resolve from a live job URL")
    for c, reported, read in wd_gaps:
        w.append(f"{c}: Workday reported {reported}, read {read}")
    for c, p, n in drops:
        w.append(f"{c} returned {n} reqs against {p} on its last read (-{100 - 100 * n // p}%): "
                 f"possible truncation or a changed endpoint, confirm before trusting 'closed'")
    if prev_pass >= 50 and vc["PASS"] < 0.6 * prev_pass:
        w.append(f"gate-passing reqs fell to {vc['PASS']} from {prev_pass} in the previous snapshot: "
                 f"suspect a gate or parser regression before a quiet market")
    since_prev = hours_since(snap_prev.get("ts"))
    if n_live >= 100 and diff_is_meaningful() and new_pass < 3 and (since_prev is None or since_prev >= 12):
        w.append(f"only {new_pass} new gate-passing req(s) across {n_live} live employers: check the "
                 f"funnel below for a filter or cache defect before reporting a quiet day")
    attempts = dstats.get("fetched", 0) + dstats.get("failed", 0)
    if attempts >= 10 and dstats.get("failed", 0) > 0.1 * attempts:
        w.append(f"detail pass failed on {dstats['failed']} of {attempts} fetches: JD text, dates and "
                 f"bands are missing for those reqs")
    return {"rows": len(rows), "carried": carried, "employers_live": n_live, "verdicts": dict(vc),
            "fail_reasons": dict(reasons.most_common()), "new": len(new_keys), "new_pass": new_pass,
            "prev_pass": prev_pass, "errors": errors, "count_drops": drops, "detail": dstats,
            "migrated": migrated, "workday_gaps": wd_gaps, "warnings": w}

def health_lines(h):
    v = h["verdicts"]
    funnel = (f"{h['rows']} reqs ({h['carried']} carried forward) from {h['employers_live']} live employer(s) -> "
              + ", ".join(f"{n} {r}" for r, n in h["fail_reasons"].items())
              + f" -> {v.get('LOCATION-POLICY', 0)} LOCATION-POLICY, {v.get('TENURE', 0)} TENURE, "
              f"{v.get('UNCOVERED', 0)} UNCOVERED, {v.get('REVIEW', 0)} REVIEW (body-rescued) -> **{v.get('PASS', 0)} PASS** "
              f"({h['new_pass']} new; previous snapshot {h['prev_pass']})")
    out = [f"Funnel: {funnel}"]
    out += [f"WARNING: {x}" for x in h["warnings"]] or ["No anomalies: every employer read, no count drops, pass volume in line."]
    out += list(h.get("notes") or [])
    return out

def print_health(h):
    print("\nSWEEP HEALTH")
    for line in health_lines(h): print("  " + line.replace("**", ""))
    print()

def report_only(gates, digest=False):
    """Rebuild the report from the last snapshot. No network. Use after a write_report crash."""
    snap = load_json(DATA / "latest.json", None)
    if not snap: sys.exit("no data/latest.json to rebuild from")
    banner = stale_banner(snap.get("ts"))
    if banner: print(banner + "\n")
    rows = snap["rows"]
    # Re-gate from the CURRENT gates.json rather than replaying stored verdicts, so gate edits
    # can be evaluated against a snapshot with no re-fetching. Descriptions are already stored,
    # so the years and never-claim checks run at full strength.
    verdicts = {k: gate_final(r, gates) for k, r in rows.items()}
    before = snap.get("verdicts", {})
    moved = sum(1 for k in verdicts if before.get(k) != verdicts[k][0])
    print(f"re-gated {len(rows)} rows against current gates.json: {moved} verdict(s) changed")
    ledger = load_json(DATA / "seen.json", {})
    cov = [tuple(c) for c in snap.get("coverage", [])]
    if not cov:
        print("warning: snapshot predates coverage capture; coverage table will be derived from rows "
              "and cannot show targets that errored")
        per = {}
        for r in rows.values(): per[(r["company"], r["ats"])] = per.get((r["company"], r["ats"]), 0) + 1
        cov = [(c, a, "OK (derived, not recorded)", n) for (c, a), n in sorted(per.items())]
    write_report(rows, verdicts, snap.get("new_keys", list(rows)), snap.get("closed", {}), ledger, cov, gates,
                 changed_jds=snap.get("changed_jds", []), digest=digest, prev={}, reposts=snap.get("reposts", {}),
                 health=snap.get("health"), prev_verdicts=dict(before, **(snap.get("flipped") or {})))

def print_coverage(cov):
    for c, a, s, n in cov: print(f"{c:28} {a:15} {n:5}  {s}")

def age_days(k, ledger):
    return days_since(ledger.get(k))

def freshness_line(r, k, ledger, g):
    """BOTH dates, every time, with the primary signal named.

    Once consecutive runs exist, first_seen from the diff is primary: a req with a
    stale board date that turns up in today's diff genuinely just went up. Before
    that, first_seen is worthless (the first run stamps every key with the same day) and
    the board date is all there is. Atlassian-class boards publish a refresh date
    only, and that is labelled as such rather than being read as a posting date.
    """
    extra = r.get("extra") or {}
    refreshed = extra.get("refreshed") if extra.get("date_kind") == "refreshed" else None
    posted = r.get("posted")
    first_seen = ledger.get(k)
    board = (f"posted {posted}" if posted else
             (f"refresh date only, age unknown ({refreshed})" if refreshed else "board date not published"))
    seen_part = f"first seen {first_seen}" if first_seen else "first seen unrecorded"
    if diff_is_meaningful():
        age = days_since(first_seen)
        primary = f"{seen_part} [PRIMARY]"
        tag = "FRESH" if (age is not None and age <= g["fresh_window_days"]) else (f"{age}d in ledger" if age is not None else "age unknown")
    else:
        age = days_since(posted)
        primary = f"{board} [PRIMARY]"
        seen_part += " (run-one stamp, not a signal)"
        tag = ("FRESH" if (age is not None and age <= g["fresh_window_days"]) else
               (f"{age}d old" if age is not None else
                ("refresh date only, age unknown" if refreshed else "age unknown")))
    # The secondary date is always the OTHER one, even when only one snapshot exists.
    other = board if diff_is_meaningful() else seen_part
    return f"{primary} · then {other} · {tag}"

def became(rows, verdicts, new_keys, prev_verdicts, labels):
    """Keys that are NOT new discoveries but whose verdict moved INTO one of `labels` this run.

    Keyed on new_keys alone, sections 1 and 2 would miss a req that became eligible through
    anything other than discovery: a gate edit, a tenure-regex fix, a location that arrived on a
    later detail read, an endorsed-market change. It would drop straight into section 4's long
    aging list, which is not a place anyone reads.

    A key absent from prev_verdicts is never counted. On the first run after any schema or snapshot
    change every key looks like a flip, and a few hundred false ones would bury the real signal."""
    if not prev_verdicts: return []
    newk = set(new_keys)
    return sorted(k for k in rows
                  if k not in newk and verdicts[k][0] in labels
                  and prev_verdicts.get(k) is not None and prev_verdicts[k] != verdicts[k][0])

CONSOLE_PASS_MAX = 25   # new PASS reqs echoed to the terminal; the report file always has all of them

def write_report(rows, verdicts, new_keys, closed, ledger, coverage, g, reports_dir=None, jds_dir=None,
                 changed_jds=None, digest=False, prev=None, reposts=None, health=None,
                 prev_verdicts=None):
    # --selftest passes a temp dir for both so synthetic data can never overwrite a live report
    # or a real JD file. Everything else uses the project dirs.
    reports_dir = reports_dir or REPORTS
    # JDs go in a dated subfolder, so a JD for a req that has since dropped out of the report
    # never sits next to current ones, indistinguishable.
    jds_dir = jds_dir or (JDS / TODAY.isoformat())
    jds_dir.mkdir(parents=True, exist_ok=True)
    changed_jds = changed_jds or []
    prev = prev or {}
    prev_verdicts = prev_verdicts or {}
    reposts = reposts or {}
    scored = load_scored()
    L = [f"# Sweep report, {TODAY.isoformat()}", "",
         "Discovery by script against first-party ATS JSON endpoints. "
         + (f"Consecutive snapshots exist ({snapshot_count()}), so **first_seen from the diff is the primary "
            "freshness signal** and the board's posted date is secondary: a req with a stale board date that "
            "appears in today's diff genuinely just went up."
            if diff_is_meaningful() else
            "**Only one snapshot exists, so first_seen is NOT yet a signal** (run one stamped every key to the "
            "same date). Freshness below falls back to board posted dates.")
         + f" Window {g['fresh_window_days']} days; evergreen after {g['evergreen_days']} days.", ""]
    if health:
        L += ["## Sweep health", ""] + [f"- {x}" for x in health_lines(health)] + [""]
    L += ["## 0. Changes to watch", "",
         "Reqs whose posting text changed since the last read are listed here.", ""]
    if changed_jds:
        L += ["### JD text changed since the last snapshot", "",
              "A quietly rewritten req (raised tenure bar, narrowed scope) looks unchanged without this.", ""]
        for k in changed_jds:
            r = rows[k]
            tail = f" **[ALREADY SCORED {scored[k]['score']} on {scored[k]['scored_on']} — RESCORE]**" if k in scored else ""
            L.append(f"- [{r['company']} · {r['title']}]({r['url']}){tail}")
        L.append("")
    L += ["## 1. New or newly eligible, gate-passing", ""]
    fresh_pass = [k for k in new_keys if verdicts[k][0] == "PASS"]
    flipped = became(rows, verdicts, new_keys, prev_verdicts, ("PASS",))
    passing = fresh_pass + flipped
    why_now = {k: "newly discovered" for k in fresh_pass}
    why_now.update({k: f"newly eligible: {prev_verdicts.get(k)} -> PASS since the last run" for k in flipped})
    if not passing: L.append("None this run.")
    digest_lines = []
    for k in sorted(passing, key=lambda k: rows[k]["company"]):
        r = rows[k]; why = verdicts[k][1]
        # A board that only publishes a refresh date must never be read as a posting date.
        refreshed = (r.get("extra") or {}).get("refreshed") if (r.get("extra") or {}).get("date_kind") == "refreshed" else None
        posted = r.get("posted") or refreshed or "not published"
        date_line = "Refreshed" if (refreshed and not r.get("posted")) else "Posted"
        risks = risk_signals(r, ledger, prev.get(k), repost_of=reposts.get(k))
        conv_label, conv = conversion_read(r, g, ledger)
        # The title itself is the link in report files too, as normal markdown.
        L += [f"### [{r['company']} · {r['title']}]({r['url']})",
              f"- Location: {r['location'] or 'not stated'}",
              f"- Dates: {freshness_line(r, k, ledger, g)}",
              f"- {date_line} (board): {posted}",
              f"- Comp: {r.get('comp') or 'not posted'}", f"- ATS id: `{k}`",
              f"- Conversion read: **{conv_label}** · " + " · ".join(conv),
              f"- Why now: {why_now[k]}"]
        if k in reposts:
            L.append(f"- REPOST of `{reposts[k]}` (first seen {ledger.get(reposts[k], '?')}); age above is the new id's, not the posting's")
        if k in scored:
            s = scored[k]
            L.append(f"- ALREADY SCORED: {s['score']} ({s.get('verdict','?')}) on {s['scored_on']}"
                     + (", resume built" if s.get("built") else ", not built"))
        if risks: L.append("- Posting risk: " + "; ".join(risks))
        if why: L.append("- Flags: " + "; ".join(why))
        digest_lines.append(f"- **[{r['company']} · {r['title']}]({r['url']})** — {r['location'] or 'location not stated'}"
                            f" · {r.get('comp') or 'comp not posted'}"
                            + (f" · risk: {', '.join(risks)}" if risks else ""))
        jd_path = jds_dir / (jd_stem(k) + ".md")
        jd_path.write_text(f"# {r['company']} · {r['title']}\n{r['url']}\nLocation: {r['location']}\n"
                           f"{date_line}: {posted}\nComp: {r.get('comp') or 'not posted'}\n\n{r['description']}",
                           encoding="utf-8")
        rel = jd_path.relative_to(ROOT) if jd_path.is_relative_to(ROOT) else jd_path
        L += [f"- JD text: `{rel}`", ""]
    L += ["## 2. New or newly flagged (for a person to review)", ""]
    flagged_labels = ("REVIEW", "TENURE", "LOCATION-POLICY", "UNCOVERED")
    flipped_flagged = set(became(rows, verdicts, new_keys, prev_verdicts, flagged_labels))
    for label in flagged_labels:
        ks = [k for k in new_keys if verdicts[k][0] == label]
        ks += [k for k in flipped_flagged if verdicts[k][0] == label]
        for k in ks:
            r = rows[k]
            moved = f" · **{prev_verdicts.get(k)} -> {label} since the last run**" if k in flipped_flagged else ""
            L.append(f"- **{label}** [{r['company']} · {r['title']}]({r['url']}) · {r['location']}{moved} · "
                     + "; ".join(verdicts[k][1]))
    L += ["", "## 3. Closed since last run (previously gate-passing)", ""]
    cl = [(k, v) for k, v in closed.items() if gate(v, g)[0] == "PASS"]
    L += [f"- {v['company']} · {v['title']} · first seen {ledger.get(k, '?')} · {v['url']}" for k, v in cl] or ["None."]
    L += ["", "## 4. Still open, gate-passing (aging)", ""]
    reported = set(new_keys) | set(flipped)          # section 1 already carried these in full
    for k in sorted((k for k in rows if k not in reported and verdicts[k][0] == "PASS"), key=lambda k: ledger.get(k, "")):
        r = rows[k]; a = age_days(k, ledger)
        tag = "EVERGREEN" if a is not None and a >= g["evergreen_days"] else f"{a}d"
        carried = (f" · CARRIED since {r['_carried_since']} ({r.get('_carried_reason')})"
                   if r.get("_carried_since") else "")
        L.append(f"- {tag:9} {r['company']} · {r['title']} · {r['location']} · {r['url']}{carried}")
    if L[-1] == "": L.append("None.")
    L += ["", "## 5. Coverage", "", "| Company | ATS | Reqs read | Status |", "|---|---|---|---|"]
    # Company links to its board where a row from that employer gives us a URL to derive it from.
    board_url = {}
    for r in rows.values():
        if r.get("url") and r["company"] not in board_url:
            m = re.match(r"(https?://[^/]+)", r["url"])
            if m: board_url[r["company"]] = m.group(1)
    L += [f"| {('[' + c + '](' + board_url[c] + ')') if c in board_url else c} | {a} | {n} | {s} |"
          for c, a, s, n in coverage]
    fails = {}
    for k in new_keys:
        v, why = verdicts[k]
        if v == "FAIL":
            reason = why[0].split(":")[0] if why else "unspecified"
            fails[reason] = fails.get(reason, 0) + 1
    L += ["", f"New reqs gated out this run: {sum(fails.values())} " + ", ".join(f"{k} {v}" for k, v in fails.items())]
    out = reports_dir / f"SWEEP_REPORT_{TODAY.isoformat()}.md"
    out.write_text("\n".join(L), encoding="utf-8"); print(f"wrote {out}")
    if digest:
        d = [f"# Shortlist, {TODAY.isoformat()}", "",
             ("Freshness: first_seen (diff) is primary." if diff_is_meaningful()
              else "Freshness: board dates only, one snapshot so far."), ""]
        if health:
            d += [f"- {x}" for x in health_lines(health)] + [""]
        d += digest_lines or ["Nothing new and gate-passing this run."]
        dp = reports_dir / f"digest_{TODAY.isoformat()}.md"
        dp.write_text("\n".join(d), encoding="utf-8"); print(f"wrote {dp}")

    # Console echo with clickable titles. The report file keeps markdown links; this
    # is the terminal view, where OSC 8 makes the title itself clickable.
    if passing:
        print(f"\nNew and gate-passing ({len(passing)}):")
        # The console shows the first CONSOLE_PASS_MAX; the report file written above lists every one.
        for k in sorted(passing, key=lambda k: rows[k]["company"])[:CONSOLE_PASS_MAX]:
            r = rows[k]
            risks = risk_signals(r, ledger, prev.get(k))
            print("  " + link(f"{r['company']} · {r['title']}", r["url"])
                  + (f"   [risk: {', '.join(risks)}]" if risks else ""))
        if len(passing) > CONSOLE_PASS_MAX:
            print(f"  ... {len(passing) - CONSOLE_PASS_MAX} more in the report ({out.name})")

# ----------------------------------------------------------------------------- window query
def show_comp(comp):
    """A comp field with no dollar figure in it is not a band.

    Atlassian's compensation field is a boilerplate paragraph about pay zones.
    money_range() refuses to extract a band from it, but older snapshot rows may
    still carry the paragraph verbatim, and printing it puts legal text where the
    band belongs. Guard at display time so bad stored data cannot masquerade as a
    posted band.
    """
    if not comp: return "not posted"
    c = re.sub(r"\s+", " ", str(comp)).strip()
    if not re.search(r"\$\s?[\d,]", c):
        return "not posted (board returned boilerplate, no figures)"
    return c[:60] + ("..." if len(c) > 60 else "")

def parse_window(s):
    """'24h' / '48h' / '7d' / '14d' -> hours. Default 48h."""
    if not s: return 48
    m = re.fullmatch(r"(\d+)\s*([hd])", str(s).strip().lower())
    if not m: return None
    n = int(m.group(1))
    return n if m.group(2) == "h" else n * 24

def window_query(gates, window="48h", company=None, floor=None, show_all=False):
    """The candidate set behind the shortlist: everything gate-passing inside the window.

    Prints both dates for every req, names which one is primary, carries prior
    scores forward so standing targets are not re-litigated, and reports what a
    floor filtered rather than dropping it silently.
    """
    snap = load_json(DATA / "latest.json", None)
    if not snap: sys.exit("no data/latest.json; run a sweep first")
    banner = stale_banner(snap.get("ts"), named=company)
    if banner: print(banner + "\n")
    hours = parse_window(window)
    if hours is None: sys.exit(f"unparseable window: {window!r} (use 24h, 48h, 7d, 14d)")
    rows = snap["rows"]; ledger = load_json(DATA / "seen.json", {}); scored = load_scored()
    reposts = snap.get("reposts", {}) or {}
    prev_hashes = {k: r.get("jd_hash") for k, r in rows.items()}
    cutoff_days = hours / 24.0
    primary = "first_seen (diff)" if diff_is_meaningful() else "board posted date"
    print(f"WINDOW {window} ({hours}h) · primary freshness signal: {primary}"
          + (f" · company filter: {company}" if company else "")
          + (f" · score floor: {floor}" if floor else ""))
    if not diff_is_meaningful():
        print("NOTE: only one snapshot exists, so first_seen is a run-one stamp and NOT a signal. "
              "Board dates are doing the work below.")
    # First-coverage stamps. An employer's earliest first_seen is the day its board was first
    # read, and every req stamped that day is coverage, not a posting; adding a board would
    # otherwise flood the window. For those rows the board date decides; with no board date
    # they go to the ageless bucket.
    first_cov = {}
    for k, r in rows.items():
        d = ledger.get(k)
        if d and (r["company"] not in first_cov or d < first_cov[r["company"]]): first_cov[r["company"]] = d
    print()
    hits, ageless, stamped = [], [], 0
    for k, r in rows.items():
        if company and company.lower() not in r["company"].lower(): continue
        v, why = gate_final(r, gates)
        if v not in ("PASS", "TENURE", "LOCATION-POLICY", "REVIEW"): continue
        age_seen = days_since(ledger.get(k))
        age_post = days_since(r.get("posted"))
        is_stamp = ledger.get(k) is not None and ledger.get(k) == first_cov.get(r["company"])
        # The age that counts is the PRIMARY signal's age, and nothing else. Falling
        # back to first_seen while only one snapshot exists would let a run-one stamp
        # decide the window, which is precisely the signal that means nothing yet. A
        # first-coverage stamp is the same non-signal one employer at a time.
        if k in reposts:
            older = days_since(ledger.get(reposts[k]))
            if older is not None: age_seen = max(age_seen or 0, older)
        age = age_seen if (diff_is_meaningful() and not is_stamp) else age_post
        if is_stamp: stamped += 1
        if age is None:
            ageless.append((k, r, v, why))       # unknown age, never silently "fresh"
            continue
        if age > cutoff_days and not show_all: continue
        hits.append((k, r, v, why, age_post, age_seen))
    if stamped:
        print(f"({stamped} gate-relevant req(s) carry a first-coverage stamp; board date decided their age)\n")
    if not hits:
        print(f"NOTHING IN THE {window} WINDOW.")
        print(f"\nOpen and unscored from the last 14 days instead:")
        alt = []
        for k, r in rows.items():
            if company and company.lower() not in r["company"].lower(): continue
            if k in scored: continue
            if gate_final(r, gates)[0] != "PASS": continue
            a = days_since(r.get("posted")) or days_since(ledger.get(k))
            if a is not None and a <= 14: alt.append((a, k, r))
        for a, k, r in sorted(alt)[:15]:
            print(f"  {a:3}d  " + link(f"{r['company']} · {r['title']}", r["url"]))
        if not alt: print("  (nothing open and unscored in 14 days either)")
        return
    # Pre-rank: a lane hint for the UNSCORED reqs only, and only as a sort order inside each
    # freshness group. A scored req keeps its score, which supersedes any hint. Nothing is filtered:
    # the candidate list is the same list it was, in a different order.
    applied_keys, applied_urls = applied_index()
    jds = jd_index()
    pre = {k: prerank(r, gates, jd_text_for(k, jds)) for k, r, *_ in hits} if gates.get("prerank") else {}
    if pre:
        print("pre = lane hint for unscored reqs (checked against past scores); a sort order, not a verdict.\n")
    print(f"{len(hits)} candidate(s) in window:\n")
    for k, r, v, why, age_post, age_seen in sorted(
            hits, key=lambda x: (x[4] if x[4] is not None else 999,
                                 0 if x[0] in scored else 1,
                                 -(pre.get(x[0]) or 0))):
        risks = risk_signals(r, ledger, None, repost_of=reposts.get(k))
        conv_label, conv = conversion_read(r, gates, ledger)
        print(link(f"{r['company']} · {r['title']}", r["url"]))
        print(f"    verdict={v}  loc={(r['location'] or 'not stated')[:110]}  comp={show_comp(r.get('comp'))}")
        print(f"    dates: {freshness_line(r, k, ledger, gates)}")
        print(f"    conversion: {conv_label} · " + " · ".join(conv))
        print(f"    key={k}")
        if r.get("_carried_since"):
            # A carried row must not look like a req read live this run.
            print(f"    CARRIED since {r['_carried_since']}: NOT confirmed open this run ({r.get('_carried_reason') or 'reason not recorded'}). "
                  "Open the link before building.")
        ap = applied_row(k, r, applied_keys, applied_urls)
        if ap:
            print(f"    *** ALREADY APPLIED {ap.get('date_applied') or 'date unknown'}"
                  + (f", status {ap['status']}" if ap.get("status") else "")
                  + (f", resume {Path(ap['resume_file']).name}" if ap.get("resume_file") else "")
                  + " - DO NOT BUILD AGAIN ***")
        if k not in scored and pre.get(k) is not None:
            print(f"    pre={pre[k]}")
        if k in scored:
            s = scored[k]
            note = "  [JD CHANGED SINCE SCORING - RESCORE]" if s.get("jd_hash") and s["jd_hash"] != prev_hashes.get(k) else ""
            band_note = "  [BAND RECOVERED SINCE SCORING - comp was unposted when scored]" if r.get("comp") and s.get("comp_at_scoring") is None and r["ats"] in DETAIL else ""
            print(f"    ALREADY SCORED {s['score']} ({s.get('verdict')}) on {s['scored_on']}"
                  + (", built" if s.get("built") else ", not built")
                  + (f", conversion {s['conversion']}" if s.get("conversion") else "") + note + band_note)
        if risks: print(f"    posting risk: {'; '.join(risks)}")
        if why: print(f"    flags: {'; '.join(why)}")
        print()
    if ageless:
        print(f"--- {len(ageless)} gate-passing req(s) with NO USABLE DATE, shown separately ---")
        print("    These are not 'in the window'. The board publishes no posted date, and first_seen is "
              "still a run-one stamp, so their age is genuinely unknown.\n" if not diff_is_meaningful()
              else "    These have no first_seen entry.\n")
        for k, r, v, why in sorted(ageless, key=lambda x: x[1]["company"])[:20]:
            print("    " + link(f"{r['company']} · {r['title']}", r["url"]) + f"  [{v}]")
        print()
    if floor:
        print(f"(Score floor {floor} applies to PRESENTATION only. Score every candidate above, then report "
              f"how many fell below the floor and the highest score among them.)")

# ----------------------------------------------------------------------------- yield report
def yield_report(gates, min_rows_noisy=500):
    """Which employers earn their read. Re-gates the snapshot and prints, per employer, rows read,
    gate-relevant reqs (PASS / LOCATION-POLICY / TENURE) and scored reqs. Nothing is dropped
    automatically: this is the list to trim from, or to scope with a category filter."""
    snap = load_json(DATA / "latest.json", None)
    if not snap: sys.exit("no data/latest.json; run a sweep first")
    scored = load_scored()
    per = {}
    for k, r in snap["rows"].items():
        e = per.setdefault(r["company"], {"ats": r["ats"], "rows": 0, "relevant": 0, "pass": 0, "scored": 0})
        e["rows"] += 1
        v = gate_final(r, gates)[0]
        if v in DETAIL_VERDICTS: e["relevant"] += 1
        if v == "PASS": e["pass"] += 1
        if k in scored: e["scored"] += 1
    zero = sorted(((c, e) for c, e in per.items() if e["relevant"] == 0 and e["scored"] == 0),
                  key=lambda x: -x[1]["rows"])
    noisy = sorted(((c, e) for c, e in per.items() if e["rows"] >= min_rows_noisy and e["relevant"] <= 2),
                   key=lambda x: -x[1]["rows"])
    top = sorted(per.items(), key=lambda x: (-x[1]["pass"], -x[1]["relevant"]))[:25]
    print(f"snapshot {snap.get('date')}: {len(per)} employers, {len(snap['rows'])} reqs\n")
    print("TOP YIELD (PASS / gate-relevant / rows)")
    for c, e in top: print(f"  {e['pass']:4} / {e['relevant']:4} / {e['rows']:6}  {c} [{e['ats']}]")
    if noisy:
        print(f"\nNOISY: {min_rows_noisy}+ reqs read, at most 2 gate-relevant (scope with a category filter or drop)")
    for c, e in noisy: print(f"  {e['rows']:6} rows, {e['relevant']} relevant  {c} [{e['ats']}]")
    print(f"\nZERO YIELD TODAY: {len(zero)} employer(s) with no gate-relevant or scored req "
          f"({sum(e['rows'] for _, e in zero)} rows read for nothing). One day is not a trend; "
          f"check again after a week before trimming.")
    for c, e in zero[:60]: print(f"  {e['rows']:6} rows  {c} [{e['ats']}]")
    if len(zero) > 60: print(f"  ... and {len(zero) - 60} more")

# ----------------------------------------------------------------------------- selftest
def fmt_verdict(v):
    """('FAIL', ['a', 'b']) -> 'FAIL: a; b'; a verdict with no reasons is just its label."""
    label, reasons = v
    return f"{label}: {'; '.join(reasons)}" if reasons else label

def selftest(g):
    fake = [norm("TestCo", "greenhouse", 1, "Technical Program Manager, Finance Systems", "Remote, United States",
                 "https://x/1", TODAY.isoformat(), "Own ERP adoption programs. 5+ years of program management. Many candidates do not meet every requirement."),
            norm("TestCo", "greenhouse", 2, "Staff Technical Program Manager", "San Francisco, CA", "https://x/2", None, "8+ years"),
            norm("TestCo", "greenhouse", 3, "Engineering Manager, Platform", "Remote", "https://x/3", None, ""),
            norm("TestCo", "greenhouse", 4, "Senior Program Manager, Business Systems", "Chicago, IL", "https://x/4", None,
                 "10+ years in business systems delivery; people management of 6 direct reports."),
            norm("Jobgether", "greenhouse", 5, "Product Manager", "Remote", "https://x/5", None, "")]
    for r in fake: print(f"{r['title'][:45]:45} -> {fmt_verdict(gate(r, g))}")
    rows = {r["key"]: r for r in fake}; verd = {k: gate(r, g) for k, r in rows.items()}
    # Synthetic data never touches reports/ or data/jds/: a selftest run must not be able to
    # overwrite a live sweep report or a real JD file.
    tmp = Path(tempfile.mkdtemp(prefix="maxq_selftest_"))
    (tmp / "jds").mkdir()
    write_report(rows, verd, list(rows), {}, {k: TODAY.isoformat() for k in rows},
                 [("TestCo", "greenhouse", "OK", 5)], g, reports_dir=tmp, jds_dir=tmp / "jds")
    print(f"selftest output is under {tmp} (temp; reports/ and data/jds/ untouched)")
    print("selftest: finished. Each line above is a sample req and the verdict the gates gave it.")

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--targets", default="targets.json",
                    help="employer list to read (default targets.json); point it at a smaller file for a quick run. "
                         "A relative path is read from the current directory first, then the repo root")
    ap.add_argument("--only", help="read one employer live and print a summary; stored state is not changed")
    ap.add_argument("--merge", help="read one employer live and MERGE into the snapshot; "
                                    "other employers' rows and first_seen dates are left untouched")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--digest", action="store_true", help="also write reports/digest_<date>.md")
    ap.add_argument("--force", action="store_true", help="sweep even if the snapshot is under the skip threshold")
    ap.add_argument("--fresh", action="store_true",
                    help="read EVERY target live (the default; kept for compatibility)")
    ap.add_argument("--cached", action="store_true",
                    help="opt back into tier caching and the 6h snapshot shortcut")
    ap.add_argument("--full", action="store_true",
                    help="walk every board end to end: no incremental stop at known reqs (runs weekly by itself)")
    ap.add_argument("--yield", dest="yield_report", action="store_true",
                    help="per-employer yield from the snapshot: reqs, gate-relevant, scored; lists zero-yield boards")
    ap.add_argument("--resume", action="store_true",
                    help="continue a crashed full run: employers already checkpointed today are not re-read")
    ap.add_argument("--report-only", action="store_true",
                    help="rebuild the report from data/latest.json, no network")
    ap.add_argument("--set-score", metavar="KEY=SCORE:VERDICT[:built|:unbuilt][:conv=HIGH|MEDIUM|LOW]",
                    action="append",
                    help="record a score (and the conversion read) in data/scored.json so standing targets are not "
                         "re-litigated. The verdict may contain colons; an empty verdict keeps the stored one; "
                         "one malformed entry rejects the whole run and nothing is written. Repeat the flag to record several")
    ap.add_argument("--show-cadence", action="store_true", help="print per-employer tier and cache age, then exit")
    ap.add_argument("--window", help="list gate-passing reqs inside a window (24h/48h/7d/14d) for the shortlist")
    ap.add_argument("--company", help="restrict --window to one employer (substring)")
    ap.add_argument("--floor", type=int, help="score floor, for the --window note only; filters nothing here")
    ap.add_argument("--prerank-check", action="store_true",
                    help="recompute the pre-rank hint's rank correlation against scored.json, then exit")
    a = ap.parse_args()
    g = load_config(ROOT / "gates.json")
    if a.set_score: cmd_set_score(a.set_score, g); sys.exit()
    if a.show_cadence:
        rl = load_json(READLOG, {})
        snap = load_json(DATA / "latest.json", {})
        b = stale_banner(snap.get("ts"))
        if b: print(b + "\n")
        print(f"snapshot: {snap.get('date','none')} ({fmt_age(hours_since(snap.get('ts')))}), "
              f"{snapshot_count()} snapshot(s) on disk, "
              f"first_seen is {'PRIMARY' if diff_is_meaningful() else 'NOT yet a signal'}")
        for t in load_targets(a.targets):
            live, why = should_read_live(t, None, rl, a.fresh)
            print(f"  {t['company']:34} tier {(t.get('tier') or 'C'):2}  {'LIVE ' if live else 'CACHE'}  {why}")
        sys.exit()
    if a.prerank_check: prerank_check(g); sys.exit()
    if a.window: window_query(g, a.window, a.company, a.floor); sys.exit()
    if a.yield_report: yield_report(g); sys.exit()
    if a.selftest: selftest(g); sys.exit()
    if a.report_only: report_only(g, digest=a.digest); sys.exit()
    if requests is None: sys.exit("pip install requests")
    # Live by default: a requested sweep is a fresh read. Cache is opt-in.
    fresh = a.fresh or a.resume or not a.cached
    run(load_targets(a.targets), g,
        only=a.only, smoke=a.smoke, merge=a.merge, digest=a.digest, force=a.force or a.resume, fresh=fresh,
        resume=a.resume, full=a.full)
