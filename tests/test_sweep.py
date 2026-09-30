"""Regression tests for sweep.py. Each test pins a failure mode the pipeline has hit on board data,
reproduced here with invented employers, ids and text.

Offline: no network, and every write goes to a temp directory (see SweepSandbox)."""
import collections, contextlib, datetime as dt, io, json, os, sys, tempfile, unittest
from pathlib import Path

from tests.stubs import sweep, gates, fixture

G = gates()


def row(company="TestCo", ats="greenhouse", jid=1, title="Product Manager, Finance Systems",
        location="Remote, United States", desc="Own NetSuite and Coupa adoption. 5+ years.", posted=None):
    return sweep.norm(company, ats, jid, title, location, f"https://example.test/{company}/{jid}", posted, desc)


class Dates(unittest.TestCase):
    def test_board_date_shapes(self):
        # 'September 2, 2026' sliced to [:10] crashes write_report at the end of a long run.
        self.assertEqual(sweep.iso_date("September 2, 2026"), "2026-09-02")
        self.assertEqual(sweep.iso_date("2026-9-9"), "2026-09-09")
        self.assertEqual(sweep.iso_date("2026-09-02T00:00:00.000+0000"), "2026-09-02")
        self.assertEqual(sweep.iso_date("9/2/2026"), "2026-09-02")
        self.assertEqual(sweep.iso_date("Posted Sep 2, 2026"), "2026-09-02")
        self.assertIsNone(sweep.iso_date("Posted 30+ days ago"))
        self.assertIsNone(sweep.iso_date(None))

    def test_days_since_never_raises(self):
        self.assertIsNone(sweep.days_since("not a date"))
        self.assertIsNone(sweep.days_since(None))


class LocationGate(unittest.TestCase):
    def verdict(self, loc, title="Product Manager, Finance Systems"):
        return sweep.gate(row(location=loc, title=title, desc=""), G)[0]

    def test_fail_closed_needs_a_us_marker(self):
        # Fail closed: a truthiness slip lets every location through. Foreign and empty locations must fail.
        self.assertEqual(self.verdict("Toronto, Canada"), "FAIL")
        self.assertEqual(self.verdict("Munich, de"), "FAIL")
        self.assertEqual(self.verdict(""), "FAIL")
        self.assertEqual(self.verdict("Remote"), "FAIL")              # remote with no country
        self.assertEqual(self.verdict("Remote - Canada"), "FAIL")

    def test_endorsed_and_policy_markets(self):
        self.assertEqual(self.verdict("Remote, United States"), "PASS")
        self.assertEqual(self.verdict("USA | Remote"), "PASS")
        self.assertEqual(self.verdict("Denver, CO"), "PASS")
        self.assertEqual(self.verdict("Boulder, CO"), "PASS")
        self.assertEqual(self.verdict("Dallas, TX"), "LOCATION-POLICY")
        self.assertEqual(self.verdict("Indianapolis, IN"), "LOCATION-POLICY")   # IN is Indiana, not India
        self.assertEqual(self.verdict("New York, NY"), "FAIL")                  # blocked market onsite
        self.assertEqual(self.verdict("New York, NY | Denver, CO"), "PASS")

    def test_home_metro_towns_are_the_home_market(self):
        # A req in a suburb landed LOCATION-POLICY because only the literal city was endorsed;
        # the metro towns count as the home market too.
        self.assertEqual(self.verdict("Greenwood Village, CO, USA"), "PASS")
        self.assertEqual(self.verdict("Englewood, Colorado, USA"), "PASS")
        self.assertEqual(self.verdict("Broomfield, CO"), "PASS")
        self.assertEqual(self.verdict("Lakewood, CO, United States"), "PASS")
        # Ambiguous town names stay scoped to the home state.
        self.assertEqual(self.verdict("Lakewood, OH, USA"), "LOCATION-POLICY")
        self.assertEqual(self.verdict("Golden, BC, Canada"), "FAIL")

    def test_endorsed_foreign_market_passes_with_knockout_flag(self):
        # An endorsed foreign market, while 'canada' sits in location_fail_any, used to be hidden
        # whatever shape the board wrote it in.
        for loc in ("Calgary, Canada", "Calgary | Calgary", "CA, AB, Calgary",
                    "Canmore, Alberta, Canada", "CAN - Calgary, AB - Remote",
                    "USA, NY, Brooklyn | Canada, AB, Calgary"):
            v, why = sweep.gate(row(location=loc, desc=""), G)
            self.assertEqual(v, "PASS", loc)
            self.assertTrue(any("work in Canada" in r for r in why), loc)
        # The rest of Canada still fails; a US-endorsed req carries no Canada flag.
        self.assertEqual(self.verdict("Toronto, Canada"), "FAIL")
        self.assertEqual(self.verdict("Remote - Canada"), "FAIL")
        self.assertEqual(sweep.gate(row(location="Denver, CO | Calgary, AB", desc=""), G)[1], [])

    def test_a_home_town_in_a_multi_city_list_passes(self):
        # 'Los Angeles, USA; <home suburb>, USA' read LOCATION-POLICY on the first segment.
        self.assertEqual(self.verdict("Los Angeles, USA; Littleton, USA"), "PASS")
        self.assertEqual(self.verdict("Westminster, CO"), "PASS")
        # Scoped town names match only in the home state: Westminster, MD is not the home market.
        self.assertEqual(self.verdict("Westminster, MD"), "LOCATION-POLICY")

    def test_blocked_remote_cannot_endorse_us_onsite(self):
        self.assertEqual(self.verdict("Remote, Canada; Chicago, IL"), "LOCATION-POLICY")

    def test_workday_remote_type_is_read(self):
        # remoteType must be read: a Workday 'Remote' req listed under Dallas otherwise fails the location policy.
        r = row(ats="workday", location="Dallas, TX", desc="")
        r["extra"]["externalPath"] = "/job/Dallas/PM_123"
        t = {"tenant": "x", "shard": "wd1", "site": "S"}
        orig = sweep.get
        sweep.get = lambda url, headers=None, **kw: {"jobPostingInfo": {
            "jobDescription": "<p>NetSuite</p>", "startDate": "September 2, 2026", "remoteType": "Remote",
            "location": "Dallas, TX"}}
        try:
            sweep.workday_detail(t, r)
        finally:
            sweep.get = orig
        self.assertEqual(r["posted"], "2026-09-02")
        self.assertEqual(sweep.gate(r, G)[0], "PASS")


class BareRemote(unittest.TestCase):
    """Many boards list a req as plain "Remote" with no country, which the fail-closed gate rejects on
    the list text alone. The JD body decides instead, and a VERIFY flag says to check it."""
    def gate(self, loc, desc, title="Senior Product Manager"):
        return sweep.gate(row(location=loc, title=title, desc=desc), G)

    def test_body_naming_the_us_passes_with_verify(self):
        v, why = self.gate("Remote", "This role is remote and open to candidates anywhere in the United States.")
        self.assertEqual(v, "PASS")
        self.assertTrue(any(w.startswith("VERIFY REMOTE: the board says remote with no country") for w in why))
        self.assertFalse(any("only named office is a blocked market" in w for w in why))

    def test_usd_band_is_us_evidence(self):
        v, why = self.gate("Remote | Remote | Remote", "The base salary range for this role is $150,000 - $190,000.")
        self.assertEqual(v, "PASS")
        self.assertTrue(any("USD pay band" in w for w in why))

    def test_state_name_is_us_evidence(self):
        self.assertEqual(self.gate("Remote-AMER", "Employees may reside in Colorado or Texas.")[0], "PASS")

    def test_foreign_body_still_fails(self):
        v, why = self.gate("Remote", "Fully remote within Germany or Poland. Salary in EUR.")
        self.assertEqual(v, "FAIL")
        self.assertTrue(why[0].startswith("location gate"))

    def test_no_body_fails_and_earns_the_fetch_on_a_detail_board(self):
        r = row(ats="workday", title="Senior Product Manager", location="Remote", desc="")
        verdict = sweep.gate(r, G)
        self.assertEqual(verdict[0], "FAIL")
        self.assertTrue(sweep.placeholder_location_fail(r, verdict, G))
        self.assertFalse(sweep.placeholder_location_fail(r, verdict))      # no gates, old behaviour

    def test_a_named_place_is_not_bare(self):
        self.assertFalse(sweep.bare_remote_only("Remote - Canada", G))
        self.assertFalse(sweep.bare_remote_only("Remote India", G))
        self.assertTrue(sweep.bare_remote_only("Remote (North America)", G))
        self.assertEqual(self.gate("Remote - Canada", "Open to residents of the United States too.")[0], "FAIL")

    def test_remote_nationwide_is_bare(self):
        # Some boards write "Remote Nationwide" for US-wide remote: it is bare, not a named place.
        self.assertTrue(sweep.bare_remote_only("Remote Nationwide", G))
        self.assertEqual(self.gate("Remote Nationwide", "Pay range: $117,300 - $161,300 per year.")[0], "PASS")
        self.assertEqual(self.gate("Remote Nationwide", "Open to candidates across Canada. Salary in CAD.")[0], "FAIL")

    def test_switch_off(self):
        g = json.loads(json.dumps(G)); g["bare_remote"]["body_check"] = False
        r = row(location="Remote", title="Senior Product Manager", desc="Open across the United States.")
        self.assertEqual(sweep.gate(r, g)[0], "FAIL")


class GreenhouseWorkStyleLabel(unittest.TestCase):
    """Some Greenhouse boards label every req "Hybrid" / "Distributed" / "In-Office"
    and name cities only in `offices`; read alone, the label fails every row at the location gate."""
    def read(self, label, offices):
        orig = sweep.etag_get
        sweep.etag_get = lambda t, url: {"jobs": [{"id": 1000000003, "title": "Senior Product Manager, Enterprise",
            "location": {"name": label}, "offices": offices, "absolute_url": "https://x.test/1",
            "first_published": "2026-09-20T00:00:00Z", "content": "<p>Own the roadmap.</p>", "departments": []}]}
        try:
            return sweep.greenhouse({"company": "Globex Systems", "slug": "globex"})[0]
        finally:
            sweep.etag_get = orig

    def test_hybrid_label_borrows_offices(self):
        r = self.read("Hybrid", [{"name": "Austin, TX", "location": "Austin, TX, United States"},
                                 {"name": "Denver, CO", "location": "Denver, Colorado, United States"}])
        self.assertEqual(r["location"], "Hybrid | Austin, TX, United States | Denver, Colorado, United States")
        self.assertEqual(sweep.gate(r, G)[0], "PASS")

    def test_distributed_label_keeps_its_remote_reading(self):
        r = self.read("Distributed; Hybrid", [{"name": "New York, NY", "location": "New York, New York, United States"}])
        self.assertTrue(r["location"].startswith("Distributed; Hybrid | New York"))

    def test_a_label_naming_a_place_is_untouched(self):
        r = self.read("Toronto", [{"name": "Austin, TX", "location": "Austin, TX, United States"}])
        self.assertEqual(r["location"], "Toronto")
        self.assertIsNone(sweep.WORK_STYLE_ONLY.fullmatch("Oregon"))
        self.assertIsNotNone(sweep.WORK_STYLE_ONLY.fullmatch("Hybrid or Remote"))


class ManagerTitleReview(unittest.TestCase):
    """'Sr. Manager, Product Management' is an individual-contributor title at some employers (one JD
    says 'without direct reporting authority'). A terminal title exclude hides those; the body decides."""
    T = "Sr. Manager, Product Management, Model Runtime"

    def test_ic_body_goes_to_review(self):
        r = row(title=self.T, location="Denver, CO",
                desc="Align cross-functional teams without direct reporting authority. 5+ years.")
        v, why = sweep.gate(r, G)
        self.assertEqual(v, "REVIEW")
        self.assertTrue(why[0].startswith("MANAGER TITLE with no people management"))

    def test_people_manager_body_stays_excluded(self):
        r = row(title=self.T, location="Denver, CO", desc="You will manage a team of 6 product managers.")
        v, why = sweep.gate(r, G)
        self.assertEqual(v, "FAIL")
        self.assertIn("the JD manages people", why[0])

    def test_team_management_years_is_evidence(self):
        # "Lead and develop a team" / "6+ years of team management experience" is people management.
        r = row(title=self.T, location="Denver, CO",
                desc="Lead and develop a team. 6+ years of team management experience.")
        self.assertEqual(sweep.gate(r, G)[0], "FAIL")

    def test_negated_direct_reports_is_not_evidence(self):
        r = row(title=self.T, location="Denver, CO", desc="This is an IC role with no direct reports.")
        self.assertEqual(sweep.gate(r, G)[0], "REVIEW")

    def test_policy_verdict_is_kept_with_the_note(self):
        r = row(title=self.T, location="Chicago, IL", desc="Own the roadmap for AI tooling.")
        v, why = sweep.gate(r, G)
        self.assertEqual(v, "LOCATION-POLICY")
        self.assertTrue(any(w.startswith("MANAGER TITLE") for w in why))

    def test_no_body_earns_a_detail_fetch(self):
        r = row(ats="workday", title=self.T, location="Denver, CO", desc="")
        r["extra"]["externalPath"] = "/job/x/SM_1"
        r["_target"] = {"tenant": "x", "shard": "wd1", "site": "S"}
        v, why = sweep.gate(r, G)
        self.assertEqual(v, "FAIL")
        self.assertTrue(why[0].endswith(sweep.MANAGER_BODY_PENDING))
        orig = sweep.get
        sweep.get = lambda url, headers=None, **kw: {"jobPostingInfo": {
            "jobDescription": "<p>Lead AI tooling strategy as an individual contributor. 5+ years.</p>",
            "startDate": "2026-09-10", "location": "US, CO, Denver"}}
        try:
            verdicts, stats = sweep.gate_all({r["key"]: r}, {}, G)
        finally:
            sweep.get = orig
        self.assertEqual(stats["fetched"], 1)
        self.assertEqual(verdicts[r["key"]][0], "REVIEW")

    def test_other_manager_excludes_stay_terminal(self):
        r = row(title="Engineering Manager, AI Platform", location="Denver, CO", desc="IC-ish role.")
        self.assertEqual(sweep.gate(r, G), ("FAIL", ["title excluded: engineering manager"]))


class AmazonWideQueries(unittest.TestCase):
    """Amazon is read by query, and an in-lane req (a grocery merchant-systems Sr PM) can match none of the
    lane queries. Wide terms reach it; a row found ONLY by them is dropped in AWS, devices, Leo and logistics."""
    def run_amazon(self, answers):
        orig = sweep.get
        def fake(url, headers=None, **kw):
            for q, jobs in answers.items():
                if "base_query=" + sweep.requests.utils.quote(q) + "&" in url:
                    return {"jobs": jobs}
            return {"jobs": []}
        sweep.get = fake
        try:
            return {r["id"]: r for r in sweep.amazon({"company": "Amazon"})}
        finally:
            sweep.get = orig

    def job(self, jid, cat, team="no-team-listed", title="Senior Product Manager"):
        return {"id_icims": jid, "title": title, "location": "US, CO, Denver", "job_path": f"/en/jobs/{jid}",
                "posted_date": "September 22, 2026", "description": "Payment and billing tools for merchants.",
                "business_category": cat, "team": {"label": team}}

    def test_wide_row_in_grocery_is_kept_and_aws_is_dropped(self):
        rows = self.run_amazon({"merchant": [self.job("40000001", "worldwide-grocery-stores")],
                                "automation": [self.job("1", "aws", "team-product-management-primary"),
                                             self.job("2", "alexa-and-amazon-devices", "team-project-kuiper"),
                                             self.job("3", "retail", "team-aws")]})
        self.assertIn("40000001", rows)
        self.assertEqual(rows["40000001"]["extra"]["business_category"], "worldwide-grocery-stores")
        for jid in ("1", "2", "3"): self.assertNotIn(jid, rows)

    def test_a_lane_query_row_is_never_dropped_by_the_wide_filter(self):
        rows = self.run_amazon({"finance systems": [self.job("9", "aws")], "automation": [self.job("9", "aws")]})
        self.assertIn("9", rows)


class WorkdayMultiLocation(unittest.TestCase):
    def test_placeholder_location_earns_detail_and_passes(self):
        # Workday lists a multi-site req as the literal "2 Locations"; the detail read supplies the places.
        r = row(ats="workday", title="Senior Technical Program Manager", location="2 Locations", desc="")
        r["extra"]["externalPath"] = "/job/x/TPM_1"
        r["_target"] = {"tenant": "x", "shard": "wd1", "site": "S"}
        verdict = sweep.gate(r, G)
        self.assertEqual(verdict[0], "FAIL")
        self.assertTrue(sweep.placeholder_location_fail(r, verdict))
        orig = sweep.get
        sweep.get = lambda url, headers=None, **kw: {"jobPostingInfo": {
            "jobDescription": "<p>Program management for NetSuite platforms. 3+ years.</p>",
            "startDate": "2026-09-10", "location": "Santa Clara, CA",
            "additionalLocations": ["US, CO, Denver"]}}
        try:
            verdicts, stats = sweep.gate_all({r["key"]: r}, {}, G)
        finally:
            sweep.get = orig
        self.assertEqual(stats["fetched"], 1)
        self.assertEqual(r["location"], "Santa Clara, CA | US, CO, Denver")
        self.assertEqual(verdicts[r["key"]][0], "PASS")

    def test_real_location_fail_is_not_fetched(self):
        r = row(ats="workday", location="Toronto, Canada", desc="")
        self.assertFalse(sweep.placeholder_location_fail(r, sweep.gate(r, G)))


