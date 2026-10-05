"""A req a gate edit freed was never in any --window, so it was never owed a score.

--window aged a req by its first_seen date or its board date. A req first seen as out of lane, then
PASS a few days later when the lane learned its wording, was older than any short window on the day it
became eligible, so it never reached one. data/eligible.json records each key's first PASS/REVIEW day,
and --window admits a freed req on that day while its posting is young, marked FREED.

Offline; every write goes to a temp directory.
"""
import contextlib, datetime as dt, io, json, tempfile, unittest
from pathlib import Path

from tests.stubs import sweep, gates

G = gates()


def row(jid, posted):
    return sweep.norm("Globex", "workday", jid, "Senior Product Manager, Internal Platforms",
                      "Remote, United States", f"https://example.test/globex/{jid}", posted,
                      "Own the product roadmap and backlog for an enterprise platform. 5+ years.")


class FreedByAGateEdit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.saved = {k: getattr(sweep, k) for k in ("DATA", "JDS", "SCORED", "CONTACTS", "TODAY")}
        sweep.DATA = base / "data"; sweep.JDS = sweep.DATA / "jds"; sweep.JDS.mkdir(parents=True)
        sweep.SCORED = sweep.DATA / "scored.json"; sweep.CONTACTS = sweep.DATA / "contacts.json"
        sweep.TODAY = dt.date(2030, 3, 21)
        self.young = row("R-40001", "2030-03-10")      # posted 11 days before it was freed
        self.old = row("R-30001", "2030-01-01")        # freed too, but stale pipeline
        rows = {r["key"]: r for r in (self.young, self.old)}
        (sweep.DATA / "latest.json").write_text(json.dumps({"rows": rows, "ts": "2030-03-21T00:00:00"}),
                                                encoding="utf-8")
        (sweep.DATA / "seen.json").write_text(json.dumps({k: "2030-03-16" for k in rows}), encoding="utf-8")
        # Two snapshot formats on disk, as a real data directory has: gzipped and raw.
        import gzip
        for d, v in (("2030-03-16", "FAIL"), ("2030-03-20", "FAIL")):
            with gzip.open(sweep.DATA / f"snapshot_{d}.json.gz", "wt", encoding="utf-8") as f:
                json.dump({"rows": rows, "verdicts": {k: v for k in rows}}, f)
        (sweep.DATA / "snapshot_2030-03-21.json").write_text(
            json.dumps({"rows": rows, "verdicts": {k: "PASS" for k in rows}}), encoding="utf-8")

    def tearDown(self):
        for k, v in self.saved.items(): setattr(sweep, k, v)
        self.tmp.cleanup()

    def window(self, w="24h"):
        out = io.StringIO()
        with contextlib.redirect_stdout(out): sweep.window_query(G, w)
        return out.getvalue()

    def backfill(self):
        with contextlib.redirect_stdout(io.StringIO()): sweep.backfill_eligible()

    def test_without_the_ledger_the_freed_req_is_invisible(self):
        """The defect as it was: a 24h window on the day it was freed lists nothing."""
        self.assertNotIn(self.young["key"], self.window())

    def test_backfill_dates_eligibility_from_the_snapshots(self):
        self.backfill()
        led = json.loads((sweep.DATA / "eligible.json").read_text(encoding="utf-8"))
        self.assertEqual(led[self.young["key"]], "2030-03-21")

    def test_the_day_it_is_freed_it_is_in_the_window_and_marked(self):
        self.backfill()
        text = self.window()
        self.assertIn(self.young["key"], text)
        self.assertIn("FREED 2030-03-21", text)
        self.assertNotIn(self.old["key"], text)        # posted 79 days ago: pipeline, not a lead

    def test_a_week_later_it_has_left_the_24h_window(self):
        self.backfill()
        sweep.TODAY = dt.date(2030, 3, 28)
        self.assertNotIn(self.young["key"], self.window())
        self.assertIn(self.young["key"], self.window("14d"))

    def test_a_pass_the_ledger_has_not_seen_counts_as_freed_today(self):
        """A gate edit since the last stamp: the ledger exists but does not hold this key yet."""
        (sweep.DATA / "eligible.json").write_text(json.dumps({"workday:Globex:R-99999": "2030-03-01"}),
                                                  encoding="utf-8")
        self.assertIn(f"FREED {sweep.TODAY.isoformat()}", self.window())

    def test_stamping_never_seeds_a_missing_ledger(self):
        """Seeding from today's verdicts would mark every open PASS row freed today."""
        with contextlib.redirect_stdout(io.StringIO()):
            n = sweep.stamp_eligible({self.young["key"]: ("PASS", [])})
        self.assertEqual(n, 0)
        self.assertFalse((sweep.DATA / "eligible.json").exists())

    def test_stamping_keeps_the_first_day(self):
        self.backfill()
        sweep.TODAY = dt.date(2030, 4, 2)
        n = sweep.stamp_eligible({self.young["key"]: ("PASS", []), "workday:Globex:R-50001": ("REVIEW", []),
                                  "workday:Globex:R-50002": ("FAIL", [])})
        led = json.loads((sweep.DATA / "eligible.json").read_text(encoding="utf-8"))
        self.assertEqual(n, 1)
        self.assertEqual(led[self.young["key"]], "2030-03-21")
        self.assertEqual(led["workday:Globex:R-50001"], "2030-04-02")
        self.assertNotIn("workday:Globex:R-50002", led)


if __name__ == "__main__":
    unittest.main()
