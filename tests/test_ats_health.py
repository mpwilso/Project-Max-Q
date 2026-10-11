"""Regression tests: per-vendor lines in the SWEEP HEALTH block.

The PASS-volume warning is board-wide (40%+), and one vendor can serve a quarter of PASS, so that vendor
renaming its location field would fail every row it serves, stay under the board-wide line, and leave
coverage reading OK. Synthetic snapshots; backtested on three weeks of real snapshots, where no line
fired on any consecutive pair.

Offline."""
import unittest

from tests.stubs import sweep


def mk(ats, i, title="Product Manager", location="Remote, United States", desc="Body.", **kw):
    r = sweep.norm(f"{ats}Co", ats, i, title, location, f"https://example.test/{ats}/{i}", None, desc)
    r.update(kw)
    return r


def snapshot(spec):
    """spec: {ats: (n_rows, n_pass, row_kwargs)} -> rows, verdict pairs, bare verdicts."""
    rows, pairs = {}, {}
    for ats, (n, n_pass, kw) in spec.items():
        for i in range(n):
            r = mk(ats, i, **kw)
            rows[r["key"]] = r
            pairs[r["key"]] = ("PASS", []) if i < n_pass else ("FAIL", ["location gate"])
    return rows, pairs, {k: v for k, (v, _) in pairs.items()}


def health(cur, prev):
    rows, pairs, _ = cur
    prows, _, pver = prev
    return sweep.sweep_health(rows, pairs, [], [], {}, {"rows": prows, "verdicts": pver}, {}, 1)


class PerAtsPassDrop(unittest.TestCase):
    def test_one_vendor_halving_warns_while_the_board_total_does_not(self):
        prev = snapshot({"greenhouse": (300, 100, {}), "ashby": (300, 100, {}), "lever": (300, 200, {})})
        cur = snapshot({"greenhouse": (300, 40, {}), "ashby": (300, 100, {}), "lever": (300, 200, {})})
        h = health(cur, prev)
        mine = [w for w in h["warnings"] if w.startswith("greenhouse: gate-passing reqs fell")]
        self.assertEqual(mine, ["greenhouse: gate-passing reqs fell to 40 from 100 in the previous snapshot "
                                "(-60%): suspect a renamed field or parser regression in that adapter before "
                                "a quiet market"])
        self.assertFalse(any(w.startswith("gate-passing reqs fell") for w in h["warnings"]))   # 340 of 400
        self.assertFalse(any(w.startswith(("ashby:", "lever:")) for w in h["warnings"]))
        self.assertEqual(h["ats_pass"], {"greenhouse": 40, "ashby": 100, "lever": 200})

    def test_exactly_half_warns_and_just_over_half_does_not(self):
        prev = snapshot({"ashby": (100, 60, {})})
        self.assertTrue(any(w.startswith("ashby: gate-passing") for w in
                            health(snapshot({"ashby": (100, 30, {})}), prev)["warnings"]))
        self.assertFalse(any(w.startswith("ashby: gate-passing") for w in
                             health(snapshot({"ashby": (100, 31, {})}), prev)["warnings"]))

    def test_a_vendor_under_thirty_pass_last_run_never_warns(self):
        prev = snapshot({"lever": (100, 29, {})})
        self.assertFalse(any(w.startswith("lever:") for w in
                             health(snapshot({"lever": (100, 0, {})}), prev)["warnings"]))

    def test_a_vendor_gone_entirely_warns(self):
        prev = snapshot({"ashby": (100, 50, {}), "lever": (100, 50, {})})
        cur = snapshot({"lever": (100, 50, {})})
        self.assertTrue(any(w.startswith("ashby: gate-passing reqs fell to 0 from 50") for w in
                            health(cur, prev)["warnings"]))

    def test_no_previous_snapshot_is_quiet(self):
        rows, pairs, _ = snapshot({"greenhouse": (100, 0, {})})
        h = sweep.sweep_health(rows, pairs, [], [], {}, {}, {}, 1)
        self.assertFalse(any(w.startswith("greenhouse:") for w in h["warnings"]))