class WorkdayWalk(unittest.TestCase):
    """Fake CXS endpoint: 50 postings, facet fam A(30)/B(20), and unstable paging on any slice over 35
    rows (page 2 repeats a row from page 1 and skips one), as large slices on real tenants do."""

    def fake(self, url, body, headers=None):
        self.calls += 1
        fam = (body.get("appliedFacets") or {}).get("fam")
        rows = [p for p in self.board if not fam or p["fam"] == fam[0]]
        off = body["offset"]
        page = rows[off:off + 20]
        if len(rows) > 35 and off == 20:
            page = [rows[0]] + rows[off + 1:off + 20]          # drift: one row lost
        facets = [] if fam else [{"facetParameter": "fam", "values": [
            {"id": "A", "descriptor": "A", "count": 30}, {"id": "B", "descriptor": "B", "count": 20}]}]
        return {"total": min(len(rows), sweep.WORKDAY_CAP), "facets": facets,
                "jobPostings": [{"externalPath": p["path"], "title": "PM"} for p in page]}

    def setUp(self):
        self.board = [{"path": f"/job/p_{i}", "fam": "A" if i < 30 else "B"} for i in range(50)]
        self.calls = 0
        self.saved = (sweep.post_json, sweep.WORKDAY_SPLIT_AT)
        sweep.post_json = self.fake
        sweep.WORKDAY_TOTALS.clear()
        os.environ["MAXQ_SERIAL"] = "1"

    def tearDown(self):
        sweep.post_json, sweep.WORKDAY_SPLIT_AT = self.saved
        sweep.WORKDAY_TOTALS.clear()
        os.environ.pop("MAXQ_SERIAL", None)

    def test_large_slice_is_split_before_walking(self):
        sweep.WORKDAY_SPLIT_AT = 25
        seen = {}
        _, complete = sweep._workday_walk("https://x", {}, seen, False)
        self.assertTrue(complete)
        self.assertEqual(len(seen), 50)

    def test_short_walk_is_resplit(self):
        sweep.WORKDAY_SPLIT_AT = 1000                          # walks directly, comes back short, re-splits
        seen = {}
        _, complete = sweep._workday_walk("https://x", {}, seen, False)
        self.assertTrue(complete)
        self.assertEqual(len(seen), 50)

    def test_facet_split_walk_reports_no_shortfall(self):
        # The split path compares the sum over facets against the top-level total, so a board that
        # only reads completely BECAUSE it was split must not then be reported as short.
        sweep.WORKDAY_SPLIT_AT = 25
        with contextlib.redirect_stdout(io.StringIO()):
            rows = sweep.workday({"company": "SplitCo", "tenant": "x", "shard": "wd1", "site": "S"})
        self.assertEqual(len(rows), 50)
        self.assertEqual(sweep.WORKDAY_TOTALS["SplitCo"], {"reported": 50, "read": 50, "complete": True})
        self.assertEqual(sweep.workday_total_gaps(), [])


class WorkdayTotalGuard(unittest.TestCase):
    """Workday CXS fails silently: a limit above 20 answers 200 with an empty list, `total` is only
    reliable on page 1, and paging can stop at a high offset without saying so. Each one reads as a
    smaller board. The page-1 total is recorded so a short read is named instead of trusted."""

    def board(self, reported, available):
        def fake(url, body, headers=None):
            off = body["offset"]
            paths = [f"/job/p_{i}" for i in range(available)][off:off + 20]
            return {"total": reported, "facets": [],
                    "jobPostings": [{"externalPath": p, "title": "PM"} for p in paths]}
        return fake

    def setUp(self):
        self.saved = sweep.post_json
        sweep.WORKDAY_TOTALS.clear()
        os.environ["MAXQ_SERIAL"] = "1"

    def tearDown(self):
        sweep.post_json = self.saved
        sweep.WORKDAY_TOTALS.clear()
        os.environ.pop("MAXQ_SERIAL", None)

    def walk(self, company, reported, available):
        sweep.post_json = self.board(reported, available)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            rows = sweep.workday({"company": company, "tenant": "x", "shard": "wd1", "site": "S"})
        return rows, out.getvalue()

    def test_short_read_is_recorded_and_warned(self):
        rows, printed = self.walk("ShortCo", 40, 30)
        self.assertEqual(len(rows), 30)
        self.assertEqual(sweep.WORKDAY_TOTALS["ShortCo"], {"reported": 40, "read": 30, "complete": False})
        self.assertEqual(sweep.workday_total_gaps(), [("ShortCo", 40, 30)])
        self.assertIn("30 reqs read", printed)              # the adapter's own line is unchanged

    def test_complete_read_is_not_warned(self):
        rows, _ = self.walk("WholeCo", 40, 40)
        self.assertEqual(len(rows), 40)
        self.assertEqual(sweep.WORKDAY_TOTALS["WholeCo"], {"reported": 40, "read": 40, "complete": True})
        self.assertEqual(sweep.workday_total_gaps(), [])

    def test_smoke_reads_are_not_recorded(self):
        sweep.post_json = self.board(40, 40)
        sweep.workday({"company": "SmokeCo", "tenant": "x", "shard": "wd1", "site": "S"}, smoke=True)
        self.assertNotIn("SmokeCo", sweep.WORKDAY_TOTALS)

    def test_threshold_allows_mid_walk_closures(self):
        # Within 5%, and any one-row shortfall, is a req that closed while the walk was running.
        gaps = lambda d: sweep.workday_total_gaps(d)
        self.assertEqual(gaps({"A": {"reported": 100, "read": 96}}), [])
        self.assertEqual(gaps({"A": {"reported": 100, "read": 94}}), [("A", 100, 94)])
        self.assertEqual(gaps({"A": {"reported": 10, "read": 9}}), [])
        self.assertEqual(gaps({"A": {"reported": 10, "read": 8}}), [("A", 10, 8)])
        self.assertEqual(gaps({"A": {"reported": 0, "read": 0}}), [])


class WorkdayCountryScope(unittest.TestCase):
    """The walk is scoped to the US and Canada through the tenant's own facets. Two synthetic page-1
    responses (one tenant with a country-level facet, one with only city values) are fixtures; neither
    carries a "Location Country" facet, so discovery is by value (see sweep.WORKDAY_SCOPE_COUNTRIES). The fake board below has a country facet."""

    class Board:
        """60 postings: 40 US, 10 Canada, 10 abroad; p_0 is listed in BOTH the US and Canada.
        facet=None drops the location facet (a tenant with no country values)."""
        def __init__(self, facet="locationCountry", fail=None):
            self.facet, self.fail, self.calls = facet, fail, []
            self.rows = ([("/job/p_%d" % i, {"US"}) for i in range(40)]
                         + [("/job/p_%d" % i, {"CA"}) for i in range(40, 50)]
                         + [("/job/p_%d" % i, {"XX"}) for i in range(50, 60)])
            self.rows[0] = ("/job/p_0", {"US", "CA"})

        def __call__(self, url, body, headers=None):
            self.calls.append(body)
            applied = body.get("appliedFacets") or {}
            want = set(applied.get(self.facet) or [])
            rows = [p for p, cs in self.rows if not want or (cs & {c.replace("id-", "") for c in want})]
            page = rows[body["offset"]: body["offset"] + 20]
            total = len(rows) if self.fail is None else self.fail
            facets = [{"facetParameter": "jobFamilyGroup", "descriptor": "Job Category",
                       "values": [{"id": "eng", "descriptor": "Engineering", "count": len(rows)}]}]
            if self.facet:
                facets.append({"facetParameter": "locationMainGroup", "values": [
                    {"facetParameter": self.facet, "descriptor": "Location", "values": [
                        {"id": "id-US", "descriptor": "United States of America", "count": 40},
                        {"id": "id-CA", "descriptor": "Canada", "count": 11},
                        {"id": "id-XX", "descriptor": "Elsewhere", "count": 10}]}]})
            return {"total": total, "facets": facets,
                    "jobPostings": [{"externalPath": p, "title": "PM", "locationsText": "x"} for p in page]}

    def setUp(self):
        self.saved = sweep.post_json
        sweep.WORKDAY_TOTALS.clear()
        os.environ["MAXQ_SERIAL"] = "1"

    def tearDown(self):
        sweep.post_json = self.saved
        sweep.WORKDAY_TOTALS.clear()
        os.environ.pop("MAXQ_SERIAL", None)

    def walk(self, board, **target):
        sweep.post_json = board
        t = dict({"company": "ScopeCo", "tenant": "x", "shard": "wd1", "site": "S"}, **target)
        with contextlib.redirect_stdout(io.StringIO()):
            rows = sweep.workday(t)
        return t, rows

    def test_city_facet_fixture_yields_city_values_that_carry_the_country(self):
        # No country facet at all: 39 city values under `locations`, "Aspen Ridge, CO, USA" style.
        # "Remote Worker Location, CA, USA" is California: the CA segment must not read as Canada.
        key, ids, how = sweep._workday_country_ids(fixture("sweep", "workday_page1_city_facet.json"))
        self.assertEqual(key, "locations")
        self.assertEqual(len(ids), 24)
        self.assertEqual(how, "21 US and 3 Canada values in facet locations")
        self.assertIn("835e7cb880e9383a043afe2adf9adde0", ids)      # Glacier Bay, BC, Canada
        self.assertNotIn("d27223e18c0939700f927e97eb7ce1c6", ids)   # Ashbourne Cross, United Kingdom

    def test_country_level_fixture_prefers_the_country_level_facet(self):
        # locationHierarchy1 (label "Locations") holds "United States" and "Canada"; the city-level
        # `locations` ("US, WA, Sound Harbor") also matches and must lose to it.
        key, ids, how = sweep._workday_country_ids(fixture("sweep", "workday_page1_country_facet.json"))
        self.assertEqual(key, "locationHierarchy1")
        self.assertEqual(sorted(ids), ["7fc92f0e1d2dd7772b8e6c8d706d4421", "d762c9531f8f9d20b12d1d044e4dc469"])
        self.assertEqual(how, "country facet locationHierarchy1")

    def test_a_facet_without_the_united_states_is_not_a_country_facet(self):
        d = {"total": 3, "facets": [{"facetParameter": "locationHierarchy2", "descriptor": "Location Type",
                                     "values": [{"id": "a", "descriptor": "Office", "count": 2},
                                                {"id": "b", "descriptor": "Remote", "count": 1}]},
                                    {"facetParameter": "jobFamilyGroup", "descriptor": "Job Category",
                                     "values": [{"id": "c", "descriptor": "United States Sales", "count": 3}]}]}
        self.assertIsNone(sweep._workday_country_ids(d))

    def test_scoped_walk_reads_the_us_and_canada_once_each(self):
        board = self.Board()
        t, rows = self.walk(board)
        self.assertEqual(t["_scope"], "US+CA")
        self.assertEqual(len(rows), 50)                                   # 40 US + 10 CA, p_0 once
        self.assertEqual(len({r["key"] for r in rows}), 50)
        self.assertFalse([r for r in rows if int(r["id"].rsplit("_", 1)[-1]) >= 50])
        self.assertEqual(board.calls[0]["appliedFacets"], {})               # one unfiltered page
        self.assertTrue(all(sorted(b["appliedFacets"].get("locationCountry") or []) == ["id-CA", "id-US"]
                            for b in board.calls[1:]))
        self.assertEqual(sweep.WORKDAY_TOTALS["ScopeCo"], {"reported": 50, "read": 50, "complete": True})
        self.assertIn("country-scoped: 50 of 60 board rows, country facet locationCountry", t["_scope_note"])

    def test_no_country_facet_falls_back_to_the_full_walk_and_says_so(self):
        t, rows = self.walk(self.Board(facet=None))
        self.assertIsNone(t["_scope"])
        self.assertEqual(len(rows), 60)
        self.assertEqual(t["_scope_note"], "full walk: no location facet names the United States")
        self.assertEqual(sweep.WORKDAY_TOTALS["ScopeCo"], {"reported": 60, "read": 60, "complete": True})

    def test_a_scope_that_reads_as_empty_falls_back(self):
        board = self.Board()
        real = board.__call__
        def zero_when_scoped(url, body, headers=None):
            d = real(url, body, headers)
            if body.get("appliedFacets"): d["total"], d["jobPostings"] = 0, []
            return d
        t, rows = self.walk(zero_when_scoped)
        self.assertIsNone(t["_scope"])
        self.assertEqual(len(rows), 60)
        self.assertTrue(t["_scope_note"].startswith("full walk: country scope looked wrong (0 of 60"))

    def test_explicit_target_facets_win_and_skip_discovery(self):
        board = self.Board()
        t, rows = self.walk(board, facets={"jobFamilyGroup": ["eng"]})
        self.assertIsNone(t["_scope"])
        self.assertEqual(t["_scope_note"], "scoped by target facets")
        self.assertEqual(len(rows), 60)
        self.assertTrue(all(b["appliedFacets"] == {"jobFamilyGroup": ["eng"]} for b in board.calls))

    def test_country_scope_false_keeps_the_full_walk(self):
        board = self.Board()
        t, rows = self.walk(board, country_scope=False)
        self.assertIsNone(t["_scope"])
        self.assertEqual(len(rows), 60)
        self.assertTrue(all(b["appliedFacets"] == {} for b in board.calls))

    def test_first_scoped_read_is_not_a_count_drop(self):
        scoped = [("ScopeCo", "workday", "OK (country-scoped: 300 of 600 board rows, country facet x)", 300)]
        self.assertEqual(sweep.count_drops(scoped, {"ScopeCo": 600}), [])                      # no read log scope
        self.assertEqual(sweep.count_drops(scoped, {"ScopeCo": 600}, {"ScopeCo": None}), [])
        self.assertEqual(sweep.count_drops(scoped, {"ScopeCo": 700}, {"ScopeCo": "US+CA"}),
                         [("ScopeCo", 700, 300)])                                              # both reads scoped: real
        plain = [("ScopeCo", "workday", "OK", 290)]
        self.assertEqual(sweep.count_drops(plain, {"ScopeCo": 600}, {"ScopeCo": None}), [("ScopeCo", 600, 290)])


class HealthWarnings(unittest.TestCase):
    """The anomaly lines in the SWEEP HEALTH block, called directly."""

    def setUp(self):
        sweep.WORKDAY_TOTALS.clear()
        self.addCleanup(sweep.WORKDAY_TOTALS.clear)

    def health(self, coverage, prev_counts):
        r = row()
        return sweep.sweep_health({r["key"]: r}, {r["key"]: ("PASS", [])}, [], coverage,
                                  prev_counts, {}, {}, len(coverage))

    def test_4xx_on_a_board_that_read_before_names_a_tenant_migration(self):
        # Workday tenants move hosts (wd5 -> wd115, wd5 -> wd504). The old host then answers a 4xx
        # after months of clean reads, which otherwise looks like a bot wall.
        cov = [("Northwind Traders", "workday", "ERROR HTTPError: 422 Client Error: Unprocessable Entity for url: "
                "https://northwind.wd5.myworkdayjobs.com/wday/cxs/northwind/NorthwindExternal/jobs", 0),
               ("Gone", "greenhouse", "ERROR HTTPError: 404 Client Error: Not Found for url: https://x/y", 0),
               ("Retired", "lever", "ERROR HTTPError: 410 Client Error: Gone for url: https://x/y", 0)]
        w = self.health(cov, {"Northwind Traders": 1800, "Gone": 240, "Retired": 31})["warnings"]
        for c in ("Northwind Traders", "Gone", "Retired"):
            self.assertTrue(any(x.startswith(f"{c}: ") and "may have migrated hosts" in x for x in w), c)

    def test_migration_hint_needs_a_prior_clean_read_and_a_4xx(self):
        first = [("NewCo", "workday", "ERROR HTTPError: 404 Client Error: Not Found for url: https://x/y", 0)]
        self.assertFalse(any("migrated" in x for x in self.health(first, {})["warnings"]))
        self.assertFalse(any("migrated" in x for x in self.health(first, {"NewCo": None})["warnings"]))
        other = [("FiveCo", "workday", "ERROR HTTPError: 503 Server Error: Service Unavailable for url: "
                  "https://x/y", 0)]
        self.assertFalse(any("migrated" in x for x in self.health(other, {"FiveCo": 500})["warnings"]))
        empty = [("Litware", "ashby", "ERROR EmptyBoard: verified target returned 0 reqs, re-resolve "
                  "the slug", 0)]
        self.assertFalse(any("migrated" in x for x in self.health(empty, {"Litware": 6})["warnings"]))

    def test_a_number_in_the_url_is_not_a_status_code(self):
        # Workday shards are numbered ("wd504" in the host), so the status code is read from the
        # error, never from the URL.
        cov = [("Northwind Traders", "workday", "ERROR ConnectionError: HTTPSConnectionPool(host="
                "'northwind.wd504.myworkdayjobs.com', port=443): Read timed out. /404/410/422", 0)]
        self.assertFalse(any("migrated" in x for x in self.health(cov, {"Northwind Traders": 1800})["warnings"]))

    def test_workday_shortfall_reaches_the_health_block(self):
        sweep.WORKDAY_TOTALS["Fabrikam Freight"] = {"reported": 2205, "read": 1873, "complete": False}
        w = self.health([("Fabrikam Freight", "workday", "OK", 1873)], {"Fabrikam Freight": 2205})["warnings"]
        self.assertIn("Fabrikam Freight: Workday reported 2205, read 1873", w)
        self.assertIn("Fabrikam Freight: Workday reported 2205, read 1873", sweep.health_lines(
            self.health([("Fabrikam Freight", "workday", "OK", 1873)], {"Fabrikam Freight": 2205}))[1])


