"""Greenhouse's job-detail endpoint (?questions=true&pay_transparency=true), read for two things.

1. The application form, each question classified against the candidate's facts (gates.json
   candidate_profile; here examples/john_doe/candidate.json: 4.5 years, clears a 4+ bar, Denver CO
   and no relocation, US citizen needing no sponsorship, not authorized in Canada, a bachelor's
   degree, no certifications, no clearance, no people management). A flag for the person deciding:
   never the score, never a gate.
2. pay_input_ranges, the structured band that never reaches the JD body on about one in eight
   Greenhouse PASS/REVIEW rows.

The two fixtures are real public job-detail responses, trimmed and anonymised (ExampleCo): a Canada
question on a US remote req with four per-state ranges, and years buckets beside a "specifically"
specialty question. Offline.
"""
import contextlib, datetime as dt, io, json, os, tempfile, unittest
from pathlib import Path

from tests.stubs import sweep, gates, fixture

G = gates()
CAND = sweep.load_candidate(G)
US_ROW = {"location": "United States - Remote"}
CANADA_ROW = {"location": "Calgary, Alberta, Canada"}


def classify(label, options=(), required=True, ftype=None, row=US_ROW, cand=None):
    ftype = ftype or ("multi_value_single_select" if options else "input_text")
    return sweep.classify_form_question(label, required, ftype, list(options), row, CAND if cand is None else cand)


def by_label(qs, text):
    return next(q for q in qs if text.lower() in q["label"].lower())


def variant(**kw):
    c = json.loads(json.dumps(CAND))
    c.update(kw)
    return c


class CandidateProfile(unittest.TestCase):
    def test_the_example_gates_point_at_john_doe(self):
        self.assertEqual(G["candidate_profile"], "examples/john_doe/candidate.json")
        self.assertEqual(CAND["years_experience"], 4.5)
        self.assertEqual(CAND["home"]["city"], "Denver")

    def test_no_profile_means_no_candidate(self):
        self.assertEqual(sweep.load_candidate({}), {})
        self.assertEqual(sweep.load_candidate({"candidate_profile": "examples/john_doe/no_such_file.json"}), {})


class RealForms(unittest.TestCase):
    def test_canada_question_on_a_us_req_is_a_knockout_flag(self):
        d = fixture("greenhouse", "detail_canada_ranges.json")
        qs = sweep.form_questions_from_detail(d, US_ROW, CAND)
        q = by_label(qs, "entitled to work in Canada")
        self.assertEqual(q["verdict"], "KNOCKOUT")
        self.assertTrue(q["truthful_answer"].startswith("NO"))
        self.assertEqual(by_label(qs, "authorized to work in the United States")["verdict"], "OK")
        self.assertEqual(by_label(qs, "require immigration sponsorship")["truthful_answer"], "No")
        self.assertEqual(by_label(qs, "state or province")["truthful_answer"], "(US) Colorado")

    def test_identity_fields_and_hidden_location_are_left_out(self):
        qs = sweep.form_questions_from_detail(fixture("greenhouse", "detail_canada_ranges.json"), US_ROW, CAND)
        labels = {q["label"] for q in qs}
        for gone in ("First Name", "Email", "Resume/CV", "Cover Letter", "Longitude"):
            self.assertNotIn(gone, labels)

    def test_four_state_ranges_give_the_first_and_a_count(self):
        d = fixture("greenhouse", "detail_canada_ranges.json")
        self.assertEqual(sweep.pay_band_from_ranges(d["pay_input_ranges"]), "$221,000 - $233,000 (+3 more ranges)")

    def test_years_buckets_and_a_specialty_question(self):
        d = fixture("greenhouse", "detail_years_buckets.json")
        qs = sweep.form_questions_from_detail(d, {"location": "United States"}, CAND)
        q = by_label(qs, "years experience do you have as a Product Manager")
        self.assertEqual((q["verdict"], q["truthful_answer"]), ("OK", "4 - 7 years"))
        self.assertEqual(by_label(qs, "specifically designing")["verdict"], "ASK")
        self.assertEqual(by_label(qs, "non-compete")["verdict"], "ASK")
        # "require company sponsorship to retain or extend your work authorization" is the sponsorship
        # question; read as an authorization one it picked the first "Yes, I will require" option.
        self.assertEqual(by_label(qs, "require company sponsorship")["truthful_answer"],
                         "No, I do not and will not require immigration sponsorship to legally work in the country "
                         "where the job is located.")
        self.assertFalse(any(q["label"].startswith(("Gender", "Race", "Veteran")) for q in qs))
        self.assertEqual(sweep.pay_band_from_ranges(d["pay_input_ranges"]), "$200,000 - $240,000")

    def test_entry_counts(self):
        e = sweep.form_entry(fixture("greenhouse", "detail_canada_ranges.json"), US_ROW, "h1", CAND)
        self.assertEqual(e["counts"]["KNOCKOUT"], 1)
        self.assertEqual(e["jd_hash"], "h1")
        self.assertEqual(e["pay_band"], "$221,000 - $233,000 (+3 more ranges)")


