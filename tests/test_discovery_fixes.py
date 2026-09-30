"""Regression tests for three sweep defects: UTC dates read a day ahead, a half-written --set-score
batch, and a failed rescue body fetch counted as an employer coverage hole.

Offline; every write goes to a temp directory. The date tests hold in any time zone: each expected
value is computed through the same local conversion the sweep uses."""
import contextlib, datetime as dt, io, json, tempfile, unittest
from pathlib import Path

from tests.stubs import sweep, gates

G = gates()


def local_date(y, m, d, hh, mm, ss=0):
    return dt.datetime(y, m, d, hh, mm, ss, tzinfo=dt.timezone.utc).astimezone().date().isoformat()


def row(company="Acme", ats="greenhouse", jid=1, title="Product Manager, AI Enablement",
        location="Remote, United States", desc="Own internal AI adoption. 5+ years.", posted=None):
    return sweep.norm(company, ats, jid, title, location, f"https://jobs.example/{company}/{jid}", posted, desc)


class UtcDatesAreLocal(unittest.TestCase):
    """TODAY is the local date, so a posting's date must be too. Slicing the date off a UTC timestamp
    dated a req posted in the evening (west of Greenwich) tomorrow, and its age printed as -1d."""

    def test_a_zulu_timestamp_is_the_local_date(self):
        want = local_date(2026, 9, 30, 1, 15)
        self.assertEqual(sweep.iso_date("2026-09-30T01:15:00Z"), want)
        self.assertEqual(sweep.iso_date("2026-09-30T01:15:00.123+00:00"), want)
        if dt.datetime.now().astimezone().utcoffset() <= -dt.timedelta(hours=1, minutes=16):
            self.assertEqual(want, "2026-09-29")          # west of UTC: the evening before

    def test_an_offset_timestamp_converts_through_its_offset(self):
        self.assertEqual(sweep.iso_date("2026-09-29T21:15:00-04:00"), local_date(2026, 9, 30, 1, 15))

    def test_a_date_written_as_midnight_and_a_bare_date_keep_their_date(self):
        self.assertEqual(sweep.iso_date("2026-09-02T00:00:00.000+0000"), "2026-09-02")
        self.assertEqual(sweep.iso_date("2026-09-30"), "2026-09-30")
        self.assertEqual(sweep.iso_date("September 30, 2026"), "2026-09-30")
        self.assertIsNone(sweep.iso_date(""))
        self.assertIsNone(sweep.iso_date(True))

    def test_epoch_values_are_local_dates(self):
        secs = int(dt.datetime(2026, 9, 30, 1, 15, tzinfo=dt.timezone.utc).timestamp())
        want = local_date(2026, 9, 30, 1, 15)
        self.assertEqual(sweep.epoch_date(secs), want)
        self.assertEqual(sweep.epoch_date(secs * 1000), want)               # milliseconds
        self.assertEqual(sweep.iso_date(secs * 1000), want)
        self.assertEqual(sweep.iso_date(str(secs)), want)
        self.assertIsNone(sweep.epoch_date("not a number"))
        self.assertIsNone(sweep.epoch_date(0))
        r = sweep._lever_rows({"company": "Globex"}, [{"id": "x", "text": "PM", "createdAt": secs * 1000,
                                                        "hostedUrl": "https://jobs.example/globex/x",
                                                        "categories": {}}])[0]
        self.assertEqual(r["posted"], want)

    def test_a_same_day_skew_never_prints_minus_one(self):
        tomorrow = (sweep.TODAY + dt.timedelta(days=1)).isoformat()
        self.assertEqual(sweep.days_since(tomorrow), 0)
        self.assertEqual(sweep.days_since((sweep.TODAY + dt.timedelta(days=3)).isoformat()), -3)   # a real re-date
        _, lines = sweep.conversion_read(row(posted=tomorrow), G, {})
        self.assertIn("posted 0d ago", lines)
        self.assertFalse([x for x in lines if "-1d" in x])