class RadancyTiles(unittest.TestCase):
    def test_href_with_fragment(self):
        tile = ('<li><a href="/job/miami/product-manager/1234/567890/#job-details-section">'
                '<h2>Product Manager</h2><span class="job-location">Miami, Florida</span></a></li>')
        orig = sweep.get
        sweep.get = lambda url, headers=None, **kw: {"results": "<ul>" + tile + "</ul>"}
        try:
            rows = sweep.radancy({"company": "Contoso Health", "origin": "https://jobs.example"}, smoke=True)
        finally:
            sweep.get = orig
        self.assertEqual([r["id"] for r in rows], ["567890"])


class TitleGate(unittest.TestCase):
    def verdict(self, title):
        return sweep.gate(row(title=title, desc=""), G)

    def test_word_boundaries(self):
        # 'intern' killed 'Internal Product Manager'; 'developer' killed a Developer Platform PM title.
        self.assertEqual(self.verdict("Internal Product Manager 5")[0], "PASS")
        self.assertEqual(self.verdict("Lead PM - AI Developer Platform, Product Manager")[0], "PASS")
        self.assertEqual(self.verdict("Product Manager Intern")[0], "FAIL")

    def test_engineering_titles_need_product_work(self):
        self.assertEqual(self.verdict("Staff Software Engineer, Developer Platform")[0], "FAIL")
        self.assertEqual(self.verdict("Software Engineer, Finance Systems Program")[0], "PASS")

    def test_noise_and_override(self):
        self.assertEqual(self.verdict("Program Manager, Data Center Construction")[0], "FAIL")
        # The plural is noise too.
        self.assertEqual(self.verdict("Technical Program Manager, Data Centers")[0], "FAIL")
        self.assertEqual(self.verdict("Business Technology Product Manager - Supply Chain Planning")[0], "PASS")


class Tenure(unittest.TestCase):
    def test_equity_years_are_not_a_bar(self):
        # An equity clause ("10 years to exercise options") is not a 10+ years bar.
        d = "3+ years of product management. You will have 10 years to exercise options."
        self.assertEqual(sweep.experience_years(d.lower(), G), [3])
        self.assertEqual(sweep.gate(row(desc=d), G)[0], "PASS")

    def test_preferred_years_are_not_the_bar(self):
        # JDs often state a required and a preferred bar side by side; only the required one counts.
        d = ("required/minimum qualifications bachelor's degree and 5+ years experience in product management. "
             "additional or preferred qualifications bachelor's degree and 8+ years experience in product management.")
        self.assertEqual(sweep.experience_years(d, G), [5])
        # With no required heading nothing is dropped, and a preferred-only bar is never erased.
        self.assertEqual(sweep.experience_years("preferred qualifications: 8+ years experience in product management.", G), [8])
        d2 = "required qualifications: a bachelor's degree. preferred qualifications: 7+ years experience in product management."
        self.assertEqual(sweep.experience_years(d2, G), [7])

    def test_real_bar_over_eight_is_tenure(self):
        self.assertEqual(sweep.gate(row(desc="12+ years of product management required."), G)[0], "TENURE")

    def test_five_years_is_cleared_six_is_reach(self):
        # The example profile clears a 5+ bar; REACH starts at 6+.
        five = sweep.gate(row(desc="5+ years of product management experience."), G)
        self.assertFalse(any(r.startswith("REACH") for r in five[1]))
        six = sweep.gate(row(desc="6+ years of product management experience."), G)
        self.assertTrue(any(r.startswith("REACH: 6+") for r in six[1]))

    def test_escape_clause(self):
        d = "10+ years of experience. Many candidates do not meet every requirement."
        self.assertEqual(sweep.gate(row(desc=d), G)[0], "PASS")

    def test_degree_tiers_and_ranges_use_the_bachelors_low_bar(self):
        # Taking the MAX figure reads a tiered bar off its high-school tier and "6–10+ years" as 10+.
        # The bar is the Bachelor's tier, and the low end of a range.
        fx = fixture("sweep", "jd_snippets.json")["tenure"]
        yrs = lambda k: sweep.experience_years(fx[k]["text"].lower(), G)
        self.assertEqual(yrs("icims_classic:Initech:204511"), [5])
        self.assertEqual(yrs("pcsx:Northwind Traders:417702335108"), [6])
        self.assertEqual(yrs("icims_classic:Initech:388120"), [8])              # Bachelor's 8, not HS 12
        self.assertEqual(yrs("pcsx:Fabrikam Freight:5520417790213"), [12])      # in-lieu 16 and Assoc 14 dropped
        v, why = sweep.gate(row(desc=fx["icims_classic:Initech:204511"]["text"]), G)
        self.assertEqual((v, why), ("PASS", []))
        v, why = sweep.gate(row(desc=fx["pcsx:Northwind Traders:417702335108"]["text"]), G)
        self.assertEqual(v, "PASS")
        self.assertIn("REACH: 6+ years stated", why)
        # Untiered JDs and equity exclusions behave as before.
        self.assertEqual(sweep.experience_years("10+ years of product management experience.", G), [10])
        self.assertEqual(sweep.experience_years("3+ years of product management. You will have 10 years to exercise options.", G), [3])
        self.assertEqual(sweep.experience_years("3+ years as a scrum master; 9+ years of program management with a bs/ba", G), [3, 9])

    def test_required_bar_beats_preferred(self):
        d = ("Required qualifications: Bachelor's degree and 4+ years of program management. "
             "Preferred qualifications: 12+ years of experience.")
        self.assertEqual(sweep.stated_years_bar(d.lower(), G)[0], 4)

    def test_qualifier_between_the_number_and_years_still_reads_as_a_bar(self):
        # "12+ overall years of program management experience": a word between the figure and
        # "years" must not hide the bar, or a 12+ req reads as having no bar at all.
        for d, want in (("12+ overall years of program management experience.", [12]),
                        ("10+ combined years of experience.", [10]),
                        ("7+ total years of product management.", [7]),
                        ("8+ cumulative years of delivery work.", [8]),
                        ("6+ relevant years of program management.", [6])):
            self.assertEqual(sweep.experience_years(d, G), want, d[:45])
        # The plain forms and the equity exclusion are unchanged.
        self.assertEqual(sweep.experience_years("5+ years of product management experience.", G), [5])
        self.assertEqual(sweep.experience_years(
            "3+ years of product management. you will have 10 years to exercise options.", G), [3])

    def test_business_systems_lane_exception_is_deliberate_and_labelled_honestly(self):
        # Business-systems postings inflate years harder than any other category, so
        # the lane is never hard-gated on tenure. It is NOT an escape clause and must not say it is.
        d = "Minimum qualifications: 10+ years of progressive experience in business systems analysis."
        v, why = sweep.gate(row(desc=d), G)
        self.assertEqual(v, "PASS")
        self.assertIn("REACH: 10+ years stated (business-systems lane exception;"
                      " the JD states no escape clause)", why)
        self.assertIsNone(sweep.ESCAPE_CLAUSES.search("business systems analysis"))
        # A real escape clause still reads as one, and the same bar without either is TENURE.
        v, why = sweep.gate(row(desc="10+ years of program management or equivalent experience."), G)
        self.assertEqual((v, why), ("PASS", ["REACH: 10+ years stated with escape clause"]))
        self.assertEqual(sweep.gate(row(desc="10+ years of program management required."), G)[0], "TENURE")

    def test_in_business_as_a_field_name_is_still_a_bar(self):
        # The "in business" suppressor exists for the company-age idiom. It must not match the FIELD
        # name: "10+ years of experience in business systems analysis" is a real bar.
        for d, want in (
                ("minimum qualifications: 10+ years of progressive experience in business "
                 "systems analysis, business process or operations management.", [10]),
                ("8+ years of experience in business program management.", [8]),
                ("6+ years of experience in business operations.", [6]),
                ("5+ years of experience in business intelligence.", [5])):
            self.assertEqual(sweep.experience_years(d, G), want, d[:50])

    def test_company_age_in_business_is_still_ignored(self):
        # The idiom the suppressor was written for must keep working.
        for d in ("we have been in business 40 years and counting.",
                  "a founder-led company, 25 years in business.",
                  "proudly in business for 30 years."):
            self.assertEqual(sweep.experience_years(d, G), [], d[:50])
        # A bar plus the idiom in a LATER sentence still yields the bar.
        self.assertEqual(sweep.experience_years(
            "4+ years of program management. we have been in business 40 years.", G), [4])

    def test_team_average_seniority_is_not_a_bar(self):
        # "Our teams average 15+ years" describes the people already there; read as a bar it would
        # stamp a 15+ tenure fail on a role whose required line is 7+.
        d = ("small and senior by design. our teams average 15+ years of experience. "
             "we move quickly. required: 7+ years of experience in product management.")
        self.assertEqual(sweep.experience_years(d, G), [7])
        self.assertEqual(sweep.experience_years("our engineers averaging 12 years of experience.", G), [])
        self.assertEqual(sweep.experience_years("consultants with an average of 11 years in the field.", G), [])
        # A real bar after a team noun still counts.
        self.assertEqual(sweep.experience_years("the team needs 8+ years of experience in product.", G), [8])


class TextAndComp(unittest.TestCase):
    def test_double_escaped_greenhouse_body(self):
        s = sweep.strip_html("&amp;lt;div&amp;gt;Hello &amp;amp; welcome&amp;lt;/div&amp;gt;")
        self.assertNotIn("<", s)
        self.assertIn("Hello & welcome", s)

    def test_band_needs_pay_context(self):
        self.assertEqual(sweep.comp_from_description("The base salary range is $152,200 - $205,900 per year."),
                         "$152,200 - $205,900")
        self.assertIsNone(sweep.comp_from_description("We grew revenue from $50M - $100M last year."))

    def test_band_shapes_jds_use(self):
        # Pay bands in the shapes JDs write them, which a single-pattern parser misses: one line per
        # city, one band per level, notes and brackets between the figures, Minimum:/Maximum: pairs,
        # "between $X and $Y", a currency code before each figure, and a bare figure pair.
        fx = fixture("sweep", "jd_snippets.json")["comp"]
        c = lambda k: sweep.comp_from_description(fx[k])
        self.assertEqual(c("workday:Acme Analytics:R100101"), "$148,300 - $201,700")      # "City - lo - hi USD annually"
        self.assertEqual(c("workday:Acme Analytics:R100102"), "$142,600 - $193,400 (+1 more in JD)")  # one line per city
        self.assertEqual(c("icims_jibe:Globex Systems:7301"), "$118,500 - $301,900")      # "USD $x - USD $y /Yr."
        self.assertEqual(c("workday:Initech:JR300201"), "$162,000 - $249,500 (+1 more in JD)")   # per level
        self.assertTrue(c("greenhouse:Northwind Traders:4400512").startswith("$124,300 - $155,400"))   # (note) between
        self.assertTrue(c("ashby:Contoso Health:7c1e2d3f-4a5b-4c6d-8e7f-9a0b1c2d3e4f")
                        .startswith("$166,000 - $249,000"))                                 # [note] between
        self.assertEqual(c("workday:Fabrikam Freight:R51207"), "$139,400 - $322,800")     # Minimum: / Maximum:
        self.assertEqual(c("workday:Litware:R0004417"), "$98,500 - $133,000")             # Hiring Rate Minimum/Maximum
        self.assertEqual(c("workday:ExampleCo:52QX18077-2"), "$104,500 - $188,250")       # between ... and ...
        self.assertTrue(c("pcsx:Globex Systems:731059").startswith("$112,000 - $124,000"))   # per year - per year
        self.assertEqual(c("workday:Initech:JR2026110457-1"), "$171,400 - $231,900")      # bare, after "pay range:"
        # Refused, never guessed: a typo'd figure, a CA$ band, an unnamed "local currency", company scale.
        self.assertIsNone(c("ashby:Northwind Traders:5d2c9e10-7b3a-4f68-a1c4-0e9d8b7a6c5f"))   # CA$...; "$181,00"
        self.assertIsNone(c("pcsx:Fabrikam Freight:830115204467"))
        self.assertIsNone(c("icims_jibe:Contoso Health:6120"))                        # "$147,00 - $229,000"
        self.assertIsNone(c("greenhouse:Acme Analytics:6600218004"))

    def test_published_salary_parser_failure_shapes(self):
        # Four shapes from a published bug list (dev.to/apify, "my salary parser was lying").
        band, comp, rng = sweep.band_from_description, sweep.comp_from_description, sweep.money_range

        # (a) A k/M suffix eating the first letter of the next word. The single figure is not a
        # range and must stay unparsed; the range beside it must come back WITHOUT the stray M,
        # which is what the suffix used to swallow out of "Medical" (120,000 -> $120,000,000,000).
        self.assertIsNone(comp("Salary: $95,000 Medical, dental and vision"))
        eaten = "The base salary range is $95,000 to $120,000 Medical, dental and vision"
        self.assertEqual(rng(eaten), "$95,000 to $120,000")
        self.assertEqual(band(eaten), "$95,000 - $120,000")
        self.assertEqual(rng("The salary range is $95k - $120k per year."), "$95k - $120k")   # k still a suffix

        # (b) A unit between the range ends. USD annual bands parse; hourly rates are NOT a band
        # and are ignored (the 20,000 annual floor refuses them), so comp reads "not posted"
        # rather than reporting $36.79 as a salary.
        self.assertEqual(band("The base pay range is $81,000 USD - $105,000 USD"), "$81,000 - $105,000")
        self.assertIsNone(band("$36.79/hr - $58.50/hr"))
        self.assertIsNone(comp("The pay range for this role is $36.79/hr - $58.50/hr."))
        self.assertIsNone(comp("Compensation: $36.79 - $58.50 per hour."))

        # (c) A bonus or insurance figure that is not the band.
        perks = ("base salary range $120,000 - $150,000 plus a $10,000 sign-on bonus and "
                 "$50,000 life insurance")
        self.assertEqual(band(perks), "$120,000 - $150,000")
        self.assertEqual(comp(perks), "$120,000 - $150,000")
        self.assertEqual(comp("A $10,000 sign-on bonus is offered. The base salary range is "
                              "$120,000 - $150,000."), "$120,000 - $150,000")

        # (d) A schedule phrase near the band ("40 hours per week" is not a pay period).
        sched = "40 hours per week ... pay range $70,000 to $90,000 per year"
        self.assertEqual(band(sched), "$70,000 - $90,000")
        self.assertEqual(comp(sched), "$70,000 to $90,000")

    def test_boilerplate_edit_does_not_change_core_hash(self):
        # Editing an employer's shared About block must not mark every one of its reqs 'JD changed'.
        boiler = "About us: we are a company that builds things for customers everywhere, since 2010."
        rows = {f"k{i}": {"company": "Acme Analytics", "description": f"Role {i} owns a very specific product area here.\n{boiler}"}
                for i in range(4)}
        for r in rows.values(): r["jd_hash"] = sweep.jd_hash(r["description"])
        sweep.core_hashes(rows)
        before = {k: r["jd_core_hash"] for k, r in rows.items()}
        for r in rows.values():
            r["description"] = r["description"].replace("since 2010", "since 2011, and proud of it")
            r["jd_hash"] = sweep.jd_hash(r["description"])
        sweep.core_hashes(rows)
        self.assertEqual(before, {k: r["jd_core_hash"] for k, r in rows.items()})

    def test_repost_detection(self):
        old = row(jid=1, title="Product Manager, AI", location="Remote, United States")
        new = row(jid=2, title="Product Manager - AI", location="Remote, United States")
        self.assertEqual(sweep.detect_reposts({new["key"]: new}, {old["key"]: old}, [new["key"]]),
                         {new["key"]: old["key"]})

    def test_generic_title_sibling_is_not_a_repost(self):
        # Reqs sharing a generic title in one city are not reposts of an old req that is still open
        # with a different JD.
        old = row(jid=1, title="Principal Product Manager", location="Springfield")
        new = row(jid=2, title="Principal Product Manager", location="Springfield")
        old["description"], new["description"] = "Shared Notebooks.", "Fleet and Capacity Planning."
        rows = {old["key"]: old, new["key"]: new}
        self.assertEqual(sweep.detect_reposts(rows, {old["key"]: old}, [new["key"]]), {})
        # Old id closed this run: still a repost even though the text changed.
        self.assertEqual(sweep.detect_reposts({new["key"]: new}, {old["key"]: old}, [new["key"]]),
                         {new["key"]: old["key"]})