class PerAtsEmptyFields(unittest.TestCase):
    def test_a_renamed_location_field_warns(self):
        prev = snapshot({"ashby": (100, 40, {})})
        cur = snapshot({"ashby": (100, 0, {"location": ""})})
        w = [x for x in health(cur, prev)["warnings"] if x.startswith("ashby: empty")]
        self.assertEqual(w, ["ashby: empty location 100% of 100 rows: suspect a renamed field in that adapter; "
                             "those rows fail the gates while coverage still reads OK"])

    def test_empty_title_and_location_share_one_line(self):
        cur = snapshot({"lever": (80, 0, {"location": "", "title": ""})})
        w = [x for x in health(cur, snapshot({}))["warnings"] if x.startswith("lever:")]
        self.assertEqual(len(w), 1)
        self.assertIn("empty location 100% of 80 rows; empty title 100% of 80 rows", w[0])

    def test_under_fifty_rows_or_sixty_percent_is_quiet(self):
        small = snapshot({"gem": (49, 0, {"location": ""})})
        self.assertFalse(any(x.startswith("gem:") for x in health(small, snapshot({}))["warnings"]))
        rows, pairs, ver = snapshot({"workable": (100, 0, {})})
        for i, r in enumerate(rows.values()):
            if i < 59: r["location"] = ""
        self.assertFalse(any(x.startswith("workable:") for x in
                             health((rows, pairs, ver), snapshot({}))["warnings"]))
        list(rows.values())[59]["location"] = ""          # 60 of 100
        self.assertTrue(any(x.startswith("workable: empty location 60%") for x in
                            health((rows, pairs, ver), snapshot({}))["warnings"]))

    def test_empty_location_rising_thirty_points_warns(self):
        def with_blank(n_blank):
            rows, pairs, ver = snapshot({"greenhouse": (100, 10, {})})
            for i, r in enumerate(rows.values()):
                if i < n_blank: r["location"] = ""
            return rows, pairs, ver
        w = health(with_blank(45), with_blank(10))["warnings"]
        self.assertIn("greenhouse: empty location rose to 45% of 100 rows from 10% in the previous snapshot: "
                      "suspect a renamed field in that adapter before trusting its location FAILs", w)
        self.assertFalse(any(x.startswith("greenhouse: empty location rose") for x in
                             health(with_blank(39), with_blank(10))["warnings"]))

    def test_stripped_rows_are_not_counted_as_empty_jd_text(self):
        # strip_irrelevant_text blanks out-of-lane bodies on purpose; that is not a vendor defect.
        cur = snapshot({"ashby": (100, 0, {"desc": "", "_text_stripped": True})})
        h = health(cur, snapshot({}))
        self.assertFalse(any(x.startswith("ashby:") for x in h["warnings"]))
        self.assertEqual(h["ats_fields"]["ashby"]["desc_expected"], 0)

    def test_empty_jd_text_on_a_list_body_board_warns(self):
        cur = snapshot({"lever": (100, 0, {"desc": ""})})
        w = [x for x in health(cur, snapshot({}))["warnings"] if x.startswith("lever:")]
        self.assertEqual(w, ["lever: empty JD text 100% of 100 rows that should carry it: suspect a renamed "
                             "field in that adapter; those rows fail the gates while coverage still reads OK"])

    def test_detail_board_bodies_are_expected_only_where_a_fetch_is_earned(self):
        # Workday lists carry no body; only gate-relevant verdicts get the detail read.
        rows, pairs, ver = snapshot({"workday": (100, 10, {"desc": ""})})
        h = health((rows, pairs, ver), snapshot({}))
        self.assertEqual(h["ats_fields"]["workday"]["desc_expected"], 10)
        self.assertFalse(any(x.startswith("workday:") for x in h["warnings"]))     # 10 < 50 expected

    def test_carried_rows_are_left_out(self):
        cur = snapshot({"ashby": (100, 0, {"location": "", "_carried_since": "2026-10-01"})})
        h = health(cur, snapshot({}))
        self.assertNotIn("ashby", h["ats_fields"])
        self.assertFalse(any(x.startswith("ashby:") for x in h["warnings"]))

    def test_new_lines_print_as_warnings_in_the_health_block(self):
        prev = snapshot({"ashby": (100, 40, {})})
        lines = sweep.health_lines(health(snapshot({"ashby": (100, 0, {"location": ""})}), prev))
        self.assertTrue(any(x.startswith("WARNING: ashby: gate-passing reqs fell to 0 from 40") for x in lines))
        self.assertTrue(any(x.startswith("WARNING: ashby: empty location 100%") for x in lines))


if __name__ == "__main__":
    unittest.main()