class Classification(unittest.TestCase):
    def test_years(self):
        self.assertEqual(classify("Do you have 7+ years of product management experience?", ["Yes", "No"])[:2],
                         ("KNOCKOUT", "NO (4.5 years against a 7+ bar)"))
        self.assertEqual(classify("Do you have at least 4 years of experience in Agile/Scrum", ["Yes", "No"])[:2], ("OK", "Yes"))
        self.assertEqual(classify("Do you have 5 or more years of professional experience?", ["Yes", "No"])[0], "KNOCKOUT")
        self.assertEqual(classify("How many years of experience do you have?", ["0-5", "5-10", "10-15", "15+"])[1], "0-5")
        self.assertEqual(classify("How many years of experience do you have?", ["0-2", "3-5", "6-8", "9+"])[1], "3-5")
        # Whole-year buckets leave 4.5 between "3-4" and "5-6"; four full years is the "3-4" answer.
        self.assertEqual(classify("How many years of experience do you have?", ["0-2", "3-4", "5-6", "7+"])[1], "3-4")
        self.assertEqual(classify("How many years of experience do you have?", ["7-10 years", "10+ years"])[0], "KNOCKOUT")
        self.assertEqual(classify("Are you 18 years of age or older?", ["Yes", "No"])[:2], ("OK", "Yes"))

    def test_optional_knockout_says_it_can_be_left_blank(self):
        v, ans, _ = classify("Do you have 8+ years of experience?", ["Yes", "No"], required=False)
        self.assertEqual(v, "KNOCKOUT")
        self.assertIn("optional question", ans)

    def test_authorization_and_sponsorship(self):
        self.assertEqual(classify("Will you now or in the future require sponsorship?", ["Yes", "No"])[:2], ("OK", "No"))
        self.assertEqual(classify("Are you legally authorized to work in the country where the job is located?",
                                  ["Yes", "No"])[:2], ("OK", "Yes"))
        self.assertEqual(classify("Are you legally authorized to work in US or Canada without visa sponsorship?",
                                  ["Yes", "No"])[0], "OK")
        self.assertEqual(classify("Work Authorization", ["I am authorized to work for any employer in the country",
                                                         "I require sponsorship"])[1],
                         "I am authorized to work for any employer in the country")

    def test_canada(self):
        self.assertEqual(classify("Are you legally authorized to work in Canada?", ["Yes", "No"])[0], "KNOCKOUT")
        # On a Canadian req the plain authorization and sponsorship questions are the knockouts.
        self.assertEqual(classify("Are you authorized to work in the stated location of this role?", ["Yes", "No"],
                                  row=CANADA_ROW)[0], "KNOCKOUT")
        self.assertEqual(classify("Do you now or will you in the future require sponsorship?", ["Yes", "No"],
                                  row=CANADA_ROW)[0], "KNOCKOUT")
        self.assertTrue(sweep._form_canada_job({"location": "Toronto, ON, CA"}))   # "CA" here is Canada

    def test_certification_clearance_degree(self):
        self.assertEqual(classify("Do you hold an active PMP certification?", ["Yes", "No"])[0], "KNOCKOUT")
        self.assertEqual(classify("Are you a Certified Scrum Product Owner (CSPO)?", ["Yes", "No"])[0], "KNOCKOUT")
        self.assertEqual(classify("I certify that the information provided is true and correct.", ["Yes", "No"])[:2],
                         ("OK", "Yes"))
        self.assertEqual(classify("Do you currently hold an active security clearance?", ["Yes", "No"])[0], "KNOCKOUT")
        self.assertEqual(classify("Are you able to obtain a security clearance?", ["Yes", "No"])[0], "OK")
        self.assertEqual(classify("Do you have a Master's degree?", ["Yes", "No"])[0], "KNOCKOUT")
        self.assertEqual(classify("What is your highest level of degree attained?",
                                  ["Associate Degree", "Bachelor's Degree", "Master's Degree"])[1], "Bachelor's Degree")

    def test_people_management(self):
        self.assertEqual(classify("Have you managed people with direct reports?", ["Yes", "No"])[0], "KNOCKOUT")
        self.assertEqual(classify("Do you have experience managing a team of engineers?", ["Yes", "No"])[0], "KNOCKOUT")
        self.assertNotEqual(classify("Have you led or managed delivery on at least one engagement where ERP was core?",
                                     ["Yes", "No"])[0], "KNOCKOUT")

    def test_ask_for_salary_start_and_essays(self):
        self.assertEqual(classify("What are your salary expectations?")[::2], ("ASK", "salary expectation"))
        self.assertEqual(classify("What are your expectations for pay in this role?")[0], "ASK")
        self.assertEqual(classify("When is the earliest you would want to start working with us?", required=False)[0], "ASK")
        v, _, why = classify("Why do you want to work at ExampleCo?", ftype="textarea")
        self.assertEqual(v, "ASK")
        self.assertTrue(why.startswith("essay"))
        self.assertEqual(classify("Additional Information", ftype="textarea", required=False)[0], "OK")

    def test_location(self):
        v, ans, _ = classify("Do you live within 50 miles of our San Francisco office and are you willing to work "
                             "in the office 3 days a week?", ["Yes", "No"])
        self.assertEqual(v, "KNOCKOUT")
        self.assertIn("Denver, CO", ans)
        self.assertEqual(classify("Are you able to work from our Denver office 3 days a week?", ["Yes", "No"])[:2],
                         ("OK", "Yes"))
        self.assertEqual(classify("Are you able to work from our NYC office 3 days a week?", ["Yes", "No"])[0], "ASK")
        self.assertEqual(classify("Are you open to relocating to Austin?", ["Yes", "No"])[0], "ASK")
        self.assertEqual(classify("Select all office locations where you would be willing to work.",
                                  ["Detroit, MI", "Denver, CO", "Pittsburgh, PA", "Boulder, CO"],
                                  ftype="multi_value_multi_select")[1], "Denver, CO; Boulder, CO")
        self.assertEqual(classify("What is your current country of residence?", ["Canada", "United States"])[1],
                         "United States")
        self.assertEqual(classify("Which time zone are you in?", ["Pacific", "Mountain", "Central", "Eastern"])[1], "Mountain")
        # "willing" alone is not a location question.
        self.assertNotEqual(classify("Are you willing to leverage AI in many areas of your role?", ["Yes", "No"])[2],
                            "onsite / relocation")