class Lanes(unittest.TestCase):
    def test_every_item_processed_once(self):
        items = [(a, i) for a in ("greenhouse", "workday", "lever") for i in range(9)]
        out = sweep.run_laned(items, lambda x: x[0], lambda x: x)
        self.assertEqual(sorted(out), sorted(items))

    def test_serial_mode(self):
        os.environ["MAXQ_SERIAL"] = "1"
        try:
            self.assertEqual(sweep.run_laned([1, 2, 3], lambda x: "a", lambda x: x * 2), [2, 4, 6])
        finally:
            del os.environ["MAXQ_SERIAL"]

    def test_old_snapshots_are_gzipped_and_still_counted(self):
        import gzip
        saved = (sweep.DATA, sweep.TODAY)
        with tempfile.TemporaryDirectory() as d:
            sweep.DATA, sweep.TODAY = Path(d), dt.date(2026, 9, 16)
            try:
                for day in ("2026-09-13", "2026-09-15"):
                    (Path(d) / f"snapshot_{day}.json").write_text(json.dumps({"date": day}), encoding="utf-8")
                n, _ = sweep.compress_old_snapshots(keep_days=2)
                self.assertEqual(n, 1)
                names = sorted(p.name for p in Path(d).iterdir())
                self.assertEqual(names, ["snapshot_2026-09-13.json.gz", "snapshot_2026-09-15.json"])
                with gzip.open(Path(d) / "snapshot_2026-09-13.json.gz", "rt", encoding="utf-8") as f:
                    self.assertEqual(json.load(f)["date"], "2026-09-13")
                self.assertEqual(sweep.snapshot_count(), 2)
            finally:
                sweep.DATA, sweep.TODAY = saved

    def test_dated_snapshot_is_written_gzipped_and_no_raw_copy_survives(self):
        # Snapshots are large: the dated copy is written gzipped, and no raw duplicate of latest.json
        # is left beside it.
        saved = (sweep.DATA, sweep.TODAY)
        with tempfile.TemporaryDirectory() as d:
            sweep.DATA, sweep.TODAY = Path(d), dt.date(2026, 9, 17)
            try:
                old = {"date": "2026-09-16", "rows": {"a": 1}}
                (Path(d) / "snapshot_2026-09-16.json").write_text(json.dumps(old), encoding="utf-8")
                stale = Path(d) / "snapshot_2026-09-17.json"             # an earlier run today, raw
                stale.write_text(json.dumps({"date": "2026-09-17", "rows": {}}), encoding="utf-8")
                os.utime(stale, (1, 1))
                snap = {"date": "2026-09-17", "rows": {"b": 2}}
                sweep.write_json_atomic(Path(d) / "latest.json", snap)
                gz = sweep.write_dated_snapshot()
                n, _ = sweep.compress_old_snapshots()
                names = sorted(p.name for p in Path(d).iterdir())
                self.assertEqual(names, ["latest.json", "snapshot_2026-09-16.json.gz", "snapshot_2026-09-17.json.gz"])
                self.assertEqual(n, 1)
                self.assertEqual(sweep.load_json(gz, None), snap)                      # identical to latest
                self.assertEqual(sweep.load_json(sweep.snapshot_path("2026-09-16"), None), old)
                self.assertEqual(sweep.snapshot_count(), 2)
                # A raw file OLDER than its .gz is a stale same-day copy: removed, never compressed over it.
                stale.write_text(json.dumps({"date": "2026-09-17", "rows": {}}), encoding="utf-8")
                os.utime(stale, (1, 1))
                sweep.compress_old_snapshots()
                self.assertEqual(sweep.load_json(gz, None), snap)
                self.assertFalse(stale.exists())
                self.assertEqual(sweep.load_json(Path(d) / "missing.json.gz", "default"), "default")
            finally:
                sweep.DATA, sweep.TODAY = saved

    def test_lane_keys_are_hosts(self):
        self.assertEqual(sweep.lane_key({"ats": "pcsx", "base": "https://fabrikam.eightfold.ai"}),
                         "fabrikam.eightfold.ai")
        self.assertEqual(sweep.lane_key({"ats": "workday", "tenant": "initech", "shard": "wd5"}), "workday:wd5")
        self.assertEqual(sweep.lane_key({"ats": "greenhouse", "slug": "globex"}), "greenhouse")
        self.assertEqual(sweep.lane_key({"ats": "oracle", "host": "XMPL.fa.us2.oraclecloud.com"}),
                         "xmpl.fa.us2.oraclecloud.com")

    def test_atomic_write_leaves_no_temp(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.json"
            sweep.write_json_atomic(p, {"a": 1})
            self.assertEqual(json.loads(p.read_text(encoding="utf-8")), {"a": 1})
            self.assertEqual([f.name for f in Path(d).iterdir()], ["x.json"])


class PreRank(unittest.TestCase):
    """The lane hint. It may reorder the unscored pile and nothing else: it is not a
    gate, it never filters, and a scored req never shows one."""

    def pre(self, title, desc=""):
        return sweep.prerank({"title": title, "description": desc}, G)

    def test_the_lane_outranks_a_research_role_and_a_sales_role(self):
        lane = self.pre("Technical Program Manager, Finance Systems",
                        "Own the roadmap and backlog for internal tools. ERP, procure-to-pay and "
                        "order-to-cash, NetSuite and Coupa. Work cross-functionally with stakeholders.")
        research = self.pre("Research Scientist, Machine Learning",
                            "PhD required. Publications in deep learning and model training expected.")
        sales = self.pre("Enterprise Account Executive",
                         "Own a quota and pipeline generation against a revenue target.")
        self.assertGreater(lane, research)
        self.assertGreater(lane, sales)

    def test_a_repeated_word_cannot_outrank_a_real_lane_match(self):
        """body_cap: a JD saying 'stakeholder' twenty times is not twenty times the match."""
        spam = self.pre("Program Coordinator", "stakeholder " * 20)
        lane = self.pre("Technical Program Manager", "ERP integration, NetSuite, Coupa.")
        self.assertGreater(lane, spam)

    def test_no_lexicon_configured_means_no_hint(self):
        self.assertIsNone(sweep.prerank({"title": "Product Manager"}, {}))

    def test_a_typo_in_the_lexicon_costs_the_hint_not_the_window(self):
        """gates.json is edited by hand. An unbalanced paren must never take --window down with it."""
        broken = {"prerank": {"title_positive": [["product (manager", 3]], "body_positive": []}}
        sweep.prerank._warned = False
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertIsNone(sweep.prerank({"title": "Product Manager"}, broken))
            self.assertIsNone(sweep.prerank({"title": "Product Manager"}, broken))
        self.assertEqual(out.getvalue().count("WARNING"), 1)      # said once, not per req
        sweep.prerank._warned = False

    def test_a_req_gone_from_the_snapshot_takes_its_title_from_the_stored_jd(self):
        jd = "# Technical Program Manager, Finance Systems\nJob ID: 1 | Remote\n\nERP and Coupa."
        self.assertGreater(sweep.prerank({"title": "", "description": ""}, G, jd),
                           sweep.prerank({"title": "", "description": ""}, G, jd.split("\n", 1)[1]))

    def test_the_window_shows_a_hint_only_for_unscored_reqs(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            saved = {k: getattr(sweep, k) for k in ("DATA", "JDS", "SCORED", "CONTACTS", "TODAY")}
            try:
                sweep.DATA = base / "data"; sweep.JDS = sweep.DATA / "jds"
                sweep.JDS.mkdir(parents=True)
                sweep.SCORED = sweep.DATA / "scored.json"; sweep.CONTACTS = sweep.DATA / "contacts.json"
                sweep.TODAY = dt.date(2026, 9, 2)
                rows = {}
                for i in (1, 2):
                    r = row(company="EnumCo", ats="fake_enum", jid=i,
                            title="Technical Program Manager, Finance Systems",
                            desc="ERP, NetSuite, Coupa, roadmap and backlog. 4+ years.")
                    r["posted"] = "2026-09-01"
                    rows[r["key"]] = r
                (sweep.DATA / "latest.json").write_text(json.dumps({"rows": rows, "ts": "2026-09-02T00:00:00"}), encoding="utf-8")
                scored_key = list(rows)[0]
                sweep.SCORED.write_text(json.dumps({scored_key: {"score": 83, "verdict": "Apply", "scored_on": "2026-09-02"}}), encoding="utf-8")

                def run(gates):
                    out = io.StringIO()
                    with contextlib.redirect_stdout(out): sweep.window_query(gates, "14d")
                    return out.getvalue()

                text = run(G)
                self.assertIn("pre=", text)
                scored_block = text.split(f"key={scored_key}")[1].split("key=")[0]
                self.assertNotIn("pre=", scored_block)          # the score supersedes the hint
                no_lex = {k: v for k, v in G.items() if k != "prerank"}
                self.assertEqual(text.count("    key="), run(no_lex).count("    key="))   # never filters
                self.assertNotIn("pre=", run(no_lex))
            finally:
                for k, v in saved.items(): setattr(sweep, k, v)


class ConfiguredPolicy(unittest.TestCase):
    """Deliberate gate policies, pinned so they do not drift."""

    def test_microsoft_or_equivalent_experience_does_not_soften_the_years(self):
        desc = "Bachelor's degree in Computer Science or equivalent experience. 10+ years of experience."
        self.assertEqual(sweep.gate(row(company="Microsoft", desc=desc), G)[0], "TENURE")
        self.assertEqual(sweep.gate(row(company="Globex Systems", desc=desc), G)[0], "PASS")   # everyone else: unchanged
        # The other escape clauses still work at Microsoft.
        self.assertEqual(sweep.gate(row(company="Microsoft", desc=desc + " Many candidates do not meet every requirement."), G)[0], "PASS")

    def test_a_bare_eight_plus_bar_stays_visible(self):
        v, why = sweep.gate(row(desc="Minimum of 8 years of product experience."), G)
        self.assertEqual(v, "PASS")
        self.assertTrue([w for w in why if w.startswith("REACH: 8+")])
        self.assertEqual(sweep.gate(row(desc="Minimum of 9 years of product experience."), G)[0], "TENURE")

    def test_an_override_engineer_goes_to_the_body_stage_never_pass(self):
        for title in ("Technical Solution AI Engineer, Enterprise Tech", "Solutions Management AI Engineer"):
            self.assertEqual(sweep.gate(row(title=title, desc=""), G)[0], "RESCUE-FETCH", title)   # wants a body read
            v, why = sweep.gate(row(title=title, desc="We sell furniture. 2+ years."), G)
            self.assertEqual(v, "FAIL", title)
        self.assertEqual(sweep.gate(row(title="AI Engineer", desc=""), G), ("FAIL", ["title excluded: ai engineer"]))


class KnownMisses(unittest.TestCase):
    """The recall set: in-lane titles the gate once missed, and near-miss titles that must stay out.
    A hole in the title lexicon never shows up in the PASS pile, so each in-lane title found some
    other way is added here."""

    SEEN = ("Sr Product Mgr", "Lead Product Mgr", "Sr Technical Project Mgr",     # some internal boards abbreviate Mgr
            "Principal Tech Program Mgr, Business Data Technologies",
            "Principal PM, Strategy & Operations, TA-Tech",
            "Manager, People Systems and Automation", "Senior Technical Release Manager",
            "Senior Product Manager, Procure to Pay", "Program Lead, Order to Cash",
            "Sr. PM-T, Finance Systems", "Business Program Management - ERP")
    NOT_SEEN = ("AI Programmer",                                     # "ai program" as a substring
                "Christmas Market Seasonal Associate",               # "hris" as a substring
                "Principal PMM, Growth",                             # marketing
                "Senior Manager, Product Management",                # people management
                "Software Engineering PMTS, Enterprise PKI",         # engineer grade code
                "GTM Systems Manager")                               # GTM systems is configured out of lane

    def test_the_titles_found_outside_the_sweep_are_in_lane(self):
        for title in self.SEEN:
            self.assertEqual(sweep.gate(row(title=title, desc=""), G)[0], "PASS", title)

    def test_the_known_false_matches_stay_out(self):
        for title in self.NOT_SEEN:
            self.assertNotEqual(sweep.gate(row(title=title, desc=""), G)[0], "PASS", title)

    def test_ic_manager_titles_reach_review(self):
        # A real IC req under a manager title reaches REVIEW instead of the title exclude.
        r = row(title="Sr. Manager, Product Management - Model Runtime",
                desc="Align cross-functional teams without direct reporting authority.")
        self.assertEqual(sweep.gate(r, G)[0], "REVIEW")


class WholeWordTitles(unittest.TestCase):
    """A short lane word ('erp' in title_include_words) is matched as a whole word only, so it can name a
    lane without leaking into every title that happens to contain those letters."""

    def test_whole_word_program_titles_are_in_lane(self):
        for title in ("Business Program Management - ERP", "Sr. Program Manager, ERP Modernization"):
            self.assertEqual(sweep.gate(row(title=title, desc=""), G)[0], "PASS", title)

    def test_a_whole_word_does_not_lift_the_engineering_guard(self):
        v, why = sweep.gate(row(title="Senior Software Engineer - ERP", desc=""), G)
        self.assertNotEqual(v, "PASS")

    def test_design_titles_stay_out(self):
        # A whole-word include once admitted 'Senior Product Designer, FDE' and let designers PASS.
        for title in ("Senior Product Designer, ERP", "Product Designer, Internal Tools",
                      "ERP Creative Designer, Ads"):
            self.assertEqual(sweep.gate(row(title=title, desc=""), G)[0], "FAIL", title)

    def test_a_word_inside_a_word_is_not_a_match(self):
        self.assertFalse(sweep._has_word("superpower operations", "erp"))


class LocationParsing(unittest.TestCase):
    """How a location string is split into places. A bad split hides reqs whose TITLE already passed,
    and costs more recall than any gap in the title lexicon."""

    def gate(self, loc):
        return sweep.gate(row(location=loc, desc=""), G)

    def test_a_bare_remote_beside_a_blocked_american_market_is_remote(self):
        for loc in ("New York | Remote", "New York, New York | Remote", "Brooklyn, NY | Remote",
                    "New York, NY (Hybrid) | Remote"):
            v, why = self.gate(loc)
            self.assertEqual(v, "PASS", loc)
            self.assertTrue([w for w in why if w.startswith("VERIFY REMOTE")], loc)     # never silently

    def test_the_blocked_market_alone_still_fails_and_a_foreign_market_lends_nothing(self):
        for loc in ("New York, NY", "New York", "Toronto | Remote", "London, UK | Remote",
                    "New York | Remote - Canada"):
            self.assertEqual(self.gate(loc)[0], "FAIL", loc)

    def test_a_spaced_slash_and_a_lowercase_or_separate_places(self):
        self.assertEqual(self.gate("Jersey City, NJ / Boulder, CO")[0], "PASS")
        self.assertEqual(self.gate("New York, NY or Remote, US")[0], "PASS")
        self.assertEqual(self.gate("Jersey City, NJ / Livingston, NJ")[0], "LOCATION-POLICY")
        self.assertEqual(sweep._loc_segments("Portland, OR"), ["Portland, OR"])          # the state, not the word
        self.assertEqual(sweep._loc_segments("Hybrid/Remote, Austin, TX"), ["Hybrid/Remote, Austin, TX"])

    def test_the_blocked_american_markets_are_a_subset_of_the_fail_list(self):
        self.assertTrue(G["blocked_us_markets"])
        self.assertEqual([m for m in G["blocked_us_markets"] if m not in G["location_fail_any"]], [])


class LiveDataRegressions(unittest.TestCase):
    """Gate defects first seen in live data, reproduced here with invented JD wording."""

    def verdict(self, desc="", title="Product Manager, Finance Systems"):
        return sweep.gate(row(desc=desc, title=title), G)

    def test_an_age_clause_and_company_history_are_not_a_years_bar(self):
        for desc in ("Must be at least 18 years of age. 4+ years of experience in product management.",
                     "For over 20 years, Initech has helped teams. 4+ years of experience.",
                     "For more than 20 years, Contoso Health has led the market. 3+ years of experience.",
                     "In the past 10 years we grew fast. 4+ years experience.",
                     "Named a leader 15 years in a row. 5+ years of experience."):
            v, why = self.verdict(desc)
            self.assertEqual(v, "PASS", desc)
            self.assertFalse([w for w in why if "REACH" in w], desc)
        # The control: the same words in front of a real bar keep it.
        self.assertEqual(self.verdict("For over 12 years of experience in program delivery, you have led releases.")[0], "TENURE")
        self.assertEqual(self.verdict("More than 10 years in product management required.")[0], "TENURE")

    def test_or_equivalent_tools_is_not_an_escape_clause(self):
        for desc in ("10+ years of experience. Familiar with Jira or equivalent tools.",
                     "20+ years of experience. CISSP, CISM, CISA, or equivalent.",
                     "12+ years of experience. MBA or equivalent business training."):
            self.assertEqual(self.verdict(desc)[0], "TENURE", desc)
        for desc in ("10+ years of experience or equivalent experience.",
                     "Bachelor's degree and 10+ years, or an equivalent combination of education and experience.",
                     "12+ years required. Or equivalent practical experience.",
                     "10+ years. Many candidates do not meet every requirement."):
            self.assertEqual(self.verdict(desc)[0], "PASS", desc)

    def test_the_bar_is_read_in_the_shapes_jds_actually_use(self):
        for desc, want in (("Minimum ten (10) years of experience.", 10), ("12 or more years of experience.", 12),
                           ("10 plus years of experience.", 10), ("8+ yrs experience.", 8),
                           ("10+ progressive years of experience.", 10), ("Seven (7) years of experience.", 7),
                           ("Five (5) to seven (7) years of experience.", 5), ("6–10+ years of experience.", 6),
                           ("Top 10 companies for years running.", None)):
            got = sweep.experience_years(desc.lower(), G)
            self.assertEqual(max(got) if got else None, want, desc)

    def test_a_department_name_is_not_an_engineering_title(self):
        for title in ("Principal TPM, Enterprise Engineering", "Business Systems Analyst, Engineering",
                      "Sr. PMT, Developer Tools"):
            self.assertNotEqual(self.verdict(title=title)[1][:1], ["engineering title without product/program work"], title)
        for title in ("Staff Software Engineer, Developer Platform", "Solutions Architects"):
            v, why = self.verdict(title=title)
            self.assertEqual(v, "FAIL", title)

    def test_business_systems_engineering_titles_are_review(self):
        # 'Business Systems Engineer', 'Coupa Procurement Developer' and 'NetSuite Architect' are
        # business-systems work. They get past the engineering-title guard, run the whole gate and
        # land REVIEW, never PASS.
        for title in ("Business Systems Engineer", "Coupa Procurement Developer",
                      "NetSuite Architect (Remote)", "NetSuite Developer"):
            v, why = self.verdict(title=title)
            self.assertEqual(v, "REVIEW", title)
            self.assertTrue(why[0].startswith("business-systems engineering title"), why)
        # The rest of the gate still runs: a blocked-market onsite business-systems title still fails location.
        v, _ = sweep.gate(row(title="Business Systems Engineer", location="New York, NY", desc=""), G)
        self.assertEqual(v, "FAIL")
        # A plain engineering title is untouched.
        self.assertEqual(self.verdict(title="Staff Software Engineer, Developer Platform")[0], "FAIL")

    def test_review_title_on_a_detail_board_gets_its_body(self):
        # A REVIEW title on a detail board needs its JD: with REVIEW left out of DETAIL_VERDICTS the
        # body is never fetched.
        calls = []
        def detail(t, r):
            calls.append(r["key"]); r["description"] = "Own the Coupa procurement catalog. 4+ years."
            return r
        saved = sweep.DETAIL.get("fake_detail")
        sweep.DETAIL["fake_detail"] = detail
        try:
            r = row(ats="fake_detail", title="Coupa Procurement Developer", location="Remote, United States", desc="")
            r["_target"] = {"company": "TestCo", "ats": "fake_detail"}
            verdicts, *_ = sweep.gate_all({r["key"]: r}, {}, G)
            self.assertEqual(calls, [r["key"]])
            self.assertTrue(r["description"])
            self.assertEqual(verdicts[r["key"]][0], "REVIEW")
        finally:
            if saved is None: sweep.DETAIL.pop("fake_detail", None)
            else: sweep.DETAIL["fake_detail"] = saved

    def test_bare_country_location_passes_with_a_verify_flag(self):
        # A bare 'United States' names no onsite market, so it is not LOCATION-POLICY. It passes,
        # flagged for verification.
        for loc in ("United States", "US", "United States, Multiple Locations, Multiple Locations",
                    "Nashville, TN, United States | United States"):
            v, why = sweep.gate(row(location=loc, desc=""), G)
            self.assertEqual(v, "PASS", loc)
            self.assertTrue(any(w.startswith("VERIFY LOCATION") for w in why), (loc, why))
        # A named unendorsed market is still LOCATION-POLICY, however the country is written.
        for loc in ("Austin, Texas, United States", "US, VA, Arlington", "US FL JAX 347", "Chicago, IL, USA"):
            self.assertEqual(sweep.gate(row(location=loc, desc=""), G)[0], "LOCATION-POLICY", loc)
        # Endorsed and remote rows are not flagged as bare.
        for loc in ("Denver, CO, United States", "Remote, United States"):
            v, why = sweep.gate(row(location=loc, desc=""), G)
            self.assertEqual(v, "PASS", loc)
            self.assertFalse(any(w.startswith("VERIFY LOCATION") for w in why), (loc, why))

    def test_interns_new_grads_and_engineer_grade_codes_are_excluded(self):
        for title in ("Finance Systems Engineer, Internship", "Associate Product Manager, New Grad (2027 Start)",
                      "Product Management Co-op", "Software Engineering PMTS, Enterprise PKI"):
            v, why = self.verdict(title=title)
            self.assertEqual((v, why[0][:14]), ("FAIL", "title excluded"), title)
        self.assertEqual(self.verdict(title="Internal Tools Product Manager")[0], "PASS")

    def test_a_no_break_space_in_a_title_is_a_space(self):
        self.assertEqual(self.verdict(title="Portfolio\xa0Program\xa0Manager, AI\xa0Enablement")[0], "PASS")

    def test_level_up_is_read_as_whole_words(self):
        def lvl(title): return sweep.conversion_signals(row(title=title), G, {}, contacts={})["level_up"]
        for title in ("Product Lead", "Staff, Product Manager", "Lead, Finance Systems", "Sr. Manager, AI Product Owner",
                      "Group Product Manager", "Principal Product Manager"):
            self.assertTrue(lvl(title), title)
        for title in ("Chief of Staff, AI", "Member of Technical Staff, Product", "Product Manager, Leadership Tools",
                      "Senior Product Manager"):
            self.assertFalse(lvl(title), title)

    def test_a_canadian_band_is_not_stored_as_comp(self):
        self.assertIsNone(sweep.comp_from_description("Compensation: the base salary range is $150,000 to $200,000 CAD."))
        self.assertTrue(sweep.comp_from_description("Compensation: the base salary range is $150,000 - $200,000 USD."))

    def test_rescue_fail_wording_stays_in_one_funnel_bucket(self):
        # health_lines keys on the text before the first colon; "; body scored 7" made one bucket per score.
        r = row(title="Showroom Coordinator", desc="We sell furniture. 2+ years of experience.")
        v, why = sweep.gate(r, G)
        self.assertEqual(v, "FAIL")
        self.assertIn("body scored", why[0])
        self.assertEqual(why[0].split(":")[0], "title out of lane")

    def test_the_jd_index_finds_ids_with_hyphens_capitals_and_underscored_vendors(self):
        saved = sweep.JDS
        with tempfile.TemporaryDirectory() as d:
            sweep.JDS = Path(d); (sweep.JDS / "2026-09-21").mkdir()
            keys = ("workday:Initech:R100200", "ashby:Globex:0a1b2c3d-4e5f-4a6b-8c9d-0e1f2a3b4c5d",
                    "icims_classic:Litware:100200", "fake_enum:Northwind:100200300-4400")
            try:
                for k in keys:
                    (sweep.JDS / "2026-09-21" / (sweep.jd_stem(k) + ".md")).write_text("# t\n" + k, encoding="utf-8")
                idx = sweep.jd_index()
                for k in keys:
                    self.assertIn(k, sweep.jd_text_for(k, idx) or "", k)
                self.assertIsNone(sweep.jd_text_for("workday:Other:R100200", idx))      # no cross-employer collision
            finally:
                sweep.JDS = saved


class CompanyWholeWords(unittest.TestCase):
    """The Conversion read matches a company to flooded boards and to contacts on whole words. As
    substrings, "meta" matched "Code Metallurgy Example" and gave it a flooded-board drag and another
    employer's contacts."""
    CONTACTS = {"_comment": "notes", "Meta": {"people": [{"name": "Pat Example"}]},
                "Globex": {"people": [{"name": "Sam Placeholder"}]}}

    def signals(self, company):
        return sweep.conversion_signals(row(company=company, title="Senior Product Manager"), G, {},
                                        contacts=self.CONTACTS)

    def test_a_longer_word_is_not_the_board(self):
        s = self.signals("Code Metallurgy Example")
        self.assertFalse(s["flooded_board"])
        self.assertEqual(s["known_contacts"], [])
        self.assertEqual(self.signals("Globexia Labs")["known_contacts"], [])

    def test_whole_names_still_match_both_ways(self):
        s = self.signals("Meta")
        self.assertTrue(s["flooded_board"])
        self.assertEqual(s["known_contacts"], ["Pat Example"])
        self.assertEqual(self.signals("Globex Corporation")["known_contacts"], ["Sam Placeholder"])

    def test_empty_company_matches_nobody(self):
        s = self.signals("")
        self.assertEqual(s["known_contacts"], [])
        self.assertFalse(s["flooded_board"])


class AlreadyApplied(unittest.TestCase):
    def test_a_keyless_tracker_row_is_found_by_the_req_id_in_its_url(self):
        # An application recorded under the short url must match the slugged url the board serves now.
        app = {"company": "Northwind Traders", "title": "Principal PM-T", "key": "",
               "url": "https://www.jobs.northwind.example/en/jobs/40000123"}
        by_url = {sweep.canon_url(app["url"]): app}
        r = row(company="Northwind Traders", ats="fake_query", jid=40000123)
        r["url"] = "https://www.jobs.northwind.example/en/jobs/40000123/principal-product-manager-technical-finance-systems"
        self.assertIs(sweep.applied_row("fake_query:Northwind Traders:40000123", r, {}, by_url), app)
        other = row(company="Northwind Traders", ats="fake_query", jid=40000456)
        other["url"] = r["url"].replace("40000123", "40000456")
        self.assertIsNone(sweep.applied_row("fake_query:Northwind Traders:40000456", other, {}, by_url))
        elsewhere = row(company="Litware", ats="fake_query", jid=40000123); elsewhere["url"] = "https://example.test/40000123"
        self.assertIsNone(sweep.applied_row("fake_query:Litware:40000123", elsewhere, {}, by_url))

    def test_tracking_parameters_do_not_hide_an_application(self):
        app = {"company": "ExampleCo", "title": "x", "key": "", "url": "https://boards.greenhouse.io/exampleco/jobs/1000000001?gh_src=abc"}
        r = row(company="ExampleCo", jid=1000000001); r["url"] = "https://job-boards.greenhouse.io/exampleco/jobs/1000000001"
        self.assertIs(sweep.applied_row("greenhouse:ExampleCo:1000000001", r, {}, {sweep.canon_url(app["url"]): app}), app)


class AtomicWrite(unittest.TestCase):
    def test_a_held_destination_is_retried_and_a_lost_cause_leaves_no_temp_file(self):
        real_replace, real_sleep = sweep.os.replace, sweep.time.sleep
        sweep.time.sleep = lambda s: None
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "x.json"
            calls = []
            def flaky(src, dst):
                calls.append(1)
                if len(calls) < 3: raise PermissionError(13, "Access is denied")
                return real_replace(src, dst)
            try:
                sweep.os.replace = flaky
                sweep.write_json_atomic(target, {"a": 1})
                self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"a": 1})
                self.assertEqual(len(calls), 3)
                def never(src, dst): raise PermissionError(13, "Access is denied")
                sweep.os.replace = never
                with self.assertRaises(PermissionError) as cm:
                    sweep.write_json_atomic(target, {"a": 2})
                self.assertIn("--resume", str(cm.exception))
                self.assertEqual(list(Path(d).glob("*.tmp")), [])              # no orphaned temp file
                self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"a": 1})   # old file intact
            finally:
                sweep.os.replace, sweep.time.sleep = real_replace, real_sleep