class SetScoreIsAllOrNothing(unittest.TestCase):
    """An empty verdict on an unscored key raised mid-batch, after the entries before it were saved."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._saved = (sweep.DATA, sweep.SCORED)
        sweep.DATA = Path(self.tmp.name)
        sweep.SCORED = sweep.DATA / "scored.json"
        sweep.SCORED.write_text(json.dumps({"k:A:9": {"score": 60, "verdict": "Maybe", "scored_on": "2026-09-01",
                                                      "built": False}}), encoding="utf-8")

    def tearDown(self):
        sweep.DATA, sweep.SCORED = self._saved
        self.tmp.cleanup()

    def run_cmd(self, *entries):
        with contextlib.redirect_stdout(io.StringIO()):
            sweep.cmd_set_score(list(entries), G)

    def test_an_empty_verdict_on_an_unscored_key_writes_nothing(self):
        before = sweep.SCORED.read_bytes()
        with self.assertRaises(SystemExit) as cm:
            self.run_cmd("k:A:1=80:Apply", "k:A:9=65:", "k:A:2=70:")
        self.assertIn("nothing written", str(cm.exception))
        self.assertIn("k:A:2", str(cm.exception))
        self.assertEqual(sweep.SCORED.read_bytes(), before)

    def test_a_good_batch_is_written_once(self):
        calls = []
        real = sweep.save_scored
        sweep.save_scored = lambda d: (calls.append(1), real(d))
        try:
            self.run_cmd("k:A:1=80:Apply", "k:A:9=65:")
        finally:
            sweep.save_scored = real
        d = json.loads(sweep.SCORED.read_text(encoding="utf-8"))
        self.assertEqual(len(calls), 1)
        self.assertEqual((d["k:A:1"]["score"], d["k:A:9"]["score"], d["k:A:9"]["verdict"]), (80, 65, "Maybe"))


class RescueFetchFailure(unittest.TestCase):
    """A refused rescue body fetch marked the whole employer UNCOVERED, and was retried every run."""

    def setUp(self):
        self.calls = []

        def detail(t, r):
            self.calls.append(r["key"])
            raise RuntimeError("403 Client Error: Forbidden")
        self._saved = sweep.DETAIL.get("fake_detail")
        sweep.DETAIL["fake_detail"] = detail

    def tearDown(self):
        if self._saved is None: sweep.DETAIL.pop("fake_detail", None)
        else: sweep.DETAIL["fake_detail"] = self._saved

    def rescue_row(self):
        r = row(company="Initech", ats="fake_detail", title="Marathon Producer", desc="")
        r["_target"] = {"company": "Initech", "ats": "fake_detail"}
        r["_list_sig"] = sweep.list_signature(r)
        return r

    def test_a_failed_rescue_fetch_is_a_req_fail_and_waits_seven_days(self):
        r = self.rescue_row()
        v, stats = sweep.gate_all({r["key"]: r}, {}, G)
        self.assertEqual(v[r["key"]][0], "FAIL")
        self.assertTrue(v[r["key"]][1][0].startswith("title out of lane: rescue fetch failed"))
        self.assertEqual((stats["failed"], stats["rescue_failed"]), (0, 1))
        self.assertTrue(r["_rescue"]["failed"])
        prev = {r["key"]: dict(r)}
        # Next run, same list read: no fetch, still a req-level FAIL.
        r2 = self.rescue_row()
        v2, stats2 = sweep.gate_all({r2["key"]: r2}, prev, G)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(v2[r2["key"]][0], "FAIL")
        self.assertEqual(stats2["rescue_fail_wait"], 1)
        self.assertIn(f"1 earlier failure(s) waiting out the {sweep.RESCUE_FAIL_RETRY_DAYS}-day retry",
                      sweep.rescue_summary(dict(stats2, rescue_candidates=1), G))
        # Once the retry interval has passed, it is tried again.
        prev[r["key"]]["_rescue"] = dict(r["_rescue"], on=(
            sweep.TODAY - dt.timedelta(days=sweep.RESCUE_FAIL_RETRY_DAYS)).isoformat())
        r3 = self.rescue_row()
        sweep.gate_all({r3["key"]: r3}, prev, G)
        self.assertEqual(len(self.calls), 2)


if __name__ == "__main__":
    unittest.main()