class ProfileDrivesTheAnswers(unittest.TestCase):
    """The same questions, a different candidate file: nothing about the candidate is in the code."""
    def test_a_relocation_market_picks_the_relocate_option(self):
        c = variant(relocation_markets=[{"name": "Austin", "pattern": "(?i:austin)"}])
        self.assertEqual(classify("Are you local to Austin and able to commute in?",
                                  ["Yes, local to Austin", "No, but willing to relocate to Austin", "No, not able to relocate"],
                                  cand=c)[1], "No, but willing to relocate to Austin")
        v, ans, _ = classify("Are you open to relocating to Austin?", ["Yes", "No"], cand=c)
        self.assertEqual(v, "OK")
        self.assertIn("relocation to Austin", ans)

    def test_held_certification_years_and_management(self):
        c = variant(certifications=["PMP"], years_experience=9, years_bar_cleared=9, people_management=True)
        self.assertEqual(classify("Do you hold an active PMP certification?", ["Yes", "No"], cand=c)[0], "OK")
        self.assertEqual(classify("Do you hold an active CSPO certification?", ["Yes", "No"], cand=c)[0], "KNOCKOUT")
        self.assertEqual(classify("Do you have 7+ years of product management experience?", ["Yes", "No"], cand=c)[0], "OK")
        self.assertEqual(classify("Have you managed people with direct reports?", ["Yes", "No"], cand=c)[0], "OK")

    def test_a_fact_left_out_is_an_ask(self):
        c = variant(years_experience=None, education={}, work_authorization={})
        self.assertEqual(classify("Do you have 7+ years of experience?", ["Yes", "No"], cand=c)[0], "ASK")
        self.assertEqual(classify("Do you have a Master's degree?", ["Yes", "No"], cand=c)[0], "ASK")
        self.assertEqual(classify("Will you now or in the future require sponsorship?", ["Yes", "No"], cand=c)[0], "ASK")