class SetScore(unittest.TestCase):
    """--set-score parsing. A colon inside the verdict survives, and a malformed conv= suffix raises
    instead of silently storing nothing."""

    def test_a_colon_in_the_verdict_survives(self):
        key, score, verdict, built, conv = sweep.parse_set_score(
            "pcsx:ExampleCo:1000000002=81:Apply: needs a check: on location:built:conv=MEDIUM")
        self.assertEqual(key, "pcsx:ExampleCo:1000000002")
        self.assertEqual((score, built, conv), (81, True, "MEDIUM"))
        self.assertEqual(verdict, "Apply: needs a check: on location")

    def test_suffix_order_and_case_do_not_matter(self):
        self.assertEqual(sweep.parse_set_score("k:A:1=80:Apply:CONV=low:Built")[2:], ("Apply", True, "LOW"))

    def test_a_ratio_in_the_verdict_is_not_a_built_flag(self):
        # "1" and "true" used to mean built, so a verdict ending "8:1" flipped the flag.
        self.assertEqual(sweep.parse_set_score("k:A:1=70:Maybe, ratio 8:1")[2:], ("Maybe, ratio 8:1", None, None))

    def test_the_wrong_conv_separator_is_rejected(self):
        for bad in ("k:A:1=80:Apply=conv=LOW", "k:A:1=80:Apply conv=LOW", "k:A:1=80:Apply:conv=LOW:then more"):
            with self.assertRaises(ValueError, msg=bad):
                sweep.parse_set_score(bad)

    def test_bad_labels_scores_and_keys_are_rejected(self):
        for bad in ("k:A:1=80:Apply:conv=MED", "k:A:1=eighty:Apply", "k:A:1=180:Apply", "=80:Apply",
                    "no-equals-sign", "k:A:1=80:Apply=built"):
            with self.assertRaises(ValueError, msg=bad):
                sweep.parse_set_score(bad)

    def test_a_conv_only_rescore_does_not_unbuild_and_an_empty_verdict_is_kept(self):
        saved = sweep.SCORED
        with tempfile.TemporaryDirectory() as d:
            sweep.SCORED = Path(d) / "scored.json"
            try:
                sweep.set_score(*sweep.parse_set_score("k:A:1=84:Apply, referral open:built")[:3], built=True)
                key, score, verdict, built, conv = sweep.parse_set_score("k:A:1=84::conv=MEDIUM")
                e = sweep.set_score(key, score, verdict, built=built, conversion=conv)
                self.assertTrue(e["built"])                                  # was reset to False
                self.assertEqual(e["verdict"], "Apply, referral open")       # was blanked
                self.assertEqual(e["conversion"], "MEDIUM")
                self.assertFalse(sweep.set_score(*sweep.parse_set_score("k:A:1=84::unbuilt")[:3], built=False)["built"])
                with self.assertRaises(ValueError):
                    sweep.set_score("k:A:2", 70, None)                       # nothing stored to keep
            finally:
                sweep.SCORED = saved

    def _cli(self, entries, rows=None):
        """cmd_set_score in a temp data dir; returns (scored.json contents, printed text)."""
        import contextlib, io
        saved = sweep.SCORED, sweep.DATA
        with tempfile.TemporaryDirectory() as d:
            sweep.DATA, sweep.SCORED = Path(d), Path(d) / "scored.json"
            (Path(d) / "latest.json").write_text(json.dumps({"rows": rows or {}}), encoding="utf-8")
            (Path(d) / "seen.json").write_text(json.dumps({k: "2030-01-02" for k in (rows or {})}), encoding="utf-8")
            out = io.StringIO()
            try:
                with contextlib.redirect_stdout(out):
                    sweep.cmd_set_score(entries, G)
                return sweep.load_scored(), out.getvalue()
            finally:
                sweep.SCORED, sweep.DATA = saved

    def test_every_repeated_flag_is_stored(self):
        # A single-valued flag kept only the last of several --set-score flags, silently.
        d, _ = self._cli(["k:A:1=80:Apply", "k:A:2=62:Maybe", "k:A:3=40:Skip"])
        self.assertEqual({k: v["score"] for k, v in d.items()}, {"k:A:1": 80, "k:A:2": 62, "k:A:3": 40})

    def test_one_bad_entry_writes_nothing(self):
        with self.assertRaises(SystemExit):
            self._cli(["k:A:1=80:Apply", "k:A:2=eighty:Maybe"])
        with self.assertRaises(SystemExit):
            self._cli(["k:A:1=80:Apply", "k:A:1=70:Maybe"])                  # same key twice

    def test_a_hand_label_that_disagrees_with_the_signals_is_named(self):
        r = row(title="Staff Product Manager", location="Remote, US", desc="10+ years of product management experience.")
        computed = sweep.conversion_signals(dict(r, key="k:A:1"), G)["label"]
        other = next(x for x in sweep.CONVERSION_LABELS if x != computed)
        _, out = self._cli([f"k:A:1=82:Apply:conv={other}"], rows={"k:A:1": r})
        self.assertIn(f"conv={other} but the sweep computes {computed}", out)
        _, out = self._cli(["k:A:1=82:Apply"], rows={"k:A:1": r})
        self.assertIn(f"Add :conv={computed}", out)


class StateCodeMarkets(unittest.TestCase):
    """endorsed_state_codes and endorsed_exact_segments. Boards also write Canada as "CA", so a
    Canadian segment must never endorse on the California code, and "New York" as a whole segment is
    the city while the same words inside a segment can be the state."""
    G2 = dict(G, endorsed_state_codes=["CA"], endorsed_exact_segments=["new york"],
              endorsed_state_code_not_if_codes=["AB", "BC", "MB", "ON", "QC", "SK"],
              location_fail_any=[x for x in G["location_fail_any"] if x not in G["blocked_us_markets"]],
              blocked_us_markets=[])

    def verdict(self, loc, g=None):
        return sweep.gate(row(location=loc, desc=""), g or self.G2)

    def test_a_state_code_endorses_any_city_in_it(self):
        self.assertEqual(self.verdict("Oakland, CA")[0], "PASS")
        self.assertEqual(self.verdict("Oakland, CA", G)[0], "LOCATION-POLICY")          # stock gates: off

    def test_ca_meaning_canada_never_endorses(self):
        self.assertEqual(self.verdict("Montreal, QC, CA")[0], "LOCATION-POLICY")
        v, why = self.verdict("Calgary, AB, CA")                                   # the endorsed foreign path
        self.assertEqual(v, "PASS")
        self.assertTrue(any(w.startswith("KNOCKOUT") for w in why), why)

    def test_an_exact_segment_is_the_city_not_the_state(self):
        self.assertEqual(self.verdict("New York")[0], "PASS")
        self.assertEqual(self.verdict("Tarrytown, New York")[0], "LOCATION-POLICY")


class SweepSandbox(unittest.TestCase):
    """End-to-end run() against fake adapters in a temp data dir, across simulated days."""

    PATCH = ("DATA", "JDS", "REPORTS", "READLOG", "SCORED", "CONTACTS", "TODAY")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.saved = {k: getattr(sweep, k) for k in self.PATCH}
        self.saved_adapters = (dict(sweep.ADAPTERS), dict(sweep.DETAIL), set(sweep.ENUMERABLE_ATS))
        self.saved_http = (sweep.get, sweep.get_conditional, set(sweep.ETAG_ATS), sweep.iso_now)
        self.G = G
        sweep.DATA = base / "data"; sweep.JDS = sweep.DATA / "jds"; sweep.REPORTS = base / "reports"
        for p in (sweep.DATA, sweep.JDS, sweep.REPORTS): p.mkdir(parents=True)
        sweep.READLOG = sweep.DATA / "read_log.json"; sweep.SCORED = sweep.DATA / "scored.json"
        sweep.CONTACTS = sweep.DATA / "contacts.json"
        sweep.TODAY = dt.date(2026, 9, 1)
        self.boards = {}                 # company -> list of rows, or an Exception to raise
        self.detail_ok = True
        os.environ["MAXQ_SERIAL"] = "1"
        self.orig_sleep = sweep.time.sleep
        sweep.time.sleep = lambda s: None        # the errored-employer retry pauses 20s

        def lister(t, smoke=False):
            v = self.boards[t["company"]]
            if isinstance(v, Exception): raise v
            return [dict(r, extra=dict(r["extra"])) for r in v]

        def detail(t, r):
            if not self.detail_ok: raise ConnectionError("detail down")
            r["description"] = "NetSuite Coupa adoption program. 4+ years."
            r["posted"] = sweep.TODAY.isoformat()
            return r

        sweep.ADAPTERS.update({"fake_enum": lister, "fake_query": lister, "fake_detail": lister})
        sweep.DETAIL["fake_detail"] = detail
        sweep.ENUMERABLE_ATS.add("fake_enum")
        self.targets = [{"company": "EnumCo", "ats": "fake_enum", "verified": True},
                        {"company": "QueryCo", "ats": "fake_query", "verified": True},
                        {"company": "FlakyCo", "ats": "fake_enum", "verified": True},
                        {"company": "DetailCo", "ats": "fake_detail", "verified": True}]

    def tearDown(self):
        for k, v in self.saved.items(): setattr(sweep, k, v)
        sweep.ADAPTERS.clear(); sweep.ADAPTERS.update(self.saved_adapters[0])
        sweep.DETAIL.clear(); sweep.DETAIL.update(self.saved_adapters[1])
        sweep.ENUMERABLE_ATS.clear(); sweep.ENUMERABLE_ATS.update(self.saved_adapters[2])
        sweep.get, sweep.get_conditional, _ats, sweep.iso_now = self.saved_http
        sweep.ETAG_ATS.clear(); sweep.ETAG_ATS.update(_ats)
        os.environ.pop("MAXQ_SERIAL", None)
        sweep.time.sleep = self.orig_sleep
        self.tmp.cleanup()

    def etag_setup(self, trust=False):
        """One employer on a fake ETag board. The lister goes through sweep.etag_get exactly as the
        greenhouse/ashby/lever adapters do; the HTTP layer answers 304 whenever the sent tag is the
        board's current one, whatever the rows say (that is what a lying vendor looks like)."""
        self.tag, self.cond_fails, self.plain_fails = 'W/"v1"', False, False
        self.boards = {"EtagCo": self.rows_for("EtagCo", "fake_etag", [1, 2])}

        def cond(url, etag, headers=None):
            if self.cond_fails: raise ConnectionError("board down")
            if etag and etag == self.tag: return 304, None, etag
            return 200, self.boards["EtagCo"], self.tag

        def plain(url, headers=None, **kw):
            if self.plain_fails: raise ConnectionError("plain read down")
            return self.boards["EtagCo"]

        def lister(t, smoke=False):
            d = sweep.etag_get(t, "https://fake.test/EtagCo")
            if d is sweep.BOARD_UNCHANGED: return d
            return [dict(r, extra=dict(r["extra"])) for r in d]

        sweep.get_conditional, sweep.get = cond, plain
        sweep.iso_now = lambda: f"{sweep.TODAY.isoformat()}T12:00:00+00:00"     # a clock the day can move
        sweep.ADAPTERS["fake_etag"] = lister
        sweep.ENUMERABLE_ATS.add("fake_etag"); sweep.ETAG_ATS.add("fake_etag")
        self.targets = [{"company": "EtagCo", "ats": "fake_etag", "verified": True}]
        self.G = dict(G, etag_trust=trust, etag_trust_ats=["fake_etag"])

    def test_empty_ok_board_reads_ok_but_plain_verified_empty_still_errors(self):
        # A board can be legitimately empty (the employer's own careers page reads the same board).
        # empty_ok marks that one target; every other verified board that reads empty still errors.
        self.boards = {"EnumCo": self.rows_for("EnumCo", "fake_enum", [1]), "EmptyOk": [], "EmptyBad": []}
        self.targets = [{"company": "EnumCo", "ats": "fake_enum", "verified": True},
                        {"company": "EmptyOk", "ats": "fake_enum", "verified": True, "empty_ok": True},
                        {"company": "EmptyBad", "ats": "fake_enum", "verified": True}]
        snap = self.sweep_day(1)
        cov = {c: s for c, a, s, n in snap["coverage"]}
        self.assertTrue(cov["EmptyOk"].startswith("OK (empty board"))
        self.assertTrue(cov["EmptyBad"].startswith("ERROR EmptyBoard"))

    def readlog(self, company="EtagCo"):
        return json.loads(sweep.READLOG.read_text(encoding="utf-8"))[company]

    def rows_for(self, company, ats, ids, desc="NetSuite adoption program. 3+ years."):
        return [row(company=company, ats=ats, jid=i, title=f"Product Manager {i}, Finance Systems", desc=desc)
                for i in ids]

    def sweep_day(self, day, **kw):
        sweep.TODAY = dt.date(2026, 9, day)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            sweep.run(self.targets, self.G, digest=True, fresh=True, **kw)
        self.out = out.getvalue()
        return json.loads((sweep.DATA / "latest.json").read_text(encoding="utf-8"))

    def test_a_repost_after_a_gap_is_caught_and_stays_flagged(self):
        # Repost detection looks further back than one snapshot and remembers what it found: a req
        # pulled on day 2 and reposted on day 3 under a new id is a repost, and still one on day 4.
        self.targets = [t for t in self.targets if t["company"] == "EnumCo"]
        def pm(i):
            return row(company="EnumCo", ats="fake_enum", jid=i, title="Product Manager, Finance Systems",
                       desc="NetSuite adoption program. 3+ years.")
        keep = self.rows_for("EnumCo", "fake_enum", [2, 3])
        self.boards = {"EnumCo": [pm(1)] + keep}
        self.sweep_day(1)
        self.boards = {"EnumCo": keep}
        self.sweep_day(2)                                        # id 1 closes
        self.boards = {"EnumCo": [pm(9)] + keep}
        s3 = self.sweep_day(3)
        self.assertEqual(s3["reposts"].get("fake_enum:EnumCo:9"), "fake_enum:EnumCo:1")
        s4 = self.sweep_day(4)
        self.assertEqual(s4["reposts"].get("fake_enum:EnumCo:9"), "fake_enum:EnumCo:1")

    def day_one(self):
        self.boards = {"EnumCo": self.rows_for("EnumCo", "fake_enum", [1, 2, 3]),
                       "QueryCo": self.rows_for("QueryCo", "fake_query", [1, 2]),
                       "FlakyCo": self.rows_for("FlakyCo", "fake_enum", [1, 2]),
                       "DetailCo": self.rows_for("DetailCo", "fake_detail", [1], desc="")}
        return self.sweep_day(1)

    def test_the_rescue_budget_advances_instead_of_rereading_the_same_reqs(self):
        """A rescued body under the floor is a "title ..." FAIL, so its text is stripped. The next run
        must not read that as "no body" and put the req back in the pool with the same title score, or
        the same top-of-pool reqs win the fetch budget every day and the backlog never shrinks.
        Also: no RESCUE-FETCH may survive into the stored verdicts or the funnel."""
        import copy
        g = copy.deepcopy(G)
        g["rescue"] = dict(g.get("rescue") or {}, enabled=True, max_fetch_per_run=2, max_fetch_per_employer=5)
        fetched = []
        def detail(t, r):
            fetched.append(r["key"])
            r["description"] = "We sell patio furniture in showrooms. 2+ years of retail experience."
        sweep.DETAIL["fake_detail"] = detail
        titles = ["Showroom Coordinator", "Patio Furniture Buyer", "Catalog Copywriter"]
        self.boards = {"EnumCo": [], "QueryCo": [], "FlakyCo": [],
                       "DetailCo": [row(company="DetailCo", ats="fake_detail", jid=i, title=t, desc="")
                                    for i, t in enumerate(titles, 1)]}
        self.targets = [t for t in self.targets if t["company"] == "DetailCo"]
        per_day = []
        for day in (1, 2, 3):
            fetched.clear()
            sweep.TODAY = dt.date(2026, 9, day)
            with contextlib.redirect_stdout(io.StringIO()):
                sweep.run(self.targets, g, digest=True, fresh=True)
            per_day.append(sorted(fetched))
            snap = json.loads((sweep.DATA / "latest.json").read_text(encoding="utf-8"))
            self.assertNotIn("RESCUE-FETCH", set(snap["verdicts"].values()))
        self.assertEqual([len(d) for d in per_day], [2, 1, 0])            # not [2, 2, 2], the same two
        self.assertEqual(len(set(per_day[0]) | set(per_day[1])), 3)       # every req read exactly once
        self.assertTrue(all("_rescue" in r and not r.get("description") for r in snap["rows"].values()))

    ABOUT = "About EnumCo: we are an equal opportunity employer building delightful software for everyone, everywhere."

    def jd_rows(self, ids, about=None, own="Own the NetSuite adoption program end to end. 3+ years of experience."):
        about = self.ABOUT if about is None else about
        return [row(company="EnumCo", ats="fake_enum", jid=i, title=f"Product Manager {i}, Finance Systems",
                    desc=f"{own} Req number {i} in the enablement group.\n{about}") for i in ids]

    def changed_after(self, day_two_rows):
        self.targets = [t for t in self.targets if t["company"] == "EnumCo"]
        self.boards = {"EnumCo": self.jd_rows([1, 2, 3])}
        self.sweep_day(1)
        self.boards = {"EnumCo": day_two_rows}
        return self.sweep_day(2)["changed_jds"]

    def test_unchanged_jds_are_not_flagged_when_the_employers_req_population_moves(self):
        """The core hash strips lines shared by 30% of the employer's reqs IN THAT RUN. Ten new reqs
        without the About line must not turn it from boilerplate into content and move every old
        hash, which flags byte-identical JDs as changed."""
        others = [row(company="EnumCo", ats="fake_enum", jid=i, title=f"Product Manager {i}, Finance Systems",
                      desc=f"A different team entirely, req {i}. 3+ years of experience.") for i in range(10, 20)]
        self.assertEqual(self.changed_after(self.jd_rows([1, 2, 3]) + others), [])

    def test_a_boilerplate_only_edit_is_still_suppressed(self):
        edited = self.ABOUT.replace("delightful", "remarkable")
        self.assertEqual(self.changed_after(self.jd_rows([1, 2, 3], about=edited)), [])

    def test_a_raised_bar_is_flagged_even_while_the_population_moves(self):
        others = [row(company="EnumCo", ats="fake_enum", jid=i, title=f"Product Manager {i}, Finance Systems",
                      desc=f"A different team entirely, req {i}. 3+ years of experience.") for i in range(10, 20)]
        day2 = self.jd_rows([1, 3]) + self.jd_rows([2], own="Own the NetSuite adoption program end to end. 7+ years of experience.") + others
        self.assertEqual(self.changed_after(day2), ["fake_enum:EnumCo:2"])

    def test_an_only_run_does_not_carry_every_other_employer(self):
        # carry_forward honours `only`: otherwise a partial run carries the whole previous snapshot
        # and prints every other employer's reqs as read.
        self.day_one()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            sweep.run(self.targets, G, only="EnumCo", fresh=True)
        text = out.getvalue()
        self.assertIn("PARTIAL RUN (--only EnumCo): 3 reqs read", text)
        self.assertNotIn("QueryCo", text.split("PARTIAL RUN")[1])

    def test_a_collapsed_count_carries_the_missing_reqs_instead_of_closing_them(self):
        """40 -> 10 -> 40: a run whose health block warns "-75%" must not also close the 30 missing
        reqs, or all 30 come back as newly discovered when the board reads whole again."""
        self.targets = [t for t in self.targets if t["company"] == "EnumCo"]
        whole = self.rows_for("EnumCo", "fake_enum", range(1, 41))
        self.boards = {"EnumCo": whole}
        self.sweep_day(1)
        self.boards = {"EnumCo": whole[:10]}
        s2 = self.sweep_day(2)
        self.assertEqual(len(s2["closed"]), 0)
        self.assertEqual(sum(1 for r in s2["rows"].values() if r.get("_carried_reason") == sweep.CARRY_DROP), 30)
        self.boards = {"EnumCo": whole}
        s3 = self.sweep_day(3)
        self.assertEqual(s3["new_keys"], [])                      # not 30 "newly discovered"
        self.assertFalse([r for r in s3["rows"].values() if r.get("_carried_since")])

    def test_a_board_that_really_shrank_closes_on_the_next_clean_read(self):
        self.targets = [t for t in self.targets if t["company"] == "EnumCo"]
        whole = self.rows_for("EnumCo", "fake_enum", range(1, 41))
        self.boards = {"EnumCo": whole}
        self.sweep_day(1)
        self.boards = {"EnumCo": whole[:10]}
        self.sweep_day(2)
        s3 = self.sweep_day(3)                                     # still 10: no longer a drop against the last read
        self.assertEqual(len(s3["rows"]), 10)
        self.assertEqual(len(s3["closed"]), 30)

    def test_a_workday_board_that_gains_the_country_scope_is_not_a_count_drop(self):
        """The first country-scoped read of a Workday tenant returns about half its rows by
        design. That must not print the -50% truncation warning, must not carry the abroad rows as a
        suspected short read, and must keep every gate-passing US req. The real workday adapter runs
        against a fake CXS endpoint; the board grows its country facet on day 2."""
        board = WorkdayCountryScope.Board(facet=None)
        us = [("/job/p_%d" % i, {"US"}) for i in range(1, 31)]
        abroad = [("/job/p_%d" % i, {"XX"}) for i in range(31, 61)]
        board.rows = us + abroad
        locs = {p: ("Remote, United States" if "US" in cs else "London, United Kingdom") for p, cs in board.rows}
        real_call = board.__call__
        def cxs(url, body, headers=None):
            d = real_call(url, body, headers)
            for p in d["jobPostings"]:
                p["title"], p["locationsText"] = "Product Manager, Finance Systems", locs[p["externalPath"]]
            return d
        def detail(t, r):
            r["description"] = "NetSuite Coupa adoption program. 4+ years."
            r["posted"] = sweep.TODAY.isoformat()
            return r
        saved = sweep.post_json, sweep.DETAIL["workday"]
        sweep.post_json, sweep.DETAIL["workday"] = cxs, detail
        try:
            self.targets = [{"company": "WdCo", "ats": "workday", "tenant": "x", "shard": "wd1", "site": "S",
                             "verified": True}]
            s1 = self.sweep_day(1)
            self.assertEqual(len(s1["rows"]), 60)
            pass1 = {k for k, v in s1["verdicts"].items() if v == "PASS"}
            self.assertEqual(len(pass1), 30)
            board.facet = "locationCountry"                      # the tenant now exposes a country facet
            s2 = self.sweep_day(2)
            cov = {c[0]: c[2] for c in s2["coverage"]}
            self.assertIn("country-scoped: 30 of 60 board rows", cov["WdCo"])
            self.assertFalse([w for w in s2["health"]["warnings"] if "returned 30 reqs against 60" in w])
            self.assertEqual(s2["health"]["count_drops"], [])
            self.assertEqual({k for k, v in s2["verdicts"].items() if v == "PASS"}, pass1)
            reasons = collections.Counter(r.get("_carried_reason") for r in s2["rows"].values())
            self.assertEqual(reasons[sweep.CARRY_SCOPE], 30)         # abroad: carried, not "truncated"
            self.assertEqual(reasons[sweep.CARRY_DROP], 0)
            self.assertIn("30 from country-scoped Workday reads", self.out)
            readlog = json.loads(sweep.READLOG.read_text(encoding="utf-8"))
            self.assertEqual(readlog["WdCo"]["scope"], "US+CA")
            self.assertEqual(readlog["WdCo"]["count"], 30)
            board.rows = us[:10] + abroad                         # day 3: a real collapse under the same scope
            s3 = self.sweep_day(3)
            self.assertEqual(s3["health"]["count_drops"], [["WdCo", 30, 10]])
        finally:
            sweep.post_json, sweep.DETAIL["workday"] = saved

    def test_a_crash_in_write_report_cannot_turn_into_a_quiet_day_on_resume(self):
        """latest.json is written, write_report raises, the checkpoints stay. --resume must not then
        load TODAY's snapshot as "previous", find nothing new, and write a report that omits the
        day's new req."""
        self.targets = [t for t in self.targets if t["company"] == "EnumCo"]
        self.boards = {"EnumCo": self.rows_for("EnumCo", "fake_enum", [1, 2, 3])}
        self.sweep_day(1)
        self.boards = {"EnumCo": self.rows_for("EnumCo", "fake_enum", [1, 2, 3, 4])}
        real = sweep.write_report
        def boom(*a, **k): raise ValueError("report crashed")
        sweep.write_report = boom
        try:
            with self.assertRaises(ValueError):
                self.sweep_day(2)
        finally:
            sweep.write_report = real
        snap = json.loads((sweep.DATA / "latest.json").read_text(encoding="utf-8"))
        self.assertEqual(snap["new_keys"], ["fake_enum:EnumCo:4"])                    # state was saved
        self.assertEqual(list((sweep.DATA / "checkpoint" / "2026-09-02").glob("*.json")), [])
        s = self.sweep_day(2, resume=True)                                            # the documented recovery
        self.assertEqual(s["new_keys"], ["fake_enum:EnumCo:4"])                       # not []
        report = (sweep.REPORTS / "SWEEP_REPORT_2026-09-02.md").read_text(encoding="utf-8")
        self.assertIn("Product Manager 4", report.split("## 2.")[0])

    def test_report_only_keeps_the_flips_the_sweep_found(self):
        self.boards = {"EnumCo": [row(company="EnumCo", ats="fake_enum", jid=1,
                                      title="Product Manager, Finance Systems", location="Munich, de")],
                       "QueryCo": [], "FlakyCo": [], "DetailCo": []}
        self.sweep_day(1)
        self.boards["EnumCo"] = [row(company="EnumCo", ats="fake_enum", jid=1,
                                     title="Product Manager, Finance Systems", location="Munich, de | Denver, CO")]
        s2 = self.sweep_day(2)
        self.assertEqual(s2["flipped"], {"fake_enum:EnumCo:1": "FAIL"})
        (sweep.REPORTS / "SWEEP_REPORT_2026-09-02.md").unlink()
        with contextlib.redirect_stdout(io.StringIO()):
            sweep.report_only(G)
        rebuilt = (sweep.REPORTS / "SWEEP_REPORT_2026-09-02.md").read_text(encoding="utf-8")
        self.assertIn("newly eligible: FAIL -> PASS", rebuilt.split("## 2.")[0])     # survives a rebuild

    def test_req_that_becomes_gate_passing_is_reported_not_buried(self):
        """Section 1 does not key on new_keys alone: a req that turns PASS for any reason other than
        discovery would otherwise go straight into section 4's aging list unannounced. Here the
        location is unusable on day 1 and a later read adds a US one, as when a board adds a second
        office city to an open req."""
        self.boards = {"EnumCo": [row(company="EnumCo", ats="fake_enum", jid=1,
                                      title="Product Manager, Finance Systems", location="Munich, de")],
                       "QueryCo": [], "FlakyCo": [], "DetailCo": []}
        s1 = self.sweep_day(1)
        self.assertEqual(s1["verdicts"]["fake_enum:EnumCo:1"], "FAIL")
        r1 = (sweep.REPORTS / "SWEEP_REPORT_2026-09-01.md").read_text(encoding="utf-8")
        self.assertIn("## 1. New or newly eligible, gate-passing", r1)

        self.boards["EnumCo"] = [row(company="EnumCo", ats="fake_enum", jid=1,
                                     title="Product Manager, Finance Systems",
                                     location="Munich, de | Denver, CO")]
        s2 = self.sweep_day(2)
        k = "fake_enum:EnumCo:1"
        self.assertEqual(s2["verdicts"][k], "PASS")
        self.assertNotIn(k, s2["new_keys"])          # not a new discovery: the id never changed
        r2 = (sweep.REPORTS / "SWEEP_REPORT_2026-09-02.md").read_text(encoding="utf-8")
        sec1 = r2.split("## 2.")[0]
        self.assertIn("EnumCo", sec1)                        # reported, not buried
        self.assertIn("newly eligible: FAIL -> PASS", sec1)
        aging = r2.split("## 4. Still open")[1].split("## 5.")[0]
        self.assertNotIn(k, aging)                           # and not listed twice

    def test_became_ignores_keys_absent_from_the_previous_run(self):
        """A key with no previous verdict is not a flip. Without this, the first run after any
        snapshot change reports every row as newly eligible and buries the real ones."""
        rows = {"a": {}, "b": {}}
        verdicts = {"a": ("PASS", []), "b": ("PASS", [])}
        self.assertEqual(sweep.became(rows, verdicts, [], {"a": "TENURE"}, ("PASS",)), ["a"])
        self.assertEqual(sweep.became(rows, verdicts, [], {}, ("PASS",)), [])
        self.assertEqual(sweep.became(rows, verdicts, ["a"], {"a": "TENURE"}, ("PASS",)), [])

    def test_errored_and_query_scoped_rows_are_not_closed(self):
        s1 = self.day_one()
        self.assertEqual(len(s1["rows"]), 8)
        self.assertEqual(s1["rows"]["fake_detail:DetailCo:1"]["posted"], "2026-09-01")
        self.boards["EnumCo"] = self.boards["EnumCo"][:2]          # req 3 genuinely closed
        self.boards["QueryCo"] = self.boards["QueryCo"][:1]        # req 2 missing from a sample
        self.boards["FlakyCo"] = ConnectionError("RemoteDisconnected")
        s2 = self.sweep_day(2)
        closed = set(s2["closed"])
        self.assertEqual(closed, {"fake_enum:EnumCo:3"})
        self.assertIn("fake_query:QueryCo:2", s2["rows"])
        self.assertEqual(s2["rows"]["fake_query:QueryCo:2"]["_carried_reason"], sweep.CARRY_SAMPLE)
        for k in ("fake_enum:FlakyCo:1", "fake_enum:FlakyCo:2"):
            self.assertEqual(s2["rows"][k]["_carried_reason"], sweep.CARRY_ERROR)
        self.assertEqual(s2["new_keys"], [])                        # nothing re-announced as new
        self.assertTrue(any("UNCOVERED" in w and "FlakyCo" in w for w in s2["health"]["warnings"]))
        self.assertIn("SWEEP HEALTH", self.out)
        report = (sweep.REPORTS / "SWEEP_REPORT_2026-09-02.md").read_text(encoding="utf-8")
        self.assertIn("## Sweep health", report)

    def test_carry_expires(self):
        self.day_one()
        self.boards["QueryCo"] = self.rows_for("QueryCo", "fake_query", [9])
        self.sweep_day(2)
        s = self.sweep_day(2 + sweep.CARRY_MAX_DAYS + 1)
        self.assertNotIn("fake_query:QueryCo:1", s["rows"])
        self.assertIn("fake_query:QueryCo:1", s["closed"])

    def test_failed_detail_inherits_and_is_not_a_jd_change(self):
        # A failed detail fetch inherits the stored body and date; it is not a JD change.
        self.day_one()
        self.detail_ok = False
        s2 = self.sweep_day(2)
        r = s2["rows"]["fake_detail:DetailCo:1"]
        self.assertTrue(r["description"])
        self.assertEqual(r["posted"], "2026-09-01")
        self.assertEqual(s2["changed_jds"], [])

    def test_resume_reuses_checkpoints(self):
        self.day_one()
        orig = sweep.sweep_health
        sweep.sweep_health = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("crash after reads"))
        try:
            with self.assertRaises(RuntimeError), contextlib.redirect_stdout(io.StringIO()):
                sweep.TODAY = dt.date(2026, 9, 2)
                sweep.run(self.targets, G, fresh=True)
        finally:
            sweep.sweep_health = orig
        ck = sweep.DATA / "checkpoint" / "2026-09-02"
        self.assertEqual(len(list(ck.glob("*.json"))), 4)
        for c in self.boards: self.boards[c] = AssertionError(f"{c} re-read despite checkpoint")
        s = self.sweep_day(2, resume=True, force=True)
        self.assertEqual(len(s["rows"]), 8)
        self.assertTrue(all("resumed" in c[2] for c in s["coverage"]))
        self.assertEqual(list(ck.glob("*.json")), [])

    def test_out_of_lane_text_is_stripped_but_hash_kept(self):
        self.day_one()
        self.boards["EnumCo"].append(row(company="EnumCo", ats="fake_enum", jid=9, title="Staff Backend Engineer",
                                         desc="Go and Kubernetes. 5+ years."))
        s = self.sweep_day(2)
        off = s["rows"]["fake_enum:EnumCo:9"]
        self.assertEqual(off["description"], "")
        self.assertTrue(off["_text_stripped"])
        self.assertEqual(off["jd_hash"], sweep.jd_hash("Go and Kubernetes. 5+ years."))
        self.assertTrue(s["rows"]["fake_enum:EnumCo:1"]["description"])        # in-lane text kept

    def test_detail_reused_when_list_unchanged(self):
        calls = {"n": 0}
        orig = sweep.DETAIL["fake_detail"]
        def counting(t, r):
            calls["n"] += 1
            return orig(t, r)
        sweep.DETAIL["fake_detail"] = counting
        self.day_one()
        self.assertEqual(calls["n"], 1)
        s2 = self.sweep_day(2)
        self.assertEqual(calls["n"], 1)                         # reused, not refetched
        self.assertIn("reused", self.out)
        self.assertEqual(s2["rows"]["fake_detail:DetailCo:1"]["posted"], "2026-09-01")
        self.boards["DetailCo"][0]["title"] = "Product Manager 1, Finance Systems (Retitled)"
        self.sweep_day(3)
        self.assertEqual(calls["n"], 2)                         # list changed: fetched again
        self.sweep_day(3 + sweep.DETAIL_TTL_DAYS)
        self.assertEqual(calls["n"], 3)                         # detail aged out: fetched again

    def test_incremental_read_stops_at_known_reqs(self):
        pages = {"n": 0}
        board = self.rows_for("IncCo", "fake_inc", list(range(100, 40, -1)))   # newest first, 60 rows

        def paged(t, smoke=False):
            out = []
            for i in range(0, len(board), 10):
                pages["n"] += 1
                page = [dict(r, extra=dict(r["extra"])) for r in board[i:i + 10]]
                out += page
                if sweep.incremental_stop(t, [r["id"] for r in page]): break
            return out
        sweep.ADAPTERS["fake_inc"] = paged
        sweep.INCREMENTAL_ATS.add("fake_inc"); sweep.ENUMERABLE_ATS.add("fake_inc")
        self.targets = [{"company": "IncCo", "ats": "fake_inc", "verified": True}]
        try:
            self.boards = {}
            self.sweep_day(1)
            self.assertEqual(pages["n"], 6)                      # first read is a full walk
            board[:0] = self.rows_for("IncCo", "fake_inc", [102, 101])
            pages["n"] = 0
            s2 = self.sweep_day(2)
            self.assertEqual(pages["n"], 4)                      # 1 page with new ids + 3 all-known pages
            self.assertEqual(sorted(s2["new_keys"]), ["fake_inc:IncCo:101", "fake_inc:IncCo:102"])
            self.assertEqual(len(s2["rows"]), 62)
            self.assertEqual(s2["closed"], {})
            tail = s2["rows"]["fake_inc:IncCo:41"]
            self.assertEqual(tail["_carried_reason"], sweep.CARRY_INCREMENTAL)
            del board[-1]                                        # req 41 closes
            pages["n"] = 0
            s3 = self.sweep_day(3, full=True)
            self.assertEqual(pages["n"], 7)
            self.assertEqual(list(s3["closed"]), ["fake_inc:IncCo:41"])
        finally:
            sweep.INCREMENTAL_ATS.discard("fake_inc"); sweep.ENUMERABLE_ATS.discard("fake_inc")

    def test_etag_measurement_counts_a_304_and_changes_nothing(self):
        self.etag_setup()
        s1 = self.sweep_day(1)
        rl1 = self.readlog()
        self.assertEqual(rl1["etag"], 'W/"v1"')
        self.assertEqual(rl1["etag_seen"], "2026-09-01T12:00:00+00:00")
        self.assertEqual((rl1["etag_304"], rl1["etag_200"]), (0, 0))
        self.assertEqual(rl1["etag_gates"], sweep.etag_gates_sig(self.G))
        self.assertIn("etag: 0 of 0 conditional reads would have been 304", self.out)
        s2 = self.sweep_day(2)
        rl2 = self.readlog()
        self.assertEqual((rl2["etag_304"], rl2["etag_200"], rl2["etag_304_mismatch"]), (1, 0, 0))
        self.assertEqual(rl2["etag_seen"], "2026-09-01T12:00:00+00:00")     # same tag: first-seen clock stays
        self.assertEqual(rl2["last_read"], "2026-09-02T12:00:00+00:00")
        self.assertIn("etag: 1 of 1 conditional reads would have been 304 (0 with a body that differed", self.out)
        self.assertIn("clean streak 1 of 3 full sweeps", self.out)     # day 1 had no 304 to prove anything
        self.assertEqual(s2["coverage"][0][2], "OK")                          # the body was read anyway
        self.assertEqual(sorted(s2["rows"]), sorted(s1["rows"]))
        self.assertEqual((s2["closed"], s2["new_keys"], s2["verdicts"]), ({}, [], s1["verdicts"]))
        self.assertNotIn("_carried_reason", s2["rows"]["fake_etag:EtagCo:1"])
        # The board retitles a req but keeps its tag: measurement sees the body differ and says so.
        self.boards["EtagCo"][1]["title"] = "Product Manager 2, Finance Systems (retitled)"
        self.sweep_day(3)
        rl3 = self.readlog()
        self.assertEqual((rl3["etag_304"], rl3["etag_304_mismatch"]), (2, 1))
        self.assertIn("(1 with a body that differed from the snapshot: EtagCo)", self.out)
        self.assertIn("clean streak 0 of 3 full sweeps", self.out)
        runs = json.loads((sweep.DATA / "etag_runs.json").read_text(encoding="utf-8"))
        self.assertEqual([(r["hits"], r["mismatch"]) for r in runs], [(0, 0), (1, 0), (1, 1)])
        self.assertEqual(runs[-1]["mismatch_boards"], ["EtagCo"])
        # A new tag answers 200: counted as a conditional miss, stored, first-seen clock reset.
        self.tag = 'W/"v2"'
        self.sweep_day(4)
        rl4 = self.readlog()
        self.assertEqual((rl4["etag"], rl4["etag_200"], rl4["etag_seen"]),
                         ('W/"v2"', 1, "2026-09-04T12:00:00+00:00"))
        self.assertIn("etag: 0 of 1 conditional reads would have been 304", self.out)

    def test_etag_clean_streak_counts_trailing_clean_runs_only(self):
        runs = [{"hits": 5, "mismatch": 0}, {"hits": 5, "mismatch": 2}, {"hits": 4, "mismatch": 0},
                {"hits": 6, "mismatch": 0}]
        self.assertEqual(sweep.etag_clean_streak(runs), 2)
        self.assertEqual(sweep.etag_clean_streak(runs + [{"hits": 0, "mismatch": 0}]), 0)
        self.assertEqual(sweep.etag_clean_streak([]), 0)

    def test_merge_does_not_record_an_etag_run(self):
        self.etag_setup()
        self.sweep_day(1)
        self.sweep_day(2, merge="EtagCo")
        runs = json.loads((sweep.DATA / "etag_runs.json").read_text(encoding="utf-8"))
        self.assertEqual(len(runs), 1)

    def test_etag_trust_skips_vendors_outside_etag_trust_ats(self):
        # Trust is per vendor (etag_trust_ats). A vendor can answer 304 for a board that has changed,
        # so a vendor off the list is read in full even with trust on.
        self.etag_setup(trust=True)
        self.G = dict(self.G, etag_trust_ats=["greenhouse", "lever"])
        self.sweep_day(1)
        s2 = self.sweep_day(2)
        self.assertEqual(s2["coverage"][0][2], "OK")

    def test_etag_trust_restamps_the_snapshot_and_never_closes_on_a_304(self):
        self.etag_setup(trust=True)
        s1 = self.sweep_day(1)
        s2 = self.sweep_day(2)
        self.assertEqual(s2["coverage"][0][2:], ["OK (304 unchanged)", 2])
        for k in ("fake_etag:EtagCo:1", "fake_etag:EtagCo:2"):
            r = s2["rows"][k]
            self.assertTrue(r["description"])
            self.assertEqual((r["jd_hash"], r["_list_sig"]), (s1["rows"][k]["jd_hash"], s1["rows"][k]["_list_sig"]))
            self.assertNotIn("_carried_reason", r); self.assertNotIn("_carried_since", r)
        self.assertEqual((s2["health"]["carried"], s2["closed"], s2["new_keys"]), (0, {}, []))
        self.assertEqual(s2["verdicts"], s1["verdicts"])
        rl = self.readlog()
        self.assertEqual((rl["last_read"][:10], rl["last_full"][:10], rl["count"], rl["etag_304"]),
                         ("2026-09-02", "2026-09-02", 2, 1))
        self.assertIn("etag: 1 of 1 conditional reads were 304, 1 board(s) re-stamped from the snapshot", self.out)
        self.assertNotIn("carried forward:", self.out)
        # Req 2 leaves the board but the vendor still says 304. 304 means unchanged: nothing closes.
        self.boards["EtagCo"] = self.boards["EtagCo"][:1]
        s3 = self.sweep_day(3)
        self.assertIn("fake_etag:EtagCo:2", s3["rows"])
        self.assertEqual(s3["closed"], {})
        # The tag moves: a real read, and the closure lands.
        self.tag = 'W/"v2"'
        s4 = self.sweep_day(4)
        self.assertEqual(s4["coverage"][0][2], "OK")
        self.assertEqual(list(s4["closed"]), ["fake_etag:EtagCo:2"])
        self.assertEqual((self.readlog()["etag"], self.readlog()["etag_200"]), ('W/"v2"', 1))

    def test_etag_trust_restamps_rows_carried_through_an_errored_read(self):
        self.etag_setup(trust=True)
        self.sweep_day(1)
        self.cond_fails = True
        s2 = self.sweep_day(2)
        self.assertEqual(s2["rows"]["fake_etag:EtagCo:1"]["_carried_reason"], sweep.CARRY_ERROR)
        self.cond_fails = False
        s3 = self.sweep_day(3)
        self.assertEqual(s3["coverage"][0][2], "OK (304 unchanged)")
        self.assertEqual(s3["health"]["carried"], 0)
        self.assertNotIn("_carried_reason", s3["rows"]["fake_etag:EtagCo:1"])

    def test_etag_trust_304_with_no_snapshot_rows_is_uncovered_not_restamped(self):
        self.etag_setup(trust=True)
        self.sweep_day(1)
        snap = json.loads((sweep.DATA / "latest.json").read_text(encoding="utf-8"))
        snap["rows"] = {}
        (sweep.DATA / "latest.json").write_text(json.dumps(snap), encoding="utf-8")
        self.plain_fails = True                    # the retry's plain read is down as well
        s2 = self.sweep_day(2)
        self.assertIn("ERROR Etag304NoRows", self.out)
        self.assertIn("retrying 1 errored employer", self.out)
        self.assertTrue(s2["coverage"][0][2].startswith("ERROR"))
        self.assertEqual(s2["rows"], {})
        self.assertTrue(any("UNCOVERED" in w and "EtagCo" in w for w in s2["health"]["warnings"]))
        self.assertEqual(self.readlog()["last_read"], "2026-09-01T12:00:00+00:00")   # no clean read logged
        # The retry reads plainly once the tags are dropped: with the board up, the run recovers.
        self.plain_fails = False
        s3 = self.sweep_day(3)
        self.assertIn("ERROR Etag304NoRows", self.out)
        self.assertEqual(s3["coverage"][0][2], "OK")
        self.assertEqual(len(s3["rows"]), 2)
        self.assertEqual(self.readlog()["last_read"], "2026-09-03T12:00:00+00:00")

    def test_etag_trust_waits_for_one_full_read_under_edited_gates(self):
        self.etag_setup(trust=True)
        self.sweep_day(1)
        self.G = dict(self.G, fresh_window_days=15)
        s2 = self.sweep_day(2)
        self.assertEqual(s2["coverage"][0][2], "OK")                          # 304 answered, not trusted
        self.assertEqual((self.readlog()["etag_304"], self.readlog()["etag_gates"]),
                         (1, sweep.etag_gates_sig(self.G)))
        s3 = self.sweep_day(3)
        self.assertEqual(s3["coverage"][0][2], "OK (304 unchanged)")

    def test_merge_on_query_scoped_board_keeps_rows(self):
        # A --merge on a query-scoped board keeps the live reqs its sample did not return.
        self.day_one()
        self.boards["QueryCo"] = self.rows_for("QueryCo", "fake_query", [2])
        sweep.TODAY = dt.date(2026, 9, 2)
        with contextlib.redirect_stdout(io.StringIO()):
            sweep.run(self.targets, G, merge="QueryCo")
        s = json.loads((sweep.DATA / "latest.json").read_text(encoding="utf-8"))
        self.assertEqual(len(s["rows"]), 8)
        self.assertEqual(s["closed"], {})

    def test_merge_rows_carry_a_jd_hash(self):
        # --merge stamps jd_hash like a full run; without it a merged employer's rows have no hash
        # and the next "JD text changed" check compares nothing.
        self.day_one()
        self.boards["QueryCo"] = self.rows_for("QueryCo", "fake_query", [1, 2])
        sweep.TODAY = dt.date(2026, 9, 2)
        with contextlib.redirect_stdout(io.StringIO()):
            sweep.run(self.targets, G, merge="QueryCo")
        s = json.loads((sweep.DATA / "latest.json").read_text(encoding="utf-8"))
        merged = [r for r in s["rows"].values() if r["company"] == "QueryCo"]
        self.assertTrue(merged)
        self.assertTrue(all(r.get("jd_hash") for r in merged))

    def scored_lookup_setup(self):
        # A scored req can be open on a query-scoped board yet missing from the snapshot (dropped by a
        # merge, not returned by the lane queries). Carry-forward looks one snapshot back, so only a
        # direct lookup recovers it.
        self.day_one()
        open_row = self.rows_for("QueryCo", "fake_query", [7])[0]
        self.lookups = []

        def lookup(t, jid):
            self.lookups.append(jid)
            if jid == "7": return dict(open_row, extra=dict(open_row["extra"]))
            if jid == "8": raise ConnectionError("lookup down")
            return None                                  # 2 is closed
        sweep.LOOKUP["fake_query"] = lookup
        self.addCleanup(sweep.LOOKUP.pop, "fake_query", None)
        # 7: scored, open, absent from the snapshot entirely
        # 2: scored, in the snapshot, absent from today's sample, closed on the board
        # 8: scored, lookup errors: left to ordinary carry-forward
        sweep.SCORED.write_text(json.dumps({k: {"score": 76, "verdict": "Apply", "scored_on": "2026-09-01"}
                                            for k in ("fake_query:QueryCo:7", "fake_query:QueryCo:2",
                                                      "fake_query:QueryCo:8", "fake_enum:EnumCo:3")}),
                                encoding="utf-8")
        self.boards["QueryCo"] = self.rows_for("QueryCo", "fake_query", [1])

    def test_scored_req_missing_from_sample_is_recovered_on_full_run(self):
        self.scored_lookup_setup()
        s = self.sweep_day(2)
        self.assertEqual(s["rows"]["fake_query:QueryCo:7"]["_carried_reason"], sweep.CARRY_SCORED)
        self.assertNotIn("fake_query:QueryCo:7", s["new_keys"])
        self.assertNotIn("fake_query:QueryCo:2", s["rows"])          # confirmed closed, not carried
        self.assertIn("fake_query:QueryCo:2", s["closed"])
        self.assertEqual(sorted(self.lookups), ["2", "7", "8"])     # enumerable boards never looked up
        self.assertIn("RECOVERED", self.out)

    def test_scored_req_missing_from_sample_is_recovered_on_merge(self):
        self.scored_lookup_setup()
        # A recovered req can still be in the first_seen ledger. The merge's "other employers' dates
        # unchanged" proof must not classify it by prev (where it is absent) and report a BUG.
        ledger = json.loads((sweep.DATA / "seen.json").read_text(encoding="utf-8"))
        ledger["fake_query:QueryCo:7"] = "2026-08-20"
        (sweep.DATA / "seen.json").write_text(json.dumps(ledger), encoding="utf-8")
        sweep.TODAY = dt.date(2026, 9, 2)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            sweep.run(self.targets, G, merge="QueryCo")
        self.assertNotIn("<-- BUG", out.getvalue())
        self.assertEqual(json.loads((sweep.DATA / "seen.json").read_text(encoding="utf-8"))
                         ["fake_query:QueryCo:7"], "2026-08-20")
        s = json.loads((sweep.DATA / "latest.json").read_text(encoding="utf-8"))
        self.assertIn("fake_query:QueryCo:7", s["rows"])
        self.assertNotIn("fake_query:QueryCo:7", s["new_keys"])
        self.assertEqual(set(s["closed"]), {"fake_query:QueryCo:2"})
        self.assertEqual(len(s["rows"]), 8)                          # 8 - closed 2 + recovered 7

    def test_errored_employer_is_retried_once(self):
        calls = {"n": 0}
        good = self.rows_for("FlakyCo", "fake_enum", [1])

        def flaky(t, smoke=False):
            calls["n"] += 1
            if calls["n"] == 1: raise ConnectionError("transient")
            return good
        sweep.ADAPTERS["fake_flaky"] = flaky
        self.targets = [{"company": "FlakyCo", "ats": "fake_flaky", "verified": True}]
        s = self.sweep_day(1)
        self.assertEqual(calls["n"], 2)
        self.assertTrue(s["coverage"][0][2].startswith("OK"))