class LiveMisreads(unittest.TestCase):
    """Three knockouts the first live read of 300 forms got wrong."""
    def test_a_us_sponsorship_question_that_names_canada_as_the_exception(self):
        v, ans, _ = classify("(Skip this question if you are applying to work in Canada or the UK). Do you now or in "
                             "the future require sponsorship for employment visa status?", ["Yes", "No"])
        self.assertEqual((v, ans), ("OK", "No"))

    def test_an_attestation_block_is_not_a_certification(self):
        v, _, _ = classify("CERTIFICATION AND CONFIDENTIALITY: By initialing below, you certify that the information "
                           "provided is true and complete.", ftype="input_text")
        self.assertNotEqual(v, "KNOCKOUT")

    def test_acknowledging_an_onsite_role_is_not_a_residence_question(self):
        v, ans, why = classify("Do you acknowledge that this is a hybrid role based in Denver and you will be required "
                               "to work from the office?", ["Yes", "No"])
        self.assertEqual((v, ans), ("OK", "Yes"))
        v, _, why = classify("Do you acknowledge that this is a hybrid role based in San Francisco, New York and/or "
                             "Chicago and you will be required to work from the office?", ["Yes", "No"])
        self.assertEqual(v, "ASK")                       # outside his markets: his call, not "where do you live"
        self.assertNotEqual(why, "current residence")

    def test_a_real_canada_question_still_knocks_out(self):
        self.assertEqual(classify("Are you legally entitled to work in Canada?", ["Yes", "No"])[0], "KNOCKOUT")

    def test_living_in_a_named_city_today_still_knocks_out(self):
        self.assertEqual(classify("Do you live in either San Francisco, New York or Chicago?", ["Yes", "No"])[0], "KNOCKOUT")
        self.assertEqual(classify("Do you live in either Denver or Chicago?", ["Yes", "No"])[0], "OK")