class Plugins(unittest.TestCase):
    def test_broken_plugin_is_reported_not_fatal(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "broken.py").write_text("NAME = 'broken'\nraise ImportError('boom')\n", encoding="utf-8")
            (Path(d) / "okay.py").write_text(
                "NAME = 'okay_test'\nENUMERABLE = True\nREQUIRED = ['slug']\n"
                "def list_jobs(t, h, smoke=False):\n    return [h.norm(t['company'], NAME, 1, 'PM', 'Remote, USA', 'u')]\n",
                encoding="utf-8")
            loaded = sweep.load_plugins(d)
            try:
                self.assertEqual(loaded, ["okay_test"])
                self.assertIn("broken", sweep.PLUGIN_ERRORS)
                self.assertIn("okay_test", sweep.ENUMERABLE_ATS)
                rows = sweep.ADAPTERS["okay_test"]({"company": "X", "slug": "x"})
                self.assertEqual(rows[0]["key"], "okay_test:X:1")
                with self.assertRaises(ValueError):
                    sweep.ADAPTERS["okay_test"]({"company": "X"})
            finally:
                sweep.ADAPTERS.pop("okay_test", None); sweep.ENUMERABLE_ATS.discard("okay_test")
                sweep.PLUGIN_ERRORS.pop("broken", None)


class EtagBoards(unittest.TestCase):
    """The real greenhouse and lever adapters against a stubbed HTTP layer: what each does with an
    armed target (run() sets _etag_prev / _etag_trust) and with a bare one."""

    def setUp(self):
        self.saved = (sweep.get, sweep.get_conditional)
        self.calls = []

    def tearDown(self):
        sweep.get, sweep.get_conditional = self.saved

    @staticmethod
    def posting(i):
        return {"id": i, "text": f"Product Manager {i}", "categories": {"location": "Remote"},
                "descriptionPlain": "Finance systems.", "lists": [], "hostedUrl": f"https://l/{i}"}

    def stub_lever(self, pages, tags):
        def cond(url, etag, headers=None):
            p = int(url.rsplit("skip=", 1)[1]) // 100
            self.calls.append(("cond", p, etag))
            if etag == tags[p]: return 304, None, etag
            return 200, pages[p], tags[p]
        def plain(url, headers=None, **kw):
            p = int(url.rsplit("skip=", 1)[1]) // 100
            self.calls.append(("plain", p)); return pages[p]
        sweep.get_conditional, sweep.get = cond, plain

    def test_lever_pages_are_conditional_one_by_one(self):
        pages = {0: [self.posting(i) for i in range(100)], 1: [self.posting(100)]}
        tags = {0: 'W/"p0"', 1: 'W/"p1"'}
        self.stub_lever(pages, tags)
        t = {"company": "L", "slug": "l", "_etag_prev": ['W/"p0"', 'W/"p1"'], "_etag_trust": True}
        self.assertIs(sweep.lever(t), sweep.BOARD_UNCHANGED)                # every page 304
        self.assertEqual((t["_etag_stat"]["hit"], t["_etag_stat"]["miss"]), (2, 0))
        self.assertEqual(self.calls, [("cond", 0, 'W/"p0"'), ("cond", 1, 'W/"p1"')])
        # Page 1 changes: page 0's 304 is no longer the whole story, so it is read plainly, in order.
        tags[1] = 'W/"p1b"'; pages[1] = [self.posting(100), self.posting(101)]
        self.calls.clear()
        t = {"company": "L", "slug": "l", "_etag_prev": ['W/"p0"', 'W/"p1"'], "_etag_trust": True}
        rows = sweep.lever(t)
        self.assertEqual([r["id"] for r in rows], [str(i) for i in range(102)])
        self.assertEqual(self.calls, [("cond", 0, 'W/"p0"'), ("cond", 1, 'W/"p1"'), ("plain", 0)])
        self.assertEqual(t["_etag_stat"]["tags"], ['W/"p0"', 'W/"p1b"'])      # what read_log stores next
        # Measurement mode: both pages 304, both bodies read anyway, both hits counted.
        self.calls.clear()
        t = {"company": "L", "slug": "l", "_etag_prev": ['W/"p0"', 'W/"p1b"'], "_etag_trust": False}
        self.assertEqual(len(sweep.lever(t)), 102)
        self.assertEqual((t["_etag_stat"]["hit"], t["_etag_stat"]["miss"]), (2, 0))
        self.assertEqual([c for c in self.calls if c[0] == "plain"], [("plain", 0), ("plain", 1)])
        # A bare target (smoke, probe, an older test) never sends a conditional request.
        self.calls.clear()
        self.assertEqual(len(sweep.lever({"company": "L", "slug": "l"})), 102)
        self.assertTrue(all(c[0] == "plain" for c in self.calls))

    def test_greenhouse_304_is_counted_in_measurement_and_trusted_in_trust(self):
        body = {"jobs": [{"id": 7, "title": "Product Manager", "location": {"name": "Remote, US"},
                          "absolute_url": "https://g/7", "content": "<p>AI</p>", "departments": []}]}
        sweep.get_conditional = lambda url, etag, headers=None: (304, None, etag) if etag == 'W/"g"' else (200, body, 'W/"g"')
        sweep.get = lambda url, headers=None, **kw: body
        t = {"company": "G", "slug": "g", "_etag_prev": ['W/"g"'], "_etag_trust": False}
        self.assertEqual([r["id"] for r in sweep.greenhouse(t)], ["7"])
        self.assertEqual((t["_etag_stat"]["hit"], t["_etag_stat"]["tags"]), (1, ['W/"g"']))
        t = {"company": "G", "slug": "g", "_etag_prev": ['W/"g"'], "_etag_trust": True}
        self.assertIs(sweep.greenhouse(t), sweep.BOARD_UNCHANGED)
        t = {"company": "G", "slug": "g", "_etag_prev": [], "_etag_trust": True}   # first read: nothing to send
        self.assertEqual(len(sweep.greenhouse(t)), 1)
        self.assertEqual((t["_etag_stat"]["hit"], t["_etag_stat"]["miss"], t["_etag_stat"]["tags"]), (0, 0, ['W/"g"']))
        # A board that returns no ETag stores none and keeps none.
        sweep.get_conditional = lambda url, etag, headers=None: (200, body, None)
        t = {"company": "G", "slug": "g", "_etag_prev": ['W/"old"'], "_etag_gates": "x"}
        sweep.greenhouse(t)
        self.assertEqual(sweep.etag_log_fields(t, {"etag": 'W/"old"', "etag_304": 4}),
                         {"etag_304": 4, "etag_200": 1, "etag_304_mismatch": 0})


class MergeTargets(unittest.TestCase):
    # A probe entry flagged "removed" in an adapters/*.targets.json file stays out on the next merge.
    def test_removed_entry_is_not_re_added(self):
        import sys
        from tools import merge_targets as mt
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); (root / "adapters").mkdir()
            (root / "targets.json").write_text(json.dumps({"targets": []}), encoding="utf-8")
            (root / "adapters" / "x.targets.json").write_text(json.dumps([
                {"company": "Gone", "ats": "greenhouse", "slug": "gone", "removed": "off lane"},
                {"company": "Kept", "ats": "greenhouse", "slug": "kept"}]), encoding="utf-8")
            old_root, old_argv = mt.ROOT, sys.argv
            mt.ROOT, sys.argv = root, ["merge_targets.py", "--write"]
            try:
                with contextlib.redirect_stdout(io.StringIO()) as out:
                    mt.main()
            finally:
                mt.ROOT, sys.argv = old_root, old_argv
            names = [t["company"] for t in json.loads((root / "targets.json").read_text(encoding="utf-8"))["targets"]]
            self.assertEqual(names, ["Kept"])
            self.assertIn("removed on purpose", out.getvalue())

    # An employer dropped in targets.json itself ("removed": {company: reason}) stays dropped even
    # when an older probe file still lists it without a "removed" flag.
    def test_targets_json_removed_list_blocks_re_adding(self):
        import sys
        from tools import merge_targets as mt
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); (root / "adapters").mkdir()
            (root / "targets.json").write_text(json.dumps({"targets": [], "removed": {"OldCo": "agency"}}),
                                               encoding="utf-8")
            (root / "adapters" / "x.targets.json").write_text(json.dumps([
                {"company": "OldCo", "ats": "ashby", "slug": "oldco"},
                {"company": "Kept", "ats": "greenhouse", "slug": "kept"}]), encoding="utf-8")
            old_root, old_argv = mt.ROOT, sys.argv
            mt.ROOT, sys.argv = root, ["merge_targets.py", "--write"]
            try:
                with contextlib.redirect_stdout(io.StringIO()) as out:
                    mt.main()
            finally:
                mt.ROOT, sys.argv = old_root, old_argv
            names = [t["company"] for t in json.loads((root / "targets.json").read_text(encoding="utf-8"))["targets"]]
            self.assertEqual(names, ["Kept"])
            self.assertIn("removed on purpose (targets.json): agency", out.getvalue())


class ConsoleEncoding(unittest.TestCase):
    # A board title carrying U+202F (narrow no-break space) has no cp1252 mapping; on a cp1252
    # console one such character must not take the whole `--window` listing down.
    TITLE = "Sr. Product Manager – AI, Café"

    def test_cp1252_stdout_replaces_instead_of_raising(self):
        buf = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict", newline="\n")
        old = sys.stdout
        sys.stdout = buf
        try:
            with self.assertRaises(UnicodeEncodeError):   # the bug, before the fix runs
                print(self.TITLE); buf.flush()
            sweep._never_crash_on_print()
            print(sweep.link(self.TITLE, "https://example.com/1"))
            buf.flush()
        finally:
            sys.stdout = old
        out = buf.buffer.getvalue().decode("cp1252")
        self.assertIn("Product Manager", out)
        self.assertIn("https://example.com/1", out)

    def test_characters_cp1252_already_encoded_are_untouched(self):
        # The report separator and en-dash live in cp1252; the fix must not change their bytes.
        buf = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict", newline="\n")
        old = sys.stdout
        sys.stdout = buf
        try:
            sweep._never_crash_on_print()
            print("ExampleCo · Sr PM – AI")
            buf.flush()
        finally:
            sys.stdout = old
        self.assertEqual(buf.buffer.getvalue(), "ExampleCo · Sr PM – AI\n".encode("cp1252"))

    def test_non_reconfigurable_stream_is_left_alone(self):
        old = sys.stdout
        sys.stdout = io.StringIO()          # no .reconfigure()
        try:
            sweep._never_crash_on_print()   # must not raise
            print(self.TITLE)
        finally:
            sys.stdout = old


if __name__ == "__main__":
    unittest.main()