class PayBands(unittest.TestCase):
    def test_usd_annual(self):
        self.assertEqual(sweep.pay_band_from_ranges([{"min_cents": 15393500, "max_cents": 21300000, "currency_type": "USD"}]),
                         "$153,935 - $213,000")

    def test_non_usd_and_hourly_ignored(self):
        self.assertIsNone(sweep.pay_band_from_ranges([{"min_cents": 15000000, "max_cents": 18000000, "currency_type": "CAD"}]))
        self.assertIsNone(sweep.pay_band_from_ranges([{"min_cents": 4500, "max_cents": 6500, "currency_type": "USD"}]))
        self.assertIsNone(sweep.pay_band_from_ranges([]))
        mixed = [{"min_cents": 15000000, "max_cents": 18000000, "currency_type": "GBP"},
                 {"min_cents": 16000000, "max_cents": 19000000, "currency_type": "USD"}]
        self.assertEqual(sweep.pay_band_from_ranges(mixed), "$160,000 - $190,000")

    def test_title_is_not_trusted(self):
        """An annual band titled 'Hourly Rate' has been seen live."""
        r = [{"min_cents": 18980000, "max_cents": 26090000, "currency_type": "USD", "title": "Bay Area Hourly Rate"}]
        self.assertEqual(sweep.pay_band_from_ranges(r), "$189,800 - $260,900")


class TempData(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.saved = {k: getattr(sweep, k) for k in ("DATA", "JDS", "SCORED", "CONTACTS", "TODAY", "gh_detail_fetch")}
        sweep.DATA = base / "data"; sweep.JDS = sweep.DATA / "jds"; sweep.JDS.mkdir(parents=True)
        sweep.SCORED = sweep.DATA / "scored.json"; sweep.CONTACTS = sweep.DATA / "contacts.json"
        sweep.TODAY = dt.date(2026, 10, 11)
        self.calls = []

    def tearDown(self):
        for k, v in self.saved.items(): setattr(sweep, k, v)
        self.tmp.cleanup()

    def stub(self, detail):
        def fetch(slug, jid):
            self.calls.append((slug, str(jid)))
            return detail
        sweep.gh_detail_fetch = fetch

    def row(self, jid, posted="2026-10-09", comp=None):
        r = sweep.norm("ExampleCo", "greenhouse", jid, "Technical Program Manager, Finance Systems", "Remote, United States",
                       f"https://example.test/jobs/{jid}", posted,
                       "Own ERP adoption programs. 4+ years of program management.", comp)
        r["_target"] = {"company": "ExampleCo", "ats": "greenhouse", "slug": "exampleco"}
        return r


class Staleness(TempData):
    def test_rules(self):
        self.assertTrue(sweep.form_stale(None, "a"))
        self.assertFalse(sweep.form_stale({"jd_hash": "a", "fetched_on": "2026-10-01"}, "a"))
        self.assertTrue(sweep.form_stale({"jd_hash": "a", "fetched_on": "2026-10-01"}, "b"))     # JD rewritten
        self.assertTrue(sweep.form_stale({"jd_hash": "a", "fetched_on": "2026-09-26"}, "a"))     # 15 days old
        self.assertFalse(sweep.form_stale({"jd_hash": "a", "fetched_on": "2026-09-27"}, "a"))    # 14 days: still fresh

    def test_candidates_are_scored_60_plus_open_greenhouse_and_stale(self):
        a, b, c = self.row(1), self.row(2), self.row(3)
        rows = {r["key"]: r for r in (a, b, c)}
        scored = {a["key"]: {"score": 72}, b["key"]: {"score": 55}, c["key"]: {"score": 80},
                  "greenhouse:ExampleCo:999": {"score": 90}}                        # closed: not in rows
        forms = {c["key"]: {"jd_hash": sweep._row_jdh(c), "fetched_on": "2026-10-05"}}
        self.assertEqual(sweep.form_check_candidates(rows, scored, forms), [a["key"]])


class DetailPass(TempData):
    def test_fills_the_band_once_then_serves_it_from_the_cache(self):
        self.stub(fixture("greenhouse", "detail_canada_ranges.json"))
        r = self.row(11)
        rows = {r["key"]: r}
        verdicts = {r["key"]: ("PASS", [])}
        line = sweep.gh_detail_pass(rows, G, verdicts, scored={})
        self.assertEqual(r["comp"], "$221,000 - $233,000 (+3 more ranges)")
        self.assertIn("1 pay band(s) filled", line)
        self.assertEqual(len(self.calls), 1)
        saved = json.loads((sweep.DATA / "form_questions.json").read_text(encoding="utf-8"))
        self.assertEqual(saved[r["key"]]["jd_hash"], sweep.jd_hash(r["description"]))
        # Next run: the list read gives no comp again; the cache answers without a fetch.
        r2 = self.row(11); rows = {r2["key"]: r2}
        line = sweep.gh_detail_pass(rows, G, verdicts, scored={})
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(r2["comp"], "$221,000 - $233,000 (+3 more ranges)")
        self.assertIn("1 from data/form_questions.json", line)

    def test_a_rewritten_jd_is_fetched_again(self):
        self.stub(fixture("greenhouse", "detail_canada_ranges.json"))
        r = self.row(12); verdicts = {r["key"]: ("PASS", [])}
        sweep.gh_detail_pass({r["key"]: r}, G, verdicts, scored={})
        r2 = self.row(12); r2["description"] += " Now 6+ years."
        sweep.gh_detail_pass({r2["key"]: r2}, G, verdicts, scored={})
        self.assertEqual(len(self.calls), 2)

    def test_fail_rows_and_rows_with_comp_are_not_fetched(self):
        self.stub(fixture("greenhouse", "detail_canada_ranges.json"))
        a, b = self.row(13), self.row(14, comp="$150,000 - $180,000")
        rows = {a["key"]: a, b["key"]: b}
        self.assertIsNone(sweep.gh_detail_pass(rows, G, {a["key"]: ("FAIL", ["title"]), b["key"]: ("PASS", [])}, scored={}))
        self.assertEqual(self.calls, [])

    def test_budget_forms_first_then_newest_pay(self):
        self.stub(fixture("greenhouse", "detail_years_buckets.json"))
        old, new, scored_row = self.row(21, "2026-09-01"), self.row(22, "2026-10-10"), self.row(23, "2026-08-01", comp="$1 - $2")
        rows = {r["key"]: r for r in (old, new, scored_row)}
        verdicts = {k: ("PASS", []) for k in rows}
        line = sweep.gh_detail_pass(rows, dict(G, gh_detail_max_fetch=2), verdicts, scored={scored_row["key"]: {"score": 81}})
        self.assertEqual(sorted(j for _, j in self.calls), ["22", "23"])      # the scored form, then the newest
        self.assertIn("1 form(s) read", line)
        self.assertIn("1 over the 2-fetch budget", line)
        self.assertIsNone(old["comp"])

    def test_no_candidate_profile_reads_no_forms_but_still_fills_bands(self):
        self.stub(fixture("greenhouse", "detail_years_buckets.json"))
        pay, scored_row = self.row(31), self.row(32, comp="$1 - $2")
        rows = {r["key"]: r for r in (pay, scored_row)}
        g = {k: v for k, v in G.items() if k != "candidate_profile"}
        line = sweep.gh_detail_pass(rows, g, {k: ("PASS", []) for k in rows}, scored={scored_row["key"]: {"score": 90}})
        self.assertEqual([j for _, j in self.calls], ["31"])
        self.assertEqual(pay["comp"], "$200,000 - $240,000")
        self.assertIn("0 form(s) read", line)


class LiveSweepWiring(TempData):
    """run() end to end with a fake Greenhouse list read: the band lands in the snapshot row and the
    gh-detail line rides in the health notes; the next day the cache answers with no fetch."""

    def setUp(self):
        super().setUp()
        self.saved_more = {k: getattr(sweep, k) for k in ("REPORTS", "READLOG")}
        self.saved_adapter = sweep.ADAPTERS.get("greenhouse")
        self.orig_sleep = sweep.time.sleep
        sweep.REPORTS = Path(self.tmp.name) / "reports"; sweep.REPORTS.mkdir()
        sweep.READLOG = sweep.DATA / "read_log.json"
        sweep.time.sleep = lambda s: None
        os.environ["MAXQ_SERIAL"] = "1"
        self.stub(fixture("greenhouse", "detail_canada_ranges.json"))

        def lister(t, smoke=False):
            r = self.row(41); r.pop("_target")
            return [r]
        sweep.ADAPTERS["greenhouse"] = lister
        self.targets = [{"company": "ExampleCo", "ats": "greenhouse", "slug": "exampleco", "verified": True}]

    def tearDown(self):
        for k, v in self.saved_more.items(): setattr(sweep, k, v)
        sweep.ADAPTERS["greenhouse"] = self.saved_adapter
        sweep.time.sleep = self.orig_sleep
        os.environ.pop("MAXQ_SERIAL", None)
        super().tearDown()

    def sweep_day(self, day):
        sweep.TODAY = dt.date(2026, 10, day)
        with contextlib.redirect_stdout(io.StringIO()):
            sweep.run(self.targets, G, fresh=True)
        return json.loads((sweep.DATA / "latest.json").read_text(encoding="utf-8"))

    def test_band_lands_in_the_snapshot_then_comes_from_the_cache(self):
        snap = self.sweep_day(10)
        k = "greenhouse:ExampleCo:41"
        self.assertEqual(snap["verdicts"][k], "PASS")
        self.assertEqual(snap["rows"][k]["comp"], "$221,000 - $233,000 (+3 more ranges)")
        self.assertTrue(any(n.startswith("gh-detail: 1 pay band(s)") for n in snap["health"]["notes"]))
        self.assertEqual(self.calls, [("exampleco", "41")])
        snap = self.sweep_day(11)
        self.assertEqual(snap["rows"][k]["comp"], "$221,000 - $233,000 (+3 more ranges)")
        self.assertEqual(len(self.calls), 1)


class WindowLines(TempData):
    def test_window_prints_the_knockout_and_the_count(self):
        r = self.row(31, posted="2026-10-09"); r.pop("_target")
        r["jd_hash"] = sweep.jd_hash(r["description"])
        self.assertIn(sweep.gate_final(r, G)[0], ("PASS", "REVIEW", "TENURE", "LOCATION-POLICY"))
        (sweep.DATA / "latest.json").write_text(json.dumps({"rows": {r["key"]: r}, "ts": "2026-10-11T08:00:00"}), encoding="utf-8")
        (sweep.DATA / "seen.json").write_text(json.dumps({r["key"]: "2026-10-09"}), encoding="utf-8")
        e = sweep.form_entry(fixture("greenhouse", "detail_canada_ranges.json"), US_ROW, r["jd_hash"], CAND)
        e["questions"].append({"label": "What are your salary expectations?", "required": True, "type": "input_text",
                               "options_summary": "", "verdict": "ASK", "truthful_answer": "his number",
                               "reason": "salary expectation"})
        sweep.save_forms({r["key"]: e})
        out = io.StringIO()
        with contextlib.redirect_stdout(out): sweep.window_query(G, "14d")
        text = out.getvalue()
        self.assertIn('KNOCKOUT (form): "Are you legally entitled to work in Canada?" -> truthful NO', text)
        self.assertIn("FORM: 1 ask, 1 knockout (ask: salary expectation) · read 2026-10-11", text)
        self.assertIn("comp=$221,000 - $233,000 (+3 more ranges) (pay_input_ranges)", text)

    def test_window_flags_a_form_read_before_a_jd_change(self):
        e = {"fetched_on": "2026-10-10", "jd_hash": "old", "questions": []}
        self.assertIn("JD changed since the form was read", sweep.form_window_lines(e, "new")[-1])
        e = {"fetched_on": "2026-09-01", "jd_hash": "x", "questions": []}
        self.assertIn("over 14 days", sweep.form_window_lines(e, "x")[-1])


if __name__ == "__main__":
    unittest.main()
